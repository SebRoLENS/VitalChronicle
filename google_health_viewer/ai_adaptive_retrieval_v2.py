"""Multilingual, typo-tolerant and guarded adaptive evidence retrieval.

This layer deliberately remains deterministic: no model call is used to decide which
health metrics to send to Ollama. It expands metric/domain names with every bundled
Weblate catalogue, tolerates modest spelling mistakes with standard-library fuzzy
matching, and falls back to a wider domain/global packet when confidence is low.

The module is installed after the response-hygiene layer. It also guards the actual
health-evidence JSON passed to ``_chat_stream``: an OptimizedOllamaClient request is
not allowed to reach Ollama unless the packet carries the retrieval-v2 marker. This
turns adaptive retrieval from a best-effort preprocessing hook into a checked part of
the real inference path.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from contextvars import ContextVar
from difflib import SequenceMatcher
from functools import cache
from typing import Any

from . import ai_engine, i18n
from . import ai_adaptive_retrieval as legacy
from .ai_pipeline import estimate_json_tokens
from .local_ai import LocalAIError

RETRIEVAL_VERSION = "multilingual-fuzzy-v2"
HIGH_CONFIDENCE = 0.86
MEDIUM_CONFIDENCE = 0.74

_CURRENT_QUESTION: ContextVar[str] = ContextVar("vc_retrieval_v2_question", default="")
_CURRENT_PROFILE: ContextVar[str] = ContextVar("vc_retrieval_v2_profile", default="standard")
_CURRENT_MODE: ContextVar[str] = ContextVar("vc_retrieval_v2_mode", default="question")
_LAST_DIAGNOSTICS: ContextVar[dict[str, Any] | None] = ContextVar(
    "vc_retrieval_v2_diagnostics", default=None
)

_BASE_ENSURE: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_BASE_ANALYZE: Callable[..., str] | None = None
_BASE_CHAT_STREAM: Callable[..., str] | None = None
_INSTALLED = False

# English source concepts are pivots into the Weblate catalogues. As soon as a
# catalogue contains a translation for one of these UI terms, that translation is
# automatically usable by the deterministic router without a code change.
_METRIC_SOURCE_TERMS: dict[str, tuple[str, ...]] = {
    "daily-heart-rate-variability": ("Heart rate variability", "Average HRV"),
    "heart-rate-variability": ("Heart rate variability", "Average HRV"),
    "active-energy-burned": ("Active energy burned", "Calories by zone"),
    "total-calories": ("Total calories",),
    "daily-resting-heart-rate": ("Resting heart rate",),
    "heart-rate": ("Heart rate",),
    "sleep": ("Sleep", "Asleep"),
    "steps": ("Steps",),
    "distance": ("Distance",),
    "daily-oxygen-saturation": ("Oxygen saturation",),
    "oxygen-saturation": ("Oxygen saturation",),
    "daily-respiratory-rate": ("Respiratory rate",),
    "respiratory-rate-sleep-summary": ("Respiratory rate", "Sleep"),
    "daily-sleep-temperature-derivations": ("Temperature", "Sleep"),
    "daily-vo2-max": ("VO2 max",),
    "weight": ("Weight",),
    "body-fat": ("Body fat",),
    "exercise": ("Exercise", "Activity and fitness"),
    "blood-pressure": ("Blood pressure",),
    "blood-glucose": ("Blood glucose",),
}

_DOMAIN_SOURCE_TERMS: dict[str, tuple[str, ...]] = {
    "activity": ("Activity", "Activity and fitness", "Activity level"),
    "sleep": ("Sleep", "Asleep"),
    "heart": ("Heart rate", "Resting heart rate", "Heart rate variability"),
    "vitals": ("Oxygen saturation", "Respiratory rate", "Blood pressure"),
    "weight": ("Weight", "Body fat"),
    "workouts": ("Exercise", "Activity and fitness"),
    "nutrition": ("Nutrition", "Food"),
}

# Colloquial fallbacks remain useful before a community catalogue is complete.
# Translation-derived labels are always added on top of these, so supported
# languages grow automatically with Weblate rather than requiring this table.
_METRIC_FALLBACKS: dict[str, tuple[str, ...]] = {
    "daily-heart-rate-variability": (
        "hrv", "heart rate variability", "variabilita della frequenza cardiaca",
    ),
    "heart-rate-variability": (
        "hrv", "heart rate variability", "variabilita della frequenza cardiaca",
    ),
    "active-energy-burned": (
        "active calories", "active energy", "calorie attive", "energia attiva",
    ),
    "total-calories": ("total calories", "calorie totali"),
    "daily-resting-heart-rate": (
        "resting heart rate", "rhr", "frequenza cardiaca a riposo", "battito a riposo",
    ),
    "heart-rate": ("heart rate", "frequenza cardiaca", "battito cardiaco"),
    "sleep": ("sleep", "sonno"),
    "steps": ("steps", "passi"),
    "distance": ("distance", "distanza"),
    "daily-oxygen-saturation": ("spo2", "oxygen saturation", "saturazione ossigeno"),
    "oxygen-saturation": ("spo2", "oxygen saturation", "saturazione ossigeno"),
    "daily-respiratory-rate": ("respiratory rate", "frequenza respiratoria"),
    "daily-vo2-max": ("vo2 max", "vo2max"),
    "weight": ("weight", "peso"),
    "body-fat": ("body fat", "grasso corporeo"),
    "exercise": ("exercise", "workout", "allenamento", "esercizio"),
    "blood-pressure": ("blood pressure", "pressione arteriosa"),
    "blood-glucose": ("blood glucose", "glucose", "glicemia"),
}

_DOMAIN_FALLBACKS: dict[str, tuple[str, ...]] = {
    "activity": (
        "activity", "physical activity", "attivita", "attivita fisica", "mi muovo",
        "movimento", "steps", "passi", "active calories", "calorie attive",
    ),
    "sleep": ("sleep", "sonno"),
    "heart": ("heart", "cardiac", "cardiaco", "hrv", "battito", "frequenza cardiaca"),
    "vitals": ("vitals", "parametri vitali", "spo2", "ossigeno", "respiratoria", "pressione"),
    "weight": ("weight", "peso", "body fat", "grasso corporeo"),
    "workouts": ("workout", "exercise", "training", "allenamento", "esercizio"),
    "nutrition": ("nutrition", "food", "diet", "nutrizione", "alimentazione", "dieta"),
}

_ASSOCIATION_FALLBACKS = (
    "correl", "associa", "relaz", "relationship", "relation", "rapport", "zusammenhang",
)
_GLOBAL_FALLBACKS = (
    "all data", "all my data", "analyse everything", "analyze everything", "anything interesting",
    "tutti i dati", "tutti i miei dati", "analizza tutto", "pattern interessanti",
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("_", " ").replace("-", " ")
    value = re.sub(r"[^a-z0-9%+ ]+", " ", value)
    return " ".join(value.split())


@cache
def _catalogues() -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    for path in sorted(i18n.CATALOGUE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            result.append(
                {
                    str(key): str(value)
                    for key, value in data.items()
                    if isinstance(key, str) and isinstance(value, str) and value.strip()
                }
            )
    return tuple(result)


@cache
def _translation_variants(source: str) -> tuple[str, ...]:
    """Return the source phrase plus every available Weblate translation of it."""

    variants = {str(source).strip()}
    source_keys = {str(source).strip()}
    normalized_source = _normalize(source)

    # If the supplied phrase is itself a translation, pivot back to the English
    # source key, then collect that key's translations from every catalogue.
    for catalogue in _catalogues():
        for key, value in catalogue.items():
            if _normalize(value) == normalized_source:
                source_keys.add(key)

    for key in tuple(source_keys):
        variants.add(key)
        for catalogue in _catalogues():
            translated = catalogue.get(key)
            if translated:
                variants.add(translated.strip())
    return tuple(sorted(value for value in variants if value))


def _expanded_terms(sources: tuple[str, ...], fallbacks: tuple[str, ...] = ()) -> tuple[str, ...]:
    values = set(fallbacks)
    for source in sources:
        values.update(_translation_variants(source))
    return tuple(sorted(value for value in values if value))


def _window_similarity(query: str, phrase: str) -> float:
    query = _normalize(query)
    phrase = _normalize(phrase)
    if not query or not phrase:
        return 0.0
    if phrase in query:
        return 1.0

    q_words = query.split()
    p_words = phrase.split()
    # Acronyms and very short terms are never fuzzy-matched: HRV must actually be
    # present, which avoids accidental short-string matches in unrelated words.
    if len(p_words) == 1 and len(phrase) <= 4:
        return 1.0 if phrase in q_words else 0.0

    best = 0.0
    expected = len(p_words)
    sizes = {max(1, expected - 1), expected, expected + 1}
    for size in sizes:
        if size > len(q_words):
            continue
        for index in range(len(q_words) - size + 1):
            window = " ".join(q_words[index : index + size])
            # Very different lengths cannot be a useful typo correction.
            if abs(len(window) - len(phrase)) > max(4, int(len(phrase) * 0.35)):
                continue
            best = max(best, SequenceMatcher(None, window, phrase).ratio())
    return best


def _metric_scores(packet: dict[str, Any], question: str) -> list[tuple[float, str, str]]:
    scored: list[tuple[float, str, str]] = []
    for data_type, info in legacy._packet_metric_index(packet).items():
        terms = {str(info.get("label") or data_type), data_type.replace("-", " ")}
        terms.update(
            _expanded_terms(
                _METRIC_SOURCE_TERMS.get(data_type, ()),
                _METRIC_FALLBACKS.get(data_type, ()),
            )
        )
        best_score = 0.0
        best_term = ""
        for term in terms:
            score = _window_similarity(question, term)
            if score > best_score:
                best_score, best_term = score, term
        if best_score >= MEDIUM_CONFIDENCE:
            scored.append((best_score, data_type, best_term))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored


def _domain_scores(question: str) -> list[tuple[float, str, str]]:
    scored: list[tuple[float, str, str]] = []
    for domain, sources in _DOMAIN_SOURCE_TERMS.items():
        terms = _expanded_terms(sources, _DOMAIN_FALLBACKS.get(domain, ()))
        best_score = 0.0
        best_term = ""
        for term in terms:
            score = _window_similarity(question, term)
            if score > best_score:
                best_score, best_term = score, term
        if best_score >= MEDIUM_CONFIDENCE:
            scored.append((best_score, domain, best_term))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored


def _is_association_question(question: str) -> bool:
    return any(_normalize(term) in _normalize(question) for term in _ASSOCIATION_FALLBACKS)


def _is_global_question(question: str) -> bool:
    query = _normalize(question)
    translated = _expanded_terms(("Analyse all data", "Complete history"), _GLOBAL_FALLBACKS)
    return any(_normalize(term) in query for term in translated if _normalize(term))


def classify_request_v2(
    packet: dict[str, Any], question: str, analysis_mode: str = "question"
) -> dict[str, Any]:
    if analysis_mode == "deep" or not question.strip():
        return {
            "mode": "global", "data_types": [], "domains": [], "confidence": 1.0,
            "reason": "deep_or_empty_request", "matched_terms": [],
        }

    metric_scores = _metric_scores(packet, question)
    high_metrics = [row for row in metric_scores if row[0] >= HIGH_CONFIDENCE]
    if high_metrics:
        best = high_metrics[0][0]
        selected = [row for row in high_metrics if row[0] >= best - 0.08]
        # A relation/correlation question commonly and legitimately names two metrics.
        # For other questions, keep only very-close matches to avoid accidental breadth.
        if not _is_association_question(question) and len(selected) > 2:
            selected = selected[:2]
        return {
            "mode": "specific_metrics",
            "data_types": [row[1] for row in selected],
            "domains": [],
            "confidence": round(best, 3),
            "reason": "high_confidence_metric_match",
            "matched_terms": [row[2] for row in selected],
        }

    domain_scores = _domain_scores(question)
    high_domains = [row for row in domain_scores if row[0] >= HIGH_CONFIDENCE]
    if high_domains:
        best = high_domains[0][0]
        selected = [row for row in high_domains if row[0] >= best - 0.06]
        return {
            "mode": "domain", "data_types": [],
            "domains": [row[1] for row in selected], "confidence": round(best, 3),
            "reason": "high_confidence_domain_match",
            "matched_terms": [row[2] for row in selected],
        }

    # A medium-confidence metric typo/paraphrase is intentionally widened to its
    # whole domain rather than trusting a narrow match.
    if metric_scores:
        index = legacy._packet_metric_index(packet)
        best = metric_scores[0][0]
        candidates = [row for row in metric_scores if row[0] >= best - 0.04]
        domains = sorted({index[row[1]]["domain"] for row in candidates if row[1] in index})
        if domains:
            return {
                "mode": "domain", "data_types": [], "domains": domains,
                "confidence": round(best, 3), "reason": "medium_metric_match_widened_to_domain",
                "matched_terms": [row[2] for row in candidates],
            }

    if domain_scores:
        best = domain_scores[0][0]
        candidates = [row for row in domain_scores if row[0] >= best - 0.04]
        return {
            "mode": "domain", "data_types": [], "domains": [row[1] for row in candidates],
            "confidence": round(best, 3), "reason": "medium_domain_match",
            "matched_terms": [row[2] for row in candidates],
        }

    if _is_global_question(question):
        return {
            "mode": "global", "data_types": [], "domains": [], "confidence": 1.0,
            "reason": "explicit_global_request", "matched_terms": [],
        }

    # Fail-safe: unknown language or unusual wording gets a broad packet. Retrieval
    # is allowed to save tokens only when it is confident enough to remove evidence.
    return {
        "mode": "general_question", "data_types": [], "domains": [], "confidence": 0.0,
        "reason": "low_confidence_broad_fallback", "matched_terms": [],
    }


def select_evidence_v2(
    packet: dict[str, Any],
    question: str,
    *,
    performance_profile: str = "standard",
    analysis_mode: str = "question",
) -> dict[str, Any]:
    profile = performance_profile if performance_profile in legacy.PROFILE_EVIDENCE_TARGETS else "standard"
    target = legacy.PROFILE_EVIDENCE_TARGETS[profile]
    original_tokens = estimate_json_tokens(packet)
    original_metric_count = sum(
        len(metrics) for metrics in (packet.get("domains") or {}).values() if isinstance(metrics, list)
    )
    intent = classify_request_v2(packet, question, analysis_mode)
    preserve_full = intent["mode"] == "global" and profile == "max" and analysis_mode == "deep"

    if intent["mode"] == "global":
        result = json.loads(json.dumps(packet)) if profile == "max" else legacy._global_profile_packet(packet, profile)
    elif intent["mode"] in {"specific_metrics", "domain"}:
        selected_domains = set(intent["domains"])
        selected_types = legacy._selected_type_set(packet, intent["data_types"], intent["domains"])
        result = legacy._filter_for_scope(
            packet,
            selected_types=selected_types,
            selected_domains=selected_domains,
            association_question=_is_association_question(question),
        )
        legacy._cap_lists(result, profile)
    else:
        result = legacy._global_profile_packet(packet, profile)

    if not preserve_full:
        legacy._trim_to_target(result, target)

    selected_metric_count = sum(
        len(metrics) for metrics in (result.get("domains") or {}).values() if isinstance(metrics, list)
    )
    metadata = result.setdefault("packet", {})
    if not isinstance(metadata, dict):
        metadata = {}
        result["packet"] = metadata
    selected_tokens = estimate_json_tokens(result)
    metadata.update(
        {
            "retrieval_version": RETRIEVAL_VERSION,
            "retrieval_mode": intent["mode"],
            "retrieval_profile": profile,
            "retrieval_confidence": intent["confidence"],
            "retrieval_reason": intent["reason"],
            "retrieval_target_tokens": target,
            "retrieval_original_tokens": original_tokens,
            "retrieval_selected_tokens": selected_tokens,
            "retrieval_original_metric_count": original_metric_count,
            "retrieval_metric_count": selected_metric_count,
            "retrieval_preserved_full_packet": preserve_full,
        }
    )
    if intent["matched_terms"]:
        metadata["retrieval_matched_terms"] = list(intent["matched_terms"])
    if intent["data_types"]:
        metadata["retrieval_selected_data_types"] = list(intent["data_types"])
    if intent["domains"]:
        metadata["retrieval_selected_domains"] = list(intent["domains"])
    metadata["estimated_tokens"] = estimate_json_tokens(result)
    return result


def _ensure_v2(snapshot: dict[str, Any]) -> dict[str, Any]:
    if _BASE_ENSURE is None:
        raise RuntimeError("Adaptive retrieval v2 was not initialized")
    compact = _BASE_ENSURE(snapshot)
    selected = select_evidence_v2(
        compact,
        _CURRENT_QUESTION.get(),
        performance_profile=_CURRENT_PROFILE.get(),
        analysis_mode=_CURRENT_MODE.get(),
    )
    metadata = selected.get("packet") or {}
    _LAST_DIAGNOSTICS.set(
        {
            "mode": metadata.get("retrieval_mode"),
            "profile": metadata.get("retrieval_profile"),
            "confidence": metadata.get("retrieval_confidence"),
            "original_tokens": metadata.get("retrieval_original_tokens"),
            "selected_tokens": metadata.get("retrieval_selected_tokens"),
            "original_metrics": metadata.get("retrieval_original_metric_count"),
            "selected_metrics": metadata.get("retrieval_metric_count"),
            "reason": metadata.get("retrieval_reason"),
        }
    )
    return selected


def _extract_evidence_packet(messages: list[dict[str, str]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        content = str(message.get("content") or "")
        start_marker = "BEGIN_HEALTH_EVIDENCE_JSON\n"
        end_marker = "\nEND_HEALTH_EVIDENCE_JSON"
        if start_marker not in content or end_marker not in content:
            continue
        raw = content.split(start_marker, 1)[1].split(end_marker, 1)[0]
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _guarded_chat_stream(self, messages, *, think, **kwargs):
    if _BASE_CHAT_STREAM is None:
        raise RuntimeError("Adaptive retrieval v2 chat guard was not initialized")
    packet = _extract_evidence_packet(messages)
    if packet is not None:
        metadata = packet.get("packet") or {}
        if metadata.get("retrieval_version") != RETRIEVAL_VERSION:
            raise LocalAIError(
                "VitalChronicle blocked an AI request because adaptive evidence retrieval was bypassed."
            )
    return _BASE_CHAT_STREAM(self, messages, think=think, **kwargs)


def _analyze_v2(
    self,
    snapshot: dict[str, Any],
    question: str = "",
    thinking_callback: Callable[[str], None] | None = None,
    answer_callback: Callable[[str], None] | None = None,
    max_tokens: int = 3200,
    model_context_limit: int | None = None,
    history: list[dict[str, str]] | None = None,
    analysis_mode: str = "question",
    cancel_callback: Callable[[], bool] | None = None,
    prompt_callback: Callable[[str], None] | None = None,
) -> str:
    if _BASE_ANALYZE is None:
        raise RuntimeError("Adaptive retrieval v2 was not initialized")
    question_token = _CURRENT_QUESTION.set(question)
    profile = getattr(self, "performance_profile", "standard")
    profile_token = _CURRENT_PROFILE.set(
        profile if profile in legacy.PROFILE_EVIDENCE_TARGETS else "standard"
    )
    mode_token = _CURRENT_MODE.set(analysis_mode)
    diagnostics_token = _LAST_DIAGNOSTICS.set(None)

    def routed_prompt_callback(text: str) -> None:
        if prompt_callback is None:
            return
        if text.startswith("# Pipeline diagnostics"):
            diag = _LAST_DIAGNOSTICS.get() or {}
            if diag:
                text += (
                    "\nAdaptive retrieval: {original_tokens} -> {selected_tokens} evidence tokens"
                    " · metrics {original_metrics} -> {selected_metrics}"
                    " · mode {mode} · confidence {confidence}"
                    " · {reason}".format(**diag)
                )
        prompt_callback(text)

    try:
        return _BASE_ANALYZE(
            self,
            snapshot,
            question=question,
            thinking_callback=thinking_callback,
            answer_callback=answer_callback,
            max_tokens=max_tokens,
            model_context_limit=model_context_limit,
            history=history,
            analysis_mode=analysis_mode,
            cancel_callback=cancel_callback,
            prompt_callback=routed_prompt_callback if prompt_callback else None,
        )
    finally:
        _LAST_DIAGNOSTICS.reset(diagnostics_token)
        _CURRENT_MODE.reset(mode_token)
        _CURRENT_PROFILE.reset(profile_token)
        _CURRENT_QUESTION.reset(question_token)


def install_ai_adaptive_retrieval_v2() -> None:
    """Install multilingual retrieval and the outgoing-request guard exactly once."""

    global _BASE_ANALYZE, _BASE_CHAT_STREAM, _BASE_ENSURE, _INSTALLED
    if _INSTALLED:
        return
    # The response-hygiene installer must run first. Capturing here deliberately
    # preserves its question-specific association diagnostics and answer sanitizer.
    _BASE_ENSURE = ai_engine.ensure_compact_evidence
    _BASE_ANALYZE = ai_engine.OptimizedOllamaClient.analyze_stream
    _BASE_CHAT_STREAM = ai_engine.OptimizedOllamaClient._chat_stream
    ai_engine.ensure_compact_evidence = _ensure_v2
    ai_engine.OptimizedOllamaClient.analyze_stream = _analyze_v2
    ai_engine.OptimizedOllamaClient._chat_stream = _guarded_chat_stream
    _INSTALLED = True
