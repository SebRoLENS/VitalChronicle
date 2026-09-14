from __future__ import annotations

from google_health_viewer import agent_runtime as base_rt
from google_health_viewer import agent_runtime_v2 as runtime_v2
from google_health_viewer import agent_tool_factory as factory
from google_health_viewer import agent_tool_factory_reliability_patch as reliability


class _FakeAgentStore:
    def __init__(self) -> None:
        self.saved = None

    def add_learned_tool(self, spec):
        self.saved = spec
        return {"status": "created", "tool": spec}


def test_common_dsl_shape_errors_are_normalized_without_model_retry():
    broken = [
        {"op": "load_series", "arguments": {"metric": "active-minutes"}},
        {"op": "get_baseline", "source": "$data", "as": "baseline_attivi"},
        {
            "op": "filter_relative",
            "source": "$data",
            "baseline_source": "$baseline_attivi",
            "direction": "above",
            "percent": 30,
            "as": "events",
        },
    ]

    normalized = reliability._normalize_pipeline(broken)
    assert normalized[0]["metric"] == "active-minutes"
    assert normalized[0]["as"] == "step_1"
    assert normalized[1]["op"] == "baseline"
    assert normalized[1]["source"] == "step_1"
    assert normalized[2]["baseline_source"] == "baseline_attivi"


def test_threshold_factory_failure_is_rewritten_to_canonical_deterministic_tool():
    executor = object.__new__(factory.EnhancedSafeToolExecutor)
    executor.agent_store = _FakeAgentStore()

    result = executor._tool_create_learned_tool(
        {
            "name": "analizza_sovraccarico_attivo_risposta_hrv_fcr",
            "description": "Threshold response test",
            "capability": "analysis.composed.personal_baseline.threshold_frequency",
            "pipeline": [
                {"op": "load_series", "arguments": {"metric": "active-minutes"}},
                {"op": "get_baseline", "source": "$data", "as": "baseline_attivi"},
                {
                    "op": "filter_relative",
                    "source": "$data",
                    "baseline_source": "$baseline_attivi",
                    "direction": "above",
                    "percent": 30,
                    "as": "giorni_sovraccarico",
                },
                {
                    "op": "load_series",
                    "arguments": {"metric": "daily-heart-rate-variability"},
                },
                {
                    "op": "load_series",
                    "arguments": {"metric": "daily-resting-heart-rate"},
                },
            ],
        }
    )

    assert result["status"] == "created"
    assert result["auto_repaired"] is True
    assert result["repair_strategy"] == "canonical_deterministic_threshold_response"
    saved = executor.agent_store.saved
    assert saved is not None
    assert saved["pipeline"][0]["op"] == "call_tool"
    assert saved["pipeline"][0]["tool"] == reliability._BUILTIN_NAME
    assert saved["pipeline"][0]["arguments"]["event_metric"] == "active-minutes"
    assert saved["pipeline"][0]["arguments"]["response_metrics"] == [
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
    ]
    assert saved["pipeline"][1] == {"op": "return", "source": "analysis"}


def test_threshold_primitive_is_present_in_dsl_tool_enum():
    enum = factory._PIPELINE_STEP_SCHEMA["properties"]["tool"]["enum"]
    assert reliability._BUILTIN_NAME in enum


def test_agent_context_and_schema_budgets_are_bounded():
    reliability._install_runtime_budget()

    assert runtime_v2.MAX_EVIDENCE_ENTRIES == 5
    assert runtime_v2.MAX_EVIDENCE_LIST_ITEMS == 10
    assert runtime_v2.MAX_EVIDENCE_ENTRY_CHARS == 3200
    assert runtime_v2.MAX_EVIDENCE_LEDGER_CHARS == 7600

    history = [
        {"role": "user", "content": "x" * 2000},
        {"role": "assistant", "content": "y" * 2000},
    ] * 4
    compacted = base_rt.compact_agent_history(history, maximum=20, message_limit=2000)
    assert len(compacted) == 4
    assert all(len(item["content"]) <= 600 for item in compacted)

    schemas = [
        {
            "type": "function",
            "function": {
                "name": f"dummy_tool_{index}",
                "description": "training recovery analysis metric tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for index in range(30)
    ]
    subset = base_rt.online_tool_subset(schemas, "analizza training recovery", maximum=50)
    assert len(subset) <= 10


def test_large_json_evidence_is_hard_capped():
    reliability._install_runtime_budget()

    text = base_rt._json_text({"values": list(range(5000))}, 20000)
    assert len(text) <= 5100
    assert "truncated" in text
