#!/usr/bin/env python3
"""Generate platform icons from the canonical SVG."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "google_health_viewer" / "assets" / "app_icon.svg"
    output = root / "generated-icons"
    output.mkdir(exist_ok=True)

    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG icon: {source}")
    image = QImage(1024, 1024, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()

    png = output / "vitalchronicle.png"
    if not image.save(str(png), "PNG"):
        raise RuntimeError("Could not write PNG icon")
    with Image.open(png) as icon:
        rgba = icon.convert("RGBA")
        rgba.save(
            output / "vitalchronicle.ico",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        rgba.save(output / "vitalchronicle.icns")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
