from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"marker not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Runtime: strictly honor the active tool schema after Tool Factory repair exhaustion,
# and make the deterministic fallback concise and baseline-consistent.
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "MAX_FACTORY_GATE_REFUSALS = 2\nMAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2\n",
    "MAX_FACTORY_GATE_REFUSALS = 2\nMAX_OUT_OF_SCOPE_TOOL_REFUSALS = 2\nMAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2\n",
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "- Preserve units and method labels returned by deterministic tools; never relabel VitalChronicle cardio-load points as kcal.\n",
    "- Preserve units and method labels returned by deterministic tools; never relabel VitalChronicle cardio-load points as kcal.\n"
    "- Once the Tool Factory repair budget is exhausted, do not call create_learned_tool again in that request. Continue with exact existing deterministic tools only.\n"
    "- If a fallback answer must derive a personal baseline from an already-returned semantic date series, use the median as the robust VitalChronicle baseline convention and state that choice once; do not switch between mean and median.\n"
    "- Final answers must be result-first. Do not narrate scratchpad deliberation, self-corrections, or step-by-step arithmetic.\n"
    "- If there are zero qualifying trigger events, report zero events and explain that response frequency/recovery cannot be estimated; do not manufacture a downstream estimate.\n",
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "                    \"Mention any learned-tool validation failure only if it materially limits the answer.\"\n",
    "                    \"Mention any learned-tool validation failure only if it materially limits the answer. \"\n"
    "                    \"Do not narrate scratchpad deliberation, self-corrections, or step-by-step arithmetic. \"\n"
    "                    \"If you must derive a personal baseline from an already-returned semantic date series, \"\n"
    "                    \"use its median as the robust VitalChronicle baseline convention and state that once. \"\n"
    "                    \"If there are zero qualifying trigger events, report that directly and do not infer \"\n"
    "                    \"response frequency or recovery time.\"\n",
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "        factory_gate_refusals = 0\n        raw_series_probes = 0\n",
    "        factory_gate_refusals = 0\n        out_of_scope_tool_refusals = 0\n        raw_series_probes = 0\n",
)

# Enforce the exact active schema on every turn, not only while the Tool Factory gate is active.
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "            if not tool_calls:\n                final_answer = str(message.get(\"content\") or \"\").strip()\n",
    '''            if not gate_active and tool_calls:\n                active_tool_names = {\n                    str(item.get("function", {}).get("name") or "")\n                    for item in active_schemas\n                    if isinstance(item, dict) and isinstance(item.get("function"), dict)\n                }\n                allowed_turn_calls = [\n                    call\n                    for call in tool_calls\n                    if isinstance(call, dict) and base_rt._tool_name(call) in active_tool_names\n                ]\n                blocked_turn_names = [\n                    base_rt._tool_name(call)\n                    for call in tool_calls\n                    if isinstance(call, dict) and base_rt._tool_name(call) not in active_tool_names\n                ]\n                if blocked_turn_names:\n                    event(\n                        _(\n                            "Agent runtime blocked a tool call unavailable in this turn: {tools}",\n                            tools=", ".join(name for name in blocked_turn_names if name),\n                        )\n                    )\n                if allowed_turn_calls:\n                    tool_calls = allowed_turn_calls\n                    message = dict(message)\n                    message["tool_calls"] = tool_calls\n                    out_of_scope_tool_refusals = 0\n                elif blocked_turn_names:\n                    out_of_scope_tool_refusals += 1\n                    messages.append(\n                        {\n                            "role": "system",\n                            "content": (\n                                "RUNTIME TOOL ALLOW-LIST: the previous tool call was rejected because "\n                                "that function is not available in this turn. Use only tools advertised "\n                                "in the current schema, or answer from the exact deterministic evidence "\n                                "already collected. If Tool Factory repairs were exhausted, do not call "\n                                "create_learned_tool again."\n                            ),\n                        }\n                    )\n                    if out_of_scope_tool_refusals >= MAX_OUT_OF_SCOPE_TOOL_REFUSALS:\n                        return self._final_answer(\n                            model=model,\n                            messages=messages,\n                            max_tokens=max_tokens,\n                            physical_limit=physical_limit,\n                            think=think,\n                            cancel_callback=cancel_callback,\n                            event=event,\n                            answer_callback=answer_callback,\n                        )\n                    continue\n\n            if not tool_calls:\n                final_answer = str(message.get("content") or "").strip()\n''',
)

replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    '''                        if factory_repairs >= MAX_FACTORY_REPAIR_ATTEMPTS:\n                            factory_disabled = True\n                            event(\n                                _(\n                                    "Tool Factory repair budget reached · continuing without proxy "\n                                    "substitution and preserving the evidence already collected."\n                                )\n                            )\n''',
    '''                        if factory_repairs >= MAX_FACTORY_REPAIR_ATTEMPTS:\n                            factory_disabled = True\n                            factory_gate_required = False\n                            repair_turn = False\n                            result = dict(result)\n                            result["repairable"] = False\n                            result["repair_budget_exhausted"] = True\n                            result["instruction"] = (\n                                "Tool Factory repair budget is exhausted for this request. Do not call "\n                                "create_learned_tool again. Continue with exact semantic deterministic "\n                                "tools and state any remaining limitation without substituting proxies."\n                            )\n                            event(\n                                _(\n                                    "Tool Factory repair budget reached · continuing without proxy "\n                                    "substitution and preserving the evidence already collected."\n                                )\n                            )\n''',
)

# Tool Factory schema: give small local models a canonical, exact event-response construction.
factory = Path("google_health_viewer/agent_tool_factory.py")
text = factory.read_text(encoding="utf-8")
marker = "\n_PIPELINE_STEP_SCHEMA: dict[str, Any] = {\n"
if marker not in text:
    raise SystemExit("pipeline schema marker not found")
example_block = '''\nEVENT_RESPONSE_PARAMETERS_EXAMPLE = {\n    "type": "object",\n    "properties": {\n        "start": {"type": "string"},\n        "end": {"type": "string"},\n        "event_percent": {"type": "number", "default": 30},\n        "response_percent": {"type": "number", "default": 20},\n        "recovery_tolerance_percent": {"type": "number", "default": 10},\n    },\n    "required": ["start", "end"],\n}\n\nEVENT_RESPONSE_PIPELINE_EXAMPLE = [\n    {\n        "op": "call_tool",\n        "tool": "calculate_cardio_load",\n        "arguments": {"start": "$start", "end": "$end"},\n        "as": "event_raw",\n    },\n    {\n        "op": "extract_series",\n        "source": "event_raw",\n        "path": "daily_load",\n        "key_field": "date",\n        "value_field": "load",\n        "as": "event_series",\n    },\n    {"op": "baseline", "source": "event_series", "as": "event_baseline"},\n    {\n        "op": "filter_relative",\n        "source": "event_series",\n        "baseline_source": "event_baseline",\n        "baseline_field": "median",\n        "direction": "above",\n        "percent": "$event_percent",\n        "as": "events",\n    },\n    {\n        "op": "call_tool",\n        "tool": "get_sleep_stage_series",\n        "arguments": {"start": "$start", "end": "$end"},\n        "as": "response_raw",\n    },\n    {\n        "op": "extract_series",\n        "source": "response_raw",\n        "path": "daily_stages",\n        "key_field": "date",\n        "value_field": "deep",\n        "as": "response_series",\n    },\n    {"op": "baseline", "source": "response_series", "as": "response_baseline"},\n    {\n        "op": "event_response",\n        "event_source": "events",\n        "response_source": "response_series",\n        "response_baseline_source": "response_baseline",\n        "baseline_field": "median",\n        "response_direction": "below",\n        "response_percent": "$response_percent",\n        "response_offset_days": 1,\n        "recovery_tolerance_percent": "$recovery_tolerance_percent",\n        "max_recovery_days": 14,\n        "as": "analysis",\n    },\n    {"op": "return", "source": "analysis"},\n]\n'''
text = text.replace(marker, example_block + marker, 1)
factory.write_text(text, encoding="utf-8")

replace_once(
    "google_health_viewer/agent_tool_factory.py",
    '''        "parameters": {\n            "type": "object",\n            "description": "JSON Schema for reusable user-adjustable inputs.",\n        },\n''',
    '''        "parameters": {\n            "type": "object",\n            "description": (\n                "JSON Schema for reusable user-adjustable inputs. Property defaults are applied at "\n                "learned-tool runtime when the caller omits that argument. For event-response tools, "\n                "use the supplied example rather than inventing a parameter shape."\n            ),\n            "examples": [EVENT_RESPONSE_PARAMETERS_EXAMPLE],\n        },\n''',
)

replace_once(
    "google_health_viewer/agent_tool_factory.py",
    '''            "description": (\n                "Safe pipeline. Store each intermediate result with 'as' and reference it by name. "\n                "Never invent operations outside the op enum."\n            ),\n''',
    '''            "description": (\n                "Safe pipeline. Store each intermediate result with 'as' and reference it by name. "\n                "Never invent operations outside the op enum. For an event above a personal baseline "\n                "followed by a next-day/night response and recovery-time analysis, copy the supplied "\n                "canonical example structure and change only the semantic tools/fields/thresholds needed."\n            ),\n            "examples": [EVENT_RESPONSE_PIPELINE_EXAMPLE],\n''',
)

replace_once(
    "google_health_viewer/agent_tool_factory.py",
    '''                "dsl_reference": DSL_REFERENCE,\n                "instruction": (\n''',
    '''                "dsl_reference": DSL_REFERENCE,\n                "canonical_event_response_pipeline": EVENT_RESPONSE_PIPELINE_EXAMPLE,\n                "canonical_event_response_parameters": EVENT_RESPONSE_PARAMETERS_EXAMPLE,\n                "instruction": (\n''',
)

replace_once(
    "google_health_viewer/agent_tool_factory.py",
    '''    def _run_learned(self, item: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:\n        pipeline = self.validate_pipeline(item["pipeline"])\n        env: dict[str, Any] = {}\n        last: Any = None\n        default_left, default_right = base._bounds(args.get("start"), args.get("end"), 60)\n''',
    '''    def _run_learned(self, item: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:\n        pipeline = self.validate_pipeline(item["pipeline"])\n        runtime_args = dict(args)\n        parameter_schema = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}\n        properties = (\n            parameter_schema.get("properties")\n            if isinstance(parameter_schema.get("properties"), dict)\n            else {}\n        )\n        for key, spec in properties.items():\n            if key not in runtime_args and isinstance(spec, dict) and "default" in spec:\n                runtime_args[str(key)] = spec["default"]\n        args = runtime_args\n        env: dict[str, Any] = {}\n        last: Any = None\n        default_left, default_right = base._bounds(args.get("start"), args.get("end"), 60)\n''',
)

# Regression tests for the real-world run that exhausted three repairs and hallucinated a fourth.
tests = Path("tests/test_agent_tool_factory_v2.py")
text = tests.read_text(encoding="utf-8")
text += '''\n\ndef test_factory_schema_includes_canonical_event_response_example(tmp_path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    executor = EnhancedSafeToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)\n    functions = {item["function"]["name"]: item["function"] for item in executor.tool_schemas()}\n    create_schema = functions["create_learned_tool"]["parameters"]\n    pipeline_examples = create_schema["properties"]["pipeline"]["examples"]\n    parameters_examples = create_schema["properties"]["parameters"]["examples"]\n\n    assert pipeline_examples[0][0]["tool"] == "calculate_cardio_load"\n    assert any(step.get("tool") == "get_sleep_stage_series" for step in pipeline_examples[0])\n    assert any(step.get("op") == "event_response" for step in pipeline_examples[0])\n    assert parameters_examples[0]["properties"]["event_percent"]["default"] == 30\n\n\ndef test_learned_tool_applies_parameter_defaults(tmp_path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    executor = StubToolExecutor(DummyHealthStore(tmp_path / "health.sqlite3"), store)\n    pipeline = _original_question_pipeline()\n    pipeline[3]["percent"] = "$event_percent"\n    pipeline[7]["response_percent"] = "$response_percent"\n    pipeline[7]["recovery_tolerance_percent"] = "$recovery_tolerance_percent"\n    created = executor.execute(\n        "create_learned_tool",\n        {\n            "name": "defaulted_event_response",\n            "description": "Reusable event response with parameter defaults.",\n            "capability": "analysis.event_response.defaults",\n            "parameters": {\n                "type": "object",\n                "properties": {\n                    "start": {"type": "string"},\n                    "end": {"type": "string"},\n                    "event_percent": {"type": "number", "default": 30},\n                    "response_percent": {"type": "number", "default": 20},\n                    "recovery_tolerance_percent": {"type": "number", "default": 10},\n                },\n            },\n            "pipeline": pipeline,\n        },\n    )\n    assert created["status"] == "created"\n\n    answer = executor.execute(\n        "defaulted_event_response",\n        {"start": "2026-01-01", "end": "2026-01-08"},\n    )["result"]\n    assert answer["trigger_events"] == 2\n    assert answer["response_matches"] == 2\n    assert answer["response_rate_percent"] == pytest.approx(100.0)\n\n\nclass ExhaustedRepairHallucinationRuntime(AgentRuntime):\n    def __init__(self, health_store, agent_store):\n        super().__init__(health_store, agent_store)\n        self.turn = 0\n        self.available_by_turn: list[set[str]] = []\n\n    def _chat_once(self, **kwargs):\n        self.turn += 1\n        names = {\n            str(item.get("function", {}).get("name") or "")\n            for item in kwargs.get("tools", [])\n            if isinstance(item, dict)\n        }\n        self.available_by_turn.append(names)\n        if self.turn <= 3:\n            return {\n                "content": "",\n                "tool_calls": [\n                    {\n                        "function": {\n                            "name": "create_learned_tool",\n                            "arguments": {\n                                "name": "repair_budget_test",\n                                "description": "Invalid until budget exhaustion",\n                                "capability": "analysis.repair_budget",\n                                "pipeline": [{"op": "invented_operation"}],\n                            },\n                        }\n                    }\n                ],\n            }\n        if self.turn == 4:\n            # Deliberately hallucinate the now-hidden factory function. Runtime must block it.\n            return {\n                "content": "",\n                "tool_calls": [\n                    {\n                        "function": {\n                            "name": "create_learned_tool",\n                            "arguments": {\n                                "name": "should_never_be_created",\n                                "description": "Must be blocked after repair exhaustion",\n                                "capability": "analysis.must_not_create",\n                                "pipeline": [{"op": "return"}],\n                            },\n                        }\n                    }\n                ],\n            }\n        return {"content": "Risposta finale deterministica dopo il repair budget."}\n\n\ndef test_factory_cannot_execute_fourth_create_after_three_failed_repairs(tmp_path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    runtime = ExhaustedRepairHallucinationRuntime(\n        DummyHealthStore(tmp_path / "health.sqlite3"), store\n    )\n    events: list[str] = []\n    answer = runtime.analyze(\n        model="test",\n        snapshot={},\n        question=(\n            "Quando il carico supera del 30% la baseline personale, quanto spesso la notte "\n            "successiva il sonno profondo scende del 20% e quanto impiega a recuperare?"\n        ),\n        history=[],\n        max_tokens=1024,\n        model_context_limit=None,\n        performance_profile="standard",\n        thread_id="repair-exhaustion-thread",\n        event_callback=events.append,\n    )\n\n    assert answer.startswith("Risposta finale deterministica")\n    assert runtime.turn == 5\n    assert "create_learned_tool" not in runtime.available_by_turn[3]\n    assert store.tool("should_never_be_created") is None\n    assert any("unavailable in this turn" in event.lower() for event in events)\n'''
tests.write_text(text, encoding="utf-8")

print("Tool Factory efficiency patch prepared")
