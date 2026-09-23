from __future__ import annotations

from typing import Any

_INSTALLED = False


def install_agent_quality_lazy_patch() -> None:
    """Defer Qt-dependent quality-runtime wiring until an agent executor is created."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    original_init = factory.EnhancedSafeToolExecutor.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        from .agent_quality_patch import install_agent_quality_patch

        install_agent_quality_patch()

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    _INSTALLED = True
