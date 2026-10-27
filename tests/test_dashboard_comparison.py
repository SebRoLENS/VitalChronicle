from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from google_health_viewer.analysis import daily_progress
from google_health_viewer.dashboard import MetricCard


def progress(values, current, *, resolution=0.1):
    day = date(2026, 10, 27)
    points = [
        (datetime.combine(day - timedelta(days=7 - i), datetime.min.time()).timestamp(), value)
        for i, value in enumerate(values)
    ]
    points.append((datetime.combine(day, datetime.min.time()).timestamp(), current))
    return daily_progress(points, "mean", day, resolution=resolution)


def test_small_deviation_is_within_personal_variability_not_exact_equality():
    r = progress([59, 60, 61, 60, 59, 61, 60], 60.5)
    assert r["comparison"] == "within"
    assert r["delta_percent"] > 0
    assert r["comparison_lower"] < 60.5 < r["comparison_upper"]
    assert progress([59, 60, 61, 60, 59, 61, 60], 63)["comparison"] == "above"
    assert progress([59, 60, 61, 60, 59, 61, 60], 57)["comparison"] == "below"


def test_margin_depends_on_metric_history_not_fixed_percentage():
    variable = progress([40, 80, 45, 90, 70, 55, 75], 70)
    stable = progress([97.1, 97.2, 97.3, 97.2, 97.1, 97.3, 97.2], 97.7)
    assert variable["comparison"] == "within"
    assert stable["comparison"] == "above"
    assert variable["comparison_margin"] > 10
    assert stable["comparison_margin"] < 0.2


def test_current_day_is_excluded_and_missing_days_are_not_zero_filled():
    r = progress([60, 61], 200)
    assert r["days_used"] == 2
    assert r["baseline"] == 60.5
    assert r["comparison_margin"] is None
    assert r["comparison"] is None
    assert r["measurement_uncertainty"] is None
    a = progress([60, 61, 59], 60)
    b = progress([60, 61, 59], 100000)
    assert a["comparison_margin"] == b["comparison_margin"]


def test_constant_history_uses_resolution_floor_and_inclusive_boundaries():
    r = progress([85] * 7, 85.05, resolution=0.1)
    assert r["comparison_margin"] == pytest.approx(0.05)
    assert r["comparison"] == "within"
    assert progress([85] * 7, 85.06, resolution=0.1)["comparison"] == "above"


def test_card_text_and_badge_agree_with_band_and_keep_actual_percentage():
    app = QApplication.instance() or QApplication([])
    r = progress([59, 60, 61, 60, 59, 61, 60], 60.5)
    card = MetricCard("HRV", "#673AB7")
    card.update_progress(
        {**r, "data_type": "daily-heart-rate-variability", "unit": "ms", "completion": False}
    )
    assert card.ratio.text() == "≈ average"
    assert card.trend.text() == "In line with your personal average"
    assert "not certified sensor uncertainty" in card.baseline.toolTip()
    assert r["percentage"] != 100
    assert "Measured difference from the average: +0.83%" in card.baseline.toolTip()
    card.close()
    app.processEvents()
