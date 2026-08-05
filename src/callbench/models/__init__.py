"""Model backends: deterministic reference planner and Claude."""

from __future__ import annotations

from .base import ANALYSIS_SCHEMA, PLAN_SCHEMA, Backend, RepairRequest, Usage
from .reference import Profile, ReferenceBackend, ReferenceConfig

__all__ = [
    "ANALYSIS_SCHEMA",
    "PLAN_SCHEMA",
    "Backend",
    "Profile",
    "ReferenceBackend",
    "ReferenceConfig",
    "RepairRequest",
    "Usage",
    "build_backend",
]


def build_backend(model: str, *, profile: str = "full", effort: str = "high") -> Backend:
    """Resolve a ``--model`` string to a backend.

    ``reference`` (optionally ``reference:<profile>``) selects the offline
    planner. A ``+tooluse`` suffix selects the native tool-use adapter rather
    than the structured-output one — two independent implementations of the
    same contract. Anything else is a Claude model id on the default adapter.
    """
    if model == "reference" or model.startswith("reference:"):
        chosen = model.split(":", 1)[1] if ":" in model else profile
        return ReferenceBackend(ReferenceConfig(profile=Profile(chosen)))

    if model.endswith("+tooluse"):
        from .anthropic_tools import AnthropicToolUseBackend, ToolUseConfig

        return AnthropicToolUseBackend(
            ToolUseConfig(model=model.removesuffix("+tooluse"), effort=effort)
        )

    from .anthropic_backend import AnthropicBackend, AnthropicConfig

    return AnthropicBackend(AnthropicConfig(model=model, effort=effort))
