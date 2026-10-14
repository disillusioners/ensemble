# Phase 2: Dispatcher Marker-Family Generalization

## Objective

Generalize ens-img marker extraction in `daemon/sources/dispatcher.py` from the LOCKED
chart-render single-feature form to a feature FAMILY (chart-render + image-gen), so
image-gen markers flow through both dispatch seams into native chat uploads — while the
chart-render path stays byte-compatible (all existing tests pass with ZERO assertion
edits) and the LOCKED regex keeps its R5-gated byte-stability.

## Marker Family Design (req#4)

**Feature token grammar (D2):** `ens-img:([a-z0-9-]+):([a-f0-9]{32})` — lowercase-kebab
feature token, 32-hex image id, full-line anchored (same anchoring discipline as the
locked form).

**Regex architecture — three passes, order preserved (F12 invariant extended, not
replaced):**

1. **Locked pass (UNCHANGED, byte-stable):** existing
   `_MARKER_RE = ^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$` (`dispatcher.py:29-32`)
   runs FIRST exactly as today. R5 note in-file stays; this pass does not change by one
   byte.
2. **Family pass (NEW):** `_FAMILY_MARKER_RE = ^<!-- ens-img:([a-z0-9-]+):([a-f0-9]{32})
   -->$` (MULTILINE) applied ONLY to lines the locked pass did NOT claim — mirrors the
   locked→sweeper structure (`dispatcher.py:37-53` invariant prose). Extracts
   `(feature, image_id)` pairs into a family result set.
3. **Near-miss sweeper (GENERALIZED, strip-only):** existing chart-render near-miss
   pattern `:50-53` extends to the family
   (`^\s*<!--\s*ens-img:[a-z0-9-]+:[^>\n]{0,64}-->\s*$`) — STRIPS junk, NEVER extracts.
   Junk HTML comments must not render literally on Discord/Telegram/Slack (original
   phase-b rationale).
   **LOAD-BEARING pass order `[AM13]`:** the generalized sweeper TEXTUALLY MATCHES
   well-formed family markers (32-hex ⊂ `[^>\n]{0,64}`) — safety exists ONLY because it
   runs LAST on lines the locked + family passes did not claim. The in-file invariant
   comment and the sweeper-last-on-residual test are the correctness proof, NOT
   optional documentation; if the order regresses, well-formed markers get
   stripped-and-lost silently.

**Pre-P2 leak window (bounded, accepted) `[AM13]`:** the current (pre-P2) sweeper is
chart-render-only, so between P1 and P2 a well-formed image-gen marker could leak as
literal comment text into chat — but ONLY if {env configured, agent invokes the tool,
chat routing active, pre-P2 window} co-occur. The natural gate is D4's fail-closed
default: `IMAGE_GEN_MODEL` unset ⇒ no markers can exist. No additional gating needed
(scheduling note: D10 — P2 lands before any e2e configures the knob).

**Provenance gate:** `feature ∈ {"chart-render", "image-gen"}` at the delivery gate
(currently `feature=="chart-render"` at `dispatcher.py:149-150`) — the tmp-image store
provenance feature tag is checked against an explicit ALLOWED_FEATURES frozenset;
unknown features are stripped-but-not-delivered (fail-closed for future features).

**Semantics reused verbatim (no redesign):**
- Extraction: dedupe + first-occurrence order (`extract_chart_images` behavior,
  `dispatcher.py:74-117`).
- Strip from chat-bound text: markers never reach end users as literal text.
- Delivery: `OutgoingMessage.images` + `delivered_image_ids` (`sources/base.py:83-123`
  — ZERO edits to base.py; it is pinned and needs none).
- Delete-after-delivery: blob deleted after successful delivery (`:388-408`/`:508-528`).
- API/no-colon sources skip extraction (`:302-304`) — markers survive for web consumers;
  family extraction must respect the same skip.
- Both seams: `dispatch_completed` (`:348`) and `dispatch_message` (`:472`) call the
  extraction; the family pass hooks the same two sites (the audit test pins ≥2 call
  sites — `tests/test_chart_image_delivery_audit.py:282-284`).

**Byte-compat proof strategy:** the locked chart-render regex, its pass order, and its
extraction semantics are untouched; the family pass is purely ADDITIVE over
non-chart-render lines. Proof: (a) the ENTIRE existing chart test corpus
(`tests/test_sources_dispatcher.py` chart suites + `tests/test_chart_image_delivery_e2e.py`
chart legs) runs UNMODIFIED and green; (b) new family unit tests clone the chart
truth-table structure; (c) a dedicated byte-compat test asserts identical outputs for
chart-render-only inputs before/after the change (golden fixtures).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `_FAMILY_MARKER_RE` + generalize `_NEAR_MISS_RE` in `dispatcher.py`; implement `extract_image_markers(text)` returning `{feature: [image_ids]}` built as locked-pass-first → family-pass-on-unclaimed-lines → sweeper-last | 1 (P1 marker format frozen) | Unit: locked pass claims chart lines; family pass claims image-gen lines; sweeper strips junk only; order invariant holds |
| 2 | Provenance gate: `ALLOWED_FEATURES = frozenset({"chart-render", "image-gen"})` at the delivery gate (`:149-150` area); unknown features stripped-not-delivered | 1 | Unit: image-gen feature delivers; hypothetical unknown feature stripped, never delivered |
| 3 | Wire family extraction into BOTH seams (`dispatch_completed` :348, `dispatch_message` :472) reusing delivery + delete-after-delivery paths; preserve API/no-colon skip (:302-304) | 2 | Integration harness: image-gen marker at both seams → `OutgoingMessage.images` populated; blob deleted post-delivery; API source skips extraction |
| 4 | New `TestExtractImageGenMarkersUnit` in `tests/test_sources_dispatcher.py` — truth-table clone of `TestExtractChartImagesUnit` (:1337 [verified in worktree]): extraction, dedupe, first-occurrence order, strip-from-chat-text, near-miss junk sweep, multi-marker lines, mixed chart+image-gen text | 1-3 | All truth-table cases pass |
| 5 | Byte-compat guard test: golden chart-render fixtures assert output identity (extracted ids, stripped text, delivery payloads) — run against pre-change fixtures captured at baseline | 1-3 | Chart outputs byte-identical pre/post |
| 6 | Extend both-seam reuse tests + audit test (≥2 call sites) to cover the family function (`tests/test_chart_image_delivery_audit.py:282-284` pin) | 3 | Audit test green with family call sites counted |
| 7 | SEAL RE-PIN for `daemon/sources/dispatcher.py` (baseline `50dd3211...` [verified in worktree :1266]): git diff audit (deltas EXACTLY commission edits), refresh SHA in `SEALED_SHA_BASELINES`, append history entry (:1226-1252 pattern), evidence pointer | 1-6 | Seal test green |

## Files Touched (complete list)

- `daemon/sources/dispatcher.py` — family regex, gate, seam wiring **+ SEAL RE-PIN** ⚠️
- `tests/test_sources_dispatcher.py` — `TestExtractImageGenMarkersUnit` + family seam
  tests (not pinned)
- `tests/test_chart_image_delivery_audit.py` — call-site pin extension (not pinned)
- `tests/test_chart_image_delivery_e2e.py` — history entry + SHA refresh (seal test
  itself; not self-pinned)
- Byte-compat fixtures (new test fixture files under `tests/` — not pinned)

**Explicitly NOT touched:** `daemon/sources/base.py` (pinned; zero edits needed —
`OutgoingMessage` already carries `images`/`delivered_image_ids`), all three adapters
(pinned; native upload already shipped 2026-10-04), `daemon/constants.py` (pinned).

## Coupling

- **Tight with Phase 1** — consumes the frozen marker format
  `'<!-- ens-img:image-gen:<32hex> -->'` (D2).
- **Tight with Phase 3** — FE regex must byte-match the family grammar; any grammar
  change here forces a P3 constants-module change.
- **Tight with Phase 4** — truth-tables + both-seam integration are P4 subjects.

## Risks (phase-specific)

- Regex widening accidentally captures chart-render lines in BOTH passes (double
  extraction). Mitigation: family pass runs ONLY on unclaimed lines (F12 order); unit
  test asserts chart ids extracted exactly once.
- Near-miss generalization over-strips: a malformed-but-real family marker could be
  stripped before extraction if passes reorder. Mitigation: order enforced in code +
  F12-style invariant comment + test asserting sweeper runs last on residual lines only.
- Unknown-feature markers (future features) silently delivered. Mitigation: explicit
  frozenset gate, fail-closed; test.
- Seal drift: dispatcher.py is pinned; unrelated edits in the same commit would poison
  the re-pin audit. Mitigation: dispatcher.py edits land as an isolated commit; diff
  audit at ceremony.

**Gate definition `[review-fold 3]`:** "existing tests pass UNMODIFIED" (DoD 1) means
ZERO edits to existing test functions/assertions; file-level ADDITIVE changes are
sanctioned (seal history/SHA refresh in the sealed test, new family tests appended).
Audit-regex arithmetic: def + 2 seams = **3** pinned call sites (the audit test counts
the definition site plus both seam call sites).

## Definition of Done (per-phase DoD)

1. ALL pre-existing chart dispatcher tests pass UNMODIFIED (zero assertion edits) —
   includes `TestExtractChartImagesUnit` and chart e2e legs.
2. `TestExtractImageGenMarkersUnit` green (truth-table clone).
3. Byte-compat guard test green (golden fixtures identical).
4. Both-seam integration tests green (family markers → native images at
   `dispatch_completed` AND `dispatch_message`; delete-after-delivery; API-skip honored).
5. Audit call-site test green.
6. `test_phase_d_does_not_modify_sealed_artifacts` green post dispatcher.py re-pin.

## Seal-Trip Subsection

**Pinned files touched by P2: exactly ONE — `daemon/sources/dispatcher.py`**
(baseline `50dd3211...` [verified in worktree at
`tests/test_chart_image_delivery_e2e.py:1266`]).

Ceremony: identical to P1's (diff audit → SHA refresh → history entry with date +
commission + exact delta description + evidence pointer) — but NO
`.prompt-artifacts.sha256` regen (dispatcher.py is not an agent prompt artifact).
History entry documents the family regex + gate + seam wiring as the sanctioned delta.

## Exit Criterion

Image-gen markers extract, deliver natively at both seams, and strip correctly — with
the entire chart corpus green untouched and the dispatcher seal re-pinned. P3 may start
(and P4's integration lane becomes writable).

## Reviewer implementation-time notes `[review-notes]`

- Make the sweeper-last-on-residual test (the AM13 load-bearing-ordering proof,
  architecture-recommendation.md §3) an explicit P2 task/acceptance item.
