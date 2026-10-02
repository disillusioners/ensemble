# TEST GATE — Stage-1 OpenDesign Self-Provisioning (ens_env_read + mcp_set_env + KMS env-ref bridge + skill v1.3.0)

Date: 2026-10-02 · Branch `feature/od-self-provisioning` @ `480b4485` (base `8b520d54`, chain bb465174→d4af89b3→a0100b0e→878928e9→027962e6→480b4485) · Gate-pack commits `ab0be5e1` (4 packs) + `7661eee9` (2 packs + PACKS rows), LOCAL only, no push.
Tester-dispatched workers: discovery `93c024bf`, packs `81e590fa`/`9c02b6d6`/`f6b8a51e`/`bdc764af`/`556a3dff`/`8c64d309`/`c83cc819`, A/B `a335a4c5`, boot `0608e4f8`, hygiene `29563e43`.

## VERDICT: ✅ READY-TO-MERGE

Every in-scope gate PASS; A/B proves zero branch-caused failures; boot smoke green under full env-poison guard; no new failures in any suite beyond the (now root-caused + remediated) pre-existing family. Named blockers: **none**.

### Scope Decision
Full change set is **23 files** (broader than the mission's 10-file list): 8 daemon files (incl. `_tool_registry.py`, `instance.py`), 3 agent files (`worker/meta.json`, `skill-set.yaml`, `install-opendesign.md` v1.3.0), 10 unit + 2 integration test files. Blast radius = tools/services/routers seam → 8 packs + A/B + boot smoke. Release-Gate e2e NOT run (feature merge gate, not a promote; e2e lane N/A per execution-lane intersection — no job/task/queue files touched). OD live-lane smoke SKIPPED-BLOCKED **by design** (full validation lands post-promote).

## §1 Unit Suites (mission item 1) — ALL PASS

| Pack | Files | Result | Runtime | Worker |
|---|---|---|---|---|
| `ens_env_tools_unit_test` | test_ens_env_tools.py + test_ens_env_registration.py | ✅ 32/32 | 5s | 81e590fa |
| `mcp_set_env_unit_test` | test_mcp_set_env_tools.py | ✅ 44/44 | 45.2s | 9c02b6d6 |
| `env_key_kms_unit_test` | test_env_key_policy.py + test_kms_attach_env_ref.py + test_kms_resolver.py | ✅ 83/83 | 29.2s | f6b8a51e |
| `kms_lane1_regression_unit_test` | 7 pre-branch KMS files | ✅ 151/151 | 27.3s | bdc764af |
| `frozen_tool_name_discovery_unit_test` (standing) | drift test | ✅ 7/7 | 1.2s | 556a3dff |
| `concurrency_atomic_unit_test` (standing) | 13 locked files | ✅ 98P/0F/74S baseline-exact | 77s | 8c64d309 |
| `odsp_changed_files_unit_test` (new) | attestation+upgrade registration, mcp_warmup_pool | ✅ 114/114 | 78.6s | c83cc819 |
| `odsp_changed_files_integration_test` (new) | maintenancer_spawn_resolves_tools, service_tool_flag_off_byte_identical | ✅ 22/22 | 6.4s | c83cc819 |

Total: **461/461 green** across all packs. Exit codes 0 everywhere; dual-layer timeouts honored (110s internal / 120–300s command); env-scrubbed via `scripts/run_tests_scrubbed.sh`.

## §2 Visibility Semantics (mission item 2) — PASS

- **Privileged ens-env stripping**: `ens-env` ∈ `PRIVILEGED_TOOL_CATEGORIES` (`TestSecurityBoundary::test_in_privileged_tool_categories`); empty-tools.allow agent does NOT receive `ens_env_read` — `TestStage0NonRegression::test_empty_allow_does_not_grant_ens_env` (`_strip_privileged_category_tools` strips from return-all paths; control `bash` still resolves → fence not over-broad).
- **Worker explicit allow**: `agents/worker/meta.json` tools.allow contains `"ens-env"` (`TestWorkerOptIn` ×2; meta.json integrity preserved).
- **mcp_set_env via infra**: `_tool_category == "infra"`, `CATEGORY_MODULES["infra"] == "daemon.tools.infra"`, AST discovery finds it, `instance.py` wiring pinned; worker already opts into `infra` (KMS-trio precedent, no meta.json change) — `TestRegistrationSeam` ×5.
- **KNOWN_TOOL_NAMES parity**: drift test PASS; both sets size 208, `known−src = []`, `src−known = []`; `ens_env_read` + `mcp_set_env` in BOTH source discovery and frozen static set (gate-critical since `_tool_registry.py` changed on branch).

## §3 Security Gates (mission item 3) — PASS

- **Secret-shape rejection set EXACTLY {KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL, PRIVATE, PWD, AUTH}**: `test_exactly_the_reviewer_eight_words` (test_env_key_policy.py:26-36) + `SECRET_MARKER_WORDS == (...)` byte-quote (test_mcp_set_env_tools.py:284-292) + 8-row positive parametrize + 4-key negative + full-set `ERROR: SECRET_SHAPED_KEY` cases. `REDACT_ONLY_MARKER_WORDS == ("BASE","HEADERS")` separate.
- **ASCII identifier validation**: `test_non_identifiers_fail` rejects `"КЕY"` (Cyrillic homoglyph), `"key\u200b"` (zero-width), `"すし_KEY"` (fullwidth), `"a-b"`, `"1KEY"`, `"KEY NAME"`, `""`, plus non-string; mcp_set_env lane discriminates `INVALID_ENV_KEY` vs `SECRET_SHAPED_KEY`.
- **Marker-shaped VALUES rejected, BOTH shapes**: `TestMarkerValueRejection::test_marker_shaped_value_rejected` (test_mcp_set_env_tools.py:368-379) — parametrized over `"__KMS_REF__KMS_HANDLE_abc123__"` AND `"__KMS_ENV__OPENAI_API_KEY__"` → `ERROR: MARKER_VALUE_FORBIDDEN`, row-untouched assertion, negative control `test_plain_values_still_pass` (`__not_a_marker__` passes → guard not over-broad).
- **Read-side redaction (~10 words incl. BASE/HEADERS)**: `TestEnsEnvReadSecretHandling` — caplog + audit trail scanned, secret values never in any record; audit logs keys only.
- **Resolver fail-closed**: `test_missing_env_var_raises_with_var_name` (names VAR, defensively asserts no `sk-`/`plaintext`), `test_missing_env_var_does_not_silent_fallback`; e2e mirror `ERROR: ENV_VAR_NOT_FOUND` + var name echo, no value.

## §4 LANE-1 Regression (mission item 4) — PASS (unchanged)

151/151 across 7 pre-branch KMS files. Minted-handle `__KMS_REF__` byte-identity pins pass (densest: test_opendesign_builtin 8 pins); `kms_attach` handle-mode + `test_handle_mode_does_not_use_env_refs` prove dual-mode coexistence; regex disjointness by construction (`KMS_ENV_MARKER_PREFIX = "__KMS_ENV__"` kms_lite.py:85, separate compiled `KMS_ENV_MARKER_RE` kms_lite.py:116 / kms_resolver.py:79 — distinct prefix from `__KMS_REF__`). Cross-check: `test_mcp_warmup_pool.py::TestKMSEnvResolution` (pooled connections resolve stored markers → plaintext for subprocesses, P3 review F3) PASS.

## §5 Pre-existing Reds A/B (mission item 5) — PROVEN PRE-EXISTING, ZERO NEW

7-leg A/B (all `timeout 300`, PYTHONPATH-pinned import root, logs `/tmp/odsp-leg{A,A2,B,B2,C,D1,D2}.log`, worker a335a4c5):

| Leg | Commit | Location | Result |
|---|---|---|---|
| A / A2 | base 8b520d54 | worktree (.env copied / none) | 80/80 PASS |
| B / B2 | HEAD | main checkout (.env / PG-scrubbed) | 16F/65P |
| C | HEAD | clean worktree | **81/81 PASS** |
| D1 | base | worktree + stale db transplant | **16F/64P** |
| D2 | HEAD | worktree + stale db transplant | **16F/65P** |

- Failure sets B, B2, D1, D2 byte-identical (md5 `fc060c1c…`, 16 router/file-engine nodes). **Root cause: stale untracked `test_mcp_servers.db`** (gitignored `test_*.db` .gitignore:69; CWD-relative fixture :58-63; schema lacks `mcp_servers.instance_metadata`; transplant reproduces at BOTH commits). Mission's claimed mechanism CONFIRMED; prior §G1 ".env confound" attribution (RESULTS/2026-09-27) REFUTED as carrier — location effect, not env.
- **Zero branch-caused failures**: clean-worktree legs 80/80 (base) vs 81/81 (HEAD) — branch adds exactly 1 PASSING test. No suite run anywhere in this gate produced a failure beyond this family.
- **Authorized remediation** (hygiene worker 29563e43): `rm test_mcp_servers.db` (untracked+ignored proven) → main checkout **81/81 in 5.15s**. TRAP: fixture recreates the file CWD-relative; it re-stales after any future `mcp_servers` column migration (`create_all` never alters columns). Quarantined + routed as test-debt (see §Follow-ups).

## §6 Boot Smoke (mission item 6) — PASS, guard-compliant

**Boot #1 statement: DSN `postgresql://ensemble:***@localhost:5432/ensemble_dev`, port 8079, `ENSEMBLE_SELF_ENV=dev`** — the only boot performed. Guard details: 3-guard all satisfied (env override verified in daemon `/proc/<pid>/environ` — load-bearing: ambient shell carried `ENSEMBLE_SELF_ENV=live`; `.env`+`data_dev/ensemble.json` cross-checked; `:8079` free pre-boot; LIVE `:9797` pid 2890792 and DEMO `:7979` pid 2185455 untouched throughout).
- Daemon booted clean ("Skill seeding complete: 0 new, 1 updated, 63 unchanged, 0 errors"); no traceback in boot log.
- Tool registration: in-process `discover_all_tool_names()` → `True True` for `ens_env_read`/`mcp_set_env`.
- **mcp_set_env round-trip** (dev DB, real opendesign row `b69046ff-…`): BEFORE `{"OD_DAEMON_URL": "http://127.0.0.1:7456"}` → merged `OD_SMOKE_CHECK=stage1-gate-ok` via `McpServerRepository.update_mcp_server` (the exact write path `daemon/tools/infra.py:1534` delegates to) → read-back verified both keys, OD_DAEMON_URL preserved → cleanup byte-equal to BEFORE. No throwaway row needed; live/demo rows never touched.
- Clean SIGTERM shutdown (exact :8079-owning PID), port freed, dev code-server EXIT-trap cleaned.

## §7 Optional OD Live-Lane (mission item 7) — SKIPPED-BLOCKED (by design)

Reason (precise): dev opendesign MCP row carries only `OD_DAEMON_URL`; `BYOK_BASE_URL`/`BYOK_MODEL` not provisioned and `BYOK_API_KEY` not bound via kms_attach — per instruction, credentials were NOT fabricated or copied from live. OD daemon confirmed listening (127.0.0.1:7456, pid 2965860, MCP-stdio only). That lane's full validation lands **post-promote by design**; blocked state is expected, not a defect.

## ensure.md Validation (Core, blast-radius scoped)

- ✅ Critical #1 no regressions in changed packs — all 8 packs PASS
- ✅ Critical #2/#3 deadlock/sync-DB integrity — `concurrency_atomic_unit_test` 98P/0F/74S baseline-exact
- ✅ Critical #4 dev.sh `--timeout-graceful-shutdown 10` — present (dev.sh:102, discovery static)
- ✅ Important #1 async callers awaited — await-audit CLEAN (1 new `async def ens_env_read`; zero bare-coroutine call sites in diff; invoked via LangGraph tool framework)
- Nice-to-have dead-code check: N/A (no deletions flagged)
- Release Gate: N/A (feature merge, not promote; no execution-lane files touched)

## Environment Remediations (transparent, both gitignored dev artifacts, NOT committed)

1. `rm /home/nea/ensemble-src/test_mcp_servers.db` — stale sqlite fixture (§5).
2. `.env` + `data_dev/ensemble.json` POSTGRES_PASSWORD `testpw` → `ensemble_dev` (actual working credential for `ensemble` role; `testpw` failed auth → boot blocked; gitignored files). Known sharp edge: dev.sh `set -a; source .env` re-exports .env values over wrapper pre-sets — wrapper env overrides other than ENSEMBLE_SELF_ENV must be made IN .env. LESSONS entry written.

## Follow-ups (non-blocking, routed)

- 🟠 **Test-debt**: `tests/unit/test_mcp_server_crud.py` CWD-relative file fixture (`sqlite:///test_mcp_servers.db`) → tmp_path/in-memory; re-stales on every mcp_servers schema migration (today's 16F family; QUARANTINE row added).
- 🟢 Doc note: dev bootstrap password (`ensemble_dev` for `ensemble` role) belongs in README dev section; dev.sh `.env` re-export sharp edge noted.
- 🟢 OD live-lane full validation rides the post-promote commission (by design).

## Documentation Updated
PACKS.md (gate section finalized), QUARANTINE.md (stale-fixture family row), LESSONS/2026-10-02-odsp-stage1-gate-lessons.md, this RESULTS doc. Doc commit follows (local only, no push).

## Overall Status
- Unit packs: ✅ 461/461 · Visibility: ✅ · Security gates: ✅ · LANE-1: ✅ · Parity: ✅ · A/B no-new: ✅ · Boot smoke: ✅ · ensure.md Core: ✅
- **Testing Complete: ✅ READY-TO-MERGE** (no named blockers; SKIPPED-BLOCKED od lane is by-design post-promote scope)
