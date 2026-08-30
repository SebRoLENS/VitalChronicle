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
from .local_ai import DEFAULT_MODEL, recommended_model

OLLAMA_LINUX_URL = "https://docs.ollama.com/linux"
OLLAMA_GPU_URL = "https://docs.ollama.com/gpu"


class AISetupDialog(QDialog):
    def __init__(
        self, model: str = DEFAULT_MODEL, profile: str = "gpu16", parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configura l’AI locale")
        self.setMinimumSize(680, 520)
        model = model.strip() or recommended_model(profile)
        cpu_only = profile == "cpu32"

        root = QVBoxLayout(self)
        title = QLabel(
            "AI locale per 32 GB di RAM e solo CPU"
            if cpu_only
            else "AI locale per 16 GB di RAM e NVIDIA RTX 4060"
        )
        title.setObjectName("pageTitle")
        intro = QLabel(
            (
                "Il profilo consigliato usa Qwen3 30B-A3B quantizzato (circa 19 GB): è un "
                "modello MoE grande, ma attiva solo una parte dei parametri per ogni token. "
                "Qwen3.5 27B privilegia la qualità dense; Qwen3.6 35B-A3B usa circa 23 GB e "
                "può portare allo swap, quindi è indicato come opzione massima."
            )
            if cpu_only
            else (
                "Il profilo consigliato usa Qwen3.5 9B quantizzato. Qwen3 14B è disponibile "
                "per privilegiare la dimensione, ma sulla RTX 4060 può usare anche la RAM di "
                "sistema. Ollama accelera automaticamente sulla GPU quando il driver funziona."
            )
        )
        intro.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(intro)

        steps = QLabel(
            (
                "1. Installa e avvia Ollama su Fedora.\n"
                "2. Torna al programma e premi “Scarica modello”.\n"
                "3. L'analisi userà la CPU: durante la generazione è normale un carico elevato."
            )
            if cpu_only
            else (
                "1. Verifica che il driver NVIDIA funzioni.\n"
                "2. Installa e avvia Ollama su Fedora.\n"
                "3. Torna al programma e premi “Scarica modello”."
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

        copy = QPushButton("Copia comandi")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(commands.toPlainText()))
        docs = QPushButton("Guida ufficiale Ollama per Linux")
        docs.clicked.connect(lambda: open_external_url(OLLAMA_LINUX_URL))
        root.addWidget(copy)
        root.addWidget(docs)
        if not cpu_only:
            gpu = QPushButton("Compatibilità GPU NVIDIA")
            gpu.clicked.connect(lambda: open_external_url(OLLAMA_GPU_URL))
            root.addWidget(gpu)
        root.addStretch()

        warning = QLabel(
            "L’AI spiega risultati statistici e non sostituisce un medico. Il programma non "
            "invia i dati a servizi AI online. Il controllo aggiornamenti consulta soltanto i "
            "metadati pubblici del modello selezionato."
        )
        warning.setObjectName("disclaimer")
        warning.setWordWrap(True)
        root.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
