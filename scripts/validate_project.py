#!/usr/bin/env python3
"""Validate release metadata, documentation, screenshots, and secret exclusions."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_URL = "https://buymeacoffee.com/sebromi"


def declared_version() -> str:
    text = (ROOT / "google_health_viewer" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.MULTILINE)
    if not match:
        raise AssertionError("Missing __version__")
    return match.group(1)


def main() -> int:
    version = declared_version()
    required_text = (
        "pyproject.toml",
        "README.md",
        "docs/manual.md",
        "docs/quick-start.md",
        "docs/releasing.md",
        "CITATION.cff",
    )
    for relative in required_text:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert version in text, f"{relative} does not declare {version}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count(SUPPORT_URL) >= 3
    assert "VitalChronicle is and will remain" in readme
    assert "http://localhost:8765/" in readme

    required_files = (
        ".github/workflows/ci.yml",
        ".github/workflows/automatic-release.yml",
        ".github/workflows/build-desktop.yml",
        "docs/screenshots/overview.png",
        "docs/screenshots/data-explorer.png",
        "docs/screenshots/local-ai.png",
        "docs/screenshots/ai-settings.png",
        "packaging/linux/vitalchronicle.desktop",
    )
    for relative in required_files:
        path = ROOT / relative
        assert path.is_file() and path.stat().st_size > 0, f"Missing {relative}"

    forbidden_patterns = (
        "client_secret*.json",
        "authorized_user*.json",
        "*.sqlite3",
        "*.db",
        "*.gguf",
    )
    for pattern in forbidden_patterns:
        leaked = [path for path in ROOT.rglob(pattern) if ".venv" not in path.parts]
        assert not leaked, f"Sensitive/generated files found: {leaked}"

    assert "VitalChronicle" in (
        ROOT / "google_health_viewer" / "branding.py"
    ).read_text(encoding="utf-8")
    print(f"VitalChronicle {version}: project validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
