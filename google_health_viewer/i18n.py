"""Small, dependency-free translation layer for VitalChronicle.

Catalogues use Weblate's simple JSON format.  English is the source language
and the guaranteed fallback; the closest supported system language is chosen
at startup.  ``VITALCHRONICLE_LANGUAGE`` is intentionally supported for tests,
screenshots, and users who need to override an incorrectly detected locale.
"""

from __future__ import annotations

import json
import locale
import os
from functools import cache
from pathlib import Path
from typing import Any

CATALOGUE_DIR = Path(__file__).with_name("locales")
DEFAULT_LANGUAGE = "en"


def _normalise_language(value: str | None) -> str:
    if not value:
        return ""
    return value.split(".", 1)[0].replace("-", "_").split("_", 1)[0].lower()


def supported_languages() -> tuple[str, ...]:
    languages = {DEFAULT_LANGUAGE}
    if CATALOGUE_DIR.is_dir():
        languages.update(path.stem.lower() for path in CATALOGUE_DIR.glob("*.json"))
    return tuple(sorted(languages))


def system_language() -> str:
    override = _normalise_language(os.environ.get("VITALCHRONICLE_LANGUAGE"))
    if override:
        return override if override in supported_languages() else DEFAULT_LANGUAGE
    try:
        from PySide6.QtCore import QLocale

        detected = _normalise_language(QLocale.system().name())
    except (ImportError, RuntimeError):
        detected = _normalise_language(locale.getlocale()[0])
    return detected if detected in supported_languages() else DEFAULT_LANGUAGE


_language = system_language()


@cache
def _catalogue(language: str) -> dict[str, str]:
    path = CATALOGUE_DIR / f"{language}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError(f"Invalid translation catalogue: {path}")
    return data


def set_language(language: str | None) -> str:
    """Select a supported language, falling back to English."""

    global _language
    requested = _normalise_language(language)
    _language = requested if requested in supported_languages() else DEFAULT_LANGUAGE
    return _language


def current_language() -> str:
    return _language


def tr(message: str, /, **values: Any) -> str:
    """Translate *message* and safely apply named format values."""

    translated = _catalogue(_language).get(message) or message
    return translated.format(**values) if values else translated


_ = tr
