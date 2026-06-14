"""
PAMCL Agent Protocols — structural typing for multi-vendor agent integration.

Third-party agents do NOT need to import this module or inherit from any base
class. They only need to implement the method signatures defined here.
Python's structural subtyping (typing.Protocol) handles the rest.

Protocol hierarchy:
    Agent                  — minimal interface: observe, act, reset
    SetpointReceiver       — optional: can receive coordinator setpoints
    ConstraintAwareAgent   — optional: can evaluate constraint status
"""

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class Agent(Protocol):
    """
    Minimal agent interface for PAMCL scheduling.

    Any object with observe(), act(), and reset() methods satisfies this
    Protocol — no inheritance or registration required.

    Methods
    -------
    observe(state) → obs
        Extract agent-relevant observations from plant state dict.
    act(obs) → controls
        Compute control outputs from observations.
    reset()
        Reset internal state for a new episode.
    """

    def observe(self, state: Dict[str, Any]) -> Any: ...
    def act(self, obs: Any) -> Dict[str, float]: ...
    def reset(self) -> None: ...


@runtime_checkable
class SetpointReceiver(Protocol):
    """
    Agent that can receive setpoint updates from a coordinator.

    The coordinator's output is mapped to update_setpoints() kwargs
    via the setpoint_dispatch config in the YAML manifest.
    """

    def update_setpoints(self, **kwargs: Any) -> None: ...


@runtime_checkable
class ConstraintAwareAgent(Protocol):
    """
    Agent that can evaluate constraint status from plant metrics.

    Typically implemented by coordinator-level agents that need
    to adjust their strategy based on constraint violations.
    The return value is opaque to PAMCL — the agent uses it internally.
    """

    def update_constraint_status(self, metrics: Dict[str, Any]) -> Any: ...
