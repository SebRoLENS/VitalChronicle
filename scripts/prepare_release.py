#!/usr/bin/env python3
"""Prepare the first declared version or the next requested semantic release."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VALID_BUMPS = {"patch", "minor", "major"}


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise RuntimeError(f"Unsupported semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def read_declared_version() -> str:
    text = (ROOT / "google_health_viewer" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read declared version")
    return match.group(1)


def released_versions() -> list[tuple[int, int, int]]:
    output = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    versions = []
    for tag in output.splitlines():
        if VERSION_PATTERN.fullmatch(tag.removeprefix("v")):
            versions.append(parse_version(tag.removeprefix("v")))
    return sorted(versions)


def bump_version(version: tuple[int, int, int], bump: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def requested_bump() -> str:
    bump = str(os.environ.get("VITAL_RELEASE_BUMP", "patch") or "patch").lower()
    return bump if bump in VALID_BUMPS else "patch"


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(old, new)
    if updated == text and old != new:
        raise RuntimeError(f"Version {old} not found in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    current = read_declared_version()
    current_tuple = parse_version(current)
    releases = released_versions()
    if not releases or current_tuple > releases[-1]:
        target_tuple = current_tuple
    else:
        target_tuple = bump_version(releases[-1], requested_bump())
    target = ".".join(str(part) for part in target_tuple)

    for relative in (
        "google_health_viewer/__init__.py",
        "pyproject.toml",
        "README.md",
        "docs/manual.md",
        "docs/quick-start.md",
        "docs/releasing.md",
        "CITATION.cff",
    ):
        replace(ROOT / relative, current, target)

    citation = ROOT / "CITATION.cff"
    citation_text = citation.read_text(encoding="utf-8")
    release_day = datetime.now(timezone.utc).date().isoformat()
    citation_text = re.sub(
        r"^date-released: .+$",
        f"date-released: {release_day}",
        citation_text,
        flags=re.MULTILINE,
    )
    citation.write_text(citation_text, encoding="utf-8")

    changelog = ROOT / "CHANGELOG.md"
    changelog_text = changelog.read_text(encoding="utf-8")
    if f"## {target} " not in changelog_text:
        entry = (
            f"## {target} — {release_day}\n\n"
            "- automated maintenance release with validated application and packaging updates.\n\n"
        )
        marker = "All notable changes to VitalChronicle are documented here.\n\n"
        changelog_text = changelog_text.replace(marker, marker + entry, 1)
        changelog.write_text(changelog_text, encoding="utf-8")

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())