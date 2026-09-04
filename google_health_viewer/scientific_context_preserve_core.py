"""Preserve Maximum/deep evidence breadth while adding compact scientific context."""

from __future__ import annotations

from typing import Any

from . import scientific_context_core
from .scientific_context import KNOWLEDGE_BASE_VERSION, scientific_context_for_types

_INSTALLED = False
_ORIGINAL_ATTACH = None


def _attach_preserving_maximum(
    result: dict[str, Any],
    question: str,
    *,
    performance_profile: str,
    analysis_mode: str,
) -> dict[str, Any]:
    if _ORIGINAL_ATTACH is None:
        raise RuntimeError("Scientific context preserve hook was not initialized")
    if performance_profile != "max" or analysis_mode != "deep":
        return _ORIGINAL_ATTACH(
            result,
            question,
            performance_profile=performance_profile,
            analysis_mode=analysis_mode,
        )

    data_types, _wants_detail = scientific_context_core._scientific_types_for_request(
        result, question, analysis_mode
    )
    if not data_types:
        return result
    contexts = scientific_context_for_types(data_types, detailed=False, maximum=8)
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
    # Maximum/deep has a pre-existing guarantee that every compact personal metric is preserved.
    # Scientific background is additive and must never evict deterministic evidence.
    return result


def install_scientific_context_preserve_core() -> None:
    global _INSTALLED, _ORIGINAL_ATTACH
    if _INSTALLED:
        return
    _ORIGINAL_ATTACH = scientific_context_core._attach_scientific_context
    scientific_context_core._attach_scientific_context = _attach_preserving_maximum
    _INSTALLED = True
