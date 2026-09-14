from __future__ import annotations

import json
import math
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

_INSTALLED = False
_RUNTIME_INSTALLED = False

_CURRENT_TOOL_RESULT_CHARS: ContextVar[int] = ContextVar(
    "vc_agent_tool_result_chars", default=5000
)
_CURRENT_LEDGER_CHARS: ContextVar[int] = ContextVar(
    "vc_agent_ledger_chars", default=7600
)
_CURRENT_LIST_ITEMS: ContextVar[int] = ContextVar(
    "vc_agent_list_items", default=6
)

_DETAIL_LIST_KEYS = {
    "episodes",
    "points",
    "daily",
    "daily_load",
    "event_days",
    "event_episodes",
    "rows",
    "records",
    "samples",
    "matches",
}


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _estimate_payload_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int:
    """Cheap conservative estimate used only for adaptive local context allocation."""

    chars = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        chars += len(str(item.get("role") or "")) + len(str(item.get("content") or ""))
        if item.get("tool_calls"):
            chars += len(_encoded(item.get("tool_calls")))
    if tools:
        chars += len(_encoded(tools))
    return max(1, math.ceil(chars / 3.5) + 192)


def _adaptive_char_budget(
    *,
    num_ctx: int,
    num_predict: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """Return tool-result chars, ledger chars and list sample size from free context."""

    context = max(2048, int(num_ctx or 4096))
    predicted = max(256, int(num_predict or 512))
    estimated = _estimate_payload_tokens(messages, tools)
    reserve = max(predicted + 512, int(context * 0.18))
    free_tokens = max(256, context - estimated - reserve)

    tool_tokens = int(free_tokens * 0.38)
    tool_tokens = max(650, min(12000, tool_tokens, int(context * 0.34)))
    tool_chars = max(2600, tool_tokens * 4)

    ledger_tokens = int(free_tokens * 0.48)
    ledger_tokens = max(1100, min(15000, ledger_tokens, int(context * 0.42)))
    ledger_chars = max(4400, ledger_tokens * 4)

    if tool_tokens >= 7000:
        list_items = 12
    elif tool_tokens >= 3500:
        list_items = 8
    elif tool_tokens >= 1800:
        list_items = 5
    else:
        list_items = 3
    return tool_chars, ledger_chars, list_items


def _sample_list(values: list[Any], limit: int, depth: int) -> Any:
    if len(values) <= limit:
        return [_compact_value(item, limit=limit, depth=depth + 1) for item in values]
    if limit <= 0:
        return {"item_count": len(values), "details_omitted": True}
    head_count = max(1, limit // 2)
    tail_count = max(0, limit - head_count)
    return {
        "item_count": len(values),
        "details_truncated": True,
        "first_items": [
            _compact_value(item, limit=limit, depth=depth + 1)
            for item in values[:head_count]
        ],
        "last_items": [
            _compact_value(item, limit=limit, depth=depth + 1)
            for item in values[-tail_count:]
        ]
        if tail_count
        else [],
    }


def _compact_value(value: Any, *, limit: int, depth: int = 0) -> Any:
    """Preserve summaries/scalars first; bound raw longitudinal detail second."""

    if depth >= 8:
        if isinstance(value, (dict, list)):
            return "[nested detail omitted]"
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, list) and name in _DETAIL_LIST_KEYS:
                result[name] = _sample_list(item, min(limit, 4), depth)
            else:
                result[name] = _compact_value(item, limit=limit, depth=depth + 1)
        return result
    if isinstance(value, list):
        return _sample_list(value, limit, depth)
    if isinstance(value, str):
        string_limit = 1200 if limit >= 8 else 800 if limit >= 4 else 420
        if len(value) > string_limit:
            return value[: string_limit - 14].rstrip() + "… [bounded]"
    return value


def _minimal_value(value: Any, *, depth: int = 0) -> Any:
    """Last-resort valid JSON skeleton that never cuts through a JSON token."""

    if depth >= 7:
        if isinstance(value, dict):
            return {"nested_keys": len(value)}
        if isinstance(value, list):
            return {"item_count": len(value), "details_omitted": True}
        if isinstance(value, str) and len(value) > 220:
            return value[:206] + "… [bounded]"
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, list):
                result[name] = {"item_count": len(item), "details_omitted": True}
            else:
                result[name] = _minimal_value(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return {"item_count": len(value), "details_omitted": True}
    if isinstance(value, str) and len(value) > 300:
        return value[:286] + "… [bounded]"
    return value


def _smart_json_text(value: Any, limit: int | None = None) -> str:
    """Serialize as valid JSON while retaining core summaries before raw detail."""

    target = max(800, int(limit if limit is not None else _CURRENT_TOOL_RESULT_CHARS.get()))
    full = _encoded(value)
    if len(full) <= target:
        return full

    preferred = max(1, _CURRENT_LIST_ITEMS.get())
    attempts: list[int] = []
    for list_limit in (preferred, 8, 5, 3, 1, 0):
        if list_limit in attempts:
            continue
        attempts.append(list_limit)
        compact = _compact_value(value, limit=list_limit)
        if isinstance(compact, dict):
            compact = dict(compact)
            compact["_context_truncation"] = {
                "truncated": True,
                "strategy": "summary_first",
                "original_chars": len(full),
                "budget_chars": target,
            }
        encoded = _encoded(compact)
        if len(encoded) <= target:
            return encoded

    minimal = _minimal_value(value)
    if isinstance(minimal, dict):
        minimal = dict(minimal)
        minimal["_context_truncation"] = {
            "truncated": True,
            "strategy": "summary_only",
            "original_chars": len(full),
            "budget_chars": target,
        }
    encoded = _encoded(minimal)
    if len(encoded) <= target:
        return encoded

    return _encoded(
        {
            "truncated": True,
            "notice": "Structured result exceeded the available context budget.",
            "original_chars": len(full),
            "budget_chars": target,
        }
    )


def _select_exact_match(result: Any, capability: str) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    wanted = str(capability or "").strip().casefold()
    if not wanted:
        return None
    matches = [
        item
        for item in result.get("matches", [])
        if isinstance(item, dict)
        and str(item.get("status") or "").casefold() == "active"
        and str(item.get("capability") or "").strip().casefold() == wanted
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda item: (
            str(item.get("kind") or "").casefold() == "learned",
            float(item.get("similarity") or 0.0),
        ),
        reverse=True,
    )
    return dict(matches[0])


def _result_successful(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    status = str(result.get("status") or "").casefold()
    nested = result.get("result")
    if not status and isinstance(nested, dict):
        status = str(nested.get("status") or "").casefold()
    return status not in {
        "invalid_arguments",
        "invalid_spec",
        "invalid_pipeline",
        "error",
        "failed",
    }


def _install_runtime_patch() -> None:
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return

    from . import agent_runtime as base_rt
    from . import agent_runtime_v2 as runtime_v2
    from . import agent_tool_factory as factory

    original_execute = factory.EnhancedSafeToolExecutor.execute
    original_chat = runtime_v2.AgentRuntime._chat_once

    def adaptive_execute(
        self: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        result = original_execute(self, name, args, thread_id=thread_id)
        if name == "search_tool_registry":
            exact = _select_exact_match(result, str(args.get("capability") or ""))
            self._vc_exact_registry_executed = False
            self._vc_exact_registry_finalized = False
            if exact:
                self._vc_exact_registry_match_name = str(exact.get("name") or "")
                self._vc_exact_registry_match_capability = str(exact.get("capability") or "")
                self._vc_exact_registry_pending = bool(self._vc_exact_registry_match_name)
                result = dict(result)
                result["exact_active_match"] = exact
                result["rule"] = (
                    "An exact active capability already exists. Reuse that exact tool before any "
                    "more generic primitive or new Tool Factory creation."
                )
            else:
                self._vc_exact_registry_match_name = ""
                self._vc_exact_registry_match_capability = ""
                self._vc_exact_registry_pending = False
        elif name == str(getattr(self, "_vc_exact_registry_match_name", "")):
            if _result_successful(result):
                self._vc_exact_registry_pending = False
                self._vc_exact_registry_executed = True
            else:
                self._vc_exact_registry_pending = True
                self._vc_exact_registry_executed = False
        return result

    def adaptive_chat(self: Any, **kwargs: Any) -> dict[str, Any]:
        messages = list(kwargs.get("messages") or [])
        tools = list(kwargs.get("tools") or [])
        tool_chars, ledger_chars, list_items = _adaptive_char_budget(
            num_ctx=int(kwargs.get("num_ctx") or 4096),
            num_predict=int(kwargs.get("num_predict") or 512),
            messages=messages,
            tools=tools,
        )
        _CURRENT_TOOL_RESULT_CHARS.set(tool_chars)
        _CURRENT_LEDGER_CHARS.set(ledger_chars)
        _CURRENT_LIST_ITEMS.set(list_items)

        exact_name = str(getattr(self.tools, "_vc_exact_registry_match_name", "") or "")
        exact_capability = str(
            getattr(self.tools, "_vc_exact_registry_match_capability", "") or ""
        )
        exact_pending = bool(getattr(self.tools, "_vc_exact_registry_pending", False))
        exact_executed = bool(getattr(self.tools, "_vc_exact_registry_executed", False))
        exact_finalized = bool(getattr(self.tools, "_vc_exact_registry_finalized", False))

        if exact_name and exact_pending:
            self._last_factory_outcome.update(
                {
                    "status": "reused",
                    "persisted": True,
                    "executed": False,
                    "tool_name": exact_name,
                    "capability": exact_capability,
                    "attempts": 0,
                }
            )
            exact_schema = next(
                (
                    schema
                    for schema in tools
                    if isinstance(schema, dict)
                    and isinstance(schema.get("function"), dict)
                    and schema["function"].get("name") == exact_name
                ),
                None,
            )
            if exact_schema is None:
                exact_schema = next(
                    (
                        schema
                        for schema in self.tools.tool_schemas()
                        if isinstance(schema, dict)
                        and isinstance(schema.get("function"), dict)
                        and schema["function"].get("name") == exact_name
                    ),
                    None,
                )
            if exact_schema is not None:
                forced_messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "RUNTIME EXACT TOOL REUSE: the registry contains an active tool whose "
                            "capability exactly matches this reusable request. Call "
                            f"{exact_name} now. Do not substitute a more generic primitive and do not "
                            "create a duplicate tool."
                        ),
                    },
                ]
                forced_kwargs = dict(kwargs)
                forced_kwargs["messages"] = forced_messages
                forced_kwargs["tools"] = [exact_schema]
                message = original_chat(self, **forced_kwargs)
                calls = message.get("tool_calls") if isinstance(message, dict) else None
                if isinstance(calls, list) and calls:
                    return message

                retry_kwargs = dict(forced_kwargs)
                retry_kwargs["messages"] = [
                    *forced_messages,
                    {
                        "role": "system",
                        "content": (
                            "The previous direct answer is rejected because the exact reusable tool "
                            f"has not been executed. Call {exact_name} with the requested period now."
                        ),
                    },
                ]
                return original_chat(self, **retry_kwargs)

        if exact_name and exact_executed and not exact_finalized:
            self._last_factory_outcome.update(
                {
                    "status": "reused",
                    "persisted": True,
                    "executed": True,
                    "tool_name": exact_name,
                    "capability": exact_capability,
                    "attempts": 0,
                }
            )
            self.tools._vc_exact_registry_finalized = True
            final_kwargs = dict(kwargs)
            final_kwargs["tools"] = []
            final_kwargs["messages"] = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "RUNTIME EXACT TOOL REUSE COMPLETE: the exact registry tool "
                        f"{exact_name} has been executed successfully. Answer now from that result. "
                        "Do not call a broader primitive and do not reopen Tool Factory creation."
                    ),
                },
            ]
            return original_chat(self, **final_kwargs)

        return original_chat(self, **kwargs)

    def adaptive_evidence_entry(
        name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> dict[str, Any]:
        entry_budget = max(1800, min(18000, _CURRENT_TOOL_RESULT_CHARS.get() // 2))
        result_text = _smart_json_text(result, int(entry_budget * 0.82))
        try:
            compact_result = json.loads(result_text)
        except ValueError:
            compact_result = {"notice": "Evidence could not be serialized safely."}
        return {
            "tool": name,
            "arguments": deepcopy(arguments),
            "result": compact_result,
        }

    def adaptive_incremental_messages(
        system_prompt: str,
        safe_history: list[dict[str, str]],
        user_content: str,
        evidence: list[dict[str, Any]],
        runtime_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ledger = list(evidence[-runtime_v2.MAX_EVIDENCE_ENTRIES :])
        ledger_text = _smart_json_text(ledger, _CURRENT_LEDGER_CHARS.get())
        state_text = _smart_json_text(runtime_state, 2600)
        return [
            {"role": "system", "content": system_prompt},
            *safe_history,
            {"role": "user", "content": user_content},
            {
                "role": "system",
                "content": (
                    "DETERMINISTIC EVIDENCE LEDGER (summary-first; omitted detail is unknown):\n"
                    + ledger_text
                    + "\nRUNTIME STATE:\n"
                    + state_text
                    + "\nChoose only the next necessary action. Do not repeat completed calls."
                ),
            },
        ]

    factory.EnhancedSafeToolExecutor.execute = adaptive_execute
    runtime_v2.AgentRuntime._chat_once = adaptive_chat
    runtime_v2._evidence_entry = adaptive_evidence_entry
    runtime_v2._incremental_messages = adaptive_incremental_messages
    base_rt._json_text = _smart_json_text
    _RUNTIME_INSTALLED = True


def install_agent_runtime_adaptive_patch() -> None:
    """Install after the existing runtime-efficiency patch so adaptive limits win."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    original_init = factory.EnhancedSafeToolExecutor.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _install_runtime_patch()

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    _INSTALLED = True
