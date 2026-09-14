from __future__ import annotations

from datetime import date
from pathlib import Path

from google_health_viewer import workers
from google_health_viewer.constants import DATA_TYPE_BY_KEY
from google_health_viewer.storage import HealthStore


class _CredentialStore:
    def save_credentials(self, _credentials) -> bool:
        return True


def _legacy_total_calories() -> dict:
    return {
        "name": "legacy-total-calories",
        "totalCalories": {
            "interval": {
                "startTime": "2026-09-01T00:00:00Z",
                "endTime": "2026-09-02T00:00:00Z",
            },
            "kcal": 2100,
        },
    }


def _daily_total_calories() -> dict:
    return {
        "civilStartTime": {
            "date": {"year": 2026, "month": 9, "day": 1},
            "time": {},
        },
        "civilEndTime": {
            "date": {"year": 2026, "month": 9, "day": 2},
            "time": {},
        },
        "totalCalories": {"kcalSum": 2100},
    }


def test_daily_rollup_migration_replaces_legacy_records(monkeypatch, tmp_path: Path):
    spec = DATA_TYPE_BY_KEY["total-calories"]
    store = HealthStore(tmp_path / "health.sqlite3")
    store.upsert_records(spec.key, [_legacy_total_calories()], "data_point")

    class _Client:
        def __init__(self, _credentials) -> None:
            pass

        def iter_daily_rollups(self, *_args, **_kwargs):
            yield [_daily_total_calories()]

    monkeypatch.setattr(workers, "GoogleHealthClient", _Client)
    monkeypatch.setattr(workers, "DATA_TYPES", (spec,))
    thread = workers.SyncThread(
        None,
        store,
        _CredentialStore(),
        date(2026, 9, 1),
        date(2026, 9, 2),
        include_resources=False,
    )

    thread.run()

    marker = f"{workers.DAILY_ROLLUP_STORAGE_VERSION}:{spec.key}"
    assert store.has_app_marker(marker)
    assert store.count_records_by_kind(spec.key, "data_point") == 0
    assert store.count_records_by_kind(spec.key, "daily_rollup") == 1


def test_empty_migration_keeps_legacy_records_retryable(monkeypatch, tmp_path: Path):
    spec = DATA_TYPE_BY_KEY["total-calories"]
    store = HealthStore(tmp_path / "health.sqlite3")
    store.upsert_records(spec.key, [_legacy_total_calories()], "data_point")

    class _Client:
        def __init__(self, _credentials) -> None:
            pass

        def iter_daily_rollups(self, *_args, **_kwargs):
            yield []

    monkeypatch.setattr(workers, "GoogleHealthClient", _Client)
    monkeypatch.setattr(workers, "DATA_TYPES", (spec,))
    thread = workers.SyncThread(
        None,
        store,
        _CredentialStore(),
        date(2026, 9, 1),
        date(2026, 9, 2),
        include_resources=False,
    )

    thread.run()

    marker = f"{workers.DAILY_ROLLUP_STORAGE_VERSION}:{spec.key}"
    assert not store.has_app_marker(marker)
    assert store.count_records_by_kind(spec.key, "data_point") == 1
