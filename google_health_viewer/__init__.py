"""VitalChronicle: a local-first Google Health dashboard."""

from . import analysis as _analysis
from .agent_runtime_efficiency_patch import (
    install_agent_runtime_efficiency_patch as _install_agent_runtime_efficiency_patch,
)
from .agent_tool_factory_compat_patch import (
    install_tool_factory_compat_patch as _install_tool_factory_compat_patch,
)
from .agent_tool_factory_reliability_patch import (
    install_agent_tool_factory_reliability_patch as _install_agent_tool_factory_reliability_patch,
)
from .agent_tool_factory_schema_guard import (
    install_schema_aware_tool_factory as _install_schema_aware_tool_factory,
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

# Keep desktop and Android on exactly the same heart-rate semantics. Importing
# the package installs the shared five-minute averaging/parser into analysis.py;
# Android receives these same files through sync_shared_core.py.
_install_shared_heart_rate_core(_analysis)

# Add richer deterministic evidence without making ordinary AI requests larger:
# the existing adaptive pipeline retains structured details for relevant metrics
# and can trim them from broad requests when the context budget is tight.
_install_deterministic_detail_core(_analysis)

# Correct Google Health sleep-stage/short-awakening parsing and add temporal
# heart-rate context for detected workouts/activity levels.
_install_deterministic_context_patch(_analysis)

# Selectively add curated scientific interpretation to relevant AI requests while
# still allowing the language model to supplement it with established general knowledge.
_install_scientific_context_core()

# Preserve the established Maximum/deep guarantee: scientific context is additive
# there and must never evict compact deterministic measurements.
_install_scientific_context_preserve_core()

# Keep pure definitions separate from personal-data analysis and ensure Google
# Health Daily records are never mistaken for partial intraday measurements.
_install_ai_query_semantics()

# Register Tool Factory validation/repair hooks without importing the Qt-heavy
# agent runtime. Runtime token/resource limits are installed lazily when the
# personal agent executor is actually created.
_install_agent_tool_factory_reliability_patch()

# Compile learned tools against the schemas they call and against the live metric
# catalogue. Existing broken learned tools are repaired or disabled before reuse,
# and new tools must pass a bounded dry-run before they can be persisted as active.
_install_schema_aware_tool_factory()

# Preserve established Tool Factory error/repair contracts and lightweight unit
# construction while layering schema-aware compilation on top.
_install_tool_factory_compat_patch()

# Reuse deterministic evidence already prepared for the conversation, recognise
# personal-median requests as factory candidates, and enforce the local-agent token
# cap on the concrete runtime even when the desktop reasoning wrapper is installed first.
_install_agent_runtime_efficiency_patch()
