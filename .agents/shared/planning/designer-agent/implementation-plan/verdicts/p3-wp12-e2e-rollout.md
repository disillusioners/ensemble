# P3-WP12 — End-to-End Integration: Folds + Deterministic Cycle + Boot Window + §6 Gate Verdict

- **Date:** 2026-09-26T22:05Z
- **Worker:** dev-coder-p3-e2e (P3-WP12, the phase's exit-proof WP)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design` (branch `feature/designer-agent-design`; base for this WP: `aa02fb49`)
- **Commits:** `9791c463` (P3-WP12a folds), `3958cf25` (P3-WP12b consumer skill + e2e test), this docs commit (P3-WP12c)
- **Boot window:** 2026-09-26T21:59:37Z → running (p3 daemon left UP for the phase-lead live cycle — see §Runbook)
- **Boot target:** `127.0.0.1:8081` (port deviation — 8079 deliberately avoided: the main checkout's dev lane owns 8079 by convention and a sibling mission is active in the main tree; P2-WP8 set the precedent with 8080)
- **DB:** `127.0.0.1:5432/ensemble_designer_p3` (FRESH — created via scrubbed wrapper this window; `ensemble_designer_p1` untouched, verified by name-guard + post-create census)
- **Ops lane (NOT git):** `/tmp/p3boot/` — scrub wrapper, boot script, full boot.log, key file (`kms-key-file`, 0o600)

---

## Verdict: **PASS** (all §6 rows green; zero blocking deviations)

---

## 1. What landed (P3-WP12a/b/c)

| Step | Deliverable | Commit |
|---|---|---|
| 1a | WP13a mint-gate: `kms_request` refuses via `validate_key_source()` before any mint; `SYSTEM_ENCRYPTION_KEY_FILE` file-source read in `_build_fernet` (rstrip CR/LF, empty ⇒ refusal); no-key message keeps the actionable env-var pointer | `9791c463` |
| 1b | Audit-lane unification: migration script's `_emit_audit_line` is a thin wrapper over `install_audit.append_install_audit` (`parent=None`; `--jsonl-path` preserved by workdir decomposition; default path = the ONE canonical lane; field set now carries `parent: null`) | `9791c463` |
| 1c | `kms_issue` audit emit: every mint appends ONE best-effort §7.4 line (handle-only `secret_ref`, `idempotency_key=""`); audit failure never fails a mint | `9791c463` |
| 1d | `job_continue` pointer comment: the `[resume]` convention (`parse_resume_message`) is the ratified resume shape — documented convention, no code path | `9791c463` |
| 2 | `agents/worker/skills-template/opendesign-verify.md` + skill-set entry (`requires: {mcp: [opendesign], tools: [bash]}`) — the WP11 consuming-skill body made concrete + the live-cycle trigger | `3958cf25` |
| 3 | `tests/unit/test_p3_e2e_cycle.py` — deterministic 6-swimlane cycle (service layer, no LLM, sqlite, §6 rows 1/2/3/4/5) | `3958cf25` |
| 5 | This verdict + `verdicts/evidence/p3-wp12/` | this commit |

## 2. Boot window (P2-WP8 choreography reused verbatim)

Ordering per `restart-protocol.md` §3 (dir-first: the consumer skill landed in `3958cf25` BEFORE the boot; skill-set.yaml is boot-discovered), boot per `verdicts/p2-wp8-e2e-rollout.md` §E.2 (detached `nohup` + scrub wrapper + uvicorn + livez poll). Evidence: `evidence/p3-wp12/boot-log-excerpts.txt`.

**Env scrub (mandatory):** standalone `#!/bin/bash` wrapper (`/tmp/p3boot/scrub-p3.sh`) unsets ALL ambient `POSTGRES_*`/`PG*` and echo-verifies ZERO survivors before exec. Re-applied inside the boot script after `.env` sourcing (dotenv re-introduces dev DB vars — overridden to p3 explicitly, verified by the engine line naming `ensemble_designer_p3`).

| Gate | Verdict | Evidence |
|---|---|---|
| G1 daemon healthy | **PASS** | `/livez`=200, `/readyz`=200; engine line `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_designer_p3` |
| G2 tool scan | **PASS** | `kms_attach`, `kms_lookup_handle`, `kms_request` in `discover_all_tool_names()` (`boot-gates-inprocess.json`) |
| G3 worker skill bank seeded | **PASS** | `worker|install-opendesign|1.0.0` + `worker|opendesign-verify|1.0.0` (63 total) (`boot-gate3-skillbank.txt`) |
| G4 OpenDesignMCP builtin | **PASS** | class `OpenDesignMCP`, `schema_version 0.16.1` (`boot-gates-inprocess.json`) |
| G5 registry strict-load | **PASS** | 1 entry, `opendesign` → `install-opendesign`, `_pending: false` (`boot-gates-inprocess.json`) |
| G6 KMS redaction filter | **PASS** | 2/2 api.py root handlers carry `KMSRedactionFilter`; `addHandler` patch auto-covers a fresh handler; functional canary scrub proven (`boot-gate6-redaction.json`) |
| G7 configure-builtin (API) | **PASS** | row `5eee7b16-…`, `is_builtin`, env = `OD_DAEMON_URL` only, NO token; `mcp_install` audit line in the canonical lane (`configure-builtin-out.json`, `audit-lane-lines.txt`) |
| G8 test-connection | **PASS** | `success: true, tools_count: 10`; full od_* list captured via direct MCP handshake (`test-connection-out.json`, `od-tool-list.json`) |

G8 note (accepted leftover, P2-WP4 §6): the OD **daemon** itself is absent on this host; the MCP binary starts and lists its 10 tools regardless — design-op failures at the OD-daemon seam are an ops-lane gap, NOT an install failure.

## 3. §6 Phase Exit Criterion — row-by-row

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | Autonomous self-install + mint + resume cycle (6 swimlanes, no human) | **PASS** | `test_p3_e2e_cycle.py::test_full_bootstrap_cycle_rows_1_3_5` — skill load → miss → envelope → mint → configure-builtin → attach → `[resume]` re-check `present` → spawn-time plaintext vs DB marker, ONE test |
| 2 | Audit lines `mcp_install` AND `kms_issue`, one lane | **PASS** | Same test asserts 1+1 events in ONE jsonl from the same run; live boot produced the `mcp_install` line (`audit-lane-lines.txt`) |
| 3 | Stored config marker-only | **PASS** | Same test: every secret-matched env value `is_marker()`; `OD_API_TOKEN` = `__KMS_REF__…__`; `OD_DAEMON_URL` stays plain default |
| 4 | Key absent ⇒ refusal, no plaintext row, clean logs | **PASS** | `::test_row4_fail_closed_without_key` — `KMSUnavailableError`, no `bound_handles`, no `kms_issue` line, no `KMS_HANDLE_`/`__KMS_REF__` in captured logs |
| 5 | Plaintext never in context (checkpoint/log scan) | **PASS** | Same test: minted plaintext absent from envelope wire, resume wire, checkpoint-shaped message dicts, audit jsonl, caplog |
| 6 | All WP1–WP11 acceptance tests green | **PASS** | 346/346 on the explicit 15-file list (`row6-test-list-output.txt`) |
| 7 | WP13 decided | **PASS** | WP13a landed (Step 1a; mint-gate acceptance tests in `test_key_hardening.py::TestMintGateThroughKmsRequest`) |
| 8 | Cross-phase R5 + R6 | **PASS** | `agents/designer/` exists (ls); `config.yaml:82` allowed_models default includes `vision` (grep below) |

Row 8 read-only asserts (run this window):

```
$ ls agents/designer/           → meta.json rule.md soul.md tools_note.md workflow.md
$ grep -n "allowed_models" config.yaml
87:  allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}
```

Note: the plan (and `restart-protocol.md`) cite `config.yaml:82`; the
line has drifted to **87** on this branch (harmless comment reshuffle
above it). The VALUE line is what matters and it carries `vision` in
the default — R6 green.

## 4. Deviations / notes

| ID | Deviation | Why / disposition |
|---|---|---|
| D1 | Boot port **8081** (canonical dev 8079 avoided) | The main checkout (sibling mission `feature/upgrade-tool-lane-fix`) owns the 8079 dev lane by convention; P2-WP8 set the off-lane precedent with 8080. 8081 is collision-free. Recorded in the runbook. |
| D2 | Migration-test audit fixtures updated (12 path fixtures → canonical lane layout; exact-field-set assertion now includes `parent: null`; per-event line-count assertions) | Direct consequence of the sanctioned audit-lane unification (fold 1b): mints now emit `kms_issue` lines and the migration line carries the helper's §7.4 field set. The script's own docstring pre-registered exactly this reconciliation ("adopt the helper's path + field set"). |
| D3 | CWD-isolation fixtures added to 4 KMS test files | Fold 1c makes every mint emit an audit line resolved from CWD; without isolation, test runs would write into the repo's real audit lane (observed once during bring-up — stray file deleted, fixture added). |
| D4 | `test_capability_resolver.py::test_load_real_registry_default_path` fixed (`pending is True` → `is False`) | **Pre-existing red** (verified failing at `aa02fb49` before any WP12 change): the test asserted the pre-WP5 "installer not yet landed" state; the installer landed in WP5 so the loader correctly resolves it. Assertion tracks the landed reality. |
| D5 | `--jsonl-path` overrides must now end with the canonical `.agents/shared/planning/designer-agent/install-audit.jsonl` layout (fail-loud `ValueError` otherwise) | The only shape that lets ONE writer serve both the CLI override and the canonical lane. Old arbitrary paths were the script-private divergence being retired. |
| D6 | Boot-log noise accepted | `plane` MCP connect errors (external Plane absent) + one pre-existing `maintenancer` deny-entry validation warning — both unrelated to P3 surface, pre-existing on this branch. |
| D7 | p3 daemon left RUNNING (not torn down) | The phase-lead live cycle (§5) needs a booted daemon with the row configured. Reuse it, or stop + re-boot via `/tmp/p3boot/boot-p3.sh`. Kill/cleanup steps are in the runbook. |

**Giter note (for the phase lead, not messaged by me):** per dispatch, the
restart-window work is self-committed (`9791c463`, `3958cf25`, WP12c docs
commit). If the mission contract wants a giter checkpoint-commit marker on
this window, that is a phase-lead → giter hand-off — no giter message was
sent from this instance.

## 5. Live-cycle runbook (phase lead executes — orchestration lane is yours)

**State left for you:** p3 daemon UP at `127.0.0.1:8081`, DB
`ensemble_designer_p3`, opendesign row already configured (zero-credential,
`OD_DAEMON_URL=http://127.0.0.1:7456`), KMS store holding NO handle yet
(the e2e test mints were in the test process, not the daemon).

**Environment facts:** LLM proxy `127.0.0.1:4124` is TEXT-ONLY (vision ⇒
500) — spawn the designer with a TEXT model override via the in-process
spawn `model=` param (HTTP InstanceCreate has NO model field). Worker
needs `opendesign-verify` loaded; installer needs `install-opendesign`.

1. **Spawn the original worker** (agent `worker`) with a task that loads
   `opendesign-verify` (skill-load lane or explicit instruction) and asks
   it to verify the `opendesign` capability.
2. **Expect the miss envelope.** The worker's pre-flight misses (no MCP
   tools granted to it), and it emits on the child-report lane:

   ```
   Result: {"kind": "capability_missing", "capability": "opendesign",
            "installer_skill": "install-opendesign", ...,
            "resume_hint": "step_after_install", "ts": "<iso>"}
   ```

   Capture: the child-report body (this IS the envelope artifact).
3. **Dispatch the installer**: spawn the installer instance (designer- or
   worker-hosted per your orchestration) with `install-opendesign` loaded;
   its Step-2 configure-builtin is idempotent — the existing row replays.
   If a token is genuinely required it mints + attaches (day-1 loopback:
   no token).
4. **Resume the worker** via `job_continue(old_job_id, message)` with:

   ```
   [resume] {"capability_id": "opendesign", "status": "capability_missing",
             "tools_now_available": ["mcp_opendesign_od_list_projects", "..."],
             "resume_from": "step_after_install"}
   ```

   (list the actual granted `mcp_opendesign_*` tools).
5. **Expect the worker's verification report**: fresh `capability_check`
   → `present`, then a plain `Result:` summary naming the od_* surface —
   the 10 tools in `evidence/p3-wp12/od-tool-list.json`.
6. **Plaintext-absence proof against the LIVE run** (all scrubbed-lane;
   DB scans hit `ensemble_designer_p3` ONLY — never prod/p1):

   ```bash
   # a. stored config marker-only (row 3, live):
   psql -h 127.0.0.1 -p 5432 -U ensemble -d ensemble_designer_p3 -tAc \
     "SELECT config->'env' FROM mcp_servers WHERE name='opendesign'"
   #    expect: OD_DAEMON_URL only (or __KMS_REF__ marker for a token) — NO plaintext

   # b. checkpoint scan (row 5, live): dump the worker instance's
   #    checkpoint blobs and grep for the minted plaintext substring.
   #    Mint happens ONLY if a token is required (loopback ⇒ skip);
   #    with no mint, assert instead: zero KMS_HANDLE_ rows in
   #    instance_metadata.bound_handles AND no kms_issue audit line:
   tail -5 .agents/shared/planning/designer-agent/install-audit.jsonl

   # c. log scan: the daemon log carries no secret material —
   #    the KMS redaction filter (G6) plus WP10's static audit hold.
   grep -E "KMS_HANDLE_|__KMS_REF__" /tmp/p3boot/boot.log || echo "clean"
   ```

7. **Kill / cleanup (yours):**

   ```bash
   kill -TERM $(pgrep -f "uvicorn daemon.api:app.*8081") ; sleep 5
   curl -s --max-time 2 http://127.0.0.1:8081/livez || echo "released"
   # DB: keep or drop ensemble_designer_p3 — your call (drop via
   # /tmp/p3boot/db-setup-p3.sh semantics; NEVER touches p1/prod).
   # Ops lane /tmp/p3boot/ is disposable; the key file never leaves it.
   ```

## 6. Evidence index (`verdicts/evidence/p3-wp12/`)

| File | Content |
|---|---|
| `row6-test-list-output.txt` | 346/346 pass, explicit 15-file list (row 6) |
| `boot-log-excerpts.txt` | scrub-OK, DB-TARGET p3, KMS file-source, engine line, seeding |
| `boot-gates-inprocess.json` | G2 tool scan + G4 builtin + G5 strict-load |
| `boot-gate3-skillbank.txt` | both worker skills seeded, 63 total |
| `boot-gate6-redaction.json` | handler coverage + addHandler patch + functional scrub |
| `configure-builtin-out.json` | live row create (zero-credential, no token) |
| `test-connection-out.json` | success, 10 tools |
| `od-tool-list.json` | the 10 od_* tool names (direct MCP handshake) |
| `audit-lane-lines.txt` | live `mcp_install` line + `kms_issue` line shape (row 2) |
