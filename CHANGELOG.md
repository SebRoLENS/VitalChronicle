# Changelog

All notable changes to VitalChronicle are documented here.

## 1.0.10 — 2026-08-31

- automated maintenance release with validated application and packaging updates.

## 1.0.9 — 2026-08-31

- automated maintenance release with validated application and packaging updates.

## 1.0.8 — 2026-08-31

- automated maintenance release with validated application and packaging updates.

## 1.0.7 — 2026-08-30

- automated maintenance release with validated application and packaging updates.

## 1.0.6 — 2026-08-30

- automated maintenance release with validated application and packaging updates.

## 1.0.5 — 2026-08-30

- automated maintenance release with validated application and packaging updates.

## 1.0.4 — 2026-08-30

- automated maintenance release with validated application and packaging updates.

## 1.0.3 — 2026-08-30

- automated maintenance release with validated application and packaging updates.

## 1.0.2 — 2026-08-30

- translated the complete application source interface to English while retaining a
  complete Italian catalogue;
- added automatic system-language detection with English fallback and an optional
  `VITALCHRONICLE_LANGUAGE` override;
- made local-AI instructions and answers follow the selected interface language;
- added Weblate-ready JSON catalogues, placeholder validation, packaging checks, and
  automatic inclusion of community translations in releases;
- regenerated all documentation screenshots from the English interface;
- corrected respiratory-rate units when the source field contains `perMinute`.

## 1.0.1 — 2026-08-30

- replaced Python's browser launcher with a frozen-app-safe, cross-platform external
  link handler for Google OAuth, documentation, GitHub, Ollama, and support links;
- added a visible **Report an issue** toolbar action linked to the GitHub issue form;
- removed institutional affiliation and contact details from the application and all
  public documentation, retaining only Sebastiano Romi and `sebastiano.romi@gmail.com`;
- added regression tests for sanitized desktop-launch environments.

## 1.0.0 — 2026-08-30

- public rebrand from Google Health Viewer to **VitalChronicle** while preserving the
  historical local data and credential directories for seamless upgrades;
- polished English README, Quick Start, detailed user manual, security policy,
  contribution guide, citation metadata, and maintainer release guide;
- direct Buy Me a Coffee links in the toolbar, Help menu, About dialog, README, and
  documentation; VitalChronicle remains free and open source;
- deterministic screenshots generated from the real application with synthetic data;
- native automated packages for Linux x86-64, Windows x86-64, macOS Apple Silicon, and
  macOS Intel, plus Python wheel/source distributions and checksums;
- automated validation, semantic release preparation, documentation build, immutable
  tags, GitHub Releases, and Linux Sigstore attestation;
- all functionality and fixes from the stable 0.2.12 application baseline, including
  incremental Google Health updates, adaptive charts, seven-day overview baselines,
  current-day smoothed heart-rate display, complete time-aware AI preparation, one-panel
  thinking/final output, and RAM-aware token recommendations.

## Pre-1.0 development series

Versions 0.1.0 through 0.2.12 established the Google Health OAuth wizard, local SQLite
archive, incremental synchronisation, metric-specific visualisations, dashboard,
exporting, and local Ollama integration. The complete 0.2.12 code is the functional
baseline for VitalChronicle 1.0.0.
