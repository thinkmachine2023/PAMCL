"""
Tests for pamcl.scheduler — CompositionScheduler (v2).
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PAMCL_ROOT = Path(__file__).parent.parent
if str(PAMCL_ROOT) not in sys.path:
    sys.path.insert(0, str(PAMCL_ROOT))

from pamcl.audit import AuditLogger
from pamcl.loader import load_composition
from pamcl.scheduler import CompositionScheduler

COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
MANIFEST_PATH = COMPOSITIONS_DIR / "slag_grinding_flotation.yaml"


def _make_scheduler(tmp_path) -> CompositionScheduler:
    """Create a scheduler from the production manifest."""
    comp = load_composition(MANIFEST_PATH)
    audit = AuditLogger(tmp_path / "audit.jsonl")
    return CompositionScheduler(
        agents=comp["agents"],
        scheduling=comp["scheduling"],
        constraint_evaluator=comp["constraint_evaluator"],
        control_clamper=comp["control_clamper"],
        audit_logger=audit,
    )


def _make_plant_state(**overrides) -> dict:
    """Construct a minimal plant_state dict for agent.observe()."""
    state = {
        "P80": 65.0,
        "overflow_rate": 60.0,
        "mill_power_kW": 448.0,
        "sump_level": 1.8,
        "ore_hardness": 1.0,
        "ore_grade": 0.035,
        "recovery": 0.80,
        "tailing_grade_pct": 0.2,
        "concentrate_grade_pct": 22.0,
        "circ_load_ratio": 0.6,
        "Cw_feed": 0.45,
        "Cw_overflow": 0.35,
        "flotation_P80": 62.0,
        "rougher_level_1": 0.5,
        "rougher_level_2": 0.5,
        "rougher_level_3": 0.5,
        "rougher_level_4": 0.5,
    }
    state.update(overrides)
    return state


def _make_metrics(**overrides) -> dict:
    """Construct a minimal metrics dict for constraint checking."""
    metrics = {
        "P80": 65.0,
        "mill_power_kW": 448.0,
        "sump_level": 1.8,
        "recovery": 0.80,
        "tailing_grade_pct": 0.2,
        "circ_load_ratio": 0.6,
    }
    metrics.update(overrides)
    return metrics


# ══════════════════════════════════════════════════════════
# 1. Initialization
# ══════════════════════════════════════════════════════════

class TestSchedulerInit:
    def test_creates_from_manifest(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        assert sched.coordinator is not None
        assert "grinding" in sched.sub_agents
        assert "flotation" in sched.sub_agents
        assert sched.step_count == 0

    def test_coord_interval(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        assert sched.coord_interval == 10


# ══════════════════════════════════════════════════════════
# 2. Step execution
# ══════════════════════════════════════════════════════════

class TestSchedulerStep:
    def test_step_returns_controls(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        controls = sched.step(state, metrics)
        assert isinstance(controls, dict)
        assert "ore_feed_rate" in controls
        assert "water_sump" in controls

    def test_step_count_increments(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        assert sched.step_count == 0
        sched.step(state, metrics)
        assert sched.step_count == 1
        sched.step(state, metrics)
        assert sched.step_count == 2

    def test_coordinator_runs_on_first_step(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        sched.step(state, metrics)
        coord_out = sched.get_last_coordinator_output()
        assert "feed_rate_target" in coord_out
        assert "mode" in coord_out

    def test_coordinator_runs_at_interval(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for i in range(11):
            sched.step(state, metrics)

        assert sched.step_count == 11

    def test_controls_are_clamped(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        controls = sched.step(state, metrics)
        if "ore_feed_rate" in controls:
            assert controls["ore_feed_rate"] >= 45.0
            assert controls["ore_feed_rate"] <= 70.0


# ══════════════════════════════════════════════════════════
# 3. Setpoint dispatch
# ══════════════════════════════════════════════════════════

class TestSetpointDispatch:
    def test_grinding_receives_setpoints(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        sched.step(state, metrics)

        grinding = sched.sub_agents["grinding"]
        coord_out = sched.get_last_coordinator_output()
        assert grinding.feed_target_tph == coord_out["feed_rate_target"]

    def test_flotation_receives_reagent_multiplier(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        sched.step(state, metrics)

        flotation = sched.sub_agents["flotation"]
        coord_out = sched.get_last_coordinator_output()
        assert flotation.reagent_multiplier == coord_out["reagent_multiplier"]


# ══════════════════════════════════════════════════════════
# 4. Audit logging
# ══════════════════════════════════════════════════════════

class TestSchedulerAudit:
    def test_setpoint_changes_logged(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for _ in range(21):
            sched.step(state, metrics)

        records = sched.audit.read_all()
        assert isinstance(records, list)

    def test_constraint_violations_logged(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()

        bad_metrics = _make_metrics(P80=75.0, mill_power_kW=500.0)

        for _ in range(5):
            sched.step(state, bad_metrics)

        records = sched.audit.read_by_type("constraint_violation")
        assert len(records) > 0
        assert records[0]["severity"] in ("ALERT", "CRITICAL")


# ══════════════════════════════════════════════════════════
# 5. Reset
# ══════════════════════════════════════════════════════════

class TestSchedulerReset:
    def test_reset_clears_state(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for _ in range(5):
            sched.step(state, metrics)
        assert sched.step_count == 5

        sched.reset()
        assert sched.step_count == 0
        assert sched.get_last_coordinator_output() == {}

    def test_can_run_after_reset(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for _ in range(5):
            sched.step(state, metrics)

        sched.reset()

        controls = sched.step(state, metrics)
        assert isinstance(controls, dict)
        assert sched.step_count == 1
