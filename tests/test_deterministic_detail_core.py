from __future__ import annotations

from google_health_viewer import analysis


def _sleep_record() -> dict:
    stages = [
        ("LIGHT", "2026-08-01T22:10:00+00:00", "2026-08-01T23:00:00+00:00"),
        ("DEEP", "2026-08-01T23:00:00+00:00", "2026-08-02T00:30:00+00:00"),
        ("LIGHT", "2026-08-02T00:30:00+00:00", "2026-08-02T01:00:00+00:00"),
        ("AWAKE", "2026-08-02T01:00:00+00:00", "2026-08-02T01:10:00+00:00"),
        ("LIGHT", "2026-08-02T01:10:00+00:00", "2026-08-02T03:00:00+00:00"),
        ("REM", "2026-08-02T03:00:00+00:00", "2026-08-02T04:00:00+00:00"),
        ("AWAKE", "2026-08-02T04:00:00+00:00", "2026-08-02T04:05:00+00:00"),
        ("REM", "2026-08-02T04:05:00+00:00", "2026-08-02T05:30:00+00:00"),
        ("AWAKE", "2026-08-02T05:30:00+00:00", "2026-08-02T06:00:00+00:00"),
    ]
    return {
        "start_time": "2026-08-01T22:00:00+00:00",
        "end_time": "2026-08-02T06:00:00+00:00",
        "payload": {
            "sleep": {
                "stages": [
                    {
                        "type": stage,
                        "interval": {"startTime": start, "endTime": end},
                    }
                    for stage, start, end in stages
                ]
            }
        },
    }


def test_sleep_details_include_awakenings_and_temporal_architecture():
    details = analysis._structured_ai_details("sleep", [_sleep_record()])

    assert details is not None
    assert details["sessions_with_stage_timeline"] == 1
    assert details["awakenings"]["mean_internal_per_session"] == 2.0
    assert details["awakenings"]["mean_awake_minutes_per_session"] == 45.0
    assert details["efficiency"]["mean_percent"] == 90.6
    assert details["latency_minutes"] == {
        "mean_sleep_onset": 10.0,
        "mean_rem": 290.0,
        "mean_deep": 50.0,
    }
    temporal = details["temporal_architecture"]
    assert temporal["mean_stage_transitions_per_session"] == 8.0
    assert set(temporal["stage_share_percent_by_night_third"]) == {
        "early",
        "middle",
        "late",
    }
    recent = details["recent_session_timelines"][0]
    assert recent["internal_awakenings"] == 2
    assert "AWAKE" in recent["stage_sequence"]
    assert "REM" in recent["stage_sequence"]


def test_numeric_metrics_get_quantile_distributions_on_demand():
    records = [
        {
            "start_time": f"2026-08-0{day}T12:00:00+00:00",
            "end_time": f"2026-08-0{day}T12:00:00+00:00",
            "payload": {"steps": {"count": count}},
        }
        for day, count in enumerate((100, 200, 300, 400), start=1)
    ]

    details = analysis._structured_ai_details("steps", records)

    assert details is not None
    distribution = details["distributions"][0]
    assert distribution["metric"] == "steps.count"
    assert distribution["samples"] == 4
    assert distribution["p25"] == 175.0
    assert distribution["p75"] == 325.0
    assert distribution["interquartile_range"] == 150.0
