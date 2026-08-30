from __future__ import annotations

import json
import string

from google_health_viewer import i18n


def test_supported_locale_and_english_fallback(monkeypatch):
    monkeypatch.setenv("VITALCHRONICLE_LANGUAGE", "it_IT.UTF-8")
    assert i18n.system_language() == "it"
    monkeypatch.setenv("VITALCHRONICLE_LANGUAGE", "de_DE.UTF-8")
    assert i18n.system_language() == "en"


def test_catalogues_are_valid_and_have_identical_keys():
    catalogues = {}
    for language in i18n.supported_languages():
        path = i18n.CATALOGUE_DIR / f"{language}.json"
        catalogues[language] = json.loads(path.read_text(encoding="utf-8"))
    assert set(catalogues["it"]) == set(catalogues["en"])
    assert all(catalogues["it"].values())
    formatter = string.Formatter()
    for message in catalogues["en"]:
        source_fields = {name for _text, name, _spec, _conv in formatter.parse(message) if name}
        for language, catalogue in catalogues.items():
            translated_fields = {
                name
                for _text, name, _spec, _conv in formatter.parse(catalogue[message])
                if name
            }
            assert translated_fields == source_fields, (language, message)


def test_translation_and_formatting(monkeypatch):
    monkeypatch.setattr(i18n, "_language", "it")
    monkeypatch.setattr(i18n, "_catalogue", lambda _language: {"Hello {name}": "Ciao {name}"})
    assert i18n.tr("Hello {name}", name="Ada") == "Ciao Ada"
