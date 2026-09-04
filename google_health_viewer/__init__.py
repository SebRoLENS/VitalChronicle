"""VitalChronicle: a local-first Google Health dashboard."""

from . import analysis as _analysis
from .deterministic_context_patch import (
    install_deterministic_context_patch as _install_deterministic_context_patch,
)
from .deterministic_detail_core import (
    install_deterministic_detail_core as _install_deterministic_detail_core,
)
from .heart_rate_core import install_shared_heart_rate_core as _install_shared_heart_rate_core

__version__ = "1.3.1"

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
