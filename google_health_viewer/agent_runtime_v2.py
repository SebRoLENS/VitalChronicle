from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import agent_runtime as base_rt
from .agent_tool_factory import EnhancedSafeToolExecutor
from .i18n import _
from .local_ai import AIAnalysisCancelled, LocalAIError

MAX_ANALYSIS_STEPS = 10
MAX_FACTORY_REPAIR_ATTEMPTS = 3
MAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2
MAX_TOTAL_MODEL_TURNS = MAX_ANALYSIS_STEPS + MAX_FACTORY_REPAIR_ATTEMPTS + 4

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
- When a runtime Tool Factory gate is active, stop raw-series probing and make the capability decision now.
- Prefer semantic deterministic tools (for example calculate_cardio_load) over guessing raw metric names.
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


def _only_named_tools(
    schemas: list[dict[str, Any]], names: set[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for schema in schemas:
        function = schema.get("function") if isinstance(schema, dict) else None
        name = str(function.get("name") or "") if isinstance(function, dict) else ""
        if name in names:
            result.append(schema)
    return result


def _factory_capability(hint: dict[str, Any]) -> str:
    signals = {str(item) for item in hint.get("signals", [])}
    parts = ["analysis", "composed"]
    if "relative personal-baseline threshold" in signals:
        parts.append("personal_baseline")
    if "event-conditioned or lagged relationship" in signals:
        parts.append("temporal_event_response")
    if "time-to-recovery/return-to-baseline" in signals:
        parts.append("recovery_latency")
    if "threshold/frequency analysis" in signals:
        parts.append("threshold_frequency")
    return ".".join(parts)


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
        physical_limit = (
            model_context_limit if model_context_limit and model_context_limit > 0 else None
        )
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
        factory_candidate = bool(hint.get("consider_reusable_tool"))
        factory_capability = _factory_capability(hint)
        if factory_candidate:
            event(_("Complex reusable transformation detected · checking available capabilities…"))
            registry_preflight = self.tools.execute(
                "search_tool_registry",
                {"capability": factory_capability, "description": request},
                thread_id=thread_id,
            )
            event(_("Tool Factory preflight: registry checked before raw-data exploration."))
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "RUNTIME TOOL FACTORY PREFLIGHT: this request contains a reusable complex "
                        f"capability pattern ({factory_capability}). Registry result: "
                        + base_rt._json_text(registry_preflight, 5000)
                        + ". You may inspect at most two raw metric series before making the "
                        "capability decision. If no exact existing capability answers the request, "
                        "create a reusable safe learned tool. Prefer semantic built-ins such as "
                        "calculate_cardio_load or get_sleep_stage_series over guessing raw metric names."
                    ),
                }
            )
        think = performance_profile != "fast"
        if thinking_callback:
            thinking_callback(_("Agent: selecting the minimum deterministic evidence needed…\n"))

        analysis_steps = 0
        factory_repairs = 0
        total_turns = 0
        factory_disabled = False
        factory_gate_required = False
        factory_resolution_seen = False
        raw_series_probes = 0
        tool_result_cache: dict[str, dict[str, Any]] = {}

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
            if factory_gate_required and not factory_disabled:
                active_schemas = _only_named_tools(active_schemas, {"create_learned_tool"})
                event(_("Tool Factory gate active · the next decision must resolve the reusable capability gap."))
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

                blocked_for_factory = False
                if name == "get_metric_series" and factory_candidate and not factory_resolution_seen:
                    raw_series_probes += 1
                    if raw_series_probes > MAX_RAW_SERIES_PROBES_BEFORE_FACTORY:
                        blocked_for_factory = True
                        factory_gate_required = True
                        result = {
                            "status": "factory_decision_required",
                            "error": (
                                "Raw-series exploration budget reached for a reusable complex request. "
                                "The registry preflight is already complete. Create a safe reusable learned "
                                "tool now instead of probing more raw metric names."
                            ),
                            "capability": factory_capability,
                        }
                        event(_("Repeated raw-series probing stopped · switching to the Tool Factory decision."))

                cache_key = base_rt._json_text({"tool": name, "arguments": args}, 8000)
                if not blocked_for_factory and cache_key in tool_result_cache:
                    result = tool_result_cache[cache_key]
                    event(_("Repeated identical tool call reused from this analysis instead of consuming another query."))
                elif not blocked_for_factory and factory_disabled and name == "create_learned_tool":
                    result = {
                        "status": "repair_budget_exhausted",
                        "error": (
                            "Tool Factory repair budget is exhausted for this request. Continue with "
                            "exact existing evidence and state any remaining capability gap."
                        ),
                    }
                elif not blocked_for_factory:
                    try:
                        result = self.tools.execute(name, args, thread_id=thread_id)
                    except Exception as exc:  # noqa: BLE001 - tool errors are evidence for the agent.
                        result = {"error": str(exc), "tool": name}
                        event(_("Tool {tool} could not complete: {error}", tool=name, error=exc))
                    else:
                        productive_tool_call = True
                        tool_result_cache[cache_key] = result

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
                        factory_resolution_seen = True
                        factory_gate_required = False
                        event(
                            _("Equivalent tool found · reusing it instead of creating a duplicate.")
                        )
                        schemas = self.tools.tool_schemas()
                    elif status == "created":
                        factory_resolution_seen = True
                        factory_gate_required = False
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
                    _("Repairing the learned-tool definition without consuming an analysis step…")
                )

            if (
                factory_candidate
                and not factory_resolution_seen
                and not factory_disabled
                and analysis_steps >= 4
            ):
                factory_gate_required = True

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
