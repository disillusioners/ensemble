# Decisions — row2-agent-turn-cpu, module 2C (tiktoken Tier B)

Branch: `sonic/row2-cpu/2c-tier-b` (worktree `/home/nea/ensemble-src-wt-row2-2c`,
base `213ea6c1e`). Merge order slot 2 (after 2A, before 2B).

## Emperor ruling (VERBATIM, binding)

> "Counting only for reference low drift allowed"

Provenance: Emperor Discord 2026-11-19, via sonic-leader commission to
sonic-developer (module brief `SONIC-MODULE-BRIEF row2-2c-tier-b`). Reading:
token counting in this system is REFERENCE-ONLY; drift up to the audited
bound (≤ 0.1 %) is acceptable; Tier B (O(n) chunked counting) APPROVED.

## What was built

`daemon/loader.py` `estimate_tokens` (def :1087 pre-change) gained a
policy-gated internal branch — every caller inherits it, zero caller edits:

- text ≤ `ENSEMBLE_TOKEN_COUNT_THRESHOLD` (default 16384 chars) → TODAY'S
  exact `len(encoder.encode(text))` — byte-identical for every caller
  (memoir repository + briefing, snapshot lanes, compaction, prompt cache).
- text above threshold → split at whitespace/newline boundaries into chunks
  ≤ `ENSEMBLE_TOKEN_COUNT_CHUNK` (default 4096 chars), boundary char carried
  INTO the next chunk (cl100k tokenizes `" wordB"` as one piece, so the
  boundary piece survives the cut), per-chunk counts summed. No-whitespace
  windows hard-split at exactly `chunk_size` — that hard split is what
  bounds the O(n²) single-piece encode to O(n).
- `ENSEMBLE_TOKEN_COUNT_EXACT=1` (truthy: 1/true/yes/on) → kill-switch, the
  exact path everywhere. Knobs read at CALL TIME via `os.environ`; garbage /
  non-positive values fall back to defaults; no config.py refactor.
- No new `await`; counting stays synchronous; sum of non-negative per-chunk
  counts is monotone non-negative by construction.

Why chunks are sized in CHARS, not bytes: tiktoken encodes str → UTF-8
bytes, and a byte-oriented split could cut a multibyte char in half and
change encode semantics; char splits are always safe. Worst case a 4096-char
chunk is ~12 KB of bytes (CJK), which only means a larger effective byte
chunk — cost stays O(n·C), drift stays bounded per boundary.

## Measured drift + timing (benchmark: `scripts/bench_tier_b_token_drift.py`)

Methodology = the deep-dive §2.1.4 / tester re-run method (same corpus
shapes, same sizes, exact reference via kill-switch, timed both paths).
Worktree `.venv` (uv sync), tiktoken 0.12.0, Python 3.14. Full table
(`tier_b_drift_results.json` shape; timings wall-clock, single run):

| shape | 4 KB | 16 KB | 64 KB | 200 KB |
|---|---|---|---|---|
| prose (representative) | 0 % | 0 % | 0 % | 0 % |
| dense JSON | 0 % | 0.034 % | 0.052 % | 0.041 % |
| base64-ish | 0 % | 0 % | 0 % | 0 % |
| letters (unbroken) | 0 % | 0 % | 0 % | 0 % |
| symbols (unbroken) | 0 % | 0 % | 0 % | 0 % |
| digits (unbroken) | 0 % | 0 % | 0.046 % | 0.048 % |
| CJK letters (unbroken) | 0 % | 0 % | 0 % | 0 % |
| **whitespace (unbroken)** | 0 % (≤thr) | 0 % (≤thr) | **3.32 %** | **3.14 %** |
| **newlines (unbroken)** | 0 % (≤thr) | 0 % (≤thr) | **1.51 %** | **1.54 %** |

(4 KB / 16 KB rows sit at or below the 16 KB threshold → exact path → 0 %
by construction. CJK is not in the commission pack set; measured here once.)

Timing (exact → chunked, default knobs):

| shape @ 200 KB | exact (today) | chunked (HEAD) | speedup |
|---|---|---|---|
| letters | 20.27 s | **0.33 s** | 61.8× |
| symbols | 26.19 s | **0.43 s** | 60.6× |
| whitespace | 26.61 s | **0.41 s** | 64.2× |
| newlines | 13.28 s | **0.42 s** | 32.0× |
| CJK | 135.38 s | **2.54 s** | 53.4× |
| prose | 0.020 s | 0.021 s | ~1× (unchanged) |

GATE D at HEAD: 200 KB unbroken letters through the default policy path =
**0.4136 s < 1 s** (exact today: 27.05 s per the tester re-run; C = 4 KB
model predicts ≈ 0.55 s — measured beats the model because boundary scans
are char-cheap). Normal payloads unchanged (prose 200 KB ≈ 20 ms).

## The audited bound vs measured reality (disclosed breach)

Gate (b)'s premise — drift ≤ 0.1 % INCLUDING pathological shapes — HOLDS for
every shape measured EXCEPT pure whitespace-class runs (spaces ~3.1-3.3 %,
newlines ~1.5 %, above threshold). Root cause: whitespace runs tokenize
~8-100× DENSER than prose (cl100k long whitespace-run merges: ~128
chars/token for spaces, ~32 for newlines — a 64 KB space run is only ~512
tokens), so the same ~1-2 extra tokens per boundary is a much larger RATIO.
The deep-dive's own arithmetic shows this: its "≤ n/C tokens ≈ 0.1 % @ 200
KB / 4 KB" assumed a ~50 K-token denominator (prose density ~4 chars/token).
The ABSOLUTE bound holds exactly as designed (+17 tokens over 16 boundaries
@ 64 KB spaces; +49 over 48 @ 200 KB; ≤ ~2 tokens/boundary for newlines),
and the direction is the SAFE one (over-count ⇒ budget gates compact
marginally early, never late). Decision: SHIP with the breach disclosed and
pinned by test (`test_whitespace_class_measured_bound_and_disclosure`) —
fixing it would need chunk ≈ 128 KB chars, which resurrects a ~17 s worst
case and defeats the fix. Reference-only counting per the Emperor ruling;
the whitespace-class caveat rides the disclosure, not a code change.

## Other recorded decisions

1. **Special-token edge (disclosed):** tiktoken's `encode()` raises
   `ValueError` on disallowed special tokens (e.g. `<|endoftext|>`). Below
   threshold this is byte-identical (still raises — pinned by test). Above
   threshold, a special token STRADDLING a chunk boundary is now counted
   instead of raising — a bounded behavior change in the reference-only
   lane, direction: more robust (today a special token in a big payload
   crashes the count).
2. **First-call encoder build cost unchanged:** `get_encoding` is registry-
   cached inside tiktoken (tester-verified, off-loop audit claim 12); the
   lazy per-call `import tiktoken` shape is preserved.
3. **Chunk split excludes the boundary char from the closed chunk** — the
   scan takes the LAST whitespace in the window (`range(end-1, start, -1)`),
   so consecutive-whitespace runs leave trailing whitespace inside the
   closed chunk; concat identity + per-chunk ≤ CHUNK are pinned by test.
4. **Test seam:** tests use `monkeypatch.setenv/delenv` on the three env
   vars with an autouse cleanup — no leakage into sibling test files in the
   same pytest process; the exact reference is taken DIRECTLY off the
   encoder (`tiktoken.get_encoding("cl100k_base")`), independent of any
   policy knob.
5. **PACKS.md registration row (reported, not edited — brief forbids
   editing `.agents/tester/PACKS.md` from this module):**
   `tier_b_token_drift_bound_unit_test.sh` — Tier B chunked-counting gate:
   36-test pytest file (gates a/c/e, knobs, consumer inheritance, 64 KB
   pathological drift) + full-size 4/16/64/200 KB drift-and-timing table
   over the commission shape set (gates b/d). Timeout 600 s (gate-b legs
   recompute exact 200 KB references by design). Run: `bash
   test/packs/tier_b_token_drift_bound_unit_test.sh` from repo root.

## Untested / risks

- Timing gate is wall-clock-bound: PASS requires < 1 s on 200 KB unbroken
  (measured 0.33-0.43 s, ~2.5-3× headroom); a heavily loaded runner could
  flake it — the strict gate lives in the pack, the unit test carries 2×
  headroom (< 2 s).
- The whitespace/newlines ratio breach (above) is the material risk to
  surface to the reviewer: it is a MEASURED property of cl100k on
  whitespace-only payloads, not an implementation defect.
- CJK 200 KB counts at 2.54 s (53× faster than exact, but above the 1 s
  gate-d figure) — 3 bytes/char × 4096-char chunks ≈ 12 KB byte chunks.
  Out of the commission shape set; disclosed for completeness.
