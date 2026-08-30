from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .external_links import open_external_url
from .i18n import _
from .local_ai import DEFAULT_MODEL, recommended_model

OLLAMA_LINUX_URL = "https://docs.ollama.com/linux"
OLLAMA_GPU_URL = "https://docs.ollama.com/gpu"


class AISetupDialog(QDialog):
    def __init__(
        self, model: str = DEFAULT_MODEL, profile: str = "gpu16", parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Set up local AI"))
        self.setMinimumSize(680, 520)
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

        steps = QLabel(
            (
                _("1. Install and start Ollama on Fedora.\n"
                  "2. Return to VitalChronicle and select Download model.\n"
                  "3. Analysis will use the CPU; high load during generation is normal.")
            )
            if cpu_only
            else (
                _("1. Check that the NVIDIA driver works.\n"
                  "2. Install and start Ollama on Fedora.\n"
                  "3. Return to VitalChronicle and select Download model.")
            )
        )
        steps.setWordWrap(True)
        root.addSpacing(12)
        root.addWidget(steps)

        commands = QPlainTextEdit()
        commands.setReadOnly(True)
        commands.setMaximumHeight(150)
        command_lines = [] if cpu_only else ["nvidia-smi"]
        command_lines.extend(
            [
                "curl -fsSL https://ollama.com/install.sh | sh",
                "sudo systemctl enable --now ollama",
                f"ollama pull {model}",
            ]
        )
        commands.setPlainText("\n".join(command_lines))
        root.addWidget(commands)

        copy = QPushButton(_("Copy commands"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(commands.toPlainText()))
        docs = QPushButton(_("Official Ollama guide for Linux"))
        docs.clicked.connect(lambda: open_external_url(OLLAMA_LINUX_URL))
        root.addWidget(copy)
        root.addWidget(docs)
        if not cpu_only:
            gpu = QPushButton(_("NVIDIA GPU compatibility"))
            gpu.clicked.connect(lambda: open_external_url(OLLAMA_GPU_URL))
            root.addWidget(gpu)
        root.addStretch()

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
