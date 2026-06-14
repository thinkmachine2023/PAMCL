"""
Tests for pamcl.audit — AuditLogger.
"""

import json
import sys
import time
from pathlib import Path

import pytest

PAMCL_ROOT = Path(__file__).parent.parent
if str(PAMCL_ROOT) not in sys.path:
    sys.path.insert(0, str(PAMCL_ROOT))

from pamcl.audit import AuditLogger


# ══════════════════════════════════════════════════════════
# 1. Basic write and read
# ══════════════════════════════════════════════════════════

class TestAuditWriteRead:
    def test_setpoint_change_roundtrip(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change(
                agent_id="coordinator",
                variable="feed_rate_target",
                old_value=60.0,
                new_value=62.0,
                reason="mode=0",
            )

        records = AuditLogger(log_path).read_all()
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "setpoint_change"
        assert r["agent_id"] == "coordinator"
        assert r["variable"] == "feed_rate_target"
        assert r["old_value"] == 60.0
        assert r["new_value"] == 62.0
        assert r["reason"] == "mode=0"

    def test_constraint_violation_roundtrip(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_constraint_violation(
                severity="CRITICAL",
                violations=["P80=73.0μm > hard_max=72.0"],
            )

        records = AuditLogger(log_path).read_all()
        assert len(records) == 1
        assert records[0]["severity"] == "CRITICAL"
        assert "P80" in records[0]["violations"][0]

    def test_mode_transition_roundtrip(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_mode_transition(
                from_mode="NORMAL",
                to_mode="CONSERVATIVE",
                reason="hardness=1.35",
            )

        records = AuditLogger(log_path).read_all()
        assert len(records) == 1
        assert records[0]["event_type"] == "mode_transition"
        assert records[0]["from_mode"] == "NORMAL"

    def test_human_intervention_roundtrip(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_human_intervention(
                operator_id="engineer_01",
                action="override_feed_rate",
                reason="manual test",
            )

        records = AuditLogger(log_path).read_all()
        assert len(records) == 1
        assert records[0]["operator_id"] == "engineer_01"


# ══════════════════════════════════════════════════════════
# 2. Multiple events and filtering
# ══════════════════════════════════════════════════════════

class TestAuditMultipleEvents:
    def test_multiple_events_append(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
            logger.log_setpoint_change("b", "y", 3.0, 4.0)
            logger.log_constraint_violation("ALERT", ["test"])

        records = AuditLogger(log_path).read_all()
        assert len(records) == 3

    def test_read_by_type(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
            logger.log_constraint_violation("ALERT", ["test"])
            logger.log_setpoint_change("b", "y", 3.0, 4.0)

        reader = AuditLogger(log_path)
        sp_records = reader.read_by_type("setpoint_change")
        assert len(sp_records) == 2
        cv_records = reader.read_by_type("constraint_violation")
        assert len(cv_records) == 1

    def test_event_count(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            assert logger.event_count == 0
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
            assert logger.event_count == 1
            logger.log_constraint_violation("ALERT", ["test"])
            assert logger.event_count == 2


# ══════════════════════════════════════════════════════════
# 3. Timestamp and format
# ══════════════════════════════════════════════════════════

class TestAuditFormat:
    def test_timestamp_fields_present(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)

        records = AuditLogger(log_path).read_all()
        r = records[0]
        assert "timestamp_iso" in r
        assert "timestamp_unix" in r
        assert isinstance(r["timestamp_unix"], float)

    def test_jsonl_format(self, tmp_path):
        """Each line is valid JSON."""
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
            logger.log_constraint_violation("ALERT", ["test"])

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # should not raise


# ══════════════════════════════════════════════════════════
# 4. Edge cases
# ══════════════════════════════════════════════════════════

class TestAuditEdgeCases:
    def test_read_nonexistent_file(self, tmp_path):
        log_path = tmp_path / "nonexistent.jsonl"
        logger = AuditLogger(log_path)
        assert logger.read_all() == []

    def test_context_manager(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
        # File should be closed after context exit
        assert logger._file.closed

    def test_parent_dirs_created(self, tmp_path):
        log_path = tmp_path / "sub" / "dir" / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            logger.log_setpoint_change("a", "x", 1.0, 2.0)
        assert log_path.exists()


# ══════════════════════════════════════════════════════════
# 5. Performance
# ══════════════════════════════════════════════════════════

class TestAuditPerformance:
    def test_write_latency(self, tmp_path):
        """Single event write should complete in < 1 ms (generous bound)."""
        log_path = tmp_path / "audit.jsonl"
        with AuditLogger(log_path) as logger:
            # Warm up
            logger.log_setpoint_change("a", "x", 1.0, 2.0)

            # Measure
            t0 = time.perf_counter()
            for _ in range(100):
                logger.log_setpoint_change("a", "x", 1.0, 2.0)
            elapsed = time.perf_counter() - t0

        avg_ms = (elapsed / 100) * 1000
        assert avg_ms < 1.0, f"Average write latency {avg_ms:.3f} ms > 1 ms"
