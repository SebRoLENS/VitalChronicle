"""Per-user Linux launcher for the packaged AppImage."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def integrate_linux_appimage(
    source: Path,
    icon_source: Path,
    *,
    home: Path | None = None,
    data_home: Path | None = None,
    refresh: bool = True,
) -> Path:
    """Install a stable launcher without root access, preserving manual launchers."""
    if not source.is_file() or not icon_source.is_file():
        raise FileNotFoundError("AppImage or bundled icon is missing")
    user_home = home or Path.home()
    data = data_home or Path(os.environ.get("XDG_DATA_HOME") or user_home / ".local/share")
    installed = user_home / ".local/opt/VitalChronicle/VitalChronicle.AppImage"
    desktop = data / "applications/vitalchronicle.desktop"
    icon = data / "icons/hicolor/scalable/apps/vitalchronicle.svg"
    for destination in (installed, desktop, icon):
        destination.parent.mkdir(parents=True, exist_ok=True)
    # Avoid copying the AppImage on every launch; a new release changes its size
    # or mtime. Copy to a temporary file before replacing an installed build.
    if source.resolve() != installed.resolve() and (
        not installed.exists()
        or source.stat().st_size != installed.stat().st_size
        or source.stat().st_mtime_ns != installed.stat().st_mtime_ns
    ):
        temporary = installed.with_name(installed.name + ".new")
        shutil.copy2(source, temporary)
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR)
        os.replace(temporary, installed)
    installed.chmod(installed.stat().st_mode | stat.S_IXUSR)

    # A manually created launcher belongs to the user. Leave it in place.
    managed = "X-VitalChronicle-Managed=true"
    if not desktop.exists() or managed in desktop.read_text(encoding="utf-8"):
        command = str(installed).replace("\\", "\\\\").replace('"', '\\"')
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=VitalChronicle\n"
            f'Exec="{command}"\nIcon=vitalchronicle\nTerminal=false\n'
            "Categories=Science;Utility;\nStartupNotify=true\n"
            f"{managed}\n",
            encoding="utf-8",
        )
        shutil.copy2(icon_source, icon)
        if refresh and shutil.which("update-desktop-database"):
            subprocess.run(
                ["update-desktop-database", str(desktop.parent)],
                check=False, capture_output=True, timeout=10,
            )
    return desktop
