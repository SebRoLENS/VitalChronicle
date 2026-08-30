from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def clean_desktop_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment safe for launching host desktop applications.

    AppImage and PyInstaller runtimes can inject Qt, Python, and dynamic-library
    paths that are valid for VitalChronicle but incompatible with the system
    browser or xdg-open. Restore the original loader path when AppImage preserved
    it, and remove the remaining private runtime paths.
    """
    environment = dict(source or os.environ)
    original_ld_path = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    if original_ld_path:
        environment["LD_LIBRARY_PATH"] = original_ld_path
    else:
        environment.pop("LD_LIBRARY_PATH", None)
    for variable in (
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "PYTHONHOME",
        "QT_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
    ):
        environment.pop(variable, None)
    return environment


def _linux_opener() -> list[str] | None:
    for executable, arguments in (
        ("/usr/bin/xdg-open", []),
        ("/usr/bin/gio", ["open"]),
        (shutil.which("xdg-open"), []),
        (shutil.which("gio"), ["open"]),
    ):
        if executable and os.path.isfile(executable) and os.access(executable, os.X_OK):
            return [executable, *arguments]
    return None


def open_external_url(url: str) -> bool:
    """Open *url* with the host browser, including from frozen applications."""
    try:
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(
                ["/usr/bin/open", url],
                env=clean_desktop_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        opener = _linux_opener()
        if opener:
            subprocess.Popen(
                [*opener, url],
                env=clean_desktop_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return bool(QDesktopServices.openUrl(QUrl(url)))
