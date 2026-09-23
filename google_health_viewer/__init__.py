"""VitalChronicle: a local-first Google Health dashboard."""

from . import analysis as _analysis
from .agent_factory_request_preserve_patch import (
    install_factory_request_preserve_patch as _install_factory_request_preserve_patch,
)
from .agent_hard_query_lazy_patch import (
    install_hard_query_lazy_patch as _install_hard_query_lazy_patch,
)
from .agent_quality_lazy_patch import (
    install_agent_quality_lazy_patch as _install_agent_quality_lazy_patch,
)
from .agent_runtime_adaptive_patch import (
    install_agent_runtime_adaptive_patch as _install_agent_runtime_adaptive_patch,
)
from .agent_runtime_efficiency_patch import (
    install_agent_runtime_efficiency_patch as _install_agent_runtime_efficiency_patch,
)
from .agent_token_budget_patch import (
    install_agent_token_budget_patch as _install_agent_token_budget_patch,
)
from .agent_tool_factory_compat_patch import (
    install_tool_factory_compat_patch as _install_tool_factory_compat_patch,
)
from .agent_tool_factory_english_patch import (
    install_tool_factory_english_patch as _install_tool_factory_english_patch,
)
from .agent_tool_factory_reliability_patch import (
    install_agent_tool_factory_reliability_patch as _install_agent_tool_factory_reliability_patch,
)
from .agent_tool_factory_schema_guard import (
    install_schema_aware_tool_factory as _install_schema_aware_tool_factory,
)
from .agent_tool_factory_semantic_compat_patch import (
    install_semantic_tool_factory_compat_patch as _install_semantic_tool_factory_compat_patch,
)
from .agent_tool_factory_semantic_guard import (
    install_semantic_tool_factory_guard as _install_semantic_tool_factory_guard,
)
from .ai_query_semantics import install_ai_query_semantics as _install_ai_query_semantics
from .deterministic_context_patch import (
    install_deterministic_context_patch as _install_deterministic_context_patch,
)
from .deterministic_detail_core import (
    install_deterministic_detail_core as _install_deterministic_detail_core,
)
from .heart_rate_core import install_shared_heart_rate_core as _install_shared_heart_rate_core
from .scientific_context_core import (
    install_scientific_context_core as _install_scientific_context_core,
)
from .scientific_context_preserve_core import (
    install_scientific_context_preserve_core as _install_scientific_context_preserve_core,
)

__version__ = "1.2.1"

_install_shared_heart_rate_core(_analysis)
_install_deterministic_detail_core(_analysis)
_install_deterministic_context_patch(_analysis)
_install_scientific_context_core()
_install_scientific_context_preserve_core()
_install_ai_query_semantics()
_install_agent_tool_factory_reliability_patch()
_install_schema_aware_tool_factory()
_install_tool_factory_compat_patch()
_install_semantic_tool_factory_guard()
_install_semantic_tool_factory_compat_patch()
_install_agent_runtime_efficiency_patch()
_install_agent_runtime_adaptive_patch()
_install_hard_query_lazy_patch()
_install_factory_request_preserve_patch()
_install_tool_factory_english_patch()
_install_agent_token_budget_patch()
_install_agent_quality_lazy_patch()
