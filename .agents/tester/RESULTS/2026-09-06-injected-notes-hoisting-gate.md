# TEST GATE — fix/injected-notes-hoisting @ c2142c69 (+4 gate commits → docs tip)

Date: 2026-09-06 · Test Leader instance: bee097d3 (Tester) · Worktree: `agents-ensemble-hoisting-fix` (main tree untouched throughout)

**VERDICT: ✅ SHIP — merge green-lit. 0 branch-caused regressions across a ~17k-node sweep; all five gate sections PASS; every sweep failure attributed (baseline rows, base-evidenced residue, context-flakes, or env-shaped).**

Branch under test: `fix/injected-notes-hoisting` @ `c2142c69` (bb4e3e89 engine ×6 files + 4e1e6698 kill-switch ×3 + c2142c69 boot validation ×4; base `feb5e915` == origin/latest).
Gate-added commits (all test/docs, per grant): `14187021` (QUARANTINE rows ×2) → `fa6826fe` (sweep-gap tests ×20) → `fa70372a` (boot-validation EOF newline) → `a7e5ffaa` (acceptance chain test ×2) → final docs commit (this RESULTS + QUARANTINE addendum + PACKS ledger; see §7).

---

## §1 Scenario closure (the incident) — PASS

**Acceptance criterion met: no pre-existing single chained test existed → wrote one.**

- File: `tests/unit/services/test_injected_notes_hoisting_acceptance.py` (commit `a7e5ffaa`), tests:
  - `TestInjectedNotesHoistingAcceptanceChain::test_full_incident_scenario_chain_end_to_end`
  - `TestInjectedNotesHoistingAcceptanceChain::test_flag_off_same_scenario_hoists_everything_legacy_shape` (negative control / non-vacuousness)
- Rig: **real `StateGraph` + real `AsyncSqliteSaver` (file-backed tmp_path, WAL, busy_timeout) + real `ContextCompactor` selection/envelope/`build_sentinel_replacement` + real `persist_compaction_result` seam + fresh-connection reload**; ONLY the LLM summarization call is stubbed. Mirrors the `test_proactive_compaction_symptom_acceptance.py` idiom.
- All five legs asserted on the reloaded checkpoint (not mocks):
  1. ANSWERED bare note ABSENT post-compaction (`note-answered` not in reloaded ids; content joined doc body only)
  2. Envelope math from LIVE `CompactionResult`: `injected_absorbed == 1` (the answered note), `injected_preserved == 2` (ctx_kind + unanswered); summarizer prompt CONTAINS the answered-note content and does NOT contain the ctx_kind body
  3. context_kind block STILL present verbatim (`context_kind == "task_context"`) AND hoisted (`ctx_idx < fold_idx`)
  4. UNANSWERED note STILL present VERBATIM and hoisted (`unanswered_idx < fold_idx`)
  5. Fold card intact (`compaction-global-*` SystemMessage, non-empty)
- Results: acceptance 2/2 PASS (0.31s); existing 32-test module 32/32; combined 34/34. Exit 0.
- Technical note: Variant-A `aupdate_state` (no `as_node`) requires ≥1 completed superstep — the test seeds one `ainvoke` first (same pattern as the proactive-compaction symptom test :337-340).

## §2 Kill-switch matrix (empirical, real load_config) — PASS

Driver: `/tmp/ks_matrix.py` (throwaway, one subprocess per case, fresh env). Resolver: `daemon/config.py:2218-2260`.

| Case | load_config | Resolved absorb | Resolved proactive |
|---|---|---|---|
| unset | OK | **ON** | True |
| `=1` | OK | **ON** | True |
| `=` (empty) | OK | **ON** | True |
| `=0` / `=false` / `=no` / `=off` (case-insensitive: `FALSE`,`OFF`; `True`→ON) | OK | **OFF** | True |
| `=purple` | **ValueError** — message names the flag: `"Invalid ENSEMBLE_INJECTED_NOTES_ABSORB value 'purple' — expected one of 0/false/no/off (disable) or 1/true/yes/on (enable)"` | n/a | n/a |
| alias `ENS_INJECTED_NOTES_ABSORB=0/1` (main unset) | OK | ON (alias inert) | True |

- **Single spelling** — no alias, no `ENS_*` prefix (empirically inert). Contrast: `ENSEMBLE_PROACTIVE_COMPACTION` HAS a legacy alias (`COMPACTION_PROACTIVE_ENABLED`); absorb does not.
- **No coupling (5/5)**: proactive=0+absorb unset → absorb ON; absorb=0+proactive unset → proactive ON; both-off, both-on independent; `proactive=0 + absorb=purple` STILL raises (no short-circuit). Confirms docstring `daemon/config.py:2245-2247`.
- Boot-validation module `tests/unit/test_injected_notes_absorb_boot_validation.py`: **4/4 PASS** (0.12s). EOF-newline nit fixed → commit `fa70372a` (1 file, 1 insertion).
- Legacy OFF-semantics through REAL compaction: 8 tests (`test_flag_off_answered_note_returns_to_legacy_hoist_shape`, `test_flag_off_all_bare_unanswered_channel_skips`, `test_flag_off_gate_flips_back_to_injections_dominate`, ctx-kind/id-less parametrizations) assert legacy two-bucket shape (`injected_preserved=1, injected_absorbed=0`, hoisted head) — **no gap**. Resolver direct matrix pinned by `TestAbsorbKillSwitchResolver` (15 parametrized).

## §3 Safety invariant sweep — PASS

- 32 shipped tests verified REAL (post-compaction state assertions, not mock call-counts). Module run: 32/32 (0.26s).
- Exits enumerated from `daemon/compaction.py`: summarization (:2520), partial_summary (:2582), truncation (:3810), emergency_truncation (:2346), skipped_injections_dominate (:2124), skipped_preserved_within_threshold (:2286), None-dedup, CompactionAborted (:570). (`chunked_summarization` is never stamped post-merge.)
- **20 gap tests ADDED** (`tests/unit/services/test_injected_notes_hoisting_sweep_gaps.py`, commit `fa6826fe`): OFF×{last-message, tool-only}; emergency×{last,tool-only,id-less}×both flags; CompactionAborted seam guard + positive control ×both flags; partial_summary ×both; truncation-fallback ×both; skipped_preserved_within_threshold result-shape + seam defense-in-depth ×both.
- Combined module: **52/52 PASS** (0.36s). No invariant violations; no weakening. Pre-write guard demonstrably catches a candidate silent-loss class (defense-in-depth positive control).

## §4a Base-failure attribution (reviewer-requested) — PASS

Both expected failures reproduced at base `feb5e915` (scratch worktree, detached, solo, <1s each) → **pre-existing, not branch-caused**:
- (a) `tests/unit/test_phase4_manager_decomposition.py::TestFacadeDelegationPattern::test_manager_pause_instance_cascade_delegates_to_lifecycle_service` — stale contract (`cascade_to_root=True` kwarg predates branch; same node as LCA-gate stale-contract row). 1F @ base (0.81s).
- (b) `tests/services/test_instance_messaging_queue_routing.py::TestMessageRouteQueueIdForwarding::test_router_forwards_queue_id_to_enqueue_message_job` — member of the 32-node `messages.py:258` MagicMock-await class (row evidenced @ e866c116; **re-verified at feb5e915**). 1F/15P file solo (1.05s).
- QUARANTINE.md rows appended (commit `14187021`, 1 file +2). Scratch worktree removed.

## §4b Full regression + attribution — PASS (0 branch-caused regressions)

Planner ground truth: **17,053 collected nodes**; 16 partitions (dir/file-based, no test-id slicing), each `timeout 300` + xdist `-n` sized 2–8 (perf-matrix P1 solo `--override-ini timeout=120`); `unset SSL_CERT_FILE SSL_CERT_DIR` mandated; drift-check before/after every invocation (all 16 stable @ `a7e5ffaa`); `tests/e2e` excluded (release-gate only, live daemon+LLM); `tests/postgres` auto-deselected by addopts.

| Partition | Scope | Result (F/E) | Attribution |
|---|---|---|---|
| P1 | perf-matrix solo | **PASS 12/12** (163s) | clean (WATCH stays open for under-load runs) |
| P2 | tests/integration | 10F/16E | 5 = row 26 exact; 21 httpx class → context-flake/env (see below) |
| P3a | job_queue a–l | 4F | row 11 family exact |
| P3b | job_queue m–z | 3F | row 11 members exact |
| P4 | services+mqr+property | 2F | row 64 (expected) + row 44 |
| P5 | **tests/unit/services (branch home)** | 7F | row 24 ×7; **hoisting files 54/54 PASS in-suite** |
| P6 | tests/unit/tools | 5F | rows 39-43 archive ×5 exact |
| P7 | unit subdirs + api/static/... | 15F/2E | 9 fresh-SQLite (row 31/32) + row 45 ×3 + residue (attributed) + jsonb PG-env |
| P8 | unit a–c | 9F/21E | rows 55, 61 (×17E), 22 (×4E) + residue (attributed) + 2 load-flakes |
| P9 | unit d–l | 14F | 11 = row 25 exact + devops ×3 (base-evidenced) — **boot-validation 4/4 PASS in-suite** |
| P10 | unit m–r | 7F | row 63 (expected, failed at HEAD as predicted) + row 23 ×5 + models_split (base-evidenced) |
| P11 | unit s–z | 54F/2E | rows 21 (47) + 16 (2, solo-pass confirmed) + residue (attributed) |
| P12 | root a–h | 5F | row 25 enqueue ×1 + residue (attributed) |
| P13 | root i–q | 58F | row 23 (26 exact) + row 31 (18) + residue (attributed) |
| P14 | root r–z | 12F | rows 31/32 (9) + row 28 (1) + skill_evolution ×2 (base-evidenced) |
| P15 | tools+opencode | **PASS 778/778** | clean |

**Totals: ≈16,554 passed / 205 failed / 41 errors / ~259 skipped (+5 xfailed, 233 collection-deselected) — 0 TIMEOUTs; longest pack 163s (P1); largest xdist pack 66.6s (P10).**

### Residue attribution (base feb5e915 scratch worktree, solo, A/B) — all branch-exonerated
- **~45 nodes FAIL at base with IDENTICAL signatures** (17 groups): devops meta ×3, coder prompt ×1, coder_developer_migration ×5, terminal_reason mirror ×1, vision ×1 (messages.py:258 class), validate_agent_id ×1, wanderer tools.allow ×2, agents_api isolation ×2, test_api MagicMock-await ×2, innate_skills 'question' ×1, llm_meta coder ×1, error_codes 19==18 ×1, memory_integration ×10, skill_evolution_config ×2, models_split LivezResponse ×1, ui_prefs search-kwarg ×2, chokepoint ×2 (incl. B1: allowlist violations in `manager.py:5637` / `job_recovery_service.py:1993`).
- **Context-flakes (solo-PASS at BOTH base and HEAD ×2; fail only under xdist partition load)** — row-53 httpx shared-process class: wc_wake ×2/3, dead_letter ×3/4, vscode routing 8/8, vscode security 7/8, rag auto_test ×1, plus row-16 task_reconciliation ×2 and builtin_mcp ×2 (load-flakes).
- **Deterministic pre-existing at BOTH commits (identical signature)**: `test_vscode_security_integration.py::test_c1_valid_repo_folder_not_blocked` (`DID NOT RAISE httpx.ConnectError`; proxy 503 @ `vscode_proxy.py:281`; test `:329`) — base G23 FAIL + HEAD-solo FAIL ×2.
- **Env-shaped, report-not-block**: jsonb_migration ×1F/2E (PG duplicate-type `infra_asset_types` in shared PG), vscode SPA-fallback 404 ×2 (unbuilt frontend dist in worktree), `test_migration_api_comprehensive::test_manager_tests_pass` (subprocess pinned to MAIN-tree rootdir — fails identically at base).

### Process findings (for future gates)
- `pytest | tail` masks exit codes; `.pytest_cache/v/cache/lastfailed` is shared across parallel runners (polluted — unusable for per-run attribution). Use log-file capture + `echo PYTEST_EXIT=$?`; per-runner `-o cache_dir`.
- Dispatcher escaped-glob bug (`test_\[a-l\]*.py` collects 0) — 4 workers self-corrected; dispatch globs unquoted.
- QUARANTINE row 29 correction: `unset SSL_CERT*` did NOT clear the httpx 503/`object.__new__()` class under partition load (21 nodes); the class behaves as row-53-style shared-process pollution — solo-deterministic PASS both commits.
- FE pack `EXPECTED_BRANCH` default is stale (`feature/mission-class`) — pin per gate (known follow-up from 2026-09-04).

## §5 FE safety — PASS (TOLERANT)

- BE change shape: `compact_executor.py:1452-1455` adds `injected_preserved`/`injected_absorbed` as flat top-level ints in `detail` (None-omitted). Additive only.
- Models: `CommandProgressDetail` (`models/index.ts:73-81`) is a compile-time-only interface; ingress is a blind cast (`sse.service.ts:98-99`); `command-state.service.ts` pure passthrough (227/242/336/417); **no runtime validators exist**.
- Render path: all consumers read known keys directly (`chat-interface.component.ts:341,350,401-429,436-437` with `typeof` guards + `default:` arm); `commandSectionCounts()` (:383-396) docblock states the additive/flat wire contract by design; fold-card path (`isCompactionDoc` :566, `compactionPreview` :573) parses message body only. **No `Object.keys`/spread over command detail anywhere** — unknown keys sit inert.
- FE drift pack: `RESULT: PASS` @ `fa6826fe` (EXPECTED_BRANCH pinned; tsc 0, build 0, 10 known SCSS warnings), 10.8s.
- Full Jest: **2459/2459** (70 suites, 10.6s, `--no-cache`, rev-parse-bracketed).

## ensure.md status (scoped)

- Core #1 changed packs PASS: the branch's packs (P5 home incl. 54 hoisting tests; P9 boot-validation) PASS; whole-repo regression attributes 100% of failures as above. Core #2/#3 (`concurrency_atomic_unit_test` incl. `test_deadlock_fix.py`) ran inside P12 → PASS. Core #4 static dev.sh check unchanged by branch (no dev.sh change in diff; FE/BE boots not in scope of this worktree gate). Release Gate NOT triggered: change is scoped (compaction/injection selection + config flag + detail payload), not cross-module architecture; e2e excluded per scope decision (documented above). No contradictions found in ensure.md methods this run.

## Commits made by this gate (test/docs only, per grant)

| Hash | Content |
|---|---|
| `14187021` | QUARANTINE.md — 2 base-attribution rows (§4a) |
| `fa6826fe` | `test_injected_notes_hoisting_sweep_gaps.py` — 20 gap tests (§3) |
| `fa70372a` | boot-validation EOF newline (§2) |
| `a7e5ffaa` | `test_injected_notes_hoisting_acceptance.py` — 2 acceptance tests (§1) |
| (final) | This RESULTS file + QUARANTINE §4b addendum row + PACKS.md ledger entry |

## Gaps / follow-ups (non-blocking)

1. Perf-matrix WATCH remains open for under-load runs (this gate ran it solo-clean by design).
2. QUARANTINE row-29 wording should be corrected (SSL-unset does not clear the httpx class under load) — owner: next gate touching rows.
3. Chokepoint allowlist red at base (`manager.py:5637`, `job_recovery_service.py:1993` direct SQL) — independent owner fix.
4. FE pack `EXPECTED_BRANCH` stale default — pack-script fix pending (known since 2026-09-04).
5. Shared `.pytest_cache` + tail-pipe evidence traps — adopt log-capture + per-runner cache dirs next sweep.

**FINAL: SHIP.**
