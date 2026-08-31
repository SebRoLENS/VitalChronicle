from __future__ import annotations

import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .external_links import open_external_url
from .i18n import _
from .local_ai import DEFAULT_MODEL, recommended_model

OLLAMA_LINUX_URL = "https://docs.ollama.com/linux"
OLLAMA_WINDOWS_URL = "https://docs.ollama.com/windows"
OLLAMA_MACOS_URL = "https://docs.ollama.com/macos"
OLLAMA_GPU_URL = "https://docs.ollama.com/gpu"


def _current_platform_index() -> int:
    if sys.platform.startswith("win"):
        return 1
    if sys.platform == "darwin":
        return 2
    return 0


class AISetupDialog(QDialog):
    def __init__(
        self, model: str = DEFAULT_MODEL, profile: str = "gpu16", parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Set up local AI"))
        self.setMinimumSize(720, 620)
        model = model.strip() or recommended_model(profile)
        cpu_only = profile == "cpu32"

        root = QVBoxLayout(self)
        title = QLabel(
            _("Local AI for 32 GB RAM and CPU only")
            if cpu_only
            else _("Local AI for 16 GB RAM and NVIDIA RTX 4060")
        )
        title.setObjectName("pageTitle")
        intro = QLabel(
            (
                _("The recommended profile uses quantised Qwen3 30B-A3B (about 19 GB). It is "
                  "a large MoE model, but activates only some parameters for each token. "
                  "Qwen3.5 27B prioritises dense-model quality; Qwen3.6 35B-A3B uses about "
                  "23 GB and may cause swapping, so it is shown as the maximum option.")
            )
            if cpu_only
            else (
                _("The recommended profile uses quantised Qwen3.5 9B. Qwen3 14B is available "
                  "when model size is preferred, but may also use system RAM with an RTX 4060. "
                  "Ollama automatically uses GPU acceleration when the driver is working.")
            )
        )
        intro.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(intro)

        root.addSpacing(12)
        platform_hint = QLabel(
            _(
                "Choose your operating system. Installation is performed with the official "
                "Ollama package; the final verification and model commands are the same on "
                "Linux, Windows, and macOS."
            )
        )
        platform_hint.setWordWrap(True)
        root.addWidget(platform_hint)

        self.platform_tabs = QTabWidget()
        self.command_boxes: dict[str, QPlainTextEdit] = {}
        linux_commands = [] if cpu_only else ["nvidia-smi"]
        linux_commands.extend(
            [
                "curl -fsSL https://ollama.com/install.sh | sh",
                "ollama --version",
                f"ollama pull {model}",
                "ollama list",
            ]
        )
        windows_commands = [] if cpu_only else ["nvidia-smi"]
        windows_commands.extend(["ollama --version", f"ollama pull {model}", "ollama list"])
        macos_commands = ["ollama --version", f"ollama pull {model}", "ollama list"]
        self._add_platform_tab(
            "linux",
            _("Linux"),
            _(
                "1. Open the official Linux guide below.\n"
                "2. Run the official installation command in a terminal. It works across "
                "supported Linux distributions.\n"
                "3. Verify Ollama, download the selected model, and confirm it appears in the "
                "list.\n"
                "4. Return to VitalChronicle and select Check again."
            ),
            linux_commands,
            OLLAMA_LINUX_URL,
        )
        self._add_platform_tab(
            "windows",
            _("Windows"),
            _(
                "1. Open the official Windows guide below.\n"
                "2. Download and run OllamaSetup.exe, then start Ollama from the Start menu.\n"
                "3. Open PowerShell, verify Ollama, and download the selected model.\n"
                "4. Return to VitalChronicle and select Check again."
            ),
            windows_commands,
            OLLAMA_WINDOWS_URL,
        )
        self._add_platform_tab(
            "macos",
            _("macOS"),
            _(
                "1. Open the official macOS guide below and download the DMG.\n"
                "2. Drag Ollama to Applications and launch it once.\n"
                "3. Open Terminal, verify Ollama, and download the selected model.\n"
                "4. Return to VitalChronicle and select Check again."
            ),
            macos_commands,
            OLLAMA_MACOS_URL,
        )
        self.platform_tabs.setCurrentIndex(_current_platform_index())
        root.addWidget(self.platform_tabs, 1)

        if not cpu_only:
            gpu = QPushButton(_("NVIDIA GPU compatibility"))
            gpu.clicked.connect(lambda: open_external_url(OLLAMA_GPU_URL))
            root.addWidget(gpu)

        warning = QLabel(
            _("AI explains statistical results and does not replace a doctor. VitalChronicle "
              "does not send data to online AI services. Update checks only retrieve public "
              "metadata for the selected model.")
        )
        warning.setObjectName("disclaimer")
        warning.setWordWrap(True)
        root.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_platform_tab(
        self,
        key: str,
        title: str,
        instructions: str,
        command_lines: list[str],
        documentation_url: str,
    ) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        steps = QLabel(instructions)
        steps.setWordWrap(True)
        layout.addWidget(steps)

        commands = QPlainTextEdit()
        commands.setReadOnly(True)
        commands.setMaximumHeight(145)
        commands.setPlainText("\n".join(command_lines))
        self.command_boxes[key] = commands
        layout.addWidget(commands)

        copy = QPushButton(_("Copy commands"))
        copy.clicked.connect(
            lambda _checked=False, box=commands: QGuiApplication.clipboard().setText(
                box.toPlainText()
            )
        )
        docs = QPushButton(_("Official Ollama guide for {platform}", platform=title))
        docs.clicked.connect(
            lambda _checked=False, url=documentation_url: open_external_url(url)
        )
        layout.addWidget(copy)
        layout.addWidget(docs)
        layout.addStretch()
        self.platform_tabs.addTab(page, title)
