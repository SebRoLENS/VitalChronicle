from __future__ import annotations

from dataclasses import dataclass

from .i18n import _

API_BASE = "https://health.googleapis.com/v4"
OAUTH_REDIRECT_URI = "http://localhost:8765/"
SCOPE_BASE = "https://www.googleapis.com/auth/googlehealth"

SCOPE_GROUPS = {
    _("Activity and fitness"): f"{SCOPE_BASE}.activity_and_fitness.readonly",
    _("Health metrics and measurements"): (f"{SCOPE_BASE}.health_metrics_and_measurements.readonly"),
    _("Sleep"): f"{SCOPE_BASE}.sleep.readonly",
    _("Nutrition and hydration"): f"{SCOPE_BASE}.nutrition.readonly",
    _("Electrocardiograms"): f"{SCOPE_BASE}.ecg.readonly",
    _("Irregular rhythm notifications"): f"{SCOPE_BASE}.irn.readonly",
    _("Profile"): f"{SCOPE_BASE}.profile.readonly",
    _("Devices and settings"): f"{SCOPE_BASE}.settings.readonly",
    _("Workout GPS routes"): f"{SCOPE_BASE}.location.readonly",
}

SCOPE_KEYS = {
    "activity": SCOPE_GROUPS[_("Activity and fitness")],
    "health": SCOPE_GROUPS[_("Health metrics and measurements")],
    "sleep": SCOPE_GROUPS[_("Sleep")],
    "nutrition": SCOPE_GROUPS[_("Nutrition and hydration")],
    "ecg": SCOPE_GROUPS[_("Electrocardiograms")],
    "irn": SCOPE_GROUPS[_("Irregular rhythm notifications")],
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
    _spec("active-energy-burned", _("Active energy burned"), _("Activity"), "activity", "interval"),
    _spec("active-minutes", _("Active minutes"), _("Activity"), "activity", "interval"),
    _spec("active-zone-minutes", _("Active zone minutes"), _("Activity"), "activity", "interval"),
    _spec("activity-level", _("Activity level"), _("Activity"), "activity", "interval"),
    _spec("altitude", _("Altitude"), _("Activity"), "activity", "interval"),
    _spec(
        "calories-in-heart-rate-zone",
        _("Calories by heart-rate zone"),
        _("Activity"),
        "activity",
        "interval",
        "daily_rollup",
        None,
    ),
    _spec("daily-vo2-max", _("Daily VO₂ max"), _("Activity"), "activity", "daily"),
    _spec("distance", _("Distance"), _("Activity"), "activity", "interval"),
    _spec(
        "exercise",
        _("Workouts"),
        _("Activity"),
        "activity",
        "session",
        filter_field="exercise.interval.civil_start_time",
    ),
    _spec("floors", _("Floors climbed"), _("Activity"), "activity", "interval", "daily_rollup", None),
    _spec("run-vo2-max", _("Running VO₂ max"), _("Activity"), "activity", "sample"),
    _spec("sedentary-period", _("Sedentary periods"), _("Activity"), "activity", "interval"),
    _spec("steps", _("Steps"), _("Activity"), "activity", "interval"),
    _spec("swim-lengths-data", _("Swimming lengths"), _("Activity"), "activity", "interval"),
    _spec(
        "time-in-heart-rate-zone",
        _("Time in heart-rate zones"),
        _("Activity"),
        "activity",
        "interval",
    ),
    _spec(
        "total-calories",
        _("Total calories"),
        _("Activity"),
        "activity",
        "interval",
        "daily_rollup",
        None,
    ),
    _spec("vo2-max", _("VO₂ max"), _("Activity"), "activity", "sample"),
    _spec("blood-glucose", _("Blood glucose"), _("Health metrics"), "health", "sample"),
    _spec("body-fat", _("Body fat"), _("Health metrics"), "health", "sample"),
    _spec(
        "core-body-temperature",
        _("Core body temperature"),
        _("Health metrics"),
        "health",
        "sample",
    ),
    _spec(
        "daily-heart-rate-variability",
        _("Daily heart-rate variability"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec(
        "daily-heart-rate-zones",
        _("Daily heart-rate zones"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec(
        "daily-oxygen-saturation",
        _("Daily oxygen saturation"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec(
        "daily-respiratory-rate",
        _("Daily respiratory rate"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec(
        "daily-resting-heart-rate",
        _("Resting heart rate"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec(
        "daily-sleep-temperature-derivations",
        _("Sleep temperature variation"),
        _("Health metrics"),
        "health",
        "daily",
    ),
    _spec("heart-rate", _("Heart rate"), _("Health metrics"), "health", "sample"),
    _spec(
        "heart-rate-variability",
        _("Heart-rate variability"),
        _("Health metrics"),
        "health",
        "sample",
    ),
    _spec("height", _("Height"), _("Health metrics"), "health", "sample"),
    _spec(
        "oxygen-saturation",
        _("Oxygen saturation"),
        _("Health metrics"),
        "health",
        "sample",
    ),
    _spec(
        "respiratory-rate-sleep-summary",
        _("Respiratory rate during sleep"),
        _("Health metrics"),
        "health",
        "sample",
    ),
    _spec("weight", _("Weight"), _("Health metrics"), "health", "sample"),
    _spec(
        "sleep",
        _("Sleep"),
        _("Sleep"),
        "sleep",
        "session",
        filter_field="sleep.interval.end_time",
    ),
    _spec(
        "food",
        _("Food catalogue"),
        _("Nutrition"),
        "nutrition",
        "food",
        filter_field=None,
        auto_sync=False,
    ),
    _spec(
        "food-measurement-unit",
        _("Food measurement units"),
        _("Nutrition"),
        "nutrition",
        "food",
        filter_field=None,
        auto_sync=False,
    ),
    _spec(
        "hydration-log",
        _("Hydration"),
        _("Nutrition"),
        "nutrition",
        "session",
        filter_field="hydration_log.interval.civil_start_time",
    ),
    _spec(
        "nutrition-log",
        _("Nutrition log"),
        _("Nutrition"),
        "nutrition",
        "session",
        filter_field="nutrition_log.interval.civil_start_time",
    ),
    _spec(
        "electrocardiogram",
        _("Electrocardiograms"),
        _("Heart"),
        "ecg",
        "session",
        filter_field="electrocardiogram.interval.start_time",
    ),
    _spec(
        "irregular-rhythm-notification",
        _("Irregular rhythm notifications"),
        _("Heart"),
        "irn",
        "session",
        filter_field=None,
    ),
)

DATA_TYPE_BY_KEY = {spec.key: spec for spec in DATA_TYPES}
