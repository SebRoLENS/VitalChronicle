from __future__ import annotations

import json

from google_health_viewer import agent_runtime_adaptive_patch as adaptive


def test_adaptive_tool_budget_grows_with_free_context():
    short_messages = [{"role": "user", "content": "confronta recupero"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "example",
                "description": "small schema",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    small, _, _ = adaptive._adaptive_char_budget(
        num_ctx=8192,
        num_predict=640,
        messages=short_messages,
        tools=tools,
    )
    large, _, _ = adaptive._adaptive_char_budget(
        num_ctx=32768,
        num_predict=1100,
        messages=short_messages,
        tools=tools,
    )
    crowded, _, _ = adaptive._adaptive_char_budget(
        num_ctx=32768,
        num_predict=1100,
        messages=[{"role": "user", "content": "x" * 60000}],
        tools=tools,
    )
    assert large > small
    assert crowded < large


def test_smart_truncation_keeps_metric_summaries_and_valid_json():
    result = {
        "status": "ok",
        "responses": {
            "daily-heart-rate-variability": {
                "baseline": 68.7,
                "mean_recovery_days": 3.11,
                "median_recovery_days": 2.0,
                "episodes": [
                    {"event_date": f"2026-08-{day:02d}", "recovery_days": day % 5 + 1}
                    for day in range(1, 25)
                ],
            },
            "daily-resting-heart-rate": {
                "baseline": 55.5,
                "mean_recovery_days": 1.0,
                "median_recovery_days": 1.0,
                "episodes": [
                    {"event_date": f"2026-08-{day:02d}", "recovery_days": 1}
                    for day in range(1, 25)
                ],
            },
        },
        "method": "deterministic comparison",
    }
    text = adaptive._smart_json_text(result, 2200)
    parsed = json.loads(text)
    assert len(text) <= 2200
    assert parsed["responses"]["daily-heart-rate-variability"]["mean_recovery_days"] == 3.11
    assert parsed["responses"]["daily-resting-heart-rate"]["mean_recovery_days"] == 1.0
    assert "_context_truncation" in parsed


def test_smart_truncation_never_returns_cut_json():
    text = adaptive._smart_json_text(
        {"points": [{"date": str(index), "value": index} for index in range(1000)]},
        900,
    )
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    assert len(text) <= 900


def test_exact_active_learned_match_is_preferred_over_generic_matches():
    capability = "analysis.composed.training_context.recovery_comparison"
    result = {
        "matches": [
            {
                "name": "generic_builtin",
                "kind": "builtin",
                "status": "active",
                "capability": capability,
                "similarity": 1.0,
            },
            {
                "name": "consecutive_vs_rest_recovery_comparison",
                "kind": "learned",
                "status": "active",
                "capability": capability,
                "similarity": 1.0,
            },
            {
                "name": "analyze_post_event_recovery",
                "kind": "builtin",
                "status": "active",
                "capability": "analysis.primitive.temporal_event_response.recovery_latency",
                "similarity": 0.82,
            },
        ]
    }
    exact = adaptive._select_exact_match(result, capability)
    assert exact is not None
    assert exact["name"] == "consecutive_vs_rest_recovery_comparison"
    assert exact["kind"] == "learned"


def test_similar_but_not_exact_capability_is_not_forced():
    result = {
        "matches": [
            {
                "name": "analyze_post_event_recovery",
                "kind": "builtin",
                "status": "active",
                "capability": "analysis.primitive.temporal_event_response.recovery_latency",
                "similarity": 0.91,
            }
        ]
    }
    assert (
        adaptive._select_exact_match(
            result, "analysis.composed.training_context.recovery_comparison"
        )
        is None
    )
