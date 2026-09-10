from pathlib import Path


def replace_once(path, old, new):
    p=Path(path); t=p.read_text()
    if new in t: return
    if old not in t: raise AssertionError(path)
    p.write_text(t.replace(old,new,1))

replace_once('google_health_viewer/agent_runtime.py', '''def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
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


class AgentRuntime:
''', '''def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
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
''')
replace_once('google_health_viewer/agent_runtime.py', '''            except LocalAIError as exc:
                lowered = str(exc).lower()
                tool_support_markers = (
                    "tool",
                    "function",
                    "unsupported",
                    "does not support",
                    "invalid tool",
                )
                if any(marker in lowered for marker in tool_support_markers):
                    answer = self._fallback(str(exc))
                else:
                    raise
''', '''            except LocalAIError as exc:
                if _tool_calling_unavailable_error(str(exc)):
                    answer = self._fallback(str(exc))
                else:
                    raise
''')

p=Path('tests/test_agent_tool_factory_v2.py'); t=p.read_text()
t=t.replace('from google_health_viewer.agent_runtime import AGENT_TRACE_PREFIX\n','from google_health_viewer.agent_runtime import (\n    AGENT_TRACE_PREFIX,\n    _tool_calling_unavailable_error,\n)\n',1)
if 'test_generic_tool_word_does_not_trigger_unsupported_model_fallback' not in t:
    t += '''\n\ndef test_generic_tool_word_does_not_trigger_unsupported_model_fallback():\n    assert _tool_calling_unavailable_error(\n        "The local model returned neither an answer nor a tool call."\n    ) is False\n    assert _tool_calling_unavailable_error("model does not support tools") is True\n'''
p.write_text(t)
