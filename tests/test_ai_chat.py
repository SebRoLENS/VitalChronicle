from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from google_health_viewer.ai_chat import AIChatWindow
from google_health_viewer.ai_conversations import ConversationStore
from google_health_viewer.i18n import set_language


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path):
    _application()
    set_language("en")
    revision = {"value": "2026-08-31T10:00:00+00:00"}
    conversations = ConversationStore(tmp_path / "chats.json")
    snapshot = {
        "observation_context": {"observed_at": "2026-08-31T10:00:00+00:00"},
        "requested_interval_coverage": {
            "scope_is_partially_observed": True,
            "coverage_notice": "Only seven of the requested thirty-one days are observed.",
        },
        "metrics": [{"data_type": "steps"}],
        "candidate_insights": [
            {
                "evidence_id": "change:steps",
                "headline": "Steps: sustained matched-period change",
                "confidence": "moderate",
                "relevance_score": 78,
                "caveat": "Higher is not automatically better.",
            }
        ],
    }
    thread = conversations.create_thread(
        title="Activity review",
        model="qwen3.5:9b",
        scope="selected",
        period={"label": "Last month", "start": "2026-08-01", "end": "2026-09-01"},
        snapshot=snapshot,
        snapshot_revision=revision["value"],
    )
    window = AIChatWindow(
        conversations=conversations,
        snapshot_builder=lambda _scope, period: (snapshot, period or {}),
        period_provider=lambda: thread["period"],
        revision_provider=lambda: revision["value"],
        model_provider=lambda: "qwen3.5:9b",
        tokens_provider=lambda: 4096,
        context_limit_provider=lambda: 32768,
    )
    return window, thread, revision


def test_chat_window_lists_threads_evidence_and_new_data_badge(tmp_path: Path):
    window, thread, revision = _window(tmp_path)
    window.open_thread(thread["id"])

    assert window.thread_list.count() == 1
    assert window.period_badge.text() == "Last month"
    assert window.evidence_tree.topLevelItemCount() == 1
    assert window.coverage_label.isVisible()
    assert "seven" in window.coverage_label.text()
    assert not window.refresh_data_button.isVisible()

    revision["value"] = "2026-08-31T12:00:00+00:00"
    window.notify_data_revision_changed()

    assert window.refresh_data_button.isVisible()
    assert "newer local data" in window.snapshot_label.text()
    window.close()


def test_thinking_and_answer_use_the_same_live_assistant_area(tmp_path: Path):
    window, thread, _revision = _window(tmp_path)
    window.open_thread(thread["id"])
    window._live_thinking = "Checking longitudinal evidence"
    window._render_transcript()

    assert "Checking longitudinal evidence" in window.transcript.toPlainText()

    window._answer_chunk("Final synthesis")
    window._render_transcript()

    rendered = window.transcript.toPlainText()
    assert "Checking longitudinal evidence" not in rendered
    assert "Final synthesis" in rendered
    window.close()


def test_exact_prompt_can_be_opened_in_the_chat_window(tmp_path: Path):
    window, thread, _revision = _window(tmp_path)
    window.open_thread(thread["id"])

    window._prompt_ready("# Final synthesis request\n\n## 1. SYSTEM\n\nVisible instructions")
    assert window.prompt_button.isEnabled()

    window.prompt_button.setChecked(True)
    assert window.prompt_view.isVisible()
    assert "Visible instructions" in window.prompt_view.toPlainText()
    window.close()
