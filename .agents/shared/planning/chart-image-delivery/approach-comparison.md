# Approach Comparison — chart-image-delivery transport channel

**Date:** 2026-10-04 · **Author:** architect (synthesis of council `bf921a19` verdict) · **Companion:** `architecture-recommendation.md` (Focus 1 / §4)

**Question:** How should a rendered-image reference travel from charter through LLM turns to the chat-platform adapter — in-band content marker, out-of-band metadata channel, or hybrid?

**Verdict: Option A (in-band marker only), hardened.** Locked marker regex stays byte-stable.

| Axis | A — in-band marker | B — out-of-band sidecar | C — hybrid |
|------|--------------------|------------------------|------------|
| Complexity | **Lowest** — one regex, stateless | Highest — stash + turn-scoping + `dispatch_message` signature change + suppression logic | A + most of B's machinery |
| Scalability | **Stateless; store reads only for matched ids** | Per-instance stash growth needs TTL/eviction | B's fallback-path costs |
| Maintainability | **One pinned contract; failures observable in content** | Truth split across two channels | Two channels + precedence rules; most test surface |
| Risk | LLM-behavioral only (strip → graceful text; stale-id → wrong image, accepted + ledgered) | Systematic wrong-image; API callers lose PNG refs or §http-api pin violated | A's residuals + B's wrong-image tail at lower probability |
| Cost | **Minimal** | ~150–300 LOC + new seams | Slightly under B, well over A |
| **Recommendation** | **✅ ADOPT (hardened)** | Reject | Reject |

**Why A wins (dominant axes — Complexity + Risk):** the in-band marker is self-correlating (position in content = intent to deliver), stateless, and preserves §http-api by construction. The decisive evidence against B/C is the **correlation crux**: the parent agent may call `generate_chart` and deliberately not include the chart — a content-blind sidecar either over-delivers (wrong images) or starves the one case it exists to fix; there is no sound suppression rule. C's fallback additionally runs on the lane that carries no `instance_id` (`dispatcher.py:189-198`), making a per-instance sidecar unreachable without a hot-path signature change.

**Adopted hardening for A:** (1) id-dedupe preserving first-occurrence order; (2) per-id resolution isolation (hallucinated id → WARN, siblings deliver); (3) optional strip-only near-miss sweeper (secondary pattern, never extracts, locked regex untouched); (4) the Phase A skill-paragraph + Phase C 21-agent guidance mitigation stack.

**Accepted residual:** real stale/foreign id → wrong image attached (1–3%, undetected under A) — ledgered in the deferred-items ledger; revisit trigger = observable strip-rate >~10% post-Phase C or user report. A mint-ledger at an instance-carrying seam is recorded as sound design debt for that revisit.

**Dissent (recorded):** the coding lane proposed C-lite (mint ledger + regex relaxation); rejected for v1 — fallback-lane check site cannot close the normal case, the delivery seam lacks `instance_id`, and relaxation conflicts with the LOCKED byte-stable regex pin.
