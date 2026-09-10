# Personal health agent

VitalChronicle's personal health agent is a local orchestration layer over the existing deterministic health-analysis pipeline and Ollama integration. It does **not** give the language model direct access to the health database, filesystem, terminal, browser, or arbitrary code execution.

## Design goals

The agent is designed to answer a health-data question by selecting the smallest useful set of deterministic tools, checking metric-specific data coverage, and then asking the local model to synthesize the returned evidence. Existing deterministic analysis remains the fallback path when the selected Ollama model does not support tool/function calling or when the personal agent is disabled.

The health archive remains the source of measured data and is read-only from the agent layer. Agent state is stored separately in `vitalchronicle_agent.sqlite3`, next to the local health database. That separate store contains learned tool definitions, explicit user feedback, user-specific associations, calibration metadata, and a local tool-event audit trail.

## Built-in health tools

The initial registry exposes deterministic capabilities for:

- available metrics, coverage, missing dates, metric series, daily summaries, baselines;
- sleep sessions, stages, awakenings, regularity, and sleep-debt estimates;
- HRV and resting-heart-rate status, recovery trends/anomalies, and readiness;
- cardio load, acute/chronic load, target load, training status, and workout summaries;
- recorded VO2-max/cardio-fitness evidence and training progression;
- stress-load and resilience estimates;
- explicit period comparison, trends, robust outliers, and exploratory same-day correlations;
- general daily-activity/workout suggestions grounded in the local evidence;
- tool-registry search, safe learned-tool creation, user-model lookup, and explicit feedback.

Readiness, cardio load, target load, training status, stress load, and resilience are **transparent VitalChronicle estimates** based on the user's own observed history. They do not reproduce or claim compatibility with proprietary Fitbit, Google, Garmin, or other vendor scores.

Every calculation is expected to return its method, available coverage, and confidence where applicable. Missing observations are omitted rather than treated as zero.

## Learned tools and the Tool Factory

A learned tool is not Python code. The Tool Factory accepts only a small declarative pipeline language and stores the validated pipeline in the separate agent database.

Allowed operations are currently:

`load_series`, `daily`, `window`, `summarize`, `trend`, `compare`, `correlate`, `count_above`, `count_below`, `ratio`, and `return`.

Pipelines are bounded in length and unrecognized operations are rejected. The agent must search the existing registry before creating a learned tool. Exact or near-equivalent capabilities are reused rather than duplicated.

On startup, built-in tools are synchronized with the registry. If a later VitalChronicle release contains a built-in capability equivalent to a previously learned tool, the learned tool is marked `superseded` and redirected to the built-in implementation instead of silently continuing as a duplicate.

## Personal calibration and feedback

Calibration is data-first. VitalChronicle calculates personal baselines, coverage, recovery/load context, sleep regularity, and workout history before deciding whether any question is useful. Questions are selected only when an answer can materially reduce uncertainty or improve future personalisation.

Answers are explicit user-reported context. Spontaneous statements such as “I feel tired today” are stored first as dated **self-report events**, not immediately promoted to stable traits. VitalChronicle may queue at most one targeted follow-up when one concise detail would materially improve future interpretation. Repeated, concordant observations can later support a learned association.

Personal context carries time semantics. Stable preferences can remain active, while temporary statements such as “I recently restarted the gym”, a current training goal, or a short-lived schedule change receive a validity window and freshness decay. Expired temporary context remains inspectable locally but is not injected into new agent analyses as current information.

Subjective reports and learned associations are never treated as evidence that a physiological state is medically safe. Subjective feedback cannot override safety-oriented language or convert a wearable-derived estimate into medical clearance.

The **Personal AI** panel allows the user to:

- enable or disable the personal agent;
- run or repeat calibration;
- inspect built-in, learned, active, and superseded tools;
- inspect tool usage and replacement history;
- inspect the user-specific associations VitalChronicle has learned;
- forget one personal association;
- delete an individual learned tool;
- reset agent personalisation without deleting the health archive or AI conversations.

During a conversation, a targeted feedback card can appear when the agent has queued a question that would improve personalisation. The user can answer or skip it.

## Ollama compatibility and fallback

When enabled, the agent uses Ollama's chat tool/function-calling interface. It runs a bounded tool loop and feeds tool results back to the same local model. Tool results are context-bounded before being returned to the model.

If the selected local model reports that tool/function calling is unsupported, VitalChronicle falls back to the existing deterministic snapshot and `OptimizedOllamaClient` synthesis path. Disabling the personal agent uses that same existing path directly.

## Safety boundary

The agent cannot create or execute arbitrary Python, shell commands, network calls, browser actions, filesystem actions, or direct health-database writes through learned tools. Its writable state is limited to the separate agent store used for safe tool definitions, feedback, personal associations, and audit events.

As with the rest of VitalChronicle, the system is intended for exploratory personal data analysis. Associations do not establish causation, and wearable-derived results are not diagnoses or treatment recommendations.
