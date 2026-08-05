"""The deterministic reference backend.

This is a rule-based planner, not a language model. It exists so that the
harness can be exercised end to end with no network, no credentials and no
sampling noise, and so that *architecture* ablations can be measured with the
planner held exactly fixed.

Read this before interpreting any number it produces
====================================================

Results from ``--model reference`` measure the **evaluation architecture**, not
a model. The planner's competence and its defect rates are prescribed by the
selected :class:`Profile`, which is why the reports label every reference run
``SYNTHETIC PLANNER`` and refuse to print a model comparison table for it.
Model comparison requires a real backend (``--model claude-opus-5``).

The profiles are deliberately crude representations of published baselines:

``GUESSING``
    No discovery. Emits literal identifiers it cannot know — the failure mode
    of direct tool calling against an unseen mailbox.
``SHALLOW``
    Searches, then acts. Skips the secondary reads (thread membership, contact
    resolution, attachment listing, condition checks), so it misses exclusions,
    ambiguity and conditions.
``FULL``
    Complete discovery chain with provenance-bearing references throughout.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..contracts import Decision, Plan, PlanStep, RiskLevel, TaskAnalysis, TaskContract
from ..schemas import Catalogue
from .base import RepairRequest, Usage


class Profile(StrEnum):
    GUESSING = "guessing"
    SHALLOW = "shallow"
    FULL = "full"


@dataclass(frozen=True)
class ReferenceConfig:
    profile: Profile = Profile.FULL
    #: When false, the planner emits a schema-invalid payload on a fixed,
    #: reproducible fraction of cases. Models the absence of constrained
    #: decoding. See the module docstring: this is a prescribed defect rate.
    strict_json: bool = True
    schema_defect_rate: float = 0.12


# Order matters: the first match wins, so the more specific intent has to be
# tested first. "Draft a reply to ..." is a draft; testing "reply" first would
# classify it as a send and turn a draft/send distinction task into a T07.
_INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bdraft\b|\bprepare a (?:reply|message)\b", "draft"),
    (r"\breply\b|\brespond\b|\bconfirm to\b", "reply"),
    (r"\bforward\b|\bpass (?:this |it )?on\b", "forward"),
    (r"\bpermanently delete\b|\bdelete\b|\bmove to trash\b|\btrash\b", "delete"),
    (r"\barchive\b", "archive"),
    (r"\blabel\b|\btag\b|\bfile (?:it|this) under\b", "label"),
    (r"\bmark\b[^.]*?\bas read\b|\bmark\b[^.]*?\bread\b", "mark_read"),
    (r"\bsend\b|\bcompose\b", "send"),
    (r"\bfind\b|\bsearch\b|\bshow me\b|\blist\b|\bwhat(?:'s| is)\b", "search"),
)

_SENDER_RE = re.compile(r"\bfrom ([A-Z][a-zà-ÿ]+(?: [A-Z][a-zà-ÿ]+)?)")
_RECIPIENT_RE = re.compile(r"\bto ([A-Z][a-zà-ÿ]+(?: [A-Z][a-zà-ÿ]+)?)")
#: The topic ends at the first clause boundary. A greedy match would swallow
#: the rest of the sentence ("the invoice 4471 and confirm that I approve") and
#: turn a search that should match into one that matches nothing.
_TOPIC_RE = re.compile(
    r"\b(?:about|regarding|on the subject of) (?:the )?([a-z0-9 ]{3,40}?)"
    r"(?=\s+(?:and|but|then|so|with|which)\b|\s*[,.;]|$)"
)
#: "reply to everyone on the q3 budget review thread" — the topic sits before
#: the word "thread" rather than after a preposition.
_THREAD_TOPIC_RE = re.compile(r"\bthe ([a-z0-9 ]{3,40}?) thread\b")
_EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[a-zA-Z]{2,}")
# Applied only once the intent is already known to be "label": the label name
# is the all-caps token in the request. Matching on the verb as well would need
# case-insensitivity, which would then also match ordinary words as labels.
_LABEL_RE = re.compile(r"\b([A-Z]{3,})\b")
_EXCLUDE_EXPLICIT_RE = re.compile(
    r"\b(?:exclude|without|except|but not|leaving out)\s+([^@\s,;]+@[^@\s,;.]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)
_EXCLUDE_DESCRIPTIVE_RE = re.compile(
    r"\b(?:exclude|without|except|but not|leaving out|keep out)\s+(?:the\s+)?"
    r"(external(?: vendor| party| participants?| recipients?)?|vendor|client|partner)",
    re.IGNORECASE,
)
_CONDITION_RE = re.compile(r"\b(?:only )?if\b|\bunless\b|\bprovided that\b", re.IGNORECASE)
_LATEST_RE = re.compile(r"\b(latest|most recent|last|newest)\b", re.IGNORECASE)
_OLDEST_RE = re.compile(r"\b(oldest|first|earliest)\b", re.IGNORECASE)
_ALL_RECIPIENTS_RE = re.compile(r"\breply all\b|\ball recipients\b|\beveryone (?:on|in)\b", re.IGNORECASE)
_ATTACHMENT_RE = re.compile(r"\battachment|attached\b", re.IGNORECASE)
_PERMANENT_RE = re.compile(r"\bpermanently\b|\bfor good\b|\bforever\b", re.IGNORECASE)
_QUANTIFIER_RE = re.compile(r"\b(everything|every|all of|all the|each)\b", re.IGNORECASE)
_AMBIGUOUS_ADDRESSEE_RE = re.compile(r"\bto ([A-Z][a-zà-ÿ]+)\.?$")


class ReferenceBackend:
    """A deterministic planner over the supplied catalogue."""

    def __init__(self, config: ReferenceConfig | None = None) -> None:
        self.config = config or ReferenceConfig()
        self.name = f"reference:{self.config.profile.value}"
        self.usage = Usage()

    # ---- role 1: contract analyst ----------------------------------------

    def analyse(self, contract: TaskContract, catalogue: Catalogue) -> TaskAnalysis:
        self.usage.calls += 1
        text = contract.user_request
        intent = _detect_intent(text)

        target: dict[str, Any] = {}
        if (sender := _SENDER_RE.search(text)) is not None:
            target["sender"] = sender.group(1)
        topic = _TOPIC_RE.search(text) or _THREAD_TOPIC_RE.search(text)
        if topic is not None:
            target["topic"] = topic.group(1).strip()
        if _LATEST_RE.search(text):
            target["selection"] = "latest"
        elif _OLDEST_RE.search(text):
            target["selection"] = "oldest"
        if _ALL_RECIPIENTS_RE.search(text):
            target["include_all_recipients"] = True

        shallow = self.config.profile is not Profile.FULL
        if not shallow:
            explicit = _EXCLUDE_EXPLICIT_RE.findall(text)
            if explicit:
                target["exclude_recipients"] = explicit
            elif _EXCLUDE_DESCRIPTIVE_RE.search(text):
                target["exclude_description"] = "external"

        dependencies: list[str] = []
        if intent in {"reply", "forward", "draft", "archive", "delete", "label", "mark_read"}:
            dependencies.append("search_matching_messages")
        if target.get("selection") == "latest":
            dependencies.append("select_latest_message")
        if not shallow and intent == "reply":
            dependencies.append("resolve_thread_membership")

        risk = RiskLevel.LOW
        if intent in {"reply", "forward", "send", "draft"}:
            risk = RiskLevel.MEDIUM
        if intent == "delete" or _PERMANENT_RE.search(text):
            risk = RiskLevel.HIGH

        ambiguities: list[str] = []
        if not shallow:
            # "Delete everything from X" names no specific message. Executing it
            # against whichever message a search happened to rank first is a
            # guess with an irreversible consequence, so it is reported as an
            # ambiguity rather than resolved by preference.
            if intent in {"delete", "archive"} and _QUANTIFIER_RE.search(text):
                ambiguities.append(
                    "the destructive scope is unbounded: the request names a class of "
                    "messages, not a specific message"
                )
            if _AMBIGUOUS_ADDRESSEE_RE.search(text):
                ambiguities.append(
                    "the addressee is named by first name only and may match more than "
                    "one contact"
                )

        return TaskAnalysis(
            primary_intent=intent,
            target=target,
            requested_effect=_effect_clause(text),
            requires_existing_message=intent
            in {"reply", "forward", "archive", "delete", "label", "mark_read", "read"},
            dependencies=dependencies,
            ambiguities=ambiguities,
            risk_level=risk,
            execution_mode="tool_chain" if dependencies else "direct_call",
        )

    # ---- role 2: tool planner --------------------------------------------

    def plan(self, contract: TaskContract, catalogue: Catalogue, analysis: TaskAnalysis) -> Plan:
        self.usage.calls += 1
        steps = self._build_steps(contract, catalogue, analysis)
        if not steps:
            return Plan(
                decision=Decision.CLARIFY,
                clarification_question=(
                    "I could not determine which mailbox operation you want. "
                    "Could you restate the request?"
                ),
                rationale="no intent matched the supplied catalogue",
            )
        if not self.config.strict_json and self._should_inject_defect(contract.task_id):
            steps = _inject_schema_defect(steps)
        return Plan(
            decision=Decision.EXECUTE,
            steps=steps,
            rationale=f"{self.config.profile.value} plan for intent {analysis.primary_intent!r}",
        )

    # ---- role 6: failure analyst / bounded repair ------------------------

    def repair(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        previous: Plan,
        request: RepairRequest,
    ) -> Plan:
        """Repair by escalating discovery, never by changing the target.

        The forbidden-change list in :class:`RepairRequest` is what stops a
        repair loop from "fixing" a rejected send by sending it somewhere else.
        A guessing planner therefore repairs into a searching planner; it never
        repairs into a different recipient.
        """
        self.usage.calls += 1
        codes = set(request.violations)

        escalate = codes & {
            "T05_FABRICATED_IDENTIFIER",
            "T06_WRONG_RECIPIENT",
            "T10_MISSING_DEPENDENCY",
            "T13_ATTACHMENT_RESOLUTION_ERROR",
            "T18_REPLY_ALL_PRIVACY_FAILURE",
            "T14_CONDITION_NOT_CHECKED",
        }
        if escalate:
            promoted = ReferenceBackend(
                ReferenceConfig(profile=Profile.FULL, strict_json=True)
            )
            richer = promoted.analyse(contract, catalogue)
            plan = promoted.plan(contract, catalogue, richer)
            plan.rationale = f"repair: escalated discovery after {sorted(escalate)}"
            return plan

        if codes & {
            "T02_MISSING_REQUIRED_ARGUMENT",
            "T03_INVALID_ARGUMENT_TYPE",
            "T04_UNDECLARED_ARGUMENT",
        }:
            steps = self._build_steps(contract, catalogue, analysis)
            return Plan(
                decision=Decision.EXECUTE,
                steps=steps,
                rationale="repair: re-emitted payloads under strict schema constraints",
            )

        if codes & {"T11_UNSAFE_AMBIGUITY"}:
            return Plan(
                decision=Decision.CLARIFY,
                clarification_question=(
                    "This request is ambiguous in a way that would change what gets "
                    "modified. Which message or recipient did you mean?"
                ),
                rationale="repair: converted to clarification rather than guessing",
            )

        return Plan(
            decision=Decision.REFUSE,
            rationale=f"repair: unresolved failure {sorted(codes)}",
        )

    # ---- advisory judge ---------------------------------------------------

    def judge(self, contract: TaskContract, transcript: list[dict[str, Any]]) -> tuple[bool, str]:
        """The reference backend abstains. Abstention is honest; a guess is not."""
        return (True, "reference backend does not perform semantic judgement")

    # ---- plan construction ------------------------------------------------

    def _build_steps(
        self, contract: TaskContract, catalogue: Catalogue, analysis: TaskAnalysis
    ) -> list[PlanStep]:
        text = contract.user_request
        intent = analysis.primary_intent
        guessing = self.config.profile is Profile.GUESSING
        shallow = self.config.profile is not Profile.FULL

        def tool(canonical: str) -> str:
            return catalogue.presented(canonical)

        steps: list[PlanStep] = []
        counter = 0

        def add(canonical: str, arguments: dict[str, Any]) -> str:
            nonlocal counter
            counter += 1
            step_id = f"s{counter}"
            steps.append(PlanStep(step_id=step_id, tool=tool(canonical), arguments=arguments))
            return step_id

        if intent == "search":
            add("search_messages", _search_args(analysis, text))
            return steps

        if intent in {"send"} and not analysis.requires_existing_message:
            recipient = _explicit_recipient(text)
            if recipient is None and not guessing:
                name = _recipient_name(text)
                if name:
                    contact_step = add("resolve_contact", {"name": name})
                    recipient = f"${contact_step}.matches[0].email"
            if recipient is None:
                recipient = "unknown@company.test"
            add(
                "send_message",
                {
                    "to": [recipient],
                    "subject": _subject_for(text),
                    "body": _body_for(analysis, text),
                },
            )
            return steps

        # Every remaining intent operates on an existing message.
        if guessing:
            search_id = None
            message_ref = _guessed_message_id(contract.task_id)
            thread_ref = _guessed_thread_id(contract.task_id)
        else:
            search_id = add("search_messages", _search_args(analysis, text))
            message_ref = f"${search_id}.results[0].message_id"
            thread_ref = f"${search_id}.results[0].thread_id"

        thread_step: str | None = None
        if intent in {"reply", "draft"} and not shallow:
            thread_step = add("read_thread", {"thread_id": thread_ref})

        if _CONDITION_RE.search(text) and not shallow and search_id is not None:
            add("read_message", {"message_id": message_ref})

        if intent == "reply":
            arguments: dict[str, Any] = {"thread_id": thread_ref, "body": _body_for(analysis, text)}
            if analysis.target.get("include_all_recipients"):
                arguments["include_all_recipients"] = True
            explicit = analysis.target.get("exclude_recipients")
            if explicit:
                arguments["exclude_recipients"] = list(explicit)
            elif analysis.target.get("exclude_description") and thread_step:
                arguments["exclude_recipients"] = f"${thread_step}.external_participants"
            add("reply_to_thread", arguments)
            return steps

        if intent == "draft":
            recipients = (
                f"${thread_step}.participants" if thread_step else [_fallback_recipient(text)]
            )
            add(
                "create_draft",
                {
                    "to": recipients,
                    "subject": _subject_for(text),
                    "body": _body_for(analysis, text),
                    "thread_id": thread_ref,
                },
            )
            return steps

        if intent == "forward":
            recipient = _explicit_recipient(text)
            if recipient is None and not shallow:
                name = _recipient_name(text)
                if name:
                    contact_step = add("resolve_contact", {"name": name})
                    recipient = f"${contact_step}.matches[0].email"
            if recipient is None:
                recipient = _fallback_recipient(text)
            if _ATTACHMENT_RE.search(text) and not shallow:
                add("list_attachments", {"message_id": message_ref})
            add(
                "forward_message",
                {"message_id": message_ref, "to": [recipient], "comment": _body_for(analysis, text)},
            )
            return steps

        if intent == "archive":
            add("archive_message", {"message_id": message_ref})
            return steps

        if intent == "delete":
            arguments = {"message_id": message_ref}
            if _PERMANENT_RE.search(text):
                arguments["permanent"] = True
            add("delete_message", arguments)
            return steps

        if intent == "label":
            match = _LABEL_RE.search(text)
            label_name = str(match.group(1)) if match else "IMPORTANT"
            add("modify_labels", {"message_id": message_ref, "add": [label_name]})
            return steps

        if intent == "mark_read":
            add("mark_read", {"message_id": message_ref, "read": True})
            return steps

        return steps

    def _should_inject_defect(self, task_id: str) -> bool:
        digest = hashlib.sha256(f"defect:{task_id}".encode()).digest()
        bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        return bucket < self.config.schema_defect_rate


# ---- text helpers ---------------------------------------------------------


def _detect_intent(text: str) -> str:
    lowered = text.lower()
    for pattern, intent in _INTENT_PATTERNS:
        if re.search(pattern, lowered):
            return intent
    return "unknown"


def _search_args(analysis: TaskAnalysis, text: str) -> dict[str, Any]:
    args: dict[str, Any] = {"sort": "received_at_desc", "limit": 5}
    if (sender := analysis.target.get("sender")) is not None:
        args["sender_name"] = sender
    if (topic := analysis.target.get("topic")) is not None:
        args["query"] = topic
    if analysis.target.get("selection") == "oldest":
        args["sort"] = "received_at_asc"
    if re.search(r"\bunread\b", text, re.IGNORECASE):
        args["is_unread"] = True
    return args


def _effect_clause(text: str) -> str:
    match = re.search(
        r"\b(?:and|to)\s+(confirm[^.]*|approve[^.]*|decline[^.]*|acknowledge[^.]*)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else text.strip()


def _body_for(analysis: TaskAnalysis, text: str) -> str:
    effect = analysis.requested_effect or _effect_clause(text)
    topic = analysis.target.get("topic")
    if topic:
        return f"Following up on {topic}: {effect}."
    return f"{effect[0].upper()}{effect[1:]}." if effect else "Acknowledged."


def _subject_for(text: str) -> str:
    match = _TOPIC_RE.search(text)
    if match:
        topic = match.group(1).strip()
        return topic[0].upper() + topic[1:]
    return "Follow-up"


def _explicit_recipient(text: str) -> str | None:
    matches: list[str] = _EMAIL_RE.findall(text)
    excluded = set(_EXCLUDE_EXPLICIT_RE.findall(text))
    for candidate in matches:
        if candidate not in excluded:
            return candidate
    return None


def _recipient_name(text: str) -> str | None:
    match = _RECIPIENT_RE.search(text)
    if match is None:
        return None
    return str(match.group(1))


def _fallback_recipient(text: str) -> str:
    """A recipient with no provenance. Deliberately fabricated.

    Reached only when the planner has been denied the discovery step that would
    have produced a real address. The provenance gate rejects it, which is the
    behaviour under test.
    """
    name = _recipient_name(text) or "unknown"
    return f"{name.split()[0].lower()}@company.test"


def _guessed_message_id(task_id: str) -> str:
    digest = hashlib.sha256(f"guess-msg:{task_id}".encode()).digest()
    return f"msg_{101 + int.from_bytes(digest[:2], 'big') % 40}"


def _guessed_thread_id(task_id: str) -> str:
    digest = hashlib.sha256(f"guess-thread:{task_id}".encode()).digest()
    return f"thread_{10 + int.from_bytes(digest[:2], 'big') % 7}"


def _inject_schema_defect(steps: list[PlanStep]) -> list[PlanStep]:
    """Add an undeclared property to the final step (exercises T04)."""
    if not steps:
        return steps
    last = steps[-1]
    mutated = dict(last.arguments)
    mutated["priority"] = "high"
    return [*steps[:-1], PlanStep(step_id=last.step_id, tool=last.tool, arguments=mutated)]
