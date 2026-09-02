from __future__ import annotations

from google_health_viewer.ai_hardware import (
    HardwareInfo,
    legacy_hardware_profile,
    reasoning_value,
    recommend_model,
)


def hardware(*, ram: float, gpu: str = "", vendor: str = "", vram: float | None = None):
    return HardwareInfo(
        os_name="Test OS",
        cpu_name="Test CPU",
        cpu_cores=16,
        ram_gb=ram,
        gpu_name=gpu,
        gpu_vendor=vendor,
        vram_gb=vram,
    )


def test_rtx_4060_class_machine_gets_three_distinct_profiles():
    machine = hardware(ram=16, gpu="NVIDIA GeForce RTX 4060", vendor="NVIDIA", vram=8)

    assert recommend_model(machine, "fast").model == "qwen3:4b"
    assert recommend_model(machine, "standard").model == "qwen3.5:9b"
    assert recommend_model(machine, "max").model == "gemma4:12b"
    assert legacy_hardware_profile(machine) == "gpu16"


def test_cpu_32gb_uses_modern_local_models_for_standard_and_maximum():
    machine = hardware(ram=32)

    assert recommend_model(machine, "fast").model == "qwen3.5:9b"
    assert recommend_model(machine, "standard").model == "gemma4:12b"
    assert recommend_model(machine, "max").model == "qwen3.8"
    assert legacy_hardware_profile(machine) == "cpu32"


def test_small_cpu_machine_is_kept_on_compact_models():
    machine = hardware(ram=12)

    assert recommend_model(machine, "fast").model == "qwen3:4b"
    assert recommend_model(machine, "standard").model == "qwen3:4b"
    assert recommend_model(machine, "max").model == "qwen3:8b"


def test_gpt_oss_uses_programmable_reasoning_levels():
    assert reasoning_value("gpt-oss:20b", "fast") == "low"
    assert reasoning_value("gpt-oss:20b", "standard") == "medium"
    assert reasoning_value("gpt-oss:20b", "max") == "high"


def test_boolean_thinking_models_use_fast_off_and_standard_or_max_on():
    assert reasoning_value("qwen3.5:9b", "fast") is False
    assert reasoning_value("qwen3.5:9b", "standard") is True
    assert reasoning_value("qwen3.5:9b", "max") is True
