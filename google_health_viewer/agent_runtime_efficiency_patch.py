from __future__ import annotations

from copy import deepcopy
from typing import Any

_INSTALLED = False
_RUNTIME_INSTALLED = False


def _agent_predict_cap(num_ctx: int, has_tools: bool) -> int:
    context = max(1, int(num_ctx))
    if has_tools:
        return 640 if context <= 8192 else 900 if context <= 16384 else 1100
    return 1600 if context <= 8192 else 2200 if context <= 16384 else 2800


def _snapshot_metric_seed(item: dict[str, Any]) -> dict[str, Any]:
    derived = item.get("derived_evidence")
    derived = derived if isinstance(derived, dict) else {}
    summary = item.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    compact_summary = {
        key: summary.get(key)
        for key in (
            "count",
            "latest",
            "mean",
            "median",
            "minimum",
            "maximum",
            "trend_percent",
        )
        if key in summary
    }
    compact_derived: dict[str, Any] = {}
    for key in (
        "personal_baselines",
        "matched_recent_comparison",
        "trend",
        "robust_anomaly_check",
        "data_quality",
    ):
        value = derived.get(key)
        if value:
            compact_derived[key] = deepcopy(value)
    result = {
        "data_type": item.get("data_type"),
        "label": item.get("label"),
        "unit": item.get("unit"),
    }
    if compact_summary:
        result["summary"] = compact_summary
    if compact_derived:
        result["derived_evidence"] = compact_derived
    structured = item.get("structured_period_comparison")
    if isinstance(structured, dict) and structured:
        result["structured_period_comparison"] = deepcopy(structured)
    return result


def _compact_snapshot_seed(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Reuse small deterministic facts that Python already prepared for this conversation."""

    snapshot = snapshot if isinstance(snapshot, dict) else {}
    metrics = [
        _snapshot_metric_seed(item)
        for item in snapshot.get("metrics", [])[:10]
        if isinstance(item, dict)
    ]
    candidate_insights = [
        deepcopy(item)
        for item in snapshot.get("candidate_insights", [])[:4]
        if isinstance(item, dict)
    ]
    seed: dict[str, Any] = {}
    analysis_brief = snapshot.get("analysis_brief")
    if isinstance(analysis_brief, dict) and analysis_brief:
        seed["analysis_brief"] = deepcopy(analysis_brief)
    if metrics:
        seed["prepared_metrics"] = metrics
    if candidate_insights:
        seed["candidate_insights"] = candidate_insights
    return seed


def _supplement_factory_hint(question: str, hint: dict[str, Any]) -> dict[str, Any]:
    """Recognise baseline wording the original Italian heuristic missed."""

    result = deepcopy(hint)
    text = str(question or "").casefold()
    signals = [str(item) for item in result.get("signals", [])]
    personal_median = any(
        marker in text
        for marker in (
            "mediana personale",
            "mia mediana",
            "mio valore mediano",
            "personal median",
        )
    ) or ("mediana" in text and any(marker in text for marker in ("mia", "mio", "personale")))
    threshold = any(
        marker in text
        for marker in ("%", "percent", "supera", "superano", "sotto", "above", "below", "exceed")
    )
    if personal_median and "relative personal-baseline threshold" not in signals:
        signals.append("relative personal-baseline threshold")
    if threshold and "threshold/frequency analysis" not in signals:
        signals.append("threshold/frequency analysis")
    result["signals"] = signals
    result["consider_reusable_tool"] = len(signals) >= 2
    if result["consider_reusable_tool"]:
        result["instruction"] = (
            "A reusable deterministic transformation is indicated. Search the registry first and "
            "prefer the exact composite capability over re-reading independent raw series."
        )
    return result


def _required_composite_tools(request: str) -> set[str]:
    text = str(request or "").casefold()
    names: set[str] = set()
    baseline_signal = any(
        marker in text
        for marker in (
            "baseline",
            "basale",
            "mediana personale",
            "mia mediana",
            "media personale",
            "personal median",
            "personal baseline",
        )
    )
    threshold_signal = any(
        marker in text for marker in ("%", "percent", "supera", "superano", "above", "below", "exceed")
    )
    recovery_signal = any(
        marker in text
        for marker in ("recuper", "torna", "tornano", "ritorna", "return to", "recovery time")
    )
    if baseline_signal and threshold_signal:
        names.add("analyze_metric_threshold_responses")
    if recovery_signal:
        names.add("analyze_post_event_recovery")
    return names


def _install_runtime_efficiency() -> None:
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return

    from . import agent_runtime as base_rt
    from . import agent_runtime_v2 as runtime_v2
    from . import agent_tool_factory_reliability_patch as reliability

    # Install the original resource patch first. In the desktop integration a reasoning-compatibility
    # wrapper may already have captured the old base method, so we also enforce the cap directly on
    # the concrete v2 runtime below.
    reliability._install_runtime_budget()

    original_chat = runtime_v2.AgentRuntime._chat_once

    def capped_chat(self: Any, **kwargs: Any) -> dict[str, Any]:
        cap = _agent_predict_cap(
            int(kwargs.get("num_ctx") or 4096),
            bool(kwargs.get("tools")),
        )
        requested = max(1, int(kwargs.get("num_predict") or cap))
        kwargs["num_predict"] = min(requested, cap)
        return original_chat(self, **kwargs)

    runtime_v2.AgentRuntime._chat_once = capped_chat

    original_initial_context = base_rt.AgentRuntime._initial_context

    def seeded_initial_context(self: Any, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        result = original_initial_context(self, snapshot)
        seed = _compact_snapshot_seed(snapshot)
        if seed:
            result["precomputed_deterministic_evidence"] = seed
            result["precomputed_evidence_rule"] = (
                "Python already prepared these deterministic facts for this conversation. Reuse them; "
                "do not call baseline/summary/raw-series tools merely to rediscover the same facts. "
                "Call a tool only for a missing transformation or missing detail."
            )
        return result

    base_rt.AgentRuntime._initial_context = seeded_initial_context

    original_hint = runtime_v2._factory_hint

    def supplemented_hint(question: str) -> dict[str, Any]:
        return _supplement_factory_hint(question, original_hint(question))

    runtime_v2._factory_hint = supplemented_hint

    original_subset = base_rt.online_tool_subset

    def composite_aware_subset(
        schemas: list[dict[str, Any]],
        request: str,
        *,
        maximum: int = 10,
        required_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        required = set(required_names or ()) | _required_composite_tools(request)
        return original_subset(
            schemas,
            request,
            maximum=maximum,
            required_names=required,
        )

    base_rt.online_tool_subset = composite_aware_subset
    _RUNTIME_INSTALLED = True


def install_agent_runtime_efficiency_patch() -> None:
    """Install lazily so importing the package still does not pull Qt into headless tooling."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    original_init = factory.EnhancedSafeToolExecutor.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _install_runtime_efficiency()

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    _INSTALLED = True
