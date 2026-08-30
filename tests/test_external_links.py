from __future__ import annotations

from google_health_viewer import external_links


def test_clean_desktop_environment_restores_host_loader_path():
    cleaned = external_links.clean_desktop_environment(
        {
            "PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/tmp/app/lib",
            "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
            "QT_PLUGIN_PATH": "/tmp/app/plugins",
            "QML2_IMPORT_PATH": "/tmp/app/qml",
            "PYTHONHOME": "/tmp/app/python",
        }
    )

    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert "QT_PLUGIN_PATH" not in cleaned
    assert "QML2_IMPORT_PATH" not in cleaned
    assert "PYTHONHOME" not in cleaned


def test_linux_link_uses_sanitized_host_opener(monkeypatch):
    launched = {}

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["environment"] = kwargs["env"]
        return object()

    monkeypatch.setattr(external_links.sys, "platform", "linux")
    monkeypatch.setattr(external_links, "_linux_opener", lambda: ["/usr/bin/xdg-open"])
    monkeypatch.setattr(external_links.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/app/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    assert external_links.open_external_url("https://example.com") is True
    assert launched["command"] == ["/usr/bin/xdg-open", "https://example.com"]
    assert "LD_LIBRARY_PATH" not in launched["environment"]
