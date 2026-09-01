import json

import pytest

from google_health_viewer.ai_engine import (
    TOKEN_USAGE_PREFIX,
    OptimizedOllamaClient,
    model_suitable_for_deep_analysis,
    recommended_generation_budget,
)
from google_health_viewer.local_ai import LocalAIError


def _snapshot():
    return {
        "analysis_scope": "all_local_history",
        "period": {"start": "2026-01-01", "end": "2026-09-01"},
        "observation_context": {"observed_at": "2026-09-01T10:00:00+02:00"},
        "metrics": [
            {
                "data_type": "steps",
                "label": "Steps",
                "summary": {"count": 200, "latest": 8000, "mean": 7200},
                "derived_evidence": {
                    "matched_recent_comparison": {
                        "recent_days": 7,
                        "recent_mean": 8000,
                        "previous_days": 7,
                        "previous_mean": 7000,
                        "percent_change": 14.3,
                        "standardized_change": 0.8,
                    }
                },
                "structured_details": {"raw_like_noise": "x" * 30000},
            },
            {
                "data_type": "sleep",
                "label": "Sleep",
                "summary": {"count": 180, "latest": 7.2, "mean": 7.0},
                "derived_evidence": {
                    "trend": {
                        "window_days": 28,
                        "observed_days": 25,
                        "direction": "stable",
                        "percent_per_week": 0.5,
                        "r_squared": 0.1,
                    }
                },
            },
        ],
        "requested_interval_coverage": {
            "requested_start": "2026-01-01",
            "requested_end": "2026-08-31",
            "requested_calendar_days": 243,
            "first_measurement_date": "2026-01-01",
            "last_measurement_date": "2026-08-31",
            "calendar_days_with_measurements": 230,
            "scope_is_partially_observed": True,
            "measurement_missing_date_ranges": [
                {"start": "2026-02-01", "end": "2026-02-01"}
            ] * 500,
            "metrics": [],
        },
        "candidate_insights": [
            {
                "evidence_id": "change:steps",
                "kind": "matched_period_change",
                "data_types": ["steps"],
                "headline": "Steps increased",
                "relevance_score": 88,
                "confidence": "moderate",
                "evidence": {"percent_change": 14.3},
            }
        ],
        "associations": [],
        "data_coverage": {"records_considered": {"steps": 500000, "sleep": 3000}},
    }


class FakeResponse:
    status_code = 200
    reason = "OK"

    def __init__(self, content, *, eval_count=120, prompt_eval_count=1800):
        self.content = content
        self.eval_count = eval_count
        self.prompt_eval_count = prompt_eval_count

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        assert decode_unicode
        chunks = [
            {"message": {"thinking": "Checking evidence. "}},
            {
                "message": {"content": self.content},
                "done": True,
                "prompt_eval_count": self.prompt_eval_count,
                "eval_count": self.eval_count,
                "eval_duration": 2_000_000_000,
            },
        ]
        return [json.dumps(chunk) for chunk in chunks]


def test_profile_generation_budgets_are_recommendations_not_hard_limits():
    assert recommended_generation_budget("fast") == 768
    assert recommended_generation_budget("standard") == 1600
    assert recommended_generation_budget("max") == 3000
    assert model_suitable_for_deep_analysis("qwen3:4b") is False
    assert model_suitable_for_deep_analysis("qwen3:8b") is True


def test_standard_deep_analysis_uses_one_compact_call_and_small_context(monkeypatch):
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return FakeResponse("Compact answer [change:steps].")

    monkeypatch.setattr("google_health_viewer.ai_engine.requests.post", fake_post)
    prompts = []
    answer = OptimizedOllamaClient(
        model="qwen3:8b", performance_profile="standard"
    ).analyze_stream(
        _snapshot(),
        analysis_mode="deep",
        max_tokens=1600,
        model_context_limit=32768,
        prompt_callback=prompts.append,
    )

    assert answer == "Compact answer [change:steps]."
    assert len(calls) == 1
    request = calls[0]["json"]
    assert request["options"]["num_ctx"] < 16384
    assert request["options"]["num_predict"] == 1600
    content = request["messages"][-1]["content"]
    assert "BEGIN_HEALTH_EVIDENCE_JSON" in content
    assert "measurement_missing_date_ranges" not in content
    assert "x" * 1000 not in content
    assert any("# Pipeline diagnostics" in prompt for prompt in prompts)
    diagnostics = prompts[-1]
    assert "Model calls: 1" in diagnostics
    assert "tok/s" in diagnostics


def test_live_token_telemetry_moves_from_estimate_to_exact_counts(monkeypatch):
    def fake_post(_url, **_kwargs):
        return FakeResponse("Streaming answer [change:steps].")

    monkeypatch.setattr("google_health_viewer.ai_engine.requests.post", fake_post)
    events = []
    answer_chunks = []
    answer = OptimizedOllamaClient(
        model="qwen3:8b", performance_profile="standard"
    ).analyze_stream(
        _snapshot(),
        analysis_mode="deep",
        max_tokens=1600,
        model_context_limit=32768,
        prompt_callback=events.append,
        answer_callback=answer_chunks.append,
    )

    telemetry = [
        json.loads(item[len(TOKEN_USAGE_PREFIX) :])
        for item in events
        if item.startswith(TOKEN_USAGE_PREFIX)
    ]
    assert answer == "Streaming answer [change:steps]."
    assert answer_chunks == ["Streaming answer [change:steps]."]
    assert len(telemetry) >= 2
    first = telemetry[0]
    assert first["exact"] is False
    assert first["input_tokens"] > 0
    assert first["generated_tokens"] == 0
    assert first["context_remaining"] == first["context"] - first["input_tokens"]

    final = telemetry[-1]
    assert final["exact"] is True
    assert final["input_tokens"] == 1800
    assert final["generated_tokens"] == 120
    assert final["output_budget"] == 1600
    assert final["output_remaining"] == 1480
    assert final["context_used"] == 1920
    assert final["context_remaining"] == final["context"] - 1920
    assert final["tokens_per_second"] == 60.0


def test_max_deep_analysis_uses_two_compact_calls(monkeypatch):
    calls = []
    responses = iter(
        [
            FakeResponse("Plan: cite change:steps.", eval_count=60),
            FakeResponse("Maximum answer [change:steps].", eval_count=180),
        ]
    )

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr("google_health_viewer.ai_engine.requests.post", fake_post)
    prompts = []
    answer = OptimizedOllamaClient(
        model="qwen3.5:9b", performance_profile="max"
    ).analyze_stream(
        _snapshot(),
        analysis_mode="deep",
        max_tokens=3000,
        model_context_limit=32768,
        prompt_callback=prompts.append,
    )

    assert answer == "Maximum answer [change:steps]."
    assert len(calls) == 2
    assert "Maximum-quality evidence plan" in calls[1]["json"]["messages"][-1]["content"]
    assert "Model calls: 2" in prompts[-1]


def test_4b_is_rejected_for_complete_history_before_ollama_call(monkeypatch):
    called = False

    def fake_post(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Ollama should not be called")

    monkeypatch.setattr("google_health_viewer.ai_engine.requests.post", fake_post)
    with pytest.raises(LocalAIError, match="4B models"):
        OptimizedOllamaClient(model="qwen3:4b").analyze_stream(
            _snapshot(), analysis_mode="deep"
        )
    assert called is False


def test_manual_large_output_budget_is_preserved_when_context_allows(monkeypatch):
    calls = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return FakeResponse("Long-budget answer.")

    monkeypatch.setattr("google_health_viewer.ai_engine.requests.post", fake_post)
    OptimizedOllamaClient(model="qwen3:8b").analyze_stream(
        _snapshot(),
        max_tokens=5000,
        model_context_limit=32768,
    )

    assert calls[0]["json"]["options"]["num_predict"] == 5000
