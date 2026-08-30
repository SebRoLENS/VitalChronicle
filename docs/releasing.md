# VitalChronicle Release Guide

Updated for VitalChronicle **1.0.1**.

## Automated path

A non-bot push to `main` that changes application or packaging files starts
`automatic-release.yml`.

1. The release-preparation script keeps `1.0.1` for the first public release and advances
   the patch version after an existing tag.
2. Lint, tests, compilation, metadata validation, screenshot generation, and the PDF
   manual build must succeed.
3. Generated metadata and documentation are committed by `github-actions[bot]`.
4. An immutable `vX.Y.Z` tag is created.
5. `build-desktop.yml` builds native packages on Linux, Windows, macOS Apple Silicon, and
   macOS Intel runners.
6. Python packages, a source archive, the manual, Sigstore material, and SHA-256 checksums
   are attached to the GitHub release.

The bot commit does not recursively start another release.

## Manual rebuild

Use **Actions → Build desktop packages → Run workflow** and provide an existing tag such
as `v1.0.1`. Existing release assets are replaced by filename.

## Version consistency

Before tagging, these files must agree:

- `google_health_viewer/__init__.py`;
- `pyproject.toml`;
- `README.md`;
- `docs/manual.md`;
- `docs/quick-start.md`;
- `CITATION.cff`.

Run locally:

```bash
python scripts/validate_project.py
ruff check .
pytest -q
```

## Security

Never add OAuth client JSON, access tokens, local databases, exported archives, model
weights, or personal health screenshots to a release. Only synthetic screenshots from
`scripts/generate_screenshots.py` belong in the repository.
