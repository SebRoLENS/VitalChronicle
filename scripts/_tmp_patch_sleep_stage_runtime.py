from pathlib import Path


path = Path("google_health_viewer/deterministic_context_patch.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    '_WAKE_STAGES = {"AWAKE", "WAKE", "OUT_OF_BED"}\n',
    '_WAKE_STAGES = {"AWAKE", "WAKE", "OUT_OF_BED", "AWAKE_IN_BED"}\n',
    1,
)
text = text.replace(
    '    "OUT_OF_BED",\n    "AWAKE",\n',
    '    "OUT_OF_BED",\n    "AWAKE_IN_BED",\n    "AWAKE",\n',
    1,
)
text = text.replace(
    '    "ASLEEP",\n    "SLEEP",\n',
    '    "ASLEEP",\n    "SLEEPING",\n    "SLEEP",\n',
    1,
)

old_normalize = '''def _normalize_stage(value: Any) -> str:
    text = str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    for stage in _KNOWN_STAGES:
        if text == stage or text.endswith(f"_{stage}"):
            return stage
    return text or "UNKNOWN"
'''
new_normalize = '''def _normalize_stage(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "UNKNOWN"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None and math.isfinite(numeric) and numeric.is_integer():
        health_connect_stages = {
            0: "UNKNOWN",
            1: "AWAKE",
            2: "SLEEPING",
            3: "OUT_OF_BED",
            4: "LIGHT",
            5: "DEEP",
            6: "REM",
            7: "AWAKE_IN_BED",
        }
        mapped = health_connect_stages.get(int(numeric))
        if mapped:
            return mapped
    text = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    for stage in _KNOWN_STAGES:
        if text == stage or text.endswith(f"_{stage}"):
            return stage
    return text or "UNKNOWN"
'''
if old_normalize not in text:
    raise SystemExit("active sleep-stage normalization marker not found")
text = text.replace(old_normalize, new_normalize, 1)

old_interval = '        intervals.append((start, end, _normalize_stage(entry.get("type"))))\n'
new_interval = '''        stage_value = analysis._find_named_value(entry, {"type", "stage", "stagetype"})
        intervals.append((start, end, _normalize_stage(stage_value)))
'''
if old_interval not in text:
    raise SystemExit("active sleep-stage interval marker not found")
text = text.replace(old_interval, new_interval, 1)

path.write_text(text, encoding="utf-8")
