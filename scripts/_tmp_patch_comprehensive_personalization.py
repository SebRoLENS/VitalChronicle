from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if new in text:
        return
    if old not in text:
        raise AssertionError(f"pattern not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1))


# Make use of current personal context an explicit core runtime rule.
replace_once(
    "google_health_viewer/agent_runtime.py",
    '''11. When confidence or coverage is low, state that clearly. A missing/None score component means unavailable evidence, never a neutral or zero value.\n\nThe health archive is read-only to the agent.''',
    '''11. When confidence or coverage is low, state that clearly. A missing/None score component means unavailable evidence, never a neutral or zero value.\n12. Current, non-expired personal context and recent subjective self-reports are evidence for personalisation.\n    Use them when they materially change interpretation or recommendations, while clearly distinguishing user-reported\n    context from measured physiology. For a comprehensive health-history analysis, recommendations must be adapted\n    to relevant current goals/context instead of remaining generic. A one-off self-report may guide a short-term\n    suggestion but must never be presented as a stable trait or as proof of causation.\n\nThe health archive is read-only to the agent.''',
)

# Add comprehensive-analysis detection and a strong synthesis policy.
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''MAX_TOTAL_MODEL_TURNS = MAX_ANALYSIS_STEPS + MAX_FACTORY_REPAIR_ATTEMPTS + 4\n\n_FACTORY_POLICY = """''',
    '''MAX_TOTAL_MODEL_TURNS = MAX_ANALYSIS_STEPS + MAX_FACTORY_REPAIR_ATTEMPTS + 4\n\n_COMPREHENSIVE_ANALYSIS_MARKERS = (\n    "analisi totale",\n    "analisi completa",\n    "analisi profonda",\n    "cronologia completa",\n    "tutta la cronologia",\n    "full analysis",\n    "complete analysis",\n    "deep analysis",\n    "complete health history",\n    "complete local health history",\n    "entire health history",\n)\n\n\ndef _is_comprehensive_analysis(question: str) -> bool:\n    text = str(question or "").strip().casefold()\n    if not text:\n        return True\n    return any(marker in text for marker in _COMPREHENSIVE_ANALYSIS_MARKERS)\n\n\n_PERSONALIZATION_POLICY = """\n\nPersonalisation synthesis policy:\n- `personal_model` contains current, non-expired learned personal context. Treat its temporal scope, confidence,\n  freshness and evidence count as part of the evidence; never resurrect expired context.\n- `recent_self_reports` are dated subjective observations. They can justify short-term, conditional suggestions,\n  but one report is not a stable trait and does not prove a physiological cause.\n- When a current personal statement materially changes interpretation, say so explicitly and distinguish it from\n  wearable-derived evidence. Example: if irregular sleep was reported as an exceptional social event, do not present\n  one low regularity score as proof of a persistent schedule problem.\n- For a comprehensive/whole-history analysis with active personal context, the final answer MUST contain a clearly\n  identifiable personalised recommendations section. Translate relevant current goals, temporary context and recent\n  self-reports into concrete next actions rather than repeating generic advice.\n- Personalisation must remain evidence-bound: do not invent preferences, schedules, symptoms or causes that are not\n  present in the current personal context, recent reports or deterministic health evidence.\n"""\n\n_FACTORY_POLICY = """''',
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''        request = question.strip() or _(\n            "Analyse my complete local health history and identify the most useful personal patterns."\n        )\n        detected_self_report = _detect_self_report(request)''',
    '''        comprehensive_analysis = _is_comprehensive_analysis(question)\n        request = question.strip() or _(\n            "Analyse my complete local health history and identify the most useful personal patterns."\n        )\n        detected_self_report = _detect_self_report(request)''',
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''        initial = self._initial_context(snapshot)\n        if captured_self_report:\n            initial["current_self_report"] = captured_self_report''',
    '''        initial = self._initial_context(snapshot)\n        active_personal_context = (\n            initial.get("personal_model") if isinstance(initial.get("personal_model"), list) else []\n        )\n        recent_self_reports = (\n            initial.get("recent_self_reports")\n            if isinstance(initial.get("recent_self_reports"), list)\n            else []\n        )\n        personalization_available = bool(active_personal_context or recent_self_reports)\n        if comprehensive_analysis and personalization_available:\n            initial["personalization_requirement"] = {\n                "mode": "comprehensive",\n                "required": True,\n                "instruction": (\n                    "Use materially relevant current personal context and recent self-reports in the final "\n                    "recommendations. Distinguish subjective reports from measured evidence, respect temporal "\n                    "validity, and do not infer causation from a single report."\n                ),\n            }\n        if captured_self_report:\n            initial["current_self_report"] = captured_self_report''',
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''        system_prompt = base_rt.agent_system_prompt() + _FACTORY_POLICY''',
    '''        system_prompt = base_rt.agent_system_prompt() + _FACTORY_POLICY + _PERSONALIZATION_POLICY''',
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''                    "If there are zero qualifying trigger events, report that directly and do not infer "\n                    "response frequency or recovery time."''',
    '''                    "If there are zero qualifying trigger events, report that directly and do not infer "\n                    "response frequency or recovery time. Use any current, non-expired personal context and "\n                    "recent self-reports already present in the session when they materially improve the "\n                    "interpretation or recommendations; keep subjective reports explicitly separate from "\n                    "measured evidence."''',
)

# Force a final synthesis pass for broad analyses when personal context exists. This prevents a good data
# summary from returning before the active personal model has been applied to recommendations.
needle = '''                if final_answer:\n                    if answer_callback:\n                        answer_callback(final_answer)\n                    event(_("Agent finished the analysis."))\n                    return final_answer\n                raise LocalAIError(_("The local model returned neither an answer nor a tool call."))'''
replacement = '''                if final_answer:\n                    if comprehensive_analysis and personalization_available:\n                        messages.append({"role": "assistant", "content": final_answer})\n                        messages.append(\n                            {\n                                "role": "system",\n                                "content": (\n                                    "COMPREHENSIVE PERSONALISATION CHECKPOINT. Treat the previous assistant "\n                                    "message as a draft, not the final answer. Preserve supported findings and "\n                                    "limitations, but now produce the final response with a clearly identifiable "\n                                    "personalised recommendations section. Use materially relevant CURRENT entries "\n                                    "from personal_model and recent_self_reports already supplied in the local "\n                                    "session context. Respect temporal validity and confidence. Explicitly label "\n                                    "subjective context as user-reported; a one-off report can support a short-term "\n                                    "conditional recommendation but not a stable trait or causal claim. Do not "\n                                    "invent new measurements and do not call tools."\n                                ),\n                            }\n                        )\n                        return self._final_answer(\n                            model=model,\n                            messages=messages,\n                            max_tokens=max_tokens,\n                            physical_limit=physical_limit,\n                            think=think,\n                            cancel_callback=cancel_callback,\n                            event=event,\n                            answer_callback=answer_callback,\n                        )\n                    if answer_callback:\n                        answer_callback(final_answer)\n                    event(_("Agent finished the analysis."))\n                    return final_answer\n                raise LocalAIError(_("The local model returned neither an answer nor a tool call."))'''
replace_once("google_health_viewer/agent_runtime_v2.py", needle, replacement)

# Tests: detection + end-to-end forced personalisation synthesis.
p = Path("tests/test_agent_tool_factory_v2.py")
t = p.read_text()
t = t.replace(
    "from google_health_viewer.agent_runtime_v2 import AgentRuntime, _factory_hint\n",
    "from google_health_viewer.agent_runtime_v2 import (\n    AgentRuntime,\n    _factory_hint,\n    _is_comprehensive_analysis,\n)\n",
    1,
)
if "test_comprehensive_analysis_detection" not in t:
    t += '''\n\ndef test_comprehensive_analysis_detection():\n    assert _is_comprehensive_analysis("") is True\n    assert _is_comprehensive_analysis("Fammi una analisi totale") is True\n    assert _is_comprehensive_analysis("Analizza solo il sonno di ieri") is False\n\n\nclass ComprehensivePersonalizationRuntime(AgentRuntime):\n    def __init__(self, health_store, agent_store):\n        super().__init__(health_store, agent_store)\n        self.turn = 0\n        self.final_messages = None\n\n    def _chat_once(self, **kwargs):\n        self.turn += 1\n        if self.turn == 1:\n            return {"content": "Analisi generale corretta ma con consigli generici."}\n        self.final_messages = kwargs.get("messages")\n        assert kwargs.get("tools") == []\n        return {\n            "content": (\n                "Raccomandazioni personalizzate: adatta il recupero al tuo attuale obiettivo "\n                "di allenamento, trattando i self-report recenti come osservazioni soggettive."\n            )\n        }\n\n\ndef test_comprehensive_analysis_forces_personalized_final_synthesis(tmp_path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    store.learn_user_model(\n        "current_training_goal",\n        "User feedback: cycling commute and strength training are current goals",\n        evidence={"answer": "cycling commute and strength training"},\n        source="feedback",\n    )\n    runtime = ComprehensivePersonalizationRuntime(\n        DummyHealthStore(tmp_path / "health.sqlite3"), store\n    )\n\n    answer = runtime.analyze(\n        model="test",\n        snapshot={},\n        question="Analisi totale",\n        history=[],\n        max_tokens=1024,\n        model_context_limit=None,\n        performance_profile="standard",\n        thread_id="personalized-total",\n    )\n\n    assert runtime.turn == 2\n    assert "Raccomandazioni personalizzate" in answer\n    assert runtime.final_messages is not None\n    joined = "\\n".join(str(item.get("content") or "") for item in runtime.final_messages)\n    assert "COMPREHENSIVE PERSONALISATION CHECKPOINT" in joined\n    assert "cycling commute and strength training" in joined\n'''
p.write_text(t)
