from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

_INSTALLED = False
_RUNTIME_INSTALLED = False
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
            "start": {"type": "string"},
            "end": {"type": "string"},
            "event_metric": {"type": "string"},
            "response_metrics": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string"},
            },
            "percent": {"type": "number", "minimum": 0, "maximum": 500, "default": 30},
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

# Tools that can return enough longitudinal detail that they should be evaluated
# alone on local hardware. The runtime keeps at most one of these calls per turn.
_HEAVY_TOOLS = {
    "get_available_metrics",
    "get_metric_series",
    "get_daily_summary",
    "get_sleep_sessions",
    "get_sleep_stage_series",
    "analyze_sleep_stages",
    "analyze_awakenings",
    "calculate_sleep_regularity",
    "calculate_sleep_debt",
    "calculate_cardio_load",
    "analyze_workout",
    "analyze_training_progression",
    _BUILTIN_NAME,
}


def _daily_series(
    executor: Any,
    metric: str,
    left: date,
    right: date,
) -> tuple[dict[str, float], dict[str, Any]]:
    from . import agent_tool_factory as factory

    raw = executor._series(metric, left, right)
    points = raw.get("points", []) if isinstance(raw, dict) else []
    aggregation = str(raw.get("aggregation") or "mean") if isinstance(raw, dict) else "mean"
    daily = factory.base._daily(points, "sum" if aggregation == "sum" else "mean")
    return daily, raw if isinstance(raw, dict) else {}


def _tool_analyze_metric_threshold_responses(
    self: Any,
    args: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    from . import agent_tool_factory as factory

    left, right = factory.base._bounds(args.get("start"), args.get("end"), 60)
    event_metric = str(args.get("event_metric") or "").strip()
    response_metrics = [
        str(item).strip()
        for item in (args.get("response_metrics") or [])
        if str(item).strip()
    ][:8]
    if not event_metric or not response_metrics:
        return {
            "status": "invalid_arguments",
            "error": "event_metric and response_metrics are required",
        }

    try:
        percent = float(args.get("percent", 30))
    except (TypeError, ValueError):
        percent = 30.0
    percent = max(0.0, min(500.0, percent))

    direction = str(args.get("direction") or "above")
    if direction not in {"above", "below"}:
        direction = "above"

    baseline_field = str(args.get("baseline_field") or "median")
    if baseline_field not in {"median", "mean"}:
        baseline_field = "median"

    try:
        offset = int(args.get("response_offset_days", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(-7, min(7, offset))

    event_series, event_raw = _daily_series(self, event_metric, left, right)
    values = list(event_series.values())
    if not values:
        return {
            "status": "no_event_data",
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "event_metric": event_metric,
            "event_days": [],
            "event_day_count": 0,
        }

    baseline = (
        statistics.median(values)
        if baseline_field == "median"
        else statistics.fmean(values)
    )
    threshold = (
        baseline * (1 + percent / 100)
        if direction == "above"
        else baseline * (1 - percent / 100)
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

    matched_counts = {metric: 0 for metric in response_metrics}
    rows: list[dict[str, Any]] = []
    for event_day, event_value in sorted(selected.items()):
        response_day = (
            date.fromisoformat(event_day) + timedelta(days=offset)
        ).isoformat()
        responses: dict[str, float | None] = {}
        for metric in response_metrics:
            value = response_series[metric].get(response_day)
            if value is None or not math.isfinite(float(value)):
                responses[metric] = None
            else:
                responses[metric] = float(value)
                matched_counts[metric] += 1
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
        "response_offset_days": offset,
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
            "Deterministic daily aggregation, personal-baseline threshold, then exact calendar-date "
            "join. Missing response dates remain null and are never shifted, inferred, or zero-filled."
        ),
    }


def _as_pipeline(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [dict(step) for step in value if isinstance(step, dict)]


def _normalize_pipeline(value: Any) -> list[dict[str, Any]]:
    pipeline = _as_pipeline(value)
    aliases: list[str] = []
    semantic_aliases: dict[str, str] = {}
    result: list[dict[str, Any]] = []

    for index, raw in enumerate(pipeline, start=1):
        step = dict(raw)
        op = str(step.get("op") or "")
        if op == "get_baseline":
            op = "baseline"
            step["op"] = op

        arguments = step.get("arguments")
        if (
            op == "load_series"
            and not step.get("metric")
            and isinstance(arguments, dict)
            and arguments.get("metric")
        ):
            step["metric"] = arguments["metric"]

        if op != "return" and not step.get("as"):
            step["as"] = f"step_{index}"
        alias = str(step.get("as") or "")

        def repair_ref(
            field: str,
            current_step: dict[str, Any] = step,
        ) -> None:
            current_value = current_step.get(field)
            if not isinstance(current_value, str) or not current_value:
                return
            ref = current_value.removeprefix("$")
            if ref in aliases:
                current_step[field] = ref
                return

            folded = ref.casefold()
            semantic = None
            if "hrv" in folded or "variab" in folded:
                semantic = semantic_aliases.get("hrv")
            elif "fcr" in folded or "rhr" in folded or "rest" in folded:
                semantic = semantic_aliases.get("rhr")
            elif "attiv" in folded or "active" in folded:
                semantic = semantic_aliases.get("active")
            current_step[field] = semantic or (aliases[-1] if aliases else ref)

        for field in (
            "source",
            "left",
            "right",
            "numerator",
            "denominator",
            "baseline_source",
            "event_source",
            "response_source",
            "response_baseline_source",
        ):
            repair_ref(field)

        result.append(step)
        if alias:
            aliases.append(alias)
            if op == "load_series":
                metric = str(step.get("metric") or "").casefold()
                if "variability" in metric or "hrv" in metric:
                    semantic_aliases["hrv"] = alias
                if "resting-heart-rate" in metric or "rhr" in metric:
                    semantic_aliases["rhr"] = alias
                if "active" in metric and "minute" in metric:
                    semantic_aliases["active"] = alias
    return result


def _threshold_recipe(
    args: dict[str, Any],
    pipeline: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    capability = str(args.get("capability") or "")
    if "personal_baseline" not in capability or "threshold" not in capability:
        return None

    metrics: list[str] = []
    percent: Any = 30
    direction = "above"
    baseline_field = "median"
    for step in pipeline:
        metric = step.get("metric")
        arguments = step.get("arguments")
        if not metric and isinstance(arguments, dict):
            metric = arguments.get("metric")
        if metric and str(metric) not in metrics:
            metrics.append(str(metric))
        if str(step.get("op") or "") == "filter_relative":
            percent = step.get("percent", percent)
            direction = str(step.get("direction") or direction)
            baseline_field = str(step.get("baseline_field") or baseline_field)

    if len(metrics) < 2:
        return None

    return [
        {
            "op": "call_tool",
            "tool": _BUILTIN_NAME,
            "arguments": {
                "start": "$start",
                "end": "$end",
                "event_metric": metrics[0],
                "response_metrics": metrics[1:8],
                "percent": percent,
                "direction": direction if direction in {"above", "below"} else "above",
                "baseline_field": (
                    baseline_field if baseline_field in {"median", "mean"} else "median"
                ),
                "response_offset_days": 0,
            },
            "as": "analysis",
        },
        {"op": "return", "source": "analysis"},
    ]


def _install_runtime_budget() -> None:
    """Patch the agent runtime lazily, avoiding Qt imports during package import."""

    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return

    from . import agent_runtime as base_rt
    from . import agent_runtime_v2 as runtime_v2

    original_subset = base_rt.online_tool_subset
    original_json = base_rt._json_text
    original_history = base_rt.compact_agent_history
    original_chat = base_rt.AgentRuntime._chat_once

    def compact_subset(
        schemas: list[dict[str, Any]],
        request: str,
        *,
        maximum: int = 10,
        required_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return original_subset(
            schemas,
            request,
            maximum=min(10, max(1, int(maximum))),
            required_names=required_names,
        )

    def compact_json(value: Any, limit: int = 5000) -> str:
        return original_json(value, min(max(800, int(limit)), 5000))

    def compact_history(
        history: list[dict[str, str]] | None,
        *,
        maximum: int = 4,
        message_limit: int = 600,
    ) -> list[dict[str, str]]:
        return original_history(
            history,
            maximum=min(4, max(1, int(maximum))),
            message_limit=min(600, max(200, int(message_limit))),
        )

    def budgeted_chat(
        self: Any,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        num_ctx: int,
        num_predict: int,
        think: bool,
        cancel_callback: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        context = max(1, int(num_ctx))
        if tools:
            predict_cap = 640 if context <= 8192 else 900 if context <= 16384 else 1100
        else:
            predict_cap = 1600 if context <= 8192 else 2200 if context <= 16384 else 2800

        message = original_chat(
            self,
            model=model,
            messages=messages,
            tools=tools,
            num_ctx=num_ctx,
            num_predict=min(max(256, int(num_predict)), predict_cap),
            think=think,
            cancel_callback=cancel_callback,
        )
        calls = (
            message.get("tool_calls")
            if isinstance(message.get("tool_calls"), list)
            else []
        )
        if not calls:
            return message

        heavy = [
            call
            for call in calls
            if isinstance(call, dict) and base_rt._tool_name(call) in _HEAVY_TOOLS
        ]
        kept = heavy[:1] if heavy else calls[: (1 if context <= 8192 else 2)]
        if len(kept) == len(calls):
            return message

        trimmed = dict(message)
        trimmed["tool_calls"] = kept
        return trimmed

    base_rt.online_tool_subset = compact_subset
    base_rt._json_text = compact_json
    base_rt.compact_agent_history = compact_history
    base_rt.AgentRuntime._chat_once = budgeted_chat

    runtime_v2.MAX_EVIDENCE_ENTRIES = 5
    runtime_v2.MAX_EVIDENCE_LIST_ITEMS = 10
    runtime_v2.MAX_EVIDENCE_ENTRY_CHARS = 3200
    runtime_v2.MAX_EVIDENCE_LEDGER_CHARS = 7600

    _RUNTIME_INSTALLED = True


def install_agent_tool_factory_reliability_patch() -> None:
    """Install safe Tool Factory repairs without importing the GUI runtime."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    factory.SAFE_COMPOSABLE_TOOLS.add(_BUILTIN_NAME)
    tool_schema = factory._PIPELINE_STEP_SCHEMA.get("properties", {}).get("tool")
    if isinstance(tool_schema, dict):
        tool_schema["enum"] = sorted(
            {*(tool_schema.get("enum") or []), _BUILTIN_NAME}
        )
    setattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{_BUILTIN_NAME}",
        _tool_analyze_metric_threshold_responses,
    )

    pipeline_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get(
        "pipeline"
    )
    if isinstance(pipeline_schema, dict):
        description = str(pipeline_schema.get("description") or "")
        if _BUILTIN_NAME not in description:
            pipeline_schema["description"] = description + (
                " For cross-metric personal-baseline threshold requests, prefer the canonical "
                f"{_BUILTIN_NAME} call_tool recipe rather than manually joining raw series."
            )
        examples = pipeline_schema.setdefault("examples", [])
        if isinstance(examples, list) and _THRESHOLD_PIPELINE_EXAMPLE not in examples:
            examples.append(_THRESHOLD_PIPELINE_EXAMPLE)

    original_init = factory.EnhancedSafeToolExecutor.__init__
    original_schemas = factory.EnhancedSafeToolExecutor.tool_schemas
    original_validate = factory.EnhancedSafeToolExecutor.validate_pipeline.__func__
    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.agent_store.sync_builtin_tools([_BUILTIN_SPEC])
        _install_runtime_budget()

    def patched_schemas(self: Any) -> list[dict[str, Any]]:
        schemas = original_schemas(self)
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
        return schemas

    @classmethod
    def patched_validate(cls: type, pipeline: Any) -> list[dict[str, Any]]:
        normalized = _normalize_pipeline(pipeline)
        return original_validate(cls, normalized if normalized else pipeline)

    def patched_create(
        self: Any,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        repaired = deepcopy(args)
        normalized = _normalize_pipeline(repaired.get("pipeline"))
        if normalized:
            repaired["pipeline"] = normalized

        recipe = _threshold_recipe(repaired, normalized)
        if recipe is not None:
            repaired["pipeline"] = recipe

        result = original_create(self, repaired, **kwargs)
        if not isinstance(result, dict):
            return result

        result = dict(result)
        changed = repaired.get("pipeline") != args.get("pipeline")
        if changed and result.get("status") in {"created", "reused"}:
            result["auto_repaired"] = True
            result["repair_strategy"] = (
                "canonical_deterministic_threshold_response"
                if recipe is not None
                else "dsl_shape_normalization"
            )
        if result.get("status") in {"invalid_pipeline", "invalid_spec"}:
            result["canonical_threshold_response_pipeline"] = (
                _THRESHOLD_PIPELINE_EXAMPLE
            )
            result["threshold_response_instruction"] = (
                "For personal-baseline threshold response analyses, rebuild the learned tool with "
                f"{_BUILTIN_NAME} using the canonical pipeline instead of manually joining raw series."
            )
        return result

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    factory.EnhancedSafeToolExecutor.tool_schemas = patched_schemas
    factory.EnhancedSafeToolExecutor.validate_pipeline = patched_validate
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = patched_create

    _INSTALLED = True
