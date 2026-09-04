"""Clarify user intent and daily-record completeness for local AI requests.

This layer runs after the scientific-context hooks.  It keeps pure scientific
questions separate from personal-data analysis and prevents the clock being in
the middle of a day from making already-emitted Google Health Daily records look
partial to the language model.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from . import ai_adaptive_retrieval, ai_engine
from .constants import DATA_TYPE_BY_KEY

_INSTALLED = False
_ORIGINAL_SELECT: Callable[..., dict[str, Any]] | None = None
_ORIGINAL_SYSTEM_PROMPT: Callable[[], str] | None = None

_DEFINITION_PREFIXES = (
    "what is ",
    "what are ",
    "define ",
    "explain ",
    "what does ",
    "cosa e ",
    "cos e ",
    "che cos e ",
    "che cosa e ",
    "cosa significa ",
    "che cosa significa ",
    "spiega ",
    "spiegami ",
)

# These make a superficially definitional sentence personal/analytical instead.
_PERSONAL_OR_ANALYTICAL_HINTS = (
    " my ",
    " mine ",
    " mio ",
    " mia ",
    " miei ",
    " mie ",
    " nel mio ",
    " nella mia ",
    " nei miei ",
    " nelle mie ",
    " today ",
    " yesterday ",
    " oggi ",
    " ieri ",
    " latest ",
    " ultimo ",
    " ultima ",
    " value ",
    " valore ",
    " data ",
    " dati ",
    " increase ",
    " increased ",
    " decrease ",
    " decreased ",
    " higher ",
    " lower ",
    " rise ",
    " fall ",
    " aument",
    " dimin",
    " sces",
    " salit",
    " bass",
    " alt",
    " trend ",
    " varia",
    " baseline ",
    " rispetto ",
    " why ",
    " perche ",
    " correl",
    " associa",
)

_SEMANTICS_PROMPT_APPENDIX = """

Request-semantics rules:
- If packet.response_mode is scientific_definition, answer the scientific concept directly. Explain
  what the metric measures physiologically, how it is commonly interpreted, what higher/lower values
  can reflect, important confounders and wearable limitations. Use supplied scientific_context and
  your own established scientific knowledge. Do NOT discuss the user's personal values, coverage,
  dates, trends or missing days unless the user explicitly asks for personal-data interpretation.
- observation.calendar_day_in_progress describes clock time only. It does NOT mean every metric for
  today is partial.
- Google Health record types marked record_type=daily are atomic Daily summaries. If a Daily record
  for today is present, treat the returned value as the complete value emitted for that date; never
  prorate, scale or discount it using elapsed clock-day percentage.
- A metric should be called partial only when that metric has its own explicit today/temporal status
  saying it is partial (for example an intraday cumulative total such as steps). Do not infer metric
  incompleteness merely because the current calendar day is still in progress.
"""


def _plain_text(text: str) -> str:
    normalized = ai_adaptive_retrieval._normalize(text)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _canonicalize_metric_typos(question: str) -> str:
    # A frequent transposition in Italian user input: HVR -> HRV.
    return re.sub(r"(?i)\bhvr\b", "HRV", question)


def _science_types(question: str) -> list[str]:
    from . import scientific_context_core

    return scientific_context_core._question_science_types(
        _canonicalize_metric_typos(question)
    )


def _is_pure_definition(question: str) -> bool:
    canonical = _canonicalize_metric_typos(question)
    if not _science_types(canonical):
        return False
    plain = _plain_text(canonical)
    padded = f" {plain} "
    if not any(plain.startswith(prefix) for prefix in _DEFINITION_PREFIXES):
        return False
    return not any(hint in padded or hint in plain for hint in _PERSONAL_OR_ANALYTICAL_HINTS)


def _annotate_daily_record_semantics(result: dict[str, Any]) -> None:
    daily_types: list[str] = []
    for metrics in (result.get("domains") or {}).values():
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            data_type = str(metric.get("data_type") or "")
            spec = DATA_TYPE_BY_KEY.get(data_type)
            if spec is None or spec.record_type != "daily":
                continue
            metric["record_semantics"] = {
                "record_type": "daily",
                "clock_day_proration": "not_applicable",
                "interpretation": (
                    "Treat a returned Daily record as the complete value emitted for its date; "
                    "do not scale or discount it by elapsed clock-day percentage."
                ),
            }
            daily_types.append(data_type)

    metadata = result.get("packet")
    if isinstance(metadata, dict) and daily_types:
        metadata["daily_summary_data_types"] = sorted(set(daily_types))


def _fix_observation_semantics(result: dict[str, Any]) -> None:
    observation = result.get("observation")
    if not isinstance(observation, dict):
        return
    day_in_progress = bool(
        observation.pop("current_day_is_incomplete", False)
        or observation.get("selected_period_includes_today")
    )
    # Elapsed-day percentage is useful only for explicitly cumulative metrics, which
    # already carry their own metric-level `today` context. Globally it invites the
    # model to discount Daily summaries such as HRV, SpO2 or resting heart rate.
    observation.pop("elapsed_day_percent", None)
    if day_in_progress:
        observation["calendar_day_in_progress"] = True
        observation["metric_completeness_rule"] = (
            "Clock-day progress alone does not make a metric partial. Only a metric-level "
            "today/temporal status may mark an intraday metric partial; returned Daily records "
            "must not be prorated by clock time."
        )


def _science_only_packet(
    result: dict[str, Any], question: str
) -> dict[str, Any]:
    science = copy.deepcopy(result.get("scientific_context") or {})
    metadata = copy.deepcopy(result.get("packet") or {})
    metadata["response_mode"] = "scientific_definition"
    metadata["personal_evidence_included"] = False
    metadata["definition_data_types"] = _science_types(question)
    minimal: dict[str, Any] = {
        "packet": metadata,
        "scientific_context": science,
    }
    metadata["estimated_tokens"] = ai_adaptive_retrieval.estimate_json_tokens(minimal)
    metadata["json_bytes"] = ai_adaptive_retrieval.json_size_bytes(minimal)
    return minimal


def _select_with_query_semantics(
    packet: dict[str, Any],
    question: str,
    *,
    performance_profile: str = "standard",
    analysis_mode: str = "question",
) -> dict[str, Any]:
    if _ORIGINAL_SELECT is None:
        raise RuntimeError("AI query semantics were not initialized")

    canonical_question = _canonicalize_metric_typos(question)
    result = _ORIGINAL_SELECT(
        packet,
        canonical_question,
        performance_profile=performance_profile,
        analysis_mode=analysis_mode,
    )

    _fix_observation_semantics(result)
    _annotate_daily_record_semantics(result)

    if analysis_mode == "question" and _is_pure_definition(question):
        return _science_only_packet(result, question)

    metadata = result.get("packet")
    if isinstance(metadata, dict):
        metadata["response_mode"] = "personal_analysis"
        metadata["estimated_tokens"] = ai_adaptive_retrieval.estimate_json_tokens(result)
        metadata["json_bytes"] = ai_adaptive_retrieval.json_size_bytes(result)
    return result


def _system_prompt_with_query_semantics() -> str:
    if _ORIGINAL_SYSTEM_PROMPT is None:
        raise RuntimeError("AI query semantics were not initialized")
    return _ORIGINAL_SYSTEM_PROMPT() + _SEMANTICS_PROMPT_APPENDIX


def install_ai_query_semantics() -> None:
    """Install intent separation and correct Daily-record completeness semantics."""

    global _INSTALLED, _ORIGINAL_SELECT, _ORIGINAL_SYSTEM_PROMPT
    if _INSTALLED:
        return
    _ORIGINAL_SELECT = ai_adaptive_retrieval.select_evidence_for_request
    _ORIGINAL_SYSTEM_PROMPT = ai_engine.compact_system_prompt
    ai_adaptive_retrieval.select_evidence_for_request = _select_with_query_semantics
    ai_engine.compact_system_prompt = _system_prompt_with_query_semantics
    _INSTALLED = True
