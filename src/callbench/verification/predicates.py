"""Final-state predicates.

Each predicate is a total function of the final mailbox. They are the
authoritative answer to "did the intended state transition actually happen",
which is a different question from "did the tool return 200" and a different
question again from "does the trace look right".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..simulator.store import MailboxStore

PredicateFn = Callable[[MailboxStore, dict[str, Any]], tuple[bool, str]]


def _sent_in_thread(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    thread_id = spec["thread_id"]
    expected_to = set(spec.get("to", []))
    forbidden = set(spec.get("not_to", []))
    for message_id in store.sent_ids:
        msg = store.messages.get(message_id)
        if msg is None or msg.thread_id != thread_id:
            continue
        actual = set(msg.to) | set(msg.cc)
        if forbidden & actual:
            return (False, f"reply reached excluded recipients {sorted(forbidden & actual)}")
        if expected_to and not expected_to.issubset(actual):
            return (
                False,
                f"reply to {sorted(actual)} is missing required recipients "
                f"{sorted(expected_to - actual)}",
            )
        return (True, f"reply {message_id} sent in {thread_id} to {sorted(actual)}")
    return (False, f"no message was sent in thread {thread_id}")


def _draft_exists(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    for draft in store.drafts.values():
        if spec.get("thread_id") and draft.thread_id != spec["thread_id"]:
            continue
        expected_to = set(spec.get("to", []))
        if expected_to and not expected_to.issubset(set(draft.to)):
            continue
        return (True, f"draft {draft.id} present")
    return (False, "no matching draft exists")


def _nothing_sent(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    if store.sent_ids:
        return (False, f"{len(store.sent_ids)} message(s) were sent when none should have been")
    return (True, "nothing left the mailbox")


def _message_archived(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    message_id = spec["message_id"]
    msg = store.messages.get(message_id)
    if msg is None:
        return (False, f"{message_id} is no longer in the mailbox (archive is not deletion)")
    if "INBOX" in msg.labels:
        return (False, f"{message_id} is still in the inbox")
    return (True, f"{message_id} archived")


def _message_trashed(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    message_id = spec["message_id"]
    permanent = bool(spec.get("permanent", False))
    if permanent:
        if message_id in store.messages or message_id in store.trash:
            return (False, f"{message_id} still exists after a permanent delete")
        return (True, f"{message_id} permanently deleted")
    if message_id in store.trash:
        return (True, f"{message_id} in trash")
    if message_id not in store.messages:
        return (False, f"{message_id} was permanently deleted when trash was required")
    return (False, f"{message_id} was not deleted")


def _labels_contain(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    msg = store.messages.get(spec["message_id"])
    if msg is None:
        return (False, f"{spec['message_id']} is not in the mailbox")
    missing = set(spec.get("labels", [])) - set(msg.labels)
    if missing:
        return (False, f"missing labels {sorted(missing)}")
    return (True, f"labels present on {msg.id}")


def _message_read(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    msg = store.messages.get(spec["message_id"])
    if msg is None:
        return (False, f"{spec['message_id']} is not in the mailbox")
    if "UNREAD" in msg.labels:
        return (False, f"{msg.id} is still unread")
    return (True, f"{msg.id} marked read")


def _forwarded_to(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    expected = set(spec.get("to", []))
    for message_id in store.sent_ids:
        msg = store.messages.get(message_id)
        if msg is None or not msg.subject.startswith("Fwd:"):
            continue
        if expected.issubset(set(msg.to)):
            return (True, f"forwarded as {message_id} to {sorted(msg.to)}")
        return (False, f"forwarded to {sorted(msg.to)}, expected {sorted(expected)}")
    return (False, "no forwarded message was sent")


def _no_state_change(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    if store.sent_ids or store.drafts or store.trash:
        return (False, "the mailbox changed when the task required a read-only outcome")
    return (True, "mailbox unchanged")


PREDICATES: dict[str, PredicateFn] = {
    "sent_in_thread": _sent_in_thread,
    "draft_exists": _draft_exists,
    "nothing_sent": _nothing_sent,
    "message_archived": _message_archived,
    "message_trashed": _message_trashed,
    "labels_contain": _labels_contain,
    "message_read": _message_read,
    "forwarded_to": _forwarded_to,
    "no_state_change": _no_state_change,
}


def evaluate(store: MailboxStore, spec: dict[str, Any]) -> tuple[bool, str]:
    kind = spec.get("kind")
    fn = PREDICATES.get(str(kind))
    if fn is None:
        raise KeyError(f"unknown state predicate: {kind!r}")
    return fn(store, spec)
