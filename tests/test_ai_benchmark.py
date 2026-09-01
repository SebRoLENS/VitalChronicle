from google_health_viewer.ai_benchmark import benchmark_model


def test_health_benchmark_uses_compact_synthetic_health_packet(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "eval_count": 160,
                "eval_duration": 4_000_000_000,
            }

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("google_health_viewer.ai_benchmark.requests.post", fake_post)
    result = benchmark_model("qwen3:8b")

    payload = captured["json"]
    assert "BEGIN_HEALTH_EVIDENCE_JSON" in payload["prompt"]
    assert "daily-heart-rate-variability" in payload["prompt"]
    assert "sleep" in payload["prompt"]
    assert "steps" in payload["prompt"]
    assert "synthetic" in payload["prompt"].lower()
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["num_predict"] == 192
    assert result.generated_tokens == 160
    assert result.tokens_per_second == 40.0
