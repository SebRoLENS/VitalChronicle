from __future__ import annotations

from google_health_viewer import analysis
from google_health_viewer import deterministic_context_patch as detail_patch


def _sleep_record(*, include_short_awakenings: bool = True, empty_short_awakenings: bool = False) -> dict:
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
    payload = {
        "startTime": "2026-08-01T22:00:00+00:00",
        "endTime": "2026-08-02T06:00:00+00:00",
        "sleepStages": [
            {"type": stage, "startTime": start, "endTime": end}
            for stage, start, end in stages
        ],
    }
    if include_short_awakenings:
        payload["shortAwakenings"] = (
            []
            if empty_short_awakenings
            else [
                {
                    "type": "AWAKE",
                    "startTime": "2026-08-02T00:10:00+00:00",
                    "endTime": "2026-08-02T00:10:30+00:00",
                },
                {
                    "type": "AWAKE",
                    "startTime": "2026-08-02T03:20:00+00:00",
                    "endTime": "2026-08-02T03:21:00+00:00",
                },
            ]
        )
    return {
        "start_time": "2026-08-01T22:00:00+00:00",
        "end_time": "2026-08-02T06:00:00+00:00",
        "payload": payload,
    }


def test_sleep_details_read_google_sleep_stages_and_short_awakenings():
    details = analysis._structured_ai_details("sleep", [_sleep_record()])

    assert details is not None
    assert details["sessions_with_stage_timeline"] == 1
    assert details["awakenings"]["mean_internal_per_session"] == 2.0
    assert details["awakenings"]["mean_awake_minutes_per_session"] == 45.0
    assert details["awakenings"]["short_awakenings_sessions_available"] == 1
    assert details["awakenings"]["short_awakenings_total"] == 2
    assert details["awakenings"]["mean_short_awakenings_per_available_session"] == 2.0
    assert details["awakenings"]["mean_short_awakening_duration_seconds"] == 45.0
    assert details["efficiency"]["mean_percent"] == 90.6
    recent = details["recent_session_timelines"][0]
    assert recent["internal_awakenings"] == 2
    assert recent["short_awakenings_count"] == 2
    assert "AWAKE" in recent["stage_sequence"]
    assert "REM" in recent["stage_sequence"]


def test_zero_short_awakenings_is_not_treated_as_missing():
    zero_details = analysis._structured_ai_details(
        "sleep", [_sleep_record(empty_short_awakenings=True)]
    )
    missing_details = analysis._structured_ai_details(
        "sleep", [_sleep_record(include_short_awakenings=False)]
    )

    assert zero_details is not None
    assert zero_details["awakenings"]["short_awakenings_sessions_available"] == 1
    assert zero_details["awakenings"]["short_awakenings_total"] == 0
    assert zero_details["awakenings"]["mean_short_awakenings_per_available_session"] == 0.0

    assert missing_details is not None
    assert missing_details["awakenings"]["short_awakenings_sessions_available"] == 0
    assert missing_details["awakenings"]["short_awakenings_total"] is None


def _heart_record(timestamp: str, bpm: float) -> dict:
    return {
        "start_time": timestamp,
        "end_time": timestamp,
        "payload": {"heartRate": {"beatsPerMinute": bpm}},
    }


def test_heart_rate_is_contextualized_by_exercise_and_activity_level():
    heart_records = [
        _heart_record("2026-08-03T10:00:00+00:00", 70),
        _heart_record("2026-08-03T10:10:00+00:00", 120),
        _heart_record("2026-08-03T10:20:00+00:00", 150),
        _heart_record("2026-08-03T11:00:00+00:00", 72),
    ]
    exercise_records = [
        {
            "start_time": "2026-08-03T10:05:00+00:00",
            "end_time": "2026-08-03T10:30:00+00:00",
            "payload": {"exercise": {"exerciseType": "RUNNING"}},
        }
    ]
    activity_level_records = [
        {
            "start_time": "2026-08-03T10:00:00+00:00",
            "end_time": "2026-08-03T10:25:00+00:00",
            "payload": {"activityLevel": {"activityLevel": "VIGOROUS"}},
        }
    ]

    context = detail_patch._heart_rate_activity_context(
        heart_records, exercise_records, activity_level_records, analysis
    )

    assert context is not None
    summary = context["summary"]
    assert summary["heart_rate_samples_during_exercise"] == 2
    assert summary["heart_rate_samples_during_exercise_percent"] == 50.0
    assert summary["mean_bpm_during_exercise"] == 135.0
    assert summary["mean_bpm_outside_exercise"] == 71.0
    assert summary["mean_difference_bpm_exercise_vs_outside"] == 64.0
    assert summary["high_hr_samples_during_exercise_percent"] == 100.0
    assert context["by_exercise_type"]["RUNNING"]["max_bpm"] == 150.0
    assert context["by_activity_level"]["VIGOROUS"]["samples"] == 3


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
