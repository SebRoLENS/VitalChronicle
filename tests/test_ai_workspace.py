from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from google_health_viewer.app import _configure_period_visibility
from google_health_viewer.local_ai import SYSTEM_PROMPT
from google_health_viewer.main_window import MainWindow
from google_health_viewer.storage import HealthStore
from google_health_viewer.updates import ReleaseInfo


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ai_settings_metrics_and_prompt_share_one_main_tab(tmp_path: Path):
    _application()
    window = MainWindow(store=HealthStore(tmp_path / "health.sqlite3"), screenshot_mode=True)

    assert window.tabs.count() == 3
    assert window.ai_sections.count() == 4
    assert window.ai_sections.isAncestorOf(window.ai_ram_edit)
    assert window.ai_sections.isAncestorOf(window.ai_metrics_tree)
    assert SYSTEM_PROMPT in window.ai_system_prompt_view.toPlainText()
    assert "Respond to the user in English" in window.ai_system_prompt_view.toPlainText()
    assert window.ai_question_period_label.text() == "Question period"
    assert window.ai_deep_analysis_button.text() == "Analyse all data"
    assert window.version_badge.text().startswith("v")
    assert window.version_badge.objectName() == "versionBadge"
    assert window._app_update_timer.interval() == 60 * 60 * 1000
    window._set_version_badge(ReleaseInfo("9.9.9", "https://example.test/release"))
    assert "9.9.9" in window.version_badge.text()
    assert window.version_badge.objectName() == "versionBadgeUpdate"
    window.close()


def test_dashboard_period_controls_are_hidden_only_in_ai_tab(tmp_path: Path):
    app = _application()
    window = MainWindow(store=HealthStore(tmp_path / "health.sqlite3"), screenshot_mode=True)
    _configure_period_visibility(window)

    window.tabs.setCurrentIndex(0)
    app.processEvents()
    assert not window.range_combo.isHidden()
    assert not window.start_date.isHidden()
    assert not window.end_date.isHidden()

    window.tabs.setCurrentIndex(2)
    app.processEvents()
    assert window.range_combo.isHidden()
    assert window.start_date.isHidden()
    assert window.end_date.isHidden()

    window.tabs.setCurrentIndex(1)
    app.processEvents()
    assert not window.range_combo.isHidden()
    assert not window.start_date.isHidden()
    assert not window.end_date.isHidden()
    window.close()


def test_deterministic_inspector_shows_partial_interval_and_values(tmp_path: Path):
    _application()
    window = MainWindow(store=HealthStore(tmp_path / "health.sqlite3"), screenshot_mode=True)
    snapshot = {
        "requested_interval_coverage": {
            "requested_calendar_days": 31,
            "calendar_days_with_any_data": 7,
            "first_observed_date": "2026-08-25",
            "last_observed_date": "2026-08-31",
            "scope_is_partially_observed": True,
            "coverage_notice": "Only one week is observed.",
            "metrics": [
                {
                    "data_type": "steps",
                    "observed_calendar_days": 7,
                    "coverage_percent": 22.6,
                }
            ],
        },
        "metrics": [
            {
                "data_type": "steps",
                "label": "Steps",
                "records_considered": 7,
                "derived_evidence": {
                    "personal_baselines": {
                        "7_days": {"mean": 6500, "standard_deviation": 320}
                    },
                    "trend": {"direction": "stable"},
                },
            }
        ],
        "candidate_insights": [
            {
                "evidence_id": "quality:requested-interval",
                "headline": "The requested interval is partially represented",
                "confidence": "high",
                "relevance_score": 100,
            }
        ],
        "associations": [],
    }

    window._populate_deterministic_snapshot(snapshot, {"label": "Last month"})

    assert window.ai_metrics_tree.topLevelItemCount() == 4
    assert "7" in window.ai_metrics_coverage.text()
    assert "Only one week" in window.ai_metrics_coverage.text()
    assert "31" in window.ai_metrics_details.toPlainText()
    window.close()