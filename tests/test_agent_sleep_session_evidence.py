from datetime import date, datetime

import pytest

from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tools import SafeToolExecutor
from google_health_viewer.storage import HealthStore


def session(start, end, minutes, main=None):
    sleep = {
        "interval": {"startTime": start, "endTime": end},
        "summary": {"minutesAsleep": str(minutes)},
        "metadata": {},
    }
    if main is not None:
        sleep["metadata"]["mainSleep"] = main
    return {"sleep": sleep}


@pytest.fixture
def executor(tmp_path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records(
        "sleep",
        [
            session("2026-09-06T02:21:00Z", "2026-09-06T10:06:00Z", 454, True),
            session("2026-09-06T20:48:00Z", "2026-09-06T21:30:00Z", 21),
            session("2026-09-07T01:00:00Z", "2026-09-07T08:00:00Z", 420, True),
        ],
    )
    return SafeToolExecutor(health, AgentStore(tmp_path / "agent.sqlite3"))


def test_daily_sleep_sums_sessions_instead_of_averaging_them(executor):
    args = {
        "metric": "sleep:sleep.summary.minutesAsleep",
        "start": "2026-09-06",
        "end": "2026-09-06",
    }
    result = executor.execute("get_metric_series", args)
    assert result["observed_days"] == 1
    assert [p["value"] for p in result["points"]] == [475]
    assert result["summary"]["minimum"] == 475
    assert executor.execute("get_baseline", args)["baseline"]["median"] == 475
    hours = executor.execute("get_daily_summary", {**args, "metric": "sleep"})
    assert hours["daily"][0]["value"] == pytest.approx(475 / 60)


def test_sessions_keep_main_sleep_separate_and_expose_local_times(executor):
    result = executor.execute("get_sleep_sessions", {"start": "2026-09-06", "end": "2026-09-07"})
    rows = result["sessions"]
    assert [r["main_sleep"] for r in rows] == [True, None, True]
    assert rows[0]["hours"] == pytest.approx(454 / 60, abs=0.001)
    assert result["main_sleep_observed_days"] == 2
    assert result["main_sleep_baseline_hours"]["count"] == 2
    assert result["main_sleep_baseline_hours"]["median"] == pytest.approx(437 / 60, abs=0.001)
    start = datetime.fromisoformat(rows[0]["start_time"])
    assert start == datetime.fromisoformat("2026-09-06T02:21:00+00:00")
    assert start.utcoffset() == start.astimezone().utcoffset()
    assert result["truncated"] is False
    assert result["omitted_sessions"] == 0


def test_session_bound_is_explicit_and_baseline_uses_full_interval(executor, monkeypatch):
    rows = [{"date": "2026-09-06", "hours": 7, "main_sleep": True}] * 65
    monkeypatch.setattr(executor, "_sleep_rows", lambda *_: ([], rows))
    result = executor.execute("get_sleep_sessions", {"start": "2026-09-06", "end": "2026-09-07"})
    assert result["session_count"] == 65
    assert result["returned_sessions"] == len(result["sessions"]) == 60
    assert result["omitted_sessions"] == 5
    assert result["truncated"] is True
    assert result["main_sleep_baseline_hours"]["count"] == 65


def test_sleep_gaps_are_never_filled(executor):
    result = executor._series("sleep", date(2026, 9, 5), date(2026, 9, 8))
    assert len(result["points"]) == 2
    missing = executor.execute(
        "get_missing_data", {"metric": "sleep", "start": "2026-09-05", "end": "2026-09-08"}
    )
    assert missing["missing_date_count"] == 2
