"""Persistent, resizable local-AI chat window."""

from __future__ import annotations

import html
import json
import re
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .ai_conversations import ConversationStore
from .ai_engine import TOKEN_USAGE_PREFIX
from .branding import APP_NAME
from .i18n import _
from .workers import AIAnalysisThread


class SnapshotBuildThread(QThread):
    completed = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        builder: Callable[[str, dict[str, Any] | None], tuple[dict, dict]],
        scope: str,
        period: dict[str, Any] | None,
    ) -> None:
        super().__init__()
        self.builder = builder
        self.scope = scope
        self.period = period

    def run(self) -> None:
        try:
            snapshot, period = self.builder(self.scope, self.period)
            self.completed.emit(snapshot, period)
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())


class AIChatWindow(QMainWindow):
    threads_changed = Signal()

    def __init__(
        self,
        *,
        conversations: ConversationStore,
        snapshot_builder: Callable[
            [str, dict[str, Any] | None], tuple[dict[str, Any], dict[str, Any]]
        ],
        period_provider: Callable[[], dict[str, Any]],
        revision_provider: Callable[[], str | None],
        model_provider: Callable[[], str],
        tokens_provider: Callable[[], int],
        context_limit_provider: Callable[[], int | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("{app} · Local AI chat", app=APP_NAME))
        self.resize(1260, 820)
        self.setMinimumSize(900, 620)
        self.conversations = conversations
        self.snapshot_builder = snapshot_builder
        self.period_provider = period_provider
        self.revision_provider = revision_provider
        self.model_provider = model_provider
        self.tokens_provider = tokens_provider
        self.context_limit_provider = context_limit_provider
        self.current_thread_id: str | None = None
        self.analysis_thread: AIAnalysisThread | None = None
        self.snapshot_thread: SnapshotBuildThread | None = None
        self._snapshot_action: tuple[str, str, bool, str] | None = None
        self._live_thinking = ""
        self._live_answer = ""
        self._answer_received = False
        self._pending_mode = "question"
        self._prompt_sections: list[str] = []
        self._activity_active = False
        self._activity_started_at = 0.0
        self._activity_events: list[str] = []
        self._activity_phase = ""
        self._token_usage_text = ""
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(1000)
        self._activity_timer.timeout.connect(self._update_activity_elapsed)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_transcript)
        self._build_ui()
        self.refresh_threads()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)

        splitter = QSplitter(Qt.Horizontal)
        sidebar = QFrame()
        sidebar.setObjectName("chatSidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 14, 12, 12)
        sidebar_title = QLabel(_("Conversations"))
        sidebar_title.setObjectName("chatSectionTitle")
        sidebar_layout.addWidget(sidebar_title)
        sidebar_hint = QLabel(_("Saved only on this computer"))
        sidebar_hint.setObjectName("cardCaption")
        sidebar_layout.addWidget(sidebar_hint)
        sidebar_actions = QHBoxLayout()
        new_button = QPushButton(_("New chat"))
        new_button.clicked.connect(lambda: self.new_conversation("selected"))
        deep_button = QPushButton(_("Deep analysis"))
        deep_button.setObjectName("primaryButton")
        deep_button.clicked.connect(lambda: self.new_conversation("all", auto_start=True))
        sidebar_actions.addWidget(new_button)
        sidebar_actions.addWidget(deep_button)
        sidebar_layout.addLayout(sidebar_actions)
        self.thread_list = QListWidget()
        self.thread_list.setObjectName("conversationList")
        self.thread_list.currentItemChanged.connect(self._thread_selected)
        sidebar_layout.addWidget(self.thread_list, 1)
        manage = QHBoxLayout()
        rename = QPushButton(_("Rename"))
        rename.clicked.connect(self.rename_current_thread)
        delete = QPushButton(_("Delete"))
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self.delete_current_thread)
        manage.addWidget(rename)
        manage.addWidget(delete)
        sidebar_layout.addLayout(manage)
        splitter.addWidget(sidebar)

        conversation = QWidget()
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(18, 2, 2, 2)
        title_row = QHBoxLayout()
        self.chat_title = QLabel(_("Local health conversation"))
        self.chat_title.setObjectName("pageTitle")
        title_row.addWidget(self.chat_title, 1)
        self.model_badge = QLabel("—")
        self.model_badge.setObjectName("chatBadge")
        title_row.addWidget(self.model_badge)
        self.period_badge = QLabel("—")
        self.period_badge.setObjectName("chatBadge")
        title_row.addWidget(self.period_badge)
        conversation_layout.addLayout(title_row)

        snapshot_row = QHBoxLayout()
        self.snapshot_label = QLabel(_("Choose or create a conversation."))
        self.snapshot_label.setObjectName("pageSubtitle")
        self.snapshot_label.setWordWrap(True)
        snapshot_row.addWidget(self.snapshot_label, 1)
        self.refresh_data_button = QPushButton(_("Refresh conversation data"))
        self.refresh_data_button.clicked.connect(self.refresh_current_snapshot)
        self.refresh_data_button.setVisible(False)
        snapshot_row.addWidget(self.refresh_data_button)
        conversation_layout.addLayout(snapshot_row)

        self.coverage_label = QLabel()
        self.coverage_label.setObjectName("coverageWarning")
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setVisible(False)
        conversation_layout.addWidget(self.coverage_label)

        self.activity_panel = QFrame()
        self.activity_panel.setObjectName("aiActivityPanel")
        activity_layout = QVBoxLayout(self.activity_panel)
        activity_layout.setContentsMargins(14, 11, 14, 11)
        activity_layout.setSpacing(6)
        activity_header = QHBoxLayout()
        self.activity_title = QLabel(_("VitalChronicle AI is working"))
        self.activity_title.setObjectName("activityTitle")
        activity_header.addWidget(self.activity_title, 1)
        self.activity_elapsed = QLabel()
        self.activity_elapsed.setObjectName("activityElapsed")
        activity_header.addWidget(self.activity_elapsed)
        activity_layout.addLayout(activity_header)
        activity_hint = QLabel(
            _("This may take some time depending on your model and hardware.")
        )
        activity_hint.setObjectName("activityHint")
        activity_hint.setWordWrap(True)
        activity_layout.addWidget(activity_hint)
        self.activity_progress = QProgressBar()
        self.activity_progress.setObjectName("aiActivityProgress")
        self.activity_progress.setRange(0, 0)
        self.activity_progress.setTextVisible(False)
        self.activity_progress.setMaximumHeight(7)
        activity_layout.addWidget(self.activity_progress)
        self.activity_log = QLabel()
        self.activity_log.setObjectName("activityLog")
        self.activity_log.setWordWrap(True)
        activity_layout.addWidget(self.activity_log)
        self.activity_panel.setVisible(False)
        conversation_layout.addWidget(self.activity_panel)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("chatTranscript")
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setPlaceholderText(
            _(
                "Start a conversation about the selected period, or request a deep analysis "
                "of the complete local history."
            )
        )
        conversation_layout.addWidget(self.transcript, 1)

        self.evidence_tree = QTreeWidget()
        self.evidence_tree.setObjectName("evidenceDrawer")
        self.evidence_tree.setHeaderLabels([_("Evidence"), _("Confidence"), _("Score")])
        self.evidence_tree.setVisible(False)
        self.evidence_tree.setMaximumHeight(230)
        conversation_layout.addWidget(self.evidence_tree)

        self.prompt_view = QPlainTextEdit()
        self.prompt_view.setObjectName("promptInspector")
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setVisible(False)
        self.prompt_view.setMaximumHeight(300)
        conversation_layout.addWidget(self.prompt_view)

        composer = QFrame()
        composer.setObjectName("chatComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 12, 10)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            _("Ask a follow-up question about the data in this conversation…")
        )
        self.input.setMaximumHeight(105)
        composer_layout.addWidget(self.input)
        action_row = QHBoxLayout()
        self.send_button = QPushButton(_("Send"))
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self.send_question)
        self.stop_button = QPushButton(_("Stop"))
        self.stop_button.clicked.connect(self.stop_analysis)
        self.stop_button.setVisible(False)
        regenerate = QPushButton(_("Regenerate"))
        regenerate.clicked.connect(self.regenerate_answer)
        copy_button = QPushButton(_("Copy answer"))
        copy_button.clicked.connect(self.copy_last_answer)
        export_button = QPushButton(_("Export chat"))
        export_button.clicked.connect(self.export_chat)
        self.evidence_button = QPushButton(_("Show evidence"))
        self.evidence_button.setCheckable(True)
        self.evidence_button.toggled.connect(self._toggle_evidence)
        self.prompt_button = QPushButton(_("Show prompt"))
        self.prompt_button.setCheckable(True)
        self.prompt_button.setEnabled(False)
        self.prompt_button.toggled.connect(self._toggle_prompt)
        action_row.addWidget(self.send_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(regenerate)
        action_row.addWidget(copy_button)
        action_row.addWidget(export_button)
        action_row.addStretch()
        action_row.addWidget(self.evidence_button)
        action_row.addWidget(self.prompt_button)
        composer_layout.addLayout(action_row)
        conversation_layout.addWidget(composer)

        disclaimer = QLabel(
            _(
                "Local exploratory analysis: it can reveal patterns, not diagnoses or causes. "
                "Confirm important findings with validated measurements and a professional."
            )
        )
        disclaimer.setObjectName("disclaimer")
        disclaimer.setWordWrap(True)
        conversation_layout.addWidget(disclaimer)
        splitter.addWidget(conversation)
        splitter.setSizes([270, 950])
        root.addWidget(splitter)
        self.setCentralWidget(central)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.send_question)

    def open_thread(self, thread_id: str | None = None) -> None:
        self.refresh_threads(select_id=thread_id)
        if not self.current_thread_id and self.thread_list.count():
            self.thread_list.setCurrentRow(0)
        self.show()
        self.raise_()
        self.activateWindow()

    def notify_data_revision_changed(self) -> None:
        """Refresh the snapshot-age badge after a Google data synchronisation."""

        self._load_current_thread()

    def refresh_threads(self, *, select_id: str | None = None) -> None:
        selected = select_id or self.current_thread_id
        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_row = -1
        for row, thread in enumerate(self.conversations.list_threads()):
            updated = str(thread.get("updated_at", "")).replace("T", " ")[:16]
            scope = (
                _("Complete history")
                if thread.get("scope") == "all"
                else str(thread.get("period", {}).get("label", _("Selected period")))
            )
            item = QListWidgetItem(f"{thread.get('title', _('Conversation'))}\n{scope} · {updated}")
            item.setData(Qt.UserRole, thread["id"])
            self.thread_list.addItem(item)
            if thread["id"] == selected:
                selected_row = row
        self.thread_list.blockSignals(False)
        if selected_row >= 0:
            self.thread_list.setCurrentRow(selected_row)
        self.threads_changed.emit()

    def _thread_selected(self, current: QListWidgetItem | None) -> None:
        previous_thread_id = self.current_thread_id
        if current is None:
            self.current_thread_id = None
        else:
            self.current_thread_id = str(current.data(Qt.UserRole))
        self._live_thinking = ""
        self._live_answer = ""
        if self.current_thread_id != previous_thread_id:
            self._prompt_sections = []
            self.prompt_view.clear()
            self.prompt_button.setEnabled(False)
            self.prompt_button.setChecked(False)
            if not self._activity_active:
                self._token_usage_text = ""
                self.activity_panel.setVisible(False)
        self._load_current_thread()

    def _load_current_thread(self) -> None:
        thread = self._current_thread()
        if not thread:
            self.chat_title.setText(_("Local health conversation"))
            self.model_badge.setText("—")
            self.period_badge.setText("—")
            self.snapshot_label.setText(_("Choose or create a conversation."))
            self.refresh_data_button.setVisible(False)
            self.coverage_label.setVisible(False)
            self.transcript.clear()
            self.evidence_tree.clear()
            return
        self.chat_title.setText(str(thread.get("title", _("Conversation"))))
        self.model_badge.setText(str(thread.get("model", "—")))
        self.period_badge.setText(
            _("Complete history")
            if thread.get("scope") == "all"
            else str(thread.get("period", {}).get("label", _("Selected period")))
        )
        observed = str(thread.get("snapshot_observed_at") or "—").replace("T", " ")[:16]
        new_data = self._has_newer_data(thread)
        self.snapshot_label.setText(
            _("Data analysed up to {time} · newer local data is available", time=observed)
            if new_data
            else _("Data analysed up to {time}", time=observed)
        )
        self.snapshot_label.setStyleSheet("color: #B06000; font-weight: 700" if new_data else "")
        self.refresh_data_button.setVisible(new_data)
        coverage = thread.get("snapshot", {}).get("requested_interval_coverage") or {}
        partial_coverage = bool(coverage.get("scope_is_partially_observed"))
        self.coverage_label.setText(
            _("Data coverage notice: {notice}", notice=coverage.get("coverage_notice", ""))
        )
        self.coverage_label.setVisible(partial_coverage)
        self._populate_evidence(thread)
        self._render_transcript()

    def new_conversation(
        self, scope: str = "selected", *, auto_start: bool = False, question: str = ""
    ) -> None:
        period = None if scope == "all" else self.period_provider()
        self._start_snapshot_build("new", scope, period, auto_start, question)

    def _start_snapshot_build(
        self,
        action: str,
        scope: str,
        period: dict[str, Any] | None,
        auto_start: bool,
        question: str,
    ) -> None:
        if self.snapshot_thread and self.snapshot_thread.isRunning():
            return
        self._snapshot_action = (action, scope, auto_start, question)
        self.snapshot_label.setText(_("Preparing deterministic health evidence…"))
        self._begin_activity(_("Reading and preparing local health data…"))
        self.refresh_data_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.snapshot_thread = SnapshotBuildThread(self.snapshot_builder, scope, period)
        self.snapshot_thread.completed.connect(self._snapshot_ready)
        self.snapshot_thread.failed.connect(self._snapshot_failed)
        self.snapshot_thread.start()

    def _snapshot_ready(self, snapshot: dict, period: dict) -> None:
        action, scope, auto_start, question = self._snapshot_action or (
            "new",
            "selected",
            False,
            "",
        )
        self._snapshot_action = None
        self.refresh_data_button.setEnabled(True)
        self.send_button.setEnabled(True)
        if not snapshot.get("metrics"):
            self._finish_activity()
            QMessageBox.information(
                self,
                _("Insufficient data"),
                _("Download Google Health data or choose a wider period first."),
            )
            self._load_current_thread()
            return
        revision = self.revision_provider()
        if action == "refresh" and self.current_thread_id:
            self.conversations.update_snapshot(self.current_thread_id, snapshot, revision, period)
            self.conversations.add_message(
                self.current_thread_id,
                "event",
                _("Conversation data refreshed from the local archive."),
            )
            thread_id = self.current_thread_id
        else:
            title = (
                _("Complete health-history analysis")
                if scope == "all"
                else _("New health conversation")
            )
            thread = self.conversations.create_thread(
                title=title,
                model=self.model_provider(),
                scope=scope,
                period=period,
                snapshot=snapshot,
                snapshot_revision=revision,
            )
            thread_id = thread["id"]
        self.refresh_threads(select_id=thread_id)
        self._load_current_thread()
        if auto_start:
            self._activity_event(_("Health evidence is ready; preparing the AI request…"))
            QTimer.singleShot(
                0,
                lambda: self._start_request(
                    question,
                    "deep" if scope == "all" and not question else "question",
                ),
            )
        else:
            self._finish_activity()

    def _snapshot_failed(self, message: str) -> None:
        self._snapshot_action = None
        self._finish_activity()
        self.refresh_data_button.setEnabled(True)
        self.send_button.setEnabled(True)
        self._load_current_thread()
        QMessageBox.warning(self, _("Data preparation failed"), message)

    def refresh_current_snapshot(self) -> None:
        thread = self._current_thread()
        if not thread:
            return
        self._start_snapshot_build(
            "refresh",
            str(thread.get("scope", "selected")),
            thread.get("period"),
            False,
            "",
        )

    def send_question(self) -> None:
        question = self.input.toPlainText().strip()
        if not question:
            return
        if not self.current_thread_id:
            self.new_conversation("selected", auto_start=True, question=question)
            self.input.clear()
            return
        self._start_request(question, "question")

    def _start_request(self, question: str, mode: str, *, persist_user: bool = True) -> None:
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
        self._live_thinking = _("Preparing the local model…\n")
        self._live_answer = ""
        self._answer_received = False
        self._prompt_sections = []
        self.prompt_view.clear()
        self.prompt_button.setEnabled(False)
        self.prompt_button.setChecked(False)
        self._set_running(True)
        if self._activity_active:
            self._activity_event(_("Question received; preparing the request for Ollama…"))
        else:
            self._begin_activity(_("Question received; preparing the request for Ollama…"))
        self.refresh_threads(select_id=thread["id"])
        self._render_transcript()
        self.analysis_thread = AIAnalysisThread(
            str(thread.get("model") or self.model_provider()),
            thread["snapshot"],
            question,
            self.tokens_provider(),
            self.context_limit_provider(),
            history=history,
            analysis_mode=mode,
        )
        self.analysis_thread.thinking_chunk.connect(self._thinking_chunk)
        self.analysis_thread.answer_chunk.connect(self._answer_chunk)
        self.analysis_thread.prompt_ready.connect(self._prompt_ready)
        self.analysis_thread.completed.connect(self._analysis_completed)
        self.analysis_thread.failed.connect(self._analysis_failed)
        self.analysis_thread.cancelled.connect(self._analysis_cancelled)
        self.analysis_thread.start()

    def _thinking_chunk(self, text: str) -> None:
        if self._activity_phase != "thinking":
            self._activity_phase = "thinking"
            self._activity_event(_("Ollama is processing the health evidence…"))
        self._live_thinking += text
        self._schedule_render()

    def _answer_chunk(self, text: str) -> None:
        if not self._answer_received:
            self._answer_received = True
            self._live_thinking = ""
            self._activity_phase = "answer"
            self._activity_event(_("The model is writing the final answer…"))
        self._live_answer += text
        self._schedule_render()

    def _prompt_ready(self, text: str) -> None:
        if text.startswith(TOKEN_USAGE_PREFIX):
            try:
                payload = json.loads(text[len(TOKEN_USAGE_PREFIX) :])
            except (TypeError, ValueError):
                return
            if isinstance(payload, dict):
                self._update_token_usage(payload)
            return
        self._prompt_sections.append(text)
        prompt_number = len(self._prompt_sections)
        if self._pending_mode == "deep" and prompt_number == 1:
            self._activity_event(_("Ollama is ranking the strongest longitudinal evidence…"))
        elif prompt_number <= 2:
            self._activity_event(_("The evidence is ready; Ollama is building the analysis…"))
        else:
            self._activity_event(_("The model is retrying with a compact evidence packet…"))
        self.prompt_view.setPlainText("\n\n" + ("\n\n" + "=" * 72 + "\n\n").join(self._prompt_sections))
        self.prompt_button.setEnabled(True)

    def _update_token_usage(self, payload: dict[str, Any]) -> None:
        try:
            context = max(1, int(payload.get("context") or 0))
            context_used = max(0, int(payload.get("context_used") or 0))
            context_remaining = max(0, int(payload.get("context_remaining") or 0))
            input_tokens = max(0, int(payload.get("input_tokens") or 0))
            generated = max(0, int(payload.get("generated_tokens") or 0))
            output_budget = max(1, int(payload.get("output_budget") or 0))
            output_remaining = max(0, int(payload.get("output_remaining") or 0))
            usage_percent = max(0.0, min(100.0, float(payload.get("usage_percent") or 0.0)))
        except (TypeError, ValueError):
            return
        exact = bool(payload.get("exact"))
        marker = "" if exact else "~"
        phase = str(payload.get("phase") or "model").replace("_", " ")
        speed = payload.get("tokens_per_second")
        try:
            speed_text = f" · {marker}{float(speed):.1f} tok/s" if speed is not None else ""
        except (TypeError, ValueError):
            speed_text = ""
        status = "NEAR LIMIT" if usage_percent >= 85 else "HIGH" if usage_percent >= 70 else "OK"
        self._token_usage_text = (
            f"{phase} · Ctx {marker}{context_used:,}/{context:,} ({usage_percent:.0f}%) "
            f"· free {marker}{context_remaining:,} | In {marker}{input_tokens:,} | "
            f"Out {marker}{generated:,}/{output_budget:,} · free {marker}{output_remaining:,}"
            f"{speed_text} | {status}"
        )
        self.activity_progress.setRange(0, 1000)
        self.activity_progress.setValue(int(round(usage_percent * 10)))
        self.activity_progress.setTextVisible(False)
        self._render_activity_log()

    def _analysis_completed(self, answer: str) -> None:
        thread = self._current_thread()
        if thread:
            known_ids = {
                str(item.get("evidence_id"))
                for item in thread.get("snapshot", {}).get("candidate_insights", [])
            }
            cited = [evidence_id for evidence_id in known_ids if f"[{evidence_id}]" in answer]
            self.conversations.add_message(
                thread["id"], "assistant", answer, evidence_ids=sorted(cited)
            )
        self._live_thinking = ""
        self._live_answer = ""
        self._finish_activity()
        self._set_running(False)
        self.refresh_threads(select_id=self.current_thread_id)
        self._load_current_thread()

    def _analysis_failed(self, message: str) -> None:
        if self.current_thread_id:
            self.conversations.add_message(
                self.current_thread_id,
                "event",
                _("Local analysis failed: {message}", message=message),
            )
        self._live_thinking = ""
        self._live_answer = ""
        self._finish_activity()
        self._set_running(False)
        self.refresh_threads(select_id=self.current_thread_id)
        self._load_current_thread()
        QMessageBox.warning(
            self,
            _("Local analysis unavailable"),
            _("{message}\n\nCheck Ollama from the Local AI tab.", message=message),
        )

    def stop_analysis(self) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.cancel()
            self.stop_button.setEnabled(False)

    def _analysis_cancelled(self) -> None:
        if self.current_thread_id:
            self.conversations.add_message(
                self.current_thread_id, "event", _("Analysis stopped by the user.")
            )
        self._live_thinking = ""
        self._live_answer = ""
        self._finish_activity()
        self._set_running(False)
        self.refresh_threads(select_id=self.current_thread_id)
        self._load_current_thread()

    def regenerate_answer(self) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            return
        thread = self._current_thread()
        if not thread:
            return
        last_user = next(
            (
                str(message.get("content", ""))
                for message in reversed(thread.get("messages", []))
                if message.get("role") == "user"
            ),
            "",
        )
        if not last_user:
            return
        self.conversations.remove_last_assistant(thread["id"])
        user_count = sum(message.get("role") == "user" for message in thread.get("messages", []))
        mode = "deep" if thread.get("scope") == "all" and user_count == 1 else "question"
        self._start_request(last_user, mode, persist_user=False)

    def rename_current_thread(self) -> None:
        thread = self._current_thread()
        if not thread:
            return
        title, accepted = QInputDialog.getText(
            self, _("Rename conversation"), _("Title"), text=str(thread.get("title", ""))
        )
        if accepted and title.strip():
            self.conversations.rename_thread(thread["id"], title)
            self.refresh_threads(select_id=thread["id"])
            self._load_current_thread()

    def delete_current_thread(self) -> None:
        thread = self._current_thread()
        if not thread:
            return
        answer = QMessageBox.question(
            self,
            _("Delete conversation"),
            _("Delete this locally saved conversation? Your health archive is not affected."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.conversations.delete_thread(thread["id"])
        self.current_thread_id = None
        self.refresh_threads()
        if self.thread_list.count():
            self.thread_list.setCurrentRow(0)
        else:
            self._load_current_thread()

    def copy_last_answer(self) -> None:
        answer = self._last_assistant_content()
        if answer:
            QApplication.clipboard().setText(answer)

    def export_chat(self) -> None:
        thread = self._current_thread()
        if not thread:
            return
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", str(thread.get("title", "chat"))).strip("-")
        filename, _filter = QFileDialog.getSaveFileName(
            self,
            _("Export conversation"),
            f"{safe_title or 'health-chat'}.md",
            _("Markdown (*.md)"),
        )
        if not filename:
            return
        Path(filename).write_text(self._thread_markdown(thread), encoding="utf-8")

    def _thread_markdown(self, thread: dict[str, Any]) -> str:
        lines = [f"# {thread.get('title', 'VitalChronicle AI')}", ""]
        period = thread.get("period", {})
        lines.extend(
            [
                f"- Model: {thread.get('model', '')}",
                f"- Period: {period.get('label', thread.get('scope', ''))}",
                f"- Data snapshot: {thread.get('snapshot_observed_at', '')}",
                "",
            ]
        )
        coverage = thread.get("snapshot", {}).get("requested_interval_coverage") or {}
        if coverage.get("scope_is_partially_observed"):
            lines.extend(
                [
                    f"> Data scope: {coverage.get('coverage_notice', '')}",
                    "",
                ]
            )
        for message in thread.get("messages", []):
            role = message.get("role")
            if role == "user":
                lines.extend(["## You", "", str(message.get("content", "")), ""])
            elif role == "assistant":
                lines.extend([f"## {APP_NAME} AI", "", str(message.get("content", "")), ""])
            else:
                lines.extend([f"> {message.get('content', '')}", ""])
        return "\n".join(lines)

    def _render_transcript(self) -> None:
        thread = self._current_thread()
        if not thread:
            self.transcript.clear()
            return
        markdown = self._thread_markdown(thread)
        if self._live_thinking:
            safe = html.escape(self._live_thinking)
            markdown += _(
                "\n\n## VitalChronicle AI · thinking\n\n> {thinking}",
                thinking=safe.replace("\n", "\n> "),
            )
        elif self._live_answer:
            markdown += f"\n\n## {APP_NAME} AI\n\n{self._live_answer}"
        self.transcript.setMarkdown(markdown)
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.transcript.setTextCursor(cursor)

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start(50)

    def _populate_evidence(self, thread: dict[str, Any]) -> None:
        self.evidence_tree.clear()
        for insight in thread.get("snapshot", {}).get("candidate_insights", []):
            item = QTreeWidgetItem(
                [
                    str(insight.get("headline", insight.get("evidence_id", ""))),
                    str(insight.get("confidence", "")),
                    str(insight.get("relevance_score", "")),
                ]
            )
            item.setToolTip(0, str(insight.get("evidence_id", "")))
            caveat = insight.get("caveat")
            if caveat:
                item.addChild(QTreeWidgetItem([str(caveat), "", ""]))
            self.evidence_tree.addTopLevelItem(item)
        self.evidence_tree.resizeColumnToContents(1)
        self.evidence_tree.resizeColumnToContents(2)

    def _toggle_evidence(self, visible: bool) -> None:
        self.evidence_tree.setVisible(visible)
        self.evidence_button.setText(_("Hide evidence") if visible else _("Show evidence"))

    def _toggle_prompt(self, visible: bool) -> None:
        self.prompt_view.setVisible(visible)
        self.prompt_button.setText(_("Hide prompt") if visible else _("Show prompt"))

    def _set_running(self, running: bool) -> None:
        self.send_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.thread_list.setEnabled(not running)
        self.input.setEnabled(not running)

    def _begin_activity(self, stage: str) -> None:
        self._activity_active = True
        self._activity_started_at = time.monotonic()
        self._activity_events = []
        self._activity_phase = ""
        self._token_usage_text = ""
        self.activity_title.setText(_("VitalChronicle AI is working"))
        self.activity_elapsed.clear()
        self.activity_progress.setRange(0, 0)
        self.activity_progress.setTextVisible(False)
        self.activity_panel.setVisible(True)
        self._activity_timer.start()
        self._activity_event(stage)
        self._update_activity_elapsed()

    def _render_activity_log(self) -> None:
        lines = [f"• {message}" for message in self._activity_events[-4:]]
        if self._token_usage_text:
            lines.append(self._token_usage_text)
        self.activity_log.setText("\n".join(lines))

    def _activity_event(self, stage: str) -> None:
        if not self._activity_active:
            return
        if self._activity_events and self._activity_events[-1] == stage:
            return
        self._activity_events.append(stage)
        self._render_activity_log()

    def _update_activity_elapsed(self) -> None:
        if not self._activity_active:
            return
        elapsed = max(0, int(time.monotonic() - self._activity_started_at))
        minutes, seconds = divmod(elapsed, 60)
        self.activity_elapsed.setText(
            _("Working · {minutes:02d}:{seconds:02d}", minutes=minutes, seconds=seconds)
        )

    def _finish_activity(self) -> None:
        self._activity_active = False
        self._activity_timer.stop()
        self._activity_events = []
        self._activity_phase = ""
        if self._token_usage_text:
            self.activity_title.setText("AI · token usage")
            self.activity_elapsed.clear()
            self._render_activity_log()
            self.activity_panel.setVisible(True)
        else:
            self.activity_panel.setVisible(False)

    def _current_thread(self) -> dict[str, Any] | None:
        return (
            self.conversations.get_thread(self.current_thread_id)
            if self.current_thread_id
            else None
        )

    def _has_newer_data(self, thread: dict[str, Any]) -> bool:
        current = self.revision_provider()
        snapshot = thread.get("snapshot_revision")
        return bool(current and snapshot and str(current) > str(snapshot))

    def _last_assistant_content(self) -> str:
        thread = self._current_thread()
        if not thread:
            return ""
        return next(
            (
                str(message.get("content", ""))
                for message in reversed(thread.get("messages", []))
                if message.get("role") == "assistant"
            ),
            "",
        )
