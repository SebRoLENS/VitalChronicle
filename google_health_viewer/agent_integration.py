from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .agent_runtime import AGENT_TRACE_PREFIX
from .agent_runtime_v2 import AgentAnalysisThread, AgentRuntime
from .agent_ui import build_personal_ai_page, refresh_personal_ai_page
from .ai_hardware import reasoning_value
from .i18n import _


def _install_agent_reasoning_compatibility() -> None:
    """Use the same Ollama reasoning semantics as the existing desktop AI path."""

    if getattr(AgentRuntime, "_reasoning_compatibility_installed", False):
        return
    original_chat_once = AgentRuntime._chat_once

    def chat_once(self, **kwargs):
        think = kwargs.get("think")
        model = str(kwargs.get("model") or "")
        if isinstance(think, bool):
            profile = str(QSettings().value("ai/performance_profile", "standard") or "standard")
            # GPT-OSS requires low/medium/high instead of a boolean. A deliberate
            # think=False pass maps to the fast/low level; boolean-thinking models
            # keep the original True/False value.
            resolved = reasoning_value(model, profile if think else "fast")
            if isinstance(resolved, str):
                kwargs["think"] = resolved
        return original_chat_once(self, **kwargs)

    AgentRuntime._chat_once = chat_once
    AgentRuntime._reasoning_compatibility_installed = True


def _install_chat_integration(ai_chat_module) -> None:
    AIChatWindow = ai_chat_module.AIChatWindow
    if getattr(AIChatWindow, "_personal_agent_integration_installed", False):
        return

    original_build_ui = AIChatWindow._build_ui
    original_load_thread = AIChatWindow._load_current_thread
    original_start_request = AIChatWindow._start_request
    original_analysis_completed = AIChatWindow._analysis_completed
    original_prompt_ready = AIChatWindow._prompt_ready

    def render_activity_log(self) -> None:
        lines = [f"• {message}" for message in self._activity_events]
        exchange = list(getattr(self, "_agent_exchange_events", []))
        if exchange:
            lines.extend(["", _("Agent exchange")])
            lines.extend(exchange)
        if self._token_usage_text:
            lines.extend(["", self._token_usage_text])
        text = "\n".join(lines)
        if isinstance(self.activity_log, QPlainTextEdit):
            scrollbar = self.activity_log.verticalScrollBar()
            at_bottom = scrollbar.value() >= max(0, scrollbar.maximum() - 2)
            self.activity_log.setPlainText(text)
            if at_bottom or self._activity_active:
                scrollbar.setValue(scrollbar.maximum())
        else:
            self.activity_log.setText(text)

    def finish_activity(self) -> None:
        self._activity_active = False
        self._activity_timer.stop()
        self._activity_phase = ""
        self.activity_progress.setRange(0, 1000)
        self.activity_progress.setValue(1000)
        self.activity_progress.setTextVisible(False)
        if self._activity_events:
            self.activity_title.setText(_("Agent activity · completed"))
            self.activity_elapsed.clear()
            self._render_activity_log()
            self.activity_panel.setVisible(True)
        elif self._token_usage_text:
            self.activity_title.setText("AI · token usage")
            self.activity_elapsed.clear()
            self._render_activity_log()
            self.activity_panel.setVisible(True)
        else:
            self.activity_panel.setVisible(False)

    def build_ui(self) -> None:
        original_build_ui(self)
        central = self.centralWidget()
        root = central.layout() if central is not None else None
        splitter = root.itemAt(0).widget() if root is not None and root.count() else None
        conversation = splitter.widget(1) if splitter is not None and splitter.count() > 1 else None
        layout = conversation.layout() if conversation is not None else None
        if layout is None:
            return

        # The base chat used a QLabel showing only the last four activity lines. Replace it with
        # a scrollable, selectable log that keeps the complete current run and remains visible
        # after completion. This is an operational trace only, not model chain-of-thought.
        activity_layout = self.activity_panel.layout()
        old_activity_log = self.activity_log
        activity_index = activity_layout.indexOf(old_activity_log)
        self.activity_log = QPlainTextEdit()
        self.activity_log.setObjectName("activityLog")
        self.activity_log.setReadOnly(True)
        self.activity_log.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.activity_log.setMinimumHeight(90)
        self.activity_log.setMaximumHeight(190)
        self.activity_log.setPlaceholderText(_("Operational agent activity will appear here."))
        activity_layout.insertWidget(max(0, activity_index), self.activity_log)
        old_activity_log.setParent(None)
        old_activity_log.deleteLater()

        activity_actions = QHBoxLayout()
        activity_actions.addStretch()
        self.activity_copy_button = QPushButton(_("Copy activity"))
        self.activity_save_button = QPushButton(_("Save activity…"))
        activity_actions.addWidget(self.activity_copy_button)
        activity_actions.addWidget(self.activity_save_button)
        activity_layout.insertLayout(max(0, activity_index) + 1, activity_actions)

        def copy_activity() -> None:
            QApplication.clipboard().setText(self.activity_log.toPlainText())

        def save_activity() -> None:
            filename, _filter = QFileDialog.getSaveFileName(
                self,
                _("Save agent activity"),
                "vitalchronicle-agent-activity.txt",
                _("Text files (*.txt);;All files (*)"),
            )
            if filename:
                Path(filename).write_text(self.activity_log.toPlainText(), encoding="utf-8")

        self.activity_copy_button.clicked.connect(copy_activity)
        self.activity_save_button.clicked.connect(save_activity)
        self._agent_exchange_events = []

        self.agent_feedback_panel = QFrame()
        self.agent_feedback_panel.setObjectName("agentFeedbackPanel")
        panel_layout = QVBoxLayout(self.agent_feedback_panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(7)

        feedback_header = QHBoxLayout()
        self.agent_feedback_badge = QLabel(_("ACTION REQUIRED"))
        self.agent_feedback_badge.setObjectName("agentFeedbackBadge")
        feedback_header.addWidget(self.agent_feedback_badge)
        self.agent_feedback_title = QLabel(_("Personal context question"))
        self.agent_feedback_title.setObjectName("agentFeedbackTitle")
        feedback_header.addWidget(self.agent_feedback_title, 1)
        panel_layout.addLayout(feedback_header)

        self.agent_feedback_question_heading = QLabel(_("Question"))
        self.agent_feedback_question_heading.setObjectName("agentFeedbackQuestionHeading")
        panel_layout.addWidget(self.agent_feedback_question_heading)
        self.agent_feedback_question = QLabel()
        self.agent_feedback_question.setObjectName("agentFeedbackQuestion")
        self.agent_feedback_question.setWordWrap(True)
        panel_layout.addWidget(self.agent_feedback_question)

        self.agent_feedback_reason = QLabel()
        self.agent_feedback_reason.setObjectName("agentFeedbackReason")
        self.agent_feedback_reason.setWordWrap(True)
        panel_layout.addWidget(self.agent_feedback_reason)

        self.agent_feedback_instruction = QLabel(
            _("Write your answer in the field below. This question is separate from the analysis above.")
        )
        self.agent_feedback_instruction.setObjectName("agentFeedbackInstruction")
        self.agent_feedback_instruction.setWordWrap(True)
        panel_layout.addWidget(self.agent_feedback_instruction)

        self.agent_feedback_answer = QPlainTextEdit()
        self.agent_feedback_answer.setObjectName("agentFeedbackAnswer")
        self.agent_feedback_answer.setPlaceholderText(_("Your answer"))
        self.agent_feedback_answer.setMinimumHeight(62)
        self.agent_feedback_answer.setMaximumHeight(110)
        panel_layout.addWidget(self.agent_feedback_answer)

        answer_row = QHBoxLayout()
        answer_row.addStretch()
        self.agent_feedback_save = QPushButton(_("Save answer"))
        self.agent_feedback_save.setObjectName("primaryButton")
        answer_row.addWidget(self.agent_feedback_save)
        self.agent_feedback_skip = QPushButton(_("Skip question"))
        answer_row.addWidget(self.agent_feedback_skip)
        panel_layout.addLayout(answer_row)
        self.agent_feedback_panel.setVisible(False)
        self._agent_pending_feedback_id = None

        coverage_index = layout.indexOf(self.coverage_label)
        insert_at = coverage_index + 1 if coverage_index >= 0 else 0
        layout.insertWidget(insert_at, self.agent_feedback_panel)

        def save_feedback() -> None:
            runtime = getattr(self, "agent_runtime", None)
            feedback_id = getattr(self, "_agent_pending_feedback_id", None)
            answer = self.agent_feedback_answer.toPlainText().strip()
            if runtime is None or not feedback_id or not answer:
                return
            runtime.agent_store.answer_feedback(str(feedback_id), answer)
            if self.current_thread_id:
                self.conversations.add_message(
                    self.current_thread_id,
                    "event",
                    _("Personalisation feedback saved locally."),
                )
            self.agent_feedback_answer.clear()
            self._refresh_agent_feedback()
            self._render_transcript()
            host = getattr(self, "agent_host_window", None)
            if host is not None:
                refresh_personal_ai_page(host)

        def skip_feedback() -> None:
            runtime = getattr(self, "agent_runtime", None)
            feedback_id = getattr(self, "_agent_pending_feedback_id", None)
            if runtime is None or not feedback_id:
                return
            runtime.agent_store.dismiss_feedback(str(feedback_id))
            self.agent_feedback_answer.clear()
            self._refresh_agent_feedback()

        self.agent_feedback_save.clicked.connect(save_feedback)
        self.agent_feedback_skip.clicked.connect(skip_feedback)

    def refresh_agent_feedback(self) -> None:
        panel = getattr(self, "agent_feedback_panel", None)
        runtime = getattr(self, "agent_runtime", None)
        if panel is None or runtime is None or not runtime.enabled or not self.current_thread_id:
            if panel is not None:
                panel.setVisible(False)
            self._agent_pending_feedback_id = None
            return
        item = runtime.agent_store.pending_feedback(self.current_thread_id)
        if not item:
            panel.setVisible(False)
            self._agent_pending_feedback_id = None
            return
        self._agent_pending_feedback_id = item.get("feedback_id")
        self.agent_feedback_question.setText(str(item.get("question") or ""))
        self.agent_feedback_reason.setText(str(item.get("reason") or ""))
        panel.setVisible(True)

    def load_current_thread(self) -> None:
        original_load_thread(self)
        self._refresh_agent_feedback()

    def start_request(self, question: str, mode: str, *, persist_user: bool = True) -> None:
        runtime = getattr(self, "agent_runtime", None)
        if runtime is None or not runtime.enabled:
            original_start_request(self, question, mode, persist_user=persist_user)
            return
        if self.analysis_thread and self.analysis_thread.isRunning():
            return
        thread = self._current_thread()
        if not thread:
            return
        display_question = question.strip() or _(
            "Analyse my complete health history deeply and explain the strongest useful patterns."
        )
        history = self.conversations.model_history(thread["id"], exclude_last_user=not persist_user)
        if persist_user:
            self.conversations.add_message(thread["id"], "user", display_question)
        self.input.clear()
        self._pending_mode = mode
        self._agent_exchange_events = []
        self._live_thinking = _("Preparing the personal agent…\n")
        self._live_answer = ""
        self._answer_received = False
        self._prompt_sections = []
        self.prompt_view.clear()
        self.prompt_button.setEnabled(False)
        self.prompt_button.setChecked(False)
        self._set_running(True)
        if self._activity_active:
            self._activity_event(_("Question received; preparing the personal agent…"))
        else:
            self._begin_activity(_("Question received; preparing the personal agent…"))
        self.refresh_threads(select_id=thread["id"])
        self._render_transcript()

        self.analysis_thread = AgentAnalysisThread(
            runtime,
            str(thread.get("model") or self.model_provider()),
            thread["snapshot"],
            question,
            self.tokens_provider(),
            self.context_limit_provider(),
            history=history,
            analysis_mode=mode,
            thread_id=str(thread["id"]),
        )
        self.analysis_thread.thinking_chunk.connect(self._thinking_chunk)
        self.analysis_thread.answer_chunk.connect(self._answer_chunk)
        self.analysis_thread.prompt_ready.connect(self._prompt_ready)
        self.analysis_thread.agent_event.connect(self._activity_event)
        self.analysis_thread.completed.connect(self._analysis_completed)
        self.analysis_thread.failed.connect(self._analysis_failed)
        self.analysis_thread.cancelled.connect(self._analysis_cancelled)
        self.analysis_thread.start()

    def prompt_ready(self, text: str) -> None:
        if text.startswith(AGENT_TRACE_PREFIX):
            try:
                payload = json.loads(text[len(AGENT_TRACE_PREFIX) :])
            except (TypeError, ValueError):
                return
            if not isinstance(payload, dict):
                return
            source = str(payload.get("source") or "Agent")
            target = str(payload.get("target") or "Runtime")
            content = str(payload.get("content") or "").strip()
            entry = f"{source} → {target}"
            if content:
                entry += f"\n{content}"
            self._agent_exchange_events.append(entry)
            # Bound only the UI transcript length; each individual message remains exact.
            self._agent_exchange_events = self._agent_exchange_events[-80:]
            self._render_activity_log()
            return
        original_prompt_ready(self, text)

    def analysis_completed(self, answer: str) -> None:
        original_analysis_completed(self, answer)
        self._refresh_agent_feedback()
        host = getattr(self, "agent_host_window", None)
        if host is not None:
            refresh_personal_ai_page(host)

    AIChatWindow._build_ui = build_ui
    AIChatWindow._render_activity_log = render_activity_log
    AIChatWindow._finish_activity = finish_activity
    AIChatWindow._refresh_agent_feedback = refresh_agent_feedback
    AIChatWindow._load_current_thread = load_current_thread
    AIChatWindow._start_request = start_request
    AIChatWindow._prompt_ready = prompt_ready
    AIChatWindow._analysis_completed = analysis_completed
    AIChatWindow._personal_agent_integration_installed = True


def install_personal_agent(main_window_module) -> None:
    """Install the agent as a thin layer over the existing desktop AI pipeline."""

    from . import ai_chat as ai_chat_module
    from .agent_localization import install_agent_language_and_calibration_ui
    from .agent_overview import install_agent_overview

    _install_agent_reasoning_compatibility()
    install_agent_language_and_calibration_ui()
    install_agent_overview(main_window_module)
    _install_chat_integration(ai_chat_module)
    MainWindow = main_window_module.MainWindow
    if getattr(MainWindow, "_personal_agent_integration_installed", False):
        return

    original_build_ai_page = MainWindow._build_ai_page
    original_ensure_chat = MainWindow._ensure_ai_chat_window
    original_sync_completed = MainWindow._sync_completed

    def build_ai_page(self):
        if not hasattr(self, "agent_runtime"):
            self.agent_runtime = AgentRuntime(self.store)
        page = original_build_ai_page(self)
        self.ai_sections.addTab(
            build_personal_ai_page(self, self.agent_runtime),
            _("Personal AI"),
        )
        return page

    def ensure_ai_chat_window(self):
        window = original_ensure_chat(self)
        window.agent_runtime = self.agent_runtime
        window.agent_host_window = self
        window._refresh_agent_feedback()
        return window

    def sync_completed(self, success: int, errors: int, automatic: bool = False) -> None:
        original_sync_completed(self, success, errors, automatic)
        refresh_personal_ai_page(self)
        self.refresh_overview()

    MainWindow._build_ai_page = build_ai_page
    MainWindow._ensure_ai_chat_window = ensure_ai_chat_window
    MainWindow._sync_completed = sync_completed
    MainWindow._personal_agent_integration_installed = True
