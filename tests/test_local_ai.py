from google_health_viewer.local_ai import (
    DEFAULT_MODEL,
    MODEL_OPTIONS,
    SYSTEM_PROMPT,
    OllamaClient,
    detected_hardware_profile,
    newer_model_suggestion,
    recommended_model,
    system_prompt,
)


def test_local_ai_profile_is_current_and_medically_guarded():
    assert DEFAULT_MODEL == "qwen3.5:9b"
    assert "Do not diagnose" in SYSTEM_PROMPT
    assert "correlations" in SYSTEM_PROMPT.lower()
    assert "same_time_mean" in SYSTEM_PROMPT
    assert "incomplete day" in SYSTEM_PROMPT
    assert "requested_interval_coverage" in SYSTEM_PROMPT
    assert "one observed week" in SYSTEM_PROMPT


def test_system_prompt_remains_english_and_only_response_language_changes():
    italian = system_prompt("it")
    french = system_prompt("fr")
    assert SYSTEM_PROMPT in italian
    assert SYSTEM_PROMPT in french
    assert "Respond to the user in Italian" in italian
    assert "Respond to the user in French" in french
    assert "Rispondi" not in italian


def test_hardware_profiles_offer_larger_models():
    # CPU/32 GB should prefer the current high-quality Qwen generation.
    assert recommended_model("cpu32") == "qwen3.8"
    assert recommended_model("gpu16") == "qwen3.5:9b"
    assert "qwen3:14b" in MODEL_OPTIONS
    assert "qwen3.5:27b" in MODEL_OPTIONS
    assert "qwen3.6:35b-a3b" in MODEL_OPTIONS
    assert detected_hardware_profile(nvidia_available=False) == "cpu32"
    assert detected_hardware_profile(nvidia_available=True) == "gpu16"


def test_newer_generation_suggestion_respects_hardware_profile():
    assert newer_model_suggestion("qwen3:8b", "gpu16") == "qwen3.5:9b"
    assert newer_model_suggestion("qwen3:30b-a3b", "cpu32") == "qwen3.8"
    assert newer_model_suggestion("qwen3.5:9b", "gpu16") is None


def test_token_recommendation_uses_ram_model_size_and_physical_context(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "google_health_viewer.local_ai.requests.post",
        lambda *_args, **_kwargs: FakeResponse({"model_info": {"qwen35.context_length": 32768}}),
    )
    monkeypatch.setattr(
        "google_health_viewer.local_ai.requests.get",
        lambda *_args, **_kwargs: FakeResponse(
            {"models": [{"name": "qwen3.5:9b", "size": 6 * 1024**3}]}
        ),
    )

    result = OllamaClient(model="qwen3.5:9b", hardware_profile="gpu16").token_recommendation(16)

    assert result.recommended_tokens == 8192
    assert result.recommended_context == 16384
    assert result.model_context_limit == 32768
    assert result.model_size_gb == 6.0
