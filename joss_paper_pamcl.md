---
title: "PAMCL: A Vendor-Agnostic Meta-Control Layer for Multi-Agent Industrial Process Control"
tags:
  - Python
  - multi-agent systems
  - industrial control
  - cyber-physical systems
  - constraints
  - YAML
  - process automation
authors:
  - name: ThinkMachine Labs
    orcid: "0000-0000-0000-0000"  # Replace with actual ORCID(s)
    affiliation: "PICCS-SF Research Group, ThinkMachine"
  # Add additional authors as needed
date: "2026-06-14"
---

# Summary

PAMCL (Physical AI Meta-Control Layer) is a lightweight, vendor-agnostic Python framework for composing, scheduling, and auditing multi-agent systems in industrial process control. It enables engineers to define agent compositions, physical constraints, control limits, and two-tier scheduling entirely in declarative YAML manifests. Agents integrate with zero coupling via Python `typing.Protocol` (no inheritance or PAMCL imports required). The framework provides a generic constraint engine with full support for max/min/range rules and four severity levels (including CAUTION), runtime hot-reloading of constraints while preserving warmup state, shadow mode for safe validation, and append-only structured JSON Lines audit logging for every setpoint change, constraint violation, mode transition, and configuration reload. A self-contained web dashboard enables visualization and analysis of audit logs.

PAMCL core (`pamcl/`) depends only on PyYAML. It is designed for continuous process industries (e.g., mineral processing, chemical, energy) where safety, auditability, and the ability to modify constraints without code changes are critical.

# Statement of need

Industrial multi-agent control systems frequently suffer from three persistent problems:

1. **Hard-coded scheduling and constraints**: Coordination logic, operating envelopes, and setpoint dispatch are often embedded in simulator or PLC code, making changes slow and error-prone.
2. **Lack of structured auditability**: Setpoint changes, constraint violations, and mode transitions are rarely recorded in a queryable, tamper-evident format suitable for compliance, root-cause analysis, or regulatory review.
3. **Tight coupling to specific agent implementations**: Adding or swapping agents (from different vendors or teams) usually requires modifying scheduling or integration code.

Existing general-purpose multi-agent platforms (e.g., JADE, SPADE, Jadex) and holonic manufacturing systems provide powerful communication and behavioral abstractions but are not optimized for the specific needs of real-time physical process control: declarative physical constraints with graded severity, two-time-scale scheduling with setpoint mapping, runtime reconfiguration without losing warmup state, and comprehensive audit trails for every control action.

PAMCL addresses these gaps by providing a thin **meta-control layer** that sits above any agents satisfying a minimal protocol interface. It has been developed within the PICCS-SF (Physical Intelligent Control & Cyber-Physical Systems) research program and has been iteratively refined through code review, including fixes for constraint severity completeness, reload semantics, validation robustness, and audit reliability.

# Mathematics

PAMCL's constraint engine implements four severity levels for each rule type:

- **NOMINAL** (0)
- **CAUTION** (1): approaching soft limit
- **ALERT** (2): soft limit violated
- **CRITICAL** (3): hard limit violated

For a `max` rule with soft limit $s$, hard limit $h$, and caution fraction $c$ (default 0.9):

$$
\text{severity} =
\begin{cases}
\text{CRITICAL} & \text{if } v > h \\
\text{ALERT}    & \text{if } v > s \\
\text{CAUTION}  & \text{if } v > s \cdot c \\
\text{NOMINAL}  & \text{otherwise}
\end{cases}
$$

Symmetric logic applies to `min` rules ($v < s / c$ for CAUTION) and `range` rules (inner bands around range_min/range_max). Warmup-exempt rules are skipped for the first $w$ steps.

Control clamping is a simple per-variable projection:

$$
u' = \max(l_{\min}, \min(l_{\max}, u))
$$

where limits come from the `control_limits` section of the manifest.

# Software description and functionality

## Core architecture

PAMCL follows a clean separation of concerns:

- `pamcl.protocols`: Three `@runtime_checkable` Protocols (`Agent`, `SetpointReceiver`, `ConstraintAwareAgent`).
- `pamcl.constraints`: `ConstraintRule`, `ConstraintEvaluator` (with warmup counter and severity logic), `ControlClamper`, and `ConstraintStatus`.
- `pamcl.loader`: `load_composition(path)`, `validate(manifest)`, `reload_constraints_from_yaml(path)`. Performs static validation (including `apiVersion`/`kind`, agent class importability, deep `control_limits` checks) and instantiates agents.
- `pamcl.scheduler`: `CompositionScheduler` implementing two-tier control (slow coordinator + fast sub-agents), setpoint dispatch via `update_setpoints()`, constraint evaluation, clamping, shadow mode, and hot-reload that preserves the evaluator's internal step count.
- `pamcl.audit`: `AuditLogger` producing append-only JSON Lines with events for setpoint changes, violations, mode transitions, human intervention, shadow controls, and config reloads. Provides `read_all()` / `read_by_type()` with resilience to malformed lines.
- `pamcl.dashboard`: Self-contained HTTP server rendering an interactive audit log viewer (severity charts, filtering, pagination).

A complete manifest (`apiVersion: pamcl/v2`) declares:
- `agents` (with `id`, `role`, `class`, `config`)
- `constraints` (rules + `warmup_steps`)
- `control_limits`
- `scheduling` (coordinator/sub-agents, intervals, `setpoint_dispatch`)

## Key features

- **Zero-dependency agent integration**: Any class implementing the required methods works; no PAMCL import or base class is needed.
- **Full constraint coverage**: Symmetric CAUTION logic for all rule types; warmup exemption; hot-reload preserves progress.
- **Safe experimentation**: `shadow_mode=True` executes everything and logs normally but returns no actuation.
- **Audit completeness**: Every control-relevant event is recorded with timestamps, agent ids, old/new values, reasons, and shadow flags.
- **Production-friendly hot-reload**: `scheduler.reload_constraints()` swaps the evaluator and clamper at runtime without restarting agents or losing warmup state.

# Example usage

```python
from pamcl import load_composition, CompositionScheduler, AuditLogger

comp = load_composition("compositions/slag_grinding_flotation.yaml")

audit = AuditLogger("logs/audit.jsonl")
scheduler = CompositionScheduler(
    agents=comp["agents"],
    scheduling=comp["scheduling"],
    constraint_evaluator=comp["constraint_evaluator"],
    control_clamper=comp["control_clamper"],
    audit_logger=audit,
    manifest_path=comp["manifest_path"],
)

plant = YourPlant()  # any object with get_obs() and step(**controls)
plant.reset()

for _ in range(480):
    controls = scheduler.step(plant.get_obs(), metrics)
    plant_controls = {k: v for k, v in controls.items() if k in PLANT_KEYS}
    _, metrics = plant.step(**plant_controls)

audit.close()
```

A minimal valid manifest excerpt:

```yaml
apiVersion: pamcl/v2
kind: Composition
agents:
  - id: coordinator
    role: coordinator
    class: mypackage.CoordinatorAgent
    config: { target_feed_rate: 66.0 }
  - id: grinding
    role: equipment
    class: vendor.grinding.GindingAgent
constraints:
  warmup_steps: 60
  rules:
    - id: p80_upper
      variable: P80
      type: max
      soft_limit: 67.0
      hard_limit: 72.0
scheduling:
  coordinator_id: coordinator
  sub_agents: [grinding]
  coordinator_interval_steps: 10
  setpoint_dispatch:
    grinding:
      feed_rate_target: feed_target_tph
```

CLI usage:

```bash
python -m pamcl validate compositions/slag_grinding_flotation.yaml
python -m pamcl inspect  compositions/slag_grinding_flotation.yaml
python -m pamcl dashboard logs/audit.jsonl
```

# Implementation

PAMCL is implemented in pure Python (requires Python ≥ 3.11). The public API is exported via `pamcl/__init__.py`. All core logic is contained in six small modules with clear responsibilities. The constraint evaluator maintains an internal step counter that is intentionally preserved across hot-reloads (via private attribute transfer in the scheduler). Audit logging is append-only with immediate `flush()` after every write. The dashboard is a single self-contained HTML+JS string served by a minimal `http.server` handler (no external dependencies).

The package is structured for easy extension: new constraint types require only a new branch in `_evaluate_rule`; new event types are added by implementing a `log_*` method on `AuditLogger`.

# Quality and testing

- 121+ passing tests covering protocols, constraint logic (including all CAUTION paths), loader validation (including new `apiVersion`/`kind` and deep `control_limits` checks), scheduler timing/dispatch/reload semantics, shadow mode, audit read/write resilience, and CLI.
- Comprehensive documentation: `docs/manifest-spec.md`, `docs/api-reference.md`, `docs/integration.md`, detailed README with architecture diagram and runnable examples.
- `CHANGELOG.md` and `FIXES_FROM_REVIEW.md` document the v0.4.0 refinements (full CAUTION support, reload semantics, validation hardening, audit robustness, dashboard escaping).
- The software is released under an OSI-approved license and hosted publicly on GitHub.

# Related work

General-purpose multi-agent platforms such as JADE (Java), SPADE (Python), and Jadex have been successfully applied to industrial control and manufacturing, including real deployments (e.g., Jadex in waste incineration). These systems excel at agent communication, behaviors, and distributed coordination but do not provide a control-specific declarative layer for physical constraints, two-tier scheduling with setpoint mapping, or built-in structured audit of control actions.

The `mango` framework (OFFIS) is the closest open-source Python peer, explicitly positioned for bridging agent-based simulation and industrial control. It offers strong messaging and role-based structuring but lacks PAMCL's YAML-driven constraint engine, hot-reload semantics, and control-oriented audit model.

Holonic and multi-agent manufacturing control has a long research tradition (HoloMAS series, Rockwell Automation's extensive holonic/agent-based PLC integration work). These approaches are typically more tightly coupled to specific hardware or discrete manufacturing contexts.

Commercial "Agentic Operations" platforms such as XMPro MAGS target similar industrial use cases with strong emphasis on audit, bounded autonomy, and OT/IT integration. PAMCL differs by being fully open-source, extremely lightweight (single PyYAML dependency), and centered on a declarative physical-constraint meta-layer rather than generative LLM-heavy agents.

Recent academic work has explored LLM-based multi-agent frameworks for autonomous plant control (often with digital twins and validator/reprompter loops). PAMCL is complementary: it provides the deterministic, auditable, constraint-enforcing substrate on top of which such higher-level agents could operate.

PAMCL's contribution is the pragmatic synthesis of these ideas into a minimal, engineer-friendly, production-oriented meta-control layer specifically for continuous physical processes.

# Acknowledgements

This work was developed within the PICCS-SF (Physical Intelligent Control & Cyber-Physical Systems) research program. We thank the internal reviewers who performed the comprehensive code review that led to the v0.4.0 robustness improvements.

# References

(no specific numbered references required by JOSS format beyond inline citations above; full prior-art discussion is in the paper body and project documentation)

---

**Repository**: https://github.com/thinkmachine/PAMCL (replace with final URL when published)

**License**: OSI-approved (to be specified in the repository, e.g. Apache-2.0)

**Version**: 0.4.0 (as of submission)

**Tests**: 121+ passing

**Documentation**: https://github.com/thinkmachine/PAMCL/tree/main/docs

**CLI**: `python -m pamcl --help`

**Citation**: Please cite this JOSS paper once published. A BibTeX entry will be generated by the JOSS system.