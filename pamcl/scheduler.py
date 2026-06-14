"""
Composition Scheduler — Declarative Agent scheduling for PAMCL v2.

Schedules any agents that satisfy the PAMCL Protocol interfaces.
No dependency on any specific simulator or agent framework.

The scheduler preserves a two-tier control flow:
  1. Coordinator decides at slow cycle (every N sub-steps)
  2. Coordinator dispatches setpoints to sub-agents
  3. Sub-agents decide at fast cycle (every step)
  4. ControlClamper enforces physical limits
  5. ConstraintEvaluator checks constraint violations
  6. Audit logger records setpoint changes and violations

Shadow Mode (v0.3):
  When shadow_mode=True, the scheduler runs all agents and evaluates
  constraints normally, but step() returns an empty dict instead of
  agent controls. All audit records are tagged with shadow=True.
  This allows validating new agent compositions against live plant
  data without affecting production.
"""

from typing import Any, Dict, List, Optional

from pamcl.protocols import Agent, SetpointReceiver, ConstraintAwareAgent
from pamcl.constraints import (
    ConstraintEvaluator,
    ConstraintSeverity,
    ConstraintStatus,
    ControlClamper,
)
from pamcl.audit import AuditLogger
from pamcl.loader import reload_constraints_from_yaml


class CompositionScheduler:
    """
    Declarative agent scheduler driven by a Composition Manifest.

    Manages the interaction between a coordinator agent and sub-agents
    at their respective time scales. Uses Protocol-based interfaces so
    any agent implementation (from any vendor) can be integrated without
    modification or inheritance.

    Parameters
    ----------
    agents : dict[str, Agent]
        agent_id → agent_instance (from loader.load_composition)
    scheduling : dict
        scheduling config block from the manifest
    constraint_evaluator : ConstraintEvaluator
        PAMCL generic constraint evaluator
    control_clamper : ControlClamper
        PAMCL control variable clamper
    audit_logger : AuditLogger
        Structured audit logger
    shadow_mode : bool
        If True, run agents and log everything, but return empty
        controls (no actuation). Default: False.
    manifest_path : str | None
        Path to the original manifest file. Used by reload_constraints().
        Returned in the dict from load_composition() and should be passed to the constructor.
    """

    def __init__(
        self,
        agents: Dict[str, Any],
        scheduling: dict,
        constraint_evaluator: ConstraintEvaluator,
        control_clamper: ControlClamper,
        audit_logger: AuditLogger,
        shadow_mode: bool = False,
        manifest_path: str | None = None,
    ):
        self.agents = agents
        self.scheduling = scheduling
        self.constraint_eval = constraint_evaluator
        self.clamper = control_clamper
        self.audit = audit_logger
        self.shadow_mode = shadow_mode
        self._manifest_path = manifest_path

        # Unpack scheduling config
        self.coordinator_id: str = scheduling["coordinator_id"]
        self.sub_agent_ids: List[str] = scheduling["sub_agents"]
        self.coord_interval: int = scheduling["coordinator_interval_steps"]
        self.setpoint_dispatch: dict = scheduling.get("setpoint_dispatch", {})

        # Direct references
        self.coordinator = agents[self.coordinator_id]
        self.sub_agents = {aid: agents[aid] for aid in self.sub_agent_ids}

        # State
        self._step_count: int = 0
        self._last_coordinator_output: Dict[str, Any] = {}
        self._last_constraint_status: ConstraintStatus = ConstraintStatus()
        self._last_mode: Optional[int] = None

    @property
    def step_count(self) -> int:
        return self._step_count

    def is_coordinator_step(self) -> bool:
        """Whether the current step is a coordinator decision point."""
        if self._step_count == 0:
            return False  # no step yet
        return (
            self._step_count == 1
            or self._step_count % self.coord_interval == 1
        )

    def step(
        self,
        plant_state: Dict[str, Any],
        metrics: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Execute one scheduling cycle.

        Flow:
          1. If coordinator decision point → coordinator.observe → act
          2. Dispatch coordinator setpoints to sub-agents
          3. Execute all sub-agents → merge control outputs
          4. ControlClamper enforces physical limits
          5. ConstraintEvaluator checks violations
          6. Audit logging for changes and violations

        Parameters
        ----------
        plant_state : dict
            Current plant observations.
        metrics : dict
            Metrics from the previous plant step. Can be {} on first step.

        Returns
        -------
        controls : dict[str, float]
            Merged and clamped control output.
            Empty dict if shadow_mode is True.
        """
        self._step_count += 1

        # ── 1. Coordinator decision (slow cycle) ──
        if self._step_count == 1 or self._step_count % self.coord_interval == 1:
            self._run_coordinator(plant_state, metrics)

        # ── 2. Sub-agent decisions (fast cycle) ──
        controls = self._run_sub_agents(plant_state)

        # ── 3. Control clamping (physical limits) ──
        controls = self.clamper.clamp(controls)

        # ── 4. Constraint evaluation ──
        if metrics:
            self._last_constraint_status = self.constraint_eval.check(metrics)

        # ── 5. Audit constraint violations ──
        if self._last_constraint_status.violations:
            self.audit.log_constraint_violation(
                severity=self._last_constraint_status.severity.name,
                violations=list(self._last_constraint_status.violations),
                shadow=self.shadow_mode,
            )

        # ── 6. Shadow mode: log controls but return empty ──
        if self.shadow_mode:
            self.audit.log_shadow_controls(
                step=self._step_count,
                controls=controls,
            )
            return {}

        return controls

    def _run_coordinator(
        self,
        plant_state: Dict[str, Any],
        metrics: Dict[str, Any],
    ):
        """Execute the coordinator agent and dispatch setpoints."""
        # Let coordinator evaluate constraints if it supports it
        if metrics and isinstance(self.coordinator, ConstraintAwareAgent):
            self.coordinator.update_constraint_status(metrics)

        # Coordinator observe → act
        coord_obs = self.coordinator.observe(plant_state)
        coord_action = self.coordinator.act(coord_obs)

        # Audit: setpoint changes
        for key, new_val in coord_action.items():
            old_val = self._last_coordinator_output.get(key)
            if old_val is not None and old_val != new_val:
                self.audit.log_setpoint_change(
                    agent_id=self.coordinator_id,
                    variable=key,
                    old_value=old_val,
                    new_value=new_val,
                    reason=f"mode={coord_action.get('mode', '?')}",
                    shadow=self.shadow_mode,
                )

        # Audit: mode transitions
        current_mode = coord_action.get("mode")
        if self._last_mode is not None and current_mode != self._last_mode:
            self.audit.log_mode_transition(
                from_mode=str(self._last_mode),
                to_mode=str(current_mode),
                reason="coordinator_policy",
                shadow=self.shadow_mode,
            )
        self._last_mode = current_mode

        self._last_coordinator_output = dict(coord_action)

        # Dispatch setpoints to sub-agents
        self._dispatch_setpoints(coord_action)

    def _run_sub_agents(
        self,
        plant_state: Dict[str, Any],
    ) -> Dict[str, float]:
        """Execute all sub-agents and merge their control outputs."""
        controls: Dict[str, float] = {}
        for agent_id in self.sub_agent_ids:
            agent = self.sub_agents[agent_id]
            obs = agent.observe(plant_state)
            action = agent.act(obs)
            controls.update(action)
        return controls

    def _dispatch_setpoints(self, coord_action: Dict[str, Any]):
        """
        Dispatch coordinator output to sub-agents via update_setpoints().

        Uses Protocol check: only dispatches if the agent implements
        SetpointReceiver (i.e., has update_setpoints method).

        The mapping is defined in scheduling.setpoint_dispatch:
          {agent_id: {coordinator_key: agent_param_name, ...}, ...}
        """
        for agent_id, mapping in self.setpoint_dispatch.items():
            agent = self.sub_agents.get(agent_id)
            if agent is None:
                continue
            kwargs: Dict[str, Any] = {}
            for coord_key, agent_key in mapping.items():
                if coord_key in coord_action:
                    kwargs[agent_key] = coord_action[coord_key]
            if kwargs and isinstance(agent, SetpointReceiver):
                agent.update_setpoints(**kwargs)

    def reset(self):
        """Reset all agents and scheduler state."""
        self._step_count = 0
        self._last_coordinator_output = {}
        self._last_constraint_status = ConstraintStatus()
        self._last_mode = None
        for agent in self.agents.values():
            agent.reset()
        self.constraint_eval.reset()

    def get_last_coordinator_output(self) -> Dict[str, Any]:
        """Return the last coordinator action for inspection."""
        return dict(self._last_coordinator_output)

    def reload_constraints(self, path: str | None = None) -> Dict[str, Any]:
        """
        Hot-reload constraints and control limits from YAML.

        Re-reads the manifest file, builds new ConstraintEvaluator and
        ControlClamper, and swaps them into the running scheduler.
        Does NOT re-instantiate agents or change scheduling.

        Warmup step counter is preserved so that warmup_exempt rules
        continue to behave correctly relative to the current episode
        progress. _last_constraint_status is reset (will be recomputed
        on the next step that supplies metrics).

        Parameters
        ----------
        path : str | None
            Path to the manifest file. If None, uses the original path
            from construction.

        Returns
        -------
        dict
            'constraint_rules': list of new rules
            'control_limits': dict of new limits
            'old_rules_count': previous rule count
            'new_rules_count': new rule count

        Raises
        ------
        ValueError
            If no manifest path is available.
        FileNotFoundError
            If the file does not exist.
        """
        target = path or self._manifest_path
        if target is None:
            raise ValueError(
                "No manifest path available. Pass path= argument or "
                "construct scheduler with manifest_path=."
            )

        old_rule_count = len(self.constraint_eval.rules)
        old_step_count = getattr(self.constraint_eval, "_step_count", 0)

        result = reload_constraints_from_yaml(target)

        new_evaluator = result["constraint_evaluator"]
        # Preserve warmup progress across hot-reload so that warmup_exempt rules
        # continue to be evaluated (or skipped) at the correct point in the episode.
        new_evaluator._step_count = old_step_count
        self.constraint_eval = new_evaluator
        self.clamper = result["control_clamper"]

        # Avoid carrying over a stale severity from the previous evaluator.
        # The next step() that receives metrics will recompute it.
        self._last_constraint_status = ConstraintStatus()

        if path:
            self._manifest_path = path

        self.audit.log_config_reload(
            old_rules=old_rule_count,
            new_rules=len(result["constraint_rules"]),
            source=str(target),
        )

        return {
            "constraint_rules": result["constraint_rules"],
            "control_limits": result["control_limits"],
            "old_rules_count": old_rule_count,
            "new_rules_count": len(result["constraint_rules"]),
        }
