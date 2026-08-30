from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWizard

from google_health_viewer.constants import SCOPE_GROUPS
from google_health_viewer.oauth import CredentialStore
from google_health_viewer.setup_wizard import SetupWizard


class _Store:
    def __init__(self, existing: bool = False) -> None:
        self.existing = existing

    def has_client(self) -> bool:
        return self.existing

    validate_client_file = staticmethod(CredentialStore.validate_client_file)

    def import_client_file(self, _path) -> None:
        self.existing = True


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_setup_wizard_has_seven_gated_steps_after_the_introduction():
    _application()
    wizard = SetupWizard(_Store())

    assert len(wizard.pageIds()) == 8
    assert wizard.button(QWizard.WizardButton.FinishButton).text() == (
        "Finish and sign in with Google"
    )
    for page_id in (1, 2, 3, 4, 7):
        page = wizard.page(page_id)
        assert not page.isComplete()
        page.confirm.setChecked(True)
        assert page.isComplete()


def test_setup_wizard_selects_every_read_only_scope_by_default():
    _application()
    wizard = SetupWizard(_Store(existing=True))

    assert wizard.credentials_page.isComplete()
    assert wizard.scopes_page.selected_scopes() == list(SCOPE_GROUPS.values())
    assert all(scope.endswith(".readonly") for scope in wizard.scopes_page.selected_scopes())
