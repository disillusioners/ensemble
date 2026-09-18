# LCA Judge User-Intent — FINAL MERGE GATE (2026-09-18)

**Branch:** `feature/lca-judge-user-intent` @ `47b56df8` (base `a6442bff` = latest; delta = 2 commits: `1b343329` U-section + `47b56df8` review fixes; **8 files** net +1256/−26 — task context said "10 files", actual is 8, discrepancy is in the task spec)
**Worktree:** `agents-ensemble-wt-lca-user-intent` (main repo occupied — untouched throughout)
**Feature:** fused judge bundle gains SOURCE U (user's original mission request, ≤2000 chars id-redacted, U-first; anchor = delegation-scanner `last_real_user_index`, re-checked via `is_real_user_message`, fail-closed to omission) + judge-prompt intent-fulfillment instruction + U-absent guard + A/C subordination; total cap 12000→14000 additive; witnesses `FusedBundle.u_chars` / `user_message_included` + eval-row `bundle_u_chars=` / `user_message_included=`.
**Incident class closed:** 4dfded83 — user asked "what is current service tool description that agents will see?", leader answered fully (3029 chars) but conversationally; intent-blind judge nudged anyway.

## VERDICT: ✅ PASS — SHIP

14 worker instances (1 discovery + 9 first wave + 4 lane re-split + 1 reuse), all `uv run python -m pytest` only, dual-layer timeouts (outer `timeout 300` + script-internal 180–280s) on every pack, READ-ONLY on production (0 production code changes, 0 prod writes, 0 commits until the single lane commit).

---

## 1. Full attestation matrix @ 47b56df8 — PASS (1194 collected / 1184 passed / 1 EXPECTED-FOREIGN)

Glob-enumerated ground truth (74 test files: `grep -rln -i attestation tests/` + touched-module grep + known-files union), partitioned into 7 packs:

| Pack | Files | Tests | Result | Runtime |
|---|---|---|---|---|
| `lcau_matrix_core_unit_test.sh` | 40 (unit 32 + tools 5 + migration 1 + probe 2) | 950 coll → 949 P / **1 F (foreign)** | PASS* | ~12s |
| `lcau_matrix_intf_f_integration_test.sh` | 20 fast | 156 coll → 145 P / 2 skip / 9 desel | PASS | 9s |
| `lcau_matrix_intf_m_integration_test.sh` | 5 real-LLM mid (per-test 120) | 13 P | PASS | 130s |
| `lcau_matrix_intf_s1_integration_test.sh` | idle_orphan_incident (per-test 240) | 3 P | PASS | 121s |
| `lcau_matrix_intf_s2_integration_test.sh` | revive_after_escalation + bound_escalation | 2 P | PASS | 130s |
| `lcau_matrix_intf_s3_integration_test.sh` | incident_acceptance + ledger_reset + mode_tri_state | 6 P | PASS | 147s |
| `lcau_matrix_ints_integration_test.sh` | in_graph_nudge_flow + live_descendants (per-test 150) | 43 P | PASS | 165s |
| `lca2_pg_attestation_integration_test.sh` (existing, reused) | live_descendants_pg_lca @ disposable PG14 :15433 | 21 P | PASS | 6.7s |

**Totals: 1194 collected / 1185 executed / 1184 passed / 1 failed / 2 skipped (live-OpenCode-gated, by design) / 9 auto-deselected (marker-gated).** Exceeds the "~1181+" expectation; identical file universe to discovery's ground truth.

\* The single red is **EXPECTED-FOREIGN, base-identical** (not branch-caused):
`tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default` — offender migration `20260915_120000_critical_notes_lifecycle.sql` (critical-notes lineage). Reproduced identically at base `a6442bff` in a throwaway worktree by the neutrality worker. Known open foreign defect (prior gates: 2026-09-16 Stage-2 flip gate, same red). **Action:** needs its own fix by the critical-notes lineage owner; not a blocker for this branch.

All 25 feature tests in `tests/unit/test_attestation_resolver_user_intent.py` green (independently verified 25 passed in 0.17s).

**Lane re-split incident (documented in LESSONS):** the integration lane was initially mis-split as fast(31)/slow(2); the 31-file pack hit pyproject's per-test `timeout=30` on real-judge-LLM files (6 files at 41–113 s, 5 more at 16–33 s). First offender verified passing standalone (1 passed in 51s @ timeout=120) and base-identical → zero feature-regression signal. Re-split into 5 balanced packs per the repo's own `lca2_matrix_10..15` slow-pack precedent (`--override-ini timeout=120|240`); all green.

## 2. Incident E2E (independently constructed) — PASS 2/2

`tests/integration/test_lcau_incident_e2e.py` (dev's feature tests never read — spec-driven construction; harness mechanics from flagship/acceptance/conftest).

- **Scenario A — ALLOW-path (4dfded83 shape):** real user question (own wording), send_message delegation evidence, quiet tree, un-attested, 300+-word conversational answer (shape-pinned non-report). Judge stubbed COMPLETE → **ALLOW**: SOURCE U section carries the question; eval-row witnesses `user_message_included=True` / `bundle_u_chars=195`; `judge_verdict=complete` / `resolver_outcome=allow` via fused-judge rescue; graph ENDED on the conversational answer; `attestation_nudge_denied_count=0`; **zero nudges injected; zero denial-ledger writes; judge invoked exactly once.**
- **Scenario B — DENY-path counterpart:** same setup, formal report-shaped final that never addresses the ask → judge stubbed NOT_COMPLETE → **deny + nudge engaged** (exactly 1 nudge, exact kwargs `{attestation_nudge, injected_message, attestation_nudge_denied_count: 1}`, deny→post-attest-allow sequence); U still present in the scored bundle; judge invoked exactly once.

Pack `lcau_incident_e2e_integration_test.sh`: green twice consecutively.

## 3. Live-LLM probe (decisive) — PASS 3/3, 0 unparsable

Real transcript reconstructed from prod PG (strictly read-only: `SET default_transaction_read_only=on`, SELECT-only; checkpoint_blobs channel=messages @ step 297, 81,958-byte blob, decoded via msgpack): user question verbatim (62 chars) + leader's actual 3029-char answer verbatim. Bundles assembled via the real `assemble_fused_bundle` @ HEAD; real `judge_fused_bundle_async` (model `quick`), ONE invocation each (attempt=1; built-in unparsable retry never fired):

| Bundle | Witnesses | Verdict | Latency |
|---|---|---|---|
| (i) actual 4dfded83 transcript | u=True, u_chars=126 | **complete** — incident class DIES | 11.5–15.6s |
| (ii) intent-mismatch (formal non-answer) | u=True, u_chars=126 | **not_complete** (rationale cites SOURCE U explicitly) | 17.4–24.2s |
| (iii) U-absent (anchor absent) | u=False, u_chars=0 | complete (sane A/B/C-only per U-absent guard) | 12.0–37.3s |

Verdicts stable across 3 sequential runs. Artifacts: `RESULTS/2026-09-18-lca-user-intent-live-probe.py` + `.out.json`.

## 4. Anchor security — PASS 32/32

`tests/unit/test_lcau_anchor_security.py`: nudge injection (production kwargs shape incl. kwargs-shim-dropped variant), `[SYSTEM CONTEXT:` framing (absent from entire bundle), internal-report sources (all four reserved namespaces, incl. flag-dropped variants), positive anchoring (U byte-equals LAST `is_real_user_message` message; exactly one U section, first in emission order), fail-closed rungs (post-scan captured-index-drift → re-check rejects, **no fallback**; no-real-user → omission). `is_real_user_message` exclusion ladder pinned (12 parametrized + sanity). No production defects found.

## 5. Caps/redaction — PASS 12/12

`tests/unit/test_lcau_caps_redaction.py`: U clips to exactly 2000 (`text[:1999]+"…"`); UUID/instance-id redaction verified absent bundle-wide (`<redacted-user-N>` placeholders); **total ≤14000 with natural max 14034 → exactly 14000**; **"+34 header tolerance" decomposed = the 34-char truncation suffix** `"…\n[bundle truncated at total cap]\n"` (1+1+31+1); A/B/C byte-identical with-vs-without U; legacy no-U shape renders natural 12033 with **no new clipping**; witnesses + eval-row fields pinned both polarities.

## 6. PG lane + boot smoke — PASS both

- **PG lane:** 21/21 via existing `lca2_pg_attestation_integration_test.sh` (disposable PG14 :15433; prod-valued `POSTGRES_*` scrubbed by the pack).
- **Boot smoke** (`lcau_boot_smoke_test.sh`, 35/35, 13.5s): enforce-default boot (all five `ENSEMBLE_LEADER_ATTESTATION_*` unset → `mode=enforce attestation_enabled=true`), 0 Traceback/CRITICAL/ValueError; scripted U-present completion through the REAL fused path — eval row: `bundle_u_chars=236 user_message_included=True judge_verdict=complete resolver_outcome=allow`; deny→nudge→complete→allow sequence, leader reached `completed`, no nudge loop; clean SIGTERM shutdown, ports 18079/15780/15433/15434 freed (lsof-verified), 0 orphan PIDs. (Worker disclosed + fixed its own script-side PID-capture defect; production untouched.)

## 7. Neutrality vs base a6442bff — NEUTRAL

Full artifact: `RESULTS/2026-09-18-lca-user-intent-neutrality-audit.md`.

- **U-absent judge input byte-identical** across 4 shapes (maxed-ABC 8626c, tiny 547c, deny-band 3387c, not-evaluated 317c) — sha256 + `cmp`-verified; the 12000→14000 raise is unreachable U-absent (max expressible ≈8.6k).
- **Prompt delta exhaustively enumerated — 5 items, all U-class:** "three"→"four sections"; SOURCE U listing; INTENT FULFILLMENT sentence; U-absent guard sentence; A/C subordination sentence. All other sentences byte-identical. (Task's "two prompt sentences" was shorthand; the audit enumerates all five additions.)
- **Hunk classification:** 18+4+1+3 hunks — all U-scoped (U builder, witnesses, gate pass-through, prompt, `BUNDLE_U_SECTION_MAX=2000`, `BUNDLE_TOTAL_MAX` 12000→14000, docs). Violation-token sweep (deny_bound / markers / SHORT_REPORT_WORD_THRESHOLD / A,B,C caps / budget / call-site count / `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048`) — **zero matches**; call-site count = 1 (graph.py:5311) both sides.
- **Foreign migration red reproduced identically at base** → matrix red proven base-identical.

## 8. No production code changes — VERIFIED

`git diff 47b56df8 -- daemon/ frontend/ docs/` EMPTY (final-audit worker proof below); all lane output = 11 new `lcau_*` pack scripts + 3 new independent test files + `.agents/tester/` artifacts. Single lane commit at the end (no mid-flight commits; shared-worktree discipline).

### Scope Decision
Full attestation/LCA family (74 files, 1194 tests) + PG lane — warranted: merge gate on a feature touching 3 attestation production modules; the family IS the blast radius. Not run: non-attestation repo suites (irrelevant to this delta — neutrality audit proves byte-equivalence outside U).

### Gaps / Notes
- 🟠 Foreign migration red (`20260915_120000_critical_notes_lifecycle.sql` boolean-int default) remains OPEN — foreign lineage, needs its own fix; not branch-caused (base-identical).
- 🟢 Task-context metadata drift: "10 files" → actual 8; "~1181+" → actual 1194/1185; "+34 header tolerance" → decomposed as the truncation suffix.
- 🟢 Integration lane re-split mid-gate (5 packs) — see `LESSONS/2026-09-18-lcau-integration-lane-split.md`.
- 🟢 9 marker-deselected + 2 OpenCode-gated tests not executed by design (gated classes; live-LLM behavior covered independently by the probe).

### Overall Status
- Matrix: ✅ PASS (1 foreign red, base-identical)
- Incident E2E: ✅ PASS · Anchor security: ✅ PASS · Caps/redaction: ✅ PASS
- Live-LLM probe: ✅ PASS 3/3 · PG lane + boot smoke: ✅ PASS · Neutrality: ✅ NEUTRAL
- **Testing Complete: ✅ READY — SHIP**
