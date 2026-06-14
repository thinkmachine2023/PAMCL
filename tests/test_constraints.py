"""
Tests for PAMCL Protocols and Constraint Engine.

Validates:
  1. Protocol structural typing works with plain classes (no inheritance)
  2. ConstraintEvaluator evaluates max/min/range rules
  3. ConstraintEvaluator warmup exemption
  4. ControlClamper enforces physical limits
"""

import pytest

from pamcl.protocols import Agent, SetpointReceiver, ConstraintAwareAgent
from pamcl.constraints import (
    ConstraintRule,
    ConstraintEvaluator,
    ConstraintSeverity,
    ConstraintStatus,
    ControlClamper,
)


# ══════════════════════════════════════════════════════════
# Mock third-party agent — no PAMCL imports, no inheritance
# ══════════════════════════════════════════════════════════

class VendorXMillAgent:
    """
    Simulates a third-party agent from Vendor X.
    Does NOT import pamcl. Does NOT inherit from any base class.
    Just implements observe/act/reset methods.
    """
    def __init__(self, speed_rpm=1200):
        self.speed_rpm = speed_rpm
        self._step = 0

    def observe(self, state):
        return {"power": state.get("mill_power_kW", 400)}

    def act(self, obs):
        self._step += 1
        return {"mill_speed_rpm": self.speed_rpm}

    def reset(self):
        self._step = 0

    def update_setpoints(self, speed_rpm=None):
        if speed_rpm is not None:
            self.speed_rpm = speed_rpm


class MinimalAgent:
    """Absolute minimum: observe, act, reset — nothing else."""
    def observe(self, state):
        return {}

    def act(self, obs):
        return {"valve": 0.5}

    def reset(self):
        pass


class NotAnAgent:
    """Missing act() — should NOT satisfy Agent Protocol."""
    def observe(self, state):
        return {}

    def reset(self):
        pass


# ══════════════════════════════════════════════════════════
# Protocol tests
# ══════════════════════════════════════════════════════════

class TestProtocolCompliance:
    """Verify Protocol structural typing with runtime_checkable."""

    def test_vendor_agent_satisfies_agent_protocol(self):
        agent = VendorXMillAgent()
        assert isinstance(agent, Agent)

    def test_vendor_agent_satisfies_setpoint_receiver(self):
        agent = VendorXMillAgent()
        assert isinstance(agent, SetpointReceiver)

    def test_minimal_agent_satisfies_agent_protocol(self):
        agent = MinimalAgent()
        assert isinstance(agent, Agent)

    def test_minimal_agent_not_setpoint_receiver(self):
        agent = MinimalAgent()
        assert not isinstance(agent, SetpointReceiver)

    def test_not_an_agent_fails_protocol(self):
        obj = NotAnAgent()
        assert not isinstance(obj, Agent)

    def test_vendor_agent_observe_act_cycle(self):
        agent = VendorXMillAgent(speed_rpm=1500)
        obs = agent.observe({"mill_power_kW": 450})
        action = agent.act(obs)
        assert action["mill_speed_rpm"] == 1500


# ══════════════════════════════════════════════════════════
# ConstraintEvaluator tests
# ══════════════════════════════════════════════════════════

class TestConstraintEvaluatorMax:
    """Test 'max' type constraint rules."""

    def test_nominal(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0, hard_limit=72.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"P80": 60.0})
        assert status.severity == ConstraintSeverity.NOMINAL

    def test_caution(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0, hard_limit=72.0,
                                caution_fraction=0.9)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        # 0.9 * 67 = 60.3 → 61.0 should trigger CAUTION
        status = ev.check({"P80": 61.0})
        assert status.severity == ConstraintSeverity.CAUTION

    def test_alert(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0, hard_limit=72.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"P80": 68.0})
        assert status.severity == ConstraintSeverity.ALERT

    def test_critical(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0, hard_limit=72.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"P80": 73.0})
        assert status.severity == ConstraintSeverity.CRITICAL

    def test_missing_variable_ignored(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"temperature": 100.0})
        assert status.severity == ConstraintSeverity.NOMINAL


class TestConstraintEvaluatorMin:
    """Test 'min' type constraint rules."""

    def test_nominal(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        # 0.90 > 0.765 / 0.9 ≈ 0.85 → clearly nominal (above the CAUTION band)
        status = ev.check({"recovery": 0.90})
        assert status.severity == ConstraintSeverity.NOMINAL

    def test_alert(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"recovery": 0.70})
        assert status.severity == ConstraintSeverity.ALERT

    def test_critical_with_hard(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765, hard_limit=0.5)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"recovery": 0.4})
        assert status.severity == ConstraintSeverity.CRITICAL

    def test_caution(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765, caution_fraction=0.9)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        # 0.765 / 0.9 ≈ 0.85; value=0.80 is between soft and the band → CAUTION
        status = ev.check({"recovery": 0.80})
        assert status.severity == ConstraintSeverity.CAUTION


class TestConstraintEvaluatorRange:
    """Test 'range' type constraint rules."""

    def test_within_range(self):
        rules = [ConstraintRule(id="sump", variable="sump_level", type="range",
                                range_min=0.8, range_max=3.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"sump_level": 1.8})
        assert status.severity == ConstraintSeverity.NOMINAL

    def test_below_range(self):
        rules = [ConstraintRule(id="sump", variable="sump_level", type="range",
                                range_min=0.8, range_max=3.0)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"sump_level": 0.5})
        assert status.severity == ConstraintSeverity.ALERT

    def test_above_hard_max(self):
        rules = [ConstraintRule(id="sump", variable="sump_level", type="range",
                                range_min=0.8, range_max=3.0, hard_max=3.5)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"sump_level": 4.0})
        assert status.severity == ConstraintSeverity.CRITICAL

    def test_caution_near_lower(self):
        rules = [ConstraintRule(id="sump", variable="sump_level", type="range",
                                range_min=0.8, range_max=3.0, caution_fraction=0.9)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        # 0.8 / 0.9 ≈ 0.889; 0.85 is >=0.8 and <0.889 → CAUTION approaching lower
        status = ev.check({"sump_level": 0.85})
        assert status.severity == ConstraintSeverity.CAUTION

    def test_caution_near_upper(self):
        rules = [ConstraintRule(id="sump", variable="sump_level", type="range",
                                range_min=0.8, range_max=3.0, caution_fraction=0.9)]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        # 3.0 * 0.9 = 2.7; 2.85 is <=3.0 and >2.7 → CAUTION approaching upper
        status = ev.check({"sump_level": 2.85})
        assert status.severity == ConstraintSeverity.CAUTION


class TestConstraintWarmup:
    """Test warmup exemption."""

    def test_warmup_exempt_rule_skipped(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765, warmup_exempt=True)]
        ev = ConstraintEvaluator(rules, warmup_steps=5)
        # Steps 1-4 are warmup — recovery violation should be skipped
        for _ in range(4):
            status = ev.check({"recovery": 0.5})
            assert status.severity == ConstraintSeverity.NOMINAL

    def test_warmup_exempt_rule_evaluated_after_warmup(self):
        rules = [ConstraintRule(id="recovery", variable="recovery", type="min",
                                soft_limit=0.765, warmup_exempt=True)]
        ev = ConstraintEvaluator(rules, warmup_steps=3)
        for _ in range(3):
            ev.check({"recovery": 0.5})
        # Step 4 is post-warmup
        status = ev.check({"recovery": 0.5})
        assert status.severity == ConstraintSeverity.ALERT

    def test_non_exempt_rule_evaluated_during_warmup(self):
        rules = [ConstraintRule(id="p80", variable="P80", type="max",
                                soft_limit=67.0, warmup_exempt=False)]
        ev = ConstraintEvaluator(rules, warmup_steps=100)
        status = ev.check({"P80": 70.0})
        assert status.severity == ConstraintSeverity.ALERT


class TestConstraintEvaluatorMultipleRules:
    """Test multiple rules — highest severity wins."""

    def test_highest_severity_wins(self):
        rules = [
            ConstraintRule(id="p80", variable="P80", type="max",
                           soft_limit=67.0, hard_limit=72.0),
            ConstraintRule(id="power", variable="mill_power_kW", type="max",
                           soft_limit=480.0),
        ]
        ev = ConstraintEvaluator(rules, warmup_steps=0)
        status = ev.check({"P80": 75.0, "mill_power_kW": 450.0})
        assert status.severity == ConstraintSeverity.CRITICAL
        # P80 > hard_limit → CRITICAL, mill_power > 0.9*480=432 → CAUTION
        assert len(status.violations) == 2

    def test_reset_clears_warmup(self):
        rules = [ConstraintRule(id="x", variable="x", type="max",
                                soft_limit=10.0, warmup_exempt=True)]
        ev = ConstraintEvaluator(rules, warmup_steps=3)
        for _ in range(5):
            ev.check({"x": 20.0})
        ev.reset()
        # After reset, back in warmup — exempt rules skipped
        status = ev.check({"x": 20.0})
        assert status.severity == ConstraintSeverity.NOMINAL


# ══════════════════════════════════════════════════════════
# ControlClamper tests
# ══════════════════════════════════════════════════════════

class TestControlClamper:
    """Test control variable clamping."""

    def test_within_limits(self):
        clamper = ControlClamper({"x": {"min": 0, "max": 100}})
        result = clamper.clamp({"x": 50.0})
        assert result["x"] == 50.0

    def test_clamp_above_max(self):
        clamper = ControlClamper({"x": {"min": 0, "max": 100}})
        result = clamper.clamp({"x": 150.0})
        assert result["x"] == 100.0

    def test_clamp_below_min(self):
        clamper = ControlClamper({"x": {"min": 10, "max": 100}})
        result = clamper.clamp({"x": 5.0})
        assert result["x"] == 10.0

    def test_unknown_key_passed_through(self):
        clamper = ControlClamper({"x": {"min": 0, "max": 100}})
        result = clamper.clamp({"x": 50.0, "y": 999.0})
        assert result["y"] == 999.0

    def test_none_value_skipped(self):
        clamper = ControlClamper({"x": {"min": 0, "max": 100}})
        result = clamper.clamp({"x": None})
        assert result["x"] is None

    def test_multiple_controls(self):
        clamper = ControlClamper({
            "feed": {"min": 45, "max": 70},
            "water": {"min": 5, "max": 60},
        })
        result = clamper.clamp({"feed": 80.0, "water": 3.0})
        assert result["feed"] == 70.0
        assert result["water"] == 5.0
