from __future__ import annotations

from google_health_viewer import ai_adaptive_retrieval, ai_engine
from google_health_viewer.constants import DATA_TYPES
from google_health_viewer.scientific_context import (
    DATA_TYPE_TO_TOPIC,
    scientific_context_for,
)


def _packet(data_type: str | None = None) -> dict:
    domains = {}
    if data_type is not None:
        domain = "heart" if "heart" in data_type else "vitals"
        label = {
            "daily-heart-rate-variability": "Daily heart-rate variability",
            "steps": "Steps",
        }.get(data_type, "Sleep temperature variation")
        metric = {
            "data_type": data_type,
            "label": label,
            "metric": "Average HRV" if data_type == "daily-heart-rate-variability" else label,
            "unit": "ms" if data_type == "daily-heart-rate-variability" else "°C",
            "summary": {
                "count": 12,
                "latest": 72.45,
                "mean": 69.0,
                "trend_percent": 20.0,
            },
            "domain": domain,
        }
        if data_type == "steps":
            metric["today"] = {
                "status": "partial",
                "today_so_far": 4200,
                "same_time_mean": 3900,
            }
        domains = {domain: [metric]}
    return {
        "packet": {"health_evidence_present": True},
        "period": {"start": "2026-08-26", "end": "2026-09-04"},
        "observation": {
            "observed_at": "2026-09-04T15:50:00+02:00",
            "local_date": "2026-09-04",
            "local_time": "15:50",
            "selected_period_includes_today": True,
            "current_day_is_incomplete": True,
            "elapsed_day_percent": 66.0,
        },
        "coverage": {
            "requested_calendar_days": 10,
            "calendar_days_with_measurements": 9,
            "calendar_days_with_measurements_percent": 90.0,
        },
        "domains": domains,
        "strongest_evidence": [],
        "associations": [],
        "archive_quality": {},
    }


def test_every_supported_google_health_type_has_scientific_context():
    keys = {spec.key for spec in DATA_TYPES}
    assert keys == set(DATA_TYPE_TO_TOPIC)
    assert all(scientific_context_for(key) is not None for key in keys)


def test_focused_temperature_question_receives_detailed_scientific_context():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet("daily-sleep-temperature-derivations"),
        "Perché la mia temperatura nel sonno è aumentata?",
        performance_profile="standard",
    )

    science = result["scientific_context"]
    context = science["metrics"]["daily-sleep-temperature-derivations"]
    assert context["topic"] == "temperature"
    assert context["confounders"]
    assert context["relationships"]
    assert context["sources"]
    assert "not evidence" in science["role"]
    assert result["packet"]["response_mode"] == "personal_analysis"
    assert ai_adaptive_retrieval.estimate_json_tokens(result) <= 2500


def test_scientific_definition_is_available_without_personal_measurements():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet(),
        "Cosa significa HRV?",
        performance_profile="standard",
    )

    science = result["scientific_context"]["metrics"]
    assert "daily-heart-rate-variability" in science
    assert "source_ids" in science["daily-heart-rate-variability"]
    assert result["packet"]["response_mode"] == "scientific_definition"


def test_pure_hrv_definition_excludes_personal_data_and_accepts_hvr_typo():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet("daily-heart-rate-variability"),
        "Cosa è HVR?",
        performance_profile="standard",
    )

    assert result["packet"]["response_mode"] == "scientific_definition"
    assert result["packet"]["personal_evidence_included"] is False
    assert result["packet"]["definition_data_types"] == ["daily-heart-rate-variability"]
    assert "domains" not in result
    assert "coverage" not in result
    assert "period" not in result
    assert "observation" not in result
    context = result["scientific_context"]["metrics"]["daily-heart-rate-variability"]
    assert context["meaning"]
    assert context["higher"]
    assert context["lower"]
    assert context["confounders"]
    assert context["sources"]


def test_personal_hrv_change_question_keeps_personal_evidence():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet("daily-heart-rate-variability"),
        "Perché la mia HRV è diminuita?",
        performance_profile="standard",
    )

    assert result["packet"]["response_mode"] == "personal_analysis"
    assert "domains" in result
    assert result["domains"]["heart"][0]["summary"]["latest"] == 72.45
    assert "scientific_context" in result


def test_daily_metric_is_not_marked_partial_because_clock_day_is_in_progress():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet("daily-heart-rate-variability"),
        "Come va la mia HRV oggi?",
        performance_profile="standard",
    )

    observation = result["observation"]
    assert observation["calendar_day_in_progress"] is True
    assert "current_day_is_incomplete" not in observation
    assert "elapsed_day_percent" not in observation
    metric = result["domains"]["heart"][0]
    assert metric["record_semantics"]["record_type"] == "daily"
    assert metric["record_semantics"]["clock_day_proration"] == "not_applicable"
    assert "today" not in metric


def test_intraday_cumulative_metric_can_still_be_explicitly_partial():
    result = ai_adaptive_retrieval.select_evidence_for_request(
        _packet("steps"),
        "Quanti passi ho fatto oggi?",
        performance_profile="standard",
    )

    metric = result["domains"]["activity"][0]
    assert metric["today"]["status"] == "partial"
    assert "record_semantics" not in metric
    assert result["observation"]["calendar_day_in_progress"] is True


def test_general_analysis_uses_compact_science_only_for_relevant_evidence():
    packet = _packet("daily-sleep-temperature-derivations")
    packet["strongest_evidence"] = [
        {
            "kind": "trend",
            "data_types": ["daily-sleep-temperature-derivations"],
            "headline": "Temperature increased",
            "relevance_score": 0.8,
        }
    ]
    result = ai_adaptive_retrieval.select_evidence_for_request(
        packet,
        "Analizza tutto",
        performance_profile="standard",
        analysis_mode="deep",
    )

    context = result["scientific_context"]["metrics"]["daily-sleep-temperature-derivations"]
    assert "sources" not in context
    assert "confounders" not in context
    assert context["baseline_rule"]


def test_system_prompt_allows_general_knowledge_but_preserves_evidence_hierarchy():
    prompt = ai_engine.compact_system_prompt()
    assert "own established general scientific knowledge" in prompt
    assert "does NOT prove" in prompt
    assert "Never invent measurements" in prompt
    assert "scientific_definition" in prompt
    assert "record_type=daily" in prompt
    assert "Do NOT discuss the user's personal values" in prompt
