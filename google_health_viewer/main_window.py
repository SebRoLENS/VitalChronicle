from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QDate, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIntValidator, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .ai_setup import AISetupDialog
from .analysis import (
    available_metrics,
    build_daily_progress_snapshot,
    build_health_snapshot,
    categorical_daily_points,
    display_points,
    format_value,
    friendly_metric_name,
    heart_rate_zone_thresholds,
    initial_x_range,
    meaningful_record_count,
    raw_points,
    rolling_mean,
    sleep_stage_points,
    summarize_series,
    visual_profile,
    y_axis_range,
)
from .branding import (
    APP_NAME,
    APP_TAGLINE,
    ISSUES_URL,
    MANUAL_URL,
    REPOSITORY_URL,
    SUPPORT_URL,
)
from .constants import DATA_TYPE_BY_KEY, DATA_TYPES, SCOPE_GROUPS
from .dashboard import OverviewPage
from .external_links import open_external_url
from .i18n import (
    SYSTEM_LANGUAGE,
    _,
    current_language,
    language_name,
    startup_language,
    supported_languages,
)
from .local_ai import (
    HARDWARE_PROFILE_LABELS,
    MODEL_DESCRIPTIONS,
    MODEL_OPTIONS,
    LocalAIError,
    OllamaClient,
    OllamaStatus,
    detected_hardware_profile,
    recommended_model,
)
from .oauth import CredentialStore
from .setup_wizard import AuthorizationHelpDialog, SetupWizard
from .storage import HealthStore
from .updates import ReleaseInfo, notification_due, semantic_version, update_kind
from .utils import summarize
from .workers import (
    AIAnalysisThread,
    AIPullThread,
    AIStatusThread,
    AuthThread,
    SyncThread,
    UpdateCheckThread,
)

DOCS_URL = "https://developers.google.com/health"
RESOURCE_LABELS = {
    "identity": _("Google Health identity"),
    "profile": _("Profile"),
    "settings": _("Settings and units"),
    "paired-devices": _("Paired devices"),
}


class DateAxis(pg.DateAxisItem):
    pass


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        store: HealthStore | None = None,
        screenshot_mode: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 920)
        self.setMinimumSize(1050, 700)
        self._screenshot_mode = screenshot_mode
        self.store = store or HealthStore()
        self.credential_store = CredentialStore()
        self.credentials = (
            None if screenshot_mode else self.credential_store.load_credentials()
        )
        self.settings = QSettings()
        self.current_type: str | None = None
        self.current_records: list[dict] = []
        self.current_snapshot: dict = {"metrics": [], "correlations": []}
        self.auth_thread: AuthThread | None = None
        self.sync_thread: SyncThread | None = None
        self.ai_status_thread: AIStatusThread | None = None
        self.ai_pull_thread: AIPullThread | None = None
        self.ai_analysis_thread: AIAnalysisThread | None = None
        self.update_check_thread: UpdateCheckThread | None = None
        self.progress_dialog: QProgressDialog | None = None
        self._authorization_dialog: AuthorizationHelpDialog | None = None
        self.sync_warnings: list[tuple[str, str]] = []
        self._plot_scale_points: list[tuple[float, float]] = []
        self._plot_profile = None
        self._overview_timer = QTimer(self)
        self._overview_timer.setSingleShot(True)
        self._overview_timer.timeout.connect(self.refresh_overview)
        self._plot_y_timer = QTimer(self)
        self._plot_y_timer.setSingleShot(True)
        self._plot_y_timer.timeout.connect(self._update_visible_y_range)
        self._auto_sync_timer = QTimer(self)
        self._auto_sync_timer.setInterval(10 * 60 * 1000)
        self._auto_sync_timer.timeout.connect(self._start_automatic_sync)
        self._model_update_timer = QTimer(self)
        self._model_update_timer.setInterval(6 * 60 * 60 * 1000)
        self._model_update_timer.timeout.connect(self.check_ai_status)
        self._app_update_timer = QTimer(self)
        self._app_update_timer.setInterval(60 * 60 * 1000)
        self._app_update_timer.timeout.connect(self._automatic_update_check)
        self._pending_model_update: str | None = None
        self._known_ai_models: set[str] = set()
        self._applying_range_preset = False
        self._build_ui()
        self.refresh_tree()
        self.refresh_overview()
        self._update_connection_status()
        if screenshot_mode:
            self.status_label.setText(_("● Demo mode · local synthetic data"))
            self.status_label.setStyleSheet(
                "color: #188038; font-weight: 700; padding-left: 14px;"
            )
        else:
            self._auto_sync_timer.start()
            self._model_update_timer.start()
            self._app_update_timer.start()
            QTimer.singleShot(600, self.check_ai_status)
            QTimer.singleShot(2500, self._automatic_update_check)
            if self.credentials:
                QTimer.singleShot(1200, self._start_automatic_sync)
            if not self.credential_store.has_client():
                QTimer.singleShot(250, self.show_setup_wizard)

    def _build_ui(self) -> None:
        toolbar = QToolBar(_("Main actions"))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        setup_action = QAction(_("Google setup"), self)
        setup_action.triggered.connect(self.show_setup_wizard)
        self.auth_action = QAction(_("Sign in with Google"), self)
        self.auth_action.triggered.connect(self.authenticate_existing)
        self.sync_action = QAction(_("Download / update"), self)
        self.sync_action.triggered.connect(lambda _checked=False: self.start_sync())
        export_action = QAction(_("Export archive"), self)
        export_action.triggered.connect(self.export_archive)
        docs_action = QAction(_("API documentation"), self)
        docs_action.triggered.connect(lambda: self.open_url(DOCS_URL))
        issue_toolbar_action = QAction("Report an issue", self)
        issue_toolbar_action.triggered.connect(lambda: self.open_url(ISSUES_URL))
        support_action = QAction(_("☕ Support development"), self)
        support_action.triggered.connect(lambda: self.open_url(SUPPORT_URL))
        toolbar.addAction(setup_action)
        toolbar.addAction(self.auth_action)
        toolbar.addAction(self.sync_action)
        toolbar.addSeparator()
        toolbar.addAction(export_action)
        toolbar.addAction(docs_action)
        toolbar.addAction(issue_toolbar_action)
        toolbar.addSeparator()
        toolbar.addAction(support_action)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 12, 18, 12)
        header = QHBoxLayout()
        app_title = QLabel(APP_NAME)
        app_title.setObjectName("appTitle")
        header.addWidget(app_title)
        self.status_label = QLabel()
        header.addWidget(self.status_label, 1)
        header.addWidget(QLabel(_("Period")))
        self.range_combo = QComboBox()
        for label, key in (
            (_("Today"), "today"),
            (_("Last 7 days"), "seven_days"),
            (_("Last month"), "month"),
            (_("Last year"), "year"),
            (_("All"), "all"),
            (_("Custom"), "custom"),
        ):
            self.range_combo.addItem(label, key)
        self.range_combo.setCurrentIndex(self.range_combo.findData("month"))
        header.addWidget(self.range_combo)
        header.addWidget(QLabel(_("From")))
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDate(QDate.currentDate().addMonths(-1))
        self.start_date.dateChanged.connect(self._date_range_changed)
        header.addWidget(self.start_date)
        header.addWidget(QLabel(_("to")))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDate(QDate.currentDate())
        self.end_date.dateChanged.connect(self._date_range_changed)
        header.addWidget(self.end_date)
        self.range_combo.currentIndexChanged.connect(self._apply_range_preset)
        root_layout.addLayout(header)

        self.tabs = QTabWidget()
        self.overview = OverviewPage()
        self.tabs.addTab(self.overview, _("Overview"))
        self.tabs.addTab(self._build_explorer_page(), _("Explore data"))
        self.tabs.addTab(self._build_ai_page(), _("Local AI analysis"))
        self.tabs.addTab(self._build_ai_settings_page(), _("AI settings"))
        root_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        settings_menu = self.menuBar().addMenu(_("Settings"))
        language_menu = settings_menu.addMenu(_("Language"))
        self._language_action_group = QActionGroup(self)
        self._language_action_group.setExclusive(True)
        saved_language = str(
            self.settings.value("interface/language", SYSTEM_LANGUAGE)
        ).lower()
        if saved_language != SYSTEM_LANGUAGE and saved_language not in supported_languages():
            saved_language = SYSTEM_LANGUAGE
        system_label = _(
            "System default ({language})",
            language=language_name(startup_language(SYSTEM_LANGUAGE)),
        )
        for label, code in (
            (system_label, SYSTEM_LANGUAGE),
            *((language_name(code), code) for code in supported_languages()),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(code == saved_language)
            action.triggered.connect(
                lambda _checked=False, selected=code: self._select_interface_language(selected)
            )
            self._language_action_group.addAction(action)
            language_menu.addAction(action)
        settings_menu.addSeparator()

        privacy = QAction(_("Delete local data and access…"), self)
        privacy.triggered.connect(self.clear_local_data)
        settings_menu.addAction(privacy)

        help_menu = self.menuBar().addMenu(_("Help"))
        manual_action = QAction(_("User manual"), self)
        manual_action.triggered.connect(lambda: self.open_url(MANUAL_URL))
        repository_action = QAction("Repository GitHub", self)
        repository_action.triggered.connect(lambda: self.open_url(REPOSITORY_URL))
        issue_action = QAction(_("Report an issue"), self)
        issue_action.triggered.connect(lambda: self.open_url(ISSUES_URL))
        update_action = QAction(_("Check for updates"), self)
        update_action.triggered.connect(
            lambda _checked=False: self.check_for_updates(force=True)
        )
        coffee_action = QAction("☕ Buy me a coffee", self)
        coffee_action.triggered.connect(lambda: self.open_url(SUPPORT_URL))
        about_action = QAction(_("About {app_name}", app_name=APP_NAME), self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(manual_action)
        help_menu.addAction(repository_action)
        help_menu.addAction(update_action)
        help_menu.addAction(issue_action)
        help_menu.addSeparator()
        help_menu.addAction(coffee_action)
        help_menu.addAction(about_action)

    def _automatic_update_check(self) -> None:
        self.check_for_updates(force=False)

    def check_for_updates(self, *, force: bool = False) -> None:
        if self.update_check_thread is not None:
            if force:
                self.statusBar().showMessage(_("An update check is already in progress."), 5000)
            return
        now = time.time()
        try:
            last_check = float(self.settings.value("updates/last_check", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_check = 0.0
        if not force and now - last_check < 24 * 60 * 60:
            return

        self.settings.setValue("updates/last_check", now)
        if force:
            self.statusBar().showMessage(_("Checking for VitalChronicle updates…"))
        thread = UpdateCheckThread(__version__)
        thread.completed.connect(
            lambda release, requested=force: self._update_check_completed(release, requested)
        )
        thread.failed.connect(
            lambda message, requested=force: self._update_check_failed(message, requested)
        )
        thread.finished.connect(self._update_check_finished)
        self.update_check_thread = thread
        thread.start()

    def _update_check_finished(self) -> None:
        thread = self.update_check_thread
        self.update_check_thread = None
        if thread is not None:
            thread.deleteLater()

    def _update_check_failed(self, message: str, force: bool) -> None:
        if not force:
            return
        self.statusBar().clearMessage()
        QMessageBox.warning(
            self,
            _("Check for updates"),
            _("Could not check for VitalChronicle updates: {message}", message=message),
        )

    def _update_check_completed(self, release: ReleaseInfo, force: bool) -> None:
        self.statusBar().clearMessage()
        current = semantic_version(__version__)
        latest = semantic_version(release.version)
        if current is None or latest is None:
            self._update_check_failed(
                _("GitHub returned an unrecognised release version."), force
            )
            return
        if latest <= current:
            self.settings.remove("updates/last_notified_version")
            self.settings.remove("updates/last_notified_at")
            if force:
                QMessageBox.information(
                    self,
                    _("Check for updates"),
                    _(
                        "VitalChronicle is up to date. Installed version: {version}",
                        version=__version__,
                    ),
                )
            return

        now = time.time()
        previous_version = str(
            self.settings.value("updates/last_notified_version", "") or ""
        )
        try:
            previous_at = float(
                self.settings.value("updates/last_notified_at", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            previous_at = 0.0
        if not force and not notification_due(
            release.version, previous_version, previous_at, now
        ):
            return

        self._show_update_available(
            release,
            update_kind(current, latest),
            reminder=not force and previous_version == release.version,
        )
        self.settings.setValue("updates/last_notified_version", release.version)
        self.settings.setValue("updates/last_notified_at", now)

    def _show_update_available(
        self, release: ReleaseInfo, kind: str, *, reminder: bool
    ) -> None:
        descriptions = {
            "patch": _("A maintenance update is available with bug fixes."),
            "minor": _("A feature update is available with new functionality."),
            "major": _("A new major version of VitalChronicle is available."),
        }
        prefix = _("Reminder: ") if reminder else ""
        message = prefix + _(
            "VitalChronicle {version} is available. {description}\n\n"
            "Keeping the app up to date is recommended for the latest fixes, reliability "
            "improvements, and compatibility updates.",
            version=release.version,
            description=descriptions[kind],
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(_("VitalChronicle update available"))
        box.setText(message)
        open_button = box.addButton(
            _("Open release page"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(_("Later"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            self.open_url(release.url)

    def _select_interface_language(self, preference: str) -> None:
        self.settings.setValue("interface/language", preference)
        selected = startup_language(preference)
        if selected == current_language():
            return
        QMessageBox.information(
            self,
            _("Restart required"),
            _(
                "Language preference saved. Restart VitalChronicle to use {language}.",
                language=language_name(selected),
            ),
        )

    def open_url(self, url: str) -> None:
        if open_external_url(url):
            return
        QMessageBox.warning(
            self,
            _("Could not open the browser"),
            _("VitalChronicle could not start the default browser.\n\n"
              "Open this address manually:\n{url}", url=url),
        )

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(_("About {app_name}", app_name=APP_NAME))
        box.setIconPixmap(self.windowIcon().pixmap(72, 72))
        box.setTextFormat(Qt.RichText)
        box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        box.setText(
            f"<h2>{APP_NAME}</h2>"
            f"<p>{APP_TAGLINE}</p>"
            + _("<p>A private local dashboard for viewing Google Health data and analysing "
              "it with Ollama models running on your computer.</p>"
              "<p><b>Free and open-source software.</b> A voluntary contribution helps "
              "keep development active.</p>")
            + f"<p><a href='{SUPPORT_URL}'>Buy me a coffee</a> · "
            f"<a href='{REPOSITORY_URL}'>GitHub</a></p>"
            + _("<p>Author: Sebastiano Romi · sebastiano.romi@gmail.com</p>")
        )
        box.exec()

    def _build_explorer_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 14, 0, 0)
        splitter = QSplitter(Qt.Horizontal)

        navigation = QWidget()
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        self.show_empty_check = QCheckBox(_("Show categories without data"))
        self.show_empty_check.setChecked(
            str(self.settings.value("view/show_empty", "false")).lower() == "true"
        )
        self.show_empty_check.toggled.connect(self._show_empty_changed)
        navigation_layout.addWidget(self.show_empty_check)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Data type"), _("Records")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumWidth(290)
        self.tree.itemSelectionChanged.connect(self._tree_selection_changed)
        navigation_layout.addWidget(self.tree, 1)
        splitter.addWidget(navigation)

        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(14, 0, 14, 0)
        chart_header = QHBoxLayout()
        self.chart_title = QLabel(_("Select a data type"))
        self.chart_title.setObjectName("pageTitle")
        chart_header.addWidget(self.chart_title, 1)
        self.metric_combo = QComboBox()
        self.metric_combo.setMinimumWidth(260)
        self.metric_combo.currentIndexChanged.connect(self.update_plot)
        chart_header.addWidget(self.metric_combo)
        self.scale_combo = QComboBox()
        self.scale_combo.addItems([_("Readable Y scale"), _("All Y values")])
        self.scale_combo.setToolTip(
            _("The readable scale uses visible values so a few extremes do not compress the "
              "chart. The mouse wheel changes only the time axis.")
        )
        self.scale_combo.currentIndexChanged.connect(self._scale_mode_changed)
        chart_header.addWidget(self.scale_combo)
        self.export_csv_button = QPushButton(_("Export CSV"))
        self.export_csv_button.clicked.connect(self.export_current_csv)
        chart_header.addWidget(self.export_csv_button)
        centre_layout.addLayout(chart_header)
        self.chart_subtitle = QLabel(_("Charts adapt automatically to each metric."))
        self.chart_subtitle.setObjectName("pageSubtitle")
        centre_layout.addWidget(self.chart_subtitle)

        stats = QHBoxLayout()
        self.stat_values: list[QLabel] = []
        for title in (_("Latest value"), _("Average"), _("Range"), _("Trend")):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 9, 12, 9)
            caption = QLabel(title)
            caption.setObjectName("cardCaption")
            value = QLabel("—")
            value.setStyleSheet("font-size: 15pt; font-weight: 700;")
            self.stat_values.append(value)
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            stats.addWidget(card, 1)
        centre_layout.addLayout(stats)

        chart_card = QFrame()
        chart_card.setObjectName("chartCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(8, 8, 8, 8)
        self.plot = pg.PlotWidget(axisItems={"bottom": DateAxis(orientation="bottom")})
        self.plot.setBackground("#FFFFFF")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setLabel("bottom", _("Date and time"))
        self.plot.getAxis("left").setTextPen("#5F6368")
        self.plot.getAxis("bottom").setTextPen("#5F6368")
        self.plot.getAxis("left").setPen("#DADCE0")
        self.plot.getAxis("bottom").setPen("#DADCE0")
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.plot.getViewBox().sigXRangeChanged.connect(self._plot_x_range_changed)
        chart_layout.addWidget(self.plot)
        centre_layout.addWidget(chart_card, 3)

        self.limit_label = QLabel()
        self.limit_label.setStyleSheet("color: #9A6700")
        centre_layout.addWidget(self.limit_label)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Date/time"), _("Source"), _("Summary")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._record_selection_changed)
        centre_layout.addWidget(self.table, 2)
        splitter.addWidget(centre)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(_("Select a record to view all original fields."))
        self.details.setMinimumWidth(310)
        splitter.addWidget(self.details)
        splitter.setSizes([290, 820, 330])
        page_layout.addWidget(splitter, 1)
        return page

    def _build_ai_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 22, 24, 22)
        title = QLabel(_("Private analysis on your computer"))
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            _("Statistics build your personal baseline; Qwen turns the results into a readable "
              "explanation. Profiles are available for NVIDIA systems with 16 GB RAM and "
              "CPU-only computers with 32 GB RAM.")
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addSpacing(12)

        config = QFrame()
        config.setObjectName("aiCard")
        config_layout = QGridLayout(config)
        config_layout.setContentsMargins(16, 14, 16, 14)
        self.ai_status_label = QLabel(_("○ Checking Ollama…"))
        self.ai_status_label.setStyleSheet("font-weight: 700; color: #5F6368")
        config_layout.addWidget(self.ai_status_label, 0, 0, 1, 3)
        config_layout.addWidget(QLabel(_("Hardware profile")), 1, 0)
        self.ai_profile_combo = QComboBox()
        for profile, label in HARDWARE_PROFILE_LABELS.items():
            self.ai_profile_combo.addItem(label, profile)
        saved_profile = str(
            self.settings.value("ai/hardware_profile", detected_hardware_profile())
        )
        profile_index = self.ai_profile_combo.findData(saved_profile)
        self.ai_profile_combo.setCurrentIndex(max(0, profile_index))
        config_layout.addWidget(self.ai_profile_combo, 1, 1, 1, 2)
        config_layout.addWidget(QLabel(_("Local model")), 2, 0)
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setEditable(True)
        self.ai_model_combo.addItems(MODEL_OPTIONS)
        saved_model = str(
            self.settings.value("ai/model", recommended_model(saved_profile))
        )
        self.ai_model_combo.setCurrentText(saved_model)
        self.ai_model_combo.currentTextChanged.connect(self._ai_model_changed)
        config_layout.addWidget(self.ai_model_combo, 2, 1)
        check = QPushButton(_("Check"))
        check.clicked.connect(self.check_ai_status)
        config_layout.addWidget(check, 2, 2)
        self.ai_model_hint = QLabel()
        self.ai_model_hint.setObjectName("pageSubtitle")
        self.ai_model_hint.setWordWrap(True)
        config_layout.addWidget(self.ai_model_hint, 3, 1, 1, 2)
        self._update_ai_model_hint(saved_model)
        self.ai_profile_combo.currentIndexChanged.connect(self._ai_profile_changed)
        setup = QPushButton(_("Local installation guide"))
        setup.clicked.connect(self.show_ai_setup)
        config_layout.addWidget(setup, 4, 0)
        self.pull_button = QPushButton(_("Download model"))
        self.pull_button.clicked.connect(self.pull_ai_model)
        config_layout.addWidget(self.pull_button, 4, 1)
        self.ai_progress = QProgressBar()
        self.ai_progress.setRange(0, 1)
        self.ai_progress.setValue(0)
        self.ai_progress.setFormat(_("Waiting"))
        config_layout.addWidget(self.ai_progress, 4, 2)
        self.model_update_button = QPushButton(_("Update model"))
        self.model_update_button.clicked.connect(self._apply_model_update)
        self.model_update_button.setVisible(False)
        config_layout.addWidget(self.model_update_button, 5, 1, 1, 2)
        config_layout.setColumnStretch(1, 1)
        root.addWidget(config)

        interval_row = QHBoxLayout()
        question_label = QLabel(_("Period used for questions"))
        question_label.setStyleSheet("font-weight: 700;")
        interval_row.addWidget(question_label)
        self.ai_range_combo = QComboBox()
        self.ai_range_combo.setMinimumWidth(190)
        for index in range(self.range_combo.count()):
            self.ai_range_combo.addItem(
                self.range_combo.itemText(index), self.range_combo.itemData(index)
            )
        self.ai_range_combo.setCurrentIndex(self.range_combo.currentIndex())
        self.ai_range_combo.currentIndexChanged.connect(self._ai_range_changed)
        interval_row.addWidget(self.ai_range_combo)
        interval_row.addStretch()
        root.addLayout(interval_row)
        self.ai_interval_label = QLabel()
        self.ai_interval_label.setObjectName("aiInterval")
        self.ai_interval_label.setWordWrap(True)
        root.addWidget(self.ai_interval_label)
        self._update_ai_interval_label()
        self.ai_question = QPlainTextEdit()
        self.ai_question.setMaximumHeight(72)
        self.ai_question.setPlaceholderText(
            _("Example: does my sleep appear associated with HRV and resting heart rate?")
        )
        root.addWidget(self.ai_question)
        actions = QHBoxLayout()
        self.auto_ai_button = QPushButton(_("Analyse complete history"))
        self.auto_ai_button.setObjectName("primaryButton")
        self.auto_ai_button.clicked.connect(
            lambda: self.start_ai_analysis("", use_all_data=True)
        )
        self.ask_ai_button = QPushButton(_("Answer the question"))
        self.ask_ai_button.setObjectName("primaryButton")
        self.ask_ai_button.clicked.connect(
            lambda: self.start_ai_analysis(self.ai_question.toPlainText())
        )
        actions.addWidget(self.auto_ai_button)
        actions.addWidget(self.ask_ai_button)
        actions.addStretch()
        root.addLayout(actions)

        answer_card = QFrame()
        answer_card.setObjectName("answerCard")
        answer_layout = QVBoxLayout(answer_card)
        answer_layout.setContentsMargins(14, 10, 14, 10)
        answer_header = QHBoxLayout()
        self.ai_result_title = QLabel(_("Local analysis"))
        self.ai_result_title.setStyleSheet("font-weight: 700;")
        self.ai_live_badge = QLabel("LIVE")
        self.ai_live_badge.setObjectName("thinkingLive")
        self.ai_live_badge.setVisible(False)
        answer_header.addWidget(self.ai_result_title)
        answer_header.addStretch()
        answer_header.addWidget(self.ai_live_badge)
        answer_layout.addLayout(answer_header)
        self.ai_output = QTextBrowser()
        self.ai_output.setOpenExternalLinks(True)
        self.ai_output.setPlaceholderText(
            _("Thinking appears here during processing and is then replaced by the final "
              "answer. No data is sent to an online AI service.")
        )
        answer_layout.addWidget(self.ai_output, 1)
        root.addWidget(answer_card, 1)
        disclaimer = QLabel(
            _("Exploratory tool: correlations and anomalies do not establish causes or diagnoses. "
              "Use validated measurements and professional advice for health decisions.")
        )
        disclaimer.setObjectName("disclaimer")
        disclaimer.setWordWrap(True)
        root.addWidget(disclaimer)
        return page

    def _build_ai_settings_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 22, 24, 22)
        title = QLabel(_("AI settings"))
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            _("Enter the computer's RAM. The recommendation considers the selected model and "
              "its physical context limit reported by Ollama.")
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addSpacing(12)

        card = QFrame()
        card.setObjectName("aiCard")
        layout = QGridLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(QLabel(_("Installed RAM")), 0, 0)
        self.ai_ram_edit = QLineEdit()
        self.ai_ram_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        default_ram = 32 if self.ai_profile_combo.currentData() == "cpu32" else 16
        self.ai_ram_edit.setText(
            str(self.settings.value("ai/ram_gb", default_ram, type=int))
        )
        self.ai_ram_edit.setPlaceholderText(_("Example: 16"))
        self.ai_ram_edit.setToolTip(_("Total installed RAM in GB."))
        self.ai_ram_edit.editingFinished.connect(self._save_ai_token_settings)
        layout.addWidget(self.ai_ram_edit, 0, 1)
        layout.addWidget(QLabel("GB"), 0, 2)

        layout.addWidget(QLabel(_("Maximum tokens")), 1, 0)
        self.ai_token_edit = QLineEdit()
        self._model_token_limit: int | None = None
        self.ai_token_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.ai_token_edit.setText(
            str(self.settings.value("ai/max_generation_tokens", 3200, type=int))
        )
        self.ai_token_edit.setToolTip(
            _("Thinking and the answer share this limit. You can edit the recommendation.")
        )
        self.ai_token_edit.editingFinished.connect(self._save_ai_token_settings)
        layout.addWidget(self.ai_token_edit, 1, 1, 1, 2)

        recommend = QPushButton(_("Recommend from RAM"))
        recommend.setObjectName("primaryButton")
        recommend.clicked.connect(self._recommend_ai_tokens)
        layout.addWidget(recommend, 2, 0, 1, 3)
        self.ai_token_recommendation = QLabel(
            _("The value remains editable. After calculation, the only maximum applied is the "
              "physical context reported by the model.")
        )
        self.ai_token_recommendation.setObjectName("pageSubtitle")
        self.ai_token_recommendation.setWordWrap(True)
        layout.addWidget(self.ai_token_recommendation, 3, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        root.addWidget(card)
        root.addStretch()
        return page

    def _date_bounds(self) -> tuple[str, str]:
        start = self._qdate_to_date(self.start_date.date()).isoformat()
        end = (self._qdate_to_date(self.end_date.date()) + timedelta(days=1)).isoformat()
        return start, end

    def _apply_range_preset(self) -> None:
        preset = self.range_combo.currentData()
        if preset == "custom":
            self._sync_ai_range_combo()
            self._update_ai_interval_label()
            return
        today = QDate.currentDate()
        if preset == "today":
            start = today
        elif preset == "seven_days":
            start = today.addDays(-6)
        elif preset == "year":
            start = today.addYears(-1)
        elif preset == "all":
            bounds = self.store.data_date_bounds()
            start = (
                QDate(bounds[0].year, bounds[0].month, bounds[0].day)
                if bounds
                else today.addYears(-1)
            )
            if bounds:
                today = QDate(bounds[1].year, bounds[1].month, bounds[1].day)
        else:
            start = today.addMonths(-1)

        self._applying_range_preset = True
        self.start_date.setDate(start)
        self.end_date.setDate(today)
        self._applying_range_preset = False
        self._sync_ai_range_combo()
        self._update_ai_interval_label()
        self._reload_current_type()
        self._overview_timer.start(180)

    def _update_ai_interval_label(self) -> None:
        if not hasattr(self, "ai_interval_label"):
            return
        label = self.range_combo.currentText()
        start = self.start_date.date().toString("dd/MM/yyyy")
        end = self.end_date.date().toString("dd/MM/yyyy")
        self.ai_interval_label.setText(
            _("Questions use: {period} · {start}–{end}. Analyse complete history ignores this "
              "time filter.", period=label, start=start, end=end)
        )

    def _sync_ai_range_combo(self) -> None:
        if not hasattr(self, "ai_range_combo"):
            return
        index = self.ai_range_combo.findData(self.range_combo.currentData())
        self.ai_range_combo.blockSignals(True)
        self.ai_range_combo.setCurrentIndex(max(0, index))
        self.ai_range_combo.blockSignals(False)

    def _ai_range_changed(self) -> None:
        index = self.range_combo.findData(self.ai_range_combo.currentData())
        if index >= 0 and index != self.range_combo.currentIndex():
            self.range_combo.setCurrentIndex(index)

    def _save_ai_token_settings(self) -> None:
        try:
            ram_gb = int(self.ai_ram_edit.text())
            if ram_gb > 0:
                self.settings.setValue("ai/ram_gb", ram_gb)
        except ValueError:
            pass
        try:
            tokens = int(self.ai_token_edit.text())
            if tokens > 0:
                if self._model_token_limit is not None:
                    tokens = min(tokens, self._model_token_limit)
                    self.ai_token_edit.setText(str(tokens))
                self.settings.setValue("ai/max_generation_tokens", tokens)
        except ValueError:
            pass

    def _recommend_ai_tokens(self) -> None:
        try:
            ram_gb = int(self.ai_ram_edit.text())
            recommendation = OllamaClient(
                model=self.ai_model_combo.currentText(),
                hardware_profile=str(self.ai_profile_combo.currentData()),
            ).token_recommendation(ram_gb)
        except (ValueError, LocalAIError) as exc:
            self.ai_token_recommendation.setText(
                _("Could not calculate a recommendation: {error}", error=exc)
            )
            return

        self._model_token_limit = recommendation.model_context_limit
        validator = self.ai_token_edit.validator()
        if isinstance(validator, QIntValidator):
            validator.setTop(
                recommendation.model_context_limit or 2_147_483_647
            )
        self.ai_token_edit.setText(str(recommendation.recommended_tokens))
        self._save_ai_token_settings()
        size_text = (
            _(" · model {size:.1f} GB", size=recommendation.model_size_gb)
            if recommendation.model_size_gb is not None
            else ""
        )
        limit_text = (
            _(" · physical limit {limit:,} tokens", limit=recommendation.model_context_limit).replace(
                ",", "."
            )
            if recommendation.model_context_limit is not None
            else _(" · physical limit not reported by the model")
        )
        self.ai_token_recommendation.setText(
            _("Recommendation: {tokens:,} tokens", tokens=recommendation.recommended_tokens)
            + size_text
            + limit_text
            + _(". You can override it; input, thinking, and the answer share the context.")
        )

    def _selected_ai_tokens(self) -> int:
        try:
            tokens = max(1, int(self.ai_token_edit.text()))
        except ValueError:
            tokens = 3200
        if self._model_token_limit is not None:
            tokens = min(tokens, self._model_token_limit)
        return tokens

    def _date_range_changed(self) -> None:
        if self._applying_range_preset:
            return
        if self.range_combo.currentData() != "custom":
            self.range_combo.blockSignals(True)
            self.range_combo.setCurrentIndex(self.range_combo.findData("custom"))
            self.range_combo.blockSignals(False)
        self._sync_ai_range_combo()
        self._update_ai_interval_label()
        self._reload_current_type()
        self._overview_timer.start(180)

    def refresh_overview(self) -> None:
        start, end = self._date_bounds()
        self.current_snapshot = build_health_snapshot(self.store, start, end)
        reference_day = self._qdate_to_date(self.end_date.date())
        progress_snapshot = build_daily_progress_snapshot(
            self.store,
            reference_day,
            heart_day=datetime.now().astimezone().date(),
        )
        self.overview.refresh(self.current_snapshot, progress_snapshot)

    def _update_connection_status(self) -> None:
        connected = self.credentials is not None
        self.status_label.setText(
            _("● Google account configured") if connected else _("○ Google account not connected")
        )
        self.status_label.setStyleSheet(
            "color: #188038; font-weight: 700; padding-left: 14px;"
            if connected
            else "color: #5F6368; padding-left: 14px;"
        )
        self.sync_action.setEnabled(connected)
        self.auth_action.setText(_("Sign in again") if connected else _("Sign in with Google"))

    def show_setup_wizard(self) -> None:
        wizard = SetupWizard(self.credential_store, self)
        wizard.configuration_ready.connect(self.start_authentication)
        wizard.open()
        self._wizard = wizard

    def authenticate_existing(self) -> None:
        if not self.credential_store.has_client():
            self.show_setup_wizard()
            return
        self.start_authentication(list(SCOPE_GROUPS.values()))

    def start_authentication(self, scopes: list[str]) -> None:
        if self.auth_thread and self.auth_thread.isRunning():
            return
        self.auth_action.setEnabled(False)
        self.status_label.setText(_("Opening the browser for sign-in…"))
        self.auth_thread = AuthThread(self.credential_store, scopes)
        self.auth_thread.succeeded.connect(self._auth_succeeded)
        self.auth_thread.failed.connect(self._auth_failed)
        self.auth_thread.url_ready.connect(self._show_authorization_help)
        self.auth_thread.start()

    def _show_authorization_help(self, url: str) -> None:
        if self._authorization_dialog is not None:
            self._authorization_dialog.close()
        self._authorization_dialog = AuthorizationHelpDialog(url, self)
        self._authorization_dialog.open()
        self.statusBar().showMessage(_("Waiting for Google sign-in…"), 300000)

    def _close_authorization_help(self) -> None:
        if self._authorization_dialog is not None:
            self._authorization_dialog.close()
            self._authorization_dialog.deleteLater()
            self._authorization_dialog = None

    def _auth_succeeded(self, credentials, secure: bool) -> None:
        self._close_authorization_help()
        self.credentials = credentials
        self.auth_action.setEnabled(True)
        self._update_connection_status()
        self.statusBar().showMessage(_("Authentication completed."), 8000)
        QTimer.singleShot(300, self._start_automatic_sync)
        if not secure:
            QMessageBox.information(
                self,
                _("Credential storage"),
                _("The system keyring was unavailable. The token was saved in a local file "
                  "that only your user can access."),
            )

    def _auth_failed(self, message: str) -> None:
        self._close_authorization_help()
        self.auth_action.setEnabled(True)
        self._update_connection_status()
        QMessageBox.critical(self, _("Authentication failed"), message)

    @staticmethod
    def _qdate_to_date(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())

    def _start_automatic_sync(self) -> None:
        self.start_sync(automatic=True)

    def start_sync(self, automatic: bool = False) -> None:
        if self.sync_thread and self.sync_thread.isRunning():
            return
        if not self.credentials:
            if not automatic:
                self.authenticate_existing()
            return
        if automatic:
            end = datetime.now().astimezone().date()
            start = end - timedelta(days=30)
        else:
            start = self._qdate_to_date(self.start_date.date())
            end = self._qdate_to_date(self.end_date.date())
        if start > end:
            if not automatic:
                QMessageBox.warning(
                    self, _("Invalid date range"), _("The start date is after the end date.")
                )
            return
        self.sync_action.setEnabled(False)
        self.sync_warnings = []
        if automatic:
            self.progress_dialog = None
            self.statusBar().showMessage(_("Incremental automatic update…"))
        else:
            sync_steps = sum(spec.auto_sync for spec in DATA_TYPES) + 1
            self.progress_dialog = QProgressDialog(
                _("Preparing…"), _("Cancel"), 0, sync_steps, self
            )
            self.progress_dialog.setWindowTitle(_("Google Health download"))
            self.progress_dialog.setMinimumDuration(0)
            self.progress_dialog.setAutoClose(False)
        self.sync_thread = SyncThread(
            self.credentials,
            self.store,
            self.credential_store,
            start,
            end,
            include_resources=(not automatic or not self.store.resources()),
        )
        if self.progress_dialog:
            self.progress_dialog.canceled.connect(self.sync_thread.cancel)
        self.sync_thread.progress.connect(self._sync_progress)
        self.sync_thread.type_done.connect(lambda *_: self.refresh_tree())
        self.sync_thread.warning.connect(self._sync_warning)
        self.sync_thread.completed.connect(
            lambda success, errors: self._sync_completed(success, errors, automatic)
        )
        self.sync_thread.failed.connect(
            lambda message: self._sync_failed(message, automatic)
        )
        self.sync_thread.start()

    def _sync_progress(self, value: int, maximum: int, label: str) -> None:
        if self.progress_dialog:
            self.progress_dialog.setMaximum(maximum)
            self.progress_dialog.setValue(value)
            self.progress_dialog.setLabelText(_("Download: {label}", label=label))
        else:
            self.statusBar().showMessage(_("Automatic update: {label}", label=label))

    def _sync_warning(self, label: str, message: str) -> None:
        self.sync_warnings.append((label, message))
        self.statusBar().showMessage(
            _("{label} skipped: the download continues with other categories.", label=label), 12000
        )

    def _sync_completed(self, success: int, errors: int, automatic: bool = False) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        self.sync_action.setEnabled(True)
        self.refresh_tree()
        self.refresh_overview()
        if self.current_type:
            self._reload_current_type()
        prefix = _("Automatic update completed") if automatic else _("Update completed")
        message = _("{prefix}: {count} categories processed", prefix=prefix, count=success)
        if errors:
            message += _(", {count} skipped with warnings", count=errors)
        self.statusBar().showMessage(message, 15000)
        if self.sync_warnings and not automatic:
            details = "\n".join(
                f"• {label}: {warning}" for label, warning in self.sync_warnings[:8]
            )
            if len(self.sync_warnings) > 8:
                details += _("\n• …and {count} more categories", count=len(self.sync_warnings) - 8)
            QMessageBox.warning(
                self,
                _("Update completed with warnings"),
                _("The following categories did not stop the download:\n\n") + details,
            )

    def _sync_failed(self, message: str, automatic: bool = False) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        self.sync_action.setEnabled(True)
        if "401" in message or "scadut" in message.lower():
            self.credentials = None
            self._update_connection_status()
        if automatic:
            self.statusBar().showMessage(
                _("Automatic update failed: {message}", message=message), 15000
            )
        else:
            QMessageBox.critical(self, _("Download failed"), message)

    def refresh_tree(self) -> None:
        selected = self.current_type
        counts = self.store.counts()
        resources = self.store.resources()
        sync_statuses = self.store.sync_statuses()
        self.tree.blockSignals(True)
        self.tree.clear()
        categories: dict[str, QTreeWidgetItem] = {}
        selected_item = None
        for spec in DATA_TYPES:
            if not spec.auto_sync:
                continue
            count = counts.get(spec.key, 0)
            if spec.key == "swim-lengths-data" and count:
                records = self.store.list_records(spec.key, limit=10000)
                count = meaningful_record_count(spec.key, records)
            if not count and not self.show_empty_check.isChecked():
                continue
            parent = categories.get(spec.category)
            if parent is None:
                parent = QTreeWidgetItem([spec.category, ""])
                parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
                categories[spec.category] = parent
                self.tree.addTopLevelItem(parent)
            item = QTreeWidgetItem([spec.label, str(count)])
            item.setData(0, Qt.UserRole, spec.key)
            status = sync_statuses.get(spec.key)
            if status:
                item.setToolTip(0, status[1])
            if not count:
                item.setForeground(0, QColor("#9AA0A6"))
            parent.addChild(item)
            if spec.key == selected:
                selected_item = item
        system_parent = QTreeWidgetItem([_("Account and devices"), ""])
        system_parent.setFlags(system_parent.flags() & ~Qt.ItemIsSelectable)
        self.tree.addTopLevelItem(system_parent)
        for key, label in RESOURCE_LABELS.items():
            item = QTreeWidgetItem([label, "1" if key in resources else "0"])
            item.setData(0, Qt.UserRole, f"resource:{key}")
            if key not in resources:
                item.setForeground(0, QColor("#9AA0A6"))
            system_parent.addChild(item)
            if f"resource:{key}" == selected:
                selected_item = item
        self.tree.expandAll()
        if selected_item:
            self.tree.setCurrentItem(selected_item)
        self.tree.blockSignals(False)

    def _show_empty_changed(self, checked: bool) -> None:
        self.settings.setValue("view/show_empty", checked)
        self.refresh_tree()

    def _tree_selection_changed(self) -> None:
        item = self.tree.currentItem()
        if not item:
            return
        key = item.data(0, Qt.UserRole)
        if key:
            value = str(key)
            if value.startswith("resource:"):
                self.load_resource(value.removeprefix("resource:"))
            else:
                self.load_data_type(value)

    def load_data_type(self, key: str) -> None:
        self.current_type = key
        self.export_csv_button.setText(_("Export CSV"))
        start, end = self._date_bounds()
        self.current_records = self.store.list_records(
            key, start, end, limit=20000, newest=True
        )
        spec = DATA_TYPE_BY_KEY[key]
        self.chart_title.setText(spec.label)
        self.table.setRowCount(min(len(self.current_records), 5000))
        for row, record in enumerate(self.current_records[:5000]):
            timestamp = record["start_time"] or record["end_time"] or "—"
            values = [timestamp, record["source"], summarize(record["payload"])]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, row)
                self.table.setItem(row, column, item)
        self.limit_label.setText(
            _("At most 5,000 records are shown in the table and 20,000 in the chart; "
              "the export includes all data.")
            if len(self.current_records) >= 5000
            else ""
        )
        self._populate_metrics()
        self.details.clear()

    def load_resource(self, key: str) -> None:
        payload = self.store.resources().get(key)
        self.current_type = f"resource:{key}"
        self.current_records = []
        self.metric_combo.clear()
        self.plot.clear()
        self.chart_title.setText(RESOURCE_LABELS.get(key, key))
        self.chart_subtitle.setText(_("Account information stored locally."))
        self.table.setRowCount(1 if payload else 0)
        self.limit_label.clear()
        self.export_csv_button.setText(_("Export JSON"))
        self._clear_stats()
        if not payload:
            self.details.setPlainText(
                _("This information is not available yet. Run a download or check the related "
                  "OAuth permission.")
            )
            return
        self.current_records = [
            {
                "record_id": key,
                "record_kind": "resource",
                "start_time": None,
                "end_time": None,
                "source": "Google Health",
                "payload": payload,
            }
        ]
        for column, value in enumerate(("—", "Google Health", summarize(payload))):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.UserRole, 0)
            self.table.setItem(0, column, item)
        self.details.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _populate_metrics(self) -> None:
        previous = self.metric_combo.currentData()
        metrics = available_metrics(self.current_records, self.current_type or "")
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for metric in metrics:
            self.metric_combo.addItem(friendly_metric_name(metric), metric)
        if previous:
            index = self.metric_combo.findData(previous)
            if index >= 0:
                self.metric_combo.setCurrentIndex(index)
        self.metric_combo.blockSignals(False)
        self.update_plot()

    def update_plot(self) -> None:
        self.plot.clear()
        self._plot_scale_points = []
        self._plot_profile = None
        if self.plot.plotItem.legend is not None:
            self.plot.plotItem.legend.clear()
            self.plot.plotItem.legend.setVisible(False)
        metric = self.metric_combo.currentData()
        if not metric or not self.current_type or self.current_type.startswith("resource:"):
            self._clear_stats()
            return
        profile = visual_profile(self.current_type, str(metric))
        points = raw_points(self.current_records, str(metric))
        shown = display_points(points, profile)
        summary = summarize_series(shown)
        if not shown or not summary:
            self._clear_stats()
            return
        x, y = zip(*shown)
        color = profile.color
        special_categories = {
            "__zone_time__",
            "__zone_calories__",
            "__activity_minutes__",
        }
        thresholds: list[tuple[float, dict[str, tuple[float, float]]]] = []
        if str(metric) == "__heart_rate_zones__":
            thresholds = heart_rate_zone_thresholds(self.current_records)
            self._plot_heart_rate_thresholds(thresholds)
        elif str(metric) in special_categories:
            categories = categorical_daily_points(self.current_records, self.current_type)
            self._plot_stacked_categories(categories)
        elif profile.chart == "bar":
            width = 0.68 * 86400
            if len(x) > 1:
                positive_gaps = [right - left for left, right in pairwise(x) if right > left]
                if positive_gaps:
                    width = min(width, min(positive_gaps) * 0.68)
            stages = sleep_stage_points(self.current_records) if self.current_type == "sleep" else []
            if stages and str(metric) == "__duration_hours__":
                self._plot_sleep_stages(stages, width)
            else:
                self.plot.addItem(
                    pg.BarGraphItem(x=x, height=y, width=width, brush=pg.mkBrush(color), pen=None)
                )
        elif profile.chart == "scatter":
            self.plot.plot(
                x,
                y,
                pen=None,
                symbol="o",
                symbolSize=5 if len(x) < 2500 else 3,
                symbolBrush=pg.mkBrush(QColor(color)),
                symbolPen=None,
            )
            smooth = rolling_mean(shown)
            if len(smooth) >= 3:
                sx, sy = zip(*smooth)
                self.plot.plot(sx, sy, pen=pg.mkPen(color, width=2.2))
        else:
            symbol = "o" if len(x) <= 400 else None
            self.plot.plot(
                x,
                y,
                pen=pg.mkPen(color, width=2.2),
                symbol=symbol,
                symbolSize=5,
                symbolBrush=pg.mkBrush(color),
                symbolPen=pg.mkPen("#FFFFFF", width=1),
            )

        if (
            str(metric) != "__heart_rate_zones__"
            and len(x) >= 5
            and summary.baseline_high > summary.baseline_low
        ):
            low_curve = self.plot.plot(
                [x[0], x[-1]], [summary.baseline_low, summary.baseline_low], pen=None
            )
            high_curve = self.plot.plot(
                [x[0], x[-1]], [summary.baseline_high, summary.baseline_high], pen=None
            )
            qcolor = QColor(color)
            self.plot.addItem(
                pg.FillBetweenItem(
                    low_curve,
                    high_curve,
                    brush=pg.mkBrush(qcolor.red(), qcolor.green(), qcolor.blue(), 25),
                )
            )
            self.plot.addItem(
                pg.InfiniteLine(
                    pos=summary.median,
                    angle=0,
                    pen=pg.mkPen(color, width=1.2, style=Qt.DashLine),
                )
            )

        self.plot.setLabel("left", friendly_metric_name(str(metric)), units=profile.unit)
        scale_points = shown
        if thresholds:
            scale_points = [
                (timestamp, boundary)
                for timestamp, zones in thresholds
                for minimum, maximum in zones.values()
                for boundary in (minimum, maximum)
            ]
        self._plot_scale_points = scale_points
        self._plot_profile = profile
        self.plot.disableAutoRange(axis="x")
        times = [timestamp for timestamp, _value in scale_points]
        if times:
            data_span = max(times) - min(times)
            margin = max(data_span * 0.04, 3600.0)
            self.plot.getViewBox().setLimits(
                xMin=min(times) - margin,
                xMax=max(times) + margin,
            )
        viewport = initial_x_range(scale_points, self.current_type, profile)
        if viewport:
            self.plot.setXRange(*viewport, padding=0)
        self._update_visible_y_range()
        if str(metric) == "__heart_rate_zones__" and thresholds:
            boundaries = [
                boundary
                for _timestamp, zones in thresholds
                for minimum, maximum in zones.values()
                for boundary in (minimum, maximum)
            ]
            self.chart_subtitle.setText(
                _("{subtitle} · each coloured area shows minimum and maximum · drag to pan",
                  subtitle=profile.subtitle)
            )
            self.stat_values[0].setText(_("{count} zones", count=len(thresholds[-1][1])))
            self.stat_values[1].setText(_("Individual thresholds"))
            self.stat_values[2].setText(f"{min(boundaries):.0f}–{max(boundaries):.0f} bpm")
            self.stat_values[3].setText(_("{count} days", count=len(thresholds)))
        else:
            self.chart_subtitle.setText(
                _("{subtitle} · {count} values · drag to pan · wheel to zoom time",
                  subtitle=profile.subtitle, count=summary.count)
            )
            self._update_stats(summary, profile.unit)

    def _scale_mode_changed(self, _index: int = 0) -> None:
        self._update_visible_y_range()

    def _plot_x_range_changed(self, *_args) -> None:
        if self._plot_scale_points:
            self._plot_y_timer.start(40)

    def _update_visible_y_range(self) -> None:
        if not self._plot_scale_points or self._plot_profile is None or not self.current_type:
            return
        left, right = self.plot.getViewBox().viewRange()[0]
        visible = [
            point for point in self._plot_scale_points if left <= point[0] <= right
        ]
        if not visible:
            return
        axis_range = y_axis_range(
            visible,
            self.current_type,
            self._plot_profile,
            show_all=self.scale_combo.currentIndex() == 1,
        )
        if axis_range:
            self.plot.setYRange(*axis_range, padding=0)

    @staticmethod
    def _category_style(category: str) -> tuple[str, str]:
        styles = {
            "LIGHT": (_("Light"), "#64B5F6"),
            "MODERATE": (_("Moderate"), "#66BB6A"),
            "VIGOROUS": (_("Vigorous"), "#FFB74D"),
            "PEAK": (_("Peak"), "#EF5350"),
            "FAT_BURN": (_("Fat burn"), "#FBC02D"),
            "CARDIO": ("Cardio", "#FB8C00"),
        }
        return styles.get(category, (category.replace("_", " ").title(), "#90A4AE"))

    def _prepare_legend(self) -> None:
        if self.plot.plotItem.legend is None:
            self.plot.addLegend(offset=(10, 10))
        else:
            self.plot.plotItem.legend.clear()
        self.plot.plotItem.legend.setVisible(True)

    def _plot_stacked_categories(
        self, series: list[tuple[float, dict[str, float]]]
    ) -> None:
        if not series:
            return
        self._prepare_legend()
        x_values = [timestamp for timestamp, _values in series]
        width = 0.68 * 86400
        bottoms = [0.0] * len(series)
        categories = sorted({key for _timestamp, values in series for key in values})
        preferred = ["LIGHT", "FAT_BURN", "MODERATE", "CARDIO", "VIGOROUS", "PEAK"]
        categories.sort(key=lambda key: preferred.index(key) if key in preferred else 99)
        for category in categories:
            values = [items.get(category, 0.0) for _timestamp, items in series]
            if not any(values):
                continue
            label, color = self._category_style(category)
            item = pg.BarGraphItem(
                x=x_values,
                y0=bottoms,
                height=values,
                width=width,
                brush=pg.mkBrush(color),
                pen=pg.mkPen("#FFFFFF", width=0.8),
            )
            self.plot.addItem(item)
            self.plot.plotItem.legend.addItem(item, label)
            bottoms = [base + value for base, value in zip(bottoms, values, strict=True)]

    def _plot_heart_rate_thresholds(
        self, thresholds: list[tuple[float, dict[str, tuple[float, float]]]]
    ) -> None:
        if not thresholds:
            return
        self._prepare_legend()
        for category in ("LIGHT", "MODERATE", "VIGOROUS", "PEAK"):
            values = [
                (timestamp, zones[category])
                for timestamp, zones in thresholds
                if category in zones
            ]
            if not values:
                continue
            x_values = [item[0] for item in values]
            minimum = [item[1][0] for item in values]
            maximum = [item[1][1] for item in values]
            label, color = self._category_style(category)
            low_curve = self.plot.plot(x_values, minimum, pen=pg.mkPen(color, width=1))
            high_curve = self.plot.plot(x_values, maximum, pen=pg.mkPen(color, width=2))
            qcolor = QColor(color)
            self.plot.addItem(
                pg.FillBetweenItem(
                    low_curve,
                    high_curve,
                    brush=pg.mkBrush(qcolor.red(), qcolor.green(), qcolor.blue(), 32),
                )
            )
            self.plot.plotItem.legend.addItem(high_curve, label)

    def _plot_sleep_stages(
        self, stages: list[tuple[float, dict[str, float]]], width: float
    ) -> None:
        palette = {
            "DEEP": (_("Deep"), "#3F51B5"),
            "REM": ("REM", "#AB47BC"),
            "LIGHT": (_("Light"), "#7986CB"),
            "ASLEEP": (_("Asleep"), "#5C6BC0"),
            "AWAKE": (_("Awake"), "#FFB74D"),
            "RESTLESS": (_("Restless"), "#FFCA28"),
        }
        if self.plot.plotItem.legend is None:
            self.plot.addLegend(offset=(10, 10))
        else:
            self.plot.plotItem.legend.clear()
        self.plot.plotItem.legend.setVisible(True)
        x_values = [timestamp for timestamp, _totals in stages]
        bottoms = [0.0] * len(stages)
        for stage_type in ("DEEP", "REM", "LIGHT", "ASLEEP", "AWAKE", "RESTLESS"):
            values = [totals.get(stage_type, 0.0) for _timestamp, totals in stages]
            if not any(values):
                continue
            label, color = palette[stage_type]
            item = pg.BarGraphItem(
                x=x_values,
                y0=bottoms,
                height=values,
                width=width,
                brush=pg.mkBrush(color),
                pen=pg.mkPen("#FFFFFF", width=0.8),
            )
            self.plot.addItem(item)
            self.plot.plotItem.legend.addItem(item, label)
            bottoms = [base + value for base, value in zip(bottoms, values, strict=True)]

    def _update_stats(self, summary, unit: str) -> None:
        self.stat_values[0].setText(format_value(summary.latest, unit))
        self.stat_values[1].setText(format_value(summary.mean, unit))
        self.stat_values[2].setText(
            f"{format_value(summary.minimum, unit)} – {format_value(summary.maximum, unit)}"
        )
        if summary.trend_percent is None:
            trend = "—"
        else:
            arrow = "↑" if summary.trend_percent > 0 else ("↓" if summary.trend_percent < 0 else "→")
            trend = f"{arrow} {abs(summary.trend_percent):.1f}%"
        if summary.anomaly_count:
            trend += _(" · {count} anomalies", count=summary.anomaly_count)
        self.stat_values[3].setText(trend)

    def _clear_stats(self) -> None:
        for label in self.stat_values:
            label.setText("—")

    def _record_selection_changed(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        marker = self.table.item(row, 0)
        index = marker.data(Qt.UserRole) if marker else row
        if index is not None and int(index) < len(self.current_records):
            payload = self.current_records[int(index)]["payload"]
            self.details.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def show_ai_setup(self) -> None:
        AISetupDialog(
            self.ai_model_combo.currentText(),
            profile=str(self.ai_profile_combo.currentData()),
            parent=self,
        ).exec()

    def _ai_profile_changed(self, _index: int = 0) -> None:
        profile = str(self.ai_profile_combo.currentData())
        self.settings.setValue("ai/hardware_profile", profile)
        self.ai_model_combo.setCurrentText(recommended_model(profile))
        self.check_ai_status()

    def _ai_model_changed(self, model: str) -> None:
        self.settings.setValue("ai/model", model)
        self._update_ai_model_hint(model)
        self._pending_model_update = None
        self.model_update_button.setVisible(False)
        if hasattr(self, "ai_token_edit"):
            self._model_token_limit = None
            validator = self.ai_token_edit.validator()
            if isinstance(validator, QIntValidator):
                validator.setTop(2_147_483_647)
            self.ai_token_recommendation.setText(
                _("Model changed: recalculate the recommendation from RAM.")
            )

    def _update_ai_model_hint(self, model: str) -> None:
        self.ai_model_hint.setText(
            MODEL_DESCRIPTIONS.get(
                model.strip(),
                _("Custom model: check that the name is available in Ollama."),
            )
        )

    def check_ai_status(self) -> None:
        if self.ai_status_thread and self.ai_status_thread.isRunning():
            return
        self.ai_status_label.setText(_("○ Checking the local service…"))
        self.ai_status_label.setStyleSheet("font-weight: 700; color: #5F6368")
        self.ai_status_thread = AIStatusThread(
            self.ai_model_combo.currentText(), str(self.ai_profile_combo.currentData())
        )
        self.ai_status_thread.completed.connect(self._ai_status_ready)
        self.ai_status_thread.start()

    def _ai_status_ready(self, status: OllamaStatus) -> None:
        if status.online:
            self._known_ai_models = set(status.models)
            installed = self.ai_model_combo.currentText() in status.models
            self.ai_status_label.setText(("● " if installed else "◐ ") + status.message)
            self.ai_status_label.setStyleSheet(
                "font-weight: 700; color: #B06000"
                if status.update_available or not installed
                else "font-weight: 700; color: #188038"
            )
            self.pull_button.setEnabled(not installed)
            self._pending_model_update = status.update_target
            self.model_update_button.setVisible(status.update_available)
            if status.update_available:
                self.ai_status_label.setText(
                    ("● " if installed else "◐ ")
                    + status.message
                    + _("\nUpdate: ")
                    + status.update_message
                )
                target = status.update_target or self.ai_model_combo.currentText()
                action = (
                    _("Use updated model")
                    if target in status.models
                    else _("Download update")
                )
                self.model_update_button.setText(f"{action} · {target}")
        else:
            self.ai_status_label.setText(_("○ Ollama is not running · open the Fedora guide"))
            self.ai_status_label.setStyleSheet("font-weight: 700; color: #D93025")
            self.pull_button.setEnabled(False)
            self._pending_model_update = None
            self.model_update_button.setVisible(False)

    def _apply_model_update(self) -> None:
        target = self._pending_model_update
        if not target:
            return
        self.ai_model_combo.setCurrentText(target)
        if target in self._known_ai_models:
            self.check_ai_status()
        else:
            self.pull_ai_model()

    def pull_ai_model(self) -> None:
        if self.ai_pull_thread and self.ai_pull_thread.isRunning():
            return
        self.pull_button.setEnabled(False)
        self.model_update_button.setEnabled(False)
        self.ai_progress.setRange(0, 0)
        self.ai_progress.setFormat(_("Starting download…"))
        self.ai_pull_thread = AIPullThread(self.ai_model_combo.currentText())
        self.ai_pull_thread.progress.connect(self.ai_progress.setFormat)
        self.ai_pull_thread.completed.connect(self._ai_pull_completed)
        self.ai_pull_thread.failed.connect(self._ai_pull_failed)
        self.ai_pull_thread.start()

    def _ai_pull_completed(self) -> None:
        self.ai_progress.setRange(0, 1)
        self.ai_progress.setValue(1)
        self.ai_progress.setFormat(_("Model ready"))
        self.model_update_button.setEnabled(True)
        self.check_ai_status()

    def _ai_pull_failed(self, message: str) -> None:
        self.ai_progress.setRange(0, 1)
        self.ai_progress.setValue(0)
        self.ai_progress.setFormat(_("Error"))
        self.pull_button.setEnabled(True)
        self.model_update_button.setEnabled(True)
        QMessageBox.critical(self, _("Model download"), message)

    def start_ai_analysis(self, question: str, *, use_all_data: bool = False) -> None:
        if self.ai_analysis_thread and self.ai_analysis_thread.isRunning():
            return
        if use_all_data:
            bounds = self.store.data_date_bounds()
            if bounds:
                all_start = bounds[0].isoformat()
                all_end = (bounds[1] + timedelta(days=1)).isoformat()
                snapshot = build_health_snapshot(
                    self.store,
                    all_start,
                    all_end,
                    record_limit=2_000_000,
                )
                snapshot["analysis_scope"] = "all_local_history"
            else:
                snapshot = {"metrics": []}
        else:
            self.refresh_overview()
            snapshot = self.current_snapshot
            snapshot["analysis_scope"] = "selected_interval"
        if not snapshot.get("metrics"):
            QMessageBox.information(
                self,
                _("Insufficient data"),
                (
                    _("Download Google Health data first.")
                    if use_all_data
                    else _("Download Google Health data or widen the selected period.")
                ),
            )
            return
        self.ask_ai_button.setEnabled(False)
        self.auto_ai_button.setEnabled(False)
        self._ai_answer_received = False
        self.ai_output.clear()
        self.ai_output.setPlaceholderText(_("The model is starting to think…"))
        self.ai_result_title.setText(_("Model thinking"))
        self.ai_live_badge.setVisible(True)
        self.ai_analysis_thread = AIAnalysisThread(
            self.ai_model_combo.currentText(),
            snapshot,
            question,
            self._selected_ai_tokens(),
            self._model_token_limit,
        )
        self.ai_analysis_thread.thinking_chunk.connect(self._ai_thinking_chunk)
        self.ai_analysis_thread.answer_chunk.connect(self._ai_answer_chunk)
        self.ai_analysis_thread.completed.connect(self._ai_analysis_completed)
        self.ai_analysis_thread.failed.connect(self._ai_analysis_failed)
        self.ai_analysis_thread.start()

    @staticmethod
    def _append_stream_text(widget, text: str) -> None:
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        widget.setTextCursor(cursor)
        widget.ensureCursorVisible()

    def _ai_thinking_chunk(self, text: str) -> None:
        self._append_stream_text(self.ai_output, text)

    def _ai_answer_chunk(self, text: str) -> None:
        if not self._ai_answer_received:
            self._ai_answer_received = True
            self.ai_output.clear()
            self.ai_result_title.setText(_("Final answer"))
            self.ai_live_badge.setVisible(False)
        self._append_stream_text(self.ai_output, text)

    def _ai_analysis_completed(self, answer: str) -> None:
        self.ask_ai_button.setEnabled(True)
        self.auto_ai_button.setEnabled(True)
        self.ai_result_title.setText(_("Final answer"))
        self.ai_live_badge.setVisible(False)
        self.ai_output.setMarkdown(answer)

    def _ai_analysis_failed(self, message: str) -> None:
        self.ask_ai_button.setEnabled(True)
        self.auto_ai_button.setEnabled(True)
        self.ai_result_title.setText(_("Analysis error"))
        self.ai_live_badge.setVisible(False)
        self.ai_output.setPlainText(message)
        QMessageBox.warning(
            self,
            _("Local analysis unavailable"),
            _("{message}\n\nCheck Ollama from the Local AI tab.", message=message),
        )

    def export_current_csv(self) -> None:
        if not self.current_type:
            return
        if self.current_type.startswith("resource:"):
            key = self.current_type.removeprefix("resource:")
            payload = self.store.resources().get(key)
            if payload is None:
                return
            filename, _selected_filter = QFileDialog.getSaveFileName(
                self, _("Export JSON"), f"google-health-{key}.json", _("JSON (*.json)")
            )
            if filename:
                Path(filename).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.statusBar().showMessage(_("Resource saved to {filename}", filename=filename), 12000)
            return
        default = f"google-health-{self.current_type}.csv"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self, _("Export CSV"), default, _("CSV (*.csv)")
        )
        if not filename:
            return
        count = self.store.export_csv(self.current_type, Path(filename))
        self.statusBar().showMessage(
            _("Exported {count} records to {filename}", count=count, filename=filename), 12000
        )

    def export_archive(self) -> None:
        today = QDate.currentDate().toString("yyyy-MM-dd")
        default = f"google-health-export-{today}.zip"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self, _("Export complete archive"), default, _("ZIP archive (*.zip)")
        )
        if not filename:
            return
        self.store.export_archive(Path(filename))
        self.statusBar().showMessage(_("Complete archive saved to {filename}", filename=filename), 12000)

    def clear_local_data(self) -> None:
        answer = QMessageBox.warning(
            self,
            _("Delete local data"),
            _("The local database and access token will be deleted. Data in your Google "
              "Health account will not be changed. Continue?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.clear()
        self.credential_store.clear_credentials()
        self.credentials = None
        self.current_records = []
        self.current_type = None
        self.table.setRowCount(0)
        self.plot.clear()
        self.details.clear()
        self.refresh_tree()
        self.refresh_overview()
        self._update_connection_status()

    def _reload_current_type(self) -> None:
        if self.current_type and self.current_type.startswith("resource:"):
            self.load_resource(self.current_type.removeprefix("resource:"))
        elif self.current_type:
            self.load_data_type(self.current_type)
