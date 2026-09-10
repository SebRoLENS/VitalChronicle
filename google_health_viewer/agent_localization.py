from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QProgressBar

from . import agent_runtime as runtime_module
from .agent_runtime import AgentRuntime
from .agent_ui import CalibrationDialog
from .i18n import current_language


_LANGUAGE_NAMES = {
    "en": "English",
    "it": "Italian",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
}


_CALIBRATION_COPY: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "high_load_subjective_tolerance": (
            "Your recent cardiovascular/training load is much higher than your longer personal baseline. How do you usually feel after weeks like this?",
            "Your answer helps distinguish a load that you commonly tolerate from one that is usually accompanied by subjective fatigue.",
        ),
        "subjective_sleep_need_context": (
            "VitalChronicle estimates that your usual sleep duration is around {hours:.1f} hours. Do you generally feel well rested with that amount?",
            "This adds subjective context to the measured sleep-duration baseline without redefining medical sleep need.",
        ),
        "current_training_goal": (
            "Is your current activity level intentional, and what is your main training goal right now?",
            "Knowing whether the recent workload reflects an intentional plan improves future coaching.",
        ),
        "sleep_schedule_context": (
            "Your sleep timing varies noticeably across the recorded nights. Is that mainly due to work/social schedules, training, or does it happen without a clear reason?",
            "This can prevent the agent from attributing an irregular schedule to training when another context explains it.",
        ),
        "primary_health_coaching_goal": (
            "What is the most important thing you want VitalChronicle to help you understand: recovery, sleep, training, general wellbeing, or something else?",
            "No strong uncertainty required a physiological follow-up, so one goal question is more useful than a standard questionnaire.",
        ),
    },
    "it": {
        "high_load_subjective_tolerance": (
            "Il tuo carico cardiovascolare/di allenamento recente è molto più alto del tuo riferimento personale di lungo periodo. Come ti senti di solito dopo settimane come questa?",
            "La risposta aiuta a distinguere un carico che tolleri abitualmente da uno che tende ad accompagnarsi a stanchezza soggettiva.",
        ),
        "subjective_sleep_need_context": (
            "VitalChronicle stima che la tua durata abituale del sonno sia di circa {hours:.1f} ore. In genere ti senti ben riposato con questa quantità?",
            "Questo aggiunge il tuo riscontro soggettivo al riferimento misurato del sonno senza ridefinire il fabbisogno medico di sonno.",
        ),
        "current_training_goal": (
            "Il tuo livello di attività attuale è intenzionale? Qual è il tuo principale obiettivo di allenamento in questo periodo?",
            "Sapere se il carico recente fa parte di un piano intenzionale rende più pertinenti i consigli futuri.",
        ),
        "sleep_schedule_context": (
            "Gli orari del tuo sonno variano sensibilmente tra le notti registrate. Dipende soprattutto da lavoro o vita sociale, dall'allenamento, oppure accade senza un motivo chiaro?",
            "Questo evita che l'agente attribuisca all'allenamento un ritmo del sonno irregolare quando esiste un'altra spiegazione.",
        ),
        "primary_health_coaching_goal": (
            "Qual è la cosa più importante che vuoi che VitalChronicle ti aiuti a capire: recupero, sonno, allenamento, benessere generale o altro?",
            "Non emerge un'incertezza fisiologica che richieda una domanda specifica, quindi una domanda sull'obiettivo è più utile di un questionario standard.",
        ),
    },
    "de": {
        "high_load_subjective_tolerance": (
            "Deine aktuelle Herz-Kreislauf-/Trainingsbelastung liegt deutlich über deinem längerfristigen persönlichen Ausgangswert. Wie fühlst du dich normalerweise nach solchen Wochen?",
            "Die Antwort hilft zu unterscheiden, ob du diese Belastung gewöhnlich gut verträgst oder ob sie meist mit subjektiver Müdigkeit einhergeht.",
        ),
        "subjective_sleep_need_context": (
            "VitalChronicle schätzt deine übliche Schlafdauer auf etwa {hours:.1f} Stunden. Fühlst du dich mit dieser Schlafmenge im Allgemeinen gut erholt?",
            "Damit wird der gemessene Schlaf-Basiswert um deinen subjektiven Eindruck ergänzt, ohne einen medizinischen Schlafbedarf festzulegen.",
        ),
        "current_training_goal": (
            "Ist dein aktuelles Aktivitätsniveau beabsichtigt, und was ist derzeit dein wichtigstes Trainingsziel?",
            "Zu wissen, ob die aktuelle Belastung Teil eines geplanten Trainings ist, verbessert zukünftige Empfehlungen.",
        ),
        "sleep_schedule_context": (
            "Deine Schlafzeiten schwanken zwischen den aufgezeichneten Nächten deutlich. Liegt das hauptsächlich an Arbeit/Sozialleben, Training oder tritt es ohne klaren Grund auf?",
            "So vermeidet der Agent, einen unregelmäßigen Schlafrhythmus dem Training zuzuschreiben, wenn ein anderer Kontext ihn besser erklärt.",
        ),
        "primary_health_coaching_goal": (
            "Was soll VitalChronicle dir vor allem helfen zu verstehen: Erholung, Schlaf, Training, allgemeines Wohlbefinden oder etwas anderes?",
            "Es gibt keine starke physiologische Unsicherheit für eine gezielte Rückfrage, daher ist eine Frage nach deinem Ziel nützlicher als ein Standardfragebogen.",
        ),
    },
    "es": {
        "high_load_subjective_tolerance": (
            "Tu carga cardiovascular/de entrenamiento reciente es mucho mayor que tu referencia personal a largo plazo. ¿Cómo sueles sentirte después de semanas así?",
            "Tu respuesta ayuda a distinguir una carga que normalmente toleras bien de otra que suele acompañarse de fatiga subjetiva.",
        ),
        "subjective_sleep_need_context": (
            "VitalChronicle estima que tu duración habitual del sueño es de unas {hours:.1f} horas. ¿Normalmente te sientes descansado con esa cantidad?",
            "Esto añade tu percepción subjetiva a la referencia de sueño medida sin redefinir una necesidad médica de sueño.",
        ),
        "current_training_goal": (
            "¿Tu nivel actual de actividad es intencionado y cuál es tu principal objetivo de entrenamiento ahora mismo?",
            "Saber si la carga reciente forma parte de un plan intencionado mejora las recomendaciones futuras.",
        ),
        "sleep_schedule_context": (
            "Tus horarios de sueño varían bastante entre las noches registradas. ¿Se debe sobre todo al trabajo o vida social, al entrenamiento o sucede sin una razón clara?",
            "Esto evita que el agente atribuya al entrenamiento un horario irregular cuando otro contexto lo explica mejor.",
        ),
        "primary_health_coaching_goal": (
            "¿Qué es lo más importante que quieres que VitalChronicle te ayude a entender: recuperación, sueño, entrenamiento, bienestar general u otra cosa?",
            "No aparece una incertidumbre fisiológica fuerte que requiera una pregunta específica, así que una pregunta sobre tu objetivo es más útil que un cuestionario estándar.",
        ),
    },
    "fr": {
        "high_load_subjective_tolerance": (
            "Votre charge cardiovasculaire/d'entraînement récente est nettement supérieure à votre référence personnelle à long terme. Comment vous sentez-vous habituellement après des semaines comme celle-ci ?",
            "Votre réponse aide à distinguer une charge que vous tolérez habituellement bien d'une charge qui s'accompagne souvent de fatigue ressentie.",
        ),
        "subjective_sleep_need_context": (
            "VitalChronicle estime que votre durée habituelle de sommeil est d'environ {hours:.1f} heures. Vous sentez-vous généralement bien reposé avec cette durée ?",
            "Cela ajoute votre ressenti au niveau de référence mesuré du sommeil sans redéfinir un besoin médical de sommeil.",
        ),
        "current_training_goal": (
            "Votre niveau d'activité actuel est-il intentionnel, et quel est votre principal objectif d'entraînement en ce moment ?",
            "Savoir si la charge récente fait partie d'un plan volontaire améliore les conseils futurs.",
        ),
        "sleep_schedule_context": (
            "Vos horaires de sommeil varient nettement entre les nuits enregistrées. Est-ce surtout lié au travail ou à la vie sociale, à l'entraînement, ou cela arrive-t-il sans raison claire ?",
            "Cela évite que l'agent attribue un rythme de sommeil irrégulier à l'entraînement lorsqu'un autre contexte l'explique mieux.",
        ),
        "primary_health_coaching_goal": (
            "Quelle est la chose la plus importante que vous voulez que VitalChronicle vous aide à comprendre : récupération, sommeil, entraînement, bien-être général ou autre chose ?",
            "Aucune incertitude physiologique forte ne nécessite une question ciblée ; une question sur votre objectif est donc plus utile qu'un questionnaire standard.",
        ),
    },
}


_UI_COPY = {
    "en": {
        "working": "Agent activity",
        "starting": "Reading local data coverage and personal baselines…",
        "baseline": "Calculating sleep, HRV, resting-heart-rate, readiness, load and resilience baselines…",
        "selecting": "Comparing uncertainties and selecting only high-information questions…",
        "model": "The local model is selecting the most useful questions. Duration depends on the model and hardware.",
        "fallback": "Adaptive model selection was unavailable; using data-driven deterministic questions.",
    },
    "it": {
        "working": "Attività dell'agente",
        "starting": "Lettura della copertura dei dati locali e dei riferimenti personali…",
        "baseline": "Calcolo dei riferimenti di sonno, HRV, frequenza a riposo, readiness, carico e resilienza…",
        "selecting": "Confronto delle incertezze e selezione delle sole domande ad alto valore informativo…",
        "model": "Il modello locale sta selezionando le domande più utili. La durata dipende dal modello e dall'hardware.",
        "fallback": "La selezione adattiva del modello non è disponibile; uso domande deterministiche guidate dai dati.",
    },
    "de": {
        "working": "Agentenaktivität",
        "starting": "Lokale Datenabdeckung und persönliche Ausgangswerte werden gelesen…",
        "baseline": "Schlaf-, HRV-, Ruhepuls-, Readiness-, Belastungs- und Resilienz-Basiswerte werden berechnet…",
        "selecting": "Unsicherheiten werden verglichen und nur informationsreiche Fragen ausgewählt…",
        "model": "Das lokale Modell wählt die nützlichsten Fragen aus. Die Dauer hängt von Modell und Hardware ab.",
        "fallback": "Die adaptive Modellauswahl war nicht verfügbar; datenbasierte deterministische Fragen werden verwendet.",
    },
    "es": {
        "working": "Actividad del agente",
        "starting": "Leyendo la cobertura de datos locales y las referencias personales…",
        "baseline": "Calculando referencias de sueño, HRV, frecuencia en reposo, readiness, carga y resiliencia…",
        "selecting": "Comparando incertidumbres y seleccionando solo preguntas de alto valor informativo…",
        "model": "El modelo local está seleccionando las preguntas más útiles. La duración depende del modelo y del hardware.",
        "fallback": "La selección adaptativa no está disponible; se usarán preguntas deterministas guiadas por los datos.",
    },
    "fr": {
        "working": "Activité de l'agent",
        "starting": "Lecture de la couverture des données locales et des références personnelles…",
        "baseline": "Calcul des références de sommeil, VFC, fréquence au repos, readiness, charge et résilience…",
        "selecting": "Comparaison des incertitudes et sélection des seules questions à forte valeur informative…",
        "model": "Le modèle local sélectionne les questions les plus utiles. La durée dépend du modèle et du matériel.",
        "fallback": "La sélection adaptative n'est pas disponible ; des questions déterministes guidées par les données seront utilisées.",
    },
}


def _language() -> str:
    code = current_language()
    return code if code in _CALIBRATION_COPY else "en"


def _ui(key: str) -> str:
    language = _language()
    return _UI_COPY.get(language, _UI_COPY["en"]).get(key, _UI_COPY["en"].get(key, key))


def localize_calibration_item(item: dict[str, Any], language: str | None = None) -> dict[str, Any]:
    code = language if language in _CALIBRATION_COPY else _language()
    key = str(item.get("learning_key") or "adaptive_context")
    pair = _CALIBRATION_COPY.get(code, _CALIBRATION_COPY["en"]).get(key)
    if not pair:
        return dict(item)
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    hours = context.get("baseline_sleep_hours")
    if not isinstance(hours, (int, float)):
        observation = str(context.get("observation") or "")
        try:
            marker = observation.split("about ", 1)[1].split(" h", 1)[0]
            hours = float(marker)
        except (IndexError, ValueError):
            hours = 0.0
    question, reason = pair
    result = dict(item)
    result["question"] = question.format(hours=float(hours))
    result["reason"] = reason
    return result


def install_agent_language_and_calibration_ui() -> None:
    if getattr(AgentRuntime, "_localized_calibration_installed", False):
        return

    original_system_prompt = runtime_module.agent_system_prompt
    original_deterministic = AgentRuntime.deterministic_calibration_questions
    original_generate = AgentRuntime.generate_calibration_questions

    def system_prompt() -> str:
        language = _LANGUAGE_NAMES.get(current_language(), "English")
        return (
            original_system_prompt()
            + "\nAll user-visible text MUST use the selected interface language ("
            + language
            + "). This includes final answers and the question, reason and statement fields passed "
              "to feedback or personal-learning tools. Never switch those fields back to English."
        )

    def deterministic(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return [localize_calibration_item(item) for item in original_deterministic(self, context)]

    def generate(self, *, model: str, model_context_limit: int | None,
                 event_callback: Callable[[str], None] | None = None,
                 cancel_callback: Callable[[], bool] | None = None):
        phase = {"count": 0}

        def progress(_text: str) -> None:
            if event_callback is None:
                return
            phase["count"] += 1
            if phase["count"] == 1:
                event_callback(_ui("baseline"))
            elif phase["count"] == 2:
                event_callback(_ui("selecting"))
                event_callback(_ui("model"))
            else:
                event_callback(_ui("fallback"))

        context, questions = original_generate(
            self,
            model=model,
            model_context_limit=model_context_limit,
            event_callback=progress,
            cancel_callback=cancel_callback,
        )
        candidates = {
            str(item.get("learning_key") or ""): item
            for item in self.deterministic_calibration_questions(context)
        }
        localized: list[dict[str, Any]] = []
        for item in questions:
            key = str(item.get("learning_key") or "")
            if key in candidates:
                # The local model chooses which data-driven questions are useful,
                # but fixed localized wording prevents an accidental language switch.
                localized.append(dict(candidates[key]))
        return context, localized or list(candidates.values())

    runtime_module.agent_system_prompt = system_prompt
    AgentRuntime.deterministic_calibration_questions = deterministic
    AgentRuntime.generate_calibration_questions = generate
    AgentRuntime._localized_calibration_installed = True

    original_build_ui = CalibrationDialog._build_ui
    original_start = CalibrationDialog._start
    original_ready = CalibrationDialog._ready
    original_failed = CalibrationDialog._failed
    original_reject = CalibrationDialog.reject

    def build_ui(self) -> None:
        original_build_ui(self)
        root = self.layout()
        self.agent_working = QProgressBar()
        self.agent_working.setRange(0, 0)
        self.agent_working.setTextVisible(False)
        self.agent_working.setFixedHeight(9)
        self.agent_working.setToolTip(_ui("model"))
        self.agent_working_details = QPlainTextEdit()
        self.agent_working_details.setReadOnly(True)
        self.agent_working_details.setMaximumHeight(92)
        self.agent_working_details.setPlaceholderText(_ui("working"))
        self.agent_working_details.setPlainText(_ui("starting"))
        # Put the moving indicator and safe operational trace directly below the status.
        root.insertWidget(3, self.agent_working)
        root.insertWidget(4, self.agent_working_details)

    def append_progress(self, text: str) -> None:
        if not text:
            return
        previous = self.agent_working_details.toPlainText().strip()
        if text in previous.splitlines():
            return
        self.agent_working_details.appendPlainText(text)
        bar = self.agent_working_details.verticalScrollBar()
        bar.setValue(bar.maximum())

    def start(self) -> None:
        original_start(self)
        if self.thread is not None:
            self.thread.progress.connect(lambda text: append_progress(self, str(text)))

    def ready(self, context, questions) -> None:
        self.agent_working.setRange(0, 1)
        self.agent_working.setValue(1)
        original_ready(self, context, questions)

    def failed(self, message: str) -> None:
        self.agent_working.setRange(0, 1)
        self.agent_working.setValue(0)
        original_failed(self, message)

    def reject(self) -> None:
        if hasattr(self, "agent_working"):
            self.agent_working.setRange(0, 1)
            self.agent_working.setValue(0)
        original_reject(self)

    CalibrationDialog._build_ui = build_ui
    CalibrationDialog._start = start
    CalibrationDialog._ready = ready
    CalibrationDialog._failed = failed
    CalibrationDialog.reject = reject
