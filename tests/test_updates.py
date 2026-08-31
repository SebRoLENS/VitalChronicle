from __future__ import annotations

from google_health_viewer.updates import (
    RELEASES_URL,
    notification_due,
    release_from_payload,
    semantic_version,
    update_kind,
)


def test_semantic_versions_and_update_kind():
    assert semantic_version("v1.2.3") == (1, 2, 3)
    assert semantic_version("2.0.0-rc1") == (2, 0, 0)
    assert semantic_version("latest") is None
    assert update_kind((1, 2, 3), (1, 2, 4)) == "patch"
    assert update_kind((1, 2, 3), (1, 3, 0)) == "minor"
    assert update_kind((1, 2, 3), (2, 0, 0)) == "major"


def test_release_payload_and_fallback_url():
    release = release_from_payload(
        {
            "tag_name": "v1.4.0",
            "assets": [
                {
                    "name": "VitalChronicle-1.4.0-linux-x86_64.AppImage",
                    "browser_download_url": "https://example.test/appimage",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                },
                {"name": "ignored-without-url"},
            ],
        }
    )
    assert release.version == "1.4.0"
    assert release.url == RELEASES_URL
    assert len(release.assets) == 1
    assert release.assets[0].size == 123
    assert release.assets[0].digest == "sha256:" + "a" * 64


def test_update_reminder_is_due_for_new_version_or_after_ten_days():
    day = 24 * 60 * 60
    assert notification_due("1.1.0", "1.0.0", 100.0, 101.0)
    assert not notification_due("1.1.0", "1.1.0", 100.0, 100.0 + 9 * day)
    assert notification_due("1.1.0", "1.1.0", 100.0, 100.0 + 10 * day)
