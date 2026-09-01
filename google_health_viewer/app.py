from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .branding import APP_NAME
from .i18n import set_language, startup_language, supported_languages
from .theme import APP_STYLESHEET

HARDWARE_AI_INTRO_KEY = "ai/hardware_ai_intro_1_1_9_seen"


def _initialize_hardware_aware_ai(settings) -> None:
    """Choose a sensible first local model without overriding an existing choice."""

    if settings.contains("ai/model"):
        return
    try:
        from .ai_hardware import detect_hardware, legacy_hardware_profile, recommend_model

        hardware = detect_hardware()
        performance = str(settings.value("ai/performance_profile", "standard") or "standard")
        recommendation = recommend_model(hardware, performance)
        settings.setValue("ai/model", recommendation.model)
        settings.setValue("ai/automatic_model_selection", True)
        settings.setValue("ai/performance_profile", performance)
        settings.setValue("ai/hardware_ram_gb", hardware.ram_gb)
        settings.setValue("ai/hardware_gpu_name", hardware.gpu_name)
        if hardware.vram_gb is not None:
            settings.setValue("ai/hardware_vram_gb", hardware.vram_gb)
        settings.setValue("ai/hardware_profile", legacy_hardware_profile(hardware))
        if hardware.ram_gb > 0:
            settings.setValue("ai/ram_gb", max(1, round(hardware.ram_gb)))
    except Exception:  # noqa: BLE001 - startup must survive incomplete hardware detection.
        return


def _should_show_hardware_ai_intro(
    settings,
    *,
    smoke_test: bool,
    had_ai_configuration: bool,
) -> bool:
    """Show the redesigned local-AI controls once to existing AI users."""

    if (
        smoke_test
        or not had_ai_configuration
        or settings.value(HARDWARE_AI_INTRO_KEY, False, type=bool)
    ):
        return False
    return True


def _show_hardware_ai_intro(window, settings) -> None:
    """Expose the hardware-aware controls once, then leave them user-invoked."""

    settings.setValue(HARDWARE_AI_INTRO_KEY, True)
    window.show_ai_setup()


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QCoreApplication.setOrganizationName("SebastianoRomi")
    # Keep the historical application identifier to preserve QSettings across
    # the 1.0 rebrand while exposing the new public name everywhere in the UI.
    QCoreApplication.setApplicationName("GoogleHealthViewer")
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    # Resolve the persisted language before importing the UI modules: several
    # translated labels are intentionally created at module import time.
    from PySide6.QtCore import QSettings

    settings = QSettings()
    preference = str(settings.value("interface/language", "system"))
    set_language(startup_language(preference))

    smoke_test = os.environ.get("VITALCHRONICLE_SMOKE_TEST") == "1"
    had_ai_configuration = settings.contains("ai/model") or settings.contains(
        "ai/hardware_profile"
    )
    if not smoke_test:
        _initialize_hardware_aware_ai(settings)

    from .main_window import MainWindow

    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "app_icon.svg")))
    if smoke_test and not {"en", "it"}.issubset(supported_languages()):
        print("Frozen build is missing the bundled translation catalogues.", file=sys.stderr)
        return 78
    window = MainWindow(screenshot_mode=smoke_test)
    window.show()
    if _should_show_hardware_ai_intro(
        settings,
        smoke_test=smoke_test,
        had_ai_configuration=had_ai_configuration,
    ):
        QTimer.singleShot(900, lambda: _show_hardware_ai_intro(window, settings))
    if smoke_test:
        QTimer.singleShot(1200, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
