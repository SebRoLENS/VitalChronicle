from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtGui import QDoubleValidator, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .ai_hardware import (
    PERFORMANCE_LABELS,
    PERFORMANCE_PROFILES,
    BenchmarkResult,
    HardwareInfo,
    benchmark_model,
    detect_hardware,
    legacy_hardware_profile,
    override_hardware,
)
from .ai_hardware import recommend_model as hardware_recommendation
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


def _bool_setting(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class BenchmarkThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self.model = model

    def run(self) -> None:
        try:
            self.completed.emit(benchmark_model(self.model))
        except Exception as exc:  # noqa: BLE001 - benchmark errors are shown in the UI.
            self.failed.emit(str(exc))


class AISetupDialog(QDialog):
    def __init__(
        self, model: str = DEFAULT_MODEL, profile: str = "gpu16", parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Local AI setup and performance"))
        self.setMinimumSize(780, 760)
        self.settings = QSettings()
        self._base_hardware = detect_hardware()
        self._recommendation = None
        self._benchmark_thread: BenchmarkThread | None = None
        self._command_model = model.strip() or recommended_model(profile)

        root = QVBoxLayout(self)
        title = QLabel(_("Hardware-aware local AI"))
        title.setObjectName("pageTitle")
        intro = QLabel(
            _(
                "VitalChronicle can detect this computer automatically, recommend a local model, "
                "and keep manual controls available. No hardware information is uploaded."
            )
        )
        intro.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(intro)

        hardware_card = QFrame()
        hardware_card.setObjectName("aiCard")
        hardware_layout = QGridLayout(hardware_card)
        hardware_layout.setContentsMargins(16, 14, 16, 14)

        self.hardware_summary = QLabel()
        self.hardware_summary.setWordWrap(True)
        self.hardware_summary.setObjectName("pageSubtitle")
        hardware_layout.addWidget(self.hardware_summary, 0, 0, 1, 4)

        hardware_layout.addWidget(QLabel(_("RAM")), 1, 0)
        self.ram_edit = QLineEdit()
        self.ram_edit.setValidator(QDoubleValidator(1.0, 1024.0, 1, self))
        stored_ram = self.settings.value("ai/hardware_ram_gb", None)
        ram_value = float(stored_ram) if stored_ram not in (None, "") else self._base_hardware.ram_gb
        self.ram_edit.setText(f"{ram_value:.1f}" if ram_value else "")
        self.ram_edit.setPlaceholderText(_("Detected RAM in GB"))
        hardware_layout.addWidget(self.ram_edit, 1, 1)
        hardware_layout.addWidget(QLabel("GB"), 1, 2)

        detect_button = QPushButton(_("Detect again"))
        detect_button.clicked.connect(self._detect_again)
        hardware_layout.addWidget(detect_button, 1, 3)

        hardware_layout.addWidget(QLabel(_("GPU")), 2, 0)
        self.gpu_edit = QLineEdit()
        stored_gpu = self.settings.value("ai/hardware_gpu_name", None)
        self.gpu_edit.setText(
            str(stored_gpu) if stored_gpu not in (None, "") else self._base_hardware.gpu_name
        )
        self.gpu_edit.setPlaceholderText(_("No GPU detected / enter manually"))
        hardware_layout.addWidget(self.gpu_edit, 2, 1, 1, 3)

        hardware_layout.addWidget(QLabel(_("VRAM")), 3, 0)
        self.vram_edit = QLineEdit()
        self.vram_edit.setValidator(QDoubleValidator(0.0, 256.0, 1, self))
        stored_vram = self.settings.value("ai/hardware_vram_gb", None)
        vram_value = (
            float(stored_vram)
            if stored_vram not in (None, "")
            else self._base_hardware.vram_gb
        )
        self.vram_edit.setText(f"{vram_value:.1f}" if vram_value is not None else "")
        self.vram_edit.setPlaceholderText(_("Unknown"))
        hardware_layout.addWidget(self.vram_edit, 3, 1)
        hardware_layout.addWidget(QLabel("GB"), 3, 2)
        hardware_layout.addWidget(
            QLabel(_("Editable when automatic detection is incomplete.")), 3, 3
        )

        hardware_layout.addWidget(QLabel(_("Performance")), 4, 0)
        self.performance_combo = QComboBox()
        for key in PERFORMANCE_PROFILES:
            self.performance_combo.addItem(_(PERFORMANCE_LABELS[key]), key)
        saved_performance = str(
            self.settings.value("ai/performance_profile", "standard") or "standard"
        )
        performance_index = self.performance_combo.findData(saved_performance)
        self.performance_combo.setCurrentIndex(max(0, performance_index))
        hardware_layout.addWidget(self.performance_combo, 4, 1, 1, 2)

        self.auto_model_check = QCheckBox(_("Automatically use the recommended model"))
        self.auto_model_check.setChecked(
            _bool_setting(self.settings.value("ai/automatic_model_selection", True))
        )
        hardware_layout.addWidget(self.auto_model_check, 4, 3)

        recommendation_title = QLabel(_("Recommendation"))
        recommendation_title.setStyleSheet("font-weight: 700;")
        hardware_layout.addWidget(recommendation_title, 5, 0)
        self.recommendation_label = QLabel()
        self.recommendation_label.setWordWrap(True)
        hardware_layout.addWidget(self.recommendation_label, 5, 1, 1, 3)

        recommendation_actions = QHBoxLayout()
        use_recommendation = QPushButton(_("Use recommended model"))
        use_recommendation.setObjectName("primaryButton")
        use_recommendation.clicked.connect(self._apply_recommendation)
        recommendation_actions.addWidget(use_recommendation)
        download_recommendation = QPushButton(_("Use and download"))
        download_recommendation.clicked.connect(self._download_recommended_model)
        recommendation_actions.addWidget(download_recommendation)
        self.benchmark_button = QPushButton(_("Benchmark selected model"))
        self.benchmark_button.clicked.connect(self._run_benchmark)
        recommendation_actions.addWidget(self.benchmark_button)
        hardware_layout.addLayout(recommendation_actions, 6, 0, 1, 4)

        self.benchmark_label = QLabel(
            _("Optional benchmark: measures the actual local Ollama decode speed on this computer.")
        )
        self.benchmark_label.setWordWrap(True)
        self.benchmark_label.setObjectName("pageSubtitle")
        hardware_layout.addWidget(self.benchmark_label, 7, 0, 1, 4)

        warning = QLabel(
            _(
                "Your computer performs the analysis. With large models, processing can take "
                "15 minutes or more."
            )
        )
        warning.setObjectName("disclaimer")
        warning.setWordWrap(True)
        hardware_layout.addWidget(warning, 8, 0, 1, 4)
        hardware_layout.setColumnStretch(1, 1)
        root.addWidget(hardware_card)

        self.ram_edit.editingFinished.connect(self._hardware_edited)
        self.gpu_edit.editingFinished.connect(self._hardware_edited)
        self.vram_edit.editingFinished.connect(self._hardware_edited)
        self.performance_combo.currentIndexChanged.connect(self._performance_changed)
        self.auto_model_check.toggled.connect(self._automatic_selection_changed)

        root.addSpacing(8)
        platform_hint = QLabel(
            _(
                "Ollama installation and model download remain fully local. The commands below "
                "follow the official Ollama setup for each operating system."
            )
        )
        platform_hint.setWordWrap(True)
        root.addWidget(platform_hint)

        self.platform_tabs = QTabWidget()
        self.command_boxes: dict[str, QPlainTextEdit] = {}
        self._add_platform_tab(
            "linux",
            _("Linux"),
            _(
                "1. Open the official Linux guide below.\n"
                "2. Run the official installation command in a terminal.\n"
                "3. Verify Ollama, download the selected model, and confirm it appears in the list.\n"
                "4. Return to VitalChronicle and select Check again."
            ),
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
            OLLAMA_MACOS_URL,
        )
        self.platform_tabs.setCurrentIndex(_current_platform_index())
        root.addWidget(self.platform_tabs, 1)

        gpu = QPushButton(_("GPU compatibility"))
        gpu.clicked.connect(lambda: open_external_url(OLLAMA_GPU_URL))
        root.addWidget(gpu)

        privacy = QLabel(
            _(
                "AI explains statistical results and does not replace a doctor. VitalChronicle "
                "does not send health data or detected hardware to online AI services."
            )
        )
        privacy.setObjectName("disclaimer")
        privacy.setWordWrap(True)
        root.addWidget(privacy)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_commands()
        self._refresh_recommendation(apply_if_auto=parent is not None)

    def _add_platform_tab(
        self,
        key: str,
        title: str,
        instructions: str,
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

    def _hardware_from_fields(self) -> HardwareInfo:
        try:
            ram = float(self.ram_edit.text())
        except ValueError:
            ram = self._base_hardware.ram_gb
        try:
            vram = float(self.vram_edit.text()) if self.vram_edit.text().strip() else None
        except ValueError:
            vram = self._base_hardware.vram_gb
        return override_hardware(
            self._base_hardware,
            ram_gb=ram,
            gpu_name=self.gpu_edit.text(),
            vram_gb=vram,
        )

    def _save_hardware(self, hardware: HardwareInfo) -> None:
        self.settings.setValue("ai/hardware_ram_gb", hardware.ram_gb)
        self.settings.setValue("ai/hardware_gpu_name", hardware.gpu_name)
        if hardware.vram_gb is None:
            self.settings.remove("ai/hardware_vram_gb")
        else:
            self.settings.setValue("ai/hardware_vram_gb", hardware.vram_gb)
        self.settings.setValue(
            "ai/performance_profile", str(self.performance_combo.currentData())
        )
        self.settings.setValue(
            "ai/automatic_model_selection", self.auto_model_check.isChecked()
        )

    def _refresh_hardware_summary(self, hardware: HardwareInfo) -> None:
        gpu = hardware.gpu_name or _("No dedicated GPU detected")
        vram = (
            _("{value:.1f} GB VRAM", value=hardware.vram_gb)
            if hardware.vram_gb is not None
            else _("VRAM unknown")
        )
        self.hardware_summary.setText(
            _(
                "Detected: {os} · {cpu} · {cores} logical CPU cores · {ram:.1f} GB RAM · "
                "{gpu} · {vram}",
                os=hardware.os_name,
                cpu=hardware.cpu_name,
                cores=hardware.cpu_cores,
                ram=hardware.ram_gb,
                gpu=gpu,
                vram=vram,
            )
        )

    def _refresh_recommendation(self, *, apply_if_auto: bool = False) -> None:
        hardware = self._hardware_from_fields()
        profile = str(self.performance_combo.currentData() or "standard")
        self._save_hardware(hardware)
        self._refresh_hardware_summary(hardware)
        self._recommendation = hardware_recommendation(hardware, profile)
        memory = (
            _("about {size:.1f} GB of model weights", size=self._recommendation.model_memory_gb)
            if self._recommendation.model_memory_gb is not None
            else _("model memory depends on the selected quantisation")
        )
        self.recommendation_label.setText(
            _(
                "{model} · {memory} · {time}. {reason}",
                model=self._recommendation.model,
                memory=memory,
                time=self._recommendation.expected_time,
                reason=self._recommendation.rationale,
            )
        )
        if apply_if_auto and self.auto_model_check.isChecked():
            self._apply_recommendation()

    def _sync_parent(self, model: str, hardware: HardwareInfo) -> None:
        self.settings.setValue("ai/model", model)
        legacy_profile = legacy_hardware_profile(hardware)
        self.settings.setValue("ai/hardware_profile", legacy_profile)
        self.settings.setValue("ai/ram_gb", max(1, round(hardware.ram_gb)))
        parent = self.parent()
        if parent is None:
            return
        profile_combo = getattr(parent, "ai_profile_combo", None)
        if profile_combo is not None:
            index = profile_combo.findData(legacy_profile)
            if index >= 0:
                profile_combo.setCurrentIndex(index)
        ram_edit = getattr(parent, "ai_ram_edit", None)
        if ram_edit is not None:
            ram_edit.setText(str(max(1, round(hardware.ram_gb))))
        model_combo = getattr(parent, "ai_model_combo", None)
        if model_combo is not None:
            model_combo.setCurrentText(model)

    def _apply_recommendation(self) -> None:
        if self._recommendation is None:
            self._refresh_recommendation()
        if self._recommendation is None:
            return
        hardware = self._hardware_from_fields()
        self._command_model = self._recommendation.model
        self._sync_parent(self._recommendation.model, hardware)
        self._refresh_commands()
        self.benchmark_label.setText(
            _(
                "Selected {model}. Use Check in the main AI panel to verify installation.",
                model=self._recommendation.model,
            )
        )

    def _download_recommended_model(self) -> None:
        self._apply_recommendation()
        parent = self.parent()
        pull = getattr(parent, "pull_ai_model", None) if parent is not None else None
        if callable(pull):
            pull()
            self.benchmark_label.setText(
                _("Model download started. Progress is shown in the main Local AI panel.")
            )
        else:
            QMessageBox.information(
                self,
                _("Download model"),
                _("Use the displayed 'ollama pull' command to download the recommended model."),
            )

    def _refresh_commands(self) -> None:
        if not hasattr(self, "command_boxes"):
            return
        linux = (
            []
            if legacy_hardware_profile(self._hardware_from_fields()) == "cpu32"
            else ["nvidia-smi"]
        )
        linux.extend(
            [
                "curl -fsSL https://ollama.com/install.sh | sh",
                "ollama --version",
                f"ollama pull {self._command_model}",
                "ollama list",
            ]
        )
        windows = (
            []
            if legacy_hardware_profile(self._hardware_from_fields()) == "cpu32"
            else ["nvidia-smi"]
        )
        windows.extend(
            ["ollama --version", f"ollama pull {self._command_model}", "ollama list"]
        )
        macos = ["ollama --version", f"ollama pull {self._command_model}", "ollama list"]
        for key, lines in (("linux", linux), ("windows", windows), ("macos", macos)):
            if key in self.command_boxes:
                self.command_boxes[key].setPlainText("\n".join(lines))

    def _detect_again(self) -> None:
        self._base_hardware = detect_hardware()
        self.ram_edit.setText(
            f"{self._base_hardware.ram_gb:.1f}" if self._base_hardware.ram_gb else ""
        )
        self.gpu_edit.setText(self._base_hardware.gpu_name)
        self.vram_edit.setText(
            f"{self._base_hardware.vram_gb:.1f}"
            if self._base_hardware.vram_gb is not None
            else ""
        )
        self._refresh_recommendation(apply_if_auto=True)
        self._refresh_commands()

    def _hardware_edited(self) -> None:
        self._refresh_recommendation(apply_if_auto=True)
        self._refresh_commands()

    def _performance_changed(self, _index: int = 0) -> None:
        self._refresh_recommendation(apply_if_auto=True)

    def _automatic_selection_changed(self, checked: bool) -> None:
        self.settings.setValue("ai/automatic_model_selection", checked)
        if checked:
            self._apply_recommendation()

    def _run_benchmark(self) -> None:
        if self._benchmark_thread and self._benchmark_thread.isRunning():
            return
        parent = self.parent()
        model_combo = getattr(parent, "ai_model_combo", None) if parent is not None else None
        model = (
            model_combo.currentText().strip()
            if model_combo is not None
            else self._command_model
        )
        if not model:
            return
        self.benchmark_button.setEnabled(False)
        self.benchmark_label.setText(_("Benchmarking {model} locally…", model=model))
        thread = BenchmarkThread(model, self)
        thread.completed.connect(self._benchmark_completed)
        thread.failed.connect(self._benchmark_failed)
        thread.finished.connect(lambda: self.benchmark_button.setEnabled(True))
        self._benchmark_thread = thread
        thread.start()

    def _benchmark_completed(self, result: BenchmarkResult) -> None:
        self.benchmark_label.setText(
            _(
                "Measured {speed:.1f} tokens/s with {model} ({tokens} generated tokens in "
                "{seconds:.1f} s). Real health analyses can be slower because prompts are larger.",
                speed=result.tokens_per_second,
                model=result.model,
                tokens=result.generated_tokens,
                seconds=result.elapsed_seconds,
            )
        )

    def _benchmark_failed(self, message: str) -> None:
        self.benchmark_label.setText(
            _(
                "Benchmark unavailable: {error}. Start Ollama and install the selected model first.",
                error=message,
            )
        )
