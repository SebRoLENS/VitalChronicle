import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from google_health_viewer.analysis import build_daily_progress_snapshot
from google_health_viewer.storage import HealthStore


def test_upsert_and_export(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    record = {
        "name": "users/me/dataTypes/steps/dataPoints/1",
        "steps": {
            "interval": {
                "startTime": "2026-08-29T10:00:00Z",
                "endTime": "2026-08-29T10:01:00Z",
            },
            "count": 30,
        },
    }
    assert store.upsert_records("steps", [record]) == 1
    assert store.upsert_records("steps", [record]) == 1
    assert store.counts() == {"steps": 1}
    destination = tmp_path / "steps.csv"
    assert store.export_csv("steps", destination) == 1
    assert "steps.count" in destination.read_text(encoding="utf-8-sig")

    store.save_resource("profile", {"age": 33})
    archive = tmp_path / "complete.zip"
    store.export_archive(archive)
    with zipfile.ZipFile(archive) as zipped:
        assert "steps.jsonl" in zipped.namelist()
        assert "account-and-devices.json" in zipped.namelist()
    assert store.data_revision() is not None


def test_sync_ranges_return_only_gaps_and_always_refresh_today(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.mark_sync_range("steps", date(2026, 8, 1), date(2026, 8, 10))
    store.mark_sync_range("steps", date(2026, 8, 15), date(2026, 8, 28))

    missing = store.missing_sync_ranges(
        "steps",
        date(2026, 8, 1),
        date(2026, 8, 29),
        refresh_date=date(2026, 8, 29),
    )
    assert missing == [
        (date(2026, 8, 11), date(2026, 8, 14)),
        (date(2026, 8, 29), date(2026, 8, 29)),
    ]


def test_existing_records_bootstrap_coverage_after_upgrade(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day in (5, 20):
        store.upsert_records(
            "steps",
            [
                {
                    "name": f"users/me/dataTypes/steps/dataPoints/{day}",
                    "steps": {
                        "interval": {
                            "startTime": f"2026-08-{day:02d}T10:00:00Z",
                            "endTime": f"2026-08-{day:02d}T10:01:00Z",
                        },
                        "count": 30,
                    },
                }
            ],
        )

    missing = store.missing_sync_ranges(
        "steps",
        date(2026, 8, 1),
        date(2026, 8, 29),
        refresh_date=date(2026, 8, 29),
    )
    assert missing == [
        (date(2026, 8, 1), date(2026, 8, 4)),
        (date(2026, 8, 21), date(2026, 8, 29)),
    ]


def test_newest_record_limit_keeps_latest_measurements(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day in (1, 2, 3):
        store.upsert_records(
            "steps",
            [
                {
                    "name": f"users/me/dataTypes/steps/dataPoints/{day}",
                    "steps": {
                        "interval": {
                            "startTime": f"2026-08-{day:02d}T10:00:00Z",
                            "endTime": f"2026-08-{day:02d}T10:01:00Z",
                        },
                        "count": day,
                    },
                }
            ],
        )
    records = store.list_records("steps", limit=2, newest=True)
    assert [record["payload"]["steps"]["count"] for record in records] == [2, 3]
    assert store.data_date_bounds() == (date(2026, 8, 1), date(2026, 8, 3))


def test_daily_rollup_replaces_the_changing_value_for_the_same_day(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")

    def rollup(kcal: float) -> dict:
        return {
            "startTime": "2026-08-29T00:00:00Z",
            "endTime": "2026-08-30T00:00:00Z",
            "totalCalories": {"kcalSum": kcal},
        }

    store.upsert_records("total-calories", [rollup(1200)], "daily_rollup")
    store.upsert_records("total-calories", [rollup(1800)], "daily_rollup")
    records = store.list_records("total-calories")
    assert len(records) == 1
    assert records[0]["payload"]["totalCalories"]["kcalSum"] == 1800


def test_dashboard_weight_uses_latest_measurement_even_when_older(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day, kilograms in ((12, 85.0), (20, 84.2)):
        store.upsert_records(
            "weight",
            [
                {
                    "name": f"weight-{day}",
                    "startTime": f"2026-08-{day:02d}T12:00:00Z",
                    "weight": {"kilograms": kilograms},
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    weight = next(metric for metric in snapshot["metrics"] if metric["data_type"] == "weight")

    assert weight["current"] == 84.2
    assert weight["value_date"] == "2026-08-20"


def test_dashboard_converts_official_weight_grams_to_kilograms(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.upsert_records(
        "weight",
        [
            {
                "name": "weight-official",
                "weight": {
                    "sampleTime": {"physicalTime": "2026-08-20T12:00:00Z"},
                    "weightGrams": "85000",
                },
            }
        ],
    )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    weight = next(metric for metric in snapshot["metrics"] if metric["data_type"] == "weight")

    assert weight["current"] == 85.0
    assert weight["unit"] == "kg"


def test_dashboard_adds_today_heart_rate_sparkline(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for hour, bpm in ((8, 58), (12, 74), (18, 112)):
        store.upsert_records(
            "heart-rate",
            [
                {
                    "name": f"heart-{hour}",
                    "startTime": f"2026-08-29T{hour:02d}:00:00Z",
                    "heartRate": {"beatsPerMinute": bpm},
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    heart = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "heart-rate-today"
    )

    assert len(heart["heart_day_points"]) == 3
    assert len(heart["heart_day_smoothed"]) == 3
    assert heart["heart_smoothing_minutes"] == 15
    assert heart["heart_day_min"] == 58
    assert heart["heart_day_max"] == 112
    assert "sparkline" not in heart


def test_dashboard_heart_rate_uses_all_today_samples_and_prior_week_band(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day in range(22, 29):
        for hour, bpm in ((8, 60), (18, 80)):
            store.upsert_records(
                "heart-rate",
                [
                    {
                        "name": f"heart-{day}-{hour}",
                        "heartRate": {
                            "sampleTime": {
                                "physicalTime": f"2026-08-{day:02d}T{hour:02d}:00:00Z"
                            },
                            "beatsPerMinute": str(bpm),
                        },
                    }
                ],
            )
    for index in range(300):
        minute = index % 60
        hour = 8 + index // 60
        store.upsert_records(
            "heart-rate",
            [
                {
                    "name": f"today-heart-{index}",
                    "heartRate": {
                        "sampleTime": {
                            "physicalTime": f"2026-08-29T{hour:02d}:{minute:02d}:00Z"
                        },
                        "beatsPerMinute": str(65 + index % 10),
                    },
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    heart = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "heart-rate-today"
    )

    assert len(heart["heart_day_points"]) == 300
    assert len(heart["heart_day_smoothed"]) < len(heart["heart_day_points"])
    assert heart["heart_day_date"] == "2026-08-29"
    assert "sparkline" not in heart
    assert all(
        datetime.fromtimestamp(timestamp, timezone.utc).date() == date(2026, 8, 29)
        for timestamp, _value in heart["heart_day_points"]
    )
    assert all(
        datetime.fromtimestamp(timestamp, timezone.utc).date() == date(2026, 8, 29)
        for timestamp, _value in heart["heart_day_smoothed"]
    )
    assert heart["heart_day_mean"] == 69.5
    assert heart["heart_day_sample_count"] == 300


def test_dashboard_reads_multiple_heart_rate_samples_from_one_record(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.upsert_records(
        "heart-rate",
        [
            {
                "name": "heart-series",
                "startTime": "2026-08-29T08:00:00Z",
                "endTime": "2026-08-29T10:00:00Z",
                "heartRate": {
                    "samples": [
                        {"time": "2026-08-29T08:00:00Z", "beatsPerMinute": 61},
                        {"time": "2026-08-29T09:00:00Z", "beatsPerMinute": 79},
                        {"time": "2026-08-29T10:00:00Z", "beatsPerMinute": 96},
                    ]
                },
            }
        ],
    )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    heart = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "heart-rate-today"
    )

    assert [value for _timestamp, value in heart["heart_day_points"]] == [61, 79, 96]


def test_dashboard_heart_graph_uses_current_day_not_selected_period_end(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day, bpm in ((28, 61), (29, 82)):
        store.upsert_records(
            "heart-rate",
            [
                {
                    "name": f"heart-{day}",
                    "heartRate": {
                        "sampleTime": {
                            "physicalTime": f"2026-08-{day:02d}T12:00:00Z"
                        },
                        "beatsPerMinute": bpm,
                    },
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(
        store,
        date(2026, 8, 28),
        heart_day=date(2026, 8, 29),
    )
    heart = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "heart-rate-today"
    )

    assert [value for _timestamp, value in heart["heart_day_points"]] == [82]
    assert heart["heart_day_date"] == "2026-08-29"


def test_dashboard_keeps_resting_and_intraday_heart_rate_in_separate_cards(
    tmp_path: Path,
):
    store = HealthStore(tmp_path / "health.sqlite3")
    resting_values = [60, 62, 64, 66, 68, 70, 72]
    for offset, bpm in enumerate(resting_values, start=22):
        store.upsert_records(
            "daily-resting-heart-rate",
            [
                {
                    "name": f"resting-{offset}",
                    "dailyRestingHeartRate": {
                        "date": {"year": 2026, "month": 8, "day": offset},
                        "beatsPerMinute": bpm,
                    },
                }
            ],
        )
    store.upsert_records(
        "heart-rate",
        [
            {
                "name": "heart-today",
                "heartRate": {
                    "sampleTime": {"physicalTime": "2026-08-29T10:00:00Z"},
                    "beatsPerMinute": 75,
                },
            }
        ],
    )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    resting = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "daily-resting-heart-rate"
    )
    intraday = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "heart-rate-today"
    )

    assert [value for _timestamp, value in resting["sparkline"]] == resting_values
    assert resting["sparkline_kind"] == "previous_seven_days"
    assert resting["sparkline_mean"] == 66.0
    assert resting["sparkline_std"] > 0
    assert "heart_day_smoothed" not in resting
    assert [value for _timestamp, value in intraday["heart_day_smoothed"]] == [75]
    assert "sparkline" not in intraday


def test_dashboard_daily_vital_sparkline_has_mean_and_standard_deviation(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day in range(23, 30):
        store.upsert_records(
            "daily-heart-rate-variability",
            [
                {
                    "name": f"hrv-{day}",
                    "startTime": f"2026-08-{day:02d}T12:00:00Z",
                    "dailyHeartRateVariability": {
                        "averageHeartRateVariabilityMilliseconds": 40 + day - 23
                    },
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    hrv = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "daily-heart-rate-variability"
    )

    assert len(hrv["sparkline"]) == 7
    assert hrv["sparkline_mean"] == 43.0
    assert hrv["sparkline_std"] > 0


def test_dashboard_daily_vital_falls_back_to_latest_available_day(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for day in range(22, 29):
        store.upsert_records(
            "daily-oxygen-saturation",
            [
                {
                    "name": f"spo2-{day}",
                    "dailyOxygenSaturation": {
                        "date": {"year": 2026, "month": 8, "day": day},
                        "averagePercentage": 95.0 + (day - 22) / 10,
                    },
                }
            ],
        )

    snapshot = build_daily_progress_snapshot(store, date(2026, 8, 29))
    oxygen = next(
        metric
        for metric in snapshot["metrics"]
        if metric["data_type"] == "daily-oxygen-saturation"
    )

    assert oxygen["current"] == 95.6
    assert oxygen["value_date"] == "2026-08-28"
    assert oxygen["latest_available"] is True
    assert len(oxygen["sparkline"]) == 7


def test_app_markers_persist_for_one_time_repairs(tmp_path: Path):
    store = HealthStore(tmp_path / "health.sqlite3")
    assert store.has_app_marker("repair") is False
    store.set_app_marker("repair")
    assert store.has_app_marker("repair") is True
