# VitalChronicle Quick Start

Updated for VitalChronicle **1.1.1**.

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
   questions, or choose **Deep analysis of complete history** for a two-pass review of every
   available category. Use **Deterministic metrics** to inspect baselines and actual data
   coverage, **Model and tokens** for output settings, and **Prompt and instructions** to
   read the permanent model rules. The preprocessing and conversation history remain local.

If the selected period is only partly represented—for example, one observed week inside a
requested month—VitalChronicle shows the actual observed dates and requires the AI to limit
its answer accordingly. In chat, **Show prompt** reveals the exact latest local Ollama payload.

Read the [complete manual](manual.md) for platform-specific installation, privacy,
troubleshooting, exports, and AI configuration.

VitalChronicle is and will remain open source. A voluntary
[contribution](https://buymeacoffee.com/sebromi) helps sustain development.
