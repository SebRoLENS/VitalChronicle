from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from .branding import APP_NAME
from .constants import OAUTH_REDIRECT_URI, SCOPE_GROUPS
from .external_links import open_external_url
from .i18n import _
from .oauth import CredentialStore, OAuthError

CLOUD_CLIENTS_URL = "https://console.cloud.google.com/auth/clients"
CLOUD_AUDIENCE_URL = "https://console.cloud.google.com/auth/audience"
CLOUD_DATA_ACCESS_URL = "https://console.cloud.google.com/auth/scopes"
HEALTH_SETUP_URL = "https://developers.google.com/health/setup"


class _IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(_("Set up personal access"))
        label = QLabel(
            _("Google requires every application that reads health data to use personal OAuth "
              "credentials. This wizard opens the required pages and checks the configuration. "
              "Your credentials and data remain on your computer.")
        )
        label.setWordWrap(True)
        note = QLabel(
            _("Setup normally takes 3–5 minutes and only needs to be completed once.")
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addSpacing(12)
        layout.addWidget(note)
        layout.addStretch()


class _CredentialsPage(QWizardPage):
    def __init__(self, store: CredentialStore) -> None:
        super().__init__()
        self.store = store
        self.setTitle(_("Create and import OAuth credentials"))
        instructions = QLabel(
            _("1. Open Google Cloud and create or select a project.\n"
              "2. Enable the Google Health API.\n"
              "3. Create a Web application OAuth client.\n"
              "4. Add exactly {redirect_uri} to the authorised redirect URIs.\n"
              "5. Download the JSON file and select it below.", redirect_uri=OAUTH_REDIRECT_URI)
        )
        instructions.setWordWrap(True)
        open_setup = QPushButton(_("Open the official setup guide"))
        open_setup.clicked.connect(lambda: open_external_url(HEALTH_SETUP_URL))
        open_clients = QPushButton(_("Open OAuth clients"))
        open_clients.clicked.connect(lambda: open_external_url(CLOUD_CLIENTS_URL))
        copy_redirect = QPushButton(_("Copy local URI"))
        copy_redirect.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(OAUTH_REDIRECT_URI)
        )
        buttons = QHBoxLayout()
        buttons.addWidget(open_setup)
        buttons.addWidget(open_clients)
        buttons.addWidget(copy_redirect)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        choose = QPushButton(_("Select JSON…"))
        choose.clicked.connect(self._choose)
        file_row = QHBoxLayout()
        file_row.addWidget(self.path, 1)
        file_row.addWidget(choose)
        layout = QVBoxLayout(self)
        layout.addWidget(instructions)
        layout.addLayout(buttons)
        layout.addSpacing(12)
        layout.addLayout(file_row)
        layout.addStretch()

    def _choose(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self, _("Select OAuth credentials"), "", _("JSON files (*.json)")
        )
        if filename:
            self.path.setText(filename)
            self.completeChanged.emit()

    def isComplete(self) -> bool:
        return bool(self.path.text()) or self.store.has_client()

    def validatePage(self) -> bool:
        if not self.path.text() and self.store.has_client():
            return True
        try:
            self.store.import_client_file(Path(self.path.text()))
        except OAuthError as exc:
            QMessageBox.warning(self, _("Invalid credentials"), str(exc))
            return False
        return True


class _CloudAccessPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(_("Authorise your account in the project"))
        text = QLabel(
            _("In the Google Cloud OAuth panel:\n\n"
              "• Audience: for personal use you can leave the app in Testing and add your "
              "Google address under Test users. Testing is not mandatory, but moving to "
              "Production may require app verification.\n"
              "• Data Access: add the Google Health scopes you will use. You can copy the "
              "complete list with the button below.\n\n"
              "In Testing, Google expires refresh tokens after seven days; VitalChronicle "
              "will simply ask you to sign in again.")
        )
        text.setWordWrap(True)
        audience = QPushButton(_("Open Audience / Test users"))
        audience.clicked.connect(lambda: open_external_url(CLOUD_AUDIENCE_URL))
        scopes = QPushButton(_("Open Data Access"))
        scopes.clicked.connect(lambda: open_external_url(CLOUD_DATA_ACCESS_URL))
        copy_scopes = QPushButton(_("Copy all read-only scopes"))
        copy_scopes.clicked.connect(
            lambda: QGuiApplication.clipboard().setText("\n".join(SCOPE_GROUPS.values()))
        )
        buttons = QHBoxLayout()
        buttons.addWidget(audience)
        buttons.addWidget(scopes)
        buttons.addWidget(copy_scopes)
        self.confirm = QCheckBox(_("I added my account and the scopes to the project"))
        self.confirm.toggled.connect(self.completeChanged)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addSpacing(16)
        layout.addWidget(self.confirm)
        layout.addStretch()

    def isComplete(self) -> bool:
        return self.confirm.isChecked()


class _ScopesPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(_("Choose the data to authorise"))
        text = QLabel(
            _("Leave every category selected to view all available data. Google will still "
              "show a summary and let you deny individual permissions.")
        )
        text.setWordWrap(True)
        self.checks: list[tuple[QCheckBox, str]] = []
        content = QWidget()
        form = QFormLayout(content)
        for label, scope in SCOPE_GROUPS.items():
            check = QCheckBox(label)
            check.setChecked(True)
            self.checks.append((check, scope))
            form.addRow(check)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(scroll, 1)

    def selected_scopes(self) -> list[str]:
        return [scope for check, scope in self.checks if check.isChecked()]


class SetupWizard(QWizard):
    configuration_ready = Signal(list)

    def __init__(self, store: CredentialStore, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("{app_name} setup", app_name=APP_NAME))
        self.setMinimumSize(760, 500)
        self.setWizardStyle(QWizard.ModernStyle)
        self.addPage(_IntroPage())
        self.credentials_page = _CredentialsPage(store)
        self.addPage(self.credentials_page)
        self.addPage(_CloudAccessPage())
        self.scopes_page = _ScopesPage()
        self.addPage(self.scopes_page)
        self.finished.connect(self._finished)

    def _finished(self, result: int) -> None:
        if result == QWizard.Accepted:
            scopes = self.scopes_page.selected_scopes()
            if scopes:
                self.configuration_ready.emit(scopes)
