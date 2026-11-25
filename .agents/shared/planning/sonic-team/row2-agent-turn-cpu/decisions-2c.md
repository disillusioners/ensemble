# Decisions — row2-agent-turn-cpu, module 2C (tiktoken Tier B)

Branch: `sonic/row2-cpu/2c-tier-b` (worktree `/home/nea/ensemble-src-wt-row2-2c`,
base `213ea6c1e`). Merge order slot 2 (after 2A, before 2B).
Fix pass: `sonic/row2-cpu/fix2c-shape-aware-cut` (worktree
`/home/nea/ensemble-src-wt-row2-fix2c`, base `2f6913a39`).

## Emperor ruling (VERBATIM, binding)

> "Counting only for reference low drift allowed"

Provenance: Emperor Discord 2026-11-19, via sonic-leader commission to
sonic-developer (module brief `SONIC-MODULE-BRIEF row2-2c-tier-b`). Reading:
token counting in this system is REFERENCE-ONLY; drift up to the audited
bound (≤ 0.1 %) is acceptable; Tier B (O(n) chunked counting) APPROVED.

## Front-door ruling (VERBATIM, binding — fix pass)

> "ADOPT the shape-aware cut as the 2C implementation — it preserves the
> Emperor's standing ruling (2026-11-19, verbatim: 'Counting only for
> reference low drift allowed') with ZERO need to widen the bound, since even
> the synthetic worst cases land at 0.000%. Keep the opt-out back to exact
> counting (ENSEMBLE_TOKEN_COUNT_EXACT=1) as already scoped. Ship the
> drift-bound proof pack against the SHAPE-AWARE behavior and report the
> before/after drift table per shape (synthetic + realistic + base64 + JSON +
> minified), so the record shows WHY no ruling extension was required."

Provenance: Ari front-door ruling, fix pass commission to sonic-coder
(2C shape-aware cut). The implementation shape (option d) and the acceptance
targets were measured by sonic-leader against the SHIPPED chunker
(tiktoken 0.12.0, 200 KB corpora, 4 KiB chunks, 16 KiB threshold) and are
binding as given.

## What was built

`daemon/loader.py` `estimate_tokens` (def :1087 pre-change) carries a
policy-gated internal branch — every caller inherits it, zero caller edits:

- text ≤ `ENSEMBLE_TOKEN_COUNT_THRESHOLD` (default 16384 chars) → TODAY'S
  exact `len(encoder.encode(text))` — byte-identical for every caller
  (memoir repository + briefing, snapshot lanes, compaction, prompt cache).
- text above threshold → split at whitespace/newline boundaries into chunks
  ≤ `ENSEMBLE_TOKEN_COUNT_CHUNK` (default 4096 chars) and summed per-chunk
  counts. O(n) instead of O(n²) on unbounded single-piece runs.
- `ENSEMBLE_TOKEN_COUNT_EXACT=1` (truthy: 1/true/yes/on) → kill-switch, the
  exact path everywhere. Knobs read at CALL TIME via `os.environ`; garbage /
  non-positive values fall back to defaults; no config.py refactor.
- No new `await`; counting stays synchronous; the sum of non-negative
  per-chunk counts is monotone non-negative by construction.

### The shape-aware cut (fix pass, option d)

The plain rule splits at the LAST whitespace/newline boundary inside the
window and carries the boundary char INTO THE NEXT CHUNK (`"wordA wordB"` →
`"wordA"` | `" wordB"`), which preserves the cl100k leading-space word piece.

**Exception (binding shape):** when the boundary char TERMINATES a whitespace
run of ≥ 16 IDENTICAL chars, the cut is taken AFTER that char (the run's last
char stays in the closed chunk). Mechanism: cl100k merges up to 32
consecutive LFs into ONE token and up to 128 consecutive spaces into one; the
plain carry-into-next-chunk rule drops one char from the window (4095 instead
of 4096) and misaligns EVERY subsequent boundary against that merge grid —
measured ~2 extra tokens per boundary. Keeping the run's last char in the
chunk preserves the grid (4096 = 128×32 = 32×128) and the run then encodes
identically to the exact path. One bounded back-scan per cut; O(n) unchanged.
Short runs (< 16 identical chars) keep the plain rule — that is what keeps
prose / JSON / base64 at 0.000%.

The NEWLINE-ALIGNED CUT variant was measured and REJECTED per the fix
commission: it regresses prose 0.000% → 0.094% and dense-JSON → 0.031% by
dropping the leading-space word-piece shape. Not implemented.

### Docstring changes (summary)

- `_iter_count_chunks`: the "carried INTO THE NEXT CHUNK" rationale was
  incomplete — now documents the ≥ 16-identical-whitespace-run exception, the
  merge-token-alignment mechanism (32 LFs / 128 spaces per token), the
  measured consequence of the plain rule (~2 tokens per boundary), and that
  the back-scan is bounded (O(n) preserved).
- `estimate_tokens`: the bound claim was corrected — the over-count is now
  ≤ ~1 token per boundary, ≤ n/CHUNK tokens total; the old
  "3-tokens-per-boundary" wording is GONE, and the shape-aware rule is named.
- Module header comment: same bound correction + one line naming the
  shape-aware rule.

## CORRECTED narrative — the breach was a SYNTHETIC-CORPUS ARTIFACT

**The prior version of this document (2C lane) claimed the ABSOLUTE bound was
"breached". That claim was measured against the PRE-FIX chunker (the plain
carry-into-next-chunk cut), and the lane-local pin `boundaries * 3 + 2` used
to police it was NOT deep-dive-sanctioned — it MASKED the breach. Both are
corrected here.** Sonic-reviewer's F1 confirmed the mask: a 200 KB pure-LF
corpus gave exact 6250 → chunked 6348 = +98 tokens over 48 boundaries
(2.04/boundary) versus the deep-dive's n/C = 48.8.

**Realistic corpora ALREADY MET the ≤ 0.1 % bound before the fix** — the
breach only appeared on synthetic single-class whitespace runs. Measured
(sonic-leader's acceptance set): LF-code 0.025 %, CRLF-code 0.020 %,
lone-CR-code 0.019 %, markdown 0.007 %, prose 0.000 %, dense-JSON 0.000 %,
base64 0.019 %, digits 0.048 %. Independently re-measured on this lane's
corpora (200 KB, default knobs): LF-code 0.0015 %, CRLF-code 0.0015 %,
lone-CR-code 0.0014 %, markdown 0.0154 %, prose 0.0000 %, minified 0.0000 %,
dense-JSON 0.0407 %, base64 0.0000 %, digits 0.0480 % — all ≤ 0.1 %.

**The shape-aware cut closed the rest**: LF-run 1.568 % → 0.000 %, spaces-run
3.135 % → 0.000 %. **NO RULING EXTENSION REQUESTED** — the Emperor's
"Counting only for reference low drift allowed" stands as written, with the
audited bound UNCHANGED at ≤ 0.1 %.

### Before / after drift table (identical corpora, default knobs, 4 KiB chunks,
16 KiB threshold; BEFORE = shipped plain cut, AFTER = shape-aware cut)

| shape | size | exact | BEFORE chunk | BEFORE drift | BEFORE over | AFTER chunk | AFTER drift | AFTER over |
|---|---|---|---|---|---|---|---|---|
| lf_run (SYNTH) | 64 KB | 2048 | 2081 | **1.6113 %** | 33 | 2048 | **0.0000 %** | 0 |
| lf_run (SYNTH) | 200 KB | 6250 | 6348 | **1.5680 %** | 98 | 6250 | **0.0000 %** | 0 |
| spaces_run (SYNTH) | 64 KB | 512 | 529 | **3.3203 %** | 17 | 512 | **0.0000 %** | 0 |
| spaces_run (SYNTH) | 200 KB | 1563 | 1612 | **3.1350 %** | 49 | 1563 | **0.0000 %** | 0 |
| letters_run (SYNTH) | 200 KB | 100000 | 100000 | 0.0000 % | 0 | 100000 | 0.0000 % | 0 |
| symbols_run (SYNTH) | 200 KB | 25000 | 25000 | 0.0000 % | 0 | 25000 | 0.0000 % | 0 |
| lf_code (REAL) | 200 KB | 68694 | 68695 | 0.0015 % | 1 | 68695 | 0.0015 % | 1 |
| crlf_code (REAL) | 200 KB | 67008 | 67009 | 0.0015 % | 1 | 67009 | 0.0015 % | 1 |
| lone_cr_code (REAL) | 200 KB | 73626 | 73627 | 0.0014 % | 1 | 73627 | 0.0014 % | 1 |
| markdown (REAL) | 200 KB | 65069 | 65079 | 0.0154 % | 10 | 65079 | 0.0154 % | 10 |
| prose (REAL) | 200 KB | 46512 | 46512 | 0.0000 % | 0 | 46512 | 0.0000 % | 0 |
| minified (REAL) | 200 KB | 97955 | 97955 | 0.0000 % | 0 | 97955 | 0.0000 % | 0 |
| dense_json (DENSE) | 200 KB | 71322 | 71351 | 0.0407 % | 29 | 71351 | 0.0407 % | 29 |
| base64 (DENSE) | 200 KB | 21875 | 21875 | 0.0000 % | 0 | 21875 | 0.0000 % | 0 |
| digits (DENSE) | 200 KB | 66667 | 66699 | 0.0480 % | 32 | 66699 | 0.0480 % | 32 |

No shape is worse after the fix; the 4 KB / 16 KB legs (below the threshold)
are byte-identical before/after (exact path untouched). The BEFORE run fails
the gate with 7 violations (lf_run ×4, spaces_run ×3); the AFTER run reports
ZERO violations. Gate D at HEAD: 200 KB unbroken letters = **0.3273 s**
(< 1 s; exact today 27.05 s).

## Pin replacement (the masking pin is gone)

`tests/unit/test_loader_chunked_counting.py`:
- `test_absolute_over_count_bound` — the DEEP-DIVE-SANCTIONED bound:
  over-count ≤ n/CHUNK tokens (≤ ~1 token per boundary) on synthetic AND
  realistic shapes. Replaces `boundaries * 3 + 2` (not sanctioned; masked the
  breach).
- `test_synthetic_shape_ceilings` — per-shape drift ceilings at the
  COMMISSION bound (≤ 0.1 %) for LF-run / spaces-run / letters-run /
  symbols-run; REDS if the cut rule regresses to the plain rule.
- `test_primitive_encodes_each_character_at_most_once` — STRUCTURAL pin
  (encoder spy): the chunked path encodes each character of the payload AT
  MOST ONCE; a whole-list re-encode inside the loop REDs it in ~2 s, without
  depending on a wall-clock timeout.
- `test_shape_aware_cut_keeps_long_whitespace_runs_whole` — the rule itself
  (long run stays whole; short run keeps the plain carry rule).

Mutation proof (both legs executed against the source, restored
byte-identically — sha256 verified):
- (ii) cut rule reverted to plain carry-into-next-chunk → RED on
  `test_synthetic_shape_ceilings[lf_run]`,
  `test_synthetic_shape_ceilings[spaces_run]`,
  `test_absolute_over_count_bound`,
  `test_shape_aware_cut_keeps_long_whitespace_runs_whole` (4 failed).
- (i) whole-list re-encode re-added inside the chunk loop → RED on the
  structural pin (1 failed in 1.78 s) + the suite times out (30 s
  per-test budget).

## Other recorded decisions

1. **Special-token edge (disclosed, unchanged by the fix):** tiktoken's
   `encode()` raises `ValueError` on disallowed special tokens (e.g.
   `<|endoftext|>`). Below threshold this is byte-identical (still raises —
   pinned by test). Above threshold, a special token STRADDLING a chunk
   boundary is counted instead of raising — a bounded behavior change in the
   reference-only lane, direction: more robust.
2. **First-call encoder build cost unchanged:** `get_encoding` is
   registry-cached inside tiktoken; the lazy per-call `import tiktoken`
   shape is preserved.
3. **Chunk split excludes the boundary char from the closed chunk** EXCEPT
   for the ≥ 16-identical-run exception; concat identity + per-chunk ≤ CHUNK
   are pinned by test, and the three sanctioned split forms (plain carry /
   shape-aware / hard split) are pinned by `test_chunker_invariants`.
4. **Chunks are sized in CHARS, not bytes:** a byte-oriented split could cut
   a multibyte char in half; char splits are always safe.
5. **Test seam:** `monkeypatch.setenv/delenv` on the three env vars with an
   autouse cleanup; the exact reference is taken DIRECTLY off the encoder,
   independent of any policy knob.
6. **Benchmark script is the gate:** `scripts/bench_tier_b_token_drift.py`
   exits non-zero on any drift > 0.1 %, any direction violation, any
   over-count > n/CHUNK, or gate-d ≥ 1 s — so the pack inherits a real gate
   rather than a printed table.
7. **PACKS.md registration row (reported, not edited — the brief forbids
   editing `.agents/tester/PACKS.md` from this module):**
   `tier_b_token_drift_bound_unit_test.sh` — Tier B chunked-counting gate
   (SHAPE-AWARE behavior): 38-test pytest file (gates a/c/e, knobs,
   consumer inheritance, structural no-re-encode pin, per-shape ceilings, the
   n/CHUNK absolute bound) + the full-size 4/16/64/200 KB drift-and-timing
   table over the commission shape set — SYNTHETIC (lf_run, spaces_run,
   letters_run, symbols_run), REALISTIC (lf_code, crlf_code, lone_cr_code,
   markdown, prose, minified), DENSE (dense_json, base64, digits) — asserting
   ≤ 0.1 % drift on ALL of them. Timeout 600 s. Run: `bash
   test/packs/tier_b_token_drift_bound_unit_test.sh` from repo root.

## Untested / risks

- Timing gate is wall-clock-bound: PASS requires < 1 s on 200 KB unbroken
  (measured 0.327 s solo; ~0.41 s under concurrent load) — ~3× headroom; the
  unit file carries a < 2 s guard so a loaded runner cannot flake the suite.
- The pre-fix "absolute bound breached" narrative was corrected, not
  deleted: the numbers it rested on were real but measured against the
  PRE-FIX chunker, and the lane pin that policed them was unsanctioned.
- CJK (not in the commission shape set) was measured once in the 2C lane:
  drift 0.000 %, chunked 2.54 s vs exact 135.4 s at 200 KB (3 bytes/char ×
  4096-char chunks ≈ 12 KB byte chunks).
- No live-daemon boot smoke (out of module scope); consumer lanes verified
  at the suite level (188 tests green).
