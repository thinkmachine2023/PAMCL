<div align="center">

# ⚙️ PAMCL

### Physical AI Meta-Control Layer

A vendor-agnostic framework for composing, scheduling, and auditing multi-agent industrial process control systems. Define agents, constraints, and scheduling in YAML — integrate any team's agents without code changes.

![Status: v0.4.0](https://img.shields.io/badge/status-v0.4.0-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Tests passing](https://img.shields.io/badge/tests-passing-brightgreen.svg)

[🌐 www.thinkmachine.work](https://www.thinkmachine.work)

</div>

---

## Why PAMCL?

- **🔌 Integrate any vendor's agents.** PAMCL uses Python `Protocol` (structural typing) — agents only need `observe()`, `act()`, `reset()` methods. No inheritance, no `import pamcl`, no vendor lock-in. A third-party `VendorXMillAgent` works out of the box.

- **📝 Declare constraints in YAML, not code.** Constraint rules (`max`, `min`, `range`) are declared per-variable with soft/hard limits, warmup exemption, and CAUTION thresholds. Change a limit → reload → done. No `OperatingEnvelope` dataclass to edit.

- **🏭 Add agents without touching scheduling code.** Adding a `HydrocycloneAgent` from Vendor Y means adding 5 lines to YAML. PAMCL reads the registry and schedules automatically.

- **📋 Audit every setpoint change.** When the coordinator shifts mode and changes `feed_rate_target` from 55 → 66 t/h, PAMCL writes a structured JSON Lines record — who changed what, when, and why.

> [!IMPORTANT]
> PAMCL core modules (`pamcl/`) have **zero dependency** on any specific simulator or agent framework. The only external dependency is `PyYAML`.

**v0.4.0 refinements**: Full CAUTION severity now implemented symmetrically for `min` and `range` rules; hot-reload via `scheduler.reload_constraints()` preserves warmup step progress for `warmup_exempt` rules; manifest validation enforces `apiVersion`/`kind` and performs deeper `control_limits` checks; audit reads are resilient to malformed lines; dashboard properly escapes event data. Only load manifests from trusted sources (agent class paths are imported at validation/load time).

---

## Quick start

### 1. Install

```bash
cd PAMCL
pip install -e ".[dev]"
```

### 2. Run tests

```bash
python -m pytest tests/ -v
# 121+ tests passing
```

### 3. Use in your control loop

```python
from pamcl import load_composition, CompositionScheduler, AuditLogger

# Load everything from one YAML file — no simulator imports needed
comp = load_composition("compositions/slag_grinding_flotation.yaml")

audit = AuditLogger("logs/audit.jsonl")
scheduler = CompositionScheduler(
    agents=comp["agents"],
    scheduling=comp["scheduling"],
    constraint_evaluator=comp["constraint_evaluator"],
    control_clamper=comp["control_clamper"],
    audit_logger=audit,
    manifest_path=comp.get("manifest_path"),
)

# Connect to your plant (any simulator or real plant interface)
plant = YourPlant()  # just needs get_obs() and step(**controls)
plant.reset()

PLANT_KEYS = {"ore_feed_rate", "water_mill", "water_sump", "pump_freq", ...}

metrics = {}
for step in range(480):
    controls = scheduler.step(plant.get_obs(), metrics)
    plant_controls = {k: v for k, v in controls.items() if k in PLANT_KEYS}
    for _ in range(30):
        _, metrics = plant.step(**plant_controls)

audit.close()
```

<details>
<summary>Why filter controls with <code>PLANT_KEYS</code>?</summary>

Some agents (e.g., `FlotationAgent`) return keys like `valve_opening` that are internal metrics, not plant inputs. The scheduler merges all agent outputs; the caller filters to plant-accepted kwargs. This is by design — PAMCL is plant-agnostic.

</details>

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    YAML Manifest (pamcl/v2)                   │
│  agents · roles · constraint rules · control_limits           │
│  scheduling · setpoint_dispatch                               │
└──────────────────────────┬───────────────────────────────────┘
                           │ load_composition()
                           ▼
┌──────────────────────────────────────────────────────────────┐
│               CompositionScheduler                            │
│                                                               │
│  ┌───────────┐    setpoints    ┌───────────┐                 │
│  │Coordinator ├───────────────►│ Equipment │  ← any vendor   │
│  │(process)   ├───────────────►│ Agent(s)  │                 │
│  └─────┬──────┘                └─────┬─────┘                 │
│        │ mode, targets               │ controls               │
│        ▼                             ▼                        │
│  ConstraintEvaluator.check(metrics)   ControlClamper.clamp()  │
│        │                             │                        │
│        ▼                             ▼                        │
│  AuditLogger  →  audit.jsonl                                  │
└──────────────────────────┬───────────────────────────────────┘
                           │ filtered controls
                           ▼
                   Plant.step(**kwargs)
```

---

## 4. Agent Protocol — zero-dependency integration

PAMCL uses `typing.Protocol` with `@runtime_checkable`. Third-party agents satisfy the protocol automatically:

```python
# Vendor X's agent — no PAMCL import, no inheritance
class VendorXMillAgent:
    def observe(self, state: dict) -> dict:
        return {"power": state.get("mill_power_kW")}

    def act(self, obs) -> dict:
        return {"mill_speed_rpm": 1200}

    def reset(self):
        pass
```

Three Protocol levels:

| Protocol | Methods | Who implements it |
|----------|---------|-------------------|
| `Agent` | `observe`, `act`, `reset` | All agents (required) |
| `SetpointReceiver` | `update_setpoints(**kwargs)` | Sub-agents receiving coordinator setpoints |
| `ConstraintAwareAgent` | `update_constraint_status(metrics)` | Coordinator (optional) |

---

## 5. Write a Composition Manifest

```yaml
apiVersion: pamcl/v2
kind: Composition

agents:
  - id: coordinator
    role: coordinator                  # ← role declared in YAML
    class: mypackage.agents.CoordinatorAgent
    config:
      target_feed_rate: 66.0

  - id: grinding
    role: equipment
    class: vendor_x.agents.MillAgent   # ← any vendor's class
    config:
      speed_rpm: 1200

constraints:
  warmup_steps: 60
  rules:
    - id: p80_upper
      variable: P80
      type: max
      soft_limit: 67.0
      hard_limit: 72.0

    - id: recovery
      variable: recovery
      type: min
      soft_limit: 0.765
      warmup_exempt: true              # ← skip during cold start

control_limits:
  ore_feed_rate: { min: 45.0, max: 70.0 }
  water_mill:    { min: 5.0,  max: 60.0 }

scheduling:
  coordinator_id: coordinator
  sub_agents: [grinding]
  coordinator_interval_steps: 10
  setpoint_dispatch:
    grinding:
      feed_rate_target: speed_rpm
```

See [docs/manifest-spec.md](docs/manifest-spec.md) for the full schema reference.

---

## 6. Add a new agent

Three steps, zero Python scheduling changes:

**Step 1.** Write the agent class (just implement `observe`, `act`, `reset`):

```python
# vendor_y/hydrocyclone_agent.py — no PAMCL or simulator imports needed
class HydrocycloneAgent:
    def __init__(self, target_d50c_um=45.0):
        self.target_d50c_um = target_d50c_um

    def observe(self, state):
        return {"pressure": state.get("cyclone_pressure_kPa")}

    def act(self, obs):
        return {"cyclone_valve": 0.5}

    def reset(self):
        pass

    def update_setpoints(self, target_d50c_um=None):
        if target_d50c_um is not None:
            self.target_d50c_um = target_d50c_um
```

**Step 2.** Add to YAML:

```yaml
agents:
  - id: hydrocyclone
    role: equipment
    class: vendor_y.hydrocyclone_agent.HydrocycloneAgent
    config:
      target_d50c_um: 45.0

scheduling:
  sub_agents: [grinding, flotation, hydrocyclone]
  setpoint_dispatch:
    hydrocyclone:
      d50c_setpoint: target_d50c_um
```

**Step 3.** Validate:

```bash
python -c "from pamcl import load_composition; load_composition('compositions/manifest.yaml')"
```

---

## 7. Read the audit log

Every setpoint change, constraint violation, and mode transition is recorded in append-only JSON Lines format:

```jsonl
{"timestamp_iso":"2026-06-14T11:30:00","event_type":"setpoint_change","agent_id":"coordinator","variable":"feed_rate_target","old_value":60.0,"new_value":62.0,"reason":"mode=0"}
{"timestamp_iso":"2026-06-14T11:35:00","event_type":"mode_transition","from_mode":"0","to_mode":"1","reason":"coordinator_policy"}
{"timestamp_iso":"2026-06-14T11:35:30","event_type":"constraint_violation","severity":"CAUTION","violations":["P80=68.2 > max=67.0"]}
```

Query programmatically:

```python
from pamcl import AuditLogger

logger = AuditLogger("logs/audit.jsonl")
violations = logger.read_by_type("constraint_violation")
mode_changes = logger.read_by_type("mode_transition")
```

---

## Project structure

```
PAMCL/
├── pamcl/
│   ├── __init__.py          # Public API exports
│   ├── __main__.py          # CLI: validate | inspect | dashboard
│   ├── protocols.py         # Agent, SetpointReceiver, ConstraintAwareAgent
│   ├── constraints.py       # ConstraintRule, ConstraintEvaluator, ControlClamper
│   ├── loader.py            # YAML loading + validation + hot-reload
│   ├── scheduler.py         # Declarative agent scheduling + shadow + hot-reload
│   ├── audit.py             # Append-only JSON Lines logger
│   └── dashboard.py         # Audit log visualization web dashboard
├── compositions/
│   └── slag_grinding_flotation.yaml
├── tests/
│   ├── test_constraints.py  # 31 tests — Protocol compliance + constraint engine
│   ├── test_loader.py       # 25 tests — validation coverage
│   ├── test_audit.py        # 13 tests — all event types + performance
│   ├── test_scheduler.py    # 13 tests — scheduling, dispatch, audit, reset
│   ├── test_integration.py  #  6 tests — end-to-end with FullPlant + config tests
│   ├── test_v03_features.py # 14 tests — shadow mode + CLI
│   └── test_v04_features.py # 19 tests — hot-reload + dashboard
├── docs/
│   ├── manifest-spec.md     # Full YAML v2 schema reference
│   ├── api-reference.md     # Module API documentation
│   └── integration.md       # FullPlant integration notes
├── pyproject.toml
└── README.md
```

---

## API overview

| Module | Entry point | Purpose |
|--------|------------|---------|
| `pamcl.protocols` | `Agent`, `SetpointReceiver`, `ConstraintAwareAgent` | Structural typing Protocols |
| `pamcl.constraints` | `ConstraintEvaluator`, `ControlClamper` | Generic constraint evaluation + control clamping |
| `pamcl.loader` | `load_composition(path)` | YAML → agents + evaluator + clamper + scheduling |
| `pamcl.loader` | `validate(manifest)` | Static validation, returns error list |
| `pamcl.loader` | `reload_constraints_from_yaml(path)` | Hot-reload constraints without restarting |
| `pamcl.scheduler` | `CompositionScheduler` | Declarative agent scheduling |
| `pamcl.scheduler` | `scheduler.reload_constraints()` | Runtime constraint hot-reload |
| `pamcl.audit` | `AuditLogger` | Append-only structured event logging |
| `pamcl.dashboard` | `serve_dashboard(log_path)` | Audit log web visualization |

See [docs/api-reference.md](docs/api-reference.md) for full signatures and examples.

See [CHANGELOG.md](CHANGELOG.md) for recent refinements (v0.4.0) and [FIXES_FROM_REVIEW.md](FIXES_FROM_REVIEW.md) for the detailed review remediation log.

---

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| **v0.1** | Manifest loader, scheduler, audit logger | ✅ Done |
| **v0.2** | Protocol-based interfaces, generic constraint engine | ✅ Done |
| **v0.2** | Agent role taxonomy (coordinator/equipment/process) | ✅ Done |
| **v0.2** | Zero `piccs_sf_simulator` dependency in core | ✅ Done |
| **v0.3** | Shadow mode (log-only, no actuation) | ✅ Done |
| **v0.3** | CLI: `python -m pamcl validate\|inspect` | ✅ Done |
| **v0.4** | Runtime constraint hot-reload | ✅ Done |
| **v0.4** | Dashboard audit log visualization | ✅ Done |
| **v0.4** | Full CAUTION support for all constraint types (`max`/`min`/`range`) | ✅ Done |
| **v0.4** | 121+ tests passing, stricter validation, hot-reload warmup preservation, audit robustness | ✅ Done |

---

## License

[MIT License](LICENSE) — free to use, modify, and distribute.

## Links

- **Website**: [www.thinkmachine.work](https://www.thinkmachine.work)
- **GitHub**: [github.com/thinkmachine2023/PAMCL](https://github.com/thinkmachine2023/PAMCL)

## Contributing / History

- Many v0.4.0 robustness and correctness improvements came from a comprehensive project review.
- See `CHANGELOG.md` and `FIXES_FROM_REVIEW.md`.
- `PAMCL_DEVGUIDE.md` is retained as historical reference only (pre-v2 design).
