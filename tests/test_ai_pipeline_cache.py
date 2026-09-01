from google_health_viewer.ai_conversations import ConversationStore
from google_health_viewer.ai_pipeline import PIPELINE_VERSION


def _snapshot(mean: float):
    return {
        "observation_context": {"observed_at": "2026-09-01T10:00:00+02:00"},
        "metrics": [
            {
                "data_type": "steps",
                "label": "Steps",
                "summary": {"count": 10, "mean": mean, "latest": mean},
            }
        ],
        "requested_interval_coverage": {
            "requested_start": "2026-08-01",
            "requested_end": "2026-08-31",
            "requested_calendar_days": 31,
            "calendar_days_with_measurements": 30,
            "scope_is_partially_observed": True,
            "metrics": [],
        },
        "candidate_insights": [],
        "associations": [],
        "data_coverage": {"records_considered": {"steps": 10}},
    }


def test_conversation_snapshot_persists_compact_packet_until_refresh(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    created = store.create_thread(
        title="Health",
        model="qwen3:8b",
        scope="all",
        period={"label": "Complete history"},
        snapshot=_snapshot(7000),
        snapshot_revision="1",
    )

    first_packet = created["snapshot"]["ai_compact_evidence"]
    assert first_packet["packet"]["pipeline_version"] == PIPELINE_VERSION
    assert first_packet["domains"]["activity"][0]["summary"]["mean"] == 7000

    loaded = store.get_thread(created["id"])
    assert loaded["snapshot"]["ai_compact_evidence"] == first_packet

    updated = store.update_snapshot(
        created["id"],
        _snapshot(9000),
        "2",
    )
    assert updated["snapshot"]["ai_compact_evidence"]["domains"]["activity"][0]["summary"]["mean"] == 9000
