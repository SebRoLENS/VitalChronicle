import sys

import pytest

from google_health_viewer.desktop_integration import integrate_linux_appimage

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux desktop integration")


def test_linux_appimage_creates_and_updates_user_launcher(tmp_path):
    source = tmp_path / "download.AppImage"
    source.write_bytes(b"first version")
    icon = tmp_path / "app_icon.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    home = tmp_path / "home"
    data = tmp_path / "xdg"

    desktop = integrate_linux_appimage(source, icon, home=home, data_home=data, refresh=False)
    installed = home / ".local/opt/VitalChronicle/VitalChronicle.AppImage"
    assert installed.read_bytes() == b"first version"
    assert installed.stat().st_mode & 0o100
    assert f'Exec="{installed}"' in desktop.read_text(encoding="utf-8")
    assert (data / "icons/hicolor/scalable/apps/vitalchronicle.svg").exists()

    source.write_bytes(b"next version of VitalChronicle")
    integrate_linux_appimage(source, icon, home=home, data_home=data, refresh=False)
    assert installed.read_bytes() == b"next version of VitalChronicle"


def test_linux_integration_preserves_manual_launcher(tmp_path):
    source = tmp_path / "VitalChronicle.AppImage"
    source.write_bytes(b"app")
    icon = tmp_path / "icon.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    data = tmp_path / "data"
    desktop = data / "applications/vitalchronicle.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\nExec=/custom/app\n", encoding="utf-8")
    integrate_linux_appimage(source, icon, home=tmp_path / "home", data_home=data, refresh=False)
    assert desktop.read_text(encoding="utf-8") == "[Desktop Entry]\nExec=/custom/app\n"
