from datetime import date, timedelta

import pytest

from google_health_viewer.agent_tools import SafeToolExecutor


class _Store:
    def user_model(self):
        return []


def _executor():
    item = SafeToolExecutor.__new__(SafeToolExecutor)
    item.agent_store = _Store()
    return item


def test_load_ratio_requires_enough_chronic_history():
    executor = _executor()
    right = date(2026, 9, 10)
    start = right - timedelta(days=15)
    daily = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(16)}
    executor._cardio_daily = lambda _left, _right: (daily, "test load")

    load = executor._load(right)

    assert load["acute_observed_days"] == 7
    assert load["chronic_observed_days"] == 9
    assert load["acute_7d"] == pytest.approx(700.0)
    assert load["chronic_weekly_equivalent_28d"] is None
    assert load["acute_chronic_ratio"] is None
    assert load["history_status"] == "insufficient_chronic_history"


def test_load_ratio_uses_observed_day_normalization_with_sufficient_coverage():
    executor = _executor()
    right = date(2026, 9, 10)
    start = right - timedelta(days=34)
    daily = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(35)}
    executor._cardio_daily = lambda _left, _right: (daily, "test load")

    load = executor._load(right)

    assert load["acute_observed_days"] == 7
    assert load["chronic_observed_days"] == 28
    assert load["acute_7d"] == pytest.approx(700.0)
    assert load["chronic_weekly_equivalent_28d"] == pytest.approx(700.0)
    assert load["acute_chronic_ratio"] == pytest.approx(1.0)
    assert load["history_status"] == "sufficient"


def test_resilience_reweights_when_load_history_is_insufficient():
    executor = _executor()
    executor._readiness = lambda _right: {"score": 56.8}
    executor._tool_calculate_sleep_regularity = lambda _args: {"regularity_score": 14.9}
    executor._load = lambda _right: {
        "acute_chronic_ratio": None,
        "history_status": "insufficient_chronic_history",
        "acute_observed_days": 7,
        "chronic_observed_days": 9,
        "acute_coverage": 1.0,
        "chronic_coverage": 9 / 28,
    }

    result = executor._resilience(date(2026, 9, 10))

    assert result["components"]["load_balance"] is None
    assert result["component_status"]["load_balance"] == "insufficient_chronic_history"
    assert result["load_balance_label"] == "unavailable_insufficient_history"
    assert result["effective_weights"] == {"readiness": 0.667, "sleep_regularity": 0.333}
    assert result["score"] == pytest.approx(42.8)
    assert "not treated as neutral or zero" in result["limitations"]


def test_zero_load_balance_is_labeled_unbalanced_not_neutral():
    executor = _executor()
    executor._readiness = lambda _right: {"score": 60.0}
    executor._tool_calculate_sleep_regularity = lambda _args: {"regularity_score": 60.0}
    executor._load = lambda _right: {
        "acute_chronic_ratio": 2.5,
        "history_status": "sufficient",
        "acute_observed_days": 7,
        "chronic_observed_days": 28,
        "acute_coverage": 1.0,
        "chronic_coverage": 1.0,
    }

    result = executor._resilience(date(2026, 9, 10))

    assert result["components"]["load_balance"] == 0.0
    assert result["load_balance_label"] == "unbalanced"
    assert result["component_status"]["load_balance"] == "available"
