# API Reference

> Module-level documentation for `pamcl` v0.4.0.

---

## `pamcl.protocols`

Three `@runtime_checkable` Protocol classes. **Third-party agents do not need to import these.**

### `Agent` (Protocol)

Minimal interface for PAMCL scheduling.

```python
class Agent(Protocol):
    def observe(self, state: Dict[str, Any]) -> Any: ...
    def act(self, obs: Any) -> Dict[str, float]: ...
    def reset(self) -> None: ...
```

### `SetpointReceiver` (Protocol)

Agent that can receive setpoint updates from a coordinator.

```python
class SetpointReceiver(Protocol):
    def update_setpoints(self, **kwargs: Any) -> None: ...
```

### `ConstraintAwareAgent` (Protocol)

Agent that can evaluate constraint status from plant metrics.

```python
class ConstraintAwareAgent(Protocol):
    def update_constraint_status(self, metrics: Dict[str, Any]) -> Any: ...
```

---

## `pamcl.constraints`

### `ConstraintRule` (dataclass)

Single constraint declaration parsed from YAML.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | *required* | Unique identifier |
| `variable` | `str` | *required* | Metrics dict key to evaluate |
| `type` | `str` | *required* | `'max'`, `'min'`, or `'range'` |
| `soft_limit` | `float \| None` | `None` | ALERT threshold |
| `hard_limit` | `float \| None` | `None` | CRITICAL threshold |
| `range_min`, `range_max` | `float \| None` | `None` | Range bounds (ALERT) |
| `hard_min`, `hard_max` | `float \| None` | `None` | Range bounds (CRITICAL) |
| `caution_fraction` | `float` | `0.9` | Controls CAUTION band width. For `max`: value > soft × cf. For `min`: value < soft / cf (while ≥ soft). For `range`: symmetric bands inside near the bounds. |
| `warmup_exempt` | `bool` | `False` | Skip during warmup |

### `ConstraintEvaluator`

Generic constraint evaluator driven by YAML rules.

```python
evaluator = ConstraintEvaluator(rules=[...], warmup_steps=60)
status = evaluator.check(metrics)   # → ConstraintStatus
evaluator.reset()                    # Reset warmup counter
```

| Method | Returns | Description |
|--------|---------|-------------|
| `check(metrics)` | `ConstraintStatus` | Evaluate all rules against metrics dict |
| `reset()` | `None` | Reset warmup step counter |

### `ConstraintStatus` (dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `severity` | `ConstraintSeverity` | Highest severity across all rules |
| `violations` | `list[str]` | Human-readable violation messages |

### `ConstraintSeverity` (IntEnum)

`NOMINAL = 0`, `CAUTION = 1`, `ALERT = 2`, `CRITICAL = 3`

### `ControlClamper`

Applies physical min/max limits to control variables.

```python
clamper = ControlClamper({"ore_feed_rate": {"min": 45.0, "max": 70.0}})
clamped = clamper.clamp(controls)   # → dict
```

---

## `pamcl.loader`

### `load_composition(path) → dict`

Load and validate a Composition Manifest from a YAML file.

```python
from pamcl import load_composition

comp = load_composition("compositions/slag_grinding_flotation.yaml")
```

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `agents` | `dict[str, Any]` | `agent_id → instantiated agent` |
| `constraint_evaluator` | `ConstraintEvaluator` | From YAML constraint rules |
| `control_clamper` | `ControlClamper` | From YAML control_limits |
| `scheduling` | `dict` | Raw scheduling config block |
| `raw` | `dict` | Complete parsed YAML |

**Raises**

| Exception | When |
|-----------|------|
| `FileNotFoundError` | Path does not exist |
| `ValueError` | Manifest fails validation (message lists all errors, including `apiVersion`/`kind`, agent references, rule structure, and control_limits shape issues) |

**Important**: Validation and loading will import the agent modules declared in the manifest. Only use manifests from trusted sources.

---

### `validate(manifest) → list[str]`

Statically validate a parsed manifest dict without instantiating agents.

**Validation rules** (current):

- Required top-level keys: `agents`, `constraints`, `scheduling`
- `apiVersion` must be present and exactly `"pamcl/v2"`
- `kind` must be present and exactly `"Composition"`
- `agents` is a non-empty list; each has `id` + `class`; IDs unique; roles valid if given
- Agent class paths are importable (executes module top-level code — only trust the manifest source)
- `scheduling` references (coordinator_id, sub_agents, setpoint_dispatch keys) must resolve to declared agent IDs
- `constraints.rules` is a list; every rule has `id`, `variable`, valid `type` (`max`/`min`/`range`)
- `control_limits` (if present) is a dict; each entry is itself a dict with optional numeric `min`/`max`

See `docs/manifest-spec.md` for the authoritative list and error messages.

---

### `reload_constraints_from_yaml(path) → dict`

Re-read constraints and control limits from a YAML manifest without re-instantiating agents or changing scheduling. Used by `CompositionScheduler.reload_constraints()`.

```python
from pamcl import reload_constraints_from_yaml

result = reload_constraints_from_yaml("compositions/manifest.yaml")
# result keys: constraint_evaluator, control_clamper, constraint_rules, control_limits
```

**Note on hot-reload**: When called via `CompositionScheduler.reload_constraints()`, the evaluator's internal step counter (used for `warmup_steps` / `warmup_exempt`) is preserved so that exemption windows continue correctly across reloads. The scheduler also clears its last constraint status so it will be recomputed on the next step with metrics.

---

## `pamcl.scheduler.CompositionScheduler`

### Constructor

```python
from pamcl import CompositionScheduler

scheduler = CompositionScheduler(
    agents=comp["agents"],
    scheduling=comp["scheduling"],
    constraint_evaluator=comp["constraint_evaluator"],
    control_clamper=comp["control_clamper"],
    audit_logger=audit,
    manifest_path=comp.get("manifest_path"),
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `agents` | `dict[str, Any]` | From `load_composition()` |
| `scheduling` | `dict` | From `load_composition()` |
| `constraint_evaluator` | `ConstraintEvaluator` | PAMCL generic constraint evaluator |
| `control_clamper` | `ControlClamper` | PAMCL control variable clamper |
| `audit_logger` | `AuditLogger` | For structured event recording |
| `shadow_mode` | `bool` | If True: agents run, audit logs, but `step()` returns `{}`. Default: `False` |
| `manifest_path` | `str \| None` | Path to manifest file for hot-reload. Returned in the dict from `load_composition()`. |

### `step(plant_state, metrics) → dict[str, float]`

Execute one scheduling cycle.

**Control flow per call:**

1. **Coordinator** (if `step_count == 1` or `step_count % interval == 1`):
   - If coordinator satisfies `ConstraintAwareAgent`: call `update_constraint_status(metrics)`
   - `coordinator.observe(plant_state) → act()`
   - Audit: setpoint changes, mode transitions
   - Dispatch setpoints to sub-agents via `update_setpoints()` (Protocol-checked)

2. **Sub-agents** (every step):
   - `agent.observe(plant_state) → act()`
   - Merge control outputs

3. **Clamping**:
   - `ControlClamper.clamp(merged)` — physical min/max limits

4. **Constraint evaluation**:
   - `ConstraintEvaluator.check(metrics)` — severity assessment

5. **Audit**:
   - Log constraint violations if any

### Other methods

| Method / Property | Returns | Description |
|-------------------|---------|-------------|
| `reset()` | `None` | Reset all agents, evaluator, and scheduler state |
| `step_count` | `int` | Steps since creation / last reset |
| `is_coordinator_step()` | `bool` | Whether current step is a coordinator decision point |
| `get_last_coordinator_output()` | `dict` | Copy of most recent coordinator action |
| `reload_constraints(path=None)` | `dict` | Hot-reload constraints from YAML. Swaps evaluator + clamper in-place. |

---

## `pamcl.audit.AuditLogger`

### Constructor

```python
from pamcl import AuditLogger

logger = AuditLogger("logs/audit.jsonl")
```

Creates parent directory automatically. Supports context manager:

```python
with AuditLogger("logs/audit.jsonl") as logger:
    logger.log_setpoint_change(...)
```

### Log methods

| Method | Key fields |
|--------|-----------|
| `log_setpoint_change(agent_id, variable, old_value, new_value, reason="", shadow=False)` | `event_type: "setpoint_change"` |
| `log_constraint_violation(severity, violations, shadow=False)` | `event_type: "constraint_violation"` |
| `log_mode_transition(from_mode, to_mode, reason="", shadow=False)` | `event_type: "mode_transition"` |
| `log_human_intervention(operator_id, action, reason="")` | `event_type: "human_intervention"` |
| `log_shadow_controls(step, controls)` | `event_type: "shadow_controls"` |
| `log_config_reload(old_rules, new_rules, source)` | `event_type: "config_reload"` |

When `shadow=True`, the record includes `"shadow": true` for filtering shadow-mode audit entries from production entries.

### Read methods

| Method | Returns |
|--------|---------|
| `read_all()` | `list[dict]` — all records in chronological order |
| `read_by_type(event_type)` | `list[dict]` — filtered by event type |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `event_count` | `int` | Events written in this session |

### `close()`

Flush and close the underlying file handle.

---

## `pamcl.__main__` (CLI)

Command-line interface for manifest management.

### `validate`

```bash
python -m pamcl validate <manifest.yaml>
```

Returns exit code 0 if valid, 1 if errors found. Prints all validation errors.

### `inspect`

```bash
python -m pamcl inspect <manifest.yaml>
```

Prints structured summary: agents (with roles and config), constraint rules, control limits, scheduling config, setpoint dispatch mapping. Also runs validation.

### `dashboard`

```bash
python -m pamcl dashboard <audit.jsonl> [--port 8765] [--no-browser]
```

Starts a local HTTP server serving an interactive audit log visualization dashboard.

---

## `pamcl.dashboard`

### `serve_dashboard(log_path, port=8765, open_browser=True)`

Start a local HTTP server serving the audit dashboard.

```python
from pamcl.dashboard import serve_dashboard
serve_dashboard("logs/audit.jsonl", port=8765)
```

### `load_audit_events(path) → list[dict]`

Load all events from a JSONL audit log file.

### `build_dashboard_html(events, filename) → str`

Build the complete dashboard HTML with embedded event data. Useful for generating static HTML reports.
