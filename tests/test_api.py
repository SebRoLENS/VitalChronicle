from datetime import date

from google_health_viewer.api import ApiError, GoogleHealthClient
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
    assert DATA_TYPE_BY_KEY["heart-rate-variability"].filter_field == (
        "heart_rate_variability.sample_time.physical_time"
    )
    assert DATA_TYPE_BY_KEY["daily-oxygen-saturation"].filter_field == (
        "daily_oxygen_saturation.date"
    )
    assert DATA_TYPE_BY_KEY["hydration-log"].filter_field == (
        "hydration_log.interval.civil_start_time"
    )
    assert DATA_TYPE_BY_KEY["irregular-rhythm-notification"].filter_field == (
        "irregular_rhythm_notification.interval.civil_start_time"
    )


def test_daily_rollup_uses_daily_endpoint_and_civil_window():
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
    assert path.endswith("/dataPoints:dailyRollUp")
    assert kwargs["json_body"]["windowSizeDays"] == 1
    assert kwargs["json_body"]["range"]["start"]["date"] == {
        "year": 2026,
        "month": 8,
        "day": 1,
    }
    assert kwargs["json_body"]["range"]["end"]["date"] == {
        "year": 2026,
        "month": 8,
        "day": 3,
    }
    assert "pageSize" not in kwargs["json_body"]


def test_heart_rate_sync_uses_five_minute_server_rollups():
    spec = DATA_TYPE_BY_KEY["heart-rate"]
    assert spec.operation == "five_minute_rollup"
    assert spec.filter_field is None

    client = GoogleHealthClient(None)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "rollupDataPoints": [
                {
                    "startTime": "2026-09-02T10:00:00+00:00",
                    "endTime": "2026-09-02T10:05:00+00:00",
                    "heartRate": {
                        "beatsPerMinuteAvg": 72.5,
                        "beatsPerMinuteMin": 68.0,
                        "beatsPerMinuteMax": 79.0,
                    },
                }
            ]
        }

    client._request = fake_request
    pages = list(
        client.iter_five_minute_heart_rate_rollups(
            spec,
            date(2026, 9, 2),
            date(2026, 9, 2),
        )
    )

    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path.endswith("/dataPoints:rollUp")
    assert kwargs["json_body"]["windowSize"] == "300s"
    assert kwargs["json_body"]["range"] == {
        "startTime": "2026-09-02T00:00:00Z",
        "endTime": "2026-09-03T00:00:00Z",
    }
    assert "pageSize" not in kwargs["json_body"]
    point = pages[0][0]
    assert point["heartRate"]["beatsPerMinuteAvg"] == 72.5
    assert point["heartRate"]["beatsPerMinuteMin"] == 68.0
    assert point["heartRate"]["beatsPerMinuteMax"] == 79.0
    assert point["heartRate"]["beatsPerMinute"] == 72.5
    assert point["name"] == (
        "heart-rate:5m:2026-09-02T10:00:00+00:00:2026-09-02T10:05:00+00:00"
    )


def test_heart_rate_rollup_falls_back_to_local_five_minute_averages():
    client = GoogleHealthClient(None)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith("dataPoints:rollUp"):
            raise ApiError(400, "Invalid argument in request.")
        return {
            "dataPoints": [
                {
                    "heartRate": {
                        "sampleTime": {"physicalTime": "2026-09-02T10:01:00Z"},
                        "beatsPerMinute": 70,
                    }
                },
                {
                    "heartRate": {
                        "sampleTime": {"physicalTime": "2026-09-02T10:04:00Z"},
                        "beatsPerMinute": 74,
                    }
                },
            ]
        }

    client._request = fake_request
    pages = list(
        client.iter_five_minute_heart_rate_rollups(
            DATA_TYPE_BY_KEY["heart-rate"],
            date(2026, 9, 2),
            date(2026, 9, 2),
        )
    )

    assert len(pages) == 1
    assert len(pages[0]) == 1
    point = pages[0][0]
    assert point["startTime"] == "2026-09-02T10:00:00Z"
    assert point["endTime"] == "2026-09-02T10:05:00Z"
    assert point["heartRate"] == {
        "beatsPerMinuteAvg": 72.0,
        "beatsPerMinuteMin": 70.0,
        "beatsPerMinuteMax": 74.0,
        "beatsPerMinute": 72.0,
    }
    list_call = next(call for call in calls if call[1].endswith("/dataPoints"))
    assert "heart_rate.sample_time.physical_time" in list_call[2]["params"]["filter"]
