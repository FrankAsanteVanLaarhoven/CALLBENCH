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
    planner. Anything else is treated as a Claude model id.
    """
    if model == "reference" or model.startswith("reference:"):
        chosen = model.split(":", 1)[1] if ":" in model else profile
        return ReferenceBackend(ReferenceConfig(profile=Profile(chosen)))

    from .anthropic_backend import AnthropicBackend, AnthropicConfig

    return AnthropicBackend(AnthropicConfig(model=model, effort=effort))
