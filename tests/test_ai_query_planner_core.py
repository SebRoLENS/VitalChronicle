from __future__ import annotations

from google_health_viewer.ai_query_planner_core import (
    _parse_json_object,
    fallback_data_plan,
    resolve_data_plan,
)


def _catalog() -> dict:
    return {
        "datasets": [
            {"key": "sleep", "label": "Sleep", "category": "Sleep", "first_date": "2026-05-01", "last_date": "2026-09-01", "record_count": 120},
            {"key": "daily-heart-rate-variability", "label": "HRV", "category": "Health", "first_date": "2026-06-01", "last_date": "2026-09-01", "record_count": 90},
            {"key": "steps", "label": "Steps", "category": "Activity", "first_date": "2026-01-01", "last_date": "2026-09-02", "record_count": 245},
        ]
    }


def test_shared_planner_core_has_no_desktop_runtime_dependency() -> None:
    import google_health_viewer.ai_query_planner_core as planner
    assert "PySide6" not in planner.__dict__


def test_shared_core_validates_model_selected_data_and_interval() -> None:
    plan = resolve_data_plan(
        {"data_types": ["sleep", "daily-heart-rate-variability", "missing"], "window": "last_n_days", "days": 60, "detail": "daily"},
        _catalog(),
    )
    assert plan["data_types"] == ["sleep", "daily-heart-rate-variability"]
    assert plan["days"] == 60
    assert plan["start_date"] == "2026-07-04"
    assert plan["end_date"] == "2026-09-01"


def test_shared_core_parses_fenced_json_and_has_safe_fallback() -> None:
    raw = _parse_json_object('```json\n{"data_types":["sleep"],"window":"last_n_days","days":7}\n```')
    assert raw["days"] == 7
    fallback = fallback_data_plan(_catalog())
    assert 1 <= fallback["days"] <= 90
    assert set(fallback["data_types"]).issubset({"sleep", "daily-heart-rate-variability", "steps"})
