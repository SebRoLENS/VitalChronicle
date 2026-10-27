from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from google_health_viewer.agent_runtime import online_tool_subset
from google_health_viewer.agent_runtime_adaptive_patch import _smart_json_text
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tools import SafeToolExecutor
from google_health_viewer.ai_insights import _daily_values
from google_health_viewer.storage import HealthStore


def make_sleep(day, minutes, *, main=True, name="main", hour=23):
    end = datetime.fromisoformat(day + "T07:00:00+00:00")
    start = (end - timedelta(days=1)).replace(hour=hour)
    return {
        "name": f"{name}-{day}",
        "sleep": {
            "interval": {"startTime": start.isoformat(), "endTime": end.isoformat()},
            "summary": {"minutesAsleep": minutes},
            "metadata": {"mainSleep": main} if main is not None else {},
        },
    }


@pytest.fixture
def archive(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    sleeps, hrv = [], []
    for i, value in enumerate([70, 71, 69, 72, 68, 25]):
        day = (date(2026, 9, 1) + timedelta(days=i)).isoformat()
        sleeps.append(make_sleep(day, 400 + i * 10))
        hrv.append(
            {
                "name": day,
                "startTime": day + "T12:00:00Z",
                "dailyHeartRateVariability": {"averageHeartRateVariabilityMilliseconds": value},
            }
        )
    short = make_sleep("2026-09-06", 21, main=None, name="additional")
    short["sleep"]["interval"] = {
        "startTime": "2026-09-06T20:48:00Z",
        "endTime": "2026-09-06T21:30:00Z",
    }
    sleeps.append(short)
    health.upsert_records("sleep", sleeps)
    health.upsert_records("daily-heart-rate-variability", hrv)
    return health, SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))


def test_sleep_association_pairs_main_night_and_omits_later_short_session(archive):
    _, tools = archive
    result = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["paired_days"] == 6
    assert result["low_metric_event_count"] == 1
    assert result["low_metric_events"]["2026-09-06"]["main_sleep_hours"] == 7.5
    assert result["low_metric_events"]["2026-09-06"]["metric_value"] == 25
    assert result["bedtime_pearson_r"] is None  # constant bedtime has no correlation
    assert len(json.dumps(result)) < 2200  # compact calculated output, not a raw daily series


def test_sleep_association_never_assumes_unlabelled_session_is_main(archive):
    health, tools = archive
    health.upsert_records("sleep", [make_sleep("2026-09-06", 450, main=None)])
    result = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["paired_days"] == 5
    assert result["low_metric_events"]["2026-09-06"]["main_sleep_hours"] is None


def test_ambiguous_main_nights_are_excluded_instead_of_arbitrarily_chosen(archive):
    health, tools = archive
    health.upsert_records("sleep", [make_sleep("2026-09-06", 120, name="other-main")])
    result = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["ambiguous_main_sleep_days"] == 1
    assert result["paired_days"] == 5
    assert result["low_metric_events"]["2026-09-06"]["main_sleep_hours"] is None


def test_prepared_sleep_and_agent_sleep_totals_agree(archive):
    health, tools = archive
    result = _daily_values(health.list_records("sleep"), "sleep")
    assert result is not None
    daily, unit, aggregation = result
    assert unit == "h"
    assert aggregation == "sum"
    assert daily[date(2026, 9, 6)] == pytest.approx(471 / 60)
    summary = tools.execute(
        "get_daily_summary", {"metric": "sleep", "start": "2026-09-06", "end": "2026-09-06"}
    )
    assert summary["daily"][0]["value"] == pytest.approx(daily[date(2026, 9, 6)])


def test_compare_periods_reports_medians_and_each_period_sample_count(archive):
    _, tools = archive
    result = tools.execute(
        "compare_periods",
        {
            "metric": "hrv",
            "start_a": "2026-08-31",
            "end_a": "2026-09-03",
            "start_b": "2026-09-04",
            "end_b": "2026-09-06",
        },
    )
    assert result["period_a"]["observed_days"] == 3
    assert result["period_a"]["expected_days"] == 4
    assert result["period_b"]["observed_days"] == 3
    assert result["median_a"] == 70
    assert result["median_b"] == 68
    assert result["unit"] == "ms"


def test_scoped_metric_discovery_retains_secondary_fields(archive):
    _, tools = archive
    result = tools.execute("get_available_metrics", {"data_type": "sleep"})
    assert len(result["data_types"]) == 1
    assert "sleep.summary.minutesAsleep" in result["data_types"][0]["metrics"]
    assert "daily-heart-rate-variability" in result["available_data_types"]


def test_catalogue_compression_preserves_all_dataset_identifiers():
    counts = {f"dataset-{i}": 100 for i in range(26)}
    counts["sleep"] = 65
    result = {
        "available_data_types": counts,
        "data_types": [
            {"data_type": name, "metrics": ["some.long.field.name"] * 12} for name in counts
        ],
    }
    compact = json.loads(_smart_json_text(result, 1800))
    assert compact["available_data_types"] == counts
    assert len(json.dumps(compact, separators=(",", ":"))) <= 1800


@pytest.mark.parametrize(
    ("question", "required"),
    [
        (
            "Correlazione HRV e sonno per orario e durata",
            {"analyze_sleep_metric_association", "calculate_correlations", "get_sleep_sessions"},
        ),
        ("Confronta HRV settembre e ottobre", {"compare_periods"}),
        (
            "Quanto sonno profondo REM e leggero ho fatto?",
            {"get_sleep_stage_series", "get_sleep_sessions"},
        ),
        ("Quali giorni mancanti nella HRV?", {"get_missing_data"}),
        ("Quanti allenamenti di nuoto?", {"analyze_workout"}),
    ],
)
def test_small_tool_menu_includes_calculations_needed_by_question(archive, question, required):
    _, tools = archive
    # get_sleep_stage_series is supplied by the enhanced executor in production.
    schemas = tools.tool_schemas() + [
        {
            "type": "function",
            "function": {
                "name": "get_sleep_stage_series",
                "description": "Sleep stages",
                "parameters": {},
            },
        }
    ]
    menu = online_tool_subset(schemas, question, maximum=10)
    names = {s["function"]["name"] for s in menu}
    assert required <= names
    assert {"search_tool_registry", "create_learned_tool"} <= names
    assert len(menu) <= 10


@pytest.mark.parametrize("draft", ["| Data | HRV |\n| 2026-09-", ""])
def test_exhausted_tool_turn_gets_complete_final_pass(archive, monkeypatch, draft):
    from google_health_viewer.agent_runtime_v2 import AgentRuntime

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": draft, "_done_reason": "length"}
        assert kwargs["tools"] == []
        assert kwargs["think"] is False
        return {"content": "Risposta completa con i dati disponibili.", "_done_reason": "stop"}

    monkeypatch.setattr(runtime, "_chat_once", chat)
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question="Come è la mia HRV?",
        history=[],
        max_tokens=1024,
        model_context_limit=8192,
        performance_profile="standard",
        thread_id=None,
    )
    assert answer == "Risposta completa con i dati disponibili."
    assert len(calls) == 2


def test_local_day_boundaries_match_planner_and_agent(tmp_path, monkeypatch):
    import os
    import time

    from google_health_viewer.ai_query_planner_core import SelectedHealthStore

    if not hasattr(time, "tzset"):
        pytest.skip("TZ override requires tzset")
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Rome")
    time.tzset()
    try:
        health = HealthStore(tmp_path / "health.sqlite3")
        health.upsert_records(
            "steps",
            [
                {
                    "name": "outside-before",
                    "startTime": "2026-10-14T21:45:00Z",
                    "steps": {"count": 900},
                },
                {
                    "name": "early-local",
                    "startTime": "2026-10-14T22:15:00Z",
                    "steps": {"count": 50},
                },
                {
                    "name": "late-local",
                    "startTime": "2026-10-15T21:45:00Z",
                    "steps": {"count": 100},
                },
                {
                    "name": "outside-after",
                    "startTime": "2026-10-15T22:15:00Z",
                    "steps": {"count": 200},
                },
            ],
        )
        tools = SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))
        result = tools.execute(
            "get_daily_summary", {"metric": "steps", "start": "2026-10-15", "end": "2026-10-15"}
        )
        assert result["daily"] == [{"date": "2026-10-15", "value": 150}]
        assert result["coverage"] == 1
        proxy = SelectedHealthStore(health, ["steps"])
        daily, _, _ = _daily_values(
            proxy.list_records("steps", "2026-10-15", "2026-10-16"), "steps"
        )
        assert daily == {date(2026, 10, 15): 150}
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_nutrition_totals_use_meal_logs_not_food_catalogue(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records(
        "food",
        [
            {
                "name": "catalogue",
                "startTime": "2026-10-20T10:00:00Z",
                "food": {"energyAvg": {"kcal": 9999}},
            }
        ],
    )
    health.upsert_records(
        "nutrition-log",
        [
            {
                "name": "meal1",
                "startTime": "2026-10-20T10:00:00Z",
                "nutritionLog": {
                    "energy": {"kcal": 100},
                    "nutrients": [{"nutrient": "PROTEIN", "quantity": {"grams": 5}}],
                },
            },
            {
                "name": "meal2",
                "startTime": "2026-10-20T16:00:00Z",
                "nutritionLog": {"energy": {"kcal": 200}},
            },
        ],
    )
    tools = SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))
    result = tools.execute("summarize_nutrition_logs", {"start": "2026-10-20", "end": "2026-10-20"})
    assert result["energy_kcal"] == 300
    assert result["protein_grams"] == 5
    assert result["protein_entries"] == 1
    empty = tools.execute("summarize_nutrition_logs", {"start": "2026-10-19", "end": "2026-10-19"})
    assert empty["energy_kcal"] is None
    assert empty["protein_grams"] is None


def test_workout_heart_rate_uses_duration_and_reports_missing_sessions(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records(
        "exercise",
        [
            {
                "name": "swim1",
                "exercise": {
                    "exerciseType": "SWIMMING",
                    "interval": {
                        "startTime": "2026-10-20T08:00:00Z",
                        "endTime": "2026-10-20T10:00:00Z",
                    },
                    "metricsSummary": {"averageHeartRateBeatsPerMinute": 100},
                },
            },
            {
                "name": "swim2",
                "exercise": {
                    "exerciseType": "SWIMMING",
                    "interval": {
                        "startTime": "2026-10-20T14:00:00Z",
                        "endTime": "2026-10-20T15:00:00Z",
                    },
                    "metricsSummary": {"averageHeartRateBeatsPerMinute": 160},
                },
            },
            {
                "name": "swim3",
                "exercise": {
                    "exerciseType": "SWIMMING",
                    "interval": {
                        "startTime": "2026-10-20T17:00:00Z",
                        "endTime": "2026-10-20T18:00:00Z",
                    },
                },
            },
        ],
    )
    tools = SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))
    result = tools.execute(
        "analyze_workout", {"start": "2026-10-20", "end": "2026-10-20", "exercise_type": "SWIMMING"}
    )
    assert result["sessions"] == 3
    assert result["total_hours"] == 4
    assert result["by_type"]["SWIMMING"]["average_heart_rate_bpm"] == 120
    assert result["by_type"]["SWIMMING"]["heart_rate_observed_sessions"] == 2
    assert result["by_type"]["SWIMMING"]["heart_rate_observed_hours"] == 3


def test_descriptive_percentage_comparison_is_not_a_threshold_factory_request():
    from google_health_viewer.agent_runtime_efficiency_patch import (
        _required_composite_tools,
        _supplement_factory_hint,
    )

    question = "Confronta la mia HRV media e mediana tra settembre e ottobre e la variazione percentuale della media."
    hint = _supplement_factory_hint(question, {"signals": ["threshold/frequency analysis"]})
    assert hint["consider_reusable_tool"] is False
    assert "analyze_metric_threshold_responses" not in _required_composite_tools(question)


def test_sleep_debt_cannot_replace_main_night_with_last_short_session(archive):
    _, tools = archive
    result = tools.execute("calculate_sleep_debt", {"end": "2026-09-06"})
    assert result["recent_nights"] == 6
    assert result["recent_mean_hours"] == pytest.approx(
        ((400 + 410 + 420 + 430 + 440 + 450 + 21) / 60) / 6, abs=0.01
    )


def test_stage_minutes_are_precomputed_and_main_scope_excludes_other_sessions(tmp_path):
    from google_health_viewer.agent_tool_factory import EnhancedSafeToolExecutor

    health = HealthStore(tmp_path / "health.sqlite3")
    main = make_sleep("2026-09-06", 300)
    main["sleep"]["stages"] = [
        {"type": "DEEP", "startTime": "2026-09-06T01:00:00Z", "endTime": "2026-09-06T02:50:00Z"},
        {"type": "LIGHT", "startTime": "2026-09-06T02:50:00Z", "endTime": "2026-09-06T06:00:00Z"},
    ]
    extra = make_sleep("2026-09-06", 30, main=None, name="additional")
    extra["sleep"]["stages"] = [
        {"type": "DEEP", "startTime": "2026-09-06T20:00:00Z", "endTime": "2026-09-06T20:30:00Z"}
    ]
    health.upsert_records("sleep", [main, extra])
    tools = EnhancedSafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))
    result = tools.execute(
        "get_sleep_stage_series", {"start": "2026-09-06", "end": "2026-09-06", "scope": "main"}
    )
    assert result["excluded_session_records"] == 1
    assert result["daily_stages"][0]["date"] == "2026-09-06"
    assert result["daily_stages"][0]["minutes"]["deep"] == 110
    assert result["daily_stages"][0]["duration_text"]["deep"] == "1 h 50 min"


def test_uncomputed_comparison_is_verified_before_publishing(archive, monkeypatch):
    from google_health_viewer.agent_runtime_v2 import AgentRuntime

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": "La media è aumentata del 99%.", "_done_reason": "stop"}
        if len(calls) == 2:
            assert {s["function"]["name"] for s in kwargs["tools"]} == {"compare_periods"}
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "compare_periods",
                            "arguments": {
                                "metric": "hrv",
                                "start_a": "2026-09-01",
                                "end_a": "2026-09-03",
                                "start_b": "2026-09-04",
                                "end_b": "2026-09-06",
                            },
                        }
                    }
                ]
            }
        return {"content": "Confronto verificato.", "_done_reason": "stop"}

    monkeypatch.setattr(runtime, "_chat_once", chat)
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question="Confronta la mia HRV nei due periodi.",
        history=[],
        max_tokens=1024,
        model_context_limit=8192,
        performance_profile="standard",
        thread_id=None,
    )
    assert "99%" not in answer
    assert "70" in answer and "55" in answer
    assert len(calls) == 2


def test_offering_a_reminder_does_not_erase_workout_answer(archive):
    from google_health_viewer.agent_runtime_v2 import AgentRuntime, _persistence_claim

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    text = "Allenamenti registrati: 0. Se desideri monitorare questa attività, posso impostare un promemoria."
    assert _persistence_claim(text) is None
    assert runtime._verified_persistence_answer(text) == text


def test_persistence_correction_preserves_actual_analysis(archive):
    from google_health_viewer.agent_runtime_v2 import AgentRuntime

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    answer = runtime._verified_persistence_answer(
        "La HRV media è 70 ms. Ho creato il tool confronto_hrv."
    )
    assert "La HRV media è 70 ms." in answer
    assert "Nessun nuovo tool" in answer


def test_explicit_empty_and_partial_periods_are_not_replaced_by_archive_dates():
    from google_health_viewer.ai_query_planner_core import resolve_data_plan

    catalog = {
        "datasets": [{"key": "weight", "first_date": "2026-08-23", "last_date": "2026-09-20"}]
    }
    plan = resolve_data_plan(
        {
            "data_types": ["weight"],
            "window": "date_range",
            "start_date": "2026-08-27",
            "end_date": "2026-10-27",
        },
        catalog,
    )
    assert plan["days"] == 62
    assert plan["end_date"] == "2026-10-27"
    empty = resolve_data_plan(
        {
            "data_types": ["weight"],
            "window": "date_range",
            "start_date": "2026-11-01",
            "end_date": "2026-11-01",
        },
        catalog,
    )
    assert empty["start_date"] == empty["end_date"] == "2026-11-01"


def test_threshold_summary_retains_counts_without_forward_fill(archive):
    from google_health_viewer.agent_tool_factory import EnhancedSafeToolExecutor

    health, tools = archive
    health.upsert_records(
        "steps",
        [
            {"name": str(i), "startTime": f"2026-09-0{i + 1}T12:00:00Z", "steps": {"count": v}}
            for i, v in enumerate([100, 100, 100, 100, 200, 200])
        ],
    )
    ex = EnhancedSafeToolExecutor(health, tools.agent_store)
    args = {
        "start": "2026-09-01",
        "end": "2026-09-06",
        "event_metric": "steps",
        "response_metrics": ["hrv"],
        "percent": 30,
        "response_offset_days": 1,
    }
    result = ex.execute("analyze_metric_threshold_responses", args)
    assert result["event_day_count"] == 2
    assert result["responses"]["hrv"]["matched_event_days"] == 1
    assert result["responses"]["hrv"]["missing_event_days"] == 1
    assert result["responses"]["hrv"]["matched_mean"] == 25
    assert result["responses"]["hrv"]["days_below_baseline"] == 1
    assert result["responses"]["hrv"]["percent_below_baseline"] == 100


def test_malformed_local_tool_call_retries_once_with_compact_no_thinking_request(
    archive, monkeypatch
):
    from copy import deepcopy

    from google_health_viewer.agent_runtime import AgentRuntime

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    captured = []

    class Response:
        reason = "Bad Request"

        def __init__(self, status, payload):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    def post(*_, **kwargs):
        captured.append(deepcopy(kwargs["json"]))
        if len(captured) == 1:
            return Response(
                400, {"error": "XML syntax error on line 8: parameter closed by function"}
            )
        return Response(200, {"message": {"content": "", "tool_calls": []}, "done_reason": "stop"})

    monkeypatch.setattr("google_health_viewer.agent_runtime.requests.post", post)
    message = runtime._chat_once(
        model="test",
        messages=[],
        tools=[{"type": "function", "function": {"name": "get_available_metrics"}}],
        num_ctx=8192,
        num_predict=640,
        think=True,
        cancel_callback=None,
    )
    assert len(captured) == 2
    assert captured[0]["think"] is True
    assert captured[1]["think"] is False
    assert captured[1]["options"]["num_predict"] <= 2048
    assert message["_done_reason"] == "stop"


def test_compressed_one_item_sample_keeps_latest_evidence():
    from google_health_viewer.agent_runtime_adaptive_patch import _sample_list

    sampled = _sample_list([{"value": i} for i in range(20)], 1, 0)
    assert sampled["last_items"] == [{"value": 19}]


def test_routine_hrv_does_not_silently_use_intraday_variant(archive):
    health, tools = archive
    health.upsert_records(
        "heart-rate-variability",
        [
            {
                "name": "raw-sample",
                "startTime": "2026-09-01T03:00:00Z",
                "heartRateVariability": {"rootMeanSquareOfSuccessiveDifferencesMilliseconds": 99},
            }
        ],
    )
    tools._request_context = "Confronta la mia HRV nei due periodi."
    with pytest.raises(ValueError, match="source daily HRV"):
        tools.execute(
            "get_daily_summary",
            {"metric": "heart-rate-variability", "start": "2026-09-01", "end": "2026-09-06"},
        )
    result = tools.execute(
        "get_daily_summary", {"metric": "hrv", "start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["data_type"] == "daily-heart-rate-variability"
    tools._request_context = "Mostra i campioni HRV intraday."
    raw = tools.execute(
        "get_metric_series",
        {"metric": "heart-rate-variability", "start": "2026-09-01", "end": "2026-09-06"},
    )
    assert raw["summary"]["latest"] == 99


def test_previous_week_comparison_rejects_eight_day_window(archive):
    _, tools = archive
    tools._comparison_windows = {
        "focus": ("2026-09-06", "2026-09-06"),
        "previous_week": ("2026-08-30", "2026-09-05"),
    }
    args = {
        "metric": "hrv",
        "start_a": "2026-08-29",
        "end_a": "2026-09-05",
        "start_b": "2026-09-06",
        "end_b": "2026-09-06",
    }
    with pytest.raises(ValueError, match="seven complete days"):
        tools.execute("compare_periods", args)
    result = tools.execute("compare_periods", {**args, "start_a": "2026-08-30"})
    assert result["period_a"]["expected_days"] == 7
    assert result["period_a"]["observed_days"] == 5


def test_multi_metric_comparison_waits_for_both_computed_results(archive, monkeypatch):
    from google_health_viewer.agent_runtime_v2 import AgentRuntime

    health, tools = archive
    runtime = AgentRuntime(health, tools.agent_store)
    calls = []
    args = {
        "start_a": "2026-09-01",
        "end_a": "2026-09-03",
        "start_b": "2026-09-04",
        "end_b": "2026-09-06",
    }

    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            return {"content": "Everything improved by 99%.", "_done_reason": "stop"}
        metric = "hrv" if len(calls) == 1 else "sleep"
        return {
            "tool_calls": [
                {"function": {"name": "compare_periods", "arguments": {**args, "metric": metric}}}
            ]
        }

    monkeypatch.setattr(runtime, "_chat_once", chat)
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question="Confronta HRV e sonno nei due periodi.",
        history=[],
        max_tokens=1024,
        model_context_limit=8192,
        performance_profile="standard",
        thread_id=None,
    )
    assert len(calls) == 1
    assert "HRV" in answer and "(h)" in answer
    assert "99%" not in answer


def test_fact_renderer_keeps_bedtime_direction_and_measured_baseline(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer

    _, tools = archive
    r = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    r["low_metric_events"]["2026-09-06"]["bedtime_delay_minutes"] = 30
    answer = render_fact_answer(
        "Correlazione HRV e sonno",
        [("analyze_sleep_metric_association", r)],
        {"analyze_sleep_metric_association"},
        "it",
    )
    assert "69,5" in answer
    assert "30 min più tardi" in answer
    assert "7 h 30 min" in answer


def test_response_threshold_does_not_claim_exact_builtin_capability():
    from google_health_viewer.agent_runtime_efficiency_patch import _supplement_factory_hint
    from google_health_viewer.agent_runtime_v2 import _factory_capability

    hint = {"signals": ["relative personal-baseline threshold", "threshold/frequency analysis"]}
    composed = _supplement_factory_hint(
        "Quando i passi superano del 30% la mia mediana personale, HRV scende del 20%?", hint
    )
    assert _factory_capability(composed).endswith(".response_threshold")


def test_exact_date_focus_does_not_change_explicit_ranges():
    from google_health_viewer.agent_answer_facts import single_date_focus

    assert single_date_focus("Notte terminata il 6 settembre 2026") == "2026-09-06"
    assert single_date_focus("Night ending September 6, 2026") == "2026-09-06"
    assert single_date_focus("Dal 27 agosto al 27 ottobre 2026") is None


def test_nightly_temperature_default_is_measured_value_not_baseline(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records(
        "daily-sleep-temperature-derivations",
        [
            {
                "name": "night",
                "startTime": "2026-09-01T00:00:00Z",
                "dailySleepTemperatureDerivations": {
                    "nightlyTemperatureCelsius": 36.2,
                    "baselineTemperatureCelsius": 35.8,
                    "relativeNightlyStddev30dCelsius": 0.1,
                },
            }
        ],
    )
    tools = SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))
    result = tools.execute(
        "get_metric_series",
        {
            "metric": "daily-sleep-temperature-derivations",
            "start": "2026-09-01",
            "end": "2026-09-01",
        },
    )
    assert result["field"].endswith("nightlyTemperatureCelsius")
    assert result["summary"]["latest"] == 36.2


def test_lagged_correlation_pairs_calendar_dates_and_preserves_missingness(archive):
    _, tools = archive
    result = tools.execute(
        "calculate_correlations",
        {
            "metric_a": "hrv",
            "metric_b": "hrv",
            "start": "2026-09-01",
            "end": "2026-09-06",
            "offset_days": 1,
        },
    )
    assert result["paired_days"] == 5
    assert result["metric_a_observed_days"] == 6
    assert result["metric_b_observed_days"] == 5
    assert result["offset_days"] == 1
    assert result["pearson_r"] is not None


def test_fact_metric_extrema_use_actual_observation_dates(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer

    _, tools = archive
    result = tools.execute(
        "get_metric_series", {"metric": "hrv", "start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["summary"]["minimum_date"] == "2026-09-06"
    assert result["summary"]["maximum_date"] == "2026-09-04"
    answer = render_fact_answer(
        "Media minimo massimo HRV", [("get_metric_series", result)], {"get_metric_series"}, "it"
    )
    assert "25 ms** (2026-09-06)" in answer
    assert "72 ms** (2026-09-04)" in answer


def test_sleep_stage_period_attributes_overnight_stages_to_wake_date(archive):
    from google_health_viewer.ai_insights import _sleep_stage_period

    health, _ = archive
    record = next(r for r in health.list_records("sleep") if r["end_time"].startswith("2026-09-01"))
    record["payload"] = {"sleep": {"stagesSummary": [{"type": "deep", "minutes": 90}]}}
    assert _sleep_stage_period([record], date(2026, 9, 1), date(2026, 9, 1))["sessions"] == 1
    assert _sleep_stage_period([record], date(2026, 8, 31), date(2026, 8, 31))["sessions"] == 0


def test_workout_facts_do_not_infer_complete_recording(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer
    from google_health_viewer.agent_runtime_v2 import _required_calculation_tools

    _, tools = archive
    q = "Quanti allenamenti di nuoto ho registrato?"
    assert _required_calculation_tools(q) == {"analyze_workout"}
    result = tools.execute(
        "analyze_workout", {"start": "2026-09-01", "end": "2026-09-06", "exercise_type": "SWIMMING"}
    )
    answer = render_fact_answer(q, [("analyze_workout", result)], {"analyze_workout"}, "it")
    assert "**0**" in answer
    assert "non dimostra" in answer
    assert "cardiaca non disponibile" in answer


def test_nutrition_facts_preserve_unknown_intake(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer
    from google_health_viewer.agent_runtime_v2 import _required_calculation_tools

    _, tools = archive
    q = "Quante calorie e proteine nella mia alimentazione?"
    assert _required_calculation_tools(q) == {"summarize_nutrition_logs"}
    result = tools.execute("summarize_nutrition_logs", {"start": "2026-09-01", "end": "2026-09-06"})
    answer = render_fact_answer(
        q, [("summarize_nutrition_logs", result)], {"summarize_nutrition_logs"}, "it"
    )
    assert "**non disponibile kcal**" in answer
    assert "non equivale a un apporto nullo" in answer


def test_unique_field_suffix_resolves_and_bad_field_is_not_missing_data(archive):
    _, tools = archive
    args = {"start": "2026-09-01", "end": "2026-09-06"}
    result = tools.execute(
        "get_metric_series",
        {**args, "metric": "daily-heart-rate-variability:averageHeartRateVariabilityMilliseconds"},
    )
    assert result["observed_days"] == 6
    assert result["summary"]["minimum"] == 25
    with pytest.raises(ValueError, match="Unknown or ambiguous metric field"):
        tools.execute(
            "get_metric_series", {**args, "metric": "daily-heart-rate-variability:nonexistent"}
        )


def test_resolved_followup_cannot_repeat_previous_week(archive):
    _, tools = archive
    tools._resolved_followup_window = ("2026-09-01", "2026-09-06")
    with pytest.raises(ValueError, match="week already analysed"):
        tools.execute(
            "get_metric_series", {"metric": "hrv", "start": "2026-09-02", "end": "2026-09-06"}
        )
    result = tools.execute(
        "get_metric_series", {"metric": "hrv", "start": "2026-09-01", "end": "2026-09-06"}
    )
    assert result["observed_days"] == 6


def test_implicit_period_comparison_requires_calculation():
    from google_health_viewer.agent_runtime_v2 import _required_calculation_tools

    assert _required_calculation_tools(
        "La mia frequenza a riposo è aumentata dal 1 al 15 ottobre rispetto al 1–15 settembre 2026? Dammi le medie."
    ) == {"compare_periods"}


def test_open_sleep_summary_distinguishes_daily_totals_and_trend_method(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer
    from google_health_viewer.agent_runtime_v2 import _required_calculation_tools

    _, tools = archive
    q = "Come sta andando il mio sonno ultimamente?"
    assert _required_calculation_tools(q) == {"get_metric_series"}
    r = tools.execute(
        "get_metric_series", {"metric": "sleep", "start": "2026-09-01", "end": "2026-09-06"}
    )
    answer = render_fact_answer(q, [("get_metric_series", r)], {"get_metric_series"}, "it")
    assert "comprese le sessioni aggiuntive" in answer
    assert "prime 2 e delle ultime 2 osservazioni" in answer
    assert "non una misura di miglioramento" in answer


def test_health_overview_reports_per_metric_coverage_and_exact_differences(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer
    from google_health_viewer.agent_runtime_v2 import _required_calculation_tools

    _, tools = archive
    q = "Fammi una panoramica dei miei dati di salute nell'ultima settimana."
    assert _required_calculation_tools(q) == {"summarize_health_overview"}
    r = tools.execute("summarize_health_overview", {"start": "2026-09-01", "end": "2026-09-06"})
    assert r["metrics"][0]["summary"]["first_to_last_change"] == -45
    assert r["metrics"][0]["observed_days"] == 6
    assert r["metrics"][1]["observed_days"] == 0
    assert all("points" not in m for m in r["metrics"])
    assert len(json.dumps(r)) < 10000
    answer = render_fact_answer(
        q, [("summarize_health_overview", r)], {"summarize_health_overview"}, "it"
    )
    assert "**-45 ms**" in answer
    assert "Nessuna osservazione disponibile" in answer
    assert "non dimostrano miglioramenti clinici" in answer
    with pytest.raises(ValueError, match="1 to 8"):
        tools.execute("summarize_health_overview", {"metrics": ["hrv"] * 9})


def test_sleep_narrative_explains_direction_without_inventing_causes(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer

    _, tools = archive
    r = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    r.update(duration_pearson_r=0.12, bedtime_pearson_r=-0.49)
    answer = render_fact_answer(
        "HRV e sonno",
        [("analyze_sleep_metric_association", r)],
        {"analyze_sleep_metric_association"},
        "it",
    )
    assert "soprattutto **l'orario" in answer
    assert "vicino a zero" in answer
    assert "più tardi tendono a corrispondere valori più bassi" in answer
    assert "non dimostra" in answer
    assert "| Data |" in answer
    assert "25 ms" in answer
    r.update(bedtime_pearson_r=0.49)
    positive = render_fact_answer(
        "HRV e sonno",
        [("analyze_sleep_metric_association", r)],
        {"analyze_sleep_metric_association"},
        "it",
    )
    assert "valori più alti" in positive
    assert "valori più bassi" not in positive


def test_sleep_narrative_handles_weak_association_and_distinct_timing_coverage(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer

    _, tools = archive
    r = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    r.update(duration_pearson_r=0.05, bedtime_pearson_r=0.04, bedtime_paired_days=4)
    answer = render_fact_answer(
        "HRV e sonno",
        [("analyze_sleep_metric_association", r)],
        {"analyze_sleep_metric_association"},
        "it",
    )
    assert "Non emerge un'associazione lineare evidente" in answer
    assert "4 notti con un inizio utilizzabile" in answer
    assert "soprattutto **l'orario" not in answer


def test_sleep_narrative_does_not_rank_an_unavailable_correlation(archive):
    from google_health_viewer.agent_answer_facts import render_fact_answer

    _, tools = archive
    r = tools.execute(
        "analyze_sleep_metric_association", {"start": "2026-09-01", "end": "2026-09-06"}
    )
    r.update(duration_pearson_r=None, bedtime_pearson_r=-0.8)
    answer = render_fact_answer(
        "HRV e sonno",
        [("analyze_sleep_metric_association", r)],
        {"analyze_sleep_metric_association"},
        "it",
    )
    assert "non stimare il coefficiente della durata" in answer
    assert "più marcato di quello della durata" not in answer
