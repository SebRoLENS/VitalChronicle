from __future__ import annotations

from pathlib import Path

from google_health_viewer.ai_conversations import ConversationStore


def _thread(store: ConversationStore) -> dict:
    return store.create_thread(
        title="Test conversation",
        model="qwen3.5:9b",
        scope="selected",
        period={"label": "Last month", "start": "2026-08-01", "end": "2026-09-01"},
        snapshot={
            "observation_context": {"observed_at": "2026-08-31T10:00:00+00:00"},
            "metrics": [{"data_type": "steps"}],
        },
        snapshot_revision="2026-08-31T10:00:00+00:00",
    )


def test_conversations_persist_messages_and_snapshot(tmp_path: Path):
    path = tmp_path / "chats.json"
    store = ConversationStore(path)
    thread = _thread(store)
    store.add_message(thread["id"], "user", "How is my activity?")
    store.add_message(
        thread["id"], "assistant", "It is stable. [trend:steps]", evidence_ids=["trend:steps"]
    )

    reloaded = ConversationStore(path).get_thread(thread["id"])

    assert reloaded is not None
    assert reloaded["snapshot"]["metrics"][0]["data_type"] == "steps"
    assert [message["role"] for message in reloaded["messages"]] == ["user", "assistant"]
    assert reloaded["messages"][-1]["evidence_ids"] == ["trend:steps"]


def test_long_conversation_keeps_recent_turns_and_compacts_older_context(tmp_path: Path):
    store = ConversationStore(tmp_path / "chats.json")
    thread = _thread(store)
    for index in range(7):
        store.add_message(thread["id"], "user", f"Question {index}")
        store.add_message(thread["id"], "assistant", f"Answer {index}")

    history = store.model_history(thread["id"], recent_messages=6)

    assert history[0]["role"] == "user"
    assert history[0]["content"].startswith("Earlier conversation excerpts")
    assert history[-1] == {"role": "assistant", "content": "Answer 6"}
    assert len(history) == 7


def test_snapshot_refresh_and_delete_are_local_and_explicit(tmp_path: Path):
    store = ConversationStore(tmp_path / "chats.json")
    thread = _thread(store)
    refreshed = store.update_snapshot(
        thread["id"],
        {"observation_context": {"observed_at": "2026-08-31T12:00:00+00:00"}, "metrics": []},
        "2026-08-31T12:00:00+00:00",
    )

    assert refreshed is not None
    assert refreshed["snapshot_revision"] == "2026-08-31T12:00:00+00:00"
    assert store.delete_thread(thread["id"])
    assert store.list_threads() == []


def test_clear_removes_all_saved_conversations(tmp_path: Path):
    store = ConversationStore(tmp_path / "chats.json")
    _thread(store)

    store.clear()

    assert ConversationStore(tmp_path / "chats.json").list_threads() == []
