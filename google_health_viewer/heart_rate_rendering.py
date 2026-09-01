"""High-resolution heart-rate rendering for the Overview cardiac card.

The stored heart-rate samples are never changed.  Only the display curve is reduced
to one-minute bins, replacing the previous five-minute bins plus fifteen-minute
moving-average window.  Real minimum, maximum, mean and sample count continue to
come from the original samples.
"""

from __future__ import annotations

from typing import Any

from . import analysis

HEART_RATE_GRAPH_BIN_SECONDS = 60
HEART_RATE_GRAPH_WINDOW_SECONDS = 60
HEART_RATE_GRAPH_SMOOTHING_MINUTES = 1

_ORIGINAL_SMOOTH = analysis.smooth_heart_rate_points
_ORIGINAL_BUILD_DAILY_PROGRESS = analysis.build_daily_progress_snapshot
_INSTALLED = False


def smooth_heart_rate_for_graph(
    points: list[tuple[float, float]],
    *,
    bin_seconds: int = HEART_RATE_GRAPH_BIN_SECONDS,
    window_seconds: int = HEART_RATE_GRAPH_WINDOW_SECONDS,
) -> list[tuple[float, float]]:
    """Return the display-only heart-rate curve at one-minute resolution by default."""

    return _ORIGINAL_SMOOTH(
        points,
        bin_seconds=bin_seconds,
        window_seconds=window_seconds,
    )


def _build_daily_progress_one_minute(*args: Any, **kwargs: Any) -> dict[str, Any]:
    snapshot = _ORIGINAL_BUILD_DAILY_PROGRESS(*args, **kwargs)
    for metric in snapshot.get("metrics", []):
        if not isinstance(metric, dict) or metric.get("data_type") != "heart-rate-today":
            continue
        # _ORIGINAL_BUILD_DAILY_PROGRESS resolves smooth_heart_rate_points from
        # the analysis module at runtime, so once installed it already uses the
        # one-minute implementation. Keep the explicit metadata in sync with it.
        metric["heart_smoothing_minutes"] = HEART_RATE_GRAPH_SMOOTHING_MINUTES
    return snapshot


def install_one_minute_heart_rate_rendering() -> None:
    """Install the display-only one-minute cardiac smoothing exactly once."""

    global _INSTALLED
    if _INSTALLED:
        return
    analysis.smooth_heart_rate_points = smooth_heart_rate_for_graph
    analysis.build_daily_progress_snapshot = _build_daily_progress_one_minute
    _INSTALLED = True
