from __future__ import annotations

import pytest

from google_health_viewer.agent_runtime import online_tool_subset
from google_health_viewer.agent_runtime_v2 import AgentRuntime
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.ai_query_planner import AIDataPlanThread
from google_health_viewer.local_ai import LocalAIError
from google_health_viewer.online_ai import (
    GROQ_MODEL,
    MISTRAL_MODEL,
    is_online_model,
    online_chat_completion,
    online_model_id,
    online_provider_for_model,
)
from google_health_viewer.storage import HealthStore


def _schema(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _Response:
    def __init__(self, status: int, payload: dict, headers: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.reason = "Too Many Requests" if status == 429 else "OK"

    def json(self):
        return self._payload


def test_online_provider_resolution_preserves_mistral_and_strips_groq_prefix() -> None:
    assert is_online_model(MISTRAL_MODEL)
    assert online_provider_for_model(MISTRAL_MODEL).name == "Mistral"
    assert online_provider_for_model(GROQ_MODEL).name == "Groq"
    assert online_model_id(GROQ_MODEL) == "openai/gpt-oss-20b"


def test_online_completion_uses_groq_endpoint_and_wire_model(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("google_health_viewer.online_ai.requests.post", fake_post)
    monkeypatch.setattr(
        "google_health_viewer.online_ai.online_api_key", lambda _provider: "test-key"
    )
    payload = online_chat_completion(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
        temperature=0.1,
    )

    assert payload["choices"][0]["message"]["content"] == "ok"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["json"]["model"] == "openai/gpt-oss-20b"
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_rate_limit_error_includes_available_provider_diagnostics(monkeypatch) -> None:
    def fake_post(*_args, **_kwargs):
        return _Response(
            429,
            {"error": {"message": "Rate limit exceeded"}},
            {
                "retry-after": "12",
                "x-ratelimit-remaining-tokens": "0",
                "x-ratelimit-reset-tokens": "14s",
            },
        )

    monkeypatch.setattr("google_health_viewer.online_ai.requests.post", fake_post)
    monkeypatch.setattr(
        "google_health_viewer.online_ai.online_api_key", lambda _provider: "test-key"
    )
    with pytest.raises(LocalAIError) as raised:
        online_chat_completion(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            temperature=0.1,
        )

    message = str(raised.value)
    assert "HTTP 429" in message
    assert "retry-after: 12" in message
    assert "tokens remaining: 0" in message
    assert "token reset: 14s" in message


def test_online_tool_subset_prioritises_agent_controls_and_relevant_health_tools() -> None:
    schemas = [
        _schema("get_sleep_sessions", "Summarize sleep sessions."),
        _schema("calculate_cardio_load", "Calculate cardiovascular training load."),
        _schema("estimate_cardio_fitness", "Estimate VO2 max."),
        _schema("search_tool_registry"),
        _schema("create_learned_tool"),
        _schema("ask_user_feedback"),
        _schema("get_metric_series"),
        _schema("unrelated_tool"),
    ]

    selected = online_tool_subset(
        schemas,
        "Confronta il mio allenamento in bicicletta con il sonno",
        maximum=7,
    )
    names = [item["function"]["name"] for item in selected]

    assert "search_tool_registry" in names
    assert "create_learned_tool" in names
    assert "ask_user_feedback" in names
    assert "get_sleep_sessions" in names
    assert "calculate_cardio_load" in names
    assert "unrelated_tool" not in names


def test_query_planner_uses_selected_online_provider(monkeypatch) -> None:
    calls: list[str] = []

    class Client:
        def _chat_stream(self, *_args, **_kwargs):
            return '{"data_types":["sleep"],"window":"last_n_days","days":7}'

    def fake_online_client(model: str, **_kwargs):
        calls.append(model)
        return Client()

    def fail_ollama(*_args, **_kwargs):
        raise AssertionError("The online planner must not instantiate Ollama")

    monkeypatch.setattr("google_health_viewer.ai_query_planner.online_client", fake_online_client)
    monkeypatch.setattr(
        "google_health_viewer.ai_query_planner.OptimizedOllamaClient", fail_ollama
    )
    catalog = {
        "datasets": [
            {
                "key": "sleep",
                "label": "Sleep",
                "category": "Sleep",
                "first_date": "2026-09-01",
                "last_date": "2026-09-14",
                "record_count": 14,
            }
        ]
    }
    completed: list[dict] = []
    thread = AIDataPlanThread(GROQ_MODEL, catalog, "Come dormo?", [], 32768)
    thread.completed.connect(completed.append)
    thread.run()

    assert calls == [GROQ_MODEL]
    assert completed[0]["data_types"] == ["sleep"]


def test_online_agent_returns_matching_tool_call_id_and_bounded_schemas(
    tmp_path, monkeypatch
) -> None:
    runtime = AgentRuntime(
        HealthStore(tmp_path / "health.sqlite3"),
        AgentStore(tmp_path / "agent.sqlite3"),
    )
    calls: list[dict] = []

    def fake_chat_once(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_7",
                        "function": {"name": "get_available_metrics", "arguments": "{}"},
                    }
                ],
            }
        return {"content": "Risposta", "tool_calls": []}

    monkeypatch.setattr(runtime, "_chat_once", fake_chat_once)
    answer = runtime.analyze(
        model=GROQ_MODEL,
        snapshot={"analysis_scope": "selected_interval"},
        question="Quali dati sono disponibili?",
        history=[],
        max_tokens=1024,
        model_context_limit=32768,
        performance_profile="fast",
        thread_id=None,
    )

    tool_message = next(item for item in calls[1]["messages"] if item["role"] == "tool")
    advertised = {
        item["function"]["name"] for item in calls[0]["tools"]
    }
    assert answer == "Risposta"
    assert tool_message["tool_call_id"] == "call_7"
    assert "tool_name" not in tool_message
    assert len(calls[0]["tools"]) <= 28
    assert "create_learned_tool" in advertised
    assert "ask_user_feedback" in advertised
