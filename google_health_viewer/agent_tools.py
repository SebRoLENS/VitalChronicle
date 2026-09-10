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

MAX_SERIES_POINTS = 360
MAX_LEARNED_STEPS = 12
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
_METRIC = {"type": "string", "description": "VitalChronicle data type or data_type:field."}
_PERIOD = {"start": _DATE, "end": _DATE}


def _period(metric: bool = False) -> dict[str, Any]:
    props = ({"metric": _METRIC} if metric else {}) | _PERIOD
    return _obj(props, ("metric",) if metric else ())


# 34 deterministic health tools + 6 agent/personalisation tools.
_TOOL_ROWS = (
    (
        "get_available_metrics",
        "List available local health metrics and record counts.",
        "data.available_metrics",
        _obj({}),
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
    ("get_sleep_sessions", "Summarize recorded sleep sessions.", "sleep.sessions", _period()),
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
        "Calculate personal sleep timing/duration regularity.",
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
        "Summarize workout frequency, duration and recorded types.",
        "training.workout_analysis",
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
        "Calculate exploratory same-day Pearson association between two metrics.",
        "analysis.correlations",
        _obj({"metric_a": _METRIC, "metric_b": _METRIC, **_PERIOD}, ("metric_a", "metric_b")),
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
        return self.health_store.list_records(
            data_type,
            start=left.isoformat(),
            end=(right + timedelta(days=1)).isoformat(),
            limit=200000,
        )

    def _series(self, metric: str, left: date, right: date) -> dict[str, Any]:
        raw = str(metric or "").strip()
        data_type, _, explicit = raw.partition(":")
        data_type = _ALIASES.get(data_type, data_type)
        records = self._records(data_type, left, right)
        metrics = available_metrics(records, data_type) if records else []
        field = explicit if explicit in metrics else (metrics[0] if metrics else None)
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
        points = display_points(raw_points(records, field), profile)
        return {
            "metric": raw,
            "data_type": data_type,
            "field": field,
            "points": [
                (float(ts), float(value)) for ts, value in points if math.isfinite(float(value))
            ],
            "unit": profile.unit,
            "aggregation": profile.aggregation,
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
                "mean": summary.mean,
                "median": summary.median,
                "minimum": summary.minimum,
                "maximum": summary.maximum,
                "trend_percent": summary.trend_percent,
                "baseline_low": summary.baseline_low,
                "baseline_high": summary.baseline_high,
                "anomaly_count": summary.anomaly_count,
            },
            "method": "Deterministic local values; missing observations are omitted, never zero-filled.",
        }
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
        for data_type, count in sorted(self.health_store.counts().items()):
            records = self.health_store.list_records(data_type, limit=min(count, 2000), newest=True)
            rows.append(
                {
                    "data_type": data_type,
                    "records": count,
                    "metrics": available_metrics(records, data_type)[:12],
                }
            )
        return {"data_types": rows, "count": len(rows)}

    def _tool_get_metric_series(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        return self._series_info(str(args.get("metric") or ""), left, right, True)

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
        }

    def _sleep_rows(
        self, left: date, right: date
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records = self._records("sleep", left, right)
        rows = []
        for record in records:
            hours = duration_hours(record)
            raw_end = record.get("end_time") or record.get("start_time")
            try:
                end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00")).astimezone()
            except (TypeError, ValueError):
                continue
            if hours is not None:
                rows.append(
                    {
                        "date": end.date().isoformat(),
                        "hours": round(hours, 3),
                        "start_time": record.get("start_time"),
                        "end_time": record.get("end_time"),
                    }
                )
        return records, rows

    def _tool_get_sleep_sessions(self, args, **_):
        left, right = _bounds(args.get("start"), args.get("end"))
        records, rows = self._sleep_rows(left, right)
        expected = (right - left).days + 1
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "sessions": rows[-60:],
            "session_count": len(rows),
            "sessions_with_stages": len(sleep_stage_points(records)),
            "coverage": round(len({row["date"] for row in rows}) / max(1, expected), 3),
            "confidence": _confidence(len({row["date"] for row in rows}), expected),
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
        starts, durations = [], []
        for row in rows:
            try:
                dt = datetime.fromisoformat(
                    str(row["start_time"]).replace("Z", "+00:00")
                ).astimezone()
            except (TypeError, ValueError):
                continue
            minute = dt.hour * 60 + dt.minute + (1440 if dt.hour < 12 else 0)
            starts.append(float(minute))
            durations.append(float(row["hours"]))
        start_sd = statistics.pstdev(starts) if len(starts) > 1 else None
        duration_sd = statistics.pstdev(durations) if len(durations) > 1 else None
        score = (
            None
            if start_sd is None or duration_sd is None
            else max(0.0, min(100.0, 100 - min(60.0, start_sd / 2) - min(40.0, duration_sd * 18)))
        )
        return {
            "regularity_score": None if score is None else round(score, 1),
            "sleep_start_sd_minutes": None if start_sd is None else round(start_sd, 1),
            "duration_sd_hours": None if duration_sd is None else round(duration_sd, 2),
            "sessions": len(durations),
            "confidence": _confidence(len(durations), (right - left).days + 1),
            "method": "VitalChronicle heuristic from personal sleep-start and duration variability.",
        }

    def _tool_calculate_sleep_debt(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        _records, rows = self._sleep_rows(right - timedelta(days=34), right)
        by_day = {row["date"]: float(row["hours"]) for row in rows}
        recent = [
            by_day[key]
            for i in range(7)
            if (key := (right - timedelta(days=i)).isoformat()) in by_day
        ]
        baseline = [
            by_day[key]
            for i in range(8, 35)
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
            "method": "Estimate against the user's prior median sleep duration; not a clinical sleep-need diagnosis.",
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
            "method": "VitalChronicle personal-baseline composite: sleep 40%, HRV 35%, resting HR 25%; missing components are reweighted. Not Fitbit's proprietary score.",
            "limitations": "Subjective feedback personalizes tolerance but cannot establish medical safety.",
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
        acute = sum(value for day, value in daily.items() if day in acute_days)
        chronic = sum(value for day, value in daily.items() if day in chronic_days) / 4
        return {
            "acute_7d": round(acute, 2),
            "chronic_weekly_equivalent_28d": round(chronic, 2),
            "acute_chronic_ratio": None if chronic <= 1e-9 else round(acute / chronic, 3),
            "observed_days": len(daily),
            "method": method,
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
        return {
            "acute_load_7d": item["acute_7d"],
            "confidence": _confidence(item["observed_days"], 35),
            "method": item["method"],
        }

    def _tool_calculate_chronic_load(self, args, **_):
        _left, right = _bounds(args.get("start"), args.get("end"), 35)
        item = self._load(right)
        return {
            "chronic_load_weekly_equivalent_28d": item["chronic_weekly_equivalent_28d"],
            "confidence": _confidence(item["observed_days"], 35),
            "method": item["method"],
        }

    def _target(self, right: date) -> dict[str, Any]:
        load = self._load(right)
        readiness = self._readiness(right)
        chronic = float(load["chronic_weekly_equivalent_28d"])
        score = readiness["score"]
        factor = 1.0 if score is None else 0.70 + 0.006 * float(score)
        centre = chronic * factor
        return {
            "target_weekly_load": {
                "lower": round(centre * 0.85, 2),
                "upper": round(centre * 1.15, 2),
            },
            "current_acute_load": load["acute_7d"],
            "readiness": readiness,
            "confidence": round(
                min(readiness["confidence"], _confidence(load["observed_days"], 35)), 3
            ),
            "method": "VitalChronicle heuristic: personal chronic load scaled by readiness with ±15% range; not Fitbit Target Load.",
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
        groups: dict[str, dict[str, float]] = {}
        for record in records:
            kind = str(
                _find_named_value(record.get("payload") or {}, {"exercisetype", "type"})
                or "UNSPECIFIED"
            ).upper()
            entry = groups.setdefault(kind, {"sessions": 0, "hours": 0.0})
            entry["sessions"] += 1
            entry["hours"] += duration_hours(record) or 0.0
        return {
            "sessions": len(records),
            "total_hours": round(sum(x["hours"] for x in groups.values()), 2),
            "by_type": groups,
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
        r = float(readiness["score"] if readiness["score"] is not None else 50)
        s = float(
            regularity["regularity_score"] if regularity["regularity_score"] is not None else 50
        )
        balance = 70 if ratio is None else max(0.0, min(100.0, 100 - abs(float(ratio) - 1) * 70))
        subjective = [
            x
            for x in self.agent_store.user_model()
            if "load" in x["key"].lower() or "fatigue" in x["key"].lower()
        ]
        bonus = min(5.0, sum(float(x["confidence"]) * 1.5 for x in subjective))
        score = max(0.0, min(100.0, 0.50 * r + 0.25 * s + 0.25 * balance + bonus))
        return {
            "score": round(score, 1),
            "label": "optimal" if score >= 75 else "balanced" if score >= 50 else "low",
            "components": {
                "readiness": round(r, 1),
                "sleep_regularity": round(s, 1),
                "load_balance": round(balance, 1),
            },
            "subjective_personalisation_used": bool(subjective),
            "method": "VitalChronicle medium-term resilience heuristic; feedback cannot remove objective safety warnings.",
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
            "mean_a": ma,
            "mean_b": mb,
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
        a = self._series(str(args.get("metric_a") or ""), left, right)
        b = self._series(str(args.get("metric_b") or ""), left, right)
        da = _daily(a["points"], "sum" if a["aggregation"] == "sum" else "mean")
        db = _daily(b["points"], "sum" if b["aggregation"] == "sum" else "mean")
        common = sorted(set(da) & set(db))
        pairs = [(da[x], db[x]) for x in common]
        r = None
        if len(pairs) >= 4:
            xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
            sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
            if sx and sy:
                mx, my = statistics.fmean(xs), statistics.fmean(ys)
                r = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) * sx * sy)
        return {
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
        return {
            "learned": self.agent_store.learn_user_model(
                str(args.get("key") or ""),
                statement,
                evidence=args.get("evidence") if isinstance(args.get("evidence"), dict) else {},
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
