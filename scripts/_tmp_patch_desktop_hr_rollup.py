from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    if new in text:
        return
    if old not in text:
        raise AssertionError(f"Expected patch anchor not found in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1))


# 1) Heart rate is a five-minute server-side rollup, not a raw sample list.
replace_once(
    "google_health_viewer/constants.py",
    '    _spec("heart-rate", _("Heart rate"), _("Health metrics"), "health", "sample"),\n',
    '''    _spec(\n        "heart-rate",\n        _("Heart rate"),\n        _("Health metrics"),\n        "health",\n        "sample",\n        "five_minute_rollup",\n        None,\n    ),\n''',
)

# 2) Mirror Android: POST dataPoints:rollUp with a 300 s window and preserve avg/min/max.
api_path = Path("google_health_viewer/api.py")
api = api_path.read_text()
if "iter_five_minute_heart_rate_rollups" not in api:
    api = api.replace("import time\n", "import copy\nimport math\nimport time\n", 1)
    anchor = '''    def get_resources(self, cancel: Callable[[], bool] | None = None) -> dict[str, dict]:\n'''
    method = '''    @staticmethod\n    def _prepare_five_minute_heart_rate_rollup(\n        point: dict[str, Any],\n    ) -> dict[str, Any]:\n        \"\"\"Preserve Google's rollup payload and expose its average canonically.\"\"\"\n        prepared = copy.deepcopy(point)\n        heart_rate = prepared.get("heartRate")\n        if isinstance(heart_rate, dict):\n            raw_average = heart_rate.get("beatsPerMinuteAvg")\n            try:\n                average = float(raw_average)\n            except (TypeError, ValueError):\n                average = None\n            if average is not None and math.isfinite(average):\n                heart_rate["beatsPerMinute"] = average\n\n        start = str(prepared.get("startTime") or "")\n        end = str(prepared.get("endTime") or "")\n        if start or end:\n            prepared["name"] = f"heart-rate:5m:{start}:{end}"\n        return prepared\n\n    def iter_five_minute_heart_rate_rollups(\n        self,\n        spec: DataTypeSpec,\n        start: date,\n        end: date,\n        cancel: Callable[[], bool] | None = None,\n    ) -> Iterator[list[dict[str, Any]]]:\n        \"\"\"Download only Google Health five-minute mean heart-rate windows.\"\"\"\n        if spec.key != "heart-rate":\n            raise ValueError("Five-minute heart-rate rollups require the heart-rate data type")\n\n        cursor = start\n        while cursor <= end:\n            chunk_end = min(end + timedelta(days=1), cursor + timedelta(days=14))\n            body: dict[str, Any] = {\n                "range": {\n                    "startTime": datetime.combine(cursor, clock.min).astimezone().isoformat(),\n                    "endTime": datetime.combine(chunk_end, clock.min).astimezone().isoformat(),\n                },\n                "windowSize": "300s",\n                "pageSize": 10000,\n            }\n            page_token = None\n            seen_tokens: set[str] = set()\n            while True:\n                if page_token:\n                    body["pageToken"] = page_token\n                else:\n                    body.pop("pageToken", None)\n                response = self._request(\n                    "POST",\n                    f"users/me/dataTypes/{spec.key}/dataPoints:rollUp",\n                    json_body=body,\n                    cancel=cancel,\n                )\n                page = [\n                    self._prepare_five_minute_heart_rate_rollup(point)\n                    for point in response.get("rollupDataPoints", [])\n                    if isinstance(point, dict)\n                ]\n                yield page\n                page_token = response.get("nextPageToken")\n                if not page_token:\n                    break\n                if page_token in seen_tokens:\n                    raise ApiError(508, _("Repeated roll-up page for {label}.", label=spec.label))\n                seen_tokens.add(page_token)\n            cursor = chunk_end\n\n'''
    if anchor not in api:
        raise AssertionError("API insertion anchor missing")
    api_path.write_text(api.replace(anchor, method + anchor, 1))

# 3) Storage helpers for migration and stable replacement of a changing five-minute window.
storage_path = Path("google_health_viewer/storage.py")
storage = storage_path.read_text()
if "def delete_records_by_kind" not in storage:
    marker = '''    @staticmethod\n    def _merge_date_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:\n'''
    helpers = '''    def delete_records_by_kind(self, data_type: str, record_kind: str) -> int:\n        \"\"\"Delete one representation without touching other records for the metric.\"\"\"\n        with self._connect() as db:\n            cursor = db.execute(\n                "DELETE FROM records WHERE data_type = ? AND record_kind = ?",\n                (data_type, record_kind),\n            )\n            removed = cursor.rowcount\n        return max(0, int(removed if removed is not None else 0))\n\n    def reset_sync_ranges(self, data_type: str) -> None:\n        \"\"\"Forget downloaded coverage so a representation migration can refetch it.\"\"\"\n        with self._connect() as db:\n            db.execute("DELETE FROM sync_ranges WHERE data_type = ?", (data_type,))\n\n    def data_type_date_bounds(self, data_type: str) -> tuple[date, date] | None:\n        \"\"\"Return first/last local calendar dates stored for one data type.\"\"\"\n        with self._connect() as db:\n            row = db.execute(\n                \"\"\"\n                SELECT MIN(substr(COALESCE(start_time, end_time), 1, 10)) AS first_day,\n                       MAX(substr(COALESCE(start_time, end_time), 1, 10)) AS last_day\n                FROM records\n                WHERE data_type = ? AND COALESCE(start_time, end_time) IS NOT NULL\n                \"\"\",\n                (data_type,),\n            ).fetchone()\n        if not row or not row["first_day"] or not row["last_day"]:\n            return None\n        try:\n            return date.fromisoformat(row["first_day"]), date.fromisoformat(row["last_day"])\n        except (TypeError, ValueError):\n            return None\n\n'''
    if marker not in storage:
        raise AssertionError("Storage helper insertion anchor missing")
    storage = storage.replace(marker, helpers + marker, 1)

old_storage = '''            if record_kind == "daily_rollup":\n                db.executemany(\n                    \"\"\"\n                    DELETE FROM records\n                    WHERE data_type = ? AND record_kind = 'daily_rollup'\n                      AND COALESCE(start_time, '') = COALESCE(?, '')\n                      AND COALESCE(end_time, '') = COALESCE(?, '')\n                      AND record_id <> ?\n                    \"\"\",\n                    [(row[0], row[3], row[4], row[1]) for row in rows],\n                )\n'''
new_storage = '''            if record_kind in {"daily_rollup", "five_minute_rollup"}:\n                db.executemany(\n                    \"\"\"\n                    DELETE FROM records\n                    WHERE data_type = ? AND record_kind = ?\n                      AND COALESCE(start_time, '') = COALESCE(?, '')\n                      AND COALESCE(end_time, '') = COALESCE(?, '')\n                      AND record_id <> ?\n                    \"\"\",\n                    [(row[0], record_kind, row[3], row[4], row[1]) for row in rows],\n                )\n'''
if new_storage not in storage:
    if old_storage not in storage:
        raise AssertionError("Storage rollup replacement anchor missing")
    storage = storage.replace(old_storage, new_storage, 1)
storage_path.write_text(storage)

# 4) Sync routing + one-time raw -> five-minute migration, preserving existing HR date bounds.
workers_path = Path("google_health_viewer/workers.py")
workers = workers_path.read_text()
if 'HEART_RATE_STORAGE_VERSION = "heart-rate-five-minute-rollup-v1"' not in workers:
    workers = workers.replace(
        'FILTER_REPAIR_VERSION = "snake-case-filters-v1"\n',
        'FILTER_REPAIR_VERSION = "snake-case-filters-v1"\nHEART_RATE_STORAGE_VERSION = "heart-rate-five-minute-rollup-v1"\n',
        1,
    )

old_ranges = '''                    repair_key = f"{FILTER_REPAIR_VERSION}:{spec.key}"\n                    needs_filter_repair = bool(\n                        spec.operation == "list"\n                        and spec.filter_field\n                        and "_" in spec.filter_field.split(".", 1)[0]\n                        and not self.store.has_app_marker(repair_key)\n                    )\n                    if needs_filter_repair:\n                        archive_bounds = self.store.data_date_bounds()\n                        repair_start = (\n                            min(self.start_date, archive_bounds[0])\n                            if archive_bounds\n                            else self.start_date\n                        )\n                        ranges = [(repair_start, self.end_date)]\n                    else:\n                        ranges = self.store.missing_sync_ranges(\n                            spec.key,\n                            self.start_date,\n                            self.end_date,\n                            refresh_date=today,\n                        )\n'''
new_ranges = '''                    repair_key = f"{FILTER_REPAIR_VERSION}:{spec.key}"\n                    needs_heart_rate_migration = bool(\n                        spec.key == "heart-rate"\n                        and not self.store.has_app_marker(HEART_RATE_STORAGE_VERSION)\n                    )\n                    needs_filter_repair = bool(\n                        spec.operation == "list"\n                        and spec.filter_field\n                        and "_" in spec.filter_field.split(".", 1)[0]\n                        and not self.store.has_app_marker(repair_key)\n                    )\n                    if needs_heart_rate_migration:\n                        heart_rate_bounds = self.store.data_type_date_bounds("heart-rate")\n                        self.store.delete_records_by_kind("heart-rate", "data_point")\n                        self.store.reset_sync_ranges("heart-rate")\n                        migration_start = (\n                            min(self.start_date, heart_rate_bounds[0])\n                            if heart_rate_bounds\n                            else self.start_date\n                        )\n                        migration_end = (\n                            max(self.end_date, heart_rate_bounds[1])\n                            if heart_rate_bounds\n                            else self.end_date\n                        )\n                        ranges = [(migration_start, migration_end)]\n                    elif needs_filter_repair:\n                        archive_bounds = self.store.data_date_bounds()\n                        repair_start = (\n                            min(self.start_date, archive_bounds[0])\n                            if archive_bounds\n                            else self.start_date\n                        )\n                        ranges = [(repair_start, self.end_date)]\n                    else:\n                        ranges = self.store.missing_sync_ranges(\n                            spec.key,\n                            self.start_date,\n                            self.end_date,\n                            refresh_date=today,\n                        )\n'''
if new_ranges not in workers:
    if old_ranges not in workers:
        raise AssertionError("Worker range-selection anchor missing")
    workers = workers.replace(old_ranges, new_ranges, 1)

old_iterator = '''                        iterator = (\n                            client.iter_daily_rollups(\n                                spec, range_start, range_end, self._is_cancelled\n                            )\n                            if spec.operation == "daily_rollup"\n                            else client.iter_data_pages(\n                                spec, range_start, range_end, self._is_cancelled\n                            )\n                        )\n                        for page in iterator:\n                            count += self.store.upsert_records(\n                                spec.key,\n                                page,\n                                (\n                                    "daily_rollup"\n                                    if spec.operation == "daily_rollup"\n                                    else "data_point"\n                                ),\n                            )\n'''
new_iterator = '''                        if spec.operation == "daily_rollup":\n                            iterator = client.iter_daily_rollups(\n                                spec, range_start, range_end, self._is_cancelled\n                            )\n                            record_kind = "daily_rollup"\n                        elif spec.operation == "five_minute_rollup":\n                            iterator = client.iter_five_minute_heart_rate_rollups(\n                                spec, range_start, range_end, self._is_cancelled\n                            )\n                            record_kind = "five_minute_rollup"\n                        else:\n                            iterator = client.iter_data_pages(\n                                spec, range_start, range_end, self._is_cancelled\n                            )\n                            record_kind = "data_point"\n                        for page in iterator:\n                            count += self.store.upsert_records(\n                                spec.key,\n                                page,\n                                record_kind,\n                            )\n'''
if new_iterator not in workers:
    if old_iterator not in workers:
        raise AssertionError("Worker iterator anchor missing")
    workers = workers.replace(old_iterator, new_iterator, 1)

old_marker = '''                    if needs_filter_repair:\n                        self.store.set_app_marker(repair_key)\n'''
new_marker = '''                    if needs_heart_rate_migration:\n                        self.store.set_app_marker(HEART_RATE_STORAGE_VERSION)\n                    if needs_filter_repair:\n                        self.store.set_app_marker(repair_key)\n'''
if new_marker not in workers:
    if old_marker not in workers:
        raise AssertionError("Worker migration marker anchor missing")
    workers = workers.replace(old_marker, new_marker, 1)
workers_path.write_text(workers)

# 5) Shared analysis stays backward-compatible, but synced desktop data are now already 5-min rollups.
replace_once(
    "google_health_viewer/heart_rate_core.py",
    '''    Desktop normally stores native samples, while Android stores Google Health\n    five-minute roll-ups to keep the local archive bounded. Both representations\n    are converted to the same timestamp/value series here before dashboard\n    aggregation.\n''',
    '''    Desktop and Android sync now store Google Health five-minute roll-ups to keep\n    the local archive bounded. Legacy raw desktop records and imported native\n    samples remain readable and are converted to the same timestamp/value series.\n''',
)

# 6) API tests: the heart-rate spec must never route to raw dataPoints and rollup payloads stay rich.
test_api_path = Path("tests/test_api.py")
test_api = test_api_path.read_text()
old_filter_assert = '''    assert DATA_TYPE_BY_KEY["heart-rate"].filter_field == (\n        "heart_rate.sample_time.physical_time"\n    )\n'''
new_filter_assert = '''    assert DATA_TYPE_BY_KEY["heart-rate-variability"].filter_field == (\n        "heart_rate_variability.sample_time.physical_time"\n    )\n'''
if old_filter_assert in test_api:
    test_api = test_api.replace(old_filter_assert, new_filter_assert, 1)
if "test_heart_rate_sync_uses_five_minute_server_rollups" not in test_api:
    test_api += '''\n\ndef test_heart_rate_sync_uses_five_minute_server_rollups():\n    spec = DATA_TYPE_BY_KEY["heart-rate"]\n    assert spec.operation == "five_minute_rollup"\n    assert spec.filter_field is None\n\n    client = GoogleHealthClient(None)\n    calls = []\n\n    def fake_request(method, path, **kwargs):\n        calls.append((method, path, kwargs))\n        return {\n            "rollupDataPoints": [\n                {\n                    "startTime": "2026-09-02T10:00:00+00:00",\n                    "endTime": "2026-09-02T10:05:00+00:00",\n                    "heartRate": {\n                        "beatsPerMinuteAvg": 72.5,\n                        "beatsPerMinuteMin": 68.0,\n                        "beatsPerMinuteMax": 79.0,\n                    },\n                }\n            ]\n        }\n\n    client._request = fake_request\n    pages = list(\n        client.iter_five_minute_heart_rate_rollups(\n            spec,\n            date(2026, 9, 2),\n            date(2026, 9, 2),\n        )\n    )\n\n    method, path, kwargs = calls[0]\n    assert method == "POST"\n    assert path.endswith("/dataPoints:rollUp")\n    assert kwargs["json_body"]["windowSize"] == "300s"\n    point = pages[0][0]\n    assert point["heartRate"]["beatsPerMinuteAvg"] == 72.5\n    assert point["heartRate"]["beatsPerMinuteMin"] == 68.0\n    assert point["heartRate"]["beatsPerMinuteMax"] == 79.0\n    assert point["heartRate"]["beatsPerMinute"] == 72.5\n    assert point["name"] == (\n        "heart-rate:5m:2026-09-02T10:00:00+00:00:2026-09-02T10:05:00+00:00"\n    )\n'''
test_api_path.write_text(test_api)

# 7) Storage regression tests: stable window replacement and selective legacy cleanup.
test_storage_path = Path("tests/test_storage.py")
test_storage = test_storage_path.read_text()
if "test_five_minute_heart_rate_rollup_replaces_same_window" not in test_storage:
    test_storage += '''\n\ndef test_five_minute_heart_rate_rollup_replaces_same_window(tmp_path: Path):\n    store = HealthStore(tmp_path / "health.sqlite3")\n\n    def rollup(avg: float) -> dict:\n        return {\n            "startTime": "2026-09-02T10:00:00+00:00",\n            "endTime": "2026-09-02T10:05:00+00:00",\n            "heartRate": {"beatsPerMinuteAvg": avg, "beatsPerMinute": avg},\n        }\n\n    store.upsert_records("heart-rate", [rollup(70.0)], "five_minute_rollup")\n    store.upsert_records("heart-rate", [rollup(74.0)], "five_minute_rollup")\n\n    records = store.list_records("heart-rate")\n    assert len(records) == 1\n    assert records[0]["record_kind"] == "five_minute_rollup"\n    assert records[0]["payload"]["heartRate"]["beatsPerMinuteAvg"] == 74.0\n\n\ndef test_heart_rate_storage_migration_removes_raw_only_and_resets_coverage(tmp_path: Path):\n    store = HealthStore(tmp_path / "health.sqlite3")\n    raw = {\n        "name": "legacy-heart-rate",\n        "heartRate": {\n            "sampleTime": {"physicalTime": "2026-09-01T10:01:00+00:00"},\n            "beatsPerMinute": 71.0,\n        },\n    }\n    rolled = {\n        "name": "heart-rate:5m:2026-09-02T10:00:00+00:00:2026-09-02T10:05:00+00:00",\n        "startTime": "2026-09-02T10:00:00+00:00",\n        "endTime": "2026-09-02T10:05:00+00:00",\n        "heartRate": {"beatsPerMinuteAvg": 72.0, "beatsPerMinute": 72.0},\n    }\n    store.upsert_records("heart-rate", [raw], "data_point")\n    store.upsert_records("heart-rate", [rolled], "five_minute_rollup")\n    store.mark_sync_range("heart-rate", date(2026, 9, 1), date(2026, 9, 2))\n\n    assert store.data_type_date_bounds("heart-rate") == (\n        date(2026, 9, 1),\n        date(2026, 9, 2),\n    )\n    assert store.delete_records_by_kind("heart-rate", "data_point") == 1\n    store.reset_sync_ranges("heart-rate")\n\n    records = store.list_records("heart-rate")\n    assert len(records) == 1\n    assert records[0]["record_kind"] == "five_minute_rollup"\n    assert store.missing_sync_ranges(\n        "heart-rate",\n        date(2026, 9, 1),\n        date(2026, 9, 2),\n        refresh_date=date(2026, 9, 3),\n    ) == [(date(2026, 9, 1), date(2026, 9, 1))]\n'''
test_storage_path.write_text(test_storage)

# 8) Heart-rate-specific CI now covers ingestion + persistence, not just rendering semantics.
workflow_path = Path(".github/workflows/heart-rate-core.yml")
workflow = workflow_path.read_text()
old_lint = '        run: ruff check google_health_viewer/__init__.py google_health_viewer/heart_rate_core.py tests/test_heart_rate_five_minute_core.py\n'
new_lint = '''        run: >-\n          ruff check google_health_viewer/__init__.py google_health_viewer/api.py\n          google_health_viewer/constants.py google_health_viewer/heart_rate_core.py\n          google_health_viewer/storage.py google_health_viewer/workers.py tests/test_api.py\n          tests/test_storage.py tests/test_heart_rate_five_minute_core.py\n'''
if new_lint not in workflow:
    if old_lint not in workflow:
        raise AssertionError("Heart-rate workflow lint anchor missing")
    workflow = workflow.replace(old_lint, new_lint, 1)
old_test = '        run: pytest -q tests/test_heart_rate_five_minute_core.py\n'
new_test = '        run: pytest -q tests/test_api.py tests/test_storage.py tests/test_heart_rate_five_minute_core.py\n'
if new_test not in workflow:
    if old_test not in workflow:
        raise AssertionError("Heart-rate workflow test anchor missing")
    workflow = workflow.replace(old_test, new_test, 1)
workflow_path.write_text(workflow)
