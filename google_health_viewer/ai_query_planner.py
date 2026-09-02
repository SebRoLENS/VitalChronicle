"""AI-first planning of the local health evidence needed for each question.

The planner sees only a catalogue of locally available data (names, coverage dates and
record counts). It cannot execute SQL or Python. A validated plan is then fulfilled by
Python, which reads only the approved data types and date range before the normal
VitalChronicle deterministic evidence pipeline and final Ollama synthesis run.
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QSettings, QThread, QTimer, Signal

from . import ai_adaptive_retrieval_v2, ai_retrieval_scope_status
from .ai_chat import AIChatWindow
from .ai_engine import OptimizedOllamaClient, _request_budget
from .ai_hardware import reasoning_value
from .ai_insights import build_ai_ready_snapshot
from .constants import DATA_TYPE_BY_KEY
from .i18n import _
from .local_ai import AIAnalysisCancelled, LocalAIError

from .ai_query_planner_core import (
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

class AIDataPlanThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        model: str,
        catalog: dict[str, Any],
        question: str,
        history: list[dict[str, str]],
        model_context_limit: int | None,
    ) -> None:
        super().__init__()
        self.model = model
        self.catalog = catalog
        self.question = question
        self.history = history
        self.model_context_limit = model_context_limit

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            profile = str(QSettings().value("ai/performance_profile", "standard") or "standard")
            client = OptimizedOllamaClient(model=self.model, performance_profile=profile)
            messages = _planner_messages(self.catalog, self.question, self.history)
            num_ctx, num_predict, _estimated = _request_budget(
                messages, PLANNER_OUTPUT_TOKENS, self.model_context_limit
            )
            configured = reasoning_value(self.model, profile)
            # Planning is intentionally the cheap pass. Models that require a named
            # reasoning level still receive their valid string value.
            think: bool | str = configured if isinstance(configured, str) else False
            raw = client._chat_stream(
                messages,
                think=think,
                num_predict=num_predict,
                num_ctx=num_ctx,
                thinking_callback=None,
                answer_callback=None,
                cancel_callback=self.isInterruptionRequested,
            )
            plan = resolve_data_plan(_parse_json_object(raw), self.catalog)
            self.completed.emit(plan)
        except AIAnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - invalid planner output has a safe fallback.
            self.failed.emit(str(exc))


class PlannedSnapshotThread(QThread):
    completed = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, store, plan: dict[str, Any]) -> None:
        super().__init__()
        self.store = store
        self.plan = plan

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            snapshot, period = build_planned_snapshot(self.store, self.plan)
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            self.completed.emit(snapshot, period)
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())


class PlannedAIChatWindow(AIChatWindow):
    """Chat window that plans and extracts evidence afresh for every user question."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.plan_thread: AIDataPlanThread | None = None
        self.planned_snapshot_thread: PlannedSnapshotThread | None = None
        self._planned_request: tuple[str, str] | None = None
        self._planned_catalog: dict[str, Any] | None = None
        self._planned_fallback_used = False

    def _main_store(self):
        owner = self.parent()
        store = getattr(owner, "store", None)
        if store is None:
            raise LocalAIError("The local health archive is not available to the AI planner.")
        return store

    def new_conversation(
        self, scope: str = "selected", *, auto_start: bool = False, question: str = ""
    ) -> None:
        if self._any_planning_running() or (self.analysis_thread and self.analysis_thread.isRunning()):
            return
        deep = scope == "all"
        thread = self.conversations.create_thread(
            title=_("Complete health-history analysis") if deep else _("New health conversation"),
            model=self.model_provider(),
            scope="auto_deep" if deep else "auto",
            period={"preset": "ai_planned", "label": "AI · automatic"},
            snapshot={
                "metrics": [],
                "correlations": [],
                "analysis_scope": "ai_planned_pending",
            },
            snapshot_revision=self.revision_provider(),
        )
        self.refresh_threads(select_id=thread["id"])
        self._load_current_thread()
        if auto_start:
            QTimer.singleShot(
                0,
                lambda: self._start_request(question, "deep" if deep else "question"),
            )

    def _any_planning_running(self) -> bool:
        return bool(
            (self.plan_thread and self.plan_thread.isRunning())
            or (self.planned_snapshot_thread and self.planned_snapshot_thread.isRunning())
        )

    def _start_request(self, question: str, mode: str, *, persist_user: bool = True) -> None:
        if self._any_planning_running() or (self.analysis_thread and self.analysis_thread.isRunning()):
            return
        thread = self._current_thread()
        if not thread:
            return
        display_question = question.strip() or _(
            "Analyse my complete health history deeply and explain the strongest useful patterns."
        )
        history = self.conversations.model_history(
            thread["id"], exclude_last_user=not persist_user
        )
        if persist_user:
            self.conversations.add_message(thread["id"], "user", display_question)
        self.input.clear()
        self._pending_mode = mode
        self._live_thinking = _("Choosing the local data needed for this question…\n")
        self._live_answer = ""
        self._answer_received = False
        self._set_running(True)
        if self._activity_active:
            self._activity_event(_("AI is choosing the metrics and time window it needs…"))
        else:
            self._begin_activity(_("AI is choosing the metrics and time window it needs…"))
        self.refresh_threads(select_id=thread["id"])
        self._render_transcript()

        try:
            catalog = build_data_catalog(self._main_store())
        except Exception as exc:  # noqa: BLE001 - local catalogue errors are user-visible.
            self._planned_failed(str(exc))
            return
        if not catalog.get("datasets"):
            self._planned_failed(_("There is not enough local data to analyse."))
            return

        self._planned_request = (display_question, mode)
        self._planned_catalog = catalog
        self._planned_fallback_used = False
        if mode == "deep" and not question.strip():
            plan = fallback_data_plan(
                catalog,
                all_history=True,
                reason="explicit_complete_history_analysis",
            )
            self._plan_ready(plan)
            return

        self.plan_thread = AIDataPlanThread(
            str(thread.get("model") or self.model_provider()),
            catalog,
            display_question,
            history,
            self.context_limit_provider(),
        )
        self.plan_thread.completed.connect(self._plan_ready)
        self.plan_thread.failed.connect(self._plan_failed)
        self.plan_thread.cancelled.connect(self._planned_cancelled)
        self.plan_thread.finished.connect(self._plan_thread_finished)
        self.plan_thread.start()

    def _plan_thread_finished(self) -> None:
        thread = self.plan_thread
        self.plan_thread = None
        if thread is not None:
            thread.deleteLater()

    def _plan_failed(self, message: str) -> None:
        catalog = self._planned_catalog
        if not catalog:
            self._planned_failed(message)
            return
        self._planned_fallback_used = True
        self._activity_event(_("Planner output was invalid; using a safe broad local-data fallback…"))
        self._plan_ready(fallback_data_plan(catalog, reason=f"planner_fallback: {message[:120]}"))

    def _plan_ready(self, plan: dict[str, Any]) -> None:
        labels = [str(value) for value in plan.get("data_labels") or []]
        summary = ", ".join(labels[:4])
        if len(labels) > 4:
            summary += f" +{len(labels) - 4}"
        self._activity_event(
            _(
                "Python is preparing {days} days of selected local evidence: {metrics}",
                days=plan.get("days", "—"),
                metrics=summary or "—",
            )
        )
        self.planned_snapshot_thread = PlannedSnapshotThread(self._main_store(), plan)
        self.planned_snapshot_thread.completed.connect(self._planned_snapshot_ready)
        self.planned_snapshot_thread.failed.connect(self._planned_snapshot_failed)
        self.planned_snapshot_thread.cancelled.connect(self._planned_cancelled)
        self.planned_snapshot_thread.finished.connect(self._planned_snapshot_thread_finished)
        self.planned_snapshot_thread.start()

    def _planned_snapshot_thread_finished(self) -> None:
        thread = self.planned_snapshot_thread
        self.planned_snapshot_thread = None
        if thread is not None:
            thread.deleteLater()

    def _planned_snapshot_ready(self, snapshot: dict, period: dict) -> None:
        request = self._planned_request
        thread = self._current_thread()
        if request is None or thread is None:
            self._planned_failed(_("The AI data request lost its conversation context."))
            return
        if not snapshot.get("metrics"):
            if not self._planned_fallback_used and self._planned_catalog:
                self._planned_fallback_used = True
                self._activity_event(_("Selected data were insufficient; widening the local evidence once…"))
                self._plan_ready(
                    fallback_data_plan(self._planned_catalog, reason="empty_selected_evidence_fallback")
                )
                return
            self._planned_failed(_("The selected local data contain no usable measurements."))
            return

        self.conversations.update_snapshot(
            thread["id"], snapshot, self.revision_provider(), period
        )
        question, mode = request
        self._activity_event(_("Selected deterministic evidence is ready; starting final analysis…"))
        # Call the base implementation directly: the planner has already persisted
        # the user message, so the final model receives the fresh snapshot without
        # re-entering this planning method.
        AIChatWindow._start_request(self, question, mode, persist_user=False)

    def _planned_snapshot_failed(self, message: str) -> None:
        self._planned_failed(message)

    def _planned_failed(self, message: str) -> None:
        if self.current_thread_id:
            self.conversations.add_message(
                self.current_thread_id,
                "event",
                _("Local data planning failed: {message}", message=message),
            )
        self._live_thinking = ""
        self._live_answer = ""
        self._finish_activity()
        self._set_running(False)
        self.refresh_threads(select_id=self.current_thread_id)
        self._load_current_thread()

    def _planned_cancelled(self) -> None:
        self._planned_request = None
        self._planned_catalog = None
        self._analysis_cancelled()

    def stop_analysis(self) -> None:
        if self.plan_thread and self.plan_thread.isRunning():
            self.plan_thread.cancel()
            self.stop_button.setEnabled(False)
        if self.planned_snapshot_thread and self.planned_snapshot_thread.isRunning():
            self.planned_snapshot_thread.cancel()
            self.stop_button.setEnabled(False)
        super().stop_analysis()

    def regenerate_answer(self) -> None:
        if self._any_planning_running() or (self.analysis_thread and self.analysis_thread.isRunning()):
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
        mode = "deep" if thread.get("scope") == "auto_deep" and user_count == 1 else "question"
        self._start_request(last_user, mode, persist_user=False)

    def refresh_current_snapshot(self) -> None:
        # Planner-managed conversations always query the current local archive on
        # the next question, so there is no stale fixed-period snapshot to refresh.
        self._load_current_thread()

    def _load_current_thread(self) -> None:
        super()._load_current_thread()
        thread = self._current_thread()
        if not thread or thread.get("scope") not in {"auto", "auto_deep"}:
            return
        self.refresh_data_button.setVisible(False)
        period = thread.get("period") or {}
        self.period_badge.setText(str(period.get("label") or "AI · automatic"))
        snapshot = thread.get("snapshot") or {}
        request = snapshot.get("ai_data_request") or {}
        if not snapshot.get("metrics"):
            self.snapshot_label.setText(
                _("Automatic data selection: ask a question and AI will choose metrics and period.")
            )
            self.snapshot_label.setStyleSheet("")
            return
        labels = [str(value) for value in request.get("data_labels") or []]
        selected = ", ".join(labels[:4])
        if len(labels) > 4:
            selected += f" +{len(labels) - 4}"
        if self._has_newer_data(thread):
            suffix = _("Newer local data will be considered automatically on the next question.")
            self.snapshot_label.setStyleSheet("color: #B06000; font-weight: 700")
        else:
            suffix = _("Each new question can request a different period and different metrics.")
            self.snapshot_label.setStyleSheet("")
        self.snapshot_label.setText(
            _(
                "Last AI-selected evidence: {metrics} · {period}. {suffix}",
                metrics=selected or "—",
                period=str(period.get("label") or "AI"),
                suffix=suffix,
            )
        )


def _planned_classifier(packet: dict[str, Any], question: str, analysis_mode: str = "question"):
    metadata = packet.get("packet") or {}
    if isinstance(metadata, dict) and metadata.get("analysis_scope") == "ai_planned":
        return {
            "mode": "global",
            "data_types": [],
            "domains": [],
            "confidence": 1.0,
            "reason": "model_planner_selected",
            "matched_terms": [],
        }
    return _ORIGINAL_CLASSIFIER(packet, question, analysis_mode)


def _planned_scope_status(packet: dict[str, Any]) -> str:
    metadata = packet.get("packet") or {}
    if isinstance(metadata, dict) and metadata.get("retrieval_reason") == "model_planner_selected":
        selected_tokens = int(metadata.get("retrieval_selected_tokens") or 0)
        metric_count = int(metadata.get("retrieval_metric_count") or 0)
        domains = ", ".join(str(value) for value in (packet.get("domains") or {})) or "—"
        return f"AI SELECTED · {domains} · {metric_count} metrics · ~{selected_tokens} evidence tokens"
    return _ORIGINAL_SCOPE_STATUS(packet)


_ORIGINAL_CLASSIFIER = ai_adaptive_retrieval_v2.classify_request_v2
_ORIGINAL_SCOPE_STATUS = ai_retrieval_scope_status.evidence_scope_status
_INSTALLED = False


def install_ai_query_planner(main_window_module) -> None:
    """Install planner-first chat and remove the obsolete AI-only period selector."""

    global _INSTALLED
    if _INSTALLED:
        return

    main_window_module.AIChatWindow = PlannedAIChatWindow
    window_class = main_window_module.MainWindow
    original_build_ai_page = window_class._build_ai_page

    def build_ai_page(window):
        page = original_build_ai_page(window)
        if hasattr(window, "ai_question_period_label"):
            window.ai_question_period_label.setVisible(False)
        if hasattr(window, "ai_range_combo"):
            window.ai_range_combo.setVisible(False)
        if hasattr(window, "ai_interval_label"):
            window.ai_interval_label.setText(
                _(
                    "Automatic AI data selection · metrics and time window are chosen separately for each question."
                )
            )
        return page

    def update_ai_interval_label(window) -> None:
        if hasattr(window, "ai_interval_label"):
            window.ai_interval_label.setText(
                _(
                    "Automatic AI data selection · metrics and time window are chosen separately for each question."
                )
            )

    window_class._build_ai_page = build_ai_page
    window_class._update_ai_interval_label = update_ai_interval_label
    ai_adaptive_retrieval_v2.classify_request_v2 = _planned_classifier
    ai_retrieval_scope_status.evidence_scope_status = _planned_scope_status
    _INSTALLED = True
