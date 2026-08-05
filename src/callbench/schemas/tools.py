"""The tool schema registry.

Every tool is declared once, as a JSON Schema (Draft 2020-12) plus side-effect
metadata. Two properties are load-bearing for the benchmark:

* ``additionalProperties: false`` everywhere, so an undeclared argument is a
  schema failure (T04) rather than a silently ignored field;
* a declared :class:`SideEffect`, so the policy gate reasons about what a call
  *does* rather than what its name suggests.

The catalogue is supplied to the agent dynamically. Nothing in the pipeline may
assume a tool called ``send_email`` exists — ``catalogue_v4`` renames every
tool precisely to punish that assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import SideEffect

_EMAIL = {"type": "string", "format": "email"}
_EMAIL_LIST = {"type": "array", "items": _EMAIL, "minItems": 1}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    side_effect: SideEffect
    idempotent: bool
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()

    @property
    def is_write(self) -> bool:
        return self.side_effect is not SideEffect.NONE

    @property
    def is_destructive(self) -> bool:
        return self.side_effect is SideEffect.DESTRUCTIVE

    @property
    def is_send(self) -> bool:
        return self.side_effect is SideEffect.SEND


CANONICAL_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_messages",
        description=(
            "Search the mailbox. Returns matching message summaries in the "
            "requested order. Does not return message bodies; use the read tool "
            "for those."
        ),
        input_schema=_obj(
            {
                "query": {"type": "string", "description": "Free-text match over subject and body."},
                "sender_name": {"type": "string", "description": "Display-name fragment of the sender."},
                "sender_email": _EMAIL,
                "label": {"type": "string"},
                "is_unread": {"type": "boolean"},
                "received_after": {"type": "string", "format": "date-time"},
                "received_before": {"type": "string", "format": "date-time"},
                "sort": {"type": "string", "enum": ["received_at_desc", "received_at_asc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            [],
        ),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("messages",),
    ),
    ToolSpec(
        name="read_message",
        description="Read one message in full, including body, recipients and label set.",
        input_schema=_obj({"message_id": {"type": "string"}}, ["message_id"]),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("messages",),
    ),
    ToolSpec(
        name="read_thread",
        description=(
            "Read every message in a thread in chronological order, with the "
            "full participant set. Required before any reply that must preserve "
            "or filter thread membership."
        ),
        input_schema=_obj({"thread_id": {"type": "string"}}, ["thread_id"]),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("threads", "messages"),
    ),
    ToolSpec(
        name="list_labels",
        description="List every label defined in the mailbox.",
        input_schema=_obj({}, []),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("labels",),
    ),
    ToolSpec(
        name="resolve_contact",
        description=(
            "Resolve a display name to candidate email addresses. Returns every "
            "match; more than one match means the name is ambiguous."
        ),
        input_schema=_obj({"name": {"type": "string", "minLength": 1}}, ["name"]),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("contacts",),
    ),
    ToolSpec(
        name="list_attachments",
        description="List the attachments carried by a message.",
        input_schema=_obj({"message_id": {"type": "string"}}, ["message_id"]),
        side_effect=SideEffect.NONE,
        idempotent=True,
        reads=("messages",),
    ),
    ToolSpec(
        name="create_draft",
        description=(
            "Create an unsent draft. Nothing leaves the mailbox. Supply "
            "thread_id to draft a reply within an existing thread."
        ),
        input_schema=_obj(
            {
                "to": _EMAIL_LIST,
                "cc": {"type": "array", "items": _EMAIL},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "thread_id": {"type": "string"},
                "attachment_ids": {"type": "array", "items": {"type": "string"}},
            },
            ["to", "subject", "body"],
        ),
        side_effect=SideEffect.CREATE,
        idempotent=False,
        writes=("drafts",),
    ),
    ToolSpec(
        name="update_draft",
        description="Replace fields on an existing draft. Omitted fields are left unchanged.",
        input_schema=_obj(
            {
                "draft_id": {"type": "string"},
                "to": _EMAIL_LIST,
                "cc": {"type": "array", "items": _EMAIL},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            ["draft_id"],
        ),
        side_effect=SideEffect.MUTATE,
        idempotent=True,
        writes=("drafts",),
    ),
    ToolSpec(
        name="send_draft",
        description="Send an existing draft. This leaves the mailbox and cannot be undone.",
        input_schema=_obj({"draft_id": {"type": "string"}}, ["draft_id"]),
        side_effect=SideEffect.SEND,
        idempotent=False,
        writes=("sent", "drafts"),
    ),
    ToolSpec(
        name="send_message",
        description=(
            "Compose and send a new message immediately, outside any existing "
            "thread. This leaves the mailbox and cannot be undone."
        ),
        input_schema=_obj(
            {
                "to": _EMAIL_LIST,
                "cc": {"type": "array", "items": _EMAIL},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachment_ids": {"type": "array", "items": {"type": "string"}},
            },
            ["to", "subject", "body"],
        ),
        side_effect=SideEffect.SEND,
        idempotent=False,
        writes=("sent",),
    ),
    ToolSpec(
        name="reply_to_thread",
        description=(
            "Reply within an existing thread. By default the reply goes to the "
            "message sender only; set include_all_recipients to reply to every "
            "thread participant, and use exclude_recipients to drop specific "
            "addresses from that set."
        ),
        input_schema=_obj(
            {
                "thread_id": {"type": "string"},
                "body": {"type": "string"},
                "include_all_recipients": {"type": "boolean"},
                "exclude_recipients": {"type": "array", "items": _EMAIL},
                "attachment_ids": {"type": "array", "items": {"type": "string"}},
            },
            ["thread_id", "body"],
        ),
        side_effect=SideEffect.SEND,
        idempotent=False,
        writes=("sent", "threads"),
    ),
    ToolSpec(
        name="forward_message",
        description=(
            "Forward a message, with its attachments, to new recipients. This "
            "discloses the original content to parties who did not receive it."
        ),
        input_schema=_obj(
            {
                "message_id": {"type": "string"},
                "to": _EMAIL_LIST,
                "comment": {"type": "string"},
            },
            ["message_id", "to"],
        ),
        side_effect=SideEffect.SEND,
        idempotent=False,
        writes=("sent",),
    ),
    ToolSpec(
        name="modify_labels",
        description="Add and/or remove labels on a message.",
        input_schema=_obj(
            {
                "message_id": {"type": "string"},
                "add": {"type": "array", "items": {"type": "string"}},
                "remove": {"type": "array", "items": {"type": "string"}},
            },
            ["message_id"],
        ),
        side_effect=SideEffect.MUTATE,
        idempotent=True,
        writes=("messages",),
    ),
    ToolSpec(
        name="archive_message",
        description=(
            "Remove a message from the inbox while keeping it in the mailbox. "
            "Reversible: the message remains searchable."
        ),
        input_schema=_obj({"message_id": {"type": "string"}}, ["message_id"]),
        side_effect=SideEffect.MUTATE,
        idempotent=True,
        writes=("messages",),
    ),
    ToolSpec(
        name="delete_message",
        description=(
            "Move a message to trash, or erase it permanently when permanent is "
            "true. Permanent deletion cannot be undone."
        ),
        input_schema=_obj(
            {"message_id": {"type": "string"}, "permanent": {"type": "boolean"}},
            ["message_id"],
        ),
        side_effect=SideEffect.DESTRUCTIVE,
        idempotent=False,
        writes=("messages", "trash"),
    ),
    ToolSpec(
        name="mark_read",
        description="Set or clear the unread state of a message.",
        input_schema=_obj(
            {"message_id": {"type": "string"}, "read": {"type": "boolean"}},
            ["message_id", "read"],
        ),
        side_effect=SideEffect.MUTATE,
        idempotent=True,
        writes=("messages",),
    ),
)

# catalogue_v4 renames every tool without changing a single schema. An agent
# that has memorised tool names rather than reading the supplied catalogue
# fails the hidden partition and passes everywhere else, which is exactly the
# contamination signal the partition exists to produce.
V4_RENAMES: dict[str, str] = {
    "search_messages": "find_messages",
    "read_message": "open_message",
    "read_thread": "open_thread",
    "list_labels": "label_catalogue",
    "resolve_contact": "lookup_contact",
    "list_attachments": "enumerate_attachments",
    "create_draft": "compose_draft",
    "update_draft": "revise_draft",
    "send_draft": "dispatch_draft",
    "send_message": "compose_and_send",
    "reply_to_thread": "respond_in_thread",
    "forward_message": "relay_message",
    "modify_labels": "set_labels",
    "archive_message": "shelve_message",
    "delete_message": "discard_message",
    "mark_read": "set_read_state",
}


class Catalogue:
    """A named set of tools handed to the agent for one task."""

    def __init__(self, name: str, specs: dict[str, ToolSpec], canonical: dict[str, str]) -> None:
        self.name = name
        self._specs = specs
        # tool name as presented -> canonical name, for oracle comparison
        self._canonical = canonical
        self._alias = {v: k for k, v in canonical.items()}

    def __contains__(self, tool: str) -> bool:
        return tool in self._specs

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def spec(self, tool: str) -> ToolSpec:
        try:
            return self._specs[tool]
        except KeyError as exc:
            raise KeyError(f"tool {tool!r} is not in catalogue {self.name!r}") from exc

    def canonical(self, tool: str) -> str:
        """Map a presented tool name back to its canonical identity."""
        return self._canonical.get(tool, tool)

    def presented(self, canonical_name: str) -> str:
        """Map a canonical name to how this catalogue presents it."""
        return self._alias.get(canonical_name, canonical_name)

    def as_prompt_payload(self) -> list[dict[str, Any]]:
        """The catalogue exactly as the agent sees it. No side-effect hints."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in self._specs.values()
        ]


def _build(name: str, renames: dict[str, str] | None) -> Catalogue:
    specs: dict[str, ToolSpec] = {}
    canonical: dict[str, str] = {}
    for spec in CANONICAL_TOOLS:
        presented = (renames or {}).get(spec.name, spec.name)
        specs[presented] = ToolSpec(
            name=presented,
            description=spec.description,
            input_schema=spec.input_schema,
            side_effect=spec.side_effect,
            idempotent=spec.idempotent,
            reads=spec.reads,
            writes=spec.writes,
        )
        canonical[presented] = spec.name
    return Catalogue(name, specs, canonical)


CATALOGUES: dict[str, Catalogue] = {
    "catalogue_v1": _build("catalogue_v1", None),
    "catalogue_v4": _build("catalogue_v4", V4_RENAMES),
}


def get_catalogue(name: str) -> Catalogue:
    try:
        return CATALOGUES[name]
    except KeyError as exc:
        raise KeyError(f"unknown catalogue {name!r}; have {sorted(CATALOGUES)}") from exc
