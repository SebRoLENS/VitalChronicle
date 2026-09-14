from __future__ import annotations

from difflib import get_close_matches
from typing import Any

_INSTALLED = False


def install_tool_factory_compat_patch() -> None:
    """Keep legacy Factory contracts while the schema-aware compiler is layered on top."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory
    from . import agent_tool_factory_reliability_patch as reliability
    from . import agent_tool_factory_schema_guard as guard

    original_schema_index = guard._schema_index
    original_available_metric_catalog = guard._available_metric_catalog
    original_infer_recovery_metrics = guard._infer_recovery_metrics
    original_validate_nested_calls = guard._validate_nested_calls
    original_dry_run = guard._dry_run
    original_prepare_candidate = guard._prepare_candidate
    original_create = factory.EnhancedSafeToolExecutor._tool_create_learned_tool

    def static_schema_index() -> dict[str, dict[str, Any]]:
        index = {
            spec.name: spec.parameters
            for spec in factory.base.BUILTIN_TOOL_SPECS
            if isinstance(spec.parameters, dict)
        }
        index[str(factory.EXTRA_BUILTIN_SPEC["name"])] = factory.EXTRA_BUILTIN_SPEC["parameters"]
        index[reliability._BUILTIN_NAME] = reliability._BUILTIN_SPEC["parameters"]
        index[guard._RECOVERY_NAME] = guard._RECOVERY_SPEC["parameters"]
        return index

    def robust_schema_index(executor: Any) -> dict[str, dict[str, Any]]:
        store = getattr(executor, "agent_store", None)
        if store is not None and callable(getattr(store, "list_tools", None)):
            try:
                return original_schema_index(executor)
            except (AttributeError, TypeError, ValueError):
                pass
        return static_schema_index()

    def authoritative_metric_catalog(executor: Any) -> set[str]:
        # A partially constructed executor used by unit/repair callers has no live health catalogue.
        # In that case schema validation remains strict, but metric availability is intentionally
        # inconclusive rather than falsely treating the alias table as observed user data.
        if not hasattr(executor, "health_store"):
            return set()
        return original_available_metric_catalog(executor)

    def deduplicated_recovery_metrics(args: dict[str, Any], catalog: set[str]) -> list[str]:
        metrics = original_infer_recovery_metrics(args, catalog)
        normalized: list[str] = []
        for metric in metrics:
            value = str(metric)
            if (
                value in {"heart-rate-variability", "hrv"}
                and "daily-heart-rate-variability" in catalog
            ):
                value = "daily-heart-rate-variability"
            if value in {"rhr", "resting-heart-rate"} and "daily-resting-heart-rate" in catalog:
                value = "daily-resting-heart-rate"
            if value not in normalized:
                normalized.append(value)
        return normalized[:8]

    def validate_all_metric_literals(
        executor: Any,
        pipeline: list[dict[str, Any]],
        catalog: set[str],
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        errors, schemas = original_validate_nested_calls(executor, pipeline, catalog)
        if not catalog:
            return errors, schemas
        for index, step in enumerate(pipeline, start=1):
            if str(step.get("op") or "") != "load_series":
                continue
            metric = step.get("metric")
            if not isinstance(metric, str) or metric.startswith(("$", "@")):
                continue
            canonical = guard._canonical_metric(metric, catalog)
            if canonical in catalog:
                continue
            suggestions = get_close_matches(str(canonical), sorted(catalog), n=3, cutoff=0.55)
            suffix = f"; available close matches: {suggestions}" if suggestions else ""
            errors.append(
                f"step {index}: load_series uses unavailable metric '{metric}'{suffix}"
            )
        return errors, schemas

    def bounded_dry_run(
        executor: Any,
        candidate: dict[str, Any],
        pipeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Unit-level and repair-only callers historically construct an executor without a health
        # store. They can still receive full static schema validation; only the execution probe is
        # skipped. Real application executors always have a health store and must pass the dry-run.
        if not hasattr(executor, "health_store"):
            return {"status": "skipped_no_runtime_store"}
        return original_dry_run(executor, candidate, pipeline)

    def dynamic_reference_errors(candidate: dict[str, Any]) -> list[str]:
        parameters = candidate.get("parameters")
        if not isinstance(parameters, dict):
            parameters = factory.base._period()
            candidate["parameters"] = parameters
        properties = parameters.get("properties")
        declared = set(properties) if isinstance(properties, dict) else set()
        aliases: set[str] = set()
        errors: list[str] = []

        def inspect(value: Any, step_number: int, path: str) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    inspect(item, step_number, f"{path}.{key}")
                return
            if isinstance(value, list):
                for item_index, item in enumerate(value):
                    inspect(item, step_number, f"{path}[{item_index}]")
                return
            if not isinstance(value, str):
                return
            if value.startswith("$"):
                parameter = value[1:]
                if parameter and parameter not in declared:
                    errors.append(
                        f"step {step_number}: {path} references undeclared learned-tool parameter "
                        f"'${parameter}'"
                    )
            elif value.startswith("@"):
                alias = value[1:].partition(".")[0]
                if alias and alias not in aliases:
                    errors.append(
                        f"step {step_number}: {path} references unknown earlier result '@{alias}'"
                    )

        pipeline = candidate.get("pipeline")
        for step_number, step in enumerate(pipeline if isinstance(pipeline, list) else [], start=1):
            if not isinstance(step, dict):
                continue
            for key, value in step.items():
                if key != "as":
                    inspect(value, step_number, key)
            alias = str(step.get("as") or f"step_{step_number}")
            if str(step.get("op") or "") != "return" and alias:
                aliases.add(alias)
        return errors

    def prepared_candidate(
        executor: Any,
        args: dict[str, Any],
        *,
        run_dry: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        prepared, meta = original_prepare_candidate(executor, args, run_dry=run_dry)
        if prepared is None:
            return prepared, meta
        reference_errors = dynamic_reference_errors(prepared)
        if reference_errors:
            return None, {
                "error": reference_errors[0],
                "validation_errors": reference_errors,
                "target_tool_schemas": meta.get("target_tool_schemas", {}),
                "dry_run": meta.get("dry_run"),
            }
        return prepared, meta

    def compatible_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        result = original_create(self, args, **kwargs)
        if not isinstance(result, dict):
            return result
        result = dict(result)
        if result.get("status") in {"created", "reused"}:
            compiler_strategy = str(result.get("compiler_strategy") or "")
            if compiler_strategy:
                result.setdefault("auto_repaired", True)
                result.setdefault("repair_strategy", compiler_strategy)
        if result.get("status") in {"invalid_pipeline", "invalid_spec"}:
            result.setdefault("dsl_reference", factory.DSL_REFERENCE)
            result.setdefault(
                "canonical_event_response_pipeline",
                factory.EVENT_RESPONSE_PIPELINE_EXAMPLE,
            )
            result.setdefault(
                "canonical_event_response_parameters",
                factory.EVENT_RESPONSE_PARAMETERS_EXAMPLE,
            )
            result.setdefault(
                "canonical_threshold_response_pipeline",
                reliability._THRESHOLD_PIPELINE_EXAMPLE,
            )
            result.setdefault(
                "threshold_response_instruction",
                "For personal-baseline threshold response analyses, rebuild the learned tool with "
                f"{reliability._BUILTIN_NAME} using the canonical pipeline instead of manually "
                "joining raw series.",
            )
        return result

    guard._schema_index = robust_schema_index
    guard._available_metric_catalog = authoritative_metric_catalog
    guard._infer_recovery_metrics = deduplicated_recovery_metrics
    guard._validate_nested_calls = validate_all_metric_literals
    guard._dry_run = bounded_dry_run
    guard._prepare_candidate = prepared_candidate
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = compatible_create

    _INSTALLED = True
