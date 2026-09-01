from __future__ import annotations

import json
import string

from google_health_viewer import i18n


def test_supported_locale_and_english_fallback(monkeypatch):
    monkeypatch.setenv("VITALCHRONICLE_LANGUAGE", "it_IT.UTF-8")
    assert i18n.system_language() == "it"
    monkeypatch.setenv("VITALCHRONICLE_LANGUAGE", "zz_ZZ.UTF-8")
    assert i18n.system_language() == "en"


def test_system_language_uses_fedora_environment_candidates(monkeypatch):
    monkeypatch.delenv("VITALCHRONICLE_LANGUAGE", raising=False)
    monkeypatch.setenv("LANGUAGE", "it_IT:en_US")
    monkeypatch.setenv("LC_ALL", "C")
    assert i18n.system_language() == "it"


def test_manual_preference_and_system_fallback(monkeypatch):
    monkeypatch.delenv("VITALCHRONICLE_LANGUAGE", raising=False)
    monkeypatch.setattr(i18n, "_system_language_candidates", lambda: ("it", "en"))
    assert i18n.startup_language("en") == "en"
    assert i18n.startup_language("system") == "it"
    assert i18n.startup_language("zz") == "it"


def test_environment_override_wins_over_saved_preference(monkeypatch):
    monkeypatch.setenv("VITALCHRONICLE_LANGUAGE", "it_IT.UTF-8")
    assert i18n.startup_language("en") == "it"


def test_empty_community_catalogue_is_not_advertised(monkeypatch, tmp_path):
    (tmp_path / "en.json").write_text('{"Hello": "Hello"}', encoding="utf-8")
    (tmp_path / "de.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fr.json").write_text('{"Hello": "Bonjour"}', encoding="utf-8")
    monkeypatch.setattr(i18n, "CATALOGUE_DIR", tmp_path)
    assert i18n.supported_languages() == ("en", "fr")


def test_catalogues_are_valid_and_preserve_placeholders():
    catalogues = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in i18n.CATALOGUE_DIR.glob("*.json")
    }
    assert "en" in catalogues
    assert "it" in catalogues
    assert set(catalogues["it"]) == set(catalogues["en"])
    assert all(catalogues["it"].values())
    assert all(message == translation for message, translation in catalogues["en"].items())
    assert not any(
        message.startswith("You are VitalChronicle's local health-data analysis module")
        for message in catalogues["en"]
    )
    formatter = string.Formatter()
    for language, catalogue in catalogues.items():
        assert isinstance(catalogue, dict)
        assert set(catalogue).issubset(catalogues["en"]), language
        for message, translation in catalogue.items():
            assert isinstance(message, str) and isinstance(translation, str)
            if not translation:
                continue
            source_fields = {
                name for _text, name, _spec, _conv in formatter.parse(message) if name
            }
            translated_fields = {
                name
                for _text, name, _spec, _conv in formatter.parse(translation)
                if name
            }
            assert translated_fields == source_fields, (language, message)


def test_translation_and_formatting(monkeypatch):
    monkeypatch.setattr(i18n, "_language", "it")
    monkeypatch.setattr(i18n, "_catalogue", lambda _language: {"Hello {name}": "Ciao {name}"})
    assert i18n.tr("Hello {name}", name="Ada") == "Ciao Ada"


def test_hardware_ai_setup_label_is_explicit(monkeypatch):
    monkeypatch.setattr(i18n, "_language", "en")
    assert i18n.tr("Local installation guide") == "Hardware & AI performance…"
    monkeypatch.setattr(i18n, "_language", "it")
    assert i18n.tr("Local installation guide") == "Hardware e prestazioni AI…"
