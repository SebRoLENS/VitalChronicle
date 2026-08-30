# VitalChronicle Quick Start

Updated for VitalChronicle **1.0.2**.

1. Download the package for your operating system from the
   [latest GitHub release](https://github.com/SebRoLENS/google-health-dashboard-ai/releases/latest).
2. Start VitalChronicle and select **Configurazione Google**.
3. In Google Cloud, create a project and enable the Google Health API.
4. Configure the OAuth audience. For personal use, Testing mode is acceptable: add your
   Google account under **Test users**.
5. Add the Google Health read-only scopes shown by the VitalChronicle wizard.
6. Create an OAuth **Web application** client and add this exact redirect URI:

   ```text
   http://localhost:8765/
   ```

7. Download the JSON client credentials and import the file in VitalChronicle.
8. Complete browser sign-in, then select **Scarica / aggiorna**.
9. To use local AI, install Ollama, pull a model such as `qwen3.5:9b`, and verify it from
   the **Analisi AI locale** page.

Read the [complete manual](manual.md) for platform-specific installation, privacy,
troubleshooting, exports, and AI configuration.

VitalChronicle is and will remain open source. A voluntary
[contribution](https://buymeacoffee.com/sebromi) helps sustain development.
