"""Compact deterministic evidence packets for local language-model analysis.

The full deterministic snapshot remains available to the UI and saved conversations,
but local models receive a much smaller, evidence-first packet.  This keeps inference
work proportional to useful health evidence rather than to the size of the archive.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

PIPELINE_VERSION = "compact-health-evidence-v1"
TARGET_INPUT_TOKENS = 4000

DOMAIN_TYPES = {
    "activity": {
        "steps",
        "distance",
        "floors",
        "active-energy-burned",
        "total-calories",
        "active-minutes",
        "active-zone-minutes",
        "time-in-heart-rate-zone",
        "calories-in-heart-rate-zone",
    },
    "sleep": {"sleep"},
    "heart": {
        "heart-rate",
        "daily-resting-heart-rate",
        "daily-heart-rate-variability",
        "heart-rate-variability",
        "daily-heart-rate-zones",
    },
    "vitals": {
        "daily-oxygen-saturation",
        "oxygen-saturation",
        "daily-respiratory-rate",
        "respiratory-rate-sleep-summary",
        "daily-sleep-temperature-derivations",
        "daily-vo2-max",
        "blood-pressure",
        "blood-glucose",
    },
    "weight": {"weight", "body-fat"},
    "workouts": {"exercise"},
    "nutrition": {"nutrition-log"},
}
DOMAIN_ORDER = ("activity", "sleep", "heart", "vitals", "weight", "workouts", "nutrition", "other")


def estimate_json_tokens(value: Any) -> int:
    """Conservative token estimate for punctuation-heavy compact JSON."""

    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(text) / 3.0))


def json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _domain_for(data_type: str) -> str:
    for domain, data_types in DOMAIN_TYPES.items():
        if data_type in data_types:
            return domain
    return "other"


def _selected(source: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source and source[key] is not None}


def _top_mapping(value: Any, maximum: int = 8) -> Any:
    if not isinstance(value, dict):
        return value
    return {str(key): item for key, item in list(value.items())[:maximum]}


def _compact_structure(value: Any, *, depth: int = 0) -> Any:
    """Bound arbitrary structured details without losing representative values."""

    if depth >= 3:
        if isinstance(value, dict):
            return {str(key): item for key, item in list(value.items())[:5] if not isinstance(item, (dict, list))}
        if isinstance(value, list):
            return value[:4]
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:10]:
            key_text = str(key)
            if key_text in {"missing_date_ranges", "isolated_missing_dates", "principles", "interpretation", "interpretation_rule", "response_rule"}:
                continue
            result[key_text] = _compact_structure(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_compact_structure(item, depth=depth + 1) for item in value[:6]]
    return value


def _compact_coverage(snapshot: dict[str, Any]) -> dict[str, Any]:
    coverage = snapshot.get("requested_interval_coverage") or {}
    result = _selected(
        coverage,
        (
            "requested_start",
            "requested_end",
            "requested_calendar_days",
            "first_measurement_date",
            "last_measurement_date",
            "calendar_days_with_measurements",
            "calendar_days_with_measurements_percent",
            "missing_measurement_calendar_days",
            "internal_missing_measurement_days",
            "measurement_gap_ranges_total",
            "measurement_gap_ranges_truncated",
            "longest_measurement_gap_days",
            "starts_after_requested_start",
            "ends_before_requested_end",
            "scope_is_partially_observed",
        ),
    )
    limited = coverage.get("limited_daily_metrics")
    if isinstance(limited, list) and limited:
        result["limited_daily_metrics"] = [
            _selected(
                item,
                (
                    "data_type",
                    "label",
                    "observed_calendar_days",
                    "coverage_percent",
                    "missing_calendar_days",
                    "longest_missing_run_days",
                ),
            )
            for item in limited[:12]
            if isinstance(item, dict)
        ]
    return result


def _compact_metric(metric: dict[str, Any], coverage_row: dict[str, Any] | None = None) -> dict[str, Any]:
    data_type = str(metric.get("data_type", ""))
    result = _selected(metric, ("data_type", "label", "metric", "unit", "summary_scope", "data_role"))
    summary = _selected(
        metric.get("summary"),
        ("count", "latest", "mean", "median", "minimum", "maximum", "trend_percent", "anomaly_count"),
    )
    if summary:
        result["summary"] = summary

    coverage = _selected(
        coverage_row,
        (
            "observed_calendar_days",
            "coverage_percent",
            "records_considered",
            "missing_calendar_days",
            "longest_missing_run_days",
            "data_role",
        ),
    )
    if coverage:
        result["coverage"] = coverage

    derived = metric.get("derived_evidence") or {}
    compact_derived: dict[str, Any] = {}
    matched = _selected(
        derived.get("matched_recent_comparison"),
        (
            "window_days",
            "recent_days",
            "recent_mean",
            "previous_days",
            "previous_mean",
            "absolute_change",
            "percent_change",
            "standardized_change",
        ),
    )
    if matched:
        compact_derived["matched_change"] = matched
    trend = _selected(
        derived.get("trend"),
        ("window_days", "observed_days", "direction", "slope_per_week", "percent_per_week", "r_squared"),
    )
    if trend:
        compact_derived["trend"] = trend
    anomaly_source = derived.get("robust_anomaly_check") or {}
    anomaly = _selected(
        anomaly_source,
        (
            "window_days",
            "baseline_samples",
            "baseline_median",
            "latest_date",
            "latest_robust_z",
        ),
    )
    anomalies = anomaly_source.get("anomalies")
    if isinstance(anomalies, list) and anomalies:
        anomaly["strongest_anomalies"] = anomalies[:2]
    if anomaly:
        compact_derived["anomaly"] = anomaly
    baselines = derived.get("personal_baselines") or {}
    if isinstance(baselines, dict):
        compact_baselines = {
            key: _selected(value, ("samples", "mean", "median", "standard_deviation", "minimum", "maximum"))
            for key, value in baselines.items()
            if key in {"7_days", "28_days", "90_days"} and isinstance(value, dict)
        }
        compact_baselines = {key: value for key, value in compact_baselines.items() if value}
        if compact_baselines:
            compact_derived["personal_baselines"] = compact_baselines
    quality = _selected(
        derived.get("data_quality"),
        ("observed_days", "coverage_percent", "longest_gap_days", "first_date", "last_date"),
    )
    if quality:
        compact_derived["data_quality"] = quality
    if compact_derived:
        result["evidence"] = compact_derived

    temporal = _selected(
        metric.get("temporal_context"),
        ("status", "local_time", "today_so_far", "same_time_mean", "same_time_days", "same_time_percent"),
    )
    if temporal:
        result["today"] = temporal

    structured = metric.get("structured_details")
    if isinstance(structured, dict) and structured:
        result["structured"] = _compact_structure(structured)
    period_comparison = metric.get("structured_period_comparison")
    if period_comparison:
        result["period_comparison"] = _compact_structure(period_comparison)
    additional = metric.get("additional_fields")
    if isinstance(additional, list) and additional:
        result["additional_fields"] = [
            _selected(item, ("metric", "unit", "count", "latest", "mean", "minimum", "maximum", "trend_percent"))
            for item in additional[:3]
            if isinstance(item, dict)
        ]
    result["domain"] = _domain_for(data_type)
    return result


def _compact_insight(insight: dict[str, Any]) -> dict[str, Any]:
    result = _selected(
        insight,
        ("evidence_id", "kind", "data_types", "headline", "relevance_score", "confidence", "caveat"),
    )
    evidence = insight.get("evidence")
    if evidence:
        result["evidence"] = _compact_structure(evidence)
    return result


def _compact_association(association: dict[str, Any]) -> dict[str, Any]:
    return _selected(
        association,
        (
            "left",
            "right",
            "left_data_type",
            "right_data_type",
            "r",
            "paired_days",
            "timing",
            "reliability_score",
        ),
    )


def _importance_by_type(snapshot: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for insight in snapshot.get("candidate_insights", []):
        if not isinstance(insight, dict):
            continue
        try:
            score = float(insight.get("relevance_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        for data_type in insight.get("data_types") or []:
            result[str(data_type)] = max(result[str(data_type)], score)
    return dict(result)


def _group_metrics(snapshot: dict[str, Any], *, maximum_per_domain: int | None = None) -> dict[str, list[dict[str, Any]]]:
    coverage_rows = {
        str(item.get("data_type", "")): item
        for item in (snapshot.get("requested_interval_coverage") or {}).get("metrics", [])
        if isinstance(item, dict)
    }
    importance = _importance_by_type(snapshot)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_metrics = [item for item in snapshot.get("metrics", []) if isinstance(item, dict)]
    raw_metrics.sort(
        key=lambda item: (
            -importance.get(str(item.get("data_type", "")), 0.0),
            str(item.get("label", item.get("data_type", ""))),
        )
    )
    for metric in raw_metrics:
        data_type = str(metric.get("data_type", ""))
        grouped[_domain_for(data_type)].append(_compact_metric(metric, coverage_rows.get(data_type)))
    if maximum_per_domain is not None:
        grouped = defaultdict(
            list,
            {
                domain: metrics[:maximum_per_domain]
                for domain, metrics in grouped.items()
            },
        )
    return {domain: grouped[domain] for domain in DOMAIN_ORDER if grouped.get(domain)}


def _build_packet(snapshot: dict[str, Any], *, maximum_per_domain: int | None, evidence_limit: int, association_limit: int) -> dict[str, Any]:
    observation = _selected(
        snapshot.get("observation_context"),
        ("observed_at", "local_date", "local_time", "selected_period_includes_today", "current_day_is_incomplete", "elapsed_day_percent"),
    )
    data_coverage = snapshot.get("data_coverage") or {}
    packet = {
        "packet": {
            "health_evidence_present": True,
            "pipeline_version": PIPELINE_VERSION,
            "analysis_scope": snapshot.get("analysis_scope"),
            "metric_count": len(snapshot.get("metrics", [])),
        },
        "period": snapshot.get("period") or {},
        "observation": observation,
        "coverage": _compact_coverage(snapshot),
        "domains": _group_metrics(snapshot, maximum_per_domain=maximum_per_domain),
        "strongest_evidence": [
            _compact_insight(item)
            for item in snapshot.get("candidate_insights", [])[:evidence_limit]
            if isinstance(item, dict)
        ],
        "associations": [
            _compact_association(item)
            for item in snapshot.get("associations", [])[:association_limit]
            if isinstance(item, dict)
        ],
        "archive_quality": {
            "truncated_data_types": list(data_coverage.get("truncated_data_types") or [])[:12],
            "records_considered_total": sum(
                int(value)
                for value in (data_coverage.get("records_considered") or {}).values()
                if isinstance(value, (int, float))
            ),
        },
    }
    return packet


def build_compact_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build an adaptive ~2k-4k token packet from a rich deterministic snapshot."""

    packet = _build_packet(snapshot, maximum_per_domain=None, evidence_limit=14, association_limit=6)
    if estimate_json_tokens(packet) > TARGET_INPUT_TOKENS:
        packet = _build_packet(snapshot, maximum_per_domain=5, evidence_limit=10, association_limit=4)
    if estimate_json_tokens(packet) > TARGET_INPUT_TOKENS:
        packet = _build_packet(snapshot, maximum_per_domain=3, evidence_limit=8, association_limit=3)
        for metrics in packet.get("domains", {}).values():
            for metric in metrics:
                # Structured categorical payloads are useful, but are the first
                # optional detail to remove when the packet remains oversized.
                metric.pop("structured", None)
                metric.pop("additional_fields", None)
    packet["packet"]["estimated_tokens"] = estimate_json_tokens(packet)
    packet["packet"]["json_bytes"] = json_size_bytes(packet)
    return packet


def ensure_compact_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    cached = snapshot.get("ai_compact_evidence")
    if (
        isinstance(cached, dict)
        and (cached.get("packet") or {}).get("pipeline_version") == PIPELINE_VERSION
    ):
        return cached
    return build_compact_evidence(snapshot)
