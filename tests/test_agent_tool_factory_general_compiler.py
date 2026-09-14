from __future__ import annotations

from google_health_viewer import agent_tool_factory as factory
from google_health_viewer import agent_tool_factory_schema_guard as guard


class _FakeStore:
    def __init__(self) -> None:
        self.saved = None

    def add_learned_tool(self, spec):
        self.saved = spec
        return {"status": "created", "tool": spec}


def test_load_series_rejects_metric_not_present_in_live_catalog():
    executor = object.__new__(factory.EnhancedSafeToolExecutor)
    errors, _schemas = guard._validate_nested_calls(
        executor,
        [
            {
                "op": "load_series",
                "metric": "active-minutez",
                "as": "series",
            },
            {"op": "return", "source": "series"},
        ],
        {
            "active-minutes",
            "daily-heart-rate-variability",
            "daily-resting-heart-rate",
        },
    )
    assert any("load_series uses unavailable metric 'active-minutez'" in error for error in errors)


def test_undeclared_runtime_parameter_cannot_be_persisted():
    executor = object.__new__(factory.EnhancedSafeToolExecutor)
    executor.agent_store = _FakeStore()

    result = executor._tool_create_learned_tool(
        {
            "name": "baseline_with_bad_parameter",
            "description": "Should fail before persistence",
            "capability": "test.parameter_validation",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
            },
            "pipeline": [
                {
                    "op": "call_tool",
                    "tool": "get_baseline",
                    "arguments": {
                        "metric": "active-minutes",
                        "start": "$start",
                        "end": "$not_declared",
                    },
                    "as": "baseline",
                },
                {"op": "return", "source": "baseline"},
            ],
        }
    )

    assert result["status"] == "invalid_pipeline"
    assert "undeclared learned-tool parameter '$not_declared'" in result["error"]
    assert executor.agent_store.saved is None
