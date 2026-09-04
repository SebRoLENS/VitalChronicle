"""Coherent local Ollama model selection for the desktop AI panel.

Installed models are always kept available as an explicit manual override.  Models
that are not installed are offered only when their known memory footprint is a good
fit for the detected machine.  The selector never silently replaces the model the
user last chose.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from .ai_hardware import HardwareInfo, detect_hardware, override_hardware
from .ai_model_catalog import is_cloud_model, model_memory_gb
from .i18n import _

MAX_SUGGESTED_MODELS = 10


def _model_key(model: str) -> str:
    value = model.strip().lower()
    return value.removesuffix(":latest")


def _same_model(left: str, right: str) -> bool:
    return _model_key(left) == _model_key(right)


def _unique_models(models: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    keys: set[str] = set()
    for raw in models:
        model = str(raw).strip()
        key = _model_key(model)
        if not model or not key or key in keys or is_cloud_model(model):
            continue
        result.append(model)
        keys.add(key)
    return tuple(result)


def usable_model_capacity_gb(hardware: HardwareInfo) -> float:
    """Return a conservative local-model memory budget for interactive use."""

    ram = max(0.0, float(hardware.ram_gb))
    if hardware.has_gpu and hardware.vram_gb:
        vram = max(0.0, float(hardware.vram_gb))
        # Match the catalogue's mixed-offload philosophy: VRAM is fully useful,
        # while only part of spare system RAM should be spent on model weights.
        return max(vram, vram + max(0.0, ram - 8.0) * 0.35)
    return max(2.5, ram - 5.0)


def optimal_model_options(
    candidates: Iterable[str],
    hardware: HardwareInfo,
    *,
    limit: int = MAX_SUGGESTED_MODELS,
) -> tuple[str, ...]:
    """Return models that are neither wastefully small nor impractically large.

    Only candidates with a known footprint are auto-suggested.  Unknown or custom
    models remain available as soon as the user installs them in Ollama.
    """

    capacity = usable_model_capacity_gb(hardware)
    lower = max(2.5, capacity * 0.30)
    upper = max(lower, capacity * 0.90)
    target = capacity * 0.62

    ranked: list[tuple[float, float, str]] = []
    fallback: list[tuple[float, float, str]] = []
    for model in _unique_models(candidates):
        size = model_memory_gb(model)
        if size is None or size <= 0:
            continue
        if size <= capacity:
            fallback.append((abs(size - target), -size, model))
        if lower <= size <= upper:
            ranked.append((abs(size - target), -size, model))

    selected = ranked or fallback
    selected.sort()
    return tuple(item[2] for item in selected[: max(1, int(limit))])


def ordered_model_choices(
    *,
    installed: Iterable[str],
    catalog: Iterable[str],
    hardware: HardwareInfo,
    last_used: str = "",
) -> tuple[str, ...]:
    """Put installed models first, then the last-used fallback, then optimal suggestions."""

    installed_models = list(_unique_models(installed))
    suggestions = list(optimal_model_options(catalog, hardware))
    result = list(installed_models)

    last = last_used.strip()
    if last and not is_cloud_model(last) and not any(_same_model(last, item) for item in result):
        # Preserve the user's last choice even if it has since been removed from
        # Ollama.  Status will clearly report that it is no longer installed.
        result.append(last)

    for model in suggestions:
        if not any(_same_model(model, item) for item in result):
            result.append(model)
    return tuple(result)


def _float_setting(settings: QSettings, key: str, default: float | None) -> float | None:
    value = settings.value(key, None)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def hardware_from_settings(settings: QSettings) -> HardwareInfo:
    """Use persisted manual overrides, falling back to fresh local detection."""

    detected = detect_hardware()
    ram = _float_setting(settings, "ai/hardware_ram_gb", detected.ram_gb)
    vram = _float_setting(settings, "ai/hardware_vram_gb", detected.vram_gb)
    gpu_value = settings.value("ai/hardware_gpu_name", None)
    gpu_name = detected.gpu_name if gpu_value in (None, "") else str(gpu_value)
    return override_hardware(
        detected,
        ram_gb=ram,
        gpu_name=gpu_name,
        vram_gb=vram,
    )


def _rebuild_combo(window, status) -> None:
    combo = window.ai_model_combo
    last_used = str(window.settings.value("ai/model", combo.currentText()) or combo.currentText())
    hardware = hardware_from_settings(window.settings)
    choices = ordered_model_choices(
        installed=status.models,
        catalog=status.catalog_models,
        hardware=hardware,
        last_used=last_used,
    )
    installed_keys = {_model_key(model) for model in status.models}

    combo.blockSignals(True)
    combo.clear()
    installed_count = 0
    for model in choices:
        if _model_key(model) in installed_keys:
            combo.addItem(model)
            installed_count += 1
    remaining = [model for model in choices if _model_key(model) not in installed_keys]
    if installed_count and remaining:
        combo.insertSeparator(combo.count())
    combo.addItems(remaining)

    selected_index = combo.findText(last_used)
    if selected_index < 0:
        for index in range(combo.count()):
            if _same_model(combo.itemText(index), last_used):
                selected_index = index
                break
    if selected_index >= 0:
        combo.setCurrentIndex(selected_index)
    elif combo.count():
        combo.setCurrentIndex(0)
    combo.blockSignals(False)

    window._known_ai_models = set(status.models)
    window._refresh_ai_model_styles()
    selected = combo.currentText().strip()
    if selected:
        window._update_ai_model_hint(selected)
        window.pull_button.setEnabled(not window._ai_model_is_installed(selected))


def install_ai_model_selector(main_window_module) -> None:
    """Install the selector without duplicating the large MainWindow implementation."""

    MainWindow = main_window_module.MainWindow
    if getattr(MainWindow, "_coherent_model_selector_installed", False):
        return

    original_build_ai_page = MainWindow._build_ai_page
    original_status_ready = MainWindow._ai_status_ready
    original_model_changed = MainWindow._ai_model_changed

    def build_ai_page(self):
        page = original_build_ai_page(self)
        combo = self.ai_model_combo
        combo.setEditable(False)
        last_used = str(self.settings.value("ai/model", combo.currentText()) or combo.currentText())
        combo.blockSignals(True)
        combo.clear()
        if last_used:
            combo.addItem(last_used)
            combo.setCurrentText(last_used)
        combo.blockSignals(False)

        banner = QLabel(
            _(
                "These are open-source models that fit this computer. Installed Ollama models "
                "are shown first. To use another model, install it manually with Ollama and it "
                "will appear here."
            )
        )
        banner.setObjectName("coverageNeutral")
        banner.setWordWrap(True)
        self.ai_model_catalog_banner = banner
        parent = combo.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.addWidget(banner, 6, 0, 1, 3)
        return page

    def ai_profile_changed(self, _index: int = 0) -> None:
        # Changing the legacy hardware profile must never overwrite the user's
        # last model. A status refresh only rebuilds the compatible choices.
        profile = str(self.ai_profile_combo.currentData())
        self.settings.setValue("ai/hardware_profile", profile)
        self.check_ai_status()

    def ai_model_changed(self, model: str) -> None:
        original_model_changed(self, model)
        if model.strip() and hasattr(self, "pull_button"):
            self.pull_button.setEnabled(not self._ai_model_is_installed(model))

    def ai_status_ready(self, status) -> None:
        original_status_ready(self, status)
        if status.online:
            _rebuild_combo(self, status)

    # The hardware dialog still shows its recommendation and keeps the explicit
    # "Use recommended model" action, but merely opening/editing the dialog no
    # longer silently replaces the last-used model.
    SetupDialog = main_window_module.AISetupDialog
    original_setup_init = SetupDialog.__init__

    def setup_init(self, *args, **kwargs):
        QSettings().setValue("ai/automatic_model_selection", False)
        original_setup_init(self, *args, **kwargs)
        self.auto_model_check.setChecked(False)
        self.auto_model_check.setVisible(False)

    MainWindow._build_ai_page = build_ai_page
    MainWindow._ai_profile_changed = ai_profile_changed
    MainWindow._ai_model_changed = ai_model_changed
    MainWindow._ai_status_ready = ai_status_ready
    SetupDialog.__init__ = setup_init
    MainWindow._coherent_model_selector_installed = True
