# VERIFICATION GATE — LCA inline-LLM report judge @ d6e30d9d (INTERIM — aggregation in progress)

Date: 2026-09-07 · Tester lane
Branch: `feature/leader-completion-attestation` @ `d6e30d9d` (base `bb052fce`; delta 2 commits: `7a899517` judge feature + `d6e30d9d` pre-merge punch list)
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/ens-lca-judge`. Main worktree untouched (currently on `feature/job-queue-mission-tree` per live-probe drift check).
Workers: 6 dispatched (matrix / e2e / liveprobe / neighborhood / pg / bootsmoke). Tester evidence commits may land on the branch during the gate (`test:` prefix, `.agents/tester/RESULTS/*` paths only — coordination addendum issued).

## Job 3 — LIVE-LLM judge probe: ✅ PASS-WITH-NOTES (worker d446d422, commit b42f7237)

- **Executable**: credentials present (OPENAI_API_KEY sk-8***, OPENAI_BASE_URL https://llm.ensem.dev/v1, OPENAI_MODEL=agentic, OPENAI_MODEL_KEYWORDS=quick → judge resolves **quick**). No .env file; shell env + `daemon.config.load_config()` resolve cleanly.
- **Real path invoked**: `daemon.services.attestation_report_judge.judge_completion_report_async` — imported, not reimplemented. Same JUDGE_SYSTEM_PROMPT, same `resolve_judge_model`, same HA wrap (`wrap_langchain_failover` + `ThinkingChatOpenAI` + `clean_llm_config`), same per-attempt `min(JUDGE_TIMEOUT_S=10, request_timeout=610)=10s` + asyncio.wait_for belt. Probe used timeout_s=15/call under shell `timeout 300`; total wall ~95s.
- **Verdict matrix 7/8**: G1 ✓(12.0s) · G2 ✗ timeout@15s → conservative False · G3 ✓(6.7s) · G4 ✓(13.6s) · N1 ✓-timeout-False · N2 ✓-no(2.6s) · N3 ✓-timeout-False · N4 ✓-timeout-False. P50 15.0s; 4/8 hit cap.
- **FINDING 🟠 MEDIUM — production timeout vs proxy tail latency**: successful calls spanned 2.6–13.6s (5× jitter, payload-independent); 4/8 exceeded 15s. Production cap is JUDGE_TIMEOUT_S=10s ⇒ those calls time out in prod too. Timeout→False is judge-as-designed (falls back to deny+nudge = pre-feature base behavior — NOT a regression vs base), but G2-class genuine reports can be nudged despite being complete; repeated timeouts risk the deny-count escalation loop. Operator decision: raise JUDGE_TIMEOUT_S / make it env-tunable, or accept + document conservative posture. Re-probe after any LLM-side latency fix.
- **FINDING 🟢 INFO — prompt+parser correct on all 4 returned responses**: single-line strict JSON, no fences/prose; conservative tone producing specific, evidence-naming reasons (counts, diff stats, commit hashes).
- Artifact: `.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe.py` (362 lines) @ `b42f7237`.
- Honesty ledger status: **CLOSED** — the previously-unverified live-model surface has now been exercised with a real call path.

## Job 1 — Attestation matrix: ✅ PASS 470/470 (worker 8543133b, 8.27s)

- Drift check (relaxed rule): HEAD `b42f7237` contains `d6e30d9d`; diff vs d6e30d9d = ONLY the sibling live-probe artifact. Proceeded correctly.
- Composition: 40 files / 470 tests — F1 `tests/unit/test_attestation_*.py` 15 files/283 · F2 `tests/unit/tools/test_attestation_*.py` 3/49 · F3 `tests/integration/test_attestation_*.py` 21/120 · F5 `tests/migration/test_attestation_migration.py` 1/18. No 4th-family files outside the globs (verified vs prior-gate `git ls-tree 9d042c88` = 37 files; current ls-tree = 40).
- Delta accounting vs prior gate (37 files/398 tests): +3 judge files, +72 tests (`test_attestation_judge_wiring.py` 21 · `test_attestation_report_judge.py` 34 · `test_attestation_judge_resolver.py` 17) = 470 exact.
- Ground truth: collect-only 470 == developer 470 (±0). Run `timeout 300 ... --tb=short -q` → **470 passed / 0 failed / 0 skipped / 0 errors, EXIT 0, 8.27s** (prior gate 8.28s). No quarantine rows triggered.

## Job 5 — Real-PG execution: ✅ PASS (worker a910b109, ~5s of runs)

- Drift: runs at `d6e30d9d` (worktree-local venv verified via `daemon.__file__`); re-checked under relaxed rule (H=`b42f7237`, ancestor OK, Results-only diff, 3 test files + daemon/ = 0 differing paths).
- Counts — exact executed-vs-skipped parity: baseline (default dialect) **72P/0F/0S/0E** 1.69s · real-PG **72P/0F/0S/0E** 1.48s · real-PG + engine-spy plugin **72P/0F/0S/0E** 1.56s. No dialect-conditional skips.
- Dialect canary: (a) engine-spy (external pytest plugin, `/tmp` only) observed **0 engine/connection builds of any dialect** → the 3 judge suites are pure-unit, PG-inert at run time (valid finding per pack definition, not a failure). (b) Positive engine-path proof: `daemon/persistence.py::_build_pg_connection_string` exercised under wired env — BOTH env branches → `postgresql` dialect, `current_database() = ensemble_lcajudge_pg_d6e30d9d`, `PostgreSQL 14.22`; asyncpg leg same DB. `DIALECT CANARY: ALL OK` (no silent SQLite fallback possible under this env).
- DB lifecycle: CREATE → existence proof → pre-drop conn count 0 → `DROP DATABASE … WITH (FORCE)` → `LIKE 'ensemble_lcajudge%'` scan = 0 rows. `ensemble_prod` untouched (read-only witness).

## Job 2 — Independent E2E: ✅ PASS 10/10 (worker 23336ef7, 1.4s, commit fdec1e9f)

Artifact `.agents/tester/RESULTS/2026-09-07-lca-judge-e2e-scenarios.py` (1365 lines, self-contained fixtures: real-graph eviction, file-SQLite tmp_path/WAL/NullPool, memory_saver, hermetic kill-switch isolation; judge stub at `_invoke_judge_llm` — the seam `judge_completion_report_async` delegates to — with call recorder). Drift rules honored (HEAD descendant of d6e30d9d; daemon/+tests/ byte-identical).

| Scenario | Verdict | Key evidence |
|---|---|---|
| (a) judge-YES → allowed | PASS | gate evaluates pre-judge (`decision=denied should_inject_nudge=True`) then judge row `verdict=yes llm_judge_model=fake-judge-yes llm_judge_latency_ms=0`; override → ZERO nudges delivered, denied_count 0 before AND after, no marker writes, `completion_gate_escalated=False`, status `idle` |
| (b) judge-NO → deny+nudge → attest → allowed | PASS | `decision=denied … attestation_required=True delegation_tool_call_total=1` → nudge byte-equals imported `ATTESTATION_NUDGE_TEXT`; header + `ReportJudge` mermaid node on constant AND delivered copy; checkpoint kwargs `{attestation_nudge, denied_count:1}`; attested-allow resets counter |
| (c1) timeout | PASS | `verdict=timeout llm_judge_error_class=TimeoutError llm_judge_model=…` + deny+nudge |
| (c2) HTTP error | PASS | `verdict=error llm_judge_error_class=APIStatusError` (real openai class) + deny+nudge |
| (c3) unparsable JSON | PASS | `verdict=unparsable` (non-JSON garbage → `_parse_judge_response` → None) + deny+nudge |
| (d) kill-switch OFF | PASS | real env setenv=0 + `reset_llm_judge_resolver_for_tests()` (the Pattern-C restart-equivalent); `is_llm_judge_enabled() is False`; stub call_count==0; zero judge rows; straight deny+nudge |
| (e1) attested / (e2) not-required / (e3) pending-wakeup / (e4) terminal_after_bound | PASS ×4 | stub call_count==0 + zero judge rows + zero nudges on each; (e3) watcher row stays PENDING; (e4) `event=leader_completion_gate_terminal_after_bound` fired, ruling-2 reset (counter cleared, escalation flag set) |

## Job 4 — Neighborhood A/B: ✅ PASS (0 introduced) (worker b8827a73, ~220s/side, evidence commit 346b1842)

- Pack: the pinned 58-file set (all files exist at BOTH commits; 0 file-set deltas). Delta overlap with neighborhood: **zero** (`git diff --name-status bb052fce..d6e30d9d` touches only judge/graph/attestation-test files; sole attestation-symbol consumer `test_attestation_compaction.py` imports untouched `attestation_scanner`).
- Counts: BASE `bb052fce` **875P/6F/5S/16E** (902 collected, 216.9s) ≡ HEAD (run at H=`fdec1e9f`, RESULTS-only ahead; code byte-identical to `d6e30d9d`) **875P/6F/5S/16E** (215.9s). Sorted FAILED+ERROR diff = **EMPTY** (22 IDs/side, `comm`/`diff` clean) → solo triage not triggered.
- Attribution: 6 FAILED families pre-existing (compaction_e2e ×2, message_queue_e2e ×3, instance_messaging_queue_routing ×1); 16 ERROR = `tests/postgres/test_initiative_message_pg.py` setup failures on the documented fresh-SQLite migration trap (PG-only `DROP CONSTRAINT`, environmental — pinned baseline class).
- Context (out of adjudication scope): prior-gate delta showed 872P/7F; the 7th family (`test_injection_compaction.py::test_all_injected_messages_skips_compaction`) passes at both bb052fce and d6e30d9d — recorded in the failedids artifact.
- Cleanup: base worktree `ens-lca-judge-base-tmp` removed (verified). Evidence: `2026-09-07-lca-judge-neighborhood-{filelist,failedids}.txt` @ `346b1842`.

## Job 6 — Boot smoke: ✅ PASS (worker 9d7a1afc, ~4 min wall, commit 2a43904c)

- **Boot-log MUST lines confirmed** (log `ens-lca-judge/data/logs/ensemble.log`; evidence copy `/tmp/lca_judge_boot_ensemble_run1_run2.log`): run-2 **L168** (same shape run-1 L18): `Leader completion attestation resolved: mode=enforce window=3 deny_bound=3 attestation_enabled=true llm_judge_enabled=true llm_judge_model=quick … ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=<unset> … Restart required to flip` → judge flag **default-ON with env unset**, resolved model **`quick`** (OPENAI_MODEL_KEYWORDS hit). Logging home `attestation_resolver.py:506-533` (`emit_attestation_boot_log` from `InstanceManager.__init__`) — exists and fires; no absence finding.
- Engine discriminator run-2 **L160**: `Creating PostgreSQL engine: localhost:5432/ensemble_smoke_lcajudge_d6e30d9d`; 0 `ensemble_prod` mentions in the run-2 region.
- **dev.sh ensure.md static check**: `--timeout-graceful-shutdown 10` PRESENT (comment `dev.sh:99`, flag on launch line ~:104). Boots replicated launch minus `--reload` (documented deviation — sibling evidence commits would trip the reloader).
- **Deny-drive SKIPPED per escape clause** (stated reason: judge stub requires shared `OPENAI_BASE_URL` override which starves the leader's own planning calls; multi-minute delegated drive > remaining budget). Nudge-content incl. `ReportJudge` node is covered by E2E scenario (b) — no coverage hole.
- **FINDING 🟠 MEDIUM ops / LOW data-impact — PRE-EXISTING (known F-DR1-2 class, not introduced by this delta)**: `POSTGRES_URL` is honored by persistence/checkpointer but ignored by `repositories/factory.py::create_postgres_engine` (:180-210 — per-field `POSTGRES_*` envs only) → run-1 repository engine fell through to `ensemble_prod` defaults (idle 6s boot, zero instance data, graceful dispose; delta introduces zero schema DDL → exposure bounded to no-op DDL guards/reads). Corrected full-env recipe committed in the smoke script; run-2 never touched prod.
- Shutdown: SIGTERM → graceful ("Database engine disposed"); ports freed (8088 never touched); recorded pids dead; disposable DB dropped; `pg_database ~'lcajudge'` sweep = 0 rows; scratch dirs removed after evidence preservation.

---

## VERDICT: ✅ PASS — merge green-lit (with notes)

All 6 leader-defined jobs PASS. 0 introduced failures anywhere. 0 production-code changes (branch delta `bb052fce..d6e30d9d` byte-identical across all runs; the only commits added during the gate are tester-evidence-only).

## Scope Decision
Delta-scoped gate per leader's 7-job definition — NOT a full-repo run. Justified: the branch already carries the 15,950-test full gate at `6e679c16` plus re-gates (`8b083522`, `42cb9518`, `9d042c88`); this delta is 2 commits, judge-path only. Coverage: matrix (attestation surface) + pinned-neighborhood A/B (adjacent surfaces) + PG + live-boot + independent E2E.

## Findings summary
- 🟠 **MEDIUM — judge timeout vs proxy tail latency** (Job 3): live successes spanned 2.6–13.6s and 4/8 calls exceeded 15s; production per-attempt cap is `JUDGE_TIMEOUT_S=10s` (`min()` with request_timeout in `attestation_report_judge.py`) ⇒ frequent prod timeouts → conservative False → deny+nudge (= pre-feature BASE behavior; not a regression, but G2-class genuine reports will be nudged and the benefit silently degrades; deny-count escalation bounds the worst case). Operator decision: raise/tune `JUDGE_TIMEOUT_S` (consider env-tunable) or accept + document conservative posture; re-probe after any LLM-side latency fix.
- 🟠 **MEDIUM ops — `POSTGRES_URL` split-brain resurfaced with boot evidence** (Job 6): repositories factory reads only per-field envs — the documented open F-DR1-2 residual, NOT introduced here. Smoke script carries the corrected full-env recipe; worth a fast-follow fix or runbook note.
- 🟢 **INFO — deny-drive skip** (Job 6): legitimate escape-clause use; nudge content (header + mermaid incl. `ReportJudge`) independently proven in E2E (b) on BOTH the imported constant and the delivered copy.
- 🟢 **INFO — boot log carries the full judge state** on one line (`llm_judge_enabled` + `llm_judge_model` + flip hint) — ship-mode observability holds.

## ensure.md scoping (Core, scoped to delta)
- No regressions in changed packs (Critical): ✅ matrix 470/470 · neighborhood 0-introduced · PG 72/72 · E2E 10/10 · live probe real-path OK
- Deadlock/concurrency pack (Critical): not in scope (no locking/concurrency work in delta)
- Sync-DB-on-asyncio (Critical): not in scope (covered by concurrency pack; judge call is asyncio-wrapped)
- dev.sh `--timeout-graceful-shutdown 10` (Critical): ✅ PRESENT (`dev.sh:99` comment + ~:104 launch flag)
- **ensure.md Core scoped: 4/4 in-scope Critical PASS.** Release Gate not warranted (no architecture change; E2E surface covered by in-graph scripted seam + boot smoke — prior-gate precedent).

## Commits landed on branch (tester-evidence-only, unpushed — batch push with merge)
- `b42f7237` — `test: LCA judge live-LLM probe script + results`
- `fdec1e9f` — `test: LCA judge independent E2E scenarios (a)-(e)`
- `346b1842` — `test: LCA judge neighborhood A/B evidence (filelist + FAILED-ID diff)`
- `2a43904c` — `test: LCA judge boot smoke script`
All: `.agents/tester/RESULTS/*` paths only; `daemon/` + `tests/` byte-identical to `d6e30d9d` throughout (verified by every worker under the relaxed drift rule).

## Follow-ups (not blockers)
- Operator decision on `JUDGE_TIMEOUT_S` (🟠 above); consider env-tunable + re-probe.
- F-DR1-2 repositories-factory env unification (now has concrete boot evidence + corrected recipe).
- QUARANTINE family row for the messages-neighborhood pre-existing set ADDED this session (closes the prior gate's deferred follow-up).
- Evidence commits unpushed — push with the merge.

## Documentation Updated
- [x] `.agents/tester/RESULTS/2026-09-07-lca-judge-merge-gate.md` — this report
- [x] `.agents/tester/PACKS.md` — gate entry at top
- [x] `.agents/tester/QUARANTINE.md` — neighborhood pre-existing family row (closes prior-gate follow-up)
- [x] `.agents/tester/LESSONS/2026-09-07-lca-judge-gate-lessons.md` — shared-worktree gate protocol + boot DB-override recipe + stale-grep trap + probe methodology
- [ ] `.agents/tester/rules/ensure.md` — no changes (user-maintained, read-only)

## Overall verdict: ✅ **PASS — merge green-lit.**
