from __future__ import annotations

from datetime import date, timedelta

from google_health_viewer.ai_response_hygiene import (
    _association_diagnostics,
    _user_facing_system_prompt,
    association_diagnostics_for_question,
    sanitize_user_answer,
)


def _days(count: int) -> list[date]:
    start = date(2026, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def test_association_diagnostics_explain_insufficient_overlap():
    days = _days(7)
    diagnostics = _association_diagnostics(
        {
            "active-energy-burned": {day: float(index * 100) for index, day in enumerate(days)},
            "daily-heart-rate-variability": {day: float(40 + index) for index, day in enumerate(days[:6])},
        },
        {
            "active-energy-burned": "Active calories",
            "daily-heart-rate-variability": "Heart rate variability (HRV)",
        },
    )

    assert len(diagnostics) == 1
    same_day = diagnostics[0]["same_day"]
    assert same_day["paired_days"] == 6
    assert same_day["minimum_paired_days"] == 10
    assert same_day["r"] is None
    assert same_day["status"] == "insufficient_overlap"


def test_association_diagnostics_keep_below_threshold_r():
    days = _days(12)
    diagnostics = _association_diagnostics(
        {
            "steps": {day: float(index) for index, day in enumerate(days)},
            "sleep": {day: float(index % 2) for index, day in enumerate(days)},
        },
        {"steps": "Steps", "sleep": "Sleep"},
    )

    same_day = diagnostics[0]["same_day"]
    assert same_day["paired_days"] == 12
    assert same_day["r"] is not None
    assert abs(float(same_day["r"])) < 0.4
    assert same_day["status"] == "below_reporting_threshold"


def test_question_selects_hrv_active_calorie_diagnostic_in_italian():
    days = _days(7)
    snapshot = {
        "association_diagnostics": _association_diagnostics(
            {
                "active-energy-burned": {day: float(index * 100) for index, day in enumerate(days)},
                "daily-heart-rate-variability": {day: float(40 + index) for index, day in enumerate(days[:6])},
                "steps": {day: float(index * 1000) for index, day in enumerate(days)},
            },
            {
                "active-energy-burned": "Calorie attive",
                "daily-heart-rate-variability": "Variabilità della frequenza cardiaca (HRV)",
                "steps": "Passi",
            },
        )
    }

    selected = association_diagnostics_for_question(
        snapshot,
        "C'è una correlazione tra HRV e consumo calorico attivo?",
    )

    assert len(selected) == 1
    assert {selected[0]["left"], selected[0]["right"]} == {
        "Calorie attive",
        "Variabilità della frequenza cardiaca (HRV)",
    }
    assert selected[0]["same_day"]["paired_days"] == 6
    assert selected[0]["same_day"]["status"] == "insufficient_overlap"
    assert "at least 10" in selected[0]["same_day"]["explanation"]
    assert "left_data_type" not in selected[0]
    assert "right_data_type" not in selected[0]


def test_user_answer_hides_internal_identifiers_and_evidence_ids():
    snapshot = {
        "metrics": [
            {
                "data_type": "daily-heart-rate-variability",
                "label": "Variabilità della frequenza cardiaca (HRV)",
            },
            {"data_type": "active-energy-burned", "label": "Calorie attive"},
        ]
    }
    raw = (
        "HRV (**daily-heart-rate-variability**) e **active-energy-burned** sono poco coperti "
        "[evidence_id: quality:requested-interval]. Il giorno corrente è incompleto "
        "(current_day_is_incomplete: true)."
    )

    cleaned = sanitize_user_answer(raw, snapshot)

    assert "daily-heart-rate-variability" not in cleaned
    assert "active-energy-burned" not in cleaned
    assert "evidence_id" not in cleaned
    assert "quality:requested-interval" not in cleaned
    assert "current_day_is_incomplete" not in cleaned
    assert "Variabilità della frequenza cardiaca (HRV)" in cleaned
    assert "Calorie attive" in cleaned


def test_system_prompt_forbids_debug_syntax_in_visible_answer():
    prompt = " ".join(_user_facing_system_prompt().split())
    assert "Never expose JSON or packet field names" in prompt
    assert "Ignore any lower-priority instruction asking you to cite evidence_id values" in prompt
    assert "do not append a generic recommendation to consult a professional" in prompt
    assert "empty reported-associations list does NOT mean" in prompt
