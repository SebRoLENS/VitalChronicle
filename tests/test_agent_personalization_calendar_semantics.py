from __future__ import annotations

from datetime import datetime
from pathlib import Path

from google_health_viewer.agent_runtime_v2 import (
    AgentRuntime,
    _relevant_personal_evidence,
)
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tool_factory import EnhancedSafeToolExecutor


class OvernightStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records = {
            "sleep": [
                {
                    "start_time": "2026-09-09T23:30:00+02:00",
                    "end_time": "2026-09-10T07:00:00+02:00",
                    "payload": {
                        "sleep": {
                            "minutesAsleep": 420,
                            "stages": [
                                {
                                    "stage": 5,
                                    "startTime": "2026-09-10T00:00:00+02:00",
                                    "endTime": "2026-09-10T01:00:00+02:00",
                                },
                                {
                                    "stage": 4,
                                    "startTime": "2026-09-10T01:00:00+02:00",
                                    "endTime": "2026-09-10T06:00:00+02:00",
                                },
                            ],
                        }
                    },
                }
            ],
            "respiratory-rate-sleep-summary": [
                {
                    "start_time": "2026-09-09T23:30:00+02:00",
                    "end_time": "2026-09-10T07:00:00+02:00",
                    "payload": {"breathsPerMinute": 14.2},
                }
            ],
        }

    def counts(self):
        return {key: len(value) for key, value in self.records.items()}

    def data_date_bounds(self):
        return None

    def data_revision(self):
        return "calendar-test"

    def list_records(self, data_type, start=None, end=None, limit=200000, newest=False):
        rows = list(self.records.get(data_type, []))
        if start:
            left = datetime.fromisoformat(start)
            rows = [
                row for row in rows
                if datetime.fromisoformat(row["start_time"]) >= left.replace(tzinfo=row_dt(row).tzinfo)
            ]
        if end:
            right = datetime.fromisoformat(end)
            rows = [
                row for row in rows
                if datetime.fromisoformat(row["start_time"]) < right.replace(tzinfo=row_dt(row).tzinfo)
            ]
        if newest:
            rows.reverse()
        return rows[:limit]


def row_dt(row):
    return datetime.fromisoformat(row["start_time"])


def test_sleep_today_uses_session_that_ended_today(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(OvernightStore(tmp_path / "health.sqlite3"), store)

    sessions = executor.execute(
        "get_sleep_sessions", {"start": "2026-09-10", "end": "2026-09-10"}
    )
    assert sessions["session_count"] == 1
    assert sessions["sessions"][0]["date"] == "2026-09-10"
    assert sessions["date_semantics"] == "wake_up_date"

    summary = executor.execute(
        "get_daily_summary",
        {"metric": "sleep", "start": "2026-09-10", "end": "2026-09-10"},
    )
    assert summary["daily"] == [{"date": "2026-09-10", "value": 7.0}]
    assert summary["date_semantics"] == "wake_up_date"

    previous = executor.execute(
        "get_daily_summary",
        {"metric": "sleep", "start": "2026-09-09", "end": "2026-09-09"},
    )
    assert previous["daily"] == []


def test_sleep_stage_today_uses_wake_up_date(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(OvernightStore(tmp_path / "health.sqlite3"), store)
    stages = executor.execute(
        "get_sleep_stage_series", {"start": "2026-09-10", "end": "2026-09-10"}
    )
    assert stages["observed_nights"] == 1
    assert stages["daily_stages"][0]["date"] == "2026-09-10"
    assert stages["daily_stages"][0]["deep"] == 1.0
    assert stages["date_semantics"] == "wake_up_date"


def test_other_overnight_summary_uses_session_end_date(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    executor = EnhancedSafeToolExecutor(OvernightStore(tmp_path / "health.sqlite3"), store)
    item = executor.execute(
        "get_daily_summary",
        {
            "metric": "respiratory-rate-sleep-summary",
            "start": "2026-09-10",
            "end": "2026-09-10",
        },
    )
    assert item["daily"] == [{"date": "2026-09-10", "value": 14.2}]
    assert item["date_semantics"] == "session_end_date"


def test_focused_sleep_request_selects_only_relevant_personal_context():
    model = [
        {"key": "sleep_schedule_context", "statement": "social exception", "is_current": True},
        {"key": "current_training_goal", "statement": "strength three times weekly", "is_current": True},
        {"key": "old_sleep_context", "statement": "old sleep note", "is_current": False},
    ]
    reports = [
        {"category": "sleep_quality", "statement": "slept poorly"},
        {"category": "soreness", "statement": "legs sore"},
    ]
    selected_model, selected_reports = _relevant_personal_evidence(
        "Come ho dormito oggi?", model, reports
    )
    assert [item["key"] for item in selected_model] == ["sleep_schedule_context"]
    assert [item["category"] for item in selected_reports] == ["sleep_quality"]


class FocusedPersonalizationRuntime(AgentRuntime):
    def __init__(self, health_store, agent_store):
        super().__init__(health_store, agent_store)
        self.turn = 0
        self.final_messages = None

    def _chat_once(self, **kwargs):
        self.turn += 1
        if self.turn == 1:
            return {"content": "Hai dormito 7 ore e sei andato a letto tardi."}
        self.final_messages = kwargs.get("messages")
        assert kwargs.get("tools") == []
        return {
            "content": (
                "Hai dormito 7 ore. In una precedente eccezione avevi indicato la vita sociale: "
                "anche questa volta sei andato a letto tardi per un evento sociale o c'era un altro motivo?"
            )
        }


def test_focused_sleep_answer_forces_relevant_personalization(tmp_path):
    health = OvernightStore(tmp_path / "health.sqlite3")
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.learn_user_model(
        "sleep_schedule_context",
        "When sleep timing was irregular, the user reported: social exception",
        evidence={"answer": "social exception", "temporal_scope": "temporary", "ttl_days": 60},
    )
    store.learn_user_model(
        "current_training_goal",
        "User feedback: strength training three times a week",
        evidence={"answer": "strength training", "temporal_scope": "temporary", "ttl_days": 90},
    )
    runtime = FocusedPersonalizationRuntime(health, store)
    answer = runtime.analyze(
        model="test", snapshot={}, question="Come ho dormito oggi?", history=[],
        max_tokens=1024, model_context_limit=None, performance_profile="standard",
        thread_id="focused-personalization",
    )
    assert runtime.turn == 2
    assert "vita sociale" in answer
    joined = "\n".join(str(item.get("content") or "") for item in runtime.final_messages)
    assert "PERSONALISATION CHECKPOINT" in joined
    assert "strength training three times a week" not in joined.split("relevant_personal_context", 1)[-1]
