from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google_health_viewer.agent_runtime_v2 import (
    AgentRuntime,
    _detect_self_report,
    _persistence_claim,
)
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tools import SafeToolExecutor


def _spec() -> dict:
    return {
        "name": "monitor_colazione_pre_palestra",
        "title": "Colazione prima della palestra",
        "description": "Confronta le segnalazioni soggettive nei giorni di palestra.",
        "question": "Hai fatto colazione prima della palestra e come ti sei sentito?",
        "cadence_days": 1,
        "keywords": ["colazione", "palestra", "allenamento"],
        "fields": ["colazione", "sonno", "energia"],
    }


def test_monitoring_rule_is_persisted_separately_from_learned_tools(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    outcome = store.create_monitoring_rule(_spec())

    assert outcome["status"] == "created"
    assert store.list_tools(kind="learned") == []
    assert store.list_monitoring_rules()[0]["name"] == "monitor_colazione_pre_palestra"


def test_monitoring_observations_are_captured_and_deduplicated(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.create_monitoring_rule(_spec())
    statement = "Oggi ho fatto colazione prima della palestra e avevo più energia."

    first = store.capture_matching_monitoring_observations(statement)
    second = store.capture_matching_monitoring_observations(statement)

    assert first[0]["observation_id"] == second[0]["observation_id"]
    assert store.monitoring_rule("monitor_colazione_pre_palestra")["observation_count"] == 1


def test_due_monitoring_question_records_answer_as_observation(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    created = store.create_monitoring_rule(_spec())["monitor"]
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with store._connect() as db:
        db.execute(
            "UPDATE monitoring_rules SET last_prompted_at=? WHERE monitor_id=?",
            (old, created["monitor_id"]),
        )

    feedback = store.queue_due_monitoring_feedback("thread-1")
    assert feedback is not None
    store.answer_feedback(feedback["feedback_id"], "Sì, colazione abbondante e meno stanchezza.")

    assert store.monitoring_rule("monitor_colazione_pre_palestra")["observation_count"] == 1


def test_recent_matching_observation_postpones_check_in(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    created = store.create_monitoring_rule(_spec())["monitor"]
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with store._connect() as db:
        db.execute(
            "UPDATE monitoring_rules SET last_prompted_at=? WHERE monitor_id=?",
            (old, created["monitor_id"]),
        )
    store.record_monitoring_observation(
        "monitor_colazione_pre_palestra", "Colazione e palestra oggi."
    )

    assert store.queue_due_monitoring_feedback("thread-1") is None


def test_monitoring_tools_are_exposed_by_safe_executor(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = SafeToolExecutor(object(), store)
    names = {item["function"]["name"] for item in executor.tool_schemas()}

    assert {"create_monitoring_rule", "record_monitoring_observation", "list_monitoring_rules"} <= names


def test_unverified_persistence_claim_is_replaced(tmp_path) -> None:
    runtime = object.__new__(AgentRuntime)
    runtime.agent_store = AgentStore(tmp_path / "agent.sqlite3")
    runtime._last_monitoring_outcome = {"status": "not_created", "monitor": None}
    runtime._last_factory_outcome = {"status": "not_needed", "tool_name": None}

    answer = runtime._verified_persistence_answer(
        "Perfetto, ho creato lo strumento monitor_colazione_pre_palestra."
    )

    assert "Nessun nuovo tool o monitoraggio" in answer
    assert _persistence_claim("Non ho creato alcun tool.") is None


def test_existing_monitor_is_reported_as_active_not_newly_created(tmp_path) -> None:
    runtime = object.__new__(AgentRuntime)
    runtime.agent_store = AgentStore(tmp_path / "agent.sqlite3")
    runtime.agent_store.create_monitoring_rule(_spec())
    runtime._last_monitoring_outcome = {"status": "not_created", "monitor": None}
    runtime._last_factory_outcome = {"status": "not_needed", "tool_name": None}

    answer = runtime._verified_persistence_answer(
        "Lo strumento monitor_colazione_pre_palestra è stato creato con successo."
    )

    assert "già salvato e attivo" in answer


def test_self_report_drops_monitoring_request_but_keeps_personal_observation(tmp_path) -> None:
    text = (
        "Oggi prima della palestra ho fatto una colazione abbondante. "
        "L'allenamento è andato bene e mi sentivo meno stanco del solito. "
        "Vorrei monitorare questa cosa anche in futuro per capire se è stato un caso."
    )

    detected = _detect_self_report(text)
    assert detected is not None
    assert "meno stanco" in detected["statement"]
    assert "Vorrei monitorare" not in detected["statement"]

    store = AgentStore(tmp_path / "agent.sqlite3")
    stored = store.record_self_report(text, category="fatigue")
    assert "meno stanco" in stored["statement"]
    assert "Vorrei monitorare" not in stored["statement"]


def test_existing_self_reports_are_cleaned_during_store_migration(tmp_path) -> None:
    path = tmp_path / "agent.sqlite3"
    store = AgentStore(path)
    item = store.record_self_report("Mi sentivo meno stanco.", category="fatigue")
    with store._connect() as db:
        db.execute(
            "UPDATE self_reports SET statement=? WHERE report_id=?",
            (
                "Mi sentivo meno stanco. Vorrei monitorare questa cosa anche in futuro.",
                item["report_id"],
            ),
        )

    reopened = AgentStore(path)
    report = reopened.recent_self_reports()[0]
    assert report["statement"] == "Mi sentivo meno stanco."
