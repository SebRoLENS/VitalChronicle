from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from datetime import date, datetime, time
from typing import Any

from .i18n import _

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def flatten_dict(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten dictionaries while retaining lists as compact JSON."""
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                result.update(flatten_dict(child, path))
            elif isinstance(child, list):
                result[path] = json.dumps(child, ensure_ascii=False, separators=(",", ":"))
            else:
                result[path] = child
    else:
        result[prefix or "value"] = value
    return result


def _walk(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _civil_to_iso(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    date_part = value.get("date", value)
    if not isinstance(date_part, dict):
        return None
    try:
        day = date(int(date_part["year"]), int(date_part["month"]), int(date_part["day"]))
    except (KeyError, TypeError, ValueError):
        return None
    time_part = value.get("time") or {}
    try:
        clock = time(
            int(time_part.get("hours", 0)),
            int(time_part.get("minutes", 0)),
            int(time_part.get("seconds", 0)),
            int(time_part.get("nanos", 0)) // 1000,
        )
    except (TypeError, ValueError):
        clock = time()
    return datetime.combine(day, clock).isoformat()


def extract_times(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    starts: list[str] = []
    ends: list[str] = []
    samples: list[str] = []
    daily: list[str] = []
    for path, value in _walk(payload):
        key = path.rsplit(".", 1)[-1].lower()
        if isinstance(value, str):
            if key in {"starttime", "physicaltime"}:
                (starts if key == "starttime" else samples).append(value)
            elif key == "endtime":
                ends.append(value)
        elif isinstance(value, dict):
            civil = _civil_to_iso(value)
            if civil:
                if key in {"civilstarttime", "sampletime"}:
                    starts.append(civil)
                elif key == "civilendtime":
                    ends.append(civil)
                elif key == "date" or path.count(".") <= 2:
                    daily.append(civil)
    start = starts[0] if starts else (samples[0] if samples else (daily[0] if daily else None))
    end = ends[0] if ends else start
    return start, end


def extract_source(payload: dict[str, Any]) -> str:
    source = payload.get("dataSource") or {}
    if not isinstance(source, dict):
        return ""
    device = source.get("device") or {}
    parts = []
    if isinstance(device, dict):
        parts.append(device.get("displayName") or device.get("manufacturer") or "")
    parts.append(source.get("platform") or "")
    parts.append(source.get("recordingMethod") or "")
    return " · ".join(str(part) for part in parts if part)


def summarize(payload: dict[str, Any], max_fields: int = 4) -> str:
    flat = flatten_dict(payload)
    ignored = (
        "dataSource",
        "name",
        "year",
        "month",
        "day",
        "hours",
        "minutes",
        "seconds",
        "nanos",
        "utcOffset",
    )
    pieces = []
    for key, value in flat.items():
        if any(token in key for token in ignored):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            pieces.append(f"{key.rsplit('.', 1)[-1]}: {value:g}")
        elif isinstance(value, str) and len(value) < 50 and not value.startswith(("{", "[")):
            pieces.append(f"{key.rsplit('.', 1)[-1]}: {value}")
        if len(pieces) >= max_fields:
            break
    return " · ".join(pieces) or _("Record available")


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def coerce_number(value: Any) -> float | None:
    """Convert JSON numbers and protobuf int64 strings to finite floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and _NUMBER.fullmatch(value.strip())
    ):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None
