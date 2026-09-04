from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from typing import Any

_WAKE_STAGES = {"AWAKE", "WAKE", "OUT_OF_BED"}
_KNOWN_STAGES = (
    "OUT_OF_BED",
    "AWAKE",
    "WAKE",
    "DEEP",
    "REM",
    "LIGHT",
    "CORE",
    "ASLEEP",
    "SLEEP",
    "RESTLESS",
    "UNKNOWN",
)
_MAX_RECENT_SLEEP_SESSIONS = 3
_MAX_TIMELINE_RUNS = 48
_MAX_RECENT_EXERCISE_SESSIONS = 6


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _normalize_stage(value: Any) -> str:
    text = str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    for stage in _KNOWN_STAGES:
        if text == stage or text.endswith(f"_{stage}"):
            return stage
    return text or "UNKNOWN"


def _find_named_list_with_presence(
    value: Any, names: set[str]
) -> tuple[bool, list[dict[str, Any]]]:
    lowered_names = {name.lower() for name in names}
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in lowered_names:
                if isinstance(child, list):
                    return True, [item for item in child if isinstance(item, dict)]
                return True, []
        for child in value.values():
            found, items = _find_named_list_with_presence(child, lowered_names)
            if found:
                return found, items
    elif isinstance(value, list):
        for child in value:
            found, items = _find_named_list_with_presence(child, lowered_names)
            if found:
                return found, items
    return False, []


def _find_named_string(value: Any, names: set[str]) -> str | None:
    lowered = {name.lower() for name in names}
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in lowered and isinstance(child, str) and child.strip():
                return child.strip()
            found = _find_named_string(child, lowered)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_named_string(child, lowered)
            if found:
                return found
    return None


def _intervals_from_entries(
    entries: list[dict[str, Any]], analysis
) -> list[tuple[float, float, str]]:
    intervals: list[tuple[float, float, str]] = []
    for entry in entries:
        start = analysis.parse_timestamp(
            analysis._find_named_value(entry, {"starttime", "physicaltime"})
        )
        end = analysis.parse_timestamp(analysis._find_named_value(entry, {"endtime"}))
        if start is None or end is None or end <= start:
            continue
        intervals.append((start, end, _normalize_stage(entry.get("type"))))
    return sorted(intervals, key=lambda item: (item[0], item[1]))


def _stage_intervals(record: dict[str, Any], analysis) -> list[tuple[float, float, str]]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return []
    # Reconciled/list responses can expose sleepStages; Sleep write payloads use stages.
    for names in ({"sleepStages"}, {"stages"}):
        present, entries = _find_named_list_with_presence(payload, names)
        if present:
            return _intervals_from_entries(entries, analysis)
    return []


def _short_awakening_intervals(
    record: dict[str, Any], analysis
) -> tuple[bool, list[tuple[float, float, str]]]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False, []
    present, entries = _find_named_list_with_presence(payload, {"shortAwakenings"})
    return (present, _intervals_from_entries(entries, analysis)) if present else (False, [])


def _sleep_stage_totals(record: dict[str, Any], analysis) -> dict[str, float]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    totals: dict[str, float] = defaultdict(float)

    present, summarized = _find_named_list_with_presence(payload, {"stagesSummary"})
    if present and summarized:
        for stage in summarized:
            minutes = analysis.coerce_number(stage.get("minutes"))
            if minutes is not None and minutes >= 0:
                totals[_normalize_stage(stage.get("type"))] += minutes / 60.0
        if totals:
            return dict(totals)

    for start, end, stage in _stage_intervals(record, analysis):
        totals[stage] += (end - start) / 3600.0
    return dict(totals)


def _sleep_stage_points(
    records: list[dict[str, Any]], analysis
) -> list[tuple[float, dict[str, float]]]:
    result: list[tuple[float, dict[str, float]]] = []
    for record in records:
        timestamp = analysis.parse_timestamp(record.get("start_time") or record.get("end_time"))
        totals = _sleep_stage_totals(record, analysis)
        if timestamp is not None and totals:
            result.append((timestamp, totals))
    return sorted(result, key=lambda item: item[0])


def _payload_looks_like_sleep(payload: Any) -> bool:
    if isinstance(payload, dict):
        keys = {key.lower() for key in payload}
        if keys & {"sleep", "sleepstages", "stagessummary", "shortawakenings"}:
            return True
        return any(_payload_looks_like_sleep(child) for child in payload.values())
    if isinstance(payload, list):
        return any(_payload_looks_like_sleep(child) for child in payload)
    return False


def _duration_hours(record: dict[str, Any], analysis, original_duration) -> float | None:
    payload = record.get("payload") or {}
    if not _payload_looks_like_sleep(payload):
        return original_duration(record)

    explicit_minutes = analysis.coerce_number(
        analysis._find_named_value(payload, {"minutesasleep"})
    )
    if explicit_minutes is not None:
        return max(0.0, explicit_minutes / 60.0)

    start = analysis.parse_timestamp(record.get("start_time"))
    end = analysis.parse_timestamp(record.get("end_time"))
    if start is None or end is None or end < start:
        return original_duration(record)

    interval_hours = (end - start) / 3600.0
    explicit_awake = analysis.coerce_number(
        analysis._find_named_value(
            payload, {"minutesawake", "awakeminutes", "minuteswake", "wakeminutes"}
        )
    )
    if explicit_awake is not None:
        return max(0.0, interval_hours - explicit_awake / 60.0)

    awake_hours = sum(
        hours
        for stage, hours in _sleep_stage_totals(record, analysis).items()
        if stage in _WAKE_STAGES
    )
    return max(0.0, interval_hours - awake_hours)


def _stage_runs(intervals: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    runs: list[tuple[float, float, str]] = []
    for start, end, stage in intervals:
        if runs and runs[-1][2] == stage and start <= runs[-1][1] + 1.0:
            old_start, old_end, old_stage = runs[-1]
            runs[-1] = (old_start, max(old_end, end), old_stage)
        else:
            runs.append((start, end, stage))
    return runs


def _sleep_session_details(record: dict[str, Any], analysis) -> dict[str, Any] | None:
    runs = _stage_runs(_stage_intervals(record, analysis))
    if not runs:
        return None

    start = analysis.parse_timestamp(record.get("start_time")) or runs[0][0]
    end = analysis.parse_timestamp(record.get("end_time")) or runs[-1][1]
    if end <= start:
        return None

    first_sleep_index = next(
        (index for index, (_s, _e, stage) in enumerate(runs) if stage not in _WAKE_STAGES),
        None,
    )
    first_sleep = runs[first_sleep_index][0] if first_sleep_index is not None else None
    wake_indices = [i for i, (_s, _e, stage) in enumerate(runs) if stage in _WAKE_STAGES]
    internal_wake_indices = [
        i
        for i in wake_indices
        if any(stage not in _WAKE_STAGES for _s, _e, stage in runs[:i])
        and any(stage not in _WAKE_STAGES for _s, _e, stage in runs[i + 1 :])
    ]
    wake_minutes = sum((runs[i][1] - runs[i][0]) / 60.0 for i in wake_indices)

    def latency(stage_name: str) -> float | None:
        if first_sleep is None:
            return None
        candidate = next(
            (s for s, _e, stage in runs if stage == stage_name and s >= first_sleep),
            None,
        )
        return None if candidate is None else max(0.0, (candidate - first_sleep) / 60.0)

    sleep_hours = analysis.duration_hours(record)
    interval_hours = (end - start) / 3600.0
    efficiency = (
        min(100.0, max(0.0, sleep_hours / interval_hours * 100.0))
        if sleep_hours is not None and interval_hours > 0
        else None
    )

    encoded = []
    for run_start, run_end, stage in runs[:_MAX_TIMELINE_RUNS]:
        encoded.append(
            f"{max(0, round((run_start - start) / 60.0))}-"
            f"{max(0, round((run_end - start) / 60.0))}m:{stage}"
        )
    if len(runs) > _MAX_TIMELINE_RUNS:
        encoded.append(f"+{len(runs) - _MAX_TIMELINE_RUNS}_more")

    short_present, short_intervals = _short_awakening_intervals(record, analysis)
    short_total_minutes = sum((end_ - start_) / 60.0 for start_, end_, _ in short_intervals)

    return {
        "date": datetime.fromtimestamp(end).astimezone().date().isoformat(),
        "start_local": datetime.fromtimestamp(start).astimezone().isoformat(timespec="minutes"),
        "end_local": datetime.fromtimestamp(end).astimezone().isoformat(timespec="minutes"),
        "total_sleep_hours": _rounded(sleep_hours, 2),
        "internal_awakenings": len(internal_wake_indices),
        "awake_minutes": _rounded(wake_minutes, 1),
        "short_awakenings_count": len(short_intervals) if short_present else None,
        "short_awakenings_total_minutes": _rounded(short_total_minutes, 1) if short_present else None,
        "stage_sequence": " | ".join(encoded),
        "latencies_minutes": {
            "sleep_onset": (
                _rounded((first_sleep - start) / 60.0, 1) if first_sleep is not None else None
            ),
            "rem": _rounded(latency("REM"), 1),
            "deep": _rounded(latency("DEEP"), 1),
        },
        "sleep_efficiency_percent": _rounded(efficiency, 1),
        "stage_transitions": sum(1 for left, right in pairwise(runs) if left[2] != right[2]),
        "_runs": runs,
        "_bounds": (start, end),
    }


def _stage_share_by_third(
    sessions: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {
        "early": defaultdict(float),
        "middle": defaultdict(float),
        "late": defaultdict(float),
    }
    names = ("early", "middle", "late")
    for session in sessions:
        start, end = session["_bounds"]
        width = (end - start) / 3.0
        boundaries = (start, start + width, start + 2.0 * width, end)
        for run_start, run_end, stage in session["_runs"]:
            for index, name in enumerate(names):
                overlap = max(
                    0.0,
                    min(run_end, boundaries[index + 1]) - max(run_start, boundaries[index]),
                )
                if overlap > 0:
                    totals[name][stage] += overlap / 60.0

    result: dict[str, dict[str, float]] = {}
    for name in names:
        total_minutes = sum(totals[name].values())
        if total_minutes > 0:
            result[name] = {
                stage: round(minutes / total_minutes * 100.0, 1)
                for stage, minutes in sorted(totals[name].items())
            }
    return result


def _sleep_ai_details(records: list[dict[str, Any]], analysis) -> dict[str, Any]:
    stage_sessions = _sleep_stage_points(records, analysis)
    totals: dict[str, float] = defaultdict(float)
    for _timestamp, stages in stage_sessions:
        for stage, hours in stages.items():
            totals[_normalize_stage(stage)] += hours
    staged_hours = sum(totals.values())
    session_count = len(stage_sessions)
    stages = {
        stage: {
            "total_hours": round(hours, 2),
            "mean_hours_per_session": round(hours / session_count, 2),
            "share_percent": round(hours / staged_hours * 100.0, 1),
        }
        for stage, hours in sorted(totals.items())
        if session_count and staged_hours > 0
    }

    sessions = [
        details
        for record in records
        if (details := _sleep_session_details(record, analysis)) is not None
    ]
    sessions.sort(key=lambda item: item["end_local"])

    def mean(values: list[float]) -> float | None:
        return _rounded(statistics.fmean(values), 1) if values else None

    internal = [float(item["internal_awakenings"]) for item in sessions]
    awake_minutes = [float(item["awake_minutes"]) for item in sessions if item["awake_minutes"] is not None]
    efficiencies = [
        float(item["sleep_efficiency_percent"])
        for item in sessions
        if item["sleep_efficiency_percent"] is not None
    ]
    transitions = [float(item["stage_transitions"]) for item in sessions]
    onset = [
        float(item["latencies_minutes"]["sleep_onset"])
        for item in sessions
        if item["latencies_minutes"]["sleep_onset"] is not None
    ]
    rem = [
        float(item["latencies_minutes"]["rem"])
        for item in sessions
        if item["latencies_minutes"]["rem"] is not None
    ]
    deep = [
        float(item["latencies_minutes"]["deep"])
        for item in sessions
        if item["latencies_minutes"]["deep"] is not None
    ]

    short_counts: list[float] = []
    short_minutes: list[float] = []
    short_durations: list[float] = []
    short_available = 0
    for record in records:
        present, intervals = _short_awakening_intervals(record, analysis)
        if present:
            short_available += 1
            short_counts.append(float(len(intervals)))
            short_minutes.append(sum((end - start) / 60.0 for start, end, _ in intervals))
            short_durations.extend(end - start for start, end, _ in intervals)

    explicit_awakenings: list[float] = []
    explicit_awake_minutes: list[float] = []
    for record in records:
        payload = record.get("payload") or {}
        count = analysis.coerce_number(
            analysis._find_named_value(
                payload, {"awakeningcount", "wakecount", "numberofawakenings", "awakeningscount"}
            )
        )
        minutes = analysis.coerce_number(
            analysis._find_named_value(
                payload, {"minutesawake", "awakeminutes", "minuteswake", "wakeminutes"}
            )
        )
        if count is not None:
            explicit_awakenings.append(count)
        if minutes is not None:
            explicit_awake_minutes.append(minutes)

    recent = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in sessions[-_MAX_RECENT_SLEEP_SESSIONS:]
    ]
    return {
        "sessions": len(records),
        "sessions_with_stages": session_count,
        "stages": stages,
        "sessions_with_stage_timeline": len(sessions),
        "awakenings": {
            "mean_internal_per_session": mean(internal) if internal else mean(explicit_awakenings),
            "mean_awake_minutes_per_session": (
                mean(awake_minutes) if awake_minutes else mean(explicit_awake_minutes)
            ),
            "short_awakenings_sessions_available": short_available,
            "short_awakenings_total": int(sum(short_counts)) if short_available else None,
            "mean_short_awakenings_per_available_session": (
                mean(short_counts) if short_available else None
            ),
            "mean_short_awake_minutes_per_available_session": (
                mean(short_minutes) if short_available else None
            ),
            "mean_short_awakening_duration_seconds": (
                mean(short_durations) if short_durations else (0.0 if short_available else None)
            ),
            "stage_awakenings_definition": (
                "internal AWAKE/WAKE/OUT_OF_BED runs exclude initial/final wake periods"
            ),
            "short_awakenings_definition": (
                "Google Health shortAwakenings are overlapping micro-awakenings, separate "
                "from the primary stage timeline"
            ),
        },
        "efficiency": {
            "mean_percent": mean(efficiencies),
            "sessions_available": len(efficiencies),
        },
        "latency_minutes": {
            "mean_sleep_onset": mean(onset),
            "mean_rem": mean(rem),
            "mean_deep": mean(deep),
        },
        "temporal_architecture": {
            "mean_stage_transitions_per_session": mean(transitions),
            "stage_share_percent_by_night_third": _stage_share_by_third(sessions),
        },
        "recent_session_timelines": recent,
        "data_availability": {
            "stage_timeline_available": bool(sessions),
            "short_awakenings_available": short_available > 0,
            "zero_short_awakenings_is_distinct_from_missing": True,
        },
    }


def _record_interval(record: dict[str, Any], analysis) -> tuple[float | None, float | None]:
    start = analysis.parse_timestamp(record.get("start_time"))
    end = analysis.parse_timestamp(record.get("end_time"))
    if start is None or end is None or end <= start:
        return None, None
    return start, end


def _heart_rate_points(
    records: list[dict[str, Any]], analysis
) -> list[tuple[float, float]]:
    metrics = analysis.available_metrics(records, "heart-rate") if records else []
    if not metrics:
        return []
    metric = "__heart_rate_samples__" if "__heart_rate_samples__" in metrics else metrics[0]
    return [
        (timestamp, value)
        for timestamp, value in analysis.raw_points(records, metric)
        if math.isfinite(timestamp) and math.isfinite(value) and 20 <= value <= 250
    ]


def _sample_summary(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "mean_bpm": _rounded(statistics.fmean(values), 1) if values else None,
        "min_bpm": _rounded(min(values), 1) if values else None,
        "max_bpm": _rounded(max(values), 1) if values else None,
        "p90_bpm": _rounded(_percentile(values, 0.90), 1) if values else None,
    }


def _heart_rate_activity_context(
    heart_records: list[dict[str, Any]],
    exercise_records: list[dict[str, Any]],
    activity_level_records: list[dict[str, Any]],
    analysis,
) -> dict[str, Any] | None:
    points = _heart_rate_points(heart_records, analysis)
    if not points:
        return None
    timestamps = [timestamp for timestamp, _ in points]
    values = [value for _, value in points]

    exercise_windows: list[tuple[float, float, str]] = []
    for record in exercise_records:
        start, end = _record_interval(record, analysis)
        if start is not None and end is not None:
            category = (
                _find_named_string(record.get("payload") or {}, {"exercisetype"})
                or _find_named_string(record.get("payload") or {}, {"activitytype"})
                or "UNSPECIFIED"
            )
            exercise_windows.append((start, end, category.upper()))
    exercise_windows.sort()

    activity_windows: list[tuple[float, float, str]] = []
    for record in activity_level_records:
        start, end = _record_interval(record, analysis)
        if start is not None and end is not None:
            level = (
                _find_named_string(record.get("payload") or {}, {"activitylevel"})
                or "UNSPECIFIED"
            )
            activity_windows.append((start, end, level.upper()))
    activity_windows.sort()

    if not exercise_windows and not activity_windows:
        return None

    def indexes(start: float, end: float) -> range:
        return range(bisect_left(timestamps, start), bisect_left(timestamps, end))

    exercise_indexes: set[int] = set()
    by_type_indexes: dict[str, set[int]] = defaultdict(set)
    recent_sessions: list[dict[str, Any]] = []
    for start, end, category in exercise_windows:
        current = list(indexes(start, end))
        exercise_indexes.update(current)
        by_type_indexes[category].update(current)
        session_values = [values[index] for index in current]
        recent_sessions.append(
            {
                "type": category,
                "start_local": datetime.fromtimestamp(start).astimezone().isoformat(timespec="minutes"),
                "end_local": datetime.fromtimestamp(end).astimezone().isoformat(timespec="minutes"),
                "duration_minutes": _rounded((end - start) / 60.0, 1),
                **_sample_summary(session_values),
            }
        )

    activity_indexes: dict[str, set[int]] = defaultdict(set)
    for start, end, level in activity_windows:
        activity_indexes[level].update(indexes(start, end))

    exercise_values = [values[index] for index in sorted(exercise_indexes)]
    outside_values = [value for index, value in enumerate(values) if index not in exercise_indexes]
    exercise_mean = statistics.fmean(exercise_values) if exercise_values else None
    outside_mean = statistics.fmean(outside_values) if outside_values else None
    threshold = _percentile(values, 0.90)
    high_indexes = {index for index, value in enumerate(values) if value >= threshold}

    return {
        "summary": {
            "heart_rate_samples": len(values),
            "exercise_sessions": len(exercise_windows),
            "exercise_sessions_with_hr_samples": sum(
                1 for session in recent_sessions if session["samples"] > 0
            ),
            "heart_rate_samples_during_exercise": len(exercise_indexes),
            "heart_rate_samples_during_exercise_percent": _rounded(
                len(exercise_indexes) / len(values) * 100.0, 1
            ),
            "mean_bpm_during_exercise": _rounded(exercise_mean, 1),
            "mean_bpm_outside_exercise": _rounded(outside_mean, 1),
            "mean_difference_bpm_exercise_vs_outside": (
                _rounded(exercise_mean - outside_mean, 1)
                if exercise_mean is not None and outside_mean is not None
                else None
            ),
            "high_hr_threshold_p90_bpm": _rounded(threshold, 1),
            "high_hr_samples_during_exercise_percent": (
                _rounded(len(high_indexes & exercise_indexes) / len(high_indexes) * 100.0, 1)
                if high_indexes
                else None
            ),
        },
        "by_exercise_type": {
            category: _sample_summary([values[index] for index in sorted(current)])
            for category, current in sorted(by_type_indexes.items())
            if current
        },
        "by_activity_level": {
            level: _sample_summary([values[index] for index in sorted(current)])
            for level, current in sorted(activity_indexes.items())
            if current
        },
        "recent_exercise_sessions": sorted(
            recent_sessions, key=lambda item: item["end_local"]
        )[-_MAX_RECENT_EXERCISE_SESSIONS:],
        "caveat": (
            "Heart-rate samples are linked by temporal overlap with recorded exercise/activity "
            "windows. This contextualizes elevations but does not by itself prove causation."
        ),
    }


def _attach_activity_context(
    snapshot: dict[str, Any],
    store,
    start: str,
    end: str,
    record_limit: int,
    analysis,
) -> None:
    heart_records = store.list_records("heart-rate", start, end, limit=record_limit, newest=True)
    if not heart_records:
        return
    context = _heart_rate_activity_context(
        heart_records,
        store.list_records("exercise", start, end, limit=record_limit, newest=True),
        store.list_records("activity-level", start, end, limit=record_limit, newest=True),
        analysis,
    )
    if not context:
        return
    for metric in snapshot.get("metrics", []):
        if not isinstance(metric, dict):
            continue
        if metric.get("data_type") not in {"heart-rate", "exercise", "activity-level"}:
            continue
        structured = metric.get("structured_details")
        if not isinstance(structured, dict):
            structured = {}
            metric["structured_details"] = structured
        structured["exercise_heart_rate_context"] = context


def install_deterministic_context_patch(analysis) -> None:
    """Fix Google Health sleep granularity and add HR/activity temporal context."""
    if getattr(analysis, "_DETERMINISTIC_CONTEXT_PATCH_INSTALLED", False):
        return

    original_duration = analysis.duration_hours
    original_build_health_snapshot = analysis.build_health_snapshot

    analysis._sleep_stage_totals = lambda record: _sleep_stage_totals(record, analysis)
    analysis.sleep_stage_points = lambda records: _sleep_stage_points(records, analysis)
    analysis.duration_hours = lambda record: _duration_hours(record, analysis, original_duration)
    analysis._sleep_ai_details = lambda records: _sleep_ai_details(records, analysis)

    def enhanced_build_health_snapshot(
        store,
        start: str,
        end: str,
        *,
        now: datetime | None = None,
        record_limit: int = 30_000,
    ) -> dict[str, Any]:
        snapshot = original_build_health_snapshot(
            store, start, end, now=now, record_limit=record_limit
        )
        _attach_activity_context(snapshot, store, start, end, record_limit, analysis)
        return snapshot

    analysis.build_health_snapshot = enhanced_build_health_snapshot
    analysis._DETERMINISTIC_CONTEXT_PATCH_INSTALLED = True
