# Changelog

All notable changes to VitalChronicle are documented here.

## 1.5.0 — 2026-09-04

- automated maintenance release with validated application and packaging updates.

## 1.2.0 — 2026-09-02

- automated maintenance release with validated application and packaging updates.

## 1.1.18 — 2026-09-02

- automated maintenance release with validated application and packaging updates.

## 1.1.17 — 2026-09-02

- automated maintenance release with validated application and packaging updates.

## 1.1.16 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.15 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.14 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.13 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.12 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.11 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.10 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.9 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.8 — 2026-09-01

- automated maintenance release with validated application and packaging updates.

## 1.1.7 — 2026-08-31

- automated maintenance release with validated application and packaging updates.

## 1.1.6 — 2026-08-31

- added a prominent animated working panel to the local-AI chat;
- shows elapsed time and explains that processing time depends on the selected model and
  hardware;
- reports genuine application stages for local-data preparation, evidence ranking, Ollama
  processing, synthesis, compact retry, and final-answer generation;
- keeps the existing unified thinking/answer transcript unchanged and avoids invented
  completion percentages;
- added English/Italian translations, interface styling, documentation, and regression tests.

## 1.1.5 — 2026-08-31

- automated maintenance release with validated application and packaging updates.

## 1.1.4 — 2026-08-31

- kept the local-AI system prompt invariantly in English in every interface language;
- removed the technical system prompt from the Weblate source catalogue so community
  translations cannot alter the instructions sent to Ollama;
- added a short English runtime directive that makes the final answer follow the selected
  interface language while preserving JSON field names and evidence identifiers;
- changed application-update checks to run at every startup and once every hour while the
  app remains open, without the previous persisted 24-hour suppression;
- added regression tests and updated the English documentation.

## 1.1.3 — 2026-08-31

- added exact compact missing-date ranges for the complete measurement scope and every
  daily metric, including isolated and consecutive internal gaps;
- made even minor gaps such as 29 observed days out of 32 mandatory AI coverage context,
  while preventing one complete metric from hiding gaps in another;
- instructed the local model never to fill, interpolate, or average across missing dates;
- added a persistent clickable version badge that changes appearance and shows the latest
  version whenever an update is available;
- added verified in-app self-updates for portable Linux AppImages and Windows executables;
  the matching package replaces the file in its current location and retains a backup;
- shortened the AI question-period label and complete-history analysis button to prevent
  truncation, while retaining their full explanations as tooltips;
- added English/Italian translations, documentation, and regression tests, including
  package-format selection, checksum failure, and same-path replacement coverage.

## 1.1.2 — 2026-08-31

- fixed interval coverage being inflated by dated personal heart-rate-zone thresholds;
- classified heart-rate-zone limits as reference configuration rather than physiological
  measurements, while retaining their latest values as useful context;
- excluded reference settings from measured-day counts, baselines, trends, anomalies, and
  cross-metric associations;
- made the AI and deterministic-metrics inspector use actual measurement days and each
  metric's own observed-day coverage;
- added English/Italian interface text, documentation, and a regression test reproducing
  32 days of thresholds alongside only six days of real measurements.

## 1.1.1 — 2026-08-31

- fixed long local-AI prompts that could cause Ollama to discard the deterministic health
  evidence and incorrectly report that no data had been supplied;
- moved the evidence JSON into the latest user message, removed only duplicated payloads,
  and sized the request from the estimated input rather than the output budget alone;
- automatically reads and persists each Ollama model's physical context limit, preserving
  all calculated metrics while reducing response space when the shared context is crowded;
- detects false missing-evidence answers, discards them before chat persistence, and retries
  with a compact evidence-first request;
- added context-budget visibility, English/Italian translations, and regression tests for
  large evidence packets and evidence-preserving retries.

## 1.1.0 — 2026-08-31

- reorganized local AI into one coherent workspace with dedicated **Analysis and chat**,
  **Deterministic metrics**, **Model and tokens**, and **Prompt and instructions** sections;
- added a transparent deterministic-metrics inspector for baselines, matched changes,
  trends, anomalies, cross-metric associations, ranked evidence, and their raw local JSON;
- added requested-versus-observed interval coverage, per-metric completeness, and a
  mandatory AI limitation notice when the selected period is only partly represented;
- exposed the permanent system prompt in the AI workspace and the exact latest Ollama
  messages—including evidence JSON, conversation context, and the question—inside chat;
- improved AI-page spacing, coverage warnings, prompt inspection, English/Italian text,
  screenshots, tests, and user documentation.

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
