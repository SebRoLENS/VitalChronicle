from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .agent_runtime import CALIBRATION_VERSION, AgentRuntime, CalibrationThread
from .agent_tools import SafeToolExecutor
from .i18n import _


class CalibrationDialog(QDialog):
    """Data-first personalisation: calculate context, then ask only useful questions."""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        model: str,
        context_limit: int | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.model = model
        self.context_limit = context_limit
        self.thread: CalibrationThread | None = None
        self.context: dict[str, Any] = {}
        self.questions: list[dict[str, Any]] = []
        self.index = 0
        self.setWindowTitle(_("Personal AI calibration"))
        self.resize(760, 520)
        self.setMinimumSize(620, 420)
        self._build_ui()
        QTimer.singleShot(0, self._start)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel(_("Calibrate VitalChronicle to you"))
        title.setObjectName("pageTitle")
        root.addWidget(title)
        intro = QLabel(
            _(
                "VitalChronicle first reads deterministic personal baselines and data coverage. "
                "It then asks only questions whose answers can improve future personalisation."
            )
        )
        intro.setObjectName("pageSubtitle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.status = QLabel(_("Preparing personal baselines…"))
        self.status.setObjectName("coverageNeutral")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.question_card = QFrame()
        self.question_card.setObjectName("aiCard")
        card = QVBoxLayout(self.question_card)
        self.counter = QLabel()
        self.counter.setObjectName("pageSubtitle")
        card.addWidget(self.counter)
        self.question_label = QLabel()
        self.question_label.setObjectName("chatSectionTitle")
        self.question_label.setWordWrap(True)
        card.addWidget(self.question_label)
        self.reason_label = QLabel()
        self.reason_label.setObjectName("pageSubtitle")
        self.reason_label.setWordWrap(True)
        card.addWidget(self.reason_label)
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText(_("Your answer is stored only on this computer."))
        self.answer.setMaximumHeight(150)
        card.addWidget(self.answer)
        root.addWidget(self.question_card, 1)
        self.question_card.setVisible(False)

        actions = QHBoxLayout()
        self.skip_button = QPushButton(_("Skip"))
        self.skip_button.clicked.connect(self._skip)
        self.save_button = QPushButton(_("Save and continue"))
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save)
        self.close_button = QPushButton(_("Stop"))
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        actions.addStretch()
        actions.addWidget(self.skip_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self.skip_button.setEnabled(False)
        self.save_button.setEnabled(False)

    def _start(self) -> None:
        self.thread = CalibrationThread(self.runtime, self.model, self.context_limit)
        self.thread.progress.connect(self.status.setText)
        self.thread.completed.connect(self._ready)
        self.thread.failed.connect(self._failed)
        self.thread.cancelled.connect(self.reject)
        self.thread.start()

    def reject(self) -> None:
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
        super().reject()

    def _ready(self, context: dict[str, Any], questions: list[dict[str, Any]]) -> None:
        self.context = context or {}
        self.questions = list(questions or [])
        if not self.context.get("available"):
            self.status.setText(_("No local health data are available for calibration yet."))
            self.close_button.setText(_("Close"))
            return
        if not self.questions:
            self.runtime.agent_store.mark_calibrated(CALIBRATION_VERSION)
            self.status.setText(
                _(
                    "Personal baselines were calculated. No additional questions are currently "
                    "needed for useful personalisation."
                )
            )
            self.close_button.setText(_("Close"))
            return
        self.status.setText(
            _(
                "Personal baselines are ready. The following questions were selected because "
                "they can reduce uncertainty in future analyses."
            )
        )
        self.question_card.setVisible(True)
        self.skip_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self._show_question()

    def _failed(self, message: str) -> None:
        self.status.setText(_("Calibration could not complete: {message}", message=message))
        self.close_button.setText(_("Close"))

    def _show_question(self) -> None:
        if self.index >= len(self.questions):
            self.runtime.agent_store.mark_calibrated(CALIBRATION_VERSION)
            self.question_card.setVisible(False)
            self.skip_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self.close_button.setText(_("Close"))
            self.status.setText(_("Personal AI calibration completed and saved locally."))
            return
        item = self.questions[self.index]
        self.counter.setText(
            _("Question {current} of {total}", current=self.index + 1, total=len(self.questions))
        )
        self.question_label.setText(str(item.get("question") or ""))
        self.reason_label.setText(str(item.get("reason") or ""))
        self.answer.clear()
        self.answer.setFocus()

    def _feedback_item(self) -> dict[str, Any]:
        item = self.questions[self.index]
        return self.runtime.agent_store.ask_feedback(
            str(item.get("question") or ""),
            reason=str(item.get("reason") or ""),
            learning_key=str(item.get("learning_key") or "adaptive_context"),
            context=item.get("context") if isinstance(item.get("context"), dict) else {},
        )

    def _save(self) -> None:
        text = self.answer.toPlainText().strip()
        if not text:
            self.answer.setFocus()
            return
        feedback = self._feedback_item()
        if feedback.get("feedback_id"):
            self.runtime.agent_store.answer_feedback(str(feedback["feedback_id"]), text)
        self.index += 1
        self._show_question()

    def _skip(self) -> None:
        feedback = self._feedback_item()
        if feedback.get("feedback_id"):
            self.runtime.agent_store.dismiss_feedback(str(feedback["feedback_id"]))
        self.index += 1
        self._show_question()


def _selected_payload(tree: QTreeWidget) -> dict[str, Any] | None:
    items = tree.selectedItems()
    if not items:
        return None
    value = items[0].data(0, Qt.UserRole)
    return value if isinstance(value, dict) else None


def refresh_personal_ai_page(window) -> None:
    runtime: AgentRuntime | None = getattr(window, "agent_runtime", None)
    if runtime is None or not hasattr(window, "agent_status_label"):
        return
    store = runtime.agent_store
    tools = store.list_tools(include_superseded=True)
    builtins = sum(item["kind"] == "builtin" for item in tools)
    learned = sum(item["kind"] == "learned" and item["status"] == "active" for item in tools)
    superseded = sum(item["kind"] == "learned" and item["status"] == "superseded" for item in tools)
    state = _("enabled") if runtime.enabled else _("disabled")
    window.agent_status_label.setText(
        _(
            "Personal agent {state} · {builtins} built-in tools · {learned} learned · "
            "{superseded} superseded",
            state=state,
            builtins=builtins,
            learned=learned,
            superseded=superseded,
        )
    )
    calibrated = store.calibration_version() >= CALIBRATION_VERSION
    window.agent_calibration_label.setText(
        _("Calibration complete") if calibrated else _("Calibration recommended")
    )

    window.agent_tools_tree.clear()
    for item in tools:
        replacement = str(item.get("replacement") or "")
        row = QTreeWidgetItem(
            [
                str(item["name"]),
                str(item["kind"]),
                str(item["capability"]),
                str(item["status"]),
                str(item["use_count"]),
                replacement,
            ]
        )
        row.setData(0, Qt.UserRole, item)
        window.agent_tools_tree.addTopLevelItem(row)

    window.agent_model_tree.clear()
    for item in store.user_model():
        row = QTreeWidgetItem(
            [
                str(item["statement"]),
                f"{float(item['confidence']) * 100:.0f}%",
                str(item["evidence_count"]),
                str(item["source"]),
            ]
        )
        row.setData(0, Qt.UserRole, item)
        window.agent_model_tree.addTopLevelItem(row)

    window.agent_events_list.clear()
    for item in store.recent_tool_events(80):
        when = str(item.get("created_at") or "").replace("T", " ")[:16]
        window.agent_events_list.addItem(f"{when} · {item.get('message', '')}")


def build_personal_ai_page(window, runtime: AgentRuntime) -> QWidget:
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(8, 16, 8, 8)
    root.setSpacing(10)

    title = QLabel(_("Personal AI"))
    title.setObjectName("pageTitle")
    root.addWidget(title)
    subtitle = QLabel(
        _(
            "A local agent can choose deterministic tools, reuse learned safe pipelines, and "
            "adapt future analyses to your personal responses. Health data remain read-only."
        )
    )
    subtitle.setObjectName("pageSubtitle")
    subtitle.setWordWrap(True)
    root.addWidget(subtitle)

    controls = QFrame()
    controls.setObjectName("aiCard")
    controls_layout = QHBoxLayout(controls)
    controls_layout.setContentsMargins(16, 12, 16, 12)
    window.agent_enabled_check = QCheckBox(_("Enable personal agent"))
    window.agent_enabled_check.setChecked(runtime.enabled)
    controls_layout.addWidget(window.agent_enabled_check)
    window.agent_status_label = QLabel()
    window.agent_status_label.setObjectName("pageSubtitle")
    controls_layout.addWidget(window.agent_status_label, 1)
    window.agent_calibration_label = QLabel()
    window.agent_calibration_label.setObjectName("chatBadge")
    controls_layout.addWidget(window.agent_calibration_label)
    calibrate = QPushButton(_("Calibrate…"))
    calibrate.setObjectName("primaryButton")
    controls_layout.addWidget(calibrate)
    reset = QPushButton(_("Reset personalisation…"))
    reset.setObjectName("dangerButton")
    controls_layout.addWidget(reset)
    root.addWidget(controls)

    tabs = QTabWidget()
    tools_page = QWidget()
    tools_layout = QVBoxLayout(tools_page)
    tools_hint = QLabel(
        _(
            "Built-in tools are versioned with VitalChronicle. Learned tools are declarative, "
            "sandboxed pipelines and are automatically superseded when an equivalent built-in "
            "capability becomes available."
        )
    )
    tools_hint.setWordWrap(True)
    tools_hint.setObjectName("pageSubtitle")
    tools_layout.addWidget(tools_hint)
    window.agent_tools_tree = QTreeWidget()
    window.agent_tools_tree.setHeaderLabels(
        [_("Tool"), _("Kind"), _("Capability"), _("Status"), _("Uses"), _("Replacement")]
    )
    window.agent_tools_tree.setAlternatingRowColors(True)
    tools_layout.addWidget(window.agent_tools_tree, 1)
    delete_tool = QPushButton(_("Delete selected learned tool"))
    tools_layout.addWidget(delete_tool)
    tabs.addTab(tools_page, _("Tool registry"))

    model_page = QWidget()
    model_layout = QVBoxLayout(model_page)
    model_hint = QLabel(
        _(
            "These are user-specific associations learned from your explicit feedback. They are "
            "context for personalisation, not medical facts or safety overrides."
        )
    )
    model_hint.setObjectName("pageSubtitle")
    model_hint.setWordWrap(True)
    model_layout.addWidget(model_hint)
    window.agent_model_tree = QTreeWidget()
    window.agent_model_tree.setHeaderLabels(
        [_("Learned about you"), _("Confidence"), _("Evidence"), _("Source")]
    )
    window.agent_model_tree.setAlternatingRowColors(True)
    model_layout.addWidget(window.agent_model_tree, 1)
    forget = QPushButton(_("Forget selected personal association"))
    model_layout.addWidget(forget)
    tabs.addTab(model_page, _("Learned about you"))

    activity_page = QWidget()
    activity_layout = QVBoxLayout(activity_page)
    activity_hint = QLabel(
        _("Local history of learned-tool creation, reuse, replacement and deletion."))
    activity_hint.setObjectName("pageSubtitle")
    activity_layout.addWidget(activity_hint)
    window.agent_events_list = QListWidget()
    activity_layout.addWidget(window.agent_events_list, 1)
    tabs.addTab(activity_page, _("Agent activity"))
    root.addWidget(tabs, 1)

    def toggle_agent(enabled: bool) -> None:
        runtime.set_enabled(enabled)
        refresh_personal_ai_page(window)

    def open_calibration() -> None:
        dialog = CalibrationDialog(
            runtime,
            model=window.ai_model_combo.currentText(),
            context_limit=getattr(window, "_model_token_limit", None),
            parent=window,
        )
        dialog.exec()
        refresh_personal_ai_page(window)

    def delete_selected_tool() -> None:
        item = _selected_payload(window.agent_tools_tree)
        if not item or item.get("kind") != "learned":
            return
        if QMessageBox.question(
            window,
            _("Delete learned tool"),
            _("Delete the learned tool {name}?", name=item["name"]),
        ) != QMessageBox.Yes:
            return
        runtime.agent_store.delete_learned_tool(str(item["name"]))
        refresh_personal_ai_page(window)

    def forget_selected() -> None:
        item = _selected_payload(window.agent_model_tree)
        if not item:
            return
        if QMessageBox.question(
            window,
            _("Forget personal association"),
            _("Forget this learned personal association?"),
        ) != QMessageBox.Yes:
            return
        runtime.agent_store.forget_user_model(str(item["key"]))
        refresh_personal_ai_page(window)

    def reset_personalisation() -> None:
        if QMessageBox.warning(
            window,
            _("Reset personal AI"),
            _(
                "This deletes learned tools, feedback and personal associations. Your health "
                "archive and AI conversations are not deleted. Continue?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        runtime.agent_store.clear()
        runtime.tools = SafeToolExecutor(runtime.health_store, runtime.agent_store)
        refresh_personal_ai_page(window)

    window.agent_enabled_check.toggled.connect(toggle_agent)
    calibrate.clicked.connect(open_calibration)
    delete_tool.clicked.connect(delete_selected_tool)
    forget.clicked.connect(forget_selected)
    reset.clicked.connect(reset_personalisation)
    refresh_personal_ai_page(window)
    return page
