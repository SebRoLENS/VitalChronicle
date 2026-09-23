from __future__ import annotations

import math
import re
import statistics
from copy import deepcopy
from datetime import date
from typing import Any

_INSTALLED = False

_SLEEP_TOTAL_ALIASES = {
    "sleep",
    "sleep_total",
    "sleep total",
    "total_sleep",
    "total sleep",
    "sleep_duration",
    "sleep duration",
    "sleep_hours",
    "sleep hours",
    "sonno",
    "sonno_totale",
    "sonno totale",
    "durata sonno",
}

_GAP_MARKERS = (
    "non posso calcolare",
    "non posso determinare",
    "non ho accesso",
    "mi manca",
    "manca la baseline",
    "manca un tool",
    "manca uno strumento",
    "lo strumento non",
    "servirebbe calcolare",
    "servirebbe uno strumento",
    "cannot calculate",
    "cannot determine",
    "i don't have access",
    "i do not have access",
    "missing baseline",
    "missing tool",
    "the tool does not",
    "would need to calculate",
)

_GAP_CAPABILITY_MARKERS = (
    "baseline",
    "mediana",
    "media",
    "percent",
    "%",
    "variaz",
    "delta",
    "correl",
    "confront",
    "metrica",
    "metric",
    "tool",
    "strument",
    "calcol",
    "calculate",
)

_SELF_REPORT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sleep_quality",
        (
            r"\b(?:ho\s+)?dormit[oa]\s+(?:molto\s+)?(?:bene|male|meglio|peggio)\b",
            r"\bsonno\s+(?:molto\s+)?(?:buono|cattivo|ottimo|pessimo)\b",
            r"\bslept\s+(?:very\s+)?(?:well|badly|better|worse)\b",
        ),
    ),
    (
        "appetite",
        (
            r"\b(?:meno|pi[uù]|molta|poca|poco)?\s*fame\b",
            r"\bappetito\b",
            r"\b(?:less|more|low|high)\s+(?:hunger|appetite)\b",
        ),
    ),
    (
        "pain",
        (
            r"\b(?:non\s+ho|ho|sento|senza)\s+(?:alcun\s+)?dolor[ei]\b",
            r"\bmal\s+di\b",
            r"\b(?:no|less|more)\s+pain\b",
            r"\b(?:i\s+have|i've\s+got)\s+pain\b",
        ),
    ),
    (
        "fatigue",
        (
            r"\b(?:mi\s+sento|sono)\s+(?:meno\s+|pi[uù]\s+)?(?:stanc[oa]|affaticat[oa])\b",
            r"\b(?:less|more)?\s*(?:tired|fatigued)\b",
        ),
    ),
    (
        "sleepiness",
        (
            r"\bho\s+sonno\b",
            r"\b(?:assonnat[oa]|sonnolenz[ao])\b",
            r"\bsleepy\b",
        ),
    ),
    (
        "stress",
        (
            r"\b(?:mi\s+sento|sono)\s+(?:meno\s+|pi[uù]\s+)?stressat[oa]\b",
            r"\b(?:less|more)?\s*stressed\b",
        ),
    ),
    (
        "energy",
        (
            r"\b(?:mi\s+sento|sono)\s+(?:pi[uù]\s+|meno\s+)?energic[oa]\b",
            r"\b(?:pi[uù]|meno)\s+energia\b",
            r"\b(?:more|less)\s+energy\b",
        ),
    ),
)


def _daily_normalized_points(
    base: Any,
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    groups: dict[str, list[tuple[float, float]]] = {}
    for ts, value in points:
        try:
            number = float(value)
            stamp = float(ts)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        groups.setdefault(base._day(stamp), []).append((stamp, number))
    result: list[tuple[float, float]] = []
    for day in sorted(groups):
        rows = groups[day]
        result.append((min(ts for ts, _ in rows), statistics.fmean(value for _, value in rows)))
    return result


def _sleep_total_series(
    executor: Any,
    metric: str,
    left: date,
    right: date,
) -> dict[str, Any]:
    from . import agent_tools as base

    records = executor._semantic_records("sleep", left, right)
    by_day: dict[str, list[tuple[float, float]]] = {}
    for record in records:
        hours = base.duration_hours(record)
        timestamp = base._record_semantic_timestamp(record, "sleep")
        if hours is None or timestamp is None:
            continue
        try:
            value = float(hours)
            stamp = float(timestamp)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        by_day.setdefault(base._day(stamp), []).append((stamp, value))
    points: list[tuple[float, float]] = []
    for day in sorted(by_day):
        rows = by_day[day]
        # Multiple sleep sessions on the same wake-up date contribute to total sleep for that day.
        points.append((max(ts for ts, _ in rows), sum(value for _, value in rows)))
    return {
        "metric": metric,
        "canonical_metric": "sleep:__duration_hours__",
        "data_type": "sleep",
        "field": "__duration_hours__",
        "points": points,
        "unit": "h",
        "aggregation": "mean",
        "date_semantics": "wake_up_date",
    }


def _enrich_threshold_result(executor: Any, args: dict[str, Any], result: Any) -> Any:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result
    from . import agent_tool_factory as factory
    from . import agent_tool_factory_reliability_patch as reliability

    enriched = deepcopy(result)
    left, right = factory.base._bounds(args.get("start"), args.get("end"), 60)
    baseline_field = str(
        enriched.get("baseline_field") or args.get("baseline_field") or "median"
    )
    if baseline_field not in {"median", "mean"}:
        baseline_field = "median"

    baselines: dict[str, float | None] = {}
    response_metrics = list((enriched.get("responses") or {}).keys())
    for metric in response_metrics:
        daily, _raw = reliability._daily_series(executor, metric, left, right)
        values = [float(value) for value in daily.values() if math.isfinite(float(value))]
        baseline = None
        if values:
            baseline = (
                statistics.median(values)
                if baseline_field == "median"
                else statistics.fmean(values)
            )
        baselines[metric] = baseline
        meta = enriched.get("responses", {}).get(metric)
        if isinstance(meta, dict):
            meta["baseline_field"] = baseline_field
            meta["baseline"] = None if baseline is None else round(float(baseline), 6)

    for row in enriched.get("event_days", []):
        if not isinstance(row, dict):
            continue
        values = row.get("responses") if isinstance(row.get("responses"), dict) else {}
        changes: dict[str, Any] = {}
        for metric, raw_value in values.items():
            baseline = baselines.get(metric)
            value = None
            try:
                value = None if raw_value is None else float(raw_value)
            except (TypeError, ValueError):
                value = None
            delta = None if value is None or baseline is None else value - baseline
            percent = (
                None
                if delta is None or baseline is None or abs(float(baseline)) < 1e-12
                else delta / abs(float(baseline)) * 100.0
            )
            changes[metric] = {
                "value": value,
                "baseline": None if baseline is None else round(float(baseline), 6),
                "baseline_field": baseline_field,
                "delta_absolute": None if delta is None else round(float(delta), 6),
                "delta_percent": None if percent is None else round(float(percent), 3),
            }
        row["response_changes"] = changes

    enriched["method"] = str(enriched.get("method") or "") + (
        " Response metrics also include their personal baseline and per-event absolute/percentage "
        "change from that baseline."
    )
    return enriched


def _looks_like_runtime_capability_gap(content: str) -> bool:
    text = str(content or "").casefold()
    return bool(
        text
        and any(marker in text for marker in _GAP_MARKERS)
        and any(marker in text for marker in _GAP_CAPABILITY_MARKERS)
    )


def _supplement_factory_hint(question: str, hint: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(hint)
    text = str(question or "").casefold()
    signals = [str(item) for item in result.get("signals", [])]
    percentages = re.findall(r"\d+(?:[\.,]\d+)?\s*%", text)
    relative_wording = any(
        marker in text
        for marker in (
            "del solito",
            "rispetto al solito",
            "rispetto alla mia",
            "rispetto al mio",
            "più del",
            "piu del",
            "sopra la mia",
            "sotto la mia",
            "above my",
            "below my",
            "than usual",
            "personal baseline",
        )
    )
    cross_metric = any(marker in text for marker in ("hrv", "variabil")) and any(
        marker in text for marker in ("sonno", "dorm", "sleep")
    )
    if (len(percentages) >= 2 or (percentages and relative_wording)) and cross_metric:
        for signal in ("relative personal-baseline threshold", "threshold/frequency analysis"):
            if signal not in signals:
                signals.append(signal)
        result["signals"] = signals
        result["consider_reusable_tool"] = True
        result["instruction"] = (
            "This is a cross-metric personal-baseline percentage comparison. Prefer the exact "
            "analyze_metric_threshold_responses capability; do not fall back to independent raw "
            "series if the composed deterministic tool can answer it."
        )
    return result


def _self_report_candidates(text: str) -> list[dict[str, str]]:
    from .agent_store import normalize_self_report_statement

    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+|(?<=;)\s+", str(text or "").strip())
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for sentence in sentences:
        clean = sentence.strip(" \t\r\n.;")
        if not clean or clean.endswith(("?", "？")):
            continue
        folded = clean.casefold()
        for category, patterns in _SELF_REPORT_RULES:
            if not any(re.search(pattern, folded, flags=re.IGNORECASE) for pattern in patterns):
                continue
            statement = normalize_self_report_statement(clean)
            key = (category, statement.casefold())
            if statement and key not in seen:
                seen.add(key)
                results.append({"category": category, "statement": statement})
            break
    return results


def _install_executor_patch() -> None:
    from . import agent_tools as base
    from . import agent_tool_factory as factory
    from . import agent_tool_factory_reliability_patch as reliability

    cls = factory.EnhancedSafeToolExecutor
    if getattr(cls, "_quality_series_patch_installed", False):
        return

    original_series = cls._series
    original_threshold = getattr(cls, f"_tool_{reliability._BUILTIN_NAME}")

    def quality_series(self: Any, metric: str, left: date, right: date) -> dict[str, Any]:
        raw_name = str(metric or "").strip()
        if ":" not in raw_name and raw_name.casefold() in _SLEEP_TOTAL_ALIASES:
            return _sleep_total_series(self, raw_name, left, right)
        result = original_series(self, metric, left, right)
        if not isinstance(result, dict):
            return result
        result = dict(result)
        points = list(result.get("points") or [])
        data_type = str(result.get("data_type") or "")
        if data_type.startswith("daily-") and points:
            normalized = _daily_normalized_points(base, points)
            if len(normalized) != len(points):
                result["points"] = normalized
                result["daily_normalized"] = True
                result["duplicate_daily_observations_collapsed"] = len(points) - len(normalized)
        return result

    def quality_threshold(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return _enrich_threshold_result(self, args, original_threshold(self, args, **kwargs))

    cls._series = quality_series
    setattr(cls, f"_tool_{reliability._BUILTIN_NAME}", quality_threshold)
    cls._quality_series_patch_installed = True


def _install_runtime_patch() -> None:
    from . import agent_runtime_v2 as runtime_v2
    from . import agent_runtime_efficiency_patch as efficiency

    if getattr(runtime_v2.AgentRuntime, "_quality_runtime_patch_installed", False):
        return

    original_hint = runtime_v2._factory_hint
    original_chat = runtime_v2.AgentRuntime._chat_once
    original_analyze = runtime_v2.AgentRuntime.analyze
    original_detect_self_report = runtime_v2._detect_self_report

    def quality_hint(question: str) -> dict[str, Any]:
        return _supplement_factory_hint(question, original_hint(question))

    def adaptive_predict_cap(num_ctx: int, has_tools: bool) -> int:
        context = max(1, int(num_ctx or 4096))
        if has_tools:
            if context <= 8192:
                return 900
            if context <= 16384:
                return 1600
            if context <= 32768:
                return 2800
            if context <= 65536:
                return 4096
            return 6144
        if context <= 8192:
            return 1800
        if context <= 16384:
            return 2800
        if context <= 32768:
            return 4096
        if context <= 65536:
            return 6144
        return 8192

    efficiency._agent_predict_cap = adaptive_predict_cap

    def quality_detect_self_report(question: str) -> dict[str, str] | None:
        candidates = _self_report_candidates(question)
        return candidates[0] if candidates else original_detect_self_report(question)

    def gap_aware_chat(self: Any, **kwargs: Any) -> dict[str, Any]:
        message = original_chat(self, **kwargs)
        tools = list(kwargs.get("tools") or [])
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        if (
            not tools
            or (isinstance(calls, list) and calls)
            or not _looks_like_runtime_capability_gap(content)
        ):
            return message
        allowed_names = {
            "search_tool_registry",
            "create_learned_tool",
            "analyze_metric_threshold_responses",
            "get_baseline",
            "get_metric_series",
            "get_daily_summary",
        }
        retry_tools = [
            schema
            for schema in tools
            if isinstance(schema, dict)
            and isinstance(schema.get("function"), dict)
            and str(schema["function"].get("name") or "") in allowed_names
        ] or tools
        retry_kwargs = dict(kwargs)
        retry_kwargs["tools"] = retry_tools
        retry_kwargs["messages"] = [
            *list(kwargs.get("messages") or []),
            {"role": "assistant", "content": content},
            {
                "role": "system",
                "content": (
                    "RUNTIME CAPABILITY-GAP CHECKPOINT: your draft says a calculation/tool/baseline is "
                    "missing, but the needed quantity appears derivable from local data. Do not finalise "
                    "yet. Re-plan now: reuse an exact composed tool if available; otherwise search the "
                    "registry and create/extend a safe reusable learned tool. Execute the resulting tool "
                    "before answering. Only keep the limitation if deterministic derivation is genuinely "
                    "impossible or Tool Factory creation is unavailable/invalid."
                ),
            },
        ]
        retried = original_chat(self, **retry_kwargs)
        retry_calls = retried.get("tool_calls") if isinstance(retried, dict) else None
        return retried if isinstance(retry_calls, list) and retry_calls else message

    def capture_additional_self_reports(self: Any, *args: Any, **kwargs: Any) -> str:
        question = str(kwargs.get("question") or "")
        thread_id = kwargs.get("thread_id")
        primary = quality_detect_self_report(question)
        primary_key = None
        if primary:
            primary_key = (
                str(primary.get("category") or ""),
                str(primary.get("statement") or "").casefold(),
            )
        for candidate in _self_report_candidates(question):
            candidate_key = (candidate["category"], candidate["statement"].casefold())
            if candidate_key == primary_key:
                continue
            self.agent_store.record_self_report(
                candidate["statement"],
                category=candidate["category"],
                thread_id=thread_id,
                context={
                    "source": "conversation",
                    "explicit_self_report": True,
                    "auto_captured": True,
                },
            )
        return original_analyze(self, *args, **kwargs)

    runtime_v2._factory_hint = quality_hint
    runtime_v2._detect_self_report = quality_detect_self_report
    runtime_v2.AgentRuntime._chat_once = gap_aware_chat
    runtime_v2.AgentRuntime.analyze = capture_additional_self_reports
    runtime_v2._SELF_REPORT_CATEGORY_TOPICS.setdefault("appetite", {"recovery"})
    runtime_v2._SELF_REPORT_CATEGORY_TOPICS.setdefault("pain", {"recovery", "training"})
    if "Any personalisation question" not in runtime_v2._PERSONALIZATION_POLICY:
        runtime_v2._PERSONALIZATION_POLICY += (
            "\n- Any personalisation question that expects an answer must be queued with "
            "ask_user_feedback so the desktop feedback banner can display it. Never leave such a "
            "question only in free-form final prose.\n"
        )
    if "capability gap discovered during execution" not in runtime_v2._FACTORY_POLICY:
        runtime_v2._FACTORY_POLICY += (
            "\n- A capability gap discovered during execution is still a Tool Factory trigger. If "
            "you find yourself saying a baseline, percentage transformation, join or reusable calculation "
            "is missing but derivable from available local data, re-plan and create/extend/reuse a tool "
            "before finalising.\n"
        )
    runtime_v2.AgentRuntime._quality_runtime_patch_installed = True


def install_agent_quality_patch() -> None:
    """Install reliability fixes discovered from real desktop agent traces."""

    global _INSTALLED
    if _INSTALLED:
        return
    _install_executor_patch()
    _install_runtime_patch()
    _INSTALLED = True
