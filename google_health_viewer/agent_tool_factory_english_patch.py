from __future__ import annotations

import re
from typing import Any

from . import agent_tool_factory as factory

_INSTALLED = False

# Deliberately conservative domain/UI vocabulary. We reject clear Italian metadata rather than
# pretending to translate arbitrary prose deterministically. The model then repairs the SAME tool
# in English before schema/semantic validation and persistence continue.
_ITALIAN_METADATA_TOKENS = {
    "analisi",
    "allenamento",
    "allenamenti",
    "cardiaca",
    "confronta",
    "confronto",
    "consecutivi",
    "consecutivo",
    "dati",
    "della",
    "delle",
    "dopo",
    "durata",
    "frequenza",
    "giorni",
    "giorno",
    "mediana",
    "notte",
    "personale",
    "recupero",
    "riposo",
    "rispetto",
    "sonno",
    "successiva",
    "successivo",
    "tempo",
    "valori",
}


def _words(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", str(value or ""))
        if token
    }


def _metadata_language_errors(args: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("name", "description", "capability"):
        text = str(args.get(field) or "").strip()
        if not text:
            continue
        italian = sorted(_words(text) & _ITALIAN_METADATA_TOKENS)
        if italian:
            errors.append(
                f"{field} must be written in English; detected Italian metadata token(s): "
                + ", ".join(italian[:6])
            )
    return errors


def install_tool_factory_english_patch() -> None:
    """Keep newly persisted learned-tool metadata in English regardless of chat language."""

    global _INSTALLED
    if _INSTALLED:
        return

    name_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get("name")
    if isinstance(name_schema, dict):
        name_schema["description"] = (
            "Reusable English snake_case tool name. Always name learned tools in English, even "
            "when the user is speaking another language."
        )
    description_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get("description")
    if isinstance(description_schema, dict):
        description_schema["description"] = (
            "Concise English description of the reusable computation. Always write this metadata "
            "in English, independently of the conversation language."
        )
    capability_schema = factory.CREATE_LEARNED_TOOL_SCHEMA.get("properties", {}).get("capability")
    if isinstance(capability_schema, dict):
        capability_schema["description"] = (
            "Stable semantic capability written in English dot-separated terms, not the wording of "
            "one user question."
        )

    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool

    def english_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        errors = _metadata_language_errors(args)
        if errors:
            return {
                "status": "invalid_metadata",
                "repairable": True,
                "error": errors[0],
                "validation_errors": errors,
                "instruction": (
                    "Repair the SAME learned tool before any further analysis. Keep its semantics and "
                    "pipeline unchanged, but rewrite name, description and capability in English. "
                    "Use an English snake_case name and an English dot-separated capability. Then "
                    "submit create_learned_tool again so normal schema, semantic and dry-run validation "
                    "can continue."
                ),
            }
        return original_create(self, args, **kwargs)

    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = english_create
    _INSTALLED = True
