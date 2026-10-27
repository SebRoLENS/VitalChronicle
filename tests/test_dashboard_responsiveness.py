import threading
import time
from datetime import date

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from google_health_viewer import analysis, workers
from google_health_viewer.constants import DATA_TYPE_BY_KEY
from google_health_viewer.main_window import MainWindow
from google_health_viewer.storage import HealthStore


def until(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert predicate()


def test_download_completion_does_not_block_gui_or_queue_many_refreshes(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    calls, ticks = [], []
    gui_thread = threading.get_ident()

    def compact(_store, start, end, *, cancelled):
        calls.append((start, end, threading.get_ident()))
        entered.set()
        assert release.wait(3)
        return {"metrics": []}

    def forbidden(*args, **kwargs):
        raise AssertionError("Full AI extraction must not run for a dashboard refresh")

    monkeypatch.setattr(analysis, "build_overview_snapshot", compact)
    monkeypatch.setattr(analysis, "build_health_snapshot", forbidden)
    monkeypatch.setattr(analysis, "build_daily_progress_snapshot", lambda *a, **kw: {"metrics": []})
    window = MainWindow(store=HealthStore(tmp_path / "health.sqlite3"), screenshot_mode=True)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()

    class Client:
        def __init__(self, _credentials):
            pass

        def iter_data_pages(self, *args):
            yield [
                {
                    "name": "sample",
                    "startTime": "2026-10-27T12:00:00Z",
                    "dailyHeartRateVariability": {"averageHeartRateVariabilityMilliseconds": 70},
                }
            ]

    class Credentials:
        def save_credentials(self, _credentials):
            return True

    monkeypatch.setattr(workers, "GoogleHealthClient", Client)
    monkeypatch.setattr(workers, "DATA_TYPES", [DATA_TYPE_BY_KEY["daily-heart-rate-variability"]])
    download = workers.SyncThread(
        None,
        window.store,
        Credentials(),
        date(2026, 10, 27),
        date(2026, 10, 27),
        include_resources=False,
    )
    finished = []
    download.completed.connect(window._sync_completed)
    download.completed.connect(lambda *args: finished.append(args))
    try:
        until(app, entered.is_set)
        assert window._overview_task.signals.parent() is app
        download.start()
        until(app, lambda: bool(finished) and len(ticks) >= 10)
        assert calls[0][2] != gui_thread
        for _ in range(10):
            window.refresh_overview()
        assert len(calls) == 1
        old = window.current_snapshot
        window._overview_ready(1, {"stale": True}, {})
        assert window.current_snapshot is old
        release.set()
        until(app, lambda: window._overview_task is None)
        assert len(calls) == 2  # active request plus only the latest pending refresh
        assert window.store.counts()["daily-heart-rate-variability"] == 1
    finally:
        release.set()
        download.wait(3000)
        timer.stop()
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
        app.processEvents()
