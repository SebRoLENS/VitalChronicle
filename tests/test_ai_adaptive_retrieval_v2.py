from __future__ import annotations

import json

import pytest

from google_health_viewer import ai_adaptive_retrieval_v2 as retrieval
from google_health_viewer.local_ai import LocalAIError


def _metric(data_type: str, label: str, value: float) -> dict:
    return {
        "data_type": data_type,
        "label": label,
        "metric": "value",
        "unit": "u",
        "summary": {"count": 30, "latest": value, "mean": value - 1, "median": value - 1},
        "coverage": {"observed_calendar_days": 28, "coverage_percent": 93.3},
    }


def _packet() -> dict:
    metrics = {
        "activity": [
            _metric("steps", "Steps", 8000),
            _metric("active-energy-burned", "Active energy burned", 520),
            _metric("distance", "Distance", 6.0),
        ],
        "sleep": [_metric("sleep", "Sleep", 7.2)],
        "heart": [
            _metric("daily-heart-rate-variability", "Heart rate variability", 47),
            _metric("daily-resting-heart-rate", "Resting heart rate", 58),
        ],
        "vitals": [_metric("daily-oxygen-saturation", "Oxygen saturation", 97)],
        "weight": [_metric("weight", "Weight", 82)],
    }
    return {
        "packet": {"pipeline_version": "compact-health-evidence-v1"},
        "period": {"start": "2026-08-01", "end": "2026-08-31"},
        "coverage": {"requested_calendar_days": 31},
        "domains": metrics,
        "strongest_evidence": [],
        "associations": [
            {
                "left": "Heart rate variability",
                "right": "Active energy burned",
                "left_data_type": "daily-heart-rate-variability",
                "right_data_type": "active-energy-burned",
                "r": 0.5,
                "paired_days": 20,
            }
        ],
        "association_diagnostics_for_request": [],
    }


def _types(packet: dict) -> set[str]:
    return {
        metric["data_type"]
        for metrics in packet.get("domains", {}).values()
        for metric in metrics
    }


def _clear_catalogue_caches() -> None:
    retrieval._catalogues.cache_clear()
    retrieval._translation_variants.cache_clear()


def test_weblate_catalogue_translation_becomes_router_vocabulary(monkeypatch, tmp_path):
    (tmp_path / "en.json").write_text(
        json.dumps(
            {
                "Heart rate variability": "Heart rate variability",
                "Active energy burned": "Active energy burned",
                "Activity": "Activity",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fr.json").write_text(
        json.dumps(
            {
                "Heart rate variability": "Variabilité de la fréquence cardiaque",
                "Active energy burned": "Énergie active dépensée",
                "Activity": "Activité",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(retrieval.i18n, "CATALOGUE_DIR", tmp_path)
    _clear_catalogue_caches()
    try:
        intent = retrieval.classify_request_v2(
            _packet(),
            "Quelle relation entre ma variabilité de la fréquence cardiaque et mon énergie active dépensée ?",
        )
    finally:
        _clear_catalogue_caches()

    assert intent["mode"] == "specific_metrics"
    assert set(intent["data_types"]) == {
        "daily-heart-rate-variability",
        "active-energy-burned",
    }
    assert intent["confidence"] >= retrieval.HIGH_CONFIDENCE


def test_typo_tolerant_matching_keeps_only_relevant_metrics():
    selected = retrieval.select_evidence_v2(
        _packet(),
        "C'è correlazione tra HRV e calrie ative?",
        performance_profile="fast",
    )

    assert selected["packet"]["retrieval_version"] == retrieval.RETRIEVAL_VERSION
    assert selected["packet"]["retrieval_mode"] == "specific_metrics"
    assert _types(selected) == {
        "daily-heart-rate-variability",
        "active-energy-burned",
    }
    assert selected["packet"]["retrieval_metric_count"] == 2
    assert selected["packet"]["retrieval_selected_tokens"] < selected["packet"]["retrieval_original_tokens"]


def test_medium_confidence_is_widened_instead_of_overfiltering(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "_metric_scores",
        lambda _packet, _question: [(0.80, "active-energy-burned", "active energy")],
    )
    monkeypatch.setattr(retrieval, "_domain_scores", lambda _question: [])
    intent = retrieval.classify_request_v2(_packet(), "odd wording")

    assert intent["mode"] == "domain"
    assert intent["domains"] == ["activity"]
    assert intent["reason"] == "medium_metric_match_widened_to_domain"


def test_unknown_language_or_wording_falls_back_to_broad_packet():
    intent = retrieval.classify_request_v2(_packet(), "これは最近どうなっていますか")
    selected = retrieval.select_evidence_v2(
        _packet(),
        "これは最近どうなっていますか",
        performance_profile="standard",
    )

    assert intent["mode"] == "general_question"
    assert intent["confidence"] == 0.0
    assert selected["packet"]["retrieval_reason"] == "low_confidence_broad_fallback"
    assert len(selected["domains"]) >= 4


def test_guard_blocks_health_packet_that_bypassed_retrieval(monkeypatch):
    called = False

    def base(_self, _messages, *, think, **_kwargs):
        nonlocal called
        called = True
        return "ok"

    monkeypatch.setattr(retrieval, "_BASE_CHAT_STREAM", base)
    messages = [
        {
            "role": "user",
            "content": (
                "BEGIN_HEALTH_EVIDENCE_JSON\n"
                + json.dumps(_packet())
                + "\nEND_HEALTH_EVIDENCE_JSON"
            ),
        }
    ]

    with pytest.raises(LocalAIError, match="retrieval was bypassed"):
        retrieval._guarded_chat_stream(object(), messages, think=False)
    assert not called


def test_guard_allows_only_retrieval_v2_packet(monkeypatch):
    called = False

    def base(_self, _messages, *, think, **_kwargs):
        nonlocal called
        called = True
        return "ok"

    monkeypatch.setattr(retrieval, "_BASE_CHAT_STREAM", base)
    selected = retrieval.select_evidence_v2(
        _packet(), "HRV e calorie attive", performance_profile="fast"
    )
    messages = [
        {
            "role": "user",
            "content": (
                "BEGIN_HEALTH_EVIDENCE_JSON\n"
                + json.dumps(selected)
                + "\nEND_HEALTH_EVIDENCE_JSON"
            ),
        }
    ]

    assert retrieval._guarded_chat_stream(object(), messages, think=False) == "ok"
    assert called
