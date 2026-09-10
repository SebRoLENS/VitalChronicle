from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from google_health_viewer.agent_runtime_v2 import _detect_self_report
from google_health_viewer.agent_store import AgentStore


def test_explicit_fatigue_statement_is_detected():
    item = _detect_self_report("Mi sento stanco oggi")
    assert item is not None
    assert item["category"] == "fatigue"


def test_general_question_is_not_misclassified_as_self_report():
    assert _detect_self_report("Perché una persona può sentirsi stanca?") is None


def test_self_report_feedback_stays_attached_to_event_not_user_model(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    report = store.record_self_report(
        "Mi sento stanco oggi", category="fatigue", thread_id="thread-1"
    )
    feedback = store.ask_feedback(
        "Che tipo di stanchezza?",
        thread_id="thread-1",
        learning_key="self_report_detail:fatigue",
        context={"self_report_id": report["report_id"], "feedback_mode": "self_report_detail"},
    )
    store.answer_feedback(feedback["feedback_id"], "Soprattutto muscolare")

    refreshed = store.self_report(report["report_id"])
    assert refreshed is not None
    assert refreshed["context"]["follow_up_answer"] == "Soprattutto muscolare"
    assert store.user_model() == []


def test_recent_training_statement_gets_temporary_validity(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    item = store.learn_user_model(
        "current_training_goal",
        "User feedback: ho ricominciato palestra da poco",
        evidence={"answer": "Ho ricominciato palestra da poco"},
        source="feedback",
    )
    assert item["temporal_scope"] == "temporary"
    assert item["is_current"] is True
    assert item["valid_until"] is not None
    valid_from = datetime.fromisoformat(item["valid_from"])
    valid_until = datetime.fromisoformat(item["valid_until"])
    assert 35 <= (valid_until - valid_from).days <= 50


def test_expired_temporary_context_is_not_injected_but_remains_inspectable(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    past_start = datetime.now(timezone.utc) - timedelta(days=90)
    past_end = datetime.now(timezone.utc) - timedelta(days=30)
    store.learn_user_model(
        "old_training_context",
        "Temporary training context",
        evidence={
            "temporal": {
                "scope": "temporary",
                "valid_from": past_start.isoformat(),
                "valid_until": past_end.isoformat(),
                "ttl_days": 60,
            }
        },
        source="feedback",
    )
    assert store.user_model() == []
    all_items = store.user_model(include_expired=True)
    assert len(all_items) == 1
    assert all_items[0]["is_current"] is False
    assert all_items[0]["confidence"] == 0.0


def test_feedback_key_antispam(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    assert store.has_recent_feedback_key("self_report_detail:fatigue") is False
    store.ask_feedback("Che tipo di stanchezza?", learning_key="self_report_detail:fatigue")
    assert store.has_recent_feedback_key("self_report_detail:fatigue") is True
