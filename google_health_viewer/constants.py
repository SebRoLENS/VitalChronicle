from __future__ import annotations

from dataclasses import dataclass

API_BASE = "https://health.googleapis.com/v4"
OAUTH_REDIRECT_URI = "http://localhost:8765/"
SCOPE_BASE = "https://www.googleapis.com/auth/googlehealth"

SCOPE_GROUPS = {
    "Attività e forma fisica": f"{SCOPE_BASE}.activity_and_fitness.readonly",
    "Misure e parametri di salute": (f"{SCOPE_BASE}.health_metrics_and_measurements.readonly"),
    "Sonno": f"{SCOPE_BASE}.sleep.readonly",
    "Nutrizione e idratazione": f"{SCOPE_BASE}.nutrition.readonly",
    "Elettrocardiogrammi": f"{SCOPE_BASE}.ecg.readonly",
    "Notifiche di ritmo irregolare": f"{SCOPE_BASE}.irn.readonly",
    "Profilo": f"{SCOPE_BASE}.profile.readonly",
    "Dispositivi e impostazioni": f"{SCOPE_BASE}.settings.readonly",
    "Percorsi GPS degli allenamenti": f"{SCOPE_BASE}.location.readonly",
}

SCOPE_KEYS = {
    "activity": SCOPE_GROUPS["Attività e forma fisica"],
    "health": SCOPE_GROUPS["Misure e parametri di salute"],
    "sleep": SCOPE_GROUPS["Sonno"],
    "nutrition": SCOPE_GROUPS["Nutrizione e idratazione"],
    "ecg": SCOPE_GROUPS["Elettrocardiogrammi"],
    "irn": SCOPE_GROUPS["Notifiche di ritmo irregolare"],
}


@dataclass(frozen=True)
class DataTypeSpec:
    key: str
    label: str
    category: str
    scope: str
    record_type: str
    operation: str = "list"
    filter_field: str | None = None
    auto_sync: bool = True


def _spec(
    key: str,
    label: str,
    category: str,
    scope_key: str,
    record_type: str,
    operation: str = "list",
    filter_field: str | None = "auto",
    auto_sync: bool = True,
) -> DataTypeSpec:
    # Health API filter expressions use snake_case data type identifiers even
    # though the REST response payload uses camelCase field names.
    api_field = key.replace("-", "_")
    if filter_field == "auto":
        suffix = {
            "interval": "interval.start_time",
            "sample": "sample_time.physical_time",
            "daily": "date",
        }.get(record_type)
        filter_field = f"{api_field}.{suffix}" if suffix else None
    return DataTypeSpec(
        key,
        label,
        category,
        SCOPE_KEYS[scope_key],
        record_type,
        operation,
        filter_field,
        auto_sync,
    )


DATA_TYPES = (
    _spec("active-energy-burned", "Energia attiva consumata", "Attività", "activity", "interval"),
    _spec("active-minutes", "Minuti attivi", "Attività", "activity", "interval"),
    _spec("active-zone-minutes", "Minuti in zona attiva", "Attività", "activity", "interval"),
    _spec("activity-level", "Livello di attività", "Attività", "activity", "interval"),
    _spec("altitude", "Altitudine", "Attività", "activity", "interval"),
    _spec(
        "calories-in-heart-rate-zone",
        "Calorie per zona cardiaca",
        "Attività",
        "activity",
        "interval",
        "daily_rollup",
        None,
    ),
    _spec("daily-vo2-max", "VO₂ max giornaliero", "Attività", "activity", "daily"),
    _spec("distance", "Distanza", "Attività", "activity", "interval"),
    _spec(
        "exercise",
        "Allenamenti",
        "Attività",
        "activity",
        "session",
        filter_field="exercise.interval.civil_start_time",
    ),
    _spec("floors", "Piani saliti", "Attività", "activity", "interval", "daily_rollup", None),
    _spec("run-vo2-max", "VO₂ max corsa", "Attività", "activity", "sample"),
    _spec("sedentary-period", "Periodi sedentari", "Attività", "activity", "interval"),
    _spec("steps", "Passi", "Attività", "activity", "interval"),
    _spec("swim-lengths-data", "Vasche di nuoto", "Attività", "activity", "interval"),
    _spec(
        "time-in-heart-rate-zone",
        "Tempo nelle zone cardiache",
        "Attività",
        "activity",
        "interval",
    ),
    _spec(
        "total-calories",
        "Calorie totali",
        "Attività",
        "activity",
        "interval",
        "daily_rollup",
        None,
    ),
    _spec("vo2-max", "VO₂ max", "Attività", "activity", "sample"),
    _spec("blood-glucose", "Glicemia", "Parametri di salute", "health", "sample"),
    _spec("body-fat", "Massa grassa", "Parametri di salute", "health", "sample"),
    _spec(
        "core-body-temperature",
        "Temperatura corporea interna",
        "Parametri di salute",
        "health",
        "sample",
    ),
    _spec(
        "daily-heart-rate-variability",
        "Variabilità cardiaca giornaliera",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec(
        "daily-heart-rate-zones",
        "Zone cardiache giornaliere",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec(
        "daily-oxygen-saturation",
        "Saturazione di ossigeno giornaliera",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec(
        "daily-respiratory-rate",
        "Frequenza respiratoria giornaliera",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec(
        "daily-resting-heart-rate",
        "Frequenza cardiaca a riposo",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec(
        "daily-sleep-temperature-derivations",
        "Variazione della temperatura nel sonno",
        "Parametri di salute",
        "health",
        "daily",
    ),
    _spec("heart-rate", "Frequenza cardiaca", "Parametri di salute", "health", "sample"),
    _spec(
        "heart-rate-variability",
        "Variabilità della frequenza cardiaca",
        "Parametri di salute",
        "health",
        "sample",
    ),
    _spec("height", "Altezza", "Parametri di salute", "health", "sample"),
    _spec(
        "oxygen-saturation",
        "Saturazione di ossigeno",
        "Parametri di salute",
        "health",
        "sample",
    ),
    _spec(
        "respiratory-rate-sleep-summary",
        "Frequenza respiratoria nel sonno",
        "Parametri di salute",
        "health",
        "sample",
    ),
    _spec("weight", "Peso", "Parametri di salute", "health", "sample"),
    _spec(
        "sleep",
        "Sonno",
        "Sonno",
        "sleep",
        "session",
        filter_field="sleep.interval.end_time",
    ),
    _spec(
        "food",
        "Catalogo alimenti",
        "Nutrizione",
        "nutrition",
        "food",
        filter_field=None,
        auto_sync=False,
    ),
    _spec(
        "food-measurement-unit",
        "Unità di misura degli alimenti",
        "Nutrizione",
        "nutrition",
        "food",
        filter_field=None,
        auto_sync=False,
    ),
    _spec(
        "hydration-log",
        "Idratazione",
        "Nutrizione",
        "nutrition",
        "session",
        filter_field="hydration_log.interval.civil_start_time",
    ),
    _spec(
        "nutrition-log",
        "Diario alimentare",
        "Nutrizione",
        "nutrition",
        "session",
        filter_field="nutrition_log.interval.civil_start_time",
    ),
    _spec(
        "electrocardiogram",
        "Elettrocardiogrammi",
        "Cuore",
        "ecg",
        "session",
        filter_field="electrocardiogram.interval.start_time",
    ),
    _spec(
        "irregular-rhythm-notification",
        "Notifiche di ritmo irregolare",
        "Cuore",
        "irn",
        "session",
        filter_field=None,
    ),
)

DATA_TYPE_BY_KEY = {spec.key: spec for spec in DATA_TYPES}
