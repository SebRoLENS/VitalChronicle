from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

_INSTALLED = False


def _as_pipeline(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [dict(step) for step in value if isinstance(step, dict)]


def _metric_slug(metric: str, index: int) -> str:
    folded = str(metric or "").casefold()
    if "variability" in folded or "hrv" in folded:
        return "hrv_series"
    if "resting-heart-rate" in folded or "resting_heart_rate" in folded or "rhr" in folded:
        return "rhr_series"
    if "active" in folded and "minute" in folded:
        return "active_minutes_series"
    clean = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    return (clean[:36] + "_series") if clean else f"series_{index}"


def _normalize_pipeline(raw: Any) -> list[dict[str, Any]]:
    """Repair harmless model-shape mistakes before spending a model repair turn."""

    pipeline = _as_pipeline(raw)
    if not pipeline:
        return []

    aliases: list[str] = []
    metric_aliases: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for index, original in enumerate(pipeline, start=1):
        step = dict(original)
        op = str(step.get("op") or "").strip()
        if op == "get_baseline":
            op = "baseline"
            step["op"] = op

        arguments = step.get("arguments")
        if op == "load_series" and not step.get("metric") and isinstance(arguments, dict):
            nested_metric = arguments.get("metric")
            if nested_metric:
                step["metric"] = nested_metric

        if op != "return" and not step.get("as"):
            if op == "load_series":
                alias = _metric_slug(str(step.get("metric") or ""), index)
            else:
                alias = f"{op or 'step'}_{index}"
            if alias in aliases:
                alias = f"{alias}_{index}"
            step["as"] = alias
        alias = str(step.get("as") or "")

        def resolve_ref(field: str) -> None:
            value = step.get(field)
            if not isinstance(value, str) or not value:
                return
            ref = value[1:] if value.startswith("$") else value
            if ref in aliases:
                step[field] = ref
                return
            folded = ref.casefold()
            semantic = None
            if "hrv" in folded or "variab" in folded:
                semantic = metric_aliases.get("hrv")
            elif "fcr" in folded or "rhr" in folded or "rest" in folded:
                semantic = metric_aliases.get("rhr")
            elif "attiv" in folded or "active" in folded:
                semantic = metric_aliases.get("active")
            if semantic:
                step[field] = semantic
            elif aliases:
                # `$data` and similar scratch-variable placeholders mean the most recent
                # deterministic intermediate result, not a learned-tool input.
                step[field] = aliases[-1]

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
            resolve_ref(field)

        result.append(step)
        if alias:
            aliases.append(alias)
            if op == "load_series":
                folded_metric = str(step.get("metric") or "").casefold()
                if "variability" in folded_metric or "hrv" in folded_metric:
                    metric_aliases["hrv"] = alias
                if "resting-heart-rate" in folded_metric or "rhr" in folded_metric:
                    metric_aliases["rhr"] = alias
                if "active" in folded_metric and "minute" in folded_metric:
                    metric_aliases["active"] = alias
    return result


def _threshold_recipe(args: dict[str, Any], pipeline: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    from . import agent_tool_factory_reliability_patch as reliability

    capability = str(args.get("capability") or "")
    if "personal_baseline" not in capability or "threshold" not in capability:
        return None

    metrics: list[str] = []
    percent: Any = 30
    direction = "above"
    baseline_field = "median"
    for step in pipeline:
        metric = step.get("metric")
        if not metric and isinstance(step.get("arguments"), dict):
            metric = step["arguments"].get("metric")
        if metric:
            text = str(metric).strip()
            if text and text not in metrics:
                metrics.append(text)
        if str(step.get("op") or "") == "filter_relative":
            percent = step.get("percent", percent)
            direction = str(step.get("direction") or direction)
            baseline_field = str(step.get("baseline_field") or baseline_field)

    if len(metrics) < 2:
        return None

    event_metric, response_metrics = metrics[0], metrics[1:8]
    return [
        {
            "op": "call_tool",
            "tool": reliability._BUILTIN_NAME,
            "arguments": {
                "start": "$start",
                "end": "$end",
                "event_metric": event_metric,
                "response_metrics": response_metrics,
                "percent": percent,
                "direction": direction if direction in {"above", "below"} else "above",
                "baseline_field": baseline_field if baseline_field in {"median", "mean"} else "median",
                "response_offset_days": 0,
            },
            "as": "analysis",
        },
        {"op": "return", "source": "analysis"},
    ]


def install_agent_tool_factory_autorepair_patch() -> None:
    """Normalize common DSL mistakes and route threshold analyses to a deterministic recipe."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory
    from . import agent_tool_factory_reliability_patch as reliability

    # The base schema is built at import time. Reliability adds the tool later, so keep the
    # JSON enum synchronized with the runtime allow-list as well.
    tool_schema = factory._PIPELINE_STEP_SCHEMA.get("properties", {}).get("tool")
    if isinstance(tool_schema, dict):
        enum = list(tool_schema.get("enum") or [])
        if reliability._BUILTIN_NAME not in enum:
            enum.append(reliability._BUILTIN_NAME)
            tool_schema["enum"] = sorted(set(str(item) for item in enum))

    original_validate = factory.EnhancedSafeToolExecutor.validate_pipeline.__func__
    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool

    @classmethod
    def repaired_validate(cls: type, pipeline: Any) -> list[dict[str, Any]]:
        normalized = _normalize_pipeline(pipeline)
        return original_validate(cls, normalized if normalized else pipeline)

    def repaired_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        repaired_args = deepcopy(args)
        normalized = _normalize_pipeline(repaired_args.get("pipeline"))
        if normalized:
            repaired_args["pipeline"] = normalized

        recipe = _threshold_recipe(repaired_args, normalized)
        if recipe is not None:
            repaired_args["pipeline"] = recipe
            result = original_create(self, repaired_args, **kwargs)
            if isinstance(result, dict):
                result = dict(result)
                result["auto_repaired"] = True
                result["repair_strategy"] = "canonical_deterministic_threshold_response"
            return result

        result = original_create(self, repaired_args, **kwargs)
        if isinstance(result, dict) and result.get("status") in {"created", "reused"}:
            result = dict(result)
            result["auto_repaired"] = repaired_args.get("pipeline") != args.get("pipeline")
            if result["auto_repaired"]:
                result["repair_strategy"] = "dsl_shape_normalization"
        return result

    factory.EnhancedSafeToolExecutor.validate_pipeline = repaired_validate
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = repaired_create
    _INSTALLED = True
