"""
Tests for PAMCL v0.3 features: Shadow Mode and CLI.
"""

import json
import subprocess
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


def _make_plant_state(**overrides) -> dict:
    state = {
        "P80": 65.0, "overflow_rate": 60.0, "mill_power_kW": 448.0,
        "sump_level": 1.8, "ore_hardness": 1.0, "ore_grade": 0.035,
        "recovery": 0.80, "tailing_grade_pct": 0.2,
        "concentrate_grade_pct": 22.0, "circ_load_ratio": 0.6,
        "Cw_feed": 0.45, "Cw_overflow": 0.35, "flotation_P80": 62.0,
        "rougher_level_1": 0.5, "rougher_level_2": 0.5,
        "rougher_level_3": 0.5, "rougher_level_4": 0.5,
    }
    state.update(overrides)
    return state


def _make_metrics(**overrides) -> dict:
    metrics = {
        "P80": 65.0, "mill_power_kW": 448.0, "sump_level": 1.8,
        "recovery": 0.80, "tailing_grade_pct": 0.2, "circ_load_ratio": 0.6,
    }
    metrics.update(overrides)
    return metrics


def _make_shadow_scheduler(tmp_path) -> CompositionScheduler:
    comp = load_composition(MANIFEST_PATH)
    audit = AuditLogger(tmp_path / "shadow_audit.jsonl")
    return CompositionScheduler(
        agents=comp["agents"],
        scheduling=comp["scheduling"],
        constraint_evaluator=comp["constraint_evaluator"],
        control_clamper=comp["control_clamper"],
        audit_logger=audit,
        shadow_mode=True,
    )


def _make_normal_scheduler(tmp_path) -> CompositionScheduler:
    comp = load_composition(MANIFEST_PATH)
    audit = AuditLogger(tmp_path / "normal_audit.jsonl")
    return CompositionScheduler(
        agents=comp["agents"],
        scheduling=comp["scheduling"],
        constraint_evaluator=comp["constraint_evaluator"],
        control_clamper=comp["control_clamper"],
        audit_logger=audit,
        shadow_mode=False,
    )


# ══════════════════════════════════════════════════════════
# Shadow Mode
# ══════════════════════════════════════════════════════════

class TestShadowMode:
    """Shadow mode: agents run, audit logs, but controls are empty."""

    def test_shadow_returns_empty_controls(self, tmp_path):
        sched = _make_shadow_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        controls = sched.step(state, metrics)
        assert controls == {}

    def test_shadow_step_count_increments(self, tmp_path):
        sched = _make_shadow_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        sched.step(state, metrics)
        sched.step(state, metrics)
        assert sched.step_count == 2

    def test_shadow_agents_still_run(self, tmp_path):
        sched = _make_shadow_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        sched.step(state, metrics)
        # Coordinator should have produced output even in shadow mode
        coord_out = sched.get_last_coordinator_output()
        assert "feed_rate_target" in coord_out

    def test_shadow_audit_has_shadow_controls(self, tmp_path):
        sched = _make_shadow_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for _ in range(5):
            sched.step(state, metrics)

        records = sched.audit.read_by_type("shadow_controls")
        assert len(records) == 5
        assert all(r["shadow"] is True for r in records)
        # Each record should contain actual control values
        assert "controls" in records[0]
        assert isinstance(records[0]["controls"], dict)

    def test_shadow_violations_tagged(self, tmp_path):
        sched = _make_shadow_scheduler(tmp_path)
        state = _make_plant_state()
        bad_metrics = _make_metrics(P80=75.0)

        for _ in range(5):
            sched.step(state, bad_metrics)

        violations = sched.audit.read_by_type("constraint_violation")
        assert len(violations) > 0
        assert all(r.get("shadow") is True for r in violations)

    def test_normal_mode_no_shadow_flag(self, tmp_path):
        sched = _make_normal_scheduler(tmp_path)
        state = _make_plant_state()
        bad_metrics = _make_metrics(P80=75.0)

        for _ in range(5):
            sched.step(state, bad_metrics)

        violations = sched.audit.read_by_type("constraint_violation")
        assert len(violations) > 0
        # Normal mode: no shadow flag
        assert all("shadow" not in r for r in violations)

    def test_normal_returns_controls(self, tmp_path):
        sched = _make_normal_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        controls = sched.step(state, metrics)
        assert controls != {}
        assert "ore_feed_rate" in controls

    def test_shadow_no_shadow_controls_in_normal(self, tmp_path):
        sched = _make_normal_scheduler(tmp_path)
        state = _make_plant_state()
        metrics = _make_metrics()

        for _ in range(5):
            sched.step(state, metrics)

        shadow_records = sched.audit.read_by_type("shadow_controls")
        assert len(shadow_records) == 0


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

class TestCLI:
    """CLI validation and inspection tool tests."""

    # PYTHONPATH must include both PAMCL/ and project root
    _env_path = f"{PAMCL_ROOT}{Path(':')}{PROJECT_ROOT}"

    def _run_cli(self, *args, **kwargs):
        """Run pamcl CLI with correct PYTHONPATH."""
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = self._env_path
        return subprocess.run(
            [sys.executable, "-m", "pamcl", *args],
            capture_output=True, text=True, env=env,
            **kwargs,
        )

    def test_validate_valid_manifest(self):
        result = self._run_cli("validate", str(MANIFEST_PATH))
        assert result.returncode == 0
        assert "✓" in result.stdout

    def test_validate_nonexistent_file(self):
        result = self._run_cli("validate", "/nonexistent.yaml")
        assert result.returncode == 1
        assert "not found" in result.stderr.lower()

    def test_validate_invalid_manifest(self, tmp_path):
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("agents: []\nconstraints:\n  rules: []\nscheduling: {}")
        result = self._run_cli("validate", str(bad_path))
        assert result.returncode == 1
        assert "✗" in result.stdout or "✗" in result.stderr

    def test_inspect_valid_manifest(self):
        result = self._run_cli("inspect", str(MANIFEST_PATH))
        assert result.returncode == 0
        assert "coordinator" in result.stdout
        assert "grinding" in result.stdout
        assert "Validation: passed" in result.stdout

    def test_inspect_shows_constraints(self):
        result = self._run_cli("inspect", str(MANIFEST_PATH))
        assert "p80_upper" in result.stdout
        assert "Control Limits" in result.stdout

    def test_no_command_shows_help(self):
        result = self._run_cli()
        assert result.returncode == 1

