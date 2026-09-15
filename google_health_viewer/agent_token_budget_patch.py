from __future__ import annotations

from collections.abc import Callable
from typing import Any

_INSTALLED = False


def adaptive_agent_predict_cap(num_ctx: int, has_tools: bool) -> int:
    """Allow larger contexts to use enough output tokens without forcing verbosity."""

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


def install_agent_token_budget_patch() -> None:
    """Replace legacy nested output caps without importing the Qt runtime eagerly."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_runtime_efficiency_patch as efficiency
    from . import agent_tool_factory_reliability_patch as reliability

    original_installer = reliability._install_runtime_budget

    def adaptive_installer() -> None:
        if reliability._RUNTIME_INSTALLED:
            return

        # This function runs lazily when the personal agent is actually created. Importing the
        # Qt-dependent runtime here preserves headless-safe package imports and test tooling.
        from . import agent_runtime as base_rt

        raw_chat = base_rt.AgentRuntime._chat_once
        original_installer()

        def adaptive_budgeted_chat(
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
            cap = adaptive_agent_predict_cap(num_ctx, bool(tools))
            requested = max(256, int(num_predict or cap))
            message = raw_chat(
                self,
                model=model,
                messages=messages,
                tools=tools,
                num_ctx=num_ctx,
                num_predict=min(requested, cap),
                think=think,
                cancel_callback=cancel_callback,
            )
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if not isinstance(calls, list) or not calls:
                return message

            heavy = [
                call
                for call in calls
                if isinstance(call, dict)
                and base_rt._tool_name(call) in reliability._HEAVY_TOOLS
            ]
            context = max(1, int(num_ctx or 4096))
            kept = heavy[:1] if heavy else calls[: (1 if context <= 8192 else 2)]
            if len(kept) == len(calls):
                return message
            trimmed = dict(message)
            trimmed["tool_calls"] = kept
            return trimmed

        base_rt.AgentRuntime._chat_once = adaptive_budgeted_chat

    reliability._install_runtime_budget = adaptive_installer
    efficiency._agent_predict_cap = adaptive_agent_predict_cap
    _INSTALLED = True
