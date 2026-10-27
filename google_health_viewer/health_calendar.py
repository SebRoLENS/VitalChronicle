"""Local calendar boundaries shared by deterministic AI reads and its planner."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .utils import parse_timestamp

SESSION_END_DATE_TYPES = {
    "sleep",
    "respiratory-rate-sleep-summary",
    "daily-sleep-temperature-derivations",
}


def record_local_day(record: dict[str, Any], data_type: str) -> str | None:
    if data_type in SESSION_END_DATE_TYPES:
        raw = record.get("end_time") or record.get("start_time")
    else:
        raw = record.get("start_time") or record.get("end_time")
    ts = parse_timestamp(raw)
    return datetime.fromtimestamp(ts).astimezone().date().isoformat() if ts is not None else None


def local_calendar_records(
    store, data_type: str, left: date, right: date, *, limit=200000, newest=False
):
    # SQLite stores source ISO strings, often UTC. Query a small superset, then
    # apply the local date semantics; string comparisons at midnight are unsafe
    # for offsets, DST and sessions that start on the previous evening.
    padding = 2 if data_type in SESSION_END_DATE_TYPES else 1
    records = store.list_records(
        data_type,
        start=(left - timedelta(days=padding)).isoformat(),
        end=(right + timedelta(days=2)).isoformat(),
        limit=limit,
        newest=newest,
    )
    return [
        r
        for r in records
        if (day := record_local_day(r, data_type)) is not None
        and left.isoformat() <= day <= right.isoformat()
    ]
