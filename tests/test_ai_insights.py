from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google_health_viewer.ai_insights import build_ai_ready_snapshot


def _daily_record(day: datetime, data_type: str, value: float) -> dict:
    payload = {
        "steps": {"count": value},
        "dailyRestingHeartRate": {"beatsPerMinute": value},
        "sleep": {"sleepSummary": {"minutesAsleep": value * 60}},
    }[data_type]
    return {
        "record_kind": "data_point",
        "start_time": day.isoformat(),
        "end_time": (day + timedelta(hours=1)).isoformat(),
        "payload": {data_type: payload},
    }


def test_ai_preprocessing_builds_matched_baselines_and_ranked_evidence():
    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    records = []
    for index in range(28):
        value = 5000.0 if index < 21 else 8000.0
        records.append(_daily_record(start + timedelta(days=index), "steps", value))

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "steps" else []

    snapshot = build_ai_ready_snapshot(
        FakeStore(),
        "2026-07-01",
        "2026-07-29",
        now=datetime(2026, 7, 29, 10, tzinfo=timezone.utc),
    )
    steps = next(item for item in snapshot["metrics"] if item["data_type"] == "steps")
    evidence = steps["derived_evidence"]

    assert evidence["personal_baselines"]["28_days"]["observed_days"] == 28
    assert evidence["matched_recent_comparison"]["recent_mean"] == 8000.0
    assert evidence["matched_recent_comparison"]["previous_mean"] == 5000.0
    assert evidence["matched_recent_comparison"]["percent_change"] == 60.0
    assert any(item["evidence_id"] == "change:steps" for item in snapshot["candidate_insights"])
    assert snapshot["preprocessing"]["version"] == "health-evidence-v3"


def test_requested_month_reports_when_only_one_week_is_observed():
    start = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    records = [
        _daily_record(start + timedelta(days=index), "steps", 6000 + index * 100)
        for index in range(7)
    ]

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "steps" else []

    snapshot = build_ai_ready_snapshot(
        FakeStore(),
        "2026-08-01",
        "2026-09-01",
        now=datetime(2026, 9, 1, 8, tzinfo=timezone.utc),
    )
    coverage = snapshot["requested_interval_coverage"]

    assert coverage["requested_calendar_days"] == 31
    assert coverage["calendar_days_with_any_data"] == 7
    assert coverage["first_observed_date"] == "2026-08-25"
    assert coverage["last_observed_date"] == "2026-08-31"
    assert coverage["scope_is_partially_observed"]
    assert "do not imply complete coverage" in coverage["coverage_notice"].lower()
    assert snapshot["candidate_insights"][0]["evidence_id"] == "quality:requested-interval"
    assert snapshot["analysis_brief"]["must_state_data_limitations_first"]


def test_ai_preprocessing_finds_repeated_cross_metric_associations():
    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    steps = []
    resting = []
    for index in range(30):
        steps.append(_daily_record(start + timedelta(days=index), "steps", 4000 + index * 100))
        resting.append(
            _daily_record(
                start + timedelta(days=index),
                "dailyRestingHeartRate",
                55 + index * 0.4,
            )
        )

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return {
                "steps": steps,
                "daily-resting-heart-rate": resting,
            }.get(data_type, [])

    snapshot = build_ai_ready_snapshot(
        FakeStore(),
        "2026-07-01",
        "2026-07-31",
        now=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
    )

    assert snapshot["associations"]
    association = snapshot["associations"][0]
    assert association["paired_days"] >= 29
    assert association["interpretation_limit"] == "association_only_not_causation"


def test_partial_day_candidate_uses_same_time_instead_of_full_day():
    local_zone = datetime.now().astimezone().tzinfo
    observed_at = datetime(2026, 8, 30, 10, 0, tzinfo=local_zone)
    records = []
    for offset in range(1, 8):
        day = observed_at.date() - timedelta(days=offset)
        for hour, count in ((8, 2000), (18, 5000)):
            stamp = datetime.combine(day, datetime.min.time(), local_zone).replace(hour=hour)
            records.append(
                {
                    "record_kind": "data_point",
                    "start_time": stamp.isoformat(),
                    "end_time": stamp.isoformat(),
                    "payload": {"steps": {"count": count}},
                }
            )
    today = observed_at.replace(hour=9)
    records.append(
        {
            "record_kind": "data_point",
            "start_time": today.isoformat(),
            "end_time": today.isoformat(),
            "payload": {"steps": {"count": 3000}},
        }
    )

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "steps" else []

    snapshot = build_ai_ready_snapshot(FakeStore(), "2026-08-20", "2026-08-31", now=observed_at)
    insight = next(
        item for item in snapshot["candidate_insights"] if item["evidence_id"] == "same-time:steps"
    )

    assert insight["evidence"]["today_so_far"] == 3000
    assert insight["evidence"]["same_time_mean"] == 2000
    assert insight["evidence"]["same_time_percent"] == 150
    assert "incomplete" in insight["caveat"].lower()
