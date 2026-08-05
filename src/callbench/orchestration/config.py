"""System configurations: the baselines and the ablations.

Every row of the results table is one :class:`SystemConfig`. Encoding the
comparison as data rather than as separate code paths is what makes the
ablations honest — "no provenance" is the full system with exactly one flag
flipped, not a different program.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..policies.gate import GateConfig


@dataclass(frozen=True)
class SystemConfig:
    name: str
    description: str
    #: Run the contract analyst. Without it the planner receives a bare
    #: intent guess and never records exclusions or ambiguities.
    use_analyst: bool = True
    gate: GateConfig = GateConfig()
    #: Bounded repair budget. 0 disables the retry controller entirely.
    max_repairs: int = 2
    #: Whether the state-transition layer counts towards pass/fail.
    verify_state: bool = True
    #: Reference-planner competence profile. Ignored by model backends.
    reference_profile: str = "full"
    #: Constrained decoding for tool payloads.
    strict_json: bool = True
    #: Pass tool descriptions to the planner. Off = names and schemas only.
    normalise_tool_descriptions: bool = True
    #: Ask the model backend for an advisory semantic judgement (never
    #: authoritative; recorded alongside the deterministic layers).
    advisory_judge: bool = False


BASELINES: tuple[SystemConfig, ...] = (
    SystemConfig(
        name="direct_tool_calling",
        description="One shot, no discovery, no gate, no repair.",
        use_analyst=False,
        gate=GateConfig.all_off(),
        max_repairs=0,
        reference_profile="guessing",
        strict_json=False,
    ),
    SystemConfig(
        name="fewshot_tool_calling",
        description="Discovery then action, no gate, no repair.",
        use_analyst=False,
        gate=GateConfig.all_off(),
        max_repairs=0,
        reference_profile="shallow",
        strict_json=False,
    ),
    SystemConfig(
        name="structured_outputs",
        description="Constrained decoding for payloads; still no gate or repair.",
        use_analyst=False,
        gate=GateConfig.all_off(),
        max_repairs=0,
        reference_profile="shallow",
    ),
    SystemConfig(
        name="single_agent_planner",
        description="Analyst and planner fused, one repair, no deterministic gate.",
        use_analyst=True,
        gate=GateConfig.all_off(),
        max_repairs=1,
        reference_profile="shallow",
    ),
    SystemConfig(
        name="multi_agent_no_hooks",
        description="Full role separation, deterministic gate disabled.",
        use_analyst=True,
        gate=GateConfig.all_off(),
        max_repairs=2,
    ),
    SystemConfig(
        name="callbench_full",
        description="Analyst, planner, deterministic gate, executor, verifier, bounded repair.",
    ),
)

FULL = BASELINES[-1]

ABLATIONS: tuple[SystemConfig, ...] = (
    replace(
        FULL,
        name="ablate_schema_validator",
        description="Full system without payload schema validation at the gate.",
        gate=replace(FULL.gate, schema_validation=False),
    ),
    replace(
        FULL,
        name="ablate_provenance",
        description="Full system without provenance tracking.",
        gate=replace(FULL.gate, provenance=False),
    ),
    replace(
        FULL,
        name="ablate_policy_guardian",
        description="Full system with the deterministic gate removed entirely.",
        gate=GateConfig.all_off(),
    ),
    replace(
        FULL,
        name="ablate_state_verifier",
        description="Full system with the state-transition layer excluded from pass/fail.",
        verify_state=False,
    ),
    replace(
        FULL,
        name="ablate_retry_controller",
        description="Full system with bounded repair disabled.",
        max_repairs=0,
    ),
    replace(
        FULL,
        name="ablate_tool_description_normalisation",
        description="Full system with tool descriptions withheld from the planner.",
        normalise_tool_descriptions=False,
    ),
)

BY_NAME: dict[str, SystemConfig] = {
    config.name: config for config in (*BASELINES, *ABLATIONS)
}

#: Ablations whose effect cannot be observed under the deterministic reference
#: planner, because that planner does not consume the ablated signal. The runner
#: reports these as skipped rather than reporting a null result as a finding.
INAPPLICABLE_TO_REFERENCE: frozenset[str] = frozenset(
    {"ablate_tool_description_normalisation"}
)


def resolve_systems(names: list[str] | None) -> list[SystemConfig]:
    if not names:
        return list(BASELINES)
    if names == ["all"]:
        return [*BASELINES, *ABLATIONS]
    if names == ["ablations"]:
        return [FULL, *ABLATIONS]
    missing = [n for n in names if n not in BY_NAME]
    if missing:
        raise KeyError(f"unknown system(s): {missing}; have {sorted(BY_NAME)}")
    return [BY_NAME[n] for n in names]
