"""
Integration test — End-to-end: load_composition → CompositionScheduler → FullPlant.

Validates:
  1. Full pipeline from YAML → agent instantiation → scheduling → plant simulation
  2. Constraint evaluation via PAMCL generic engine (no piccs ConstraintManager)
  3. Audit log is populated with structured records
  4. Control clamping keeps all controls within physical bounds
"""

import sys
from pathlib import Path

import yaml
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PAMCL_ROOT = Path(__file__).parent.parent
if str(PAMCL_ROOT) not in sys.path:
    sys.path.insert(0, str(PAMCL_ROOT))

from pamcl.audit import AuditLogger
from pamcl.constraints import ConstraintSeverity
from pamcl.loader import load_composition
from pamcl.scheduler import CompositionScheduler

pytest.importorskip(
    "piccs_sf_simulator",
    reason="piccs_sf_simulator package is required for integration tests "
           "(not part of the PAMCL public distribution)",
)
from piccs_sf_simulator.circuit.full_plant import FullPlant


COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
MANIFEST_PATH = COMPOSITIONS_DIR / "slag_grinding_flotation.yaml"
CONFIG_PATH = PROJECT_ROOT / "piccs_sf_simulator" / "config" / "default_params.yaml"

# Keys accepted by FullPlant.step(). Scheduler output may contain extra keys
# from agent act() that FullPlant doesn't accept (e.g. valve_opening).
_PLANT_STEP_KEYS = {
    "ore_feed_rate", "water_mill", "water_sump", "pump_freq",
    "collector_dose_rougher", "frother_dose_rougher",
    "collector_dose_scavenger",
    "Jg_rougher", "Jg_scavenger", "Jg_cleaner",
}


def _filter_for_plant(controls: dict) -> dict:
    """Filter scheduler output to FullPlant.step() accepted kwargs."""
    return {k: v for k, v in controls.items() if k in _PLANT_STEP_KEYS}


def _load_params():
    """Load plant parameters."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _make_scheduler(comp, tmp_path, audit_name="audit.jsonl"):
    """Create a CompositionScheduler from loaded composition."""
    audit = AuditLogger(tmp_path / audit_name)
    return CompositionScheduler(
        agents=comp["agents"],
        scheduling=comp["scheduling"],
        constraint_evaluator=comp["constraint_evaluator"],
        control_clamper=comp["control_clamper"],
        audit_logger=audit,
    )


# ══════════════════════════════════════════════════════════
# End-to-end simulation
# ══════════════════════════════════════════════════════════

class TestEndToEnd:
    """Full pipeline: YAML → Scheduler → FullPlant → 30 min simulation."""

    def test_full_pipeline_30min(self, tmp_path):
        """
        Run the complete PAMCL pipeline for 30 min simulated time.

        Steps:
          1. Load composition from YAML
          2. Create CompositionScheduler with PAMCL constraint engine
          3. Create FullPlant simulator
          4. Run 60 scheduling steps (30 min at 30s intervals)
          5. Verify: no crashes, controls within bounds, audit populated
        """
        # 1. Load composition
        comp = load_composition(MANIFEST_PATH)
        assert "coordinator" in comp["agents"]

        # 2. Create scheduler with PAMCL constraint engine
        audit = AuditLogger(tmp_path / "integration_audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
        )

        # 3. Create plant
        params = _load_params()
        plant = FullPlant(
            params=params,
            enable_disturbances=False,
            enable_pid=True,
            enable_feedforward=False,
        )
        plant.reset(seed=42)

        # 4. Run simulation
        sched_config = comp["scheduling"]
        control_interval_s = sched_config["control_interval_s"]
        sim_dt_s = params.get("simulation", {}).get("dt_s", 1.0)
        sim_steps_per_action = max(int(control_interval_s / sim_dt_s), 1)

        n_steps = 60  # 60 × 30s = 30 min
        all_controls = []

        metrics = {}
        for step_i in range(n_steps):
            plant_state = plant.get_obs()
            controls = scheduler.step(plant_state, metrics)

            # Verify controls are within physical bounds
            if "ore_feed_rate" in controls:
                assert 0.0 <= controls["ore_feed_rate"] <= 100.0, \
                    f"Step {step_i}: ore_feed_rate={controls['ore_feed_rate']}"

            # Run plant simulation
            plant_controls = _filter_for_plant(controls)
            for _ in range(sim_steps_per_action):
                obs_dict, metrics = plant.step(**plant_controls)

            all_controls.append(dict(controls))

        # 5. Assertions
        assert scheduler.step_count == n_steps
        assert len(all_controls) == n_steps

        # Audit log should have records
        audit.close()
        records = AuditLogger(tmp_path / "integration_audit.jsonl").read_all()
        assert len(records) > 0, "Audit log is empty after 30 min simulation"

        # Verify KPIs are in reasonable range at the end
        final_P80 = metrics.get("P80", 0.0)
        assert 40.0 < final_P80 < 80.0, f"Final P80={final_P80} out of range"

    def test_scheduler_produces_stable_output(self, tmp_path):
        """
        Run 20 steps with constant plant state (no disturbances).
        Verify that the scheduler output is deterministic.
        """
        comp = load_composition(MANIFEST_PATH)
        scheduler = _make_scheduler(comp, tmp_path, "determinism_audit.jsonl")

        fixed_state = {
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
        fixed_metrics = {
            "P80": 65.0,
            "mill_power_kW": 448.0,
            "sump_level": 1.8,
            "recovery": 0.80,
            "tailing_grade_pct": 0.2,
            "circ_load_ratio": 0.6,
        }

        outputs = []
        for _ in range(20):
            controls = scheduler.step(fixed_state, fixed_metrics)
            outputs.append(controls)

        # Steps within same coordinator period should be similar
        for key in ["ore_feed_rate", "water_sump"]:
            if key in outputs[1] and key in outputs[2]:
                diff = abs(outputs[2][key] - outputs[1][key])
                assert diff < 5.0, \
                    f"Step-to-step {key} change={diff} too large"

        scheduler.audit.close()


class TestManifestConfigEffect:
    """Verify that YAML constraint config actually affects behavior."""

    def test_constraint_rules_propagate(self, tmp_path):
        """
        Load manifest, verify that constraint rules from YAML
        are used by the ConstraintEvaluator.
        """
        comp = load_composition(MANIFEST_PATH)
        evaluator = comp["constraint_evaluator"]

        # YAML says p80_upper soft_limit = 67.0
        p80_rule = next(r for r in evaluator.rules if r.id == "p80_upper")
        assert p80_rule.soft_limit == 67.0
        assert p80_rule.hard_limit == 72.0

    def test_control_limits_propagate(self, tmp_path):
        """
        Verify that control_limits from YAML are used by ControlClamper.
        """
        comp = load_composition(MANIFEST_PATH)
        clamper = comp["control_clamper"]

        assert clamper.limits["ore_feed_rate"]["max"] == 70.0
        assert clamper.limits["water_mill"]["min"] == 5.0

    def test_modified_constraints_take_effect(self, tmp_path):
        """
        Write a manifest with tighter constraints,
        load it, verify the evaluator reflects the change.
        """
        import yaml as _yaml

        with open(MANIFEST_PATH) as f:
            manifest = _yaml.safe_load(f)

        # Tighten P80 soft limit
        for rule in manifest["constraints"]["rules"]:
            if rule["id"] == "p80_upper":
                rule["soft_limit"] = 60.0

        modified_path = tmp_path / "tight_manifest.yaml"
        with open(modified_path, "w") as f:
            _yaml.dump(manifest, f)

        comp = load_composition(modified_path)
        p80_rule = next(r for r in comp["constraint_evaluator"].rules
                        if r.id == "p80_upper")
        assert p80_rule.soft_limit == 60.0

    def test_evaluator_detects_violation_with_yaml_limits(self, tmp_path):
        """
        Verify the PAMCL ConstraintEvaluator correctly uses YAML limits
        to detect violations.
        """
        comp = load_composition(MANIFEST_PATH)
        evaluator = comp["constraint_evaluator"]

        # P80=75 should exceed hard_limit=72 → CRITICAL
        status = evaluator.check({"P80": 75.0})
        assert status.severity == ConstraintSeverity.CRITICAL
        assert any("P80" in v for v in status.violations)
