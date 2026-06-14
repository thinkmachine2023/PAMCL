"""
PAMCL — Physical AI Meta-Control Layer.

Generic configuration and audit layer for multi-agent industrial
process control. Vendor-agnostic: agents only need to satisfy
the Protocol interfaces defined in pamcl.protocols.

Modules:
    protocols   — Agent, SetpointReceiver, ConstraintAwareAgent
    constraints — ConstraintRule, ConstraintEvaluator, ControlClamper
    loader      — Composition Manifest loading + validation
    scheduler   — Declarative Agent scheduling
    audit       — Structured audit logging
"""

from pamcl.protocols import Agent, SetpointReceiver, ConstraintAwareAgent
from pamcl.constraints import (
    ConstraintRule,
    ConstraintEvaluator,
    ConstraintSeverity,
    ConstraintStatus,
    ControlClamper,
)
from pamcl.loader import load_composition, validate, reload_constraints_from_yaml
from pamcl.scheduler import CompositionScheduler
from pamcl.audit import AuditLogger

__version__ = "0.4.0"

__all__ = [
    # Protocols
    "Agent",
    "SetpointReceiver",
    "ConstraintAwareAgent",
    # Constraints
    "ConstraintRule",
    "ConstraintEvaluator",
    "ConstraintSeverity",
    "ConstraintStatus",
    "ControlClamper",
    # Loader
    "load_composition",
    "validate",
    "reload_constraints_from_yaml",
    # Scheduler
    "CompositionScheduler",
    # Audit
    "AuditLogger",
]
