from __future__ import annotations

from typing import Any

from . import agent_tool_factory as factory
from . import agent_tool_factory_schema_guard as guard
from . import agent_tool_factory_semantic_guard as semantic

_INSTALLED = False


def _pipeline_self_description(args: dict[str, Any]) -> str:
    """Describe only semantics already present when no original user request exists."""

    pipeline = args.get("pipeline")
    features = semantic._pipeline_semantics(pipeline if isinstance(pipeline, list) else [])
    markers: list[str] = []
    if "comparison" in features:
        markers.append("compare")
    if "consecutive_training" in features:
        markers.append("consecutive training")
    if "prior_rest" in features:
        markers.append("after a rest day")
    if "recovery" in features:
        markers.append("recovery")
    if "high_load_filter" in features:
        markers.append("high load")
    return " ".join(markers) or "reusable deterministic analysis"


def _fallback_semantic_context(args: dict[str, Any]) -> str:
    """Use descriptions only when they encode a complete, unambiguous known contract."""

    description = str(args.get("description") or "").strip()
    requirements = semantic._semantic_requirements(description)
    if semantic._is_training_context_comparison(requirements):
        return description
    return _pipeline_self_description(args)


def install_semantic_tool_factory_compat_patch() -> None:
    """Avoid treating a tool description as the user's request when that request is unavailable."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_prepare = guard._prepare_candidate
    original_execute = factory.EnhancedSafeToolExecutor.execute

    def contextual_prepare(
        executor: Any,
        args: dict[str, Any],
        *,
        run_dry: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        live_request = str(getattr(executor, "_factory_user_request", "") or "").strip()
        if live_request:
            return original_prepare(executor, args, run_dry=run_dry)

        fallback = _fallback_semantic_context(args)
        had_attribute = hasattr(executor, "_factory_user_request")
        previous = getattr(executor, "_factory_user_request", None)
        executor._factory_user_request = fallback
        try:
            prepared, meta = original_prepare(executor, args, run_dry=run_dry)
        finally:
            if had_attribute:
                executor._factory_user_request = previous
            else:
                try:
                    delattr(executor, "_factory_user_request")
                except AttributeError:
                    pass
        if isinstance(meta, dict):
            meta = dict(meta)
            description = str(args.get("description") or "").strip()
            context = "unambiguous_description" if fallback == description else "declared_pipeline"
            meta.setdefault("semantic_context", context)
        return prepared, meta

    def contextual_execute(
        self: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        result = original_execute(self, name, arguments, thread_id=thread_id)
        if (
            name == "create_learned_tool"
            and isinstance(result, dict)
            and result.get("status") in {"created", "reused"}
        ):
            self._factory_user_request = ""
        return result

    guard._prepare_candidate = contextual_prepare
    factory.EnhancedSafeToolExecutor.execute = contextual_execute
    _INSTALLED = True
