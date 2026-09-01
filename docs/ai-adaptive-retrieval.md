# Adaptive local-AI evidence retrieval

VitalChronicle keeps the complete deterministic health snapshot locally and selects a smaller evidence packet for each local-AI request.

Desktop evidence targets:

- **Fast:** about 1,200 compact JSON tokens.
- **Standard:** about 2,500 compact JSON tokens.
- **Maximum:** up to the existing approximately 4,000-token compact packet.
- **Maximum deep analysis:** preserves the complete compact evidence packet.

Specific metric questions retain the requested metrics, their coverage, matching deterministic insights, relevant reported associations, and any association diagnostics already selected for the request. Domain questions retain that domain. Unknown questions fall back to a broad profile-sized packet rather than guessing aggressively.

The complete deterministic snapshot is never discarded or replaced by this retrieval layer. The selector does not re-read health records and does not recalculate statistics; it only filters the already prepared compact evidence immediately before local inference.
