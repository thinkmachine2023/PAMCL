"""
Structured audit logger for PAMCL.

Append-only JSON Lines format. Records setpoint changes, constraint violations,
mode transitions, and human interventions. Not an Event Sourcing system — the
log is consumed by human auditors and regulatory reports, not by state
reconstruction engines.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List


class AuditLogger:
    """
    Append-only structured audit logger.

    Each log record is a single JSON line with:
      - timestamp_iso: ISO 8601 local time
      - timestamp_unix: float epoch seconds
      - event_type: one of setpoint_change | constraint_violation |
                    mode_transition | human_intervention
      - (event-specific fields)

    Parameters
    ----------
    log_path : str | Path
        Output file path. Parent directories are created automatically.
    """

    def __init__(self, log_path: str | Path):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._event_count = 0

    # ── Core write ──

    def _ensure_open(self):
        if self._file is None or self._file.closed:
            self._file = open(self._path, "a", encoding="utf-8")

    def _write(self, event_type: str, payload: dict):
        self._ensure_open()
        record = {
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "timestamp_unix": time.time(),
            "event_type": event_type,
            **payload,
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()
        self._event_count += 1

    # ── Typed log methods ──

    def log_setpoint_change(
        self,
        agent_id: str,
        variable: str,
        old_value: float,
        new_value: float,
        reason: str = "",
        shadow: bool = False,
    ):
        """Record a setpoint change event."""
        payload = {
            "agent_id": agent_id,
            "variable": variable,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
        }
        if shadow:
            payload["shadow"] = True
        self._write("setpoint_change", payload)

    def log_constraint_violation(
        self,
        severity: str,
        violations: List[str],
        shadow: bool = False,
    ):
        """Record a constraint violation event."""
        payload = {
            "severity": severity,
            "violations": violations,
        }
        if shadow:
            payload["shadow"] = True
        self._write("constraint_violation", payload)

    def log_mode_transition(
        self,
        from_mode: str,
        to_mode: str,
        reason: str = "",
        shadow: bool = False,
    ):
        """Record an operating mode transition."""
        payload = {
            "from_mode": from_mode,
            "to_mode": to_mode,
            "reason": reason,
        }
        if shadow:
            payload["shadow"] = True
        self._write("mode_transition", payload)

    def log_human_intervention(
        self,
        operator_id: str,
        action: str,
        reason: str = "",
    ):
        """Record a human intervention event."""
        self._write("human_intervention", {
            "operator_id": operator_id,
            "action": action,
            "reason": reason,
        })

    def log_shadow_controls(
        self,
        step: int,
        controls: Dict[str, float],
    ):
        """Record shadow mode control outputs (not actuated)."""
        self._write("shadow_controls", {
            "shadow": True,
            "step": step,
            "controls": controls,
        })

    def log_config_reload(
        self,
        old_rules: int,
        new_rules: int,
        source: str,
    ):
        """Record a constraint hot-reload event."""
        self._write("config_reload", {
            "old_rules": old_rules,
            "new_rules": new_rules,
            "source": source,
        })

    # ── Read-back ──

    def read_all(self) -> List[Dict[str, Any]]:
        """
        Read all log records from the file.

        Malformed lines (e.g. truncated writes or manual edits) are skipped
        so that a partially corrupted log does not lose all subsequent events.

        Returns
        -------
        list[dict]
            Parsed log records in chronological order.
        """
        if not self._path.exists():
            return []
        records = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Skip bad line; log remains usable for the good records.
                    continue
        return records

    def read_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """
        Read log records filtered by event_type.

        Parameters
        ----------
        event_type : str
            One of: setpoint_change, constraint_violation, mode_transition,
            human_intervention, shadow_controls, config_reload.
        """
        return [r for r in self.read_all() if r.get("event_type") == event_type]

    # ── Lifecycle ──

    @property
    def event_count(self) -> int:
        return self._event_count

    def close(self):
        """Flush and close the log file."""
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
