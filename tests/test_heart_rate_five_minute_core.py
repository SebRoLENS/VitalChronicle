from __future__ import annotations

from datetime import date, datetime

from google_health_viewer import analysis


def test_five_minute_average_is_arithmetic_mean_without_extra_smoothing():
    start = datetime(2026, 9, 2, 10, 0).astimezone().timestamp()
    points = [
        (start + 0 * 60, 60.0),
        (start + 1 * 60, 70.0),
        (start + 2 * 60, 80.0),
        (start + 5 * 60, 90.0),
        (start + 6 * 60, 100.0),
    ]

    averaged = analysis.smooth_heart_rate_points(points)

    assert len(averaged) == 2
    assert averaged[0][1] == 70.0
    assert averaged[1][1] == 95.0


def test_google_health_rollup_average_is_parsed_as_heart_rate():
    record = {
        "record_kind": "five_minute_rollup",
        "start_time": None,
        "end_time": None,
        "payload": {
            "startTime": "2026-09-02T10:00:00+00:00",
            "endTime": "2026-09-02T10:05:00+00:00",
            "heartRate": {"beatsPerMinuteAvg": 72.5},
        },
    }

    metrics = analysis.available_metrics([record], "heart-rate")
    assert metrics[0] == "__heart_rate_samples__"
    assert analysis.raw_points([record], metrics[0])[0][1] == 72.5


def test_dashboard_reports_five_minute_heart_rate_intervals():
    local_tz = datetime.now().astimezone().tzinfo
    day = date(2026, 9, 2)
    records = []
    for minute, bpm in ((0, 60.0), (1, 70.0), (2, 80.0), (5, 90.0), (6, 100.0)):
        timestamp = datetime(2026, 9, 2, 10, minute, tzinfo=local_tz).isoformat()
        records.append(
            {
                "record_kind": "data_point",
                "start_time": timestamp,
                "end_time": timestamp,
                "payload": {
                    "heartRate": {
                        "sampleTime": {"physicalTime": timestamp},
                        "beatsPerMinute": bpm,
                    }
                },
            }
        )

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "heart-rate" else []

    snapshot = analysis.build_daily_progress_snapshot(FakeStore(), day)
    heart = next(metric for metric in snapshot["metrics"] if metric["data_type"] == "heart-rate-today")

    assert heart["heart_smoothing_minutes"] == 5
    assert heart["heart_day_sample_count"] == 2
    assert [value for _timestamp, value in heart["heart_day_smoothed"]] == [70.0, 95.0]
    assert heart["current"] == 95.0
    assert heart["heart_day_min"] == 70.0
    assert heart["heart_day_max"] == 95.0
    assert heart["heart_day_mean"] == 82.5
