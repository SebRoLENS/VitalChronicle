from __future__ import annotations

import math
import statistics
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
    "UNKNOWN",
)
_MAX_DISTRIBUTIONS = 3
_MAX_RECENT_SLEEP_SESSIONS = 3
_MAX_TIMELINE_RUNS = 48


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _stage_intervals(record: dict[str, Any], analysis) -> list[tuple[float, float, str]]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return []
    intervals: list[tuple[float, float, str]] = []
    for stage in analysis._find_named_list(payload, "stages"):
        start = analysis.parse_timestamp(
            analysis._find_named_value(stage, {"starttime", "physicaltime"})
        )
        end = analysis.parse_timestamp(analysis._find_named_value(stage, {"endtime"}))
        if start is None or end is None or end <= start:
            continue
        intervals.append((start, end, _normalize_stage(stage.get("type"))))
    intervals.sort(key=lambda item: (item[0], item[1]))
    return intervals


def _stage_runs(intervals: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Collapse adjacent fragments of the same stage without inventing missing time."""
    runs: list[tuple[float, float, str]] = []
    for start, end, stage in intervals:
        if runs and runs[-1][2] == stage and start <= runs[-1][1] + 1.0:
            previous_start, previous_end, previous_stage = runs[-1]
            runs[-1] = (previous_start, max(previous_end, end), previous_stage)
        else:
            runs.append((start, end, stage))
    return runs


def _record_bounds(
    record: dict[str, Any],
    runs: list[tuple[float, float, str]],
    analysis,
) -> tuple[float | None, float | None]:
    start = analysis.parse_timestamp(record.get("start_time"))
    end = analysis.parse_timestamp(record.get("end_time"))
    if start is None and runs:
        start = runs[0][0]
    if end is None and runs:
        end = runs[-1][1]
    if start is not None and end is not None and end <= start:
        return None, None
    return start, end


def _explicit_numeric(payload: Any, names: set[str], analysis) -> float | None:
    value = analysis._find_named_value(payload, names)
    return analysis.coerce_number(value)


def _sleep_session_details(record: dict[str, Any], analysis) -> dict[str, Any] | None:
    intervals = _stage_intervals(record, analysis)
    runs = _stage_runs(intervals)
    if not runs:
        return None

    start, end = _record_bounds(record, runs, analysis)
    first_sleep_index = next(
        (index for index, (_s, _e, stage) in enumerate(runs) if stage not in _WAKE_STAGES),
        None,
    )
    first_sleep = runs[first_sleep_index][0] if first_sleep_index is not None else None

    wake_indices = [index for index, (_s, _e, stage) in enumerate(runs) if stage in _WAKE_STAGES]
    wake_minutes = sum((runs[index][1] - runs[index][0]) / 60.0 for index in wake_indices)
    internal_awakenings = sum(
        1
        for index in wake_indices
        if any(stage not in _WAKE_STAGES for _s, _e, stage in runs[:index])
        and any(stage not in _WAKE_STAGES for _s, _e, stage in runs[index + 1 :])
    )
    transitions = sum(1 for left, right in pairwise(runs) if left[2] != right[2])

    def latency(stage_name: str) -> float | None:
        if first_sleep is None:
            return None
        candidate = next(
            (s for s, _e, stage in runs if stage == stage_name and s >= first_sleep),
            None,
        )
        return None if candidate is None else max(0.0, (candidate - first_sleep) / 60.0)

    onset_latency = (
        max(0.0, (first_sleep - start) / 60.0)
        if first_sleep is not None and start is not None
        else None
    )
    rem_latency = latency("REM")
    deep_latency = latency("DEEP")

    sleep_hours = analysis.duration_hours(record)
    interval_hours = (
        (end - start) / 3600.0 if start is not None and end is not None and end > start else None
    )
    efficiency = None
    if sleep_hours is not None and interval_hours is not None and interval_hours > 0:
        efficiency = min(100.0, max(0.0, sleep_hours / interval_hours * 100.0))

    anchor = start if start is not None else runs[0][0]
    encoded = []
    for run_start, run_end, stage in runs[:_MAX_TIMELINE_RUNS]:
        offset_start = max(0, round((run_start - anchor) / 60.0))
        offset_end = max(offset_start, round((run_end - anchor) / 60.0))
        encoded.append(f"{offset_start}-{offset_end}m:{stage}")
    if len(runs) > _MAX_TIMELINE_RUNS:
        encoded.append(f"+{len(runs) - _MAX_TIMELINE_RUNS}_more")

    reference = end if end is not None else runs[-1][1]
    return {
        "date": datetime.fromtimestamp(reference).astimezone().date().isoformat(),
        "start_local": datetime.fromtimestamp(anchor).astimezone().isoformat(timespec="minutes"),
        "end_local": datetime.fromtimestamp(reference).astimezone().isoformat(timespec="minutes"),
        "total_sleep_hours": _rounded(sleep_hours, 2),
        "sleep_efficiency_percent": _rounded(efficiency, 1),
        "internal_awakenings": internal_awakenings,
        "awake_minutes": _rounded(wake_minutes, 1),
        "stage_transitions": transitions,
        "latencies_minutes": {
            "sleep_onset": _rounded(onset_latency, 1),
            "rem": _rounded(rem_latency, 1),
            "deep": _rounded(deep_latency, 1),
        },
        "stage_sequence": " | ".join(encoded),
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
        runs = session["_runs"]
        start, end = session["_bounds"]
        if start is None:
            start = runs[0][0]
        if end is None:
            end = runs[-1][1]
        if end <= start:
            continue
        width = (end - start) / 3.0
        boundaries = (start, start + width, start + 2.0 * width, end)
        for run_start, run_end, stage in runs:
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
        if total_minutes <= 0:
            continue
        result[name] = {
            stage: round(minutes / total_minutes * 100.0, 1)
            for stage, minutes in sorted(totals[name].items())
        }
    return result


def _enhanced_sleep_details(records: list[dict[str, Any]], analysis) -> dict[str, Any]:
    stage_sessions = analysis.sleep_stage_points(records)
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

    architecture_sessions = [
        details
        for record in records
        if (details := _sleep_session_details(record, analysis)) is not None
    ]
    architecture_sessions.sort(key=lambda item: item["end_local"])

    awakenings = [float(item["internal_awakenings"]) for item in architecture_sessions]
    awake_minutes = [
        float(item["awake_minutes"])
        for item in architecture_sessions
        if item["awake_minutes"] is not None
    ]
    efficiencies = [
        float(item["sleep_efficiency_percent"])
        for item in architecture_sessions
        if item["sleep_efficiency_percent"] is not None
    ]
    transitions = [float(item["stage_transitions"]) for item in architecture_sessions]
    rem_latencies = [
        float(item["latencies_minutes"]["rem"])
        for item in architecture_sessions
        if item["latencies_minutes"]["rem"] is not None
    ]
    deep_latencies = [
        float(item["latencies_minutes"]["deep"])
        for item in architecture_sessions
        if item["latencies_minutes"]["deep"] is not None
    ]
    onset_latencies = [
        float(item["latencies_minutes"]["sleep_onset"])
        for item in architecture_sessions
        if item["latencies_minutes"]["sleep_onset"] is not None
    ]

    # If raw stage intervals are unavailable, keep explicit summary fields useful.
    explicit_awakenings = []
    explicit_awake_minutes = []
    for record in records:
        payload = record.get("payload") or {}
        count = _explicit_numeric(
            payload,
            {"awakeningcount", "wakecount", "numberofawakenings", "awakeningscount"},
            analysis,
        )
        if count is not None:
            explicit_awakenings.append(count)
        minutes = _explicit_numeric(
            payload,
            {"minutesawake", "awakeminutes", "minuteswake", "wakeminutes"},
            analysis,
        )
        if minutes is not None:
            explicit_awake_minutes.append(minutes)

    def mean(values: list[float]) -> float | None:
        return _rounded(statistics.fmean(values), 1) if values else None

    recent = []
    for item in architecture_sessions[-_MAX_RECENT_SLEEP_SESSIONS:]:
        recent.append({key: value for key, value in item.items() if not key.startswith("_")})

    return {
        "sessions": len(records),
        "sessions_with_stages": session_count,
        "stages": stages,
        "sessions_with_stage_timeline": len(architecture_sessions),
        "awakenings": {
            "mean_internal_per_session": (
                mean(awakenings) if awakenings else mean(explicit_awakenings)
            ),
            "mean_awake_minutes_per_session": (
                mean(awake_minutes) if awake_minutes else mean(explicit_awake_minutes)
            ),
            "definition": (
                "internal wake episodes exclude the final wake-up when raw stage intervals "
                "are available"
            ),
        },
        "efficiency": {
            "mean_percent": mean(efficiencies),
            "sessions_available": len(efficiencies),
        },
        "latency_minutes": {
            "mean_sleep_onset": mean(onset_latencies),
            "mean_rem": mean(rem_latencies),
            "mean_deep": mean(deep_latencies),
        },
        "temporal_architecture": {
            "mean_stage_transitions_per_session": mean(transitions),
            "stage_share_percent_by_night_third": _stage_share_by_third(architecture_sessions),
        },
        "recent_session_timelines": recent,
    }


def _numeric_distributions(
    data_type: str, records: list[dict[str, Any]], analysis
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for metric in analysis.available_metrics(records, data_type)[:_MAX_DISTRIBUTIONS]:
        profile = analysis.visual_profile(data_type, metric)
        points = analysis.display_points(analysis.raw_points(records, metric), profile)
        values: list[float] = []
        for _timestamp, value in points:
            number = _finite(value)
            if number is not None:
                values.append(number)
        if len(values) < 2:
            continue
        mean = statistics.fmean(values)
        std = statistics.pstdev(values) if len(values) > 1 else 0.0
        p25 = _percentile(values, 0.25)
        p75 = _percentile(values, 0.75)
        result.append(
            {
                "metric": metric,
                "unit": profile.unit,
                "samples": len(values),
                "p10": _rounded(_percentile(values, 0.10), 3),
                "p25": _rounded(p25, 3),
                "p75": _rounded(p75, 3),
                "p90": _rounded(_percentile(values, 0.90), 3),
                "interquartile_range": _rounded(p75 - p25, 3),
                "standard_deviation": _rounded(std, 3),
                "coefficient_of_variation_percent": (
                    _rounded(std / abs(mean) * 100.0, 1) if abs(mean) > 1e-12 else None
                ),
            }
        )
    return result


def install_deterministic_detail_core(analysis) -> None:
    """Enrich deterministic evidence while keeping rich details optional for the LLM.

    ``analysis.py`` still owns the canonical snapshot. This installer only extends
    its structured deterministic details, which the existing compact/adaptive AI
    pipeline already keeps for a specifically requested metric and trims first when
    a broad request would exceed the token budget.
    """
    if getattr(analysis, "_DETERMINISTIC_DETAIL_CORE_INSTALLED", False):
        return

    original_structured = analysis._structured_ai_details

    def enhanced_sleep(records: list[dict[str, Any]]) -> dict[str, Any]:
        return _enhanced_sleep_details(records, analysis)

    def enhanced_structured(
        data_type: str, records: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        details = original_structured(data_type, records) or {}
        if data_type != "sleep":
            distributions = _numeric_distributions(data_type, records, analysis)
            if distributions:
                details = {"distributions": distributions, **details}
        return details or None

    analysis._sleep_ai_details = enhanced_sleep
    analysis._structured_ai_details = enhanced_structured
    analysis._DETERMINISTIC_DETAIL_CORE_INSTALLED = True
