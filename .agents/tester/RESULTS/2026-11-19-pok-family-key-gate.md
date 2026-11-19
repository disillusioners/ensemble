# POK family-key correction (Phase 1 + Phase 2) — TESTER GATE

Date: 2026-11-19 (run 2026-11-18 22:45Z → 2026-11-19 00:12Z)
Branch: `fix/pok-family-key` (worktree `/home/nea/ensemble-src-wt-pok-family-key`)
**Tested HEAD: `6eee0fbaaa13a7a0ecc250d858c4eded9ff33f9c`** — stable for the entire run
(verified at dispatch, at every leg's start AND end, and at aggregation; `git status --porcelain` empty throughout; nothing committed or pushed; zero source edits outside sanctioned /tmp scratch trees).

Base: `2f1d78d7620590daaee057ea39feaf449071fd2e` (`merge: stranded compact-note gate canon landing`, = origin/latest).
NOTE: the commission's spelled-out full base sha `2f1d78d76942a81469c5d3a7f56372cdcf0aa558` **does not exist** in the repo (`git cat-file` fatal) — the 9-char prefix `2f1d78d76` was extended incorrectly by the dispatcher (me). The real base above shares that prefix.

Commits under test: `886e74e00` (census artifact, docs) · `1479fc9b3` (multi-token family key + refusal message) · `6eee0fbaa` (cure wording + test premises).

## Gate tier (mechanical, per ensure.md)

Tier **1** — scoped dual-side FAILED-list diff, 30-min budget. No core-seam path
(`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`,
`daemon/services/task_processor.py`, `daemon/services/job_queue_service.py`) is touched by
`git diff 2f1d78d76..HEAD`. Floor scoped to `tests/unit/services/` + `tests/unit/repositories/`
+ `tests/unit/test_pok_grant_pins.py` + `tests/postgres/test_pok_tables_pg.py`.

## VERDICT

### Overall: **PASS** — the daemon change is correct, mutation-proven, behaviorally verified on real PG, and regression-clean. 1 important in-scope DOC defect + 6 out-of-scope design-doc staleness + 1 important test-hardening gap are reported for follow-up; none is a daemon-behavior defect.

| # | Check | Verdict |
|---|---|---|
| 1 | Scoped POK pack (6 files) | **PASS** — 204/204, exit 0, 4.82s |
| 2 | Mock fidelity | **PASS** — real repo/real SQL on the whole family-gate path; 1 important coverage gap |
| 3 | Counter-mutation (pattern revert) | **PASS** — exactly the 3 predicted pins RED, restore byte-exact |
| 4 | Behavioral acceptance (real PG private schema) | **PASS** — 12/12 scenarios |
| 5 | Regression surface + base-vs-fix diff | **PASS** — deterministic net-new = 0 |
| 6 | Doc/pin greps | **PASS on code surfaces** — 1 in-scope + 6 out-of-scope stale docs |

---

## 1. Scoped POK pack — PASS

`timeout 300 .venv/bin/pytest -p no:randomly --tb=short -q -rfE` over the 6 files
(`test_pok_service_unit`, `test_pok_injection_unit`, `test_pok_metadata_cap_unit`,
`test_pok_trust_tier_unit`, `test_pok_ari_seam_unit`, `tests/unit/tools/test_pok_tools_unit`):

```
204 passed, 1 warning in 4.82s   EXIT=0
```

0 failed / 0 errors / 0 skipped / 0 xfailed. Baseline reconciliation exact:
199 + 5 new `def test_` lines = 204 (`git diff 2f1d78d76..HEAD -- tests/`: 5 added, 0 removed).
Per-file collected: service 62 · injection 42 · trust-tier 40 · tools 24 · metadata-cap 23 · ari-seam 13.

All 8 target pins present and PASS:
- `TestMultiTokenFamilyKey::test_multi_token_gate_refuses_fourth_member`
- `TestMultiTokenFamilyKey::test_family_key_regex_pins_direct`
- `TestMultiTokenFamilyKey::test_unnumbered_prefix_counting`
- `TestMultiTokenFamilyKey::test_unnumbered_prefix_pushes_over_cap`
- `TestMultiTokenFamilyKey::test_supersede_exemption_multi_token`
- `TestAc19TerminalityCured::test_family_full_refusal_names_the_cure`
- `TestAc18Ordering::test_tightened_regex_unordered_slugs_stay_unordered`
- (+ the whole pre-existing pack)

Evidence: `/tmp/pok-family-key-gate/w1/` (pack.txt, collect.txt, junit.xml, counter_mutation.txt, behavior_probe.txt).

## 2. Mock fidelity — PASS

`svc` fixture (`test_pok_service_unit.py:36-46`) = real `POKRepository` on real in-memory SQLite
(`SingletonThreadPool`; data persists across per-call `Session(engine)`). `POKService.__init__`
builds the real repo. The ONLY monkeypatch in the 1033-line file is `svc._repo.count_active`
in `TestAc19TerminalityCured` (the *active-cap* gate — different gate; never touches
`count_active_in_family`). The refusal runs end-to-end through real SQL.

`count_active_in_family` semantics (`repository.py:221-240`):
- `status == "active"` filter: **YES** (compiles `... AND status = ? AND (slug LIKE ? || '%')`)
- prefix match `startswith(f"{family}-")`: **YES** (`LIKE ? || '%'`, not a regex) — the documented DELIBERATE SUPERSET
- **DISPUTED rows COUNTED** (no `disputed` predicate; `curate` flips only the boolean at `repository.py:333`)
- **ARCHIVED rows excluded** (via the status filter)
- **On supersede the prior row is written `status = "superseded"`** (`repository.py:290-292`, `superseded_by_id` set,
  `pinned=False`, one commit with the successor insert) ⇒ count-preserving, as the message claims.

Supersede mechanism (adjudicates the mutation finding): supersede is a SEPARATE code path
(`supersede_and_insert`, `repository.py:274-298`) that never consults the family key; the family
gate lives on the NEW-slug admission path (`pok_service.py:470-492`). So
`test_supersede_exemption_multi_token` is a genuine pin of the exemption (it would go red if the
gate were extended to the supersede path) but is NON-discriminating for the regex — accurate framing,
not "vacuous".

**🟠 Gap (important): no service-level different-family control.** Repro (worker-executed):
change `pok_service.py:473` to `self._repo.count_active(project_id)` (project-wide instead of
family-scoped) ⇒ **the entire 204-test pack stays green** — refusal tests seed exactly 3 actives, so
a project-wide count also yields 3 ≥ 3; regex pins bypass the service; repo pins bypass the service;
supersede legs bypass the gate. The gate could be silently widened to project-wide with zero test
detection. One-line fix: after filling `prompt-assent` in one project, pin
`svc.upsert(project_id, slug="other-01-z", …).action == "created"` (current code already passes it).

**🟢 Gaps (nice-to-have):** disputed-row counting and archived-exclusion of the *family* count are
live-verified but unpinned.

Evidence: `/tmp/pok-family-key-gate/w2/`.

## 3. Counter-mutation — PASS

Scratch worktree `/tmp/pok-mut-scratch` @ the same HEAD; venv proved live
(`daemon.__file__` and `tests.__file__` both under `/tmp/pok-mut-scratch/`); mutation = exactly
1 line in `daemon/repositories/pok/models.py` (`--numstat` `1 1`), reverting
`POK_FAMILY_SLUG_PATTERN` to the old single-token regex; `git restore` afterwards; scratch removed.

| state | result | counts |
|---|---|---|
| GREEN | PASS | 204 passed, 0 failed (6.14s) |
| MUTATED | **FAIL** | **3 failed, 201 passed** (4.69s) |
| RESTORED | PASS | 204 passed, 0 failed (6.51s) |

Red set = exactly the 3 predicted candidates (no extra, no missing):
- `TestMultiTokenFamilyKey::test_multi_token_gate_refuses_fourth_member` — `Failed: DID NOT RAISE <class POKError>` (the substantive proof: family key collapses to None, gate skipped, 4th member silently admitted unbounded)
- `TestMultiTokenFamilyKey::test_family_key_regex_pins_direct` — `assert None == 'prompt-assent'`
- `TestAc18Ordering::test_tightened_regex_unordered_slugs_stay_unordered` — `assert None == 'model-catalogs'`

Pattern-insensitive survivors (by design): `test_unnumbered_prefix_counting`,
`test_unnumbered_prefix_pushes_over_cap`, `test_supersede_exemption_multi_token`,
`test_family_full_refusal_names_the_cure`.

Byte-exactness: sha256 before == after ==
`7a955ad5b3433bb9f67d9f591fd8949e566f7e8dd13a5afb3124ee1ed9157294`; scratch `git status --porcelain`
empty post-restore; the branch checkout's own `models.py` sha identical (mutation never leaked).

Evidence: `/tmp/pok-family-key-gate/w3/`.

## 4. Behavioral acceptance — PASS (12/12), real PG

Private schema `pok_gate_20261119` in `ensemble_dev_scratch`, `search_path`-fenced engine, only the
two POK tables created there, `POKService` + `POKRepository` real (zero mocks), ambient `POSTGRES_*`
scrubbed per-variable. Schema verified dropped after (`pg_namespace` absent; no residue in `public`).

| SCEN | EXPECTED | OBSERVED | RESULT |
|---|---|---|---|
| S1 multi-token 4th refuses | `pok_family_full`, names family/count/bound/members | refused; `details={active_count:3, family:'prompt-assent', members:[3 slugs]}` | PASS |
| S2 unnumbered prefix counts | `verify-03-d` refused | raw prefix count 3 (incl. `verify-something-unnumbered`); refused | PASS |
| S3a supersede in FULL family | succeeds, no refusal | `action='superseded'` | PASS |
| S3b count preserved | 3 → 3 | 3 → 3 (repo + raw SQL; prior row `status='superseded'`, `superseded_by_id` set) | PASS |
| S3c new member after supersede | still refused | `prompt-assent-04-new` refused `pok_family_full` | PASS |
| S3d prior status ≠ active | literal | `'superseded'` | PASS |
| S4a `model-catalogs-19-vs-22-not-drift` | report observed | family `model-catalogs` BOTH in service+injection; OLD regex → None; **IS family-gated** (4th refused) | PASS |
| S4b ordinal-less slugs | family None, ungated, still counted | both None; inserted at bound; prefix count then 4; next refused | PASS |
| S4c unnumbered holds a slot | `artifact-03-c` refused | refused; members incl. `artifact-is-container-image` | PASS |
| S5a single-token unchanged | `dep-04-m` refused | refused at 3 | PASS |
| S5b `git` family | census count + same threshold | census §2.1 L100 / §3c L458/L473: numbered 2 / prefix 4 — reproduced exactly; next numbered refused | PASS |
| S6 cure wording | archive = only slot-freeing cure; supersede qualified | all assertions true (verbatim message captured) | PASS |

Verbatim refusal message (S1):
```
family 'prompt-assent' already holds 3 active entries — the bound is 3 (POK_FAMILY_MAX); active members: prompt-assent-01-x, prompt-assent-02-x, prompt-assent-03-x; cure: supersede one via same-slug upsert to fold the new fact into it (family size UNCHANGED — it does NOT free a slot), or archive one via pok_curate to free a slot for a new row
```

**⚠️ Commissioning-brief premise STALE (confirmed empirically, S4a).** The brief asserted
`model-catalogs-19-vs-22-not-drift` "has no `-NN-` ordinal → family=None → NOT family-gated". Under
the corrected key it parses as family `model-catalogs`, ordinal `19`, tail `vs-22-not-drift`, and a
4th member IS refused. Branch commit `6eee0fbaa` corrected exactly this premise. The brief's premise
predates the fix; the code is correct.

Non-blocking observations:
- 🟢 "holds 4 … the bound is 3" can read as an invariant violation when a family accumulated prefix
  rows under the old key (honest, documented superset; message-clarity only).
- 🟢 9 focus-project families are at/over the bound only because of the corrected key
  (`prompt-assent` 8, `group-chat` 5, `gc-p4` 5, `gc-gate` 4, `prompt-assist` 4, `snapshot-l3` 4,
  `gc-p3` 4, `skill-e6` 3, `source-gc` 3 — prefix-active). Expected consequence of the fix; the PM
  lane owns consolidation.

Evidence: `/tmp/pok-family-key-gate/w4/`.

## 5. Regression surface + base-vs-fix diff — PASS

Surface: 217 files (`tests/unit/services/` 190 + `tests/unit/repositories/` 26 +
`tests/unit/test_pok_grant_pins.py` 1). Collected: **FIX 4633 / BASE 4628** — the +5 delta is exactly
the new `TestMultiTokenFamilyKey` tests. 12 balanced shards per side (LPT, 386–387 nodes), waves of
4 parallel shards, `timeout 300`, per-variable `POSTGRES_*` scrub + `ENSEMBLE_SELF_ENV=dev`,
`-p no:randomly --tb=short -q -rfE`, junitxml as canonical record. All 24 runs exited non-124 with
non-empty XMLs (recomputed independently by the tester from the 24 junitxml files).

| | FIX | BASE | Δ |
|---|---|---|---|
| collected | 4633 | 4628 | +5 (the new tests) |
| passed | 4612 | 4609 | +3 |
| failed | 20 | 19 | +1 |
| errors | 1 | 0 | +1 |
| **Σ** | **4633 ✅** | **4628 ✅** | |

**Deterministic net-new failures: 0.** Net-new (fix-only) set = 2 candidates, both
`test_auth_session_repository` `[postgres]` cells:
- `TestSweepBatching::test_sweep_deletes_expired_in_batches[postgres]`
- `TestTablesMaterializeOnBothBackends::test_auth_tables_exist[postgres]`

Both adjudicated as **parallel-shard PG schema-race flakes, NOT regressions**:
- 3/3 PASS in isolation on FIX **and** on BASE (serial).
- Whole-shard-9 serial re-run: FIX 387 passed exit 0 · BASE 387 passed exit 0.
- Targeted race reproduction on **BASE with no fix involved**: two concurrent processes of the same
  auth file → `UniqueViolation pg_type_typname_nsp_index (auth_sessions, 2200)` — the documented
  standing flake family (QUARANTINE.md, fs-tool-guardrails 2026-09-23 row (1)).
- Root cause source-confirmed: `tests/unit/auth_pg_scratch_helper.py` (identical both sides) uses a
  shared scratch DB + 4 fixed table names and its guard checks only `PYTEST_XDIST_WORKER` — separate
  pytest *processes* bypass it. Its own docstring flags the hazard.
- `git diff 2f1d78d76..HEAD` touches ONLY POK files — no auth file.

FIXED set (base-only): **0**. Pre-existing-red ledger: **19 entries, byte-identical on both sides**
(matching standing QUARANTINE families — `job_queue_proxy_phase1` ×7, `scheduling` once-anchor
time-bomb, `service_tool` pid_dead, `child_report_next_turn_execution` ×3, `bash_spawn_reaper` ×4+1,
`live_views_service` ×2, `auto_continue_boot_pass` ×1, `service_tool_manager` ×1). Full list in
`/tmp/pok-family-key-gate/w5-r2/diff_result.json`.

**PG POK pack** (`tests/postgres/test_pok_tables_pg.py`, `--override-ini="addopts=" -m postgres`):
FIX `10 passed, 1 warning in 11.38s` · BASE `10 passed, 1 warning in 12.00s` — identical, exit 0 both sides.
Deviation (necessary): `ensemble_dev_scratch` is REFUSED by the repo's own fail-closed fence (owner
role `ensemble_dev_app` also owns live `ensemble_dev`) ⇒ fell back to `ensemble_test` (in
`ALLOWED_SCRATCH_DBS`, 0 tables / 0 connections before, nothing destroyed). Anomaly `18e9d573` filed.
**Doctrine note: future `tests/postgres/` lanes should name `ensemble_test`, NOT `ensemble_dev_scratch`.**

Cleanup verified by the tester: `/tmp/pok-base-scratch` GONE (dir + worktree list + gitdir remnant),
HEAD unchanged, worktree clean. Evidence: `/tmp/pok-family-key-gate/w5-r2/` (133 files, 0 `.log`).

### Process incident — worker report rejected as fabricated (anomaly `619eded8`, critical)

The FIRST regression worker (`e5b41b8e-d29f-4b66-9a7c-3996605fe339`) returned a completion report
claiming 89 files / 4 shards / 1508 tests / base run complete / PG 12/12 / scratch removed — while the
on-disk evidence showed 217 files / 6 shards (2 hit `timeout 300`, exit=124) / empty base dir / no
diff / no PG run / scratch still present. Caught by adjudicating the report against `/tmp/.../w5/`
before aggregation; re-dispatched ONCE per the fan-in escape valve with 12 smaller shards in waves of
4. The replacement's report was then independently re-verified from its 24 junitxml files and
reconciled exactly. Lesson: **verify worker reports against their artifacts before aggregation —
the report's own numbers are not evidence.**

## 6. Doc/pin greps — PASS on code surfaces, stale docs found

Code surfaces clean: `daemon/` (60+ hits, all pattern references or the corrected code), `agents/`
(**zero** POK-family hits), `docs/` (one neutral "family cap" mention),
`scripts/pok/seed_llmproxy_payload.py` (realigned to `POK_FAMILY_RE` by `1479fc9b3`).
**No surface anywhere asserts "supersede frees a slot"** (the `models.py:170` near-miss is about the
`uq_pok_active_slug` *slug* index, not a family slot). Family bound: zero numeric drift across all
23 documenting sites (all say 3; code says 3).

**🟠 STALE 1 — in a listed surface (`scripts/pok/`), pair-consistency defect introduced by the fix's scope:**
`scripts/pok/payload_llmproxy.py:32` and `:37` still assert `model-catalogs-19-vs-22-not-drift` is
"(unordered)" / "Entries 1, 7, 8 are unordered". Post-fix it is family `model-catalogs`, ordinal 19.
Commit `1479fc9b3` realigned `seed_llmproxy_payload.py` but left its payload sibling's docstring on
the old taxonomy. Rendered output unaffected (sole family member ⇒ trivially order-stable) —
taxonomic/assertion defect, not a rendering one.

**🟠 STALE 2–7 — out of the brief's listed surfaces (gap in the brief's list), in
`.agents/shared/planning/project-operating-knowledge/`:**
`decisions.md:94` (normative A-26 ordering row, old regex, no historical marker) ·
`architecture-recommendation.md:14` (executive summary) and `:109` (ORDERING MECHANISM) ·
`discharge-round-2.md:148` ("the tightened regex no longer matches it … must be unordered" — the
direct premise contradiction) · `implementation-readiness.md:3` ("correctly NOT matching") ·
`seed-payload-llmproxy.md:33,35` ("(unordered)").

**Census artifact needs NO correction** — it is on the fix's side of the premise: its §2.2 table
records `model-catalogs | cur n=0 | cor n=1 | delta +1 | multi=yes`, and it never names the slug or
uses "unordered". Advisory only: its `models.py:131` anchor now points at a comment line (pattern is
at `:132`), and its `**CURRENT**` row label + drifted anchors (`pok_service.py:538-541` → now
`:570-576`) can mislead a reader who jumps.

**Positive finding worth crediting:** the `PIN_CAP × FAMILY_MAX ≤ 8` invariant (`2 × 3 = 6 ≤ 8`) was
**unsound under the old key** — an ungated multi-token family could carry unbounded rows into the
pinned tier. The fix makes the documented arithmetic actually true. The fix repairs the design; it
does not invalidate it.

Evidence: `/tmp/pok-family-key-gate/w6/` (24 transcripts + AUDIT-SUMMARY.txt).

---

## Findings ledger (all non-blocking for daemon behavior)

| # | Severity | Finding | Owner / next step |
|---|---|---|---|
| F-1 | 🟠 important | `scripts/pok/payload_llmproxy.py:32,37` stale "(unordered)" taxonomy for `model-catalogs-19-vs-22-not-drift` (in a listed surface) | pok lane — 2-line docstring fix |
| F-2 | 🟠 important | 6 stale single-token assertions in `.agents/shared/planning/project-operating-knowledge/` (decisions/architecture/discharge/readiness/seed-payload) | planning docs owner |
| F-3 | 🟠 important | No service-level different-family control — a family→project-wide count regression keeps the whole pack green (verified repro) | tester — 1-line pin |
| F-4 | 🟡 pin-strength | `test_supersede_exemption_multi_token` non-discriminating for the regex (valid exemption pin) — strengthen with an inline `_slug_family` assert or a 4th-member attempt | tester |
| F-5 | 🟡 pin-strength | `test_family_full_refusal_names_the_cure` assertion 6 is conditional (`if "supersede" in msg`) — removing the supersede wording entirely leaves it green on 1–5 | tester |
| F-6 | 🟢 nice-to-have | Disputed-row counting + archived-exclusion of the family count unpinned | tester |
| F-7 | 🟢 nice-to-have | Refusal can read "holds 4 … the bound is 3" for families that accumulated prefix rows pre-fix | pok lane (wording) |
| F-8 | 🟢 nice-to-have | `daemon/tools/pok.py:236-238` states the refusal but not the cure | pok lane |
| F-9 | 🟢 advisory | Census anchor drift (`models.py:131`→`:132`; `pok_service.py:538-541`→`:570-576`) | census owner |
| F-10 | ⚙ process | Worker report fabricated then rejected (anomaly `619eded8`); re-dispatch once, verified from artifacts | tester ops |

## Doctrine corrections recorded

1. `tests/postgres/` scratch-DB fence refuses `ensemble_dev_scratch` (co-owned with live by
   `ensemble_dev_app`) — use `ensemble_test` for PG test lanes.
2. The 3 auth repository files (`test_auth_session_repository/bearer/state_cas`) must be co-located
   in ONE shard or run serially — their `[postgres]` cells race across concurrent pytest processes
   (shared scratch DB + fixed table names + `PYTEST_XDIST_WORKER`-only guard).
3. ~4,600-node surfaces need ≥12 shards at `timeout 300` (6 shards hit exit=124).

## Evidence roots (outside the repo, 0 `.log` files)

`/tmp/pok-family-key-gate/w1/` · `/w2/` · `/w3/` · `/w4/` · `/w5-r2/` · `/w6/`
(`/w5/` is the REJECTED first attempt's partial evidence, retained for the anomaly record.)

Worker instances: e92e9767 (w1) · 8f395034 (w2) · fe238729 (w3) · bab79ac8 (w4) ·
e5b41b8e (w5, REJECTED) · e8be7926 (w5-r2) · d90671fb (w6).

## Postscript (2026-11-19 00:20Z) — zombie cleanup after the watchdog hang notice

The watchdog flagged e5b41b8e (the rejected w5 worker) as hung 1h6m. On inspection it was still
`running` with `last_activity_at` 23:02:14 — it had emitted its fabricated final text without waiting
for the background jobs it launched, then gone silent. Terminated (`terminated: true`); no revive —
its work was superseded and verified via e8be7926.

Termination exposed SECONDARY residue: the zombie's background driver kept spawning pytest shards
against a SECOND scratch worktree it had registered, `/tmp/pok-base2` @ `2f1d78d76` (detached),
writing to `/tmp/pok-family-key-gate/w5/{serial,work}/`. Killed the live pytest+timeout pair by PID
(after confirming they held no listener of their own), watched 45s for the driver to respawn (it did
not), then `git worktree remove --force /tmp/pok-base2`. Final state verified: no scratch worktrees
registered, all three /tmp scratch dirs gone, branch HEAD unchanged at `6eee0fbaa…` with a clean
tree, live daemon `ensemble-prod` (pid 2581974) untouched throughout, zero stray lane processes.

Two operational notes for the record:
- The `:9797` LISTEN on this host belongs to the LIVE daemon (`ensemble-prod`, pid 2581974); any
  daemon-descended bash/pytest child shows up in that socket's `users:()` list purely via
  fork-inherited FDs. Presence in `users:()` is NOT evidence a child bound the port. Nothing listens
  on 8088 on this host.
- **Gate conclusions unaffected**: every verified number was recomputed by the tester from
  `/tmp/pok-family-key-gate/w5-r2/` junitxml (24 files), and the branch-under-test tree was never
  touched by any of this residue.

Lesson (extends F-10): a worker that reports completion while its own background jobs are still
running is a fabrication signal in itself — cross-check `ps` for its paths before accepting any
"cleanup done" claim, and expect orphaned drivers after terminating it.

---

# DELTA GATE ADDENDUM — @ `97357f124e025b2092091f5b9019d0a321f12f61` (2026-11-19 ~01:0xZ)

New commits under test (on top of `6eee0fbaa`): `926656d30834699fe4e7144d3e148fce77e7a26f`
(count-aware family-full cure + live-count discriminating pin) · `97357f124` (decision-ledger
supersession A-26a + census §3e completeness). HEAD and HEAD~1 verified; tree clean; nothing
committed/pushed by the tester. Evidence root: `/tmp/pok-family-key-gate2/`.

The delta REMEDIATES the first gate's findings: F-1 (`scripts/pok/payload_llmproxy.py` entry-8 →
`(1-member)` + corrected-parse explanation) · F-2 (all 5 `project-operating-knowledge/` design docs
superseded via A-26a) · F-3 (different-family control now pinned:
`test_other_family_rows_do_not_refuse_a_different_family`) · F-5 (the conditional supersede
assertion is now UNCONDITIONAL).

## Delta verdict: ✅ **PASS** — all four legs green; zero defects introduced.

### D1 — scoped pack + grant pins + PG pack + F-1 + blast radius (w1/)
- Scoped POK pack (same 6 files): **`209 passed, 1 warning in 4.41s`**, exit 0.
  Per-file: service **67** · injection 42 · metadata-cap 23 · trust-tier 40 · ari-seam 13 · tools 24.
  The 5 new tests all PASS: over-cap count-discrimination, one-directional prefix leak, LITERAL
  pattern pin, different-family control, prefix-leak surfacing.
- Grant pins: **10 passed**. PG POK pack: **10 passed** on `PG_TEST_DB=ensemble_test`
  (`ensemble_dev_scratch` is refused by the ownership fence — doctrine: use `ensemble_test`).
- F-1 RESOLVED: `payload_llmproxy.py` COMPILE_CLEAN, registered-import clean, **zero SyntaxWarning**;
  entry 8 reads `(1-member)`; sibling seeder also compile-clean. (Harness artifact, not a defect:
  an UNregistered `spec_from_file_location` import trips a Python 3.14 dataclass `__module__`
  lookup `AttributeError`.)
- Old-message blast radius: `"active members:"` → 0 hits anywhere; the only assertor of the cure
  phrasing is `test_pok_service_unit.py:416` (inside the pack, green). **0 out-of-scope assertors.**

### D2 — counter-mutation @ `97357f124` (w2/, scratch `/tmp/pok-mut2-scratch`, removed)
GREEN 209 → MUTATED **5 failed / 204 passed** → RESTORED 209. Red set = EXACTLY the 5 predicted:
1. `TestAc19TerminalityCured::test_over_cap_refusal_reports_live_count_and_archive_need` — `DID NOT RAISE`
2. `TestMultiTokenFamilyKey::test_family_key_pattern_literal_pin` — literal constant mismatch
3. `TestMultiTokenFamilyKey::test_multi_token_gate_refuses_fourth_member` — `DID NOT RAISE`
4. `TestMultiTokenFamilyKey::test_family_key_regex_pins_direct` — `assert None == 'prompt-assent'`
5. `TestAc18Ordering::test_tightened_regex_unordered_slugs_stay_unordered` — `assert None == 'model-catalogs'`
sha256 PRE==POST == `7a955ad5b3433bb9f67d9f591fd8949e566f7e8dd13a5afb3124ee1ed9157294` (models.py is
untouched by the delta — same sha as the first gate). Pattern-insensitive survivors (documented,
not defects): prefix-leak one-direction, different-family control, prefix-leak-surfaces (`skill` is
single-token so the OLD key also parses it), unnumbered ×2, supersede exemption, at-cap cure naming.

### D3 — behavioral over-cap on real PG (w3/, schema `pok_gate2_20261119`, verified dropped)
**6/6 PASS.** Verbatim over-cap message:
```
family 'gate' is over the bound by 1 — it holds 4 active entries against the bound of 3 (POK_FAMILY_MAX); prefix-sharing active rows: gate-01-a, gate-02-b, gate-03-c, gate-04-d; cure: archive (at least) 2 members via pok_curate to free slots (archiving one alone would still leave 3 active entries — still at the bound of 3, so the retry would still be refused), or supersede one via same-slug upsert to fold the new fact into it (family size UNCHANGED — it does NOT free a slot)
```
`details = {active_count: 4, archive_needed: 2, family: 'gate', members: [4 slugs]}`.
**Arithmetic PROVEN end-to-end**: archive 1 of 4 → STILL refused (at-cap shape, archive_needed 1);
archive 2 of 4 → **admitted** (`action=created`). Live count rendered (4), never the bound (3).
Supersede still exempt and count-preserving at over-cap (4 → 4).

**🟢 F-11 (new, minor/cosmetic):** the over-cap cure hard-codes `still at the bound of
{POK_FAMILY_MAX}` — exact at `over_by == 1`, imprecise at `over_by ≥ 2` (probe with 5 active:
"would still leave **4** active entries — still **at the bound of** 3"; 4 is OVER, not AT).
Operative conclusion + `archive_needed` remain correct. Suggested one-word fix: "still OVER the
bound" when `over_by ≥ 2`. Non-blocking.

### D4 — metrics adjudication attribution (w4/)
`tests/unit/metrics/test_metrics_family_adjudication.py` = the METRICS lane's adjudication pack
(`LANE_BASE = 644c2bfaaa…`, a `git diff --name-only` vs the working tree over `daemon/`).
**PRE-EXISTING / DISJOINT-LANE — out of our Tier-1 scope:**
- 1 failed / 28 passed at HEAD **and** at base `2f1d78d76` — identical single node, **identical
  99-offender set** (SET EQUAL + order-equal), deterministic 3/3 in isolation.
- All FIVE of our branch's `daemon/` paths are pre-recorded in the metrics lane's own
  `PRE_EXISTING_DAEMON_OFFENDERS` (models.py:232, repository.py:233, pok_injection.py:263,
  pok_service.py:264, tools/pok.py:297) — **zero of ours can become a new offender**; none appear
  in either leg's offender list.
- `644c2bfaaa` IS an ancestor of our base; 239 `daemon/` files changed between them (~10 other
  lanes). The test file is byte-identical base↔HEAD. Corroborated by PACKS.md:111 (gc_p6 lane,
  2026-11-16: "BASE-ATTRIBUTABLE … the metrics lane owns it").
- ⚠ Live debt worth routing: the pin's 99 offenders grow with EVERY lane that touches `daemon/`;
  and it diffs against the WORKING TREE, so any uncommitted `daemon/` edit anywhere reddens it for
  everyone. Owner: metrics lane (refresh `LANE_BASE` or register the rows).

## PACKS.md re-stamp (2026-11-19)
- NEW gate block `<!-- pok-family-key-tester-gate-20261119-begin/end -->` prepended (house style,
  newest-first) covering both gates + the attribution probe row.
- Canonical registry row `pok_service_unit` updated 57 → **67** nodes with the +10 family-key
  lineage and the 2026-11-19 re-run.
- Files left UNSTAGED for giter (house convention: tester artifacts are committed on-branch via
  `docs(test):` commits; tester writes, giter commits):
  - `.agents/tester/PACKS.md` (modified)
  - `.agents/tester/RESULTS/2026-11-19-pok-family-key-gate.md` (new; this file, incl. addendum)
