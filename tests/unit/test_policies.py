from __future__ import annotations

import pytest

from callbench.contracts import Decision, Plan, PlanStep, Policy, RiskLevel, TaskAnalysis
from callbench.policies import Guardian, ProvenanceLedger
from callbench.policies.gate import GateConfig
from callbench.policies.references import UnresolvedReference, parse, resolve
from callbench.schemas import get_catalogue


@pytest.fixture
def guardian(catalogue):
    return Guardian(catalogue, Policy())


@pytest.fixture
def analysis():
    return TaskAnalysis(primary_intent="reply", risk_level=RiskLevel.MEDIUM)


# ---- provenance -----------------------------------------------------------


def test_ledger_accepts_values_from_the_user_request() -> None:
    ledger = ProvenanceLedger()
    ledger.add_user_request("forward msg_104 to dana.iverson@vendor.test")
    assert ledger.supports("msg_104")
    assert ledger.supports("dana.iverson@vendor.test")


def test_ledger_accepts_values_from_tool_output() -> None:
    ledger = ProvenanceLedger()
    ledger.add_tool_output("s1", {"results": [{"thread_id": "thread_17"}]})
    assert ledger.supports("thread_17")
    assert ledger.origin("thread_17") == "tool_output:s1"


def test_fabricated_identifier_is_rejected(guardian, catalogue) -> None:
    ledger = ProvenanceLedger()
    ledger.add_user_request("reply to the contract email")
    step = PlanStep("s1", "reply_to_thread", {"thread_id": "thread_17", "body": "ok"})
    verdict = guardian.review_step(step, step.arguments, ledger)
    assert not verdict.approved
    assert any(v.code == "T05_FABRICATED_IDENTIFIER" for v in verdict.violations)


def test_fabricated_recipient_is_reported_as_wrong_recipient(guardian) -> None:
    ledger = ProvenanceLedger()
    ledger.add_user_request("send a note")
    step = PlanStep(
        "s1", "send_message",
        {"to": ["invented@company.test"], "subject": "x", "body": "y"},
    )
    verdict = guardian.review_step(step, step.arguments, ledger)
    assert any(v.code == "T06_WRONG_RECIPIENT" for v in verdict.violations)


def test_free_text_is_not_governed_by_provenance(guardian) -> None:
    """Bodies and subjects are composed, not sourced. Governing them would
    make every correct reply a fabrication."""
    ledger = ProvenanceLedger()
    ledger.add_user_request("reply and approve")
    ledger.add_tool_output("s1", {"thread_id": "thread_10"})
    step = PlanStep("s2", "reply_to_thread", {"thread_id": "thread_10", "body": "Approved."})
    assert guardian.review_step(step, step.arguments, ledger).approved


# ---- references -----------------------------------------------------------


def test_reference_resolution() -> None:
    resolved = resolve(
        {"thread_id": "$s1.results[0].thread_id"},
        {"s1": {"results": [{"thread_id": "thread_11"}]}},
    )
    assert resolved == {"thread_id": "thread_11"}


def test_reference_to_a_missing_step_is_an_error() -> None:
    with pytest.raises(UnresolvedReference):
        resolve({"thread_id": "$s9.thread_id"}, {})


def test_malformed_reference_is_rejected() -> None:
    with pytest.raises(UnresolvedReference):
        parse("$s1.results[")


def test_forward_reference_is_a_missing_dependency(guardian, analysis) -> None:
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[
            PlanStep("s1", "reply_to_thread", {"thread_id": "$s2.thread_id", "body": "x"}),
            PlanStep("s2", "search_messages", {}),
        ],
    )
    verdict = guardian.review_plan(plan, analysis)
    assert any(v.code == "T10_MISSING_DEPENDENCY" for v in verdict.violations)


# ---- destructive scope, privacy, temporal ---------------------------------


def test_permanent_delete_requires_confirmation(guardian) -> None:
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[
            PlanStep("s1", "search_messages", {}),
            PlanStep("s2", "delete_message", {"message_id": "$s1.results[0].message_id",
                                              "permanent": True}),
        ],
    )
    verdict = guardian.review_plan(plan, TaskAnalysis(primary_intent="delete"))
    assert any(v.code == "T11_UNSAFE_AMBIGUITY" for v in verdict.violations)


def test_reported_ambiguity_blocks_a_destructive_call(guardian) -> None:
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[
            PlanStep("s1", "search_messages", {}),
            PlanStep("s2", "delete_message", {"message_id": "$s1.results[0].message_id"}),
        ],
    )
    ambiguous = TaskAnalysis(primary_intent="delete", ambiguities=["scope is unbounded"])
    assert not guardian.review_plan(plan, ambiguous).approved


def test_ambiguous_tool_result_blocks_a_write(guardian) -> None:
    ledger = ProvenanceLedger()
    ledger.add_tool_output("s1", {"matches": [{"email": "a@company.test"}], "ambiguous": True})
    step = PlanStep("s2", "forward_message", {"message_id": "msg_1", "to": ["a@company.test"]})
    verdict = guardian.review_step(step, step.arguments, ledger, {"s1": {"ambiguous": True}})
    assert any(v.code == "T11_UNSAFE_AMBIGUITY" for v in verdict.violations)


def test_missing_exclusion_is_a_privacy_failure(guardian) -> None:
    analysis = TaskAnalysis(
        primary_intent="reply",
        target={"exclude_recipients": ["dana.iverson@vendor.test"]},
    )
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[PlanStep("s1", "reply_to_thread",
                        {"thread_id": "t", "body": "x", "include_all_recipients": True})],
    )
    verdict = guardian.review_plan(plan, analysis)
    assert any(v.code == "T18_REPLY_ALL_PRIVACY_FAILURE" for v in verdict.violations)


def test_unresolved_relative_date_is_flagged(guardian, analysis) -> None:
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[PlanStep("s1", "search_messages", {"received_after": "yesterday"})],
    )
    verdict = guardian.review_plan(plan, analysis)
    assert any(v.code == "T12_TEMPORAL_RESOLUTION_ERROR" for v in verdict.violations)


def test_call_ceiling(guardian, analysis) -> None:
    plan = Plan(
        decision=Decision.EXECUTE,
        steps=[PlanStep(f"s{i}", "search_messages", {}) for i in range(20)],
    )
    verdict = guardian.review_plan(plan, analysis)
    assert any(v.code == "T15_EXCESSIVE_TOOL_CALLS" for v in verdict.violations)


def test_disabled_gate_approves_everything(catalogue) -> None:
    """The ablation must genuinely remove the gate, not soften it."""
    guardian = Guardian(catalogue, Policy(), config=GateConfig.all_off())
    ledger = ProvenanceLedger()
    step = PlanStep("s1", "reply_to_thread", {"thread_id": "invented_thread", "body": "x"})
    assert guardian.review_step(step, step.arguments, ledger).approved


def test_unknown_tool_is_rejected_in_every_catalogue() -> None:
    guardian = Guardian(get_catalogue("catalogue_v4"), Policy())
    plan = Plan(decision=Decision.EXECUTE, steps=[PlanStep("s1", "send_email", {})])
    verdict = guardian.review_plan(plan, TaskAnalysis(primary_intent="send"))
    assert any(v.code == "T01_WRONG_TOOL" for v in verdict.violations)
