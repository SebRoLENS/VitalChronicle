from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .analysis import (
    available_metrics,
    categorical_daily_points,
    display_points,
    duration_hours,
    raw_points,
    sleep_stage_points,
    summarize_series,
    visual_profile,
)
from .agent_store import AgentStore
from .health_calendar import SESSION_END_DATE_TYPES, local_calendar_records, record_local_day
from .utils import coerce_number, parse_timestamp

MAX_SERIES_POINTS = 360
MAX_LEARNED_STEPS = 12
MIN_ACUTE_LOAD_OBSERVED_DAYS = 5
MIN_CHRONIC_LOAD_OBSERVED_DAYS = 21
ALLOWED_DSL_OPS = {
    "load_series",
    "daily",
    "window",
    "summarize",
    "trend",
    "compare",
    "correlate",
    "count_above",
    "count_below",
    "ratio",
    "return",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    capability: str
    parameters: dict[str, Any]

    def registry_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "parameters": self.parameters,
        }

    def ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


_DATE = {"type": "string", "description": "Local date YYYY-MM-DD."}
_METRIC = {"type": "string", "description": "Dataset, data_type:field, or discovered field. Use hrv for ordinary daily HRV comparisons; heart-rate-variability is a distinct intraday sample metric."}
_PERIOD = {"start": _DATE, "end": _DATE}


def _period(metric: bool = False) -> dict[str, Any]:
    props = ({"metric": _METRIC} if metric else {}) | _PERIOD
    return _obj(props, ("metric",) if metric else ())


# Deterministic health tools and agent/personalisation controls.
_TOOL_ROWS = (
    (
        "get_available_metrics",
        "List local datasets. Pass data_type for its exact field names; dataset names also work as metric identifiers.",
        "data.available_metrics",
        _obj({"data_type": {"type": "string"}}),
    ),
    (
        "get_data_coverage",
        "Measure observed and missing dates for a metric.",
        "data.coverage",
        _period(True),
    ),
    (
        "get_metric_series",
        "Read a bounded deterministic metric series without zero-filling gaps.",
        "data.metric_series",
        _period(True),
    ),
    (
        "summarize_health_overview",
        "Summarize up to eight chosen daily metrics with exact counts, extrema dates and changes. Defaults to HRV, resting HR, sleep totals and steps. No causal or clinical interpretation.",
        "data.health_overview",
        _obj({**_PERIOD, "metrics": {"type": "array", "items": _METRIC, "maxItems": 8}}),
    ),
    (
        "get_daily_summary",
        "Return daily values and summary for a metric.",
        "data.daily_summary",
        _period(True),
    ),
    (
        "get_baseline",
        "Calculate a robust personal baseline for a metric.",
        "data.personal_baseline",
        _period(True),
    ),
    (
        "get_missing_data",
        "List missing dates and consecutive gaps for a metric.",
        "data.missing_dates",
        _period(True),
    ),
    (
        "get_sleep_sessions",
        ("Read sleep sessions, mainSleep labels, local bedtimes and main-sleep baseline. "
         "Use for nightly comparisons; short additional sessions are not entire nights."),
        "sleep.sessions",
        _period(),
    ),
    (
        "analyze_sleep_metric_association",
        ("Calculate main-sleep duration/bedtime correlations with a daily metric (default HRV), "
         "and pair low metric outliers with the preceding main night. Returns compact statistics "
         "and up to eight events, not raw series. Use for HRV dips versus sleep timing/duration."),
        "sleep.metric_association",
        _obj({"metric": _METRIC, **_PERIOD}),
    ),
    (
        "analyze_sleep_stages",
        "Summarize recorded sleep-stage durations and shares.",
        "sleep.stages",
        _period(),
    ),
    (
        "analyze_awakenings",
        "Analyze recorded internal awakenings and awake time.",
        "sleep.awakenings",
        _period(),
    ),
    (
        "calculate_sleep_regularity",
        "Calculate main-sleep timing/duration regularity and weekday versus weekend median bedtimes, correctly handling midnight. Positive weekend delay means later.",
        "sleep.regularity",
        _period(),
    ),
    (
        "calculate_sleep_debt",
        "Estimate recent sleep deficit against the user's longer baseline.",
        "sleep.debt",
        _period(),
    ),
    (
        "calculate_hrv_status",
        "Compare recent HRV with the personal baseline.",
        "recovery.hrv_status",
        _period(),
    ),
    (
        "calculate_rhr_status",
        "Compare recent resting heart rate with the personal baseline.",
        "recovery.rhr_status",
        _period(),
    ),
    (
        "calculate_readiness",
        "Calculate transparent VitalChronicle readiness from sleep, HRV and resting HR.",
        "recovery.readiness",
        _period(),
    ),
    (
        "calculate_recovery_trend",
        "Summarize the direction of recent recovery signals.",
        "recovery.trend",
        _period(),
    ),
    (
        "detect_recovery_anomaly",
        "Detect unusual personal recovery deviations.",
        "recovery.anomaly",
        _period(),
    ),
    (
        "calculate_cardio_load",
        "Calculate transparent cardiovascular load from recorded intensity data.",
        "training.cardio_load",
        _period(),
    ),
    (
        "calculate_acute_load",
        "Calculate recent 7-day training/cardio load.",
        "training.acute_load",
        _period(),
    ),
    (
        "calculate_chronic_load",
        "Calculate prior 28-day weekly-equivalent load.",
        "training.chronic_load",
        _period(),
    ),
    (
        "calculate_target_load",
        "Estimate a personal target-load range from chronic load and readiness.",
        "training.target_load",
        _period(),
    ),
    (
        "calculate_training_status",
        "Classify recent load relative to personal history and recovery.",
        "training.status",
        _period(),
    ),
    (
        "analyze_workout",
        "Summarize workout counts, duration and duration-weighted recorded mean heart rate by type. Optional exercise_type is an exact recorded type such as SWIMMING.",
        "training.workout_analysis",
        _obj({**_PERIOD, "exercise_type": {"type": "string"}}),
    ),
    (
        "summarize_nutrition_logs",
        "Sum recorded dietary calories and protein from actual nutrition-log meals. Food is a catalogue, not consumed meals. Missing nutrients remain unknown; logs may not cover all intake.",
        "nutrition.intake",
        _period(),
    ),
    (
        "estimate_cardio_fitness",
        "Return recorded VO2-max/cardio-fitness evidence when available.",
        "fitness.cardio_fitness",
        _period(),
    ),
    ("analyze_fitness_trend", "Analyze recorded cardio-fitness trend.", "fitness.trend", _period()),
    (
        "analyze_training_progression",
        "Compare recent training load with longer personal history.",
        "fitness.training_progression",
        _period(),
    ),
    (
        "calculate_stress_load",
        "Estimate physiological strain from recovery deviation and training load.",
        "resilience.stress_load",
        _period(),
    ),
    (
        "calculate_resilience",
        "Estimate medium-term VitalChronicle resilience from recovery stability, sleep and load.",
        "resilience.score",
        _period(),
    ),
    (
        "analyze_stress_recovery_balance",
        "Compare recent estimated strain with recovery/resilience evidence.",
        "resilience.balance",
        _period(),
    ),
    (
        "compare_periods",
        "Compare one metric between two explicit periods.",
        "analysis.compare_periods",
        _obj(
            {"metric": _METRIC, "start_a": _DATE, "end_a": _DATE, "start_b": _DATE, "end_b": _DATE},
            ("metric", "start_a", "end_a", "start_b", "end_b"),
        ),
    ),
    (
        "detect_outliers",
        "Detect robust personal outliers for a metric.",
        "analysis.outliers",
        _period(True),
    ),
    (
        "calculate_correlations",
        "Calculate exploratory Pearson association. offset_days shifts metric B relative to metric A (0=same day, 1=following day). Returns compact paired statistics, not raw series.",
        "analysis.correlations",
        _obj({"metric_a": _METRIC, "metric_b": _METRIC, **_PERIOD,
              "offset_days": {"type": "integer", "minimum": -7, "maximum": 7, "default": 0}},
             ("metric_a", "metric_b")),
    ),
    (
        "detect_trends",
        "Calculate deterministic trend for a metric.",
        "analysis.trends",
        _period(True),
    ),
    (
        "recommend_daily_activity",
        "Suggest general activity from readiness, load and personal history.",
        "coaching.daily_activity",
        _period(),
    ),
    (
        "recommend_workout",
        "Choose a general workout type/intensity from readiness, load and preferences.",
        "coaching.workout",
        _obj(
            {
                **_PERIOD,
                "available_minutes": {"type": "integer", "minimum": 5, "maximum": 300},
                "goal": {"type": "string"},
                "preferred_activity": {"type": "string"},
            }
        ),
    ),
    (
        "record_self_report",
        "Store an explicit dated subjective self-report in the local agent store without promoting it to a stable association.",
        "agent.self_report",
        _obj(
            {
                "statement": {"type": "string"},
                "category": {"type": "string"},
                "observed_at": {"type": "string"},
                "intensity": {"type": "number", "minimum": 0, "maximum": 10},
                "context": {"type": "object"},
            },
            ("statement",),
        ),
    ),
    (
        "get_recent_self_reports",
        "Read recent explicit subjective self-reports from the local agent store.",
        "agent.self_reports",
        _obj(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": 3650},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "category": {"type": "string"},
            }
        ),
    ),
    (
        "search_tool_registry",
        "Search existing tools before creating a new capability.",
        "agent.registry_search",
        _obj(
            {"capability": {"type": "string"}, "description": {"type": "string"}}, ("capability",)
        ),
    ),
    (
        "create_learned_tool",
        "Create a reusable tool only as a validated safe declarative pipeline; equivalent tools are reused.",
        "agent.create_learned_tool",
        _obj(
            {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "capability": {"type": "string"},
                "parameters": {"type": "object"},
                "pipeline": {
                    "type": "array",
                    "maxItems": MAX_LEARNED_STEPS,
                    "items": {"type": "object"},
                },
            },
            ("name", "description", "capability", "pipeline"),
        ),
    ),
    (
        "get_user_model",
        "Read learned user-specific associations and their confidence.",
        "agent.user_model",
        _obj({}),
    ),
    (
        "ask_user_feedback",
        "Queue one targeted question when it can materially reduce uncertainty.",
        "agent.ask_feedback",
        _obj(
            {
                "question": {"type": "string"},
                "reason": {"type": "string"},
                "learning_key": {"type": "string"},
                "context": {"type": "object"},
            },
            ("question", "reason"),
        ),
    ),
    (
        "create_monitoring_rule",
        "Create or update a persistent in-app check-in rule. This is not a learned analysis tool, background listener, or operating-system notification.",
        "agent.create_monitoring_rule",
        _obj(
            {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "question": {"type": "string"},
                "cadence_days": {"type": "integer", "minimum": 1, "maximum": 30},
                "keywords": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "string"},
                },
                "fields": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
            },
            ("name", "title", "question", "cadence_days", "keywords"),
        ),
    ),
    (
        "record_monitoring_observation",
        "Record one dated subjective observation for an existing monitoring rule.",
        "agent.record_monitoring_observation",
        _obj(
            {
                "name": {"type": "string"},
                "statement": {"type": "string"},
                "observed_at": {"type": "string"},
                "context": {"type": "object"},
            },
            ("name", "statement"),
        ),
    ),
    (
        "list_monitoring_rules",
        "List active persistent in-app monitoring/check-in rules and observation counts.",
        "agent.monitoring_rules",
        _obj({}),
    ),
    (
        "get_user_feedback_history",
        "Read recent answered feedback used for personalisation.",
        "agent.feedback_history",
        _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    ),
    (
        "learn_user_association",
        "Store a cautiously worded user-specific association; never a medical-safety claim.",
        "agent.learn_association",
        _obj(
            {
                "key": {"type": "string"},
                "statement": {"type": "string"},
                "evidence": {"type": "object"},
                "temporal_scope": {"type": "string", "enum": ["stable", "temporary"]},
                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 3650},
            },
            ("key", "statement"),
        ),
    ),
)
BUILTIN_TOOL_SPECS = tuple(ToolSpec(*row) for row in _TOOL_ROWS)

_ALIASES = {
    "hrv": "daily-heart-rate-variability",
    "heart_rate_variability": "daily-heart-rate-variability",
    "rhr": "daily-resting-heart-rate",
    "resting_heart_rate": "daily-resting-heart-rate",
    "sleep_duration": "sleep",
    "workout": "exercise",
    "workouts": "exercise",
    "cardio_fitness": "daily-vo2-max",
    "vo2max": "daily-vo2-max",
}

_END_DATE_TYPES = SESSION_END_DATE_TYPES


def _date_semantics(data_type: str) -> str:
    if data_type == "sleep":
        return "wake_up_date"
    if data_type in _END_DATE_TYPES:
        return "session_end_date"
    return "calendar_observation_date"


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _bounds(start: Any, end: Any, days: int = 28) -> tuple[date, date]:
    today = datetime.now().astimezone().date()
    right = _parse_date(end) or today
    left = _parse_date(start) or right - timedelta(days=days - 1)
    return (right, left) if right < left else (left, right)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().date().isoformat()


def _record_semantic_timestamp(record: dict[str, Any], data_type: str) -> float | None:
    raw = (
        record.get("end_time") or record.get("start_time")
        if data_type in _END_DATE_TYPES
        else record.get("start_time") or record.get("end_time")
    )
    return parse_timestamp(raw)


def _semantic_day(record: dict[str, Any], data_type: str) -> str | None:
    return record_local_day(record, data_type)


def _daily(points: list[tuple[float, float]], aggregation: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for ts, value in points:
        grouped.setdefault(_day(ts), []).append(float(value))
    return {
        key: sum(values) if aggregation == "sum" else statistics.fmean(values)
        for key, values in grouped.items()
    }


def _baseline(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "mean": None, "mad": None}
    median = statistics.median(values)
    return {
        "count": len(values),
        "median": round(median, 4),
        "mean": round(statistics.fmean(values), 4),
        "mad": round(statistics.median(abs(v - median) for v in values), 4),
    }


def _confidence(observed: int, expected: int, components: int = 1, available: int = 1) -> float:
    if observed <= 0 or available <= 0:
        return 0.0
    coverage = min(1.0, observed / max(1, expected))
    sample = min(1.0, observed / 14.0)
    component = min(1.0, available / max(1, components))
    return round(max(0.05, min(0.98, 0.55 * coverage + 0.25 * sample + 0.2 * component)), 3)


class SafeToolExecutor:
    """Read-only built-ins plus learned pipelines from a strict allow-list."""

    def __init__(self, health_store, agent_store: AgentStore) -> None:
        self.health_store = health_store
        self.agent_store = agent_store
        self._specs = {spec.name: spec for spec in BUILTIN_TOOL_SPECS}
        agent_store.sync_builtin_tools(spec.registry_spec() for spec in BUILTIN_TOOL_SPECS)

    def tool_schemas(self) -> list[dict[str, Any]]:
        schemas = [spec.ollama_schema() for spec in BUILTIN_TOOL_SPECS]
        schemas += [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["parameters"] or _obj({}),
                },
            }
            for item in self.agent_store.list_tools(kind="learned")
        ]
        return schemas

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        stored = self.agent_store.tool(name)
        if stored and stored["status"] == "superseded" and stored.get("replacement"):
            name = str(stored["replacement"])
            stored = self.agent_store.tool(name)
        handler = getattr(self, f"_tool_{name}", None)
        if handler:
            result = handler(args, thread_id=thread_id)
            self.agent_store.record_tool_use(name)
            return result
        if stored and stored["kind"] == "learned" and stored["status"] == "active":
            result = self._run_learned(stored, args)
            self.agent_store.record_tool_use(name)
            return result
        raise ValueError(f"Unknown or inactive agent tool: {name}")

    def _records(self, data_type: str, left: date, right: date) -> list[dict[str, Any]]:
        return local_calendar_records(self.health_store, data_type, left, right)

    def _semantic_records(
        self, data_type: str, left: date, right: date
    ) -> list[dict[str, Any]]:
        return self._records(data_type, left, right)

    def _series(self, metric: str, left: date, right: date) -> dict[str, Any]:
        raw = str(metric or "").strip()
        data_type, _, explicit = raw.partition(":")
        if not explicit and "." in data_type:
            # get_available_metrics returns field names such as
            # dailyHeartRateVariability.averageHeartRateVariabilityMilliseconds.
            # Accept those names directly rather than querying a nonexistent data type.
            prefix = data_type.split(".", 1)[0]
            inferred = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", prefix).lower()
            if inferred in self.health_store.counts():
                explicit = data_type
                data_type = inferred
        data_type = _ALIASES.get(data_type, data_type)
        context = str(getattr(self, "_request_context", "") or "").casefold()
        if (
            context and data_type == "heart-rate-variability"
            and "daily-heart-rate-variability" in self.health_store.counts()
            and not any(marker in context for marker in (
                "campion", "intraday", "intranotte", "raw", "ogni minuto", "minuto per minuto",
                "heartratevariability.",
            ))
            and not re.search(r"(?<!daily-)heart-rate-variability\b", context)
        ):
            raise ValueError(
                "For an ordinary HRV question use metric='hrv' (source daily HRV). "
                "heart-rate-variability contains intraday samples and is a different measurement. "
                "It is available only when that sample-level variant is explicitly requested."
            )
        records = self._semantic_records(data_type, left, right)
        metrics = available_metrics(records, data_type) if records else []
        field = metrics[0] if metrics else None
        if explicit:
            candidates = [m for m in metrics if m == explicit or m.endswith("." + explicit)]
            if records and len(candidates) != 1:
                raise ValueError(
                    f"Unknown or ambiguous metric field {explicit!r} for {data_type}. "
                    f"Available fields: {', '.join(metrics[:12])}. "
                    "Use the dataset name alone for its primary metric."
                )
            field = candidates[0] if len(candidates) == 1 else None
        if field is None:
            return {
                "metric": raw,
                "data_type": data_type,
                "field": None,
                "points": [],
                "unit": "",
                "aggregation": "none",
            }
        profile = visual_profile(data_type, field)
        source_points = raw_points(records, field)
        if data_type in _END_DATE_TYPES:
            source_points = []
            for record in records:
                timestamp = _record_semantic_timestamp(record, data_type)
                candidate = raw_points([record], field)
                if timestamp is not None and candidate:
                    source_points.append((timestamp, float(candidate[-1][1])))
        points = display_points(source_points, profile)
        if data_type == "sleep":
            # A daily sleep value is the total recorded sleep, not the mean
            # session length (which makes a nap halve an otherwise normal night).
            daily = _daily(source_points, "sum")
            points = [
                (datetime.fromisoformat(day).astimezone().timestamp(), value)
                for day, value in sorted(daily.items())
            ]
        return {
            "metric": raw,
            "data_type": data_type,
            "field": field,
            "points": [
                (float(ts), float(value)) for ts, value in points
                if math.isfinite(float(value)) and left.isoformat() <= _day(ts) <= right.isoformat()
            ],
            "unit": profile.unit,
            "aggregation": "sum" if data_type == "sleep" else profile.aggregation,
            "date_semantics": _date_semantics(data_type),
        }

    def _series_info(
        self, metric: str, left: date, right: date, include_points: bool = False
    ) -> dict[str, Any]:
        series = self._series(metric, left, right)
        points = series["points"]
        days = sorted({_day(ts) for ts, _ in points})
        expected = (right - left).days + 1
        summary = summarize_series(points)
        result = {
            "metric": metric,
            "data_type": series["data_type"],
            "field": series["field"],
            "unit": series["unit"],
            "date_semantics": series.get("date_semantics", _date_semantics(series["data_type"])),
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "observed_days": len(days),
            "expected_days": expected,
            "coverage": round(len(days) / max(1, expected), 3),
            "confidence": _confidence(len(days), expected),
            "summary": None
            if summary is None
            else {
                "count": summary.count,
                "latest": summary.latest,
                "earliest": points[0][1],
                "first_date": _day(points[0][0]),
                "last_date": _day(points[-1][0]),
                "first_to_last_change": summary.latest - points[0][1],
                "mean": summary.mean,
                "median": summary.median,
                "minimum": summary.minimum,
                "maximum": summary.maximum,
                "minimum_date": _day(min(points, key=lambda point: point[1])[0]),
                "maximum_date": _day(max(points, key=lambda point: point[1])[0]),
                "trend_percent": summary.trend_percent,
                "baseline_low": summary.baseline_low,
                "baseline_high": summary.baseline_high,
                "anomaly_count": summary.anomaly_count,
            },
            "method": "Deterministic local values; missing observations are omitted, never zero-filled.",
        }
        if series["data_type"] == "sleep":
            result["method"] += (
                " Sleep values are daily totals of recorded sessions by wake-up date,"
                " including additional sessions; use get_sleep_sessions for main-night timing/duration."
            )
        if include_points:
            stride = max(1, math.ceil(len(points) / MAX_SERIES_POINTS))
            bounded = points[::stride]
            result["points"] = [
                {"time": datetime.fromtimestamp(ts).astimezone().isoformat(), "value": value}
                for ts, value in bounded
            ]
            result["downsampled"] = len(bounded) < len(points)
        return result

    def _tool_get_available_metrics(self, args, **_):
        rows = []
        counts = self.health_store.counts()
        selected = str(args.get("data_type") or "").strip()
        selected = _ALIASES.get(selected, selected)
        for data_type, count in sorted(counts.items()):
            if selected and data_type != selected:
                continue
            records = self.health_store.list_records(
                data_type, limit=min(count, 2000 if selected else 1), newest=True
            )
            rows.append(
                {
                    "data_type": data_type,
                    "records": count,
                    "metrics": available_metrics(records, data_type)[:12 if selected else 1],
                }
            )
        return {
            "available_data_types": counts,
            "data_types": rows,
            "count": len(rows),
            "field_lookup": "Pass data_type to inspect all its field names; use dataset names for primary metrics.",
        }

    def _tool_get_metric_series(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        focus = getattr(self, "_resolved_followup_window", None)
        if focus and (left.isoformat(), right.isoformat()) != focus:
            raise ValueError(
                f"This follow-up asks for {focus[0]} through {focus[1]}, inclusive. "
                "Use that interval, not the week already analysed in conversation history."
            )
        return self._series_info(str(args.get("metric") or ""), left, right, True)

    def _tool_summarize_health_overview(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 7)
        metrics = args.get("metrics", ["hrv", "rhr", "sleep", "steps"])
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 8 or not all(isinstance(m, str) for m in metrics):
            raise ValueError("metrics must contain 1 to 8 metric identifiers")
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "includes_today": left <= date.today() <= right,
            "metrics": [self._series_info(m, left, right) for m in dict.fromkeys(metrics)],
            "method": "Descriptive observed daily measurements only. No causality, significance test, medical assessment or recovery clearance.",
        }

    def _coverage(self, args: dict[str, Any]) -> dict[str, Any]:
        left, right = _bounds(args.get("start"), args.get("end"))
        info = self._series_info(str(args.get("metric") or ""), left, right)
        series = self._series(str(args.get("metric") or ""), left, right)
        observed = {_day(ts) for ts, _ in series["points"]}
        missing = [
            (left + timedelta(days=i)).isoformat()
            for i in range((right - left).days + 1)
            if (left + timedelta(days=i)).isoformat() not in observed
        ]
        return {**info, "missing_dates": missing[:366], "missing_date_count": len(missing)}

    def _tool_get_data_coverage(self, args, **_):
        return self._coverage(args)

    def _tool_get_missing_data(self, args, **_):
        item = self._coverage(args)
        dates = [date.fromisoformat(value) for value in item["missing_dates"]]
        ranges: list[tuple[date, date]] = []
        for day in dates:
            if ranges and day == ranges[-1][1] + timedelta(days=1):
                ranges[-1] = (ranges[-1][0], day)
            else:
                ranges.append((day, day))
        return {
            "metric": item["metric"],
            "period": item["period"],
            "coverage": item["coverage"],
            "missing_date_count": item["missing_date_count"],
            "missing_ranges": [
                {"start": a.isoformat(), "end": b.isoformat(), "days": (b - a).days + 1}
                for a, b in ranges
            ],
        }

    def _tool_get_daily_summary(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        metric = str(args.get("metric") or "")
        series = self._series(metric, left, right)
        daily = _daily(series["points"], "sum" if series["aggregation"] == "sum" else "mean")
        return {
            **self._series_info(metric, left, right),
            "daily": [{"date": day, "value": value} for day, value in sorted(daily.items())],
            "daily_baseline": _baseline(list(daily.values())),
        }

    def _tool_get_baseline(self, args, **_):
        item = self._tool_get_daily_summary(args)
        return {
            "metric": item["metric"],
            "period": item["period"],
            "unit": item["unit"],
            "coverage": item["coverage"],
            "confidence": item["confidence"],
            "baseline": item["daily_baseline"],
            "status": "descriptive_only_short_history" if item["observed_days"] < 7 else "available",
            "interpretation": "Fewer than 7 observed days cannot establish a usual personal baseline; widen the interval. Equality with a single-day value does not demonstrate stable recovery or medical safety." if item["observed_days"] < 7 else "Descriptive personal reference, not medical safety or a prediction.",
        }

    def _sleep_rows(
        self, left: date, right: date
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records = self._semantic_records("sleep", left, right)
        rows = []
        for record in records:
            hours = duration_hours(record)
            raw_end = record.get("end_time") or record.get("start_time")
            try:
                end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00")).astimezone()
            except (TypeError, ValueError):
                continue
            if hours is not None:
                sleep = record.get("payload", {}).get("sleep", {})
                main_sleep = sleep.get("metadata", {}).get("mainSleep")
                try:
                    start = datetime.fromisoformat(
                        str(record.get("start_time")).replace("Z", "+00:00")
                    ).astimezone().isoformat()
                except (TypeError, ValueError):
                    start = record.get("start_time")
                rows.append(
                    {
                        "date": end.date().isoformat(),
                        "hours": round(hours, 3),
                        "minutes_asleep": round(hours * 60, 3),
                        "start_time": start,
                        "end_time": end.isoformat(),
                        "main_sleep": main_sleep if isinstance(main_sleep, bool) else None,
                    }
                )
        return records, rows

    def _tool_get_sleep_sessions(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        records, rows = self._sleep_rows(left, right)
        expected = (right - left).days + 1
        main_rows = [row for row in rows if row["main_sleep"] is True]
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "interpretation": (
                "main_sleep=true is explicitly labelled mainSleep by the source; null is unknown,"
                " not a duplicate or a confirmed nap. Multiple sessions on one date are not"
                " necessarily duplicate records. Compare HRV with the main sleep ending on that"
                " date, not a later short session. Timestamps include the computer's local UTC"
                " offset. Daily sleep metric totals include all sessions, unlike this main-sleep baseline."
            ),
            "main_sleep_baseline_hours": _baseline([row["hours"] for row in main_rows]),
            "main_sleep_observed_days": len({row["date"] for row in main_rows}),
            "returned_sessions": min(len(rows), 60),
            "omitted_sessions": max(0, len(rows) - 60),
            "truncated": len(rows) > 60,
            "truncation_notice": (
                "Request a shorter interval to inspect omitted sessions." if len(rows) > 60 else None
            ),
            "session_count": len(rows),
            "sessions_with_stages": len(sleep_stage_points(records)),
            "coverage": round(len({row["date"] for row in rows}) / max(1, expected), 3),
            "confidence": _confidence(len({row["date"] for row in rows}), expected),
            "date_semantics": "wake_up_date",
            "sessions": rows[-60:],
        }

    def _tool_analyze_sleep_metric_association(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 60)
        metric = str(args.get("metric") or "hrv")
        series = self._series(metric, left, right)
        daily = _daily(series["points"], "sum" if series["aggregation"] == "sum" else "mean")
        _, rows = self._sleep_rows(left, right)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row["main_sleep"] is True:
                grouped.setdefault(row["date"], []).append(row)
        # Do not choose arbitrarily if the source labels multiple main sleeps.
        main = {day: items[0] for day, items in grouped.items() if len(items) == 1}
        paired = sorted(set(main) & set(daily))
        starts: dict[str, float] = {}
        for day in paired:
            try:
                dt = datetime.fromisoformat(main[day]["start_time"])
            except (TypeError, ValueError):
                continue
            minute = dt.hour * 60 + dt.minute
            starts[day] = float(minute + (1440 if dt.hour < 12 else 0))

        def pearson(pairs):
            if len(pairs) < 4:
                return None
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            if not statistics.pstdev(xs) or not statistics.pstdev(ys):
                return None
            return round(statistics.correlation(xs, ys), 4)

        baseline = _baseline(list(daily.values()))
        sigma = 1.4826 * (baseline["mad"] or 0)
        threshold = (baseline["median"] - 2.5 * sigma) if sigma else None
        low_dates = sorted(day for day in daily if threshold is not None and daily[day] < threshold)
        events = {}
        for day in low_dates[:8]:
            night = main.get(day)
            events[day] = {
                "metric_value": daily[day],
                "main_sleep_hours": night["hours"] if night else None,
                "main_sleep_minutes": night["minutes_asleep"] if night else None,
                "local_start": night["start_time"] if night else None,
            }
        usual_minute = statistics.median(starts.values()) if starts else None
        for day, event in events.items():
            delay = starts.get(day)
            event["bedtime_delay_minutes"] = (
                delay - usual_minute if delay is not None and usual_minute is not None else None
            )
        return {
            "metric": metric,
            "unit": series["unit"],
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "expected_days": (right - left).days + 1,
            "metric_observed_days": len(daily),
            "metric_median": baseline["median"],
            "metric_mean": baseline["mean"],
            "main_sleep_observed_days": len(main),
            "paired_days": len(paired),
            "bedtime_paired_days": len(starts),
            "duration_pearson_r": pearson([(daily[d], main[d]["hours"]) for d in paired]),
            "bedtime_pearson_r": pearson([(daily[d], starts[d]) for d in starts]),
            "main_sleep_median_hours": statistics.median([r["hours"] for r in main.values()]) if main else None,
            "usual_local_start": (
                f"{int(usual_minute) // 60 % 24:02}:{int(usual_minute) % 60:02}"
                if usual_minute is not None else None
            ),
            "low_metric_threshold": round(threshold, 4) if threshold is not None else None,
            "low_metric_event_count": len(low_dates),
            "omitted_events": max(0, len(low_dates) - len(events)),
            "low_metric_events": events,
            "ambiguous_main_sleep_days": len(grouped) - len(main),
            "method": "Source-labelled mainSleep, same wake date. Bedtime unwrapped at noon: positive bedtime_delay_minutes means LATER than usual, even after midnight. Low < median-2.5*1.4826*MAD. Missing pairs omitted. Exploratory, not causal.",
        }

    def _tool_analyze_sleep_stages(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        records, rows = self._sleep_rows(left, right)
        stages = sleep_stage_points(records)
        totals: dict[str, float] = {}
        for _ts, item in stages:
            for stage, hours in item.items():
                totals[stage] = totals.get(stage, 0.0) + float(hours)
        total = sum(totals.values())
        return {
            "sessions": len(rows),
            "sessions_with_stages": len(stages),
            "stages": {
                name: {
                    "hours": round(hours, 2),
                    "share_percent": round(hours / total * 100, 1) if total else None,
                }
                for name, hours in sorted(totals.items())
            },
            "confidence": _confidence(len(stages), max(1, len(rows))),
            "limitations": "Wearable-derived sleep stages are summarized as recorded; missing stages are not inferred.",
        }

    def _tool_analyze_awakenings(self, args, **_):
        from . import analysis as analysis_module
        from .deterministic_detail_core import _enhanced_sleep_details

        left, right = _bounds(args.get("start"), args.get("end"))
        records = self._records("sleep", left, right)
        details = _enhanced_sleep_details(records, analysis_module)
        details["confidence"] = _confidence(
            int(details.get("sessions_with_stage_timeline") or 0), max(1, len(records))
        )
        return details

    def _tool_calculate_sleep_regularity(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 42)
        _records, rows = self._sleep_rows(left, right)
        main_rows = [row for row in rows if row["main_sleep"] is True]
        starts, durations = [], []
        cohorts: dict[str, list[float]] = {"weekend": [], "weekday": []}
        for row in main_rows:
            try:
                dt = datetime.fromisoformat(
                    str(row["start_time"]).replace("Z", "+00:00")
                ).astimezone()
            except (TypeError, ValueError):
                continue
            minute = dt.hour * 60 + dt.minute + (1440 if dt.hour < 12 else 0)
            starts.append(float(minute))
            durations.append(float(row["hours"]))
            cohort = "weekend" if date.fromisoformat(row["date"]).weekday() >= 5 else "weekday"
            cohorts[cohort].append(float(minute))
        start_sd = statistics.pstdev(starts) if len(starts) > 1 else None
        duration_sd = statistics.pstdev(durations) if len(durations) > 1 else None
        score = (
            None
            if start_sd is None or duration_sd is None
            else max(0.0, min(100.0, 100 - min(60.0, start_sd / 2) - min(40.0, duration_sd * 18)))
        )
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "regularity_score": None if score is None else round(score, 1),
            "sleep_start_sd_minutes": None if start_sd is None else round(start_sd, 1),
            "duration_sd_hours": None if duration_sd is None else round(duration_sd, 2),
            "sessions": len(durations),
            "excluded_non_main_sessions": len(rows) - len(main_rows),
            "bedtime_by_wake_day_type": {
                name: {
                    "observed_nights": len(values),
                    "median_start_minutes_unwrapped": statistics.median(values) if values else None,
                    "median_local_start": (
                        f"{int(statistics.median(values)) // 60 % 24:02}:{int(statistics.median(values)) % 60:02}"
                        if values else None
                    ),
                } for name, values in cohorts.items()
            },
            "weekend_delay_minutes": (
                statistics.median(cohorts["weekend"]) - statistics.median(cohorts["weekday"])
                if all(cohorts.values()) else None
            ),
            "confidence": _confidence(len(durations), (right - left).days + 1),
            "method": "Source-labelled mainSleep only, local bedtime unwrapped at noon. Weekend=Saturday/Sunday WAKE dates (Friday/Saturday nights). Positive delay=later. Regularity score is a VitalChronicle heuristic. Unlabelled sessions excluded, not missing sleep data.",
        }

    def _tool_calculate_sleep_debt(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        series = self._series("sleep", right - timedelta(days=34), right)
        by_day = _daily(series["points"], "sum")
        recent = [
            by_day[key]
            for i in range(7)
            if (key := (right - timedelta(days=i)).isoformat()) in by_day
        ]
        baseline = [
            by_day[key]
            for i in range(7, 35)
            if (key := (right - timedelta(days=i)).isoformat()) in by_day
        ]
        need = (
            statistics.median(baseline)
            if baseline
            else (statistics.median(recent) if recent else None)
        )
        debt = None if need is None else sum(max(0.0, need - value) for value in recent)
        return {
            "baseline_sleep_hours": None if need is None else round(need, 2),
            "recent_mean_hours": round(statistics.fmean(recent), 2) if recent else None,
            "estimated_sleep_debt_hours_7d": None if debt is None else round(debt, 2),
            "recent_nights": len(recent),
            "baseline_nights": len(baseline),
            "confidence": _confidence(len(recent) + len(baseline), 35),
            "method": "Recorded daily sleep totals including additional sessions, compared with prior median (or recent median if no prior baseline). Missing days omitted, never extrapolated to seven nights. Not a clinical sleep-need diagnosis.",
        }

    def _recent_baseline(self, metric: str, right: date) -> dict[str, Any]:
        series = self._series(metric, right - timedelta(days=34), right)
        daily = _daily(series["points"], "mean")
        recent_days = {(right - timedelta(days=i)).isoformat() for i in range(7)}
        recent = [value for day, value in daily.items() if day in recent_days]
        baseline = [value for day, value in daily.items() if day not in recent_days]
        rmean = statistics.fmean(recent) if recent else None
        bmedian = statistics.median(baseline) if baseline else None
        delta = None if rmean is None or bmedian is None else rmean - bmedian
        pct = None if delta is None or abs(bmedian) < 1e-12 else delta / abs(bmedian) * 100
        return {
            "metric": metric,
            "as_of": right.isoformat(),
            "recent_period": {"start": (right - timedelta(days=6)).isoformat(), "end": right.isoformat()},
            "current_day_value": daily.get(right.isoformat()),
            "latest_observed_date": max(daily) if daily else None,
            "recent_mean": rmean,
            "baseline_median": bmedian,
            "delta": delta,
            "delta_percent": pct,
            "recent_days_observed": len(recent),
            "baseline_days_observed": len(baseline),
            "confidence": _confidence(len(recent) + len(baseline), 35),
            "unit": series["unit"],
        }

    def _tool_calculate_hrv_status(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        return self._recent_baseline("daily-heart-rate-variability", right)

    def _tool_calculate_rhr_status(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        return self._recent_baseline("daily-resting-heart-rate", right)

    @staticmethod
    def _component(
        recent: float | None, baseline: float | None, higher_good: bool, scale: float
    ) -> float | None:
        if recent is None or baseline is None or abs(baseline) < 1e-12:
            return None
        relative = (recent - baseline) / abs(baseline)
        signed = relative if higher_good else -relative
        return max(0.0, min(100.0, 70 + 30 * math.tanh(signed / scale)))

    def _readiness(self, right: date) -> dict[str, Any]:
        items = {
            "sleep": self._recent_baseline("sleep", right),
            "hrv": self._recent_baseline("daily-heart-rate-variability", right),
            "resting_heart_rate": self._recent_baseline("daily-resting-heart-rate", right),
        }
        weights = {"sleep": 0.40, "hrv": 0.35, "resting_heart_rate": 0.25}
        scores = {
            "sleep": self._component(
                items["sleep"]["recent_mean"], items["sleep"]["baseline_median"], True, 0.12
            ),
            "hrv": self._component(
                items["hrv"]["recent_mean"], items["hrv"]["baseline_median"], True, 0.20
            ),
            "resting_heart_rate": self._component(
                items["resting_heart_rate"]["recent_mean"],
                items["resting_heart_rate"]["baseline_median"],
                False,
                0.10,
            ),
        }
        available = {key: value for key, value in scores.items() if value is not None}
        weight_sum = sum(weights[key] for key in available)
        score = (
            None
            if not available
            else sum(value * weights[key] for key, value in available.items()) / weight_sum
        )
        observed = sum(int(item["recent_days_observed"]) for item in items.values())
        return {
            "score": None if score is None else round(score, 1),
            "label": None
            if score is None
            else ("high" if score >= 75 else "moderate" if score >= 50 else "low"),
            "confidence": _confidence(observed, 21, 3, len(available)),
            "components": {key: round(value, 1) for key, value in available.items()},
            "component_evidence": items,
            "method": "VitalChronicle composite of RECENT SEVEN-DAY MEANS versus prior baselines: sleep 40%, HRV 35%, resting HR 25%; missing components reweighted. Not a single-day measurement or Fitbit score.",
            "limitations": "Wearable signals cannot identify or rule out causes of fatigue. A reassuring score does not establish recovery or medical safety; user-reported fatigue remains relevant.",
        }

    def _tool_calculate_readiness(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        return self._readiness(right)

    def _tool_calculate_recovery_trend(self, args, **_):
        item = self._tool_calculate_readiness(args)
        return {
            "readiness": item,
            "changes_percent": {
                key: value.get("delta_percent") for key, value in item["component_evidence"].items()
            },
            "direction": None
            if item["score"] is None
            else (
                "favorable"
                if item["score"] >= 75
                else "mixed"
                if item["score"] >= 50
                else "reduced_recovery_signals"
            ),
        }

    def _tool_detect_recovery_anomaly(self, args, **_):
        item = self._tool_calculate_readiness(args)
        flags = []
        for key, evidence in item["component_evidence"].items():
            delta = evidence.get("delta_percent")
            adverse = delta is not None and (
                (key == "resting_heart_rate" and delta > 8)
                or (key != "resting_heart_rate" and delta < -12)
            )
            if adverse:
                flags.append({"metric": key, "delta_percent": round(delta, 1)})
        return {
            "flags": flags,
            "readiness": item,
            "interpretation": "Personal deviations; not a diagnosis or cause.",
        }

    def _cardio_daily(self, left: date, right: date) -> tuple[dict[str, float], str]:
        records = self._records("time-in-heart-rate-zone", left, right)
        zones = categorical_daily_points(records, "time-in-heart-rate-zone") if records else []
        if zones:
            weights = {
                "LIGHT": 1,
                "FAT_BURN": 1.2,
                "MODERATE": 2,
                "CARDIO": 3,
                "VIGOROUS": 4,
                "PEAK": 5,
            }
            return {
                _day(ts): sum(
                    float(minutes) * weights.get(str(zone).upper(), 2)
                    for zone, minutes in values.items()
                )
                for ts, values in zones
            }, "recorded heart-rate-zone minutes weighted by intensity"
        active = self._series("active-zone-minutes", left, right)
        daily = _daily(active["points"], "sum")
        if daily:
            return daily, "recorded active-zone minutes"
        exercise = self._series("exercise", left, right)
        return {
            day: hours * 60 for day, hours in _daily(exercise["points"], "sum").items()
        }, "exercise-duration fallback"

    def _load(self, right: date) -> dict[str, Any]:
        daily, method = self._cardio_daily(right - timedelta(days=34), right)
        acute_days = {(right - timedelta(days=i)).isoformat() for i in range(7)}
        chronic_days = {(right - timedelta(days=i)).isoformat() for i in range(7, 35)}
        acute_values = [value for day, value in daily.items() if day in acute_days]
        chronic_values = [value for day, value in daily.items() if day in chronic_days]
        acute_observed = len(acute_values)
        chronic_observed = len(chronic_values)
        acute_sufficient = acute_observed >= MIN_ACUTE_LOAD_OBSERVED_DAYS
        chronic_sufficient = chronic_observed >= MIN_CHRONIC_LOAD_OBSERVED_DAYS
        acute = (
            statistics.fmean(acute_values) * 7
            if acute_sufficient and acute_values
            else None
        )
        chronic = (
            statistics.fmean(chronic_values) * 7
            if chronic_sufficient and chronic_values
            else None
        )
        ratio = (
            None
            if acute is None or chronic is None or chronic <= 1e-9
            else acute / chronic
        )
        if not acute_sufficient:
            history_status = "insufficient_acute_history"
        elif not chronic_sufficient:
            history_status = "insufficient_chronic_history"
        else:
            history_status = "sufficient"
        return {
            "acute_7d": None if acute is None else round(acute, 2),
            "chronic_weekly_equivalent_28d": None if chronic is None else round(chronic, 2),
            "acute_chronic_ratio": None if ratio is None else round(ratio, 3),
            "acute_observed_total": round(sum(acute_values), 2),
            "chronic_observed_total": round(sum(chronic_values), 2),
            "acute_observed_days": acute_observed,
            "chronic_observed_days": chronic_observed,
            "acute_coverage": round(acute_observed / 7, 3),
            "chronic_coverage": round(chronic_observed / 28, 3),
            "acute_history_sufficient": acute_sufficient,
            "chronic_history_sufficient": chronic_sufficient,
            "history_status": history_status,
            "observed_days": len(daily),
            "method": method + "; weekly equivalents are normalized over observed days only when coverage is sufficient",
        }

    def _tool_calculate_cardio_load(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 7)
        daily, method = self._cardio_daily(left, right)
        expected = (right - left).days + 1
        return {
            "daily_load": [
                {"date": day, "load": round(value, 2)} for day, value in sorted(daily.items())
            ],
            "total_load": round(sum(daily.values()), 2),
            "observed_days": len(daily),
            "coverage": round(len(daily) / max(1, expected), 3),
            "confidence": _confidence(len(daily), expected),
            "unit": "VitalChronicle load points",
            "method": f"VitalChronicle load index from {method}; not Google's proprietary Cardio Load and not kcal.",
        }

    def _tool_calculate_acute_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        sufficient = bool(item["acute_history_sufficient"])
        return {
            "acute_load_7d": item["acute_7d"],
            "observed_total": item["acute_observed_total"],
            "observed_days": item["acute_observed_days"],
            "coverage": item["acute_coverage"],
            "status": "available" if sufficient else "insufficient_history",
            "confidence": _confidence(item["acute_observed_days"], 7) if sufficient else 0.0,
            "method": item["method"],
            "limitations": None if sufficient else "At least 5 observed days in the recent 7-day window are required; missing days are not treated as zero.",
        }

    def _tool_calculate_chronic_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        sufficient = bool(item["chronic_history_sufficient"])
        return {
            "chronic_load_weekly_equivalent_28d": item["chronic_weekly_equivalent_28d"],
            "observed_total": item["chronic_observed_total"],
            "observed_days": item["chronic_observed_days"],
            "coverage": item["chronic_coverage"],
            "status": "available" if sufficient else "insufficient_history",
            "confidence": _confidence(item["chronic_observed_days"], 28) if sufficient else 0.0,
            "method": item["method"],
            "limitations": None if sufficient else "At least 21 observed days in the prior 28-day window are required; missing days are not treated as zero.",
        }

    def _target(self, right: date) -> dict[str, Any]:
        load = self._load(right)
        readiness = self._readiness(right)
        chronic = load["chronic_weekly_equivalent_28d"]
        if chronic is None:
            return {
                "target_weekly_load": None,
                "current_acute_load": load["acute_7d"],
                "readiness": readiness,
                "status": "insufficient_history",
                "confidence": 0.0,
                "method": "VitalChronicle target load requires a usable prior 28-day chronic-load estimate.",
                "limitations": "Not enough observed chronic-load history; no target is estimated and missing days are not treated as zero.",
            }
        score = readiness["score"]
        factor = 1.0 if score is None else 0.70 + 0.006 * float(score)
        centre = float(chronic) * factor
        return {
            "target_weekly_load": {
                "lower": round(centre * 0.85, 2),
                "upper": round(centre * 1.15, 2),
            },
            "current_acute_load": load["acute_7d"],
            "readiness": readiness,
            "status": "available",
            "confidence": round(
                min(readiness["confidence"], _confidence(load["chronic_observed_days"], 28)), 3
            ),
            "method": "VitalChronicle heuristic: personal chronic load scaled by readiness with ±15% range; not Fitbit Target Load.",
            "limitations": None,
        }

    def _tool_calculate_target_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        return self._target(right)

    def _training(self, right: date) -> dict[str, Any]:
        load = self._load(right)
        readiness = self._readiness(right)
        ratio, score = load["acute_chronic_ratio"], readiness["score"]
        if ratio is None:
            status = "insufficient_history"
        elif ratio < 0.65:
            status = "reduced_load"
        elif ratio <= 1.15:
            status = "productive" if score is not None and score >= 75 else "maintaining"
        elif ratio <= 1.45:
            status = "increasing" if score is None or score >= 50 else "high_load_low_recovery"
        else:
            status = (
                "very_high_recent_load" if score is None or score >= 50 else "overreaching_signal"
            )
        return {
            "status": status,
            "load": load,
            "readiness": readiness,
            "method": "Descriptive personal load/recovery classification; not a diagnosis.",
        }

    def _tool_calculate_training_status(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        return self._training(right)

    def _tool_analyze_workout(self, args, **_):
        from .analysis import _find_named_value

        left, right = _bounds(args.get("start"), args.get("end"), 28)
        records = self._records("exercise", left, right)
        groups: dict[str, dict[str, Any]] = {}
        selected = str(args.get("exercise_type") or "").strip().upper()
        selected = {
            "CYCLING": "BIKING", "BIKE": "BIKING", "BICI": "BIKING",
            "BICICLETTA": "BIKING", "CICLISMO": "BIKING",
            "SWIM": "SWIMMING", "NUOTO": "SWIMMING",
        }.get(selected, selected)
        available_types: set[str] = set()
        selected_records = 0
        for record in records:
            kind = str(
                _find_named_value(record.get("payload") or {}, {"exercisetype", "type"})
                or "UNSPECIFIED"
            ).upper()
            available_types.add(kind)
            if selected and kind != selected:
                continue
            selected_records += 1
            entry = groups.setdefault(kind, {
                "sessions": 0, "hours": 0.0, "heart_rate_observed_sessions": 0,
                "heart_rate_observed_hours": 0.0, "_weighted_hr": 0.0,
            })
            hours = duration_hours(record)
            entry["sessions"] += 1
            entry["hours"] += hours or 0.0
            hr = coerce_number(_find_named_value(
                record.get("payload") or {}, {"averageheartratebeatsperminute"}
            ))
            if hr is not None and math.isfinite(hr) and hours and hours > 0:
                entry["heart_rate_observed_sessions"] += 1
                entry["heart_rate_observed_hours"] += hours
                entry["_weighted_hr"] += hr * hours
        for entry in groups.values():
            entry["average_heart_rate_bpm"] = (
                round(entry["_weighted_hr"] / entry["heart_rate_observed_hours"], 2)
                if entry["heart_rate_observed_hours"] else None
            )
            del entry["_weighted_hr"]
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "sessions": selected_records,
            "exercise_type_filter": selected or None,
            "available_recorded_types": sorted(available_types),
            "total_hours": round(sum(x["hours"] for x in groups.values()), 2),
            "by_type": groups,
            "method": "Recorded sessions; mean HR weighted by recorded session duration for sessions with mean HR. Not a mean of raw heart-rate samples. Missing HR is not inferred.",
        }

    def _tool_summarize_nutrition_logs(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        records = self._records("nutrition-log", left, right)
        energy, protein = [], []
        days = set()
        for record in records:
            item = record.get("payload", {}).get("nutritionLog", {})
            value = coerce_number(item.get("energy", {}).get("kcal"))
            if value is not None and math.isfinite(value):
                energy.append(value)
            nutrients = item.get("nutrients", [])
            for nutrient in nutrients if isinstance(nutrients, list) else []:
                if str(nutrient.get("nutrient", "")).upper() != "PROTEIN":
                    continue
                value = coerce_number(nutrient.get("quantity", {}).get("grams"))
                if value is not None and math.isfinite(value):
                    protein.append(value)
            day = _semantic_day(record, "nutrition-log")
            if day:
                days.add(day)
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "recorded_meals": len(records),
            "observed_days": len(days),
            "energy_kcal": round(sum(energy), 2) if energy else None,
            "protein_grams": round(sum(protein), 2) if protein else None,
            "meals_with_energy": len(energy),
            "protein_entries": len(protein),
            "method": "Actual nutrition-log entries only; excludes the food catalogue. Recorded totals are not complete dietary intake or estimated requirements; missing nutrients are unknown.",
        }

    def _vo2(self, left: date, right: date) -> dict[str, Any]:
        for metric in ("daily-vo2-max", "run-vo2-max", "vo2-max"):
            item = self._series(metric, left, right)
            if item["points"]:
                return item
        return self._series("daily-vo2-max", left, right)

    def _tool_estimate_cardio_fitness(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 90)
        item = self._vo2(left, right)
        summary = summarize_series(item["points"])
        return {
            "recorded": bool(item["points"]),
            "latest_vo2_max": summary.latest if summary else None,
            "unit": item["unit"],
            "method": "Recorded VO2-max only; no population estimate is invented when absent.",
        }

    def _tool_analyze_fitness_trend(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 90)
        item = self._vo2(left, right)
        summary = summarize_series(item["points"])
        return {
            "latest": summary.latest if summary else None,
            "trend_percent": summary.trend_percent if summary else None,
            "method": "Trend of recorded VO2-max/cardio-fitness evidence.",
        }

    def _tool_analyze_training_progression(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        ratio = item["acute_chronic_ratio"]
        return {
            "load": item,
            "progression_percent": None if ratio is None else round((ratio - 1) * 100, 1),
            "interpretation": "More recent load is not automatically better.",
        }

    def _tool_calculate_stress_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        readiness = self._readiness(right)
        load = self._load(right)
        recovery_strain = 50 if readiness["score"] is None else 100 - float(readiness["score"])
        ratio = load["acute_chronic_ratio"]
        load_strain = 50 if ratio is None else max(0.0, min(100.0, 50 + (float(ratio) - 1) * 70))
        return {
            "stress_load": round(0.65 * recovery_strain + 0.35 * load_strain, 1),
            "components": {
                "recovery_strain": round(recovery_strain, 1),
                "load_strain": round(load_strain, 1),
            },
            "method": "VitalChronicle physiological-strain heuristic; not a mental-stress diagnosis.",
        }

    def _resilience(self, right: date) -> dict[str, Any]:
        readiness = self._readiness(right)
        regularity = self._tool_calculate_sleep_regularity(
            {"start": (right - timedelta(days=41)).isoformat(), "end": right.isoformat()}
        )
        load = self._load(right)
        ratio = load["acute_chronic_ratio"]
        r = readiness["score"]
        s = regularity["regularity_score"]
        balance = (
            None
            if ratio is None
            else max(0.0, min(100.0, 100 - abs(float(ratio) - 1) * 70))
        )
        values = {
            "readiness": None if r is None else float(r),
            "sleep_regularity": None if s is None else float(s),
            "load_balance": balance,
        }
        weights = {"readiness": 0.50, "sleep_regularity": 0.25, "load_balance": 0.25}
        available = {key: value for key, value in values.items() if value is not None}
        weight_sum = sum(weights[key] for key in available)
        objective_score = (
            None
            if not available
            else sum(float(value) * weights[key] for key, value in available.items()) / weight_sum
        )
        subjective = [
            x
            for x in self.agent_store.user_model()
            if "load" in x["key"].lower() or "fatigue" in x["key"].lower()
        ]
        bonus = min(5.0, sum(float(x["confidence"]) * 1.5 for x in subjective))
        score = (
            None
            if objective_score is None
            else max(0.0, min(100.0, objective_score + bonus))
        )
        load_balance_label = (
            "unavailable_insufficient_history"
            if balance is None
            else "balanced"
            if balance >= 85
            else "moderately_unbalanced"
            if balance >= 50
            else "unbalanced"
        )
        return {
            "score": None if score is None else round(score, 1),
            "label": None
            if score is None
            else ("optimal" if score >= 75 else "balanced" if score >= 50 else "low"),
            "components": {
                "readiness": None if r is None else round(float(r), 1),
                "sleep_regularity": None if s is None else round(float(s), 1),
                "load_balance": None if balance is None else round(balance, 1),
            },
            "component_status": {
                "readiness": "available" if r is not None else "unavailable",
                "sleep_regularity": "available" if s is not None else "unavailable",
                "load_balance": "available" if balance is not None else load["history_status"],
            },
            "load_balance_label": load_balance_label,
            "load_history": {
                "acute_observed_days": load["acute_observed_days"],
                "chronic_observed_days": load["chronic_observed_days"],
                "acute_coverage": load["acute_coverage"],
                "chronic_coverage": load["chronic_coverage"],
                "acute_chronic_ratio": ratio,
            },
            "effective_weights": {
                key: round(weights[key] / weight_sum, 3) for key in available
            } if weight_sum else {},
            "subjective_personalisation_used": bool(subjective),
            "method": "VitalChronicle medium-term resilience heuristic: readiness 50%, sleep regularity 25%, load balance 25%; unavailable components are omitted and remaining weights are renormalized. Feedback cannot remove objective safety warnings.",
            "limitations": (
                "Load balance is unavailable because acute/chronic history is insufficient; it is not treated as neutral or zero."
                if balance is None
                else None
            ),
        }

    def _tool_calculate_resilience(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 42)
        return self._resilience(right)

    def _tool_analyze_stress_recovery_balance(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 42)
        stress = self._tool_calculate_stress_load({"end": right.isoformat()})
        resilience = self._resilience(right)
        return {
            "balance": round(float(resilience["score"]) - float(stress["stress_load"]), 1),
            "stress": stress,
            "resilience": resilience,
        }

    def _tool_compare_periods(self, args, **_):
        metric = str(args.get("metric") or "")
        windows = getattr(self, "_comparison_windows", None)
        if windows:
            supplied = {(str(args.get("start_a")), str(args.get("end_a"))),
                        (str(args.get("start_b")), str(args.get("end_b")))}
            expected = {tuple(windows["focus"]), tuple(windows["previous_week"])}
            if supplied != expected:
                raise ValueError(
                    f"For this today-versus-previous-week request use exactly these periods: {windows}. "
                    "The previous week is seven complete days excluding the focus day, not eight days."
                )
            args = {**args,
                    "start_a": windows["previous_week"][0], "end_a": windows["previous_week"][1],
                    "start_b": windows["focus"][0], "end_b": windows["focus"][1]}
        a = self._tool_get_daily_summary(
            {"metric": metric, "start": args.get("start_a"), "end": args.get("end_a")}
        )
        b = self._tool_get_daily_summary(
            {"metric": metric, "start": args.get("start_b"), "end": args.get("end_b")}
        )
        ma, mb = a["daily_baseline"]["mean"], b["daily_baseline"]["mean"]
        delta = None if ma is None or mb is None else float(mb) - float(ma)
        return {
            "metric": metric,
            "data_type": a["data_type"],
            "field": a["field"],
            "unit": a["unit"],
            "period_a": {**a["period"], "observed_days": a["observed_days"], "expected_days": a["expected_days"]},
            "period_b": {**b["period"], "observed_days": b["observed_days"], "expected_days": b["expected_days"]},
            "mean_a": ma,
            "mean_b": mb,
            "median_a": a["daily_baseline"]["median"],
            "median_b": b["daily_baseline"]["median"],
            "delta": delta,
            "delta_percent": None
            if delta is None or abs(float(ma)) < 1e-12
            else round(delta / abs(float(ma)) * 100, 2),
            "confidence": round(min(a["confidence"], b["confidence"]), 3),
            "limitations": "Observed dates only; gaps are not zero-filled.",
        }

    def _tool_detect_outliers(self, args, **_):
        item = self._tool_get_daily_summary(args)
        daily = {row["date"]: float(row["value"]) for row in item["daily"]}
        base = _baseline(list(daily.values()))
        median, mad = base["median"], base["mad"]
        outliers = []
        if median is not None and mad:
            sigma = 1.4826 * float(mad)
            outliers = [
                {"date": day, "value": value, "robust_z": round((value - float(median)) / sigma, 2)}
                for day, value in daily.items()
                if abs((value - float(median)) / sigma) >= 2.5
            ]
        return {
            "metric": item["metric"],
            "baseline": base,
            "outliers": outliers,
            "confidence": item["confidence"],
        }

    def _tool_calculate_correlations(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"), 60)
        offset = int(args.get("offset_days") or 0)
        if not -7 <= offset <= 7:
            raise ValueError("offset_days must be between -7 and 7")
        a = self._series(str(args.get("metric_a") or ""), left, right)
        b = self._series(str(args.get("metric_b") or ""), left + timedelta(days=offset), right + timedelta(days=offset))
        da = _daily(a["points"], "sum" if a["aggregation"] == "sum" else "mean")
        db = _daily(b["points"], "sum" if b["aggregation"] == "sum" else "mean")
        pairs = [(value, db[target]) for day, value in sorted(da.items())
                 if (target := (date.fromisoformat(day) + timedelta(days=offset)).isoformat()) in db]
        r = None
        if len(pairs) >= 4:
            xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
            sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
            if sx and sy:
                mx, my = statistics.fmean(xs), statistics.fmean(ys)
                r = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) * sx * sy)
        return {
            "metric_a": a["data_type"], "metric_b": b["data_type"],
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "offset_days": offset,
            "metric_a_observed_days": len(da), "metric_b_observed_days": len(db),
            "paired_days": len(pairs),
            "pearson_r": None if r is None else round(r, 4),
            "confidence": _confidence(len(pairs), (right - left).days + 1),
            "limitations": "Exploratory association; correlation is not causation or prediction.",
        }

    def _tool_detect_trends(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        item = self._series_info(str(args.get("metric") or ""), left, right)
        summary = item["summary"] or {}
        return {
            "metric": item["metric"],
            "trend_percent": summary.get("trend_percent"),
            "latest": summary.get("latest"),
            "confidence": item["confidence"],
            "coverage": item["coverage"],
        }

    def _tool_recommend_daily_activity(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        target = self._target(right)
        training = self._training(right)
        score = target["readiness"]["score"]
        if score is None or score < 45:
            suggestion = "favor recovery, mobility, walking or another easy session"
        elif score < 70:
            suggestion = "a moderate session is reasonable if it matches how you feel"
        else:
            suggestion = "a moderate-to-hard session may fit the current recovery evidence if it matches your plan"
        return {
            "suggestion": suggestion,
            "readiness": target["readiness"],
            "training_status": training["status"],
            "target_load": target["target_weekly_load"],
            "safety": "General wearable-data coaching, not medical clearance; subjective wellbeing never overrides concerning symptoms or professional restrictions.",
        }

    def _tool_recommend_workout(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        readiness = self._readiness(right)
        score = readiness["score"]
        intensity = (
            "easy"
            if score is None or score < 50
            else "moderate"
            if score < 75
            else "moderate-to-hard"
        )
        preferred = str(args.get("preferred_activity") or "").strip()
        return {
            "activity": preferred
            or ("walking / mobility" if intensity == "easy" else "cycling / running / strength"),
            "intensity": intensity,
            "duration_minutes": int(args.get("available_minutes") or 45),
            "goal": str(args.get("goal") or "general fitness"),
            "readiness": readiness,
            "safety": "Stop/adjust for pain, illness or unusual symptoms; this is not medical clearance.",
        }

    def _tool_search_tool_registry(self, args, **_):
        candidate = {
            "name": "candidate",
            "capability": str(args.get("capability") or ""),
            "description": str(args.get("description") or ""),
            "parameters": {},
        }
        matches = self.agent_store.find_similar_tools(candidate, limit=8, include_superseded=True)
        return {
            "matches": [
                {
                    "name": x["name"],
                    "kind": x["kind"],
                    "status": x["status"],
                    "capability": x["capability"],
                    "similarity": x["similarity"],
                    "replacement": x.get("replacement"),
                }
                for x in matches
            ],
            "rule": "Reuse or compose an existing capability before creating a tool.",
        }

    @staticmethod
    def validate_pipeline(pipeline: Any) -> list[dict[str, Any]]:
        if not isinstance(pipeline, list) or not pipeline or len(pipeline) > MAX_LEARNED_STEPS:
            raise ValueError(f"Pipeline must contain 1..{MAX_LEARNED_STEPS} steps")
        allowed_fields = {
            "op",
            "as",
            "metric",
            "source",
            "days",
            "aggregation",
            "left",
            "right",
            "threshold",
            "numerator",
            "denominator",
            "fields",
        }
        result = []
        for index, step in enumerate(pipeline):
            if not isinstance(step, dict) or str(step.get("op") or "") not in ALLOWED_DSL_OPS:
                raise ValueError(f"Unsafe or unknown pipeline operation at step {index + 1}")
            result.append({str(k): v for k, v in step.items() if k in allowed_fields})
        if result[-1]["op"] != "return":
            result.append({"op": "return"})
        return result

    def _tool_create_learned_tool(self, args, **_):
        name = str(args.get("name") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name):
            raise ValueError("Learned tool name must be snake_case")
        pipeline = self.validate_pipeline(args.get("pipeline"))
        for step in pipeline:
            if step["op"] == "load_series" and not step.get("metric"):
                raise ValueError("load_series requires metric")
        spec = {
            "name": name,
            "description": str(args.get("description") or ""),
            "capability": str(args.get("capability") or name),
            "parameters": args.get("parameters")
            if isinstance(args.get("parameters"), dict)
            else _period(),
            "pipeline": pipeline,
            "dependencies": [x.get("metric") for x in pipeline if x.get("metric")],
            "confidence": 0.65,
        }
        result = self.agent_store.add_learned_tool(spec)
        return {
            "status": result["status"],
            "tool": result.get("tool"),
            "validation": "safe declarative operations only",
        }

    def _tool_record_self_report(self, args, *, thread_id=None, **_):
        item = self.agent_store.record_self_report(
            str(args.get("statement") or ""),
            category=str(args.get("category") or "wellbeing"),
            thread_id=thread_id,
            observed_at=str(args.get("observed_at") or "") or None,
            intensity=args.get("intensity")
            if isinstance(args.get("intensity"), (int, float))
            else None,
            context=args.get("context") if isinstance(args.get("context"), dict) else {},
        )
        return {
            "stored": bool(item),
            "report": item,
            "rule": "A single subjective report is a dated event, not a stable learned association.",
        }

    def _tool_get_recent_self_reports(self, args, **_):
        return {
            "reports": self.agent_store.recent_self_reports(
                days=max(1, min(3650, int(args.get("days") or 30))),
                limit=max(1, min(200, int(args.get("limit") or 50))),
                category=str(args.get("category") or "").strip() or None,
            ),
            "rule": "Use recent self-reports as dated subjective context; do not treat them as diagnoses or stable traits.",
        }

    def _tool_get_user_model(self, args, **_):
        return {
            "entries": self.agent_store.user_model(),
            "rule": "Personal associations are context, not medical safety facts.",
        }

    def _tool_ask_user_feedback(self, args, *, thread_id=None, **_):
        item = self.agent_store.ask_feedback(
            str(args.get("question") or ""),
            thread_id=thread_id,
            reason=str(args.get("reason") or ""),
            learning_key=str(args.get("learning_key") or ""),
            context=args.get("context") if isinstance(args.get("context"), dict) else {},
        )
        return {
            "queued": bool(item),
            "feedback_id": item.get("feedback_id"),
            "question": item.get("question"),
            "rule": "Subjective wellbeing never proves physiological safety.",
        }

    def _tool_create_monitoring_rule(self, args, **_):
        return self.agent_store.create_monitoring_rule(args)

    def _tool_record_monitoring_observation(self, args, **_):
        return {
            "stored": True,
            "observation": self.agent_store.record_monitoring_observation(
                str(args.get("name") or ""),
                str(args.get("statement") or ""),
                observed_at=str(args.get("observed_at") or "") or None,
                context=args.get("context") if isinstance(args.get("context"), dict) else {},
            ),
        }

    def _tool_list_monitoring_rules(self, args, **_):
        return {
            "monitoring_rules": self.agent_store.list_monitoring_rules(),
            "rule": "These are in-app check-ins, not learned analysis tools or background notifications.",
        }

    def _tool_get_user_feedback_history(self, args, **_):
        limit = max(1, min(50, int(args.get("limit") or 20)))
        with self.agent_store._connect() as db:
            rows = db.execute(
                "SELECT feedback_id FROM feedback WHERE answered_at IS NOT NULL ORDER BY answered_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {"feedback": [self.agent_store.feedback(str(row["feedback_id"])) for row in rows]}

    def _tool_learn_user_association(self, args, **_):
        statement = str(args.get("statement") or "").strip()
        if any(
            term in statement.lower()
            for term in ("medically safe", "safe for you", "no risk", "cannot harm")
        ):
            raise ValueError("Subjective personalisation cannot become a medical safety claim")
        evidence = dict(args.get("evidence") if isinstance(args.get("evidence"), dict) else {})
        temporal_scope = str(args.get("temporal_scope") or "").strip().lower()
        if temporal_scope:
            evidence["temporal_scope"] = temporal_scope
        if isinstance(args.get("ttl_days"), int):
            evidence["ttl_days"] = int(args["ttl_days"])
        return {
            "learned": self.agent_store.learn_user_model(
                str(args.get("key") or ""),
                statement,
                evidence=evidence,
                source="agent",
            )
        }

    @staticmethod
    def _resolve(value: Any, args: dict[str, Any]) -> Any:
        return args.get(value[1:]) if isinstance(value, str) and value.startswith("$") else value

    def _run_learned(self, item: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        pipeline = self.validate_pipeline(item["pipeline"])
        env: dict[str, Any] = {}
        last: Any = None
        default_left, default_right = _bounds(args.get("start"), args.get("end"), 60)
        for index, step in enumerate(pipeline):
            op = step["op"]
            alias = str(step.get("as") or f"step_{index + 1}")
            source = env.get(str(step.get("source") or ""), last)
            if op == "load_series":
                left, right = _bounds(
                    self._resolve(step.get("left"), args) or default_left,
                    self._resolve(step.get("right"), args) or default_right,
                )
                last = self._series(str(self._resolve(step.get("metric"), args) or ""), left, right)
            elif op == "daily":
                last = (
                    _daily((source or {}).get("points", []), str(step.get("aggregation") or "mean"))
                    if isinstance(source, dict)
                    else {}
                )
            elif op == "window":
                cutoff = default_right - timedelta(days=max(1, int(step.get("days") or 7)) - 1)
                last = (
                    {
                        day: value
                        for day, value in (source or {}).items()
                        if _parse_date(day) and _parse_date(day) >= cutoff
                    }
                    if isinstance(source, dict)
                    else source
                )
            elif op == "summarize":
                last = (
                    _baseline([float(v) for v in source.values()])
                    if isinstance(source, dict)
                    else _baseline([])
                )
            elif op == "trend":
                values = list(source.values()) if isinstance(source, dict) else []
                last = {
                    "trend_percent": None
                    if len(values) < 2 or abs(values[0]) < 1e-12
                    else (values[-1] - values[0]) / abs(values[0]) * 100
                }
            elif op == "compare":
                a, b = env.get(str(step.get("left") or "")), env.get(str(step.get("right") or ""))
                av = list(a.values()) if isinstance(a, dict) else []
                bv = list(b.values()) if isinstance(b, dict) else []
                ma, mb = (
                    (statistics.fmean(av) if av else None),
                    (statistics.fmean(bv) if bv else None),
                )
                last = {
                    "mean_a": ma,
                    "mean_b": mb,
                    "delta": None if ma is None or mb is None else mb - ma,
                }
            elif op == "correlate":
                a, b = env.get(str(step.get("left") or "")), env.get(str(step.get("right") or ""))
                common = (
                    sorted(set(a or {}) & set(b or {}))
                    if isinstance(a, dict) and isinstance(b, dict)
                    else []
                )
                pairs = [(a[x], b[x]) for x in common]
                r = None
                if len(pairs) >= 4:
                    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
                    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
                    if sx and sy:
                        mx, my = statistics.fmean(xs), statistics.fmean(ys)
                        r = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) * sx * sy)
                last = {"paired_days": len(pairs), "pearson_r": r}
            elif op in {"count_above", "count_below"}:
                threshold = float(self._resolve(step.get("threshold"), args) or 0)
                values = list(source.values()) if isinstance(source, dict) else []
                last = {
                    "count": sum(
                        v > threshold if op == "count_above" else v < threshold for v in values
                    ),
                    "total": len(values),
                }
            elif op == "ratio":
                n, d = (
                    env.get(str(step.get("numerator") or "")),
                    env.get(str(step.get("denominator") or "")),
                )
                last = (
                    None
                    if not isinstance(n, (int, float))
                    or not isinstance(d, (int, float))
                    or abs(d) < 1e-12
                    else n / d
                )
            elif op == "return":
                last = source
            env[alias] = last
        return {
            "tool": item["name"],
            "result": last,
            "pipeline_steps": len(pipeline),
            "confidence": item["confidence"],
            "method": "Safe learned declarative tool; no arbitrary code, terminal, filesystem, browser or network access.",
        }
