from google_health_viewer.utils import coerce_number, extract_source, extract_times, flatten_dict


def test_flatten_dict_keeps_scalar_paths():
    payload = {"steps": {"count": 42, "interval": {"startTime": "2026-01-01T10:00:00Z"}}}
    flat = flatten_dict(payload)
    assert flat["steps.count"] == 42
    assert flat["steps.interval.startTime"] == "2026-01-01T10:00:00Z"


def test_extract_physical_times_and_source():
    payload = {
        "dataSource": {"platform": "FITBIT", "device": {"displayName": "Fitbit Air"}},
        "steps": {
            "interval": {
                "startTime": "2026-01-01T10:00:00Z",
                "endTime": "2026-01-01T10:01:00Z",
            },
            "count": "12",
        },
    }
    assert extract_times(payload) == (
        "2026-01-01T10:00:00Z",
        "2026-01-01T10:01:00Z",
    )
    assert extract_source(payload) == "Fitbit Air · FITBIT"


def test_extract_daily_date():
    payload = {"dailyRestingHeartRate": {"date": {"year": 2026, "month": 8, "day": 29}}}
    start, end = extract_times(payload)
    assert start == "2026-08-29T00:00:00"
    assert end == start


def test_protobuf_integer_strings_are_numeric():
    assert coerce_number("72") == 72.0
    assert coerce_number("1.25e2") == 125.0
    assert coerce_number("ACTIVE") is None
