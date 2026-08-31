# VitalChronicle Android

This branch contains the native Android 0.1 implementation of VitalChronicle.

## Architecture

The Android UI, OAuth flow, Google Health transport and Gemini Nano integration are Kotlin/Android components. The deterministic health-analysis engine is **not rewritten**: the build copies the same `analysis.py`, `ai_insights.py`, `constants.py`, `i18n.py` and `utils.py` modules used by VitalChronicle desktop and executes them on Android through Chaquopy.

This intentionally keeps the scientific/data interpretation layer synchronized between desktop and mobile while allowing each platform to use an appropriate UI and OS integration.

## AI privacy

Automatic AI uses Android's on-device Gemini Nano through ML Kit GenAI when supported. Health evidence is prepared locally by the shared deterministic core. No cloud AI endpoint is used. On unsupported devices the deterministic evidence inspector remains functional.

## Google Health

Android 0.1 accepts the same OAuth Web client JSON used by VitalChronicle desktop, including the `http://localhost:8765/` loopback redirect. OAuth credentials and tokens are encrypted using Android Keystore. Google Health records are stored in a SQLite schema compatible with the desktop archive.

## Version

Android app version: **0.1.0**.
Desktop core source: recorded in `shared_core_revision.json` at build time.

This branch is temporary. The intended public home is a separate `VitalChronicle-Android` repository, which will synchronize the shared deterministic core from `SebRoLENS/VitalChronicle` rather than duplicating its logic.
