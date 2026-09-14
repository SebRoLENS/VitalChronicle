from __future__ import annotations

from typing import Any

_INSTALLED = False


def install_hard_query_lazy_patch() -> None:
    """Defer Qt-dependent hard-query runtime imports until an executor is actually created."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import agent_tool_factory as factory

    original_init = factory.EnhancedSafeToolExecutor.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        from . import agent_hard_query_reliability_patch as hard

        hard.install_hard_query_reliability_patch()
        self.agent_store.sync_builtin_tools([hard._SLEEP_CONDITIONED_SPEC])
        hard._install_runtime_support()

    factory.EnhancedSafeToolExecutor.__init__ = patched_init
    _INSTALLED = True
