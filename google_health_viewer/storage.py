from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from .utils import extract_source, extract_times, flatten_dict


class HealthStore:
    def __init__(self, path: Path | None = None) -> None:
        data_dir = Path(user_data_dir("GoogleHealthViewer", "SebastianoRomi"))
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(data_dir, 0o700)
        except OSError:
            pass
        self.path = path or data_dir / "health_data.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                    data_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_kind TEXT NOT NULL DEFAULT 'data_point',
                    start_time TEXT,
                    end_time TEXT,
                    source TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (data_type, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_records_type_time
                    ON records(data_type, start_time);
                CREATE TABLE IF NOT EXISTS resources (
                    resource_type TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_log (
                    data_type TEXT PRIMARY KEY,
                    last_sync TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sync_ranges (
                    data_type TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    PRIMARY KEY (data_type, start_date, end_date)
                );
                CREATE INDEX IF NOT EXISTS idx_sync_ranges_type
                    ON sync_ranges(data_type, start_date, end_date);
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _record_id(
        data_type: str,
        payload: dict[str, Any],
        record_kind: str = "data_point",
        start: str | None = None,
        end: str | None = None,
    ) -> str:
        if payload.get("name"):
            return str(payload["name"])
        if record_kind == "daily_rollup" and (start or end):
            return f"{data_type}:rollup:{start or ''}:{end or ''}"
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return f"{data_type}:{hashlib.sha256(raw).hexdigest()}"

    def upsert_records(
        self,
        data_type: str,
        records: Iterable[dict[str, Any]],
        record_kind: str = "data_point",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for payload in records:
            start, end = extract_times(payload)
            rows.append(
                (
                    data_type,
                    self._record_id(data_type, payload, record_kind, start, end),
                    record_kind,
                    start,
                    end,
                    extract_source(payload),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now,
                )
            )
        if not rows:
            return 0
        with self._connect() as db:
            if record_kind == "daily_rollup":
                db.executemany(
                    """
                    DELETE FROM records
                    WHERE data_type = ? AND record_kind = 'daily_rollup'
                      AND COALESCE(start_time, '') = COALESCE(?, '')
                      AND COALESCE(end_time, '') = COALESCE(?, '')
                      AND record_id <> ?
                    """,
                    [(row[0], row[3], row[4], row[1]) for row in rows],
                )
            db.executemany(
                """
                INSERT INTO records
                    (data_type, record_id, record_kind, start_time, end_time,
                     source, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_type, record_id) DO UPDATE SET
                    record_kind=excluded.record_kind,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    source=excluded.source,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def save_resource(self, resource_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO resources(resource_type, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(resource_type) DO UPDATE SET
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (
                    resource_type,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def resources(self) -> dict[str, dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT resource_type, payload FROM resources ORDER BY resource_type"
            ).fetchall()
        return {row["resource_type"]: json.loads(row["payload"]) for row in rows}

    def sync_statuses(self) -> dict[str, tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT data_type, status, message FROM sync_log").fetchall()
        return {row["data_type"]: (str(row["status"]), str(row["message"])) for row in rows}

    def set_sync_status(self, data_type: str, status: str, message: str = "") -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sync_log(data_type, last_sync, status, message) VALUES (?, ?, ?, ?)
                ON CONFLICT(data_type) DO UPDATE SET
                    last_sync=excluded.last_sync, status=excluded.status, message=excluded.message
                """,
                (data_type, datetime.now(timezone.utc).isoformat(), status, message),
            )

    def has_app_marker(self, key: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row is not None

    def set_app_marker(self, key: str, value: str = "1") -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO app_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    @staticmethod
    def _merge_date_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:
        merged: list[tuple[date, date]] = []
        for start, end in sorted(ranges):
            if end < start:
                continue
            if merged and start <= merged[-1][1] + timedelta(days=1):
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, end))
            else:
                merged.append((start, end))
        return merged

    def _sync_ranges(self, data_type: str) -> list[tuple[date, date]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT start_date, end_date FROM sync_ranges
                WHERE data_type = ? ORDER BY start_date
                """,
                (data_type,),
            ).fetchall()
            if rows:
                return [
                    (date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"]))
                    for row in rows
                ]
            # Upgrade path from versions that stored records but not download coverage.
            bounds = db.execute(
                """
                SELECT MIN(substr(COALESCE(start_time, end_time), 1, 10)) AS first_day,
                       MAX(substr(COALESCE(start_time, end_time), 1, 10)) AS last_day
                FROM records WHERE data_type = ?
                """,
                (data_type,),
            ).fetchone()
        if not bounds or not bounds["first_day"] or not bounds["last_day"]:
            return []
        try:
            return [
                (
                    date.fromisoformat(str(bounds["first_day"])),
                    date.fromisoformat(str(bounds["last_day"])),
                )
            ]
        except ValueError:
            return []

    def missing_sync_ranges(
        self,
        data_type: str,
        start: date,
        end: date,
        *,
        refresh_date: date | None = None,
    ) -> list[tuple[date, date]]:
        """Return only uncovered historical ranges plus the still-changing current day."""
        refresh_date = refresh_date or datetime.now().astimezone().date()
        effective_end = min(end, refresh_date)
        if start > effective_end:
            return []

        historical_end = effective_end
        refresh_current = start <= refresh_date <= effective_end
        if refresh_current:
            historical_end = refresh_date - timedelta(days=1)

        missing: list[tuple[date, date]] = []
        if start <= historical_end:
            cursor = start
            for covered_start, covered_end in self._merge_date_ranges(
                self._sync_ranges(data_type)
            ):
                if covered_end < cursor or covered_start > historical_end:
                    continue
                if covered_start > cursor:
                    missing.append((cursor, min(historical_end, covered_start - timedelta(days=1))))
                cursor = max(cursor, covered_end + timedelta(days=1))
                if cursor > historical_end:
                    break
            if cursor <= historical_end:
                missing.append((cursor, historical_end))

        if refresh_current:
            missing.append((refresh_date, refresh_date))
        return self._merge_date_ranges(missing)

    def mark_sync_range(self, data_type: str, start: date, end: date) -> None:
        if end < start:
            return
        ranges = self._merge_date_ranges([*self._sync_ranges(data_type), (start, end)])
        with self._connect() as db:
            db.execute("DELETE FROM sync_ranges WHERE data_type = ?", (data_type,))
            db.executemany(
                "INSERT INTO sync_ranges(data_type, start_date, end_date) VALUES (?, ?, ?)",
                [(data_type, left.isoformat(), right.isoformat()) for left, right in ranges],
            )

    def list_records(
        self,
        data_type: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 20000,
        newest: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["data_type = ?"]
        params: list[Any] = [data_type]
        if start:
            clauses.append("(start_time IS NULL OR start_time >= ?)")
            params.append(start)
        if end:
            clauses.append("(start_time IS NULL OR start_time < ?)")
            params.append(end)
        params.append(limit)
        order = "DESC" if newest else "ASC"
        query = f"""
            SELECT record_id, record_kind, start_time, end_time, source, payload
            FROM records WHERE {" AND ".join(clauses)}
            ORDER BY COALESCE(start_time, updated_at) {order} LIMIT ?
        """
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        if newest:
            rows.reverse()
        return [
            {
                "record_id": row["record_id"],
                "record_kind": row["record_kind"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "source": row["source"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT data_type, COUNT(*) AS n FROM records GROUP BY data_type"
            ).fetchall()
        return {row["data_type"]: int(row["n"]) for row in rows}

    def data_date_bounds(self) -> tuple[date, date] | None:
        """Return the first and last local calendar dates represented in the archive."""
        with self._connect() as db:
            row = db.execute(
                """
                SELECT MIN(substr(COALESCE(start_time, end_time), 1, 10)) AS first_day,
                       MAX(substr(COALESCE(start_time, end_time), 1, 10)) AS last_day
                FROM records
                WHERE COALESCE(start_time, end_time) IS NOT NULL
                """
            ).fetchone()
        if not row or not row["first_day"] or not row["last_day"]:
            return None
        try:
            return date.fromisoformat(row["first_day"]), date.fromisoformat(row["last_day"])
        except (TypeError, ValueError):
            return None

    def export_csv(self, data_type: str, destination: Path) -> int:
        records = self.list_records(data_type, limit=10_000_000)
        flattened = []
        columns = {"_start_time", "_end_time", "_source", "_record_id"}
        for record in records:
            row = flatten_dict(record["payload"])
            row.update(
                {
                    "_start_time": record["start_time"],
                    "_end_time": record["end_time"],
                    "_source": record["source"],
                    "_record_id": record["record_id"],
                }
            )
            flattened.append(row)
            columns.update(row)
        ordered = ["_start_time", "_end_time", "_source", "_record_id"] + sorted(
            columns - {"_start_time", "_end_time", "_source", "_record_id"}
        )
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ordered, delimiter=";")
            writer.writeheader()
            writer.writerows(flattened)
        return len(records)

    def export_archive(self, destination: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="google-health-export-") as tmp:
            tmp_path = Path(tmp)
            with (
                self._connect() as source,
                sqlite3.connect(tmp_path / "health_data.sqlite3") as target,
            ):
                source.backup(target)
            counts = self.counts()
            for data_type in counts:
                records = self.list_records(data_type, limit=10_000_000)
                with (tmp_path / f"{data_type}.jsonl").open("w", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record["payload"], ensure_ascii=False) + "\n")
            (tmp_path / "account-and-devices.json").write_text(
                json.dumps(self.resources(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            manifest = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "record_counts": counts,
                "format": "JSON Lines and SQLite",
            }
            (tmp_path / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(tmp_path.iterdir()):
                    archive.write(file, file.name)

    def clear(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM records")
            db.execute("DELETE FROM resources")
            db.execute("DELETE FROM sync_log")
            db.execute("DELETE FROM sync_ranges")
