# ENSEMBLE_SELF_ENV Auto-Resolution — Independent Functional Verification

**Commission**: ENSEMBLE_SELF_ENV auto-resolution feature, branch `feature/env-auto-resolution` @ `ba295fec` (base `b024bb09` = v0.13.10), worktree `/home/nea/ensemble-worktrees/env-autodetect`. Review gates already passed (deep-review + confirm-pass, 0 blocking). Tester mandate: independent functional verification with mock scrutiny.

**Date**: 2026-09-22 · **Tester verdict: ✅ GATES-PASS** (0 branch-caused failures; all mandated scenarios + hard invariants PROVEN; mock audit found no claim-invalidating divergence; 3 non-blocking findings).

**Workers (5 dispatched, 5 reported, 0 re-dispatches)**:
| Node | Worker | Instance ID | Skill | Result |
|---|---|---|---|---|
| W-A mock audit | env-resolve-mock-audit | `53459e35-53e7-456f-9e6a-ba3b9e48bf8c` | — (infra) | ✅ no invalidating divergence; frozen-binary path exercised for real |
| W-B scenario matrix | env-resolve-scenario-matrix | `d927525d-48ef-4dbf-ba5e-b8410869953e` | — (infra) | ✅ 45/45 PASS incl. hard invariants |
| W-C tools file | env-resolve-reg-tools-file | `7ac682db-e5d6-4f9b-b30c-214ca346157e` | test-pack-execution | ✅ PASS 144/144 (exact match vs claim) |
| W-D tools dir | env-resolve-reg-tools-dir | `4161b839-e66e-41ad-af8c-6988ead85856` | test-pack-execution | ✅ PASS 2836P/0F/5S/5-des |
| W-E shell stage lane | env-resolve-reg-shell-stage | `b6547d40-8fba-4b46-b39d-a7669864dcb9` | test-pack-execution | ⚠️ 225P/43F — ALL 43 pre-existing host-arch (adjudicated, quarantined) |

All runs on verified SHA `ba295fecace0e5176e42ef898764ede1fe760951`. Safety held throughout: no daemon boot, no drills/staging/promote, live (9797) / demo (7979) untouched, mandatory `env -u` POSTGRES_*+DATABASE_URL scrub on every invocation, verify-only (zero repo edits/commits by workers).

---

## 1. Scenario Matrix (W-B — in-process, real `daemon.tools.upgrade_tools` imported, only paths/env/seams monkeypatched) — 45/45 PASS

Driver `/tmp/envres-matrix/driver.py`, log `/tmp/envres-matrix/final_run.log`.

| Scenario | Cases | Verdict | Evidence highlights |
|---|---|---|---|
| S1 explicit-marker-wins | 4/4 ✅ | `marker=live` over demo shape → `live`/`explicit`; `marker=dev` over live shape → `dev`/`explicit`. Case-sensitivity confirmed: `DEV`, `Live` → `None` + source `ignored-garbage` + WARN (fall through) |
| S2 auto-derive per shape | 5/5 ✅ | demo→`demo`, live→`live`, dev (POSTGRES_DB=ensemble_dev)→`dev`, frozen-binary+releases/ temp tree→`sandbox`, no-signal→`None`/`auto-unresolved` — all source `auto` |
| S3 false-values ⇒ fail-closed unresolved | 14/14 ✅ | `{0,false,FALSE,False,No,no,off,OFF," false ","FALSE "," off","\tFalse\t"}` → `None`/`opted-out`. **No auto-derive under opt-out**: live shape on disk + `marker=false` → still `None`. **Tool layer**: `system_upgrade` + `system_restart` refuse with token `env-marker-absent` on BOTH opt-out and marker-absent-unresolvable arms (token identical; hint text branches `opted-out` vs `auto-unresolved` — see §5 nuance) |
| S4 HARD INVARIANT — auto-resolved LIVE keeps 3-factor gate, refuses restart | 5/5 ✅ | `system_restart` → `live-restart-refused`. Factor drops: `user_confirmed=False` → `user-confirmation-missing`; no user-origin window → `user-confirmation-missing` (whitelisted user-origin body); invalid nonce → `nonce-mismatch`. Explicit-marker live arm: identical refusals (no auto-path bypass). Fully-sandboxed all-3-factors arm: **ARMS successfully** (`UPGRADE ARMED — run_id=… env=live`), `spawn_calls=0` (D2/D3 arm→return→poll seam preserved), `set_pending_system_execution` called — gate is reachable ONLY with all factors; auto-resolution changes identity DETECTION, never the gate |
| S5 release_info WITHOUT marker | 1/1 ✅ | Output: `RELEASE INFO — env=live`, `env-marker: ENSEMBLE_SELF_ENV ABSENT — auto-derived self-env=live (multi-signal resolution: …) All gates apply normally.` Resolved env surfaced, not blank/unresolved |
| S6 garbage marker | 14/14 ✅ | 7 garbage shapes (`banana`,`DEV`,`Live`,`prod`,`flase`,`production`,`demo;live`) → WARN + `ignored-garbage` + auto-derive fall-through (→`live` with live shape; →`None` clean). Empty/whitespace markers (`''`,`' '`,`'\t'`,`'\n'`) = ABSENT (trim→None, source `auto`), not garbage — spec-consistent |

**Contract deviations: NONE observed.**

## 2. Mock-Quality Audit (W-A — TrueAuto rule) — no claim-invalidating divergence

Full table in worker report; 14 dimensions audited against REAL host layouts (read-only).

**Key results**:
- **Frozen-binary sandbox path exercised FOR REAL (first time)** — reviewer carry-forward ("only statically inferred") closed. A1 uncanonical dir + frozen binary → `sandbox`; A2 canonical live dir + frozen binary → `live` (install-dir guard holds). Both match unit-test claims exactly. Artifacts: `/tmp/envres-audit/audit.py` + trees.
- **Demo-misresolution concern RESOLVED**: real daemons are ELF (`file` → "ELF 64-bit"), `sys.frozen=False` in production ⇒ frozen-binary rule never fires on this host; demo resolves via explicit marker (`ENSEMBLE_SELF_ENV=demo` in demo `.env`) or, marker-less, via install-dir + POSTGRES_DB=ensemble_demo cross-check → `demo`. No misresolution.
- **Real-install resolution table (read-only inspection)**: LIVE `~/agents-ensemble` (.env: no marker, POSTGRES_DB=ensemble_prod, no releases/ — pre-P2.1) → auto-derive `live` — the standing "env=unresolved self-ID gap" critical note on port 9797 closes at next deploy of this feature. DEMO → `demo` (explicit marker). DEV/sandbox `~/agents-ensemble-dev` → `sandbox` (explicit marker; POSTGRES_DB=ensemble_sandbox).
- **Marker source matches**: code reads `os.environ["ENSEMBLE_SELF_ENV"]`; launcher exports from `INSTALL_DIR/.env`; tests patch the same source. `_read_env_value` parser over-covers real `.env` shapes (plain KEY=VALUE) — good.
- **Resolver is read-only**: 0 files added/removed under scratch homes across all resolutions; touches only env gets + Path reads.

**Findings (non-blocking)**:
- 🟠 **D2 — garbage-marker WARN not deduped**: 10 calls → 10 identical WARNINGs (comment "log once per resolution" = per call, not per process). Log-spam risk for a typo'd marker in a real install; dormant today (no real install has a garbage marker). Suggest follow-up patch: module-level `_WARNED_GARBAGE_MARKERS` set / time-based soft-dedupe. NOT applied (verify-only gate).
- 🟡 **D1 — dev-install divergence**: real dev install uses POSTGRES_DB=`ensemble_sandbox` (+ explicit marker `sandbox`), test models `ensemble_dev` (the canonical compose default). No claim invalidation: explicit marker wins at the real install; the modeled dev shape covers the real `uv run python -m daemon` case.
- 🟡 **D3 — frozen-binary path dead on this host** (no PyInstaller processes): exercised only in tests + this audit's temp trees; live for future frozen builds.

## 3. Regression

| Pack | Scope | Result | Runtime |
|---|---|---|---|
| `env_resolution_tools_file_unit_test` (ad-hoc) | `tests/unit/tools/test_upgrade_tools.py` exact | **PASS — 144 passed / 0 failed / 0 skipped** — EXACT match vs developer claim | 7.07s |
| `env_resolution_tools_dir_unit_test` (ad-hoc) | `tests/unit/tools/` full dir | **PASS — 2836 passed / 0 failed** / 5 skipped (in-source env-conditional: PG-only SQL ×2, ENSEMBLE_TEST_PG_URL unset ×2, Windows-only ×1 — benign) / 5 deselected (exact `TestAccessMemoryArchive` quarantined family per QUARANTINE.md; 6th family member not quarantined, ran+passed) | 179.10s |
| `release_journal_unit_test` (registered `test/packs/release_journal_unit_test.sh`) | scripts/upgrade/ pipeline (stage.sh lane) | **225 passed / 43 failed — ALL 43 pre-existing host-arch** (see below). Log `/tmp/pack-run-envautodetect.log` | 93s |

**43-failure adjudication (PRE-EXISTING, 0 branch-caused)**: root cause = GNU `date` lacks BSD `-j`/`-v` (`date: invalid option -- 'j'` saturates the log before every cluster) at `lib.sh:86 _iso_to_epoch`, `lib.sh:709 cooldown arm`, retention eviction + `tests/test_release_journal.sh:460,735-736,936,988,1033` — exactly the standing "P2.1 GNU debt, 3 sites" critical note. Proof: `git diff b024bb09..ba295fec --stat` **EMPTY** over every failing file (byte-identical to base); branch's stage.sh delta is comment-only (24 lines, 0 executable lines). First Linux-host run of this pack (243→271/271 green history = BSD host). Consolidated QUARANTINE.md row added 2026-09-22 (this worktree) so future Linux gates don't re-litigate. All Linux-runnable sections (journal atomicity, torn-write, manifest, version-smoke, live-guard, no-.env-in-release, idempotent re-stage) PASS.

**stage.sh change summary (W-E)**: comment-only documentation of the supersession — marker optional, explicit opt-out preserves fail-closed, marker-absent auto-derives; stage still stages the marker (seeds it onto the marker-less LIVE 9797 install at next stage). Zero executable lines ⇒ no functional validation owed beyond the pack above.

## 4. ensure.md (scoped by blast radius — actor-tool layer, NOT execution lane)

| Requirement | Status | Evidence |
|---|---|---|
| Core #1 no regressions in changed packs | ✅ | tools file 144/144, tools dir 2836/0F; release_journal 43F all pre-existing-quarantined |
| Core #4 dev.sh `--timeout-graceful-shutdown 10` | ✅ | `dev.sh:102` (static grep, W-E) |
| Core #2/#3 concurrency/async-DB packs | N/A (scope) | Pure env/path resolution logic; no async or DB-call changes in diff — out of blast radius |
| Boot gate | N/A (scope) | Task context: actor-tool layer ≠ job/task/queue execution lane; not mandated |
| Release Gate | N/A | Not a big/critical/architecture change |

No contradictions with ensure.md methods this gate. No `pytest -x` used anywhere; all packs dual-layer timeout.

## 5. Contradictions vs developer/reviewer claims

**None.**
- "144 passed" — exact match (W-C).
- Reviewer carry-forward "frozen-binary sandbox resolution only statically inferred" — was accurate; now closed by real in-process exercise (W-A A1/A2 + W-B S2-sandbox).
- One NUANCE (not a contradiction): S3 opt-out vs marker-absent refusals share the identical contract token `env-marker-absent`; the hint TEXT branches (`opted-out` vs `auto-unresolved`) via `_env_marker_absent_hint`. Fail-closed behavior is identical; the branch is an intentional affordance. Commission's "verbatim" holds at token/state level.

## 6. Follow-ups (non-blocking, for leader routing)

1. 🟠 Garbage-marker WARN dedupe (D2) — small follow-up patch; do NOT fold into this branch silently (verify-only gate honored).
2. 🟡 P2.1 GNU-debt uname-dispatch port — standing separate commission; now ALSO the sole blocker for `release_journal` pack on Linux hosts (43 quarantined nodes).
3. 🟢 Optional test enrichment: model the real dev-install shape (`ensemble_sandbox` DB + explicit marker) as an additional case; add a WARN-frequency annotation if D2 is patched.

## 7. Documentation updated (this session, worktree `.agents/tester/`, docs uncommitted per verify-gate precedent)

- RESULTS/2026-09-22-env-auto-resolution-verification.md (this file)
- QUARANTINE.md — new consolidated row: release_journal GNU/BSD date-debt family (43 nodes, host-arch, Linux-visible)
- PACKS.md — ad-hoc pack registrations + gate outcome entry
- LESSONS/2026-09-22-env-auto-resolution-gate-lessons.md

## 8. Side effects

NONE. No daemon restart/boot, no staging/promote/tag, no live(9797)/demo(9799?→7979) contact (W-A verified daemons via read-only livez; W-B patched spawn seam to raise; `spawn_calls=0` asserted), no POSTGRES_* leakage (11-var scrub on every invocation), zero worker edits/commits/pushes. Scratch artifacts in `/tmp/envres-audit/`, `/tmp/envres-matrix/`, `/tmp/pack-run-envautodetect.log` only.

## Overall

- Unit regression: ✅ PASS (144/144 exact + 2836/0F)
- Shell/stage lane: ✅ PASS-for-branch (43F all pre-existing host-arch, quarantined)
- Scenario matrix + hard invariants: ✅ 45/45 PASS
- Mock audit: ✅ no invalidating divergence; frozen-path gap closed
- ensure.md (scoped): ✅
- **Testing verdict: GATES-PASS — merge-ready from the testing perspective, with the 3 non-blocking follow-ups above.**

---

# ADDENDUM — Re-gate on INTEGRATED tree (2026-09-22, second session)

**Trigger**: Q&A-channel merge landed on origin/latest (`f583cd7b`, 20 commits: event models UNION, POST /jobs/{work_id}/answer, mid_flight_report tool, wedge guard) and was merged into `feature/env-auto-resolution`. Integrated tree @ **`bdc340c8`** (`bdc340c85b2661ed56cc9b8b007ffcb4f02009c9` = merge of `5e1aa0b1` [our 5 commits incl. tester artifacts] + `f583cd7b`). Code overlap vs Q&A side: NONE (doc overlaps CHANGELOG/PACKS.md resolved by proven-union). Verify-only, no fixes, no commits.

**Re-gate verdict: ✅ GATES-PASS for the integrated tree — zero drift vs the pre-merge gate on every re-run item.**

| Item | Worker / Instance | Result | vs Prior run |
|---|---|---|---|
| Import sanity | R1 `db256988-132b-454d-b900-76d7cc969502` | ✅ `import daemon` → `/home/nea/ensemble-worktrees/env-autodetect/daemon/__init__.py` (inside worktree; editable-install trap avoided) | n/a (new check) |
| Our lane pack: `tests/unit/tools/test_upgrade_tools.py` | R1 | ✅ **144 passed / 0 failed / 0 skipped** in 8.24s, exit 0 | EXACT match (144P/0F @ ba295fec, 7.07s; +1.17s = normal variance) |
| CORE spot-check C1 — auto-resolved LIVE keeps 3-factor gate + restart refusal (5 sub-checks: identity `live`/`auto`; `live-restart-refused`; `user-confirmation-missing` ×2 factor-drop legs; `nonce-mismatch`) | R2 `936e17ea-0bec-470c-ad34-79988eb161c4` | ✅ 5/5 PASS | IDENTICAL tokens/bodies vs prior S4 |
| CORE spot-check C2 — false marker ⇒ fail-closed (7 sub-checks: resolution `None`/`opted-out` with live shape on disk ×3 variants; tool-layer `env-marker-absent` on system_upgrade + system_restart ×3 variants) | R2 | ✅ 7/7 PASS | IDENTICAL vs prior S3 |
| CORE spot-check C3 — release_info marker-less surfaces `env=live` + `ENSEMBLE_SELF_ENV ABSENT — auto-derived` line | R2 | ✅ 1/1 PASS | IDENTICAL output shape vs prior S5 |
| Q&A good-neighbor slice: `test_midflight_qa.py` + `test_answer_gate_resume_chain.py` + `test_answer_resume_real_chain.py` | R3 `c013f967-bd0e-40aa-bc87-b6681c1c8a20` | ✅ **54/54** (42+10+2, all files present, no fallback) in 7.00s, exit 0 | Zero deviation vs latest post-merge gate (54/54 @ ca4ab125) — merge did NOT break the Q&A side |

**Method notes (R2)**: prior driver `/tmp/envres-matrix/driver.py` reused as module + trimmed runner `/tmp/envres-regate/runner_regate.py`; per-check wiped scratch homes (`/tmp/envres-regate/{c1,c2,c3}/`); `spawn_executor` patched to RAISE across C1 — never fired (refusals resolve upstream of the spawn seam). Full evidence at `/tmp/envres-regate/`.

**Cross-lane intersection**: `daemon.tools.upgrade_tools` + `daemon.tools.upgrade_journal` import cleanly on the merged tree; all harness seams intact; no behavioral change in the upgrade-tools lane attributable to the 20 Q&A commits; no unexpected warnings / new refusal tokens / changed output text.

**Scope note**: per commission, the prior 45-scenario matrix was NOT fully re-run (144 + C1/C2/C3 passing satisfies the re-gate). The prior S4d fully-sandboxed all-3-factors ARM sub-case was likewise not re-run — its gate-reachability proof stands from the pre-merge session (@ ba295fec, code unchanged in our lane by the merge).

**Side effects**: NONE — no daemon boot, no staging/promote/tag, live(9797)/demo(7979) untouched, POSTGRES_*/DATABASE_URL/PG* scrubbed on every invocation (×12 `env -u`), zero worker edits/commits/pushes. Clean worktree apart from this uncommitted documentation (giter to commit).

**Integrated-tree verdict: GATES-PASS.**

