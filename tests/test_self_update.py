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


def test_verified_appimage_uses_release_filename_and_removes_old_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = tmp_path / "VitalChronicle-1.1.10-linux-x86_64.AppImage"
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

    installed = tmp_path / asset.name
    assert result.destination == installed
    assert result.backup is None
    assert installed.read_bytes() == new_content
    assert not current.exists()
    assert not current.with_name(f".{current.name}.rollback").exists()
    if os.name != "nt":
        assert installed.stat().st_mode & 0o100


def test_verified_appimage_also_normalizes_a_custom_filename(
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

    def fake_download(_asset, destination, _progress):
        destination.write_bytes(new_content)
        return digest

    monkeypatch.setattr("google_health_viewer.self_update._download", fake_download)
    result = install_update(_release(asset), UpdateTarget("appimage", current, asset))

    assert result.destination == tmp_path / asset.name
    assert result.destination.read_bytes() == new_content
    assert not current.exists()


def test_windows_helper_renames_new_executable_and_removes_old_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = tmp_path / "VitalChronicle-1.1.10-windows-x86_64.exe"
    current.write_bytes(b"old application")
    new_content = b"new verified application"
    digest = hashlib.sha256(new_content).hexdigest()
    asset = ReleaseAsset(
        "VitalChronicle-1.2.3-windows-x86_64.exe",
        "https://example.test/windows",
        digest=f"sha256:{digest}",
    )

    def fake_download(_asset, destination, _progress):
        destination.write_bytes(new_content)
        return digest

    monkeypatch.setattr("google_health_viewer.self_update._download", fake_download)
    result = install_update(_release(asset), UpdateTarget("windows-exe", current, asset))

    installed = tmp_path / asset.name
    assert result.pending_exit
    assert result.destination == installed
    assert result.backup is None
    assert result.helper is not None
    helper_text = result.helper.read_text(encoding="utf-8")
    staged = current.with_name(f".{asset.name}.update")
    assert f'del /f /q "{current}"' in helper_text
    assert f'move /y "{staged}" "{installed}"' in helper_text
    assert f'start "" "{installed}"' in helper_text
    assert ".rollback" in helper_text
    assert helper_text.count("del /f /q") >= 2


def test_rejects_unsafe_release_asset_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    current = tmp_path / "current.AppImage"
    current.write_bytes(b"old")
    new_content = b"new"
    digest = hashlib.sha256(new_content).hexdigest()
    asset = ReleaseAsset(
        "../VitalChronicle.AppImage",
        "https://example.test/linux",
        digest=f"sha256:{digest}",
    )

    def fake_download(_asset, destination, _progress):
        destination.write_bytes(new_content)
        return digest

    monkeypatch.setattr("google_health_viewer.self_update._download", fake_download)
    with pytest.raises(SelfUpdateError, match="unsafe filename"):
        install_update(_release(asset), UpdateTarget("appimage", current, asset))
    assert current.read_bytes() == b"old"


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
