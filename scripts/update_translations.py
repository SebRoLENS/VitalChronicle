#!/usr/bin/env python3
"""Refresh the Weblate JSON catalogues from ``_()`` calls in Python sources."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "google_health_viewer"
LOCALES = PACKAGE / "locales"


def source_messages() -> list[str]:
    messages: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            message = node.args[0].value
            if message not in messages:
                messages.append(message)
    return messages


def write_catalogue(language: str, messages: list[str]) -> None:
    path = LOCALES / f"{language}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    catalogue = {
        message: message if language == "en" else existing.get(message, "")
        for message in messages
    }
    path.write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    messages = source_messages()
    write_catalogue("en", messages)
    write_catalogue("it", messages)
    print(f"Updated {len(messages)} source messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
