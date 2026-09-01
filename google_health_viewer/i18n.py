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
SYSTEM_LANGUAGE = "system"

# Transitional UI rename: the source string still exists in the Weblate
# catalogues, but it now opens hardware detection, model recommendations,
# performance profiles, reasoning controls, and benchmarking—not just an
# installation guide. Keep the visible label accurate until the source key is
# migrated in the translation catalogues.
_UI_TEXT_OVERRIDES = {
    "Local installation guide": {
        "en": "Hardware & AI performance…",
        "it": "Hardware e prestazioni AI…",
    }
}


def _normalise_language(value: str | None) -> str:
    if not value:
        return ""
    return (
        value.strip()
        .split(".", 1)[0]
        .replace("-", "_")
        .split("_", 1)[0]
        .lower()
    )


def _system_language_candidates() -> tuple[str, ...]:
    """Return system locale candidates from desktop and process settings.

    Frozen Qt applications can report the generic ``C`` locale even when the
    desktop session is configured differently.  Fedora and many other Linux
    desktops expose the useful value through ``LANGUAGE`` or ``LC_*`` instead,
    while Qt's ``uiLanguages`` is the most reliable source on Windows/macOS.
    """

    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        for part in value.replace(";", ":").split(":"):
            normalised = _normalise_language(part)
            if normalised and normalised not in candidates:
                candidates.append(normalised)

    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        add(os.environ.get(variable))
    try:
        from PySide6.QtCore import QLocale

        system_locale = QLocale.system()
        for value in system_locale.uiLanguages():
            add(value)
        add(system_locale.name())
    except (ImportError, RuntimeError):
        pass
    try:
        add(locale.getlocale()[0])
    except (TypeError, ValueError):
        pass
    return tuple(candidates)


def supported_languages() -> tuple[str, ...]:
    languages = {DEFAULT_LANGUAGE}
    if CATALOGUE_DIR.is_dir():
        for path in CATALOGUE_DIR.glob("*.json"):
            if path.stem.lower() == DEFAULT_LANGUAGE:
                continue
            try:
                catalogue = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(catalogue, dict) and any(
                isinstance(value, str) and value.strip() for value in catalogue.values()
            ):
                languages.add(path.stem.lower())
    return tuple(sorted(languages))


def system_language() -> str:
    override = _normalise_language(os.environ.get("VITALCHRONICLE_LANGUAGE"))
    if override:
        return override if override in supported_languages() else DEFAULT_LANGUAGE
    supported = set(supported_languages())
    return next(
        (candidate for candidate in _system_language_candidates() if candidate in supported),
        DEFAULT_LANGUAGE,
    )


def startup_language(preference: str | None = SYSTEM_LANGUAGE) -> str:
    """Resolve the persisted preference, preserving the environment override."""

    if os.environ.get("VITALCHRONICLE_LANGUAGE"):
        return system_language()
    requested = _normalise_language(preference)
    if preference != SYSTEM_LANGUAGE and requested in supported_languages():
        return requested
    return system_language()


def language_name(language: str) -> str:
    """Return a native display name for a language catalogue."""

    code = _normalise_language(language)
    built_in = {"en": "English", "it": "Italiano"}
    if code in built_in:
        return built_in[code]
    try:
        from PySide6.QtCore import QLocale

        name = QLocale(code).nativeLanguageName().strip()
        if name:
            return name[0].upper() + name[1:]
    except (ImportError, RuntimeError):
        pass
    return code.upper()


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

    override = _UI_TEXT_OVERRIDES.get(message, {}).get(_language)
    translated = override or _catalogue(_language).get(message) or message
    return translated.format(**values) if values else translated


_ = tr
