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
from .oauth import CredentialStore, OAuthError

CLOUD_CLIENTS_URL = "https://console.cloud.google.com/auth/clients"
CLOUD_AUDIENCE_URL = "https://console.cloud.google.com/auth/audience"
CLOUD_DATA_ACCESS_URL = "https://console.cloud.google.com/auth/scopes"
HEALTH_SETUP_URL = "https://developers.google.com/health/setup"


class _IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Configura l'accesso personale")
        label = QLabel(
            "Google richiede che ogni applicazione che legge dati sanitari abbia credenziali "
            "OAuth personali. Questa procedura guidata apre le pagine necessarie e controlla "
            "la configurazione. Le credenziali e i dati resteranno sul tuo computer."
        )
        label.setWordWrap(True)
        note = QLabel(
            "La configurazione richiede in genere 3–5 minuti e va eseguita una sola volta."
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
        self.setTitle("Crea e importa le credenziali OAuth")
        instructions = QLabel(
            "1. Apri Google Cloud e crea o seleziona un progetto.\n"
            "2. Abilita Google Health API.\n"
            "3. Crea un client OAuth di tipo “Applicazione web”.\n"
            f"4. Inserisci esattamente {OAUTH_REDIRECT_URI} tra gli URI di reindirizzamento.\n"
            "5. Scarica il file JSON e selezionalo qui sotto."
        )
        instructions.setWordWrap(True)
        open_setup = QPushButton("Apri la configurazione ufficiale")
        open_setup.clicked.connect(lambda: open_external_url(HEALTH_SETUP_URL))
        open_clients = QPushButton("Apri i client OAuth")
        open_clients.clicked.connect(lambda: open_external_url(CLOUD_CLIENTS_URL))
        copy_redirect = QPushButton("Copia URI locale")
        copy_redirect.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(OAUTH_REDIRECT_URI)
        )
        buttons = QHBoxLayout()
        buttons.addWidget(open_setup)
        buttons.addWidget(open_clients)
        buttons.addWidget(copy_redirect)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        choose = QPushButton("Seleziona JSON…")
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
        filename, _ = QFileDialog.getOpenFileName(
            self, "Seleziona le credenziali OAuth", "", "File JSON (*.json)"
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
            QMessageBox.warning(self, "Credenziali non valide", str(exc))
            return False
        return True


class _CloudAccessPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Autorizza il tuo account nel progetto")
        text = QLabel(
            "Nel pannello OAuth di Google Cloud:\n\n"
            "• Audience: per l'uso personale puoi lasciare lo stato “Testing” e aggiungere "
            "il tuo indirizzo Google tra i Test users. La modalità Testing non è obbligatoria, "
            "ma passare in produzione può richiedere la verifica dell'app.\n"
            "• Data Access: aggiungi gli scope Google Health che userai. Puoi copiare "
            "l'elenco completo con il pulsante qui sotto.\n\n"
            "In modalità Testing Google fa scadere il refresh token dopo 7 giorni; il "
            "programma ti chiederà semplicemente di accedere di nuovo."
        )
        text.setWordWrap(True)
        audience = QPushButton("Apri Audience / Test users")
        audience.clicked.connect(lambda: open_external_url(CLOUD_AUDIENCE_URL))
        scopes = QPushButton("Apri Data Access")
        scopes.clicked.connect(lambda: open_external_url(CLOUD_DATA_ACCESS_URL))
        copy_scopes = QPushButton("Copia tutti gli scope di lettura")
        copy_scopes.clicked.connect(
            lambda: QGuiApplication.clipboard().setText("\n".join(SCOPE_GROUPS.values()))
        )
        buttons = QHBoxLayout()
        buttons.addWidget(audience)
        buttons.addWidget(scopes)
        buttons.addWidget(copy_scopes)
        self.confirm = QCheckBox("Ho aggiunto il mio account e gli scope nel progetto")
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
        self.setTitle("Scegli i dati da autorizzare")
        text = QLabel(
            "Per vedere tutto, lascia selezionate tutte le categorie. Google mostrerà "
            "comunque un riepilogo e potrai negare singoli permessi."
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
        self.setWindowTitle(f"Configurazione {APP_NAME}")
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
