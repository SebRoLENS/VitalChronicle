from __future__ import annotations

from datetime import datetime

from google_health_viewer import agent_hard_query_reliability_patch as hard
from google_health_viewer import agent_runtime_v2 as runtime_v2
from google_health_viewer.agent_store import AgentStore

hard.install_hard_query_reliability_patch()

QUESTION = (
    "Di solito mi alleno 3-4 volte a settimana, soprattutto la mattina. "
    "Normalmente vado a letto verso mezzanotte e mi sveglio verso le 7:30. "
    "Nei giorni in cui mi alleno per due giorni consecutivi, voglio capire se dopo il secondo "
    "allenamento HRV e frequenza cardiaca a riposo recuperano più lentamente quando la notte "
    "successiva dormo almeno il 15% meno della mia mediana personale rispetto a quando dormo "
    "almeno quanto la mia mediana. Considera gli ultimi 90 giorni."
)


def _points(values: dict[str, float]) -> list[tuple[float, float]]:
    return [
        (datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp(), value)
        for day, value in values.items()
    ]


def test_hard_question_detects_two_distinct_personal_contexts():
    candidates = hard._durable_context_candidates(QUESTION)
    by_key = {item["model_key"]: item for item in candidates}

    assert "training_routine_context" in by_key
    assert "sleep_schedule_context" in by_key
    assert "3-4 volte a settimana" in by_key["training_routine_context"]["statement"]
    assert "mezzanotte" in by_key["sleep_schedule_context"]["statement"]


def test_pending_confirmation_always_shows_the_context_being_confirmed(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    statement = "Normalmente vado a letto verso mezzanotte e mi sveglio verso le 7:30."
    store.ask_feedback(
        "Vuoi che ricordi questo contesto personale duraturo per le future analisi?",
        thread_id="thread-1",
        learning_key="personal_context:sleep_schedule_context",
        context={
            "feedback_mode": "durable_context_confirmation",
            "candidate_statement": statement,
            "model_key": "sleep_schedule_context",
            "temporal_scope": "stable",
        },
    )

    pending = store.pending_feedback("thread-1")
    assert pending is not None
    assert statement in pending["question"]


def test_runtime_uses_specific_capability_for_the_hard_question():
    hint = runtime_v2._factory_hint(QUESTION)
    capability = runtime_v2._factory_capability(hint)

    assert hint["consider_reusable_tool"] is True
    assert hard._SLEEP_CONDITIONED_SIGNAL in hint["signals"]
    assert capability == hard._SLEEP_CONDITIONED_COMPOSED_CAPABILITY


def test_sleep_conditioned_recipe_preserves_all_requested_semantics():
    pipeline, parameters = hard._sleep_conditioned_recipe(
        {},
        QUESTION,
        {"daily-heart-rate-variability", "daily-resting-heart-rate"},
    )

    assert pipeline[0]["tool"] == hard._SLEEP_CONDITIONED_NAME
    assert pipeline[0]["arguments"]["response_metrics"] == [
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
    ]
    assert parameters["properties"]["sleep_deficit_percent"]["default"] == 15

    contract = hard.semantic._semantic_contract_errors(QUESTION, pipeline)
    assert contract["missing"] == []
    assert contract["introduced"] == []


def test_sleep_conditioned_primitive_uses_second_day_and_following_night():
    class Executor:
        @staticmethod
        def _series(metric, _left, _right):
            values = {
                "exercise": {
                    "2026-09-01": 1.0,
                    "2026-09-02": 1.0,
                    "2026-09-05": 1.0,
                    "2026-09-06": 1.0,
                },
                "sleep:sleep.summary.minutesAsleep": {
                    "2026-09-01": 420,
                    "2026-09-02": 420,
                    "2026-09-03": 300,
                    "2026-09-04": 420,
                    "2026-09-05": 420,
                    "2026-09-06": 420,
                    "2026-09-07": 450,
                    "2026-09-08": 420,
                },
                "daily-heart-rate-variability": {
                    "2026-09-01": 60,
                    "2026-09-02": 60,
                    "2026-09-03": 45,
                    "2026-09-04": 60,
                    "2026-09-05": 60,
                    "2026-09-06": 60,
                    "2026-09-07": 60,
                    "2026-09-08": 60,
                },
                "daily-resting-heart-rate": {
                    "2026-09-01": 50,
                    "2026-09-02": 50,
                    "2026-09-03": 65,
                    "2026-09-04": 50,
                    "2026-09-05": 50,
                    "2026-09-06": 50,
                    "2026-09-07": 50,
                    "2026-09-08": 50,
                },
            }[metric]
            return {
                "points": _points(values),
                "aggregation": "sum" if metric == "exercise" else "mean",
                "unit": "h" if metric == "exercise" else "test",
                "field": metric,
                "date_semantics": "wake_up_date" if metric.startswith("sleep:") else "calendar_observation_date",
            }

    result = hard._tool_compare_sleep_conditioned_consecutive_training_recovery(
        Executor(),
        {
            "start": "2026-09-01",
            "end": "2026-09-08",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "sleep_deficit_percent": 15,
            "recovery_tolerance_percent": 10,
            "max_recovery_days": 3,
        },
    )

    assert result["status"] == "ok"
    assert result["second_consecutive_training_events"] == 2
    assert result["cohorts"]["low_sleep_event_count"] == 1
    assert result["cohorts"]["baseline_or_above_event_count"] == 1
    hrv = result["responses"]["daily-heart-rate-variability"]
    rhr = result["responses"]["daily-resting-heart-rate"]
    assert hrv["low_sleep"]["median_recovery_days"] == 2
    assert hrv["baseline_or_above_sleep"]["median_recovery_days"] == 1
    assert hrv["median_recovery_delta_days_low_minus_baseline_or_above"] == 1
    assert rhr["median_recovery_delta_days_low_minus_baseline_or_above"] == 1
    assert "statistical" in result["limitations"].casefold()
