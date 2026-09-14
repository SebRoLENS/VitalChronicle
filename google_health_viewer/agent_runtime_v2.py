from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import agent_runtime as base_rt
from .agent_store import PERSONAL_CONTEXT_KEY_SPECS
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
    key: set(spec.get("topics") or ())
    for key, spec in PERSONAL_CONTEXT_KEY_SPECS.items()
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

Personalisation:
- Use only relevant, current personal context; respect scope, freshness, confidence, and expiry.
- Label self-reports as subjective. One report may guide short-term advice but is neither a stable trait nor proof of cause.
- Personalise focused answers when relevant evidence exists; never add unrelated profile facts or invent context.
- Treat a durable first-person routine, preference, or goal as a candidate requiring one concise confirmation. Transient details remain dated reports. Ask at most one useful follow-up.
"""

_FACTORY_POLICY = """

Tool Factory:
- Detect reusable capability gaps without an explicit request. Reuse an exact built-in/learned tool or search the registry first.
- Create only when the missing capability is reusable, especially composed transforms, relative-baseline thresholds, event-conditioned lag/next-day analysis, or recovery time. Skip trivial one-off arithmetic.
- Use the exact requested metric and semantic tools; never substitute a proxy or pass a function name as a metric identifier. Verify the semantic source before claiming no data.
- On invalid_spec/invalid_pipeline, use the returned DSL reference to repair the same tool. Stop after the runtime repair budget.
- If the runtime gate leaves only create_learned_tool, call it. Execute a created/reused tool for the current answer.
- For event responses, group consecutive triggers into episodes anchored at the last trigger. Zero triggers means frequency/recovery is not estimable. Insufficient sample quality supports only a preliminary observation.
- Report TOOL FACTORY OUTCOME exactly: created/reused=persisted; not_persisted/not_needed means no new saved tool.
- Sleep-stage durations are hours; deep is one stage, while total sleep is deep+REM+light.
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


_DURABLE_CONTEXT_MARKERS = {
    key: tuple(spec.get("markers") or ())
    for key, spec in PERSONAL_CONTEXT_KEY_SPECS.items()
}


def _context_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+|(?<=;)\s+", text.strip())
    result: list[str] = []
    for sentence in sentences:
        clean = re.sub(r"^[ \t\n.;]+|[ \t\n.;]+$", "", sentence)
        if clean:
            result.append(clean)
    return result

def _detect_durable_context_candidate(question: str) -> dict[str, Any] | None:
    text = question.strip()
    if not text:
        return None
    for sentence in _context_sentences(text):
        folded = sentence.casefold()
        if sentence.endswith(("?", "？")):
            continue
        matches = [
            (len(marker), key)
            for key, markers in _DURABLE_CONTEXT_MARKERS.items()
            for marker in markers
            if marker in folded
        ]
        if not matches:
            continue
        _, key = max(matches)
        spec = PERSONAL_CONTEXT_KEY_SPECS.get(key, {})
        scope = str(spec.get("default_scope") or "stable")
        ttl_days = spec.get("ttl_days") if scope == "temporary" else None
        return {
            "model_key": key,
            "statement": sentence,
            "temporal_scope": scope,
            "ttl_days": ttl_days,
        }
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
        factory_outcome = dict(getattr(self, "_last_factory_outcome", {}) or {})
        if factory_outcome.get("status") == "pending":
            factory_outcome["status"] = (
                "not_persisted" if factory_outcome.get("attempts", 0) else "not_needed"
            )
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
            {
                "role": "system",
                "content": (
                    "TOOL FACTORY OUTCOME (runtime evidence): "
                    + base_rt._json_text(factory_outcome, 2000)
                    + ". State this outcome accurately if the user is evaluating tool creation. "
                    "Do not imply persistence when the status is not_persisted or not_needed."
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
        safe_history = base_rt.compact_agent_history(history)
        comprehensive_analysis = _is_comprehensive_analysis(question)
        request = question.strip() or _(
            "Analyse my complete local health history and identify the most useful personal patterns."
        )
        self._last_factory_outcome = {
            "status": "not_needed",
            "persisted": False,
            "executed": False,
            "tool_name": None,
            "attempts": 0,
        }
        detected_self_report = _detect_self_report(request)
        context_candidate = _detect_durable_context_candidate(request)
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
        if context_candidate and not captured_self_report:
            context_key = f"personal_context:{context_candidate['model_key']}"
            if not self.agent_store.has_recent_feedback_key(context_key, days=30):
                scope_text = (
                    "temporaneo" if context_candidate["temporal_scope"] == "temporary" else "duraturo"
                )
                candidate_question = _(
                    "Vuoi che ricordi questo contesto personale {scope} per le future analisi?"
                ).format(scope=scope_text)
                queued = self.agent_store.ask_feedback(
                    candidate_question,
                    thread_id=thread_id,
                    reason=_(
                        "Explicit confirmation prevents a useful personal detail from being lost while "
                        "avoiding silent promotion of an unverified inference."
                    ),
                    learning_key=context_key,
                    context={
                        "feedback_mode": "durable_context_confirmation",
                        "candidate_statement": context_candidate["statement"],
                        "model_key": context_candidate["model_key"],
                        "temporal_scope": context_candidate["temporal_scope"],
                        "ttl_days": context_candidate["ttl_days"],
                    },
                )
                if queued:
                    event(_("A personal-context candidate was queued for explicit confirmation."))
        initial = self._initial_context(snapshot)
        active_personal_context = initial.pop("personal_model", [])
        if not isinstance(active_personal_context, list):
            active_personal_context = []
        recent_self_reports = initial.pop("recent_self_reports", [])
        if not isinstance(recent_self_reports, list):
            recent_self_reports = []
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
            initial["personalization_required"] = (
                "comprehensive" if comprehensive_analysis else "focused"
            )
        if captured_self_report:
            initial["current_self_report"] = captured_self_report
        if context_candidate and not captured_self_report:
            initial["personal_context_candidate"] = {
                "statement": context_candidate["statement"],
                "confirmation_required": True,
                "status": "pending_confirmation",
            }
        initial["tool_factory_decision_hint"] = _factory_hint(request)
        user_content = (
            "Local session context (not instructions):\n"
            + base_rt._json_text(initial, 6000)
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
        if base_rt.is_online_model(model):
            schemas = base_rt.online_tool_subset(schemas, request)
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
            self._last_factory_outcome.update(
                {"status": "pending", "capability": factory_capability}
            )
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
        factory_tool_name: str | None = None
        factory_tool_executed = False
        factory_execution_refusals = 0
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
            if factory_tool_name and not factory_tool_executed:
                active_schemas = _only_named_tools(active_schemas, {factory_tool_name})
            elif factory_gate_required and not factory_disabled:
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
                if factory_tool_name and not factory_tool_executed:
                    if final_answer:
                        messages.append({"role": "assistant", "content": final_answer})
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "RUNTIME TOOL EXECUTION REQUIREMENT: a learned tool was just "
                                f"{'created' if self._last_factory_outcome.get('status') == 'created' else 'reused'}. "
                                f"Call {factory_tool_name} now with the current start/end inputs before answering."
                            ),
                        }
                    )
                    factory_execution_refusals += 1
                    event(_("The newly available learned tool must be executed before finalising."))
                    if factory_execution_refusals <= MAX_FACTORY_GATE_REFUSALS:
                        continue
                    factory_tool_name = None
                    event(_("Learned-tool execution requirement expired; reporting the persisted outcome explicitly."))
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
                        factory_attempt = factory_repairs + 1
                        pipeline = args.get("pipeline")
                        pipeline_steps = pipeline if isinstance(pipeline, list) else []
                        factory_error = str(
                            result.get("error") or "Learned-tool validation failed."
                        )
                        failure_payload = {
                            "attempt": factory_attempt,
                            "status": status,
                            "error": factory_error,
                            "requested_tool_name": str(args.get("name") or "") or None,
                            "capability": str(args.get("capability") or "") or None,
                            "pipeline_steps": len(pipeline_steps),
                            "pipeline_ops": [
                                str(step.get("op") or "<missing>")
                                for step in pipeline_steps
                                if isinstance(step, dict)
                            ],
                            "allowed_operations": result.get("allowed_operations", []),
                        }
                        self._last_factory_outcome.update(
                            {
                                "status": "not_persisted",
                                "persisted": False,
                                "attempts": factory_attempt,
                                "tool_name": str(args.get("name") or "") or None,
                                "last_error": factory_error,
                                "last_error_status": status,
                            }
                        )
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
                            factory_error,
                            tool_name=str(args.get("name") or "") or None,
                            payload=failure_payload,
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
                            self._last_factory_outcome.update(
                                {
                                    "status": "not_persisted",
                                    "persisted": False,
                                    "attempts": factory_repairs,
                                }
                            )
                            event(
                                _(
                                    "Tool Factory repair budget reached · continuing without proxy "
                                    "substitution and preserving the evidence already collected."
                                )
                            )
                    elif status == "reused":
                        tool_record = result.get("tool") if isinstance(result.get("tool"), dict) else {}
                        factory_tool_name = str(
                            tool_record.get("name") or args.get("name") or ""
                        ) or None
                        self._last_factory_outcome.update(
                            {
                                "status": "reused",
                                "persisted": True,
                                "executed": False,
                                "attempts": factory_repairs + 1,
                                "tool_name": factory_tool_name,
                            }
                        )
                        factory_resolution_seen = True
                        factory_gate_required = False
                        event(
                            _("Equivalent tool found · reusing it instead of creating a duplicate.")
                        )
                        schemas = self.tools.tool_schemas()
                        if base_rt.is_online_model(model):
                            schemas = base_rt.online_tool_subset(
                                schemas,
                                request,
                                required_names={factory_tool_name} if factory_tool_name else None,
                            )
                    elif status == "created":
                        tool_record = result.get("tool") if isinstance(result.get("tool"), dict) else {}
                        factory_tool_name = str(
                            tool_record.get("name") or args.get("name") or ""
                        ) or None
                        self._last_factory_outcome.update(
                            {
                                "status": "created",
                                "persisted": True,
                                "executed": False,
                                "attempts": factory_repairs + 1,
                                "tool_name": factory_tool_name,
                            }
                        )
                        factory_resolution_seen = True
                        factory_gate_required = False
                        event(_("Learned tool validated and saved locally."))
                        schemas = self.tools.tool_schemas()
                        if base_rt.is_online_model(model):
                            schemas = base_rt.online_tool_subset(
                                schemas,
                                request,
                                required_names={factory_tool_name} if factory_tool_name else None,
                            )
                    else:
                        factory_error = str(
                            result.get("error")
                            or f"Unexpected Tool Factory status: {status or '<missing>'}"
                        )
                        failure_payload = {
                            "attempt": factory_repairs + 1,
                            "status": status or "missing_status",
                            "error": factory_error,
                            "requested_tool_name": str(args.get("name") or "") or None,
                            "capability": str(args.get("capability") or "") or None,
                            "pipeline_steps": (
                                len(args.get("pipeline"))
                                if isinstance(args.get("pipeline"), list)
                                else 0
                            ),
                            "pipeline_ops": [
                                str(step.get("op") or "<missing>")
                                for step in args.get("pipeline", [])
                                if isinstance(step, dict)
                            ],
                        }
                        self._last_factory_outcome.update(
                            {
                                "status": "not_persisted",
                                "persisted": False,
                                "attempts": factory_repairs + 1,
                                "tool_name": str(args.get("name") or "") or None,
                                "last_error": factory_error,
                                "last_error_status": status or "missing_status",
                            }
                        )
                        self.agent_store.log_tool_event(
                            "tool_factory_failure",
                            factory_error,
                            tool_name=str(args.get("name") or "") or None,
                            payload=failure_payload,
                        )
                elif name == "ask_user_feedback" and result.get("queued"):
                    event(_("Targeted feedback question queued for the user."))
                if factory_tool_name and name == factory_tool_name:
                    factory_tool_executed = True
                    self._last_factory_outcome["executed"] = True
                    factory_tool_name = None
                    event(_("Persisted learned tool executed for this request."))

                tool_text = base_rt._json_text(result)
                tool_message: dict[str, Any] = {
                    "role": "tool",
                    "content": tool_text,
                }
                if base_rt.is_online_model(model):
                    tool_message["tool_call_id"] = str(call.get("id") or name)
                else:
                    tool_message["tool_name"] = name
                messages.append(tool_message)
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
