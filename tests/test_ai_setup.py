from __future__ import annotations

from PySide6.QtWidgets import QApplication

from google_health_viewer.ai_setup import AISetupDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ai_setup_includes_linux_windows_and_macos_instructions():
    _app()
    dialog = AISetupDialog(model="qwen3.5:9b", profile="gpu16")

    assert dialog.platform_tabs.count() == 3
    assert [dialog.platform_tabs.tabText(index) for index in range(3)] == [
        "Linux",
        "Windows",
        "macOS",
    ]
    assert "curl -fsSL https://ollama.com/install.sh | sh" in (
        dialog.command_boxes["linux"].toPlainText()
    )
    assert "ollama pull qwen3.5:9b" in dialog.command_boxes["windows"].toPlainText()
    assert "ollama pull qwen3.5:9b" in dialog.command_boxes["macos"].toPlainText()
    assert "Fedora" not in " ".join(
        box.toPlainText() for box in dialog.command_boxes.values()
    )

    dialog.close()
