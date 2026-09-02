# Adaptive local-AI evidence retrieval

VitalChronicle now uses a **planner → Python → analyst** pipeline for ordinary AI questions.

1. The local model first receives only a metadata catalogue of datasets that actually exist on the computer: dataset keys and labels, local coverage dates, categories, and record counts. No health measurement values are included in this planning call.
2. The planner returns a constrained JSON request describing the data types and time window it needs. It cannot execute SQL or Python and may select only catalogue keys.
3. Python validates that request, clamps dates and breadth to safe values, and reads only the approved record types from the local SQLite archive.
4. VitalChronicle computes deterministic summaries, personal baselines, trends, coverage, anomalies, structured details, and exploratory associations from that selected evidence.
5. The final local-model call receives the compact deterministic evidence packet and produces the user-facing answer.

The question-period selector in the AI workspace is therefore no longer needed. Each question can request a different interval: a few days for a current-state question, weeks for a trend, longer matched periods for associations, or the available history when the question requires it. The global date controls remain available for the Overview and data explorer and do not constrain ordinary AI questions.

The existing compact-evidence retrieval layer remains installed as a final context-budget and request-integrity guard. For planner-managed snapshots it does **not** perform a second keyword-based semantic selection; the model planner has already selected the source data. Fast/Standard/Maximum profiles can still compact optional evidence detail to fit the physical context window.

If planning fails or returns invalid JSON, Python uses a broad but bounded local fallback instead of failing the conversation. Explicit **Analyse all data** requests continue to use the complete available local history.

All planning, extraction, deterministic processing, and model inference remain local when Ollama is used.