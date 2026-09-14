from __future__ import annotations

from datetime import datetime

from google_health_viewer import agent_tool_factory_schema_guard as schema_guard
from google_health_viewer import agent_tool_factory_semantic_guard as semantic


QUESTION = (
    "Quando mi alleno per due giorni consecutivi, il recupero dopo il secondo allenamento "
    "è più lento rispetto a quando mi alleno dopo almeno un giorno di riposo?"
)


def _points(values: dict[str, float]) -> list[tuple[float, float]]:
    return [
        (datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp(), value)
        for day, value in values.items()
    ]


def test_semantic_contract_detects_comparison_and_unrequested_high_load_filter():
    requirements = semantic._semantic_requirements(QUESTION)
    assert {
        "comparison",
        "consecutive_training",
        "prior_rest",
        "recovery",
    }.issubset(requirements)
    assert "high_load_filter" not in requirements

    old_recovery_pipeline = [
        {
            "op": "call_tool",
            "tool": schema_guard._RECOVERY_NAME,
            "arguments": {
                "start": "$start",
                "end": "$end",
                "event_source": "cardio_load",
                "response_metrics": [
                    "daily-heart-rate-variability",
                    "daily-resting-heart-rate",
                ],
                "event_percent": "$event_percent",
            },
            "as": "recovery",
        },
        {"op": "return", "source": "recovery"},
    ]
    contract = semantic._semantic_contract_errors(QUESTION, old_recovery_pipeline)

    assert "comparison" in contract["missing"]
    assert "consecutive_training" in contract["missing"]
    assert "prior_rest" in contract["missing"]
    assert contract["introduced"] == ["high_load_filter"]


def test_semantic_recipe_preserves_the_actual_question_instead_of_high_load_proxy():
    candidate = {
        "name": "consecutive_vs_rest_recovery_comparison",
        "description": (
            "Confronta il recupero dopo allenamenti consecutivi rispetto a quando si allena "
            "dopo almeno un giorno di riposo."
        ),
        "capability": "analysis.composed.temporal_event_response.recovery_latency",
        "pipeline": [
            {
                "op": "call_tool",
                "tool": schema_guard._RECOVERY_NAME,
                "arguments": {
                    "start": "$start",
                    "end": "$end",
                    "event_source": "cardio_load",
                    "response_metrics": ["daily-heart-rate-variability"],
                    "event_percent": 30,
                },
            }
        ],
    }
    catalog = {
        "exercise",
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
    }

    pipeline, parameters = semantic._context_comparison_recipe(candidate, QUESTION, catalog)

    assert pipeline[0]["tool"] == semantic._CONTEXT_RECOVERY_NAME
    assert "event_percent" not in pipeline[0]["arguments"]
    assert pipeline[0]["arguments"]["response_metrics"] == [
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
    ]
    assert parameters["properties"]["minimum_rest_days"]["default"] == 1
    contract = semantic._semantic_contract_errors(QUESTION, pipeline)
    assert contract["missing"] == []
    assert contract["introduced"] == []


def test_context_recovery_primitive_compares_consecutive_training_with_training_after_rest():
    class Executor:
        @staticmethod
        def _series(metric, _left, _right):
            values = {
                "exercise": {
                    "2026-09-01": 1.0,
                    "2026-09-02": 1.0,
                    "2026-09-05": 1.0,
                },
                "daily-heart-rate-variability": {
                    "2026-09-01": 60,
                    "2026-09-02": 60,
                    "2026-09-03": 45,
                    "2026-09-04": 60,
                    "2026-09-05": 60,
                    "2026-09-06": 60,
                    "2026-09-07": 60,
                },
                "daily-resting-heart-rate": {
                    "2026-09-01": 50,
                    "2026-09-02": 50,
                    "2026-09-03": 65,
                    "2026-09-04": 50,
                    "2026-09-05": 50,
                    "2026-09-06": 50,
                    "2026-09-07": 50,
                },
            }[metric]
            return {
                "points": _points(values),
                "aggregation": "sum" if metric == "exercise" else "mean",
                "unit": "h" if metric == "exercise" else "test",
                "field": metric,
                "date_semantics": "calendar_observation_date",
            }

    result = semantic._tool_compare_training_context_recovery(
        Executor(),
        {
            "start": "2026-09-01",
            "end": "2026-09-07",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "recovery_tolerance_percent": 10,
            "max_recovery_days": 3,
            "minimum_rest_days": 1,
        },
    )

    assert result["status"] == "ok"
    assert result["consecutive_training_days"] == ["2026-09-02"]
    assert result["after_rest_days"] == ["2026-09-05"]
    assert "high" not in result["group_definitions"]["consecutive_training"].casefold()

    hrv = result["responses"]["daily-heart-rate-variability"]
    rhr = result["responses"]["daily-resting-heart-rate"]
    assert hrv["consecutive_training"]["median_recovery_days"] == 2
    assert hrv["after_rest"]["median_recovery_days"] == 1
    assert hrv["median_recovery_delta_days_consecutive_minus_rest"] == 1
    assert rhr["consecutive_training"]["median_recovery_days"] == 2
    assert rhr["after_rest"]["median_recovery_days"] == 1


def test_runtime_semantic_capability_is_specific_enough_for_registry_reuse():
    from google_health_viewer import agent_runtime_v2 as runtime_v2

    semantic._install_runtime_semantics()
    hint = runtime_v2._factory_hint(QUESTION)
    capability = runtime_v2._factory_capability(hint)

    assert hint["consider_reusable_tool"] is True
    assert "training-context recovery comparison" in hint["signals"]
    assert capability == semantic._COMPOSED_CONTEXT_CAPABILITY
