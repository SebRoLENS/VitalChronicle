from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from google_health_viewer.agent_runtime_v2 import (
    _detect_durable_context_candidate,
    _detect_self_report,
)
from google_health_viewer.agent_store import AgentStore, PERSONAL_CONTEXT_KEY_SPECS


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


def test_durable_context_is_saved_only_after_explicit_confirmation(tmp_path: Path):
    candidate = _detect_durable_context_candidate("Di solito vado a letto alle 23")
    assert candidate is not None
    store = AgentStore(tmp_path / "agent.sqlite3")
    feedback = store.ask_feedback(
        "Vuoi che ricordi questo contesto personale duraturo?",
        learning_key="personal_context:sleep_schedule_context",
        context={
            "feedback_mode": "durable_context_confirmation",
            "candidate_statement": candidate["statement"],
            "model_key": candidate["model_key"],
            "temporal_scope": candidate["temporal_scope"],
        },
    )
    store.answer_feedback(feedback["feedback_id"], "sì")
    model = store.user_model()
    assert len(model) == 1
    assert model[0]["key"] == "sleep_schedule_context"
    assert model[0]["statement"] == "Di solito vado a letto alle 23"


def test_durable_context_decline_does_not_enter_user_model(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    feedback = store.ask_feedback(
        "Vuoi che ricordi questo contesto personale duraturo?",
        learning_key="personal_context:sleep_schedule_context",
        context={
            "feedback_mode": "durable_context_confirmation",
            "candidate_statement": "Di solito vado a letto alle 23",
            "model_key": "sleep_schedule_context",
            "temporal_scope": "stable",
        },
    )
    store.answer_feedback(feedback["feedback_id"], "no")
    assert store.user_model() == []


def test_context_candidate_extracts_only_personal_sentence_from_compound_request():
    candidate = _detect_durable_context_candidate(
        "Di solito mi alleno in bicicletta cinque giorni a settimana. "
        "Nei 17 giorni disponibili, quanto spesso il sonno diminuisce?"
    )
    assert candidate is not None
    assert candidate["model_key"] == "training_routine_context"
    assert candidate["statement"] == "Di solito mi alleno in bicicletta cinque giorni a settimana"


def test_legacy_polluted_training_context_is_migrated(tmp_path: Path):
    path = tmp_path / "agent.sqlite3"
    store = AgentStore(path)
    store.learn_user_model(
        "current_training_goal",
        (
            "Di solito mi alleno in bicicletta cinque giorni a settimana. "
            "Nei 17 giorni disponibili, quanto spesso il sonno diminuisce?"
        ),
        evidence={
            "context": {
                "candidate_statement": (
                    "Di solito mi alleno in bicicletta cinque giorni a settimana. "
                    "Nei 17 giorni disponibili, quanto spesso il sonno diminuisce?"
                )
            }
        },
        source="explicit_user_confirmation",
    )

    migrated = AgentStore(path)
    model = migrated.user_model()
    assert len(model) == 1
    assert model[0]["key"] == "training_routine_context"
    assert model[0]["statement"] == (
        "Di solito mi alleno in bicicletta cinque giorni a settimana"
    )
    assert migrated.recent_tool_events()[-1]["event_type"] == "personal_context_migrated"


def test_personal_context_catalogue_is_rich_and_typed():
    expected = {
        "sleep_schedule_context",
        "sleep_quality_context",
        "subjective_sleep_need_context",
        "training_routine_context",
        "training_frequency_context",
        "current_training_goal",
        "training_preferences",
        "training_constraints",
        "subjective_recovery_baseline",
        "high_load_subjective_tolerance",
        "usual_energy_level",
        "usual_fatigue_response",
        "stress_context",
        "work_schedule_context",
        "nutrition_context",
        "stimulant_context",
        "illness_context",
        "medication_context",
        "travel_context",
        "personal_analysis_preferences",
        "coaching_preferences",
        "primary_health_coaching_goal",
    }
    assert expected <= set(PERSONAL_CONTEXT_KEY_SPECS)
    for key in expected:
        spec = PERSONAL_CONTEXT_KEY_SPECS[key]
        assert spec["topics"]
        assert spec["markers"]
        assert spec["default_scope"] in {"stable", "temporary"}


def test_richer_context_markers_are_classified_by_semantic_key():
    examples = {
        "Sono spesso sotto stress per il lavoro": "stress_context",
        "Ho un infortunio al ginocchio e devo evitare la corsa": "training_constraints",
        "Sono in viaggio e ho il jet lag": "travel_context",
        "Preferisco spiegazioni brevi": "coaching_preferences",
        "Di solito dormo bene": "sleep_quality_context",
        "Mi alleno cinque giorni a settimana": "training_frequency_context",
    }
    for statement, expected_key in examples.items():
        candidate = _detect_durable_context_candidate(statement)
        assert candidate is not None
        assert candidate["model_key"] == expected_key
