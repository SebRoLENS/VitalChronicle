"""Question-aware deterministic evidence retrieval for local AI models.

VitalChronicle keeps the rich deterministic snapshot intact, but a local language
model rarely needs every metric for every question. This module selects the most
relevant part of the already compact evidence packet immediately before inference.
It never re-reads health records or recalculates statistics.

Desktop evidence targets are intentionally profile-dependent:
- Fast: about 1,200 compact JSON tokens.
- Standard: about 2,500 compact JSON tokens.
- Maximum: up to the existing approximately 4,000-token compact packet.

Maximum deep analysis preserves the complete compact packet, so the adaptive layer
cannot make the most capable desktop mode less informative than before.
"""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from . import ai_engine
from .ai_pipeline import estimate_json_tokens, json_size_bytes

PROFILE_EVIDENCE_TARGETS = {
    "fast": 1200,
    "standard": 2500,
    "max": 4000,
}

_PROFILE_LIST_LIMITS = {
    "fast": (4, 2, 2),
    "standard": (10, 4, 4),
    "max": (14, 6, 6),
}

_CURRENT_QUESTION: ContextVar[str] = ContextVar(
    "vitalchronicle_adaptive_question", default=""
)
_CURRENT_PROFILE: ContextVar[str] = ContextVar(
    "vitalchronicle_adaptive_profile", default="standard"
)
_CURRENT_ANALYSIS_MODE: ContextVar[str] = ContextVar(
    "vitalchronicle_adaptive_analysis_mode", default="question"
)

_ORIGINAL_ENSURE: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_ORIGINAL_ANALYZE: Callable[..., str] | None = None
_INSTALLED = False

# These phrases imply a truly broad request only after explicit metric/domain
# mentions have been considered. This prevents e.g. "analisi generale della mia
# attività fisica" from being mistaken for a request about every health domain.
_GLOBAL_HINTS = (
    "all data",
    "all my data",
    "all history",
    "complete history",
    "entire history",
    "analyse everything",
    "analyze everything",
    "anything interesting",
    "interesting patterns",
    "tutti i dati",
    "tutti i miei dati",
    "tutta la storia",
    "intera storia",
    "analizza tutto",
    "analizzare tutto",
    "pattern interessanti",
    "toutes les donnees",
    "tous mes donnees",
    "todos los datos",
    "todos mis datos",
    "alle daten",
    "alle meine daten",
)

_ASSOCIATION_HINTS = (
    "correl",
    "associa",
    "relaz",
    "relationship",
    "relation",
    "rapport",
    "zusammenhang",
)

_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "activity": (
        "activity",
        "physical activity",
        "attivita",
        "attivita fisica",
        "active minutes",
        "minuti attivi",
        "steps",
        "passi",
        "distance",
        "distanza",
        "calories",
        "calorie",
        "energy",
        "energia",
    ),
    "sleep": ("sleep", "sonno", "sommeil", "sueno", "schlaf"),
    "heart": (
        "heart",
        "cardiac",
        "cardiaca",
        "cardiaco",
        "hrv",
        "heart rate",
        "frequenza cardiaca",
        "battito",
        "battiti",
    ),
    "vitals": (
        "vitals",
        "parametri vitali",
        "spo2",
        "oxygen",
        "ossigeno",
        "respiratory",
        "respiratoria",
        "temperature",
        "temperatura",
        "blood pressure",
        "pressione",
        "glucose",
        "glicemia",
        "vo2",
    ),
    "weight": (
        "weight",
        "peso",
        "body fat",
        "grasso corporeo",
        "composition",
        "composizione corporea",
    ),
    "workouts": (
        "workout",
        "workouts",
        "exercise",
        "training",
        "allenamento",
        "allenamenti",
        "esercizio",
    ),
    "nutrition": (
        "nutrition",
        "food",
        "diet",
        "nutrizione",
        "alimentazione",
        "cibo",
        "dieta",
    ),
}

_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "daily-heart-rate-variability": (
        "hrv",
        "heart rate variability",
        "variabilita della frequenza cardiaca",
        "variabilite de la frequence cardiaque",
        "variabilidad de la frecuencia cardiaca",
        "herzfrequenzvariabilitat",
    ),
    "heart-rate-variability": (
        "hrv",
        "heart rate variability",
        "variabilita della frequenza cardiaca",
    ),
    "active-energy-burned": (
        "active calories",
        "active calorie",
        "active energy",
        "calorie attive",
        "consumo calorico attivo",
        "energia attiva",
    ),
    "total-calories": ("total calories", "calorie totali", "energia totale"),
    "daily-resting-heart-rate": (
        "resting heart rate",
        "rhr",
        "frequenza cardiaca a riposo",
        "battito a riposo",
    ),
    "heart-rate": ("heart rate", "frequenza cardiaca", "battito cardiaco"),
    "sleep": ("sleep", "sonno", "durata del sonno", "sleep duration"),
    "steps": ("steps", "passi", "step count", "numero di passi"),
    "distance": ("distance", "distanza"),
    "daily-oxygen-saturation": (
        "spo2",
        "oxygen saturation",
        "saturazione ossigeno",
        "saturazione di ossigeno",
    ),
    "oxygen-saturation": ("spo2", "oxygen saturation", "saturazione ossigeno"),
    "daily-respiratory-rate": (
        "respiratory rate",
        "breathing rate",
        "frequenza respiratoria",
    ),
    "respiratory-rate-sleep-summary": (
        "sleep respiratory rate",
        "frequenza respiratoria nel sonno",
    ),
    "daily-sleep-temperature-derivations": (
        "sleep temperature",
        "skin temperature",
        "temperatura nel sonno",
        "temperatura cutanea",
    ),
    "daily-vo2-max": ("vo2 max", "vo2max"),
    "weight": ("weight", "peso"),
    "body-fat": ("body fat", "grasso corporeo", "percentuale di grasso"),
    "exercise": ("exercise", "workout", "allenamento", "esercizio"),
    "blood-pressure": ("blood pressure", "pressione sanguigna", "pressione arteriosa"),
    "blood-glucose": ("blood glucose", "glucose", "glicemia", "glucosio"),
}


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().replace("_", " ").replace("-", " ")
    return " ".join(normalized.split())


def _contains_phrase(query: str, phrase: str) -> bool:
    normalized = _normalize(phrase)
    return bool(normalized and normalized in query)


def _metric_score(query: str, data_type: str, label: str) -> int:
    score = 0
    label_text = _normalize(label)
    data_type_text = _normalize(data_type)
    if label_text and len(label_text) >= 3 and label_text in query:
        score = max(score, 8)
    if data_type_text and data_type_text in query:
        score = max(score, 6)
    for alias in _METRIC_ALIASES.get(data_type, ()):
        if _contains_phrase(query, alias):
            score = max(score, 10)
    query_words = {word for word in query.split() if len(word) >= 5}
    label_words = {word for word in label_text.split() if len(word) >= 5}
    overlap = len(query_words & label_words)
    if overlap:
        score = max(score, min(5, overlap * 2))
    return score


def _packet_metric_index(packet: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for domain, metrics in (packet.get("domains") or {}).items():
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            data_type = str(metric.get("data_type") or "")
            if data_type:
                result[data_type] = {
                    "label": str(metric.get("label") or data_type),
                    "domain": str(domain),
                }
    return result


def _matched_data_types(packet: dict[str, Any], question: str) -> list[str]:
    query = _normalize(question)
    scored: list[tuple[int, str]] = []
    for data_type, info in _packet_metric_index(packet).items():
        score = _metric_score(query, data_type, info["label"])
        if score:
            scored.append((score, data_type))
    if not scored:
        return []
    scored.sort(key=lambda row: (-row[0], row[1]))
    best = scored[0][0]
    return [data_type for score, data_type in scored if score >= 6 or score >= best - 1]


def _matched_domains(question: str) -> list[str]:
    query = _normalize(question)
    return [
        domain
        for domain, aliases in _DOMAIN_ALIASES.items()
        if any(_contains_phrase(query, alias) for alias in aliases)
    ]


def _has_global_hint(question: str) -> bool:
    query = _normalize(question)
    return any(_contains_phrase(query, hint) for hint in _GLOBAL_HINTS)


def classify_retrieval_request(
    packet: dict[str, Any], question: str, analysis_mode: str = "question"
) -> dict[str, Any]:
    """Classify the request deterministically without another model call."""

    if analysis_mode == "deep" or not question.strip():
        return {"mode": "global", "data_types": [], "domains": []}

    # Explicit metrics and domains take priority over generic wording such as
    # "general analysis". This makes "general analysis of my activity" a domain query.
    data_types = _matched_data_types(packet, question)
    if data_types:
        return {"mode": "specific_metrics", "data_types": data_types, "domains": []}

    domains = _matched_domains(question)
    if domains:
        return {"mode": "domain", "data_types": [], "domains": domains}

    if _has_global_hint(question):
        return {"mode": "global", "data_types": [], "domains": []}

    return {"mode": "general_question", "data_types": [], "domains": []}


def _selected_type_set(
    packet: dict[str, Any], data_types: list[str], domains: list[str]
) -> set[str]:
    selected = set(data_types)
    for data_type, info in _packet_metric_index(packet).items():
        if info["domain"] in domains:
            selected.add(data_type)
    return selected


def _filter_domains(
    packet: dict[str, Any], selected_types: set[str], selected_domains: set[str]
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for domain, metrics in (packet.get("domains") or {}).items():
        if not isinstance(metrics, list):
            continue
        if domain in selected_domains:
            kept = copy.deepcopy(metrics)
        else:
            kept = [
                copy.deepcopy(metric)
                for metric in metrics
                if isinstance(metric, dict)
                and str(metric.get("data_type") or "") in selected_types
            ]
        if kept:
            result[str(domain)] = kept
    return result


def _compact_coverage_row(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "data_type",
        "label",
        "observed_calendar_days",
        "coverage_percent",
        "missing_calendar_days",
        "longest_missing_run_days",
    )
    return {key: item[key] for key in keys if key in item and item[key] is not None}


def _filter_coverage(coverage: Any, selected_types: set[str]) -> Any:
    if not isinstance(coverage, dict):
        return copy.deepcopy(coverage)
    result = copy.deepcopy(coverage)
    limited = result.get("limited_daily_metrics")
    if isinstance(limited, list) and selected_types:
        result["limited_daily_metrics"] = [
            copy.deepcopy(item)
            for item in limited
            if isinstance(item, dict)
            and str(item.get("data_type") or "") in selected_types
        ]
    return result


def _filter_insights(insights: Any, selected_types: set[str]) -> list[dict[str, Any]]:
    if not isinstance(insights, list):
        return []
    result = []
    for item in insights:
        if not isinstance(item, dict):
            continue
        data_types = {str(value) for value in item.get("data_types") or []}
        if not data_types or not selected_types or data_types & selected_types:
            result.append(copy.deepcopy(item))
    return result


def _filter_associations(
    associations: Any,
    selected_types: set[str],
    *,
    association_question: bool,
) -> list[dict[str, Any]]:
    if not isinstance(associations, list):
        return []
    if not selected_types:
        return copy.deepcopy(associations)
    result = []
    for item in associations:
        if not isinstance(item, dict):
            continue
        endpoints = {
            str(item.get("left_data_type") or ""),
            str(item.get("right_data_type") or ""),
        }
        if len(selected_types) >= 2 and association_question:
            keep = endpoints <= selected_types
        else:
            keep = bool(endpoints & selected_types)
        if keep:
            result.append(copy.deepcopy(item))
    return result


def _filter_for_scope(
    packet: dict[str, Any],
    *,
    selected_types: set[str],
    selected_domains: set[str],
    association_question: bool,
) -> dict[str, Any]:
    result = copy.deepcopy(packet)
    result["domains"] = _filter_domains(packet, selected_types, selected_domains)
    result["coverage"] = _filter_coverage(packet.get("coverage"), selected_types)
    result["strongest_evidence"] = _filter_insights(
        packet.get("strongest_evidence"), selected_types
    )
    result["associations"] = _filter_associations(
        packet.get("associations"),
        selected_types,
        association_question=association_question,
    )
    archive = result.get("archive_quality")
    if isinstance(archive, dict) and selected_types:
        truncated = archive.get("truncated_data_types")
        if isinstance(truncated, list):
            archive["truncated_data_types"] = [
                value for value in truncated if str(value) in selected_types
            ]
    return result


def _cap_lists(packet: dict[str, Any], profile: str) -> None:
    evidence_limit, association_limit, diagnostic_limit = _PROFILE_LIST_LIMITS[profile]
    evidence = packet.get("strongest_evidence")
    if isinstance(evidence, list):
        packet["strongest_evidence"] = evidence[:evidence_limit]
    associations = packet.get("associations")
    if isinstance(associations, list):
        packet["associations"] = associations[:association_limit]
    diagnostics = packet.get("association_diagnostics_for_request")
    if isinstance(diagnostics, list):
        packet["association_diagnostics_for_request"] = diagnostics[:diagnostic_limit]


def _drop_optional_metric_details(packet: dict[str, Any]) -> None:
    for metrics in (packet.get("domains") or {}).values():
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            metric.pop("structured", None)
            metric.pop("additional_fields", None)
            metric.pop("period_comparison", None)


def _lean_metric(metric: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(metric[key])
        for key in ("data_type", "label", "metric", "unit", "summary_scope", "data_role")
        if key in metric and metric[key] is not None
    }
    summary = metric.get("summary")
    if isinstance(summary, dict):
        result["summary"] = {
            key: copy.deepcopy(summary[key])
            for key in ("count", "latest", "mean", "median", "trend_percent", "anomaly_count")
            if key in summary and summary[key] is not None
        }
    coverage = metric.get("coverage")
    if isinstance(coverage, dict):
        result["coverage"] = {
            key: copy.deepcopy(coverage[key])
            for key in ("observed_calendar_days", "coverage_percent", "longest_missing_run_days")
            if key in coverage and coverage[key] is not None
        }
    evidence = metric.get("evidence")
    if isinstance(evidence, dict):
        lean_evidence: dict[str, Any] = {}
        matched = evidence.get("matched_change")
        if isinstance(matched, dict):
            lean_evidence["matched_change"] = {
                key: copy.deepcopy(matched[key])
                for key in ("window_days", "recent_days", "recent_mean", "previous_days", "previous_mean", "percent_change")
                if key in matched and matched[key] is not None
            }
        trend = evidence.get("trend")
        if isinstance(trend, dict):
            lean_evidence["trend"] = {
                key: copy.deepcopy(trend[key])
                for key in ("window_days", "observed_days", "direction", "percent_per_week", "r_squared")
                if key in trend and trend[key] is not None
            }
        anomaly = evidence.get("anomaly")
        if isinstance(anomaly, dict) and anomaly.get("latest_robust_z") is not None:
            lean_evidence["anomaly"] = {
                key: copy.deepcopy(anomaly[key])
                for key in ("baseline_samples", "baseline_median", "latest_date", "latest_robust_z")
                if key in anomaly and anomaly[key] is not None
            }
        if lean_evidence:
            result["evidence"] = lean_evidence
    today = metric.get("today")
    if isinstance(today, dict):
        result["today"] = {
            key: copy.deepcopy(today[key])
            for key in ("status", "today_so_far", "same_time_mean", "same_time_days", "same_time_percent")
            if key in today and today[key] is not None
        }
    if "domain" in metric:
        result["domain"] = metric["domain"]
    return result


def _make_metrics_lean(packet: dict[str, Any]) -> None:
    for domain, metrics in list((packet.get("domains") or {}).items()):
        if isinstance(metrics, list):
            packet["domains"][domain] = [
                _lean_metric(metric) for metric in metrics if isinstance(metric, dict)
            ]


def _lean_global_coverage(packet: dict[str, Any]) -> None:
    coverage = packet.get("coverage")
    if not isinstance(coverage, dict):
        return
    limited = coverage.get("limited_daily_metrics")
    if isinstance(limited, list):
        retained_types = {
            str(metric.get("data_type") or "")
            for metrics in (packet.get("domains") or {}).values()
            if isinstance(metrics, list)
            for metric in metrics
            if isinstance(metric, dict)
        }
        coverage["limited_daily_metrics"] = [
            _compact_coverage_row(item)
            for item in limited
            if isinstance(item, dict)
            and str(item.get("data_type") or "") in retained_types
        ]


def _compact_insight_for_budget(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(item[key])
        for key in ("kind", "data_types", "headline", "relevance_score", "confidence", "caveat")
        if key in item and item[key] is not None
    }
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        useful_keys = (
            "percent_change",
            "standardized_change",
            "observed_days",
            "direction",
            "percent_per_week",
            "r_squared",
            "latest_robust_z",
            "coverage_percent",
        )
        compact = {
            key: copy.deepcopy(evidence[key])
            for key in useful_keys
            if key in evidence and evidence[key] is not None
        }
        if compact:
            result["evidence"] = compact
    return result


def _make_evidence_lean(packet: dict[str, Any]) -> None:
    evidence = packet.get("strongest_evidence")
    if isinstance(evidence, list):
        packet["strongest_evidence"] = [
            _compact_insight_for_budget(item)
            for item in evidence
            if isinstance(item, dict)
        ]


def _trim_to_target(packet: dict[str, Any], target: int) -> None:
    if estimate_json_tokens(packet) <= target:
        return
    _drop_optional_metric_details(packet)

    evidence = packet.get("strongest_evidence")
    while isinstance(evidence, list) and len(evidence) > 3 and estimate_json_tokens(packet) > target:
        evidence.pop()

    associations = packet.get("associations")
    while isinstance(associations, list) and len(associations) > 1 and estimate_json_tokens(packet) > target:
        associations.pop()

    diagnostics = packet.get("association_diagnostics_for_request")
    while isinstance(diagnostics, list) and len(diagnostics) > 1 and estimate_json_tokens(packet) > target:
        diagnostics.pop()

    if estimate_json_tokens(packet) > target:
        _make_metrics_lean(packet)
        _make_evidence_lean(packet)
        _lean_global_coverage(packet)

    if estimate_json_tokens(packet) > target:
        packet.pop("archive_quality", None)

    # Preserve cross-domain breadth as long as possible. Only remove extra metrics
    # from a domain; a broad Fast request keeps at least one metric per domain.
    while estimate_json_tokens(packet) > target:
        candidates = [
            (domain, metrics)
            for domain, metrics in (packet.get("domains") or {}).items()
            if isinstance(metrics, list) and len(metrics) > 1
        ]
        if not candidates:
            break
        domain, metrics = max(candidates, key=lambda item: len(item[1]))
        packet["domains"][domain] = metrics[:-1]
        _lean_global_coverage(packet)

    # Last non-destructive budget reduction: the global coverage scalars and each
    # metric's own coverage already communicate data quality, so the repeated list
    # can be dropped without losing which selected metrics are sparse.
    if estimate_json_tokens(packet) > target:
        coverage = packet.get("coverage")
        if isinstance(coverage, dict):
            coverage.pop("limited_daily_metrics", None)


def _global_profile_packet(packet: dict[str, Any], profile: str) -> dict[str, Any]:
    result = copy.deepcopy(packet)
    domain_limit = 1 if profile == "fast" else 2 if profile == "standard" else None
    if domain_limit is not None:
        for domain, metrics in list((result.get("domains") or {}).items()):
            if isinstance(metrics, list):
                result["domains"][domain] = metrics[:domain_limit]
    _cap_lists(result, profile)
    if profile == "fast":
        _make_metrics_lean(result)
        _make_evidence_lean(result)
        _lean_global_coverage(result)
        result.pop("archive_quality", None)
    return result


def select_evidence_for_request(
    packet: dict[str, Any],
    question: str,
    *,
    performance_profile: str = "standard",
    analysis_mode: str = "question",
) -> dict[str, Any]:
    """Return request-relevant deterministic evidence without mutating the input."""

    profile = (
        performance_profile
        if performance_profile in PROFILE_EVIDENCE_TARGETS
        else "standard"
    )
    target = PROFILE_EVIDENCE_TARGETS[profile]
    intent = classify_retrieval_request(packet, question, analysis_mode)
    query = _normalize(question)
    association_question = any(hint in query for hint in _ASSOCIATION_HINTS)
    preserve_full = (
        intent["mode"] == "global"
        and profile == "max"
        and analysis_mode == "deep"
    )

    if intent["mode"] == "global":
        result = copy.deepcopy(packet) if profile == "max" else _global_profile_packet(packet, profile)
    elif intent["mode"] in {"specific_metrics", "domain"}:
        selected_domains = set(intent["domains"])
        selected_types = _selected_type_set(packet, intent["data_types"], intent["domains"])
        result = _filter_for_scope(
            packet,
            selected_types=selected_types,
            selected_domains=selected_domains,
            association_question=association_question,
        )
        _cap_lists(result, profile)
    else:
        result = _global_profile_packet(packet, profile)

    # This is the critical guarantee for desktop Maximum/deep: it gets exactly the
    # complete compact evidence produced by the existing pipeline, not a trimmed variant.
    if not preserve_full:
        _trim_to_target(result, target)

    metadata = result.setdefault("packet", {})
    if not isinstance(metadata, dict):
        metadata = {}
        result["packet"] = metadata
    metric_count = sum(
        len(metrics)
        for metrics in (result.get("domains") or {}).values()
        if isinstance(metrics, list)
    )
    metadata["retrieval_mode"] = intent["mode"]
    metadata["retrieval_profile"] = profile
    metadata["retrieval_target_tokens"] = target
    metadata["retrieval_metric_count"] = metric_count
    metadata["retrieval_preserved_full_packet"] = preserve_full
    if intent["data_types"]:
        metadata["retrieval_selected_data_types"] = list(intent["data_types"])
    if intent["domains"]:
        metadata["retrieval_selected_domains"] = list(intent["domains"])
    metadata["estimated_tokens"] = estimate_json_tokens(result)
    metadata["json_bytes"] = json_size_bytes(result)
    return result


def _ensure_adaptive_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    if _ORIGINAL_ENSURE is None:
        raise RuntimeError("Adaptive AI retrieval was not initialized")
    packet = _ORIGINAL_ENSURE(snapshot)
    return select_evidence_for_request(
        packet,
        _CURRENT_QUESTION.get(),
        performance_profile=_CURRENT_PROFILE.get(),
        analysis_mode=_CURRENT_ANALYSIS_MODE.get(),
    )


def _analyze_stream_adaptive(
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
    if _ORIGINAL_ANALYZE is None:
        raise RuntimeError("Adaptive AI retrieval was not initialized")
    question_token = _CURRENT_QUESTION.set(question)
    profile = getattr(self, "performance_profile", "standard")
    profile_token = _CURRENT_PROFILE.set(
        profile if profile in PROFILE_EVIDENCE_TARGETS else "standard"
    )
    mode_token = _CURRENT_ANALYSIS_MODE.set(analysis_mode)
    try:
        return _ORIGINAL_ANALYZE(
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
            prompt_callback=prompt_callback,
        )
    finally:
        _CURRENT_ANALYSIS_MODE.reset(mode_token)
        _CURRENT_PROFILE.reset(profile_token)
        _CURRENT_QUESTION.reset(question_token)


def install_ai_adaptive_retrieval() -> None:
    """Install question-aware retrieval after the response-hygiene hooks."""

    global _INSTALLED, _ORIGINAL_ANALYZE, _ORIGINAL_ENSURE
    if _INSTALLED:
        return
    _ORIGINAL_ENSURE = ai_engine.ensure_compact_evidence
    _ORIGINAL_ANALYZE = ai_engine.OptimizedOllamaClient.analyze_stream
    ai_engine.ensure_compact_evidence = _ensure_adaptive_evidence
    ai_engine.OptimizedOllamaClient.analyze_stream = _analyze_stream_adaptive
    _INSTALLED = True
