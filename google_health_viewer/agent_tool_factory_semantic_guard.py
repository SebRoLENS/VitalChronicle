from __future__ import annotations

import statistics
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from . import agent_tool_factory as factory
from . import agent_tool_factory_reliability_patch as reliability
from . import agent_tool_factory_schema_guard as guard

_INSTALLED = False
_RUNTIME_INSTALLED = False
_CONTEXT_RECOVERY_NAME = "compare_training_context_recovery"
_CONTEXT_RECOVERY_CAPABILITY = "analysis.primitive.training_context_recovery_comparison"
_COMPOSED_CONTEXT_CAPABILITY = "analysis.composed.training_context.recovery_comparison"

_CONTEXT_RECOVERY_SPEC = {
    "name": _CONTEXT_RECOVERY_NAME,
    "description": (
        "Deterministically compare recovery after a recorded training day that follows another "
        "training day with recovery after a recorded training day preceded by one or more rest days. "
        "No high-intensity threshold is introduced unless the user explicitly asks for one."
    ),
    "capability": _CONTEXT_RECOVERY_CAPABILITY,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "end": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "response_metrics": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string"},
                "default": [
                    "daily-heart-rate-variability",
                    "daily-resting-heart-rate",
                ],
            },
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
            "minimum_rest_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 1,
            },
        },
        "required": ["start", "end"],
    },
}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _semantic_requirements(text: str) -> set[str]:
    folded = str(text or "").casefold()
    requirements: set[str] = set()
    if _contains_any(
        folded,
        (
            "confront",
            "rispetto a",
            "rispetto ad",
            "più lento",
            "piu lento",
            "più veloce",
            "piu veloce",
            "compare",
            "compared",
            "versus",
            " vs ",
        ),
    ):
        requirements.add("comparison")
    if _contains_any(
        folded,
        (
            "consecutiv",
            "due giorni di fila",
            "giorni di fila",
            "back-to-back",
            "back to back",
            "consecutive",
            "two days in a row",
        ),
    ):
        requirements.add("consecutive_training")
    if _contains_any(
        folded,
        (
            "giorno di riposo",
            "giorni di riposo",
            "dopo almeno un giorno di riposo",
            "preceduto da riposo",
            "after a rest day",
            "after at least one rest day",
            "preceded by rest",
        ),
    ):
        requirements.add("prior_rest")
    if _contains_any(
        folded,
        (
            "recuper",
            "torna alla baseline",
            "torni alla baseline",
            "return to baseline",
            "recovery",
            "recover",
        ),
    ):
        requirements.add("recovery")
    if _contains_any(
        folded,
        (
            "intens",
            "alto carico",
            "alta intensità",
            "alta intensita",
            "high load",
            "high intensity",
            "hard workout",
        ),
    ):
        requirements.add("high_load_filter")
    return requirements


def _is_training_context_comparison(requirements: set[str]) -> bool:
    return {
        "comparison",
        "consecutive_training",
        "prior_rest",
        "recovery",
    }.issubset(requirements)


def _pipeline_semantics(pipeline: list[dict[str, Any]]) -> set[str]:
    features: set[str] = set()
    for step in pipeline:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op") or "")
        tool = str(step.get("tool") or "")
        if op in {"compare", "compare_periods"}:
            features.add("comparison")
        if op == "event_response":
            features.add("recovery")
        if op == "filter_relative":
            features.add("high_load_filter")
        if tool == guard._RECOVERY_NAME:
            features.add("recovery")
            arguments = step.get("arguments")
            if isinstance(arguments, dict):
                percent = arguments.get("event_percent", 30)
                if percent not in (None, 0, 0.0, "0", "$zero"):
                    features.add("high_load_filter")
        if tool == _CONTEXT_RECOVERY_NAME:
            features.update(
                {
                    "comparison",
                    "consecutive_training",
                    "prior_rest",
                    "recovery",
                }
            )
    return features


def _semantic_contract_errors(
    request: str,
    pipeline: list[dict[str, Any]],
) -> dict[str, list[str]]:
    required = _semantic_requirements(request)
    provided = _pipeline_semantics(pipeline)
    missing = sorted(required - provided)
    introduced: list[str] = []
    if "high_load_filter" in provided and "high_load_filter" not in required:
        introduced.append("high_load_filter")
    return {
        "required": sorted(required),
        "provided": sorted(provided),
        "missing": missing,
        "introduced": introduced,
    }


def _response_metrics_from_request(request: str, catalog: set[str]) -> list[str]:
    text = str(request or "").casefold()
    result: list[str] = []
    if _contains_any(text, ("hrv", "variabil")):
        result.append("daily-heart-rate-variability")
    if _contains_any(
        text,
        ("fcr", "rhr", "frequenza cardiaca a riposo", "resting heart rate"),
    ):
        result.append("daily-resting-heart-rate")
    if _contains_any(text, ("sonno", "sleep")):
        result.append("sleep")
    if not result:
        result = [
            "daily-heart-rate-variability",
            "daily-resting-heart-rate",
        ]
    if catalog:
        result = [metric for metric in result if metric in catalog]
    return result[:8]


def _context_comparison_recipe(
    args: dict[str, Any],
    request: str,
    catalog: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response_metrics = _response_metrics_from_request(request, catalog)
    if not response_metrics:
        response_metrics = [
            "daily-heart-rate-variability",
            "daily-resting-heart-rate",
        ]
    pipeline = [
        {
            "op": "call_tool",
            "tool": _CONTEXT_RECOVERY_NAME,
            "arguments": {
                "start": "$start",
                "end": "$end",
                "response_metrics": response_metrics,
                "baseline_field": "median",
                "recovery_tolerance_percent": "$recovery_tolerance_percent",
                "max_recovery_days": "$max_recovery_days",
                "minimum_rest_days": "$minimum_rest_days",
            },
            "as": "comparison",
        },
        {"op": "return", "source": "comparison"},
    ]
    parameters = deepcopy(args.get("parameters")) if isinstance(args.get("parameters"), dict) else {}
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        parameters["properties"] = properties
    properties.setdefault("start", {"type": "string", "description": "Local date YYYY-MM-DD."})
    properties.setdefault("end", {"type": "string", "description": "Local date YYYY-MM-DD."})
    properties.setdefault(
        "recovery_tolerance_percent",
        {"type": "number", "minimum": 0, "maximum": 100, "default": 10},
    )
    properties.setdefault(
        "max_recovery_days",
        {"type": "integer", "minimum": 1, "maximum": 60, "default": 14},
    )
    properties.setdefault(
        "minimum_rest_days",
        {"type": "integer", "minimum": 1, "maximum": 30, "default": 1},
    )
    parameters["type"] = "object"
    parameters["additionalProperties"] = False
    required = parameters.get("required")
    if not isinstance(required, list):
        required = []
        parameters["required"] = required
    for key in ("start", "end"):
        if key not in required:
            required.append(key)
    return pipeline, parameters


def _rest_days_before(day: date, training_days: set[str], left: date, limit: int) -> int:
    count = 0
    cursor = day - timedelta(days=1)
    while cursor >= left and count < limit:
        if cursor.isoformat() in training_days:
            break
        count += 1
        cursor -= timedelta(days=1)
    return count


def _metric_group_recovery(
    series: dict[str, float],
    baseline: float | None,
    event_days: list[str],
    *,
    tolerance: float,
    max_days: int,
    training_days: set[str],
) -> dict[str, Any]:
    episodes: list[dict[str, Any]] = []
    recovered: list[float] = []
    for event_day in event_days:
        event_date = date.fromisoformat(event_day)
        recovery_date: str | None = None
        recovery_value: float | None = None
        first_observed_date: str | None = None
        first_observed_value: float | None = None
        intervening_training_days: list[str] = []
        for delta in range(1, max_days + 1):
            candidate_date = event_date + timedelta(days=delta)
            candidate = candidate_date.isoformat()
            if candidate in training_days:
                intervening_training_days.append(candidate)
            if candidate not in series:
                continue
            value = float(series[candidate])
            if first_observed_date is None:
                first_observed_date = candidate
                first_observed_value = value
            if baseline is None:
                continue
            denominator = max(abs(float(baseline)), 1e-12)
            deviation = abs(value - float(baseline)) / denominator * 100
            if deviation <= tolerance:
                recovery_date = candidate
                recovery_value = value
                recovered.append(float(delta))
                break
        episodes.append(
            {
                "event_date": event_day,
                "first_observed_response_date": first_observed_date,
                "first_observed_response_value": first_observed_value,
                "recovery_date": recovery_date,
                "recovery_value": recovery_value,
                "recovery_days": (
                    None
                    if recovery_date is None
                    else (date.fromisoformat(recovery_date) - event_date).days
                ),
                "intervening_training_days": intervening_training_days,
            }
        )
    return {
        "event_count": len(event_days),
        "evaluated_events": len(episodes),
        "recovered_events": len(recovered),
        "unrecovered_events": len(event_days) - len(recovered),
        "mean_recovery_days": round(statistics.fmean(recovered), 2) if recovered else None,
        "median_recovery_days": round(statistics.median(recovered), 2) if recovered else None,
        "episodes": episodes[:120],
    }


def _tool_compare_training_context_recovery(
    self: Any,
    args: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    left, right = factory.base._bounds(args.get("start"), args.get("end"), 60)
    response_metrics = [
        str(item).strip()
        for item in (
            args.get("response_metrics")
            or ["daily-heart-rate-variability", "daily-resting-heart-rate"]
        )
        if str(item).strip()
    ][:8]
    baseline_field = str(args.get("baseline_field") or "median")
    if baseline_field not in {"median", "mean"}:
        baseline_field = "median"
    try:
        tolerance = float(args.get("recovery_tolerance_percent", 10))
    except (TypeError, ValueError):
        tolerance = 10.0
    tolerance = max(0.0, min(100.0, tolerance))
    try:
        max_days = int(args.get("max_recovery_days", 14))
    except (TypeError, ValueError):
        max_days = 14
    max_days = max(1, min(60, max_days))
    try:
        minimum_rest_days = int(args.get("minimum_rest_days", 1))
    except (TypeError, ValueError):
        minimum_rest_days = 1
    minimum_rest_days = max(1, min(30, minimum_rest_days))

    exercise_raw = self._series("exercise", left, right)
    exercise_daily = factory.base._daily(
        exercise_raw.get("points", []),
        "sum" if exercise_raw.get("aggregation") == "sum" else "mean",
    )
    training_days = {
        day for day, value in exercise_daily.items() if float(value) > 0
    }
    consecutive: list[str] = []
    after_rest: list[str] = []
    unclassified: list[str] = []
    for day_text in sorted(training_days):
        day = date.fromisoformat(day_text)
        previous = (day - timedelta(days=1)).isoformat()
        if previous in training_days:
            consecutive.append(day_text)
            continue
        if (day - left).days < minimum_rest_days:
            unclassified.append(day_text)
            continue
        rest_days = _rest_days_before(day, training_days, left, minimum_rest_days)
        if rest_days >= minimum_rest_days:
            after_rest.append(day_text)
        else:
            unclassified.append(day_text)

    responses: dict[str, Any] = {}
    for metric in response_metrics:
        series, raw = guard._daily_metric_series(self, metric, left, right)
        values = list(series.values())
        baseline = None
        if values:
            baseline = (
                statistics.median(values)
                if baseline_field == "median"
                else statistics.fmean(values)
            )
        consecutive_result = _metric_group_recovery(
            series,
            baseline,
            consecutive,
            tolerance=tolerance,
            max_days=max_days,
            training_days=training_days,
        )
        rest_result = _metric_group_recovery(
            series,
            baseline,
            after_rest,
            tolerance=tolerance,
            max_days=max_days,
            training_days=training_days,
        )
        a = consecutive_result.get("median_recovery_days")
        b = rest_result.get("median_recovery_days")
        responses[metric] = {
            "baseline": None if baseline is None else round(float(baseline), 6),
            "baseline_field": baseline_field,
            "tolerance_percent": tolerance,
            "observed_days": len(series),
            "unit": raw.get("unit"),
            "field": raw.get("field"),
            "date_semantics": raw.get("date_semantics"),
            "consecutive_training": consecutive_result,
            "after_rest": rest_result,
            "median_recovery_delta_days_consecutive_minus_rest": (
                None if a is None or b is None else round(float(a) - float(b), 2)
            ),
        }

    comparable_metrics = [
        metric
        for metric, item in responses.items()
        if item["consecutive_training"].get("median_recovery_days") is not None
        and item["after_rest"].get("median_recovery_days") is not None
    ]
    minimum_group = min(len(consecutive), len(after_rest)) if consecutive and after_rest else 0
    quality = (
        "insufficient_for_comparison"
        if minimum_group < 2
        else "preliminary"
        if minimum_group < 5
        else "adequate"
    )
    return {
        "status": "ok",
        "period": {"start": left.isoformat(), "end": right.isoformat()},
        "training_source": "exercise",
        "training_observed_days": len(training_days),
        "group_definitions": {
            "consecutive_training": (
                "Recorded training day whose immediately preceding calendar day also contains recorded exercise."
            ),
            "after_rest": (
                f"Recorded training day preceded by at least {minimum_rest_days} calendar day(s) "
                "without recorded exercise inside the analysed interval."
            ),
        },
        "consecutive_training_days": consecutive,
        "after_rest_days": after_rest,
        "unclassified_boundary_days": unclassified,
        "response_metrics": response_metrics,
        "responses": responses,
        "comparable_metrics": comparable_metrics,
        "can_compare": bool(comparable_metrics),
        "sample_quality": quality,
        "minimum_rest_days": minimum_rest_days,
        "max_recovery_days": max_days,
        "recovery_tolerance_percent": tolerance,
        "method": (
            "Deterministic context-conditioned recovery comparison using all recorded exercise days; "
            "no intensity threshold is added. Recovery is the first observed post-training day within "
            "the configured absolute percentage distance from each response metric's personal baseline. "
            "Missing dates are skipped, never zero-filled. Later training inside a recovery window is "
            "reported as intervening training rather than silently ignored."
        ),
    }


def _install_runtime_semantics() -> None:
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return
    from . import agent_runtime as base_rt
    from . import agent_runtime_v2 as runtime_v2

    original_hint = runtime_v2._factory_hint
    original_capability = runtime_v2._factory_capability
    original_subset = base_rt.online_tool_subset

    def semantic_hint(question: str) -> dict[str, Any]:
        result = deepcopy(original_hint(question))
        requirements = _semantic_requirements(question)
        if _is_training_context_comparison(requirements):
            signals = [str(item) for item in result.get("signals", [])]
            marker = "training-context recovery comparison"
            if marker not in signals:
                signals.append(marker)
            result["signals"] = signals
            result["consider_reusable_tool"] = True
            result["semantic_requirements"] = sorted(requirements)
            result["instruction"] = (
                "A reusable context-conditioned comparison is required. Preserve the distinction "
                "between consecutive training and training after rest; do not replace it with a "
                "generic high-load recovery analysis."
            )
        return result

    def semantic_capability(hint: dict[str, Any]) -> str:
        signals = {str(item) for item in hint.get("signals", [])}
        if "training-context recovery comparison" in signals:
            return _COMPOSED_CONTEXT_CAPABILITY
        return original_capability(hint)

    def semantic_subset(
        schemas: list[dict[str, Any]],
        request: str,
        *,
        maximum: int = 10,
        required_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        required = set(required_names or ())
        if _is_training_context_comparison(_semantic_requirements(request)):
            required.add(_CONTEXT_RECOVERY_NAME)
        return original_subset(
            schemas,
            request,
            maximum=maximum,
            required_names=required,
        )

    runtime_v2._factory_hint = semantic_hint
    runtime_v2._factory_capability = semantic_capability
    base_rt.online_tool_subset = semantic_subset
    _RUNTIME_INSTALLED = True


def install_semantic_tool_factory_guard() -> None:
    """Require learned tools to preserve the user's requested semantics, not only execute safely."""

    global _INSTALLED
    if _INSTALLED:
        return

    factory.SAFE_COMPOSABLE_TOOLS.add(_CONTEXT_RECOVERY_NAME)
    tool_schema = factory._PIPELINE_STEP_SCHEMA.get("properties", {}).get("tool")
    if isinstance(tool_schema, dict):
        tool_schema["enum"] = sorted(
            {*(tool_schema.get("enum") or []), _CONTEXT_RECOVERY_NAME}
        )
    reliability._HEAVY_TOOLS.add(_CONTEXT_RECOVERY_NAME)
    setattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{_CONTEXT_RECOVERY_NAME}",
        _tool_compare_training_context_recovery,
    )

    original_prepare = guard._prepare_candidate
    original_init = factory.EnhancedSafeToolExecutor.__init__
    original_schemas = factory.EnhancedSafeToolExecutor.tool_schemas
    original_execute = factory.EnhancedSafeToolExecutor.execute

    def semantic_prepare(
        executor: Any,
        args: dict[str, Any],
        *,
        run_dry: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        request = str(
            getattr(executor, "_factory_user_request", "")
            or args.get("description")
            or ""
        )
        requirements = _semantic_requirements(request)
        candidate = deepcopy(args)
        semantic_repair = False
        if _is_training_context_comparison(requirements):
            catalog = guard._available_metric_catalog(executor)
            pipeline, parameters = _context_comparison_recipe(candidate, request, catalog)
            candidate["pipeline"] = pipeline
            candidate["parameters"] = parameters
            candidate["capability"] = _COMPOSED_CONTEXT_CAPABILITY
            candidate["description"] = (
                "Compare recovery after recorded training on consecutive days with recovery after "
                "recorded training preceded by one or more rest days."
            )
            semantic_repair = True

        prepared, meta = original_prepare(executor, candidate, run_dry=run_dry)
        if prepared is None:
            return prepared, meta
        pipeline = prepared.get("pipeline") if isinstance(prepared.get("pipeline"), list) else []
        contract = _semantic_contract_errors(request, pipeline)
        if contract["missing"] or contract["introduced"]:
            problems = [
                *(f"missing semantic operation: {item}" for item in contract["missing"]),
                *(f"unrequested semantic constraint: {item}" for item in contract["introduced"]),
            ]
            return None, {
                "error": "learned-tool semantic contract mismatch: " + "; ".join(problems),
                "validation_errors": problems,
                "semantic_validation": "failed",
                "semantic_contract": contract,
                "semantic_repair_required": True,
                "target_tool_schemas": meta.get("target_tool_schemas", {}),
                "dry_run": meta.get("dry_run"),
                "instruction": (
                    "Repair this same learned tool so its actual pipeline implements every requested "
                    "operation and removes constraints the user did not ask for. Re-run schema "
                    "validation and dry-run; do not continue the analysis without first attempting repair."
                ),
            }
        meta = dict(meta)
        meta["semantic_validation"] = "passed"
        meta["semantic_contract"] = contract
        if semantic_repair:
            meta["strategy"] = "semantic_training_context_recovery_comparison"
            meta["semantic_auto_repaired"] = True
        return prepared, meta

    def semantic_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.agent_store.sync_builtin_tools([_CONTEXT_RECOVERY_SPEC])
        _install_runtime_semantics()

    def semantic_schemas(self: Any) -> list[dict[str, Any]]:
        schemas = original_schemas(self)
        if not any(
            isinstance(item, dict)
            and isinstance(item.get("function"), dict)
            and item["function"].get("name") == _CONTEXT_RECOVERY_NAME
            for item in schemas
        ):
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": _CONTEXT_RECOVERY_NAME,
                        "description": _CONTEXT_RECOVERY_SPEC["description"],
                        "parameters": _CONTEXT_RECOVERY_SPEC["parameters"],
                    },
                }
            )
        return schemas

    def semantic_execute(
        self: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        if name == "search_tool_registry":
            description = str(args.get("description") or "").strip()
            if description:
                self._factory_user_request = description
        result = original_execute(self, name, args, thread_id=thread_id)
        if name == "create_learned_tool" and isinstance(result, dict):
            result = dict(result)
            if result.get("status") in {"invalid_pipeline", "invalid_spec"}:
                if result.get("semantic_repair_required") or "semantic" in str(
                    result.get("error") or ""
                ).casefold():
                    result["repairable"] = True
                    result["instruction"] = (
                        "Semantic validation failed. Repair the SAME tool before doing any further "
                        "analysis: preserve every requested comparison/condition, remove unrequested "
                        "constraints, validate again, then execute the repaired tool."
                    )
        return result

    guard._prepare_candidate = semantic_prepare
    factory.EnhancedSafeToolExecutor.__init__ = semantic_init
    factory.EnhancedSafeToolExecutor.tool_schemas = semantic_schemas
    factory.EnhancedSafeToolExecutor.execute = semantic_execute
    _INSTALLED = True
