from __future__ import annotations

import google_health_viewer.ai_model_catalog as catalog
from google_health_viewer.ai_model_catalog import (
    CURATED_MODEL_OPTIONS,
    _extract_model_families,
    is_cloud_model,
    newer_model_suggestion,
    recommended_model_for_hardware,
)


def test_catalog_includes_current_qwen_gemma_and_open_weight_options():
    assert "qwen3.8" in CURATED_MODEL_OPTIONS
    assert "gemma4:12b" in CURATED_MODEL_OPTIONS
    assert "gpt-oss:20b" in CURATED_MODEL_OPTIONS
    assert not any(is_cloud_model(model) for model in CURATED_MODEL_OPTIONS)


def test_official_library_parser_accepts_future_generations_and_rejects_cloud():
    html = '''
    <a href="/library/qwen3.8">Qwen 3.8</a>
    <a href="/library/qwen4">Qwen 4</a>
    <a href="/library/gemma5">Gemma 5</a>
    <a href="/library/random-model">Other</a>
    <a href="/library/gemma5-cloud">Cloud</a>
    '''
    models = _extract_model_families(html)
    assert "qwen4" in models
    assert "gemma5" in models
    assert "random-model" not in models
    assert all("cloud" not in model for model in models)


def test_future_qwen_generation_is_suggested_without_app_update_on_cpu_profile(monkeypatch):
    monkeypatch.setattr(catalog, "_remote_model_size_gb", lambda _model: None)
    assert newer_model_suggestion(
        "qwen3.8", "cpu32", catalog_models=("qwen3.8", "qwen4")
    ) == "qwen4"


def test_modern_hardware_ladder_keeps_small_gpu_conservative(monkeypatch):
    monkeypatch.setattr(catalog, "_read_cache", dict)
    assert recommended_model_for_hardware(
        ram_gb=16, vram_gb=8, has_gpu=True, profile="standard"
    ) == "qwen3.5:9b"
    assert recommended_model_for_hardware(
        ram_gb=16, vram_gb=8, has_gpu=True, profile="max"
    ) == "gemma4:12b"
