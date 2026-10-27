"""Render verified local calculations without asking a small model to redo arithmetic.

The agent still chooses tools/arguments. These focused answers use only their full
Python results, not the sampled evidence or the model's draft. Open-ended coaching
and custom analyses continue through the normal synthesis path.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def single_date_focus(question: str) -> str | None:
    """Conservative focus for exact-date facts, not a general language planner."""
    text = question.casefold()
    iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    without_iso = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    if re.search(
        r"\b(?:dal|dall|from|between|tra|fra)\b|\d\s*[–-]\s*\d|\b(?:o|or|and|oppure)\s+\d",
        without_iso,
    ):
        return None
    months = [
        ("gennaio", "january"),
        ("febbraio", "february"),
        ("marzo", "march"),
        ("aprile", "april"),
        ("maggio", "may"),
        ("giugno", "june"),
        ("luglio", "july"),
        ("agosto", "august"),
        ("settembre", "september"),
        ("ottobre", "october"),
        ("novembre", "november"),
        ("dicembre", "december"),
    ]
    found = set(iso)
    for month, names in enumerate(months, 1):
        for name in names:
            matches = re.findall(rf"\b(\d{{1,2}})\s+{name}\s+(\d{{4}})\b", text)
            matches += re.findall(rf"\b{name}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", text)
            for day, year in matches:
                try:
                    found.add(date(int(year), month, int(day)).isoformat())
                except ValueError:
                    return None
    return next(iter(found)) if len(found) == 1 else None


def comparison_roles(question: str) -> set[str]:
    text = question.casefold()
    markers = {
        "hrv": ("hrv", "variabilit"),
        "sleep": ("sonno", "sleep", "dorm", "stanotte"),
        "rhr": ("riposo", "resting", "rhr", "fcr"),
        "weight": ("peso", "weight"),
        "steps": ("passi", "steps"),
        "temperature": ("temperatur",),
        "oxygen": ("ossigen", "oxygen", "saturaz", "spo2"),
        "respiratory": ("respirat", "breaths"),
    }
    roles = {role for role, words in markers.items() if any(w in text for w in words)}
    if "temperature" in roles and not any(
        w in text for w in ("durata", "duration", "ore di sonno")
    ):
        roles.discard("sleep")
    return roles


def metric_role(result: dict[str, Any]) -> str:
    metric = str(result.get("data_type") or result.get("metric") or "").casefold()
    if "variability" in metric or metric == "hrv":
        return "hrv"
    if "temperature" in metric:
        return "temperature"
    if "oxygen" in metric:
        return "oxygen"
    if "respiratory" in metric:
        return "respiratory"
    if "sleep" in metric:
        return "sleep"
    if "resting" in metric or metric in {"rhr", "fcr"}:
        return "rhr"
    if "weight" in metric:
        return "weight"
    if metric.startswith("steps"):
        return "steps"
    return metric.split(":", 1)[0]


def render_fact_answer(
    question: str,
    results: list[tuple[str, dict[str, Any]]],
    required_tools: set[str],
    language: str,
) -> str | None:
    if language not in {"it", "en"}:
        return None
    text = question.casefold()
    if any(
        word in text
        for word in (
            "consigl",
            "raccomand",
            "programma",
            "piano di",
            "recommend",
            "should i",
            "crea un",
            "create a",
            "monitoraggio",
            "promemoria",
        )
    ):
        return None
    it = language == "it"

    def tr(italian, english):
        return italian if it else english

    def num(value, places=2):
        if value is None:
            return tr("non disponibile", "unavailable")
        s = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
        return s.replace(".", ",") if it else s

    def duration(minutes):
        if minutes is None:
            return tr("non disponibile", "unavailable")
        value = round(float(minutes), 2)
        return f"{int(value // 60)} h {num(value % 60)} min"

    labels = {
        "hrv": tr("HRV giornaliera", "Daily HRV"),
        "sleep": tr("Sonno registrato", "Recorded sleep"),
        "rhr": tr("Frequenza cardiaca a riposo", "Resting heart rate"),
        "weight": tr("Peso", "Weight"),
        "steps": tr("Passi", "Steps"),
        "temperature": tr("Temperatura notturna registrata", "Recorded nightly temperature"),
        "oxygen": tr("Saturazione registrata", "Recorded oxygen saturation"),
        "respiratory": tr("Frequenza respiratoria registrata", "Recorded respiratory rate"),
    }

    def latest(name):
        return next((r for n, r in reversed(results) if n == name and not r.get("error")), None)

    if "summarize_health_overview" in required_tools:
        r = latest("summarize_health_overview")
        if r is None:
            return None
        lines = [
            tr(
                f"Panoramica delle misure registrate: **{r['period']['start']}–{r['period']['end']}**.",
                f"Overview of recorded measurements: **{r['period']['start']}–{r['period']['end']}**.",
            )
        ]
        for item in r["metrics"]:
            label = labels.get(metric_role(item), item["data_type"])
            s = item.get("summary")
            lines += [
                "",
                f"**{label} ({item['unit']})**",
                "",
                tr(
                    f"Giorni osservati: {item['observed_days']}/{item['expected_days']}.",
                    f"Observed days: {item['observed_days']}/{item['expected_days']}.",
                ),
            ]
            if not s:
                lines.append(tr("Nessuna osservazione disponibile.", "No observations available."))
                continue
            lines.append("")
            lines += [
                tr(
                    f"- Media: **{num(s['mean'])}**; mediana: {num(s['median'])} {item['unit']}.",
                    f"- Mean: **{num(s['mean'])}**; median: {num(s['median'])} {item['unit']}.",
                ),
                tr(
                    f"- Minimo: {num(s['minimum'])} ({s['minimum_date']}); massimo: {num(s['maximum'])} ({s['maximum_date']}).",
                    f"- Minimum: {num(s['minimum'])} ({s['minimum_date']}); maximum: {num(s['maximum'])} ({s['maximum_date']}).",
                ),
                tr(
                    f"- Prima → ultima misura: {num(s['earliest'])} ({s['first_date']}) → {num(s['latest'])} ({s['last_date']}); differenza **{num(s['first_to_last_change'])} {item['unit']}**.",
                    f"- First → latest measurement: {num(s['earliest'])} ({s['first_date']}) → {num(s['latest'])} ({s['last_date']}); difference **{num(s['first_to_last_change'])} {item['unit']}**.",
                ),
            ]
        lines += [
            "",
            tr(
                "Il sonno è il totale delle sessioni per data di risveglio. Le differenze descrivono solo le misure osservate: non dimostrano miglioramenti clinici, cause o correlazioni. I dati mancanti restano sconosciuti.",
                "Sleep is the total of sessions by wake date. Differences describe observed measurements only; they do not establish clinical improvement, causes or correlations. Missing data remain unknown.",
            ),
        ]
        if r["includes_today"]:
            lines += [
                "",
                tr(
                    "L'intervallo include oggi: i totali dell'attività possono essere ancora parziali.",
                    "This interval includes today: activity totals may still be partial.",
                ),
            ]
        return "\n".join(lines)

    if "analyze_workout" in required_tools:
        r = latest("analyze_workout")
        if r is None:
            return None
        lines = [
            tr(
                f"Allenamenti registrati nel periodo **{r['period']['start']}–{r['period']['end']}**: **{r['sessions']}**, durata totale **{num(r['total_hours'])} h**. Filtro: {r['exercise_type_filter'] or 'tutti i tipi'}.",
                f"Recorded workouts in **{r['period']['start']}–{r['period']['end']}**: **{r['sessions']}**, total duration **{num(r['total_hours'])} h**. Filter: {r['exercise_type_filter'] or 'all types'}.",
            ),
            "",
        ]
        for kind, group in sorted(r["by_type"].items()):
            lines.append(
                tr(
                    f"- {kind}: {group['sessions']} sessioni, {num(group['hours'])} h; FC media ponderata per durata **{num(group['average_heart_rate_bpm'])} bpm**, disponibile in **{group['heart_rate_observed_sessions']}/{group['sessions']}** sessioni.",
                    f"- {kind}: {group['sessions']} sessions, {num(group['hours'])} h; duration-weighted mean HR **{num(group['average_heart_rate_bpm'])} bpm**, available in **{group['heart_rate_observed_sessions']}/{group['sessions']}** sessions.",
                )
            )
        if not r["sessions"]:
            lines.append(
                tr(
                    "Nessuna sessione registrata con questo filtro; frequenza cardiaca non disponibile.",
                    "No recorded sessions match this filter; heart rate unavailable.",
                )
            )
        lines += [
            "",
            tr(
                "Il riepilogo descrive solo le sessioni presenti nell'archivio: non dimostra che tutte le attività siano state registrate. La FC mancante non è stimata.",
                "This summary covers archived sessions only; it does not establish that every activity was recorded. Missing HR is not estimated.",
            ),
        ]
        return "\n".join(lines)

    if "summarize_nutrition_logs" in required_tools:
        r = latest("summarize_nutrition_logs")
        if r is None:
            return None
        return "\n\n".join(
            [
                tr(
                    f"Diario alimentare **{r['period']['start']}–{r['period']['end']}**: **{r['recorded_meals']} pasti registrati**, {r['observed_days']} giorni con registrazioni.",
                    f"Food diary **{r['period']['start']}–{r['period']['end']}**: **{r['recorded_meals']} recorded meals**, {r['observed_days']} days with entries.",
                ),
                tr(
                    f"Energia registrata: **{num(r['energy_kcal'])} kcal** ({r['meals_with_energy']} pasti con energia). Proteine registrate: **{num(r['protein_grams'])} g** ({r['protein_entries']} voci con proteine).",
                    f"Recorded energy: **{num(r['energy_kcal'])} kcal** ({r['meals_with_energy']} meals with energy). Recorded protein: **{num(r['protein_grams'])} g** ({r['protein_entries']} protein entries).",
                ),
                tr(
                    "L'assenza di registrazioni non equivale a un apporto nullo. Questi totali non sono il fabbisogno stimato né garantiscono la copertura dell'intera alimentazione; il catalogo degli alimenti è escluso.",
                    "Missing entries do not mean zero intake. These totals are not estimated requirements and do not establish complete dietary coverage; the food catalogue is excluded.",
                ),
            ]
        )

    if "analyze_sleep_metric_association" in required_tools:
        r = latest("analyze_sleep_metric_association")
        if r is None:
            return None
        metric_name = labels.get(metric_role(r), r["metric"])
        rd, rb = r.get("duration_pearson_r"), r.get("bedtime_pearson_r")
        if r["paired_days"] < 4 or (rd is None and rb is None):
            opening = tr(
                "Non ho abbastanza coppie utilizzabili per valutare un'associazione lineare affidabile. Posso descrivere le notti osservate, ma non trarre una conclusione sulla correlazione.",
                "There are not enough usable pairs to assess a reliable linear association. I can describe observed nights, but cannot conclude that they are correlated.",
            )
        elif rd is None:
            opening = tr(
                "Posso esaminare l'associazione con l'orario, ma non stimare il coefficiente della durata con i dati disponibili. Non posso quindi stabilire quale dei due aspetti sia più associato ai cali.",
                "I can examine timing, but cannot estimate the duration coefficient from the available data. I therefore cannot compare which aspect is more associated with the dips.",
            )
        elif rb is None:
            opening = tr(
                "Posso esaminare l'associazione con la durata, ma non stimare il coefficiente dell'orario con i dati disponibili. Questo limite impedisce di confrontare direttamente i due aspetti.",
                "I can examine duration, but cannot estimate the timing coefficient from the available data. This prevents a direct comparison of the two aspects.",
            )
        elif abs(rb) >= 0.2 and abs(rb) > abs(rd) + 0.1:
            opening = tr(
                "Il segnale che emerge nei dati riguarda soprattutto **l'orario in cui inizi il sonno**: il suo coefficiente di associazione è più marcato di quello della durata. Questo confronto è descrittivo, non dimostra quale fattore abbia causato i cali.",
                "The clearest descriptive signal concerns **when your sleep starts**: its association coefficient is larger in magnitude than the duration coefficient. This comparison does not establish what caused the dips.",
            )
        elif abs(rd) >= 0.2 and abs(rd) > abs(rb) + 0.1:
            opening = tr(
                "Nei dati disponibili il segnale più evidente riguarda **la durata del sonno**, più che l'orario di inizio. È un'associazione da esplorare, non la dimostrazione di una causa dei cali.",
                "In the available data, **sleep duration** shows the clearer descriptive association compared with bedtime. This is a pattern to explore, not proof of what caused the dips.",
            )
        elif max(abs(rd), abs(rb)) < 0.2:
            opening = tr(
                "Non emerge un'associazione lineare evidente con durata o orario del sonno in questo campione. I singoli cali possono coincidere con notti particolari senza formare uno schema costante.",
                "Neither duration nor bedtime shows a clear linear association in this sample. Individual dips may coincide with unusual nights without forming a consistent pattern.",
            )
        else:
            opening = tr(
                "Nei dati ci sono associazioni descrittive con il sonno, ma non emerge un fattore nettamente predominante. Conviene distinguere la durata dall'orario e guardare anche le singole notti.",
                "There are descriptive sleep associations, but no clearly dominant factor. It helps to distinguish duration from timing and also examine individual nights.",
            )
        lines = [
            opening,
            "",
            tr(
                f"Ho confrontato **{r['paired_days']} giorni abbinabili** con {metric_name} e una notte principale identificata dalla fonte, dal {r['period']['start']} al {r['period']['end']}. Le date senza coppie sono escluse.",
                f"I compared **{r['paired_days']} paired days** of {metric_name} and source-labelled main sleep, from {r['period']['start']} to {r['period']['end']}. Missing pairs are excluded.",
            ),
        ]
        if rd is not None:
            interpretation = (
                tr(
                    "Il coefficiente è vicino a zero: qui non si osserva una relazione lineare marcata fra quantità di sonno e questa metrica.",
                    "The coefficient is close to zero: there is no pronounced linear duration association here.",
                )
                if abs(rd) < 0.2
                else tr(
                    f"Nel campione, a notti più lunghe tendono a corrispondere valori {'più alti' if rd > 0 else 'più bassi'} della metrica.",
                    f"In this sample, longer nights tend to accompany {'higher' if rd > 0 else 'lower'} metric values.",
                )
            )
            lines += [
                "",
                tr(
                    f"Per la **durata**, Pearson è **r = {num(rd)}**. {interpretation}",
                    f"For **duration**, Pearson is **r = {num(rd)}**. {interpretation}",
                ),
            ]
        if rb is not None:
            interpretation = (
                tr(
                    "Il coefficiente è vicino a zero: l'orario non mostra un'associazione lineare marcata in queste osservazioni.",
                    "The coefficient is close to zero: timing shows no pronounced linear association in these observations.",
                )
                if abs(rb) < 0.2
                else tr(
                    f"Il segno {'negativo' if rb < 0 else 'positivo'} indica che alle notti iniziate più tardi tendono a corrispondere valori {'più bassi' if rb < 0 else 'più alti'} della metrica. Non significa che succeda ogni volta.",
                    f"The {'negative' if rb < 0 else 'positive'} sign means later starts tend to accompany {'lower' if rb < 0 else 'higher'} metric values. This does not mean it happens every time.",
                )
            )
            lines += [
                "",
                tr(
                    f"Per l'**orario di inizio**, Pearson è **r = {num(rb)}**. {interpretation}",
                    f"For **sleep start time**, Pearson is **r = {num(rb)}**. {interpretation}",
                ),
            ]
            if r.get("bedtime_paired_days", r["paired_days"]) != r["paired_days"]:
                lines += [
                    "",
                    tr(
                        f"Per il coefficiente dell'orario ho potuto usare {r['bedtime_paired_days']} notti con un inizio utilizzabile; la copertura può differire da quella della durata.",
                        f"The timing coefficient uses {r['bedtime_paired_days']} nights with usable start times; its coverage can differ from the duration comparison.",
                    ),
                ]
        if r.get("metric_median") is not None:
            lines += [
                "",
                tr(
                    f"Come riferimenti personali, la mediana della metrica è {num(r['metric_median'])} {r['unit']}, la notte principale dura tipicamente {duration(r['main_sleep_median_hours'] * 60) if r.get('main_sleep_median_hours') is not None else '—'} (mediana) e l'orario mediano di inizio è {r['usual_local_start'] or '—'}.",
                    f"For personal context, the metric median is {num(r['metric_median'])} {r['unit']}, main sleep typically lasts {duration(r['main_sleep_median_hours'] * 60) if r.get('main_sleep_median_hours') is not None else '—'} (median), and median bedtime is {r['usual_local_start'] or '—'}.",
                ),
            ]
        if r.get("low_metric_events"):
            lines += [
                "",
                tr(
                    "Ecco le notti abbinate ai cali sotto la soglia personale calcolata:",
                    "These are the nights paired with dips below the calculated personal threshold:",
                ),
                "",
                tr(
                    "| Data | Valore | Notte principale | Inizio | Scarto dall'orario mediano |",
                    "| Date | Value | Main sleep | Start | Offset from median bedtime |",
                ),
                "|---|---:|---|---|---|",
            ]
            for day, event in r["low_metric_events"].items():
                mins = event.get("main_sleep_minutes")
                if mins is None and event.get("main_sleep_hours") is not None:
                    mins = round(event["main_sleep_hours"] * 60)
                start = str(event.get("local_start") or "")
                clock = start[11:16] if len(start) >= 16 else tr("non disponibile", "unavailable")
                delay = event.get("bedtime_delay_minutes")
                timing = (
                    ""
                    if delay is None
                    else tr(
                        f"{num(abs(delay))} min {'più tardi' if delay >= 0 else 'prima'}",
                        f"{num(abs(delay))} min {'later' if delay >= 0 else 'earlier'}",
                    )
                )
                lines.append(
                    f"| {day} | {num(event['metric_value'])} {r['unit']} | {duration(mins)} | {clock} | {timing or '—'} |"
                )
            median = r.get("main_sleep_median_hours")
            durations = [
                e["main_sleep_minutes"] / 60
                for e in r["low_metric_events"].values()
                if e.get("main_sleep_minutes") is not None
            ]
            if (
                median is not None
                and any(h < median for h in durations)
                and any(h > median for h in durations)
            ):
                lines += [
                    "",
                    tr(
                        "Fra le notti mostrate ci sono durate sia inferiori sia superiori alla tua mediana. I cali, quindi, non coincidono tutti con una notte più corta del solito.",
                        "The displayed nights include durations both below and above your median. The dips therefore do not all coincide with shorter-than-usual sleep.",
                    ),
                ]
            if r.get("omitted_events"):
                lines += [
                    "",
                    tr(
                        f"Altri {r['omitted_events']} cali non sono mostrati nella tabella; i coefficienti usano tutte le coppie disponibili.",
                        f"Another {r['omitted_events']} dips are not shown in the table; coefficients use all available pairs.",
                    ),
                ]
        else:
            no_events = tr(
                "Nessun calo sotto la soglia robusta calcolata nel periodo.",
                "No dips below the calculated robust threshold in this period.",
            )
            if not r.get("metric_observed_days"):
                no_events = tr(
                    "Non ci sono osservazioni della metrica per identificare i cali nel periodo.",
                    "There are no metric observations to identify dips in this period.",
                )
            elif r.get("low_metric_threshold") is None:
                no_events = tr(
                    "La soglia robusta dei cali non è stimabile con la variabilità disponibile; non assegno eventi a questa categoria.",
                    "A robust dip threshold cannot be estimated from the available variability; no events are assigned to that category.",
                )
            lines += [
                "",
                no_events,
            ]
        lines += [
            "",
            tr(
                "Ho considerato la notte principale, senza scambiarla con eventuali sessioni brevi aggiuntive. Il confronto riguarda solo i giorni osservati: altri fattori non misurati possono contribuire ai cali e non è stato eseguito un test di significatività. L'associazione non dimostra che andare a letto tardi abbia causato quei valori.",
                "I used the main night, not a short additional session. This comparison covers observed days only: unmeasured factors may contribute to the dips, and no significance test was performed. The association does not establish that a later bedtime caused these values.",
            ),
        ]
        return "\n".join(lines)

    if "calculate_correlations" in required_tools:
        r = latest("calculate_correlations")
        if r is None:
            return None
        names = [
            labels.get(metric_role({"metric": r[key]}), r[key].replace("-", " "))
            for key in ("metric_a", "metric_b")
        ]
        return "\n".join(
            [
                f"**{names[0]} ↔ {names[1]}**",
                "",
                tr(
                    f"Periodo delle date della prima metrica: {r['period']['start']}–{r['period']['end']}.",
                    f"First-metric date period: {r['period']['start']}–{r['period']['end']}.",
                ),
                tr(
                    f"Scarto della seconda metrica: {r['offset_days']} giorno/i. Giorni osservati nelle rispettive finestre: {r['metric_a_observed_days']} e {r['metric_b_observed_days']}.",
                    f"Second-metric offset: {r['offset_days']} day(s). Observed days in their respective windows: {r['metric_a_observed_days']} and {r['metric_b_observed_days']}.",
                ),
                tr(
                    f"Giorni abbinati: **{r['paired_days']}**. Pearson **r = {num(r['pearson_r'], 4)}**.",
                    f"Paired days: **{r['paired_days']}**. Pearson **r = {num(r['pearson_r'], 4)}**.",
                ),
                "",
                tr(
                    "Associazione descrittiva, non causale. Non è stato eseguito un test di significatività statistica; dati mancanti e coppie assenti non sono interpolati.",
                    "Descriptive association, not causation. No statistical significance test was performed; missing observations/pairs are not interpolated.",
                ),
            ]
        )

    if "compare_periods" in required_tools:
        comparisons = [r for n, r in results if n == "compare_periods" and "period_a" in r]
        if not comparisons or not comparison_roles(question) <= {
            metric_role(r) for r in comparisons
        }:
            return None
        lines = []
        seen = set()
        for r in comparisons:
            key = (r["metric"], str(r["period_a"]), str(r["period_b"]))
            if key in seen:
                continue
            seen.add(key)
            role = metric_role(r)
            label = labels.get(role, r["metric"])
            if r.get("data_type") == "heart-rate-variability":
                label = tr("HRV: medie giornaliere dei campioni", "HRV: daily means of samples")
            lines += [
                f"**{label} ({r['unit']})**",
                "",
                tr(
                    "| Periodo | Giorni osservati/attesi | Media | Mediana |",
                    "| Period | Observed/expected days | Mean | Median |",
                ),
                "|---|---:|---:|---:|",
            ]
            for suffix in ("a", "b"):
                p = r[f"period_{suffix}"]
                lines.append(
                    f"| {p['start']}–{p['end']} | {p['observed_days']}/{p['expected_days']} | {num(r[f'mean_{suffix}'])} | {num(r[f'median_{suffix}'])} |"
                )
            lines += [
                "",
                tr(
                    f"Variazione della media dal primo al secondo periodo: **{num(r['delta_percent'])}%** ({num(r['delta'])} {r['unit']}).",
                    f"Mean change from the first to the second period: **{num(r['delta_percent'])}%** ({num(r['delta'])} {r['unit']}).",
                ),
                "",
            ]
        lines.append(
            tr(
                "Sono confrontati solo i giorni osservati; le lacune non valgono zero.",
                "Only observed days are compared; missing dates are not zero.",
            )
        )
        return "\n".join(lines)

    if "get_sleep_stage_series" in required_tools:
        r = latest("get_sleep_stage_series")
        if r is None:
            return None
        lines = [tr("Fasi registrate per data di risveglio:", "Recorded stages by wake-up date:")]
        stage_labels = {
            "deep": tr("Profondo", "Deep"),
            "rem": "REM",
            "light": tr("Leggero", "Light"),
            "awake": tr("Sveglio", "Awake"),
            "total_sleep": tr("Totale dormito", "Total asleep"),
        }
        sessions = r.get("session_stages", [])
        focus = single_date_focus(question)
        if focus:
            sessions = [s for s in sessions if s["date"] == focus]
        if not sessions:
            return tr(
                "Non sono disponibili dettagli per singola sessione in questo risultato. Serve interrogare un intervallo più breve.",
                "Per-session stage details are unavailable in this result; query a shorter interval.",
            )
        for session in sessions:
            label = (
                tr("Sonno principale", "Main sleep")
                if session.get("main_sleep") is True
                else tr("Altra sessione registrata", "Other recorded session")
            )
            lines += ["", f"**{session['date']} · {label}**"]
            for key, label in stage_labels.items():
                if key in session["minutes"]:
                    lines.append(
                        f"- {label}: **{duration(session['minutes'][key])}** ({num(session['minutes'][key])} min)."
                    )
        if r.get("omitted_session_details"):
            lines += [
                "",
                tr(
                    f"Altre {r['omitted_session_details']} sessioni omesse: richiedi un intervallo più breve per il dettaglio.",
                    f"{r['omitted_session_details']} additional sessions omitted; request a shorter interval for details.",
                ),
            ]
        lines += [
            "",
            tr(
                "Il totale dormito comprende profondo, REM e leggero; il tempo sveglio è separato.",
                "Total asleep includes deep, REM and light; awake time is separate.",
            ),
        ]
        return "\n".join(lines)

    if "calculate_sleep_regularity" in required_tools:
        r = latest("calculate_sleep_regularity")
        if r is None:
            return None
        groups = r.get("bedtime_by_wake_day_type", {})
        if not groups:
            return None
        lines = [
            tr(
                "Confronto degli orari mediani delle notti principali:",
                "Main-night median bedtime comparison:",
            )
        ]
        for key, label in [
            ("weekday", tr("Feriali", "Weekdays")),
            ("weekend", tr("Weekend", "Weekend")),
        ]:
            g = groups[key]
            lines += [
                f"- **{label}**: {g['median_local_start'] or tr('non disponibile', 'unavailable')} ({g['observed_nights']} "
                + tr("notti osservate).", "observed nights).")
            ]
        delay = r.get("weekend_delay_minutes")
        if delay is not None:
            lines += [
                "",
                tr(
                    f"Nel weekend l’orario mediano è di **{num(abs(delay))} minuti {'più tardi' if delay >= 0 else 'prima'}**.",
                    f"The weekend median bedtime is **{num(abs(delay))} minutes {'later' if delay >= 0 else 'earlier'}**.",
                ),
            ]
        lines += [
            "",
            tr(
                "Weekend significa risveglio di sabato o domenica, quindi notti di venerdì e sabato. Sono usate solo sessioni principali identificate dalla fonte; mezzanotte è gestita come prosecuzione della sera.",
                "Weekend means waking on Saturday or Sunday, i.e. Friday/Saturday nights. Only source-labelled main sessions are used; after midnight continues the preceding evening.",
            ),
        ]
        return "\n".join(lines)

    if "calculate_readiness" in required_tools:
        r = latest("calculate_readiness")
        if r is None:
            return None
        lines = [
            tr(
                "Misure della data richiesta e riferimento personale precedente:",
                "Requested-date measurements and prior personal reference:",
            )
        ]
        for key, e in r.get("component_evidence", {}).items():
            label = {
                "sleep": labels["sleep"],
                "hrv": labels["hrv"],
                "resting_heart_rate": labels["rhr"],
            }[key]
            lines += [
                f"- **{label} · {e.get('as_of', '')}**: {num(e.get('current_day_value'))} {e.get('unit', '')}; "
                + tr(
                    f"mediana precedente {num(e.get('baseline_median'))} ({e.get('baseline_days_observed', 0)} giorni osservati).",
                    f"prior median {num(e.get('baseline_median'))} ({e.get('baseline_days_observed', 0)} observed days).",
                )
            ]
        if any(word in text for word in ("punteggio", "score", "readiness")):
            lines += [
                "",
                tr(
                    f"Punteggio stimato dall’app: {num(r.get('score'))}/100, basato sulle medie degli ultimi sette giorni.",
                    f"App-estimated score: {num(r.get('score'))}/100, based on the last seven days' means.",
                ),
            ]
        lines += [
            "",
            tr(
                "Questi segnali non identificano né escludono le cause della stanchezza e non dimostrano che il recupero sia completo.",
                "These signals neither identify nor rule out causes of fatigue and do not establish complete recovery.",
            ),
        ]
        return "\n".join(lines)
    if "get_metric_series" in required_tools and comparison_roles(question) == {"weight"}:
        r = latest("get_metric_series")
        if r is None or r.get("data_type") != "weight":
            return None
        s = r.get("summary")
        if not s:
            return tr(
                f"Nessuna misura di peso registrata nel periodo {r['period']['start']}–{r['period']['end']}.",
                f"No recorded weight measurements in {r['period']['start']}–{r['period']['end']}.",
            )
        return "\n".join(
            [
                tr(
                    f"Intervallo interrogato: {r['period']['start']}–{r['period']['end']}.",
                    f"Queried period: {r['period']['start']}–{r['period']['end']}.",
                ),
                "",
                tr(
                    f"- Prima misura: **{s['first_date']} · {num(s['earliest'])} {r['unit']}**.",
                    f"- First measurement: **{s['first_date']} · {num(s['earliest'])} {r['unit']}**.",
                ),
                tr(
                    f"- Ultima misura: **{s['last_date']} · {num(s['latest'])} {r['unit']}**.",
                    f"- Last measurement: **{s['last_date']} · {num(s['latest'])} {r['unit']}**.",
                ),
                tr(
                    f"- Differenza tra queste misure: **{num(s['first_to_last_change'])} {r['unit']}**.",
                    f"- Difference between these measurements: **{num(s['first_to_last_change'])} {r['unit']}**.",
                ),
                tr(
                    f"- Misurazioni: **{s['count']}**, su {r['observed_days']} giorni osservati.",
                    f"- Measurements: **{s['count']}**, across {r['observed_days']} observed days.",
                ),
                tr(
                    f"- Media delle misure: {num(s['mean'])} {r['unit']}; mediana: {num(s['median'])} {r['unit']}.",
                    f"- Measurement mean: {num(s['mean'])} {r['unit']}; median: {num(s['median'])} {r['unit']}.",
                ),
                "",
                tr(
                    "Il cambiamento riguarda solo prima e ultima misura osservata. Non inferisco valori o cambiamenti nei giorni non registrati dell’intervallo.",
                    "The change concerns only the first and last observed measurements. Unrecorded dates within the interval are not inferred.",
                ),
            ]
        )

    if "get_metric_series" in required_tools:
        r = latest("get_metric_series")
        if comparison_roles(question) == {"temperature"}:
            measured = next(
                (
                    value
                    for name, value in reversed(results)
                    if name == "get_metric_series"
                    and str(value.get("field") or "").endswith("nightlyTemperatureCelsius")
                ),
                None,
            )
            if measured is not None:
                r = measured
        if r is None or comparison_roles(question) != {metric_role(r)}:
            return None
        role = metric_role(r)
        s = r.get("summary")
        if not s:
            return tr(
                f"Nessuna osservazione per la metrica nel periodo {r['period']['start']}–{r['period']['end']}.",
                f"No metric observations in {r['period']['start']}–{r['period']['end']}.",
            )
        label = labels.get(role, r["data_type"].replace("-", " "))
        if r["data_type"] == "heart-rate-variability":
            label = tr("HRV dei campioni intragiornalieri", "Intraday-sample HRV")
        lines = [
            f"**{label} ({r['unit']})**",
            "",
            tr(
                f"Periodo: {r['period']['start']}–{r['period']['end']}. Giorni con osservazioni: **{r['observed_days']}/{r['expected_days']}**.",
                f"Period: {r['period']['start']}–{r['period']['end']}. Days with observations: **{r['observed_days']}/{r['expected_days']}**.",
            ),
        ]
        if r["data_type"] == "steps" and r["expected_days"] == 1:
            lines += [
                "",
                tr(
                    f"Totale giornaliero registrato: **{num(s['latest'])} {r['unit']}**.",
                    f"Recorded daily total: **{num(s['latest'])} {r['unit']}**.",
                ),
            ]
        else:
            lines += [
                "",
                tr(
                    f"- Media: **{num(s['mean'])} {r['unit']}**; mediana: {num(s['median'])} {r['unit']}.",
                    f"- Mean: **{num(s['mean'])} {r['unit']}**; median: {num(s['median'])} {r['unit']}.",
                ),
                tr(
                    f"- Minimo: **{num(s['minimum'])} {r['unit']}** ({s['minimum_date']}).",
                    f"- Minimum: **{num(s['minimum'])} {r['unit']}** ({s['minimum_date']}).",
                ),
                tr(
                    f"- Massimo: **{num(s['maximum'])} {r['unit']}** ({s['maximum_date']}).",
                    f"- Maximum: **{num(s['maximum'])} {r['unit']}** ({s['maximum_date']}).",
                ),
            ]
        if role == "temperature" and str(r.get("field") or "").endswith(
            "nightlyTemperatureCelsius"
        ):
            lines += [
                "",
                tr(
                    "Questi valori riguardano la temperatura notturna registrata. Il riferimento personale della fonte è una metrica distinta e non sostituisce queste misure.",
                    "These are the recorded nightly temperatures. The source's personal reference is a separate metric and does not replace these measurements.",
                ),
            ]
        if r["data_type"] == "daily-oxygen-saturation":
            lines += [
                "",
                tr(
                    "Minimo e massimo si riferiscono alle medie giornaliere, non ai singoli campioni intranotte.",
                    "Minimum and maximum refer to daily means, not individual overnight samples.",
                ),
            ]
        if r["data_type"] == "sleep":
            window = max(1, min(7, s["count"] // 3))
            lines += [
                "",
                tr(
                    "Sono totali giornalieri per data di risveglio, comprese le sessioni aggiuntive; non sono solo le notti principali.",
                    "These are daily totals by wake date, including additional sessions, not main nights alone.",
                ),
                tr(
                    f"Variazione tra la media delle prime {window} e delle ultime {window} osservazioni: **{num(s['trend_percent'])}%**. È un confronto descrittivo della durata, non una misura di miglioramento della qualità del sonno.",
                    f"Change between the means of the first {window} and last {window} observations: **{num(s['trend_percent'])}%**. This describes duration, not an improvement in sleep quality.",
                ),
            ]
        lines += [
            "",
            tr(
                "Le date prive di osservazioni non sono interpolate.",
                "Dates without observations are not interpolated.",
            ),
        ]
        return "\n".join(lines)

    if "get_sleep_sessions" in required_tools:
        r = latest("get_sleep_sessions")
        if r is None:
            return None
        focus = single_date_focus(question)
        sessions = r.get("sessions", [])
        if focus:
            sessions = [s for s in sessions if s["date"] == focus]
        left, right = (focus, focus) if focus else (r["period"]["start"], r["period"]["end"])
        lines = [
            tr(
                f"Sessioni registrate con risveglio nel periodo {left}–{right}:",
                f"Recorded sessions ending in {left}–{right}:",
            )
        ]
        if len(sessions) <= 6 and not r.get("truncated"):
            totals: dict[str, float] = {}
            for session in sessions:
                minutes = session.get("minutes_asleep", round(session["hours"] * 60))
                label = (
                    tr("Sonno principale", "Main sleep")
                    if session.get("main_sleep") is True
                    else tr(
                        "Altra sessione registrata (tipo non specificato)",
                        "Other recorded session (type unspecified)",
                    )
                )
                start = str(session.get("start_time") or "").replace("T", " ")
                end = str(session.get("end_time") or "").replace("T", " ")
                lines += [
                    "",
                    f"**{session['date']} · {label}**",
                    f"- {duration(minutes)}; {start} → {end}.",
                ]
                totals[session["date"]] = totals.get(session["date"], 0) + minutes
            for day, minutes in totals.items():
                lines += [
                    "",
                    tr(
                        f"Totale delle sessioni terminate il {day}: **{duration(minutes)}**. Le altre sessioni non sostituiscono la notte principale.",
                        f"Total for sessions ending on {day}: **{duration(minutes)}**. Other sessions do not replace the main night.",
                    ),
                ]
            if not totals:
                lines.append(
                    tr(
                        "Nessuna sessione registrata nel periodo.",
                        "No recorded sessions in this period.",
                    )
                )
        else:
            baseline = r.get("main_sleep_baseline_hours", {})
            lines += [
                tr(
                    f"Notti principali identificate: {r['main_sleep_observed_days']}. Durata media: {duration(baseline.get('mean') * 60) if baseline.get('mean') is not None else num(None)}; mediana: {duration(baseline.get('median') * 60) if baseline.get('median') is not None else num(None)}.",
                    f"Source-labelled main nights: {r['main_sleep_observed_days']}. Mean duration: {duration(baseline.get('mean') * 60) if baseline.get('mean') is not None else num(None)}; median: {duration(baseline.get('median') * 60) if baseline.get('median') is not None else num(None)}.",
                )
            ]
            if r.get("truncated"):
                lines.append(
                    tr(
                        f"{r['omitted_sessions']} sessioni non sono incluse nel dettaglio restituito; interrogare intervalli più brevi per il dettaglio completo.",
                        f"{r['omitted_sessions']} sessions are omitted from the returned detail; request shorter intervals for complete details.",
                    )
                )
        return "\n".join(lines)

    if "analyze_metric_threshold_responses" in required_tools:
        r = latest("analyze_metric_threshold_responses")
        if r is None:
            return None
        if r.get("status") == "no_event_data":
            return tr(
                "Non ci sono dati per la metrica che dovrebbe definire gli eventi; non è possibile valutare la relazione richiesta.",
                "There are no observations for the event metric; the requested relationship cannot be evaluated.",
            )
        if "responses" not in r:
            return None
        lines = [
            tr(
                f"Nel periodo {r['period']['start']}–{r['period']['end']} ci sono **{r['event_day_count']} giorni** che superano la soglia richiesta.",
                f"In {r['period']['start']}–{r['period']['end']}, **{r['event_day_count']} days** meet the requested threshold.",
            )
        ]
        event_label = labels.get(metric_role({"metric": r["event_metric"]}), r["event_metric"])
        lines += [
            tr(
                f"{event_label}: riferimento {num(r['event_baseline'])} {r['event_unit']}; soglia {num(r['threshold'])} {r['event_unit']}; {r['event_observed_days']} giorni osservati.",
                f"{event_label}: reference {num(r['event_baseline'])} {r['event_unit']}; threshold {num(r['threshold'])} {r['event_unit']}; {r['event_observed_days']} observed days.",
            )
        ]
        for metric, response in r["responses"].items():
            label = labels.get(metric_role({"metric": metric}), metric)
            lines += [
                "",
                f"**{label} · "
                + tr(
                    f"scarto temporale: {r['response_offset_days']} giorno/i**",
                    f"day offset: {r['response_offset_days']}**",
                ),
                tr(
                    f"- Eventi abbinabili: **{response['matched_event_days']}**; senza misura di risposta: {response['missing_event_days']}.",
                    f"- Matched events: **{response['matched_event_days']}**; missing responses: {response['missing_event_days']}.",
                ),
                tr(
                    f"- Riferimento personale: {num(response.get('baseline_value'))} {response['unit']} ({response['baseline_observed_days']} giorni osservati).",
                    f"- Personal reference: {num(response.get('baseline_value'))} {response['unit']} ({response['baseline_observed_days']} observed days).",
                ),
                tr(
                    f"- Risposte sotto il riferimento: **{response.get('days_below_baseline')} su {response['matched_event_days']}** ({num(response.get('percent_below_baseline'))}%).",
                    f"- Responses below reference: **{response.get('days_below_baseline')} of {response['matched_event_days']}** ({num(response.get('percent_below_baseline'))}%).",
                ),
                tr(
                    f"- Media delle risposte abbinate: {num(response.get('matched_mean'))} {response['unit']}.",
                    f"- Matched-response mean: {num(response.get('matched_mean'))} {response['unit']}.",
                ),
            ]
        lines += [
            "",
            tr(
                "I valori mancanti non sono interpolati. Questo conteggio descrive gli eventi osservati e da solo non dimostra un effetto causale.",
                "Missing values are not interpolated. This count describes observed events and does not by itself demonstrate a causal effect.",
            ),
        ]
        return "\n".join(lines)
    return None
