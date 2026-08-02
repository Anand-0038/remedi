# Why this wins

Remedi wins on three strengths judges can quickly verify:

1. **Closed-loop incident handling**
   - Not just detection: it traces failing assertions, computes lineage blast radius, generates remediation artifacts, and records outcome in catalog context.

2. **No fake success path**
   - Fixture mode is explicitly labeled and isolated.
   - Live failures are surfaced as failures; local success is not substituted.
   - Apply requires the exact sealed proposal (`run_id`) and rejects tampered/replay plans.

3. **Production-facing developer UX**
   - Single judge path: `make verify-local` + `make serve`.
   - Visual workflow in a lightweight UI (incident queue, proposal receipt, artifacts, selftest).
   - Strong guardrails (schema grounding, owner-safe writes, proposal integrity).

This is not just a tool demo; it is an evidence-first remediation workflow with explicit handoff semantics and a bounded, auditable boundary between discovery, recommendation, approval, and catalog write-back.
