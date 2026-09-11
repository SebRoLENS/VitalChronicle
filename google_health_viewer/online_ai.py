"""Optional online Mistral provider and explicit first-use consent UI."""

from __future__ import annotations

import json
from typing import Any

import requests
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .ai_engine import OptimizedOllamaClient
from .external_links import open_external_url
from .i18n import _
from .local_ai import LocalAIError, OllamaStatus

MISTRAL_MODEL = "mistral-small-latest"
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_KEYS_URL = "https://console.mistral.ai/api-keys/"
MISTRAL_API_KEY_SETTING = "ai/mistral_api_key"
MISTRAL_CONSENT_SETTING = "ai/mistral_online_consent"


def is_mistral_model(model: str) -> bool:
    return model.strip().lower() == MISTRAL_MODEL


def mistral_api_key(settings: QSettings | None = None) -> str:
    store = settings or QSettings()
    return str(store.value(MISTRAL_API_KEY_SETTING, "") or "").strip()


def has_mistral_consent(settings: QSettings | None = None) -> bool:
    store = settings or QSettings()
    return bool(store.value(MISTRAL_CONSENT_SETTING, False, type=bool))


class MistralSetupDialog(QDialog):
    """Guide key creation and collect explicit permission for online health analysis."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Mistral online AI"))
        self.setMinimumWidth(650)
        self.settings = QSettings()

        root = QVBoxLayout(self)
        title = QLabel(_("Use Mistral online"))
        title.setObjectName("pageTitle")
        root.addWidget(title)

        intro = QLabel(
            _(
                "Mistral will receive the question and the minimum deterministic health "
                "evidence needed for the answer. This information leaves your computer."
            )
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        steps = QLabel(
            _(
                "1. Open the Mistral API keys page.\n"
                "2. Sign in or create a Mistral account.\n"
                "3. Create a key, copy it, and paste it below.\n"
                "4. Do not share the key with anyone."
            )
        )
        steps.setWordWrap(True)
        root.addWidget(steps)

        links = QHBoxLayout()
        docs = QPushButton(_("Open Mistral API keys"))
        docs.clicked.connect(lambda: open_external_url(MISTRAL_KEYS_URL))
        links.addWidget(docs)
        links.addStretch()
        root.addLayout(links)

        root.addWidget(QLabel(_("Mistral API key")))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setText(mistral_api_key(self.settings))
        self.key_edit.setPlaceholderText(_("Paste your API key here"))
        root.addWidget(self.key_edit)

        privacy = QLabel(
            _(
                "By continuing, you acknowledge that the selected question, health "
                "evidence, and conversation context may be sent to Mistral. The key and "
                "the conversation remain stored locally by VitalChronicle."
            )
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("disclaimer")
        root.addWidget(privacy)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_edit.setFocus()
            return
        self.settings.setValue(MISTRAL_API_KEY_SETTING, key)
        self.settings.setValue(MISTRAL_CONSENT_SETTING, True)
        self.accept()


class MistralClient(OptimizedOllamaClient):
    """Use the existing evidence pipeline with Mistral's OpenAI-compatible endpoint."""

    def __init__(self, *args, api_key: str = "", **kwargs) -> None:
        self.api_key = api_key.strip()
        super().__init__(*args, **kwargs)

    def status(self) -> OllamaStatus:
        if not self.api_key:
            return OllamaStatus(
                online=False,
                models=(),
                message=_("Mistral API key not configured."),
                catalog_models=(MISTRAL_MODEL,),
            )
        return OllamaStatus(
            online=True,
            models=(MISTRAL_MODEL,),
            message=_("Mistral online AI is ready."),
            catalog_models=(MISTRAL_MODEL,),
            model_context_limit=None,
        )

    def _chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        think: bool | str,
        num_predict: int,
        num_ctx: int,
        thinking_callback,
        answer_callback,
        cancel_callback=None,
    ) -> str:
        if not self.api_key:
            raise LocalAIError(_("Mistral API key is not configured."))

        self._telemetry_started_at = __import__("time").monotonic()
        self._telemetry_characters = 0
        self._telemetry_input_tokens = 0
        self._telemetry_context = max(1, int(num_ctx))
        self._telemetry_output_budget = max(1, int(num_predict))
        self._telemetry_last_emit = 0.0
        self._call_stats.append(
            {
                "call": len(self._call_stats) + 1,
                "phase": self._current_phase,
                "elapsed_seconds": 0.0,
                "prompt_tokens_reported": None,
                "generated_tokens": None,
                "tokens_per_second": None,
                "context": num_ctx,
                "output_budget": num_predict,
            }
        )

        answer_parts: list[str] = []
        try:
            with requests.post(
                MISTRAL_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": num_predict,
                    "temperature": 0.2,
                },
                stream=True,
                timeout=(15, 900),
            ) as response:
                if response.status_code >= 400:
                    try:
                        detail = response.json().get("message") or response.json().get("error")
                    except (TypeError, ValueError):
                        detail = None
                    raise LocalAIError(
                        _("Mistral request failed: {detail}", detail=detail or response.reason)
                    )
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if cancel_callback and cancel_callback():
                        response.close()
                        raise LocalAIError(_("Analysis stopped."))
                    if not line or not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if payload_text == "[DONE]":
                        break
                    try:
                        payload = json.loads(payload_text)
                    except ValueError:
                        continue
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        self._telemetry_characters += len(content)
                        answer_parts.append(content)
                        if answer_callback:
                            answer_callback(content)
            return "".join(answer_parts).strip()
        except requests.RequestException as exc:
            raise LocalAIError(_("Mistral request failed: {detail}", detail=exc)) from exc

    def analyze_stream(self, *args, **kwargs) -> str:
        return super().analyze_stream(*args, **kwargs)
