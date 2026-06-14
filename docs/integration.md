# Integration with FullPlant

> How PAMCL connects to the existing `piccs_sf_simulator` control loop (reference implementation).

**Note (v0.4)**: The `pamcl` package itself has zero dependency on the simulator. Integration tests and the historical comparison below use it only as a concrete `FullPlant` + agent example. See `README.md` "Important" callout and `CHANGELOG.md`.

---

## Control flow comparison

### Before PAMCL: `MultiAgentPlantEnv.step()`

```python
# Hardcoded in Python
grinding_action = grinding_agent.act(grinding_obs)
flotation_action = flotation_agent.act(flotation_obs)

controls = {
    "ore_feed_rate": grinding_action.get("ore_feed_rate", 60.0),
    "water_mill": grinding_action.get("water_mill"),
    # ... 10 explicit key mappings ...
}

controls = constraint_mgr.clamp_controls(controls, status)
for _ in range(steps_per_action):
    obs, metrics = plant.step(**controls)
```

### After PAMCL: `CompositionScheduler.step()`

```python
# Driven by YAML
controls = scheduler.step(plant.get_obs(), metrics)
plant_controls = {k: v for k, v in controls.items() if k in PLANT_KEYS}
for _ in range(steps_per_action):
    obs, metrics = plant.step(**plant_controls)
```

The scheduling logic, constraint evaluation, control clamping, and setpoint dispatch are all handled internally by the scheduler based on the YAML manifest.

---

## FullPlant.step() parameter whitelist

`FullPlant.step()` accepts exactly these named parameters:

```python
def step(
    self,
    ore_feed_rate: float = None,
    water_mill: float = None,
    water_sump: float = None,
    pump_freq: float = None,
    collector_dose_rougher: float = 0.0,
    frother_dose_rougher: float = 0.0,
    collector_dose_scavenger: float = 0.0,
    Jg_rougher: float = 1.2,
    Jg_scavenger: float = 1.0,
    Jg_cleaner: float = 0.8,
) -> tuple:
```

The scheduler output may contain additional keys from agent `act()` outputs (e.g., `valve_opening` from `FlotationAgent`). These must be filtered before passing to `FullPlant.step()`:

```python
PLANT_KEYS = {
    "ore_feed_rate", "water_mill", "water_sump", "pump_freq",
    "collector_dose_rougher", "frother_dose_rougher",
    "collector_dose_scavenger",
    "Jg_rougher", "Jg_scavenger", "Jg_cleaner",
}

plant_controls = {k: v for k, v in controls.items() if k in PLANT_KEYS}
```

> [!NOTE]
> `valve_opening` is not a plant input — it's an internal metric. The flotation cell level is controlled by a PID loop inside `FullPlant` that computes `valve_openings_rougher` automatically when `enable_pid=True`.

---

## Constraint architecture (v0.2)

PAMCL v0.2 uses its own `ConstraintEvaluator` (declarative, YAML-driven) instead of `piccs_sf_simulator.ConstraintManager`.

Two constraint evaluators may coexist in a PICCS-SF deployment:

| Instance | Owner | Purpose |
|----------|-------|---------|
| Internal | `CoordinatorAgent.__init__()` | Mode decisions, feed rate policy inside coordinator |
| External | PAMCL `ConstraintEvaluator` (from YAML) | Constraint checking on merged scheduler output |

The PAMCL evaluator is generic and operates only on key-value metrics. The coordinator's internal evaluator may use its own domain-specific logic (e.g., severity-based feed rate reduction).

> [!IMPORTANT]
> During the warmup period (default: 60 steps = 30 min), rules marked `warmup_exempt: true` are silently skipped. A cold-start simulation where P80 begins at ~2000 μm will trigger violations during warmup — this is expected physical transient behavior, not a control system fault.

---

## Time scale mapping

```
Coordinator cycle:     ├──────────────── 5 min ─────────────────┤
                       │                                         │
Sub-agent cycle:       ├── 30s ──┤── 30s ──┤── ... ──┤── 30s ──┤
                       │         │         │         │         │
Simulation dt:         ├1s┤1s┤...├1s┤1s┤...├1s┤1s┤...├1s┤1s┤...│
```

| Level | Period | Config key |
|-------|--------|------------|
| Coordinator | 10 × 30s = 300s (5 min) | `scheduling.coordinator_interval_steps` |
| Sub-agents | 30s | `scheduling.control_interval_s` |
| Simulation | 1s | `simulation.dt_s` (in `default_params.yaml`) |

The integration code computes `sim_steps_per_action = control_interval_s / dt_s = 30`.

---

## Observation routing

Each agent type expects specific observation keys. `FullPlant` provides agent-specific observation methods:

| Agent | Observation source | Key fields |
|-------|--------------------|------------|
| `CoordinatorAgent` | `plant.get_coordinator_obs()` | `P80`, `recovery`, `mill_power_kW`, `ore_hardness`, `ore_grade` |
| `GrindingAgent` | `plant.get_grinding_obs()` | `P80`, `sump_level`, `mill_power_kW`, `ore_hardness`, `mill_holdup` |
| `FlotationAgent` | `plant.get_flotation_obs()` | `flotation_P80`, `flotation_feed_grade`, `rougher_levels`, `recovery` |

The `CompositionScheduler` passes the full `plant.get_obs()` to all agents. Each agent's `observe()` method extracts relevant fields internally using `.get()` with safe defaults.

---

## Protocol compatibility with existing PICCS-SF agents

The existing PICCS-SF agents (`CoordinatorAgent`, `GrindingAgent`, `FlotationAgent`) already satisfy the PAMCL Protocols without modification:

| PICCS-SF Agent | `Agent` | `SetpointReceiver` | `ConstraintAwareAgent` |
|----------------|---------|--------------------|-----------------------|
| `CoordinatorAgent` | ✅ | ✅ | ✅ |
| `GrindingAgent` | ✅ | ✅ | ✗ |
| `FlotationAgent` | ✅ | ✅ | ✗ |

This is by design — PAMCL's Protocol interfaces were modeled after the existing agent method signatures.
