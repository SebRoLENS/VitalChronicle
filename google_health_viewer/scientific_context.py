"""Curated scientific interpretation context for VitalChronicle health metrics.

This module is deliberately separate from deterministic measurements.  The snapshot says
what was observed in the user's data; this catalogue says what a metric can plausibly mean,
which confounders matter, and which conclusions are not justified from a wearable metric alone.
The local language model may supplement this background with its own general knowledge, but
must never treat the background as evidence that a listed cause applies to the user.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

KNOWLEDGE_BASE_VERSION = "scientific-context-v1"

SOURCES: dict[str, dict[str, str]] = {
    "JACC_WEARABLES_2023": {
        "title": "Consumer Wearable Health and Fitness Technology in Cardiovascular Medicine",
        "year": "2023",
        "kind": "state-of-the-art review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10662962/",
    },
    "HRV_WEARABLE_2023": {
        "title": "Heart Rate Variability Measurement through a Smart Wearable Device",
        "year": "2023",
        "kind": "review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10742885/",
    },
    "HRV_TRAINING_2024": {
        "title": "Heart Rate Variability Applications in Strength and Conditioning",
        "year": "2024",
        "kind": "narrative review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11204851/",
    },
    "RESPIRATORY_RATE_2020": {
        "title": "The Importance of Respiratory Rate Monitoring: From Healthcare to Sport and Exercise",
        "year": "2020",
        "kind": "review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7665156/",
    },
    "SLEEP_STAGING_2021": {
        "title": "A Systematic Review of Sensing Technologies for Wearable Sleep Staging",
        "year": "2021",
        "kind": "systematic review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7956647/",
    },
    "SLEEP_RELIABILITY_2024": {
        "title": "Evaluating reliability in wearable devices for sleep staging",
        "year": "2024",
        "kind": "scoping review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10948771/",
    },
    "PPG_ROADMAP_2023": {
        "title": "The 2023 wearable photoplethysmography roadmap",
        "year": "2023",
        "kind": "roadmap/review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10686289/",
    },
    "VO2_INTERLIVE_2022": {
        "title": "Validity of Estimating the Maximal Oxygen Consumption by Consumer Wearables",
        "year": "2022",
        "kind": "systematic review and meta-analysis",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9213394/",
    },
    "WEARABLE_ACTIVITY_2022": {
        "title": "Wearable activity trackers—advanced technology or advanced marketing?",
        "year": "2022",
        "kind": "review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9022022/",
    },
    "WEARABLE_INFECTION_2022": {
        "title": "The performance of wearable sensors in the detection of SARS-CoV-2 infection",
        "year": "2022",
        "kind": "systematic review",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9020803/",
    },
    "WHO_ACTIVITY_2020": {
        "title": "WHO guidelines on physical activity and sedentary behaviour",
        "year": "2020",
        "kind": "guideline",
        "url": "https://www.who.int/publications/i/item/9789240015128",
    },
    "CDC_BMI_2024": {
        "title": "CDC Body Mass Index guidance",
        "year": "2024",
        "kind": "public-health guidance",
        "url": "https://www.cdc.gov/bmi/faq/index.html",
    },
}


def _topic(
    meaning: str,
    *,
    baseline_rule: str,
    higher: list[str] | None = None,
    lower: list[str] | None = None,
    confounders: list[str] | None = None,
    relationships: list[str] | None = None,
    limitations: list[str] | None = None,
    sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "meaning": meaning,
        "baseline_rule": baseline_rule,
        "higher": higher or [],
        "lower": lower or [],
        "confounders": confounders or [],
        "relationships": relationships or [],
        "limitations": limitations or [],
        "source_ids": list(sources),
    }


TOPICS: dict[str, dict[str, Any]] = {
    "activity_volume": _topic(
        "Movement-volume metrics such as steps, distance, active minutes and floors estimate how much physical activity occurred. They describe behaviour and workload, not fitness by themselves.",
        baseline_rule="Interpret trends against the person's usual activity, day type and measurement completeness; a partial day must not be compared directly with completed days.",
        higher=[
            "Often reflects more locomotion or longer/more intense activity, which is generally favourable when sustainable.",
            "A sudden increase can also represent an unusually heavy workload and may help explain higher heart rate, lower short-term HRV or greater fatigue afterwards.",
        ],
        lower=[
            "May reflect rest/recovery, illness, travel, sedentary behaviour, schedule changes or missing wear time.",
            "Persistent reductions matter more when confirmed by other activity metrics and complete device coverage.",
        ],
        confounders=["device non-wear", "activity type poorly captured by wrist motion", "stride/algorithm error", "terrain", "assistive transport"],
        relationships=["heart rate", "active-zone minutes", "exercise sessions", "energy expenditure", "sleep/recovery"],
        limitations=["Consumer wearables vary in validity by metric and activity type; step count is usually more robust than energy expenditure."],
        sources=("WEARABLE_ACTIVITY_2022", "WHO_ACTIVITY_2020"),
    ),
    "energy": _topic(
        "Active or total energy expenditure is an algorithmic estimate of metabolic energy use, commonly derived from motion, heart rate and personal characteristics.",
        baseline_rule="Use within-device longitudinal changes more confidently than exact calorie values; absolute wearable calorie estimates can have substantial error.",
        higher=["Can accompany greater activity duration/intensity, larger body size, heat stress or elevated heart rate."],
        lower=["Can accompany lower activity, rest or incomplete wear time."],
        confounders=["device algorithm", "body-profile settings", "exercise modality", "heart-rate sensor error", "non-wear"],
        relationships=["steps", "exercise", "heart rate", "active minutes"],
        limitations=["Do not infer energy balance, weight change or dietary adequacy from wearable expenditure alone."],
        sources=("WEARABLE_ACTIVITY_2022",),
    ),
    "heart_rate": _topic(
        "Heart rate is the number of cardiac beats per minute. It responds rapidly to exercise and also to autonomic tone, temperature, posture, hydration, stress, stimulants, illness and many medications.",
        baseline_rule="Interpret heart rate in context and relative to the individual's own time-of-day and activity-matched baseline. Resting and exercising heart rate are physiologically different states.",
        higher=[
            "During exercise, a rise is expected and should be interpreted with exercise intensity and duration.",
            "Outside exercise, a sustained rise from personal baseline can accompany heat, dehydration, stress, poor recovery, infection/inflammation, pain, stimulants or medications, among many other causes.",
        ],
        lower=[
            "A lower resting value can accompany endurance training or recovery, but can also reflect medication effects, conduction abnormalities or sensor error.",
            "An unexpectedly low value is not automatically beneficial; context and symptoms matter.",
        ],
        confounders=["movement artefact", "poor skin contact", "skin perfusion", "exercise modality", "temperature", "medications", "caffeine/nicotine"],
        relationships=["exercise/activity level", "HRV", "temperature", "respiratory rate", "sleep", "oxygen saturation"],
        limitations=["Wrist PPG is less accurate during some forms of exercise than ECG/chest-strap measurement; isolated outliers should be treated cautiously."],
        sources=("JACC_WEARABLES_2023", "PPG_ROADMAP_2023", "WEARABLE_INFECTION_2022"),
    ),
    "resting_heart_rate": _topic(
        "Resting heart rate is a low-activity estimate intended to reflect basal cardiac rate with minimal acute exertional influence.",
        baseline_rule="Personal longitudinal baseline is more informative than population comparison; verify that changes are not explained by exercise, sleep, temperature or measurement conditions.",
        higher=["Can occur with reduced recovery, heat, dehydration, acute illness/inflammation, psychological stress, pain, stimulants or deconditioning."],
        lower=["Can accompany endurance adaptation, improved recovery or medication effects; marked unexplained decreases are not inherently favourable."],
        confounders=["measurement timing", "sleep/wake state", "recent exercise", "medications", "illness", "sensor quality"],
        relationships=["HRV", "sleep", "temperature", "respiratory rate", "recent exercise"],
        limitations=["A deviation is a nonspecific physiological signal, not a diagnosis."],
        sources=("JACC_WEARABLES_2023", "WEARABLE_INFECTION_2022"),
    ),
    "hrv": _topic(
        "Heart-rate variability (HRV) quantifies beat-to-beat timing variation and is strongly influenced by autonomic regulation. Time-domain indices such as RMSSD are commonly used by wearables as recovery/stress markers.",
        baseline_rule="Prefer consistent measurement conditions and the person's own rolling baseline. Absolute HRV values are difficult to compare across people, devices, algorithms and times of day.",
        higher=["Relative increases can accompany stronger vagal modulation, recovery or training adaptation, but a single high value is not automatically better."],
        lower=["Relative decreases can accompany recent intense exercise, poor sleep, alcohol, dehydration, psychological stress, acute illness, pain, travel or medications."],
        confounders=["measurement posture/time", "breathing pattern", "ectopic beats/artefact filtering", "recent exercise", "alcohol/nicotine", "hydration", "sleep", "medications"],
        relationships=["resting heart rate", "exercise load", "sleep", "temperature", "respiratory rate"],
        limitations=["HRV should not be simplified to a direct measure of sympathetic activity or 'sympathovagal balance'; interpretation depends on the HRV metric used."],
        sources=("HRV_WEARABLE_2023", "HRV_TRAINING_2024", "JACC_WEARABLES_2023"),
    ),
    "oxygen_saturation": _topic(
        "Peripheral oxygen saturation (SpO2) estimates the fraction of haemoglobin carrying oxygen using optical pulse oximetry/PPG.",
        baseline_rule="Look for repeated, technically plausible deviations from the individual's baseline and measurement context rather than relying on a single consumer-wearable value.",
        higher=["Within the normal physiological range, small upward fluctuations usually have little interpretive value."],
        lower=["True reductions may occur with respiratory or cardiopulmonary problems, sleep-disordered breathing or altitude, but motion/perfusion/sensor artefact can also lower readings."],
        confounders=["motion", "low peripheral perfusion", "sensor fit", "skin characteristics", "altitude", "device algorithm"],
        relationships=["respiratory rate", "sleep", "heart rate", "altitude"],
        limitations=["Consumer SpO2 is not equivalent to arterial blood-gas measurement and accuracy worsens in some conditions, particularly at lower saturation."],
        sources=("PPG_ROADMAP_2023", "JACC_WEARABLES_2023"),
    ),
    "respiratory_rate": _topic(
        "Respiratory rate is breaths per minute. It is sensitive to metabolic demand and to multiple physiological stressors, including exercise, heat/cold, emotional stress and illness.",
        baseline_rule="A sustained change from a stable personal resting/sleep baseline is generally more informative than an isolated value.",
        higher=["Can accompany exercise, heat stress, anxiety, pain, fever/infection, respiratory or cardiovascular stress and other causes."],
        lower=["Can occur during deeper sleep/relaxation, with some medications or with altered respiratory control; isolated low wearable estimates may be artefactual."],
        confounders=["sleep stage", "exercise", "talking", "posture", "temperature", "device inference method"],
        relationships=["heart rate", "temperature", "SpO2", "sleep", "exercise"],
        limitations=["It is sensitive but nonspecific: the same directional change can have many causes."],
        sources=("RESPIRATORY_RATE_2020", "WEARABLE_INFECTION_2022"),
    ),
    "temperature": _topic(
        "Wearable temperature metrics may represent skin/peripheral temperature, a nocturnal deviation from personal baseline, or an algorithmic estimate. They are not automatically equivalent to core body temperature.",
        baseline_rule="Interpret direction and persistence relative to the same device's personal baseline and similar measurement conditions; first establish what anatomical/derived temperature the field represents.",
        higher=[
            "Can reflect a warmer environment, bedding/clothing, altered skin perfusion, circadian effects or exercise/heat exposure.",
            "A sustained rise together with higher resting heart rate and/or respiratory rate, lower HRV and altered sleep can be compatible with systemic physiological stress such as infection/inflammation, but remains nonspecific.",
        ],
        lower=["Can reflect a cooler environment, altered peripheral perfusion, circadian timing, device contact or other behavioural/physiological changes."],
        confounders=["ambient temperature", "bedding/clothing", "sensor location/contact", "circadian phase", "exercise", "peripheral perfusion"],
        relationships=["resting heart rate", "HRV", "respiratory rate", "sleep", "activity"],
        limitations=["Do not label a wearable skin-temperature change as fever unless the measurement is explicitly a validated core-temperature estimate; inflammation/infection is only one possible explanation."],
        sources=("WEARABLE_INFECTION_2022", "JACC_WEARABLES_2023"),
    ),
    "vo2max": _topic(
        "VO2 max estimates maximal aerobic oxygen uptake and is a marker of cardiorespiratory fitness. Consumer wearables usually estimate it indirectly from heart rate, speed/workload and personal characteristics rather than measuring respiratory gases.",
        baseline_rule="Use longitudinal trends under comparable exercise conditions more confidently than small single-session changes.",
        higher=["A sustained increase can reflect improved aerobic fitness or favourable algorithm inputs/training performance."],
        lower=["A sustained decrease can reflect detraining, fatigue/illness, changed exercise conditions, heat, altitude or estimation error."],
        confounders=["exercise protocol", "heart-rate accuracy", "terrain", "temperature", "altitude", "device algorithm"],
        relationships=["exercise volume/intensity", "heart rate", "pace/distance", "recovery"],
        limitations=["Individual wearable VO2-max estimation error can be substantial; exercise-based estimates tend to perform better than resting estimates but are not laboratory gas-exchange tests."],
        sources=("VO2_INTERLIVE_2022", "JACC_WEARABLES_2023"),
    ),
    "sleep": _topic(
        "Sleep metrics estimate sleep duration, timing, awakenings and architecture. Consumer wearables infer sleep and stages from movement and physiological signals; polysomnography remains the reference method for clinical sleep staging.",
        baseline_rule="Emphasize repeated patterns in duration, continuity, timing and awakenings. Treat individual stage percentages as estimates rather than exact neurophysiological measurements.",
        higher=["More total sleep may reflect recovery or increased sleep opportunity, but unusually long sleep can also occur after sleep debt, illness or schedule change."],
        lower=["Reduced duration or fragmented sleep can follow schedule constraints, stress, environment, exercise timing, alcohol, illness and many other causes."],
        confounders=["quiet wake misclassified as sleep", "device algorithm", "sensor contact", "sleep environment", "irregular schedules", "naps"],
        relationships=["resting heart rate", "HRV", "temperature", "respiratory rate", "activity/exercise"],
        limitations=["Wearable sleep-stage classification is less reliable than polysomnography; do not diagnose sleep disorders from stage estimates or awakening counts alone."],
        sources=("SLEEP_STAGING_2021", "SLEEP_RELIABILITY_2024"),
    ),
    "glucose": _topic(
        "Blood-glucose measurements reflect circulating glucose concentration and are strongly influenced by meals, fasting state, exercise, hormones, stress, illness and glucose-regulating medications.",
        baseline_rule="Interpret only with measurement method and timing (fasting, post-meal, random) known. Clinical thresholds depend on validated measurement protocols and should not be inferred from an unspecified wearable field.",
        higher=["Can occur after carbohydrate intake and with stress hormones, illness or impaired glucose regulation, among other causes."],
        lower=["Can occur with fasting, prolonged exercise, some medications or inadequate intake; sensor/measurement artefact is also possible."],
        confounders=["meal timing", "exercise", "measurement method", "medications", "illness/stress"],
        relationships=["nutrition", "exercise", "sleep", "weight/body composition"],
        limitations=["VitalChronicle should not diagnose diabetes or hypoglycaemia from consumer data alone; clinical interpretation requires validated methods and context."],
        sources=(),
    ),
    "weight": _topic(
        "Body weight is total body mass. Short-term changes often reflect fluid balance and gastrointestinal contents; longer-term trends can reflect changes in fat mass, lean mass and/or fluid status.",
        baseline_rule="Use a trend across repeated measurements under similar conditions; day-to-day changes should not automatically be interpreted as fat gain or loss.",
        higher=["Can reflect increased tissue mass, fluid retention, food/gut contents or measurement conditions."],
        lower=["Can reflect tissue loss, fluid loss/dehydration, glycogen-associated water shifts or measurement conditions."],
        confounders=["time of day", "hydration", "meals", "clothing", "scale placement/calibration"],
        relationships=["body fat estimate", "activity", "nutrition", "hydration"],
        limitations=["Weight and BMI are screening/context measures, not complete measures of health or body composition."],
        sources=("CDC_BMI_2024",),
    ),
    "body_fat": _topic(
        "Body-fat percentage estimates the fraction of body mass attributed to adipose tissue. Consumer scales commonly use bioelectrical impedance and are sensitive to hydration and algorithm assumptions.",
        baseline_rule="Interpret longer-term within-device trends under standardized conditions; small daily changes are often measurement/fluid noise.",
        higher=["A sustained increase may reflect increased fat mass, but short-term increases can be driven by hydration/impedance variation."],
        lower=["A sustained decrease may reflect fat loss, but short-term decreases can be driven by hydration/impedance variation."],
        confounders=["hydration", "recent exercise", "skin temperature", "meals", "device algorithm"],
        relationships=["weight", "activity", "nutrition"],
        limitations=["Consumer bioimpedance is not equivalent to reference body-composition methods."],
        sources=("WEARABLE_ACTIVITY_2022",),
    ),
    "hydration": _topic(
        "Hydration logs record reported fluid intake, not whole-body hydration status. Hydration status also depends on losses through urine, sweat and respiration and on dietary water/electrolytes.",
        baseline_rule="Interpret intake in context of body size, climate, exercise/sweat losses and other fluids/foods; a logged volume is incomplete if logging is incomplete.",
        higher=["Can reflect deliberate rehydration, hot weather, exercise, thirst or increased logging."],
        lower=["Can reflect lower intake or simply incomplete logging."],
        confounders=["manual logging completeness", "food water", "sweat loss", "climate", "exercise"],
        relationships=["exercise", "temperature", "heart rate", "weight"],
        limitations=["Do not infer dehydration solely from low logged intake."],
        sources=(),
    ),
    "nutrition": _topic(
        "Nutrition logs describe reported food/energy/nutrient intake. They can provide behavioural context for weight, glucose and activity but are usually incomplete and subject to portion-size and database error.",
        baseline_rule="Treat logged intake as an observed subset unless completeness is known; compare like periods and avoid assuming unlogged food equals zero intake.",
        higher=["Higher logged energy or nutrient intake may represent true intake changes or better logging completeness."],
        lower=["Lower logged intake may represent true intake changes or missing entries."],
        confounders=["under-reporting", "portion estimates", "food database", "missing meals"],
        relationships=["weight", "glucose", "activity", "hydration"],
        limitations=["Do not diagnose nutrient deficiency or excess from sparse food logs alone."],
        sources=(),
    ),
    "altitude": _topic(
        "Altitude is environmental elevation and is primarily contextual rather than a physiological outcome. Higher altitude lowers ambient oxygen pressure and can influence oxygen saturation, heart rate, breathing and exercise performance.",
        baseline_rule="Use altitude to explain concurrent physiological measurements rather than treating altitude itself as a health improvement or deterioration.",
        higher=["May plausibly contribute to lower SpO2, higher respiratory/heart rate and altered exercise capacity depending on elevation and acclimatization."],
        lower=["Returning to lower altitude can reverse altitude-related physiological stress."],
        confounders=["GPS/barometer error", "indoor location", "device calibration"],
        relationships=["SpO2", "respiratory rate", "heart rate", "VO2 max/exercise"],
        limitations=["Effects depend strongly on absolute altitude, ascent rate, acclimatization and individual susceptibility."],
        sources=("JACC_WEARABLES_2023",),
    ),
    "zones": _topic(
        "Heart-rate zones classify exercise intensity using heart-rate thresholds. Time or calories in zones are derived workload descriptors, not independent physiological measurements.",
        baseline_rule="Interpret with the zone-definition method and the person's exercise context; thresholds may be estimated and can change with settings or fitness.",
        higher=["More time in higher zones generally indicates greater cardiovascular intensity if the heart-rate data and thresholds are valid."],
        lower=["More time in lower zones can reflect easier/recovery activity or different exercise modality."],
        confounders=["zone threshold settings", "heart-rate sensor error", "medications affecting heart rate", "temperature", "fitness changes"],
        relationships=["heart rate", "exercise", "active minutes", "energy expenditure"],
        limitations=["Do not equate a zone label with a direct measurement of lactate threshold or metabolic substrate use unless specifically validated."],
        sources=("JACC_WEARABLES_2023",),
    ),
    "exercise": _topic(
        "Exercise-session records identify structured bouts of physical activity, their timing, duration and type. They provide crucial context for interpreting simultaneous heart-rate and short-term recovery changes.",
        baseline_rule="Interpret physiological changes during and after exercise relative to exercise type, duration and intensity and to comparable previous sessions.",
        higher=["Greater duration/intensity can explain higher heart rate and may transiently lower HRV or increase temperature/respiratory rate during recovery."],
        lower=["Reduced training load can reflect planned recovery, schedule change, illness or reduced activity."],
        confounders=["auto-detection errors", "misclassified exercise type", "unrecorded workouts"],
        relationships=["heart rate", "HRV", "temperature", "respiratory rate", "activity volume", "VO2 max"],
        limitations=["Exercise timing overlap supports contextual attribution but does not prove that every concurrent physiological change was caused by exercise."],
        sources=("JACC_WEARABLES_2023", "WHO_ACTIVITY_2020"),
    ),
    "ecg": _topic(
        "A wearable ECG records cardiac electrical activity, usually from a limited-lead configuration. It can characterize rhythm during the recording but does not replace a clinical 12-lead ECG for many diagnostic questions.",
        baseline_rule="Interpret the actual tracing, recording quality, symptoms and rhythm classification together; a device label alone is insufficient for diagnosis.",
        higher=[],
        lower=[],
        confounders=["motion", "poor electrode contact", "short recording duration", "algorithm classification limits"],
        relationships=["heart rate", "irregular-rhythm notifications", "symptom timing"],
        limitations=["Automated wearable ECG/rhythm labels can be false positive or false negative; clinically important findings require professional review."],
        sources=("JACC_WEARABLES_2023",),
    ),
    "irregular_rhythm": _topic(
        "An irregular-rhythm notification is an algorithmic event indicating that pulse/rhythm patterns met the device's detection criteria. It is a screening signal rather than a confirmed arrhythmia diagnosis.",
        baseline_rule="Treat occurrence, recurrence, recording quality and confirmatory ECG information as more informative than a notification alone.",
        higher=[],
        lower=[],
        confounders=["motion/artefact", "ectopic beats", "algorithm/device characteristics"],
        relationships=["ECG", "heart rate", "symptom timing"],
        limitations=["A notification cannot determine all arrhythmia types and absence of notifications does not exclude arrhythmia."],
        sources=("JACC_WEARABLES_2023",),
    ),
    "height": _topic(
        "Height is a relatively stable anthropometric characteristic in adults and is mainly useful for normalization or derived measures such as BMI and some fitness estimates.",
        baseline_rule="Adult short-term variation is usually measurement/posture error rather than biological change.",
        higher=[],
        lower=[],
        confounders=["measurement posture", "time of day", "manual entry error"],
        relationships=["weight", "BMI/body composition", "VO2 normalization"],
        limitations=["Do not interpret small adult height changes from consumer records as a physiological trend."],
        sources=("CDC_BMI_2024",),
    ),
    "catalogue": _topic(
        "Catalogue/reference records describe available foods or measurement units and are metadata rather than physiological measurements.",
        baseline_rule="Do not interpret catalogue records as evidence of consumption or health change.",
        limitations=["Reference metadata should not enter physiological trend or association calculations."],
        sources=(),
    ),
}


DATA_TYPE_TO_TOPIC: dict[str, str] = {
    "active-energy-burned": "energy",
    "active-minutes": "activity_volume",
    "active-zone-minutes": "zones",
    "activity-level": "activity_volume",
    "altitude": "altitude",
    "calories-in-heart-rate-zone": "zones",
    "daily-vo2-max": "vo2max",
    "distance": "activity_volume",
    "exercise": "exercise",
    "floors": "activity_volume",
    "run-vo2-max": "vo2max",
    "sedentary-period": "activity_volume",
    "steps": "activity_volume",
    "swim-lengths-data": "activity_volume",
    "time-in-heart-rate-zone": "zones",
    "total-calories": "energy",
    "vo2-max": "vo2max",
    "blood-glucose": "glucose",
    "body-fat": "body_fat",
    "core-body-temperature": "temperature",
    "daily-heart-rate-variability": "hrv",
    "daily-heart-rate-zones": "zones",
    "daily-oxygen-saturation": "oxygen_saturation",
    "daily-respiratory-rate": "respiratory_rate",
    "daily-resting-heart-rate": "resting_heart_rate",
    "daily-sleep-temperature-derivations": "temperature",
    "heart-rate": "heart_rate",
    "heart-rate-variability": "hrv",
    "height": "height",
    "oxygen-saturation": "oxygen_saturation",
    "respiratory-rate-sleep-summary": "respiratory_rate",
    "weight": "weight",
    "sleep": "sleep",
    "food": "catalogue",
    "food-measurement-unit": "catalogue",
    "hydration-log": "hydration",
    "nutrition-log": "nutrition",
    "electrocardiogram": "ecg",
    "irregular-rhythm-notification": "irregular_rhythm",
}


def scientific_context_for(data_type: str, *, detailed: bool = False) -> dict[str, Any] | None:
    topic_name = DATA_TYPE_TO_TOPIC.get(str(data_type))
    if topic_name is None:
        return None
    source = TOPICS[topic_name]
    if detailed:
        result = deepcopy(source)
        result["topic"] = topic_name
        result["sources"] = [
            {"source_id": source_id, **SOURCES[source_id]}
            for source_id in source["source_ids"]
            if source_id in SOURCES
        ]
        return result
    return {
        "topic": topic_name,
        "meaning": source["meaning"],
        "baseline_rule": source["baseline_rule"],
        "higher": source["higher"][:1],
        "lower": source["lower"][:1],
        "limitations": source["limitations"][:1],
        "source_ids": list(source["source_ids"]),
    }


def scientific_context_for_types(
    data_types: list[str] | tuple[str, ...] | set[str],
    *,
    detailed: bool = False,
    maximum: int | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for data_type in data_types:
        key = str(data_type)
        if key in result:
            continue
        context = scientific_context_for(key, detailed=detailed)
        if context is not None:
            result[key] = context
        if maximum is not None and len(result) >= maximum:
            break
    return result
