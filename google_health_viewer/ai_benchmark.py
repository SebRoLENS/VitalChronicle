"""Representative local benchmark for the compact VitalChronicle AI pipeline."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from .ai_hardware import BenchmarkResult, reasoning_value

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


def _synthetic_health_packet() -> dict[str, Any]:
    """Small non-personal packet shaped like real deterministic health evidence."""

    return {
        "packet": {
            "health_evidence_present": True,
            "pipeline_version": "benchmark-compact-health-v1",
            "analysis_scope": "synthetic_benchmark",
            "metric_count": 6,
        },
        "period": {"start": "2026-07-01", "end": "2026-08-29"},
        "coverage": {
            "requested_calendar_days": 59,
            "calendar_days_with_measurements": 56,
            "scope_is_partially_observed": True,
            "missing_measurement_calendar_days": 3,
            "longest_measurement_gap_days": 1,
        },
        "domains": {
            "activity": [
                {
                    "data_type": "steps",
                    "summary": {"mean": 7420, "latest": 8110},
                    "evidence": {
                        "matched_change": {
                            "recent_days": 7,
                            "recent_mean": 8030,
                            "previous_days": 7,
                            "previous_mean": 7110,
                            "percent_change": 12.9,
                            "standardized_change": 0.72,
                        },
                        "trend": {
                            "observed_days": 28,
                            "direction": "upward",
                            "percent_per_week": 3.1,
                            "r_squared": 0.38,
                        },
                    },
                },
                {
                    "data_type": "active-minutes",
                    "summary": {"mean": 46.2, "latest": 51.0},
                    "evidence": {
                        "matched_change": {"percent_change": 9.4, "recent_days": 7, "previous_days": 7}
                    },
                },
            ],
            "sleep": [
                {
                    "data_type": "sleep",
                    "summary": {"mean": 7.08, "median": 7.12, "latest": 6.84},
                    "evidence": {
                        "matched_change": {"percent_change": -4.8, "recent_days": 7, "previous_days": 7},
                        "data_quality": {"observed_days": 55, "coverage_percent": 93.2},
                    },
                    "structured": {
                        "sessions_with_stages": 52,
                        "stages": {
                            "DEEP": {"mean_hours_per_session": 1.18, "share_percent": 16.7},
                            "REM": {"mean_hours_per_session": 1.55, "share_percent": 21.9},
                        },
                    },
                }
            ],
            "heart": [
                {
                    "data_type": "daily-resting-heart-rate",
                    "summary": {"mean": 59.8, "latest": 61.0},
                    "evidence": {
                        "trend": {
                            "observed_days": 27,
                            "direction": "stable",
                            "percent_per_week": 0.7,
                            "r_squared": 0.08,
                        }
                    },
                },
                {
                    "data_type": "daily-heart-rate-variability",
                    "summary": {"mean": 48.6, "latest": 43.2},
                    "evidence": {
                        "anomaly": {
                            "baseline_samples": 48,
                            "baseline_median": 49.1,
                            "latest_robust_z": -2.3,
                        }
                    },
                },
            ],
            "vitals": [
                {
                    "data_type": "daily-oxygen-saturation",
                    "summary": {"mean": 96.7, "latest": 96.5},
                    "coverage": {"observed_calendar_days": 49, "coverage_percent": 83.1},
                }
            ],
        },
        "strongest_evidence": [
            {
                "evidence_id": "change:steps",
                "headline": "Steps: recent matched-period mean is higher",
                "relevance_score": 84.0,
                "confidence": "moderate",
                "evidence": {"percent_change": 12.9},
            },
            {
                "evidence_id": "anomaly:daily-heart-rate-variability",
                "headline": "HRV: latest observation is unusual for the personal baseline",
                "relevance_score": 62.0,
                "confidence": "moderate",
                "evidence": {"latest_robust_z": -2.3},
            },
        ],
        "associations": [
            {
                "left": "Sleep",
                "right": "Daily HRV",
                "r": 0.43,
                "paired_days": 47,
                "timing": "same_day",
                "reliability_score": 0.71,
            }
        ],
    }


def benchmark_model(
    model: str,
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: float = 180.0,
) -> BenchmarkResult:
    """Measure a short synthesis over representative compact health evidence."""

    packet = json.dumps(_synthetic_health_packet(), ensure_ascii=False, separators=(",", ":"))
    prompt = (
        "You are benchmarking a local health-data synthesis pipeline. The JSON below is synthetic "
        "and contains no real user data. In 4 concise bullet points: identify the two strongest "
        "patterns, state the important coverage limitation, mention the exploratory sleep/HRV "
        "association without implying causation, and cite the evidence_id values where available.\n\n"
        f"BEGIN_HEALTH_EVIDENCE_JSON\n{packet}\nEND_HEALTH_EVIDENCE_JSON"
    )
    started = time.monotonic()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": reasoning_value(model, "fast"),
            "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 192},
            "keep_alive": "5m",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    elapsed = max(0.001, time.monotonic() - started)
    count = int(payload.get("eval_count") or 0)
    duration_ns = int(payload.get("eval_duration") or 0)
    decode_seconds = duration_ns / 1_000_000_000 if duration_ns > 0 else elapsed
    speed = count / max(0.001, decode_seconds)
    return BenchmarkResult(
        model=model,
        tokens_per_second=speed,
        generated_tokens=count,
        elapsed_seconds=elapsed,
    )
