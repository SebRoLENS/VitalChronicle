<p align="center">
  <img src="google_health_viewer/assets/app_icon.svg" width="112" alt="VitalChronicle icon">
</p>

<h1 align="center">VitalChronicle</h1>

<p align="center"><strong>Your health history, privately understood.</strong></p>

<p align="center">
  <a href="https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest"><img src="https://img.shields.io/github/v/release/SebRoLENS/google-health-dashboard-ai?label=release" alt="Latest release"></a>
  <a href="https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest"><img src="https://img.shields.io/badge/Linux-AppImage-FCC624?logo=linux&logoColor=black" alt="Linux AppImage"></a>
  <a href="https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest"><img src="https://img.shields.io/badge/Windows-x86__64-0078D4?logo=windows" alt="Windows x86-64"></a>
  <a href="https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest"><img src="https://img.shields.io/badge/macOS-Intel%20%7C%20Apple%20Silicon-000000?logo=apple" alt="macOS Intel and Apple Silicon"></a>
  <a href="https://github.com/SebRoLENS/google-health-dashboard-ai/actions/workflows/ci.yml"><img src="https://github.com/SebRoLENS/google-health-dashboard-ai/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://hosted.weblate.org/projects/vitalchronicle/application/"><img src="https://hosted.weblate.org/widget/vitalchronicle/svg-badge.svg" alt="Translation status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <a href="https://buymeacoffee.com/sebromi"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-support%20development-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>
</p>

VitalChronicle is a local-first desktop application for downloading, storing,
exploring, and exporting personal data made available through the Google Health API.
It combines adaptive visualisations with optional analysis performed by a local Ollama
model. Health records stay on the computer unless the user explicitly exports them.

> **VitalChronicle is and will remain free and open-source software.** If it is useful
> to you, a voluntary [contribution through Buy Me a Coffee](https://buymeacoffee.com/sebromi)
> helps keep development active and the downloadable packages available.

Another valuable way to support this open-source project is to
**[help translate VitalChronicle on Weblate](https://hosted.weblate.org/projects/vitalchronicle/application/)**.
No programming experience is required.

VitalChronicle is an independent project. It is not affiliated with, endorsed by, or
supported by Google. It is an exploratory wellness-data tool, not a medical device and
not a replacement for professional medical advice.

## Download

**Current release: [VitalChronicle v1.1.15](https://github.com/SebRoLENS/google-health-dashboard-ai/releases/tag/v1.1.15)**

**[Download the latest VitalChronicle release for Linux, Windows, or macOS](https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest)**

The release workflow rewrites the explicit version and tag link above whenever it publishes
a new version. The **release** badge and generic download links also follow GitHub's latest
release; the explicit line does not depend on the external badge cache.

| Platform | Release package | Notes |
|---|---|---|
| Linux x86-64 | AppImage + Sigstore bundle | No Python installation required |
| Windows x86-64 | Standalone `.exe` | Unsigned; SmartScreen may warn |
| macOS Apple Silicon | `.dmg` | Ad-hoc signed, not notarised |
| macOS Intel x86-64 | `.dmg` | Ad-hoc signed, not notarised |
| Python 3.10+ | Wheel and source archive | For development or unsupported systems |
| Android | Not currently included | Requires a separate mobile UI and OAuth flow |

Windows and macOS packages are not signed with paid platform certificates. Download
only from the official release page and verify `SHA256SUMS.txt`. The Linux AppImage is
attested through GitHub Actions and includes a detached Sigstore bundle.

## Interface

Screenshots are generated from the real application using synthetic demonstration data.
No personal health record or credential is included in this repository.

### Seven-day overview

![VitalChronicle overview with daily progress and physiological trends](docs/screenshots/overview.png)

The overview distinguishes cumulative metrics from physiological measurements. Steps,
sleep, and active-zone minutes use completion bars against the preceding seven-day
average. Heart rate and other vital measurements use neutral above/below-baseline
comparisons, compact trend plots, and a one-standard-deviation reference band.

### Data explorer

![VitalChronicle data explorer with an adaptive steps chart](docs/screenshots/data-explorer.png)

Charts adapt to the selected record: daily bars for steps and energy, point clouds for
dense measurements, stacked bars for sleep stages and heart-rate zones, and readable
vertical ranges for noisy measurements. Panning and zooming operate on time.

### Private local AI workspace

![VitalChronicle local AI control centre](docs/screenshots/ai-control-center.png)

All AI controls now live in one workspace. Its four internal sections separate analysis
and chat, deterministic metrics, model/token configuration, and prompt inspection without
adding unrelated top-level pages. Conversations open in a larger, resizable window with
local thread history, follow-up questions, stop/regenerate controls, Markdown export, an
evidence drawer, and the exact prompt used for the latest query.

![VitalChronicle local AI conversation](docs/screenshots/local-ai.png)

Thinking and the final answer share one assistant area. Each conversation pins its data
snapshot and visibly offers a refresh when newer local data become available.

Before Ollama receives anything, deterministic Python preparation calculates personal
7/28/90-day baselines, matched recent-versus-previous periods, robust anomalies, trends,
weekly patterns, coverage gaps, sleep-stage and workout comparisons, plus same-day and
one-day-lagged associations. Evidence is ranked so the model can synthesize sustained,
multi-metric patterns instead of merely reporting that one day differs from another.
Incomplete cumulative totals are compared only with previous days at the same local time.

### Transparent deterministic evidence

![VitalChronicle deterministic metrics inspector](docs/screenshots/ai-deterministic-metrics.png)

The inspector shows exactly what Python calculated before Ollama is called: measurement
coverage, 7-day baselines, matched changes, trends, robust anomalies, associations, and
ranked evidence. Selecting a row reveals the complete local JSON and interpretation limits.
If a month was requested but only one week is observed, the interface reports both ranges
and the AI must begin by limiting its conclusions to that week. Missing measurements are
never silently treated as zero or as a complete requested interval.

Isolated and consecutive missing dates are preserved as compact ranges for each daily
metric. A metric with 29 observed days out of 32 therefore remains explicitly incomplete,
even when another metric contains measurements on all 32 days; the AI is forbidden to
interpolate across those gaps.

Dated reference settings are kept separate from measurements. In particular, Google's
personal heart-rate-zone thresholds remain available as context, but their dates cannot
increase health-data coverage or generate physiological trends.

The evidence packet is placed in the latest model message so it cannot be discarded before
the question when the context is crowded. VitalChronicle reads and remembers the physical
context limit reported by Ollama, removes duplicated evidence, preserves every calculated
metric, and reduces only the available response budget when necessary. A false “no evidence
provided” response is automatically rejected and retried with an evidence-first request.

The header always shows the installed version. Its badge turns amber and displays the newer
version after the private update check discovers a more recent GitHub release. This check runs
at every application startup and once per hour while VitalChronicle remains open.
Portable Linux AppImages and Windows executables can then install the matching package from
inside the app: VitalChronicle verifies SHA-256, replaces the file in its current location,
and keeps the previous build as a `.previous` backup. Source, virtual-environment, and macOS
installs continue through the official release page rather than attempting an unsafe replacement.

### AI memory and token settings

![VitalChronicle local AI RAM and token settings](docs/screenshots/ai-settings.png)

The user enters installed RAM and receives a model-aware token recommendation. The
value remains editable and is limited only by the physical context reported by Ollama.

### Inspectable model instructions

![VitalChronicle system prompt inspector](docs/screenshots/ai-prompt.png)

The permanent system prompt is readable inside the AI workspace. Its technical instructions
always remain in English and are deliberately excluded from Weblate; a short English directive
selects the language of the final answer from the interface language. During a conversation,
**Show prompt** displays the exact messages sent for the latest request, including the
processed evidence JSON, retained conversation context, and current question. This view
remains local and may contain processed health information, so it should be reviewed before
copying or sharing.

While a request is active, the chat displays an animated working panel with elapsed time and
the real stage reached by the application: local-data preparation, evidence ranking, Ollama
processing, synthesis, retry, or final-answer generation. It deliberately avoids a fictional
percentage because local models do not expose reliable total progress.

## Main capabilities

- seven-step beginner-friendly Google Cloud and OAuth configuration inside the application,
  including direct links, completion checks, JSON validation, and browser fallbacks;
- incremental synchronisation that downloads only missing intervals;
- silent refresh at startup and every ten minutes while the application is open;
- per-category error isolation, warnings, pagination, and resumable local coverage;
- local SQLite archive with CSV, JSON, and complete ZIP export;
- purpose-specific plots for activity, sleep, heart, oxygen, temperature, nutrition,
  workouts, sedentary time, body measurements, and other available categories;
- readable time windows and robust vertical scales for dense measurements;
- seven-day personal baselines and transparent statistical bands;
- deterministic AI preparation with matched periods, multi-horizon personal baselines,
  robust anomaly detection, requested-versus-observed coverage, data-quality scoring, and
  cautious cross-metric associations;
- a dedicated inspector for every calculated deterministic parameter and its raw local JSON;
- persistent local AI conversations in a separate resizable window, including follow-up
  questions, pinned/updatable data snapshots, visible thinking, evidence, exact-prompt
  inspection, export, and stop;
- two-pass complete-history synthesis through local Qwen/Ollama, with NVIDIA 16 GB and
  CPU-only 32 GB profiles;
- model-update notifications and model-context-aware token recommendations;
- automatic in-app checks for new VitalChronicle releases, a manual
  **Help → Check for updates** command, and verified same-format self-updates for portable
  Linux AppImages and Windows executables;
- no cloud AI service and no transmission of health data to the developer;
- robust system-language detection, a persistent **Settings → Language** selector, English
  fallback, and community catalogues managed through Weblate.

## Quick start

1. Download the package for your operating system from the
   [latest release](https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest).
2. Start VitalChronicle.
3. Select **Google setup** and follow the wizard.
4. Create a personal Google Cloud project, enable the Google Health API, and add your
   account as a test user if the OAuth project is in Testing mode.
5. Create an OAuth client of type **Web application** with this exact redirect URI:

   ```text
   http://localhost:8765/
   ```

6. Download the OAuth JSON once and import it in the wizard.
7. Sign in through the browser and select **Download / update**.

The complete procedure, screenshots, scope list, and troubleshooting steps are in the
**[detailed user manual](docs/manual.md)**. A versioned PDF manual is attached to every
release.

## Install from source

Python 3.10 or newer is required. On Fedora:

```bash
sudo dnf install python3 python3-pip
git clone https://github.com/SebRoLENS/google-health-dashboard-ai.git
cd google-health-dashboard-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
vitalchronicle
```

On Windows activate the environment with `.venv\Scripts\activate`; on macOS and
other Unix-like systems use `source .venv/bin/activate`.

## Google OAuth: Testing or Production?

Testing mode is convenient for personal use and does not require public verification.
Add the intended Google accounts under **Audience → Test users**. For an external OAuth
project in Testing, Google normally issues refresh tokens that expire after seven days,
so VitalChronicle may occasionally ask the user to sign in again.

Production status is not mandatory for personal use. Publishing an app that requests
sensitive or restricted scopes to a wider audience can require Google verification.
Each VitalChronicle user creates and controls their own OAuth client; client secrets and
tokens must never be committed to GitHub or shared with the developer.

## Local AI setup

Install [Ollama](https://ollama.com/), start its service, and pull a compatible model.
For example:

```bash
ollama pull qwen3.5:9b
ollama list
curl -sS http://127.0.0.1:11434/api/version
```

VitalChronicle does not present local-model output as a diagnosis. Wearable data may be
incomplete or inaccurate, correlations do not establish causality, and important health
decisions should be discussed with a qualified professional.

## Documentation

- [Detailed manual](docs/manual.md)
- [Quick start](docs/quick-start.md)
- [Release and maintenance guide](docs/releasing.md)
- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)

## Privacy and data ownership

Health data are stored in a private local SQLite database. OAuth credentials use the
system keyring when available and otherwise fall back to a user-readable local file with
restricted permissions. Local Ollama analysis is sent only to `127.0.0.1`. The only
remote services contacted are Google for authorised health-data access and the Ollama
registry when checking whether model weights have changed.

The application deliberately retains the historical internal `GoogleHealthViewer` data
directory name so that upgrading from 0.2.12 to current VitalChronicle releases preserves existing
records, credentials, and settings.

## Support development

VitalChronicle is developed as free, open-source software and will remain so. No feature
is placed behind a donation. If the project saves you time, you can
**[support its continued development on Buy Me a Coffee](https://buymeacoffee.com/sebromi)**.

An equally useful non-financial contribution is to
**[translate the application on Weblate](https://hosted.weblate.org/projects/vitalchronicle/application/)**.
Translations committed by Weblate are validated by GitHub Actions and included automatically in
subsequent application releases.

The same link is available inside the application toolbar and **Help** menu.

## Author and contact

Sebastiano Romi<br>
[sebastiano.romi@gmail.com](mailto:sebastiano.romi@gmail.com)

Bug reports and feature requests belong in the
[GitHub issue tracker](https://github.com/SebRoLENS/google-health-dashboard-ai/issues).

## License

VitalChronicle is released under the [MIT License](LICENSE). The source code is public,
and the software is free to use, study, modify, and redistribute under that license.

If you would like to help the project while keeping it open for everyone,
[Buy Me a Coffee](https://buymeacoffee.com/sebromi).
