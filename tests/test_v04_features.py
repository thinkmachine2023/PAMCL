"""
Tests for PAMCL v0.4 features: Hot-Reload and Dashboard.
"""

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PAMCL_ROOT = Path(__file__).parent.parent
if str(PAMCL_ROOT) not in sys.path:
    sys.path.insert(0, str(PAMCL_ROOT))

from pamcl.audit import AuditLogger
from pamcl.loader import load_composition, reload_constraints_from_yaml
from pamcl.scheduler import CompositionScheduler
from pamcl.dashboard import load_audit_events, build_dashboard_html

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


def _make_modified_manifest(tmp_path, extra_rule=None, new_limit=None):
    """Create a modified manifest YAML for reload testing."""
    with open(MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)

    if extra_rule:
        manifest["constraints"]["rules"].append(extra_rule)
    if new_limit:
        manifest.setdefault("control_limits", {}).update(new_limit)

    modified_path = tmp_path / "modified_manifest.yaml"
    with open(modified_path, "w") as f:
        yaml.dump(manifest, f)
    return modified_path


# ══════════════════════════════════════════════════════════
# Hot-Reload: reload_constraints_from_yaml
# ══════════════════════════════════════════════════════════

class TestReloadConstraintsFromYaml:
    """Test the standalone reload function."""

    def test_reload_returns_evaluator_and_clamper(self):
        result = reload_constraints_from_yaml(MANIFEST_PATH)
        assert "constraint_evaluator" in result
        assert "control_clamper" in result
        assert "constraint_rules" in result
        assert "control_limits" in result

    def test_reload_rules_match_original(self):
        comp = load_composition(MANIFEST_PATH)
        result = reload_constraints_from_yaml(MANIFEST_PATH)
        assert len(result["constraint_rules"]) == len(comp["constraint_evaluator"].rules)

    def test_reload_with_extra_rule(self, tmp_path):
        extra = {"id": "test_extra", "variable": "test_var", "type": "max", "soft_limit": 99.0}
        modified = _make_modified_manifest(tmp_path, extra_rule=extra)
        result = reload_constraints_from_yaml(modified)

        original = reload_constraints_from_yaml(MANIFEST_PATH)
        assert len(result["constraint_rules"]) == len(original["constraint_rules"]) + 1

    def test_reload_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            reload_constraints_from_yaml("/nonexistent.yaml")

    def test_reload_invalid_constraints(self, tmp_path):
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("agents: []\nscheduling: {}")
        with pytest.raises(ValueError, match="constraints"):
            reload_constraints_from_yaml(bad_path)


# ══════════════════════════════════════════════════════════
# Hot-Reload: CompositionScheduler.reload_constraints
# ══════════════════════════════════════════════════════════

class TestSchedulerHotReload:
    """Test runtime constraint hot-reload via scheduler."""

    def test_reload_from_stored_path(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            manifest_path=comp["manifest_path"],
        )

        # Run a few steps
        state = _make_plant_state()
        metrics = _make_metrics()
        for _ in range(3):
            scheduler.step(state, metrics)

        # Reload
        result = scheduler.reload_constraints()
        assert result["old_rules_count"] == result["new_rules_count"]

    def test_reload_from_explicit_path(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
        )

        # Create modified manifest with an extra rule
        extra = {"id": "extra_rule", "variable": "test_var", "type": "min", "soft_limit": 0.5}
        modified = _make_modified_manifest(tmp_path, extra_rule=extra)

        result = scheduler.reload_constraints(str(modified))
        assert result["new_rules_count"] == result["old_rules_count"] + 1

    def test_reload_swaps_evaluator(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            manifest_path=comp["manifest_path"],
        )

        old_eval = scheduler.constraint_eval
        scheduler.reload_constraints()
        assert scheduler.constraint_eval is not old_eval

    def test_reload_swaps_clamper(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            manifest_path=comp["manifest_path"],
        )

        old_clamper = scheduler.clamper
        scheduler.reload_constraints()
        assert scheduler.clamper is not old_clamper

    def test_reload_logs_config_event(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            manifest_path=comp["manifest_path"],
        )

        scheduler.reload_constraints()
        records = audit.read_by_type("config_reload")
        assert len(records) == 1
        assert "old_rules" in records[0]
        assert "new_rules" in records[0]
        assert "source" in records[0]

    def test_reload_no_path_raises(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            # No manifest_path
        )

        with pytest.raises(ValueError, match="No manifest path"):
            scheduler.reload_constraints()

    def test_reload_preserves_step_count(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
            manifest_path=comp["manifest_path"],
        )

        state = _make_plant_state()
        metrics = _make_metrics()
        for _ in range(5):
            scheduler.step(state, metrics)

        scheduler.reload_constraints()
        assert scheduler.step_count == 5  # not reset

    def test_reload_new_limits_take_effect(self, tmp_path):
        comp = load_composition(MANIFEST_PATH)
        audit = AuditLogger(tmp_path / "audit.jsonl")
        scheduler = CompositionScheduler(
            agents=comp["agents"],
            scheduling=comp["scheduling"],
            constraint_evaluator=comp["constraint_evaluator"],
            control_clamper=comp["control_clamper"],
            audit_logger=audit,
        )

        # Create manifest with very tight ore_feed_rate limits
        modified = _make_modified_manifest(
            tmp_path,
            new_limit={"ore_feed_rate": {"min": 60.0, "max": 62.0}},
        )
        scheduler.reload_constraints(str(modified))

        # Now run a step — controls should be clamped to [60, 62]
        state = _make_plant_state()
        metrics = _make_metrics()
        controls = scheduler.step(state, metrics)
        if "ore_feed_rate" in controls:
            assert 60.0 <= controls["ore_feed_rate"] <= 62.0

    def test_load_composition_includes_manifest_path(self):
        comp = load_composition(MANIFEST_PATH)
        assert "manifest_path" in comp
        assert Path(comp["manifest_path"]).exists()


# ══════════════════════════════════════════════════════════
# Dashboard: HTML generation
# ══════════════════════════════════════════════════════════

class TestDashboard:
    """Test dashboard HTML generation (not the server)."""

    def _make_audit_log(self, tmp_path):
        """Create a sample audit log for testing."""
        log_path = tmp_path / "test_audit.jsonl"
        logger = AuditLogger(log_path)
        logger.log_setpoint_change("coordinator", "feed_rate", 60.0, 62.0, "mode=0")
        logger.log_constraint_violation("ALERT", ["P80=68.2 > max=67.0"])
        logger.log_mode_transition("0", "1", "coordinator_policy")
        logger.log_shadow_controls(step=1, controls={"ore_feed_rate": 62.0})
        logger.log_config_reload(old_rules=6, new_rules=7, source="manifest.yaml")
        logger.close()
        return log_path

    def test_load_audit_events(self, tmp_path):
        log_path = self._make_audit_log(tmp_path)
        events = load_audit_events(log_path)
        assert len(events) == 5

    def test_build_dashboard_html_contains_events(self, tmp_path):
        log_path = self._make_audit_log(tmp_path)
        events = load_audit_events(log_path)
        html = build_dashboard_html(events, "test_audit.jsonl")
        assert "PAMCL Audit Dashboard" in html
        assert "feed_rate" in html
        assert "ALERT" in html

    def test_build_dashboard_html_embeds_json(self, tmp_path):
        log_path = self._make_audit_log(tmp_path)
        events = load_audit_events(log_path)
        html = build_dashboard_html(events, "test.jsonl")
        # Should contain the events as embedded JSON
        assert "setpoint_change" in html
        assert "constraint_violation" in html
        assert "config_reload" in html

    def test_empty_log_produces_valid_html(self, tmp_path):
        log_path = tmp_path / "empty.jsonl"
        log_path.write_text("")
        events = load_audit_events(log_path)
        assert events == []
        html = build_dashboard_html(events, "empty.jsonl")
        assert "PAMCL Audit Dashboard" in html
        assert "[]" in html  # empty events array

    def test_dashboard_server_responds(self, tmp_path):
        """Test that the HTTP server actually serves the dashboard."""
        log_path = self._make_audit_log(tmp_path)

        from pamcl.dashboard import serve_dashboard, load_audit_events as lae, build_dashboard_html as bdh
        import http.server

        events = lae(log_path)
        html = bdh(events, log_path.name)
        html_bytes = html.encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            def log_message(self, *a): pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=5)
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "PAMCL Audit Dashboard" in body
        finally:
            server.shutdown()
