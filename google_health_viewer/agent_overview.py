from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .i18n import current_language


_COPY = {
    "en": {
        "section": "Personal health scores",
        "subtitle": "Deterministic estimates from your personal baselines; no cloud AI is required.",
        "readiness": "Readiness",
        "resilience": "Resilience",
        "training": "Training status",
        "load": "Training load",
        "confidence": "Confidence {value:.0f}%",
        "target": "Target {low:.0f}–{high:.0f}",
        "ratio": "Acute/chronic ratio {value:.2f}",
        "no_data": "Not enough data yet",
    },
    "it": {
        "section": "Indicatori personali",
        "subtitle": "Stime deterministiche basate sui tuoi riferimenti personali; non richiedono AI cloud.",
        "readiness": "Readiness",
        "resilience": "Resilienza",
        "training": "Stato allenamento",
        "load": "Carico di allenamento",
        "confidence": "Confidenza {value:.0f}%",
        "target": "Obiettivo {low:.0f}–{high:.0f}",
        "ratio": "Rapporto acuto/cronico {value:.2f}",
        "no_data": "Dati ancora insufficienti",
    },
    "de": {
        "section": "Persönliche Gesundheitswerte",
        "subtitle": "Deterministische Schätzungen aus deinen persönlichen Ausgangswerten; keine Cloud-KI erforderlich.",
        "readiness": "Readiness",
        "resilience": "Resilienz",
        "training": "Trainingsstatus",
        "load": "Trainingsbelastung",
        "confidence": "Konfidenz {value:.0f}%",
        "target": "Ziel {low:.0f}–{high:.0f}",
        "ratio": "Akut/chronisch {value:.2f}",
        "no_data": "Noch nicht genügend Daten",
    },
    "es": {
        "section": "Indicadores personales",
        "subtitle": "Estimaciones deterministas basadas en tus referencias personales; no requieren IA en la nube.",
        "readiness": "Readiness",
        "resilience": "Resiliencia",
        "training": "Estado de entrenamiento",
        "load": "Carga de entrenamiento",
        "confidence": "Confianza {value:.0f}%",
        "target": "Objetivo {low:.0f}–{high:.0f}",
        "ratio": "Relación aguda/crónica {value:.2f}",
        "no_data": "Aún no hay datos suficientes",
    },
    "fr": {
        "section": "Indicateurs personnels",
        "subtitle": "Estimations déterministes à partir de vos références personnelles ; aucune IA cloud n'est requise.",
        "readiness": "Readiness",
        "resilience": "Résilience",
        "training": "Statut d'entraînement",
        "load": "Charge d'entraînement",
        "confidence": "Confiance {value:.0f}%",
        "target": "Cible {low:.0f}–{high:.0f}",
        "ratio": "Rapport aigu/chronique {value:.2f}",
        "no_data": "Pas encore assez de données",
    },
}

_STATUS = {
    "it": {
        "high": "Alta", "moderate": "Moderata", "low": "Bassa",
        "optimal": "Ottimale", "balanced": "Bilanciata",
        "insufficient_history": "Cronologia insufficiente",
        "reduced_load": "Carico ridotto", "productive": "Produttivo",
        "maintaining": "Mantenimento", "increasing": "In aumento",
        "high_load_low_recovery": "Carico alto / recupero basso",
        "very_high_recent_load": "Carico recente molto alto",
        "overreaching_signal": "Possibile sovraccarico",
    },
    "en": {
        "high": "High", "moderate": "Moderate", "low": "Low",
        "optimal": "Optimal", "balanced": "Balanced",
        "insufficient_history": "Insufficient history",
        "reduced_load": "Reduced load", "productive": "Productive",
        "maintaining": "Maintaining", "increasing": "Increasing",
        "high_load_low_recovery": "High load / low recovery",
        "very_high_recent_load": "Very high recent load",
        "overreaching_signal": "Possible overreaching",
    },
}


def _lang() -> str:
    code = current_language()
    return code if code in _COPY else "en"


def _t(key: str, **values: Any) -> str:
    text = _COPY.get(_lang(), _COPY["en"]).get(key, _COPY["en"].get(key, key))
    return text.format(**values) if values else text


def _status(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    language = _lang()
    if language in _STATUS and raw in _STATUS[language]:
        return _STATUS[language][raw]
    if raw in _STATUS["en"]:
        return _STATUS["en"][raw]
    return raw.replace("_", " ").strip().capitalize()


class PersonalScoreCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setMinimumHeight(132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")
        self.caption = QLabel(_t("no_data"))
        self.caption.setObjectName("cardCaption")
        self.caption.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        layout.addStretch()

    def set_score(self, score: Any, label: Any, confidence: Any = None) -> None:
        if not isinstance(score, (int, float)):
            self.value.setText("—")
            self.caption.setText(_t("no_data"))
            return
        self.value.setText(f"{float(score):.0f}/100")
        parts = [_status(label)] if label else []
        if isinstance(confidence, (int, float)):
            parts.append(_t("confidence", value=float(confidence) * 100))
        self.caption.setText(" · ".join(parts))

    def set_text(self, value: str, caption: str = "") -> None:
        self.value.setText(value or "—")
        self.caption.setText(caption or _t("no_data"))


def build_agent_overview_payload(runtime, end: date) -> dict[str, Any]:
    readiness = runtime.tools.execute("calculate_readiness", {"end": end.isoformat()})
    resilience = runtime.tools.execute("calculate_resilience", {"end": end.isoformat()})
    training = runtime.tools.execute("calculate_training_status", {"end": end.isoformat()})
    target = runtime.tools.execute("calculate_target_load", {"end": end.isoformat()})
    return {
        "readiness": readiness,
        "resilience": resilience,
        "training": training,
        "target": target,
    }


def install_agent_overview(main_window_module) -> None:
    MainWindow = main_window_module.MainWindow
    OverviewPage = main_window_module.OverviewPage
    if getattr(MainWindow, "_agent_overview_installed", False):
        return

    original_overview_init = OverviewPage.__init__
    original_refresh_overview = MainWindow.refresh_overview

    def overview_init(self, parent=None) -> None:
        original_overview_init(self, parent)
        root = self.layout()
        panel = QFrame()
        panel.setObjectName("overviewHero")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 14)
        heading = QHBoxLayout()
        title = QLabel(_t("section"))
        title.setObjectName("chatSectionTitle")
        heading.addWidget(title)
        heading.addStretch()
        subtitle = QLabel(_t("subtitle"))
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        panel_layout.addLayout(heading)
        panel_layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.agent_score_cards = {
            "readiness": PersonalScoreCard(_t("readiness")),
            "resilience": PersonalScoreCard(_t("resilience")),
            "training": PersonalScoreCard(_t("training")),
            "load": PersonalScoreCard(_t("load")),
        }
        for index, card in enumerate(self.agent_score_cards.values()):
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        panel_layout.addLayout(grid)
        self.agent_score_panel = panel
        # The normal layout is hero, spacing, scroll area, disclaimer. Put the
        # high-value personal scores directly above the larger metric-card grid.
        root.insertWidget(2, panel)

    def refresh_agent_scores(self, payload: dict[str, Any] | None) -> None:
        cards = getattr(self, "agent_score_cards", None)
        if not cards:
            return
        if not payload:
            for card in cards.values():
                card.set_text("—", _t("no_data"))
            return
        readiness = payload.get("readiness") or {}
        cards["readiness"].set_score(
            readiness.get("score"), readiness.get("label"), readiness.get("confidence")
        )
        resilience = payload.get("resilience") or {}
        cards["resilience"].set_score(
            resilience.get("score"), resilience.get("label"), resilience.get("confidence")
        )
        training = payload.get("training") or {}
        load = training.get("load") or {}
        ratio = load.get("acute_chronic_ratio")
        cards["training"].set_text(
            _status(training.get("status")),
            _t("ratio", value=float(ratio)) if isinstance(ratio, (int, float)) else _t("no_data"),
        )
        target = payload.get("target") or {}
        current = target.get("current_acute_load")
        target_range = target.get("target_weekly_load") or {}
        low, high = target_range.get("lower"), target_range.get("upper")
        caption = _t("no_data")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            caption = _t("target", low=float(low), high=float(high))
            confidence = target.get("confidence")
            if isinstance(confidence, (int, float)):
                caption += " · " + _t("confidence", value=float(confidence) * 100)
        cards["load"].set_text(
            f"{float(current):.0f}" if isinstance(current, (int, float)) else "—",
            caption,
        )

    def refresh_overview(self) -> None:
        original_refresh_overview(self)
        runtime = getattr(self, "agent_runtime", None)
        if runtime is None or not runtime.enabled:
            self.overview.refresh_agent_scores(None)
            return
        try:
            reference_day = self._qdate_to_date(self.end_date.date())
            payload = build_agent_overview_payload(runtime, reference_day)
        except Exception:  # noqa: BLE001 - overview must remain usable with sparse data.
            payload = None
        self.overview.refresh_agent_scores(payload)

    OverviewPage.__init__ = overview_init
    OverviewPage.refresh_agent_scores = refresh_agent_scores
    MainWindow.refresh_overview = refresh_overview
    MainWindow._agent_overview_installed = True
