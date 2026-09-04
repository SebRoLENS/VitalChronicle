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
        domains = {
            "vitals": [
                {
                    "data_type": data_type,
                    "label": "Sleep temperature variation",
                    "metric": "Temperature variation",
                    "unit": "°C",
                    "summary": {
                        "count": 12,
                        "latest": 0.4,
                        "mean": 0.1,
                        "trend_percent": 20.0,
                    },
                    "domain": "vitals",
                }
            ]
        }
    return {
        "packet": {"health_evidence_present": True},
        "period": {"start": "2026-08-01", "end": "2026-09-01"},
        "coverage": {},
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
