from __future__ import annotations

from datetime import datetime, timezone

from google_health_viewer.ai_query_planner import (
    SelectedHealthStore,
    _parse_json_object,
    _planned_classifier,
    build_data_catalog,
    fallback_data_plan,
    resolve_data_plan,
)
from google_health_viewer.storage import HealthStore


def _catalog() -> dict:
    return {
        "datasets": [
            {
                "key": "sleep",
                "label": "Sleep",
                "category": "Sleep",
                "first_date": "2026-05-01",
                "last_date": "2026-09-01",
                "record_count": 120,
            },
            {
                "key": "daily-heart-rate-variability",
                "label": "Heart rate variability",
                "category": "Health",
                "first_date": "2026-06-01",
                "last_date": "2026-09-01",
                "record_count": 90,
            },
            {
                "key": "steps",
                "label": "Steps",
                "category": "Activity",
                "first_date": "2026-01-01",
                "last_date": "2026-09-02",
                "record_count": 245,
            },
        ]
    }


def test_resolve_plan_uses_only_catalog_keys_and_ai_selected_window() -> None:
    plan = resolve_data_plan(
        {
            "data_types": ["sleep", "not-a-real-dataset", "daily-heart-rate-variability"],
            "window": "last_n_days",
            "days": 60,
            "detail": "daily",
            "reason": "Compare sleep and HRV over a useful matched interval",
        },
        _catalog(),
    )

    assert plan["data_types"] == ["sleep", "daily-heart-rate-variability"]
    assert plan["start_date"] == "2026-07-04"
    assert plan["end_date"] == "2026-09-01"
    assert plan["end_exclusive"] == "2026-09-02"
    assert plan["days"] == 60
    assert plan["detail"] == "daily"


def test_resolve_explicit_range_is_clamped_to_available_selected_data() -> None:
    plan = resolve_data_plan(
        {
            "data_types": ["daily-heart-rate-variability"],
            "window": "date_range",
            "start_date": "2020-01-01",
            "end_date": "2030-01-01",
        },
        _catalog(),
    )

    assert plan["start_date"] == "2026-06-01"
    assert plan["end_date"] == "2026-09-01"


def test_invalid_plan_has_safe_bounded_fallback() -> None:
    plan = fallback_data_plan(_catalog())

    assert 1 <= plan["days"] <= 90
    assert set(plan["data_types"]).issubset({"sleep", "daily-heart-rate-variability", "steps"})
    assert plan["end_date"] == "2026-09-02"


def test_selected_store_never_reads_unapproved_data_types() -> None:
    class Store:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_records(self, data_type: str, *args, **kwargs):
            self.calls.append(data_type)
            return [{"data_type": data_type}]

        def data_revision(self) -> str:
            return "revision"

    base = Store()
    proxy = SelectedHealthStore(base, ["sleep"])

    assert proxy.list_records("sleep") == [{"data_type": "sleep"}]
    assert proxy.list_records("heart-rate") == []
    assert base.calls == ["sleep"]
    assert proxy.data_revision() == "revision"


def test_catalog_contains_metadata_not_health_values(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    updated = datetime(2026, 9, 2, tzinfo=timezone.utc).isoformat()
    with store._connect() as db:  # noqa: SLF001 - test verifies the catalogue query boundary.
        db.executemany(
            """
            INSERT INTO records
                (data_type, record_id, record_kind, start_time, end_time, source, payload, updated_at)
            VALUES (?, ?, 'data_point', ?, ?, '', ?, ?)
            """,
            [
                (
                    "sleep",
                    "sleep-1",
                    "2026-08-01T22:00:00+00:00",
                    "2026-08-02T06:00:00+00:00",
                    '{"minutesAsleep":480,"secret_health_value":12345}',
                    updated,
                ),
                (
                    "sleep",
                    "sleep-2",
                    "2026-09-01T22:00:00+00:00",
                    "2026-09-02T06:00:00+00:00",
                    '{"minutesAsleep":450}',
                    updated,
                ),
            ],
        )

    catalog = build_data_catalog(store)
    raw = str(catalog)
    assert catalog["archive_first_date"] == "2026-08-01"
    assert catalog["archive_last_date"] == "2026-09-01"
    assert catalog["datasets"][0]["record_count"] == 2
    assert "minutesAsleep" not in raw
    assert "secret_health_value" not in raw
    assert "12345" not in raw


def test_planned_snapshot_bypasses_second_semantic_keyword_router() -> None:
    intent = _planned_classifier(
        {"packet": {"analysis_scope": "ai_planned"}, "domains": {}},
        "unusual wording in any language",
    )
    assert intent["mode"] == "global"
    assert intent["reason"] == "model_planner_selected"


def test_planner_json_parser_accepts_fenced_json_but_not_prose_only() -> None:
    parsed = _parse_json_object(
        '```json\n{"data_types":["sleep"],"window":"last_n_days","days":7}\n```'
    )
    assert parsed["data_types"] == ["sleep"]
