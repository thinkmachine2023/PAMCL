# Composition Manifest Specification

> Full schema reference for `pamcl/v2` manifests.

---

## Top-level structure

```yaml
apiVersion: pamcl/v2           # Required. Fixed value. Enforced by validate().
kind: Composition               # Required. Fixed value. Enforced by validate().
metadata:                       # Optional. Human-readable metadata.
  name: <string>
  version: <semver>
  description: <string>

agents: [...]                   # Required. Non-empty list of agent definitions.
constraints: {...}              # Required. Declarative constraint rules.
control_limits: {...}           # Optional. Physical limits per control variable.
scheduling: {...}               # Required. Scheduling configuration.
```

The loader performs full top-level validation (including `apiVersion`/`kind` presence and exact values) plus deep structural checks before proceeding. See the tables in this document and `docs/api-reference.md`. 

**Trust boundary**: Agent `class` paths are imported during validation and loading. Only process manifests from trusted sources.

---

## `agents`

A list of agent definitions. Each entry instantiates one agent.

```yaml
agents:
  - id: <string>                # Required. Unique identifier within this manifest.
    role: <string>              # Optional. One of: coordinator, equipment, process
    class: <module.ClassName>   # Required. Fully-qualified Python class path.
    config:                     # Optional. Passed as **kwargs to __init__().
      <key>: <value>
```

### Validation rules

| Rule | Error message |
|------|---------------|
| Each agent must have `id` | `agents[i]: missing 'id'` |
| Each agent must have `class` | `agents[i]: missing 'class'` |
| IDs must be unique | `Duplicate agent ID: '<id>'` |
| Class path must contain `.` | `Invalid class path (no module): '<path>'` |
| Module must be importable | `Cannot import module '<module>': ...` |
| Class must exist in module | `Module '<module>' has no attribute '<class>'` |
| Role must be valid (if specified) | `agents[i]: invalid role '<role>'` |

### Agent contract (Protocol-based)

Agents do **not** need to inherit from any base class. They only need matching method signatures:

> **Import side effects**: The loader will `importlib.import_module` the declared `class` paths both during `validate()` and at instantiation time. Any top-level code in the agent's Python package will run. Only use manifests from trusted sources.

**Required** — all agents must implement:

```python
def observe(self, state: dict) -> Any        # Extract relevant observations
def act(self, obs: Any) -> dict              # Return control dict
def reset(self)                               # Reset internal state
```

**Optional** — sub-agents receiving setpoints:

```python
def update_setpoints(self, **kwargs)          # Receive coordinator setpoints
```

**Optional** — coordinator evaluating constraints:

```python
def update_constraint_status(self, metrics: dict) -> Any
```

> [!NOTE]
> These are `typing.Protocol` definitions with `@runtime_checkable`. Any class with matching methods automatically satisfies the protocol — no `import pamcl` required.

---

## `constraints`

A declarative block of constraint rules evaluated by `ConstraintEvaluator`.

```yaml
constraints:
  warmup_steps: 60              # Steps during which warmup_exempt rules are skipped

  rules:
    - id: <string>              # Required. Unique constraint identifier.
      variable: <string>        # Required. Key in the metrics dict.
      type: <string>            # Required. One of: max, min, range
      soft_limit: <float>       # ALERT threshold (for max/min types)
      hard_limit: <float>       # CRITICAL threshold (for max/min types)
      min: <float>              # Lower bound (for range type, ALERT)
      max: <float>              # Upper bound (for range type, ALERT)
      hard_min: <float>         # Lower bound (for range type, CRITICAL)
      hard_max: <float>         # Upper bound (for range type, CRITICAL)
      caution_fraction: <float> # Fraction of soft_limit for CAUTION (default: 0.9)
      warmup_exempt: <bool>     # Skip during warmup period (default: false)
```

### Constraint types

| Type | Severity logic |
|------|---------------|
| `max` | value > hard_limit → CRITICAL, value > soft_limit → ALERT, value > soft_limit × caution_fraction → CAUTION |
| `min` | value < hard_limit → CRITICAL, value < soft_limit → ALERT, value < soft_limit / caution_fraction (while still ≥ soft_limit) → CAUTION |
| `range` | value outside [hard_min, hard_max] → CRITICAL, value outside [min, max] → ALERT; inside range but within caution band near min or max (range_min / cf or range_max × cf) → CAUTION |

### Example

```yaml
constraints:
  warmup_steps: 60
  rules:
    - id: p80_upper
      variable: P80
      type: max
      soft_limit: 67.0
      hard_limit: 72.0

    - id: sump_level
      variable: sump_level
      type: range
      min: 0.8
      max: 3.0

    - id: recovery
      variable: recovery
      type: min
      soft_limit: 0.765
      warmup_exempt: true       # Skip during cold-start transient
```

### Validation rules

| Rule | Error message |
|------|---------------|
| `rules` key must exist | `constraints: missing 'rules'` |
| Each rule must have `id` | `constraints.rules[i]: missing 'id'` |
| Each rule must have `variable` | `constraints.rules[i]: missing 'variable'` |
| Each rule must have valid `type` | `constraints.rules[i]: invalid type '<type>'` |
| `apiVersion` must be present and "pamcl/v2" | `Missing top-level 'apiVersion' ...` or `Unsupported apiVersion ...` |
| `kind` must be present and "Composition" | `Missing top-level 'kind' ...` or `Unsupported kind ...` |
| `control_limits['name']` must be dict with numeric min/max | `control_limits['name'] must be a dict...` / `... must be numeric if present` |

---

## `control_limits`

Physical min/max limits for control variables. Applied by `ControlClamper` after agent outputs are merged.

```yaml
control_limits:
  ore_feed_rate:          { min: 45.0, max: 70.0 }
  water_mill:             { min: 5.0,  max: 60.0 }
  collector_dose_rougher: { min: 0.0,  max: 60.0 }
  Jg_rougher:             { min: 0.3,  max: 2.5 }
```

Values outside declared bounds are clamped silently. Control variables not listed here pass through unclamped.

---

## `scheduling`

Controls how the coordinator and sub-agents interact.

```yaml
scheduling:
  control_interval_s: 30.0              # Sub-agent decision period (seconds)
  coordinator_interval_steps: 10        # Coordinator decides every N sub-steps
  episode_length_min: 240.0             # Episode duration (minutes)

  coordinator_id: coordinator           # Must reference an agent ID
  sub_agents: [grinding, flotation]     # Must reference agent IDs

  setpoint_dispatch:                    # Coordinator → sub-agent param mapping
    grinding:
      feed_rate_target: feed_target_tph
    flotation:
      reagent_multiplier: reagent_multiplier
```

### Runtime behavior notes

- **Hot reload** (`CompositionScheduler.reload_constraints()`): The internal step counter used for `warmup_steps` / `warmup_exempt` is preserved across reloads. The scheduler clears its last `ConstraintStatus` so it will be recomputed on the next step that supplies metrics. Use `reload_constraints_from_yaml()` for the low-level evaluator/clamper only (it always starts the counter at 0).
- **Missing metrics**: Rules whose `variable` is absent from the metrics dict passed to `check()` are silently ignored (no violation, no diagnostic). This is by design for partial observations; critical variables should be reliably produced by your plant.

### `setpoint_dispatch`

Maps coordinator output keys to sub-agent `update_setpoints()` kwargs:

```
coordinator.act() returns:  {feed_rate_target: 62.0, reagent_multiplier: 1.1}
                                     │                        │
  setpoint_dispatch.grinding:        ▼                        │
    feed_rate_target → feed_target_tph                        │
                                                              │
  setpoint_dispatch.flotation:                                ▼
    reagent_multiplier → reagent_multiplier

  Result:
    grinding.update_setpoints(feed_target_tph=62.0)
    flotation.update_setpoints(reagent_multiplier=1.1)
```

> [!NOTE]
> Dispatch only fires if the sub-agent satisfies the `SetpointReceiver` Protocol (i.e., has an `update_setpoints` method). This is checked at runtime using `isinstance()`.

### Validation rules

| Rule | Error message |
|------|---------------|
| `coordinator_id` must exist in agents | `scheduling.coordinator_id '<id>' not found in agents` |
| Each `sub_agents` entry must exist | `scheduling.sub_agents: '<id>' not found in agents` |
| Each `setpoint_dispatch` key must exist | `scheduling.setpoint_dispatch: agent '<id>' not found in agents` |

---

## Complete example

See [`compositions/slag_grinding_flotation.yaml`](../compositions/slag_grinding_flotation.yaml) for a production-ready manifest.
