# Contributing to VitalChronicle

Thank you for helping improve VitalChronicle. The project is and will remain open source.

## Before opening an issue

- Search existing issues.
- State the VitalChronicle version and operating system.
- Include reproducible steps and the complete error text.
- Remove health records, Google OAuth credentials, access tokens, database files, and
  personally identifying screenshots.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
pytest -q
python scripts/validate_project.py
```

Keep user-facing application text in Italian for the current development series. Project
documentation is English-first until the planned application translation is complete.

## Pull requests

Keep changes focused, add regression tests, preserve the historical local data-directory
identifier, and explain any privacy or OAuth implications. Synthetic data must be used for
screenshots and tests.

By contributing, you agree that your contribution is released under the MIT License.
