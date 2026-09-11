from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

import requests
from PySide6.QtCore import QSettings, QThread, Signal

from .agent_store import AgentStore
from .agent_tools import SafeToolExecutor
from .ai_engine import TOKEN_USAGE_PREFIX, OptimizedOllamaClient, _request_budget
from .i18n import _, current_language
from .local_ai import (
    AIAnalysisCancelled,
    DEFAULT_OLLAMA_URL,
    LocalAIError,
)
from .online_ai import MISTRAL_API_URL, MistralClient, is_mistral_model, mistral_api_key

MAX_AGENT_STEPS = 10
MAX_TOOL_RESULT_CHARS = 24000
AGENT_TRACE_PREFIX = "__VC_AGENT_TRACE__:"
CALIBRATION_VERSION = 1


AGENT_SYSTEM_PROMPT = """You are VitalChronicle's local personal health agent.
You have deterministic read-only tools over the user's local health archive plus a safe registry
of reusable learned tools. Use tools instead of doing health arithmetic in your head whenever a
deterministic tool can answer the question.

Operating rules:
1. Check actual data coverage before comparing periods. Missing measurements are never zero.
2. Prefer existing built-in or learned tools. Search the tool registry before creating a learned tool.
3. Create a learned tool only for a genuinely reusable capability gap, never just to answer a single
   easy question. Learned tools are declarative pipelines; never request arbitrary code, terminal,
   filesystem, browser, network or database-write access.
4. Built-in tools take precedence over equivalent learned tools. If a capability is already covered,
   reuse or compose it rather than creating a duplicate.
5. An explicit current subjective statement such as feeling tired, sore, sleepy, stressed or unusually energetic
   is a dated self-report event. Store it locally as an event; do not immediately promote one report to a stable trait.
6. Ask at most one targeted follow-up when it would materially improve interpretation of a new self-report. Avoid
   routine or repetitive questionnaires. Follow-up details remain attached to that dated report unless repeated evidence
   later supports a genuine association.
7. Learned personal context has time semantics. Temporary context such as "recently restarted training", current goals
   or a short-lived schedule change must lose weight with age and stop being used after its validity window. Never use
   expired context as if it were current. Stable preferences/associations require repeated evidence or an explicitly
   stable user statement. Subjective context never proves physiological safety or suppresses objective safety advice.
8. Separate measured observations, deterministic calculations, user-reported context, learned
   associations and possible explanations. Correlation does not prove causation.
9. Never diagnose disease, change treatment, or present wearable-derived scores as medical clearance.
10. Readiness, cardio load, target load, training status and resilience returned by tools are
   transparent VitalChronicle estimates based on personal baselines, not proprietary Google/Fitbit scores.
11. When confidence or coverage is low, state that clearly. A missing/None score component means unavailable evidence, never a neutral or zero value.
12. Current, non-expired personal context and recent subjective self-reports are evidence for personalisation.
    Use materially relevant context in EVERY answer, not only whole-history analyses, while clearly distinguishing
    user-reported context from measured physiology. A one-off self-report may guide a short-term suggestion but must
    never be presented as a stable trait or as proof of causation. If a current observation resembles the situation
    that originally produced a temporary learned context, do not assume the same cause: mention the prior explanation
    and ask one concise contextual question when resolving that uncertainty would improve the advice.
13. Calendar language is metric-aware. Interpret today/yesterday/last night in the user's local calendar and respect
    each tool's date_semantics. Sleep belongs to the local wake-up date: "how did I sleep today?" means the sleep
    session that ended this morning, not a future session beginning tonight. Overnight-derived summaries belong to
    their local session-end date. Intraday cumulative metrics for today may be incomplete and must be labelled partial.

The health archive is read-only to the agent. Learned tools, feedback and personal associations are
stored separately and locally. Use the minimum useful number of tool calls, then answer clearly.
"""


RESPONSE_LANGUAGES = {
    "it": "Italian",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
}


def agent_system_prompt() -> str:
    language = RESPONSE_LANGUAGES.get(current_language(), "English")
    return AGENT_SYSTEM_PROMPT + f"\nRespond to the user in {language}."


def _json_text(value: Any, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 120] + json.dumps(
        {"truncated": True, "notice": "Tool result was bounded for model context."},
        ensure_ascii=False,
    )


def _tool_name(call: dict[str, Any]) -> str:
    function = call.get("function") if isinstance(call, dict) else None
    return str((function or {}).get("name") or call.get("name") or "")


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") if isinstance(call, dict) else None
    raw = (function or {}).get("arguments") if isinstance(function, dict) else call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _tool_calling_unavailable_error(detail: str) -> bool:
    text = str(detail or "").casefold()
    markers = (
        "does not support tools", "doesn't support tools", "tools are not supported",
        "tool calling is not supported", "tool calls are not supported",
        "tool use is not supported", "does not support tool calling",
        "doesn't support tool calling", "does not support function calling",
        "doesn't support function calling", "function calling is not supported",
        "unsupported tool calling", "unsupported function calling",
    )
    return any(marker in text for marker in markers)


class AgentRuntime:
    def __init__(self, health_store, agent_store: AgentStore | None = None) -> None:
        self.health_store = health_store
        self.agent_store = agent_store or AgentStore.beside_health_store(health_store)
        self.tools = SafeToolExecutor(health_store, self.agent_store)
        self._telemetry_callback: Callable[[str], None] | None = None
        self._telemetry_phase = "agent"
        self._agent_model_calls = 0
        self._agent_prompt_tokens_total = 0
        self._agent_generated_tokens_total = 0

    def _reset_agent_telemetry(
        self, callback: Callable[[str], None] | None
    ) -> None:
        self._telemetry_callback = callback
        self._telemetry_phase = "agent"
        self._agent_model_calls = 0
        self._agent_prompt_tokens_total = 0
        self._agent_generated_tokens_total = 0

    def _emit_agent_trace(
        self, source: str, target: str, content: str, *, kind: str = "message"
    ) -> None:
        callback = self._telemetry_callback
        if callback is None:
            return
        callback(
            AGENT_TRACE_PREFIX
            + json.dumps(
                {
                    "kind": kind,
                    "source": source,
                    "target": target,
                    "content": content,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def _emit_agent_usage(
        self, payload: dict[str, Any], *, num_ctx: int, num_predict: int
    ) -> None:
        callback = self._telemetry_callback
        if callback is None:
            return
        try:
            prompt_tokens = max(0, int(payload.get("prompt_eval_count") or 0))
            generated_tokens = max(0, int(payload.get("eval_count") or 0))
            eval_duration = max(0, int(payload.get("eval_duration") or 0))
        except (TypeError, ValueError):
            return
        self._agent_model_calls += 1
        self._agent_prompt_tokens_total += prompt_tokens
        self._agent_generated_tokens_total += generated_tokens
        context = max(1, int(num_ctx))
        context_used = min(context, prompt_tokens + generated_tokens)
        speed = None
        if generated_tokens and eval_duration > 0:
            speed = generated_tokens / max(0.001, eval_duration / 1_000_000_000)
        exact = "prompt_eval_count" in payload and "eval_count" in payload
        callback(
            TOKEN_USAGE_PREFIX
            + json.dumps(
                {
                    "phase": self._telemetry_phase,
                    "agentic": True,
                    "call": self._agent_model_calls,
                    "exact": exact,
                    "input_tokens": prompt_tokens,
                    "generated_tokens": generated_tokens,
                    "total_input_tokens": self._agent_prompt_tokens_total,
                    "total_generated_tokens": self._agent_generated_tokens_total,
                    "total_tokens": (
                        self._agent_prompt_tokens_total + self._agent_generated_tokens_total
                    ),
                    "output_budget": max(1, int(num_predict)),
                    "output_remaining": max(0, int(num_predict) - generated_tokens),
                    "context": context,
                    "context_used": context_used,
                    "context_remaining": max(0, context - context_used),
                    "usage_percent": round(100.0 * context_used / context, 1),
                    "tokens_per_second": (round(speed, 2) if speed is not None else None),
                },
                separators=(",", ":"),
            )
        )

    @property
    def enabled(self) -> bool:
        return QSettings().value("ai/personal_agent_enabled", True, type=bool)

    def set_enabled(self, enabled: bool) -> None:
        QSettings().setValue("ai/personal_agent_enabled", bool(enabled))

    def needs_calibration(self) -> bool:
        return (
            bool(self.health_store.counts())
            and self.agent_store.calibration_version() < CALIBRATION_VERSION
        )

    def _initial_context(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        snapshot = snapshot or {}
        bounds = self.health_store.data_date_bounds()
        local_now = datetime.now().astimezone()
        return {
            "data_revision": self.health_store.data_revision(),
            "local_now": local_now.isoformat(),
            "local_date": local_now.date().isoformat(),
            "calendar_semantics": (
                "Interpret relative dates in the user's local calendar. Sleep and overnight-derived "
                "measurements belong to the date on which the session ended / the user woke up; "
                "respect date_semantics returned by tools. Today may be partial for cumulative intraday metrics."
            ),
            "archive_bounds": (
                {"start": bounds[0].isoformat(), "end": bounds[1].isoformat()} if bounds else None
            ),
            "conversation_scope": snapshot.get("analysis_scope"),
            "requested_interval_coverage": snapshot.get("requested_interval_coverage"),
            "personal_model": self.agent_store.user_model()[:20],
            "recent_self_reports": self.agent_store.recent_self_reports(days=30, limit=20),
            "safe_tool_count": len(self.tools.tool_schemas()),
            "rule": "Use tools for calculations and respect metric-specific coverage.",
        }

    @staticmethod
    def _mistral_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert the Ollama-shaped tool transcript to the Mistral format."""
        converted: list[dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if item.get("role") == "tool" and "tool_name" in item:
                item["tool_call_id"] = str(item.pop("tool_name"))
            converted.append(item)
        return converted

    def _mistral_chat_once(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        num_ctx: int,
        num_predict: int,
        cancel_callback: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        if not mistral_api_key():
            raise LocalAIError(_("Mistral API key is not configured."))
        try:
            response = requests.post(
                MISTRAL_API_URL,
                headers={
                    "Authorization": f"Bearer {mistral_api_key()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": self._mistral_messages(messages),
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                    "max_tokens": num_predict,
                    "temperature": 0.15,
                },
                timeout=(15, 900),
            )
        except requests.RequestException as exc:
            raise LocalAIError(_("Mistral agent request failed: {error}", error=exc)) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise LocalAIError(_("Mistral returned invalid agent JSON.")) from exc
        if response.status_code >= 400:
            error = payload.get("message")
            if not error and isinstance(payload.get("error"), dict):
                error = payload["error"].get("message") or payload["error"].get("type")
            raise LocalAIError(
                _("Mistral agent request failed: {error}", error=error or response.reason)
            )
        choices = payload.get("choices") or []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise LocalAIError(_("Mistral returned no agent message."))
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        telemetry = {
            "prompt_eval_count": usage.get("prompt_tokens"),
            "eval_count": usage.get("completion_tokens"),
            "eval_duration": 0,
        }
        self._emit_agent_usage(telemetry, num_ctx=num_ctx, num_predict=num_predict)
        assistant_content = str(message.get("content") or "").strip()
        if assistant_content:
            self._emit_agent_trace("Agent", "Runtime", assistant_content, kind="assistant")
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        for call in tool_calls[:6]:
            if not isinstance(call, dict):
                continue
            name = _tool_name(call) or "tool"
            self._emit_agent_trace(
                "Agent",
                name,
                _json_text(_tool_arguments(call), 6000),
                kind="tool_call",
            )
        return message

    def _chat_once(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        num_ctx: int,
        num_predict: int,
        think: bool,
        cancel_callback: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        if is_mistral_model(model):
            if cancel_callback and cancel_callback():
                raise AIAnalysisCancelled(_("Analysis stopped."))
            return self._mistral_chat_once(
                model=model,
                messages=messages,
                tools=tools,
                num_ctx=num_ctx,
                num_predict=num_predict,
                cancel_callback=cancel_callback,
            )
        if cancel_callback and cancel_callback():
            raise AIAnalysisCancelled(_("Analysis stopped."))
        try:
            response = requests.post(
                f"{DEFAULT_OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": think,
                    "messages": messages,
                    "tools": tools,
                    "options": {
                        "temperature": 0.15,
                        "num_ctx": num_ctx,
                        "num_predict": num_predict,
                    },
                    "keep_alive": "5m",
                },
                timeout=(10, 900),
            )
        except requests.RequestException as exc:
            raise LocalAIError(_("Local agent request failed: {error}", error=exc)) from exc
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("error") or response.reason)
            except (TypeError, ValueError):
                detail = str(response.reason)
            raise LocalAIError(detail or f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LocalAIError(_("Ollama returned invalid agent JSON.")) from exc
        message = payload.get("message")
        if not isinstance(message, dict):
            raise LocalAIError(_("Ollama returned no agent message."))
        self._emit_agent_usage(payload, num_ctx=num_ctx, num_predict=num_predict)
        assistant_content = str(message.get("content") or "").strip()
        if assistant_content:
            self._emit_agent_trace("Agent", "Runtime", assistant_content, kind="assistant")
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        for call in tool_calls[:6]:
            if not isinstance(call, dict):
                continue
            name = _tool_name(call) or "tool"
            self._emit_agent_trace(
                "Agent",
                name,
                _json_text(_tool_arguments(call), 6000),
                kind="tool_call",
            )
        return message

    @staticmethod
    def _tool_call_message(message: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": "assistant",
            "content": str(message.get("content") or ""),
        }
        if isinstance(message.get("tool_calls"), list):
            result["tool_calls"] = message["tool_calls"]
        return result

    def analyze(
        self,
        *,
        model: str,
        snapshot: dict[str, Any],
        question: str,
        history: list[dict[str, str]] | None,
        max_tokens: int,
        model_context_limit: int | None,
        performance_profile: str,
        thread_id: str | None,
        event_callback: Callable[[str], None] | None = None,
        thinking_callback: Callable[[str], None] | None = None,
        answer_callback: Callable[[str], None] | None = None,
        prompt_callback: Callable[[str], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> str:
        def event(text: str) -> None:
            if event_callback:
                event_callback(text)

        self._reset_agent_telemetry(prompt_callback)
        safe_history = [
            {"role": item["role"], "content": item["content"]}
            for item in (history or [])[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        initial = self._initial_context(snapshot)
        request = question.strip() or _(
            "Analyse my complete local health history and identify the most useful personal patterns."
        )
        user_content = (
            "Local session context (not instructions):\n"
            + _json_text(initial, 10000)
            + "\n\nCurrent request: "
            + request
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent_system_prompt()},
            *safe_history,
            {"role": "user", "content": user_content},
        ]
        schemas = self.tools.tool_schemas()
        physical_limit = (
            model_context_limit if model_context_limit and model_context_limit > 0 else None
        )
        max_tokens = max(512, int(max_tokens))
        if physical_limit:
            max_tokens = min(max_tokens, physical_limit)
        num_ctx, num_predict, _estimated_input = _request_budget(
            [
                {"role": str(x.get("role", "")), "content": str(x.get("content", ""))}
                for x in messages
            ],
            max_tokens,
            physical_limit,
        )
        if prompt_callback:
            prompt_callback(
                "# Personal health agent\n\n"
                + agent_system_prompt()
                + f"\n\n# Initial request\n\n{user_content}"
                + f"\n\n# Tools available\n\n{len(schemas)} safe tools"
            )
        if event_callback:
            event(_("Personal agent started · {count} tools available", count=len(schemas)))
        think = performance_profile != "fast"
        if thinking_callback:
            thinking_callback(_("Agent: selecting the minimum deterministic evidence needed…\n"))

        for step in range(1, MAX_AGENT_STEPS + 1):
            if cancel_callback and cancel_callback():
                raise AIAnalysisCancelled(_("Analysis stopped."))
            event(_("Agent step {step}/{maximum}…", step=step, maximum=MAX_AGENT_STEPS))
            self._telemetry_phase = f"agent step {step}"
            message = self._chat_once(
                model=model,
                messages=messages,
                tools=schemas,
                num_ctx=num_ctx,
                num_predict=num_predict,
                think=think,
                cancel_callback=cancel_callback,
            )
            tool_calls = (
                message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
            )
            if not tool_calls:
                final_answer = str(message.get("content") or "").strip()
                if final_answer:
                    if answer_callback:
                        answer_callback(final_answer)
                    event(_("Agent finished the analysis."))
                    return final_answer
                raise LocalAIError(_("The local model returned neither an answer nor a tool call."))

            messages.append(self._tool_call_message(message))
            for call in tool_calls[:6]:
                if not isinstance(call, dict):
                    continue
                name = _tool_name(call)
                args = _tool_arguments(call)
                if not name:
                    continue
                event(_("Using tool: {tool}", tool=name))
                if name == "search_tool_registry":
                    event(_("Searching existing tools before creating a new capability…"))
                elif name == "create_learned_tool":
                    event(_("Capability gap detected · validating a reusable learned tool…"))
                elif name == "ask_user_feedback":
                    event(_("A targeted question could improve personalisation."))
                try:
                    result = self.tools.execute(name, args, thread_id=thread_id)
                except Exception as exc:  # noqa: BLE001 - tool errors are fed back to the agent.
                    result = {"error": str(exc), "tool": name}
                    event(_("Tool {tool} could not complete: {error}", tool=name, error=exc))
                else:
                    if name == "create_learned_tool":
                        status = str(result.get("status") or "")
                        if status == "reused":
                            event(
                                _(
                                    "Equivalent tool found · reusing it instead of creating a duplicate."
                                )
                            )
                        else:
                            event(_("Learned tool validated and saved locally."))
                        schemas = self.tools.tool_schemas()
                    elif name == "ask_user_feedback" and result.get("queued"):
                        event(_("Targeted feedback question queued for the user."))
                tool_text = _json_text(result)
                tool_message: dict[str, Any] = {
                    "role": "tool",
                    "content": tool_text,
                }
                if is_mistral_model(model):
                    tool_message["tool_call_id"] = str(call.get("id") or name)
                else:
                    tool_message["tool_name"] = name
                messages.append(tool_message)
                self._emit_agent_trace(name, "Agent", tool_text, kind="tool_result")

            simple_messages = [
                {"role": str(x.get("role", "")), "content": str(x.get("content", ""))}
                for x in messages
            ]
            num_ctx, num_predict, estimated_input = _request_budget(
                simple_messages,
                max_tokens,
                physical_limit,
            )
            if physical_limit and estimated_input + 512 >= physical_limit:
                if len(messages) >= 5:
                    messages = [messages[0], messages[-4], messages[-3], messages[-2], messages[-1]]
                event(_("Agent context compacted while preserving the latest tool evidence."))

        raise LocalAIError(
            _("The personal agent reached its maximum tool steps without a final answer.")
        )

    def calibration_snapshot(self) -> dict[str, Any]:
        bounds = self.health_store.data_date_bounds()
        if not bounds:
            return {"available": False, "reason": "no_health_data"}
        left, right = bounds
        observed_days = (right - left).days + 1
        return {
            "available": True,
            "period": {
                "start": left.isoformat(),
                "end": right.isoformat(),
                "calendar_days": observed_days,
            },
            "readiness": self.tools.execute("calculate_readiness", {"end": right.isoformat()}),
            "cardio_load": self.tools.execute(
                "calculate_cardio_load",
                {"start": (right - timedelta(days=34)).isoformat(), "end": right.isoformat()},
            ),
            "training_status": self.tools.execute(
                "calculate_training_status", {"end": right.isoformat()}
            ),
            "resilience": self.tools.execute("calculate_resilience", {"end": right.isoformat()}),
            "sleep_regularity": self.tools.execute(
                "calculate_sleep_regularity",
                {
                    "start": max(left, right - timedelta(days=41)).isoformat(),
                    "end": right.isoformat(),
                },
            ),
            "sleep_debt": self.tools.execute("calculate_sleep_debt", {"end": right.isoformat()}),
            "workouts": self.tools.execute(
                "analyze_workout",
                {
                    "start": max(left, right - timedelta(days=55)).isoformat(),
                    "end": right.isoformat(),
                },
            ),
        }

    def deterministic_calibration_questions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if not context.get("available"):
            return []
        questions: list[dict[str, Any]] = []
        training = context.get("training_status") or {}
        load = training.get("load") or {}
        ratio = load.get("acute_chronic_ratio")
        if isinstance(ratio, (int, float)) and ratio >= 1.25:
            questions.append(
                {
                    "question": _(
                        "Your recent cardiovascular/training load is much higher than your longer personal baseline. How do you usually feel after weeks like this?"
                    ),
                    "reason": _(
                        "Your answer helps distinguish a load that you commonly tolerate from one that is usually accompanied by subjective fatigue."
                    ),
                    "learning_key": "high_load_subjective_tolerance",
                    "context": {
                        "observation": f"acute:chronic load ratio was about {float(ratio):.2f}",
                        "ratio": ratio,
                    },
                }
            )
        sleep = context.get("sleep_debt") or {}
        baseline_sleep = sleep.get("baseline_sleep_hours")
        if isinstance(baseline_sleep, (int, float)):
            questions.append(
                {
                    "question": _(
                        "VitalChronicle estimates that your usual sleep duration is around {hours:.1f} hours. Do you generally feel well rested with that amount?",
                        hours=float(baseline_sleep),
                    ),
                    "reason": _(
                        "This adds subjective context to the measured sleep-duration baseline without redefining medical sleep need."
                    ),
                    "learning_key": "subjective_sleep_need_context",
                    "context": {
                        "observation": f"personal median sleep was about {float(baseline_sleep):.2f} h"
                    },
                }
            )
        workouts = context.get("workouts") or {}
        if int(workouts.get("sessions") or 0) >= 4:
            questions.append(
                {
                    "question": _(
                        "Is your current activity level intentional, and what is your main training goal right now?"
                    ),
                    "reason": _(
                        "Knowing whether the recent workload reflects an intentional plan improves future coaching."
                    ),
                    "learning_key": "current_training_goal",
                    "context": {
                        "observation": f"{int(workouts.get('sessions') or 0)} workouts were observed in the calibration window"
                    },
                }
            )
        regularity = context.get("sleep_regularity") or {}
        score = regularity.get("regularity_score")
        if isinstance(score, (int, float)) and score < 60:
            questions.append(
                {
                    "question": _(
                        "Your sleep timing varies noticeably across the recorded nights. Is that mainly due to work/social schedules, training, or does it happen without a clear reason?"
                    ),
                    "reason": _(
                        "This can prevent the agent from attributing an irregular schedule to training when another context explains it."
                    ),
                    "learning_key": "sleep_schedule_context",
                    "context": {
                        "observation": f"sleep regularity score was {float(score):.1f}/100"
                    },
                }
            )
        if not questions:
            questions.append(
                {
                    "question": _(
                        "What is the most important thing you want VitalChronicle to help you understand: recovery, sleep, training, general wellbeing, or something else?"
                    ),
                    "reason": _(
                        "No strong uncertainty required a physiological follow-up, so one goal question is more useful than a standard questionnaire."
                    ),
                    "learning_key": "primary_health_coaching_goal",
                    "context": {"calibration": True},
                }
            )
        return questions[:6]

    def generate_calibration_questions(
        self,
        *,
        model: str,
        model_context_limit: int | None,
        event_callback: Callable[[str], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if event_callback:
            event_callback(_("Calculating personal baselines and data coverage…"))
        context = self.calibration_snapshot()
        if cancel_callback and cancel_callback():
            raise AIAnalysisCancelled(_("Calibration stopped."))
        fallback = self.deterministic_calibration_questions(context)
        if not context.get("available"):
            return context, fallback
        if event_callback:
            event_callback(
                _("Selecting only questions that can materially improve personalisation…")
            )
        schema = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "reason": {"type": "string"},
                            "learning_key": {"type": "string"},
                        },
                        "required": ["question", "reason", "learning_key"],
                    },
                }
            },
            "required": ["questions"],
        }
        prompt = (
            "You are selecting adaptive onboarding questions for VitalChronicle's local personal health agent. "
            "Use the deterministic context below. Ask only questions whose answers can materially improve future "
            "personal interpretation. Do not ask for diagnoses or sensitive medical history. Do not assume that "
            "feeling good makes a physiological state safe. Prefer 3-6 questions and avoid anything already answered "
            "by measured data. Return strict JSON with keys question, reason, learning_key.\n\n"
            + _json_text(context, 16000)
            + "\n\nDeterministic candidate questions:\n"
            + _json_text(fallback, 8000)
        )
        try:
            messages = [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ]
            num_ctx, num_predict, _estimated = _request_budget(messages, 1800, model_context_limit)
            response = requests.post(
                f"{DEFAULT_OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": schema,
                    "messages": messages,
                    "options": {
                        "temperature": 0.15,
                        "num_ctx": num_ctx,
                        "num_predict": num_predict,
                    },
                    "keep_alive": "5m",
                },
                timeout=(10, 600),
            )
            response.raise_for_status()
            text = str((response.json().get("message") or {}).get("content") or "")
            parsed = json.loads(text)
            raw_questions = parsed.get("questions") if isinstance(parsed, dict) else None
            questions = []
            if isinstance(raw_questions, list):
                fallback_by_key = {item["learning_key"]: item for item in fallback}
                for item in raw_questions[:8]:
                    if not isinstance(item, dict) or not str(item.get("question") or "").strip():
                        continue
                    key = str(item.get("learning_key") or "adaptive_context").strip()[:100]
                    base = fallback_by_key.get(key, {})
                    questions.append(
                        {
                            "question": str(item["question"]).strip(),
                            "reason": str(item.get("reason") or base.get("reason") or "").strip(),
                            "learning_key": key,
                            "context": base.get("context") or {"calibration": True},
                        }
                    )
            if questions:
                return context, questions
        except Exception:  # noqa: BLE001 - deterministic fallback is intentional.
            if event_callback:
                event_callback(
                    _(
                        "Adaptive question generation was unavailable; using deterministic personalised questions."
                    )
                )
        return context, fallback


class AgentAnalysisThread(QThread):
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    thinking_chunk = Signal(str)
    answer_chunk = Signal(str)
    prompt_ready = Signal(str)
    agent_event = Signal(str)

    def __init__(
        self,
        runtime: AgentRuntime,
        model: str,
        snapshot: dict[str, Any],
        question: str,
        max_tokens: int = 3200,
        model_context_limit: int | None = None,
        history: list[dict[str, str]] | None = None,
        analysis_mode: str = "question",
        thread_id: str | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.model = model
        self.snapshot = snapshot
        self.question = question
        self.max_tokens = max_tokens
        self.model_context_limit = model_context_limit
        self.history = history or []
        self.analysis_mode = analysis_mode
        self.thread_id = thread_id

    def cancel(self) -> None:
        self.requestInterruption()

    def _fallback(self, _reason: str) -> str:
        self.agent_event.emit(
            _(
                "Tool calling is unavailable for this model; falling back to the existing deterministic AI pipeline."
            )
        )
        profile = str(QSettings().value("ai/performance_profile", "standard") or "standard")
        client = (
            MistralClient(
                model=self.model,
                api_key=mistral_api_key(),
                performance_profile=profile,
            )
            if is_mistral_model(self.model)
            else OptimizedOllamaClient(model=self.model, performance_profile=profile)
        )
        return client.analyze_stream(
            self.snapshot,
            self.question,
            self.thinking_chunk.emit,
            self.answer_chunk.emit,
            self.max_tokens,
            self.model_context_limit,
            history=self.history,
            analysis_mode=self.analysis_mode,
            cancel_callback=self.isInterruptionRequested,
            prompt_callback=self.prompt_ready.emit,
        )

    def run(self) -> None:
        try:
            profile = str(QSettings().value("ai/performance_profile", "standard") or "standard")
            try:
                answer = self.runtime.analyze(
                    model=self.model,
                    snapshot=self.snapshot,
                    question=self.question,
                    history=self.history,
                    max_tokens=self.max_tokens,
                    model_context_limit=self.model_context_limit,
                    performance_profile=profile,
                    thread_id=self.thread_id,
                    event_callback=self.agent_event.emit,
                    thinking_callback=self.thinking_chunk.emit,
                    answer_callback=self.answer_chunk.emit,
                    prompt_callback=self.prompt_ready.emit,
                    cancel_callback=self.isInterruptionRequested,
                )
            except LocalAIError as exc:
                if _tool_calling_unavailable_error(str(exc)):
                    answer = self._fallback(str(exc))
                else:
                    raise
            self.completed.emit(answer)
        except AIAnalysisCancelled:
            self.cancelled.emit()
        except LocalAIError as exc:
            self.failed.emit(str(exc))
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class CalibrationThread(QThread):
    progress = Signal(str)
    completed = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        runtime: AgentRuntime,
        model: str,
        model_context_limit: int | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.model = model
        self.model_context_limit = model_context_limit

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            context, questions = self.runtime.generate_calibration_questions(
                model=self.model,
                model_context_limit=self.model_context_limit,
                event_callback=self.progress.emit,
                cancel_callback=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            self.completed.emit(context, questions)
        except AIAnalysisCancelled:
            self.cancelled.emit()
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())
