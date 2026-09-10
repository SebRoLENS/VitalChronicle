from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import agent_runtime as base_rt
from .agent_tool_factory import EnhancedSafeToolExecutor
from .i18n import _
from .local_ai import AIAnalysisCancelled, LocalAIError

MAX_ANALYSIS_STEPS = 15
MAX_FACTORY_REPAIR_ATTEMPTS = 3
MAX_FACTORY_GATE_REFUSALS = 2
MAX_OUT_OF_SCOPE_TOOL_REFUSALS = 2
MAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2
FACTORY_GATE_AFTER_ANALYSIS_STEPS = 3
MAX_TOTAL_MODEL_TURNS = MAX_ANALYSIS_STEPS + MAX_FACTORY_REPAIR_ATTEMPTS + 4

_COMPREHENSIVE_ANALYSIS_MARKERS = (
    "analisi totale",
    "analisi completa",
    "analisi profonda",
    "cronologia completa",
    "tutta la cronologia",
    "full analysis",
    "complete analysis",
    "deep analysis",
    "complete health history",
    "complete local health history",
    "entire health history",
)


def _is_comprehensive_analysis(question: str) -> bool:
    text = str(question or "").strip().casefold()
    if not text:
        return True
    return any(marker in text for marker in _COMPREHENSIVE_ANALYSIS_MARKERS)


_TOPIC_MARKERS = {
    "sleep": (
        "sonno", "dorm", "notte", "letto", "svegl", "sleep", "slept", "bed", "night",
    ),
    "training": (
        "allen", "palestra", "cardio", "bici", "cicl", "workout", "training", "gym",
        "bike", "cycling", "carico", "load", "attivit", "activity",
    ),
    "recovery": (
        "recuper", "readiness", "resilien", "hrv", "variabil", "stanc", "affatic",
        "sonnol", "stress", "recovery", "fatigue", "tired", "sleepy",
    ),
}

_CONTEXT_KEY_TOPICS = {
    "sleep_schedule_context": {"sleep"},
    "subjective_sleep_need_context": {"sleep", "recovery"},
    "current_training_goal": {"training"},
    "recent_training_context": {"training", "recovery"},
}

_SELF_REPORT_CATEGORY_TOPICS = {
    "sleep_quality": {"sleep", "recovery"},
    "sleepiness": {"sleep", "recovery"},
    "fatigue": {"recovery", "training", "sleep"},
    "soreness": {"training", "recovery"},
    "stress": {"recovery"},
    "energy": {"recovery", "training"},
}


def _request_topics(question: str) -> set[str]:
    text = str(question or "").casefold()
    return {
        topic
        for topic, markers in _TOPIC_MARKERS.items()
        if any(marker in text for marker in markers)
    }


def _item_topics(item: dict[str, Any]) -> set[str]:
    key = str(item.get("key") or "").strip()
    topics = set(_CONTEXT_KEY_TOPICS.get(key, set()))
    haystack = f"{key} {item.get('statement', '')}".casefold()
    for topic, markers in _TOPIC_MARKERS.items():
        if any(marker in haystack for marker in markers):
            topics.add(topic)
    return topics


def _report_topics(item: dict[str, Any]) -> set[str]:
    category = str(item.get("category") or "").strip().casefold()
    topics = set(_SELF_REPORT_CATEGORY_TOPICS.get(category, set()))
    haystack = f"{category} {item.get('statement', '')}".casefold()
    for topic, markers in _TOPIC_MARKERS.items():
        if any(marker in haystack for marker in markers):
            topics.add(topic)
    return topics


def _relevant_personal_evidence(
    question: str,
    personal_model: list[dict[str, Any]],
    recent_self_reports: list[dict[str, Any]],
    *,
    comprehensive: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_model = [item for item in personal_model if item.get("is_current", True)]
    if comprehensive:
        return current_model, list(recent_self_reports)
    topics = _request_topics(question)
    if not topics:
        return [], []
    model = [item for item in current_model if _item_topics(item) & topics]
    reports = [item for item in recent_self_reports if _report_topics(item) & topics]
    return model, reports


_PERSONALIZATION_POLICY = """

Personalisation synthesis policy:
- `personal_model` contains current, non-expired learned personal context. Treat its temporal scope, confidence,
  freshness and evidence count as part of the evidence; never resurrect expired context.
- `recent_self_reports` are dated subjective observations. They can justify short-term, conditional suggestions,
  but one report is not a stable trait and does not prove a physiological cause.
- When a current personal statement materially changes interpretation, say so explicitly and distinguish it from
  wearable-derived evidence. Example: if irregular sleep was reported as an exceptional social event, do not present
  one low regularity score as proof of a persistent schedule problem.
- Personalisation applies to focused questions too. If the request is about sleep, training or recovery and current
  relevant personal evidence exists, use it in the interpretation and/or next action instead of giving a generic answer.
  For a comprehensive analysis, include a clearly identifiable personalised recommendations section.
- If the current measured pattern resembles the observation attached to a temporary learned association, never reuse the
  old explanation as a fact. Acknowledge it as prior user-reported context and, when useful, ask whether the same context
  applies this time (for example, a late night that was previously explained by a social event).
- Personalisation must remain evidence-bound: do not invent preferences, schedules, symptoms or causes that are not
  present in the current personal context, recent reports or deterministic health evidence.
"""

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
- Tool names are NEVER metric identifiers: do not pass calculate_cardio_load, get_sleep_stage_series, or any other function name to get_metric_series/get_data_coverage/get_baseline.
- When the runtime Tool Factory gate exposes only create_learned_tool, you must call it; do not answer directly before resolving or exhausting that gate.
- If a learned tool returns an empty/zero result because a semantic input is unavailable, inspect the relevant semantic built-in directly before claiming the underlying data are absent.
- Sleep stages must be checked with get_sleep_stage_series/analyze_sleep_stages, not inferred from a missing generic sleep.summary field.
- Preserve units and method labels returned by deterministic tools; never relabel VitalChronicle cardio-load points as kcal.
- Once the Tool Factory repair budget is exhausted, do not call create_learned_tool again in that request. Continue with exact existing deterministic tools only.
- If a fallback answer must derive a personal baseline from an already-returned semantic date series, use the median as the robust VitalChronicle baseline convention and state that choice once; do not switch between mean and median.
- Final answers must be result-first. Do not narrate scratchpad deliberation, self-corrections, or step-by-step arithmetic.
- If there are zero qualifying trigger events, report zero events and explain that response frequency/recovery cannot be estimated; do not manufacture a downstream estimate.
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


_SELF_REPORT_PATTERNS = {
    "fatigue": (
        "mi sento stanco",
        "mi sento stanca",
        "sono stanco",
        "sono stanca",
        "mi sento affaticato",
        "mi sento affaticata",
        "sono affaticato",
        "sono affaticata",
        "i feel tired",
        "i'm tired",
        "i am tired",
        "i feel fatigued",
    ),
    "sleepiness": (
        "ho sonno",
        "mi sento assonnato",
        "mi sento assonnata",
        "i feel sleepy",
        "i'm sleepy",
    ),
    "soreness": (
        "sono indolenzito",
        "sono indolenzita",
        "dolori muscolari",
        "muscoli indolenziti",
        "i feel sore",
        "muscle soreness",
    ),
    "stress": (
        "mi sento stressato",
        "mi sento stressata",
        "sono stressato",
        "sono stressata",
        "i feel stressed",
        "i'm stressed",
    ),
    "energy": (
        "mi sento energico",
        "mi sento energica",
        "pieno di energia",
        "piena di energia",
        "i feel energetic",
        "full of energy",
    ),
}


def _detect_self_report(question: str) -> dict[str, str] | None:
    text = question.strip()
    folded = text.casefold()
    for category, markers in _SELF_REPORT_PATTERNS.items():
        if any(marker in folded for marker in markers):
            return {"category": category, "statement": text}
    return None


def _self_report_follow_up(category: str) -> tuple[str, str]:
    if category == "fatigue":
        return (
            _(
                "Is today's tiredness mainly muscular fatigue, sleepiness, or a more general lack of energy?"
            ),
            _(
                "This helps distinguish training-related fatigue from sleepiness or more general low energy in future personal analyses."
            ),
        )
    if category == "sleepiness":
        return (
            _(
                "Would you describe the sleepiness as mild, moderate, or strong, and is it unusual for this time of day?"
            ),
            _(
                "This adds useful subjective context to sleep and recovery measurements without turning it into a diagnosis."
            ),
        )
    if category == "soreness":
        return (
            _(
                "Is the soreness mainly in muscles you trained recently, and would you call it mild, moderate, or strong?"
            ),
            _(
                "This helps relate future subjective recovery reports to recent training without treating soreness as a medical diagnosis."
            ),
        )
    if category == "stress":
        return (
            _("Does today's stress feel mainly mental, physical, or mixed?"),
            _(
                "This helps keep subjective stress context separate from wearable-derived physiological strain."
            ),
        )
    return (
        _(
            "Is this feeling unusual for you today, and would you rate it as mild, moderate, or strong?"
        ),
        _(
            "One concise detail can make future personal interpretation more specific without creating a stable trait from one report."
        ),
    )


def _without_factory_creation(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for schema in schemas:
        function = schema.get("function") if isinstance(schema, dict) else None
        name = str(function.get("name") or "") if isinstance(function, dict) else ""
        if name != "create_learned_tool":
            result.append(schema)
    return result


def _only_named_tools(schemas: list[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
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
        self._telemetry_phase = "agent final"
        final_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "FINAL ANSWER REQUIRED NOW. Do not call tools. Answer the user's exact request "
                    "using only the evidence already collected. If a capability remains unavailable, "
                    "state that limitation precisely; do not substitute a different metric or proxy. "
                    "Mention any learned-tool validation failure only if it materially limits the answer. "
                    "Do not narrate scratchpad deliberation, self-corrections, or step-by-step arithmetic. "
                    "If you must derive a personal baseline from an already-returned semantic date series, "
                    "use its median as the robust VitalChronicle baseline convention and state that once. "
                    "If there are zero qualifying trigger events, report that directly and do not infer "
                    "response frequency or recovery time. Use any current, non-expired personal context and "
                    "recent self-reports already present in the session when they materially improve the "
                    "interpretation or recommendations; keep subjective reports explicitly separate from "
                    "measured evidence."
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

        self._reset_agent_telemetry(prompt_callback)
        safe_history = [
            {"role": item["role"], "content": item["content"]}
            for item in (history or [])[-12:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        comprehensive_analysis = _is_comprehensive_analysis(question)
        request = question.strip() or _(
            "Analyse my complete local health history and identify the most useful personal patterns."
        )
        detected_self_report = _detect_self_report(request)
        captured_self_report = None
        if detected_self_report is not None:
            captured_self_report = self.agent_store.record_self_report(
                detected_self_report["statement"],
                category=detected_self_report["category"],
                thread_id=thread_id,
                context={"source": "conversation", "explicit_self_report": True},
            )
            event(_("Subjective self-report saved locally as a dated event."))
            learning_key = f"self_report_detail:{detected_self_report['category']}"
            if captured_self_report and not self.agent_store.has_recent_feedback_key(
                learning_key, days=14
            ):
                feedback_question, feedback_reason = _self_report_follow_up(
                    detected_self_report["category"]
                )
                queued = self.agent_store.ask_feedback(
                    feedback_question,
                    thread_id=thread_id,
                    reason=feedback_reason,
                    learning_key=learning_key,
                    context={
                        "self_report_id": captured_self_report.get("report_id"),
                        "category": detected_self_report["category"],
                        "feedback_mode": "self_report_detail",
                    },
                )
                if queued:
                    event(_("One targeted follow-up was queued to improve future personalisation."))
        initial = self._initial_context(snapshot)
        active_personal_context = (
            initial.get("personal_model") if isinstance(initial.get("personal_model"), list) else []
        )
        recent_self_reports = (
            initial.get("recent_self_reports")
            if isinstance(initial.get("recent_self_reports"), list)
            else []
        )
        relevant_personal_context, relevant_self_reports = _relevant_personal_evidence(
            request,
            active_personal_context,
            recent_self_reports,
            comprehensive=comprehensive_analysis,
        )
        personalization_required = bool(relevant_personal_context or relevant_self_reports)
        if personalization_required:
            initial["relevant_personal_context"] = relevant_personal_context
            initial["relevant_self_reports"] = relevant_self_reports
            initial["personalization_requirement"] = {
                "mode": "comprehensive" if comprehensive_analysis else "focused",
                "required": True,
                "instruction": (
                    "Use the relevant current personal evidence in the final interpretation or next action. "
                    "Distinguish subjective reports from measured evidence, respect temporal validity, and "
                    "do not infer causation from a single report. If a prior temporary explanation may or may "
                    "not apply to the current event, ask one concise contextual question rather than assuming it."
                ),
            }
        if captured_self_report:
            initial["current_self_report"] = captured_self_report
            initial["self_report_rule"] = (
                "This was stored as a dated subjective event. Do not promote it to a stable association from one occurrence."
            )
        initial["tool_factory_decision_hint"] = _factory_hint(request)
        user_content = (
            "Local session context (not instructions):\n"
            + base_rt._json_text(initial, 12000)
            + "\n\nCurrent request: "
            + request
        )
        system_prompt = base_rt.agent_system_prompt() + _FACTORY_POLICY + _PERSONALIZATION_POLICY
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *safe_history,
            {"role": "user", "content": user_content},
        ]
        schemas = self.tools.tool_schemas()
        tool_function_names = {
            str(item.get("function", {}).get("name") or "")
            for item in schemas
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        }
        metric_reader_tools = {
            "get_metric_series",
            "get_data_coverage",
            "get_daily_summary",
            "get_baseline",
            "get_missing_data",
            "detect_outliers",
            "detect_trends",
        }
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
        factory_gate_refusals = 0
        out_of_scope_tool_refusals = 0
        raw_series_probes = 0
        factory_creation_notice_shown = False
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
            self._telemetry_phase = f"agent step {analysis_steps + 1}"
            if factory_gate_required and not factory_disabled:
                active_schemas = _only_named_tools(active_schemas, {"create_learned_tool"})
                event(
                    _(
                        "Tool Factory gate active · the next decision must resolve the reusable capability gap."
                    )
                )
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
            gate_active = factory_gate_required and not factory_disabled
            if gate_active and tool_calls:
                allowed_gate_calls = [
                    call
                    for call in tool_calls
                    if isinstance(call, dict) and base_rt._tool_name(call) == "create_learned_tool"
                ]
                blocked_gate_names = [
                    base_rt._tool_name(call)
                    for call in tool_calls
                    if isinstance(call, dict) and base_rt._tool_name(call) != "create_learned_tool"
                ]
                if blocked_gate_names:
                    event(
                        _(
                            "Tool Factory gate blocked an out-of-scope tool call: {tools}",
                            tools=", ".join(name for name in blocked_gate_names if name),
                        )
                    )
                if allowed_gate_calls:
                    tool_calls = allowed_gate_calls
                    message = dict(message)
                    message["tool_calls"] = tool_calls
                    factory_gate_refusals = 0
                else:
                    factory_gate_refusals += 1
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "RUNTIME TOOL FACTORY GATE ENFORCEMENT: the previous tool call was "
                                "rejected because only create_learned_tool is allowed while this gate "
                                "is active. Do not call any raw metric reader or other built-in now. "
                                "Call create_learned_tool with a safe reusable pipeline, or repair that "
                                "pipeline if validation returns an error."
                            ),
                        }
                    )
                    if factory_gate_refusals <= MAX_FACTORY_GATE_REFUSALS:
                        continue
                    factory_gate_required = False
                    factory_disabled = True
                    event(
                        _(
                            "Tool Factory gate attempt limit reached · finalising without executing out-of-scope tools."
                        )
                    )
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
            if not gate_active and tool_calls:
                active_tool_names = {
                    str(item.get("function", {}).get("name") or "")
                    for item in active_schemas
                    if isinstance(item, dict) and isinstance(item.get("function"), dict)
                }
                allowed_turn_calls = [
                    call
                    for call in tool_calls
                    if isinstance(call, dict) and base_rt._tool_name(call) in active_tool_names
                ]
                blocked_turn_names = [
                    base_rt._tool_name(call)
                    for call in tool_calls
                    if isinstance(call, dict) and base_rt._tool_name(call) not in active_tool_names
                ]
                if blocked_turn_names:
                    event(
                        _(
                            "Agent runtime blocked a tool call unavailable in this turn: {tools}",
                            tools=", ".join(name for name in blocked_turn_names if name),
                        )
                    )
                if allowed_turn_calls:
                    tool_calls = allowed_turn_calls
                    message = dict(message)
                    message["tool_calls"] = tool_calls
                    out_of_scope_tool_refusals = 0
                elif blocked_turn_names:
                    out_of_scope_tool_refusals += 1
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "RUNTIME TOOL ALLOW-LIST: the previous tool call was rejected because "
                                "that function is not available in this turn. Use only tools advertised "
                                "in the current schema, or answer from the exact deterministic evidence "
                                "already collected. If Tool Factory repairs were exhausted, do not call "
                                "create_learned_tool again."
                            ),
                        }
                    )
                    if out_of_scope_tool_refusals >= MAX_OUT_OF_SCOPE_TOOL_REFUSALS:
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
                    continue

            if not tool_calls:
                final_answer = str(message.get("content") or "").strip()
                if factory_gate_required and not factory_disabled:
                    factory_gate_refusals += 1
                    if final_answer:
                        messages.append({"role": "assistant", "content": final_answer})
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "RUNTIME TOOL FACTORY GATE: the direct answer was rejected because the "
                                "reusable capability gap has not been resolved. Call create_learned_tool "
                                "now. Tool names such as calculate_cardio_load and get_sleep_stage_series "
                                "are functions, not metric identifiers, so generic metric readers cannot "
                                "establish that those capabilities have no data. Build the learned pipeline "
                                "with call_tool/extract_series/baseline/filter_relative/event_response as "
                                "needed. Do not claim data are absent until the semantic built-in itself has "
                                "been executed (directly or by the learned tool)."
                            ),
                        }
                    )
                    event(
                        _(
                            "Direct answer blocked by Tool Factory gate · the reusable capability must be resolved first."
                        )
                    )
                    if factory_gate_refusals <= MAX_FACTORY_GATE_REFUSALS:
                        continue
                    factory_gate_required = False
                    factory_disabled = True
                    event(
                        _(
                            "Tool Factory gate attempt limit reached · finalising without inventing missing data."
                        )
                    )
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
                if final_answer:
                    if personalization_required:
                        messages.append({"role": "assistant", "content": final_answer})
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "PERSONALISATION CHECKPOINT. Treat the previous assistant message as a draft. "
                                    "Preserve supported findings and limitations, but make the final response genuinely "
                                    "personal using ONLY relevant_personal_context and relevant_self_reports from the "
                                    "local session context. For focused questions, weave the relevant context naturally "
                                    "into interpretation or the next action; do not add unrelated profile facts. For a "
                                    "comprehensive analysis, include a clearly identifiable personalised recommendations "
                                    "section. Respect temporal validity and confidence. Explicitly label subjective context "
                                    "as user-reported. If a current pattern resembles a prior temporary event explanation, "
                                    "do not assume the old cause still applies: ask one concise contextual question when it "
                                    "would improve interpretation. Do not invent measurements and do not call tools."
                                ),
                            }
                        )
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
                    if not factory_creation_notice_shown:
                        event(
                            _("The AI is creating a custom tool; this may take longer than usual.")
                        )
                        factory_creation_notice_shown = True
                    event(_("Capability gap detected · validating a reusable learned tool…"))
                elif name == "ask_user_feedback":
                    event(_("A targeted question could improve personalisation."))

                blocked_for_factory = False
                metric_argument = str(args.get("metric") or "").strip()
                if (
                    name in metric_reader_tools
                    and metric_argument
                    and metric_argument in tool_function_names
                ):
                    blocked_for_factory = True
                    factory_gate_required = factory_candidate and not factory_resolution_seen
                    result = {
                        "status": "invalid_metric_identifier",
                        "error": (
                            f"'{metric_argument}' is a tool/function name, not a raw VitalChronicle "
                            "metric identifier. Call that semantic tool directly, or compose it inside "
                            "a learned tool using call_tool. This result does NOT mean the underlying "
                            "health data are missing."
                        ),
                        "tool_name": metric_argument,
                    }
                    event(
                        _(
                            "Tool name rejected as a raw metric identifier: {metric}",
                            metric=metric_argument,
                        )
                    )
                if (
                    not blocked_for_factory
                    and name == "get_metric_series"
                    and factory_candidate
                    and not factory_resolution_seen
                ):
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
                        event(
                            _(
                                "Repeated raw-series probing stopped · switching to the Tool Factory decision."
                            )
                        )

                cache_key = base_rt._json_text({"tool": name, "arguments": args}, 8000)
                if not blocked_for_factory and cache_key in tool_result_cache:
                    result = tool_result_cache[cache_key]
                    event(
                        _(
                            "Repeated identical tool call reused from this analysis instead of consuming another query."
                        )
                    )
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
                            factory_gate_required = False
                            repair_turn = False
                            result = dict(result)
                            result["repairable"] = False
                            result["repair_budget_exhausted"] = True
                            result["instruction"] = (
                                "Tool Factory repair budget is exhausted for this request. Do not call "
                                "create_learned_tool again. Continue with exact semantic deterministic "
                                "tools and state any remaining limitation without substituting proxies."
                            )
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

                tool_text = base_rt._json_text(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": tool_text,
                    }
                )
                self._emit_agent_trace(name, "Agent", tool_text, kind="tool_result")

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
                and analysis_steps >= FACTORY_GATE_AFTER_ANALYSIS_STEPS
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
