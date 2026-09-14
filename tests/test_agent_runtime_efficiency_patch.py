from __future__ import annotations

from google_health_viewer import agent_runtime_efficiency_patch as efficiency


def test_personal_median_threshold_is_a_factory_candidate():
    hint = {
        "consider_reusable_tool": False,
        "signals": ["threshold/frequency analysis"],
        "instruction": "Prefer existing exact tools.",
    }
    result = efficiency._supplement_factory_hint(
        "Nei giorni in cui i minuti attivi superano del 30% la mia mediana personale, cosa succede?",
        hint,
    )
    assert result["consider_reusable_tool"] is True
    assert "relative personal-baseline threshold" in result["signals"]
    assert "threshold/frequency analysis" in result["signals"]


def test_composite_tool_is_required_for_threshold_and_recovery_questions():
    threshold = efficiency._required_composite_tools(
        "Quando i minuti attivi superano del 30% la mia mediana personale, confronta HRV e FCR"
    )
    recovery = efficiency._required_composite_tools(
        "Dopo allenamenti intensi quanto tempo impiegano HRV e FCR a tornare ai valori abituali?"
    )
    assert "analyze_metric_threshold_responses" in threshold
    assert "analyze_post_event_recovery" in recovery


def test_agent_output_budget_is_hard_capped_by_context_and_phase():
    assert efficiency._agent_predict_cap(8192, True) == 640
    assert efficiency._agent_predict_cap(16384, True) == 900
    assert efficiency._agent_predict_cap(32768, True) == 1100
    assert efficiency._agent_predict_cap(8192, False) == 1600
    assert efficiency._agent_predict_cap(16384, False) == 2200
    assert efficiency._agent_predict_cap(32768, False) == 2800


def test_snapshot_seed_reuses_precomputed_deterministic_metric_facts():
    snapshot = {
        "analysis_brief": {"available_metric_count": 3},
        "metrics": [
            {
                "data_type": "active-minutes",
                "label": "Minuti attivi",
                "unit": "min",
                "summary": {"count": 20, "median": 265.0, "mean": 281.5},
                "derived_evidence": {
                    "personal_baselines": {
                        "28_days": {"observed_days": 20, "median": 265.0, "mean": 281.5}
                    },
                    "data_quality": {"observed_days": 20, "coverage_percent": 100.0},
                },
            },
            {
                "data_type": "daily-heart-rate-variability",
                "label": "Variabilità cardiaca giornaliera",
                "unit": "ms",
                "summary": {"count": 19, "median": 68.7},
                "derived_evidence": {
                    "data_quality": {"observed_days": 19, "coverage_percent": 95.0}
                },
            },
        ],
        "candidate_insights": [
            {"evidence_id": "quality:hrv", "kind": "data_quality_limit"}
        ],
    }
    seed = efficiency._compact_snapshot_seed(snapshot)
    assert seed["analysis_brief"]["available_metric_count"] == 3
    assert seed["prepared_metrics"][0]["data_type"] == "active-minutes"
    assert seed["prepared_metrics"][0]["summary"]["median"] == 265.0
    assert (
        seed["prepared_metrics"][0]["derived_evidence"]["personal_baselines"]["28_days"]["median"]
        == 265.0
    )
    assert seed["prepared_metrics"][1]["data_type"] == "daily-heart-rate-variability"
