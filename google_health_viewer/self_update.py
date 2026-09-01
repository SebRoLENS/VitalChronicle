"""Safe, package-aware updates for portable VitalChronicle builds."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import requests

from .updates import ReleaseAsset, ReleaseInfo


class SelfUpdateError(RuntimeError):
    """Raised when an update cannot be verified or safely installed."""


@dataclass(frozen=True)
class UpdateTarget:
    kind: str
    destination: Path
    asset: ReleaseAsset


@dataclass(frozen=True)
class UpdateResult:
    destination: Path
    backup: Path | None = None
    helper: Path | None = None

    @property
    def pending_exit(self) -> bool:
        return self.helper is not None


def select_update_target(
    release: ReleaseInfo,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    executable: str | None = None,
    frozen: bool | None = None,
) -> UpdateTarget | None:
    """Select only the asset matching the package currently being executed."""
    environment = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    executable_path = sys.executable if executable is None else executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen

    if system.startswith("linux") and environment.get("APPIMAGE"):
        kind = "appimage"
        destination = Path(environment["APPIMAGE"]).expanduser().absolute()
        expected = f"VitalChronicle-{release.version}-linux-x86_64.AppImage"
    elif system == "win32" and is_frozen:
        kind = "windows-exe"
        destination = Path(executable_path).absolute()
        expected = f"VitalChronicle-{release.version}-windows-x86_64.exe"
    else:
        return None

    asset = next((item for item in release.assets if item.name == expected), None)
    if asset is None or not destination.is_file():
        return None
    return UpdateTarget(kind, destination, asset)


def parse_sha256_manifest(content: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in content.splitlines():
        parts = raw_line.strip().split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            continue
        filename = parts[1].lstrip("* ")
        try:
            int(parts[0], 16)
        except ValueError:
            continue
        checksums[filename] = parts[0].lower()
    return checksums


def _expected_digest(release: ReleaseInfo, asset: ReleaseAsset) -> str:
    if asset.digest:
        algorithm, separator, digest = asset.digest.partition(":")
        if separator and algorithm.lower() == "sha256" and len(digest) == 64:
            return digest.lower()
    manifest = next((item for item in release.assets if item.name == "SHA256SUMS.txt"), None)
    if manifest is None:
        raise SelfUpdateError("The release does not provide a SHA-256 checksum.")
    response = requests.get(manifest.url, timeout=(10, 60))
    response.raise_for_status()
    digest = parse_sha256_manifest(response.text).get(asset.name)
    if digest is None:
        raise SelfUpdateError("The release checksum does not include this package.")
    return digest


def _download(
    asset: ReleaseAsset,
    destination: Path,
    progress: Callable[[int, str], None] | None,
) -> str:
    digest = hashlib.sha256()
    downloaded = 0
    with requests.get(asset.url, stream=True, timeout=(10, 300)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or asset.size or 0)
        with destination.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                stream.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                percent = int(downloaded * 100 / total) if total else 0
                if progress:
                    progress(percent, f"{downloaded / 1048576:.1f} MB")
            stream.flush()
            os.fsync(stream.fileno())
    return digest.hexdigest()


def _release_destination(target: UpdateTarget) -> Path:
    """Return the canonical versioned filename shipped by the GitHub release."""
    asset_name = target.asset.name.strip()
    if (
        not asset_name
        or asset_name in {".", ".."}
        or "/" in asset_name
        or "\\" in asset_name
        or Path(asset_name).name != asset_name
    ):
        raise SelfUpdateError("The release package has an unsafe filename.")
    return target.destination.with_name(asset_name)


def install_update(
    release: ReleaseInfo,
    target: UpdateTarget,
    progress: Callable[[int, str], None] | None = None,
) -> UpdateResult:
    """Download, verify, and install or stage a compatible portable update."""
    destination = target.destination
    if not destination.is_file():
        raise SelfUpdateError("The running application file no longer exists.")
    if not os.access(destination.parent, os.W_OK):
        raise SelfUpdateError("The application folder is not writable.")

    installed_destination = _release_destination(target)
    staged = destination.with_name(f".{target.asset.name}.update")
    try:
        actual = _download(target.asset, staged, progress)
        expected = _expected_digest(release, target.asset)
        if actual.lower() != expected.lower():
            raise SelfUpdateError("SHA-256 verification failed; the current app was not changed.")

        if target.kind == "appimage":
            mode = stat.S_IMODE(destination.stat().st_mode)
            staged.chmod(mode | stat.S_IXUSR)
            if installed_destination == destination:
                os.replace(staged, destination)
                return UpdateResult(destination)

            rollback = destination.with_name(f".{destination.name}.rollback")
            rollback.unlink(missing_ok=True)
            os.replace(destination, rollback)
            try:
                # Install with the exact versioned filename published by the release,
                # then remove the temporary rollback copy. Linux permits renaming the
                # currently executing AppImage, so the running process can finish safely.
                os.replace(staged, installed_destination)
            except Exception:
                os.replace(rollback, destination)
                raise
            rollback.unlink(missing_ok=True)
            return UpdateResult(installed_destination)

        if target.kind == "windows-exe":
            helper = destination.with_name(f".{destination.stem}-update.cmd")
            rollback = destination.with_name(f".{destination.name}.rollback")
            unsafe_paths = (destination, installed_destination, staged, rollback, helper)
            if any(
                character in str(path)
                for path in unsafe_paths
                for character in ("%", "\r", "\n")
            ):
                raise SelfUpdateError(
                    "The application path contains characters that the Windows updater "
                    "cannot handle safely."
                )
            # The helper runs only after the GUI exits. It keeps a temporary rollback
            # copy, deletes the old executable, installs the verified package under the
            # new release filename, then removes the rollback copy after success.
            helper.write_text(
                "@echo off\r\n"
                "setlocal\r\n"
                f'copy /b /y "{destination}" "{rollback}" >nul 2>&1\r\n'
                "if errorlevel 1 exit /b 1\r\n"
                "for /l %%i in (1,1,30) do (\r\n"
                f'  del /f /q "{destination}" >nul 2>&1\r\n'
                f'  if not exist "{destination}" goto replace\r\n'
                "  timeout /t 1 /nobreak >nul\r\n"
                ")\r\n"
                f'del /f /q "{rollback}" >nul 2>&1\r\n'
                "exit /b 1\r\n"
                ":replace\r\n"
                f'move /y "{staged}" "{installed_destination}" >nul 2>&1\r\n'
                "if errorlevel 1 (\r\n"
                f'  move /y "{rollback}" "{destination}" >nul 2>&1\r\n'
                "  exit /b 1\r\n"
                ")\r\n"
                f'del /f /q "{rollback}" >nul 2>&1\r\n'
                f'start "" "{installed_destination}"\r\n'
                'del "%~f0"\r\n',
                encoding="utf-8",
            )
            return UpdateResult(installed_destination, helper=helper)
        raise SelfUpdateError("This package type cannot be updated automatically.")
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def launch_windows_helper(result: UpdateResult) -> None:
    if result.helper is None:
        raise SelfUpdateError("No Windows update helper was prepared.")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(result.helper)],
        creationflags=flags,
        close_fds=True,
    )
