# VitalChronicle Release Guide

Updated for VitalChronicle **1.3.0**.

## Automated path

A non-bot push to `main` that changes application or packaging files starts
`automatic-release.yml`.

1. The release-preparation script keeps a manually advanced semantic version when one is
   already declared. Otherwise it advances from the latest tag according to the triggering
   commit message: the default is a patch release, `[minor]` requests a feature/minor release,
   and `[major]` requests a major release.
2. Lint, tests, compilation, metadata validation, screenshot generation, and the PDF
   manual build must succeed.
3. Generated metadata and documentation are committed by `github-actions[bot]`.
4. An immutable `vX.Y.Z` tag is created.
5. `build-desktop.yml` builds native packages on Linux, Windows, macOS Apple Silicon, and
   macOS Intel runners.
6. Python packages, a source archive, the manual, Sigstore material, and SHA-256 checksums
   are attached to the GitHub release.

The bot commit does not recursively start another release.

## Version policy

Use semantic versioning consistently:

- bug fixes and maintenance: `x.x.y` (patch, default);
- user-facing features: `x.y.0` (`[minor]` in the triggering commit);
- breaking changes: `x.0.0` (`[major]` in the triggering commit).

## Manual rebuild

Use **Actions → Build desktop packages → Run workflow** and provide an existing tag such
as `v1.3.0`. Existing release assets are replaced by filename.

## Version consistency

Before tagging, these files must agree:

- `google_health_viewer/__init__.py`;
- `pyproject.toml`;
- `README.md` (the explicit current-release line and tag link);
- `docs/manual.md`;
- `docs/quick-start.md`;
- `CITATION.cff`.

`scripts/prepare_release.py` rewrites the explicit version in `README.md` before the tag is
created. Generic download buttons still target GitHub's `releases/latest`, while the
machine-maintained version line links to the exact tag and remains correct even if the
external Shields.io badge is temporarily cached.

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