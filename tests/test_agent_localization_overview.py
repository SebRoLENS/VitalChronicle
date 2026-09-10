from __future__ import annotations

from datetime import date

from google_health_viewer.agent_localization import localize_calibration_item
from google_health_viewer.agent_overview import build_agent_overview_payload


def test_calibration_question_is_localized_to_italian() -> None:
    item = {
        "learning_key": "high_load_subjective_tolerance",
        "question": "English model wording",
        "reason": "English reason",
        "context": {"ratio": 1.42},
    }
    localized = localize_calibration_item(item, "it")
    assert localized["question"].startswith("Il tuo carico cardiovascolare")
    assert "tolleri abitualmente" in localized["reason"]


def test_sleep_calibration_keeps_personal_baseline_value() -> None:
    item = {
        "learning_key": "subjective_sleep_need_context",
        "context": {"observation": "personal median sleep was about 6.75 h"},
    }
    localized = localize_calibration_item(item, "it")
    assert "6.8 ore" in localized["question"]


def test_overview_payload_uses_deterministic_agent_tools() -> None:
    class Tools:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute(self, name: str, arguments: dict):
            self.calls.append((name, arguments))
            values = {
                "calculate_readiness": {"score": 81.0, "label": "high", "confidence": 0.9},
                "calculate_resilience": {"score": 73.0, "label": "balanced"},
                "calculate_training_status": {"status": "maintaining", "load": {"acute_chronic_ratio": 1.03}},
                "calculate_target_load": {"current_acute_load": 105.0, "target_weekly_load": {"lower": 90.0, "upper": 120.0}},
            }
            return values[name]

    class Runtime:
        def __init__(self) -> None:
            self.tools = Tools()

    runtime = Runtime()
    payload = build_agent_overview_payload(runtime, date(2026, 9, 10))
    assert payload["readiness"]["score"] == 81.0
    assert payload["training"]["status"] == "maintaining"
    assert [name for name, _args in runtime.tools.calls] == [
        "calculate_readiness",
        "calculate_resilience",
        "calculate_training_status",
        "calculate_target_load",
    ]
    assert all(args["end"] == "2026-09-10" for _name, args in runtime.tools.calls)
