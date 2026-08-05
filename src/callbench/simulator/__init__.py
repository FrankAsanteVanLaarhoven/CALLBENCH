"""The local email simulator: the only execution surface in this benchmark."""

from __future__ import annotations

from .fixtures import REFERENCE_TIME, build_fixture, fixture_ids
from .store import Attachment, Contact, Draft, MailboxStore, Message, Thread, ToolError
from .tools import HANDLERS, invoke

__all__ = [
    "Attachment",
    "Contact",
    "Draft",
    "HANDLERS",
    "MailboxStore",
    "Message",
    "REFERENCE_TIME",
    "Thread",
    "ToolError",
    "build_fixture",
    "fixture_ids",
    "invoke",
]
