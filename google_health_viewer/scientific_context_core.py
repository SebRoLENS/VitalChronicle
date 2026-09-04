"""Runtime integration of curated physiology with the adaptive AI evidence pipeline."""

from __future__ import annotations

from typing import Any

from . import ai_adaptive_retrieval, ai_engine
from .scientific_context import (
    DATA_TYPE_TO_TOPIC,
    KNOWLEDGE_BASE_VERSION,
    scientific_context_for_types,
)

_INSTALLED = False
_ORIGINAL_SELECT = None

_SCIENCE_HINTS = (
    "what is",
    "what does",
    "meaning",
    "means",
    "explain",
    "why",
    "cause",
    "causes",
    "interpret",
    "physiology",
    "scientific",
    "cosa e",
    "cos e",
    "cosa significa",
    "significa",
    "spiega",
    "spiegami",
    "perche",
    "perché",
    "causa",
    "cause",
    "interpretare",
    "fisiologia",
    "scientifico",
)

_SCIENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "heart-rate": ("heart rate", "frequenza cardiaca", "battito", "battiti"),
    "daily-resting-heart-rate": ("resting heart rate", "rhr", "frequenza cardiaca a riposo"),
    "daily-heart-rate-variability": ("hrv", "heart rate variability", "variabilita cardiaca", "variabilità cardiaca"),
    "daily-oxygen-saturation": ("spo2", "oxygen saturation", "saturazione", "ossigenazione"),
    "daily-respiratory-rate": ("respiratory rate", "breathing rate", "frequenza respiratoria", "respirazione"),
    "daily-sleep-temperature-derivations": ("skin temperature", "sleep temperature", "temperatura cutanea", "temperatura nel sonno", "temperatura"),
    "sleep": ("sleep", "sonno", "awakening", "awakenings", "risveglio", "risvegli", "rem", "deep sleep", "sonno profondo"),
    "daily-vo2-max": ("vo2 max", "vo2max", "capacita aerobica", "capacità aerobica"),
    "exercise": ("exercise", "workout", "training", "allenamento", "esercizio"),
    "steps": ("steps", "passi"),
    "active-minutes": ("active minutes", "minuti attivi", "attivita fisica", "attività fisica"),
    "weight": ("weight", "peso"),
    "body-fat": ("body fat", "grasso corporeo"),
    "blood-glucose": ("blood glucose", "glucose", "glicemia", "glucosio"),
    "hydration-log": ("hydration", "idratazione", "acqua"),
    "nutrition-log": ("nutrition", "diet", "food", "nutrizione", "dieta", "alimentazione"),
    "altitude": ("altitude", "elevation", "altitudine", "quota"),
    "electrocardiogram": ("ecg", "electrocardiogram", "elettrocardiogramma"),
    "irregular-rhythm-notification": ("irregular rhythm", "arrhythmia", "aritmia", "ritmo irregolare"),
}


def _normalize(text: str) -> str:
    return ai_adaptive_retrieval._normalize(text)


def _science_question(question: str) -> bool:
    query = _normalize(question)
    return any(_normalize(hint) in query for hint in _SCIENCE_HINTS)


def _question_science_types(question: str) -> list[str]:
    query = _normalize(question)
    matched: list[str] = []
    for data_type, aliases in _SCIENCE_ALIASES.items():
        if any(_normalize(alias) in query for alias in aliases):
            matched.append(data_type)
    return matched


def _present_data_types(packet: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for metrics in (packet.get("domains") or {}).values():
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if isinstance(metric, dict) and metric.get("data_type"):
                value = str(metric["data_type"])
                if value not in result:
                    result.append(value)
    return result


def _evidence_data_types(packet: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for insight in packet.get("strongest_evidence") or []:
        if not isinstance(insight, dict):
            continue
        for value in insight.get("data_types") or []:
            data_type = str(value)
            if data_type in DATA_TYPE_TO_TOPIC and data_type not in result:
                result.append(data_type)
    return result


def _scientific_types_for_request(
    result: dict[str, Any], question: str, analysis_mode: str
) -> tuple[list[str], bool]:
    metadata = result.get("packet") or {}
    retrieval_mode = str(metadata.get("retrieval_mode") or "")
    question_types = _question_science_types(question)
    present = _present_data_types(result)

    if question_types:
        # Scientific explanations remain available even if that metric has no personal data.
        return question_types, _science_question(question)
    if retrieval_mode == "specific_metrics":
        selected = [str(value) for value in metadata.get("retrieval_selected_data_types") or []]
        return selected or present, _science_question(question)
    if retrieval_mode == "domain":
        return present[:6], False
    if analysis_mode == "deep" or retrieval_mode == "global":
        evidence_types = _evidence_data_types(result)
        return (evidence_types or present)[:8], False
    return _evidence_data_types(result)[:4], False


def _attach_scientific_context(
    result: dict[str, Any],
    question: str,
    *,
    performance_profile: str,
    analysis_mode: str,
) -> dict[str, Any]:
    data_types, wants_detail = _scientific_types_for_request(result, question, analysis_mode)
    if not data_types:
        return result

    # Full explanatory cards are reserved for focused scientific/"why" questions.
    detailed = wants_detail and len(data_types) <= 2 and performance_profile != "fast"
    maximum = 2 if detailed else (4 if performance_profile == "fast" else 6 if performance_profile == "standard" else 8)
    contexts = scientific_context_for_types(data_types, detailed=detailed, maximum=maximum)
    if not contexts:
        return result

    result["scientific_context"] = {
        "knowledge_base_version": KNOWLEDGE_BASE_VERSION,
        "role": (
            "Curated general scientific background, not evidence that any listed mechanism "
            "or cause applies to this user. Match explanations to the measured evidence."
        ),
        "model_knowledge_rule": (
            "The model may add established general scientific knowledge beyond this catalogue, "
            "but must label unsupported user-specific causes as possibilities and must not invent "
            "measurements, symptoms, diagnoses or clinical thresholds."
        ),
        "metrics": contexts,
    }

    target = ai_adaptive_retrieval.PROFILE_EVIDENCE_TARGETS.get(
        performance_profile,
        ai_adaptive_retrieval.PROFILE_EVIDENCE_TARGETS["standard"],
    )
    # Give the existing evidence trimmer first chance to remove redundant optional details.
    ai_adaptive_retrieval._trim_to_target(result, target)
    metrics = result.get("scientific_context", {}).get("metrics")
    while (
        isinstance(metrics, dict)
        and len(metrics) > 1
        and ai_adaptive_retrieval.estimate_json_tokens(result) > target
    ):
        metrics.pop(next(reversed(metrics)))
    if ai_adaptive_retrieval.estimate_json_tokens(result) > target and not detailed:
        result.pop("scientific_context", None)
    return result


def _select_with_science(
    packet: dict[str, Any],
    question: str,
    *,
    performance_profile: str = "standard",
    analysis_mode: str = "question",
) -> dict[str, Any]:
    if _ORIGINAL_SELECT is None:
        raise RuntimeError("Scientific context integration was not initialized")
    result = _ORIGINAL_SELECT(
        packet,
        question,
        performance_profile=performance_profile,
        analysis_mode=analysis_mode,
    )
    result = _attach_scientific_context(
        result,
        question,
        performance_profile=performance_profile,
        analysis_mode=analysis_mode,
    )
    metadata = result.get("packet")
    if isinstance(metadata, dict):
        metadata["scientific_context_version"] = KNOWLEDGE_BASE_VERSION
        metadata["estimated_tokens"] = ai_adaptive_retrieval.estimate_json_tokens(result)
        metadata["json_bytes"] = ai_adaptive_retrieval.json_size_bytes(result)
    return result


_SCIENTIFIC_SYSTEM_PROMPT = """You are VitalChronicle's local health-data synthesis module.
The supplied JSON can contain two different epistemic layers:
1) measured/derived personal evidence (coverage, domains, strongest_evidence, associations), and
2) scientific_context, which is curated general background about what metrics mean and which
mechanisms or confounders can plausibly affect them.

Personal evidence tells you what happened to this user. scientific_context does NOT prove that any
listed cause applies to this user. You may also use your own established general scientific knowledge
to explain physiology or add plausible mechanisms, especially when the user asks a scientific
question. Clearly distinguish general knowledge and plausible explanations from user-specific facts.
Never invent measurements, symptoms or events. If your internal knowledge conflicts with supplied
curated scientific_context, prefer the supplied context and express uncertainty.

Read coverage first, then domains, strongest_evidence, associations and relevant scientific_context.
Lead with the strongest useful finding and quantify supported changes. Cite evidence_id values in
square brackets for important user-specific claims. A higher or lower value is not automatically
better or worse. Missing data are not zero. If observation says the current day is incomplete, do not
compare partial totals with complete days or extrapolate them linearly. Limit personal conclusions to
dates and metrics actually covered by the packet.

When explaining a variation, rank explanations by how well they fit concurrent measured data. For
example, an increase in heart rate overlapping an exercise session has direct contextual support;
infection, dehydration, heat or stress remain possibilities unless corroborated by other supplied
signals. Multi-metric patterns can strengthen plausibility but remain nonspecific.

Associations are exploratory and do not prove causation, prediction or diagnosis. Wearable data can
contain measurement error and weak coverage lowers confidence. Do not diagnose, prescribe, change
treatment, or invent clinical thresholds. If a potentially relevant pattern deserves follow-up, say
which measurements could be reviewed with a qualified professional.

For complete-history analysis, synthesize across all available domains without creating sections for
absent domains. Prefer sustained matched-period changes, personal baselines, robust anomalies and
cross-domain patterns over isolated values. End with important uncertainty and a short list of useful
things to monitor.
"""


def _scientific_system_prompt() -> str:
    language = ai_engine.RESPONSE_LANGUAGE_NAMES.get(ai_engine.current_language(), "English")
    return (
        _SCIENTIFIC_SYSTEM_PROMPT
        + "\nRespond to the user in "
        + language
        + ". Keep JSON field names, evidence_id values and scientific source identifiers unchanged."
    )


def install_scientific_context_core() -> None:
    """Install selective scientific retrieval and a non-restrictive evidence hierarchy."""
    global _INSTALLED, _ORIGINAL_SELECT
    if _INSTALLED:
        return
    _ORIGINAL_SELECT = ai_adaptive_retrieval.select_evidence_for_request
    ai_adaptive_retrieval.select_evidence_for_request = _select_with_science
    ai_engine.compact_system_prompt = _scientific_system_prompt
    _INSTALLED = True
