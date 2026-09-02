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

PLANNER_VERSION = "ai-data-planner-v1"
MAX_SELECTED_DATA_TYPES = 8
MAX_LOOKBACK_DAYS = 3650
PLANNER_OUTPUT_TOKENS = 560

PLANNER_SYSTEM_PROMPT = """You are VitalChronicle's local data-request planner.
You do not analyse health values and you do not answer the user's health question.
You receive only metadata describing which health datasets exist locally. Choose the
smallest sufficient set of data and time range that Python should prepare for a second,
separate analysis call.

Return exactly one JSON object and no prose or markdown:
{
  "data_types": ["catalog-key", "catalog-key"],
  "window": "last_n_days" | "all_history" | "date_range",
  "days": 30,
  "start_date": null,
  "end_date": null,
  "detail": "daily" | "intraday" | "events" | "summary",
  "reason": "short explanation"
}

Rules:
- data_types may contain only exact keys present in the catalogue.
- Select at most 8 data types and prefer fewer when they are sufficient.
- Choose the time span from the scientific meaning of the question, not from a UI preset.
- For a current/single-day question, usually request a few recent days for context.
- For a short trend, usually request weeks; for correlations or personal baselines,
  usually request enough matched days (often 60-90 when available).
- Long-term change questions may justify months or all available history.
- Include supporting data only when it can materially help answer the question.
- Use date_range only when the question explicitly identifies a calendar period.
- end_date is inclusive. For relative windows, anchor to the latest locally available
  date in the selected datasets.
- Missing catalogue coverage is not zero and must not be invented.
- Conversation excerpts are context only and never health evidence.
"""


def build_data_catalog(store) -> dict[str, Any]:
    """Return metadata-only coverage for datasets that actually exist locally."""

    with store._connect() as db:  # noqa: SLF001 - the store owns the local DB boundary.
        rows = db.execute(
            """
            SELECT data_type,
                   COUNT(*) AS record_count,
                   MIN(substr(COALESCE(start_time, end_time), 1, 10)) AS first_date,
                   MAX(substr(COALESCE(start_time, end_time), 1, 10)) AS last_date
            FROM records
            WHERE COALESCE(start_time, end_time) IS NOT NULL
            GROUP BY data_type
            ORDER BY data_type
            """
        ).fetchall()

    datasets: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["data_type"])
        first_date = str(row["first_date"] or "")
        last_date = str(row["last_date"] or "")
        if not first_date or not last_date:
            continue
        spec = DATA_TYPE_BY_KEY.get(key)
        datasets.append(
            {
                "key": key,
                "label": str(spec.label if spec else key.replace("-", " ").title()),
                "category": str(spec.category if spec else "Other"),
                "first_date": first_date,
                "last_date": last_date,
                "record_count": int(row["record_count"] or 0),
            }
        )

    first = min((item["first_date"] for item in datasets), default=None)
    last = max((item["last_date"] for item in datasets), default=None)
    return {
        "catalog_version": PLANNER_VERSION,
        "local_date": datetime.now().astimezone().date().isoformat(),
        "archive_first_date": first,
        "archive_last_date": last,
        "datasets": datasets,
    }


def _history_excerpt(history: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in history[-6:]:
        role = str(item.get("role") or "")
        content = " ".join(str(item.get("content") or "").split())
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content[:600]})
    return result


def _planner_messages(
    catalog: dict[str, Any], question: str, history: list[dict[str, str]]
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "conversation_context": _history_excerpt(history),
        "available_local_data": catalog,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner did not return a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("planner response is not an object")
    return parsed


def _catalog_rows(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("key")): item
        for item in catalog.get("datasets", [])
        if isinstance(item, dict) and item.get("key")
    }


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _fallback_types(catalog: dict[str, Any]) -> list[str]:
    available = _catalog_rows(catalog)
    preferred = (
        "sleep",
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
        "heart-rate",
        "steps",
        "exercise",
        "daily-oxygen-saturation",
        "weight",
    )
    selected = [key for key in preferred if key in available]
    if selected:
        return selected[:MAX_SELECTED_DATA_TYPES]
    return list(available)[:MAX_SELECTED_DATA_TYPES]


def fallback_data_plan(
    catalog: dict[str, Any], *, all_history: bool = False, reason: str = "planner_fallback"
) -> dict[str, Any]:
    """Return a broad but bounded plan when model planning is unavailable or invalid."""

    return resolve_data_plan(
        {
            "data_types": list(_catalog_rows(catalog)) if all_history else _fallback_types(catalog),
            "window": "all_history" if all_history else "last_n_days",
            "days": 90,
            "detail": "summary",
            "reason": reason,
        },
        catalog,
        force_all=all_history,
    )


def resolve_data_plan(
    raw_plan: dict[str, Any],
    catalog: dict[str, Any],
    *,
    force_all: bool = False,
) -> dict[str, Any]:
    """Validate model output and resolve it to an exact, bounded local query."""

    available = _catalog_rows(catalog)
    if not available:
        raise ValueError("no local health data are available")

    requested = raw_plan.get("data_types")
    requested_keys = requested if isinstance(requested, list) else []
    selected: list[str] = []
    for value in requested_keys:
        key = str(value)
        if key in available and key not in selected:
            selected.append(key)
        if len(selected) >= MAX_SELECTED_DATA_TYPES:
            break

    if force_all:
        selected = list(available)
    elif not selected:
        selected = _fallback_types(catalog)
    if not selected:
        raise ValueError("the planner did not select any available data")

    selected_rows = [available[key] for key in selected]
    first_available = min(
        day for row in selected_rows if (day := _parse_day(row.get("first_date"))) is not None
    )
    last_available = max(
        day for row in selected_rows if (day := _parse_day(row.get("last_date"))) is not None
    )

    window = str(raw_plan.get("window") or "last_n_days").strip().lower()
    if force_all:
        window = "all_history"

    if window == "all_history":
        start_day = first_available
        end_day = last_available
    elif window == "date_range":
        requested_start = _parse_day(raw_plan.get("start_date"))
        requested_end = _parse_day(raw_plan.get("end_date"))
        if requested_start is None or requested_end is None:
            window = "last_n_days"
        else:
            start_day = max(first_available, requested_start)
            end_day = min(last_available, requested_end)
            if start_day > end_day:
                window = "last_n_days"
    if window not in {"all_history", "date_range"}:
        try:
            days = int(raw_plan.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(MAX_LOOKBACK_DAYS, days))
        end_day = last_available
        start_day = max(first_available, end_day - timedelta(days=days - 1))
        window = "last_n_days"

    detail = str(raw_plan.get("detail") or "summary").lower()
    if detail not in {"daily", "intraday", "events", "summary"}:
        detail = "summary"
    actual_days = max(1, (end_day - start_day).days + 1)
    reason = " ".join(str(raw_plan.get("reason") or "").split())[:240]
    selected_labels = [str(available[key].get("label") or key) for key in selected]
    return {
        "planner_version": PLANNER_VERSION,
        "data_types": selected,
        "data_labels": selected_labels,
        "window": window,
        "days": actual_days,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "end_exclusive": (end_day + timedelta(days=1)).isoformat(),
        "detail": detail,
        "reason": reason or "model_selected_local_evidence",
    }


class SelectedHealthStore:
    """Proxy that prevents the deterministic pipeline from reading unrequested types."""

    def __init__(self, store, allowed_data_types: list[str]) -> None:
        self._store = store
        self._allowed = frozenset(allowed_data_types)

    def list_records(self, data_type: str, *args, **kwargs):
        if data_type not in self._allowed:
            return []
        return self._store.list_records(data_type, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def build_planned_snapshot(store, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fulfil a validated AI request using only selected local record types."""

    selected = [str(value) for value in plan.get("data_types") or []]
    if not selected:
        raise ValueError("planned query contains no data types")
    proxy = SelectedHealthStore(store, selected)
    days = max(1, int(plan.get("days") or 1))
    # The planner reduces breadth first; this backend ceiling prevents pathological
    # intraday archives from exhausting RAM without imposing a user-facing time limit.
    if days <= 7:
        record_limit = 350_000
    elif days <= 90:
        record_limit = 250_000
    else:
        record_limit = 150_000
    snapshot = build_ai_ready_snapshot(
        proxy,
        str(plan["start_date"]),
        str(plan["end_exclusive"]),
        record_limit=record_limit,
    )
    snapshot["analysis_scope"] = "ai_planned"
    snapshot["ai_data_request"] = dict(plan)
    start_day = date.fromisoformat(str(plan["start_date"]))
    end_day = date.fromisoformat(str(plan["end_date"]))
    period = {
        "preset": "ai_planned",
        "label": f"AI · {start_day.strftime('%d/%m/%Y')}–{end_day.strftime('%d/%m/%Y')}",
        "start": str(plan["start_date"]),
        "end": str(plan["end_exclusive"]),
        "display_start": start_day.strftime("%d/%m/%Y"),
        "display_end": end_day.strftime("%d/%m/%Y"),
        "selected_data_types": selected,
        "planner_reason": str(plan.get("reason") or ""),
    }
    return snapshot, period


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
