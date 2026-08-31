import json

from google_health_viewer.local_ai import (
    DEFAULT_MODEL,
    MODEL_OPTIONS,
    SYSTEM_PROMPT,
    OllamaClient,
    detected_hardware_profile,
    newer_model_suggestion,
    recommended_model,
)


def test_local_ai_profile_is_current_and_medically_guarded():
    assert DEFAULT_MODEL == "qwen3.5:9b"
    assert "Do not diagnose" in SYSTEM_PROMPT
    assert "correlations" in SYSTEM_PROMPT.lower()
    assert "same_time_mean" in SYSTEM_PROMPT
    assert "incomplete day" in SYSTEM_PROMPT


def test_hardware_profiles_offer_larger_models():
    assert recommended_model("cpu32") == "qwen3:30b-a3b"
    assert recommended_model("gpu16") == "qwen3.5:9b"
    assert "qwen3:14b" in MODEL_OPTIONS
    assert "qwen3.5:27b" in MODEL_OPTIONS
    assert "qwen3.6:35b-a3b" in MODEL_OPTIONS
    assert detected_hardware_profile(nvidia_available=False) == "cpu32"
    assert detected_hardware_profile(nvidia_available=True) == "gpu16"


def test_newer_generation_suggestion_respects_hardware_profile():
    assert newer_model_suggestion("qwen3:8b", "gpu16") == "qwen3.5:9b"
    assert newer_model_suggestion("qwen3:30b-a3b", "cpu32") == "qwen3.6:35b-a3b"
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


def test_status_detects_new_weights_for_installed_model(monkeypatch):
    class FakeResponse:
        def __init__(self, payload=None, digest=None):
            self._payload = payload or {}
            self.headers = {"Docker-Content-Digest": digest} if digest else {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **_kwargs):
        if url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "qwen3.5:9b", "digest": "sha256:old"}]})
        return FakeResponse(digest="sha256:new")

    monkeypatch.setattr("google_health_viewer.local_ai.requests.get", fake_get)
    status = OllamaClient(model="qwen3.5:9b", hardware_profile="gpu16").status()

    assert status.online
    assert status.update_available
    assert status.update_target == "qwen3.5:9b"
    assert "new weights" in status.update_message


def test_analysis_streams_thinking_and_final_answer(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode
            chunks = [
                {"message": {"thinking": "Controllo i dati. "}},
                {"message": {"thinking": "Confronto la baseline."}},
                {"message": {"content": "Risposta finale."}},
                {"done": True, "message": {"content": ""}},
            ]
            return [json.dumps(chunk) for chunk in chunks]

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("google_health_viewer.local_ai.requests.post", fake_post)
    thinking = []
    answer_chunks = []
    answer = OllamaClient(model="qwen3:8b").analyze_stream(
        {"metrics": [{"label": "Passi", "summary": {"latest": 5000}}]},
        thinking_callback=thinking.append,
        answer_callback=answer_chunks.append,
    )

    assert answer == "Risposta finale."
    assert "".join(thinking) == "Controllo i dati. Confronto la baseline."
    assert answer_chunks == ["Risposta finale."]
    assert captured["stream"] is True
    assert captured["json"]["think"] is True
    assert captured["json"]["stream"] is True


def test_generation_tokens_have_no_artificial_upper_limit(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            return [json.dumps({"message": {"content": "OK"}})]

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("google_health_viewer.local_ai.requests.post", fake_post)
    answer = OllamaClient(model="qwen3.5:9b").analyze_stream(
        {"metrics": [{"label": "Passi", "summary": {"latest": 5000}}]},
        max_tokens=20000,
    )

    assert answer == "OK"
    assert captured["json"]["options"]["num_predict"] == 20000
    assert captured["json"]["options"]["num_ctx"] == 40000


def test_analysis_retries_without_thinking_when_first_answer_is_empty(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __init__(self, chunks):
            self.chunks = chunks

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode
            return [json.dumps(chunk) for chunk in self.chunks]

    responses = [
        FakeResponse(
            [
                {"message": {"thinking": "Analizzo tutta la cronologia."}},
                {"done": True, "message": {"content": ""}},
            ]
        ),
        FakeResponse(
            [
                {"message": {"content": "Ecco la risposta recuperata."}},
                {"done": True, "message": {"content": ""}},
            ]
        ),
    ]

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("google_health_viewer.local_ai.requests.post", fake_post)
    thinking = []
    answer_chunks = []

    answer = OllamaClient(model="qwen3.5:9b").analyze_stream(
        {"metrics": [{"label": "Sonno", "summary": {"latest": 7.5}}]},
        thinking_callback=thinking.append,
        answer_callback=answer_chunks.append,
        max_tokens=4096,
    )

    assert answer == "Ecco la risposta recuperata."
    assert len(calls) == 2
    assert calls[0]["json"]["think"] is True
    assert calls[1]["json"]["think"] is False
    assert calls[0]["json"]["options"]["num_predict"] == 4096
    assert calls[1]["json"]["options"]["num_predict"] == 4096
    assert "Preparing the final answer" in "".join(thinking)
    assert answer_chunks == ["Ecco la risposta recuperata."]


def test_analysis_reports_ollama_error_body(monkeypatch):
    class FakeResponse:
        status_code = 500
        reason = "Internal Server Error"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def json(self):
            return {"error": "llama-server non disponibile"}

    monkeypatch.setattr(
        "google_health_viewer.local_ai.requests.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    try:
        OllamaClient(model="qwen3:8b").analyze({"metrics": [{"label": "Passi"}]})
    except Exception as exc:  # noqa: BLE001 - verify the user-facing boundary.
        assert "llama-server non disponibile" in str(exc)
    else:
        raise AssertionError("Expected the Ollama error to be propagated")


def test_deep_analysis_uses_evidence_selection_then_streams_final_answer(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __init__(self, chunks):
            self.chunks = chunks

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode
            return [json.dumps(chunk) for chunk in self.chunks]

    responses = [
        FakeResponse(
            [
                {"message": {"thinking": "Valuto la solidità."}},
                {"message": {"content": "Use change:steps and association:1."}},
            ]
        ),
        FakeResponse(
            [
                {"message": {"thinking": "Collego i domini."}},
                {"message": {"content": "Sintesi profonda [change:steps]."}},
            ]
        ),
    ]

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("google_health_viewer.local_ai.requests.post", fake_post)
    thinking = []
    answer_chunks = []
    answer = OllamaClient(model="qwen3.5:9b").analyze_stream(
        {
            "metrics": [{"data_type": "steps"}],
            "candidate_insights": [{"evidence_id": "change:steps"}],
        },
        analysis_mode="deep",
        thinking_callback=thinking.append,
        answer_callback=answer_chunks.append,
    )

    assert answer == "Sintesi profonda [change:steps]."
    assert len(calls) == 2
    assert "evidence plan" in calls[1]["json"]["messages"][-2]["content"].lower()
    assert "Evidence pass" in "".join(thinking)
    assert answer_chunks == ["Sintesi profonda [change:steps]."]


def test_follow_up_history_is_sent_before_current_question(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            return [json.dumps({"message": {"content": "Follow-up answer"}})]

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("google_health_viewer.local_ai.requests.post", fake_post)
    OllamaClient().analyze_stream(
        {"metrics": [{"data_type": "sleep"}]},
        "And what about HRV?",
        history=[
            {"role": "user", "content": "How is sleep?"},
            {"role": "assistant", "content": "Sleep is stable."},
        ],
    )

    messages = captured["json"]["messages"]
    assert messages[-3:] == [
        {"role": "user", "content": "How is sleep?"},
        {"role": "assistant", "content": "Sleep is stable."},
        {"role": "user", "content": "Current request: And what about HRV?"},
    ]
