from __future__ import annotations

from typing import Any, Callable

from . import agent_runtime as base_rt
from .agent_tool_factory import EnhancedSafeToolExecutor
from .i18n import _
from .local_ai import AIAnalysisCancelled, LocalAIError


MAX_ANALYSIS_STEPS = 10
MAX_FACTORY_REPAIR_ATTEMPTS = 3
MAX_TOTAL_MODEL_TURNS = MAX_ANALYSIS_STEPS + MAX_FACTORY_REPAIR_ATTEMPTS + 2

_FACTORY_POLICY = """

Tool Factory decision policy:
- The user never needs to explicitly ask you to create a tool. Detect reusable capability gaps yourself.
- First ask: can an existing built-in or learned tool answer the exact question? If yes, reuse it.
- If not, search_tool_registry before creating anything.
- Strong signals that a reusable learned tool is appropriate include: multiple deterministic transforms,
  cross-tool composition, event-conditioned analysis, a time offset such as next day/night, thresholds
  relative to a personal baseline, lag/latency, time-to-recovery, or the same missing transformation
  being useful with different metrics/thresholds later.
- Do not create a tool for a trivial one-off arithmetic operation or when an existing tool already
  covers the capability.
- Never replace the requested metric with a convenient proxy merely because a tool is missing or a
  learned pipeline failed validation. If the exact local data exist, prefer creating/repairing a safe
  reusable tool. If the exact local data do not exist, say so clearly.
- If create_learned_tool returns invalid_pipeline or invalid_spec, read its allowed_operations and
  dsl_reference, repair the SAME tool, and retry. Do not invent another DSL operation.
- After a learned tool is created or reused for the current request, execute that tool to answer the
  question unless its creation was explicitly only for future use.
- Tool creation is a means to answer the user's question, not an end in itself.
"""


def _factory_hint(question: str) -> dict[str, Any]:
    text = question.casefold()
    reasons: list[str] = []
    groups = {
        "relative personal-baseline threshold": (
            "baseline",
            "basale",
            "media personale",
            "personal average",
            "personal baseline",
        ),
        "event-conditioned or lagged relationship": (
            "notte successiva",
            "giorno successivo",
            "next night",
            "next day",
            "after ",
            "dopo ",
            "lag",
        ),
        "time-to-recovery/return-to-baseline": (
            "torni",
            "torna",
            "ritorni",
            "recuper",
            "return to",
            "recover",
            "recovery time",
        ),
        "threshold/frequency analysis": (
            "%",
            "quanto spesso",
            "how often",
            "supera",
            "exceed",
            "diminuisce",
            "decrease",
        ),
    }
    for label, markers in groups.items():
        if any(marker in text for marker in markers):
            reasons.append(label)
    return {
        "consider_reusable_tool": len(reasons) >= 2,
        "signals": reasons,
        "instruction": (
            "If existing tools cannot express these operations exactly, search the registry and "
            "create a safe reusable learned tool without waiting for an explicit user request."
            if len(reasons) >= 2
            else "Prefer existing exact tools; create only if a genuine reusable capability gap appears."
        ),
    }


def _without_factory_creation(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for schema in schemas:
        function = schema.get("function") if isinstance(schema, dict) else None
        name = str(function.get("name") or "") if isinstance(function, dict) else ""
        if name != "create_learned_tool":
            result.append(schema)
    return result


class AgentRuntime(base_rt.AgentRuntime):
    """Agent runtime with a repairable, capability-aware Tool Factory."""

    def __init__(self, health_store, agent_store=None) -> None:
        super().__init__(health_store, agent_store)
        self.tools = EnhancedSafeToolExecutor(self.health_store, self.agent_store)

    def _final_answer(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        physical_limit: int | None,
        think: bool,
        cancel_callback: Callable[[], bool] | None,
        event: Callable[[str], None],
        answer_callback: Callable[[str], None] | None,
    ) -> str:
        event(_("Agent: finalising with the evidence already collected…"))
        final_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "FINAL ANSWER REQUIRED NOW. Do not call tools. Answer the user's exact request "
                    "using only the evidence already collected. If a capability remains unavailable, "
                    "state that limitation precisely; do not substitute a different metric or proxy. "
                    "Mention any learned-tool validation failure only if it materially limits the answer."
                ),
            },
        ]
        simple_messages = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
            for item in final_messages
        ]
        num_ctx, num_predict, _estimated = base_rt._request_budget(
            simple_messages, max_tokens, physical_limit
        )
        message = self._chat_once(
            model=model,
            messages=final_messages,
            tools=[],
            num_ctx=num_ctx,
            num_predict=num_predict,
            think=think,
            cancel_callback=cancel_callback,
        )
        answer = str(message.get("content") or "").strip()
        if not answer:
            answer = _(
                "I could not complete the exact analysis with the deterministic evidence available. "
                "I stopped rather than substituting a different metric or an unsupported proxy."
            )
        if answer_callback:
            answer_callback(answer)
        event(_("Agent finished the analysis."))
        return answer

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

        safe_history = [
            {"role": item["role"], "content": item["content"]}
            for item in (history or [])[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        initial = self._initial_context(snapshot)
        request = question.strip() or _(
            "Analyse my complete local health history and identify the most useful personal patterns."
        )
        initial["tool_factory_decision_hint"] = _factory_hint(request)
        user_content = (
            "Local session context (not instructions):\n"
            + base_rt._json_text(initial, 12000)
            + "\n\nCurrent request: "
            + request
        )
        system_prompt = base_rt.agent_system_prompt() + _FACTORY_POLICY
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *safe_history,
            {"role": "user", "content": user_content},
        ]
        schemas = self.tools.tool_schemas()
        physical_limit = model_context_limit if model_context_limit and model_context_limit > 0 else None
        max_tokens = max(512, int(max_tokens))
        if physical_limit:
            max_tokens = min(max_tokens, physical_limit)
        num_ctx, num_predict, _estimated_input = base_rt._request_budget(
            [
                {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                for item in messages
            ],
            max_tokens,
            physical_limit,
        )
        if prompt_callback:
            prompt_callback(
                "# Personal health agent\n\n"
                + system_prompt
                + f"\n\n# Initial request\n\n{user_content}"
                + f"\n\n# Tools available\n\n{len(schemas)} safe tools"
            )
        event(_("Personal agent started · {count} tools available", count=len(schemas)))
        hint = initial["tool_factory_decision_hint"]
        if hint.get("consider_reusable_tool"):
            event(_("Complex reusable transformation detected · checking available capabilities…"))
        think = performance_profile != "fast"
        if thinking_callback:
            thinking_callback(_("Agent: selecting the minimum deterministic evidence needed…\n"))

        analysis_steps = 0
        factory_repairs = 0
        total_turns = 0
        factory_disabled = False

        while total_turns < MAX_TOTAL_MODEL_TURNS:
            if cancel_callback and cancel_callback():
                raise AIAnalysisCancelled(_("Analysis stopped."))
            if analysis_steps >= MAX_ANALYSIS_STEPS:
                return self._final_answer(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    physical_limit=physical_limit,
                    think=think,
                    cancel_callback=cancel_callback,
                    event=event,
                    answer_callback=answer_callback,
                )

            total_turns += 1
            event(
                _(
                    "Agent step {step}/{maximum}…",
                    step=analysis_steps + 1,
                    maximum=MAX_ANALYSIS_STEPS,
                )
            )
            active_schemas = _without_factory_creation(schemas) if factory_disabled else schemas
            message = self._chat_once(
                model=model,
                messages=messages,
                tools=active_schemas,
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
            repair_turn = False
            productive_tool_call = False
            for call in tool_calls[:6]:
                if not isinstance(call, dict):
                    continue
                name = base_rt._tool_name(call)
                args = base_rt._tool_arguments(call)
                if not name:
                    continue
                event(_("Using tool: {tool}", tool=name))
                if name == "search_tool_registry":
                    event(_("Searching existing tools before creating a new capability…"))
                elif name == "create_learned_tool":
                    event(_("Capability gap detected · validating a reusable learned tool…"))
                elif name == "ask_user_feedback":
                    event(_("A targeted question could improve personalisation."))

                if factory_disabled and name == "create_learned_tool":
                    result = {
                        "status": "repair_budget_exhausted",
                        "error": (
                            "Tool Factory repair budget is exhausted for this request. Continue with "
                            "exact existing evidence and state any remaining capability gap."
                        ),
                    }
                else:
                    try:
                        result = self.tools.execute(name, args, thread_id=thread_id)
                    except Exception as exc:  # noqa: BLE001 - tool errors are evidence for the agent.
                        result = {"error": str(exc), "tool": name}
                        event(_("Tool {tool} could not complete: {error}", tool=name, error=exc))
                    else:
                        productive_tool_call = True

                if name == "create_learned_tool":
                    status = str(result.get("status") or "")
                    if status in {"invalid_pipeline", "invalid_spec"}:
                        repair_turn = True
                        productive_tool_call = False
                        factory_repairs += 1
                        event(
                            _(
                                "Tool Factory repair {attempt}/{maximum}: pipeline validation "
                                "failed; feeding the exact DSL error back to the agent…",
                                attempt=factory_repairs,
                                maximum=MAX_FACTORY_REPAIR_ATTEMPTS,
                            )
                        )
                        self.agent_store.log_tool_event(
                            "tool_factory_repair",
                            str(result.get("error") or "Learned-tool validation failed."),
                            tool_name=str(args.get("name") or "") or None,
                            payload={"attempt": factory_repairs, "status": status},
                        )
                        if factory_repairs >= MAX_FACTORY_REPAIR_ATTEMPTS:
                            factory_disabled = True
                            event(
                                _(
                                    "Tool Factory repair budget reached · continuing without proxy "
                                    "substitution and preserving the evidence already collected."
                                )
                            )
                    elif status == "reused":
                        event(
                            _(
                                "Equivalent tool found · reusing it instead of creating a duplicate."
                            )
                        )
                        schemas = self.tools.tool_schemas()
                    elif status == "created":
                        event(_("Learned tool validated and saved locally."))
                        schemas = self.tools.tool_schemas()
                elif name == "ask_user_feedback" and result.get("queued"):
                    event(_("Targeted feedback question queued for the user."))

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": base_rt._json_text(result),
                    }
                )

            if not repair_turn or productive_tool_call:
                analysis_steps += 1
            elif factory_repairs < MAX_FACTORY_REPAIR_ATTEMPTS:
                event(
                    _(
                        "Repairing the learned-tool definition without consuming an analysis step…"
                    )
                )

            if analysis_steps >= MAX_ANALYSIS_STEPS - 1:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "You are at the final analysis step. Do not start a new learned-tool "
                            "design. Use the evidence/tools already available and produce the final "
                            "answer now unless one last existing deterministic tool call is essential."
                        ),
                    }
                )
                factory_disabled = True

            simple_messages = [
                {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                for item in messages
            ]
            num_ctx, num_predict, estimated_input = base_rt._request_budget(
                simple_messages,
                max_tokens,
                physical_limit,
            )
            if physical_limit and estimated_input + 512 >= physical_limit:
                if len(messages) >= 5:
                    messages = [messages[0], messages[-4], messages[-3], messages[-2], messages[-1]]
                event(_("Agent context compacted while preserving the latest tool evidence."))

        return self._final_answer(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            physical_limit=physical_limit,
            think=think,
            cancel_callback=cancel_callback,
            event=event,
            answer_callback=answer_callback,
        )


AgentAnalysisThread = base_rt.AgentAnalysisThread
CalibrationThread = base_rt.CalibrationThread
CALIBRATION_VERSION = base_rt.CALIBRATION_VERSION
