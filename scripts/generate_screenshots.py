#!/usr/bin/env python3
"""Render documentation screenshots from the real GUI and synthetic health data."""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from google_health_viewer.main_window import MainWindow
from google_health_viewer.storage import HealthStore
from google_health_viewer.theme import APP_STYLESHEET


def _stamp(day, hour: int = 12, minute: int = 0) -> str:
    return datetime.combine(day, time(hour, minute), timezone.utc).isoformat()


def _interval_payload(key: str, day, value_key: str, value: float) -> dict:
    start = _stamp(day, 9)
    end = _stamp(day, 18)
    return {
        "name": f"demo/{key}/{day.isoformat()}",
        key: {
            "interval": {"startTime": start, "endTime": end},
            value_key: value,
        },
        "dataSource": {
            "device": {"displayName": "Synthetic wearable"},
            "platform": "DEMO",
        },
    }


def seed_demo_data(store: HealthStore) -> None:
    now = datetime.now(timezone.utc)
    today = now.date()
    steps = [6840, 7420, 8110, 6290, 9030, 7760, 4380]
    sleep_minutes = [438, 462, 451, 425, 480, 446, 421]
    active_minutes = [38, 46, 52, 31, 61, 44, 27]
    hrv = [44, 47, 42, 49, 46, 45, 48]
    oxygen = [96.1, 95.8, 96.4, 96.0, 95.9, 96.3, 96.2]
    breathing = [14.2, 14.5, 14.0, 14.3, 14.1, 14.4, 14.2]
    temperature = [-0.1, 0.0, 0.2, 0.1, -0.2, 0.0, 0.1]
    resting = [62, 61, 63, 60, 62, 61, 62]

    for offset in range(6, -1, -1):
        index = 6 - offset
        day = today - timedelta(days=offset)
        store.upsert_records(
            "steps",
            [_interval_payload("steps", day, "count", steps[index])],
        )
        store.upsert_records(
            "active-zone-minutes",
            [
                _interval_payload(
                    "activeZoneMinutes",
                    day,
                    "activeZoneMinutes",
                    active_minutes[index],
                )
            ],
        )
        store.upsert_records(
            "daily-resting-heart-rate",
            [
                {
                    "name": f"demo/resting/{day.isoformat()}",
                    "dailyRestingHeartRate": {
                        "date": {"year": day.year, "month": day.month, "day": day.day},
                        "beatsPerMinute": resting[index],
                    },
                }
            ],
        )
        store.upsert_records(
            "daily-heart-rate-variability",
            [
                {
                    "name": f"demo/hrv/{day.isoformat()}",
                    "startTime": _stamp(day),
                    "dailyHeartRateVariability": {
                        "averageHeartRateVariabilityMilliseconds": hrv[index]
                    },
                }
            ],
        )
        store.upsert_records(
            "daily-oxygen-saturation",
            [
                {
                    "name": f"demo/oxygen/{day.isoformat()}",
                    "dailyOxygenSaturation": {
                        "date": {"year": day.year, "month": day.month, "day": day.day},
                        "averagePercentage": oxygen[index],
                    },
                }
            ],
        )
        store.upsert_records(
            "daily-respiratory-rate",
            [
                {
                    "name": f"demo/respiratory/{day.isoformat()}",
                    "startTime": _stamp(day),
                    "dailyRespiratoryRate": {"breathsPerMinute": breathing[index]},
                }
            ],
        )
        store.upsert_records(
            "daily-sleep-temperature-derivations",
            [
                {
                    "name": f"demo/temperature/{day.isoformat()}",
                    "startTime": _stamp(day),
                    "dailySleepTemperatureDerivations": {"temperatureDelta": temperature[index]},
                }
            ],
        )
        sleep_start = datetime.combine(day - timedelta(days=1), time(23, 0), timezone.utc)
        sleep_end = sleep_start + timedelta(minutes=sleep_minutes[index] + 28)
        store.upsert_records(
            "sleep",
            [
                {
                    "name": f"demo/sleep/{day.isoformat()}",
                    "sleep": {
                        "interval": {
                            "startTime": sleep_start.isoformat(),
                            "endTime": sleep_end.isoformat(),
                        },
                        "sleepSummary": {
                            "minutesAsleep": sleep_minutes[index],
                            "stagesSummary": [
                                {"type": "DEEP", "minutes": 92 + index},
                                {"type": "REM", "minutes": 104 + index * 2},
                                {"type": "LIGHT", "minutes": 225 + index * 3},
                            ],
                        },
                    },
                }
            ],
        )

        for hour, bpm in ((8, 60 + index % 2), (18, 78 + index % 3)):
            if day == today:
                continue
            store.upsert_records(
                "heart-rate",
                [
                    {
                        "name": f"demo/heart/{day.isoformat()}/{hour}",
                        "heartRate": {
                            "sampleTime": {"physicalTime": _stamp(day, hour)},
                            "beatsPerMinute": bpm,
                        },
                    }
                ],
            )

    elapsed_minutes = max(
        45, int((now - datetime.combine(today, time(), timezone.utc)).total_seconds() / 60)
    )
    for minute in range(0, elapsed_minutes, 5):
        measured = datetime.combine(today, time(), timezone.utc) + timedelta(minutes=minute)
        bpm = 69 + 8 * math.sin(minute / 37) + 3 * math.sin(minute / 9)
        store.upsert_records(
            "heart-rate",
            [
                {
                    "name": f"demo/heart/today/{minute}",
                    "heartRate": {
                        "sampleTime": {"physicalTime": measured.isoformat()},
                        "beatsPerMinute": round(bpm, 1),
                    },
                }
            ],
        )

    weight_day = today - timedelta(days=2)
    store.upsert_records(
        "weight",
        [
            {
                "name": "demo/weight/latest",
                "weight": {
                    "sampleTime": {"physicalTime": _stamp(weight_day, 8)},
                    "weightGrams": 85000,
                },
            }
        ],
    )
    store.upsert_records(
        "exercise",
        [
            {
                "name": "demo/exercise/cycling",
                "exercise": {
                    "interval": {
                        "startTime": _stamp(today - timedelta(days=1), 8),
                        "endTime": _stamp(today - timedelta(days=1), 9),
                    },
                    "exerciseType": "CYCLING",
                    "activeDuration": 48,
                },
            }
        ],
    )
    store.save_resource("profile", {"displayName": "Demo profile"})


def capture(window: MainWindow, target: Path) -> None:
    window.ensurePolished()
    window.show()
    QApplication.processEvents()
    pixmap = window.grab()
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write screenshot: {target}")
    if pixmap.width() < 1200 or pixmap.height() < 760:
        raise RuntimeError(f"Unexpected screenshot size: {pixmap.size()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    QCoreApplication.setOrganizationName("SebastianoRomi")
    QCoreApplication.setApplicationName("VitalChronicleScreenshot")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    with tempfile.TemporaryDirectory(prefix="vitalchronicle-screenshots-") as directory:
        store = HealthStore(Path(directory) / "demo.sqlite3")
        seed_demo_data(store)
        window = MainWindow(store=store, screenshot_mode=True)
        window.resize(1480, 920)

        window.tabs.setCurrentIndex(0)
        window.refresh_overview()
        capture(window, args.output / "overview.png")

        window.tabs.setCurrentIndex(1)
        window.load_data_type("steps")
        capture(window, args.output / "data-explorer.png")

        window.tabs.setCurrentIndex(2)
        window.ai_status_label.setText("● Ollama online · qwen3.5:9b disponibile")
        window.ai_status_label.setStyleSheet("font-weight: 700; color: #188038")
        capture(window, args.output / "ai-control-center.png")

        snapshot, period = window._build_ai_snapshot_for_chat(
            "selected", window._current_ai_period()
        )
        thread = window.conversation_store.create_thread(
            title="Sleep, activity and cardiac trends",
            model="qwen3.5:9b",
            scope="selected",
            period=period,
            snapshot=snapshot,
            snapshot_revision=store.data_revision(),
        )
        window.conversation_store.add_message(
            thread["id"],
            "user",
            "How have sleep, activity and cardiac metrics moved together this month?",
        )
        window.conversation_store.add_message(
            thread["id"],
            "assistant",
            "### Local demonstration summary\n\n"
            "- **Activity:** broadly stable against the personal baseline.\n"
            "- **Sleep:** duration is fairly consistent, with normal night-to-night variation.\n"
            "- **Cardiac metrics:** the synthetic wearable data alone cannot establish a "
            "clinical conclusion.\n\n"
            "This screen uses local demonstration data only.",
        )
        chat = window._ensure_ai_chat_window()
        chat.open_thread(thread["id"])
        capture(chat, args.output / "local-ai.png")
        chat.hide()

        window.tabs.setCurrentIndex(3)
        window.ai_ram_edit.setText("16")
        window.ai_token_edit.setText("8192")
        window.ai_token_recommendation.setText(
            "Consiglio dimostrativo: 8.192 token · modello 6,6 GB · "
            "limite fisico dichiarato dal modello 32.768 token."
        )
        capture(window, args.output / "ai-settings.png")
        window.close()

    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
