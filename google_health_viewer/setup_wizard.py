from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from .oauth import CredentialStore, OAuthError, port_is_available

CLOUD_PROJECT_URL = "https://console.cloud.google.com/projectselector2/home/dashboard"
CLOUD_API_URL = "https://console.cloud.google.com/apis/library/health.googleapis.com"
CLOUD_BRANDING_URL = "https://console.cloud.google.com/auth/branding"
CLOUD_AUDIENCE_URL = "https://console.cloud.google.com/auth/audience"
CLOUD_DATA_ACCESS_URL = "https://console.cloud.google.com/auth/scopes"
CLOUD_CLIENTS_URL = "https://console.cloud.google.com/auth/clients"
HEALTH_SETUP_URL = "https://developers.google.com/health/setup"


def _copy(value: str) -> None:
    QGuiApplication.clipboard().setText(value)


def _open_page(parent: QWidget, url: str) -> None:
    if open_external_url(url):
        return
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(_("Could not open the browser"))
    box.setText(
        _(
            "VitalChronicle could not open this Google page automatically. Copy the "
            "address below and paste it into your browser."
        )
    )
    box.setDetailedText(url)
    copy_button = box.addButton(_("Copy address"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is copy_button:
        _copy(url)


def _text(value: str) -> QLabel:
    label = QLabel(value)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _card(title: str, body: str) -> QFrame:
    card = QFrame()
    card.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(card)
    heading = QLabel(title)
    heading.setStyleSheet("font-weight: 700; font-size: 15px;")
    layout.addWidget(heading)
    layout.addWidget(_text(body))
    return card


class _ConfirmedPage(QWizardPage):
    def __init__(self, title: str, introduction: str, confirmation: str) -> None:
        super().__init__()
        self.setTitle(title)
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(_text(introduction))
        self.layout.addSpacing(8)
        self.confirm = QCheckBox(confirmation)
        self.confirm.toggled.connect(self.completeChanged)

    def finish_layout(self, action_text: str | None = None) -> None:
        self.layout.addSpacing(14)
        gate = QFrame()
        gate.setFrameShape(QFrame.Shape.StyledPanel)
        gate_layout = QVBoxLayout(gate)
        action = action_text or _("Next step")
        hint = QLabel(f"☑  →  {action}")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-weight: 700; font-size: 14px;")
        gate_layout.addWidget(hint)
        gate_layout.addWidget(self.confirm)
        self.layout.addWidget(gate)
        self.layout.addStretch()

    def isComplete(self) -> bool:
        return self.confirm.isChecked()


class _IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(_("Connect VitalChronicle to Google Health"))
        self.setSubTitle(_("A beginner-friendly setup in seven small steps"))
        layout = QVBoxLayout(self)
        layout.addWidget(
            _card(
                _("What this wizard does"),
                _(
                    "Google requires a personal Google Cloud project before an application can "
                    "read health data. The wizard will tell you exactly which page to open, which "
                    "button to press, and what to enter. No programming knowledge is required."
                ),
            )
        )
        layout.addWidget(
            _card(
                _("Before you begin"),
                _(
                    "You need your Google account, an internet connection, and a web browser. "
                    "Use the same Google account that contains the health data you want to view. "
                    "Keep this wizard open while you work in the browser."
                ),
            )
        )
        layout.addWidget(
            _card(
                _("Privacy"),
                _(
                    "The OAuth client file and health archive remain on this computer. Never "
                    "publish the downloaded JSON file, attach it to a GitHub issue, or send it "
                    "to anyone—including the VitalChronicle developer."
                ),
            )
        )
        note = QLabel(
            _(
                "Typical setup time: about 10 minutes. It normally only needs to be done once."
            )
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-weight: 600; padding-top: 8px;")
        layout.addWidget(note)
        layout.addStretch()


class _ProjectPage(_ConfirmedPage):
    def __init__(self) -> None:
        super().__init__(
            _("Step 1 of 7 — Create a Google Cloud project"),
            _(
                "A project is a private container for the API configuration. Creating one does "
                "not upload your health data to Google Cloud and normally does not require billing."
            ),
            _("I can see my selected project name in the top bar of Google Cloud"),
        )
        self.layout.addWidget(
            _card(
                _("Do this in the browser"),
                _(
                    "1. Click “Open project selector” below and sign in to Google.\n"
                    "2. Click “New project”.\n"
                    "3. Enter “VitalChronicle Personal” as the project name.\n"
                    "4. If Google asks for a location, choose “No organisation”.\n"
                    "5. Click “Create”, wait for completion, then select the new project.\n"
                    "6. Check that “VitalChronicle Personal” appears in the top bar."
                ),
            )
        )
        buttons = QHBoxLayout()
        project = QPushButton(_("Open project selector"))
        project.clicked.connect(lambda: _open_page(self, CLOUD_PROJECT_URL))
        guide = QPushButton(_("Open official Google guide"))
        guide.clicked.connect(lambda: _open_page(self, HEALTH_SETUP_URL))
        buttons.addWidget(project)
        buttons.addWidget(guide)
        self.layout.addLayout(buttons)
        self.finish_layout()


class _ApiPage(_ConfirmedPage):
    def __init__(self) -> None:
        super().__init__(
            _("Step 2 of 7 — Enable Google Health API"),
            _("This permits the selected project to make Google Health API requests."),
            _("The Google Health API page now shows “Manage” or “API enabled”"),
        )
        self.layout.addWidget(
            _card(
                _("Do this in the browser"),
                _(
                    "1. Click “Open Google Health API” below.\n"
                    "2. Check the project name in the top bar. If it is wrong, select the project "
                    "created in step 1.\n"
                    "3. Click “Enable”.\n"
                    "4. Wait until the page shows “Manage” or confirms that the API is enabled."
                ),
            )
        )
        button = QPushButton(_("Open Google Health API"))
        button.clicked.connect(lambda: _open_page(self, CLOUD_API_URL))
        self.layout.addWidget(button)
        self.finish_layout()


class _AudiencePage(_ConfirmedPage):
    def __init__(self) -> None:
        super().__init__(
            _("Step 3 of 7 — Configure the app and add yourself"),
            _(
                "For personal use, keep the project in Testing and explicitly add your own "
                "Google address as a test user. Public app verification is not required for this setup."
            ),
            _("My Google address appears in the Test users list"),
        )
        self.layout.addWidget(
            _card(
                _("A. Register the app if Google asks you to"),
                _(
                    "1. Open “Branding”. If you see “Get started”, click it.\n"
                    "2. App name: “VitalChronicle Personal”.\n"
                    "3. User support email: choose your own Google address.\n"
                    "4. Audience: choose “External”.\n"
                    "5. Contact email: enter your own address.\n"
                    "6. Accept the policy acknowledgement if shown, then click “Create”."
                ),
            )
        )
        self.layout.addWidget(
            _card(
                _("B. Add your account as a test user"),
                _(
                    "1. Open “Audience”.\n"
                    "2. Confirm “Publishing status: Testing” and “User type: External”.\n"
                    "3. Under “Test users”, click “Add users”.\n"
                    "4. Enter the Google address containing your health data.\n"
                    "5. Click “Save” and check that the address appears in the list."
                ),
            )
        )
        buttons = QHBoxLayout()
        branding = QPushButton(_("Open Branding"))
        branding.clicked.connect(lambda: _open_page(self, CLOUD_BRANDING_URL))
        audience = QPushButton(_("Open Audience / Test users"))
        audience.clicked.connect(lambda: _open_page(self, CLOUD_AUDIENCE_URL))
        buttons.addWidget(branding)
        buttons.addWidget(audience)
        self.layout.addLayout(buttons)
        note = _text(
            _(
                "Testing tokens normally expire after seven days. If that happens, "
                "VitalChronicle will simply ask you to sign in again."
            )
        )
        note.setStyleSheet("color: #5f6368;")
        self.layout.addWidget(note)
        self.finish_layout()


class _CloudScopesPage(_ConfirmedPage):
    def __init__(self) -> None:
        super().__init__(
            _("Step 4 of 7 — Allow the read-only health permissions"),
            _(
                "These permissions let the OAuth client request health data. Every permission "
                "used by VitalChronicle is read-only: the app cannot modify Google Health data."
            ),
            _("I selected the Google Health read-only scopes and clicked Save"),
        )
        self.layout.addWidget(
            _card(
                _("Do this in the browser"),
                _(
                    "1. Click “Open Data Access”.\n"
                    "2. Click “Add or remove scopes”.\n"
                    "3. In the API filter, search for “Google Health API”.\n"
                    "4. Select the read-only scopes you want. For all VitalChronicle features, "
                    "select every scope copied by the button below.\n"
                    "5. Click “Update” at the bottom of the panel.\n"
                    "6. Back on Data Access, click “Save”."
                ),
            )
        )
        buttons = QHBoxLayout()
        access = QPushButton(_("Open Data Access"))
        access.clicked.connect(lambda: _open_page(self, CLOUD_DATA_ACCESS_URL))
        copy_scopes = QPushButton(_("Copy all read-only scopes"))
        copy_scopes.clicked.connect(lambda: _copy("\n".join(SCOPE_GROUPS.values())))
        buttons.addWidget(access)
        buttons.addWidget(copy_scopes)
        self.layout.addLayout(buttons)
        self.finish_layout()


class _CredentialsPage(QWizardPage):
    def __init__(self, store: CredentialStore) -> None:
        super().__init__()
        self.store = store
        self._selected_valid = False
        self.setTitle(_("Step 5 of 7 — Create and import the OAuth client"))
        self.setSubTitle(_("VitalChronicle checks the downloaded JSON before continuing"))
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(
            _card(
                _("Create the client in the browser"),
                _(
                    "1. Click “Open OAuth clients”, then “Create client”.\n"
                    "2. Application type: choose “Web application”.\n"
                    "3. Name: enter “VitalChronicle personal desktop”.\n"
                    "4. Leave “Authorised JavaScript origins” empty.\n"
                    "5. Under “Authorised redirect URIs”, click “Add URI”.\n"
                    "6. Paste the exact local address shown below, including the final slash.\n"
                    "7. Click “Create”, then immediately download the JSON file."
                ),
            )
        )
        buttons = QHBoxLayout()
        clients = QPushButton(_("Open OAuth clients"))
        clients.clicked.connect(lambda: _open_page(self, CLOUD_CLIENTS_URL))
        copy_redirect = QPushButton(_("Copy local redirect URI"))
        copy_redirect.clicked.connect(lambda: _copy(OAUTH_REDIRECT_URI))
        buttons.addWidget(clients)
        buttons.addWidget(copy_redirect)
        self.layout.addLayout(buttons)
        redirect = QLineEdit(OAUTH_REDIRECT_URI)
        redirect.setReadOnly(True)
        redirect.setStyleSheet("font-family: monospace; font-weight: 600;")
        self.layout.addWidget(redirect)
        self.layout.addSpacing(10)
        self.layout.addWidget(_text(_("Now select the JSON file that Google downloaded:")))
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        choose = QPushButton(_("Select downloaded JSON…"))
        choose.clicked.connect(self._choose)
        file_row = QHBoxLayout()
        file_row.addWidget(self.path, 1)
        file_row.addWidget(choose)
        self.layout.addLayout(file_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        if self.store.has_client():
            self.status.setText(
                _("✓ A valid OAuth client is already stored. Select a JSON only to replace it.")
            )
            self.status.setStyleSheet("color: #188038; font-weight: 600;")
        self.layout.addStretch()

    def _choose(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self, _("Select OAuth credentials"), "", _("JSON files (*.json)")
        )
        if not filename:
            return
        try:
            self.store.validate_client_file(Path(filename))
        except OAuthError as exc:
            self.path.clear()
            self._selected_valid = False
            self.status.setText(_("✕ This JSON cannot be used: {error}", error=exc))
            self.status.setStyleSheet("color: #b3261e; font-weight: 600;")
            QMessageBox.warning(self, _("Invalid credentials"), str(exc))
        else:
            self.path.setText(filename)
            self._selected_valid = True
            self.status.setText(_("✓ Valid OAuth client. The redirect URI is correct."))
            self.status.setStyleSheet("color: #188038; font-weight: 600;")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._selected_valid or self.store.has_client()

    def validatePage(self) -> bool:
        if not self._selected_valid and self.store.has_client():
            return True
        try:
            self.store.import_client_file(Path(self.path.text()))
        except OAuthError as exc:
            QMessageBox.warning(self, _("Invalid credentials"), str(exc))
            return False
        return True


class _ScopesPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(_("Step 6 of 7 — Choose what VitalChronicle may read"))
        text = _text(
            _(
                "Leave every category selected for the complete dashboard and full-history AI "
                "analysis. You may deselect a category for privacy; its related charts and "
                "analysis will then be unavailable. VitalChronicle never requests write access."
            )
        )
        self.checks: list[tuple[QCheckBox, str]] = []
        content = QWidget()
        form = QFormLayout(content)
        for label, scope in SCOPE_GROUPS.items():
            check = QCheckBox(label)
            check.setChecked(True)
            check.toggled.connect(self.completeChanged)
            self.checks.append((check, scope))
            form.addRow(check)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(scroll, 1)
        select_all = QPushButton(_("Select all categories"))
        select_all.clicked.connect(self._select_all)
        layout.addWidget(select_all)

    def _select_all(self) -> None:
        for check, _scope in self.checks:
            check.setChecked(True)

    def isComplete(self) -> bool:
        return any(check.isChecked() for check, _scope in self.checks)

    def selected_scopes(self) -> list[str]:
        return [scope for check, scope in self.checks if check.isChecked()]


class _ReadyPage(_ConfirmedPage):
    def __init__(self) -> None:
        super().__init__(
            _("Step 7 of 7 — Sign in and approve access"),
            _(
                "When you click “Finish and sign in with Google”, VitalChronicle opens your "
                "system browser and waits for Google on a private local callback."
            ),
            _("I am ready to sign in with the test-user account configured in step 3"),
        )
        self.layout.addWidget(
            _card(
                _("What happens next"),
                _(
                    "1. Choose the same Google account added under Test users.\n"
                    "2. If Google shows “This app has not been verified”, check that the project "
                    "and app name are yours. Then click “Advanced” and “Go to VitalChronicle "
                    "Personal” to continue. This warning is expected for a private testing project.\n"
                    "3. Review the read-only permissions and click “Continue” or “Allow”.\n"
                    "4. Wait for “Authentication completed” in the browser.\n"
                    "5. Close the browser tab and return to VitalChronicle; the first data update "
                    "starts automatically."
                ),
            )
        )
        note = _text(
            _(
                "Do not close VitalChronicle during sign-in. The local address uses port 8765 "
                "only for the Google response and is never exposed to the internet."
            )
        )
        note.setStyleSheet("color: #5f6368;")
        self.layout.addWidget(note)
        self.finish_layout(_("Finish and sign in with Google"))

    def validatePage(self) -> bool:
        if port_is_available():
            return True
        QMessageBox.warning(
            self,
            _("Local sign-in port is busy"),
            _(
                "Another program is using local port 8765. Close that program, then click "
                "“Finish and sign in with Google” again."
            ),
        )
        return False


class AuthorizationHelpDialog(QDialog):
    """Non-modal fallback shown while the browser-based OAuth flow is active."""

    def __init__(self, url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url = url
        self.setWindowTitle(_("Sign in with Google"))
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _text(
                _(
                    "Your browser should open automatically. Complete the Google sign-in there. "
                    "If nothing opens, use “Open browser” below or copy the link manually."
                )
            )
        )
        address = QLineEdit(url)
        address.setReadOnly(True)
        address.setCursorPosition(0)
        layout.addWidget(address)
        buttons = QHBoxLayout()
        open_button = QPushButton(_("Open browser"))
        open_button.clicked.connect(lambda: _open_page(self, self.url))
        copy_button = QPushButton(_("Copy sign-in link"))
        copy_button.clicked.connect(lambda: _copy(self.url))
        buttons.addWidget(open_button)
        buttons.addWidget(copy_button)
        layout.addLayout(buttons)
        waiting = QLabel(_("Waiting for Google… This window closes automatically after sign-in."))
        waiting.setStyleSheet("color: #1769aa; font-weight: 600;")
        layout.addWidget(waiting)


class SetupWizard(QWizard):
    configuration_ready = Signal(list)

    def __init__(self, store: CredentialStore, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("{app_name} setup", app_name=APP_NAME))
        self.setMinimumSize(900, 680)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.setButtonText(QWizard.WizardButton.NextButton, _("Next step"))
        self.setButtonText(QWizard.WizardButton.BackButton, _("Back"))
        self.setButtonText(QWizard.WizardButton.CancelButton, _("Cancel"))
        self.setButtonText(
            QWizard.WizardButton.FinishButton, _("Finish and sign in with Google")
        )
        self.addPage(_IntroPage())
        self.addPage(_ProjectPage())
        self.addPage(_ApiPage())
        self.addPage(_AudiencePage())
        self.addPage(_CloudScopesPage())
        self.credentials_page = _CredentialsPage(store)
        self.addPage(self.credentials_page)
        self.scopes_page = _ScopesPage()
        self.addPage(self.scopes_page)
        self.addPage(_ReadyPage())
        self.finished.connect(self._finished)

    def _finished(self, result: int) -> None:
        if result == QWizard.DialogCode.Accepted:
            scopes = self.scopes_page.selected_scopes()
            if scopes:
                self.configuration_ready.emit(scopes)
