"""
Tests for pamcl.loader — Manifest loading and validation (v2 format).
"""

import sys
from pathlib import Path

import pytest
import yaml

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PAMCL_ROOT = Path(__file__).parent.parent
if str(PAMCL_ROOT) not in sys.path:
    sys.path.insert(0, str(PAMCL_ROOT))

from pamcl.loader import load_composition, validate
from pamcl.constraints import ConstraintEvaluator, ControlClamper


# ── Fixtures ──

COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
VALID_MANIFEST_PATH = COMPOSITIONS_DIR / "slag_grinding_flotation.yaml"


def _load_valid_manifest() -> dict:
    """Load the production manifest as a dict for mutation in tests."""
    with open(VALID_MANIFEST_PATH) as f:
        return yaml.safe_load(f)


def _write_manifest(manifest: dict, tmpdir: Path) -> Path:
    """Write a manifest dict to a temp YAML file."""
    path = tmpdir / "test_manifest.yaml"
    with open(path, "w") as f:
        yaml.dump(manifest, f)
    return path


# ══════════════════════════════════════════════════════════
# 1. Valid manifest loads successfully
# ══════════════════════════════════════════════════════════

class TestValidManifest:
    def test_load_returns_agents(self):
        result = load_composition(VALID_MANIFEST_PATH)
        assert "agents" in result
        assert "coordinator" in result["agents"]
        assert "grinding" in result["agents"]
        assert "flotation" in result["agents"]

    def test_load_returns_constraint_evaluator(self):
        result = load_composition(VALID_MANIFEST_PATH)
        assert isinstance(result["constraint_evaluator"], ConstraintEvaluator)
        assert len(result["constraint_evaluator"].rules) > 0

    def test_load_returns_control_clamper(self):
        result = load_composition(VALID_MANIFEST_PATH)
        assert isinstance(result["control_clamper"], ControlClamper)
        assert "ore_feed_rate" in result["control_clamper"].limits

    def test_load_returns_scheduling(self):
        result = load_composition(VALID_MANIFEST_PATH)
        sched = result["scheduling"]
        assert sched["coordinator_id"] == "coordinator"
        assert "grinding" in sched["sub_agents"]
        assert "flotation" in sched["sub_agents"]

    def test_load_returns_raw_yaml(self):
        result = load_composition(VALID_MANIFEST_PATH)
        assert "raw" in result
        assert result["raw"]["apiVersion"] == "pamcl/v2"

    def test_agents_are_correct_types(self):
        pytest.importorskip(
            "piccs_sf_simulator",
            reason="piccs_sf_simulator required to verify concrete agent classes from the composition",
        )
        from piccs_sf_simulator.agents.coordinator_agent import CoordinatorAgent
        from piccs_sf_simulator.agents.grinding_agent import GrindingAgent
        from piccs_sf_simulator.agents.flotation_agent import FlotationAgent

        result = load_composition(VALID_MANIFEST_PATH)
        assert isinstance(result["agents"]["coordinator"], CoordinatorAgent)
        assert isinstance(result["agents"]["grinding"], GrindingAgent)
        assert isinstance(result["agents"]["flotation"], FlotationAgent)

    def test_constraint_rules_parsed(self):
        result = load_composition(VALID_MANIFEST_PATH)
        evaluator = result["constraint_evaluator"]
        rule_ids = {r.id for r in evaluator.rules}
        assert "p80_upper" in rule_ids
        assert "mill_power" in rule_ids
        assert "sump_level" in rule_ids

    def test_control_limits_parsed(self):
        result = load_composition(VALID_MANIFEST_PATH)
        clamper = result["control_clamper"]
        assert clamper.limits["ore_feed_rate"]["max"] == 70.0
        assert clamper.limits["water_mill"]["min"] == 5.0

    def test_warmup_steps_parsed(self):
        result = load_composition(VALID_MANIFEST_PATH)
        evaluator = result["constraint_evaluator"]
        assert evaluator.warmup_steps == 60


# ══════════════════════════════════════════════════════════
# 2. Validation rejects bad manifests
# ══════════════════════════════════════════════════════════

class TestValidationErrors:
    def test_missing_agents_key(self):
        m = _load_valid_manifest()
        del m["agents"]
        errors = validate(m)
        assert any("agents" in e for e in errors)

    def test_missing_constraints_key(self):
        m = _load_valid_manifest()
        del m["constraints"]
        errors = validate(m)
        assert any("constraints" in e for e in errors)

    def test_missing_scheduling_key(self):
        m = _load_valid_manifest()
        del m["scheduling"]
        errors = validate(m)
        assert any("scheduling" in e for e in errors)

    def test_duplicate_agent_ids(self):
        m = _load_valid_manifest()
        m["agents"].append(m["agents"][0])
        errors = validate(m)
        assert any("Duplicate" in e for e in errors)

    def test_invalid_agent_class(self):
        m = _load_valid_manifest()
        m["agents"][0]["class"] = "nonexistent.module.FakeAgent"
        errors = validate(m)
        assert any("Cannot import" in e or "no attribute" in e for e in errors)

    def test_invalid_coordinator_id_reference(self):
        m = _load_valid_manifest()
        m["scheduling"]["coordinator_id"] = "ghost_agent"
        errors = validate(m)
        assert any("ghost_agent" in e for e in errors)

    def test_invalid_sub_agent_reference(self):
        m = _load_valid_manifest()
        m["scheduling"]["sub_agents"] = ["grinding", "nonexistent"]
        errors = validate(m)
        assert any("nonexistent" in e for e in errors)

    def test_missing_constraint_rules(self):
        m = _load_valid_manifest()
        del m["constraints"]["rules"]
        errors = validate(m)
        assert any("rules" in e for e in errors)

    def test_invalid_constraint_rule_type(self):
        m = _load_valid_manifest()
        m["constraints"]["rules"] = [
            {"id": "bad", "variable": "x", "type": "invalid_type"}
        ]
        errors = validate(m)
        assert any("invalid_type" in e for e in errors)

    def test_constraint_rule_missing_variable(self):
        m = _load_valid_manifest()
        m["constraints"]["rules"] = [
            {"id": "bad", "type": "max"}
        ]
        errors = validate(m)
        assert any("variable" in e for e in errors)

    def test_agent_missing_id(self):
        m = _load_valid_manifest()
        del m["agents"][0]["id"]
        errors = validate(m)
        assert any("missing 'id'" in e for e in errors)

    def test_agent_missing_class(self):
        m = _load_valid_manifest()
        del m["agents"][0]["class"]
        errors = validate(m)
        assert any("missing 'class'" in e for e in errors)

    def test_class_path_no_module(self):
        m = _load_valid_manifest()
        m["agents"][0]["class"] = "JustAClassName"
        errors = validate(m)
        assert any("no module" in e.lower() or "Invalid" in e for e in errors)

    def test_invalid_agent_role(self):
        m = _load_valid_manifest()
        m["agents"][0]["role"] = "invalid_role"
        errors = validate(m)
        assert any("invalid_role" in e for e in errors)


# ══════════════════════════════════════════════════════════
# 3. File not found
# ══════════════════════════════════════════════════════════

class TestFileErrors:
    def test_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_composition("/nonexistent/path/manifest.yaml")

    def test_load_raises_on_validation_error(self, tmp_path):
        bad = {
            "agents": [],
            "constraints": {"rules": []},
            "scheduling": {},
        }
        path = _write_manifest(bad, tmp_path)
        with pytest.raises(ValueError, match="validation failed"):
            load_composition(path)
