"""Expose the actual adaptive-evidence scope in the live AI status.

The adaptive router already stores its decision inside the compact packet that is
sent to Ollama.  This small UI-facing hook reads that *actual outgoing packet*
and adds an explicit scope summary to the token-usage phase.  It therefore does
not guess from the user's question: what the user sees is what the model really
received.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import ai_engine
from .ai_adaptive_retrieval_v2 import RETRIEVAL_VERSION, _extract_evidence_packet
from .i18n import _, current_language

_BASE_CHAT_STREAM: Callable[..., str] | None = None
_INSTALLED = False

_DOMAIN_LABELS = {
    "activity": "Activity",
    "sleep": "Sleep",
    "heart": "Heart rate",
    "vitals": "Vitals",
    "weight": "Weight",
    "workouts": "Exercise",
    "nutrition": "Nutrition",
    "other": "Other",
}


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _domain_text(packet: dict[str, Any]) -> str:
    domains = [str(value) for value in (packet.get("domains") or {}).keys()]
    labels = [_( _DOMAIN_LABELS.get(domain, domain.replace("_", " ").title()) ) for domain in domains]
    if not labels:
        return "—"
    if len(labels) <= 3:
        return ", ".join(labels)
    return ", ".join(labels[:3]) + f" +{len(labels) - 3}"


def evidence_scope_status(packet: dict[str, Any]) -> str:
    """Describe how much of the compact deterministic evidence will reach Ollama."""

    metadata = packet.get("packet") or {}
    if not isinstance(metadata, dict) or metadata.get("retrieval_version") != RETRIEVAL_VERSION:
        return ""

    original_metrics = _integer(metadata.get("retrieval_original_metric_count"))
    selected_metrics = _integer(metadata.get("retrieval_metric_count"))
    original_tokens = _integer(metadata.get("retrieval_original_tokens"))
    selected_tokens = _integer(metadata.get("retrieval_selected_tokens"))
    filtered = bool(original_metrics and selected_metrics < original_metrics)
    domains = _domain_text(packet)

    if current_language() == "it":
        scope = "DATI PARZIALI" if filtered else "TUTTE LE METRICHE"
        metric_text = f"metriche {selected_metrics}/{original_metrics}" if original_metrics else "metriche n/d"
        token_text = (
            f"evidenze ~{selected_tokens}/{original_tokens} token"
            if original_tokens
            else f"evidenze ~{selected_tokens} token"
        )
    else:
        scope = "PARTIAL DATA" if filtered else "ALL METRICS"
        metric_text = f"metrics {selected_metrics}/{original_metrics}" if original_metrics else "metrics n/a"
        token_text = (
            f"evidence ~{selected_tokens}/{original_tokens} tokens"
            if original_tokens
            else f"evidence ~{selected_tokens} tokens"
        )

    return f"{scope} · {domains} · {metric_text} · {token_text}"


def _chat_stream_with_scope_status(self, messages, *, think, **kwargs):
    if _BASE_CHAT_STREAM is None:
        raise RuntimeError("AI evidence-scope status hook was not initialized")

    packet = _extract_evidence_packet(messages)
    previous_phase = getattr(self, "_current_phase", "model")
    if packet is not None:
        summary = evidence_scope_status(packet)
        if summary:
            self._current_phase = f"{previous_phase} · {summary}"
    try:
        return _BASE_CHAT_STREAM(self, messages, think=think, **kwargs)
    finally:
        self._current_phase = previous_phase


def install_ai_retrieval_scope_status() -> None:
    """Append outgoing evidence scope to the existing live token telemetry."""

    global _BASE_CHAT_STREAM, _INSTALLED
    if _INSTALLED:
        return
    _BASE_CHAT_STREAM = ai_engine.OptimizedOllamaClient._chat_stream
    ai_engine.OptimizedOllamaClient._chat_stream = _chat_stream_with_scope_status
    _INSTALLED = True
