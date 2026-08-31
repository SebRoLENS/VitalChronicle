from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from google_health_viewer.self_update import (
    SelfUpdateError,
    UpdateTarget,
    install_update,
    parse_sha256_manifest,
    select_update_target,
)
from google_health_viewer.updates import ReleaseAsset, ReleaseInfo


def _release(*assets: ReleaseAsset) -> ReleaseInfo:
    return ReleaseInfo("1.2.3", "https://example.test/release", tuple(assets))


def test_selects_only_the_package_matching_the_running_appimage(tmp_path: Path):
    current = tmp_path / "My-current-copy.AppImage"
    current.write_bytes(b"old")
    linux = ReleaseAsset(
        "VitalChronicle-1.2.3-linux-x86_64.AppImage", "https://example.test/linux"
    )
    windows = ReleaseAsset(
        "VitalChronicle-1.2.3-windows-x86_64.exe", "https://example.test/windows"
    )

    target = select_update_target(
        _release(windows, linux),
        environ={"APPIMAGE": str(current)},
        platform="linux",
        frozen=False,
    )

    assert target == UpdateTarget("appimage", current.resolve(), linux)


def test_does_not_cross_update_package_formats(tmp_path: Path):
    current = tmp_path / "VitalChronicle.AppImage"
    current.write_bytes(b"old")
    release = _release(
        ReleaseAsset(
            "VitalChronicle-1.2.3-windows-x86_64.exe",
            "https://example.test/windows",
        )
    )
    assert (
        select_update_target(
            release,
            environ={"APPIMAGE": str(current)},
            platform="linux",
        )
        is None
    )


def test_parse_sha256_manifest_ignores_invalid_rows():
    digest = "a" * 64
    assert parse_sha256_manifest(
        f"{digest}  package.AppImage\ninvalid  ignored\n"
    ) == {"package.AppImage": digest}


def test_verified_appimage_replaces_same_path_and_keeps_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = tmp_path / "custom-name.AppImage"
    current.write_bytes(b"old application")
    current.chmod(0o755)
    new_content = b"new verified application"
    digest = hashlib.sha256(new_content).hexdigest()
    asset = ReleaseAsset(
        "VitalChronicle-1.2.3-linux-x86_64.AppImage",
        "https://example.test/linux",
        digest=f"sha256:{digest}",
    )
    release = _release(asset)

    def fake_download(_asset, destination, _progress):
        destination.write_bytes(new_content)
        return digest

    monkeypatch.setattr("google_health_viewer.self_update._download", fake_download)
    result = install_update(release, UpdateTarget("appimage", current, asset))

    assert result.destination == current
    assert current.read_bytes() == new_content
    assert result.backup.read_bytes() == b"old application"
    if os.name != "nt":
        assert current.stat().st_mode & 0o100


def test_checksum_failure_keeps_current_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = tmp_path / "current.AppImage"
    current.write_bytes(b"old")
    asset = ReleaseAsset(
        "VitalChronicle-1.2.3-linux-x86_64.AppImage",
        "https://example.test/linux",
        digest="sha256:" + "0" * 64,
    )

    def fake_download(_asset, destination, _progress):
        destination.write_bytes(b"tampered")
        return "f" * 64

    monkeypatch.setattr("google_health_viewer.self_update._download", fake_download)
    with pytest.raises(SelfUpdateError, match="SHA-256"):
        install_update(_release(asset), UpdateTarget("appimage", current, asset))
    assert current.read_bytes() == b"old"
