"""Release metadata and semantic-version helpers for application updates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

RELEASES_URL = "https://github.com/SebRoLENS/google-health-dashboard-ai/releases"
LATEST_RELEASE_API = (
    "https://api.github.com/repos/SebRoLENS/google-health-dashboard-ai/releases/latest"
)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str


def semantic_version(value: str) -> tuple[int, int, int] | None:
    """Parse a stable ``major.minor.patch`` version, accepting a leading ``v``."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def update_kind(
    current: tuple[int, int, int], latest: tuple[int, int, int]
) -> str:
    if latest[0] != current[0]:
        return "major"
    if latest[1] != current[1]:
        return "minor"
    return "patch"


def notification_due(
    latest: str,
    last_notified_version: str,
    last_notified_at: float,
    now: float,
) -> bool:
    """Notify for a new version, then remind at most once every ten days."""
    if latest != last_notified_version:
        return True
    return now - last_notified_at >= 10 * 24 * 60 * 60


def release_from_payload(payload: Any) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise TypeError("GitHub returned invalid release metadata.")
    parsed = semantic_version(str(payload.get("tag_name", "")))
    if parsed is None:
        raise ValueError("GitHub returned an unrecognised release version.")
    version = ".".join(str(value) for value in parsed)
    return ReleaseInfo(version, str(payload.get("html_url") or RELEASES_URL))


def fetch_latest_release(current_version: str) -> ReleaseInfo:
    """Fetch the latest public release without blocking the GUI thread."""
    response = requests.get(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VitalChronicle/{current_version}",
        },
        timeout=10,
    )
    response.raise_for_status()
    return release_from_payload(response.json())
