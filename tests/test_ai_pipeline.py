from google_health_viewer.ai_pipeline import (
    PIPELINE_VERSION,
    build_compact_evidence,
    ensure_compact_evidence,
    estimate_json_tokens,
)


def _rich_snapshot():
    metrics = []
    coverage_metrics = []
    types = [
        ("steps", "Steps"),
        ("sleep", "Sleep"),
        ("daily-resting-heart-rate", "Resting heart rate"),
        ("daily-heart-rate-variability", "HRV"),
        ("daily-oxygen-saturation", "Oxygen saturation"),
        ("weight", "Weight"),
        ("exercise", "Exercise"),
    ]
    for index, (data_type, label) in enumerate(types):
        metrics.append(
            {
                "data_type": data_type,
                "label": label,
                "metric": "value",
                "unit": "u",
                "summary": {
                    "count": 1000 + index,
                    "latest": 10 + index,
                    "mean": 9.5 + index,
                    "median": 9.4 + index,
                    "minimum": 2,
                    "maximum": 20,
                    "trend_percent": 5.0 + index,
                    "anomaly_count": 2,
                },
                "derived_evidence": {
                    "matched_recent_comparison": {
                        "window_days": 7,
                        "recent_days": 7,
                        "recent_mean": 11,
                        "previous_days": 7,
                        "previous_mean": 10,
                        "percent_change": 10.0,
                        "standardized_change": 0.7,
                    },
                    "trend": {
                        "window_days": 28,
                        "observed_days": 26,
                        "direction": "upward",
                        "percent_per_week": 3.2,
                        "r_squared": 0.45,
                    },
                    "robust_anomaly_check": {
                        "window_days": 90,
                        "baseline_samples": 70,
                        "baseline_median": 10,
                        "latest_date": "2026-08-31",
                        "latest_robust_z": 2.7,
                        "anomalies": [
                            {"date": f"2026-08-{day:02d}", "value": 20 + day, "robust_z": 3.1}
                            for day in range(1, 20)
                        ],
                    },
                    "personal_baselines": {
                        "7_days": {"samples": 7, "mean": 10, "standard_deviation": 1.2},
                        "28_days": {"samples": 27, "mean": 9.8, "standard_deviation": 1.4},
                    },
                    "data_quality": {
                        "observed_days": 80,
                        "coverage_percent": 88.9,
                        "longest_gap_days": 2,
                    },
                },
                "structured_details": {
                    "very_large_unused_list": [f"detail-{number}" for number in range(1000)],
                    "category_totals": {f"category-{number}": number for number in range(50)},
                },
            }
        )
        coverage_metrics.append(
            {
                "data_type": data_type,
                "label": label,
                "observed_calendar_days": 80,
                "coverage_percent": 88.9,
                "records_considered": 1000,
                "missing_calendar_days": 10,
                "longest_missing_run_days": 2,
                "missing_date_ranges": [
                    {"start": f"2026-06-{day:02d}", "end": f"2026-06-{day:02d}"}
                    for day in range(1, 29)
                ],
            }
        )
    return {
        "analysis_scope": "all_local_history",
        "period": {"start": "2026-01-01", "end": "2026-09-01"},
        "observation_context": {"observed_at": "2026-09-01T10:00:00+02:00"},
        "metrics": metrics,
        "requested_interval_coverage": {
            "requested_start": "2026-01-01",
            "requested_end": "2026-08-31",
            "requested_calendar_days": 243,
            "first_measurement_date": "2026-01-03",
            "last_measurement_date": "2026-08-31",
            "calendar_days_with_measurements": 220,
            "missing_measurement_calendar_days": 23,
            "measurement_gap_ranges_total": 17,
            "longest_measurement_gap_days": 3,
            "scope_is_partially_observed": True,
            "measurement_missing_date_ranges": [
                {"start": f"2026-02-{day:02d}", "end": f"2026-02-{day:02d}"}
                for day in range(1, 25)
            ],
            "metrics": coverage_metrics,
        },
        "candidate_insights": [
            {
                "evidence_id": f"change:{data_type}",
                "kind": "matched_period_change",
                "data_types": [data_type],
                "headline": f"{label} changed",
                "relevance_score": 90 - index,
                "confidence": "moderate",
                "evidence": {"percent_change": 10 + index},
            }
            for index, (data_type, label) in enumerate(types)
        ],
        "associations": [
            {
                "left": "Sleep",
                "right": "HRV",
                "left_data_type": "sleep",
                "right_data_type": "daily-heart-rate-variability",
                "r": 0.55,
                "paired_days": 60,
                "timing": "same_day",
                "reliability_score": 0.8,
            }
        ],
        "data_coverage": {
            "records_considered": {data_type: 50000 for data_type, _label in types},
            "truncated_data_types": [],
        },
    }


def test_compact_packet_is_domain_based_bounded_and_omits_long_gap_lists():
    packet = build_compact_evidence(_rich_snapshot())
    serialized = str(packet)

    assert packet["packet"]["pipeline_version"] == PIPELINE_VERSION
    assert packet["packet"]["health_evidence_present"] is True
    assert {"activity", "sleep", "heart", "vitals", "weight", "workouts"}.issubset(
        packet["domains"]
    )
    assert "missing_date_ranges" not in serialized
    assert "measurement_missing_date_ranges" not in serialized
    assert "very_large_unused_list" in serialized
    assert len(packet["domains"]["activity"][0].get("structured", {}).get("very_large_unused_list", [])) <= 6
    assert estimate_json_tokens(packet) <= 4200
    assert packet["packet"]["estimated_tokens"] <= 4200


def test_cached_packet_is_reused_until_snapshot_is_rebuilt():
    snapshot = _rich_snapshot()
    packet = build_compact_evidence(snapshot)
    snapshot["ai_compact_evidence"] = packet

    assert ensure_compact_evidence(snapshot) is packet
