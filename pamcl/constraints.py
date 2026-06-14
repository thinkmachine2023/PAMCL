"""
PAMCL Generic Constraint Engine — declarative constraint evaluation.

Replaces the hardcoded OperatingEnvelope + ConstraintManager from
piccs_sf_simulator with a YAML-driven constraint model.

Components:
    ConstraintSeverity  — NOMINAL / CAUTION / ALERT / CRITICAL
    ConstraintStatus    — evaluation result with severity + violation list
    ConstraintRule      — single constraint declaration (from YAML)
    ConstraintEvaluator — evaluates all rules against metrics dict
    ControlClamper      — applies physical min/max limits to control variables
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional


class ConstraintSeverity(IntEnum):
    """Constraint violation severity levels."""
    NOMINAL = 0
    CAUTION = 1     # approaching limit
    ALERT = 2       # soft limit violated
    CRITICAL = 3    # hard limit violated


@dataclass
class ConstraintStatus:
    """Result of constraint evaluation."""
    severity: ConstraintSeverity = ConstraintSeverity.NOMINAL
    violations: List[str] = field(default_factory=list)


@dataclass
class ConstraintRule:
    """
    Single constraint declaration, parsed from YAML.

    Supported types:
        'max'   — variable must stay below soft_limit / hard_limit
        'min'   — variable must stay above soft_limit / hard_limit
        'range' — variable must stay within [range_min, range_max]

    Parameters
    ----------
    id : str
        Unique constraint identifier.
    variable : str
        Key in the metrics dict to evaluate.
    type : str
        One of 'max', 'min', 'range'.
    soft_limit : float or None
        Threshold for ALERT severity.
    hard_limit : float or None
        Threshold for CRITICAL severity.
    range_min, range_max : float or None
        Bounds for 'range' type constraints (ALERT level).
    hard_min, hard_max : float or None
        Hard bounds for 'range' type (CRITICAL level).
    caution_fraction : float
        Controls the CAUTION band (default 0.9):
        - 'max': CAUTION when value > soft_limit * caution_fraction
        - 'min': CAUTION when value < soft_limit / caution_fraction (while >= soft_limit)
        - 'range': CAUTION when inside the range but within the band near range_min or range_max
    warmup_exempt : bool
        If True, skip evaluation during warmup period.
    """
    id: str
    variable: str
    type: str  # 'max', 'min', 'range'
    soft_limit: Optional[float] = None
    hard_limit: Optional[float] = None
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    hard_min: Optional[float] = None
    hard_max: Optional[float] = None
    caution_fraction: float = 0.9
    warmup_exempt: bool = False


class ConstraintEvaluator:
    """
    Generic constraint evaluator driven by declarative rules.

    Evaluates a list of ConstraintRule against a metrics dict and
    returns a ConstraintStatus with the highest severity found.

    Parameters
    ----------
    rules : list[ConstraintRule]
        Constraint rules parsed from YAML manifest.
    warmup_steps : int
        Number of initial steps during which warmup_exempt rules are skipped.
    """

    def __init__(self, rules: List[ConstraintRule], warmup_steps: int = 60):
        self.rules = list(rules)
        self.warmup_steps = warmup_steps
        self._step_count = 0

    def check(self, metrics: Dict[str, Any]) -> ConstraintStatus:
        """
        Evaluate all constraint rules against current metrics.

        Parameters
        ----------
        metrics : dict
            Plant metrics (key-value pairs).

        Returns
        -------
        ConstraintStatus
        """
        self._step_count += 1
        in_warmup = self._step_count < self.warmup_steps

        severity = ConstraintSeverity.NOMINAL
        violations: List[str] = []

        for rule in self.rules:
            if in_warmup and rule.warmup_exempt:
                continue

            value = metrics.get(rule.variable)
            if value is None:
                continue

            rule_sev, msg = self._evaluate_rule(rule, value)
            if rule_sev > ConstraintSeverity.NOMINAL:
                severity = max(severity, rule_sev)
                violations.append(msg)

        return ConstraintStatus(severity=severity, violations=violations)

    def _evaluate_rule(
        self, rule: ConstraintRule, value: float
    ) -> tuple:
        """Evaluate a single rule. Returns (severity, message)."""
        if rule.type == "max":
            return self._eval_max(rule, value)
        elif rule.type == "min":
            return self._eval_min(rule, value)
        elif rule.type == "range":
            return self._eval_range(rule, value)
        return (ConstraintSeverity.NOMINAL, "")

    def _eval_max(self, rule: ConstraintRule, value: float) -> tuple:
        if rule.hard_limit is not None and value > rule.hard_limit:
            return (
                ConstraintSeverity.CRITICAL,
                f"{rule.variable}={value:.4g} > hard_max={rule.hard_limit}",
            )
        if rule.soft_limit is not None and value > rule.soft_limit:
            return (
                ConstraintSeverity.ALERT,
                f"{rule.variable}={value:.4g} > max={rule.soft_limit}",
            )
        if (
            rule.soft_limit is not None
            and value > rule.soft_limit * rule.caution_fraction
        ):
            return (
                ConstraintSeverity.CAUTION,
                f"{rule.variable}={value:.4g} approaching max={rule.soft_limit}",
            )
        return (ConstraintSeverity.NOMINAL, "")

    def _eval_min(self, rule: ConstraintRule, value: float) -> tuple:
        if rule.hard_limit is not None and value < rule.hard_limit:
            return (
                ConstraintSeverity.CRITICAL,
                f"{rule.variable}={value:.4g} < hard_min={rule.hard_limit}",
            )
        if rule.soft_limit is not None and value < rule.soft_limit:
            return (
                ConstraintSeverity.ALERT,
                f"{rule.variable}={value:.4g} < min={rule.soft_limit}",
            )
        if rule.soft_limit is not None and value < rule.soft_limit / rule.caution_fraction:
            return (
                ConstraintSeverity.CAUTION,
                f"{rule.variable}={value:.4g} approaching min={rule.soft_limit}",
            )
        return (ConstraintSeverity.NOMINAL, "")

    def _eval_range(self, rule: ConstraintRule, value: float) -> tuple:
        # Hard limits
        if rule.hard_min is not None and value < rule.hard_min:
            return (
                ConstraintSeverity.CRITICAL,
                f"{rule.variable}={value:.4g} < hard_min={rule.hard_min}",
            )
        if rule.hard_max is not None and value > rule.hard_max:
            return (
                ConstraintSeverity.CRITICAL,
                f"{rule.variable}={value:.4g} > hard_max={rule.hard_max}",
            )
        # Soft limits (ALERT)
        if rule.range_min is not None and value < rule.range_min:
            return (
                ConstraintSeverity.ALERT,
                f"{rule.variable}={value:.4g} < min={rule.range_min}",
            )
        if rule.range_max is not None and value > rule.range_max:
            return (
                ConstraintSeverity.ALERT,
                f"{rule.variable}={value:.4g} > max={rule.range_max}",
            )
        # CAUTION bands (approaching soft bounds from inside the range)
        if rule.range_min is not None and value < rule.range_min / rule.caution_fraction:
            return (
                ConstraintSeverity.CAUTION,
                f"{rule.variable}={value:.4g} approaching min={rule.range_min}",
            )
        if rule.range_max is not None and value > rule.range_max * rule.caution_fraction:
            return (
                ConstraintSeverity.CAUTION,
                f"{rule.variable}={value:.4g} approaching max={rule.range_max}",
            )
        return (ConstraintSeverity.NOMINAL, "")


    def reset(self):
        """Reset warmup counter."""
        self._step_count = 0


class ControlClamper:
    """
    Clamp control variables to declared physical limits.

    Parameters
    ----------
    limits : dict[str, dict]
        {control_name: {'min': float, 'max': float}, ...}
        Parsed from the YAML control_limits block.
    """

    def __init__(self, limits: Dict[str, Dict[str, float]]):
        self.limits = dict(limits)

    def clamp(self, controls: Dict[str, float]) -> Dict[str, float]:
        """
        Apply physical limits to all control variables.

        Parameters
        ----------
        controls : dict
            Raw control values from agents.

        Returns
        -------
        dict
            Clamped control values.
        """
        result = dict(controls)
        for key, value in result.items():
            if key in self.limits and value is not None:
                lo = self.limits[key].get("min", float("-inf"))
                hi = self.limits[key].get("max", float("inf"))
                result[key] = max(lo, min(hi, value))
        return result
