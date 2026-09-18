# LCA User-Intent — NEUTRALITY AUDIT vs base a6442bff (merge gate)

- **Date:** 2026-09-18
- **Lane:** LCA user-intent merge gate — neutrality audit
- **HEAD audited:** `47b56df8` (`feature/lca-judge-user-intent`, worktree `agents-ensemble-wt-lca-user-intent`)
- **Base:** `a6442bff` (prod v0.13.3) — throwaway worktree `/tmp/ens-wt-lcau-base-a6442bff` (detached, removed post-audit)
- **Auditor:** Worker (tester lane) — read-only on the feature worktree; base proofs in /tmp only
- **Method:** real-module capture (`uv run --frozen python /tmp/lcau_capture.py <wt> …` in EACH worktree) — imports the real `assemble_fused_bundle` + `FUSED_JUDGE_SYSTEM_PROMPT`, stubs the single LLM seam `attestation_report_judge._invoke_judge_llm` (module-global attribute assignment; the judge resolves it at call time), and records the exact `(system_prompt, user_payload)` `judge_fused_bundle_async` would send. Identical script + identical fixed corpus in both worktrees; `PYTHONDONTWRITEBYTECODE=1` on the feature worktree.

## VERDICT: ✅ NEUTRAL

U-absent judge payloads are byte-identical base↔HEAD on all 4 shapes; the only behavioral deltas are U-scoped and exactly the expected classes; the matrix's one red is base-identical (foreign critical-notes migration).

---

## 1. U-absent byte-equivalence — hash table

Corpus (all U-absent: HEAD called without `user_intent_message`, base has no such kwarg; HEAD witnesses `u_chars=0` / `user_message_included=false` on every shape):

1. `shape1_maxed_abc` — max expressible A+B+C: 8 evidence notes (A clips at **exactly 3000**), 3×1500-char leader messages (B=4725, structural max), 14 tree rows (C renders 10, C=868). Total **8626** — note: U-absent max expressible ≈ 8.6k < 12000, so the 12000→14000 total-cap raise is **unreachable U-absent** (inert); the A-cap clip path was exercised and identical.
2. `shape2_tiny` — minimal live signals, one short AIMessage (547 chars).
3. `shape3_deny_band` — `user_answer_pending=true`, busy/pending descendants, marker+length triggers, promise notes (3387 chars).
4. `shape4_not_evaluated` — all sources None/empty ("not evaluated" branches, 317 chars).

| Shape | user payload (bundle) chars | bundle sha256 base | bundle sha256 HEAD | byte-identical |
|---|---|---|---|---|
| shape1_maxed_abc | 8626 | `44a4a2e24637b4754fef9a9cd634616cd9bf32e1be7a2abc72afbb6175628d9c` | `44a4a2e2…d9c` (same) | ✅ (also `cmp`-verified) |
| shape2_tiny | 547 | `6e6219b6e51e6f23df7600b878dc8d671dcf79cbbc60f1796834dcb4bc1fad81` | `6e6219b6…d81` (same) | ✅ (cmp-verified) |
| shape3_deny_band | 3387 | `ef8fbab708dfa59242c6c9505adadb11f95bfe7c3d812e90812042641081b98d` | `ef8fbab7…98d` (same) | ✅ (cmp-verified) |
| shape4_not_evaluated | 317 | `48e39927e3aba8f2499831408270ae4b889ce7ab59acc5ee6eec3630e0adf2a0` | `48e39927…2a0` (same) | ✅ (cmp-verified) |

Component hashes:

| Component | base | HEAD |
|---|---|---|
| system_prompt | `e371c9690bd174515c36fbf40e989c485894cddb43558c272d7642b9ac239e1e` (1093 ch) | `c65f25dc6ca9230c0a325378406e52880ad2dfa85a4f1014dc28babeb9025338` (1603 ch) — expected U-class delta, see §2 |
| full payload (system+NUL+sep+user) | shape1 `0bf91cc3…`, shape2 `8be76918…`, shape3 `5e6d7387…`, shape4 `aff1baf2…` | shape1 `0a66ef60…`, shape2 `cab2588c…`, shape3 `0315274a…`, shape4 `0818bd5e…` — differ ONLY via system prompt |

Behavior witnesses (both worktrees): judge `invoked=True`, verdict per stub, **exactly 1 LLM attempt per shape** (no spurious retry) — invocation-count behavior unchanged. Bundle passed VERBATIM as entire user payload (asserted in-script). Raw payloads: `/tmp/lcau_capture/{base,head}/*.txt` (+ `.json` records) — scratch, removed at cleanup; hashes above are the durable record.

## 2. Prompt delta — FUSED_JUDGE_SYSTEM_PROMPT (base 1093 → HEAD 1603 chars, +510)

Every added/changed sentence, verbatim:

1. **CHANGED (one word):** "You will receive a fused evidence bundle with **three** sections: " → "…with **four** sections: "
2. **ADDED (enumeration, U first):** "SOURCE U (the user's original request for this mission), "
3. **ADDED (intent-fulfillment instruction):** "INTENT FULFILLMENT (SOURCE U): a message that genuinely ANSWERS or FULFILLS the user's request IS a completion report regardless of its formality, formatting, or shape; a formal-looking report that does NOT address the user's request is NOT complete. "
4. **ADDED (U-absent guard):** "If SOURCE U is absent, judge on A/B/C alone - do not infer the user's request. "
5. **ADDED (A/C subordination):** "SOURCE A advisories and SOURCE C live/pending descendants still indicate NOT_COMPLETE even when SOURCE U appears fulfilled. "

All other sentences (CONSERVATIVE, judge-only-on-evidence, JSON shape, no-markdown) byte-identical. Exactly the expected "U enumeration + intent-fulfillment + guard/subordination" class; no deny/marker/conservatism semantics altered.

## 3. Behavior-delta enumeration — `git diff a6442bff..47b56df8` hunk classification

Full delta = 8 files: 3 daemon (below), docs/setup.md, tests/probe, new test unit (+1029), 2 .agents planning docs (decisions.md +14, requirements.md +12).

### daemon/services/attestation_gate.py (+16, 1 hunk) — ✅ expected class
| Hunk | Classification |
|---|---|
| @@ -1295,6 +1295,22 | Pass-through only: computes `user_intent_message=messages[delegation_scan.last_real_user_index]` (bounds-checked, `last_real_user_found`-gated, else `None`) and forwards to `evaluate_resolver_activation`. No deny_bound, no marker scan, no band logic touched. |

### daemon/services/attestation_report_judge.py (+30/−14, 4 hunks) — ✅ expected class
| Hunk | Classification |
|---|---|
| @@ -60 (docstring) | Comment-only: input cap mention 12000→14000 + U ≤2000 |
| @@ -456 (block comment) | Comment-only: cap arithmetic mention + U ≤2000, total ≤14000 |
| @@ -484 (prompt comment + constant) | **The judge prompt delta** (§2 above) + its docstring comment |
| @@ -673 (docstring) | Comment-only: bundle_text cap mention ≤14000 |

Verified invariants: `FUSED_JUDGE_MAX_OUTPUT_CHARS: int = 2048` unchanged (grep both worktrees, line 125); `_attempt_once`/retry body untouched; **call-site count = 1** in both (`daemon/graph.py:5311` — grep across `daemon/`); `_invoke_judge_llm` body untouched (only referenced by the capture stub).

### daemon/services/attestation_resolver_activation.py (+136/−26, 18 hunks) — ✅ expected classes
| Hunk | Classification |
|---|---|
| @@ -42, @@ -57 | Module-docstring mentions of section U / witness fields (docs-only) |
| @@ -79 | Import `is_real_user_message` from attestation_scanner (U's fail-closed real-user re-check — expected) |
| @@ -95 | `__all__` += `BUNDLE_U_SECTION_MAX` (expected) |
| @@ -154 | Caps: **+`BUNDLE_U_SECTION_MAX: int = 2000`** and **`BUNDLE_TOTAL_MAX: int = 12000` → `14000`** (EXPECTED total-cap raise, additive for U). A/B/C constants (3000/6000/3000) UNTOUCHED |
| @@ -272, @@ -281 + fields | `FusedBundle` docstring cap mention; **+`u_chars`/`user_message_included` witness fields (default 0/False — additive, back-compatible)** (expected) |
| @@ -788 | **+`_USER_INTENT_SLOT_HINT` + `_build_u_section`** — U-section rendering, fail-closed to `None` on absent/non-user message (expected) |
| @@ -795, @@ -812, @@ -834 | `assemble_fused_bundle`: +`user_intent_message=None` kwarg; emission order U→A→B→C with U **omitted entirely when absent** (expected; U-absent byte-identity PROVEN by §1); witnesses stamped |
| @@ -928, @@ -951, @@ -977 | `evaluate_resolver_activation`: +`user_intent_message` pass-through kwarg + docstring + forwarding into bundle assembly (expected) |
| @@ -1026, @@ -1042, @@ -1077 | `emit_resolver_eval_row`: docstring; **log format + `bundle_u_chars=%s user_message_included=%s `** + the two args (expected witness fields; additive key=value tail — grep consumers field-name-anchored) |
| @@ -1109 | `log_shadow_fail_open`: comment-only — documents the fields' DELIBERATE absence on fail-open rows (no format change) |

### docs/setup.md (+6/−3, 3 hunks) — ✅ expected class (doc mention)
| Hunk | Classification |
|---|---|
| @@ -581 | Judge section: "A+B+C evidence bundle" → "U+A+B+C" + intent-fulfillment semantics description |
| @@ -591 | Bounds: input cap 12,000→14,000 with per-section 3000/6000/3000/**2000 (U additive)** + U-omission/`user_message_included=false` semantics; `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048` unchanged |
| @@ -1218 | Eval-row field list + `bundle_u_chars`/`user_message_included` |

### tests (not behavior) — ✅
- `tests/unit/test_attestation_resolver_user_intent.py` (+1029, new): U pin suite.
- `tests/probe/lca2_live_fused_judge_probe.py` (+39/−8): probe bundle authors gain U sections + comment cap/order updates — test-only.

### VIOLATION-TOKEN SWEEP
Regex sweep over ALL `^[+-]` lines of the full `daemon/` delta for: `deny_bound`, `CHILD_TERMINAL_PROMISE_MARKERS`, `MID_WORK_MARKERS`, `SHORT_REPORT_WORD_THRESHOLD`, `FUSED_JUDGE_MAX_OUTPUT_CHARS`, `BUNDLE_A/B/C_SECTION_MAX: int`, `_invoke_judge_llm`, `judge_fused_bundle_async(` → **ZERO matches**. No hunk touches: deny bound/band logic, marker lists, short-report threshold, A/B/C caps, judge budget/invocation count, output cap, or the single call site.

## 4. Foreign-red base-check (BASE worktree @ a6442bff)

`timeout 120 uv run --frozen python -m pytest "tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default" -q --tb=short -p no:cacheprovider`

**FAILED — base-identical** (pytest 0.48s), offender exactly the foreign critical-notes migration:

```
E   AssertionError: BOOLEAN column(s) with int-literal DEFAULT 0 found in shipped migrations: ["20260915_120000_critical_notes_lifecycle.sql: 'BOOLEAN NOT NULL DEFAULT 0'"]. PostgreSQL rejects ``DEFAULT 0`` on BOOLEAN (psycopg.errors.DatatypeMismatch); use ``DEFAULT FALSE``. Legacy already-applied migrations are added to _LEGACY_BOOLEAN_DEFAULT_0_ALLOWLIST only with justification — NEVER edited (checksum ledger). Ref: LESSONS/2026-09-06-lca-pg-boolean-default-defect.md
E   assert not ["20260915_120000_critical_notes_lifecycle.sql: 'BOOLEAN NOT NULL DEFAULT 0'"]
FAILED tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default
1 failed in 0.48s
```

→ The matrix's one red is pre-existing at base (known foreign defect, critical-notes lineage 68182287), **not branch-caused**.

## 5. Runtimes

| Step | Wall |
|---|---|
| Base worktree create + `uv sync --frozen` | ~60s |
| Base capture (4 shapes) | 3s |
| HEAD capture (4 shapes) | 2s |
| Base-red pytest | 5s wall (0.48s pytest) |
| Cleanup | see §6 |

## 6. Cleanup performed

`git worktree remove --force /tmp/ens-wt-lcau-base-a6442bff` + `/tmp/lcau_capture*` scratch removed after audit. Feature worktree untouched except this lane artifact (`.agents/tester/RESULTS/`, untracked until lane commit); `PYTHONDONTWRITEBYTECODE=1` prevented pycache writes there.

## 7. Audit limitations (disclosed)

- Byte-equivalence proven over the expressible U-absent surface: 4 fixed shapes covering cap-binding (A clip at exactly 3000), tiny, deny-band, and not-evaluated assembly paths. U-absent max expressible ≈ 8.6k, so the total-cap-clip branch is unreachable U-absent in BOTH versions (unchanged code path, but not exercised by any input — structurally inert).
- Full payload (system+user) hashes differ by construction — the system prompt legitimately changes (§2); the neutrality claim binds the USER payload (bundle), which is byte-identical.
- Judge invocation count verified under the stub seam (1 attempt/shape, both versions); the real-HTTP path is out of scope for a neutrality audit (no LLM calls made).
