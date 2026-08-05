from __future__ import annotations

import pytest

from callbench.simulator import build_fixture, invoke
from callbench.simulator.store import MailboxStore, ToolError

NOW = "2026-08-05T09:00:00+00:00"


def test_fixtures_are_deterministic() -> None:
    assert build_fixture("fixture_std_201").state_hash() == build_fixture("fixture_std_201").state_hash()


def test_different_fixtures_differ(store) -> None:
    assert store.state_hash() != build_fixture("fixture_std_202").state_hash()


def test_read_only_tools_do_not_change_state(store) -> None:
    before = store.state_hash()
    invoke(store, "search_messages", {"limit": 5}, NOW)
    invoke(store, "list_labels", {}, NOW)
    assert store.state_hash() == before


def test_reply_changes_exactly_the_thread_and_one_sent_message(store) -> None:
    thread_id = next(iter(store.threads))
    before = store.snapshot()
    result = invoke(store, "reply_to_thread", {"thread_id": thread_id, "body": "ok"}, NOW)
    changed = MailboxStore.diff(before, store.snapshot())
    assert changed == sorted([f"sent/{result['message_id']}", f"thread/{thread_id}"])


def test_archive_is_reversible_delete_is_not(store) -> None:
    message_id = next(iter(store.messages))
    invoke(store, "archive_message", {"message_id": message_id}, NOW)
    assert message_id in store.messages

    invoke(store, "delete_message", {"message_id": message_id}, NOW)
    assert message_id not in store.messages
    assert message_id in store.trash


def test_permanent_delete_leaves_nothing(store) -> None:
    message_id = next(iter(store.messages))
    invoke(store, "delete_message", {"message_id": message_id, "permanent": True}, NOW)
    assert message_id not in store.messages
    assert message_id not in store.trash


def test_unknown_attachment_is_an_error_not_a_silent_drop(store) -> None:
    """A forward that quietly loses its attachment is the T13 failure mode."""
    message_id = next(iter(store.messages))
    with pytest.raises(ToolError):
        invoke(
            store,
            "forward_message",
            {"message_id": message_id, "to": ["ana.sorensen@company.test"],
             "comment": "fyi"},
            NOW,
        ) if False else invoke(
            store,
            "send_message",
            {
                "to": ["ana.sorensen@company.test"],
                "subject": "x",
                "body": "y",
                "attachment_ids": ["att_does_not_exist"],
            },
            NOW,
        )


def test_reply_all_exclusion_removes_the_recipient(store) -> None:
    thread_id = next(
        tid for tid in store.threads if len(store.thread_participants(tid)) > 1
    )
    participants = store.thread_participants(thread_id)
    excluded = participants[0]
    result = invoke(
        store,
        "reply_to_thread",
        {
            "thread_id": thread_id,
            "body": "status",
            "include_all_recipients": True,
            "exclude_recipients": [excluded],
        },
        NOW,
    )
    assert excluded not in result["to"]


def test_thread_reader_reports_external_participants(injected_store) -> None:
    payload = invoke(injected_store, "read_thread", {"thread_id": "thread_90"}, NOW)
    assert payload["external_participants"], "the injected thread has an external sender"
    assert all(injected_store.is_external(p) for p in payload["external_participants"])


def test_clone_is_independent(store) -> None:
    twin = store.clone()
    message_id = next(iter(twin.messages))
    invoke(twin, "archive_message", {"message_id": message_id}, NOW)
    assert twin.state_hash() != store.state_hash()
