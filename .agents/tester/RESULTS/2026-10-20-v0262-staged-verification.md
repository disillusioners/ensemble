# Staged Verification — v0.26.2 (hide ask_questions from non-root instances)

**Commission:** hide the `ask_questions` tool from non-root instances (root = user-facing chat-source attached)
**Round:** Phase 9 independent staged verification — last gate before the commission's final report to the user
**Date:** 2026-10-20 (UTC, ~22:50–23:10)
**Staged artifact:** `/home/nea/agents-ensemble/releases/v0.26.2`
**Source commit:** `463583800c6062c4199c6f2402ee806cbd01b0e4` (== `refs/tags/v0.26.2^{commit}` == `refs/heads/latest` == `origin/latest` at verify time; tag LOCAL-ONLY, unpushed; no tip drift — the tag commit IS the tip)
**Staging worktree:** `/home/nea/ensemble-src-wt-stage-v0262` (detached @ `463583800c…`, porcelain clean, BOTH provenance sidecars kept)
**Mode:** read-only w.r.t. everything except sanctioned scratch (boot smoke) and these RESULTS/PACKS docs. 4 parallel worker lanes, 0 re-dispatches, 0 timeouts, 0 code changes, 0 promote.

## VERDICT: 🟢 PASS — STAGED-VERIFIED (4/4 mission items)

| # | Mission item | Verdict | Lane |
|---|---|---|---|
| 1 | Payload integrity | ✅ PASS 7/7 | W1 `64a36603` (v0262-integrity) |
| 2 | Boot smoke (fenced) | ✅ PASS | W2 `401c2301` (v0262-bootsmoke) |
| 3 | Gate behavior spot-check | ✅ PASS — 14P re-run green; live child-toolset probe N/A (no endpoint exists) | W3 `9cc582d2` (v0262-gatespot) |
| 4 | Isolation (no flip / no push / no stray writes) | ✅ PASS 7/7 | W4 `3f43040b` (v0262-noflip-git) |
| 5 | Docs (this file + PACKS closure + explicit-path commit) | ✅ | tester |

**Promote-readiness:** staged v0.26.2 verified promote-ready. NO promote performed — the user 3-factor nonce ceremony owns it. `current → releases/v0.25.7` untouched; live daemon MainPID 2296104 continuous since 06:48:44Z.

**Recomputed binary sha256 (3-way match):** `ac7e34b7dda267301603a6e0a23a898abdc4abedd4acb9f5740d2babe2d81e43` (disk == manifest `binary_sha256` == commission claim; additionally == binary sidecar `artifact_sha256`).

---

## Item 1 — Payload integrity (PASS 7/7, W1 `64a36603`)

1. **Payload shape source-less — PASS.** Exactly 7 top-level entries: `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/`. `find` at any depth for `*.build-provenance.json` → empty; no `.env*`; no top-level `scripts/` or `daemon/` (dirs by those names exist only as vendored plugin content under `plugins/opendesign/**`, byte-identical to v0.26.1).
2. **Binary sha — PASS (3-way).** disk `sha256sum` == manifest == claimed `ac7e34b7dda267301603a6e0a23a898abdc4abedd4acb9f5740d2babe2d81e43`.
3. **Manifest fields — PASS.** `version='v0.26.2'`, `binary_version='0.26.2'`, `rollback_safe=True`, `staged_at='2026-10-20T22:37:38Z'`; recomputed `config.yaml` sha `7bab75e96b875caa05693225624f3063a48074485447ef0840681d4fc10a7ae7` == `config_sha256`; recomputed `launcher.sh` sha `a3a44461b39270744917a0017123870dca62b6293273da699e6dfb15bffebd3f` == `launcher_sha256`.
4. **known_schema_gen — PASS.** Manifest `20261020_060000_create_board_tables.sql` == lexicographically-newest migration in the staging worktree; 106 migration files; `uniq -d` on prefixes → empty.
5. **Per-file integrity — PASS.** Re-hashed every manifest entry vs staged files: `agents_manifest` 339/339, `frontend_manifest` 92/92, `plugins_manifest` 4930/4930 (5,361 total) — 0 mismatch, 0 missing, 0 extra. In-payload seal `agents/.prompt-artifacts.sha256`: `sha256sum -c` from release root → **161 OK / 0 FAILED**.
6. **Tree claims vs v0.26.1 — PASS.** agents_tree `31907c83a5256b2412e2e8ec4a67a932c84c4884c0ce482f3932234ce6ef8cc7` (v0.26.2) vs `ec9d3179bbbc28b28d2061fb39df8245821aaf2ee1ed2cb9ab57841506d62fda` (v0.26.1) → DIFFER as claimed; frontend_tree `8d6afeaa8c7fda70acbc05deec17fca89e1a43256179c8e6405fd35a2293ae03` and plugins_tree `4715cf64dad62d209c91206c560e475d7fa4cec8f67f6182ffbcd21b723685a7` EQUAL across both releases; `diff -rq` frontend → empty, plugins → empty. All 6 tree hashes independently recomputed via the house algorithm (`lib.sh` `_tree_manifest`+`_tree_hash_of_lines`, reimplemented in python) → all MATCH. Agents diff = **82 files, 0 "Only in"**: 78 `.md` + 3 `.json` + 1 `.sha256` (seal); the 3 `.json` are exactly `agents/{ari,devops,leader}/meta.json`.
7. **Gate spot checks in the STAGED agents tree — PASS.**
   - `agents/leader/meta.json`: `tools.allow` (24 entries) contains `"question"` ✓; `innate_skills=["coordination","chart","todo"]` → NO `"question"` ✓
   - `agents/devops/meta.json`: `tools.allow` (16) contains `"question"` ✓; `innate_skills=["dynamic-skill","chart","todo"]` → NO `"question"` ✓
   - `agents/ari/meta.json`: `tools.allow` (30) contains `"question"` ✓; `innate_skills=["job-orchestration","chart","todo","question"]` → `"question"` PRESENT ✓
   - Verbatim meta.json deltas confirmed the intended migration shape: leader/devops REMOVED `question` from `innate_skills`; ari ADDED `question` to BOTH `tools.allow` and `innate_skills`.

## Item 2 — Fenced boot smoke (PASS, W2 `401c2301`)

Recipe replicated from the v0.26.0/v0.26.1 precedent RESULTS docs (skill `staged-binary-boot-smoke` located via `skill_search`).

- **Banner (verbatim from boot.log):** `daemon.api - INFO - Starting Ensemble v0.26.2` — count 2 (PyInstaller onefile parent+child; benign, identical to precedent).
- **Health:** `/livez` → 200, body `{"status":"alive","uptime_seconds":7.48…,"version":"0.26.2"}` ✅; `/readyz` → 200, all components true (`database`, `queue_freshness`, `services`, `checkpoint_saver`), `draining:false`. Ready at poll #7 (21 s).
- **Log hygiene:** 0× `42804` / `DatatypeMismatch` / `Application startup failed` / `DuplicateMigrationVersionError`; `Prompt-artifact baseline clean: 161 versioned artifact(s)` ×1; 12 ERROR lines ALL in `daemon.mcp.*` (computer-use ×9, plane ×3) = known-benign class.
- **Scratch provisioning:** dir `/tmp/v0262-smoke.QzRxkf` (FAKE_HOME + CWD with copied `config.yaml`); PG `ensemble_smoke_boot_b3b28beb` on local PG 16.15 @127.0.0.1:5432, fresh role-owned DB → **77 public tables** incl. `board_items`/`board_notes`/`board_item_tags`/`instances`; `schema_migrations`=0 rows on PG (expected per M6 precedent). Port **19100** (free per `ss -ltn` immediately pre-bind; never 9797/7979/8079/8088).
- **Teardown:** ownership re-proven pre-signal (exe == staged binary, `POSTGRES_DB`=scratch, `PORT=19100`) → `kill -TERM -<pgid>` (whole group) → port released in 1 s, group fully exited, **SIGKILL never used**; `pgrep` for the staged binary = 0; DB + role dropped (`pg_database`=0, `pg_roles`=0); scratch removed via prefix guard (0 remaining `/tmp/v0262-smoke.*`); staged payload `find -newermt` = **0 modifications**, binary sha unchanged.
- **Live fence intact:** `:9797/livez` → 200 `{"status":"alive",…,"version":"0.25.7"}`; `ensemble-main` MainPID **2296104** / NRestarts=0 / ActiveEnterTimestamp `Tue 2026-10-20 06:48:44 UTC` unchanged; `current → releases/v0.25.7`; 8079/8088 never bound; 7979 untouched; remote `ensemble_prod` (10.44.0.2) never contacted — every DB write went to the scratch DB.

## Item 3 — Gate behavior spot-check (PASS, W3 `9cc582d2`)

- **Gate pack re-run on the staging worktree:** `tests/unit/tools/test_question_tool_root_gate.py` (the file added by commission merge `9ec01fc81`, 876 insertions, 14 test functions) — command `cd /home/nea/ensemble-src-wt-stage-v0262 && PYTHONDONTWRITEBYTECODE=1 timeout 300 .venv/bin/python -m pytest -p no:cacheprovider tests/unit/tools/test_question_tool_root_gate.py -q --tb=short` → **`14 passed, 225 warnings in 10.78s`** — matches the giter's 14P claim, delta 0. Worktree HEAD `463583800c6062c4199c6f2402ee806cbd01b0e4` unchanged; porcelain EMPTY before AND after (this run wrote zero files; pre-existing `__pycache__` from the giter's run is gitignored). Venv identity proven: `sys.executable` = worktree `.venv`, `daemon` resolves inside the worktree (not the main checkout's editable `.pth`).
- **Giter green set cited (per brief, after the one spot-verify):** gate 14P (independently re-verified above) · flow 19P · tripwire 24P · prompt-artifact baseline 161 (baseline independently verified TWICE here: in-payload seal 161 OK / 0 FAILED by W1, and at boot by W2).
- **Optional live child-toolset probe: N/A (not a gap).** All 174 OpenAPI routes enumerated — there is NO per-instance built-in-tool-list endpoint (only MCP-scoped `InstanceInfo.mcp_tool_names`); the gate is `_is_user_facing_root(parent_id, source_type)` (`daemon/tools/instance.py:6159`). Per the brief's "if cheap" qualifier, no new harness was built; live behavior coverage stands on the 14P gate pack.

## Item 4 — Isolation (PASS 7/7, W4 `3f43040b`)

1. **No flip:** `readlink -f /home/nea/agents-ensemble/current` → `/home/nea/agents-ensemble/releases/v0.25.7` (symlink dated Oct 20 06:48, predates this commission).
2. **Live daemon untouched:** MainPID `2296104`, NRestarts `0`, ActiveEnterTimestamp `Tue 2026-10-20 06:48:44 UTC`; `:9797/livez` → 200, version `0.25.7`.
3. **Tag not pushed + ref bindings:** `ls-remote origin | grep v0.26.2` → 0 matches; `refs/heads/latest` == `origin/latest` == `463583800c6062c4199c6f2402ee806cbd01b0e4` == `refs/tags/v0.26.2^{commit}`; `merge-base --is-ancestor 463583800… latest` rc=0 — **no concurrent-lane drift; the tag commit IS the tip** (unlike the v0.26.0 M1 situation).
4. **Provenance sidecars (staging worktree):** both exist and parse — binary sidecar `git_head=463583800c6062c4199c6f2402ee806cbd01b0e4`, `git_dirty=False`, `artifact_sha256=ac7e34b7dda26730…` (matches manifest); FE sidecar same head, `artifact_sha256=a017dd2135b0f532…`; worktree detached at that HEAD, porcelain 0 lines.
5. **Migration delta re-derived (claim EMPTY, 106==106):** `V261=a4f28c26b2cfac067169d298bc33fc945ffe265b`; `git diff --stat V261..463583800… -- daemon/migrations/versions/` → EMPTY, rc=0; per-rev file counts 106/106; duplicate-prefix check per rev → empty; full file lists IDENTICAL.
6. **rollback_safe:** manifest `rollback_safe=True` (primary evidence) — the stage journal line `rollback_safe=true source=explicit` is NOT recoverable (state.json history: 0 v0.26.2 entries — stage SUCCESSES are unjournaled by design; no `/tmp/ens-stage-*v0262*` logs exist; `journalctl -t ensemble-upgrade` grep → zero matches, possibly privilege-limited view). Disclosed, not failed — the known silent-on-pass class (v0.25.2 precedent).
7. **No pending promote ops:** `state.json` `pending_op=None`, `in_flight=None` (current=v0.25.7, previous=v0.25.7; cooldown/pending_actions/quarantined keys untouched).

## Gaps & disclosures (none blocking)

1. **rollback_safe journal line unrecoverable** (Item 4 check 6b) — manifest field is primary; silent-on-pass provenance-gap class; follow-up unchanged from the standing 🟢 note (add a pass-path log line in `lib.sh` `_verify_artifact_provenance`).
2. **W3 rc=0 inferred, not captured** — the worker's wrapper used a bashism under `/bin/sh` (dash `PIPESTATUS` bad substitution), so its printed `EXIT CODE: 2` is the wrapper's error, NOT pytest's. pytest's own summary `14 passed, 225 warnings in 10.78s` with 0 failed/0 error entails rc=0. Pack was not re-run just to re-capture `$?`.
3. **W2: 22 WARNING lines not individually classified** before scratch (and boot.log) removal — expected content includes the known-benign LiveViews passcode-substrate warning (`SYSTEM_ENCRYPTION_KEY` deliberately not passed to keep it out of process cmdline). No ERROR outside the benign `daemon.mcp.*` set.
4. **W2 minor evidence-ordering note:** `state.json` sha captured post-boot rather than pre-boot; pre-boot fence was instead confirmed via symlink target + `:9797` livez version.
5. **W2 port deviation from skill default:** skill says boot on 8079; the commission brief forbids 8079/8088/7979/9797 → used 19100 (stricter fence honored; skill improvement filed by the worker).
6. **A1 — agents-tree sweep breadth:** 82 differing files across 31 agent dirs (78 `.md` prompt sweeps + the 3 gate `meta.json` + 1 seal). ADJUDICATED ACCEPTED COMMISSION SCOPE, not drift: every file attributes to the commission's own `v0.26.1..HEAD` sweep commits (`564ed0d2f` open-questions contract sweep class C, `4088be633` semantic-class sweep, + follow-ups and seal re-pins `78469d671`/`b05ac37d4`/`6fa226976` + baseline regens); the 3 `.json` are exactly the intended gate files; seal verifies 161/161 in-payload; sampled content shows ask-instruction rewrites to root-conditional wording; no unrelated-topic bleed.
7. **Live child-toolset probe N/A** — no API endpoint exists (174 routes enumerated); behavior covered by the 14P gate pack instead. Recorded as a neutral datum: `ask_questions` appears 0× in boot.log (registry not logged by name — proves nothing either way).

## Worker roster

| Lane | Instance | Flavor | Result |
|---|---|---|---|
| W1 integrity | `64a36603-976c-4f6b-a19c-070eb730eef3` | no load_skill (read-only infra) | PASS 7/7 |
| W2 bootsmoke | `401c2301-9b97-4213-b823-94111a88c2cf` | skill `staged-binary-boot-smoke` | PASS |
| W3 gatespot | `9cc582d2-75d4-4470-8511-b16f674f63a6` | no load_skill | PASS (14P) |
| W4 noflip-git | `3f43040b-0df1-49f6-a6e2-e2c16d70a023` | no load_skill (read-only infra) | PASS 7/7 |

ensure.md scope: artifact/staged gate — source-side unit packs already green per the giter (gate 14P re-verified independently here; flow 19P / tripwire 24P cited per brief; baseline 161 verified in-payload AND at boot). QUARANTINE: no flaky tests encountered — no updates applicable.

**Bottom line:** every mission item PASSes on independently recomputed evidence. Staged v0.26.2 is promote-ready; the user 3-factor nonce ceremony owns the promote. `current → releases/v0.25.7` and the live daemon were untouched throughout.
