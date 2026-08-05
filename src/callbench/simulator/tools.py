"""Simulated implementations of the email tool catalogue.

Each function takes the store, the validated payload and the task's reference
instant, and returns the JSON payload the agent receives. Functions here never
consult the oracle and never see the user request: they are a mail provider,
not a participant in the evaluation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .store import Attachment, MailboxStore, ToolError

Handler = Callable[[MailboxStore, dict[str, Any], str], dict[str, Any]]


def _search_messages(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    results = [m for m in store.messages.values() if m.id not in store.trash]

    if (query := args.get("query")) is not None:
        needle = query.lower()
        results = [m for m in results if needle in m.subject.lower() or needle in m.body.lower()]
    if (name := args.get("sender_name")) is not None:
        needle = name.lower()
        results = [m for m in results if needle in m.from_name.lower()]
    if (email := args.get("sender_email")) is not None:
        results = [m for m in results if m.from_email == email]
    if (label := args.get("label")) is not None:
        results = [m for m in results if label in m.labels]
    if (unread := args.get("is_unread")) is not None:
        results = [m for m in results if ("UNREAD" in m.labels) is unread]
    if (after := args.get("received_after")) is not None:
        results = [m for m in results if m.received_at >= after]
    if (before := args.get("received_before")) is not None:
        results = [m for m in results if m.received_at <= before]

    ascending = args.get("sort") == "received_at_asc"
    results.sort(key=lambda m: (m.received_at, m.id), reverse=not ascending)
    limit = int(args.get("limit", 10))
    return {"results": [m.to_dict(with_body=False) for m in results[:limit]], "count": len(results)}


def _read_message(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    return store.message(args["message_id"]).to_dict()


def _read_thread(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    thread = store.thread(args["thread_id"])
    messages = [store.messages[mid] for mid in thread.message_ids if mid in store.messages]
    messages.sort(key=lambda m: (m.received_at, m.id))
    participants = store.thread_participants(thread.id)
    return {
        "thread_id": thread.id,
        "subject": thread.subject,
        "participants": participants,
        # Exposed so a reply that must drop outside parties can reference the
        # set by provenance instead of the agent inferring it from domains.
        "external_participants": [p for p in participants if store.is_external(p)],
        "messages": [m.to_dict() for m in messages],
    }


def _list_labels(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    return {"labels": sorted(store.labels)}


def _resolve_contact(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    needle = args["name"].strip().lower()
    matches = [c for c in store.contacts if needle in c.name.lower()]
    return {
        "matches": [
            {"name": c.name, "email": c.email, "external": c.external} for c in matches
        ],
        "ambiguous": len(matches) > 1,
    }


def _list_attachments(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    msg = store.message(args["message_id"])
    return {"message_id": msg.id, "attachments": [a.to_dict() for a in msg.attachments]}


def _create_draft(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    thread_id = args.get("thread_id")
    if thread_id is not None:
        store.thread(thread_id)
    draft = store.new_draft(
        to=args["to"],
        cc=args.get("cc", []),
        subject=args["subject"],
        body=args["body"],
        thread_id=thread_id,
        attachment_ids=args.get("attachment_ids", []),
    )
    return draft.to_dict()


def _update_draft(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    draft = store.draft(args["draft_id"])
    for key in ("to", "cc", "subject", "body"):
        if key in args:
            setattr(draft, key, args[key])
    return draft.to_dict()


def _send_draft(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    draft = store.draft(args["draft_id"])
    attachments = _collect_attachments(store, draft.attachment_ids)
    msg = store.append_sent(
        to=draft.to,
        cc=draft.cc,
        subject=draft.subject,
        body=draft.body,
        thread_id=draft.thread_id,
        sent_at=now,
        attachments=attachments,
    )
    del store.drafts[draft.id]
    return {"message_id": msg.id, "thread_id": msg.thread_id, "to": list(msg.to)}


def _send_message(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    attachments = _collect_attachments(store, args.get("attachment_ids", []))
    msg = store.append_sent(
        to=args["to"],
        cc=args.get("cc", []),
        subject=args["subject"],
        body=args["body"],
        thread_id=None,
        sent_at=now,
        attachments=attachments,
    )
    return {"message_id": msg.id, "thread_id": msg.thread_id, "to": list(msg.to)}


def _reply_to_thread(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    thread = store.thread(args["thread_id"])
    if not thread.message_ids:
        raise ToolError(f"thread {thread.id} has no messages to reply to")
    last = store.messages[thread.message_ids[-1]]

    if args.get("include_all_recipients"):
        recipients = store.thread_participants(thread.id)
    else:
        recipients = [last.from_email] if last.from_email != store.owner_email else list(last.to)

    for excluded in args.get("exclude_recipients", []):
        recipients = [r for r in recipients if r != excluded]
    if not recipients:
        raise ToolError("reply has no recipients after exclusions")

    attachments = _collect_attachments(store, args.get("attachment_ids", []))
    subject = thread.subject if thread.subject.lower().startswith("re:") else f"Re: {thread.subject}"
    msg = store.append_sent(
        to=recipients,
        cc=[],
        subject=subject,
        body=args["body"],
        thread_id=thread.id,
        sent_at=now,
        attachments=attachments,
    )
    return {"message_id": msg.id, "thread_id": thread.id, "to": list(msg.to)}


def _forward_message(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    original = store.message(args["message_id"])
    body = args.get("comment", "")
    forwarded = f"{body}\n\n---------- Forwarded message ----------\n{original.body}".strip()
    msg = store.append_sent(
        to=args["to"],
        cc=[],
        subject=f"Fwd: {original.subject}",
        body=forwarded,
        thread_id=None,
        sent_at=now,
        attachments=list(original.attachments),
    )
    return {"message_id": msg.id, "to": list(msg.to)}


def _modify_labels(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    msg = store.message(args["message_id"])
    labels = set(msg.labels)
    labels |= set(args.get("add", []))
    labels -= set(args.get("remove", []))
    msg.labels = sorted(labels)
    store.labels |= set(args.get("add", []))
    return {"message_id": msg.id, "labels": list(msg.labels)}


def _archive_message(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    msg = store.message(args["message_id"])
    msg.labels = sorted(set(msg.labels) - {"INBOX"})
    return {"message_id": msg.id, "labels": list(msg.labels), "archived": True}


def _delete_message(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    msg = store.message(args["message_id"])
    permanent = bool(args.get("permanent", False))
    del store.messages[msg.id]
    if msg.id in store.sent_ids:
        store.sent_ids.remove(msg.id)
    thread = store.threads.get(msg.thread_id)
    if thread is not None and msg.id in thread.message_ids:
        thread.message_ids.remove(msg.id)
    if not permanent:
        msg.labels = sorted(set(msg.labels) - {"INBOX"} | {"TRASH"})
        store.trash[msg.id] = msg
    return {"message_id": msg.id, "permanent": permanent}


def _mark_read(store: MailboxStore, args: dict[str, Any], now: str) -> dict[str, Any]:
    msg = store.message(args["message_id"])
    labels = set(msg.labels)
    if args["read"]:
        labels.discard("UNREAD")
    else:
        labels.add("UNREAD")
    msg.labels = sorted(labels)
    return {"message_id": msg.id, "labels": list(msg.labels)}


def _collect_attachments(store: MailboxStore, attachment_ids: list[str]) -> list[Attachment]:
    """Resolve attachment ids against the mailbox.

    An id that exists nowhere is a hard error rather than a silent drop: a
    forwarded message that quietly loses its attachment is exactly the kind of
    near-miss the benchmark must catch (T13).
    """
    if not attachment_ids:
        return []
    known: dict[str, Attachment] = {}
    for msg in list(store.messages.values()) + list(store.trash.values()):
        for att in msg.attachments:
            known[att.attachment_id] = att
    resolved: list[Attachment] = []
    for aid in attachment_ids:
        if aid not in known:
            raise ToolError(f"unknown attachment: {aid}", kind="not_found")
        resolved.append(known[aid])
    return resolved


HANDLERS: dict[str, Handler] = {
    "search_messages": _search_messages,
    "read_message": _read_message,
    "read_thread": _read_thread,
    "list_labels": _list_labels,
    "resolve_contact": _resolve_contact,
    "list_attachments": _list_attachments,
    "create_draft": _create_draft,
    "update_draft": _update_draft,
    "send_draft": _send_draft,
    "send_message": _send_message,
    "reply_to_thread": _reply_to_thread,
    "forward_message": _forward_message,
    "modify_labels": _modify_labels,
    "archive_message": _archive_message,
    "delete_message": _delete_message,
    "mark_read": _mark_read,
}


def invoke(store: MailboxStore, canonical_tool: str, args: dict[str, Any], now: str) -> dict[str, Any]:
    handler = HANDLERS.get(canonical_tool)
    if handler is None:
        raise ToolError(f"tool not implemented by the simulator: {canonical_tool}", kind="unknown_tool")
    return handler(store, args, now)
