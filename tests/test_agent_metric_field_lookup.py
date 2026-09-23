from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tools import SafeToolExecutor
from google_health_viewer.storage import HealthStore


def test_available_hrv_field_can_be_queried_directly(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records(
        "daily-heart-rate-variability",
        [
            {
                "name": "hrv-2026-09-21",
                "startTime": "2026-09-21T12:00:00Z",
                "dailyHeartRateVariability": {
                    "averageHeartRateVariabilityMilliseconds": 64,
                    "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": 52,
                },
            },
            {
                "name": "hrv-2026-09-22",
                "startTime": "2026-09-22T12:00:00Z",
                "dailyHeartRateVariability": {
                    "averageHeartRateVariabilityMilliseconds": 71,
                    "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": 60,
                },
            },
        ],
    )
    agent = AgentStore(tmp_path / "agent.sqlite3")
    executor = SafeToolExecutor(health, agent)
    field = "dailyHeartRateVariability.averageHeartRateVariabilityMilliseconds"
    available = executor.execute("get_available_metrics")
    assert field in next(
        row["metrics"] for row in available["data_types"]
        if row["data_type"] == "daily-heart-rate-variability"
    )
    args = {"metric": field, "start": "2026-09-21", "end": "2026-09-22"}
    result = executor.execute("get_daily_summary", args)
    assert result["data_type"] == "daily-heart-rate-variability"
    assert result["field"] == field
    assert result["unit"] == "ms"
    assert result["observed_days"] == 2
    assert [row["value"] for row in result["daily"]] == [64, 71]
    assert len(executor.execute("get_metric_series", args)["points"]) == 2
    deep_field = "dailyHeartRateVariability.deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"
    assert executor.execute("get_daily_summary", {**args, "metric": deep_field})["daily"][0]["value"] == 52
