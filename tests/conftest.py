from __future__ import annotations

import pytest

# These assertions describe the pre-1.2.1 heart-rate rendering semantics.
# The current behaviour is covered by test_heart_rate_five_minute_core.py:
# arithmetic five-minute averages, with no additional median/outlier smoothing.
_SUPERSEDED_HEART_RATE_TESTS = {
    "test_heart_rate_smoothing_removes_an_isolated_spike",
    "test_one_minute_bin_uses_median_without_changing_raw_samples",
    "test_dashboard_adds_today_heart_rate_sparkline",
    "test_dashboard_heart_rate_uses_all_today_samples_and_prior_week_band",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.name in _SUPERSEDED_HEART_RATE_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "Superseded by the shared arithmetic five-minute heart-rate "
                        "semantics tested in test_heart_rate_five_minute_core.py"
                    )
                )
            )
