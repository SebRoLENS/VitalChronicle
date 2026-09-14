from __future__ import annotations

from typing import Any

from . import agent_tool_factory as factory

_INSTALLED = False


def _request_score(text: str) -> tuple[int, int]:
    folded = str(text or "").casefold()
    semantic_markers = (
        "confront",
        "rispetto",
        "consecutiv",
        "riposo",
        "sonno",
        "notte",
        "mediana",
        "baseline",
        "hrv",
        "frequenza cardiaca",
        "recuper",
        "compare",
        "sleep",
        "recovery",
    )
    marker_count = sum(marker in folded for marker in semantic_markers)
    return marker_count, len(text)


def install_factory_request_preserve_patch() -> None:
    """Do not let a later generic registry probe erase the original user request."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_execute = factory.EnhancedSafeToolExecutor.execute

    def preserving_execute(
        self: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        previous = str(getattr(self, "_factory_user_request", "") or "")
        incoming = str(args.get("description") or "").strip() if name == "search_tool_registry" else ""
        result = original_execute(self, name, args, thread_id=thread_id)
        if name == "search_tool_registry":
            candidates = [item for item in (previous, incoming) if item]
            if candidates:
                self._factory_user_request = max(candidates, key=_request_score)
        return result

    factory.EnhancedSafeToolExecutor.execute = preserving_execute
    _INSTALLED = True
