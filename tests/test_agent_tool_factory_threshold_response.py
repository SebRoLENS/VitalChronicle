from __future__ import annotations

from datetime import datetime

from google_health_viewer import agent_tool_factory as factory
from google_health_viewer import agent_tool_factory_reliability_patch as reliability


def _local_ts(day: str) -> float:
    tz = datetime.now().astimezone().tzinfo
    return datetime.fromisoformat(day).replace(hour=12, tzinfo=tz).timestamp()


class _FakeExecutor:
    def __init__(self) -> None:
        self.data = {
            "active-minutes": {
                "2026-08-26": 154.0,
                "2026-08-27": 230.0,
                "2026-08-28": 345.0,
                "2026-08-29": 318.0,
                "2026-08-30": 162.0,
                "2026-08-31": 239.0,
                "2026-09-01": 292.0,
                "2026-09-02": 258.0,
                "2026-09-03": 205.0,
                "2026-09-04": 257.0,
                "2026-09-05": 474.0,
                "2026-09-06": 420.0,
                "2026-09-07": 347.0,
                "2026-09-08": 236.0,
                "2026-09-09": 294.0,
                "2026-09-10": 204.0,
                "2026-09-11": 359.0,
                "2026-09-12": 343.0,
                "2026-09-13": 272.0,
                "2026-09-14": 204.0,
            },
            "daily-heart-rate-variability": {
                "2026-08-27": 58.8,
                "2026-08-28": 63.7,
                "2026-08-29": 67.699,
                "2026-08-30": 58.5,
                "2026-08-31": 78.95,
                "2026-09-01": 68.7,
                "2026-09-02": 78.5,
                "2026-09-03": 75.3,
                "2026-09-04": 72.449,
                "2026-09-05": 54.25,
                "2026-09-06": 25.05,
                "2026-09-07": 71.9,
                "2026-09-08": 77.1,
                "2026-09-09": 64.75,
                "2026-09-10": 74.2,
                "2026-09-11": 74.5,
                "2026-09-12": 54.45,
                "2026-09-13": 75.6,
                "2026-09-14": 68.3,
            },
            "daily-resting-heart-rate": {
                "2026-08-28": 59.0,
                "2026-09-05": 62.0,
                "2026-09-06": 64.0,
                "2026-09-07": 60.0,
                "2026-09-11": 58.0,
            },
        }

    def _series(self, metric, _left, _right):
        values = self.data.get(metric, {})
        return {
            "metric": metric,
            "field": metric,
            "unit": "min" if metric == "active-minutes" else "ms",
            "aggregation": "mean",
            "date_semantics": "calendar_observation_date",
            "points": [(_local_ts(day), value) for day, value in values.items()],
        }


def test_threshold_response_keeps_exact_dates_and_values():
    result = reliability._tool_analyze_metric_threshold_responses(
        _FakeExecutor(),
        {
            "start": "2026-08-26",
            "end": "2026-09-14",
            "event_metric": "active-minutes",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "percent": 30,
            "direction": "above",
            "baseline_field": "median",
        },
    )

    assert result["status"] == "ok"
    assert result["event_baseline"] == 265.0
    assert result["threshold"] == 344.5
    assert result["event_day_count"] == 5

    rows = {row["event_date"]: row for row in result["event_days"]}
    assert list(rows) == [
        "2026-08-28",
        "2026-09-05",
        "2026-09-06",
        "2026-09-07",
        "2026-09-11",
    ]
    assert rows["2026-08-28"]["responses"]["daily-heart-rate-variability"] == 63.7
    assert rows["2026-09-05"]["responses"]["daily-heart-rate-variability"] == 54.25
    assert rows["2026-09-06"]["responses"]["daily-heart-rate-variability"] == 25.05
    assert rows["2026-09-07"]["responses"]["daily-heart-rate-variability"] == 71.9
    assert rows["2026-09-11"]["responses"]["daily-heart-rate-variability"] == 74.5
    assert rows["2026-09-06"]["responses"]["daily-resting-heart-rate"] == 64.0


def test_factory_schema_advertises_deterministic_threshold_recipe():
    assert reliability._BUILTIN_NAME in factory.SAFE_COMPOSABLE_TOOLS
    assert hasattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{reliability._BUILTIN_NAME}",
    )

    pipeline_schema = factory.CREATE_LEARNED_TOOL_SCHEMA["properties"]["pipeline"]
    assert reliability._BUILTIN_NAME in pipeline_schema["description"]
    assert reliability._THRESHOLD_PIPELINE_EXAMPLE in pipeline_schema["examples"]


def test_invalid_pipeline_returns_canonical_threshold_repair_recipe():
    executor = object.__new__(factory.EnhancedSafeToolExecutor)
    result = executor._tool_create_learned_tool(
        {
            "name": "threshold_response_test",
            "description": "test",
            "capability": reliability._BUILTIN_CAPABILITY,
            "pipeline": [{"op": "definitely_not_a_real_operation"}],
        }
    )

    assert result["status"] == "invalid_pipeline"
    assert result["canonical_threshold_response_pipeline"] == reliability._THRESHOLD_PIPELINE_EXAMPLE
    assert reliability._BUILTIN_NAME in result["threshold_response_instruction"]
