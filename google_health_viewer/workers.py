from __future__ import annotations

import traceback
from datetime import date, datetime, timedelta

from PySide6.QtCore import QSettings, QThread, Signal

from .ai_hardware import reasoning_value
from .api import ApiError, GoogleHealthClient
from .constants import DATA_TYPES
from .i18n import _
from .local_ai import AIAnalysisCancelled, LocalAIError, OllamaClient
from .oauth import CredentialStore, OAuthError, authenticate
from .self_update import UpdateTarget, install_update
from .storage import HealthStore
from .updates import fetch_latest_release

FILTER_REPAIR_VERSION = "snake-case-filters-v1"


class AuthThread(QThread):
    succeeded = Signal(object, bool)
    failed = Signal(str)
    url_ready = Signal(str)

    def __init__(self, store: CredentialStore, scopes: list[str]) -> None:
        super().__init__()
        self.store = store
        self.scopes = scopes

    def run(self) -> None:
        try:
            credentials = authenticate(self.store.client_file, self.scopes, self.url_ready.emit)
            secure = self.store.save_credentials(credentials)
            self.succeeded.emit(credentials, secure)
        except OAuthError as exc:
            self.failed.emit(str(exc))
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())


class SyncThread(QThread):
    progress = Signal(int, int, str)
    type_done = Signal(str, int)
    completed = Signal(int, int)
    failed = Signal(str)
    warning = Signal(str, str)

    def __init__(
        self,
        credentials,
        store: HealthStore,
        credential_store: CredentialStore,
        start: date,
        end: date,
        include_resources: bool = True,
    ) -> None:
        super().__init__()
        self.credentials = credentials
        self.store = store
        self.credential_store = credential_store
        self.start_date = start
        self.end_date = end
        self.include_resources = include_resources
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        client = GoogleHealthClient(self.credentials)
        sync_types = [spec for spec in DATA_TYPES if spec.auto_sync]
        success = 0
        errors = 0
        today = datetime.now().astimezone().date()
        try:
            if self.include_resources:
                self.progress.emit(0, len(sync_types) + 1, _("Profile, settings, and devices"))
                for key, payload in client.get_resources(self._is_cancelled).items():
                    self.store.save_resource(key, payload)
            for spec in DATA_TYPES:
                if not spec.auto_sync:
                    self.store.set_sync_status(
                        spec.key,
                        "skipped",
                        _("Reference catalogue not downloaded automatically."),
                    )
            for index, spec in enumerate(sync_types, start=1):
                if self._cancel:
                    break
                self.progress.emit(index, len(sync_types) + 1, spec.label)
                count = 0
                try:
                    repair_key = f"{FILTER_REPAIR_VERSION}:{spec.key}"
                    needs_filter_repair = bool(
                        spec.operation == "list"
                        and spec.filter_field
                        and "_" in spec.filter_field.split(".", 1)[0]
                        and not self.store.has_app_marker(repair_key)
                    )
                    if needs_filter_repair:
                        archive_bounds = self.store.data_date_bounds()
                        repair_start = (
                            min(self.start_date, archive_bounds[0])
                            if archive_bounds
                            else self.start_date
                        )
                        ranges = [(repair_start, self.end_date)]
                    else:
                        ranges = self.store.missing_sync_ranges(
                            spec.key,
                            self.start_date,
                            self.end_date,
                            refresh_date=today,
                        )
                    for range_start, range_end in ranges:
                        iterator = (
                            client.iter_daily_rollups(
                                spec, range_start, range_end, self._is_cancelled
                            )
                            if spec.operation == "daily_rollup"
                            else client.iter_data_pages(
                                spec, range_start, range_end, self._is_cancelled
                            )
                        )
                        for page in iterator:
                            count += self.store.upsert_records(
                                spec.key,
                                page,
                                (
                                    "daily_rollup"
                                    if spec.operation == "daily_rollup"
                                    else "data_point"
                                ),
                            )
                        # Today is intentionally left open: wearable data can still arrive.
                        stable_end = min(range_end, today - timedelta(days=1))
                        self.store.mark_sync_range(spec.key, range_start, stable_end)
                    if needs_filter_repair:
                        self.store.set_app_marker(repair_key)
                    message = (
                        (
                            _(
                                "{count} records received · compatibility recovery completed",
                                count=count,
                            )
                            if needs_filter_repair
                            else _(
                                "{count} records received in {ranges} missing ranges",
                                count=count,
                                ranges=len(ranges),
                            )
                        )
                        if ranges
                        else _("Already up to date: no historical ranges are missing")
                    )
                    self.store.set_sync_status(spec.key, "ok", message)
                    success += 1
                except ApiError as exc:
                    if exc.status == 499:
                        break
                    if exc.status == 401:
                        raise
                    self.store.set_sync_status(spec.key, "error", str(exc))
                    self.warning.emit(spec.label, str(exc))
                    errors += 1
                except Exception as exc:  # noqa: BLE001 - a category must not stop the rest.
                    message = _("Unexpected error: {error}", error=exc)
                    self.store.set_sync_status(spec.key, "error", message)
                    self.warning.emit(spec.label, message)
                    errors += 1
                self.type_done.emit(spec.key, count)
            self.credential_store.save_credentials(self.credentials)
            self.completed.emit(success, errors)
        except ApiError as exc:
            self.failed.emit(str(exc))
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())


class AIStatusThread(QThread):
    completed = Signal(object)

    def __init__(self, model: str, hardware_profile: str) -> None:
        super().__init__()
        self.model = model
        self.hardware_profile = hardware_profile

    def run(self) -> None:
        self.completed.emit(
            OllamaClient(model=self.model, hardware_profile=self.hardware_profile).status()
        )


class UpdateCheckThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self.current_version = current_version

    def run(self) -> None:
        try:
            self.completed.emit(fetch_latest_release(self.current_version))
        except Exception as exc:  # noqa: BLE001 - thread boundary reports network failures.
            self.failed.emit(str(exc))


class AppUpdateThread(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, release, target: UpdateTarget) -> None:
        super().__init__()
        self.release = release
        self.target = target

    def run(self) -> None:
        try:
            self.completed.emit(
                install_update(self.release, self.target, self.progress.emit)
            )
        except Exception as exc:  # noqa: BLE001 - thread boundary reports failures.
            self.failed.emit(str(exc))


class AIPullThread(QThread):
    progress = Signal(str)
    completed = Signal()
    failed = Signal(str)

    def __init__(self, model: str) -> None:
        super().__init__()
        self.model = model

    def run(self) -> None:
        try:
            OllamaClient(model=self.model).pull(self.progress.emit)
            self.completed.emit()
        except LocalAIError as exc:
            self.failed.emit(str(exc))
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())


class AIAnalysisThread(QThread):
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    thinking_chunk = Signal(str)
    answer_chunk = Signal(str)
    prompt_ready = Signal(str)

    def __init__(
        self,
        model: str,
        snapshot: dict,
        question: str,
        max_tokens: int = 3200,
        model_context_limit: int | None = None,
        history: list[dict[str, str]] | None = None,
        analysis_mode: str = "question",
    ) -> None:
        super().__init__()
        self.model = model
        self.snapshot = snapshot
        self.question = question
        self.max_tokens = max_tokens
        self.model_context_limit = model_context_limit
        self.history = history or []
        self.analysis_mode = analysis_mode

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            profile = str(QSettings().value("ai/performance_profile", "standard") or "standard")
            configured_reasoning = reasoning_value(self.model, profile)
            client = OllamaClient(model=self.model)
            original_chat_stream = client._chat_stream

            def profile_chat_stream(messages, *, think, **kwargs):
                # GPT-OSS requires an explicit low/medium/high value even in the
                # evidence-preserving retry. Boolean-thinking models keep the
                # retry's deliberate think=False behaviour.
                if isinstance(configured_reasoning, str):
                    resolved_think = configured_reasoning
                else:
                    resolved_think = configured_reasoning if think else False
                return original_chat_stream(
                    messages,
                    think=resolved_think,
                    **kwargs,
                )

            client._chat_stream = profile_chat_stream
            answer = client.analyze_stream(
                self.snapshot,
                self.question,
                self.thinking_chunk.emit,
                self.answer_chunk.emit,
                self.max_tokens,
                self.model_context_limit,
                history=self.history,
                analysis_mode=self.analysis_mode,
                cancel_callback=self.isInterruptionRequested,
                prompt_callback=self.prompt_ready.emit,
            )
            self.completed.emit(answer)
        except AIAnalysisCancelled:
            self.cancelled.emit()
        except LocalAIError as exc:
            self.failed.emit(str(exc))
        except Exception:  # noqa: BLE001 - thread boundary reports unexpected failures.
            self.failed.emit(traceback.format_exc())
