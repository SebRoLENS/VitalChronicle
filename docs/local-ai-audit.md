# Reproducible local AI audit

The audit runner uses the real query planner, deterministic snapshot builder and personal-agent runtime with an installed Ollama model. It opens the health archive in SQLite read-only mode. Each case gets an isolated agent database, so test self-reports, learned tools and monitoring rules do not alter the user's normal profile or conversations.

Create a private JSON file with representative questions. History is optional:

```json
[
  {"id": "daily_total", "question": "How many steps did I record on 2026-10-15? Give the daily total."},
  {"id": "sleep_hrv", "question": "Over the last two months, did low HRV coincide with shorter main sleep or later bedtimes?"},
  {"id": "followup", "question": "And the previous week?", "history": [
    {"role": "user", "content": "What was my average HRV from October 20 to 26, 2026?"},
    {"role": "assistant", "content": "The analysed period was October 20–26, 2026."}
  ]}
]
```

Run from the checkout with its Python environment:

```bash
python scripts/audit_local_ai.py \
  --health-db /path/to/health_data.sqlite3 \
  --prompts /private/path/prompts.json \
  --output /private/path/audit-run-1 \
  --model qwen3.5:9b
```

Use a new output directory for each round. `--only daily_total sleep_hrv` selects cases to repeat. `--profile fast` tests tool selection with reasoning disabled; the default is `standard`. `--context 32768` is a ceiling, not a request to load a full 32K context.

Run one audit process at a time against the same Ollama instance. The runner already executes cases sequentially. During local verification, overlapping runs with different context allocations left the Ollama scheduler waiting despite an idle inference slot; unloading the model through Ollama and retrying sequentially restored progress. Exclude those stalled runs from latency comparisons.

Each case stores the validated retrieval plan, compact final answer, operational trace, model-call/token counters, elapsed time and source fingerprint. A fresh worker process loads the production modules for each case. Results contain personal health information: keep the prompt and output directories outside the repository and do not publish them with code changes.

Assess the answers against independent calculations from the recorded data, rather than treating a fluent response or a zero exit code as a pass. Check the exact dates, units, denominators, missing observations, source-labelled main sleep, midnight/DST boundaries, and whether a requested calculation actually ran. Include unavailable metrics, uncovered dates, multi-period comparisons, follow-ups, nutrition logs versus the food catalogue, workout summaries and subjective self-reports.

The runtime keeps at most ten tool schemas in the normal local menu. Longitudinal calculations run in Python and return compact aggregates; the model receives bounded examples only when needed. Repeated identical reads reuse cached results, keep one evidence entry and temporarily defer the repeated reader so the next action can progress. Cache keys retain the complete arguments even when the model's evidence is compressed.

For supported routine numerical questions in Italian and English, the agent selects the calculation and Python renders the verified facts directly. This covers main-night associations, period comparisons, sleep stages and timing, weekday/weekend timing, recovery measurements, weight changes, descriptive metric statistics, recorded workouts/nutrition and the supported threshold-frequency analysis. A compact health-overview tool can summarize up to eight chosen metrics without returning daily rows; each metric retains its own coverage and extrema dates. Sleep trends identify the observation windows being compared and never equate longer duration with better sleep quality. Required calculations must finish before an answer is accepted. Composed custom analyses retain their full execution path; a preliminary routine read cannot replace them. Open-ended coaching and other languages still use model synthesis and require separate review.

Regression tests cover these contracts in `tests/test_agent_audit_regressions.py` and `tests/test_agent_sleep_session_evidence.py`. They do not require Ollama or personal data. Live audit runs complement those tests and should be repeated when changing tool selection, evidence compression, model settings or synthesis instructions. A small local model can still make unsupported statements; do not interpret this audit as a guarantee about every future phrasing.
