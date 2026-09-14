from __future__ import annotations

import json
import math
import statistics
from copy import deepcopy
from datetime import date, datetime, timedelta
from difflib import get_close_matches
from typing import Any

from . import agent_store as agent_store_mod
from . import agent_tool_factory as factory
from . import agent_tool_factory_reliability_patch as reliability

_INSTALLED = False
_RECOVERY_NAME = "analyze_post_event_recovery"
_RECOVERY_CAPABILITY = "analysis.primitive.temporal_event_recovery"
_BAD_RUNTIME_STATUSES = {
    "error",
    "failed",
    "invalid_arguments",
    "invalid_pipeline",
    "invalid_spec",
    "repair_budget_exhausted",
}

_RECOVERY_SPEC = {
    "name": _RECOVERY_NAME,
    "description": (
        "Deterministically identify unusually high-load event episodes and measure when one or more "
        "response metrics first return within a configurable percentage of their personal baselines. "
        "Use cardio_load for workout intensity or metric plus event_metric for a raw metric trigger."
    ),
    "capability": _RECOVERY_CAPABILITY,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "end": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "event_source": {
                "type": "string",
                "enum": ["cardio_load", "metric"],
                "default": "cardio_load",
            },
            "event_metric": {"type": "string"},
            "response_metrics": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string"},
            },
            "event_percent": {"type": "number", "minimum": 0, "maximum": 500, "default": 30},
            "baseline_field": {
                "type": "string",
                "enum": ["median", "mean"],
                "default": "median",
            },
            "recovery_tolerance_percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "default": 10,
            },
            "max_recovery_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
                "default": 14,
            },
        },
        "required": ["start", "end", "response_metrics"],
    },
}

_RECOVERY_PIPELINE_EXAMPLE = [
    {
        "op": "call_tool",
        "tool": _RECOVERY_NAME,
        "arguments": {
            "start": "$start",
            "end": "$end",
            "event_source": "cardio_load",
            "response_metrics": [
                "daily-heart-rate-variability",
                "daily-resting-heart-rate",
            ],
            "event_percent": "$event_percent",
            "baseline_field": "median",
            "recovery_tolerance_percent": "$recovery_tolerance_percent",
            "max_recovery_days": "$max_recovery_days",
        },
        "as": "recovery",
    },
    {"op": "return", "source": "recovery"},
]

_METRIC_SYNONYMS = {
    "hrv": "daily-heart-rate-variability",
    "heart_rate_variability": "daily-heart-rate-variability",
    "heart rate variability": "daily-heart-rate-variability",
    "rhr": "daily-resting-heart-rate",
    "fcr": "daily-resting-heart-rate",
    "resting_heart_rate": "daily-resting-heart-rate",
    "resting heart rate": "daily-resting-heart-rate",
    "active_minutes": "active-minutes",
    "active minutes": "active-minutes",
    "minuti attivi": "active-minutes",
    "workout": "exercise",
    "workouts": "exercise",
    "allenamento": "exercise",
    "allenamenti": "exercise",
}


def _numeric(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _daily_metric_series(
    executor: Any,
    metric: str,
    left: date,
    right: date,
) -> tuple[dict[str, float], dict[str, Any]]:
    raw = executor._series(metric, left, right)
    points = raw.get("points", []) if isinstance(raw, dict) else []
    aggregation = str(raw.get("aggregation") or "mean") if isinstance(raw, dict) else "mean"
    daily = factory.base._daily(points, "sum" if aggregation == "sum" else "mean")
    return daily, raw if isinstance(raw, dict) else {}


def _episodes(days: list[str]) -> list[list[str]]:
    result: list[list[str]] = []
    for day in sorted(days):
        if (
            result
            and date.fromisoformat(day)
            == date.fromisoformat(result[-1][-1]) + timedelta(days=1)
        ):
            result[-1].append(day)
        else:
            result.append([day])
    return result


def _tool_analyze_post_event_recovery(
    self: Any,
    args: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    left, right = factory.base._bounds(args.get("start"), args.get("end"), 60)
    response_metrics = [
        str(item).strip()
        for item in (args.get("response_metrics") or [])
        if str(item).strip()
    ][:8]
    if not response_metrics:
        return {"status": "invalid_arguments", "error": "response_metrics is required"}

    event_source = str(args.get("event_source") or "cardio_load")
    if event_source not in {"cardio_load", "metric"}:
        return {
            "status": "invalid_arguments",
            "error": "event_source must be cardio_load or metric",
        }

    event_metric = str(args.get("event_metric") or "").strip()
    if event_source == "metric" and not event_metric:
        return {
            "status": "invalid_arguments",
            "error": "event_metric is required when event_source=metric",
        }

    baseline_field = str(args.get("baseline_field") or "median")
    if baseline_field not in {"median", "mean"}:
        baseline_field = "median"
    event_percent = _numeric(args.get("event_percent", 30), 30.0, 0.0, 500.0)
    tolerance = _numeric(
        args.get("recovery_tolerance_percent", 10), 10.0, 0.0, 100.0
    )
    try:
        max_days = int(args.get("max_recovery_days", 14))
    except (TypeError, ValueError):
        max_days = 14
    max_days = max(1, min(60, max_days))

    if event_source == "cardio_load":
        load = self._tool_calculate_cardio_load(
            {"start": left.isoformat(), "end": right.isoformat()}
        )
        event_series: dict[str, float] = {}
        for row in load.get("daily_load", []) if isinstance(load, dict) else []:
            if not isinstance(row, dict):
                continue
            try:
                value = float(row.get("load"))
                day = date.fromisoformat(str(row.get("date") or "")[:10]).isoformat()
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                event_series[day] = value
        event_meta = {
            "source": "cardio_load",
            "unit": load.get("unit") if isinstance(load, dict) else None,
            "method": load.get("method") if isinstance(load, dict) else None,
        }
    else:
        event_series, raw = _daily_metric_series(self, event_metric, left, right)
        event_meta = {
            "source": "metric",
            "metric": event_metric,
            "unit": raw.get("unit"),
            "field": raw.get("field"),
            "date_semantics": raw.get("date_semantics"),
        }

    event_values = list(event_series.values())
    if not event_values:
        return {
            "status": "no_event_data",
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "event_source": event_source,
            "event_metric": event_metric or None,
            "response_metrics": response_metrics,
            "event_episodes": [],
            "event_episode_count": 0,
        }

    event_baseline = (
        statistics.median(event_values)
        if baseline_field == "median"
        else statistics.fmean(event_values)
    )
    event_threshold = event_baseline * (1 + event_percent / 100)
    selected = [day for day, value in event_series.items() if value > event_threshold]
    event_episodes = _episodes(selected)

    responses: dict[str, Any] = {}
    for metric in response_metrics:
        series, raw = _daily_metric_series(self, metric, left, right)
        values = list(series.values())
        response_baseline = (
            statistics.median(values)
            if values and baseline_field == "median"
            else statistics.fmean(values)
            if values
            else None
        )
        rows: list[dict[str, Any]] = []
        recovered_days: list[float] = []
        for episode in event_episodes:
            episode_end = date.fromisoformat(episode[-1])
            first_observed_date = None
            first_observed_value = None
            recovery_date = None
            recovery_value = None
            if response_baseline is not None:
                for delta_days in range(1, max_days + 1):
                    candidate = (episode_end + timedelta(days=delta_days)).isoformat()
                    if candidate not in series:
                        continue
                    value = float(series[candidate])
                    if first_observed_date is None:
                        first_observed_date = candidate
                        first_observed_value = value
                    denominator = max(abs(float(response_baseline)), 1e-12)
                    relative_deviation = abs(value - float(response_baseline)) / denominator * 100
                    if relative_deviation <= tolerance:
                        recovery_date = candidate
                        recovery_value = value
                        recovered_days.append(float(delta_days))
                        break
            rows.append(
                {
                    "event_episode_start": episode[0],
                    "event_episode_end": episode[-1],
                    "event_dates": episode,
                    "first_observed_response_date": first_observed_date,
                    "first_observed_response_value": first_observed_value,
                    "recovery_date": recovery_date,
                    "recovery_value": recovery_value,
                    "recovery_days_after_episode": (
                        None
                        if recovery_date is None
                        else (
                            date.fromisoformat(recovery_date) - episode_end
                        ).days
                    ),
                }
            )
        responses[metric] = {
            "baseline": None if response_baseline is None else round(float(response_baseline), 6),
            "baseline_field": baseline_field,
            "tolerance_percent": tolerance,
            "observed_days": len(series),
            "unit": raw.get("unit"),
            "field": raw.get("field"),
            "date_semantics": raw.get("date_semantics"),
            "evaluated_episodes": len(event_episodes),
            "recovered_episodes": len(recovered_days),
            "unrecovered_episodes": len(event_episodes) - len(recovered_days),
            "mean_recovery_days": (
                round(statistics.fmean(recovered_days), 2) if recovered_days else None
            ),
            "median_recovery_days": (
                round(statistics.median(recovered_days), 2) if recovered_days else None
            ),
            "episodes": rows[:120],
        }

    quality = (
        "insufficient_for_typical_estimate"
        if len(event_episodes) < 3
        else "preliminary"
        if len(event_episodes) < 5
        else "adequate"
    )
    return {
        "status": "ok",
        "period": {"start": left.isoformat(), "end": right.isoformat()},
        "event_source": event_source,
        "event_metric": event_metric or None,
        "event_meta": event_meta,
        "event_observed_days": len(event_series),
        "event_baseline": round(float(event_baseline), 6),
        "event_percent": event_percent,
        "event_threshold": round(float(event_threshold), 6),
        "event_episode_count": len(event_episodes),
        "event_episodes": event_episodes[:120],
        "response_metrics": response_metrics,
        "responses": responses,
        "max_recovery_days": max_days,
        "recovery_tolerance_percent": tolerance,
        "sample_quality": quality,
        "can_estimate_typical_recovery": len(event_episodes) >= 3,
        "method": (
            "Deterministic event-episode analysis. High-load days are selected relative to the "
            "personal event baseline and consecutive days are grouped. For each response metric, "
            "recovery is the first observed post-episode day within the configured absolute percentage "
            "distance from that metric's personal baseline. Missing dates are skipped, never zero-filled."
        ),
    }


def _available_metric_catalog(executor: Any) -> set[str]:
    cached = getattr(executor, "_tool_factory_metric_catalog", None)
    if isinstance(cached, set):
        return cached
    catalog: set[str] = set()
    try:
        available = executor._tool_get_available_metrics({})
    except Exception:  # noqa: BLE001 - catalog failure must not break the factory.
        available = {}
    for item in available.get("data_types", []) if isinstance(available, dict) else []:
        if not isinstance(item, dict):
            continue
        data_type = str(item.get("data_type") or "").strip()
        if not data_type:
            continue
        catalog.add(data_type)
        for metric in item.get("metrics", []) if isinstance(item.get("metrics"), list) else []:
            metric_name = str(metric or "").strip()
            if metric_name:
                catalog.add(f"{data_type}:{metric_name}")
    for alias, target in getattr(factory.base, "_ALIASES", {}).items():
        catalog.add(str(alias))
        catalog.add(str(target))
    setattr(executor, "_tool_factory_metric_catalog", catalog)
    return catalog


def _canonical_metric(value: Any, catalog: set[str]) -> Any:
    if not isinstance(value, str) or value.startswith(("$", "@")):
        return value
    text = value.strip()
    folded = text.casefold()
    target = _METRIC_SYNONYMS.get(folded)
    if target and (not catalog or target in catalog):
        return target
    aliases = getattr(factory.base, "_ALIASES", {})
    target = str(aliases.get(text, text))
    if not catalog or target in catalog or text in catalog:
        return target if target in catalog else text
    return text


def _canonicalize_call_metrics(
    args: dict[str, Any],
    parameter_schema: dict[str, Any],
    catalog: set[str],
) -> dict[str, Any]:
    result = deepcopy(args)
    properties = parameter_schema.get("properties", {})
    if not isinstance(properties, dict):
        return result
    for key, schema in properties.items():
        if key not in result or not isinstance(schema, dict):
            continue
        if "metric" not in str(key).casefold():
            continue
        value = result[key]
        if isinstance(value, list):
            result[key] = [_canonical_metric(item, catalog) for item in value]
        else:
            result[key] = _canonical_metric(value, catalog)
    return result


def _schema_index(executor: Any) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for schema in executor.tool_schemas():
        function = schema.get("function") if isinstance(schema, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "")
        parameters = function.get("parameters")
        if name and isinstance(parameters, dict):
            index[name] = parameters
    return index


def _dynamic_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("$", "@"))


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if _dynamic_reference(value):
        return []
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return [f"{path} must be an object"]
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required", [])
        required = required if isinstance(required, list) else []
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            for key in unknown:
                errors.append(f"{path}.{key} is not accepted by the target tool")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                errors.extend(_validate_value(item, child, f"{path}.{key}"))
        return errors
    if expected == "array":
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} requires at least {minimum} item(s)")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} allows at most {maximum} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_value(item, item_schema, f"{path}[{index}]"))
        return errors
    if expected == "string" and not isinstance(value, str):
        errors.append(f"{path} must be a string")
    elif expected == "number" and not isinstance(value, (int, float)):
        errors.append(f"{path} must be a number")
    elif expected == "integer" and not isinstance(value, int):
        errors.append(f"{path} must be an integer")
    elif expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{path} must be a boolean")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} must be one of {enum}")
    if isinstance(value, (int, float)):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} must be >= {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} must be <= {maximum}")
    return errors


def _metric_errors(
    arguments: dict[str, Any],
    parameter_schema: dict[str, Any],
    catalog: set[str],
    tool_name: str,
) -> list[str]:
    if not catalog:
        return []
    properties = parameter_schema.get("properties", {})
    if not isinstance(properties, dict):
        return []
    errors: list[str] = []
    for key in properties:
        if "metric" not in str(key).casefold() or key not in arguments:
            continue
        values = arguments[key] if isinstance(arguments[key], list) else [arguments[key]]
        for value in values:
            if _dynamic_reference(value) or not isinstance(value, str):
                continue
            canonical = _canonical_metric(value, catalog)
            if canonical in catalog:
                continue
            suggestions = get_close_matches(canonical, sorted(catalog), n=3, cutoff=0.55)
            suffix = f"; available close matches: {suggestions}" if suggestions else ""
            errors.append(
                f"{tool_name}.{key} uses unavailable metric '{value}'{suffix}"
            )
    return errors


def _validate_nested_calls(
    executor: Any,
    pipeline: list[dict[str, Any]],
    catalog: set[str],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    schemas = _schema_index(executor)
    errors: list[str] = []
    used: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(pipeline, start=1):
        if str(step.get("op") or "") != "call_tool":
            continue
        tool = str(step.get("tool") or "")
        target = schemas.get(tool)
        if target is None:
            errors.append(f"step {index}: target tool '{tool}' has no callable schema")
            continue
        used[tool] = target
        arguments = step.get("arguments") or {}
        if not isinstance(arguments, dict):
            errors.append(f"step {index}: {tool} arguments must be an object")
            continue
        errors.extend(_validate_value(arguments, target, f"step {index} {tool}.arguments"))
        errors.extend(_metric_errors(arguments, target, catalog, tool))
        if tool == _RECOVERY_NAME:
            event_source = str(arguments.get("event_source") or "cardio_load")
            if event_source == "metric" and not arguments.get("event_metric"):
                errors.append(
                    f"step {index}: {_RECOVERY_NAME}.event_metric is required when event_source=metric"
                )
    return errors, used


def _joined_candidate_text(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, default=str).casefold()


def _infer_recovery_metrics(args: dict[str, Any], catalog: set[str]) -> list[str]:
    text = _joined_candidate_text(args)
    result: list[str] = []
    if any(token in text for token in ("hrv", "heart-rate-variability", "variabil")):
        preferred = "daily-heart-rate-variability"
        result.append(preferred if not catalog or preferred in catalog else "heart-rate-variability")
    if any(
        token in text
        for token in ("rhr", "fcr", "resting-heart-rate", "frequenza cardiaca a riposo")
    ):
        result.append("daily-resting-heart-rate")

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and "metric" in key.casefold():
            canonical = _canonical_metric(value, catalog)
            if isinstance(canonical, str) and canonical in catalog and canonical not in result:
                if canonical not in {"exercise", "active-minutes"}:
                    result.append(canonical)

    visit(args)
    return result[:8]


def _recovery_recipe(
    args: dict[str, Any],
    catalog: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    capability = str(args.get("capability") or "").casefold()
    if "recovery_latency" not in capability and not (
        "recovery" in capability and "temporal" in capability
    ):
        return None
    response_metrics = _infer_recovery_metrics(args, catalog)
    if not response_metrics:
        return None
    text = _joined_candidate_text(args)
    event_source = "metric" if any(
        token in text for token in ("active-minutes", "active_minutes", "minuti attivi")
    ) else "cardio_load"
    call_arguments: dict[str, Any] = {
        "start": "$start",
        "end": "$end",
        "event_source": event_source,
        "response_metrics": response_metrics,
        "event_percent": "$event_percent",
        "baseline_field": "median",
        "recovery_tolerance_percent": "$recovery_tolerance_percent",
        "max_recovery_days": "$max_recovery_days",
    }
    if event_source == "metric":
        call_arguments["event_metric"] = "active-minutes"
    pipeline = [
        {
            "op": "call_tool",
            "tool": _RECOVERY_NAME,
            "arguments": call_arguments,
            "as": "recovery",
        },
        {"op": "return", "source": "recovery"},
    ]
    parameters = deepcopy(args.get("parameters")) if isinstance(args.get("parameters"), dict) else {}
    properties = parameters.setdefault("properties", {})
    if not isinstance(properties, dict):
        properties = {}
        parameters["properties"] = properties
    properties.setdefault("start", {"type": "string", "description": "Local date YYYY-MM-DD."})
    properties.setdefault("end", {"type": "string", "description": "Local date YYYY-MM-DD."})
    properties.setdefault(
        "event_percent",
        {"type": "number", "minimum": 0, "maximum": 500, "default": 30},
    )
    properties.setdefault(
        "recovery_tolerance_percent",
        {"type": "number", "minimum": 0, "maximum": 100, "default": 10},
    )
    properties.setdefault(
        "max_recovery_days",
        {"type": "integer", "minimum": 1, "maximum": 60, "default": 14},
    )
    parameters.setdefault("type", "object")
    parameters.setdefault("additionalProperties", False)
    required = parameters.setdefault("required", ["start", "end"])
    if isinstance(required, list):
        for item in ("start", "end"):
            if item not in required:
                required.append(item)
    return pipeline, parameters


def _canonicalize_pipeline(
    executor: Any,
    pipeline: list[dict[str, Any]],
    catalog: set[str],
) -> list[dict[str, Any]]:
    schemas = _schema_index(executor)
    result: list[dict[str, Any]] = []
    for raw in pipeline:
        step = deepcopy(raw)
        if str(step.get("op") or "") == "load_series" and step.get("metric"):
            step["metric"] = _canonical_metric(step["metric"], catalog)
        if str(step.get("op") or "") == "call_tool":
            tool = str(step.get("tool") or "")
            target = schemas.get(tool, {})
            arguments = step.get("arguments")
            if isinstance(arguments, dict):
                step["arguments"] = _canonicalize_call_metrics(arguments, target, catalog)
        result.append(step)
    return result


def _runtime_error(value: Any) -> str | None:
    if isinstance(value, dict):
        status = str(value.get("status") or "").casefold()
        if status in _BAD_RUNTIME_STATUSES:
            return str(value.get("error") or f"runtime status {status}")
        if value.get("error"):
            return str(value.get("error"))
        for item in value.values():
            found = _runtime_error(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _runtime_error(item)
            if found:
                return found
    return None


def _dry_run(
    executor: Any,
    candidate: dict[str, Any],
    pipeline: list[dict[str, Any]],
) -> dict[str, Any]:
    parameters = candidate.get("parameters") if isinstance(candidate.get("parameters"), dict) else {}
    properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
    required = parameters.get("required") if isinstance(parameters.get("required"), list) else []
    today = datetime.now().astimezone().date()
    runtime_args: dict[str, Any] = {
        "start": (today - timedelta(days=20)).isoformat(),
        "end": today.isoformat(),
    }
    for key, spec in properties.items():
        if key in runtime_args or not isinstance(spec, dict):
            continue
        if "default" in spec:
            runtime_args[str(key)] = spec["default"]
    missing = [str(key) for key in required if key not in runtime_args]
    if missing:
        return {"status": "skipped_missing_inputs", "missing": missing}
    item = {
        "name": str(candidate.get("name") or "factory_candidate"),
        "pipeline": pipeline,
        "parameters": parameters,
        "confidence": 0.7,
    }
    try:
        result = executor._run_learned(item, runtime_args)
    except Exception as exc:  # noqa: BLE001 - dry-run exceptions are validation evidence.
        return {"status": "failed", "error": str(exc)}
    error = _runtime_error(result)
    if error:
        return {"status": "failed", "error": error, "result": result}
    return {"status": "passed", "result_status": (result.get("result") or {}).get("status") if isinstance(result.get("result"), dict) else None}


def _prepare_candidate(
    executor: Any,
    args: dict[str, Any],
    *,
    run_dry: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    prepared = deepcopy(args)
    catalog = _available_metric_catalog(executor)
    normalized = reliability._normalize_pipeline(prepared.get("pipeline"))
    if normalized:
        prepared["pipeline"] = normalized

    threshold_recipe = reliability._threshold_recipe(prepared, normalized)
    strategy = None
    if threshold_recipe is not None:
        prepared["pipeline"] = threshold_recipe
        strategy = "canonical_deterministic_threshold_response"

    recovery = _recovery_recipe(prepared, catalog)
    if recovery is not None:
        prepared["pipeline"], prepared["parameters"] = recovery
        strategy = "canonical_deterministic_recovery_latency"

    pipeline = prepared.get("pipeline")
    if not isinstance(pipeline, list):
        return None, {"error": "pipeline must be a list", "validation_errors": ["pipeline must be a list"]}
    pipeline = _canonicalize_pipeline(executor, pipeline, catalog)
    prepared["pipeline"] = pipeline
    try:
        validated = factory.EnhancedSafeToolExecutor.validate_pipeline(pipeline)
    except (TypeError, ValueError) as exc:
        return None, {"error": str(exc), "validation_errors": [str(exc)]}
    prepared["pipeline"] = validated

    errors, schemas = _validate_nested_calls(executor, validated, catalog)
    if errors:
        return None, {
            "error": errors[0],
            "validation_errors": errors,
            "target_tool_schemas": schemas,
        }

    dry = _dry_run(executor, prepared, validated) if run_dry else {"status": "not_requested"}
    if dry.get("status") == "failed":
        return None, {
            "error": f"learned-tool dry-run failed: {dry.get('error')}",
            "validation_errors": [f"dry-run: {dry.get('error')}"],
            "dry_run": dry,
            "target_tool_schemas": schemas,
        }
    return prepared, {
        "strategy": strategy,
        "dry_run": dry,
        "target_tool_schemas": schemas,
    }


def _dependencies(pipeline: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for step in pipeline:
        metric = step.get("metric")
        tool = step.get("tool")
        if metric:
            result.append(str(metric))
        if tool:
            result.append(f"tool:{tool}")
    return result


def _mark_invalid(store: Any, name: str, reason: str) -> None:
    if not hasattr(store, "_connect"):
        return
    try:
        with store._connect() as db:
            db.execute(
                "UPDATE tools SET status='invalid',replacement=NULL,updated_at=? "
                "WHERE name=? AND kind='learned'",
                (agent_store_mod._now(), name),
            )
        store.log_tool_event(
            "tool_invalidated",
            reason,
            tool_name=name,
            payload={"reason": reason},
        )
    except Exception:  # noqa: BLE001 - invalidation is best-effort safety cleanup.
        return


def _repair_existing_tool(executor: Any, item: dict[str, Any]) -> bool:
    candidate = {
        "name": item.get("name"),
        "description": item.get("description"),
        "capability": item.get("capability"),
        "parameters": item.get("parameters"),
        "pipeline": item.get("pipeline"),
    }
    prepared, meta = _prepare_candidate(executor, candidate, run_dry=True)
    if prepared is None:
        _mark_invalid(
            executor.agent_store,
            str(item.get("name") or ""),
            f"Existing learned tool failed schema-aware validation: {meta.get('error')}",
        )
        return False
    if prepared.get("pipeline") == item.get("pipeline") and prepared.get("parameters") == item.get("parameters"):
        return True
    spec = {
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "capability": str(item.get("capability") or item.get("name") or ""),
        "parameters": prepared.get("parameters") or item.get("parameters") or {},
        "outputs": item.get("outputs") or {},
        "pipeline": prepared["pipeline"],
        "dependencies": _dependencies(prepared["pipeline"]),
        "confidence": float(item.get("confidence") or 0.7),
        "version": int(item.get("version") or 1),
    }
    executor.agent_store.add_learned_tool(spec)
    executor.agent_store.log_tool_event(
        "tool_auto_repaired",
        f"Recompiled learned tool {spec['name']} against live schemas and metric identifiers.",
        tool_name=spec["name"],
        payload={"strategy": meta.get("strategy"), "dry_run": meta.get("dry_run")},
    )
    return True


def install_schema_aware_tool_factory() -> None:
    """Compile learned tools against real schemas/metrics and verify them before persistence."""

    global _INSTALLED
    if _INSTALLED:
        return

    factory.SAFE_COMPOSABLE_TOOLS.add(_RECOVERY_NAME)
    tool_schema = factory._PIPELINE_STEP_SCHEMA.get("properties", {}).get("tool")
    if isinstance(tool_schema, dict):
        tool_schema["enum"] = sorted({*(tool_schema.get("enum") or []), _RECOVERY_NAME})
    reliability._HEAVY_TOOLS.add(_RECOVERY_NAME)
    setattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{_RECOVERY_NAME}",
        _tool_analyze_post_event_recovery,
    )

    pipeline_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get("pipeline")
    if isinstance(pipeline_schema, dict):
        description = str(pipeline_schema.get("description") or "")
        if "target tool's real JSON schema" not in description:
            pipeline_schema["description"] = description + (
                " Every call_tool is compiled against the target tool's real JSON schema: never invent "
                "argument names. Metric literals must come from get_available_metrics. For recovery-latency "
                f"tools prefer the canonical {_RECOVERY_NAME} recipe."
            )
        examples = pipeline_schema.setdefault("examples", [])
        if isinstance(examples, list) and _RECOVERY_PIPELINE_EXAMPLE not in examples:
            examples.append(_RECOVERY_PIPELINE_EXAMPLE)

    original_init = factory.EnhancedSafeToolExecutor.__init__
    original_schemas = factory.EnhancedSafeToolExecutor.tool_schemas
    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool
    original_execute = factory.EnhancedSafeToolExecutor.execute

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.agent_store.sync_builtin_tools([_RECOVERY_SPEC])
        for item in self.agent_store.list_tools(include_superseded=False, kind="learned"):
            _repair_existing_tool(self, item)

    def patched_schemas(self: Any) -> list[dict[str, Any]]:
        schemas = original_schemas(self)
        if not any(
            isinstance(item, dict)
            and isinstance(item.get("function"), dict)
            and item["function"].get("name") == _RECOVERY_NAME
            for item in schemas
        ):
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": _RECOVERY_NAME,
                        "description": _RECOVERY_SPEC["description"],
                        "parameters": _RECOVERY_SPEC["parameters"],
                    },
                }
            )
        return schemas

    def patched_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        prepared, meta = _prepare_candidate(self, args, run_dry=True)
        if prepared is None:
            return {
                "status": "invalid_pipeline",
                "repairable": True,
                "error": str(meta.get("error") or "Schema-aware validation failed."),
                "validation_errors": meta.get("validation_errors", []),
                "target_tool_schemas": meta.get("target_tool_schemas", {}),
                "dry_run": meta.get("dry_run"),
                "allowed_operations": sorted(factory.ALLOWED_DSL_OPS),
                "instruction": (
                    "Repair the same tool using only exact target-tool argument names and exact metric "
                    "identifiers from get_available_metrics. Do not invent fields or substitute proxies."
                ),
            }
        result = original_create(self, prepared, **kwargs)
        if isinstance(result, dict) and result.get("status") in {"created", "reused"}:
            result = dict(result)
            result["schema_validation"] = "passed"
            result["dry_run"] = meta.get("dry_run")
            if meta.get("strategy"):
                result["compiler_strategy"] = meta["strategy"]
        return result

    def patched_execute(
        self: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        stored = self.agent_store.tool(name)
        if stored and stored.get("kind") == "learned" and stored.get("status") == "active":
            if not _repair_existing_tool(self, stored):
                return {
                    "status": "invalid_learned_tool",
                    "error": (
                        f"Learned tool '{name}' failed schema-aware validation and was disabled "
                        "instead of being executed repeatedly."
                    ),
                    "repairable": True,
                }
        result = original_execute(self, name, arguments, thread_id=thread_id)
        if stored and stored.get("kind") == "learned":
            error = _runtime_error(result)
            if error:
                current = self.agent_store.tool(name)
                if current and _repair_existing_tool(self, current):
                    retried = original_execute(self, name, arguments, thread_id=thread_id)
                    retry_error = _runtime_error(retried)
                    if not retry_error:
                        return retried
                    error = retry_error
                _mark_invalid(
                    self.agent_store,
                    name,
                    f"Learned tool failed execution validation: {error}",
                )
                return {
                    "status": "invalid_learned_tool",
                    "error": str(error),
                    "repairable": True,
                }
        return result

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    factory.EnhancedSafeToolExecutor.tool_schemas = patched_schemas
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = patched_create
    factory.EnhancedSafeToolExecutor.execute = patched_execute

    _INSTALLED = True
