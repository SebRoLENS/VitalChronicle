from pathlib import Path

path = Path("google_health_viewer/main_window.py")
text = path.read_text(encoding="utf-8")

old_hint = '''    def _update_ai_model_hint(self, model: str) -> None:
        self.ai_model_hint.setText(model_description(model))

    def check_ai_status(self) -> None:
'''
new_hint = '''    def _update_ai_model_hint(self, model: str) -> None:
        self.ai_model_hint.setText(model_description(model))

    def _ai_model_is_installed(self, model: str) -> bool:
        value = model.strip().lower()
        if not value:
            return False
        installed = {item.strip().lower() for item in self._known_ai_models}
        if value in installed:
            return True
        if value.endswith(":latest"):
            return value.removesuffix(":latest") in installed
        if ":" not in value:
            return f"{value}:latest" in installed
        return False

    def _refresh_ai_model_styles(self) -> None:
        for index in range(self.ai_model_combo.count()):
            model = self.ai_model_combo.itemText(index)
            installed = self._ai_model_is_installed(model)
            self.ai_model_combo.setItemData(
                index,
                QColor("#188038") if installed else None,
                Qt.ItemDataRole.ForegroundRole,
            )
            self.ai_model_combo.setItemData(
                index,
                _("Installed locally") if installed else None,
                Qt.ItemDataRole.ToolTipRole,
            )

    def check_ai_status(self) -> None:
'''
if old_hint not in text:
    raise SystemExit("Could not find model hint insertion point")
text = text.replace(old_hint, new_hint, 1)

old_catalog = '''            self.ai_model_combo.setCurrentText(selected_model)
            self.ai_model_combo.blockSignals(False)
            self._update_ai_model_hint(selected_model)
            installed = selected_model in status.models
'''
new_catalog = '''            self.ai_model_combo.setCurrentText(selected_model)
            self.ai_model_combo.blockSignals(False)
            self._refresh_ai_model_styles()
            self._update_ai_model_hint(selected_model)
            installed = self._ai_model_is_installed(selected_model)
'''
if old_catalog not in text:
    raise SystemExit("Could not find status model population block")
text = text.replace(old_catalog, new_catalog, 1)

old_offline = '''        else:
            self.ai_status_label.setText(
                _("○ Ollama is not running · open the installation guide")
            )
'''
new_offline = '''        else:
            self._known_ai_models = set()
            self._refresh_ai_model_styles()
            self.ai_status_label.setText(
                _("○ Ollama is not running · open the installation guide")
            )
'''
if old_offline not in text:
    raise SystemExit("Could not find offline status block")
text = text.replace(old_offline, new_offline, 1)

path.write_text(text, encoding="utf-8")
