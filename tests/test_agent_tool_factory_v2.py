from __future__ import annotations

from pathlib import Path

import pytest

from google_health_viewer.agent_runtime_v2 import AgentRuntime, _factory_hint
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tool_factory import EnhancedSafeToolExecutor


class DummyHealthStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def counts(self):
        return {}

    def list_records(self, *_args, **_kwargs):
        return []

    def data_date_bounds(self):
        return None

    def data_revision(self):
        return "test-revision"


class StubToolExecutor(EnhancedSafeToolExecutor):
    def _tool_calculate_cardio_load(self, _args, **_):
        return {
            "daily_load": [
                {"date": "2026-01-01", "load": 100.0},
                {"date": "2026-01-02", "load": 100.0},
                {"date": "2026-01-03", "load": 160.0},
                {"date": "2026-01-04", "load": 100.0},
                {"date": "2026-01-05", "load": 170.0},
                {"date": "2026-01-06", "load": 100.0},
            ]
        }

    def _tool_get_sleep_stage_series(self, _args, **_):
        return {
            "daily_stages": [
                {"date": "2026-01-01", "deep": 1.5},
                {"date": "2026-01-02", "deep": 1.5},
                {"date": "2026-01-03", "deep": 1.5},
                {"date": "2026-01-04", "deep": 1.0},
                {"date": "2026-01-05", "deep": 1.5},
                {"date": "2026-01-06", "deep": 1.1},
                {"date": "2026-01-07", "deep": 1.4},
                {"date": "2026-01-08", "deep": 1.5},
            ]
        }


def _original_question_pipeline():
    return [
        {
            "op": "call_tool",
            "tool": "calculate_cardio_load",
            "arguments": {"start": "$start", "end": "$end"},
            "as": "cardio_raw",
        },
        {
            "op": "extract_series",
            "source": "cardio_raw",
            "path": "daily_load",
            "key_field": "date",
            "value_field": "load",
            "as": "cardio",
        },
        {"op": "baseline", "source": "cardio", "as": "cardio_baseline"},
        {
            "op": "filter_relative",
            "source": "cardio",
            "baseline_source": "cardio_baseline",
            "baseline_field": "median",
            "direction": "above",
            "percent": 30,
            "as": "high_load",
        },
        {
            "op": "call_tool",
            "tool": "get_sleep_stage_series",
            "arguments": {"start": "$start", "end": "$end"},
            "as": "sleep_raw",
        },
        {
            "op": "extract_series",
            "source": "sleep_raw",
            "path": "daily_stages",
            "key_field": "date",
            "value_field": "deep",
            "as": "deep_sleep",
        },
        {"op": "baseline", "source": "deep_sleep", "as": "deep_baseline"},
        {
            "op": "event_response",
            "event_source": "high_load",
            "response_source": "deep_sleep",
            "response_baseline_source": "deep_baseline",
            "baseline_field": "median",
            "response_direction": "below",
            "response_percent": 20,
            "response_offset_days": 1,
            "recovery_tolerance_percent": 10,
            "max_recovery_days": 7,
            "as": "event_analysis",
        },
        {"op": "return", "source": "event_analysis"},
    ]


def test_factory_schema_documents_safe_operations(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    schemas = executor.tool_schemas()
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    assert "get_sleep_stage_series" in functions
    create_schema = functions["create_learned_tool"]["parameters"]
    op_schema = create_schema["properties"]["pipeline"]["items"]["properties"]["op"]
    assert "call_tool" in op_schema["enum"]
    assert "filter_relative" in op_schema["enum"]
    assert "event_response" in op_schema["enum"]


def test_invalid_pipeline_returns_repair_instructions(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    result = executor.execute(
        "create_learned_tool",
        {
            "name": "bad_filter_tool",
            "description": "test",
            "capability": "test.invalid_filter",
            "pipeline": [{"op": "filter"}],
        },
    )

    assert result["status"] == "invalid_pipeline"
    assert result["repairable"] is True
    assert "Unsupported learned-tool operation 'filter'" in result["error"]
    assert "filter_relative" in result["allowed_operations"]
    assert "event_response" in result["dsl_reference"]


def test_learned_tool_can_answer_event_response_and_recovery(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = StubToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    created = executor.execute(
        "create_learned_tool",
        {
            "name": "cardio_load_deep_sleep_recovery",
            "description": "Analyse deep-sleep response after unusually high cardiovascular load.",
            "capability": "analysis.event_conditioned_sleep_recovery",
            "parameters": {
                "type": "object",
                "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            },
            "pipeline": _original_question_pipeline(),
        },
    )
    assert created["status"] == "created"

    answer = executor.execute(
        "cardio_load_deep_sleep_recovery",
        {"start": "2026-01-01", "end": "2026-01-08"},
    )["result"]

    assert answer["trigger_events"] == 2
    assert answer["evaluable_events"] == 2
    assert answer["response_matches"] == 2
    assert answer["response_rate_percent"] == pytest.approx(100.0)
    assert answer["mean_recovery_days"] == pytest.approx(1.0)


def test_complex_question_is_flagged_without_explicit_create_request():
    hint = _factory_hint(
        "Quando il mio carico cardiovascolare supera del 30% la baseline personale, quanto spesso "
        "la notte successiva il sonno profondo diminuisce del 20% e quanti giorni servono perché "
        "torni alla baseline?"
    )
    assert hint["consider_reusable_tool"] is True
    assert len(hint["signals"]) >= 3


class RepairRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0

    def _chat_once(self, **kwargs):
        self.turn += 1
        if self.turn <= 3:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "repair_me",
                                "description": "Reusable test tool",
                                "capability": "test.repair_budget",
                                "pipeline": [{"op": "invented_filter"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale con il capability gap dichiarato."}


def test_factory_repairs_do_not_end_analysis_without_answer(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = RepairRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    events: list[str] = []

    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando un valore supera la baseline del 30%, quanto spesso il giorno successivo "
            "scende del 20% e quanto impiega a recuperare?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="test-thread",
        event_callback=events.append,
    )

    assert answer.startswith("Risposta finale")
    assert runtime.turn == 4
    assert any("repair 3/3" in event for event in events)
    assert not any("maximum tool steps" in event for event in events)
