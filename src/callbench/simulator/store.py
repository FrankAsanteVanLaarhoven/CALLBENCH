"""The simulated mailbox.

Two properties matter more than fidelity:

* **Determinism.** The same fixture and the same tool sequence always produce
  the same state, so a case is replayable and a state hash is a meaningful
  identity.
* **Observability.** Every mutation emits a before/after hash and the exact set
  of changed resource paths, so the state-transition verifier can assert that
  the *correct* resource changed and *only* that resource changed. An agent
  that reaches the right final answer through a wrong side effect fails here.

No connector in this package can reach a real mail provider. The executor
refuses any tool that is not backed by this store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any


class ToolError(RuntimeError):
    """Raised by a simulated tool when the operation is not possible."""

    def __init__(self, message: str, *, kind: str = "invalid_operation") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    filename: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class Contact:
    name: str
    email: str
    external: bool = False


@dataclass
class Message:
    id: str
    thread_id: str
    from_email: str
    from_name: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    received_at: str
    labels: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    def to_dict(self, *, with_body: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message_id": self.id,
            "thread_id": self.thread_id,
            "from": self.from_email,
            "from_name": self.from_name,
            "to": list(self.to),
            "cc": list(self.cc),
            "subject": self.subject,
            "received_at": self.received_at,
            "labels": sorted(self.labels),
            "attachments": [a.to_dict() for a in self.attachments],
        }
        if with_body:
            payload["body"] = self.body
        else:
            payload["snippet"] = self.body[:80]
        return payload


@dataclass
class Draft:
    id: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    thread_id: str | None = None
    attachment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.id,
            "to": list(self.to),
            "cc": list(self.cc),
            "subject": self.subject,
            "body": self.body,
            "thread_id": self.thread_id,
            "attachment_ids": list(self.attachment_ids),
        }


@dataclass
class Thread:
    id: str
    subject: str
    message_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"thread_id": self.id, "subject": self.subject, "message_ids": list(self.message_ids)}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class MailboxStore:
    """An in-memory mailbox with content-addressed snapshots."""

    def __init__(
        self,
        *,
        fixture_id: str,
        owner_email: str,
        owner_name: str,
        messages: Iterable[Message] = (),
        threads: Iterable[Thread] = (),
        contacts: Iterable[Contact] = (),
        labels: Iterable[str] = (),
        internal_domain: str = "company.test",
    ) -> None:
        self.fixture_id = fixture_id
        self.owner_email = owner_email
        self.owner_name = owner_name
        self.internal_domain = internal_domain
        self.messages: dict[str, Message] = {m.id: m for m in messages}
        self.threads: dict[str, Thread] = {t.id: t for t in threads}
        self.drafts: dict[str, Draft] = {}
        self.trash: dict[str, Message] = {}
        self.sent_ids: list[str] = []
        self.contacts: list[Contact] = list(contacts)
        self.labels: set[str] = set(labels) | {"INBOX", "UNREAD", "SENT", "TRASH"}
        self._counter = 1000

    # ---- identity ---------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    # ---- snapshots and diffs ---------------------------------------------

    def snapshot(self) -> dict[str, str]:
        """Resource path -> canonical serialisation of that resource."""
        snap: dict[str, str] = {}
        for msg in self.messages.values():
            bucket = "sent" if msg.id in self.sent_ids else "message"
            snap[f"{bucket}/{msg.id}"] = _canon(msg.to_dict())
        for draft in self.drafts.values():
            snap[f"draft/{draft.id}"] = _canon(draft.to_dict())
        for msg in self.trash.values():
            snap[f"trash/{msg.id}"] = _canon(msg.to_dict())
        for thread in self.threads.values():
            snap[f"thread/{thread.id}"] = _canon(thread.to_dict())
        return snap

    def state_hash(self) -> str:
        digest = hashlib.sha256()
        for path, blob in sorted(self.snapshot().items()):
            digest.update(path.encode())
            digest.update(b"\x00")
            digest.update(blob.encode())
            digest.update(b"\x00")
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
        changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
        return sorted(changed)

    def clone(self) -> MailboxStore:
        """A deep-enough copy for replay. Fixtures are immutable by construction."""
        other = MailboxStore(
            fixture_id=self.fixture_id,
            owner_email=self.owner_email,
            owner_name=self.owner_name,
            contacts=self.contacts,
            labels=self.labels,
            internal_domain=self.internal_domain,
        )
        other.messages = {
            mid: replace(m, to=list(m.to), cc=list(m.cc), labels=list(m.labels),
                         attachments=list(m.attachments))
            for mid, m in self.messages.items()
        }
        other.threads = {
            tid: replace(t, message_ids=list(t.message_ids)) for tid, t in self.threads.items()
        }
        other.drafts = {
            did: replace(d, to=list(d.to), cc=list(d.cc), attachment_ids=list(d.attachment_ids))
            for did, d in self.drafts.items()
        }
        other.trash = {
            mid: replace(m, to=list(m.to), cc=list(m.cc), labels=list(m.labels),
                         attachments=list(m.attachments))
            for mid, m in self.trash.items()
        }
        other.sent_ids = list(self.sent_ids)
        other._counter = self._counter
        return other

    # ---- accessors --------------------------------------------------------

    def message(self, message_id: str) -> Message:
        if message_id in self.messages:
            return self.messages[message_id]
        if message_id in self.trash:
            raise ToolError(f"message {message_id} is in trash", kind="not_found")
        raise ToolError(f"no such message: {message_id}", kind="not_found")

    def thread(self, thread_id: str) -> Thread:
        if thread_id not in self.threads:
            raise ToolError(f"no such thread: {thread_id}", kind="not_found")
        return self.threads[thread_id]

    def draft(self, draft_id: str) -> Draft:
        if draft_id not in self.drafts:
            raise ToolError(f"no such draft: {draft_id}", kind="not_found")
        return self.drafts[draft_id]

    def thread_participants(self, thread_id: str) -> list[str]:
        """Every address that appears on any message in the thread, owner excluded."""
        seen: list[str] = []
        for mid in self.thread(thread_id).message_ids:
            msg = self.messages.get(mid)
            if msg is None:
                continue
            for addr in [msg.from_email, *msg.to, *msg.cc]:
                if addr != self.owner_email and addr not in seen:
                    seen.append(addr)
        return seen

    def is_external(self, email: str) -> bool:
        return not email.endswith(f"@{self.internal_domain}")

    # ---- mutations --------------------------------------------------------

    def append_sent(
        self,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        thread_id: str | None,
        sent_at: str,
        attachments: list[Attachment] | None = None,
    ) -> Message:
        message_id = self._next_id("msg")
        if thread_id is None:
            thread_id = self._next_id("thread")
            self.threads[thread_id] = Thread(id=thread_id, subject=subject)
        msg = Message(
            id=message_id,
            thread_id=thread_id,
            from_email=self.owner_email,
            from_name=self.owner_name,
            to=list(to),
            cc=list(cc),
            subject=subject,
            body=body,
            received_at=sent_at,
            labels=["SENT"],
            attachments=list(attachments or []),
        )
        self.messages[message_id] = msg
        self.sent_ids.append(message_id)
        self.threads[thread_id].message_ids.append(message_id)
        return msg

    def new_draft(
        self,
        *,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        thread_id: str | None,
        attachment_ids: list[str] | None = None,
    ) -> Draft:
        draft = Draft(
            id=self._next_id("draft"),
            to=list(to),
            cc=list(cc),
            subject=subject,
            body=body,
            thread_id=thread_id,
            attachment_ids=list(attachment_ids or []),
        )
        self.drafts[draft.id] = draft
        return draft
