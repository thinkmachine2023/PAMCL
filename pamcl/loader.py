"""
Composition Manifest loader and validator (v2).

Loads YAML Composition Manifests and produces:
  - Instantiated Agent dict
  - ConstraintEvaluator from declarative constraint rules
  - ControlClamper from control_limits
  - Scheduling configuration

Zero dependency on any specific simulator or agent framework.
"""

import importlib
from pathlib import Path
from typing import Any, Dict, List

import yaml

from pamcl.constraints import ConstraintEvaluator, ConstraintRule, ControlClamper


# Valid constraint rule types
_VALID_RULE_TYPES = {"max", "min", "range"}

# Valid agent roles
_VALID_ROLES = {"coordinator", "equipment", "process"}


def load_composition(path: str | Path) -> Dict[str, Any]:
    """
    Load and validate a Composition Manifest.

    Parameters
    ----------
    path : str | Path
        Path to the YAML manifest file.

    Returns
    -------
    dict
        'agents'               : dict[str, Agent]         — agent_id → instance
        'constraint_evaluator' : ConstraintEvaluator       — from YAML rules
        'control_clamper'      : ControlClamper             — from YAML limits
        'scheduling'           : dict
        'raw'                  : dict                      — original parsed YAML
        'manifest_path'        : str

    Raises
    ------
    ValueError
        If manifest validation fails.
    FileNotFoundError
        If path does not exist.

    Note: Agent class paths are imported during validation and instantiation.
    Module top-level code in the referenced agent packages will execute.
    Only load manifests you trust (see validate() for more).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    errors = validate(raw)
    if errors:
        raise ValueError(
            "Manifest validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    agents = _instantiate_agents(raw["agents"])
    rules = _build_constraint_rules(raw["constraints"])
    warmup = raw["constraints"].get("warmup_steps", 60)
    evaluator = ConstraintEvaluator(rules, warmup_steps=warmup)
    clamper = ControlClamper(raw.get("control_limits", {}))

    return {
        "agents": agents,
        "constraint_evaluator": evaluator,
        "control_clamper": clamper,
        "scheduling": raw["scheduling"],
        "raw": raw,
        "manifest_path": str(path.resolve()),
    }


def validate(manifest: dict) -> List[str]:
    """
    Statically validate a parsed manifest dict.

    NOTE ON IMPORT SIDE EFFECTS:
    Validation (and load_composition) will call importlib.import_module for every
    agent 'class' path to verify the module and class exist. This means top-level
    code in those modules WILL execute during validation. This is intentional
    (the manifest declares live agent classes that must be importable in the
    runtime environment). Callers should only load manifests from trusted sources.

    Returns
    -------
    list[str]
        Error messages. Empty list means valid.
    """
    errors: List[str] = []

    # ── 1. Required top-level keys ──
    for key in ("agents", "constraints", "scheduling"):
        if key not in manifest:
            errors.append(f"Missing required top-level key: '{key}'")
    if errors:
        return errors  # cannot continue without required keys

    # ── 1b. apiVersion / kind (documented as required/fixed in manifest-spec) ──
    api_version = manifest.get("apiVersion")
    if api_version is None:
        errors.append("Missing top-level 'apiVersion' (expected 'pamcl/v2')")
    elif api_version != "pamcl/v2":
        errors.append(f"Unsupported apiVersion '{api_version}' (expected 'pamcl/v2')")

    kind = manifest.get("kind")
    if kind is None:
        errors.append("Missing top-level 'kind' (expected 'Composition')")
    elif kind != "Composition":
        errors.append(f"Unsupported kind '{kind}' (expected 'Composition')")

    # ── 2. agents must be a non-empty list ──
    agents_list = manifest["agents"]
    if not isinstance(agents_list, list) or len(agents_list) == 0:
        errors.append("'agents' must be a non-empty list")
        return errors

    # ── 3. Each agent must have 'id' and 'class' ──
    for i, agent_def in enumerate(agents_list):
        if "id" not in agent_def:
            errors.append(f"agents[{i}]: missing 'id'")
        if "class" not in agent_def:
            errors.append(f"agents[{i}]: missing 'class'")
    if errors:
        return errors

    # ── 4. Agent ID uniqueness ──
    agent_ids = [a["id"] for a in agents_list]
    seen = set()
    for aid in agent_ids:
        if aid in seen:
            errors.append(f"Duplicate agent ID: '{aid}'")
        seen.add(aid)

    # ── 5. Agent class importability ──
    for agent_def in agents_list:
        class_path = agent_def["class"]
        module_path, sep, class_name = class_path.rpartition(".")
        if not sep:
            errors.append(f"Invalid class path (no module): '{class_path}'")
            continue
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            errors.append(f"Cannot import module '{module_path}': {e}")
            continue
        if not hasattr(mod, class_name):
            errors.append(
                f"Module '{module_path}' has no attribute '{class_name}'"
            )

    # ── 6. Agent role validation ──
    for i, agent_def in enumerate(agents_list):
        role = agent_def.get("role")
        if role is not None and role not in _VALID_ROLES:
            errors.append(
                f"agents[{i}]: invalid role '{role}' "
                f"(valid: {sorted(_VALID_ROLES)})"
            )

    # ── 7. scheduling structure ──
    sched = manifest["scheduling"]
    if not isinstance(sched, dict):
        errors.append("'scheduling' must be a dict")
        return errors

    all_ids = set(agent_ids)

    # coordinator_id reference
    coord_id = sched.get("coordinator_id")
    if coord_id is None:
        errors.append("scheduling: missing 'coordinator_id'")
    elif coord_id not in all_ids:
        errors.append(
            f"scheduling.coordinator_id '{coord_id}' not found in agents"
        )

    # sub_agents references
    sub_agents = sched.get("sub_agents")
    if sub_agents is None:
        errors.append("scheduling: missing 'sub_agents'")
    elif not isinstance(sub_agents, list):
        errors.append("scheduling.sub_agents must be a list")
    else:
        for sub_id in sub_agents:
            if sub_id not in all_ids:
                errors.append(
                    f"scheduling.sub_agents: '{sub_id}' not found in agents"
                )

    # setpoint_dispatch references
    dispatch = sched.get("setpoint_dispatch", {})
    if isinstance(dispatch, dict):
        for agent_id in dispatch:
            if agent_id not in all_ids:
                errors.append(
                    f"scheduling.setpoint_dispatch: agent '{agent_id}' "
                    f"not found in agents"
                )

    # ── 8. constraints structure ──
    constraints = manifest["constraints"]
    if not isinstance(constraints, dict):
        errors.append("'constraints' must be a dict")
    else:
        rules = constraints.get("rules")
        if rules is None:
            errors.append("constraints: missing 'rules'")
        elif not isinstance(rules, list):
            errors.append("constraints.rules must be a list")
        else:
            for i, rule in enumerate(rules):
                if "id" not in rule:
                    errors.append(f"constraints.rules[{i}]: missing 'id'")
                if "variable" not in rule:
                    errors.append(f"constraints.rules[{i}]: missing 'variable'")
                rtype = rule.get("type")
                if rtype is None:
                    errors.append(f"constraints.rules[{i}]: missing 'type'")
                elif rtype not in _VALID_RULE_TYPES:
                    errors.append(
                        f"constraints.rules[{i}]: invalid type '{rtype}' "
                        f"(valid: {sorted(_VALID_RULE_TYPES)})"
                    )

    # ── 9. control_limits structure (deep) ──
    ctrl_limits = manifest.get("control_limits", {})
    if not isinstance(ctrl_limits, dict):
        errors.append("'control_limits' must be a dict")
    else:
        for name, bounds in ctrl_limits.items():
            if not isinstance(bounds, dict):
                errors.append(
                    f"control_limits['{name}'] must be a dict with optional numeric 'min'/'max'"
                )
            else:
                for bname in ("min", "max"):
                    val = bounds.get(bname)
                    if val is not None and not isinstance(val, (int, float)):
                        errors.append(
                            f"control_limits['{name}']['{bname}'] must be numeric if present"
                        )

    return errors


def _instantiate_agents(agent_defs: list) -> Dict[str, Any]:
    """Instantiate all agents from manifest definitions."""
    agents: Dict[str, Any] = {}
    for agent_def in agent_defs:
        class_path = agent_def["class"]
        module_path, _, class_name = class_path.rpartition(".")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        config = agent_def.get("config", {})
        agent_id = agent_def["id"]
        try:
            agents[agent_id] = cls(**config)
        except Exception as e:
            raise ValueError(
                f"Failed to instantiate agent '{agent_id}' ({class_path}) "
                f"with config {config}: {e}"
            ) from e
    return agents


def _build_constraint_rules(constraints: dict) -> List[ConstraintRule]:
    """Build ConstraintRule list from manifest constraints block."""
    rules = []
    for rule_def in constraints.get("rules", []):
        rules.append(ConstraintRule(
            id=rule_def["id"],
            variable=rule_def["variable"],
            type=rule_def["type"],
            soft_limit=rule_def.get("soft_limit"),
            hard_limit=rule_def.get("hard_limit"),
            range_min=rule_def.get("min"),
            range_max=rule_def.get("max"),
            hard_min=rule_def.get("hard_min"),
            hard_max=rule_def.get("hard_max"),
            caution_fraction=rule_def.get("caution_fraction", 0.9),
            warmup_exempt=rule_def.get("warmup_exempt", False),
        ))
    return rules


def reload_constraints_from_yaml(
    path: str | Path,
) -> Dict[str, Any]:
    """
    Re-read constraints and control_limits from a YAML manifest.

    Returns new ConstraintEvaluator and ControlClamper without
    re-instantiating agents or modifying scheduling.

    Parameters
    ----------
    path : str | Path
        Path to the YAML manifest file.

    Returns
    -------
    dict
        'constraint_evaluator' : ConstraintEvaluator
        'control_clamper'      : ControlClamper
        'constraint_rules'     : list[ConstraintRule]
        'control_limits'       : dict

    Raises
    ------
    FileNotFoundError
        If path does not exist.
    ValueError
        If constraint section is invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    constraints = raw.get("constraints")
    if not isinstance(constraints, dict) or "rules" not in constraints:
        raise ValueError("Manifest missing valid 'constraints' section")

    rules = _build_constraint_rules(constraints)
    warmup = constraints.get("warmup_steps", 60)
    evaluator = ConstraintEvaluator(rules, warmup_steps=warmup)
    ctrl_limits = raw.get("control_limits", {})
    clamper = ControlClamper(ctrl_limits)

    return {
        "constraint_evaluator": evaluator,
        "control_clamper": clamper,
        "constraint_rules": rules,
        "control_limits": ctrl_limits,
    }
