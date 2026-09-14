from __future__ import annotations

import re
import statistics
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from . import agent_runtime_v2 as runtime_v2
from . import agent_store as store_mod
from . import agent_tool_factory as factory
from . import agent_tool_factory_reliability_patch as reliability
from . import agent_tool_factory_schema_guard as guard
from . import agent_tool_factory_semantic_guard as semantic
from .i18n import _

_INSTALLED = False
_RUNTIME_INSTALLED = False

_SLEEP_CONDITIONED_NAME = "compare_sleep_conditioned_consecutive_training_recovery"
_SLEEP_CONDITIONED_PRIMITIVE_CAPABILITY = (
    "analysis.primitive.sleep_conditioned_consecutive_training_recovery"
)
_SLEEP_CONDITIONED_COMPOSED_CAPABILITY = (
    "analysis.composed.training_context.sleep_conditioned_return_comparison"
)
_SLEEP_CONDITIONED_SIGNAL = "sleep-conditioned consecutive-training recovery comparison"

_SLEEP_CONDITIONED_SPEC = {
    "name": _SLEEP_CONDITIONED_NAME,
    "description": (
        "Compare return-to-baseline recovery after the second day of a consecutive-training pair, "
        "splitting those events by sleep on the following night relative to the user's personal "
        "sleep baseline. The low-sleep cohort and baseline-or-above cohort remain distinct; no "
        "training-intensity threshold is introduced unless explicitly requested."
    ),
    "capability": _SLEEP_CONDITIONED_PRIMITIVE_CAPABILITY,
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
            "sleep_deficit_percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "default": 15,
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
        },
        "required": ["start", "end"],
    },
}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_sleep_conditioned_consecutive_recovery(text: str) -> bool:
    folded = str(text or "").casefold()
    return all(
        (
            _contains_any(
                folded,
                (
                    "consecutiv",
                    "due giorni di fila",
                    "back-to-back",
                    "back to back",
                    "consecutive",
                ),
            ),
            _contains_any(
                folded,
                (
                    "secondo allenamento",
                    "secondo giorno",
                    "second workout",
                    "second training day",
                ),
            ),
            _contains_any(folded, ("sonno", "dorm", "sleep", "slept")),
            _contains_any(
                folded,
                (
                    "notte successiva",
                    "notte seguente",
                    "next night",
                    "following night",
                ),
            ),
            _contains_any(
                folded,
                (
                    "mediana",
                    "baseline",
                    "median",
                    "valore abituale",
                    "valori abituali",
                ),
            ),
            _contains_any(
                folded,
                (
                    "recuper",
                    "torna",
                    "tornano",
                    "return",
                    "recover",
                    "recovery",
                ),
            ),
            _contains_any(
                folded,
                (
                    "confront",
                    "rispetto a",
                    "più lentamente",
                    "piu lentamente",
                    "compare",
                    "compared",
                    "versus",
                    " vs ",
                ),
            ),
        )
    )


def _sleep_deficit_percent(request: str) -> float:
    folded = str(request or "").casefold()
    matches = re.findall(r"(\d+(?:[\.,]\d+)?)\s*%", folded)
    for raw in matches:
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        if 0 <= value <= 100:
            return value
    return 15.0


def _response_metrics(request: str, catalog: set[str]) -> list[str]:
    folded = str(request or "").casefold()
    result: list[str] = []
    if _contains_any(folded, ("hrv", "variabil")):
        result.append("daily-heart-rate-variability")
    if _contains_any(
        folded,
        ("fcr", "rhr", "frequenza cardiaca a riposo", "resting heart rate"),
    ):
        result.append("daily-resting-heart-rate")
    if not result:
        result = ["daily-heart-rate-variability", "daily-resting-heart-rate"]
    if catalog:
        result = [metric for metric in result if metric in catalog]
    return result[:8]


def _sleep_conditioned_recipe(
    args: dict[str, Any],
    request: str,
    catalog: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deficit = _sleep_deficit_percent(request)
    metrics = _response_metrics(request, catalog)
    if not metrics:
        metrics = ["daily-heart-rate-variability", "daily-resting-heart-rate"]
    pipeline = [
        {
            "op": "call_tool",
            "tool": _SLEEP_CONDITIONED_NAME,
            "arguments": {
                "start": "$start",
                "end": "$end",
                "response_metrics": metrics,
                "sleep_deficit_percent": "$sleep_deficit_percent",
                "baseline_field": "median",
                "recovery_tolerance_percent": "$recovery_tolerance_percent",
                "max_recovery_days": "$max_recovery_days",
            },
            "as": "comparison",
        },
        {"op": "return", "source": "comparison"},
    ]
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "end": {"type": "string", "description": "Local date YYYY-MM-DD."},
            "sleep_deficit_percent": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "default": deficit,
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
        "required": ["start", "end"],
    }
    return pipeline, parameters


def _daily_sleep_minutes(executor: Any, left: date, right: date) -> tuple[dict[str, float], dict[str, Any]]:
    return guard._daily_metric_series(executor, "sleep:sleep.summary.minutesAsleep", left, right)


def _tool_compare_sleep_conditioned_consecutive_training_recovery(
    self: Any,
    args: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    left, right = factory.base._bounds(args.get("start"), args.get("end"), 90)
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
        sleep_deficit = float(args.get("sleep_deficit_percent", 15))
    except (TypeError, ValueError):
        sleep_deficit = 15.0
    sleep_deficit = max(0.0, min(100.0, sleep_deficit))
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

    exercise_raw = self._series("exercise", left, right)
    exercise_daily = factory.base._daily(
        exercise_raw.get("points", []),
        "sum" if exercise_raw.get("aggregation") == "sum" else "mean",
    )
    training_days = {day for day, value in exercise_daily.items() if float(value) > 0}

    # Select only the second day of a training streak. A third/fourth consecutive day is
    # not silently reclassified as another "second workout" event.
    second_training_days: list[str] = []
    for day_text in sorted(training_days):
        day = date.fromisoformat(day_text)
        previous = (day - timedelta(days=1)).isoformat()
        two_days_before = (day - timedelta(days=2)).isoformat()
        if previous in training_days and two_days_before not in training_days:
            second_training_days.append(day_text)

    sleep_series, sleep_raw = _daily_sleep_minutes(self, left, right + timedelta(days=1))
    sleep_values = list(sleep_series.values())
    sleep_baseline = None
    if sleep_values:
        sleep_baseline = (
            statistics.median(sleep_values)
            if baseline_field == "median"
            else statistics.fmean(sleep_values)
        )
    low_threshold = (
        None
        if sleep_baseline is None
        else float(sleep_baseline) * (1.0 - sleep_deficit / 100.0)
    )

    low_sleep_events: list[str] = []
    baseline_or_above_events: list[str] = []
    intermediate_events: list[str] = []
    missing_sleep_events: list[str] = []
    event_sleep: list[dict[str, Any]] = []
    for event_day in second_training_days:
        sleep_day = (date.fromisoformat(event_day) + timedelta(days=1)).isoformat()
        value = sleep_series.get(sleep_day)
        cohort = "missing"
        if value is None or sleep_baseline is None or low_threshold is None:
            missing_sleep_events.append(event_day)
        elif float(value) < float(low_threshold):
            low_sleep_events.append(event_day)
            cohort = "low_sleep"
        elif float(value) >= float(sleep_baseline):
            baseline_or_above_events.append(event_day)
            cohort = "baseline_or_above"
        else:
            intermediate_events.append(event_day)
            cohort = "intermediate_excluded"
        event_sleep.append(
            {
                "event_date": event_day,
                "following_sleep_date": sleep_day,
                "following_sleep_value": value,
                "cohort": cohort,
            }
        )

    responses: dict[str, Any] = {}
    comparable_metrics: list[str] = []
    for metric in response_metrics:
        series, raw = guard._daily_metric_series(self, metric, left, right + timedelta(days=max_days))
        values = list(series.values())
        baseline = None
        if values:
            baseline = (
                statistics.median(values)
                if baseline_field == "median"
                else statistics.fmean(values)
            )
        low_result = semantic._metric_group_recovery(
            series,
            baseline,
            low_sleep_events,
            tolerance=tolerance,
            max_days=max_days,
            training_days=training_days,
        )
        normal_result = semantic._metric_group_recovery(
            series,
            baseline,
            baseline_or_above_events,
            tolerance=tolerance,
            max_days=max_days,
            training_days=training_days,
        )
        low_median = low_result.get("median_recovery_days")
        normal_median = normal_result.get("median_recovery_days")
        low_mean = low_result.get("mean_recovery_days")
        normal_mean = normal_result.get("mean_recovery_days")
        can_compare = low_median is not None and normal_median is not None
        if can_compare:
            comparable_metrics.append(metric)
        responses[metric] = {
            "baseline": None if baseline is None else round(float(baseline), 6),
            "baseline_field": baseline_field,
            "tolerance_percent": tolerance,
            "observed_days": len(series),
            "unit": raw.get("unit"),
            "field": raw.get("field"),
            "date_semantics": raw.get("date_semantics"),
            "low_sleep": low_result,
            "baseline_or_above_sleep": normal_result,
            "median_recovery_delta_days_low_minus_baseline_or_above": (
                None
                if low_median is None or normal_median is None
                else round(float(low_median) - float(normal_median), 2)
            ),
            "mean_recovery_delta_days_low_minus_baseline_or_above": (
                None
                if low_mean is None or normal_mean is None
                else round(float(low_mean) - float(normal_mean), 2)
            ),
            "can_compare": can_compare,
        }

    smallest_group = min(len(low_sleep_events), len(baseline_or_above_events))
    sample_quality = (
        "insufficient"
        if smallest_group < 2
        else "preliminary"
        if smallest_group < 5
        else "adequate"
    )
    return {
        "status": "ok",
        "period": {"start": left.isoformat(), "end": right.isoformat()},
        "training_event_definition": "second day of a training streak only",
        "training_days_observed": len(training_days),
        "second_consecutive_training_events": len(second_training_days),
        "sleep_metric": "sleep.summary.minutesAsleep",
        "sleep_unit": sleep_raw.get("unit") or "min",
        "sleep_date_semantics": sleep_raw.get("date_semantics") or "wake_up_date",
        "sleep_baseline": None if sleep_baseline is None else round(float(sleep_baseline), 6),
        "sleep_baseline_field": baseline_field,
        "sleep_deficit_percent": sleep_deficit,
        "low_sleep_threshold": None if low_threshold is None else round(float(low_threshold), 6),
        "cohorts": {
            "low_sleep_event_count": len(low_sleep_events),
            "baseline_or_above_event_count": len(baseline_or_above_events),
            "intermediate_excluded_event_count": len(intermediate_events),
            "missing_following_sleep_event_count": len(missing_sleep_events),
        },
        "event_sleep_classification": event_sleep[:120],
        "response_metrics": response_metrics,
        "responses": responses,
        "comparable_metrics": comparable_metrics,
        "can_compare": bool(comparable_metrics),
        "sample_quality": sample_quality,
        "recovery_tolerance_percent": tolerance,
        "max_recovery_days": max_days,
        "method": (
            "Deterministic two-stage cohort analysis. It selects only the second day of each "
            "training streak, assigns the following night's recorded sleep by wake-up date, splits "
            "events into sleep below the personal baseline by the configured percentage versus sleep "
            "at or above the personal baseline, excludes the intermediate band, then compares "
            "return-to-baseline recovery for each response metric. Missing dates are never zero-filled."
        ),
        "limitations": (
            "Descriptive within-person comparison only. No statistical significance is claimed unless "
            "a separate validated statistical test is explicitly performed."
        ),
    }


def _enhanced_semantic_requirements(text: str) -> set[str]:
    result = set(_ORIGINAL_SEMANTIC_REQUIREMENTS(text))
    if _is_sleep_conditioned_consecutive_recovery(text):
        result.update(
            {
                "comparison",
                "consecutive_training",
                "second_consecutive_training",
                "recovery",
                "sleep_condition",
                "following_night",
                "personal_sleep_baseline",
            }
        )
    return result


def _enhanced_pipeline_semantics(pipeline: list[dict[str, Any]]) -> set[str]:
    result = set(_ORIGINAL_PIPELINE_SEMANTICS(pipeline))
    for step in pipeline:
        if isinstance(step, dict) and str(step.get("tool") or "") == _SLEEP_CONDITIONED_NAME:
            result.update(
                {
                    "comparison",
                    "consecutive_training",
                    "second_consecutive_training",
                    "recovery",
                    "sleep_condition",
                    "following_night",
                    "personal_sleep_baseline",
                }
            )
    return result


def _durable_context_candidates(question: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for sentence in runtime_v2._context_sentences(str(question or "")):
        if sentence.endswith(("?", "？")):
            continue
        folded = sentence.casefold()
        matches = [
            (len(marker), key)
            for key, markers in runtime_v2._DURABLE_CONTEXT_MARKERS.items()
            for marker in markers
            if marker in folded
        ]
        if not matches:
            continue
        _, key = max(matches)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        spec = store_mod.PERSONAL_CONTEXT_KEY_SPECS.get(key, {})
        scope = str(spec.get("default_scope") or "stable")
        candidates.append(
            {
                "model_key": key,
                "statement": sentence,
                "temporal_scope": scope,
                "ttl_days": spec.get("ttl_days") if scope == "temporary" else None,
            }
        )
    return candidates


def _confirmation_question(candidate: dict[str, Any]) -> str:
    scope_text = "temporaneo" if candidate["temporal_scope"] == "temporary" else "duraturo"
    question = _(
        "Vuoi che ricordi questo contesto personale {scope} per le future analisi?"
    ).format(scope=scope_text)
    return f"{question}\n\n“{candidate['statement']}”"


def _install_runtime_support() -> None:
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return

    original_hint = runtime_v2._factory_hint
    original_capability = runtime_v2._factory_capability
    original_analyze = runtime_v2.AgentRuntime.analyze

    def hard_hint(question: str) -> dict[str, Any]:
        result = deepcopy(original_hint(question))
        if _is_sleep_conditioned_consecutive_recovery(question):
            signals = [str(item) for item in result.get("signals", [])]
            if _SLEEP_CONDITIONED_SIGNAL not in signals:
                signals.append(_SLEEP_CONDITIONED_SIGNAL)
            result["signals"] = signals
            result["consider_reusable_tool"] = True
            result["instruction"] = (
                "This request needs a reusable sleep-conditioned consecutive-training recovery "
                "comparison. Preserve second-day training selection, following-night sleep cohorts, "
                "personal sleep baseline and both recovery metrics; do not substitute high-load events."
            )
        return result

    def hard_capability(hint: dict[str, Any]) -> str:
        if _SLEEP_CONDITIONED_SIGNAL in {str(item) for item in hint.get("signals", [])}:
            return _SLEEP_CONDITIONED_COMPOSED_CAPABILITY
        return original_capability(hint)

    def multi_context_analyze(self: Any, *args: Any, **kwargs: Any) -> str:
        question = str(kwargs.get("question") or "")
        thread_id = kwargs.get("thread_id")
        event_callback = kwargs.get("event_callback")
        queued_count = 0
        for candidate in _durable_context_candidates(question):
            context_key = f"personal_context:{candidate['model_key']}"
            if self.agent_store.has_recent_feedback_key(context_key, days=30):
                continue
            queued = self.agent_store.ask_feedback(
                _confirmation_question(candidate),
                thread_id=thread_id,
                reason=_(
                    "Explicit confirmation prevents a useful personal detail from being lost while "
                    "avoiding silent promotion of an unverified inference."
                ),
                learning_key=context_key,
                context={
                    "feedback_mode": "durable_context_confirmation",
                    "candidate_statement": candidate["statement"],
                    "model_key": candidate["model_key"],
                    "temporal_scope": candidate["temporal_scope"],
                    "ttl_days": candidate["ttl_days"],
                },
            )
            if queued:
                queued_count += 1
        if queued_count and callable(event_callback):
            for _index in range(queued_count):
                event_callback(_("A personal-context candidate was queued for explicit confirmation."))
        return original_analyze(self, *args, **kwargs)

    runtime_v2._factory_hint = hard_hint
    runtime_v2._factory_capability = hard_capability
    runtime_v2.AgentRuntime.analyze = multi_context_analyze
    _RUNTIME_INSTALLED = True


def install_hard_query_reliability_patch() -> None:
    """Support multi-context prompts and a reusable sleep-conditioned recovery comparison."""

    global _INSTALLED, _ORIGINAL_PIPELINE_SEMANTICS, _ORIGINAL_SEMANTIC_REQUIREMENTS
    if _INSTALLED:
        return

    _ORIGINAL_SEMANTIC_REQUIREMENTS = semantic._semantic_requirements
    _ORIGINAL_PIPELINE_SEMANTICS = semantic._pipeline_semantics
    semantic._semantic_requirements = _enhanced_semantic_requirements
    semantic._pipeline_semantics = _enhanced_pipeline_semantics

    factory.SAFE_COMPOSABLE_TOOLS.add(_SLEEP_CONDITIONED_NAME)
    tool_schema = factory._PIPELINE_STEP_SCHEMA.get("properties", {}).get("tool")
    if isinstance(tool_schema, dict):
        tool_schema["enum"] = sorted({*(tool_schema.get("enum") or []), _SLEEP_CONDITIONED_NAME})
    reliability._HEAVY_TOOLS.add(_SLEEP_CONDITIONED_NAME)
    setattr(
        factory.EnhancedSafeToolExecutor,
        f"_tool_{_SLEEP_CONDITIONED_NAME}",
        _tool_compare_sleep_conditioned_consecutive_training_recovery,
    )

    original_prepare = guard._prepare_candidate
    original_init = factory.EnhancedSafeToolExecutor.__init__
    original_schemas = factory.EnhancedSafeToolExecutor.tool_schemas
    original_pending_feedback = store_mod.AgentStore.pending_feedback

    def hard_prepare(
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
        if not _is_sleep_conditioned_consecutive_recovery(request):
            return original_prepare(executor, args, run_dry=run_dry)
        candidate = deepcopy(args)
        catalog = guard._available_metric_catalog(executor)
        pipeline, parameters = _sleep_conditioned_recipe(candidate, request, catalog)
        candidate["pipeline"] = pipeline
        candidate["parameters"] = parameters
        candidate["capability"] = _SLEEP_CONDITIONED_COMPOSED_CAPABILITY
        candidate["description"] = (
            "Compare HRV/resting-heart-rate return to baseline after the second day of consecutive "
            "training, conditioned on whether following-night sleep is below the personal sleep "
            "baseline by the configured percentage or at/above the personal baseline."
        )
        prepared, meta = original_prepare(executor, candidate, run_dry=run_dry)
        meta = dict(meta)
        if prepared is not None:
            meta["semantic_validation"] = "passed"
            meta["strategy"] = "sleep_conditioned_consecutive_training_recovery"
            meta["semantic_auto_repaired"] = True
        return prepared, meta

    def hard_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.agent_store.sync_builtin_tools([_SLEEP_CONDITIONED_SPEC])
        _install_runtime_support()

    def hard_schemas(self: Any) -> list[dict[str, Any]]:
        schemas = original_schemas(self)
        if not any(
            isinstance(item, dict)
            and isinstance(item.get("function"), dict)
            and item["function"].get("name") == _SLEEP_CONDITIONED_NAME
            for item in schemas
        ):
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": _SLEEP_CONDITIONED_NAME,
                        "description": _SLEEP_CONDITIONED_SPEC["description"],
                        "parameters": _SLEEP_CONDITIONED_SPEC["parameters"],
                    },
                }
            )
        return schemas

    def visible_pending_feedback(
        self: store_mod.AgentStore,
        thread_id: str | None = None,
    ) -> dict[str, Any] | None:
        item = original_pending_feedback(self, thread_id)
        if not item:
            return item
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        statement = str(context.get("candidate_statement") or "").strip()
        question = str(item.get("question") or "").strip()
        if (
            context.get("feedback_mode") == "durable_context_confirmation"
            and statement
            and statement.casefold() not in question.casefold()
        ):
            item = dict(item)
            item["question"] = f"{question}\n\n“{statement}”" if question else statement
        return item

    guard._prepare_candidate = hard_prepare
    factory.EnhancedSafeToolExecutor.__init__ = hard_init
    factory.EnhancedSafeToolExecutor.tool_schemas = hard_schemas
    store_mod.AgentStore.pending_feedback = visible_pending_feedback
    _install_runtime_support()
    _INSTALLED = True
