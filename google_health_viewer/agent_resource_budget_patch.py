from __future__ import annotations

from typing import Any, Callable

_INSTALLED = False

_HEAVY_TOOLS = {
    "get_metric_series",
    "get_daily_summary",
    "get_sleep_stage_series",
    "calculate_cardio_load",
    "analyze_sleep_stages",
    "analyze_workout",
    "analyze_training_progression",
    "analyze_metric_threshold_responses",
}


def install_agent_resource_budget_patch() -> None:
    """Bound per-turn work so the local agent remains usable on modest hardware.

    The goal is not to remove analytical depth: it is to make the agent acquire evidence in
    smaller deterministic chunks, especially for large metric series, and to avoid repeatedly
    carrying oversized tool schemas/results through every model turn.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_runtime as base_rt
    from . import agent_runtime_v2 as runtime_v2

    original_subset = base_rt.online_tool_subset
    original_json_text = base_rt._json_text
    original_history = base_rt.compact_agent_history
    original_chat_once = base_rt.AgentRuntime._chat_once

    def compact_subset(
        schemas: list[dict[str, Any]],
        request: str,
        *,
        maximum: int = 10,
        required_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Ten schemas normally retain the core registry/factory/data readers plus only the
        # most relevant domain tool. Learned tools explicitly required by the runtime remain kept.
        return original_subset(
            schemas,
            request,
            maximum=min(10, max(1, int(maximum))),
            required_names=required_names,
        )

    def compact_json_text(value: Any, limit: int = 6000) -> str:
        return original_json_text(value, min(max(800, int(limit)), 6000))

    def compact_history(
        history: list[dict[str, str]] | None,
        *,
        maximum: int = 3,
        message_limit: int = 750,
    ) -> list[dict[str, str]]:
        return original_history(
            history,
            maximum=min(3, max(1, int(maximum))),
            message_limit=min(750, max(200, int(message_limit))),
        )

    def budgeted_chat_once(
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
            predict_cap = 768 if context <= 8192 else 1024 if context <= 16384 else 1200
        else:
            predict_cap = 1800 if context <= 8192 else 2400 if context <= 16384 else 3000

        message = original_chat_once(
            self,
            model=model,
            messages=messages,
            tools=tools,
            num_ctx=num_ctx,
            num_predict=min(max(256, int(num_predict)), predict_cap),
            think=think,
            cancel_callback=cancel_callback,
        )

        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        if not tool_calls:
            return message

        def call_name(call: Any) -> str:
            return base_rt._tool_name(call) if isinstance(call, dict) else ""

        # Large raw/derived series are deliberately isolated. For lightweight operations two calls
        # may proceed together; on <=8K contexts even those are serialized one per model turn.
        heavy_indexes = [index for index, call in enumerate(tool_calls) if call_name(call) in _HEAVY_TOOLS]
        if heavy_indexes:
            kept = [tool_calls[heavy_indexes[0]]]
        else:
            limit = 1 if context <= 8192 else 2
            kept = tool_calls[:limit]

        if len(kept) == len(tool_calls):
            return message
        trimmed = dict(message)
        trimmed["tool_calls"] = kept
        return trimmed

    base_rt.online_tool_subset = compact_subset
    base_rt._json_text = compact_json_text
    base_rt.compact_agent_history = compact_history
    base_rt.AgentRuntime._chat_once = budgeted_chat_once

    # These globals are read dynamically by the v2 evidence compactor. Lower values prevent a
    # large metric result from being replayed verbatim through many later turns.
    runtime_v2.MAX_EVIDENCE_ENTRIES = 5
    runtime_v2.MAX_EVIDENCE_LIST_ITEMS = 12
    runtime_v2.MAX_EVIDENCE_ENTRY_CHARS = 3600
    runtime_v2.MAX_EVIDENCE_LEDGER_CHARS = 8500

    _INSTALLED = True
