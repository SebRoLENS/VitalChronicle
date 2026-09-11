from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any

from . import agent_tools as base

EXTRA_BUILTIN_SPEC = {
    "name": "get_sleep_stage_series",
    "description": (
        "Return per-night recorded sleep-stage DURATIONS in hours (deep, REM, light, awake) "
        "keyed by wake-up date. The 'deep' field is deep-sleep duration only; 'total_sleep' is "
        "deep+REM+light and must never be called deep sleep. Missing stages are never inferred."
    ),
    "capability": "sleep.stage_series",
    "parameters": base._period(),
}

SAFE_COMPOSABLE_TOOLS = {
    spec.name for spec in base.BUILTIN_TOOL_SPECS if not spec.capability.startswith("agent.")
}
SAFE_COMPOSABLE_TOOLS.add(EXTRA_BUILTIN_SPEC["name"])

NEW_DSL_OPS = {
    "call_tool",
    "extract_series",
    "baseline",
    "filter_relative",
    "shift_days",
    "event_response",
}
ALLOWED_DSL_OPS = set(base.ALLOWED_DSL_OPS) | NEW_DSL_OPS

DSL_REFERENCE = {
    "call_tool": "Call one allow-listed deterministic built-in tool; no agent/meta tools.",
    "extract_series": (
        "Convert a list of rows inside a tool result into a {date: numeric_value} series."
    ),
    "baseline": "Calculate mean/median/MAD from a date series.",
    "filter_relative": ("Keep dates above/below a baseline by a percentage, e.g. > baseline +30%."),
    "shift_days": "Shift date keys by a bounded integer number of calendar days.",
    "event_response": (
        "Measure an offset response after selected events and time-to-recovery to a personal "
        "baseline."
    ),
    "load_series": "Load one raw VitalChronicle metric series.",
    "daily": "Aggregate a loaded raw series by local calendar day.",
    "window": "Keep the last N days of a date series.",
    "summarize": "Calculate a robust summary/baseline of a date series.",
    "trend": "Calculate first-to-last percentage trend.",
    "compare": "Compare means of two stored date series.",
    "correlate": "Calculate same-day Pearson correlation between two stored date series.",
    "count_above": "Count values above an absolute threshold.",
    "count_below": "Count values below an absolute threshold.",
    "ratio": "Divide two numeric stored values.",
    "return": "Return the selected stored result.",
}

EVENT_RESPONSE_PARAMETERS_EXAMPLE = {
    "type": "object",
    "properties": {
        "start": {"type": "string"},
        "end": {"type": "string"},
        "event_percent": {"type": "number", "default": 30},
        "response_percent": {"type": "number", "default": 20},
        "recovery_tolerance_percent": {"type": "number", "default": 10},
    },
    "required": ["start", "end"],
}

EVENT_RESPONSE_PIPELINE_EXAMPLE = [
    {
        "op": "call_tool",
        "tool": "calculate_cardio_load",
        "arguments": {"start": "$start", "end": "$end"},
        "as": "event_raw",
    },
    {
        "op": "extract_series",
        "source": "event_raw",
        "path": "daily_load",
        "key_field": "date",
        "value_field": "load",
        "as": "event_series",
    },
    {"op": "baseline", "source": "event_series", "as": "event_baseline"},
    {
        "op": "filter_relative",
        "source": "event_series",
        "baseline_source": "event_baseline",
        "baseline_field": "median",
        "direction": "above",
        "percent": "$event_percent",
        "as": "events",
    },
    {
        "op": "call_tool",
        "tool": "get_sleep_stage_series",
        "arguments": {"start": "$start", "end": "$end"},
        "as": "response_raw",
    },
    {
        "op": "extract_series",
        "source": "response_raw",
        "path": "daily_stages",
        "key_field": "date",
        "value_field": "deep",
        "as": "response_series",
    },
    {"op": "baseline", "source": "response_series", "as": "response_baseline"},
    {
        "op": "event_response",
        "event_source": "events",
        "response_source": "response_series",
        "response_baseline_source": "response_baseline",
        "baseline_field": "median",
        "response_direction": "below",
        "response_percent": "$response_percent",
        "response_offset_days": 1,
        "recovery_tolerance_percent": "$recovery_tolerance_percent",
        "max_recovery_days": 14,
        "as": "analysis",
    },
    {"op": "return", "source": "analysis"},
]

_PIPELINE_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "op": {
            "type": "string",
            "enum": sorted(ALLOWED_DSL_OPS),
            "description": "Safe declarative operation. Only values in this enum are accepted.",
        },
        "as": {"type": "string", "description": "Optional name for this step result."},
        "metric": {"type": "string", "description": "Metric for load_series."},
        "source": {"type": "string", "description": "Alias of an earlier step result."},
        "days": {"type": "integer", "minimum": -30, "maximum": 365},
        "aggregation": {"type": "string", "enum": ["mean", "sum"]},
        "left": {"type": "string"},
        "right": {"type": "string"},
        "threshold": {},
        "numerator": {"type": "string"},
        "denominator": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "string"}},
        "tool": {
            "type": "string",
            "enum": sorted(SAFE_COMPOSABLE_TOOLS),
            "description": "Built-in deterministic tool called by call_tool.",
        },
        "arguments": {
            "type": "object",
            "description": (
                "Arguments for call_tool. Values beginning with $ reference learned-tool inputs, "
                "for example $start or $end."
            ),
        },
        "path": {
            "type": "string",
            "description": "Dot-separated path to the row list for extract_series.",
        },
        "key_field": {"type": "string", "description": "Date field in extract_series rows."},
        "value_field": {
            "type": "string",
            "description": (
                "Numeric field in extract_series rows. For get_sleep_stage_series use 'deep' "
                "only for deep-sleep duration; use 'total_sleep' for total sleep."
            ),
        },
        "baseline_source": {
            "type": "string",
            "description": "Alias containing a baseline summary.",
        },
        "baseline_field": {
            "type": "string",
            "enum": ["median", "mean"],
            "description": "Baseline statistic to use; median is preferred for personal baselines.",
        },
        "direction": {"type": "string", "enum": ["above", "below"]},
        "percent": {
            "description": "Percentage relative to baseline; may also be a $parameter reference."
        },
        "event_source": {"type": "string"},
        "response_source": {"type": "string"},
        "response_baseline_source": {"type": "string"},
        "response_direction": {"type": "string", "enum": ["above", "below"]},
        "response_percent": {},
        "response_offset_days": {"type": "integer", "minimum": -7, "maximum": 7},
        "recovery_tolerance_percent": {},
        "max_recovery_days": {"type": "integer", "minimum": 1, "maximum": 60},
        "episode_mode": {
            "type": "string",
            "enum": ["contiguous"],
            "description": "Group consecutive trigger dates into one episode and anchor recovery to its last date.",
        },
    },
    "required": ["op"],
}

CREATE_LEARNED_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "description": "Reusable snake_case tool name."},
        "description": {"type": "string"},
        "capability": {
            "type": "string",
            "description": "Stable semantic capability, not the wording of one user question.",
        },
        "parameters": {
            "type": "object",
            "description": (
                "JSON Schema for reusable user-adjustable inputs. Property defaults are applied at "
                "learned-tool runtime when the caller omits that argument. For event-response tools, "
                "use the supplied example rather than inventing a parameter shape."
            ),
            "examples": [EVENT_RESPONSE_PARAMETERS_EXAMPLE],
        },
        "pipeline": {
            "type": "array",
            "minItems": 1,
            "maxItems": base.MAX_LEARNED_STEPS,
            "items": _PIPELINE_STEP_SCHEMA,
            "description": (
                "Safe pipeline. Store each intermediate result with 'as' and reference it by name. "
                "Never invent operations outside the op enum. For an event above a personal baseline "
                "followed by a next-day/night response and recovery-time analysis, copy the supplied "
                "canonical example structure and change only the semantic tools/fields/thresholds needed."
            ),
            "examples": [EVENT_RESPONSE_PIPELINE_EXAMPLE],
        },
    },
    "required": ["name", "description", "capability", "pipeline"],
}


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in (piece for piece in str(path or "").split(".") if piece):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _date_series(value: Any) -> dict[str, float]:
    if isinstance(value, dict) and isinstance(value.get("series"), dict):
        value = value["series"]
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            date.fromisoformat(str(key)[:10])
            number = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[str(key)[:10]] = number
    return result


def _baseline_value(value: Any, field: str = "median") -> float | None:
    if isinstance(value, dict) and isinstance(value.get("baseline"), dict):
        value = value["baseline"]
    if not isinstance(value, dict):
        return None
    candidate = value.get(field) if field in {"mean", "median"} else value.get("median")
    try:
        number = float(candidate)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class EnhancedSafeToolExecutor(base.SafeToolExecutor):
    """Safe Tool Factory v2 with composable deterministic primitives.

    Learned tools remain declarative and read-only. The additional operations only compose
    allow-listed deterministic tools and bounded transformations over their returned values.
    """

    def __init__(self, health_store, agent_store: base.AgentStore) -> None:
        super().__init__(health_store, agent_store)
        self.agent_store.sync_builtin_tools([EXTRA_BUILTIN_SPEC])

    def tool_schemas(self) -> list[dict[str, Any]]:
        schemas = super().tool_schemas()
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict):
                continue
            if function.get("name") == "create_learned_tool":
                function["description"] = (
                    "Create a reusable learned tool from the documented safe DSL. Do this "
                    "automatically when an exact reusable capability is missing; search the registry "
                    "first. Never invent an op outside the provided enum."
                )
                function["parameters"] = CREATE_LEARNED_TOOL_SCHEMA
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": EXTRA_BUILTIN_SPEC["name"],
                    "description": EXTRA_BUILTIN_SPEC["description"],
                    "parameters": EXTRA_BUILTIN_SPEC["parameters"],
                },
            }
        )
        return schemas

    def _tool_get_sleep_stage_series(self, args, **_):
        left, right = base._bounds(args.get("start"), args.get("end"), 60)
        records = self._semantic_records("sleep", left, right)
        by_day: dict[str, dict[str, float]] = {}
        sessions_with_stages = 0
        for record in records:
            stage_points = base.sleep_stage_points([record])
            if not stage_points:
                continue
            values = stage_points[0][1]
            if not isinstance(values, dict):
                continue
            day = base._semantic_day(record, "sleep")
            if day is None:
                continue
            sessions_with_stages += 1
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
        rows = []
        for day, values in sorted(by_day.items()):
            rounded = {key: round(value, 4) for key, value in values.items()}
            rounded["total_sleep"] = round(
                rounded["deep"] + rounded["rem"] + rounded["light"], 4
            )
            rows.append({"date": day, **rounded})
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
            "semantics": {
                "deep": "deep_sleep_duration_hours",
                "rem": "rem_sleep_duration_hours",
                "light": "light_sleep_duration_hours",
                "awake": "awake_duration_hours",
                "total_sleep": "deep_plus_rem_plus_light_duration_hours",
            },
            "method": (
                "Recorded wearable sleep-stage durations grouped by local wake-up date. "
                "total_sleep is a derived sum; deep remains deep-stage duration only."
            ),
            "limitations": (
                "Missing sleep stages are omitted and never inferred or zero-filled. A nonzero "
                "sleep_session_records value with zero sessions_with_stages means sessions exist "
                "but no recognizable stage detail was recorded."
            ),
        }

    @classmethod
    def validate_pipeline(cls, pipeline: Any) -> list[dict[str, Any]]:
        if not isinstance(pipeline, list) or not pipeline or len(pipeline) > base.MAX_LEARNED_STEPS:
            raise ValueError(f"Pipeline must contain 1..{base.MAX_LEARNED_STEPS} steps")
        allowed_fields = set(_PIPELINE_STEP_SCHEMA["properties"])
        aliases: set[str] = set()
        result: list[dict[str, Any]] = []
        for index, raw_step in enumerate(pipeline):
            step_number = index + 1
            if not isinstance(raw_step, dict):
                raise TypeError(f"Pipeline step {step_number} must be an object")
            op = str(raw_step.get("op") or "")
            if op not in ALLOWED_DSL_OPS:
                allowed = ", ".join(sorted(ALLOWED_DSL_OPS))
                raise ValueError(
                    f"Unsupported learned-tool operation '{op or '<missing>'}' at step "
                    f"{step_number}. Allowed operations: {allowed}. Repair the pipeline using "
                    "only these operations."
                )
            step = {str(key): value for key, value in raw_step.items() if key in allowed_fields}
            alias = str(step.get("as") or f"step_{step_number}")
            if not alias.replace("_", "").isalnum():
                raise ValueError(f"Invalid result alias '{alias}' at step {step_number}")

            def require_alias(
                field: str,
                current_step: dict[str, Any] = step,
                current_number: int = step_number,
            ) -> None:
                ref = str(current_step.get(field) or "")
                if ref and ref not in aliases:
                    raise ValueError(
                        f"Step {current_number} references unknown earlier result '{ref}' in {field}."
                    )

            if op == "load_series" and not step.get("metric"):
                raise ValueError(f"load_series requires metric at step {step_number}")
            if op == "call_tool":
                tool = str(step.get("tool") or "")
                if tool not in SAFE_COMPOSABLE_TOOLS:
                    raise ValueError(
                        f"call_tool at step {step_number} may only call deterministic built-ins. "
                        f"Unsupported tool '{tool}'."
                    )
                if step.get("arguments") is not None and not isinstance(
                    step.get("arguments"), dict
                ):
                    raise ValueError(f"call_tool arguments must be an object at step {step_number}")
            if op in {
                "daily",
                "window",
                "summarize",
                "trend",
                "count_above",
                "count_below",
                "extract_series",
                "baseline",
                "filter_relative",
                "shift_days",
                "return",
            }:
                require_alias("source")
            if op in {"compare", "correlate"}:
                require_alias("left")
                require_alias("right")
            if op == "ratio":
                require_alias("numerator")
                require_alias("denominator")
            if op == "filter_relative":
                require_alias("baseline_source")
                if str(step.get("direction") or "") not in {"above", "below"}:
                    raise ValueError(
                        f"filter_relative direction must be above or below at step {step_number}"
                    )
            if op == "shift_days":
                try:
                    shift = int(step.get("days") or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"shift_days requires an integer days value at step {step_number}"
                    ) from exc
                if not -30 <= shift <= 30:
                    raise ValueError(f"shift_days is limited to -30..30 days at step {step_number}")
            if op == "event_response":
                for field in ("event_source", "response_source", "response_baseline_source"):
                    require_alias(field)
                if str(step.get("response_direction") or "") not in {"above", "below"}:
                    raise ValueError(
                        f"event_response response_direction must be above or below at step {step_number}"
                    )
                try:
                    max_days = int(step.get("max_recovery_days") or 14)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"event_response max_recovery_days must be an integer at step {step_number}"
                    ) from exc
                if not 1 <= max_days <= 60:
                    raise ValueError(
                        f"event_response max_recovery_days is limited to 1..60 at step {step_number}"
                    )
            result.append(step)
            aliases.add(alias)
        if result[-1]["op"] != "return":
            result.append(
                {"op": "return", "source": str(result[-1].get("as") or f"step_{len(result)}")}
            )
        return result

    def _tool_create_learned_tool(self, args, **_):
        name = str(args.get("name") or "")
        if not base.re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name):
            return {
                "status": "invalid_spec",
                "repairable": True,
                "error": "Learned tool name must be snake_case (3..64 characters).",
            }
        try:
            pipeline = self.validate_pipeline(args.get("pipeline"))
        except ValueError as exc:
            return {
                "status": "invalid_pipeline",
                "repairable": True,
                "error": str(exc),
                "allowed_operations": sorted(ALLOWED_DSL_OPS),
                "dsl_reference": DSL_REFERENCE,
                "canonical_event_response_pipeline": EVENT_RESPONSE_PIPELINE_EXAMPLE,
                "canonical_event_response_parameters": EVENT_RESPONSE_PARAMETERS_EXAMPLE,
                "instruction": (
                    "Repair this same reusable tool using only the documented operations; do not "
                    "replace the user's requested metric with a proxy just because validation failed."
                ),
            }
        dependencies: list[str] = []
        for step in pipeline:
            if step.get("metric"):
                dependencies.append(str(step["metric"]))
            if step.get("tool"):
                dependencies.append(f"tool:{step['tool']}")
        spec = {
            "name": name,
            "description": str(args.get("description") or ""),
            "capability": str(args.get("capability") or name),
            "parameters": (
                args.get("parameters")
                if isinstance(args.get("parameters"), dict)
                else base._period()
            ),
            "pipeline": pipeline,
            "dependencies": dependencies,
            "confidence": 0.7,
        }
        result = self.agent_store.add_learned_tool(spec)
        return {
            "status": result["status"],
            "tool": result.get("tool"),
            "validation": "safe composable declarative operations only",
            "dsl_version": 2,
        }

    @staticmethod
    def _runtime_value(value: Any, args: dict[str, Any], env: dict[str, Any]) -> Any:
        if isinstance(value, str):
            if value.startswith("$"):
                return args.get(value[1:])
            if value.startswith("@"):
                alias, _, path = value[1:].partition(".")
                return _path_value(env.get(alias), path)
            return value
        if isinstance(value, dict):
            return {
                str(key): EnhancedSafeToolExecutor._runtime_value(item, args, env)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [EnhancedSafeToolExecutor._runtime_value(item, args, env) for item in value]
        return value

    @staticmethod
    def _percent(value: Any, args: dict[str, Any], env: dict[str, Any], default: float) -> float:
        resolved = EnhancedSafeToolExecutor._runtime_value(value, args, env)
        try:
            number = float(default if resolved is None else resolved)
        except (TypeError, ValueError):
            number = default
        return max(0.0, min(500.0, number))

    def _run_learned(self, item: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        pipeline = self.validate_pipeline(item["pipeline"])
        runtime_args = dict(args)
        parameter_schema = (
            item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        )
        properties = (
            parameter_schema.get("properties")
            if isinstance(parameter_schema.get("properties"), dict)
            else {}
        )
        for key, spec in properties.items():
            if key not in runtime_args and isinstance(spec, dict) and "default" in spec:
                runtime_args[str(key)] = spec["default"]
        args = runtime_args
        env: dict[str, Any] = {}
        last: Any = None
        default_left, default_right = base._bounds(args.get("start"), args.get("end"), 60)
        for index, step in enumerate(pipeline):
            op = step["op"]
            alias = str(step.get("as") or f"step_{index + 1}")
            source = env.get(str(step.get("source") or ""), last)
            if op == "load_series":
                left, right = base._bounds(
                    self._runtime_value(step.get("left"), args, env) or default_left,
                    self._runtime_value(step.get("right"), args, env) or default_right,
                )
                last = self._series(
                    str(self._runtime_value(step.get("metric"), args, env) or ""), left, right
                )
            elif op == "call_tool":
                tool = str(step.get("tool") or "")
                call_args = self._runtime_value(step.get("arguments") or {}, args, env)
                last = self.execute(tool, call_args if isinstance(call_args, dict) else {})
            elif op == "extract_series":
                rows = _path_value(source, str(step.get("path") or ""))
                if rows is None:
                    rows = source
                key_field = str(step.get("key_field") or "date")
                value_field = str(step.get("value_field") or "value")
                extracted: dict[str, float] = {}
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        key = str(row.get(key_field) or "")[:10]
                        try:
                            date.fromisoformat(key)
                            value = float(row.get(value_field))
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(value):
                            extracted[key] = value
                elif isinstance(rows, dict):
                    extracted = _date_series(rows)
                last = extracted
            elif op == "daily":
                last = (
                    base._daily(
                        (source or {}).get("points", []),
                        str(step.get("aggregation") or "mean"),
                    )
                    if isinstance(source, dict)
                    else {}
                )
            elif op == "window":
                cutoff = default_right - timedelta(days=max(1, int(step.get("days") or 7)) - 1)
                last = {
                    day: value
                    for day, value in _date_series(source).items()
                    if date.fromisoformat(day) >= cutoff
                }
            elif op in {"summarize", "baseline"}:
                last = base._baseline(list(_date_series(source).values()))
            elif op == "filter_relative":
                series = _date_series(source)
                baseline = _baseline_value(
                    env.get(str(step.get("baseline_source") or "")),
                    str(step.get("baseline_field") or "median"),
                )
                percent = self._percent(step.get("percent"), args, env, 0.0)
                direction = str(step.get("direction") or "above")
                if baseline is None:
                    last = {"series": {}, "count": 0, "total": len(series), "baseline": None}
                else:
                    threshold = (
                        baseline * (1 + percent / 100)
                        if direction == "above"
                        else baseline * (1 - percent / 100)
                    )
                    selected = {
                        day: value
                        for day, value in series.items()
                        if (value > threshold if direction == "above" else value < threshold)
                    }
                    last = {
                        "series": selected,
                        "count": len(selected),
                        "total": len(series),
                        "baseline": baseline,
                        "threshold": threshold,
                        "direction": direction,
                        "percent": percent,
                    }
            elif op == "shift_days":
                shift = int(step.get("days") or 0)
                last = {
                    (date.fromisoformat(day) + timedelta(days=shift)).isoformat(): value
                    for day, value in _date_series(source).items()
                }
            elif op == "trend":
                values = list(_date_series(source).values())
                last = {
                    "trend_percent": (
                        None
                        if len(values) < 2 or abs(values[0]) < 1e-12
                        else (values[-1] - values[0]) / abs(values[0]) * 100
                    )
                }
            elif op == "compare":
                a = _date_series(env.get(str(step.get("left") or "")))
                b = _date_series(env.get(str(step.get("right") or "")))
                av, bv = list(a.values()), list(b.values())
                ma = statistics.fmean(av) if av else None
                mb = statistics.fmean(bv) if bv else None
                last = {
                    "mean_a": ma,
                    "mean_b": mb,
                    "delta": None if ma is None or mb is None else mb - ma,
                }
            elif op == "correlate":
                a = _date_series(env.get(str(step.get("left") or "")))
                b = _date_series(env.get(str(step.get("right") or "")))
                common = sorted(set(a) & set(b))
                pairs = [(a[day], b[day]) for day in common]
                r = None
                if len(pairs) >= 4:
                    xs = [x for x, _ in pairs]
                    ys = [y for _, y in pairs]
                    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
                    if sx and sy:
                        mx, my = statistics.fmean(xs), statistics.fmean(ys)
                        r = sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) * sx * sy)
                last = {"paired_days": len(pairs), "pearson_r": r}
            elif op in {"count_above", "count_below"}:
                threshold = float(self._runtime_value(step.get("threshold"), args, env) or 0)
                values = list(_date_series(source).values())
                last = {
                    "count": sum(
                        value > threshold if op == "count_above" else value < threshold
                        for value in values
                    ),
                    "total": len(values),
                }
            elif op == "ratio":
                numerator = env.get(str(step.get("numerator") or ""))
                denominator = env.get(str(step.get("denominator") or ""))
                last = (
                    None
                    if not isinstance(numerator, (int, float))
                    or not isinstance(denominator, (int, float))
                    or abs(denominator) < 1e-12
                    else numerator / denominator
                )
            elif op == "event_response":
                event_series = _date_series(env.get(str(step.get("event_source") or "")))
                response_series = _date_series(env.get(str(step.get("response_source") or "")))
                baseline = _baseline_value(
                    env.get(str(step.get("response_baseline_source") or "")),
                    str(step.get("baseline_field") or "median"),
                )
                direction = str(step.get("response_direction") or "below")
                response_percent = self._percent(step.get("response_percent"), args, env, 20.0)
                tolerance = self._percent(step.get("recovery_tolerance_percent"), args, env, 10.0)
                offset = int(step.get("response_offset_days") or 1)
                max_days = int(step.get("max_recovery_days") or 14)
                evaluated = 0
                matches: list[dict[str, Any]] = []
                if baseline is not None:
                    threshold = (
                        baseline * (1 + response_percent / 100)
                        if direction == "above"
                        else baseline * (1 - response_percent / 100)
                    )
                    trigger_days = sorted(event_series)
                    episodes: list[list[str]] = []
                    for event_day in trigger_days:
                        if (
                            episodes
                            and date.fromisoformat(event_day)
                            == date.fromisoformat(episodes[-1][-1]) + timedelta(days=1)
                        ):
                            episodes[-1].append(event_day)
                        else:
                            episodes.append([event_day])
                    episode_matches: list[dict[str, Any]] = []
                    for episode in episodes:
                        episode_start = episode[0]
                        episode_end = episode[-1]
                        response_day = date.fromisoformat(episode_end) + timedelta(days=offset)
                        response_key = response_day.isoformat()
                        if response_key not in response_series:
                            continue
                        evaluated += 1
                        response_value = response_series[response_key]
                        is_match = (
                            response_value > threshold
                            if direction == "above"
                            else response_value < threshold
                        )
                        if not is_match:
                            continue
                        recovery_days_after_response = None
                        recovery_date = None
                        for delta_days in range(1, max_days + 1):
                            candidate = (response_day + timedelta(days=delta_days)).isoformat()
                            if candidate not in response_series:
                                continue
                            value = response_series[candidate]
                            recovered = (
                                value <= baseline * (1 + tolerance / 100)
                                if direction == "above"
                                else value >= baseline * (1 - tolerance / 100)
                            )
                            if recovered:
                                recovery_days_after_response = delta_days
                                recovery_date = candidate
                                break
                        recovery_days_after_episode = (
                            (date.fromisoformat(recovery_date) - date.fromisoformat(episode_end)).days
                            if recovery_date
                            else None
                        )
                        episode_matches.append(
                            {
                                "event_date": episode_end,
                                "event_episode_start": episode_start,
                                "event_episode_end": episode_end,
                                "event_dates": episode,
                                "response_date": response_key,
                                "response_value": response_value,
                                "recovery_date": recovery_date,
                                "recovery_days": recovery_days_after_response,
                                "recovery_days_after_response": recovery_days_after_response,
                                "recovery_days_after_episode": recovery_days_after_episode,
                            }
                        )
                    recovered_after_response = [
                        float(row["recovery_days_after_response"])
                        for row in episode_matches
                        if row.get("recovery_days_after_response") is not None
                    ]
                    recovered_after_episode = [
                        float(row["recovery_days_after_episode"])
                        for row in episode_matches
                        if row.get("recovery_days_after_episode") is not None
                    ]
                    sample_quality = (
                        "insufficient_for_typical_estimate"
                        if len(episodes) < 3
                        else "preliminary"
                        if len(episodes) < 5
                        else "adequate"
                    )
                    last = {
                        "trigger_events": len(event_series),
                        "trigger_episodes": len(episodes),
                        "evaluable_events": evaluated,
                        "evaluable_episodes": evaluated,
                        "response_matches": len(episode_matches),
                        "response_rate_percent": (
                            round(len(episode_matches) / evaluated * 100, 2) if evaluated else None
                        ),
                        "mean_recovery_days": (
                            round(statistics.fmean(recovered_after_response), 2)
                            if recovered_after_response
                            else None
                        ),
                        "median_recovery_days": (
                            round(statistics.median(recovered_after_response), 2)
                            if recovered_after_response
                            else None
                        ),
                        "mean_recovery_days_after_episode": (
                            round(statistics.fmean(recovered_after_episode), 2)
                            if recovered_after_episode
                            else None
                        ),
                        "median_recovery_days_after_episode": (
                            round(statistics.median(recovered_after_episode), 2)
                            if recovered_after_episode
                            else None
                        ),
                        "recovered_events": len(recovered_after_episode),
                        "unrecovered_events": len(episode_matches) - len(recovered_after_episode),
                        "sample_quality": sample_quality,
                        "can_estimate_typical_recovery": len(episodes) >= 3,
                        "generalization_note": (
                            "Only one or two trigger episodes are available; recovery is preliminary "
                            "and must not be described as the user's usual or typical response."
                            if len(episodes) < 3
                            else "Estimate is based on multiple trigger episodes; still observational, not causal."
                        ),
                        "response_baseline": baseline,
                        "response_threshold": threshold,
                        "response_direction": direction,
                        "response_percent": response_percent,
                        "response_offset_days": offset,
                        "recovery_tolerance_percent": tolerance,
                        "max_recovery_days": max_days,
                        "matched_events": episode_matches[:120],
                        "method": (
                            "Event-conditioned deterministic analysis with consecutive trigger dates "
                            "grouped into episodes. Recovery is reported both from the response night "
                            "and, explicitly, from the episode's last trigger date. Missing dates are "
                            "skipped, never treated as zero. Recovery means returning within the "
                            "configured percentage of the personal baseline."
                        ),
                    }
                else:
                    last = {
                        "trigger_events": len(event_series),
                        "trigger_episodes": 0,
                        "evaluable_events": 0,
                        "evaluable_episodes": 0,
                        "response_matches": 0,
                        "response_rate_percent": None,
                        "mean_recovery_days": None,
                        "mean_recovery_days_after_episode": None,
                        "sample_quality": "insufficient_for_typical_estimate",
                        "can_estimate_typical_recovery": False,
                        "error": "Response baseline is unavailable.",
                    }
            elif op == "return":
                last = source
            env[alias] = last
        return {
            "tool": item["name"],
            "result": last,
            "pipeline_steps": len(pipeline),
            "confidence": item["confidence"],
            "dsl_version": 2,
            "method": (
                "Safe learned declarative tool; deterministic built-ins and bounded transforms only. "
                "No arbitrary code, terminal, filesystem, browser, network, or health-data writes."
            ),
        }
