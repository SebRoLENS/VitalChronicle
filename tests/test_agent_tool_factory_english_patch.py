from __future__ import annotations

from google_health_viewer import agent_tool_factory_english_patch as english


def test_clear_italian_learned_tool_metadata_is_rejected():
    errors = english._metadata_language_errors(
        {
            "name": "analisi_recupero_allenamento_consecutivo",
            "description": "Confronta il recupero dopo allenamenti consecutivi e sonno ridotto.",
            "capability": "analysis.composed.recupero_personale",
        }
    )

    assert errors
    assert any("name must be written in English" in item for item in errors)
    assert any("description must be written in English" in item for item in errors)
    assert any("capability must be written in English" in item for item in errors)


def test_english_learned_tool_metadata_is_accepted():
    errors = english._metadata_language_errors(
        {
            "name": "compare_consecutive_training_recovery_by_following_sleep",
            "description": (
                "Compare recovery after the second consecutive training day across following-night "
                "sleep cohorts relative to a personal sleep baseline."
            ),
            "capability": "analysis.composed.training_context.sleep_conditioned_return_comparison",
        }
    )

    assert errors == []


def test_known_compiled_capability_gets_canonical_english_name():
    prepared = english._canonicalize_known_english_metadata(
        {
            "name": "analisi_recupero_allenamento_consecutivo",
            "description": "Compare recovery after consecutive training by sleep cohort.",
            "capability": "analysis.composed.training_context.sleep_conditioned_return_comparison",
        }
    )

    assert prepared["name"] == "compare_consecutive_training_recovery_by_following_sleep"
    assert english._metadata_language_errors(prepared) == []
