# VitalChronicle Quick Start

Updated for VitalChronicle **1.1.17**.

1. Download the package for your operating system from the
   [latest GitHub release](https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest).
2. Start VitalChronicle and select **Google setup**.
3. In Google Cloud, create a project and enable the Google Health API.
4. Configure the OAuth audience. For personal use, Testing mode is acceptable: add your
   Google account under **Test users**.
5. Add the Google Health read-only scopes shown by the VitalChronicle wizard.
6. Create an OAuth **Web application** client and add this exact redirect URI:

   ```text
   http://localhost:8765/
   ```

7. Download the JSON client credentials and import the file in VitalChronicle.
8. Complete browser sign-in, then select **Download / update**.
9. To use local AI, install Ollama, pull a model such as `qwen3.5:9b`, and verify it from
   **Local AI analysis → Analysis and chat**. Open the separate chat window for follow-up
   questions, or choose **Analyse all data** for a two-pass review of every
   available category. Use **Deterministic metrics** to inspect baselines and actual data
   coverage, **Model and tokens** for output settings, and **Prompt and instructions** to
   read the permanent English model rules. They are excluded from Weblate, while the final
   answer still follows the selected interface language. The preprocessing and conversation
   history remain local.
   While Ollama works, an animated panel confirms the accepted request and shows elapsed time
   plus the latest real preparation or inference stages.

If the selected period is only partly represented—for example, one observed week inside a
requested month—VitalChronicle shows the actual measurement dates and requires the AI to limit
its answer accordingly. In chat, **Show prompt** reveals the exact latest local Ollama payload.

Personal heart-rate-zone thresholds are reference settings, not measurements. Their dated
records do not increase the reported health-data coverage.

Daily metrics retain compact ranges for isolated and consecutive missing dates. The version
badge beside the application title turns amber when a newer release is available.
The check runs at every startup and then once per hour while the app remains open.
Portable AppImage and Windows builds can select **Update now** to download only the matching
package, verify SHA-256, replace the file at the same path, and retain the previous build.

Read the [complete manual](manual.md) for platform-specific installation, privacy,
troubleshooting, exports, and AI configuration.

VitalChronicle is and will remain open source. A voluntary
[contribution](https://buymeacoffee.com/sebromi) helps sustain development.
