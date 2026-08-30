from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_REGISTRY_URL = "https://registry.ollama.ai"
DEFAULT_MODEL = "qwen3.5:9b"
HARDWARE_PROFILE_LABELS = {
    "gpu16": "NVIDIA GPU · 16 GB RAM",
    "cpu32": "Solo CPU · 32 GB RAM",
}
MODEL_OPTIONS = (
    "qwen3.5:9b",
    "qwen3:14b",
    "qwen3:30b-a3b",
    "qwen3.5:27b",
    "qwen3.6:35b-a3b",
    "qwen3:8b",
    "qwen3:4b",
)
MODEL_DESCRIPTIONS = {
    "qwen3.5:9b": "Attuale ed efficiente · circa 6,6 GB · consigliato per RTX 4060/16 GB",
    "qwen3:14b": "Più grande · circa 9,3 GB · può usare insieme GPU e RAM",
    "qwen3:30b-a3b": "Alta qualità MoE · circa 19 GB · consigliato per CPU/32 GB",
    "qwen3.5:27b": "Dense 27B · circa 17 GB · accurato ma molto lento su CPU",
    "qwen3.6:35b-a3b": "Massimo MoE · circa 23 GB · sperimentale su 32 GB",
    "qwen3:8b": "Compatto · circa 5,2 GB · compatibilità",
    "qwen3:4b": "Leggero · circa 2,5 GB · compatibilità",
}


def recommended_model(profile: str) -> str:
    return "qwen3:30b-a3b" if profile == "cpu32" else DEFAULT_MODEL


def newer_model_suggestion(model: str, profile: str) -> str | None:
    """Return a newer generation that remains sensible for the selected hardware."""
    name, _, tag = model.strip().partition(":")
    if name == "qwen3":
        if profile == "cpu32":
            return "qwen3.6:35b-a3b" if tag in {"30b-a3b", "32b"} else "qwen3.5:27b"
        return "qwen3.5:9b"
    if name == "qwen3.5" and profile == "cpu32" and tag in {"27b", "35b", "35b-a3b"}:
        return "qwen3.6:35b-a3b"
    return None


def detected_hardware_profile(nvidia_available: bool | None = None) -> str:
    if nvidia_available is None:
        nvidia_available = shutil.which("nvidia-smi") is not None
    return "gpu16" if nvidia_available else "cpu32"


class LocalAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class OllamaStatus:
    online: bool
    models: tuple[str, ...]
    message: str
    update_available: bool = False
    update_message: str = ""
    update_target: str | None = None


@dataclass(frozen=True)
class TokenRecommendation:
    recommended_tokens: int
    recommended_context: int
    model_context_limit: int | None
    model_size_gb: float | None
    ram_gb: int


SYSTEM_PROMPT = """Sei il modulo locale di analisi di VitalChronicle.
Rispondi in italiano chiaro e conciso usando esclusivamente il riepilogo statistico fornito.
Separa osservazioni, possibili associazioni e limiti. Non inventare valori mancanti.
Le correlazioni non dimostrano causalità. Non formulare diagnosi, non modificare terapie e non
presentare soglie generiche come verità individuali. Quando un cambiamento potrebbe essere
clinicamente importante, suggerisci di discuterne con un professionista indicando quali dati
mostrare. Ricorda che i dati dei wearable possono contenere errori.
Rispetta sempre observation_context e temporal_context. I valori today_so_far appartengono a
una giornata ancora incompleta: non definirli sopra o sotto la media confrontandoli con giornate
intere. Se same_time_mean è disponibile, usa soltanto quel confronto e indica quanti giorni lo
compongono; altrimenti dichiara che è troppo presto per giudicare. Non considerare un dato
odierno assente come zero e non estrapolare linearmente un totale di metà giornata.
Quando analysis_scope è all_local_history, esamina tutte le voci di metrics e data_coverage,
inclusi additional_fields e structured_details. Non limitarti ai dati della Panoramica:
tratta esplicitamente sonno e fasi del sonno, allenamenti, attività, parametri vitali,
nutrizione/idratazione e dati cardiaci quando sono presenti. Se una categoria non è presente,
non inventarla.
"""


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        hardware_profile: str = "gpu16",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip() or DEFAULT_MODEL
        self.hardware_profile = hardware_profile

    @staticmethod
    def _normalized_digest(value: str | None) -> str:
        return (value or "").lower().removeprefix("sha256:")

    def _remote_model_digest(self) -> str | None:
        name, separator, tag = self.model.partition(":")
        if "/" in name or not name:
            return None
        tag = tag if separator and tag else "latest"
        url = (
            f"{OLLAMA_REGISTRY_URL}/v2/library/{quote(name, safe='')}/manifests/"
            f"{quote(tag, safe='')}"
        )
        response = requests.get(
            url,
            headers={
                "Accept": (
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                )
            },
            timeout=7,
        )
        response.raise_for_status()
        return response.headers.get("Docker-Content-Digest")

    def token_recommendation(self, ram_gb: int) -> TokenRecommendation:
        """Estimate a useful generation budget and respect model-declared context."""
        if ram_gb <= 0:
            raise LocalAIError("Inserisci una quantità di RAM positiva.")
        try:
            show_response = requests.post(
                f"{self.base_url}/api/show",
                json={"model": self.model, "verbose": False},
                timeout=6,
            )
            show_response.raise_for_status()
            show_payload = show_response.json()
            model_info = show_payload.get("model_info") or {}
            context_limits = []
            if isinstance(model_info, dict):
                for key, value in model_info.items():
                    if str(key).lower().endswith(".context_length"):
                        try:
                            parsed = int(value)
                        except (TypeError, ValueError):
                            continue
                        if parsed > 0:
                            context_limits.append(parsed)
            context_limit = max(context_limits) if context_limits else None

            tags_response = requests.get(f"{self.base_url}/api/tags", timeout=4)
            tags_response.raise_for_status()
            model_items = tags_response.json().get("models", [])
            installed = next(
                (item for item in model_items if str(item.get("name")) == self.model),
                None,
            )
            size_bytes = (installed or {}).get("size")
            model_size_gb = (
                float(size_bytes) / 1024**3
                if isinstance(size_bytes, (int, float)) and size_bytes > 0
                else None
            )
        except requests.RequestException as exc:
            raise LocalAIError(f"Impossibile leggere i limiti del modello: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise LocalAIError("Ollama ha restituito metadati del modello non validi.") from exc

        # Reserve memory for the OS and, on CPU, for the model weights. With the
        # GPU profile only a fraction is charged to system RAM because weights
        # are primarily offloaded to VRAM. The result is a recommendation, not
        # an artificial validation boundary.
        os_reserve_gb = 4.0
        model_ram_gb = (model_size_gb or 0.0) * (
            0.25 if self.hardware_profile == "gpu16" else 1.0
        )
        context_headroom_gb = max(1.0, float(ram_gb) - os_reserve_gb - model_ram_gb)
        raw_recommendation = max(1, math.floor(context_headroom_gb * 1024))
        recommended_tokens = 1 << (raw_recommendation.bit_length() - 1)
        if context_limit is not None:
            # Leave room for the health snapshot and system prompt. This only
            # affects the recommendation; the editable field may reach the
            # full physical model context.
            recommended_tokens = min(recommended_tokens, max(1, context_limit // 2))
        recommended_context = recommended_tokens * 2
        if context_limit is not None:
            recommended_context = min(recommended_context, context_limit)
        return TokenRecommendation(
            recommended_tokens=recommended_tokens,
            recommended_context=recommended_context,
            model_context_limit=context_limit,
            model_size_gb=model_size_gb,
            ram_gb=ram_gb,
        )

    def status(self) -> OllamaStatus:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=4)
            response.raise_for_status()
            model_items = response.json().get("models", [])
            models = tuple(
                str(item.get("name")) for item in model_items if item.get("name")
            )
        except requests.RequestException as exc:
            return OllamaStatus(False, (), f"Ollama non raggiungibile: {exc}")
        except (TypeError, ValueError) as exc:
            return OllamaStatus(False, (), f"Risposta Ollama non valida: {exc}")

        installed_item = next(
            (item for item in model_items if str(item.get("name")) == self.model), None
        )
        message = (
            f"Ollama pronto · {self.model}"
            if installed_item
            else f"Ollama è attivo, ma il modello {self.model} non è ancora installato."
        )
        update_messages: list[str] = []
        update_target: str | None = None

        if installed_item:
            local_digest = self._normalized_digest(str(installed_item.get("digest", "")))
            try:
                remote_digest = self._normalized_digest(self._remote_model_digest())
            except (requests.RequestException, TypeError, ValueError):
                remote_digest = ""
            if local_digest and remote_digest and local_digest != remote_digest:
                update_messages.append(f"nuovi pesi disponibili per {self.model}")
                update_target = self.model

        successor = newer_model_suggestion(self.model, self.hardware_profile)
        if successor:
            qualifier = "già installata" if successor in models else "disponibile"
            update_messages.append(
                f"generazione più recente adatta al profilo {qualifier}: {successor}"
            )
            update_target = successor

        return OllamaStatus(
            True,
            models,
            message,
            bool(update_messages),
            " · ".join(update_messages),
            update_target,
        )

    def pull(self, progress: Callable[[str], None] | None = None) -> None:
        try:
            with requests.post(
                f"{self.base_url}/api/pull",
                json={"model": self.model, "stream": True},
                stream=True,
                timeout=(10, 1800),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    payload = json.loads(line)
                    status = str(payload.get("status", "Download in corso"))
                    total = payload.get("total")
                    completed = payload.get("completed")
                    if total and completed:
                        status += f" · {completed / total * 100:.0f}%"
                    if progress:
                        progress(status)
        except (requests.RequestException, ValueError) as exc:
            raise LocalAIError(f"Download del modello non riuscito: {exc}") from exc

    def _chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        think: bool,
        num_predict: int,
        num_ctx: int,
        thinking_callback: Callable[[str], None] | None,
        answer_callback: Callable[[str], None] | None,
    ) -> str:
        with requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": True,
                "think": think,
                "messages": messages,
                "options": {
                    "temperature": 0.2,
                    "num_ctx": num_ctx,
                    "num_predict": num_predict,
                },
                "keep_alive": "5m",
            },
            stream=True,
            timeout=(10, 900),
        ) as response:
            if response.status_code >= 400:
                try:
                    error = response.json().get("error")
                except (TypeError, ValueError):
                    error = None
                detail = str(error or response.reason or f"HTTP {response.status_code}")
                raise LocalAIError(f"Analisi locale non riuscita: {detail}")
            response.raise_for_status()
            answer_parts: list[str] = []
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("error"):
                    raise LocalAIError(
                        f"Analisi locale non riuscita: {payload['error']}"
                    )
                message = payload.get("message") or {}
                thinking = message.get("thinking")
                content = message.get("content")
                if isinstance(thinking, str) and thinking and thinking_callback:
                    thinking_callback(thinking)
                if isinstance(content, str) and content:
                    answer_parts.append(content)
                    if answer_callback:
                        answer_callback(content)
            return "".join(answer_parts).strip()

    def analyze_stream(
        self,
        snapshot: dict[str, Any],
        question: str = "",
        thinking_callback: Callable[[str], None] | None = None,
        answer_callback: Callable[[str], None] | None = None,
        max_tokens: int = 3200,
        model_context_limit: int | None = None,
    ) -> str:
        if not snapshot.get("metrics"):
            raise LocalAIError("Non ci sono abbastanza dati locali nel periodo selezionato.")
        request_text = question.strip() or (
            "Analizza tutti i dati disponibili: evidenzia tendenze, cambiamenti rispetto "
            "alla baseline personale, anomalie statistiche e correlazioni che meritano "
            "attenzione. Includi anche fasi del sonno, allenamenti e campi secondari."
        )
        context = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        max_tokens = max(1, int(max_tokens))
        if model_context_limit is not None and model_context_limit > 0:
            max_tokens = min(max_tokens, model_context_limit)
        num_ctx = max(16384, max_tokens * 2)
        if model_context_limit is not None and model_context_limit > 0:
            num_ctx = min(num_ctx, model_context_limit)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Riepilogo statistico locale:\n{context}\n\n"
                    f"Richiesta dell'utente: {request_text}"
                ),
            },
        ]
        try:
            answer = self._chat_stream(
                messages,
                think=True,
                num_predict=max_tokens,
                num_ctx=num_ctx,
                thinking_callback=thinking_callback,
                answer_callback=answer_callback,
            )
            if not answer:
                if thinking_callback:
                    thinking_callback(
                        "\n\nThinking completato. Preparo la risposta finale…\n"
                    )
                fallback_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "Fornisci adesso la risposta finale in italiano, senza altro "
                            "ragionamento interno."
                        ),
                    },
                ]
                answer = self._chat_stream(
                    fallback_messages,
                    think=False,
                    num_predict=max_tokens,
                    num_ctx=num_ctx,
                    thinking_callback=None,
                    answer_callback=answer_callback,
                )
            if not answer:
                raise LocalAIError(
                    "Ollama non ha prodotto una risposta finale neppure al secondo tentativo."
                )
            return answer
        except LocalAIError:
            raise
        except requests.RequestException as exc:
            raise LocalAIError(f"Analisi locale non riuscita: {exc}") from exc
        except (TypeError, ValueError, KeyError) as exc:
            raise LocalAIError(f"Risposta del modello locale non valida: {exc}") from exc

    def analyze(
        self,
        snapshot: dict[str, Any],
        question: str = "",
        max_tokens: int = 3200,
        model_context_limit: int | None = None,
    ) -> str:
        return self.analyze_stream(
            snapshot,
            question,
            max_tokens=max_tokens,
            model_context_limit=model_context_limit,
        )
