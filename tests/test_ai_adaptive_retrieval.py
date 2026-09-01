from __future__ import annotations

from google_health_viewer.ai_adaptive_retrieval import (
    PROFILE_EVIDENCE_TARGETS,
    classify_retrieval_request,
    select_evidence_for_request,
)
from google_health_viewer.ai_pipeline import estimate_json_tokens


def _metric(data_type: str, label: str, value: float) -> dict:
    return {
        "data_type": data_type,
        "label": label,
        "metric": "value",
        "unit": "u",
        "summary": {
            "count": 90,
            "latest": value,
            "mean": value - 1,
            "median": value - 1.2,
            "minimum": value - 5,
            "maximum": value + 5,
        },
        "coverage": {
            "observed_calendar_days": 80,
            "coverage_percent": 88.9,
            "records_considered": 500,
        },
        "evidence": {
            "trend": {
                "window_days": 28,
                "observed_days": 25,
                "direction": "stable",
                "slope_per_week": 0.1,
                "percent_per_week": 0.5,
                "r_squared": 0.2,
            },
            "personal_baselines": {
                "7_days": {"mean": value, "samples": 7},
                "28_days": {"mean": value - 1, "samples": 28},
                "90_days": {"mean": value - 2, "samples": 80},
            },
        },
        "structured": {"details": [f"unused-{index}" for index in range(40)]},
    }


def _packet() -> dict:
    metrics = {
        "activity": [
            _metric("steps", "Steps", 8000),
            _metric("active-energy-burned", "Active calories", 550),
            _metric("distance", "Distance", 6.2),
        ],
        "sleep": [_metric("sleep", "Sleep", 7.3)],
        "heart": [
            _metric(
                "daily-heart-rate-variability",
                "Heart rate variability (HRV)",
                48,
            ),
            _metric("daily-resting-heart-rate", "Resting heart rate", 57),
        ],
        "vitals": [_metric("daily-oxygen-saturation", "Oxygen saturation", 97)],
        "weight": [_metric("weight", "Weight", 82)],
        "workouts": [_metric("exercise", "Exercise", 4)],
    }
    data_types = [
        metric["data_type"]
        for domain_metrics in metrics.values()
        for metric in domain_metrics
    ]
    return {
        "packet": {
            "health_evidence_present": True,
            "pipeline_version": "compact-health-evidence-v1",
            "analysis_scope": "selected_period",
            "metric_count": len(data_types),
        },
        "period": {"start": "2026-06-01", "end": "2026-08-31"},
        "observation": {"current_day_is_incomplete": False},
        "coverage": {
            "requested_calendar_days": 92,
            "calendar_days_with_measurements": 84,
            "scope_is_partially_observed": True,
            "limited_daily_metrics": [
                {
                    "data_type": data_type,
                    "label": data_type,
                    "observed_calendar_days": 80,
                    "coverage_percent": 88.9,
                }
                for data_type in data_types
            ],
        },
        "domains": metrics,
        "strongest_evidence": [
            {
                "evidence_id": f"trend:{data_type}",
                "kind": "multiweek_trend",
                "data_types": [data_type],
                "headline": f"{data_type} trend",
                "relevance_score": 80 - index,
                "confidence": "moderate",
                "evidence": {"percent_per_week": index + 1},
            }
            for index, data_type in enumerate(data_types)
        ],
        "associations": [
            {
                "left": "Heart rate variability (HRV)",
                "right": "Active calories",
                "left_data_type": "daily-heart-rate-variability",
                "right_data_type": "active-energy-burned",
                "r": 0.52,
                "paired_days": 35,
                "timing": "same_day",
                "reliability_score": 0.52,
            },
            {
                "left": "Sleep",
                "right": "Heart rate variability (HRV)",
                "left_data_type": "sleep",
                "right_data_type": "daily-heart-rate-variability",
                "r": 0.49,
                "paired_days": 40,
                "timing": "same_day",
                "reliability_score": 0.49,
            },
        ],
        "association_diagnostics_for_request": [
            {
                "left": "Heart rate variability (HRV)",
                "right": "Active calories",
                "same_day": {
                    "paired_days": 35,
                    "r": 0.52,
                    "status": "reported",
                },
            }
        ],
        "archive_quality": {
            "truncated_data_types": [],
            "records_considered_total": 100000,
        },
    }


def _types(packet: dict) -> set[str]:
    return {
        metric["data_type"]
        for metrics in packet.get("domains", {}).values()
        for metric in metrics
    }


def test_specific_hrv_calorie_question_keeps_only_relevant_metrics():
    packet = _packet()
    selected = select_evidence_for_request(
        packet,
        "C'è una correlazione tra HRV e calorie attive?",
        performance_profile="fast",
    )

    assert selected["packet"]["retrieval_mode"] == "specific_metrics"
    assert _types(selected) == {
        "daily-heart-rate-variability",
        "active-energy-burned",
    }
    assert len(selected["associations"]) == 1
    assert selected["association_diagnostics_for_request"]
    assert estimate_json_tokens(selected) <= PROFILE_EVIDENCE_TARGETS["fast"] + 80


def test_domain_question_keeps_the_requested_domain():
    packet = _packet()
    selected = select_evidence_for_request(
        packet,
        "Fammi un'analisi generale della mia attività fisica",
        performance_profile="standard",
    )

    assert selected["packet"]["retrieval_mode"] == "domain"
    assert set(selected["domains"]) == {"activity"}
    assert _types(selected) == {"steps", "active-energy-burned", "distance"}
    assert estimate_json_tokens(selected) <= PROFILE_EVIDENCE_TARGETS["standard"] + 80


def test_fast_global_request_keeps_cross_domain_breadth_with_small_budget():
    selected = select_evidence_for_request(
        _packet(),
        "Analizza tutti i dati e dimmi i pattern interessanti",
        performance_profile="fast",
    )

    assert selected["packet"]["retrieval_mode"] == "global"
    assert len(selected["domains"]) >= 5
    assert all(len(metrics) == 1 for metrics in selected["domains"].values())
    assert estimate_json_tokens(selected) <= PROFILE_EVIDENCE_TARGETS["fast"] + 80


def test_maximum_deep_analysis_preserves_complete_compact_evidence():
    packet = _packet()
    selected = select_evidence_for_request(
        packet,
        "",
        performance_profile="max",
        analysis_mode="deep",
    )

    assert selected["packet"]["retrieval_mode"] == "global"
    assert _types(selected) == _types(packet)
    assert len(selected["strongest_evidence"]) == len(packet["strongest_evidence"])
    assert len(selected["associations"]) == len(packet["associations"])
    assert selected["domains"]["activity"][0]["structured"] == packet["domains"]["activity"][0]["structured"]


def test_unrecognized_question_falls_back_to_broad_profile_packet():
    packet = _packet()
    intent = classify_retrieval_request(packet, "Come sto andando ultimamente?")
    selected = select_evidence_for_request(
        packet,
        "Come sto andando ultimamente?",
        performance_profile="standard",
    )

    assert intent["mode"] == "general_question"
    assert selected["packet"]["retrieval_mode"] == "general_question"
    assert len(selected["domains"]) >= 5
