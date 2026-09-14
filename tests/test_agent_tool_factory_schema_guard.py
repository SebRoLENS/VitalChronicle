from __future__ import annotations

from datetime import datetime

from google_health_viewer import agent_tool_factory_schema_guard as guard


def test_nested_call_validation_rejects_invented_arguments_and_missing_required_fields():
    class Executor:
        @staticmethod
        def tool_schemas():
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "analyze_metric_threshold_responses",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "start": {"type": "string"},
                                "end": {"type": "string"},
                                "event_metric": {"type": "string"},
                                "response_metrics": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["start", "end", "event_metric", "response_metrics"],
                        },
                    },
                }
            ]

    pipeline = [
        {
            "op": "call_tool",
            "tool": "analyze_metric_threshold_responses",
            "arguments": {
                "start": "$start",
                "end": "$end",
                "metric": "heart-rate-variability",
                "event_source": "exercise",
                "max_recovery_days": 60,
            },
            "as": "bad",
        }
    ]
    errors, _schemas = guard._validate_nested_calls(
        Executor(),
        pipeline,
        {"heart-rate-variability", "exercise"},
    )

    assert any("event_metric is required" in error for error in errors)
    assert any("response_metrics is required" in error for error in errors)
    assert any("metric is not accepted" in error for error in errors)
    assert any("event_source is not accepted" in error for error in errors)
    assert any("max_recovery_days is not accepted" in error for error in errors)


def test_recovery_compiler_rewrites_the_real_broken_tool_to_a_valid_primitive():
    broken = {
        "name": "analizza_recupero_post_allenamento",
        "description": (
            "Analizza il tempo di recupero di HRV e frequenza cardiaca a riposo "
            "dopo allenamenti intensi."
        ),
        "capability": "analysis.composed.temporal_event_response.recovery_latency",
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
                "tool": "analyze_metric_threshold_responses",
                "arguments": {
                    "direction": "above",
                    "event_source": "exercise",
                    "fields": ["hrv", "rhr"],
                    "max_recovery_days": 60,
                    "metric": "heart-rate-variability",
                    "percent": 120,
                    "recovery_tolerance_percent": 5,
                    "response_source": "daily-resting-heart-rate",
                },
                "as": "recupero",
            }
        ],
    }
    catalog = {
        "daily-heart-rate-variability",
        "heart-rate-variability",
        "daily-resting-heart-rate",
        "exercise",
        "active-minutes",
    }

    compiled = guard._recovery_recipe(broken, catalog)
    assert compiled is not None
    pipeline, parameters = compiled

    assert pipeline[0]["tool"] == guard._RECOVERY_NAME
    assert pipeline[0]["arguments"]["event_source"] == "cardio_load"
    assert pipeline[0]["arguments"]["response_metrics"] == [
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
    ]
    assert pipeline[0]["arguments"]["event_percent"] == "$event_percent"
    assert "metric" not in pipeline[0]["arguments"]
    assert "event_source" in pipeline[0]["arguments"]
    assert "response_source" not in pipeline[0]["arguments"]
    assert parameters["properties"]["event_percent"]["default"] == 30
    assert parameters["properties"]["recovery_tolerance_percent"]["default"] == 10
    assert parameters["properties"]["max_recovery_days"]["default"] == 14


def test_metric_aliases_are_canonicalized_against_live_catalog():
    catalog = {
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
        "active-minutes",
    }

    assert guard._canonical_metric("hrv", catalog) == "daily-heart-rate-variability"
    assert guard._canonical_metric("rhr", catalog) == "daily-resting-heart-rate"
    assert guard._canonical_metric("minuti attivi", catalog) == "active-minutes"


def test_post_event_recovery_primitive_returns_recovery_for_multiple_metrics():
    class Executor:
        @staticmethod
        def _tool_calculate_cardio_load(_args):
            return {
                "daily_load": [
                    {"date": "2026-09-01", "load": 100},
                    {"date": "2026-09-02", "load": 100},
                    {"date": "2026-09-03", "load": 150},
                    {"date": "2026-09-04", "load": 100},
                    {"date": "2026-09-05", "load": 100},
                    {"date": "2026-09-06", "load": 100},
                ],
                "unit": "load points",
                "method": "test load",
            }

        @staticmethod
        def _series(metric, _left, _right):
            values = {
                "daily-heart-rate-variability": {
                    "2026-09-01": 60,
                    "2026-09-02": 60,
                    "2026-09-03": 60,
                    "2026-09-04": 45,
                    "2026-09-05": 58,
                    "2026-09-06": 60,
                },
                "daily-resting-heart-rate": {
                    "2026-09-01": 50,
                    "2026-09-02": 50,
                    "2026-09-03": 50,
                    "2026-09-04": 60,
                    "2026-09-05": 52,
                    "2026-09-06": 50,
                },
            }[metric]
            points = [
                (
                    datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp(),
                    value,
                )
                for day, value in values.items()
            ]
            return {
                "points": points,
                "aggregation": "mean",
                "unit": "test",
                "field": metric,
                "date_semantics": "calendar_observation_date",
            }

    result = guard._tool_analyze_post_event_recovery(
        Executor(),
        {
            "start": "2026-09-01",
            "end": "2026-09-06",
            "event_source": "cardio_load",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "event_percent": 30,
            "recovery_tolerance_percent": 10,
            "max_recovery_days": 3,
        },
    )

    assert result["status"] == "ok"
    assert result["event_episode_count"] == 1
    assert result["event_episodes"] == [["2026-09-03"]]
    assert result["responses"]["daily-heart-rate-variability"]["median_recovery_days"] == 2
    assert result["responses"]["daily-resting-heart-rate"]["median_recovery_days"] == 2
