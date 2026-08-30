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

English is the source and fallback language. Wrap every new user-facing string in
`google_health_viewer.i18n._()`, then run `python scripts/update_translations.py`.

Translate through Weblate whenever possible. Weblate commits catalogue changes back to GitHub;
placeholder and catalogue checks run automatically before translations are packaged. The
interface currently ships complete English and Italian catalogues. Documentation remains
English-first while community translations grow.

## Pull requests

Keep changes focused, add regression tests, preserve the historical local data-directory
identifier, and explain any privacy or OAuth implications. Synthetic data must be used for
screenshots and tests.

By contributing, you agree that your contribution is released under the MIT License.
