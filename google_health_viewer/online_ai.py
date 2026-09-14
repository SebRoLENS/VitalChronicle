"""Optional OpenAI-compatible online providers and explicit consent UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
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
GROQ_MODEL = "groq:openai/gpt-oss-20b"
GROQ_LARGE_MODEL = "groq:openai/gpt-oss-120b"
ONLINE_MODELS = (MISTRAL_MODEL, GROQ_MODEL, GROQ_LARGE_MODEL)


@dataclass(frozen=True)
class OnlineProvider:
    key: str
    name: str
    api_url: str
    setup_url: str
    api_key_setting: str
    consent_setting: str
    model_prefix: str = ""


MISTRAL_PROVIDER = OnlineProvider(
    key="mistral",
    name="Mistral",
    api_url="https://api.mistral.ai/v1/chat/completions",
    setup_url="https://console.mistral.ai/home",
    api_key_setting="ai/mistral_api_key",
    consent_setting="ai/mistral_online_consent",
)
GROQ_PROVIDER = OnlineProvider(
    key="groq",
    name="Groq",
    api_url="https://api.groq.com/openai/v1/chat/completions",
    setup_url="https://console.groq.com/keys",
    api_key_setting="ai/groq_api_key",
    consent_setting="ai/groq_online_consent",
    model_prefix="groq:",
)
ONLINE_PROVIDERS = (MISTRAL_PROVIDER, GROQ_PROVIDER)

# Backwards-compatible names used by existing integrations.
MISTRAL_API_URL = MISTRAL_PROVIDER.api_url
MISTRAL_KEYS_URL = MISTRAL_PROVIDER.setup_url
MISTRAL_API_KEY_SETTING = MISTRAL_PROVIDER.api_key_setting
MISTRAL_CONSENT_SETTING = MISTRAL_PROVIDER.consent_setting


def online_provider_for_model(model: str) -> OnlineProvider | None:
    value = model.strip().lower()
    if value == MISTRAL_MODEL:
        return MISTRAL_PROVIDER
    for provider in ONLINE_PROVIDERS:
        if provider.model_prefix and value.startswith(provider.model_prefix):
            return provider
    return None


def online_model_id(model: str) -> str:
    provider = online_provider_for_model(model)
    if provider and provider.model_prefix:
        return model.strip()[len(provider.model_prefix) :]
    return model.strip()


def is_online_model(model: str) -> bool:
    return online_provider_for_model(model) is not None


def is_mistral_model(model: str) -> bool:
    provider = online_provider_for_model(model)
    return provider is not None and provider.key == "mistral"


def is_groq_model(model: str) -> bool:
    provider = online_provider_for_model(model)
    return provider is not None and provider.key == "groq"


def online_api_key(
    model_or_provider: str | OnlineProvider, settings: QSettings | None = None
) -> str:
    provider = (
        model_or_provider
        if isinstance(model_or_provider, OnlineProvider)
        else online_provider_for_model(model_or_provider)
    )
    if provider is None:
        return ""
    store = settings or QSettings()
    return str(store.value(provider.api_key_setting, "") or "").strip()


def has_online_consent(
    model_or_provider: str | OnlineProvider, settings: QSettings | None = None
) -> bool:
    provider = (
        model_or_provider
        if isinstance(model_or_provider, OnlineProvider)
        else online_provider_for_model(model_or_provider)
    )
    if provider is None:
        return False
    store = settings or QSettings()
    return bool(store.value(provider.consent_setting, False, type=bool))


def mistral_api_key(settings: QSettings | None = None) -> str:
    return online_api_key(MISTRAL_PROVIDER, settings)


def has_mistral_consent(settings: QSettings | None = None) -> bool:
    return has_online_consent(MISTRAL_PROVIDER, settings)


def provider_display_name(model: str) -> str:
    provider = online_provider_for_model(model)
    return provider.name if provider else "Ollama"


def provider_activity_name(model: str) -> str:
    return provider_display_name(model) if is_online_model(model) else _("the local model")


class OnlineAISetupDialog(QDialog):
    """Guide API-key creation and collect explicit online-data consent."""

    def __init__(self, provider: OnlineProvider, parent=None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.settings = QSettings()
        self.setWindowTitle(_("{provider} online AI", provider=provider.name))
        self.setMinimumWidth(650)

        root = QVBoxLayout(self)
        title = QLabel(_("Use {provider} online", provider=provider.name))
        title.setObjectName("pageTitle")
        root.addWidget(title)

        intro = QLabel(
            _(
                "{provider} will receive the question and the minimum deterministic health "
                "evidence needed for the answer. This information leaves your computer.",
                provider=provider.name,
            )
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        steps = QLabel(
            _(
                "1. Open the {provider} API keys page.\n"
                "2. Sign in or create an account.\n"
                "3. Create a key, copy it, and paste it below.\n"
                "4. Do not share the key with anyone.",
                provider=provider.name,
            )
        )
        steps.setWordWrap(True)
        root.addWidget(steps)

        links = QHBoxLayout()
        docs = QPushButton(_("Open {provider} console", provider=provider.name))
        docs.clicked.connect(lambda: open_external_url(provider.setup_url))
        links.addWidget(docs)
        links.addStretch()
        root.addLayout(links)

        root.addWidget(QLabel(_("{provider} API key", provider=provider.name)))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setText(online_api_key(provider, self.settings))
        self.key_edit.setPlaceholderText(_("Paste your API key here"))
        root.addWidget(self.key_edit)

        privacy = QLabel(
            _(
                "By continuing, you acknowledge that the selected question, health evidence, "
                "and conversation context may be sent to {provider}. The key and the conversation "
                "remain stored locally by VitalChronicle.",
                provider=provider.name,
            )
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("disclaimer")
        root.addWidget(privacy)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_edit.setFocus()
            return
        self.settings.setValue(self.provider.api_key_setting, key)
        self.settings.setValue(self.provider.consent_setting, True)
        self.accept()


class MistralSetupDialog(OnlineAISetupDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(MISTRAL_PROVIDER, parent)


class GroqSetupDialog(OnlineAISetupDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(GROQ_PROVIDER, parent)


def setup_dialog_for_model(model: str, parent=None) -> OnlineAISetupDialog:
    provider = online_provider_for_model(model)
    if provider is None:
        raise ValueError(f"No online provider for model {model!r}")
    return OnlineAISetupDialog(provider, parent)


def _response_error(response: requests.Response, provider: OnlineProvider) -> LocalAIError:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = {}
    detail: Any = payload.get("message") if isinstance(payload, dict) else None
    nested = payload.get("error") if isinstance(payload, dict) else None
    if not detail and isinstance(nested, dict):
        detail = nested.get("message") or nested.get("type") or nested.get("code")
    elif not detail and nested:
        detail = nested
    detail = str(detail or response.reason or f"HTTP {response.status_code}")

    diagnostics: list[str] = [f"HTTP {response.status_code}"]
    for header, label in (
        ("retry-after", "retry-after"),
        ("x-ratelimit-remaining-requests", "requests remaining"),
        ("x-ratelimit-remaining-tokens", "tokens remaining"),
        ("x-ratelimit-reset-requests", "request reset"),
        ("x-ratelimit-reset-tokens", "token reset"),
    ):
        value = response.headers.get(header)
        if value:
            diagnostics.append(f"{label}: {value}")
    if response.status_code == 429 and len(diagnostics) == 1:
        diagnostics.append("check the provider usage and limits page")
    return LocalAIError(
        _(
            "{provider} request failed: {detail} ({diagnostics})",
            provider=provider.name,
            detail=detail,
            diagnostics="; ".join(diagnostics),
        )
    )


def online_chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int,
    temperature: float,
    timeout: tuple[int, int] = (15, 900),
) -> dict[str, Any]:
    provider = online_provider_for_model(model)
    if provider is None:
        raise LocalAIError(_("Unknown online AI provider."))
    key = online_api_key(provider)
    if not key:
        raise LocalAIError(_("{provider} API key is not configured.", provider=provider.name))
    request_body: dict[str, Any] = {
        "model": online_model_id(model),
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        request_body.update({"tools": tools, "tool_choice": "auto"})
    try:
        response = requests.post(
            provider.api_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LocalAIError(
            _("{provider} request failed: {detail}", provider=provider.name, detail=exc)
        ) from exc
    if response.status_code >= 400:
        raise _response_error(response, provider)
    try:
        payload = response.json()
    except ValueError as exc:
        raise LocalAIError(
            _("{provider} returned invalid JSON.", provider=provider.name)
        ) from exc
    if not isinstance(payload, dict):
        raise LocalAIError(_("{provider} returned invalid JSON.", provider=provider.name))
    return payload


class OpenAICompatibleClient(OptimizedOllamaClient):
    """Run the deterministic evidence pipeline through an online compatible API."""

    def __init__(self, *args, api_key: str = "", **kwargs) -> None:
        model = str(kwargs.get("model") or (args[0] if args else ""))
        self.provider = online_provider_for_model(model)
        if self.provider is None:
            raise ValueError(f"No online provider for model {model!r}")
        self.api_key = api_key.strip() or online_api_key(self.provider)
        super().__init__(*args, **kwargs)

    def status(self) -> OllamaStatus:
        configured = bool(self.api_key)
        return OllamaStatus(
            # The remote provider exists even before its optional key is configured;
            # this keeps the generic status UI out of the Ollama-unreachable branch.
            online=True,
            models=(self.model,) if configured else (),
            message=(
                _("{provider} online AI is ready.", provider=self.provider.name)
                if configured
                else _("{provider} API key not configured.", provider=self.provider.name)
            ),
            catalog_models=tuple(
                model
                for model in ONLINE_MODELS
                if online_provider_for_model(model) == self.provider
            ),
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
            raise LocalAIError(
                _("{provider} API key is not configured.", provider=self.provider.name)
            )
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
                self.provider.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": online_model_id(self.model),
                    "messages": messages,
                    "stream": True,
                    "max_tokens": num_predict,
                    "temperature": 0.2,
                },
                stream=True,
                timeout=(15, 900),
            ) as response:
                if response.status_code >= 400:
                    raise _response_error(response, self.provider)
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
            raise LocalAIError(
                _("{provider} request failed: {detail}", provider=self.provider.name, detail=exc)
            ) from exc


class MistralClient(OpenAICompatibleClient):
    """Backwards-compatible Mistral client name."""


class GroqClient(OpenAICompatibleClient):
    """Groq client using the shared OpenAI-compatible transport."""


def online_client(model: str, *, performance_profile: str = "standard") -> OpenAICompatibleClient:
    return OpenAICompatibleClient(model=model, performance_profile=performance_profile)
