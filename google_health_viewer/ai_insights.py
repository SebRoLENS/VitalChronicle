"""Deterministic health-data preparation for the local language model.

The language model should explain evidence, not discover basic statistics from a
large dump of records.  This module turns the compact snapshot produced by
``analysis.py`` into matched comparisons, personal baselines, robust anomalies,
data-quality notes, and cautiously worded cross-metric associations.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from .analysis import (
    INTRADAY_CUMULATIVE_TYPES,
    available_metrics,
    build_health_snapshot,
    categorical_daily_points,
    display_points,
    duration_hours,
    raw_points,
    sleep_stage_points,
    visual_profile,
)
from .constants import DATA_TYPE_BY_KEY
from .utils import flatten_dict, parse_timestamp

DAILY_EXPECTED_TYPES = {
    "active-energy-burned",
    "active-minutes",
    "active-zone-minutes",
    "daily-heart-rate-variability",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-resting-heart-rate",
    "daily-sleep-temperature-derivations",
    "daily-vo2-max",
    "distance",
    "floors",
    "heart-rate",
    "sleep",
    "steps",
    "total-calories",
}

# Google emits these records on dated endpoints, but their values describe a
# reference/configuration rather than a physiological observation.  They remain
# useful context for the model, but must never make a requested interval look
# measured or feed longitudinal health trends and associations.
REFERENCE_CONFIGURATION_TYPES = {
    "daily-heart-rate-zones",
}

VITAL_TYPES = {
    "daily-heart-rate-variability",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-resting-heart-rate",
    "daily-sleep-temperature-derivations",
    "heart-rate",
    "heart-rate-variability",
    "oxygen-saturation",
    "respiratory-rate-sleep-summary",
}


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _daily_values(
    records: list[dict[str, Any]], data_type: str
) -> tuple[dict[date, float], str, str] | None:
    available = available_metrics(records, data_type)
    if not available:
        return None
    metric = available[0]
    profile = visual_profile(data_type, metric)
    if data_type == "sleep":
        points: list[tuple[float, float]] = []
        for record in records:
            timestamp = parse_timestamp(record.get("end_time") or record.get("start_time"))
            value = duration_hours(record)
            if timestamp is not None and value is not None:
                points.append((timestamp, value))
        aggregation = "mean"
    else:
        points = display_points(raw_points(records, metric), profile)
        aggregation = "sum" if profile.aggregation == "sum" else "mean"

    grouped: dict[date, list[float]] = defaultdict(list)
    for timestamp, value in points:
        if math.isfinite(value):
            grouped[datetime.fromtimestamp(timestamp).date()].append(float(value))  # noqa: DTZ006
    daily = {
        day: sum(values) if aggregation == "sum" else statistics.fmean(values)
        for day, values in grouped.items()
        if values
    }
    return daily, profile.unit, aggregation


def _window(values: dict[date, float], end_day: date, days: int) -> list[tuple[date, float]]:
    start_day = end_day - timedelta(days=days - 1)
    return sorted((day, value) for day, value in values.items() if start_day <= day <= end_day)


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _matched_period_comparison(
    values: dict[date, float], end_day: date, window_days: int = 7
) -> dict[str, Any] | None:
    recent = _window(values, end_day, window_days)
    previous = _window(values, end_day - timedelta(days=window_days), window_days)
    recent_values = [value for _day, value in recent]
    previous_values = [value for _day, value in previous]
    if len(recent_values) < 3 or len(previous_values) < 3:
        return None
    recent_mean = statistics.fmean(recent_values)
    previous_mean = statistics.fmean(previous_values)
    delta = recent_mean - previous_mean
    delta_percent = delta / abs(previous_mean) * 100.0 if abs(previous_mean) > 1e-12 else None
    combined = recent_values + previous_values
    pooled_std = statistics.pstdev(combined) if len(combined) > 1 else 0.0
    return {
        "window_days": window_days,
        "recent_start": recent[0][0].isoformat(),
        "recent_end": recent[-1][0].isoformat(),
        "recent_days": len(recent_values),
        "recent_mean": _round(recent_mean),
        "previous_start": previous[0][0].isoformat(),
        "previous_end": previous[-1][0].isoformat(),
        "previous_days": len(previous_values),
        "previous_mean": _round(previous_mean),
        "absolute_change": _round(delta),
        "percent_change": _round(delta_percent, 1),
        "standardized_change": _round(delta / pooled_std, 2) if pooled_std > 1e-12 else None,
        "comparison_is_matched": True,
    }


def _trend(values: dict[date, float], end_day: date, days: int = 28) -> dict[str, Any] | None:
    recent = _window(values, end_day, days)
    if len(recent) < 5:
        return None
    origin = recent[0][0]
    xs = [float((day - origin).days) for day, _value in recent]
    ys = [value for _day, value in recent]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator <= 0:
        return None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    fitted = [y_mean + slope * (x - x_mean) for x in xs]
    total = sum((y - y_mean) ** 2 for y in ys)
    residual = sum((y - fit) ** 2 for y, fit in zip(ys, fitted))
    r_squared = 1.0 - residual / total if total > 1e-12 else 0.0
    weekly_percent = slope * 7.0 / abs(y_mean) * 100.0 if abs(y_mean) > 1e-12 else None
    noise = statistics.pstdev(ys) if len(ys) > 1 else 0.0
    meaningful = abs(slope * 7.0) >= max(noise * 0.25, abs(y_mean) * 0.02)
    direction = "stable"
    if meaningful and slope > 0:
        direction = "upward"
    elif meaningful and slope < 0:
        direction = "downward"
    return {
        "window_days": days,
        "observed_days": len(recent),
        "direction": direction,
        "slope_per_week": _round(slope * 7.0),
        "percent_per_week": _round(weekly_percent, 1),
        "r_squared": _round(max(0.0, r_squared), 3),
    }


def _robust_anomalies(
    values: dict[date, float], end_day: date, days: int = 90
) -> dict[str, Any] | None:
    recent = _window(values, end_day, days)
    samples = [value for _day, value in recent]
    if len(samples) < 7:
        return None
    centre = statistics.median(samples)
    deviations = [abs(value - centre) for value in samples]
    mad = statistics.median(deviations)
    scale = 1.4826 * mad
    if scale <= 1e-12:
        scale = statistics.pstdev(samples)
    if scale <= 1e-12:
        return {
            "method": "median_and_MAD",
            "window_days": days,
            "baseline_samples": len(samples),
            "anomalies": [],
        }
    anomalies = [
        {
            "date": day.isoformat(),
            "value": _round(value),
            "robust_z": _round((value - centre) / scale, 2),
        }
        for day, value in recent
        if abs((value - centre) / scale) >= 2.5
    ]
    anomalies.sort(key=lambda item: abs(float(item["robust_z"])), reverse=True)
    latest_day, latest_value = recent[-1]
    return {
        "method": "median_and_MAD",
        "window_days": days,
        "baseline_samples": len(samples),
        "baseline_median": _round(centre),
        "robust_scale": _round(scale),
        "latest_date": latest_day.isoformat(),
        "latest_robust_z": _round((latest_value - centre) / scale, 2),
        "anomalies": anomalies[:5],
    }


def _day_of_week_pattern(values: dict[date, float]) -> dict[str, Any] | None:
    grouped: dict[int, list[float]] = defaultdict(list)
    for day, value in values.items():
        grouped[day.weekday()].append(value)
    means = {
        index: statistics.fmean(samples) for index, samples in grouped.items() if len(samples) >= 2
    }
    if len(means) < 4:
        return None
    names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    high = max(means, key=means.get)
    low = min(means, key=means.get)
    return {
        "means": {
            names[index]: {"mean": _round(value), "days": len(grouped[index])}
            for index, value in sorted(means.items())
        },
        "highest_mean_day": names[high],
        "lowest_mean_day": names[low],
    }


def _longest_gap(days: list[date]) -> int:
    if len(days) < 2:
        return 0
    return max(max(0, (right - left).days - 1) for left, right in pairwise(days))


def _metric_features(
    values: dict[date, float],
    data_type: str,
    start_day: date,
    end_day: date,
    today: date,
) -> dict[str, Any]:
    complete_end = min(end_day, today - timedelta(days=1))
    completed_values = {
        day: value
        for day, value in values.items()
        if day <= complete_end or data_type not in INTRADAY_CUMULATIVE_TYPES
    }
    analysis_end = max(completed_values, default=complete_end)
    ordered = sorted(completed_values)
    all_values = [completed_values[day] for day in ordered]
    expected_days = max(0, (complete_end - start_day).days + 1)
    coverage = None
    if data_type in DAILY_EXPECTED_TYPES and expected_days:
        coverage = min(
            100.0,
            len([day for day in ordered if start_day <= day <= complete_end])
            / expected_days
            * 100.0,
        )
    baselines: dict[str, Any] = {}
    for window_days in (7, 28, 90):
        window = _window(completed_values, analysis_end, window_days)
        samples = [value for _day, value in window]
        if samples:
            baselines[f"{window_days}_days"] = {
                "observed_days": len(samples),
                "mean": _round(_mean(samples)),
                "median": _round(_median(samples)),
                "standard_deviation": _round(
                    statistics.pstdev(samples) if len(samples) > 1 else 0.0
                ),
                "minimum": _round(min(samples)),
                "maximum": _round(max(samples)),
            }
    result: dict[str, Any] = {
        "personal_baselines": baselines,
        "matched_recent_comparison": _matched_period_comparison(completed_values, analysis_end),
        "trend": _trend(completed_values, analysis_end),
        "robust_anomaly_check": _robust_anomalies(completed_values, analysis_end),
        "weekly_pattern": _day_of_week_pattern(completed_values),
        "data_quality": {
            "observed_days": len(ordered),
            "expected_daily_samples": data_type in DAILY_EXPECTED_TYPES,
            "expected_days_in_period": expected_days if data_type in DAILY_EXPECTED_TYPES else None,
            "coverage_percent": _round(coverage, 1),
            "first_observation": ordered[0].isoformat() if ordered else None,
            "last_observation": ordered[-1].isoformat() if ordered else None,
            "days_since_last_observation": (today - ordered[-1]).days if ordered else None,
            "longest_internal_gap_days": _longest_gap(ordered),
        },
    }
    if all_values:
        mean = statistics.fmean(all_values)
        std = statistics.pstdev(all_values) if len(all_values) > 1 else 0.0
        result["variability"] = {
            "standard_deviation": _round(std),
            "coefficient_of_variation_percent": _round(std / abs(mean) * 100.0, 1)
            if abs(mean) > 1e-12
            else None,
            "interquartile_range": _round(_interquartile_range(all_values)),
        }
    return result


def _interquartile_range(values: list[float]) -> float:
    ordered = sorted(values)
    if len(ordered) < 4:
        return max(ordered) - min(ordered) if ordered else 0.0
    midpoint = len(ordered) // 2
    lower = ordered[:midpoint]
    upper = ordered[midpoint + (len(ordered) % 2) :]
    return statistics.median(upper) - statistics.median(lower)


def _record_day(record: dict[str, Any]) -> date | None:
    timestamp = parse_timestamp(record.get("end_time") or record.get("start_time"))
    return datetime.fromtimestamp(timestamp).date() if timestamp is not None else None  # noqa: DTZ006


def _exercise_type(record: dict[str, Any]) -> str:
    for key, value in flatten_dict(record.get("payload") or {}).items():
        if key.rsplit(".", 1)[-1].lower() in {"exercisetype", "activitytype"} and value:
            return str(value).upper()
    return "UNSPECIFIED"


def _exercise_period(records: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    sessions: dict[str, int] = defaultdict(int)
    hours: dict[str, float] = defaultdict(float)
    for record in records:
        day = _record_day(record)
        if day is None or not start <= day <= end:
            continue
        category = _exercise_type(record)
        sessions[category] += 1
        duration = duration_hours(record)
        if duration is not None:
            hours[category] += duration
    return {
        category: {"sessions": count, "hours": _round(hours.get(category, 0.0), 2)}
        for category, count in sorted(sessions.items())
    }


def _sleep_stage_period(records: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    totals: dict[str, float] = defaultdict(float)
    sessions = 0
    for timestamp, stages in sleep_stage_points(records):
        day = datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
        if not start <= day <= end:
            continue
        sessions += 1
        for stage, hours in stages.items():
            totals[stage] += hours
    total = sum(totals.values())
    return {
        "sessions": sessions,
        "stages": {
            stage: {
                "mean_hours": _round(hours / sessions, 2) if sessions else None,
                "share_percent": _round(hours / total * 100.0, 1) if total else None,
            }
            for stage, hours in sorted(totals.items())
        },
    }


def _structured_period_comparison(
    data_type: str, records: list[dict[str, Any]], end_day: date
) -> dict[str, Any] | None:
    if data_type == "exercise":
        days = 28
        recent_start = end_day - timedelta(days=days - 1)
        previous_end = recent_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        return {
            "window_days": days,
            "recent": _exercise_period(records, recent_start, end_day),
            "previous": _exercise_period(records, previous_start, previous_end),
        }
    if data_type == "sleep":
        days = 7
        recent_start = end_day - timedelta(days=days - 1)
        previous_end = recent_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        return {
            "window_days": days,
            "recent": _sleep_stage_period(records, recent_start, end_day),
            "previous": _sleep_stage_period(records, previous_start, previous_end),
        }
    if data_type in {"time-in-heart-rate-zone", "calories-in-heart-rate-zone", "active-minutes"}:
        points = categorical_daily_points(records, data_type)
        recent_start = end_day - timedelta(days=6)
        totals: dict[str, float] = defaultdict(float)
        for timestamp, categories in points:
            day = datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
            if recent_start <= day <= end_day:
                for category, value in categories.items():
                    totals[category] += value
        return {
            "window_days": 7,
            "recent_category_totals": {key: _round(value) for key, value in sorted(totals.items())},
        }
    return None


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 10:
        return None
    left = [pair[0] for pair in pairs]
    right = [pair[1] for pair in pairs]
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left) * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator > 1e-12 else None


def _associations(
    daily_by_type: dict[str, dict[date, float]], labels: dict[str, str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    data_types = sorted(daily_by_type)
    for index, left_type in enumerate(data_types):
        left = daily_by_type[left_type]
        for right_type in data_types[index + 1 :]:
            right = daily_by_type[right_type]
            shared = sorted(set(left) & set(right))
            same_pairs = [(left[day], right[day]) for day in shared]
            same_r = _pearson(same_pairs)
            if same_r is not None and abs(same_r) >= 0.4:
                result.append(
                    {
                        "left_data_type": left_type,
                        "left": labels[left_type],
                        "right_data_type": right_type,
                        "right": labels[right_type],
                        "timing": "same_day",
                        "r": round(same_r, 3),
                        "paired_days": len(same_pairs),
                        "reliability_score": round(
                            abs(same_r) * min(1.0, len(same_pairs) / 30.0), 3
                        ),
                        "interpretation_limit": "association_only_not_causation",
                    }
                )
            lag_pairs = [
                (value, right[day + timedelta(days=1)])
                for day, value in left.items()
                if day + timedelta(days=1) in right
            ]
            lag_r = _pearson(lag_pairs)
            if lag_r is not None and abs(lag_r) >= 0.45:
                result.append(
                    {
                        "left_data_type": left_type,
                        "left": labels[left_type],
                        "right_data_type": right_type,
                        "right": labels[right_type],
                        "timing": "left_precedes_right_by_one_day",
                        "r": round(lag_r, 3),
                        "paired_days": len(lag_pairs),
                        "reliability_score": round(abs(lag_r) * min(1.0, len(lag_pairs) / 30.0), 3),
                        "interpretation_limit": "exploratory_lagged_association_not_prediction_or_causation",
                    }
                )
    result.sort(key=lambda item: item["reliability_score"], reverse=True)
    return result[:12]


def _requested_interval_coverage(
    snapshot: dict[str, Any],
    observed_dates_by_type: dict[str, set[date]],
    start_day: date,
    end_day: date,
) -> dict[str, Any]:
    requested_days = max(0, (end_day - start_day).days + 1)
    measurement_days = sorted(
        {
            day
            for data_type, days in observed_dates_by_type.items()
            if data_type not in REFERENCE_CONFIGURATION_TYPES
            for day in days
            if start_day <= day <= end_day
        }
    )
    first_measurement = measurement_days[0] if measurement_days else None
    last_measurement = measurement_days[-1] if measurement_days else None
    days_with_measurements = len(measurement_days)
    measurement_coverage = (
        days_with_measurements / requested_days * 100.0 if requested_days else None
    )

    metric_rows = []
    limited_daily_metrics = []
    reference_configuration_metrics = []
    for metric in snapshot.get("metrics", []):
        data_type = str(metric.get("data_type", ""))
        dates = sorted(observed_dates_by_type.get(data_type, set()))
        is_reference = data_type in REFERENCE_CONFIGURATION_TYPES
        expected_daily = data_type in DAILY_EXPECTED_TYPES
        coverage = len(dates) / requested_days * 100.0 if expected_daily and requested_days else None
        if is_reference:
            expected_frequency = "reference_or_configuration"
        elif expected_daily:
            expected_frequency = "daily_or_more"
        else:
            expected_frequency = "event_or_irregular"
        row = {
            "data_type": data_type,
            "label": str(metric.get("label", data_type)),
            "data_role": "reference_configuration" if is_reference else "measurement",
            "expected_frequency": expected_frequency,
            "observed_calendar_days": len(dates),
            "requested_calendar_days": requested_days,
            "coverage_percent": _round(coverage, 1),
            "first_observation": dates[0].isoformat() if dates else None,
            "last_observation": dates[-1].isoformat() if dates else None,
            "records_considered": int(metric.get("records_considered", 0) or 0),
            "excluded_from_measurement_coverage": is_reference,
        }
        metric_rows.append(row)
        if is_reference:
            reference_configuration_metrics.append(
                {
                    "data_type": data_type,
                    "label": row["label"],
                    "records_considered": row["records_considered"],
                    "dated_records": len(dates),
                    "interpretation": (
                        "Reference thresholds/settings, not physiological measurements. Their "
                        "dates do not establish health-data coverage."
                    ),
                }
            )
        if expected_daily and coverage is not None and coverage < 80.0:
            limited_daily_metrics.append(
                {
                    "data_type": data_type,
                    "label": row["label"],
                    "coverage_percent": row["coverage_percent"],
                    "observed_calendar_days": len(dates),
                }
            )

    starts_late = first_measurement is None or first_measurement > start_day
    ends_early = last_measurement is None or last_measurement < end_day
    partial = bool(starts_late or ends_early or limited_daily_metrics)
    reference_note = (
        " Reference/configuration records such as personal heart-rate-zone thresholds are "
        "excluded because they are not health measurements."
        if reference_configuration_metrics
        else ""
    )
    if not measurement_days:
        notice = (
            f"The requested interval is {start_day.isoformat()} to {end_day.isoformat()} "
            f"({requested_days} calendar days), but no supported health-measurement dates are "
            f"available. Do not describe the requested interval as analysed.{reference_note}"
        )
    elif partial:
        notice = (
            f"The requested interval is {start_day.isoformat()} to {end_day.isoformat()} "
            f"({requested_days} calendar days), but actual health measurements occur on "
            f"{days_with_measurements} calendar days from {first_measurement.isoformat()} to "
            f"{last_measurement.isoformat()}. Daily metrics are incompletely covered; use each "
            "metric's own observed-day count and never infer coverage from another metric. "
            "Explicitly limit every conclusion to the dates and metrics actually observed; do "
            f"not imply complete coverage of the requested interval.{reference_note}"
        )
    else:
        notice = (
            f"The requested interval is {start_day.isoformat()} to {end_day.isoformat()} "
            f"({requested_days} calendar days). Health measurements span the complete requested "
            f"interval, although individual metrics may still be intermittent.{reference_note}"
        )

    return {
        "requested_start": start_day.isoformat(),
        "requested_end": end_day.isoformat(),
        "requested_calendar_days": requested_days,
        "first_measurement_date": (
            first_measurement.isoformat() if first_measurement else None
        ),
        "last_measurement_date": last_measurement.isoformat() if last_measurement else None,
        # Compatibility aliases used by existing saved conversations and UI code.
        "first_observed_date": first_measurement.isoformat() if first_measurement else None,
        "last_observed_date": last_measurement.isoformat() if last_measurement else None,
        "observed_span_days": (
            (last_measurement - first_measurement).days + 1
            if first_measurement is not None and last_measurement is not None
            else 0
        ),
        "calendar_days_with_measurements": days_with_measurements,
        "calendar_days_with_measurements_percent": _round(measurement_coverage, 1),
        # Compatibility aliases; their semantics are now measurement-only.
        "calendar_days_with_any_data": days_with_measurements,
        "calendar_days_with_any_data_percent": _round(measurement_coverage, 1),
        "starts_after_requested_start": starts_late,
        "ends_before_requested_end": ends_early,
        "scope_is_partially_observed": partial,
        "limited_daily_metrics": limited_daily_metrics,
        "reference_configuration_metrics": reference_configuration_metrics,
        "metrics": metric_rows,
        "coverage_notice": notice,
        "response_rule": (
            "State the requested interval and the actual measurement coverage near the start "
            "of the answer whenever scope_is_partially_observed is true. Use each metric's own "
            "observed-day count; one well-covered metric cannot establish coverage for another. "
            "Reference/configuration records do not count as measurements. Never describe "
            "missing days as analysed and never treat absence as zero."
        ),
    }


def _candidate_insights(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    interval_coverage = snapshot.get("requested_interval_coverage") or {}
    if interval_coverage.get("scope_is_partially_observed"):
        candidates.append(
            {
                "evidence_id": "quality:requested-interval",
                "kind": "requested_interval_data_limit",
                "data_types": [],
                "headline": "The requested interval is only partially represented in local data",
                "evidence": interval_coverage,
                "relevance_score": 100.0,
                "confidence": "high",
                "caveat": interval_coverage.get("coverage_notice", ""),
            }
        )
    for metric in snapshot.get("metrics", []):
        data_type = str(metric.get("data_type", ""))
        label = str(metric.get("label", data_type))
        unit = str(metric.get("unit", ""))
        features = metric.get("derived_evidence") or {}
        comparison = features.get("matched_recent_comparison") or {}
        change = comparison.get("percent_change")
        standardized = comparison.get("standardized_change")
        if isinstance(change, (int, float)) and abs(change) >= 5:
            score = min(100.0, abs(float(change)) * 1.2 + abs(float(standardized or 0)) * 12)
            candidates.append(
                {
                    "evidence_id": f"change:{data_type}",
                    "kind": "matched_period_change",
                    "data_types": [data_type],
                    "headline": f"{label}: recent matched-period mean is {'higher' if change > 0 else 'lower'}",
                    "evidence": {"unit": unit, **comparison},
                    "relevance_score": round(score, 1),
                    "confidence": "moderate"
                    if comparison.get("recent_days", 0) >= 5
                    and comparison.get("previous_days", 0) >= 5
                    else "low",
                    "caveat": "Higher or lower does not automatically mean better or worse.",
                }
            )
        anomaly = features.get("robust_anomaly_check") or {}
        latest_z = anomaly.get("latest_robust_z")
        if isinstance(latest_z, (int, float)) and abs(latest_z) >= 2.0:
            candidates.append(
                {
                    "evidence_id": f"anomaly:{data_type}",
                    "kind": "personal_baseline_deviation",
                    "data_types": [data_type],
                    "headline": f"{label}: latest complete observation is unusual for the personal baseline",
                    "evidence": {"unit": unit, **anomaly},
                    "relevance_score": min(100.0, round(abs(float(latest_z)) * 22, 1)),
                    "confidence": "moderate" if anomaly.get("baseline_samples", 0) >= 14 else "low",
                    "caveat": "Check sensor quality and repeated measurements before interpreting an isolated value.",
                }
            )
        trend = features.get("trend") or {}
        percent = trend.get("percent_per_week")
        r_squared = trend.get("r_squared")
        if (
            trend.get("direction") != "stable"
            and isinstance(r_squared, (int, float))
            and r_squared >= 0.2
        ):
            candidates.append(
                {
                    "evidence_id": f"trend:{data_type}",
                    "kind": "multiweek_trend",
                    "data_types": [data_type],
                    "headline": f"{label}: {trend['direction']} multiweek tendency",
                    "evidence": {"unit": unit, **trend},
                    "relevance_score": min(
                        90.0, round(abs(float(percent or 0)) * 1.5 + float(r_squared) * 35, 1)
                    ),
                    "confidence": "moderate" if trend.get("observed_days", 0) >= 14 else "low",
                    "caveat": "A fitted trend summarizes the period and may not continue.",
                }
            )
        temporal = metric.get("temporal_context") or {}
        same_time_percent = temporal.get("same_time_percent")
        if (
            isinstance(same_time_percent, (int, float))
            and temporal.get("same_time_days", 0) >= 3
            and abs(same_time_percent - 100.0) >= 10
        ):
            candidates.append(
                {
                    "evidence_id": f"same-time:{data_type}",
                    "kind": "partial_day_same_time_comparison",
                    "data_types": [data_type],
                    "headline": f"{label}: today's partial value differs from the usual value at this time",
                    "evidence": {"unit": unit, **temporal},
                    "relevance_score": min(75.0, round(abs(float(same_time_percent) - 100.0), 1)),
                    "confidence": "moderate",
                    "caveat": "Today is incomplete; this is a same-time comparison, not a full-day forecast.",
                }
            )
        quality = features.get("data_quality") or {}
        coverage = quality.get("coverage_percent")
        if isinstance(coverage, (int, float)) and coverage < 60:
            candidates.append(
                {
                    "evidence_id": f"quality:{data_type}",
                    "kind": "data_quality_limit",
                    "data_types": [data_type],
                    "headline": f"{label}: limited daily coverage weakens comparisons",
                    "evidence": quality,
                    "relevance_score": round(55.0 + (60.0 - float(coverage)) / 3.0, 1),
                    "confidence": "high",
                    "caveat": "Absence of recorded data is not evidence that the measured event did not occur.",
                }
            )

    for index, association in enumerate(snapshot.get("associations", []), start=1):
        candidates.append(
            {
                "evidence_id": f"association:{index}",
                "kind": "cross_metric_association",
                "data_types": [association["left_data_type"], association["right_data_type"]],
                "headline": f"{association['left']} and {association['right']} move together in this history",
                "evidence": association,
                "relevance_score": round(float(association["reliability_score"]) * 100.0, 1),
                "confidence": "moderate" if association["paired_days"] >= 20 else "low",
                "caveat": "Exploratory association only; it does not establish cause, direction, or prediction.",
            }
        )
    candidates.sort(key=lambda item: item["relevance_score"], reverse=True)
    return candidates[:20]


def build_ai_ready_snapshot(
    store,
    start: str,
    end: str,
    *,
    now: datetime | None = None,
    record_limit: int = 30_000,
) -> dict[str, Any]:
    """Return the compact snapshot plus model-ready deterministic evidence."""

    local_now = (now or datetime.now().astimezone()).astimezone()
    try:
        start_day = date.fromisoformat(start[:10])
        exclusive_end = date.fromisoformat(end[:10])
    except ValueError:
        start_day = local_now.date()
        exclusive_end = start_day + timedelta(days=1)
    end_day = min(exclusive_end - timedelta(days=1), local_now.date())
    snapshot = build_health_snapshot(store, start, end, now=local_now, record_limit=record_limit)
    daily_by_type: dict[str, dict[date, float]] = {}
    observed_dates_by_type: dict[str, set[date]] = {}
    labels: dict[str, str] = {}
    metric_by_type = {str(item.get("data_type")): item for item in snapshot.get("metrics", [])}
    for data_type, metric in metric_by_type.items():
        records = store.list_records(data_type, start, end, limit=record_limit, newest=True)
        observed_dates_by_type[data_type] = {
            day for record in records if (day := _record_day(record)) is not None
        }
        if data_type in REFERENCE_CONFIGURATION_TYPES:
            metric["data_role"] = "reference_configuration"
            metric["interpretation_rule"] = (
                "These dated values are personal reference thresholds/settings, not health "
                "measurements. Do not calculate physiological trends from them or use their "
                "dates as evidence of health-data coverage."
            )
            labels[data_type] = str(metric.get("label") or DATA_TYPE_BY_KEY[data_type].label)
            continue
        daily_result = _daily_values(records, data_type)
        if daily_result:
            daily, _unit, _aggregation = daily_result
            daily_by_type[data_type] = daily
            metric["derived_evidence"] = _metric_features(
                daily, data_type, start_day, end_day, local_now.date()
            )
        structured_comparison = _structured_period_comparison(data_type, records, end_day)
        if structured_comparison:
            metric["structured_period_comparison"] = structured_comparison
        labels[data_type] = str(metric.get("label") or DATA_TYPE_BY_KEY[data_type].label)

    snapshot["preprocessing"] = {
        "version": "health-evidence-v3",
        "performed_at": local_now.isoformat(),
        "principles": [
            "personal baselines instead of generic population thresholds",
            "matched complete periods for recent comparisons",
            "same-time baselines for incomplete cumulative days",
            "robust median-and-MAD anomaly detection",
            "correlations require repeated paired days and never imply causation",
            "missing recordings are treated as missing, never as zero",
            "requested intervals are distinguished from the dates actually observed",
        ],
    }
    snapshot["requested_interval_coverage"] = _requested_interval_coverage(
        snapshot, observed_dates_by_type, start_day, end_day
    )
    snapshot["associations"] = _associations(daily_by_type, labels)
    snapshot["candidate_insights"] = _candidate_insights(snapshot)
    snapshot["analysis_brief"] = {
        "available_metric_count": len(snapshot.get("metrics", [])),
        "top_evidence_ids": [item["evidence_id"] for item in snapshot["candidate_insights"][:8]],
        "coverage_notice": snapshot["requested_interval_coverage"]["coverage_notice"],
        "must_state_data_limitations_first": snapshot["requested_interval_coverage"][
            "scope_is_partially_observed"
        ],
        "requested_synthesis": (
            "Prioritize sustained or multi-metric patterns over trivial day-to-day arithmetic. "
            "Explain the strongest evidence, its magnitude, confidence, and limitations."
        ),
    }
    return snapshot
