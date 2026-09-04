from __future__ import annotations

from google_health_viewer.ai_hardware import HardwareInfo
from google_health_viewer.ai_model_selector import (
    optimal_model_options,
    ordered_model_choices,
)


def _cpu_hardware(ram_gb: float) -> HardwareInfo:
    return HardwareInfo(
        os_name="Linux",
        cpu_name="Test CPU",
        cpu_cores=16,
        ram_gb=ram_gb,
    )


def test_cpu32_catalog_hides_models_that_are_too_small_or_too_large():
    models = optimal_model_options(
        (
            "qwen3:4b",
            "qwen3:8b",
            "qwen3:14b",
            "gpt-oss:20b",
            "qwen3.8",
            "qwen3.6:35b-a3b",
            "gpt-oss:120b",
        ),
        _cpu_hardware(32),
    )

    assert "qwen3.8" in models
    assert "gpt-oss:20b" in models
    assert "qwen3:4b" not in models
    assert "gpt-oss:120b" not in models


def test_installed_models_are_always_kept_first_even_outside_optimal_band():
    choices = ordered_model_choices(
        installed=("tiny-custom:latest", "huge-custom:latest"),
        catalog=("qwen3:4b", "qwen3.8", "gpt-oss:120b"),
        hardware=_cpu_hardware(32),
        last_used="huge-custom:latest",
    )

    assert choices[:2] == ("tiny-custom:latest", "huge-custom:latest")
    assert "qwen3.8" in choices


def test_last_used_model_is_preserved_if_it_was_removed_from_ollama():
    choices = ordered_model_choices(
        installed=("qwen3.8:latest",),
        catalog=("qwen3.8", "gpt-oss:20b"),
        hardware=_cpu_hardware(32),
        last_used="my-old-model:7b",
    )

    assert choices[0] == "qwen3.8:latest"
    assert choices[1] == "my-old-model:7b"


def test_midrange_machine_does_not_offer_very_large_catalog_models():
    models = optimal_model_options(
        ("qwen3:4b", "qwen3:8b", "qwen3.5:9b", "qwen3.8", "gpt-oss:120b"),
        _cpu_hardware(16),
    )

    assert "qwen3:8b" in models
    assert "qwen3.5:9b" in models
    assert "qwen3.8" not in models
    assert "gpt-oss:120b" not in models
