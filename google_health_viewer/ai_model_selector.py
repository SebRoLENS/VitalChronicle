"""Coherent local Ollama model selection for the desktop AI panel.

Installed models are kept available only when their known memory footprint fits the
detected machine. Unknown custom models remain available because VitalChronicle cannot
reliably size them. The selector never proposes a known model that exceeds the safe
local memory budget and never silently replaces a compatible model the user last chose.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from .ai_hardware import HardwareInfo, detect_hardware, override_hardware
from .ai_model_catalog import is_cloud_model, model_memory_gb
from .i18n import _

MAX_SUGGESTED_MODELS = 10
MISTRAL_MODEL = "mistral-small-latest"


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


def model_fits_hardware(model: str, hardware: HardwareInfo) -> bool:
    """Return False only when a model has a known footprint above safe capacity."""

    size = model_memory_gb(model)
    if size is None or size <= 0:
        # Custom/local models with unknown size cannot be rejected safely here.
        return True
    return size <= usable_model_capacity_gb(hardware)


def optimal_model_options(
    candidates: Iterable[str],
    hardware: HardwareInfo,
    *,
    limit: int = MAX_SUGGESTED_MODELS,
) -> tuple[str, ...]:
    """Return models that are neither wastefully small nor impractically large.

    Only candidates with a known footprint are auto-suggested. Unknown or custom
    models remain available only when the user already has them locally.
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
    """Put compatible installed models first, then compatible optimal suggestions."""

    installed_models = [
        model
        for model in _unique_models(installed)
        if model_fits_hardware(model, hardware)
    ]
    suggestions = [MISTRAL_MODEL, *optimal_model_options(catalog, hardware)]
    result = list(installed_models)

    last = last_used.strip()
    if (
        last
        and not is_cloud_model(last)
        and model_fits_hardware(last, hardware)
        and not any(_same_model(last, item) for item in result)
    ):
        # Preserve a compatible previous/custom choice even if it has since been
        # removed from Ollama. Status will report that it is no longer installed.
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


def _hardware_summary(hardware: HardwareInfo) -> str:
    gpu = hardware.gpu_name or _("No dedicated GPU detected")
    vram = (
        f"{hardware.vram_gb:.1f} GB"
        if hardware.vram_gb is not None
        else _("VRAM unknown")
    )
    return (
        f"{_('RAM')}: {hardware.ram_gb:.1f} GB · "
        f"{_('GPU')}: {gpu} · {_('VRAM')}: {vram}"
    )


def _refresh_hardware_summary(window, hardware: HardwareInfo) -> None:
    label = getattr(window, "ai_hardware_summary", None)
    if label is None:
        return
    label.setText(_hardware_summary(hardware))
    label.setToolTip(
        _(
            "VitalChronicle can detect this computer automatically, recommend a local model, "
            "and keep manual controls available. No hardware information is uploaded."
        )
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

    _refresh_hardware_summary(window, hardware)
    window._known_ai_models = set(status.models)
    window._refresh_ai_model_styles()
    selected = combo.currentText().strip()
    window._update_online_model_ui(selected)
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
        hardware = hardware_from_settings(self.settings)
        initial_catalog = tuple(
            combo.itemText(index)
            for index in range(combo.count())
            if combo.itemText(index).strip()
        )
        choices = [MISTRAL_MODEL, *optimal_model_options(initial_catalog, hardware)]
        if (
            last_used
            and model_fits_hardware(last_used, hardware)
            and not any(_same_model(last_used, item) for item in choices)
        ):
            choices.insert(0, last_used)

        combo.blockSignals(True)
        combo.clear()
        combo.addItems(choices)
        if last_used:
            index = combo.findText(last_used)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._update_online_model_ui(combo.currentText())

        parent = combo.parentWidget()
        layout = parent.layout() if parent is not None else None

        # The old profile selector is an implementation detail that made the first
        # AI card unnecessarily technical. Keep it alive for backwards-compatible
        # settings, but replace it visually with the hardware VitalChronicle found.
        self.ai_profile_combo.setVisible(False)
        if parent is not None:
            for label in parent.findChildren(QLabel):
                if label.text() == _("Hardware profile"):
                    label.setVisible(False)
                    break

        hardware_summary = QLabel()
        hardware_summary.setObjectName("pageSubtitle")
        hardware_summary.setWordWrap(True)
        self.ai_hardware_summary = hardware_summary
        _refresh_hardware_summary(self, hardware)

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
        if layout is not None:
            layout.addWidget(hardware_summary, 1, 0, 1, 3)
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
        self._update_online_model_ui(model)

    def ai_status_ready(self, status) -> None:
        original_status_ready(self, status)
        if status.online:
            _rebuild_combo(self, status)

    # The hardware dialog still shows its recommendation and keeps the explicit
    # "Use recommended model" action, but merely opening/editing the dialog no
    # longer silently replaces the last-used model.
    SetupDialog = main_window_module.AISetupDialog
    original_setup_init = SetupDialog.__init__
    original_setup_sync_parent = SetupDialog._sync_parent

    def setup_init(self, *args, **kwargs):
        QSettings().setValue("ai/automatic_model_selection", False)
        original_setup_init(self, *args, **kwargs)
        self.auto_model_check.setChecked(False)
        self.auto_model_check.setVisible(False)

    def setup_sync_parent(self, model: str, hardware: HardwareInfo) -> None:
        # Explicitly choosing a hardware recommendation is still allowed. Since
        # the main combo is deliberately non-editable, make sure that explicit
        # choice exists in the list before the legacy dialog selects it.
        parent = self.parent()
        combo = getattr(parent, "ai_model_combo", None) if parent is not None else None
        if combo is not None and combo.findText(model) < 0:
            combo.addItem(model)
        original_setup_sync_parent(self, model, hardware)

    MainWindow._build_ai_page = build_ai_page
    MainWindow._ai_profile_changed = ai_profile_changed
    MainWindow._ai_model_changed = ai_model_changed
    MainWindow._ai_status_ready = ai_status_ready
    SetupDialog.__init__ = setup_init
    SetupDialog._sync_parent = setup_sync_parent
    MainWindow._coherent_model_selector_installed = True
