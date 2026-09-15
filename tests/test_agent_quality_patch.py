from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from google_health_viewer import agent_quality_patch as quality
from google_health_viewer import agent_runtime_efficiency_patch as efficiency
from google_health_viewer import agent_tool_factory_reliability_patch as reliability
from google_health_viewer import agent_tools


def test_self_report_candidates_capture_multiple_subjective_observations():
    items = quality._self_report_candidates(
        "Oggi ho dormito bene. Sento meno fame oggi. Non ho dolori alla schiena."
    )
    assert [(item["category"], item["statement"]) for item in items] == [
        ("sleep_quality", "Oggi ho dormito bene"),
        ("appetite", "Sento meno fame oggi"),
        ("pain", "Non ho dolori alla schiena"),
    ]


def test_factory_hint_detects_cross_metric_relative_percentages():
    hint = quality._supplement_factory_hint(
        "Quando dormo il 3% in più del solito, l'HRV migliora del 5% o più?",
        {"consider_reusable_tool": False, "signals": [], "instruction": ""},
    )
    assert hint["consider_reusable_tool"] is True
    assert "relative personal-baseline threshold" in hint["signals"]
    assert "threshold/frequency analysis" in hint["signals"]
    assert "analyze_metric_threshold_responses" in hint["instruction"]


def test_runtime_capability_gap_detection_is_specific():
    assert quality._looks_like_runtime_capability_gap(
        "Non posso determinare la variazione percentuale perché mi manca la baseline HRV."
    )
    assert not quality._looks_like_runtime_capability_gap(
        "Il campione è piccolo, quindi la conclusione è preliminare."
    )


def test_large_context_does_not_keep_old_artificial_output_ceiling():
    assert efficiency._agent_predict_cap(131072, True) >= 6144
    assert efficiency._agent_predict_cap(131072, False) >= 8192


def test_daily_normalization_collapses_duplicate_calendar_dates():
    class Base:
        @staticmethod
        def _day(ts: float) -> str:
            return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()

    day1 = datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp()
    day2 = datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp()
    points = [
        (day1, 69.1),
        (day1 + 3600, 77.6),
        (day1 + 7200, 78.5),
        (day2, 75.3),
    ]
    normalized = quality._daily_normalized_points(Base, points)
    assert len(normalized) == 2
    assert normalized[0][1] == pytest.approx((69.1 + 77.6 + 78.5) / 3)
    assert normalized[1][1] == pytest.approx(75.3)


def test_sleep_total_alias_builds_daily_hours(monkeypatch):
    class Executor:
        def _semantic_records(self, data_type, left, right):
            assert data_type == "sleep"
            return [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    timestamps = {
        "a": datetime(2026, 9, 10, 7, tzinfo=timezone.utc).timestamp(),
        "b": datetime(2026, 9, 10, 14, tzinfo=timezone.utc).timestamp(),
        "c": datetime(2026, 9, 11, 7, tzinfo=timezone.utc).timestamp(),
    }
    durations = {"a": 7.0, "b": 0.5, "c": 8.0}
    monkeypatch.setattr(agent_tools, "duration_hours", lambda record: durations[record["id"]])
    monkeypatch.setattr(
        agent_tools,
        "_record_semantic_timestamp",
        lambda record, _data_type: timestamps[record["id"]],
    )
    monkeypatch.setattr(
        agent_tools,
        "_day",
        lambda ts: datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
    )

    result = quality._sleep_total_series(
        Executor(), "sleep_total", date(2026, 9, 10), date(2026, 9, 11)
    )
    assert result["field"] == "__duration_hours__"
    assert result["unit"] == "h"
    assert result["date_semantics"] == "wake_up_date"
    assert [value for _ts, value in result["points"]] == pytest.approx([7.5, 8.0])


def test_threshold_result_includes_response_baseline_and_percent_delta(monkeypatch):
    monkeypatch.setattr(
        reliability,
        "_daily_series",
        lambda _executor, metric, _left, _right: (
            {
                "2026-09-01": 60.0,
                "2026-09-02": 70.0,
                "2026-09-03": 80.0,
            },
            {"unit": "ms", "field": "hrv"},
        ),
    )
    result = {
        "status": "ok",
        "baseline_field": "median",
        "responses": {"hrv": {"observed_days": 3}},
        "event_days": [
            {
                "event_date": "2026-09-03",
                "response_date": "2026-09-03",
                "event_value": 8.0,
                "responses": {"hrv": 80.0},
            }
        ],
        "method": "Base method.",
    }
    enriched = quality._enrich_threshold_result(
        object(),
        {"start": "2026-09-01", "end": "2026-09-03", "baseline_field": "median"},
        result,
    )
    assert enriched["responses"]["hrv"]["baseline"] == pytest.approx(70.0)
    change = enriched["event_days"][0]["response_changes"]["hrv"]
    assert change["delta_absolute"] == pytest.approx(10.0)
    assert change["delta_percent"] == pytest.approx(14.286, abs=0.001)
