from __future__ import annotations

from pathlib import Path

import pytest

from google_health_viewer.agent_runtime import AgentRuntime, _tool_arguments
from google_health_viewer.agent_store import AgentStore
from google_health_viewer.agent_tools import SafeToolExecutor
from google_health_viewer.storage import HealthStore


def _step_record(day: int, count: int) -> dict:
    return {
        "name": f"users/me/dataTypes/steps/dataPoints/{day}",
        "steps": {
            "interval": {
                "startTime": f"2026-08-{day:02d}T10:00:00Z",
                "endTime": f"2026-08-{day:02d}T10:01:00Z",
            },
            "count": count,
        },
    }


def test_agent_store_keeps_feedback_and_health_data_separate(tmp_path: Path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records("steps", [_step_record(1, 1000)])
    agent = AgentStore.beside_health_store(health)

    feedback = agent.ask_feedback(
        "How did this workload feel?",
        learning_key="load_tolerance",
        context={"observation": "training load was above the recent baseline"},
    )
    answered = agent.answer_feedback(str(feedback["feedback_id"]), "Manageable, not exhausting")

    assert answered is not None
    learned = agent.user_model_entry("load_tolerance")
    assert learned is not None
    assert learned["evidence_count"] == 1
    assert 0.0 < learned["confidence"] < 1.0
    assert health.counts() == {"steps": 1}
    assert agent.path != health.path


def test_builtin_tool_supersedes_equivalent_learned_tool(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3")
    learned = store.add_learned_tool(
        {
            "name": "my_baseline",
            "capability": "personal.metric.baseline",
            "description": "Calculate a personal metric baseline",
            "parameters": {"type": "object", "properties": {}},
            "pipeline": [{"op": "return"}],
        }
    )
    assert learned["status"] == "created"

    store.sync_builtin_tools(
        [
            {
                "name": "builtin_baseline",
                "capability": "personal.metric.baseline",
                "description": "Calculate a personal metric baseline",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
    )

    old = store.tool("my_baseline")
    assert old is not None
    assert old["status"] == "superseded"
    assert old["replacement"] == "builtin_baseline"


def test_safe_tool_executor_rejects_arbitrary_code(tmp_path: Path):
    health = HealthStore(tmp_path / "health.sqlite3")
    agent = AgentStore(tmp_path / "agent.sqlite3")
    executor = SafeToolExecutor(health, agent)

    with pytest.raises(ValueError, match="Unsafe or unknown"):
        executor.validate_pipeline([{"op": "python", "code": "open('/tmp/x','w')"}])

    validated = executor.validate_pipeline(
        [
            {"op": "load_series", "metric": "steps", "as": "series"},
            {"op": "summarize", "source": "series", "as": "summary"},
        ]
    )
    assert validated[-1] == {"op": "return"}
    assert all("code" not in step for step in validated)


def test_metric_tool_reads_local_series_without_zero_filling(tmp_path: Path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records("steps", [_step_record(1, 1000), _step_record(3, 1800)])
    agent = AgentStore(tmp_path / "agent.sqlite3")
    executor = SafeToolExecutor(health, agent)

    result = executor.execute(
        "get_metric_series",
        {
            "metric": "steps:steps.count",
            "start": "2026-08-01",
            "end": "2026-08-03",
        },
    )

    assert result["observed_days"] == 2
    assert result["expected_days"] == 3
    assert result["coverage"] == pytest.approx(2 / 3, abs=0.001)
    assert len(result["points"]) == 2
    assert {point["date"] for point in result["points"]} == {"2026-08-01", "2026-08-03"}


def test_runtime_starts_uncalibrated_when_health_data_exist(tmp_path: Path):
    health = HealthStore(tmp_path / "health.sqlite3")
    health.upsert_records("steps", [_step_record(1, 1000)])
    runtime = AgentRuntime(health, AgentStore(tmp_path / "agent.sqlite3"))

    assert runtime.needs_calibration()
    runtime.agent_store.mark_calibrated()
    assert not runtime.needs_calibration()


def test_tool_argument_parser_accepts_ollama_dict_and_json_string():
    assert _tool_arguments({"function": {"arguments": {"metric": "hrv"}}}) == {
        "metric": "hrv"
    }
    assert _tool_arguments(
        {"function": {"arguments": '{"metric":"sleep","days":7}'}}
    ) == {"metric": "sleep", "days": 7}
    assert _tool_arguments({"function": {"arguments": "not-json"}}) == {}
