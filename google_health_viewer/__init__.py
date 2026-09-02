"""VitalChronicle: a local-first Google Health dashboard."""

__version__ = "1.2.1"

# Keep desktop and Android on exactly the same heart-rate semantics. Importing
# the package installs the shared five-minute averaging/parser into analysis.py;
# Android receives these same files through sync_shared_core.py.
from . import analysis as _analysis
from .heart_rate_core import install_shared_heart_rate_core as _install_shared_heart_rate_core

_install_shared_heart_rate_core(_analysis)
