from datetime import date

from google_health_viewer.api import GoogleHealthClient
from google_health_viewer.constants import DATA_TYPE_BY_KEY


def test_interval_filter_uses_exclusive_next_day():
    spec = DATA_TYPE_BY_KEY["steps"]
    value = GoogleHealthClient._date_filter(spec, date(2026, 8, 1), date(2026, 8, 2))
    assert "steps.interval.start_time" in value
    assert "2026-08-03" in value


def test_daily_filter_uses_civil_dates():
    spec = DATA_TYPE_BY_KEY["daily-resting-heart-rate"]
    value = GoogleHealthClient._date_filter(spec, date(2026, 8, 1), date(2026, 8, 2))
    assert value == (
        'daily_resting_heart_rate.date >= "2026-08-01" AND '
        'daily_resting_heart_rate.date < "2026-08-03"'
    )


def test_session_and_ecg_filters_follow_supported_api_fields():
    nutrition = GoogleHealthClient._date_filter(
        DATA_TYPE_BY_KEY["nutrition-log"], date(2026, 8, 1), date(2026, 8, 2)
    )
    assert nutrition is not None
    assert "nutrition_log.interval.civil_start_time" in nutrition

    ecg = GoogleHealthClient._date_filter(
        DATA_TYPE_BY_KEY["electrocardiogram"], date(2026, 8, 1), date(2026, 8, 2)
    )
    assert ecg is not None
    assert ecg.startswith("electrocardiogram.interval.start_time >=")
    assert " AND " not in ecg


def test_reference_food_catalogs_are_not_synced_automatically():
    assert DATA_TYPE_BY_KEY["food"].auto_sync is False
    assert DATA_TYPE_BY_KEY["food-measurement-unit"].auto_sync is False


def test_multiword_sample_and_daily_filters_use_api_snake_case():
    assert DATA_TYPE_BY_KEY["heart-rate"].filter_field == (
        "heart_rate.sample_time.physical_time"
    )
    assert DATA_TYPE_BY_KEY["daily-oxygen-saturation"].filter_field == (
        "daily_oxygen_saturation.date"
    )
    assert DATA_TYPE_BY_KEY["hydration-log"].filter_field == (
        "hydration_log.interval.civil_start_time"
    )


def test_daily_rollup_uses_current_rollup_endpoint_and_physical_window():
    client = GoogleHealthClient(None)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"rollupDataPoints": []}

    client._request = fake_request
    list(
        client.iter_daily_rollups(
            DATA_TYPE_BY_KEY["total-calories"],
            date(2026, 8, 1),
            date(2026, 8, 2),
        )
    )
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path.endswith("/dataPoints:rollUp")
    assert kwargs["json_body"]["windowSize"] == "86400s"
    assert "startTime" in kwargs["json_body"]["range"]
