"""Platform-neutral AI-first health-data query planner.

This module contains the reusable planner contract shared by VitalChronicle desktop
and Android. It deliberately has no Qt, Android, Ollama, ML Kit or UI dependency.
The language model only receives metadata; Python validates its JSON plan and then
reads only the selected local datasets and interval.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from .ai_insights import build_ai_ready_snapshot
from .constants import DATA_TYPE_BY_KEY

PLANNER_VERSION = "ai-data-planner-v1"
MAX_SELECTED_DATA_TYPES = 8
MAX_LOOKBACK_DAYS = 3650
PLANNER_OUTPUT_TOKENS = 560

PLANNER_SYSTEM_PROMPT = """You are VitalChronicle's local data-request planner.
You do not analyse health values and you do not answer the user's health question.
You receive only metadata describing which health datasets exist locally. Choose the
smallest sufficient set of data and time range that Python should prepare for a second,
separate analysis call.

Return exactly one JSON object and no prose or markdown:
{
  "data_types": ["catalog-key", "catalog-key"],
  "window": "last_n_days" | "all_history" | "date_range",
  "days": 30,
  "start_date": null,
  "end_date": null,
  "detail": "daily" | "intraday" | "events" | "summary",
  "reason": "short explanation"
}

Rules:
- data_types may contain only exact keys present in the catalogue.
- Select at most 8 data types and prefer fewer when they are sufficient.
- Choose the time span from the scientific meaning of the question, not from a UI preset.
- For a current/single-day question, usually request a few recent days for context.
- For a short trend, usually request weeks; for correlations or personal baselines,
  usually request enough matched days (often 60-90 when available).
- Long-term change questions may justify months or all available history.
- Include supporting data only when it can materially help answer the question.
- Use date_range only when the question explicitly identifies a calendar period.
- end_date is inclusive. For relative windows, anchor to the latest locally available
  date in the selected datasets.
- Missing catalogue coverage is not zero and must not be invented.
- Conversation excerpts are context only and never health evidence.
"""


def build_data_catalog(store) -> dict[str, Any]:
    """Return metadata-only coverage for datasets that actually exist locally."""

    with store._connect() as db:  # noqa: SLF001 - the store owns the local DB boundary.
        rows = db.execute(
            """
            SELECT data_type,
                   COUNT(*) AS record_count,
                   MIN(substr(COALESCE(start_time, end_time), 1, 10)) AS first_date,
                   MAX(substr(COALESCE(start_time, end_time), 1, 10)) AS last_date
            FROM records
            WHERE COALESCE(start_time, end_time) IS NOT NULL
            GROUP BY data_type
            ORDER BY data_type
            """
        ).fetchall()

    datasets: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["data_type"])
        first_date = str(row["first_date"] or "")
        last_date = str(row["last_date"] or "")
        if not first_date or not last_date:
            continue
        spec = DATA_TYPE_BY_KEY.get(key)
        datasets.append(
            {
                "key": key,
                "label": str(spec.label if spec else key.replace("-", " ").title()),
                "category": str(spec.category if spec else "Other"),
                "first_date": first_date,
                "last_date": last_date,
                "record_count": int(row["record_count"] or 0),
            }
        )

    first = min((item["first_date"] for item in datasets), default=None)
    last = max((item["last_date"] for item in datasets), default=None)
    return {
        "catalog_version": PLANNER_VERSION,
        "local_date": datetime.now().astimezone().date().isoformat(),
        "archive_first_date": first,
        "archive_last_date": last,
        "datasets": datasets,
    }


def _history_excerpt(history: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in history[-6:]:
        role = str(item.get("role") or "")
        content = " ".join(str(item.get("content") or "").split())
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content[:600]})
    return result


def _planner_messages(
    catalog: dict[str, Any], question: str, history: list[dict[str, str]]
) -> list[dict[str, str]]:
    payload = {
        "question": question,
        "conversation_context": _history_excerpt(history),
        "available_local_data": catalog,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner did not return a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("planner response is not an object")
    return parsed


def _catalog_rows(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("key")): item
        for item in catalog.get("datasets", [])
        if isinstance(item, dict) and item.get("key")
    }


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _fallback_types(catalog: dict[str, Any]) -> list[str]:
    available = _catalog_rows(catalog)
    preferred = (
        "sleep",
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
        "heart-rate",
        "steps",
        "exercise",
        "daily-oxygen-saturation",
        "weight",
    )
    selected = [key for key in preferred if key in available]
    if selected:
        return selected[:MAX_SELECTED_DATA_TYPES]
    return list(available)[:MAX_SELECTED_DATA_TYPES]


def fallback_data_plan(
    catalog: dict[str, Any], *, all_history: bool = False, reason: str = "planner_fallback"
) -> dict[str, Any]:
    """Return a broad but bounded plan when model planning is unavailable or invalid."""

    return resolve_data_plan(
        {
            "data_types": list(_catalog_rows(catalog)) if all_history else _fallback_types(catalog),
            "window": "all_history" if all_history else "last_n_days",
            "days": 90,
            "detail": "summary",
            "reason": reason,
        },
        catalog,
        force_all=all_history,
    )


def resolve_data_plan(
    raw_plan: dict[str, Any],
    catalog: dict[str, Any],
    *,
    force_all: bool = False,
) -> dict[str, Any]:
    """Validate model output and resolve it to an exact, bounded local query."""

    available = _catalog_rows(catalog)
    if not available:
        raise ValueError("no local health data are available")

    requested = raw_plan.get("data_types")
    requested_keys = requested if isinstance(requested, list) else []
    selected: list[str] = []
    for value in requested_keys:
        key = str(value)
        if key in available and key not in selected:
            selected.append(key)
        if len(selected) >= MAX_SELECTED_DATA_TYPES:
            break

    if force_all:
        selected = list(available)
    elif not selected:
        selected = _fallback_types(catalog)
    if not selected:
        raise ValueError("the planner did not select any available data")

    selected_rows = [available[key] for key in selected]
    first_available = min(
        day for row in selected_rows if (day := _parse_day(row.get("first_date"))) is not None
    )
    last_available = max(
        day for row in selected_rows if (day := _parse_day(row.get("last_date"))) is not None
    )

    window = str(raw_plan.get("window") or "last_n_days").strip().lower()
    if force_all:
        window = "all_history"

    if window == "all_history":
        start_day = first_available
        end_day = last_available
    elif window == "date_range":
        requested_start = _parse_day(raw_plan.get("start_date"))
        requested_end = _parse_day(raw_plan.get("end_date"))
        if requested_start is None or requested_end is None:
            window = "last_n_days"
        else:
            start_day = max(first_available, requested_start)
            end_day = min(last_available, requested_end)
            if start_day > end_day:
                window = "last_n_days"
    if window not in {"all_history", "date_range"}:
        try:
            days = int(raw_plan.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(MAX_LOOKBACK_DAYS, days))
        end_day = last_available
        start_day = max(first_available, end_day - timedelta(days=days - 1))
        window = "last_n_days"

    detail = str(raw_plan.get("detail") or "summary").lower()
    if detail not in {"daily", "intraday", "events", "summary"}:
        detail = "summary"
    actual_days = max(1, (end_day - start_day).days + 1)
    reason = " ".join(str(raw_plan.get("reason") or "").split())[:240]
    selected_labels = [str(available[key].get("label") or key) for key in selected]
    return {
        "planner_version": PLANNER_VERSION,
        "data_types": selected,
        "data_labels": selected_labels,
        "window": window,
        "days": actual_days,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "end_exclusive": (end_day + timedelta(days=1)).isoformat(),
        "detail": detail,
        "reason": reason or "model_selected_local_evidence",
    }


class SelectedHealthStore:
    """Proxy that prevents the deterministic pipeline from reading unrequested types."""

    def __init__(self, store, allowed_data_types: list[str]) -> None:
        self._store = store
        self._allowed = frozenset(allowed_data_types)

    def list_records(self, data_type: str, *args, **kwargs):
        if data_type not in self._allowed:
            return []
        return self._store.list_records(data_type, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def build_planned_snapshot(store, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fulfil a validated AI request using only selected local record types."""

    selected = [str(value) for value in plan.get("data_types") or []]
    if not selected:
        raise ValueError("planned query contains no data types")
    proxy = SelectedHealthStore(store, selected)
    days = max(1, int(plan.get("days") or 1))
    # The planner reduces breadth first; this backend ceiling prevents pathological
    # intraday archives from exhausting RAM without imposing a user-facing time limit.
    if days <= 7:
        record_limit = 350_000
    elif days <= 90:
        record_limit = 250_000
    else:
        record_limit = 150_000
    snapshot = build_ai_ready_snapshot(
        proxy,
        str(plan["start_date"]),
        str(plan["end_exclusive"]),
        record_limit=record_limit,
    )
    snapshot["analysis_scope"] = "ai_planned"
    snapshot["ai_data_request"] = dict(plan)
    start_day = date.fromisoformat(str(plan["start_date"]))
    end_day = date.fromisoformat(str(plan["end_date"]))
    period = {
        "preset": "ai_planned",
        "label": f"AI · {start_day.strftime('%d/%m/%Y')}–{end_day.strftime('%d/%m/%Y')}",
        "start": str(plan["start_date"]),
        "end": str(plan["end_exclusive"]),
        "display_start": start_day.strftime("%d/%m/%Y"),
        "display_end": end_day.strftime("%d/%m/%Y"),
        "selected_data_types": selected,
        "planner_reason": str(plan.get("reason") or ""),
    }
    return snapshot, period
