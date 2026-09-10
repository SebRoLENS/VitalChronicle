from __future__ import annotations

import json
from pathlib import Path

import pytest

from google_health_viewer.agent_runtime import (
    AGENT_TRACE_PREFIX,
    _tool_calling_unavailable_error,
)
from google_health_viewer.agent_runtime_v2 import AgentRuntime, _factory_hint
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tool_factory import EnhancedSafeToolExecutor
from google_health_viewer.ai_engine import TOKEN_USAGE_PREFIX


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


class FactoryGateRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0
        self.available_by_turn: list[set[str]] = []

    def _chat_once(self, **kwargs):
        self.turn += 1
        names = {
            str(item.get("function", {}).get("name") or "")
            for item in kwargs.get("tools", [])
            if isinstance(item, dict)
        }
        self.available_by_turn.append(names)
        if self.turn <= 3:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_metric_series",
                            "arguments": {"metric": f"guessed-metric-{self.turn}"},
                        }
                    }
                ],
            }
        if self.turn == 4:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "forced_factory_tool",
                                "description": "Reusable threshold and temporal analysis",
                                "capability": "analysis.composed.personal_baseline_temporal_event_response",
                                "pipeline": [{"op": "return"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale dopo la decisione della Tool Factory."}


def test_complex_query_stops_repeated_raw_metric_probing_and_forces_factory(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = FactoryGateRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    events: list[str] = []

    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando il carico supera del 20% la baseline personale, il sonno profondo della notte "
            "successiva diminuisce e dopo quanti giorni torna al livello abituale?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="gate-thread",
        event_callback=events.append,
    )

    assert answer.startswith("Risposta finale")
    assert runtime.turn == 5
    assert runtime.available_by_turn[3] == {"create_learned_tool"}
    assert any("raw-series probing stopped" in event.lower() for event in events)
    assert any("registry checked" in event.lower() for event in events)


class GateRefusalRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0
        self.available_by_turn: list[set[str]] = []

    def _chat_once(self, **kwargs):
        self.turn += 1
        names = {
            str(item.get("function", {}).get("name") or "")
            for item in kwargs.get("tools", [])
            if isinstance(item, dict)
        }
        self.available_by_turn.append(names)
        if self.turn <= 4:
            return {
                "content": "",
                "tool_calls": [{"function": {"name": "get_available_metrics", "arguments": {}}}],
            }
        if self.turn == 5:
            return {"content": "Non ci sono dati, quindi rispondo subito."}
        if self.turn == 6:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "gate_resolved_tool",
                                "description": "Reusable composed temporal analysis",
                                "capability": "analysis.composed.personal_baseline.temporal_event_response",
                                "pipeline": [{"op": "return"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale dopo la creazione del tool."}


def test_factory_gate_rejects_direct_answer_until_capability_is_resolved(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = GateRefusalRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    events: list[str] = []
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando il carico supera del 20% la baseline personale, il sonno profondo della notte "
            "successiva diminuisce e dopo quanti giorni torna al livello abituale?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="gate-refusal-thread",
        event_callback=events.append,
    )
    assert answer.startswith("Risposta finale dopo")
    assert runtime.turn == 7
    assert runtime.available_by_turn[4] == {"create_learned_tool"}
    assert runtime.available_by_turn[5] == {"create_learned_tool"}
    assert any("direct answer blocked" in event.lower() for event in events)


class ToolNameAsMetricRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0

    def _chat_once(self, **kwargs):
        self.turn += 1
        if self.turn == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_data_coverage",
                            "arguments": {"metric": "calculate_cardio_load"},
                        }
                    }
                ],
            }
        if self.turn == 2:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "semantic_metric_router",
                                "description": "Use semantic built-ins instead of tool names as metrics",
                                "capability": "analysis.composed.personal_baseline.temporal_event_response",
                                "pipeline": [{"op": "return"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale corretta."}


def test_tool_name_cannot_be_misused_as_raw_metric_identifier(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = ToolNameAsMetricRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    events: list[str] = []
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando il carico supera del 20% la baseline personale, il sonno profondo della notte "
            "successiva diminuisce e dopo quanti giorni torna al livello abituale?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="tool-name-thread",
        event_callback=events.append,
    )
    assert answer == "Risposta finale corretta."
    assert any("tool name rejected" in event.lower() for event in events)


class StrictGateHallucinationRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0
        self.available_by_turn: list[set[str]] = []

    def _chat_once(self, **kwargs):
        self.turn += 1
        names = {
            str(item.get("function", {}).get("name") or "")
            for item in kwargs.get("tools", [])
            if isinstance(item, dict)
        }
        self.available_by_turn.append(names)
        if self.turn <= 4:
            return {
                "content": "",
                "tool_calls": [{"function": {"name": "get_available_metrics", "arguments": {}}}],
            }
        if self.turn == 5:
            # Reproduce a local model hallucinating a tool that was NOT advertised
            # while the runtime gate exposed only create_learned_tool.
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_metric_series",
                            "arguments": {"metric": "active-energy-burned"},
                        }
                    }
                ],
            }
        if self.turn == 6:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "strict_gate_tool",
                                "description": "Reusable temporal baseline analysis",
                                "capability": "analysis.composed.personal_baseline.temporal_event_response",
                                "pipeline": [{"op": "return"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale dopo il gate runtime."}


def test_gate_rejects_hallucinated_tool_not_exposed_by_schema(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = StrictGateHallucinationRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    events: list[str] = []

    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando il carico supera del 20% la baseline personale, il sonno profondo della notte "
            "successiva diminuisce e dopo quanti giorni torna al livello abituale?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="strict-gate-thread",
        event_callback=events.append,
    )

    assert answer == "Risposta finale dopo il gate runtime."
    assert runtime.available_by_turn[4] == {"create_learned_tool"}
    assert runtime.available_by_turn[5] == {"create_learned_tool"}
    assert any("out-of-scope tool call" in event.lower() for event in events)
    assert not any(event == "Using tool: get_metric_series" for event in events)


class SleepStageHealthStore(DummyHealthStore):
    def list_records(self, data_type, *_args, **_kwargs):
        if data_type != "sleep":
            return []
        return [
            {
                "start_time": "2026-08-01T22:00:00+00:00",
                "end_time": "2026-08-02T06:00:00+00:00",
                "payload": {
                    "sleep": {
                        "stages": [
                            {
                                "stage": 5,
                                "startTime": "2026-08-01T23:00:00+00:00",
                                "endTime": "2026-08-02T00:30:00+00:00",
                            },
                            {
                                "stage": 6,
                                "startTime": "2026-08-02T00:30:00+00:00",
                                "endTime": "2026-08-02T01:30:00+00:00",
                            },
                        ]
                    }
                },
            }
        ]


def test_sleep_stage_series_uses_health_connect_codes_and_wakeup_date(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(SleepStageHealthStore(tmp_path / "health.sqlite3"), store)
    result = executor.execute(
        "get_sleep_stage_series", {"start": "2026-08-01", "end": "2026-08-02"}
    )

    assert result["sleep_session_records"] == 1
    assert result["sessions_with_stages"] == 1
    assert result["daily_stages"][0]["date"] == "2026-08-02"
    assert result["daily_stages"][0]["deep"] == 1.5
    assert result["date_semantics"] == "wake_up_date"


def test_agent_analysis_budget_is_fifteen_steps():
    from google_health_viewer import agent_runtime_v2

    assert agent_runtime_v2.MAX_ANALYSIS_STEPS == 15


def test_factory_schema_includes_canonical_event_response_example(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    functions = {item["function"]["name"]: item["function"] for item in executor.tool_schemas()}
    create_schema = functions["create_learned_tool"]["parameters"]
    pipeline_examples = create_schema["properties"]["pipeline"]["examples"]
    parameters_examples = create_schema["properties"]["parameters"]["examples"]

    assert pipeline_examples[0][0]["tool"] == "calculate_cardio_load"
    assert any(step.get("tool") == "get_sleep_stage_series" for step in pipeline_examples[0])
    assert any(step.get("op") == "event_response" for step in pipeline_examples[0])
    assert parameters_examples[0]["properties"]["event_percent"]["default"] == 30


def test_learned_tool_applies_parameter_defaults(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = StubToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    pipeline = _original_question_pipeline()
    pipeline[3]["percent"] = "$event_percent"
    pipeline[7]["response_percent"] = "$response_percent"
    pipeline[7]["recovery_tolerance_percent"] = "$recovery_tolerance_percent"
    created = executor.execute(
        "create_learned_tool",
        {
            "name": "defaulted_event_response",
            "description": "Reusable event response with parameter defaults.",
            "capability": "analysis.event_response.defaults",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "event_percent": {"type": "number", "default": 30},
                    "response_percent": {"type": "number", "default": 20},
                    "recovery_tolerance_percent": {"type": "number", "default": 10},
                },
            },
            "pipeline": pipeline,
        },
    )
    assert created["status"] == "created"

    answer = executor.execute(
        "defaulted_event_response",
        {"start": "2026-01-01", "end": "2026-01-08"},
    )["result"]
    assert answer["trigger_events"] == 2
    assert answer["response_matches"] == 2
    assert answer["response_rate_percent"] == pytest.approx(100.0)


class ExhaustedRepairHallucinationRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0
        self.available_by_turn: list[set[str]] = []

    def _chat_once(self, **kwargs):
        self.turn += 1
        names = {
            str(item.get("function", {}).get("name") or "")
            for item in kwargs.get("tools", [])
            if isinstance(item, dict)
        }
        self.available_by_turn.append(names)
        if self.turn <= 3:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "repair_budget_test",
                                "description": "Invalid until budget exhaustion",
                                "capability": "analysis.repair_budget",
                                "pipeline": [{"op": "invented_operation"}],
                            },
                        }
                    }
                ],
            }
        if self.turn == 4:
            # Deliberately hallucinate the now-hidden factory function. Runtime must block it.
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_learned_tool",
                            "arguments": {
                                "name": "should_never_be_created",
                                "description": "Must be blocked after repair exhaustion",
                                "capability": "analysis.must_not_create",
                                "pipeline": [{"op": "return"}],
                            },
                        }
                    }
                ],
            }
        return {"content": "Risposta finale deterministica dopo il repair budget."}


def test_factory_cannot_execute_fourth_create_after_three_failed_repairs(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = ExhaustedRepairHallucinationRuntime(
        DummyHealthStore(tmp_path / "health.sqlite3"), store
    )
    events: list[str] = []
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question=(
            "Quando il carico supera del 30% la baseline personale, quanto spesso la notte "
            "successiva il sonno profondo scende del 20% e quanto impiega a recuperare?"
        ),
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="repair-exhaustion-thread",
        event_callback=events.append,
    )

    assert answer.startswith("Risposta finale deterministica")
    assert runtime.turn == 5
    assert "create_learned_tool" not in runtime.available_by_turn[3]
    assert store.tool("should_never_be_created") is None
    assert any("unavailable in this turn" in event.lower() for event in events)


def test_agent_runtime_emits_exact_cumulative_token_usage_and_exchange(monkeypatch, tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    runtime = AgentRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    telemetry: list[str] = []
    runtime._reset_agent_telemetry(telemetry.append)

    payloads = iter(
        [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_available_metrics",
                                "arguments": {},
                            }
                        }
                    ],
                },
                "prompt_eval_count": 120,
                "eval_count": 18,
                "eval_duration": 1_000_000_000,
            },
            {
                "message": {"content": "Final answer"},
                "prompt_eval_count": 220,
                "eval_count": 30,
                "eval_duration": 2_000_000_000,
            },
        ]
    )

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "google_health_viewer.agent_runtime.requests.post",
        lambda *_args, **_kwargs: FakeResponse(next(payloads)),
    )

    runtime._telemetry_phase = "agent step 1"
    runtime._chat_once(
        model="test",
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        num_ctx=4096,
        num_predict=512,
        think=False,
        cancel_callback=None,
    )
    runtime._telemetry_phase = "agent step 2"
    runtime._chat_once(
        model="test",
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        num_ctx=4096,
        num_predict=512,
        think=False,
        cancel_callback=None,
    )

    usage = [
        json.loads(item[len(TOKEN_USAGE_PREFIX) :])
        for item in telemetry
        if item.startswith(TOKEN_USAGE_PREFIX)
    ]
    assert len(usage) == 2
    assert usage[0]["exact"] is True
    assert usage[0]["input_tokens"] == 120
    assert usage[0]["generated_tokens"] == 18
    assert usage[1]["call"] == 2
    assert usage[1]["total_input_tokens"] == 340
    assert usage[1]["total_generated_tokens"] == 48
    assert usage[1]["total_tokens"] == 388

    traces = [
        json.loads(item[len(AGENT_TRACE_PREFIX) :])
        for item in telemetry
        if item.startswith(AGENT_TRACE_PREFIX)
    ]
    assert any(
        item["kind"] == "tool_call" and item["target"] == "get_available_metrics"
        for item in traces
    )
    assert any(
        item["kind"] == "assistant" and item["content"] == "Final answer"
        for item in traces
    )


def test_agent_analysis_reports_tool_results_in_exchange(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")

    class TraceRuntime(AgentRuntime):
        def __init__(self, health_store, agent_store):
            super().__init__(health_store, agent_store)
            self.turn = 0

        def _chat_once(self, **_kwargs):
            self.turn += 1
            if self.turn == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_available_metrics", "arguments": {}}}
                    ],
                }
            return {"content": "Done"}

    runtime = TraceRuntime(DummyHealthStore(tmp_path / "health.sqlite3"), store)
    telemetry: list[str] = []
    answer = runtime.analyze(
        model="test",
        snapshot={},
        question="Quali metriche sono disponibili?",
        history=[],
        max_tokens=1024,
        model_context_limit=None,
        performance_profile="standard",
        thread_id="trace-thread",
        prompt_callback=telemetry.append,
    )
    assert answer == "Done"
    traces = [
        json.loads(item[len(AGENT_TRACE_PREFIX) :])
        for item in telemetry
        if item.startswith(AGENT_TRACE_PREFIX)
    ]
    assert any(
        item["kind"] == "tool_result" and item["source"] == "get_available_metrics"
        for item in traces
    )


def test_generic_tool_word_does_not_trigger_unsupported_model_fallback():
    assert _tool_calling_unavailable_error(
        "The local model returned neither an answer nor a tool call."
    ) is False
    assert _tool_calling_unavailable_error("model does not support tools") is True
