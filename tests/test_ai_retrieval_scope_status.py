from __future__ import annotations

import json

from google_health_viewer import ai_adaptive_retrieval_v2 as retrieval
from google_health_viewer import ai_retrieval_scope_status as scope_status
from google_health_viewer.i18n import current_language, set_language


def _metric(data_type: str, label: str, value: float) -> dict:
    return {
        "data_type": data_type,
        "label": label,
        "metric": "value",
        "unit": "u",
        "summary": {"count": 30, "latest": value, "mean": value},
    }


def _packet() -> dict:
    return {
        "packet": {"pipeline_version": "compact-health-evidence-v1"},
        "domains": {
            "activity": [
                _metric("steps", "Steps", 8000),
                _metric("active-energy-burned", "Active energy burned", 500),
            ],
            "sleep": [_metric("sleep", "Sleep", 7.2)],
            "heart": [
                _metric("daily-heart-rate-variability", "Heart rate variability", 45),
                _metric("daily-resting-heart-rate", "Resting heart rate", 58),
            ],
        },
        "coverage": {"requested_calendar_days": 30},
        "strongest_evidence": [],
        "associations": [],
    }


def test_natural_italian_sleep_question_reaches_only_sleep_metric():
    selected = retrieval.select_evidence_v2(
        _packet(),
        "ciao vorrei sapere come vanno i miei dati di sonno",
        performance_profile="standard",
    )

    assert selected["packet"]["retrieval_mode"] == "specific_metrics"
    assert selected["packet"]["retrieval_metric_count"] == 1
    assert set(selected["domains"]) == {"sleep"}
    assert selected["domains"]["sleep"][0]["data_type"] == "sleep"


def test_italian_live_status_says_when_evidence_is_partial():
    selected = retrieval.select_evidence_v2(
        _packet(),
        "ciao vorrei sapere come vanno i miei dati di sonno",
        performance_profile="standard",
    )
    previous = current_language()
    try:
        set_language("it")
        text = scope_status.evidence_scope_status(selected)
    finally:
        set_language(previous)

    assert "DATI PARZIALI" in text
    assert "metriche 1/5" in text
    assert "evidenze ~" in text


def test_live_status_reports_all_metrics_for_broad_packet():
    selected = retrieval.select_evidence_v2(
        _packet(),
        "これは最近どうなっていますか",
        performance_profile="standard",
    )
    previous = current_language()
    try:
        set_language("it")
        text = scope_status.evidence_scope_status(selected)
    finally:
        set_language(previous)

    assert "TUTTE LE METRICHE" in text
    assert "metriche 5/5" in text


def test_scope_status_wraps_the_actual_outgoing_packet(monkeypatch):
    selected = retrieval.select_evidence_v2(
        _packet(),
        "come vanno i miei dati di sonno",
        performance_profile="standard",
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
    observed_phase = ""

    def base(client, _messages, *, think, **_kwargs):
        nonlocal observed_phase
        observed_phase = client._current_phase
        return "ok"

    class Client:
        _current_phase = "final synthesis"

    monkeypatch.setattr(scope_status, "_BASE_CHAT_STREAM", base)
    previous = current_language()
    try:
        set_language("it")
        client = Client()
        result = scope_status._chat_stream_with_scope_status(
            client,
            messages,
            think=True,
        )
    finally:
        set_language(previous)

    assert result == "ok"
    assert "DATI PARZIALI" in observed_phase
    assert client._current_phase == "final synthesis"
