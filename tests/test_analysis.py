from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from google_health_viewer.analysis import (
    available_metrics,
    build_daily_progress_snapshot,
    build_health_snapshot,
    categorical_daily_points,
    daily_progress,
    display_points,
    heart_rate_zone_thresholds,
    initial_x_range,
    meaningful_record_count,
    raw_points,
    recent_daily_series,
    sleep_stage_points,
    smooth_heart_rate_points,
    summarize_series,
    visual_profile,
    y_axis_range,
)


def test_heart_rate_smoothing_removes_an_isolated_spike():
    start = datetime(2026, 8, 29, 8, tzinfo=timezone.utc).timestamp()
    points = [(start + minute * 60, 70.0) for minute in range(30)]
    points[12] = (points[12][0], 180.0)

    smoothed = smooth_heart_rate_points(points)

    assert len(smoothed) == 6
    assert all(value == 70.0 for _timestamp, value in smoothed)


def _record(hour: int, count: int) -> dict:
    timestamp = datetime(2026, 8, 1, hour, tzinfo=timezone.utc).isoformat()
    return {
        "start_time": timestamp,
        "end_time": timestamp,
        "payload": {"steps": {"count": count}},
    }


def test_steps_are_aggregated_as_daily_bars():
    records = [_record(8, 100), _record(12, 250), _record(18, 300)]
    metric = available_metrics(records, "steps")[0]
    assert metric == "steps.count"
    profile = visual_profile("steps", metric)
    assert profile.chart == "bar"
    shown = display_points(raw_points(records, metric), profile)
    assert len(shown) == 1
    assert shown[0][1] == 650


def test_daily_progress_compares_today_with_previous_seven_days():
    day = 86400.0
    start = datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp()
    points = [(start + index * day, 7000.0) for index in range(7)]
    points.append((start + 7 * day, 3400.0))

    progress = daily_progress(points, "sum", date(2026, 8, 8))

    assert progress["baseline"] == 7000.0
    assert progress["current"] == 3400.0
    assert round(progress["percentage"], 1) == 48.6
    assert progress["days_used"] == 7


def test_daily_progress_uses_available_days_and_excludes_current_day():
    points = [
        (datetime(2026, 8, 5, 12, tzinfo=timezone.utc).timestamp(), 100.0),
        (datetime(2026, 8, 6, 12, tzinfo=timezone.utc).timestamp(), 300.0),
        (datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp(), 1000.0),
    ]

    progress = daily_progress(points, "sum", date(2026, 8, 8))

    assert progress["baseline"] == 200.0
    assert progress["percentage"] == 500.0
    assert progress["days_used"] == 2


def test_ai_snapshot_compares_partial_steps_with_the_same_time_of_day():
    local_zone = datetime.now().astimezone().tzinfo
    observed_at = datetime(2026, 8, 30, 10, 0, tzinfo=local_zone)
    records = []
    for offset in range(1, 8):
        day = observed_at.date() - timedelta(days=offset)
        for hour, count in ((8, 3000), (18, 4000)):
            timestamp = datetime.combine(day, datetime.min.time(), local_zone).replace(
                hour=hour
            )
            records.append(
                {
                    "record_kind": "data_point",
                    "start_time": timestamp.isoformat(),
                    "end_time": timestamp.isoformat(),
                    "payload": {"steps": {"count": count}},
                }
            )
    today_timestamp = observed_at.replace(hour=9)
    records.append(
        {
            "record_kind": "data_point",
            "start_time": today_timestamp.isoformat(),
            "end_time": today_timestamp.isoformat(),
            "payload": {"steps": {"count": 3400}},
        }
    )

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "steps" else []

    snapshot = build_health_snapshot(
        FakeStore(), "2026-08-23", "2026-08-31", now=observed_at
    )
    steps = snapshot["metrics"][0]
    context = steps["temporal_context"]

    assert steps["summary_scope"] == "completed_days_only"
    assert steps["summary"]["mean"] == 7000
    assert context["today_so_far"] == 3400
    assert context["same_time_mean"] == 3000
    assert context["same_time_days"] == 7
    assert round(context["same_time_percent"], 1) == 113.3
    assert snapshot["observation_context"]["current_day_is_incomplete"] is True


def test_recent_daily_series_contains_only_latest_seven_days():
    points = [
        (
            datetime(2026, 8, day, 12, tzinfo=timezone.utc).timestamp(),
            float(day),
        )
        for day in range(1, 10)
    ]

    recent = recent_daily_series(points, "mean", date(2026, 8, 9))

    assert [value for _timestamp, value in recent] == [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]


def test_sleep_progress_is_assigned_to_wakeup_day():
    records = []
    for day in range(1, 9):
        records.append(
            {
                "start_time": f"2026-08-{day - 1 or 1:02d}T22:00:00+00:00",
                "end_time": f"2026-08-{day:02d}T06:00:00+00:00",
                "payload": {
                    "sleep": {
                        "sleepSummary": {
                            "minutesAsleep": "240" if day == 8 else "480"
                        }
                    }
                },
            }
        )

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return records if data_type == "sleep" else []

    snapshot = build_daily_progress_snapshot(FakeStore(), date(2026, 8, 8))
    sleep = next(metric for metric in snapshot["metrics"] if metric["data_type"] == "sleep")

    assert sleep["current"] == 4.0
    assert sleep["baseline"] == 8.0
    assert sleep["percentage"] == 50.0


def test_heart_rate_uses_scatter_and_robust_summary():
    points = [(float(index), value) for index, value in enumerate([60, 61, 62, 61, 120])]
    profile = visual_profile("heart-rate", "heartRate.beatsPerMinute")
    assert profile.chart == "scatter"
    summary = summarize_series(points)
    assert summary is not None
    assert summary.median == 61
    assert summary.maximum == 120
    assert summary.anomaly_count == 1


def test_sleep_exposes_derived_duration():
    records = [
        {
            "start_time": "2026-08-01T22:30:00+00:00",
            "end_time": "2026-08-02T06:30:00+00:00",
            "payload": {"sleep": {"sleepSummary": {"minutesAsleep": "450"}}},
        }
    ]
    metrics = available_metrics(records, "sleep")
    assert metrics[0] == "__duration_hours__"
    assert raw_points(records, "__duration_hours__")[0][1] == 7.5


def test_sleep_stage_summary_is_available_for_stacked_bars():
    records = [
        {
            "start_time": "2026-08-01T22:00:00+00:00",
            "end_time": "2026-08-02T06:00:00+00:00",
            "payload": {
                "sleep": {
                    "sleepSummary": {
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": "90"},
                            {"type": "REM", "minutes": "120"},
                        ]
                    }
                }
            },
        }
    ]
    stages = sleep_stage_points(records)
    assert stages[0][1] == {"DEEP": 1.5, "REM": 2.0}


def test_raw_sleep_stage_intervals_are_available_when_summary_is_missing():
    records = [
        {
            "start_time": "2026-08-01T22:00:00+00:00",
            "end_time": "2026-08-02T06:00:00+00:00",
            "payload": {
                "sleep": {
                    "stages": [
                        {
                            "type": "DEEP",
                            "interval": {
                                "startTime": "2026-08-01T23:00:00+00:00",
                                "endTime": "2026-08-02T00:30:00+00:00",
                            },
                        }
                    ]
                }
            },
        }
    ]

    assert sleep_stage_points(records)[0][1] == {"DEEP": 1.5}


def test_complete_ai_snapshot_includes_sleep_stages_and_exercise_types():
    sleep_records = [
        {
            "start_time": "2026-08-01T22:00:00+00:00",
            "end_time": "2026-08-02T06:00:00+00:00",
            "payload": {
                "sleep": {
                    "sleepSummary": {
                        "minutesAsleep": "450",
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": "90"},
                            {"type": "REM", "minutes": "120"},
                        ],
                    }
                }
            },
        }
    ]
    exercise_records = [
        {
            "start_time": "2026-08-03T10:00:00+00:00",
            "end_time": "2026-08-03T11:00:00+00:00",
            "payload": {"exercise": {"exerciseType": "RUNNING"}},
        }
    ]

    class FakeStore:
        def list_records(self, data_type, *_args, **_kwargs):
            return {
                "sleep": sleep_records,
                "exercise": exercise_records,
            }.get(data_type, [])

    snapshot = build_health_snapshot(FakeStore(), "2026-08-01", "2026-08-05")
    by_type = {item["data_type"]: item for item in snapshot["metrics"]}

    assert by_type["sleep"]["structured_details"]["stages"]["DEEP"][
        "total_hours"
    ] == 1.5
    assert by_type["exercise"]["structured_details"]["by_type"]["RUNNING"][
        "total_hours"
    ] == 1.0
    assert snapshot["data_coverage"]["analyzed_data_types"] == ["exercise", "sleep"]


def test_time_in_heart_rate_zone_becomes_daily_stacked_minutes():
    records = [
        {
            "start_time": "2026-08-01T10:00:00+00:00",
            "end_time": "2026-08-01T10:30:00+00:00",
            "payload": {"timeInHeartRateZone": {"heartRateZoneType": "MODERATE"}},
        },
        {
            "start_time": "2026-08-01T11:00:00+00:00",
            "end_time": "2026-08-01T11:15:00+00:00",
            "payload": {"timeInHeartRateZone": {"heartRateZoneType": "PEAK"}},
        },
    ]
    assert available_metrics(records, "time-in-heart-rate-zone")[0] == "__zone_time__"
    daily = categorical_daily_points(records, "time-in-heart-rate-zone")
    assert daily[0][1] == {"MODERATE": 30.0, "PEAK": 15.0}


def test_calories_and_threshold_lists_are_parsed():
    calorie_record = {
        "start_time": "2026-08-01T00:00:00+00:00",
        "end_time": "2026-08-02T00:00:00+00:00",
        "payload": {
            "caloriesInHeartRateZone": {
                "caloriesInHeartRateZones": [
                    {"heartRateZone": "LIGHT", "kcal": 100.0},
                    {"heartRateZone": "VIGOROUS", "kcal": 35.0},
                ]
            }
        },
    }
    assert categorical_daily_points([calorie_record], "calories-in-heart-rate-zone")[0][
        1
    ] == {"LIGHT": 100.0, "VIGOROUS": 35.0}

    threshold_record = {
        "start_time": "2026-08-01T00:00:00+00:00",
        "end_time": "2026-08-01T00:00:00+00:00",
        "payload": {
            "dailyHeartRateZones": {
                "heartRateZones": [
                    {
                        "heartRateZoneType": "MODERATE",
                        "minBeatsPerMinute": "115",
                        "maxBeatsPerMinute": "140",
                    }
                ]
            }
        },
    }
    assert heart_rate_zone_thresholds([threshold_record])[0][1] == {
        "MODERATE": (115.0, 140.0)
    }


def test_readable_axis_ignores_single_extreme_outlier():
    values = [60.0 + (index % 8) for index in range(100)] + [220.0]
    points = [(float(index), value) for index, value in enumerate(values)]
    profile = visual_profile("heart-rate", "heartRate.beatsPerMinute")
    readable = y_axis_range(points, "heart-rate", profile)
    full = y_axis_range(points, "heart-rate", profile, show_all=True)
    assert readable is not None and full is not None
    assert readable[1] < 100
    assert full[1] > 220


def test_full_sleep_respiratory_rate_is_the_default_metric():
    record = {
        "start_time": "2026-08-01T06:00:00+00:00",
        "end_time": "2026-08-01T06:00:00+00:00",
        "payload": {
            "respiratoryRateSleepSummary": {
                "deepSleepStats": {"breathsPerMinute": 11.0},
                "fullSleepStats": {"breathsPerMinute": 14.0},
            }
        },
    }
    metrics = available_metrics([record], "respiratory-rate-sleep-summary")
    assert metrics[0].endswith("fullSleepStats.breathsPerMinute")


def test_zero_stroke_swim_records_are_not_meaningful():
    record = {
        "start_time": "2026-08-01T10:00:00+00:00",
        "end_time": "2026-08-01T10:01:00+00:00",
        "payload": {"swimLengthsData": {"strokeCount": "0"}},
    }
    assert meaningful_record_count("swim-lengths-data", [record]) == 0
    assert "__swim_lengths__" not in available_metrics([record], "swim-lengths-data")


def test_dense_measurements_open_on_the_latest_day():
    points = [(float(hour * 3600), 70.0) for hour in range(24 * 7)]
    profile = visual_profile("heart-rate", "heartRate.beatsPerMinute")
    viewport = initial_x_range(points, "heart-rate", profile)
    assert viewport is not None
    latest_local = datetime.fromtimestamp(points[-1][0])  # noqa: DTZ006
    expected_start = datetime.combine(latest_local.date(), datetime.min.time()).timestamp()
    assert viewport[0] == expected_start
    assert viewport[1] - viewport[0] < 86400
    assert viewport[1] >= points[-1][0]


def test_sparse_heart_rate_still_opens_on_one_calendar_day():
    points = [
        (
            datetime(2026, 8, day, 8 + index, tzinfo=timezone.utc).timestamp(),
            65.0 + index,
        )
        for day in range(23, 30)
        for index in range(2)
    ]
    profile = visual_profile("heart-rate", "heartRate.beatsPerMinute")
    viewport = initial_x_range(points, "heart-rate", profile)

    assert viewport is not None
    latest_local = datetime.fromtimestamp(points[-1][0])  # noqa: DTZ006
    expected_start = datetime.combine(latest_local.date(), datetime.min.time()).timestamp()
    assert viewport[0] == expected_start
    assert viewport[1] - viewport[0] < 86400


def test_readable_calorie_scale_ignores_single_extreme_day():
    values = [2000.0 + index * 10 for index in range(20)] + [50000.0]
    points = [(float(index), value) for index, value in enumerate(values)]
    profile = visual_profile("total-calories", "totalCalories.kcalSum")
    readable = y_axis_range(points, "total-calories", profile)
    full = y_axis_range(points, "total-calories", profile, show_all=True)
    assert readable is not None and full is not None
    assert readable[1] < 5000
    assert full[1] > 50000


def test_readable_oxygen_scale_does_not_collapse_because_of_zero_markers():
    values = [0.0] * 10 + [95.0 + (index % 4) for index in range(100)]
    points = [(float(index), value) for index, value in enumerate(values)]
    profile = visual_profile("oxygen-saturation", "oxygenSaturation.percentage")
    readable = y_axis_range(points, "oxygen-saturation", profile)
    full = y_axis_range(points, "oxygen-saturation", profile, show_all=True)
    assert readable is not None and full is not None
    assert readable[0] > 90
    assert full[0] < 0


def test_long_daily_series_opens_on_latest_thirty_days():
    day = 86400.0
    points = [(index * day, 2000.0) for index in range(365)]
    profile = visual_profile("total-calories", "totalCalories.kcalSum")
    viewport = initial_x_range(points, "total-calories", profile)
    assert viewport is not None
    assert 30 * day <= viewport[1] - viewport[0] < 31 * day
