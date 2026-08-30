from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from .constants import DATA_TYPE_BY_KEY, DATA_TYPES
from .i18n import _
from .utils import coerce_number, flatten_dict, parse_timestamp


@dataclass(frozen=True)
class VisualProfile:
    chart: str
    aggregation: str
    color: str
    unit: str
    subtitle: str
    scale: float = 1.0


@dataclass(frozen=True)
class SeriesSummary:
    count: int
    latest: float
    mean: float
    median: float
    minimum: float
    maximum: float
    trend_percent: float | None
    baseline_low: float
    baseline_high: float
    anomaly_count: int


TYPE_VISUALS: dict[str, tuple[str, str, str, str]] = {
    "steps": ("bar", "sum", "#34A853", _("steps")),
    "floors": ("bar", "sum", "#0F9D58", _("floors")),
    "distance": ("bar", "sum", "#00A0B0", "m"),
    "active-minutes": ("bar", "sum", "#F9AB00", "min"),
    "active-zone-minutes": ("bar", "sum", "#FF8F00", "min"),
    "active-energy-burned": ("bar", "sum", "#F57C00", "kcal"),
    "total-calories": ("bar", "sum", "#EF6C00", "kcal"),
    "swim-lengths-data": ("bar", "sum", "#039BE5", _("lengths")),
    "heart-rate": ("scatter", "none", "#EA4335", "bpm"),
    "daily-resting-heart-rate": ("line", "none", "#D93025", "bpm"),
    "heart-rate-variability": ("scatter", "none", "#7E57C2", "ms"),
    "daily-heart-rate-variability": ("line", "none", "#673AB7", "ms"),
    "oxygen-saturation": ("scatter", "none", "#4285F4", "%"),
    "daily-oxygen-saturation": ("line", "none", "#1A73E8", "%"),
    "daily-respiratory-rate": ("line", "none", "#00ACC1", _("breaths/min")),
    "respiratory-rate-sleep-summary": ("line", "none", "#0097A7", _("breaths/min")),
    "core-body-temperature": ("line", "none", "#E91E63", "°C"),
    "daily-sleep-temperature-derivations": ("line", "none", "#C2185B", "°C"),
    "blood-glucose": ("scatter", "none", "#8D6E63", "mg/dL"),
    "weight": ("line", "none", "#5C6BC0", "kg"),
    "body-fat": ("line", "none", "#AB47BC", "%"),
    "daily-vo2-max": ("line", "none", "#00897B", "mL/kg/min"),
    "run-vo2-max": ("line", "none", "#00796B", "mL/kg/min"),
    "vo2-max": ("line", "none", "#00796B", "mL/kg/min"),
    "sleep": ("bar", "none", "#7E57C2", "h"),
    "exercise": ("bar", "sum", "#E8710A", "h"),
    "sedentary-period": ("bar", "sum", "#78909C", "h"),
    "hydration-log": ("bar", "sum", "#039BE5", "mL"),
    "nutrition-log": ("bar", "sum", "#43A047", "kcal"),
    "time-in-heart-rate-zone": ("bar", "sum", "#EF5350", "min"),
    "calories-in-heart-rate-zone": ("bar", "sum", "#FF7043", "kcal"),
}

PREFERRED_FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "steps": ("count",),
    "heart-rate": ("beatsPerMinute",),
    "daily-resting-heart-rate": ("beatsPerMinute",),
    "daily-heart-rate-variability": ("averageHeartRateVariabilityMilliseconds",),
    "heart-rate-variability": (
        "rootMeanSquareOfSuccessiveDifferencesMilliseconds",
        "standardDeviationMilliseconds",
    ),
    "daily-oxygen-saturation": ("averagePercentage", "percentage", "average"),
    "oxygen-saturation": ("percentage",),
    "daily-respiratory-rate": ("breathsPerMinute", "rate"),
    "respiratory-rate-sleep-summary": (
        "fullSleepStats.breathsPerMinute",
        "breathsPerMinute",
        "rate",
    ),
    "daily-sleep-temperature-derivations": ("temperatureDelta", "temperature"),
    "core-body-temperature": ("temperature", "celsius"),
    "weight": ("weightGrams", "kilograms", "weight"),
    "body-fat": ("percentage", "bodyFat"),
    "daily-vo2-max": ("vo2Max",),
    "run-vo2-max": ("vo2Max",),
    "vo2-max": ("vo2Max",),
    "sleep": ("minutesAsleep",),
    "exercise": ("activeDuration",),
    "active-energy-burned": ("kcalSum", "kcal"),
    "total-calories": ("kcalSum", "kcal"),
    "floors": ("countSum", "count"),
    "distance": ("distanceMillimetersSum", "distanceMillimeters"),
    "swim-lengths-data": ("strokeCount",),
}

SNAPSHOT_TYPES = (
    "steps",
    "daily-resting-heart-rate",
    "sleep",
    "daily-heart-rate-variability",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-sleep-temperature-derivations",
    "weight",
    "active-zone-minutes",
)

AI_SNAPSHOT_TYPES = tuple(spec.key for spec in DATA_TYPES if spec.auto_sync)

COMPLETION_TYPES = {
    "steps",
    "sleep",
    "active-zone-minutes",
}

# These values grow during the day. A total observed at 10:00 must never be compared
# directly with the totals of completed days.
INTRADAY_CUMULATIVE_TYPES = {
    "steps",
    "floors",
    "distance",
    "active-minutes",
    "active-zone-minutes",
    "active-energy-burned",
    "total-calories",
    "exercise",
    "sedentary-period",
    "hydration-log",
    "nutrition-log",
}

SEVEN_DAY_SPARKLINE_TYPES = {
    "daily-heart-rate-variability",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-sleep-temperature-derivations",
}

LATEST_DAILY_VALUE_TYPES = SEVEN_DAY_SPARKLINE_TYPES | {"daily-resting-heart-rate"}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_IGNORED_NUMERIC_FIELDS = {
    "year",
    "month",
    "day",
    "hours",
    "minutes",
    "seconds",
    "nanos",
    "utcoffset",
    "version",
}


def friendly_metric_name(metric: str) -> str:
    virtual_labels = {
        "__duration_hours__": _("Daily duration"),
        "__heart_rate_samples__": _("Heart rate"),
        "__zone_time__": _("Time by zone"),
        "__zone_calories__": _("Calories by zone"),
        "__heart_rate_zones__": _("Zone thresholds"),
        "__activity_minutes__": _("Minutes by intensity"),
        "__swim_lengths__": _("Completed lengths"),
    }
    if metric in virtual_labels:
        return virtual_labels[metric]
    leaf = metric.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ")
    leaf = _CAMEL_BOUNDARY.sub(" ", leaf)
    replacements = {
        "beats per minute": _("Heart rate"),
        "count": _("Count"),
        "minutes asleep": _("Sleep time"),
        "average heart rate variability milliseconds": _("Average HRV"),
        "root mean square of successive differences milliseconds": _("HRV (RMSSD)"),
        "standard deviation milliseconds": _("HRV (SDNN)"),
        "percentage": _("Percentage"),
        "temperature delta": _("Temperature variation"),
        "vo2 max": "VO₂ max",
    }
    normalized = leaf.strip().lower()
    return replacements.get(normalized, leaf.strip().capitalize())


def available_metrics(records: list[dict[str, Any]], data_type: str) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for record in records[:5000]:
        for key, value in flatten_dict(record["payload"]).items():
            leaf = key.rsplit(".", 1)[-1].lower()
            if coerce_number(value) is not None and leaf not in _IGNORED_NUMERIC_FIELDS:
                counts[key] += 1

    if data_type in {"sleep", "exercise", "sedentary-period"} and any(
        duration_hours(record) is not None for record in records[:100]
    ):
        counts["__duration_hours__"] = len(records)
    virtual_metric = {
        "heart-rate": "__heart_rate_samples__",
        "time-in-heart-rate-zone": "__zone_time__",
        "calories-in-heart-rate-zone": "__zone_calories__",
        "daily-heart-rate-zones": "__heart_rate_zones__",
        "active-minutes": "__activity_minutes__",
        "swim-lengths-data": "__swim_lengths__",
    }.get(data_type)
    if virtual_metric and _virtual_has_data(records, virtual_metric):
        counts[virtual_metric] = len(records)

    hints = PREFERRED_FIELD_HINTS.get(data_type, ())

    def rank(item: tuple[str, int]) -> tuple[int, int, str]:
        key, count = item
        if key.startswith("__"):
            return -1, -count, key
        hint_rank = next(
            (index for index, hint in enumerate(hints) if key.lower().endswith(hint.lower())),
            len(hints) + 1,
        )
        return hint_rank, -count, key

    return [key for key, _count in sorted(counts.items(), key=rank)]


def duration_hours(record: dict[str, Any]) -> float | None:
    payload = flatten_dict(record["payload"])
    for key, value in payload.items():
        if key.lower().endswith("minutesasleep"):
            minutes = coerce_number(value)
            if minutes is not None:
                return minutes / 60.0
    start = parse_timestamp(record.get("start_time"))
    end = parse_timestamp(record.get("end_time"))
    if start is not None and end is not None and end >= start:
        return (end - start) / 3600.0
    return None


def parse_duration_seconds(value: Any) -> float | None:
    if isinstance(value, str) and value.endswith("s"):
        return coerce_number(value[:-1])
    return coerce_number(value)


def _find_named_list(value: Any, name: str) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == name.lower() and isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
            found = _find_named_list(child, name)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_named_list(child, name)
            if found:
                return found
    return []


def _find_named_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in names:
                return child
            found = _find_named_value(child, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_named_value(child, names)
            if found is not None:
                return found
    return None


def _record_timestamp(record: dict[str, Any]) -> float | None:
    return parse_timestamp(record.get("start_time") or record.get("end_time"))


def _normalize_category(value: str) -> str:
    normalized = value.upper()
    for category in ("FAT_BURN", "MODERATE", "VIGOROUS", "LIGHT", "CARDIO", "PEAK"):
        if normalized.endswith(category):
            return category
    return normalized


def _zone_time_for_record(record: dict[str, Any]) -> tuple[str, float] | None:
    zone = _find_named_value(record["payload"], {"heartratezonetype", "heartratezone"})
    duration = duration_hours(record)
    if isinstance(zone, str) and duration is not None and duration > 0:
        return _normalize_category(zone), duration * 60.0
    return None


def _zone_calories_for_record(record: dict[str, Any]) -> dict[str, float]:
    entries = _find_named_list(record["payload"], "caloriesInHeartRateZones")
    result: dict[str, float] = defaultdict(float)
    for entry in entries:
        zone = entry.get("heartRateZone") or entry.get("heartRateZoneType")
        kcal = coerce_number(entry.get("kcal"))
        if isinstance(zone, str) and kcal is not None and kcal > 0:
            result[_normalize_category(zone)] += kcal
    return dict(result)


def _activity_minutes_for_record(record: dict[str, Any]) -> dict[str, float]:
    entries = _find_named_list(record["payload"], "activeMinutesByActivityLevel")
    result: dict[str, float] = defaultdict(float)
    for entry in entries:
        level = entry.get("activityLevel")
        minutes = coerce_number(entry.get("activeMinutes"))
        if isinstance(level, str) and minutes is not None and minutes > 0:
            result[_normalize_category(level)] += minutes
    return dict(result)


def heart_rate_zone_thresholds(
    records: list[dict[str, Any]],
) -> list[tuple[float, dict[str, tuple[float, float]]]]:
    result = []
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        zones: dict[str, tuple[float, float]] = {}
        for entry in _find_named_list(record["payload"], "heartRateZones"):
            zone = entry.get("heartRateZoneType")
            minimum = coerce_number(entry.get("minBeatsPerMinute"))
            maximum = coerce_number(entry.get("maxBeatsPerMinute"))
            if isinstance(zone, str) and minimum is not None and maximum is not None:
                zones[_normalize_category(zone)] = (minimum, maximum)
        if zones:
            result.append((timestamp, zones))
    return sorted(result, key=lambda item: item[0])


def categorical_daily_points(
    records: list[dict[str, Any]], data_type: str
) -> list[tuple[float, dict[str, float]]]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        day = datetime.fromtimestamp(timestamp).date().isoformat()  # noqa: DTZ006
        values: dict[str, float] = {}
        if data_type == "time-in-heart-rate-zone":
            zone_time = _zone_time_for_record(record)
            if zone_time:
                values = {zone_time[0]: zone_time[1]}
        elif data_type == "calories-in-heart-rate-zone":
            values = _zone_calories_for_record(record)
        elif data_type == "active-minutes":
            values = _activity_minutes_for_record(record)
        for category, value in values.items():
            grouped[day][category] += value
    return [
        (datetime.fromisoformat(day).timestamp(), dict(values))
        for day, values in sorted(grouped.items())
        if values
    ]


def _stroke_count(record: dict[str, Any]) -> float | None:
    return coerce_number(_find_named_value(record["payload"], {"strokecount"}))


def _heart_rate_sample_points(
    records: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Read both one-sample records and records containing a samples array."""
    points: list[tuple[float, float]] = []
    for record in records:
        fallback = _record_timestamp(record)
        samples = _find_named_list(record["payload"], "samples")
        for sample in samples:
            bpm = coerce_number(_find_named_value(sample, {"beatsperminute"}))
            raw_time = _find_named_value(
                sample, {"physicaltime", "sampletime", "starttime", "time"}
            )
            timestamp = parse_timestamp(raw_time) if isinstance(raw_time, str) else fallback
            if timestamp is not None and bpm is not None:
                points.append((timestamp, bpm))
        if not samples:
            bpm = coerce_number(
                _find_named_value(record["payload"], {"beatsperminute"})
            )
            if fallback is not None and bpm is not None:
                points.append((fallback, bpm))
    return sorted(points)


def _virtual_has_data(records: list[dict[str, Any]], metric: str) -> bool:
    if metric == "__heart_rate_samples__":
        return bool(_heart_rate_sample_points(records))
    if metric == "__zone_time__":
        return any(_zone_time_for_record(record) for record in records)
    if metric == "__zone_calories__":
        return any(_zone_calories_for_record(record) for record in records)
    if metric == "__heart_rate_zones__":
        return bool(heart_rate_zone_thresholds(records))
    if metric == "__activity_minutes__":
        return any(_activity_minutes_for_record(record) for record in records)
    if metric == "__swim_lengths__":
        return any((_stroke_count(record) or 0) > 0 for record in records)
    return False


def meaningful_record_count(data_type: str, records: list[dict[str, Any]]) -> int:
    if data_type == "swim-lengths-data":
        return sum((_stroke_count(record) or 0) > 0 for record in records)
    return len(records)


def sleep_stage_points(records: list[dict[str, Any]]) -> list[tuple[float, dict[str, float]]]:
    """Return per-session sleep-stage durations in hours."""

    def find_stages(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            stages = value.get("stagesSummary")
            if isinstance(stages, list):
                return [item for item in stages if isinstance(item, dict)]
            for child in value.values():
                found = find_stages(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_stages(child)
                if found:
                    return found
        return []

    result = []
    for record in records:
        timestamp = parse_timestamp(record.get("start_time") or record.get("end_time"))
        if timestamp is None:
            continue
        totals: dict[str, float] = defaultdict(float)
        summarized_stages = find_stages(record["payload"])
        for stage in summarized_stages:
            stage_type = str(stage.get("type", "ALTRO")).upper()
            minutes = coerce_number(stage.get("minutes"))
            if minutes is not None:
                totals[stage_type] += minutes / 60.0
        # Some sleep sessions expose only the raw stage intervals. Keep those
        # sessions available to both the chart and the complete AI analysis.
        if not summarized_stages:
            for stage in _find_named_list(record["payload"], "stages"):
                stage_type = str(stage.get("type", "ALTRO")).upper()
                start = parse_timestamp(
                    _find_named_value(stage, {"starttime", "physicaltime"})
                )
                end = parse_timestamp(_find_named_value(stage, {"endtime"}))
                if start is not None and end is not None and end > start:
                    totals[stage_type] += (end - start) / 3600.0
        if totals:
            result.append((timestamp, dict(totals)))
    return sorted(result, key=lambda item: item[0])


def raw_points(records: list[dict[str, Any]], metric: str) -> list[tuple[float, float]]:
    if metric == "__heart_rate_samples__":
        return _heart_rate_sample_points(records)
    points: list[tuple[float, float]] = []
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        if metric == "__duration_hours__":
            value = duration_hours(record)
        elif metric == "__zone_time__":
            zone_time = _zone_time_for_record(record)
            value = zone_time[1] if zone_time else None
        elif metric == "__zone_calories__":
            calories = _zone_calories_for_record(record)
            value = sum(calories.values()) if calories else None
        elif metric == "__activity_minutes__":
            minutes = _activity_minutes_for_record(record)
            value = sum(minutes.values()) if minutes else None
        elif metric == "__heart_rate_zones__":
            entries = _find_named_list(record["payload"], "heartRateZones")
            maxima = [coerce_number(entry.get("maxBeatsPerMinute")) for entry in entries]
            value = max((item for item in maxima if item is not None), default=None)
        elif metric == "__swim_lengths__":
            value = 1.0 if (_stroke_count(record) or 0) > 0 else None
        else:
            value = coerce_number(flatten_dict(record["payload"]).get(metric))
        if value is not None:
            points.append((timestamp, value))
    points.sort(key=lambda point: point[0])
    return points


def visual_profile(data_type: str, metric: str) -> VisualProfile:
    chart, aggregation, color, unit = TYPE_VISUALS.get(
        data_type, ("line", "none", "#1A73E8", "")
    )
    lowered = metric.lower()
    scale = 1.0
    if metric == "__duration_hours__":
        unit = "h"
    elif metric in {"__zone_time__", "__activity_minutes__"}:
        unit = "min"
        aggregation = "sum"
    elif metric == "__zone_calories__":
        unit = "kcal"
        aggregation = "sum"
    elif metric == "__heart_rate_zones__":
        unit = "bpm"
        chart = "line"
    elif metric == "__swim_lengths__":
        unit = _("lengths")
        chart = "bar"
        aggregation = "sum"
    elif "beatsperminute" in lowered:
        unit = "bpm"
    elif "millisecond" in lowered:
        unit = "ms"
    elif "percentage" in lowered or "percent" in lowered:
        unit = "%"
    elif (
        "minute" in lowered
        and "millisecond" not in lowered
        and data_type not in {"daily-respiratory-rate", "respiratory-rate-sleep-summary"}
    ):
        unit = "min"
    elif "count" in lowered and data_type == "steps":
        unit = _("steps")
    if "millimeter" in lowered and data_type in {"distance", "altitude", "height"}:
        scale = 0.001
        unit = "m"
    elif data_type == "weight" and (
        "weightgrams" in lowered or lowered.rsplit(".", 1)[-1] == "grams"
    ):
        scale = 0.001
        unit = "kg"
    elif "milligram" in lowered and data_type == "weight":
        scale = 0.000001
        unit = "kg"
    elif "microliter" in lowered and data_type == "hydration-log":
        scale = 0.001
        unit = "mL"

    descriptions = {
        "bar": _("Daily total"),
        "scatter": _("Individual measurements and local trend"),
        "line": _("Trend over time and personal range"),
    }
    subtitle = descriptions[chart]
    if data_type == "exercise":
        subtitle = _("Total workout duration per day")
    elif data_type == "sedentary-period":
        subtitle = _("Total sedentary time per day")
    elif metric == "__zone_time__":
        subtitle = _("Daily minutes by heart-rate zone")
    elif metric == "__zone_calories__":
        subtitle = _("Daily calories by heart-rate zone")
    elif metric == "__heart_rate_zones__":
        subtitle = _("Personal heart-rate zone thresholds over time")
    elif metric == "__activity_minutes__":
        subtitle = _("Daily minutes by intensity")
    return VisualProfile(chart, aggregation, color, unit, subtitle, scale)


def display_points(
    points: list[tuple[float, float]], profile: VisualProfile
) -> list[tuple[float, float]]:
    if profile.scale != 1.0:
        points = [(timestamp, value * profile.scale) for timestamp, value in points]
    if profile.aggregation == "none":
        if len(points) <= 12000:
            return points
        stride = math.ceil(len(points) / 12000)
        return points[::stride]

    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for timestamp, value in points:
        # Use the computer's timezone so daily totals match the user's calendar day.
        day = datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
        grouped[day.isoformat()].append((timestamp, value))
    result = []
    for day, values in grouped.items():
        timestamp = datetime.fromisoformat(day).timestamp()
        number = (
            sum(item[1] for item in values)
            if profile.aggregation == "sum"
            else statistics.fmean(item[1] for item in values)
        )
        result.append((timestamp, number))
    return sorted(result)


def initial_x_range(
    points: list[tuple[float, float]], data_type: str, profile: VisualProfile
) -> tuple[float, float] | None:
    """Choose a useful recent viewport while keeping the entire series pannable."""
    if not points:
        return None
    timestamps = sorted(timestamp for timestamp, _value in points)
    earliest, latest = timestamps[0], timestamps[-1]
    day = 86400.0
    if latest <= earliest:
        return latest - day / 2, latest + day / 2

    # Heart-rate charts always open on one calendar day. The seven-day baseline
    # belongs only to the overview band and must never become the visible series.
    if data_type == "heart-rate":
        latest_day = datetime.fromtimestamp(latest).date()  # noqa: DTZ006
        day_start = datetime.combine(latest_day, time.min).timestamp()
        left = max(earliest, day_start)
        padding = max((latest - left) * 0.02, 60.0)
        return left, latest + padding

    span = latest - earliest
    span_days = max(span / day, 1.0)
    density = len(timestamps) / span_days
    dense_types = {
        "heart-rate",
        "heart-rate-variability",
        "oxygen-saturation",
        "respiratory-rate-sleep-summary",
    }
    if data_type in dense_types and density >= 24 and span > day:
        window = day
    elif span > 30 * day:
        window = 30 * day
    else:
        window = span
    left = max(earliest, latest - window)
    padding = max((latest - left) * 0.02, 60.0)
    return left, latest + padding


def y_axis_range(
    points: list[tuple[float, float]],
    data_type: str,
    profile: VisualProfile,
    *,
    show_all: bool = False,
) -> tuple[float, float] | None:
    """Return a readable range while keeping extreme values accessible via 'show all'."""
    if not points:
        return None
    values = sorted(value for _timestamp, value in points if math.isfinite(value))
    if not values:
        return None

    readable_bounds = {
        "heart-rate": (20.0, 250.0),
        "daily-resting-heart-rate": (20.0, 180.0),
        "heart-rate-variability": (0.0, 500.0),
        "daily-heart-rate-variability": (0.0, 500.0),
        "oxygen-saturation": (50.0, 100.0),
        "daily-oxygen-saturation": (50.0, 100.0),
        "daily-respiratory-rate": (4.0, 60.0),
        "respiratory-rate-sleep-summary": (4.0, 60.0),
    }
    if not show_all and data_type in readable_bounds:
        minimum, maximum = readable_bounds[data_type]
        plausible = [value for value in values if minimum <= value <= maximum]
        if plausible:
            values = plausible

    def percentile(fraction: float) -> float:
        if len(values) == 1:
            return values[0]
        position = (len(values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return values[lower]
        weight = position - lower
        return values[lower] * (1 - weight) + values[upper] * weight

    if profile.chart == "bar":
        if show_all or len(values) < 12:
            top = values[-1]
        else:
            first_quartile = percentile(0.25)
            third_quartile = percentile(0.75)
            iqr = third_quartile - first_quartile
            fence = (
                third_quartile + 3.0 * iqr
                if iqr > 0
                else max(third_quartile * 1.5, third_quartile + 1.0)
            )
            top = min(values[-1], fence)
        return 0.0, max(1.0, top * 1.12)

    if show_all or len(values) < 12:
        low, high = values[0], values[-1]
    else:
        first_quartile = percentile(0.25)
        third_quartile = percentile(0.75)
        iqr = third_quartile - first_quartile
        low, high = percentile(0.01), percentile(0.99)
        if iqr > 0:
            low = max(low, first_quartile - 3.0 * iqr)
            high = min(high, third_quartile + 3.0 * iqr)

    minimum_spans = {
        "heart-rate": 30.0,
        "daily-resting-heart-rate": 10.0,
        "heart-rate-variability": 20.0,
        "daily-heart-rate-variability": 15.0,
        "oxygen-saturation": 4.0,
        "daily-oxygen-saturation": 4.0,
        "daily-respiratory-rate": 4.0,
        "respiratory-rate-sleep-summary": 4.0,
        "core-body-temperature": 2.0,
        "daily-sleep-temperature-derivations": 2.0,
    }
    span = high - low
    desired = max(span, minimum_spans.get(data_type, max(abs(high) * 0.05, 1.0)))
    midpoint = (low + high) / 2.0
    padding = desired * 0.08
    return midpoint - desired / 2.0 - padding, midpoint + desired / 2.0 + padding


def summarize_series(points: list[tuple[float, float]]) -> SeriesSummary | None:
    if not points:
        return None
    values = [value for _timestamp, value in points]
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    robust_sigma = 1.4826 * mad
    if robust_sigma == 0:
        robust_sigma = statistics.pstdev(values) if len(values) > 1 else 0.0
    low = median - 2.5 * robust_sigma
    high = median + 2.5 * robust_sigma
    anomalies = sum(value < low or value > high for value in values) if robust_sigma else 0

    window = max(1, min(7, len(values) // 3))
    old = statistics.fmean(values[:window])
    recent = statistics.fmean(values[-window:])
    trend = ((recent - old) / abs(old) * 100.0) if abs(old) > 1e-12 else None
    return SeriesSummary(
        count=len(values),
        latest=values[-1],
        mean=statistics.fmean(values),
        median=median,
        minimum=min(values),
        maximum=max(values),
        trend_percent=trend,
        baseline_low=low,
        baseline_high=high,
        anomaly_count=anomalies,
    )


def rolling_mean(points: list[tuple[float, float]], window: int = 15) -> list[tuple[float, float]]:
    if not points:
        return []
    size = max(2, min(window, max(2, len(points) // 8)))
    result = []
    values: list[float] = []
    for timestamp, value in points:
        values.append(value)
        sample = values[-size:]
        result.append((timestamp, statistics.fmean(sample)))
    return result


def format_value(value: float | None, unit: str = "") -> str:
    if value is None:
        return "—"
    absolute = abs(value)
    if absolute >= 1000:
        number = f"{value:,.0f}".replace(",", ".")
    elif absolute >= 100:
        number = f"{value:.0f}"
    elif absolute >= 10:
        number = f"{value:.1f}"
    else:
        number = f"{value:.2f}"
    return f"{number} {unit}".strip()


def _daily_map(points: list[tuple[float, float]], aggregation: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for timestamp, value in points:
        # Correlations must use the same local calendar day shown in the charts.
        day = datetime.fromtimestamp(timestamp).date().isoformat()  # noqa: DTZ006
        grouped[day].append(value)
    return {
        day: sum(values) if aggregation == "sum" else statistics.fmean(values)
        for day, values in grouped.items()
    }


def daily_progress(
    points: list[tuple[float, float]],
    aggregation: str,
    reference_day: date,
    window_days: int = 7,
) -> dict[str, Any]:
    """Compare one calendar day with the preceding rolling window."""
    daily = _daily_map(points, aggregation)
    current = daily.get(reference_day.isoformat())
    previous_days = [
        (reference_day - timedelta(days=offset)).isoformat()
        for offset in range(1, window_days + 1)
    ]
    baseline_values = [daily[day] for day in previous_days if day in daily]
    baseline = statistics.fmean(baseline_values) if baseline_values else None
    percentage = None
    delta_percent = None
    if current is not None and baseline is not None and baseline > 1e-12 and current >= 0:
        percentage = current / baseline * 100.0
        delta_percent = (current - baseline) / baseline * 100.0
    return {
        "current": current,
        "baseline": baseline,
        "percentage": percentage,
        "delta_percent": delta_percent,
        "days_used": len(baseline_values),
        "window_days": window_days,
    }


def recent_daily_series(
    points: list[tuple[float, float]],
    aggregation: str,
    reference_day: date,
    window_days: int = 7,
) -> list[tuple[float, float]]:
    daily = _daily_map(points, aggregation)
    first_day = reference_day - timedelta(days=window_days - 1)
    result = []
    for offset in range(window_days):
        current_day = first_day + timedelta(days=offset)
        value = daily.get(current_day.isoformat())
        if value is not None:
            result.append((datetime.fromisoformat(current_day.isoformat()).timestamp(), value))
    return result


def _downsample_series(
    points: list[tuple[float, float]], maximum: int = 220
) -> list[tuple[float, float]]:
    if len(points) <= maximum:
        return points
    stride = math.ceil(len(points) / maximum)
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def smooth_heart_rate_points(
    points: list[tuple[float, float]],
    *,
    bin_seconds: int = 300,
    window_seconds: int = 900,
) -> list[tuple[float, float]]:
    """Build a readable intraday curve without altering the stored samples.

    A median for each five-minute bin removes isolated sensor spikes, then a
    centered moving average over roughly fifteen minutes softens the remaining
    short oscillations. Sparse measurements remain visible as individual points.
    """
    if bin_seconds <= 0 or window_seconds <= 0:
        raise ValueError(_("Smoothing intervals must be positive"))

    buckets: dict[int, list[float]] = defaultdict(list)
    for timestamp, value in points:
        if (
            math.isfinite(timestamp)
            and math.isfinite(value)
            and 20 <= value <= 250
        ):
            buckets[math.floor(timestamp / bin_seconds)].append(value)
    if not buckets:
        return []

    binned = [
        (
            bucket * bin_seconds + bin_seconds / 2,
            statistics.median(values),
        )
        for bucket, values in sorted(buckets.items())
    ]
    half_window = max(window_seconds / 2, bin_seconds / 2)
    return [
        (
            timestamp,
            statistics.fmean(
                candidate
                for candidate_timestamp, candidate in binned
                if abs(candidate_timestamp - timestamp) <= half_window
            ),
        )
        for timestamp, _value in binned
    ]


def _same_clock_baseline(
    records: list[dict[str, Any]],
    metric: str,
    profile: VisualProfile,
    local_now: datetime,
    window_days: int = 7,
) -> dict[str, Any]:
    """Compare today's partial total only with earlier days at the same local time."""
    intraday_records = [
        record for record in records if record.get("record_kind") != "daily_rollup"
    ]
    points = raw_points(intraday_records, metric)
    if profile.scale != 1.0:
        points = [(timestamp, value * profile.scale) for timestamp, value in points]

    clock_seconds = (
        local_now.hour * 3600
        + local_now.minute * 60
        + local_now.second
        + local_now.microsecond / 1_000_000
    )
    today = local_now.date()
    totals: dict[str, float] = defaultdict(float)
    for timestamp, value in points:
        measured = datetime.fromtimestamp(timestamp).astimezone()
        measured_seconds = (
            measured.hour * 3600
            + measured.minute * 60
            + measured.second
            + measured.microsecond / 1_000_000
        )
        if measured.date() < today and measured_seconds <= clock_seconds:
            totals[measured.date().isoformat()] += value

    previous_days = [today - timedelta(days=offset) for offset in range(1, window_days + 1)]
    values = [totals[day.isoformat()] for day in previous_days if day.isoformat() in totals]
    return {
        "same_time_mean": statistics.fmean(values) if values else None,
        "same_time_days": len(values),
    }


def _partial_day_context(
    records: list[dict[str, Any]],
    metric: str,
    profile: VisualProfile,
    shown: list[tuple[float, float]],
    completed_points: list[tuple[float, float]],
    local_now: datetime,
) -> dict[str, Any]:
    today_key = local_now.date().isoformat()
    today_so_far = _daily_map(shown, "sum").get(today_key)
    same_clock = _same_clock_baseline(records, metric, profile, local_now)
    same_time_mean = same_clock["same_time_mean"]
    same_time_percent = None
    if (
        today_so_far is not None
        and same_time_mean is not None
        and abs(same_time_mean) > 1e-12
    ):
        same_time_percent = today_so_far / same_time_mean * 100.0

    completed_summary = summarize_series(completed_points)
    has_today = today_so_far is not None
    return {
        "status": "partial_day" if has_today else "no_data_yet",
        "observed_at": local_now.isoformat(),
        "local_time": local_now.strftime("%H:%M"),
        "today_so_far": today_so_far,
        "completed_days_mean": completed_summary.mean if completed_summary else None,
        **same_clock,
        "same_time_percent": same_time_percent,
        "interpretation": (
            "Compare today's value only with same_time_mean; today's total is still partial. "
            "Do not project it linearly to the end of the day."
            if has_today
            else "A missing value for today does not mean zero: the day is still in progress."
        ),
    }


def _generic_enum_details(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    enum_pattern = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")
    for record in records:
        for path, value in flatten_dict(record["payload"]).items():
            lowered = path.lower()
            if "datasource" in lowered or not isinstance(value, str):
                continue
            normalized = value.strip().upper()
            if enum_pattern.fullmatch(normalized):
                counters[path.rsplit(".", 1)[-1]][normalized] += 1
    return {
        field: dict(counter.most_common(10))
        for field, counter in list(counters.items())[:6]
    }


def _sleep_ai_details(records: list[dict[str, Any]]) -> dict[str, Any]:
    stage_sessions = sleep_stage_points(records)
    totals: dict[str, float] = defaultdict(float)
    for _timestamp, stages in stage_sessions:
        for stage, hours in stages.items():
            totals[stage] += hours
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
    return {
        "sessions": len(records),
        "sessions_with_stages": session_count,
        "stages": stages,
    }


def _exercise_ai_details(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    hours: dict[str, float] = defaultdict(float)
    for record in records:
        exercise_type = _find_named_value(record["payload"], {"exercisetype", "type"})
        category = str(exercise_type or "NON_SPECIFICATO").upper()
        counts[category] += 1
        duration = duration_hours(record)
        if duration is not None:
            hours[category] += duration
    return {
        "sessions": len(records),
        "by_type": {
            category: {
                "sessions": count,
                "total_hours": round(hours.get(category, 0.0), 2),
            }
            for category, count in counts.most_common(15)
        },
    }


def _nutrition_ai_details(records: list[dict[str, Any]]) -> dict[str, Any]:
    meals: Counter[str] = Counter()
    nutrients: dict[str, float] = defaultdict(float)
    foods: Counter[str] = Counter()
    for record in records:
        payload = record["payload"]
        meal = _find_named_value(payload, {"mealtype"})
        if isinstance(meal, str):
            meals[meal.upper()] += 1
        food = _find_named_value(payload, {"fooddisplayname"})
        if isinstance(food, str) and food.strip():
            foods[food.strip()] += 1
        for entry in _find_named_list(payload, "nutrients"):
            nutrient = entry.get("nutrient")
            grams = coerce_number(_find_named_value(entry, {"grams"}))
            if isinstance(nutrient, str) and grams is not None:
                nutrients[nutrient.upper()] += grams
    return {
        "entries": len(records),
        "meals": dict(meals.most_common()),
        "top_foods": dict(foods.most_common(10)),
        "nutrients_total_grams": {
            name: round(value, 2) for name, value in sorted(nutrients.items())
        },
    }


def _structured_ai_details(
    data_type: str, records: list[dict[str, Any]]
) -> dict[str, Any] | None:
    details: dict[str, Any] = {}
    if data_type == "sleep":
        details.update(_sleep_ai_details(records))
    elif data_type == "exercise":
        details.update(_exercise_ai_details(records))
    elif data_type == "nutrition-log":
        details.update(_nutrition_ai_details(records))
    elif data_type in {
        "time-in-heart-rate-zone",
        "calories-in-heart-rate-zone",
        "active-minutes",
    }:
        totals: dict[str, float] = defaultdict(float)
        for _timestamp, categories in categorical_daily_points(records, data_type):
            for category, value in categories.items():
                totals[category] += value
        details["category_totals"] = {
            category: round(value, 2) for category, value in sorted(totals.items())
        }
    elif data_type == "daily-heart-rate-zones":
        zones = heart_rate_zone_thresholds(records)
        if zones:
            details["latest_thresholds_bpm"] = zones[-1][1]

    enum_details = _generic_enum_details(records)
    if enum_details:
        details["categorical_fields"] = enum_details
    return details or None


def _additional_ai_metrics(
    records: list[dict[str, Any]],
    data_type: str,
    metrics: list[str],
    maximum: int = 4,
) -> list[dict[str, Any]]:
    result = []
    for metric in metrics:
        profile = visual_profile(data_type, metric)
        summary = summarize_series(display_points(raw_points(records, metric), profile))
        if not summary:
            continue
        result.append(
            {
                "metric": friendly_metric_name(metric),
                "unit": profile.unit,
                "count": summary.count,
                "latest": summary.latest,
                "mean": summary.mean,
                "minimum": summary.minimum,
                "maximum": summary.maximum,
                "trend_percent": summary.trend_percent,
            }
        )
        if len(result) >= maximum:
            break
    return result


def build_daily_progress_snapshot(
    store,
    reference_day: date | None = None,
    *,
    heart_day: date | None = None,
) -> dict[str, Any]:
    """Build dashboard comparisons independently from the long analysis interval."""
    reference_day = reference_day or datetime.now().astimezone().date()
    heart_day = heart_day or reference_day
    query_start = (reference_day - timedelta(days=8)).isoformat()
    query_end = (reference_day + timedelta(days=1)).isoformat()
    metrics: list[dict[str, Any]] = []
    for data_type in SNAPSHOT_TYPES:
        value_day = reference_day
        latest_value: float | None = None
        if data_type == "weight":
            latest_records = store.list_records(data_type, limit=1, newest=True)
            if not latest_records:
                continue
            available = available_metrics(latest_records, data_type)
            if not available:
                continue
            metric = available[0]
            profile = visual_profile(data_type, metric)
            latest_points = display_points(raw_points(latest_records, metric), profile)
            if not latest_points:
                continue
            latest_timestamp, latest_value = latest_points[-1]
            value_day = datetime.fromtimestamp(latest_timestamp).date()  # noqa: DTZ006
            weight_start = (value_day - timedelta(days=8)).isoformat()
            weight_end = (value_day + timedelta(days=1)).isoformat()
            records = store.list_records(
                data_type, weight_start, weight_end, limit=30000, newest=True
            )
        else:
            records = store.list_records(
                data_type, query_start, query_end, limit=30000, newest=True
            )
        if not records:
            continue
        if data_type != "weight":
            available = available_metrics(records, data_type)
            if not available:
                continue
            metric = available[0]
            profile = visual_profile(data_type, metric)
        if data_type == "sleep":
            points = []
            for record in records:
                timestamp = parse_timestamp(record.get("end_time") or record.get("start_time"))
                value = duration_hours(record)
                if timestamp is not None and value is not None:
                    points.append((timestamp, value))
            points.sort()
        else:
            points = raw_points(records, metric)
        shown = display_points(points, profile)
        if data_type in LATEST_DAILY_VALUE_TYPES:
            available_points = [
                point
                for point in shown
                if datetime.fromtimestamp(point[0]).date() <= reference_day  # noqa: DTZ006
            ]
            if available_points:
                latest_timestamp, latest_value = available_points[-1]
                value_day = datetime.fromtimestamp(latest_timestamp).date()  # noqa: DTZ006
        comparison = daily_progress(
            shown,
            "sum" if profile.aggregation == "sum" else "mean",
            value_day,
        )
        if latest_value is not None:
            comparison["current"] = latest_value
            baseline = comparison.get("baseline")
            if baseline is not None and baseline > 1e-12:
                comparison["percentage"] = latest_value / baseline * 100.0
                comparison["delta_percent"] = (latest_value - baseline) / baseline * 100.0
        item = {
            "data_type": data_type,
            "label": DATA_TYPE_BY_KEY[data_type].label,
            "metric": friendly_metric_name(metric),
            "unit": profile.unit,
            "completion": data_type in COMPLETION_TYPES,
            "value_date": value_day.isoformat(),
            "latest_available": value_day != reference_day,
            **comparison,
        }
        if data_type in SEVEN_DAY_SPARKLINE_TYPES:
            sparkline = recent_daily_series(
                shown,
                "sum" if profile.aggregation == "sum" else "mean",
                value_day,
            )
            values = [value for _timestamp, value in sparkline]
            if values:
                item.update(
                    {
                        "sparkline": sparkline,
                        "sparkline_kind": "seven_day",
                        "sparkline_mean": statistics.fmean(values),
                        "sparkline_std": statistics.pstdev(values) if len(values) > 1 else 0.0,
                    }
                )
        metrics.append(item)

    # Parse timestamps first and filter afterwards. This avoids lexical timezone edge cases
    # in SQLite and supports both one-sample and multi-sample heart-rate records.
    heart_records = store.list_records("heart-rate", limit=30000, newest=True)
    if heart_records:
        heart_metrics = available_metrics(heart_records, "heart-rate")
        if heart_metrics:
            heart_profile = visual_profile("heart-rate", heart_metrics[0])
            heart_points = display_points(
                raw_points(heart_records, heart_metrics[0]), heart_profile
            )
            today_points = [
                point
                for point in heart_points
                if datetime.fromtimestamp(point[0]).date() == heart_day  # noqa: DTZ006
            ]
            baseline_start = heart_day - timedelta(days=7)
            baseline_values = [
                value
                for timestamp, value in heart_points
                if baseline_start
                <= datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
                < heart_day
                and 20 <= value <= 250
            ]
            if today_points:
                heart_item = next(
                    (
                        item
                        for item in metrics
                        if item["data_type"] == "daily-resting-heart-rate"
                    ),
                    None,
                )
                if heart_item is None:
                    comparison = daily_progress(heart_points, "mean", heart_day)
                    heart_item = {
                        "data_type": "daily-resting-heart-rate",
                        "label": _("Heart rate"),
                        "metric": "Media giornaliera",
                        "unit": heart_profile.unit,
                        "completion": False,
                        "value_date": heart_day.isoformat(),
                        **comparison,
                    }
                    metrics.append(heart_item)
                heart_item.update(
                    {
                        # Cardiac intraday data deliberately uses separate keys.
                        # This prevents the generic seven-day sparkline path from
                        # ever being selected by the overview card.
                        "heart_day_points": today_points,
                        "heart_day_smoothed": smooth_heart_rate_points(today_points),
                        "heart_smoothing_minutes": 15,
                        "heart_day_date": heart_day.isoformat(),
                        "heart_day_min": min(value for _timestamp, value in today_points),
                        "heart_day_max": max(value for _timestamp, value in today_points),
                        "heart_baseline_mean": (
                            statistics.fmean(baseline_values) if baseline_values else None
                        ),
                        "heart_baseline_std": (
                            statistics.pstdev(baseline_values)
                            if len(baseline_values) > 1
                            else 0.0 if baseline_values else None
                        ),
                        "heart_baseline_days": len(
                            {
                                datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
                                for timestamp, value in heart_points
                                if baseline_start
                                <= datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006
                                < heart_day
                                and 20 <= value <= 250
                            }
                        ),
                    }
                )
    return {
        "reference_date": reference_day.isoformat(),
        "window_days": 7,
        "metrics": metrics,
    }


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 7 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_sum = sum((x - left_mean) ** 2 for x in left)
    right_sum = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator > 0 else None


def build_health_snapshot(
    store,
    start: str,
    end: str,
    *,
    now: datetime | None = None,
    record_limit: int = 30_000,
) -> dict[str, Any]:
    local_now = (now or datetime.now().astimezone()).astimezone()
    today = local_now.date()
    try:
        start_day = date.fromisoformat(start[:10])
        exclusive_end_day = date.fromisoformat(end[:10])
    except ValueError:
        start_day = today
        exclusive_end_day = today + timedelta(days=1)
    includes_today = start_day <= today < exclusive_end_day
    elapsed_seconds = (
        local_now.hour * 3600
        + local_now.minute * 60
        + local_now.second
        + local_now.microsecond / 1_000_000
    )
    metrics: list[dict[str, Any]] = []
    daily: dict[str, dict[str, float]] = {}
    available_data_types: list[str] = []
    analyzed_data_types: list[str] = []
    truncated_data_types: list[str] = []
    records_considered: dict[str, int] = {}
    for data_type in AI_SNAPSHOT_TYPES:
        records = store.list_records(
            data_type, start, end, limit=record_limit, newest=True
        )
        if not records:
            continue
        if meaningful_record_count(data_type, records) == 0:
            continue
        available_data_types.append(data_type)
        records_considered[data_type] = len(records)
        if len(records) >= record_limit:
            truncated_data_types.append(data_type)
        structured_details = _structured_ai_details(data_type, records)
        available = available_metrics(records, data_type)
        if not available:
            metrics.append(
                {
                    "data_type": data_type,
                    "label": DATA_TYPE_BY_KEY[data_type].label,
                    "records_considered": len(records),
                    "summary_scope": "structured_data",
                    "structured_details": structured_details
                    or {
                        "records_present": len(records),
                        "note": _("Category present without compact numeric fields."),
                    },
                }
            )
            analyzed_data_types.append(data_type)
            continue
        metric = available[0]
        profile = visual_profile(data_type, metric)
        points = raw_points(records, metric)
        shown = display_points(points, profile)
        analysis_points = shown
        temporal_context = None
        summary_scope = "selected_period"
        if includes_today and data_type in INTRADAY_CUMULATIVE_TYPES:
            completed_points = [
                point
                for point in shown
                if datetime.fromtimestamp(point[0]).date() != today  # noqa: DTZ006
            ]
            temporal_context = _partial_day_context(
                records, metric, profile, shown, completed_points, local_now
            )
            if completed_points:
                analysis_points = completed_points
                summary_scope = "completed_days_only"
            else:
                summary_scope = "partial_day_only"
        summary = summarize_series(analysis_points)
        if not summary:
            metrics.append(
                {
                    "data_type": data_type,
                    "label": DATA_TYPE_BY_KEY[data_type].label,
                    "records_considered": len(records),
                    "summary_scope": "structured_data",
                    "structured_details": structured_details
                    or {"records_present": len(records)},
                }
            )
            analyzed_data_types.append(data_type)
            continue
        label = DATA_TYPE_BY_KEY[data_type].label
        item = {
            "data_type": data_type,
            "label": label,
            "metric": friendly_metric_name(metric),
            "unit": profile.unit,
            "summary": asdict(summary),
            "summary_scope": summary_scope,
            "records_considered": len(records),
        }
        if temporal_context:
            item["temporal_context"] = temporal_context
        additional_fields = _additional_ai_metrics(records, data_type, available[1:])
        if additional_fields:
            item["additional_fields"] = additional_fields
        if structured_details:
            item["structured_details"] = structured_details
        metrics.append(item)
        analyzed_data_types.append(data_type)
        daily[label] = _daily_map(
            analysis_points, "sum" if profile.aggregation == "sum" else "mean"
        )

    correlations = []
    labels = list(daily)
    for index, left_label in enumerate(labels):
        for right_label in labels[index + 1 :]:
            shared = sorted(set(daily[left_label]) & set(daily[right_label]))
            coefficient = _pearson(
                [daily[left_label][day] for day in shared],
                [daily[right_label][day] for day in shared],
            )
            if coefficient is not None and abs(coefficient) >= 0.35:
                correlations.append(
                    {
                        "left": left_label,
                        "right": right_label,
                        "r": round(coefficient, 3),
                        "days": len(shared),
                    }
                )
    correlations.sort(key=lambda item: abs(item["r"]), reverse=True)
    return {
        "period": {"start": start, "end": end, "end_is_exclusive": True},
        "observation_context": {
            "observed_at": local_now.isoformat(),
            "local_date": today.isoformat(),
            "local_time": local_now.strftime("%H:%M"),
            "selected_period_includes_today": includes_today,
            "current_day_is_incomplete": includes_today,
            "elapsed_day_percent": round(elapsed_seconds / 86400.0 * 100.0, 1),
            "interpretation_rule": (
                "Today's totals are partial: use the same-time comparison when available; "
                "do not compare them with complete days or extrapolate them linearly. "
                "A missing value for today does not mean zero."
            ),
        },
        "metrics": metrics,
        "correlations": correlations[:8],
        "data_coverage": {
            "available_data_types": available_data_types,
            "analyzed_data_types": analyzed_data_types,
            "record_limit_per_type": record_limit,
            "truncated_data_types": truncated_data_types,
            "records_considered": records_considered,
            "interpretation_rule": (
                "Examine all available metrics, additional_fields, and structured_details; "
                "do not limit the analysis to data shown in the Overview."
            ),
        },
    }
