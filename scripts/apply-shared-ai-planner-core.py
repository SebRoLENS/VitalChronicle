#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

MODULE = Path("google_health_viewer/ai_query_planner.py")
CORE = Path("google_health_viewer/ai_query_planner_core.py")
TEST = Path("tests/test_ai_query_planner_core.py")
SELF = Path("scripts/apply-shared-ai-planner-core.py")
WORKFLOW = Path(".github/workflows/apply-shared-ai-planner-core.yml")

text = MODULE.read_text(encoding="utf-8")
start = text.index('PLANNER_VERSION = "ai-data-planner-v1"')
end = text.index("class AIDataPlanThread")
pure_block = text[start:end].rstrip() + "\n"

core_header = '''"""Platform-neutral AI-first health-data query planner.

This module contains the reusable planner contract shared by VitalChronicle desktop
and Android. It deliberately has no Qt, Android, Ollama, ML Kit or UI dependency.
The language model only receives metadata; Python validates its JSON plan and then
reads only the selected local datasets and interval.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from .ai_insights import build_ai_ready_snapshot
from .constants import DATA_TYPE_BY_KEY

'''
CORE.write_text(core_header + pure_block, encoding="utf-8")

shared_import = '''from .ai_query_planner_core import (
    MAX_LOOKBACK_DAYS,
    MAX_SELECTED_DATA_TYPES,
    PLANNER_OUTPUT_TOKENS,
    PLANNER_SYSTEM_PROMPT,
    PLANNER_VERSION,
    SelectedHealthStore,
    _catalog_rows,
    _fallback_types,
    _history_excerpt,
    _parse_day,
    _parse_json_object,
    _planner_messages,
    build_data_catalog,
    build_planned_snapshot,
    fallback_data_plan,
    resolve_data_plan,
)

'''
MODULE.write_text(text[:start] + shared_import + text[end:], encoding="utf-8")

TEST.write_text('''from __future__ import annotations

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
    raw = _parse_json_object('```json\\n{"data_types":["sleep"],"window":"last_n_days","days":7}\\n```')
    assert raw["days"] == 7
    fallback = fallback_data_plan(_catalog())
    assert 1 <= fallback["days"] <= 90
    assert set(fallback["data_types"]).issubset({"sleep", "daily-heart-rate-variability", "steps"})
''', encoding="utf-8")

for path in (SELF, WORKFLOW):
    if path.exists():
        path.unlink()

print("Shared platform-neutral AI planner core extracted and desktop rewired.")
