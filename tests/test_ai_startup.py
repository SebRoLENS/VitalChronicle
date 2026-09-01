from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSettings

from google_health_viewer import app as app_module
from google_health_viewer.ai_hardware import HardwareInfo


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def _machine() -> HardwareInfo:
    return HardwareInfo(
        os_name="Linux",
        cpu_name="Test CPU",
        cpu_cores=16,
        ram_gb=16.0,
        gpu_name="NVIDIA GeForce RTX 4060",
        gpu_vendor="NVIDIA",
        vram_gb=8.0,
    )


def test_startup_initializes_standard_recommendation_for_new_install(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr("google_health_viewer.ai_hardware.detect_hardware", _machine)

    app_module._initialize_hardware_aware_ai(settings)

    assert settings.value("ai/model") == "qwen3.5:9b"
    assert settings.value("ai/performance_profile") == "standard"
    assert settings.value("ai/automatic_model_selection", type=bool) is True
    assert settings.value("ai/hardware_profile") == "gpu16"
    assert settings.value("ai/ram_gb", type=int) == 16


def test_startup_preserves_existing_manual_model(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("ai/model", "custom-local-model:latest")
    monkeypatch.setattr(
        "google_health_viewer.ai_hardware.detect_hardware",
        lambda: replace(_machine(), ram_gb=64.0, vram_gb=24.0),
    )

    app_module._initialize_hardware_aware_ai(settings)

    assert settings.value("ai/model") == "custom-local-model:latest"
    assert not settings.contains("ai/hardware_ram_gb")


def test_existing_ai_user_sees_hardware_intro_once(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("ai/model", "qwen3.5:9b")

    assert app_module._should_show_hardware_ai_intro(settings, smoke_test=False) is True

    settings.setValue(app_module.HARDWARE_AI_INTRO_KEY, True)
    assert app_module._should_show_hardware_ai_intro(settings, smoke_test=False) is False


def test_hardware_intro_is_disabled_for_smoke_tests(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("ai/model", "qwen3.5:9b")

    assert app_module._should_show_hardware_ai_intro(settings, smoke_test=True) is False
