from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .analysis import format_value


def _format_metric_value(data_type: str, value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if data_type == "weight":
        return f"{value:.1f} {unit}".strip()
    return format_value(value, unit)


class SparklineWidget(QWidget):
    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._points: list[tuple[float, float]] = []
        self._mean: float | None = None
        self._std: float | None = None
        self._x_bounds: tuple[float, float] | None = None
        self.setFixedHeight(62)

    def set_series(
        self,
        points: list[tuple[float, float]],
        mean: float | None = None,
        std: float | None = None,
        x_bounds: tuple[float, float] | None = None,
    ) -> None:
        self._points = points
        self._mean = mean
        self._std = std
        self._x_bounds = x_bounds
        self.setVisible(bool(points))
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        background = QColor("#F8FAFD")
        painter.setPen(Qt.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(area, 7, 7)

        values = [value for _timestamp, value in self._points]
        if self._mean is not None and self._std is not None:
            values.extend([self._mean - self._std, self._mean + self._std])
        low, high = min(values), max(values)
        if high - low < 1e-9:
            padding = max(abs(high) * 0.03, 1.0)
            low -= padding
            high += padding
        else:
            padding = (high - low) * 0.12
            low -= padding
            high += padding

        first_x, last_x = self._x_bounds or (
            self._points[0][0],
            self._points[-1][0],
        )

        def map_x(timestamp: float) -> float:
            if last_x == first_x:
                return area.center().x()
            return area.left() + (timestamp - first_x) / (last_x - first_x) * area.width()

        def map_y(value: float) -> float:
            return area.bottom() - (value - low) / (high - low) * area.height()

        if self._mean is not None and self._std is not None:
            top = map_y(self._mean + self._std)
            bottom = map_y(self._mean - self._std)
            band = QColor(self._color)
            band.setAlpha(38)
            painter.setBrush(band)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                QRectF(area.left(), min(top, bottom), area.width(), abs(bottom - top)), 4, 4
            )
            mean_color = QColor(self._color)
            mean_color.setAlpha(145)
            mean_pen = QPen(mean_color, 1.1, Qt.DashLine)
            painter.setPen(mean_pen)
            mean_y = map_y(self._mean)
            painter.drawLine(QPointF(area.left(), mean_y), QPointF(area.right(), mean_y))

        path = QPainterPath()
        for index, (timestamp, value) in enumerate(self._points):
            point = QPointF(map_x(timestamp), map_y(value))
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._color, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        last_point = QPointF(map_x(self._points[-1][0]), map_y(self._points[-1][1]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(last_point, 3.2, 3.2)


class MetricCard(QFrame):
    def __init__(self, title: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.color = color
        self.setMinimumSize(235, 255)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        accent = QFrame()
        accent.setFixedHeight(5)
        accent.setStyleSheet(f"background: {color}; border-radius: 2px;")
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")
        self.trend = QLabel("Nessun dato nel periodo")
        self.trend.setObjectName("cardCaption")
        self.trend.setWordWrap(True)
        self.sparkline = SparklineWidget(color)
        self.sparkline.setVisible(False)
        self.sparkline_caption = QLabel()
        self.sparkline_caption.setObjectName("sparklineCaption")
        self.sparkline_caption.setWordWrap(True)
        self.sparkline_caption.setVisible(False)
        progress_header = QHBoxLayout()
        self.baseline = QLabel("Media 7 gg · —")
        self.baseline.setObjectName("progressBaseline")
        self.ratio = QLabel("—")
        self.ratio.setObjectName("progressRatio")
        self.ratio.setAlignment(Qt.AlignCenter)
        progress_header.addWidget(self.baseline, 1)
        progress_header.addWidget(self.ratio)
        self.progress = QProgressBar()
        self.progress.setObjectName("completionBar")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        layout.addWidget(accent)
        layout.addSpacing(4)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.trend)
        layout.addWidget(self.sparkline)
        layout.addWidget(self.sparkline_caption)
        layout.addSpacing(5)
        layout.addLayout(progress_header)
        layout.addWidget(self.progress)
        layout.addStretch()

    def update_metric(self, metric: dict) -> None:
        summary = metric["summary"]
        unit = metric.get("unit", "")
        data_type = metric.get("data_type", "")
        self.value.setText(_format_metric_value(data_type, summary["latest"], unit))
        trend = summary.get("trend_percent")
        parts = [f"Media {_format_metric_value(data_type, summary['mean'], unit)}"]
        if trend is not None:
            arrow = "↑" if trend > 0 else ("↓" if trend < 0 else "→")
            parts.append(f"{arrow} {abs(trend):.1f}% nel periodo")
        self.trend.setText(" · ".join(parts))
        self.baseline.setText("Confronto giornaliero non disponibile")
        self.ratio.setText("—")
        self.progress.setVisible(False)
        self.sparkline.setVisible(False)
        self.sparkline_caption.setVisible(False)

    def update_progress(self, metric: dict) -> None:
        unit = metric.get("unit", "")
        current = metric.get("current")
        baseline = metric.get("baseline")
        percentage = metric.get("percentage")
        delta = metric.get("delta_percent")
        days_used = int(metric.get("days_used", 0))
        completion = bool(metric.get("completion"))
        value_date = metric.get("value_date")
        data_type = metric.get("data_type", "")

        def formatted(value: float | None) -> str:
            return _format_metric_value(data_type, value, unit)

        self.value.setText(formatted(current))
        if current is None:
            self.trend.setText("Nessun dato per questo giorno")
        elif baseline is None:
            self.trend.setText("Servono dati dei giorni precedenti")
        elif percentage is None:
            difference = current - baseline
            direction = "sopra" if difference > 0 else ("sotto" if difference < 0 else "in media")
            if direction == "in media":
                self.trend.setText("In linea con la media personale")
            else:
                arrow = "↑" if difference > 0 else "↓"
                self.trend.setText(
                    f"{arrow} {formatted(abs(difference))} {direction} la media"
                )
        elif completion:
            if percentage >= 100:
                self.trend.setText(f"Media personale superata del {percentage - 100:.0f}%")
            else:
                remaining = max(0.0, baseline - current)
                self.trend.setText(f"Mancano {formatted(remaining)} alla media")
        else:
            difference = current - baseline
            direction = "sopra" if difference > 0 else ("sotto" if difference < 0 else "in media")
            if direction == "in media":
                self.trend.setText("In linea con la media personale")
            else:
                arrow = "↑" if difference > 0 else "↓"
                self.trend.setText(
                    f"{arrow} {formatted(abs(difference))} {direction} la media"
                )

        if metric.get("data_type") == "weight" and value_date:
            measured = date.fromisoformat(value_date).strftime("%d/%m/%Y")
            self.trend.setText(f"Ultima misurazione: {measured} · {self.trend.text()}")
        elif metric.get("latest_available") and value_date:
            measured = date.fromisoformat(value_date).strftime("%d/%m/%Y")
            self.trend.setText(f"Ultimo dato: {measured} · {self.trend.text()}")

        is_heart_day = data_type == "daily-resting-heart-rate"
        if is_heart_day:
            # Never read the generic `sparkline` field for the cardiac card.
            # Only the dedicated, smoothed intraday series is accepted. Raw
            # samples remain available for the real minimum and maximum.
            sparkline = metric.get("heart_day_smoothed") or []
            mean = metric.get("heart_baseline_mean")
            std = metric.get("heart_baseline_std")
        else:
            sparkline = metric.get("sparkline") or []
            mean = metric.get("sparkline_mean")
            std = metric.get("sparkline_std")
        if sparkline:
            if is_heart_day:
                graph_day = date.fromisoformat(
                    metric.get("heart_day_date") or metric["value_date"]
                )
                day_start = datetime.combine(graph_day, time.min).astimezone()
                day_end = day_start + timedelta(days=1)
                now = datetime.now().astimezone()
                visible_end = min(day_end, now) if graph_day == now.date() else day_end
                sparkline = [
                    point
                    for point in sparkline
                    if day_start.timestamp() <= point[0] < day_end.timestamp()
                ]
                self.sparkline.set_series(
                    sparkline,
                    mean,
                    std,
                    (day_start.timestamp(), visible_end.timestamp()),
                )
                minimum = format_value(metric.get("heart_day_min"), unit)
                maximum = format_value(metric.get("heart_day_max"), unit)
                days = metric.get("heart_baseline_days", 0)
                baseline_text = (
                    f" · media {days} gg {format_value(mean, unit)}"
                    f" · banda ±1σ ({format_value(std, unit)})"
                    if mean is not None and std is not None
                    else ""
                )
                smoothing = metric.get("heart_smoothing_minutes", 15)
                caption = (
                    f"Solo oggi · curva smussata {smoothing} min · "
                    f"min–max reali {minimum}–{maximum}{baseline_text}"
                )
            else:
                self.sparkline.set_series(sparkline, mean, std)
                caption = (
                    f"Ultimi 7 giorni · media {format_value(mean, unit)} · "
                    f"banda ±1σ ({format_value(std, unit)})"
                )
            self.sparkline.setVisible(bool(sparkline))
            self.sparkline_caption.setText(caption)
            self.sparkline_caption.setVisible(bool(sparkline))
        else:
            self.sparkline.set_series([])
            self.sparkline.setVisible(False)
            self.sparkline_caption.setVisible(False)

        days_label = "giorno" if days_used == 1 else "giorni"
        self.baseline.setText(
            f"Media {days_used} {days_label} · {formatted(baseline)}"
            if days_used
            else "Media 7 giorni · —"
        )
        self.progress.setVisible(completion)
        if percentage is None:
            self.ratio.setText("—")
            self.progress.setValue(0)
            ratio_style = "background: #F1F3F4; color: #5F6368;"
        elif not completion:
            if abs(delta or 0.0) < 0.05:
                self.ratio.setText("≈ media")
            else:
                arrow = "↑" if (delta or 0.0) > 0 else "↓"
                self.ratio.setText(f"{arrow} {abs(delta or 0.0):.1f}%")
            self.progress.setValue(0)
            ratio_style = "background: #E8F0FE; color: #174EA6;"
        else:
            shown_percentage = max(0.0, percentage)
            self.ratio.setText(
                ">999%" if shown_percentage > 999 else f"{shown_percentage:.0f}%"
            )
            self.progress.setValue(round(min(shown_percentage, 100.0) * 10))
            reached = completion and shown_percentage >= 100
            ratio_style = (
                "background: #E6F4EA; color: #137333;"
                if reached
                else "background: #E8F0FE; color: #174EA6;"
            )
        self.ratio.setStyleSheet(
            f"{ratio_style} border-radius: 8px; padding: 3px 7px; font-weight: 700;"
        )
        self.progress.setStyleSheet(
            "QProgressBar { border: none; background: #E8EAED; border-radius: 5px; }"
            f"QProgressBar::chunk {{ background: {self.color}; border-radius: 5px; }}"
        )


CARD_COLORS = {
    "steps": "#34A853",
    "daily-resting-heart-rate": "#EA4335",
    "sleep": "#7E57C2",
    "daily-heart-rate-variability": "#673AB7",
    "daily-oxygen-saturation": "#4285F4",
    "daily-respiratory-rate": "#00ACC1",
    "daily-sleep-temperature-derivations": "#E91E63",
    "weight": "#5C6BC0",
    "active-zone-minutes": "#F9AB00",
}


class OverviewPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        hero = QFrame()
        hero.setObjectName("overviewHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        heading = QHBoxLayout()
        title = QLabel("La tua salute, in sintesi")
        title.setObjectName("pageTitle")
        pill = QLabel("MEDIA MOBILE · 7 GIORNI")
        pill.setObjectName("overviewPill")
        heading.addWidget(title, 1)
        heading.addWidget(pill)
        self.subtitle = QLabel(
            "Il valore di oggi viene confrontato con i sette giorni precedenti."
        )
        self.subtitle.setObjectName("pageSubtitle")
        self.subtitle.setWordWrap(True)
        hero_layout.addLayout(heading)
        hero_layout.addWidget(self.subtitle)
        root.addWidget(hero)
        root.addSpacing(14)

        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.cards: dict[str, MetricCard] = {}
        for index, (key, color) in enumerate(CARD_COLORS.items()):
            card = MetricCard("In attesa di dati", color)
            self.cards[key] = card
            self.grid.addWidget(card, index // 3, index % 3)
        for column in range(3):
            self.grid.setColumnStretch(column, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        note = QLabel(
            "Le fasce personali e le anomalie sono calcoli statistici, non valutazioni mediche."
        )
        note.setObjectName("disclaimer")
        note.setAlignment(Qt.AlignCenter)
        root.addWidget(note)

    def refresh(self, snapshot: dict, progress_snapshot: dict | None = None) -> None:
        by_type = {metric["data_type"]: metric for metric in snapshot.get("metrics", [])}
        progress_snapshot = progress_snapshot or {}
        by_progress = {
            metric["data_type"]: metric for metric in progress_snapshot.get("metrics", [])
        }
        reference_text = progress_snapshot.get("reference_date")
        if reference_text:
            reference_day = date.fromisoformat(reference_text)
            today = datetime.now().astimezone().date()
            prefix = (
                "Oggi"
                if reference_day == today
                else reference_day.strftime("Il %d/%m/%Y")
            )
            self.subtitle.setText(
                f"{prefix} rispetto alla media mobile dei sette giorni precedenti. "
                "Le percentuali degli indicatori fisiologici sono confronti neutri."
            )
        for key, card in self.cards.items():
            metric = by_type.get(key)
            progress = by_progress.get(key)
            if progress:
                card.title.setText(progress["label"])
                card.update_progress(progress)
            elif metric:
                card.title.setText(metric["label"])
                card.update_metric(metric)
            else:
                card.title.setText(_fallback_label(key))
                card.value.setText("—")
                card.trend.setText("Nessun dato nel periodo")
                card.baseline.setText("Media 7 giorni · —")
                card.ratio.setText("—")
                card.progress.setValue(0)
                card.progress.setVisible(False)
                card.sparkline.setVisible(False)
                card.sparkline_caption.setVisible(False)


def _fallback_label(key: str) -> str:
    labels = {
        "steps": "Passi",
        "daily-resting-heart-rate": "Frequenza a riposo",
        "sleep": "Sonno",
        "daily-heart-rate-variability": "HRV giornaliera",
        "daily-oxygen-saturation": "Saturazione di ossigeno",
        "daily-respiratory-rate": "Frequenza respiratoria",
        "daily-sleep-temperature-derivations": "Temperatura nel sonno",
        "weight": "Peso",
        "active-zone-minutes": "Minuti in zona attiva",
    }
    return labels.get(key, key)
