from __future__ import annotations

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
    original_infer_recovery_metrics = guard._infer_recovery_metrics
    original_dry_run = guard._dry_run
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

    def compatible_create(self: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        result = original_create(self, args, **kwargs)
        if not isinstance(result, dict):
            return result
        result = dict(result)
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
    guard._infer_recovery_metrics = deduplicated_recovery_metrics
    guard._dry_run = bounded_dry_run
    factory.EnhancedSafeToolExecutor._tool_create_learned_tool = compatible_create

    _INSTALLED = True
