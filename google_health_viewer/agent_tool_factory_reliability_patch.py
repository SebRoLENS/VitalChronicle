from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

_INSTALLED = False
_BUILTIN_NAME = "analyze_metric_threshold_responses"
_BUILTIN_CAPABILITY = "analysis.composed.personal_baseline.threshold_frequency"

_BUILTIN_SPEC = {
    "name": _BUILTIN_NAME,
    "description": (
        "Deterministically find dates when one metric crosses a percentage threshold relative to "
        "its personal baseline, then align one or more response metrics by exact calendar date (or "
        "a bounded day offset). Use this instead of manually joining independent raw series."
    ),
    "capability": _BUILTIN_CAPABILITY,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start": {"type": "string", "description": "Local start date YYYY-MM-DD."},
            "end": {"type": "string", "description": "Local end date YYYY-MM-DD."},
            "event_metric": {
                "type": "string",
                "description": "Metric whose personal baseline and threshold define the event dates.",
            },
            "response_metrics": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string"},
                "description": "Metrics aligned to each selected event date.",
            },
            "percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 500,
                "default": 30,
                "description": "Percentage above/below the event baseline.",
            },
            "direction": {
                "type": "string",
                "enum": ["above", "below"],
                "default": "above",
            },
            "baseline_field": {
                "type": "string",
                "enum": ["median", "mean"],
                "default": "median",
            },
            "response_offset_days": {
                "type": "integer",
                "minimum": -7,
                "maximum": 7,
                "default": 0,
                "description": "Calendar-day offset applied only to response lookup dates.",
            },
        },
        "required": ["start", "end", "event_metric", "response_metrics"],
    },
}

_THRESHOLD_PIPELINE_EXAMPLE = [
    {
        "op": "call_tool",
        "tool": _BUILTIN_NAME,
        "arguments": {
            "start": "$start",
            "end": "$end",
            "event_metric": "active-minutes",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "percent": 30,
            "direction": "above",
            "baseline_field": "median",
            "response_offset_days": 0,
        },
        "as": "analysis",
    },
    {"op": "return", "source": "analysis"},
]


def _daily_series(executor: Any, metric: str, left: date, right: date) -> tuple[dict[str, float], dict[str, Any]]:
    from . import agent_tool_factory as factory

    raw = executor._series(metric, left, right)
    points = raw.get("points", []) if isinstance(raw, dict) else []
    aggregation = str(raw.get("aggregation") or "mean") if isinstance(raw, dict) else "mean"
    daily = factory.base._daily(points, "sum" if aggregation == "sum" else "mean")
    return daily, raw if isinstance(raw, dict) else {}


def _tool_analyze_metric_threshold_responses(self: Any, args: dict[str, Any], **_: Any) -> dict[str, Any]:
    from . import agent_tool_factory as factory

    left, right = factory.base._bounds(args.get("start"), args.get("end"), 60)
    event_metric = str(args.get("event_metric") or "").strip()
    response_metrics = [
        str(item).strip()
        for item in (args.get("response_metrics") or [])
        if str(item).strip()
    ][:8]
    if not event_metric:
        return {"status": "invalid_arguments", "error": "event_metric is required"}
    if not response_metrics:
        return {"status": "invalid_arguments", "error": "response_metrics must contain at least one metric"}

    try:
        percent = float(args.get("percent", 30))
    except (TypeError, ValueError):
        percent = 30.0
    percent = max(0.0, min(500.0, percent))
    direction = str(args.get("direction") or "above").strip().lower()
    if direction not in {"above", "below"}:
        direction = "above"
    baseline_field = str(args.get("baseline_field") or "median").strip().lower()
    if baseline_field not in {"median", "mean"}:
        baseline_field = "median"
    try:
        response_offset_days = int(args.get("response_offset_days", 0))
    except (TypeError, ValueError):
        response_offset_days = 0
    response_offset_days = max(-7, min(7, response_offset_days))

    event_series, event_raw = _daily_series(self, event_metric, left, right)
    event_values = list(event_series.values())
    if not event_values:
        return {
            "status": "no_event_data",
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "event_metric": event_metric,
            "response_metrics": response_metrics,
            "event_days": [],
            "event_day_count": 0,
            "limitations": "The requested event metric has no deterministic daily observations in this period.",
        }

    baseline = (
        statistics.median(event_values)
        if baseline_field == "median"
        else statistics.fmean(event_values)
    )
    threshold = (
        baseline * (1.0 + percent / 100.0)
        if direction == "above"
        else baseline * (1.0 - percent / 100.0)
    )
    selected = {
        day: value
        for day, value in event_series.items()
        if (value > threshold if direction == "above" else value < threshold)
    }

    response_series: dict[str, dict[str, float]] = {}
    response_meta: dict[str, dict[str, Any]] = {}
    for metric in response_metrics:
        series, raw = _daily_series(self, metric, left, right)
        response_series[metric] = series
        response_meta[metric] = {
            "observed_days": len(series),
            "unit": raw.get("unit"),
            "field": raw.get("field"),
            "date_semantics": raw.get("date_semantics"),
        }

    rows: list[dict[str, Any]] = []
    matched_counts = {metric: 0 for metric in response_metrics}
    for event_day, event_value in sorted(selected.items()):
        response_day = (date.fromisoformat(event_day) + timedelta(days=response_offset_days)).isoformat()
        responses: dict[str, float | None] = {}
        for metric in response_metrics:
            value = response_series[metric].get(response_day)
            if value is not None and math.isfinite(float(value)):
                matched_counts[metric] += 1
                responses[metric] = float(value)
            else:
                responses[metric] = None
        rows.append(
            {
                "event_date": event_day,
                "response_date": response_day,
                "event_value": float(event_value),
                "responses": responses,
            }
        )

    return {
        "status": "ok",
        "period": {"start": left.isoformat(), "end": right.isoformat()},
        "event_metric": event_metric,
        "event_field": event_raw.get("field"),
        "event_unit": event_raw.get("unit"),
        "event_observed_days": len(event_series),
        "baseline_field": baseline_field,
        "event_baseline": round(float(baseline), 6),
        "direction": direction,
        "percent": percent,
        "threshold": round(float(threshold), 6),
        "event_day_count": len(rows),
        "response_offset_days": response_offset_days,
        "event_days": rows,
        "responses": {
            metric: {
                **response_meta[metric],
                "matched_event_days": matched_counts[metric],
                "missing_event_days": len(rows) - matched_counts[metric],
            }
            for metric in response_metrics
        },
        "method": (
            "Deterministic daily aggregation followed by a personal-baseline threshold and an exact "
            "calendar-date join. Missing response dates remain null and are never shifted, inferred, "
            "or zero-filled."
        ),
    }


def install_agent_tool_factory_reliability_patch() -> None:
    """Install a deterministic primitive for cross-metric threshold analyses.

    This deliberately fixes the failure mode where the model tries to build a long learned-tool
    pipeline from raw series, then manually reconstructs date/value pairs after validation fails.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    factory.SAFE_COMPOSABLE_TOOLS.add(_BUILTIN_NAME)
    setattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{_BUILTIN_NAME}",
        _tool_analyze_metric_threshold_responses,
    )

    original_init = factory.EnhancedSafeToolExecutor.__init__
    original_tool_schemas = factory.EnhancedSafeToolExecutor.tool_schemas
    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.agent_store.sync_builtin_tools([_BUILTIN_SPEC])

    def patched_tool_schemas(self: Any) -> list[dict[str, Any]]:
        schemas = original_tool_schemas(self)
        if not any(
            isinstance(item, dict)
            and isinstance(item.get("function"), dict)
            and item["function"].get("name") == _BUILTIN_NAME
            for item in schemas
        ):
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": _BUILTIN_NAME,
                        "description": _BUILTIN_SPEC["description"],
                        "parameters": _BUILTIN_SPEC["parameters"],
                    },
                }
            )
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict) or function.get("name") != "create_learned_tool":
                continue
            description = str(function.get("description") or "")
            hint = (
                " For requests that select days above/below a personal baseline and compare one or "
                "more other metrics on those dates, compose the learned tool with "
                f"{_BUILTIN_NAME} rather than manually chaining load_series/correlate."
            )
            if _BUILTIN_NAME not in description:
                function["description"] = description + hint
        return schemas

    def patched_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        result = original_create(self, args, **kwargs)
        if isinstance(result, dict) and result.get("status") in {"invalid_pipeline", "invalid_spec"}:
            result = dict(result)
            result["canonical_threshold_response_pipeline"] = _THRESHOLD_PIPELINE_EXAMPLE
            result["threshold_response_instruction"] = (
                "If the request is a personal-baseline threshold followed by same-day/offset response "
                "metrics, replace the manual raw-series pipeline with the canonical two-step pipeline "
                f"using {_BUILTIN_NAME}. This avoids date misalignment and unsupported pseudo-variables."
            )
        return result

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    factory.EnhancedSafeToolExecutor.tool_schemas = patched_tool_schemas
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = patched_create

    pipeline_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get("pipeline")
    if isinstance(pipeline_schema, dict):
        description = str(pipeline_schema.get("description") or "")
        extra = (
            " For cross-metric personal-baseline threshold requests, prefer the canonical "
            f"{_BUILTIN_NAME} call_tool recipe; do not manually join independent date series."
        )
        if _BUILTIN_NAME not in description:
            pipeline_schema["description"] = description + extra
        examples = pipeline_schema.setdefault("examples", [])
        if isinstance(examples, list) and _THRESHOLD_PIPELINE_EXAMPLE not in examples:
            examples.append(_THRESHOLD_PIPELINE_EXAMPLE)

    _INSTALLED = True
