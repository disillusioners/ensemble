# unit-secret-exposure — INDEPENDENT FUNCTIONAL GATE VERIFICATION (2026-10-24)

**Lane:** `fix/unit-secret-exposure` @ FROZEN HEAD `b374963c3f689df6611f5c79c53fce5f6f60d4a6` (base `9fc95176b8dcf6c98c6dd55f346ebe4e4916cb7d`), worktree `/home/nea/ensemble-src-wt-unit-secret-exposure`.
**Spec:** `.agents/shared/planning/unit-secret-exposure/` (plan-overview ACs; dev-evidence = claims; MEASURED counts + git history authoritative — the adopt pack evolved 75 → 77 → 80 across folds).
**Change shape:** `scripts/` (adopt-unit.sh generator strip + deploy.sh/stage.sh `.env` 0600 fences) + `docs/runbooks/` + `tests/test_adopt_unit.sh` only. **Zero daemon code, zero payload-tree content** (launcher.sh untouched).

## VERDICT: ✅ **PASS-WITH-NOTES — GO** for the lane contract (merge `--no-ff` → bump → tag → STAGE-ONLY above v0.27.5, re-derived from `releases/` dirs + LOCAL tags at stage time; **NO PROMOTE** — user 3-factor nonce ceremony owns it).

All 8 gate deliverables PASS. Notes §8 are disclosures (one doc-amendment item for the developer lane, one pack-coverage gap closed out-of-band tonight, one estimate-correction) — **no defect found in the lane's shipped surface.**

---

## §1 Adopt pack — two-leg base-diff (worker `27b3dc76`)

| Leg | Command (identical inner invocation) | Result | Runtime |
|---|---|---|---|
| HEAD `b374963c3` | `cd <worktree> && timeout 300 bash tests/test_adopt_unit.sh` | **PASS=80 FAIL=0, rc=0** | 43 s |
| BASE `9fc95176b` | same, cwd = `git archive 9fc95176b` extraction + HEAD pack copied in | **PASS=65 FAIL=15, rc=1** (parent leg, expected FAIL) | 46 s |

- **Count parity:** 65+15 = 80 = HEAD total (same pack both legs). The dev's recorded base numbers (62/13 @ 75-assert pack) are **superseded** — fresh derivation at the 80-assert pack is **65/15**.
- **The 15 base REDs** (FAIL@parent proof, by family): zero-env content pins (2: no minted PORT / no minted POSTGRES_*) · masking-banner pins (2: no masked-section banner, zero-env section announced) · in-pack zero-env mutation baseline/restore legs (2) · static mode pins (5: deploy installed-copy `install -m 600`, deploy demo-guard generate-mv→chmod, stage marker rewrite-mv, stage marker append-mv, stage create path) · mode-pin mutation legs (4: stage create-site baseline/restore + deploy demo-guard baseline/restore).
- **Expected asymmetry (not a defect):** the in-pack mutation *RED* legs ("planted line DETECTED") PASS at base — the base generator mints on its own, so the detector fires both ways; only the GREEN baseline/restore legs are base-divergent.
- Evidence retained: `/tmp/adopt-verify.NzVlu5/` (`leg1_head.out`, `leg2_base.out`).

## §2 Out-of-band mutation legs (worker `781a8787`; throwaway `git archive` copies, never repo files)

Baseline control (unmutated HEAD copy): **80/0** — copy-run mechanics proven before any revert.

| Leg | Revert (in copy only) | Expected | **Measured** | Notes |
|---|---|---|---|---|
| A — P1-only (generator strip) | base `adopt-unit.sh` + base template | ≈6 | **6 RED** (74/6) | exactly the zero-env + masking-banner + zero-env-mutation-baseline families; zero off-surface |
| B — P5-only (mode fences) | base `deploy.sh` + base `stage.sh` | ≈7 | **9 RED** (71/9) | see NOTE-1: the ≈7 estimate assumed ONE in-pack mutation pair; the pack carries TWO (stage create-site + deploy demo-guard), each baseline+restore → 5 static + 4 mutation = 9, all on-surface |
| C — template-only | base template, HEAD generator | ≈29 | **29 RED** (51/29) | **torn-token guard fired, exit 78 confirmed**: `adopt-unit[adopt]: WARN: REFUSED: unsubstituted template token(s) survived generation — refusing to install a torn unit`; all 29 REDs are generation-content asserts downstream of the refusal |
| D — deploy-site neuter | `install -m 600` → 644 at deploy.sh:328 (the only `install -m 600` in the file), copy only | RED → GREEN | **1 RED** (79/1: `mode pin: deploy.sh installed-copy install -m 600 (0600-at-creation)`) → restore → **80/0** | cmp-verified restore |

**Partition proof:** Leg A (6) + Leg B (9) = **15 = exactly the full-base RED set** of §1 — the isolated reverts partition the parent failures with zero overlap and zero off-surface spillover. Every leg cmp-verified base/HEAD on each reverted file pre-run; worktree porcelain = 0 pre/mid/post.

## §3 Deferred packs — base-vs-HEAD, net-new gate (identical commands both sides)

### §3.1 `tests/test_release_journal.sh` (worker `e42ccf27`) — **NET-NEW = ∅**

| Leg | Counts | rc | Runtime |
|---|---|---|---|
| HEAD | **338 passed / 5 failed / 2 skipped** | 1 (designed) | 237 s |
| BASE `9fc95176b` | **338 passed / 5 failed / 2 skipped** | 1 | 227 s |

Failure lists **byte-identical** (both directions of the set difference empty; `comm`-verified): `f2 gate: guard refusal fires, not the F2 one` · `cap boundary: count=3 in-window reads 3` · `count=2 in-window reads 2` · `count_rollback after stale window resets then counts → 1` · `9 evicts the true-oldest (2020 ISO, not pinned)` — all pre-existing ledger/cycle-count reds, no adopt/stage/deploy surface. Skips identical both sides (no-uv-host; real-systemd-mutation named fence). Command identity: `timeout 850 bash tests/test_release_journal.sh` both legs (dev-recorded wrapper; actual runtimes 227–237 s, far under). 3×-isolation not triggered (no differing names).

### §3.2 `tests/test_stage_freshness_guard.sh` (worker `d0057396`) — **NET-NEW = ∅ — PRIORITY GATE PASSED**

| Leg | Counts | rc | Runtime |
|---|---|---|---|
| HEAD | **110 passed / 8 failed** | 8 | 50 s |
| BASE `9fc95176b` | **110 passed / 8 failed** | 8 | 45 s |

Same 8 names both sides (override/refusal-journaling family — `(vi)` ×4, `(vii-c)`, `(viii-f)`, `(x)` ×2 — pre-existing, unrelated to `.env` mode). **Zero chmod/umask/mode/marker-rewrite reds on either side.** Pack blob identical both sides (`ede95a57…`).

**The HEAD-only stage.sh chmod+umask fence demonstrably executes in-harness** (instrumented diagnostic copies, verdict legs pristine): 9 successful stage.sh runs across 6 legs, 18/18 observations at HEAD = **.env mode 600, umask 0077** (BASE: mode 644, umask 0022) — the lane's intended delta `644 → 600` proven on the create path, with pack results unchanged (110/8) in the instrumented runs.

**NOTE-2 (coverage gap, closed out-of-band tonight):** every harness leg stages into a *virgin* sandbox install → the pack exercises **only the CREATE path**; the marker-**REWRITE** and marker-**APPEND** paths are never reached by the pack. Closed tonight by a standalone block-level probe (the `ENV_FILE=…marker staged` span extracted verbatim from each tree's stage.sh, executed against three `.env` pre-states): all three paths at HEAD = rc 0, mode 644→**600** (create: absent→600), marker written ×1, `POSTGRES_PASSWORD` line preserved, no `.tmp` residue, umask restored 0022→0022; BASE = 644→644 throughout. A durable regression pin for rewrite/append remains a future test-pack addition (not a lane defect — the lane's code is verified working on all three paths).

## §4 Static doc gates (worker `b0724b16`, read-only)

| Check | Result |
|---|---|
| **AC3.6 stale-prose sweep** (needles `carries secret env\|secret env value masked\|POSTGRES_\* values\|secret env\|secret masking\|substituted User/port/PG` over `scripts/ scripts/systemd/ docs/runbooks/ tests/`) | **PASS — exactly 4 hits, all adjudicated, zero unexplained**: (a) `install-dir-protection-layer1-arm.md:33` our own negation; (b) `tests/test_adopt_unit.sh:417` the pack's MOVED+INVERTED negative needle (inside `assert_not_contains` — current line **:417**, drifted as predicted); (c) `test_module_lane_isolation.py:464` webhook HMAC (unrelated); (d) `test_mcp_server_crud.py:1239` MCP config (unrelated) |
| **D9 `EnvironmentFile=` gate** | **PASS-with-disclosure**: `scripts/` + `launcher.sh` = **0 hits** (mechanism surface clean). Non-docs repo-wide = 5 hits = the adjudicated A8 precedent (`test/packs/hard_reboot_drill_e2e_test.sh:707`) **+ the lane's own 4 new A2 D9-pin lines** in `test_adopt_unit.sh` (:377/:384 comments, :390-391 the `assert_not_contains` pin + PassEnvironment sibling) — same inverted-needle class as AC3.6(b). `git diff` additions fully classified: **zero mechanism introductions** (the one docs/ hit is a paired negation rewrite). See NOTE-3 |
| **SC3 token gate** (`__ENV_LINES__` in `scripts/ scripts/systemd/ docs/runbooks/`) | **PASS — 0 hits** |
| **Named-skips byte-untouched** (`tests/test_supervision_e2e.sh`, `test/packs/hard_reboot_drill_e2e_test.sh`) | **PASS — diff empty; NOT RUN tonight** (real user-bus units / `sudo systemctl stop` — forbidden by the no-live-mutation constraint; §19 named-skip precedent) |
| **Zero edits outside the adopt pack** (`git diff --stat base..HEAD -- tests/ test/`) | **PASS — exactly 1 file: `tests/test_adopt_unit.sh`** (+251/−21) |
| **WEIGH — adopt-unit.sh:443 silent chmod** (leader default = keep) | **KEEP — no concrete hazard**: (1) failure delta is ∅ — the silent chmod runs after additive staging + read-back verify; on failure the file keeps its *pre-adoption* mode (the identical pre-existing exposure — a loud refusal could not reduce it); (2) exploitability requires traversal the host already blocks (`/home/nea` 750, `nea` group has **no members** — exclusion delta ∅ verified on-host); (3) the D-Bus surface is closed by the generator strip, not the mode; caller class is a polkit-gated operator. Live `.env` is currently 0644 — remediation stays the D10 user-decision item |

## §5 Scratch-install boot smoke — T4.6 fixture (worker `08c87851`) — **PASS on all 8 gated items**

Route A used: `systemd-run --user` transient (`ens-bootsmoke-097d0d.service`), **zero `--setenv`** (user-manager env pre-verified clean of `PORT`/`POSTGRES_*`/`HOST`/`ENSEMBLE_*`) → a *true zero-env unit*. Fences: mktemp `$WORK` + `FAKE_HOME` + escape guard; scratch port 10995 (10000-19999 range, verified free); throwaway PG16 role+DB on 127.0.0.1:5432 (`sudo -u postgres`, DUMMY credentials, DROP-verified); per-variable POSTGRES_* scrub (never `env -u POSTGRES_*`); `POSTGRES_DB != ensemble_prod` asserted; binary = newest staged `ensemble-prod` **copied read-only into the scratch releases/ tree** (cn cc3d107b — install-root derivation lands inside scratch); `ENSEMBLE_SELF_ENV=sandbox` explicit in the scratch `.env`.

1. **Unit content gate (fixed generator, fenced DRY_RUN preview): PASS** — zero `Environment=PORT=`, zero `Environment=POSTGRES_`, zero `__ENV_LINES__`; comment-stripped directive count = **1**, exactly `Environment=MALLOC_ARENA_MAX=2`; zero systemctl calls (stub), 0 `__TOKEN__` survivors; substitutions rode through (ExecStart absolute, WorkingDirectory `%h/…`, User).
2. **Boot + health: PASS** — `livez` 200 (`version 0.27.5`), `readyz` 200 (database/queue_freshness/services/checkpoint_saver all true, draining false); **time-to-healthy 18 s**; PG checkpointer on the throwaway DB; loopback only.
3. **Process-env provenance: PASS (decisive pair)** — launcher (unit MainPID) `/proc/<pid>/environ`: **0** matches for `PORT`/`POSTGRES_*`/`HOST` (the unit supplied NOTHING); daemon (port owner) environ: `PORT`, `HOST`, all 5 `POSTGRES_*` keys (DB/User = throwaway names, PASSWORD = DUMMY matched exactly), `ENSEMBLE_SELF_ENV=sandbox`, `MALLOC_ARENA_MAX=2` — exactly the scratch `.env`. **Zero `OPENAI_*` keys** → no LLM credentials held; real-LLM contact structurally impossible.
4. **Missing-`.env` leg: PASS** — launcher WARN "env file not found … continuing with inherited environment" → daemon `PostgreSQL unreachable at boot … fe_sendauth: no password supplied` → **`exiting 75 EX_TEMPFAIL; launcher will retry with capped backoff`** (ladder 5s→10s→20s→40s observed, `NOTIFY[tempfail-75]`, unit stayed active; bounded 90 s). See NOTE-5 (75, not 78 — recorded as observed).
5. **Unreadable-`.env` leg: PASS** — `launcher.sh: line 146: … Permission denied` (the `load_env_file` redirect) → **zero exports** (no `PORT`/`POSTGRES_*` in the unit env) → same boot-block 75 → retry 60 s cap. Extra TEST-NET leg (`192.0.2.10`): `ConnectionTimeout` → 75 → 60 s cap (tempfail classification independently confirmed).
6. **Cleanup: all six verified** — transient units stopped+reset-failed (4), `list-units 'ens-bootsmoke*'` = 0; throwaway DB+role dropped (pg_database/pg_roles counts 0/0); `$WORK` removed (escape-guarded; no `/tmp/boot-smoke.*`); port 10995 free, :8088/:8079 never bound; live/demo PIDs unchanged (9797→3523329, 7979→1147), no file under `/home/nea/agents-ensemble` mtime-newer than run start; worktree porcelain 0, HEAD `b374963c3` unchanged.

Disclosed deviations (all benign): health poll cap 120 s→180 s (actual 18 s); `ensemble.json` replicated in installed-norm *shape* with scratch values (empty password — exactly the live norm's `password: ""` shape, which is what makes the missing-`.env` leg boot-block); `WorkingDirectory=%h/…` preview is a FAKE_HOME fixture artifact (DRY_RUN only, nothing installed); pre-existing `/tmp/boot-smoke-scratch-path` file (2026-10-21, prior lane) left in place.

## §6 Lone-`Environment=` reset validation (worker `5f5c1e72`) — **GATE PASS** (systemd 255, user scope, 8 throwaway probes, zero live contact)

- **Gate:** drop-in with `[Service]` header + lone bare `Environment=` → `systemctl --user show <probe> -p Environment` returns exactly `Environment=` (empty list, **no KEY=VALUE entries**); persisted across a second `daemon-reload`. Controls: `DropInPaths=` listed, `[Unit] Description=` override applied, appended `Environment=PROBE_C=three` visible — the drop-in demonstrably reached the unit.
- **Form corrections (load-bearing for the morning):**
  1. **`[Service]` header REQUIRED** — a headerless lone `Environment=` line is *silently ignored* (list unchanged).
  2. **Only the bare form resets** — `Environment=""` (quoted empty) is a NO-OP.
  3. **Full-list reset clears non-secrets too** (`PORT`/`MALLOC_ARENA_MAX` vanish); retain-recipe = reset + re-add in the SAME drop-in (`Environment=` then `Environment=PORT=…`) — verified `Environment=PORT=9797`-shaped result.
  4. **Drop-in must live in the load-path dir** (`/etc/systemd/system/<unit>.service.d/` for the live case); drop-ins beside a *linked* unit file are ignored (observed; the canonical path is what the runbook prescribes anyway).
- **Wrong-form CONFIRMED:** per-variable `Environment=PROBE_A=` → key STAYS listed present-but-empty (`Environment=PROBE_A= PROBE_B=two`) — the runbook's "WRONG, must not appear" claim is empirically true.
- **Runbook conformance (worker `b0724b16` follow-up):** `docs/runbooks/systemd-adoption.md:571-584` prescribes the drop-in WITH the `[Service]` header (:574) above the lone `Environment=` (:583); the step-1 scratch probe drop-in conforms (:556-559); the runbook's own scratch gate would catch a stripped header before live contact. **No latent runbook defect.** MALLOC_ARENA_MAX note (ii) citation verified exact (`launcher.sh:1658-1659`).
- **Residuals for the morning decision:** (i) evidence is user-scope; the live system-scope application (root, system-bus daemon-reload) remains unvalidated-by-construction — belongs to the morning ceremony; (ii) the drop-in does NOT remove plaintext from the unit *file* — but `/etc/systemd/system/ensemble-main.service` is already 0600 root:root (non-root cannot read it); the D-Bus property is the live leak vector and the drop-in closes it; (iii) `EnvironmentFile=` values never appear in the D-Bus `Environment` property (observed) — informative for future design, NOT a change to the D9 invariant.

## §7 Named-skips

`tests/test_supervision_e2e.sh` and `test/packs/hard_reboot_drill_e2e_test.sh`: **byte-untouched vs base (diff empty) and NOT RUN** — real user-manager unit writes / `sudo systemctl stop` are outside tonight's no-live-mutation constraint. Their `Environment=`/`EnvironmentFile=` hits are their own fixtures/acknowledged A8 precedent (intact). Disclosed, not silently skipped.

## §8 Notes & disclosures (non-blocking)

- **NOTE-1 (estimate correction, not a defect):** P5-only revert measured **9** RED vs the ≈7 expectation — the estimate assumed one in-pack mutation pair; the final-fold pack carries TWO (stage create-site + deploy demo-guard), each with baseline+restore legs. All 9 on-surface; 6+9=15 partitions the full-base RED set exactly.
- **NOTE-2 (pack coverage gap, closed tonight):** the stage-freshness harness covers only the CREATE path (virgin installs); rewrite/append verified working via standalone block-probe (§3.2). Durable pin = future test-pack addition.
- **NOTE-3 (doc amendment for the developer lane):** `dev-evidence.md:259` instructs "confirm the only non-docs/ `EnvironmentFile=` hit repo-wide is `…:707`" — now internally inconsistent with the lane's OWN A2 pins (verbatim re-run yields **5** hits, not 1). The gate's intent (zero new `EnvironmentFile=` mechanism) is satisfied; the doc's literal expectation needs the A2-pin whitelist added. Routed to the developer lane via the leader (read-only for the tester; frozen tree untouched).
- **NOTE-4 (base counts superseded):** adopt-pack base = **65/15** at the 80-assert pack (dev's 62/13 was at the 75-assert pack). HEAD = 80/0 confirms the 77→80 fold count.
- **NOTE-5 (boot-env taxonomy precision):** missing-`.env`/empty-password lands on **75** (`OperationalError` "no password supplied"), not 78 — 78 remains correct for genuine auth *refusal* (SQLSTATE 28P01). Recorded as observed; a one-line precision addition to the runbook's A5 taxonomy is optional morning polish.
- **NOTE-6 (operational):** the mutation-leg worker's shell carried LIVE-poisoned ambient env (`ENSEMBLE_SELF_ENV=live`, `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2` — inherited from the live daemon context); scrubbed per-variable on every run with zero live contact. Per-variable scrub discipline is load-bearing for all workers on this host.
- **Timeouts:** journal/stage legs used the dev-recorded wrappers (`timeout 850`/`timeout 800`) for command identity with the dev's base evidence; actual runtimes 45–237 s, all far under both the wrappers and the 5-min pack cap.

## §9 Morning-report items (routed per plan D10; nothing new blocking)

1. **Live remediation (user decision, §5a runbook)** — validated tonight: form conforms (§6), gate semantics confirmed; runbook's own step-1 scratch gate stands. Live `.env` still 0644; live unit still carries the six minted lines until remediation.
2. **NOTE-3** doc amendment (`dev-evidence.md:259` A2-pin whitelist) — developer lane.
3. **NOTE-2** durable rewrite/append regression pin — future test-pack addition.
4. **NOTE-5** optional A5-taxonomy precision line (75 for no-password-supplied; 78 for 28P01 refusal).
5. Standing D10 items unchanged (`.env.bak-2026-10-20T21:30:40Z` 644, existing 644 `.env.prod.demo`, deferred key rotation, silent-SQLite refusal-hardening candidate, executor `--setenv` prefix fence — cn W3/D10.11).
6. W9 stage-time hazard stands: the REMOTE tag `v0.27.5` mis-points (→ `9fc95176b`); re-derive the stage version from `releases/` dirs + LOCAL tags only.
7. **W2 live-boundary disclosure** (carried per plan §8): P5's stage-time chmod touches the LIVE install `.env` at real stage time (mode-only, ∅ exclusion delta; `nea`-uid staging precondition N3 documented).

## §10 Worker ledger

| Worker | Instance | Skill | Deliverable | Result |
|---|---|---|---|---|
| adopt-head-base | `27b3dc76-e845-46d5-ad6d-5a17818b1ffe` | test-pack-execution | §1 | PASS |
| adopt-mutation-legs | `781a8787-edbf-4f47-b538-1f480dea457f` | test-pack-execution | §2 | PASS |
| release-journal-diff | `e42ccf27-63e5-4e4b-bdf6-a750b63d57d6` | test-pack-execution | §3.1 | PASS |
| stage-freshness-diff | `d0057396-f179-495e-b4ae-a263a828c1f3` | test-pack-execution | §3.2 | PASS |
| static-doc-gates (+1 revival) | `b0724b16-6678-4de5-95ad-b2a1111706cf` | none (infra) | §4 + §6 conformance | PASS |
| lone-env-probe | `5f5c1e72-6b7a-4a80-95cd-34673b866c72` | none (infra) | §6 | PASS |
| bootsmoke-scratch-install | `08c87851-a3d2-4334-8960-281ea90da1fd` | e2e-test | §5 | PASS |

Fence attestation (all workers): zero live contact (no systemctl system-bus mutations; no writes under `/home/nea/agents-ensemble`; live/demo PIDs unchanged; ports 8088/9797/7979/8079 untouched); worktree READ-ONLY throughout (porcelain 0, HEAD `b374963c3` unchanged, no commits); per-variable POSTGRES_* scrubs; secrets hygiene (key names + lengths, DUMMYs only in throwaway contexts). Persistent evidence: `/tmp/adopt-verify.NzVlu5/`, `/tmp/sf-guard-verify/` (all other scratch removed and verified gone).

**Overall: Unit/pack gates ✅ · Deferred packs net-new ∅ ✅ · Static gates ✅ · Boot smoke ✅ · Lone-env validation ✅ · Named-skips confirmed ✅ — PASS-WITH-NOTES, GO.**
