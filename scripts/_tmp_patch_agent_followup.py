from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"marker not found in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Runtime: 15 analysis steps, slightly earlier Tool Factory gate, clearer slow-path notice,
# and stronger instructions against inferring missing semantic data from generic raw metrics.
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "MAX_ANALYSIS_STEPS = 10\nMAX_FACTORY_REPAIR_ATTEMPTS = 3\nMAX_FACTORY_GATE_REFUSALS = 2\nMAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2\n",
    "MAX_ANALYSIS_STEPS = 15\nMAX_FACTORY_REPAIR_ATTEMPTS = 3\nMAX_FACTORY_GATE_REFUSALS = 2\nMAX_RAW_SERIES_PROBES_BEFORE_FACTORY = 2\nFACTORY_GATE_AFTER_ANALYSIS_STEPS = 3\n",
)
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "- When the runtime Tool Factory gate exposes only create_learned_tool, you must call it; do not answer directly before resolving or exhausting that gate.\n",
    "- When the runtime Tool Factory gate exposes only create_learned_tool, you must call it; do not answer directly before resolving or exhausting that gate.\n- If a learned tool returns an empty/zero result because a semantic input is unavailable, inspect the relevant semantic built-in directly before claiming the underlying data are absent.\n- Sleep stages must be checked with get_sleep_stage_series/analyze_sleep_stages, not inferred from a missing generic sleep.summary field.\n- Preserve units and method labels returned by deterministic tools; never relabel VitalChronicle cardio-load points as kcal.\n",
)
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "        raw_series_probes = 0\n        tool_result_cache: dict[str, dict[str, Any]] = {}\n",
    "        raw_series_probes = 0\n        factory_creation_notice_shown = False\n        tool_result_cache: dict[str, dict[str, Any]] = {}\n",
)
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "                elif name == \"create_learned_tool\":\n                    event(_(\"Capability gap detected · validating a reusable learned tool…\"))\n",
    "                elif name == \"create_learned_tool\":\n                    if not factory_creation_notice_shown:\n                        event(\n                            _(\n                                \"The AI is creating a custom tool; this may take longer than usual.\"\n                            )\n                        )\n                        factory_creation_notice_shown = True\n                    event(_(\"Capability gap detected · validating a reusable learned tool…\"))\n",
)
replace_once(
    "google_health_viewer/agent_runtime_v2.py",
    "                and analysis_steps >= 4\n",
    "                and analysis_steps >= FACTORY_GATE_AFTER_ANALYSIS_STEPS\n",
)

# 2) Progress indicator: when an answer is complete, stop the indeterminate animation and
# leave a static full bar instead of a still-moving busy indicator.
replace_once(
    "google_health_viewer/agent_integration.py",
    "        self._activity_timer.stop()\n        self._activity_phase = \"\"\n        if self._activity_events:\n",
    "        self._activity_timer.stop()\n        self._activity_phase = \"\"\n        self.activity_progress.setRange(0, 1000)\n        self.activity_progress.setValue(1000)\n        self.activity_progress.setTextVisible(False)\n        if self._activity_events:\n",
)
replace_once(
    "google_health_viewer/ai_chat.py",
    "    def _finish_activity(self) -> None:\n        self._activity_active = False\n        self._activity_timer.stop()\n        self._activity_events = []\n",
    "    def _finish_activity(self) -> None:\n        self._activity_active = False\n        self._activity_timer.stop()\n        self.activity_progress.setRange(0, 1000)\n        self.activity_progress.setValue(1000)\n        self.activity_progress.setTextVisible(False)\n        self._activity_events = []\n",
)

# 3) Sleep stages: support Health Connect stage/stageType numeric codes as well as Fitbit-style
# type strings and summary dictionaries.
analysis = Path("google_health_viewer/analysis.py")
text = analysis.read_text(encoding="utf-8")
old_sleep = '''def _sleep_stage_totals(record: dict[str, Any]) -> dict[str, float]:
    """Return the duration of each sleep stage for one session."""

    def find_stages(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            stages = value.get("stagesSummary")
            if isinstance(stages, list):
                return [item for item in stages if isinstance(item, dict)]
            for child in value.values():
                found = find_stages(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_stages(child)
                if found:
                    return found
        return []

    totals: dict[str, float] = defaultdict(float)
    summarized_stages = find_stages(record["payload"])
    for stage in summarized_stages:
        stage_type = str(stage.get("type", "ALTRO")).upper()
        minutes = coerce_number(stage.get("minutes"))
        if minutes is not None:
            totals[stage_type] += minutes / 60.0
    # Some sleep sessions expose only the raw stage intervals. Keep those
    # sessions available to both the chart and the complete AI analysis.
    if not summarized_stages:
        for stage in _find_named_list(record["payload"], "stages"):
            stage_type = str(stage.get("type", "ALTRO")).upper()
            start = parse_timestamp(
                _find_named_value(stage, {"starttime", "physicaltime"})
            )
            end = parse_timestamp(_find_named_value(stage, {"endtime"}))
            if start is not None and end is not None and end > start:
                totals[stage_type] += (end - start) / 3600.0
    return dict(totals)
'''
new_sleep = '''_HEALTH_CONNECT_SLEEP_STAGE_CODES = {
    0: "UNKNOWN",
    1: "AWAKE",
    2: "SLEEPING",
    3: "OUT_OF_BED",
    4: "LIGHT",
    5: "DEEP",
    6: "REM",
    7: "AWAKE_IN_BED",
}


def _normalize_sleep_stage(value: Any) -> str:
    """Normalize Fitbit/Health Connect sleep-stage values to stable names."""

    if value is None or isinstance(value, bool):
        return "UNKNOWN"
    numeric = coerce_number(value)
    if numeric is not None and float(numeric).is_integer():
        mapped = _HEALTH_CONNECT_SLEEP_STAGE_CODES.get(int(numeric))
        if mapped:
            return mapped
    normalized = re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")
    if "AWAKE_IN_BED" in normalized:
        return "AWAKE_IN_BED"
    if "OUT_OF_BED" in normalized or "AWAKE_OUT_OF_BED" in normalized:
        return "OUT_OF_BED"
    if "DEEP" in normalized:
        return "DEEP"
    if "REM" in normalized:
        return "REM"
    if "LIGHT" in normalized:
        return "LIGHT"
    if "AWAKE" in normalized or normalized == "WAKE":
        return "AWAKE"
    if "SLEEPING" in normalized or normalized == "SLEEP":
        return "SLEEPING"
    return normalized or "UNKNOWN"


def _sleep_stage_totals(record: dict[str, Any]) -> dict[str, float]:
    """Return the duration of each sleep stage for one session."""

    def find_stages(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            stages = value.get("stagesSummary")
            if isinstance(stages, list):
                return [item for item in stages if isinstance(item, dict)]
            if isinstance(stages, dict):
                rows: list[dict[str, Any]] = []
                for stage_name, stage_value in stages.items():
                    if isinstance(stage_value, dict):
                        rows.append({"type": stage_name, **stage_value})
                    elif coerce_number(stage_value) is not None:
                        rows.append({"type": stage_name, "minutes": stage_value})
                if rows:
                    return rows
            for child in value.values():
                found = find_stages(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_stages(child)
                if found:
                    return found
        return []

    totals: dict[str, float] = defaultdict(float)
    summarized_stages = find_stages(record["payload"])
    for stage in summarized_stages:
        raw_type = _find_named_value(stage, {"type", "stage", "stagetype"})
        stage_type = _normalize_sleep_stage(raw_type)
        minutes = coerce_number(_find_named_value(stage, {"minutes", "durationminutes"}))
        if minutes is not None:
            totals[stage_type] += minutes / 60.0
    # Health Connect commonly exposes raw Stage objects with `stage`/`stageType`
    # (integer constants 0..7) plus startTime/endTime rather than Fitbit's `type` field.
    if not summarized_stages:
        for stage in _find_named_list(record["payload"], "stages"):
            raw_type = _find_named_value(stage, {"type", "stage", "stagetype"})
            stage_type = _normalize_sleep_stage(raw_type)
            start = parse_timestamp(
                _find_named_value(stage, {"starttime", "physicaltime"})
            )
            end = parse_timestamp(_find_named_value(stage, {"endtime"}))
            if start is not None and end is not None and end > start:
                totals[stage_type] += (end - start) / 3600.0
    return dict(totals)
'''
if old_sleep not in text:
    raise SystemExit("sleep parser marker not found")
analysis.write_text(text.replace(old_sleep, new_sleep, 1), encoding="utf-8")

# 4) Semantic sleep-stage series: group an overnight session by wake-up date, which makes
# event day + 1 correspond to the following night's sleep; expose diagnostics when stages truly are absent.
factory = Path("google_health_viewer/agent_tool_factory.py")
text = factory.read_text(encoding="utf-8")
text = text.replace(
    '        "Return per-night recorded sleep-stage durations (deep, REM, light, awake) as a "\n        "deterministic date series. Missing stages are never inferred."\n',
    '        "Return per-night recorded sleep-stage durations (deep, REM, light, awake) keyed by "\n        "wake-up date as a deterministic series. Missing stages are never inferred."\n',
    1,
)
old_stage_tool = '''    def _tool_get_sleep_stage_series(self, args, **_):
        left, right = base._bounds(args.get("start"), args.get("end"), 60)
        records = self._records("sleep", left, right)
        stages = base.sleep_stage_points(records)
        by_day: dict[str, dict[str, float]] = {}
        for ts, values in stages:
            if not isinstance(values, dict):
                continue
            day = base._day(float(ts))
            row = by_day.setdefault(day, {"deep": 0.0, "rem": 0.0, "light": 0.0, "awake": 0.0})
            for raw_name, raw_value in values.items():
                name = str(raw_name).strip().lower().replace("-", "_").replace(" ", "_")
                if "deep" in name:
                    target = "deep"
                elif "rem" in name:
                    target = "rem"
                elif "light" in name:
                    target = "light"
                elif "awake" in name or "wake" in name:
                    target = "awake"
                else:
                    continue
                try:
                    hours = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(hours):
                    row[target] += hours
        expected = (right - left).days + 1
        rows = [
            {"date": day, **{key: round(value, 4) for key, value in values.items()}}
            for day, values in sorted(by_day.items())
        ]
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "daily_stages": rows,
            "observed_nights": len(rows),
            "expected_days": expected,
            "coverage": round(len(rows) / max(1, expected), 3),
            "confidence": base._confidence(len(rows), expected),
            "method": "Recorded wearable sleep-stage durations grouped by local date.",
            "limitations": "Missing sleep stages are omitted and never inferred or zero-filled.",
        }
'''
new_stage_tool = '''    def _tool_get_sleep_stage_series(self, args, **_):
        left, right = base._bounds(args.get("start"), args.get("end"), 60)
        records = self._records("sleep", left, right)
        by_day: dict[str, dict[str, float]] = {}
        sessions_with_stages = 0
        for record in records:
            stage_points = base.sleep_stage_points([record])
            if not stage_points:
                continue
            values = stage_points[0][1]
            if not isinstance(values, dict):
                continue
            wake_day = base._parse_date(record.get("end_time") or record.get("start_time"))
            if wake_day is None:
                continue
            sessions_with_stages += 1
            day = wake_day.isoformat()
            row = by_day.setdefault(day, {"deep": 0.0, "rem": 0.0, "light": 0.0, "awake": 0.0})
            for raw_name, raw_value in values.items():
                name = str(raw_name).strip().lower().replace("-", "_").replace(" ", "_")
                if "deep" in name:
                    target = "deep"
                elif "rem" in name:
                    target = "rem"
                elif "light" in name:
                    target = "light"
                elif "awake" in name or "wake" in name or "out_of_bed" in name:
                    target = "awake"
                else:
                    continue
                try:
                    hours = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(hours):
                    row[target] += hours
        expected = (right - left).days + 1
        rows = [
            {"date": day, **{key: round(value, 4) for key, value in values.items()}}
            for day, values in sorted(by_day.items())
        ]
        return {
            "period": {"start": left.isoformat(), "end": right.isoformat()},
            "daily_stages": rows,
            "sleep_session_records": len(records),
            "sessions_with_stages": sessions_with_stages,
            "observed_nights": len(rows),
            "expected_days": expected,
            "coverage": round(len(rows) / max(1, expected), 3),
            "confidence": base._confidence(len(rows), expected),
            "date_semantics": "wake_up_date",
            "method": "Recorded wearable sleep-stage durations grouped by local wake-up date.",
            "limitations": (
                "Missing sleep stages are omitted and never inferred or zero-filled. A nonzero "
                "sleep_session_records value with zero sessions_with_stages means sessions exist "
                "but no recognizable stage detail was recorded."
            ),
        }
'''
if old_stage_tool not in text:
    raise SystemExit("sleep stage series tool marker not found")
factory.write_text(text.replace(old_stage_tool, new_stage_tool, 1), encoding="utf-8")

# 5) Make cardio-load units explicit so the final model cannot confuse the load index with kcal.
replace_once(
    "google_health_viewer/agent_tools.py",
    '            "coverage": round(len(daily) / max(1, expected), 3),\n            "confidence": _confidence(len(daily), expected),\n            "method": f"VitalChronicle load index from {method}; not Google\'s proprietary Cardio Load.",\n',
    '            "coverage": round(len(daily) / max(1, expected), 3),\n            "confidence": _confidence(len(daily), expected),\n            "unit": "VitalChronicle load points",\n            "method": f"VitalChronicle load index from {method}; not Google\'s proprietary Cardio Load and not kcal.",\n',
)

# 6) Personal AI page: readable non-elided rows plus inspectable details for tools and learned user associations.
ui = Path("google_health_viewer/agent_ui.py")
text = ui.read_text(encoding="utf-8")
text = text.replace("from __future__ import annotations\n\nfrom typing import Any\n", "from __future__ import annotations\n\nimport json\nfrom typing import Any\n", 1)
text = text.replace("    QHBoxLayout,\n    QLabel,\n", "    QHBoxLayout,\n    QHeaderView,\n    QLabel,\n", 1)
helper_marker = '''def _selected_payload(tree: QTreeWidget) -> dict[str, Any] | None:
    items = tree.selectedItems()
    if not items:
        return None
    value = items[0].data(0, Qt.UserRole)
    return value if isinstance(value, dict) else None


'''
helpers = '''def _selected_payload(tree: QTreeWidget) -> dict[str, Any] | None:
    items = tree.selectedItems()
    if not items:
        return None
    value = items[0].data(0, Qt.UserRole)
    return value if isinstance(value, dict) else None


def _pretty_detail(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def _tool_detail_text(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{_('Name')}: {_pretty_detail(item.get('name'))}",
            f"{_('Kind')}: {_pretty_detail(item.get('kind'))}",
            f"{_('Version')}: {_pretty_detail(item.get('version'))}",
            f"{_('Status')}: {_pretty_detail(item.get('status'))}",
            f"{_('Capability')}: {_pretty_detail(item.get('capability'))}",
            f"{_('Confidence')}: {float(item.get('confidence') or 0) * 100:.0f}%",
            f"{_('Uses')}: {_pretty_detail(item.get('use_count'))}",
            f"{_('Last used')}: {_pretty_detail(item.get('last_used_at'))}",
            f"{_('Replacement')}: {_pretty_detail(item.get('replacement'))}",
            "",
            _("Description"),
            _pretty_detail(item.get("description")),
            "",
            _("Parameters"),
            _pretty_detail(item.get("parameters")),
            "",
            _("Outputs"),
            _pretty_detail(item.get("outputs")),
            "",
            _("Dependencies"),
            _pretty_detail(item.get("dependencies")),
            "",
            _("Pipeline"),
            _pretty_detail(item.get("pipeline")),
            "",
            f"{_('Created')}: {_pretty_detail(item.get('created_at'))}",
            f"{_('Updated')}: {_pretty_detail(item.get('updated_at'))}",
        ]
    )


def _user_model_detail_text(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{_('Key')}: {_pretty_detail(item.get('key'))}",
            f"{_('Confidence')}: {float(item.get('confidence') or 0) * 100:.0f}%",
            f"{_('Evidence')}: {_pretty_detail(item.get('evidence_count'))}",
            f"{_('Source')}: {_pretty_detail(item.get('source'))}",
            f"{_('Updated')}: {_pretty_detail(item.get('updated_at'))}",
            "",
            _("Learned association"),
            _pretty_detail(item.get("statement")),
            "",
            _("Evidence details"),
            _pretty_detail(item.get("evidence")),
        ]
    )


def _show_detail_dialog(parent: QWidget, title: str, text: str) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(820, 620)
    dialog.setMinimumSize(620, 420)
    layout = QVBoxLayout(dialog)
    details = QPlainTextEdit()
    details.setReadOnly(True)
    details.setPlainText(text)
    layout.addWidget(details, 1)
    actions = QHBoxLayout()
    actions.addStretch()
    close_button = QPushButton(_("Close"))
    close_button.clicked.connect(dialog.accept)
    actions.addWidget(close_button)
    layout.addLayout(actions)
    dialog.exec()


'''
if helper_marker not in text:
    raise SystemExit("agent_ui helper marker not found")
text = text.replace(helper_marker, helpers, 1)

text = text.replace(
    '''        row.setData(0, Qt.UserRole, item)\n        window.agent_tools_tree.addTopLevelItem(row)\n''',
    '''        row.setData(0, Qt.UserRole, item)\n        row.setToolTip(0, f"{item['name']}\\n{item.get('description', '')}")\n        row.setToolTip(2, str(item.get("capability") or ""))\n        window.agent_tools_tree.addTopLevelItem(row)\n''',
    1,
)
text = text.replace(
    '''        row.setData(0, Qt.UserRole, item)\n        window.agent_model_tree.addTopLevelItem(row)\n''',
    '''        row.setData(0, Qt.UserRole, item)\n        row.setToolTip(0, str(item.get("statement") or ""))\n        window.agent_model_tree.addTopLevelItem(row)\n''',
    1,
)
old_tools_ui = '''    window.agent_tools_tree.setAlternatingRowColors(True)
    tools_layout.addWidget(window.agent_tools_tree, 1)
    delete_tool = QPushButton(_("Delete selected learned tool"))
    tools_layout.addWidget(delete_tool)
'''
new_tools_ui = '''    window.agent_tools_tree.setAlternatingRowColors(True)
    window.agent_tools_tree.setWordWrap(True)
    window.agent_tools_tree.setTextElideMode(Qt.ElideNone)
    tools_header = window.agent_tools_tree.header()
    tools_header.setStretchLastSection(False)
    tools_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tools_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    tools_header.setSectionResizeMode(2, QHeaderView.Stretch)
    tools_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    tools_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    tools_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
    tools_layout.addWidget(window.agent_tools_tree, 1)
    tool_actions = QHBoxLayout()
    view_tool = QPushButton(_("View selected tool details…"))
    delete_tool = QPushButton(_("Delete selected learned tool"))
    tool_actions.addWidget(view_tool)
    tool_actions.addStretch()
    tool_actions.addWidget(delete_tool)
    tools_layout.addLayout(tool_actions)
'''
if old_tools_ui not in text:
    raise SystemExit("tools UI marker not found")
text = text.replace(old_tools_ui, new_tools_ui, 1)
old_model_ui = '''    window.agent_model_tree.setAlternatingRowColors(True)
    model_layout.addWidget(window.agent_model_tree, 1)
    forget = QPushButton(_("Forget selected personal association"))
    model_layout.addWidget(forget)
'''
new_model_ui = '''    window.agent_model_tree.setAlternatingRowColors(True)
    window.agent_model_tree.setWordWrap(True)
    window.agent_model_tree.setTextElideMode(Qt.ElideNone)
    model_header = window.agent_model_tree.header()
    model_header.setSectionResizeMode(0, QHeaderView.Stretch)
    model_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    model_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    model_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    model_layout.addWidget(window.agent_model_tree, 1)
    model_actions = QHBoxLayout()
    view_model = QPushButton(_("View selected association details…"))
    forget = QPushButton(_("Forget selected personal association"))
    model_actions.addWidget(view_model)
    model_actions.addStretch()
    model_actions.addWidget(forget)
    model_layout.addLayout(model_actions)
'''
if old_model_ui not in text:
    raise SystemExit("model UI marker not found")
text = text.replace(old_model_ui, new_model_ui, 1)

insert_before_delete = '''    def delete_selected_tool() -> None:
'''
new_detail_handlers = '''    def show_selected_tool_details() -> None:
        item = _selected_payload(window.agent_tools_tree)
        if not item:
            return
        _show_detail_dialog(
            window,
            _("Tool details · {name}", name=str(item.get("name") or "")),
            _tool_detail_text(item),
        )

    def show_selected_model_details() -> None:
        item = _selected_payload(window.agent_model_tree)
        if not item:
            return
        _show_detail_dialog(window, _("Learned association details"), _user_model_detail_text(item))

    def delete_selected_tool() -> None:
'''
if insert_before_delete not in text:
    raise SystemExit("detail handler insertion marker not found")
text = text.replace(insert_before_delete, new_detail_handlers, 1)
text = text.replace(
    '''    window.agent_enabled_check.toggled.connect(toggle_agent)\n    calibrate.clicked.connect(open_calibration)\n    delete_tool.clicked.connect(delete_selected_tool)\n    forget.clicked.connect(forget_selected)\n''',
    '''    window.agent_enabled_check.toggled.connect(toggle_agent)\n    calibrate.clicked.connect(open_calibration)\n    view_tool.clicked.connect(show_selected_tool_details)\n    window.agent_tools_tree.itemDoubleClicked.connect(\n        lambda _item, _column: show_selected_tool_details()\n    )\n    delete_tool.clicked.connect(delete_selected_tool)\n    view_model.clicked.connect(show_selected_model_details)\n    window.agent_model_tree.itemDoubleClicked.connect(\n        lambda _item, _column: show_selected_model_details()\n    )\n    forget.clicked.connect(forget_selected)\n''',
    1,
)
ui.write_text(text, encoding="utf-8")

# 7) Regression tests.
analysis_test = Path("tests/test_analysis.py")
t = analysis_test.read_text(encoding="utf-8")
if "test_health_connect_numeric_sleep_stage_codes_are_parsed" not in t:
    t += '''\n\ndef test_health_connect_numeric_sleep_stage_codes_are_parsed():\n    records = [\n        {\n            "start_time": "2026-08-01T22:00:00+00:00",\n            "end_time": "2026-08-02T06:00:00+00:00",\n            "payload": {\n                "sleep": {\n                    "stages": [\n                        {\n                            "stage": 4,\n                            "startTime": "2026-08-01T22:00:00+00:00",\n                            "endTime": "2026-08-01T23:00:00+00:00",\n                        },\n                        {\n                            "stage": 5,\n                            "startTime": "2026-08-01T23:00:00+00:00",\n                            "endTime": "2026-08-02T00:30:00+00:00",\n                        },\n                        {\n                            "stageType": 6,\n                            "startTime": "2026-08-02T00:30:00+00:00",\n                            "endTime": "2026-08-02T02:00:00+00:00",\n                        },\n                    ]\n                }\n            },\n        }\n    ]\n\n    stages = sleep_stage_points(records)[0][1]\n    assert stages["LIGHT"] == 1.0\n    assert stages["DEEP"] == 1.5\n    assert stages["REM"] == 1.5\n'''
    analysis_test.write_text(t, encoding="utf-8")

factory_test = Path("tests/test_agent_tool_factory_v2.py")
t = factory_test.read_text(encoding="utf-8")
if "test_sleep_stage_series_uses_health_connect_codes_and_wakeup_date" not in t:
    t += '''\n\nclass SleepStageHealthStore(DummyHealthStore):\n    def list_records(self, data_type, *_args, **_kwargs):\n        if data_type != "sleep":\n            return []\n        return [\n            {\n                "start_time": "2026-08-01T22:00:00+00:00",\n                "end_time": "2026-08-02T06:00:00+00:00",\n                "payload": {\n                    "sleep": {\n                        "stages": [\n                            {\n                                "stage": 5,\n                                "startTime": "2026-08-01T23:00:00+00:00",\n                                "endTime": "2026-08-02T00:30:00+00:00",\n                            },\n                            {\n                                "stage": 6,\n                                "startTime": "2026-08-02T00:30:00+00:00",\n                                "endTime": "2026-08-02T01:30:00+00:00",\n                            },\n                        ]\n                    }\n                },\n            }\n        ]\n\n\ndef test_sleep_stage_series_uses_health_connect_codes_and_wakeup_date(tmp_path):\n    store = AgentStore(tmp_path / "agent.sqlite3")\n    executor = EnhancedSafeToolExecutor(SleepStageHealthStore(tmp_path / "health.sqlite3"), store)\n    result = executor.execute(\n        "get_sleep_stage_series", {"start": "2026-08-01", "end": "2026-08-02"}\n    )\n\n    assert result["sleep_session_records"] == 1\n    assert result["sessions_with_stages"] == 1\n    assert result["daily_stages"][0]["date"] == "2026-08-02"\n    assert result["daily_stages"][0]["deep"] == 1.5\n    assert result["date_semantics"] == "wake_up_date"\n\n\ndef test_agent_analysis_budget_is_fifteen_steps():\n    from google_health_viewer import agent_runtime_v2\n\n    assert agent_runtime_v2.MAX_ANALYSIS_STEPS == 15\n'''
    factory_test.write_text(t, encoding="utf-8")

chat_test = Path("tests/test_ai_chat.py")
t = chat_test.read_text(encoding="utf-8")
old = '''    window._finish_activity()\n    assert window.activity_panel.isVisible()\n    assert window.activity_title.text() == "AI · token usage"\n    assert not window._activity_timer.isActive()\n'''
new = '''    window._finish_activity()\n    assert window.activity_panel.isVisible()\n    assert window.activity_title.text() == "AI · token usage"\n    assert not window._activity_timer.isActive()\n    assert window.activity_progress.maximum() == 1000\n    assert window.activity_progress.value() == 1000\n'''
if old not in t:
    raise SystemExit("ai_chat progress test marker not found")
chat_test.write_text(t.replace(old, new, 1), encoding="utf-8")

ui_test = Path("tests/test_agent_ui_details.py")
if not ui_test.exists():
    ui_test.write_text(
        '''from google_health_viewer.agent_ui import _tool_detail_text, _user_model_detail_text\n\n\ndef test_tool_details_keep_full_name_description_and_pipeline():\n    long_name = "analyze_cardio_load_deep_sleep_response_with_personal_recovery"\n    text = _tool_detail_text(\n        {\n            "name": long_name,\n            "kind": "learned",\n            "version": 1,\n            "status": "active",\n            "capability": "analysis.temporal.event.response.recovery",\n            "description": "A deliberately long description that must remain fully inspectable.",\n            "confidence": 0.7,\n            "use_count": 3,\n            "pipeline": [{"op": "call_tool", "tool": "calculate_cardio_load"}],\n        }\n    )\n    assert long_name in text\n    assert "deliberately long description" in text\n    assert "calculate_cardio_load" in text\n\n\ndef test_user_model_details_keep_full_statement_and_evidence():\n    statement = "A long learned association that should never be hidden by the compact table view."\n    text = _user_model_detail_text(\n        {\n            "key": "recovery_preference",\n            "statement": statement,\n            "confidence": 0.63,\n            "evidence_count": 2,\n            "source": "feedback",\n            "evidence": [{"answer": "Example personal feedback"}],\n        }\n    )\n    assert statement in text\n    assert "Example personal feedback" in text\n''',
        encoding="utf-8",
    )
