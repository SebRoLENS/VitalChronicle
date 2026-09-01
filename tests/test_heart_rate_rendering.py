from __future__ import annotations

from datetime import datetime, timezone

import google_health_viewer.heart_rate_rendering as rendering


def test_heart_rate_graph_uses_one_minute_bins_by_default():
    start = datetime(2026, 9, 1, 8, tzinfo=timezone.utc).timestamp()
    points = [(start + minute * 60, 70.0 + minute) for minute in range(30)]

    smoothed = rendering.smooth_heart_rate_for_graph(points)

    assert len(smoothed) == 30
    assert smoothed[0][1] == 70.0
    assert smoothed[-1][1] == 99.0
    assert rendering.HEART_RATE_GRAPH_BIN_SECONDS == 60
    assert rendering.HEART_RATE_GRAPH_WINDOW_SECONDS == 60


def test_one_minute_bin_uses_median_without_changing_raw_samples():
    start = datetime(2026, 9, 1, 8, tzinfo=timezone.utc).timestamp()
    points = [
        (start + 5, 70.0),
        (start + 20, 72.0),
        (start + 40, 180.0),
        (start + 65, 75.0),
    ]
    original = list(points)

    smoothed = rendering.smooth_heart_rate_for_graph(points)

    assert points == original
    assert len(smoothed) == 2
    assert smoothed[0][1] == 72.0
    assert smoothed[1][1] == 75.0


def test_dashboard_snapshot_reports_one_minute_smoothing(monkeypatch):
    monkeypatch.setattr(
        rendering,
        "_ORIGINAL_BUILD_DAILY_PROGRESS",
        lambda *args, **kwargs: {
            "metrics": [
                {
                    "data_type": "heart-rate-today",
                    "heart_smoothing_minutes": 15,
                },
                {"data_type": "steps"},
            ]
        },
    )

    snapshot = rendering._build_daily_progress_one_minute()
    heart = next(
        metric for metric in snapshot["metrics"] if metric["data_type"] == "heart-rate-today"
    )

    assert heart["heart_smoothing_minutes"] == 1
