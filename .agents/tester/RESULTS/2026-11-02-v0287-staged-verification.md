# G14 — STAGED VERIFICATION of `releases/v0.28.7` (S1–S4) — lane `plane-pm-issue-tools`

**Executed:** 2026-11-02 23:44Z → 2026-11-02 23:57Z · **Tree under test:** `04b44ab79f1359f28ac49ddf1263144b80339afd` (tag `v0.28.7` target) · staging worktree `/home/nea/ensemble-src-wt-stage-v0287` (read-only; porcelain 0 lines PRE **and** POST) · branch `tester/v0287-staged-verification` (PUSHED, **not** merged — leader sweeps).

**Binding recipe (read FIRST, executed as written):** `.agents/tester/RESULTS/2026-11-02-plane-pm-phase6-smoke-rehearsal.md` §8 (S1 + S2 ready-to-execute checklists) and §9 (re-run recipe) + the re-runnable harness in `.agents/tester/EVIDENCE/plane-pm-smoke-2026-11-02/` (`setup_leg.sh`, `boot_leg.sh`, `assert_leg.sh`, `teardown_leg.sh`, `fence.sh`) — all five scripts present in the staged tree and read before execution.

**Object:** `/home/nea/agents-ensemble/releases/v0.28.7` · expected binary sha256 **`6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49`** · tag `v0.28.7` PUSHED (annotated) · L1 tip-identity re-confirmed (`HEAD == origin/latest == tag target == 04b44ab79`).

**Raw artifacts:** `.agents/tester/EVIDENCE/v0287-staged-2026-11-02/` (15 files — boot log, per-leg assertion/row/teardown outputs, env-name census, redacted env + redacted REST view, the PRE/MID/POST fence captures, the setup log, the S3 matrix, and both migration censuses). No real bearer and no scratch role password appear in any committed file (scan in §5).

---

## 0. Verdict summary

| Gate | Scope | Verdict | Headline number |
|---|---|---|---|
| **S1** | artifact integrity (8 checks) | **PASS** | 4-way sha `6f652d6b…` ×4; manifest delta = exactly the 5 expected scalars; migrations 119 == 119; prompt-baseline **161/161**; payload source-less |
| **S2** | isolated boot smoke (binary-copy form) | **PASS** | banner `Starting Ensemble v0.28.7`; livez 200 / readyz 200 all-true `reasons=[]`; plane row **schema_version 3** + header `x-workspace-slug: e2e`; fence PRE == MID == POST |
| **S3** | binary-level lane content (one-way control) | **PASS** | **49/49** assertions, 0 mismatches; plane `schema_version` STAGED `'3'` vs CONTROL `'2'`; keep-set **28/28** in `meta.allow`, bare `"plane"` entry gone |
| **S4** | ceremony inputs | **PASS** | `in_flight=None`, `pending_op=None`, `quarantined=['v0.28.5']`; command recorded verbatim; retention census recorded |

**STAGED-VERIFIED: PASS-WITH-NOTES** — 10 notes enumerated in §6. No gate failed. The notes are: (i) **the artifact was already promoted live before this verification started** (§6 N1 — the dispatch premise "LIVE = v0.28.4" was stale), (ii) one harness filename deviation that made 2 of the committed recipe's greps vacuous and was re-run non-vacuously (§6 N2), and (iii–x) disclosed scope/shape items — among them N10, the shared-cgroup exposure of the boot (no live process harmed; pids continuous).

---

## 1. S1 — ARTIFACT INTEGRITY

### S1.1 — 4-way sha256 RE-HASH (re-hashed on disk; the sidecar's recorded value was never trusted)

| # | Waypoint | sha256 |
|---|---|---|
| 1 | `releases/v0.28.7/ensemble-prod` (release dir, on disk) | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| 2 | `ensemble-src-wt-stage-v0287/dist/ensemble-prod` (staging worktree) | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| 3 | `manifest.json.binary_sha256` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| 4 | sidecar `dist/ensemble-prod.build-provenance.json` → `artifact_sha256` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |

**PASS** — all four identical and equal to the expected value.

### S1.2 — Tag peel-verified LOCAL + REMOTE

```
git cat-file -t v0.28.7            -> tag                       (annotated)
git rev-parse v0.28.7              -> e4728f1e83a4cb9cdec1d9554c8786c6cd479874   (tag object)
git rev-parse v0.28.7^{}           -> 04b44ab79f1359f28ac49ddf1263144b80339afd   (peels to the bump commit)
git ls-remote origin refs/tags/v0.28.7     -> e4728f1e…  refs/tags/v0.28.7
git ls-remote origin refs/tags/v0.28.7^{}  -> 04b44ab79…  refs/tags/v0.28.7^{}
git ls-remote origin refs/heads/latest     -> 04b44ab79f1359f28ac49ddf1263144b80339afd
```

**PASS** — local tag object == remote tag object; both peel to `04b44ab79`; remote ref present (PUSHED). **L1 tip-identity re-confirmed cheaply**: `HEAD == origin/latest == tag target == 04b44ab79`.

### S1.3 — Both provenance sidecars

| Sidecar | git_head | git_dirty | artifact_sha256 | build_at |
|---|---|---|---|---|
| `dist/ensemble-prod.build-provenance.json` | `04b44ab79f1359f28ac49ddf1263144b80339afd` | `false` | `6f652d6b…` | `2026-11-02T23:26:23Z` |
| `frontend/dist/frontend/browser/index.html.build-provenance.json` | `04b44ab79f1359f28ac49ddf1263144b80339afd` | `false` | `5fef719bb90916f7bfca8a9bcd486d4d5ee8c6cac3dde7e70a79a1167968ed99` | `2026-11-02T23:22:57Z` |

**PASS** — both sidecars present, both `git_head == 04b44ab79`, both `git_dirty=false`.

### S1.4 — Manifest deltas vs v0.28.6 RE-DERIVED (both manifests read from `releases/`)

**Changed scalars — exactly the 4 version fields + the agents tree hash, nothing else:**

| key | v0.28.6 | v0.28.7 |
|---|---|---|
| `binary_sha256` | `f80d4403477eedfaec4a4b3e92d04dadf2e0310dc720829ee28aa3b44416f060` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| `binary_version` | `0.28.6` | `0.28.7` |
| `version` | `v0.28.6` | `v0.28.7` |
| `staged_at` | `2026-11-02T23:01:09Z` | `2026-11-02T23:33:28Z` |
| `agents_tree_sha256` | `45e7be32e8516b64e0e47991940fd97d9125018db90582bd058cbb45aa0f466c` | `b76c83a27700a3811995a143c13cba5cf2477445beb9f597825368a8426e3e64` |

**Identical (as required):** `config_sha256` (`0f0875bd…`), `launcher_sha256` (`a3a44461…`), `known_schema_gen` (`20261031_120002_create_project_skill_usage_records.sql`), `frontend_tree_sha256` (`ab6a0ced…`), `plugins_tree_sha256` (`4715cf64…`), `rollback_safe` (`true`), `contains_contract_phase` (`true`), `pipeline_scripts_floor_commit` (`953603108…`).

**Per-file maps:** `agents_manifest` **338 == 338** (0 added, 0 removed, **6 changed** — all `project-manager/*` + `.prompt-artifacts.sha256`); `frontend_manifest` **95 == 95, 0 changed**; `plugins_manifest` **4930 == 4930, 0 changed**.

**Re-hash of every manifest entry on disk: 5363/5363 entries, 0 discrepancies.**

**PASS** — no delta beyond the expected set.

### S1.5 — Migration census, BOTH directions vs the live binary

Method: PyInstaller `CArchiveReader` → PYZ TOC → the embedded `daemon/migrations/versions/*.sql` set (binary-level, read-only; the binary is never executed).

```
STAGED  releases/v0.28.7/ensemble-prod : 119 embedded .sql entries, 119 distinct prefixes, duplicates: none (uniq -d empty)
CONTROL releases/v0.28.4/ensemble-prod : 119 embedded .sql entries, 119 distinct prefixes, duplicates: none (uniq -d empty)
set diff staged vs control                        : EMPTY (diff rc=0)
source tree at 04b44ab79                         : 119 .sql files, 0 duplicate prefixes
```

**OUR-LANE-ZERO proof:** `git diff fa1817efa..04b44ab79 -- daemon/migrations/versions/` → **empty** (0 files) ⇒ zero lane-authored `.sql`.

**PASS** — 119 == 119 in both directions, zero lane-authored migrations.

### S1.6 — Prompt-artifact baseline (house tool, `--check`, read-only, from the staging worktree)

```
python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree /home/nea/ensemble-src-wt-stage-v0287
  -> PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256   (rc=0)

python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree /home/nea/agents-ensemble/releases/v0.28.7
  -> PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256   (rc=0)
```

**PASS — 161/161 at both the staged tree AND the staged payload.**

### S1.7 — Payload SOURCE-LESS (+ the lane recipe's NB6 payload assertion)

| check | result |
|---|---|
| `releases/v0.28.7/scripts/` | **ABSENT** ✅ |
| any `.env` / `.env.*` at any depth | **0** ✅ |
| any `*.build-provenance.json` at any depth | **0** ✅ (both sidecars live in the staging worktree, not the release dir) |
| `releases/v0.28.7/daemon/` source tree | **ABSENT** ✅ |
| `.staging.*` / `.aside.*` residue anywhere in `releases/` | **0** ✅ |
| release-dir contents | `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` |
| **NB6** `grep -rIl "PLANE_MCP_API_KEY"` in the release dir | **0 files** ✅ |
| **NB6** `plane_api_[A-Za-z0-9_]{6,}` bearer-shaped literals in the release dir | **0** ✅ |

**PASS** — source-less payload; no `PLANE_*` key material in the payload.

### S1.8 — Staging-worktree porcelain identical pre/post

```
git status --porcelain=v1  ->  0 lines  (PRE, captured before the first boot action)
git status --porcelain=v1  ->  0 lines  (POST, after the full smoke + teardown)
diff PRE POST             ->  byte-identical
```

**PASS** — the staging worktree was never modified.

---

## 2. S2 — ISOLATED BOOT SMOKE (binary-copy form)

**Recipe deviations are listed in §6; the `assert_leg.sh` and `teardown_leg.sh` were executed BYTE-IDENTICAL to the committed recipe except the `RUN` path (verified by `diff` before use).**

### Setup (adapted `setup_leg.sh`)

```
scratch root   : /tmp/v0287-smoke.s2.scratch.ufPDkR      (mktemp)
FAKE_HOME      : /tmp/v0287-smoke.s2.home.iqVEaC         (mktemp)
escape guard   : neither path under /home/nea/agents-ensemble* or /home/nea/ensemble-src*  -> PASS
port           : 8951  (verified free via `ss -ltn`; 9797/7979/8079/8088 never used)
scratch PG     : role v0287_smoke_s2_1793663324_32445 / db ens_v0287_s2_1793663324_2972
                 LOCAL 127.0.0.1:5432 only; `ensemble_prod` does NOT exist on the local
                 cluster (count=0, asserted in setup) -> prod could not be addressed
binary copy    : releases/v0.28.7/ensemble-prod -> $SCRATCH/ensemble-prod
                 (sha256 re-verified in-scratch = 6f652d6b… ; + config.yaml + agents/ copied)
public Plane URL vs LIVE install .env (sha-compare only, never echoed) : MATCH
```

### Boot (adapted `boot_leg.sh`) — `env -i` allowlist, 24 names

`ENSEMBLE_SELF_ENV=sandbox` **pinned explicitly** · `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (never `env -u`) · `MCP_DISABLE_BUILT_IN_CONTEXT7=true` · `HOST=127.0.0.1` · `PORT=DAEMON_PORT=8951` · `POSTGRES_*` → the scratch role/DB on 127.0.0.1:5432 · `PLANE_MCP_URL` = the real public URL · `PLANE_MCP_API_KEY` = the **DUMMY** `plane_api_staged_smoke_dummy_s2` (asserted by a pre-boot `grep -qx`) · `PLANE_MCP_WORKSPACE_SLUG=e2e`.

### Assertions (committed `assert_leg.sh`)

```
port 8951 bound : LISTEN 127.0.0.1:8951  users:(("ensemble-prod",pid=2404386,fd=25))
livez           : http=200  {"status":"alive","uptime_seconds":22.13,"version":"0.28.7"}
readyz          : http=200  status=ready  components={database:True, queue_freshness:True,
                                                     services:True, checkpoint_saver:True}  reasons=[]
mcp-servers     : http=200
```

**Banner (verbatim, from the boot log):**

```
23:49:01 - daemon.api - INFO - Starting Ensemble v0.28.7
23:49:02 - daemon.api - INFO - Starting Ensemble v0.28.7
```

(the banner is logged twice — `daemon/api.py` and `daemon/__main__.py` — expected, not a defect.)

**Plane surface (the DB row — the Authorization is redacted IN SQL):**

```
plane|t|t|3|e2e|Bearer <redacted,len=38>|streamable-http|https://mcp.ensem.dev/plane/http/api-key/mcp
DB plane row count: 1
```

⇒ row **created**, `is_builtin=true`, `is_active=true`, **`config_schema_version == 3`**, **`headers['x-workspace-slug'] == e2e`**.

**REST view** (`leg-s2.mcp-servers.json`, bearer redacted at the writer): the plane entry carries `config_schema_version: "3"`, `is_builtin: true`, `is_active: true`, `transport: streamable-http`, `headers.x-workspace-slug: "e2e"`, `Authorization: "Bearer <redacted,len=38>"`.

**Boot-log plane lines (verbatim):**

```
23:49:05 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=sandbox source=explicit-marker slug=e2e
23:49:05 - daemon.manager - INFO - Created built-in MCP server: plane
23:49:06 - daemon.services.mcp_service - INFO - eager_warm_schemas: primed 0/2 MCP server schema(s) (plane: 0 tools, webfetch: 0 tools)
```

`source=explicit-marker` proves the **marker** classified the environment (not an auto-derivation). The resolved plane tool list is **EMPTY by design** (dummy bearer ⇒ handshake fails); per the G2-fold iter-003 ruling the tool list is **not** asserted.

**THE VACUITY TRIPWIRE (re-run non-vacuously — see §6 N2):** env-name census = **24 names**; `PLANE_MCP_URL` PRESENT, `PLANE_MCP_API_KEY` PRESENT, `PLANE_MCP_WORKSPACE_SLUG` PRESENT; the literal `missing environment configuration` (the `CAUSE_UNAVAILABLE` skip reason) is **ABSENT from the real boot log** (searched at the moment of the re-run: 327 lines / 48 576 bytes, existence and size asserted **first** so the grep could not silently read an empty file) ⇒ **TRIPWIRE CLEAR — the leg is NOT vacuous** (the refusal/allow path was reached with the credentials present, not via the missing-credential skip). The committed artifact is the log's **final** state after the graceful shutdown (424 lines / 56 919 bytes); the tripwire was re-run against the pre-shutdown log and the committed log contains no such string either.

**Error / warning census — identical class shape to the lane's own rehearsal (NOT a v0.28.7 defect):**

| | rehearsal L1 (source-run) | rehearsal L4 | **staged s2 (frozen binary)** |
|---|---|---|---|
| ERROR lines | 8 | 8 | **8** |
| WARNING lines | 27 | 29 | **15** (the shutdown-phase worker warnings appear only after teardown) |

The 8 ERROR classes are byte-identical across all three: `2× webfetch warmup_pool FileNotFoundError` + `1× webfetch warm up pool` + `2× plane "Connection closed"` + `2× "Failed to connect MCP server: Connection closed"` + `2× "[Errno 2] No such file or directory"`. The `webfetch` class is the built-in trying to exec `uvx` (absent from the daemon PATH) and the `plane` class is the dummy-bearer handshake failing **by design** — both are the **inherited harness shape**, proven by comparison against the lane's own committed rehearsal logs, not an artifact regression.

**Journal resolution (the live install was never touched):**

```
23:49:05 - daemon.mcp.init_guard - WARNING - MCP state-transition journal write failed (server=webfetch state=degraded):
           journal absent at /tmp/v0287-smoke.s2.home.iqVEaC/agents-ensemble/releases/state.json
23:49:12 - daemon.services.upgrade_journal_sweep - INFO - UpgradeJournalSweepService started: ... install_dir=<none — dev/unresolved>
23:49:12 - daemon.api - INFO - UpgradeJournalSweepService started: ... install_dir=<none — dev/unresolved>
```

The promote journal resolved **inside the FAKE_HOME**; the live path `/home/nea/agents-ensemble` is referenced **0 times** in the whole boot log.

### Teardown (SIGTERM-only)

Ownership inspected **before** any signal (cmdline + cwd + exe + cgroup + environ + every bound port):

```
pid 2404386  cmdline=/tmp/v0287-smoke.s2.scratch.ufPDkR/ensemble-prod
             cwd=/tmp/v0287-smoke.s2.scratch.ufPDkR
             environ: HOME=<FAKE_HOME>  ENSEMBLE_SELF_ENV=sandbox  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
                      POSTGRES_HOST=127.0.0.1 POSTGRES_DB=ens_v0287_s2_…  PORT=DAEMON_PORT=8951
                      MCP_DISABLE_BUILT_IN_CONTEXT7=true  PLANE_MCP_WORKSPACE_SLUG=e2e
             bound ports: ONLY 127.0.0.1:8951        (live :9797 owner = pid 2400252 — a DIFFERENT pid)
```

`SIGTERM → child gone after 1 s; bootloader parent exited on its own after 1 s; NO SIGKILL`.
Graceful: `Graceful shutdown complete` · `Application shutdown complete.` · `Finished server process [2404386]`.

| teardown check | result |
|---|---|
| orphan `python -m daemon` | none |
| processes referencing the scratch path | **0** |
| anything bound to port 8951 | **0** (port FREE) |
| scratch DB remaining / scratch role remaining | **0 / 0** (both dropped) |
| scratch root + FAKE_HOME removed | yes (0 dirs left) |
| surviving context7/npm processes | **attributed to the LIVE/DEMO daemons by cwd+cgroup** (`/home/nea/agents-ensemble` + `ensemble-main.service`; `/home/nea/agents-ensemble-demo` + `ensemble-demo.service`) — **not** ours (our smoke spawned no stdio child; context7 was disabled) |

### LIVE FENCE — PRE == MID == POST

| | PRE 23:47:27Z | MID 23:50:31Z | POST 23:51:02Z |
|---|---|---|---|
| live `state.json` sha256 | `0fecefc399d52471db4269bc8c36f12807a92d2896aa1335b50158ebb52feb90` | **identical** | **identical** ✅ |
| live `current` symlink | `releases/v0.28.7` | `releases/v0.28.7` | `releases/v0.28.7` ✅ |
| live `:9797` pid | 2400252 | 2400252 | 2400252 ✅ |
| live `:9797` uptime | 482.3 s | 665.7 s | 697.0 s ✅ continuous |
| live livez | `alive 0.28.7` | `alive 0.28.7` | `alive 0.28.7` ✅ |
| demo `:7979` pid | 525852 | 525852 | 525852 ✅ |
| demo livez / uptime | `alive 0.16.3` / 138243.5 s | 138426.8 s | 138458.1 s ✅ continuous |

**The live promote journal was never written ⇒ no live ceremony nonce could have been pruned.** (The PRE sha was recorded FRESH by this lane — the shas quoted elsewhere for earlier epochs are not reused.)

---

## 3. S3 — BINARY-LEVEL LANE CONTENT (one-way control)

**Method (house precedent):** `CArchive` → `PYZ` → unmarshal → walk code objects, collecting `co_names` / `co_varnames` / `co_freevars` (identifiers) and `co_consts` strings + string-tuples (literals). Raw strings over a **compressed** PYZ are structurally false-negative — the PYZ is decompressed and unmarshalled first. **Execution-free**; neither binary was executed to extract. Interpreter: the uv-managed **Python 3.14.7** (the certified 3.13 interpreter cannot read a 3.14 artifact); PyInstaller 6.19.0.

**Control binary:** `releases/v0.28.4/ensemble-prod` (the prior release — still on disk; read-only). It is the correct negative control because v0.28.4 predates the lane. *(Note §6 N1: the currently-live binary is now v0.28.7 — byte-identical to the staged artifact — so the control had to be the prior release.)*

**Result: 49/49 assertions, 0 mismatches.** Full matrix in `EVIDENCE/v0287-staged-2026-11-02/s3-matrix.txt`.

**Direct schema-version discriminator (extracted from the binaries, not the source):**

```
plane builtin config schema_version   STAGED = ['3']      CONTROL (v0.28.4) = ['2']
```

| family | representative assertions | STAGED | CONTROL |
|---|---|---|---|
| **(a)** workspace resolver + D-1 explicit-marker predicate | `daemon.plane_workspace` module (new in the lane) — `resolve_plane_workspace`, `normalize_workspace_slug`, `current_plane_workspace`, `reset_resolution_cache`, `_escape_for_reason`, `_header_value`, `_has_header`, `VALID_ENVS`, `PRODUCTION_WORKSPACE_SLUG`, `TEST_WORKSPACE_SLUG`, `REASON_PRODUCTION_ONLY`, `REASON_UNSET_SLUG`; literal `explicit-marker`, `PLANE_MCP_WORKSPACE_SLUG`, `ensemble_prod`; in `plane.py`: `Plane workspace resolution: env=%s source=%s slug=%s`, `PlaneWorkspaceResolution` | **PRESENT** | **ABSENT** |
| **(b)** corrected classifier sets (73 / 104 / 0) | READ tuple **exactly** `('list_','get_','search_','retrieve_','count_','read_')`; WRITE tuple **exactly** `('create_','update_','delete_','remove_','set_','manage_','transfer_','complete_','link_','import_','attach_','detach_','upload_','resolve_')`; the **pre-lane** tuples `('list_','get_','search_')` and `('create_','update_','delete_','add_','remove_','set_','edit_','assign_')` | **PRESENT** / pre-lane **ABSENT** | pre-lane **PRESENT** / corrected **ABSENT** |
| **(c)** schema-version-3 heal | `workspace scoping` (the v3 bump docstring); in `manager.py`: `config refreshed — persisted workspace slug differed from the resolved slug`, `slug_drift`, `resolved_slug` | **PRESENT** | **ABSENT** |
| **(d)** prefix-sweep ownership guard (`plane_sync_project` exclusion) | `daemon.tools.instance`: `_owned_elsewhere` (the helper — **0 occurrences pre-lane, 3 in the staged tree**), `owned_elsewhere` | `_owned_elsewhere` **PRESENT** | **ABSENT** |
| **(e)** probe-path authority + scrub + escape-completion | `daemon.routers.mcp_servers`: `_scrub_auth_echo`, `_normalize_probe_url`, `_plane_probe_refusal`, `_escape_for_reason`, `_BEARER_ECHO_RE`, `_AUTH_ASSIGN_ECHO_RE`, `_mcp_kill_switch_enabled` | **PRESENT** | **ABSENT** |
| **(f)** operator route gates | `_plane_workspace_write_refusal`; literals `the operator write route is refused before any row is created or modified`, `Plane workspace policy refused this operation:`, `PLANE_MCP_ENABLED=false`, `Plane workspace mismatch: the submitted x-workspace-slug` | **PRESENT** | **ABSENT** |
| **(g)** loud-skip typed reasons (verbatim) | `workspace 'nea' is production-only; this environment resolves to {label}`; `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'`; `Builtin '%s' skipped — workspace refused (POLICY): %s%s`; `Builtin '%s' deactivated — workspace refused (POLICY): %s%s`; `CAUSE_WORKSPACE_REFUSED` (manager + base); `the builtin was POLICY-REFUSED this boot` | **PRESENT** | **ABSENT** |
| **(h)** registry attribution (the lane's support surface) | `daemon.registry`: `agents_referencing_builtin_mcp`, `agents_by_mcp_full_access` | **PRESENT** | **ABSENT** |

### (h) — the AGENTS PAYLOAD: the A′-narrowed grant (28 literal names, no bare category entry)

| | staged payload | control payload (v0.28.4) |
|---|---|---|
| the 28 `gate-i-keep-set.json` names present | **28/28**, ALL as literal entries in `meta.allow` (61 entries total) | **0/28** |
| bare `"plane"` CATEGORY entry in `allow`/`deny` | **NONE** ✅ | **PRESENT** (`allow` contains `"plane"`) |
| `meta.mcp_full_access` | `["plane"]` | `["plane"]` |

The control payload carries the exact leak the A′-narrowing closes: a bare `"plane"` category entry that the prefix sweep could populate with `plane_sync_project`. The staged payload replaces it with the 28 explicit tool names.

---

## 4. S4 — CEREMONY INPUTS

### S4.1 — `state.json` promote fields (READ-ONLY; byte-intact across the whole verification)

```
current           = 'v0.28.7'
previous          = 'v0.28.4'
in_flight         = None
pending_op        = None
quarantined       = ['v0.28.5']      (pre-existing; NOT touched)
pending_actions   = {}
pending_restart   = None
pending_wakes     = {}
cooldown_until    = '2026-11-01T22:28:40Z'
rollback_window_count = {'24h': 2, 'window_start': '2026-11-01T22:32:36Z'}
```

> **DISCREPANCY vs the dispatch brief (reported, not absorbed — see §6 N1):** the brief stated `current=v0.28.4, previous=v0.28.3`. The **actual** values are `current=v0.28.7, previous=v0.28.4` because the user ceremony **already promoted v0.28.7 at 23:40:45Z**, ~7 minutes before this lane's fence PRE. The journal's own event trail (read-only) records it: `promote-start 23:37:39Z` → `intent_flip 23:37:53Z (integrity verified)` → `supervision_boot 23:39:35Z` → `commit 23:40:45Z "promote v0.28.7 committed (gate+soak green; previous=v0.28.4)"`. The in-flight / pending-op / quarantine fields are exactly as the brief expected. **`in_flight=None` and `pending_op=None` ⇒ no promote is in flight; this lane performed NO promote and wrote NO state.**

### S4.2 — Ceremony command (verbatim)

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.7 bash scripts/upgrade/promote.sh live --f2-verified-closed
```

### S4.3 — Retention census (`releases/`)

```
v0.28.4   v0.28.6   v0.28.7      -> 3 release dirs   (keep-3 target)
current -> releases/v0.28.7
```

> **DISCREPANCY vs the dispatch brief (reported — §6 N7):** the brief expected 5 dirs (keep-3 over-retention). The actual census is **3** — the keep-3 retention already ran as part of the 23:40Z promote, so `v0.28.3` and `v0.28.5` are no longer on disk (`v0.28.5` survives only as the journal's `quarantined` entry). Protected set present: live `v0.28.7` + `v0.28.6` + `v0.28.4` (previous). **Eviction is machinery-owned — NOTHING was evicted by this lane.** (Four `rollback.lock.d.stale.*` directories also sit in `releases/`; they are machinery-owned stale locks, not releases.)

---

## 5. THE GITER ANOMALY — carried verbatim, and independently re-verified

**Carried verbatim from the dispatch:**

> GITER ANOMALY (CARRY VERBATIM in your evidence + independently re-verify the FINAL artifact): stage.sh first-build race — the first staged binary (496b655…) was copied during PyInstaller's EXE finalization window; fixed via existing-binary re-stage; the FINAL artifact (6f652d6b…) is the one you verify.

**Independent re-verification by this lane (the FINAL artifact, re-hashed from disk — the sidecar's recorded value was never trusted):**

| waypoint | sha256 |
|---|---|
| `releases/v0.28.7/ensemble-prod` (on disk) | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| `ensemble-src-wt-stage-v0287/dist/ensemble-prod` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| `manifest.json.binary_sha256` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| sidecar `artifact_sha256` | `6f652d6bb29239709787bf2f1bcb5a00f26bfbb17d644bc4169d2f873261ef49` |
| **the FIRST-build artifact (`496b655…`) anywhere in the release dir or the staging `dist/`** | **0 files** — the raced artifact is **not** what shipped ✅ |

Ordering corroborates the re-stage: sidecar `build_at 23:26:23Z` < release-dir binary mtime `23:32:00Z` ⇒ the release copy was taken from the **completed** binary. The shipped binary is executable (`-rwxr-xr-x`), boots (S2) and carries the full lane content (S3).

**Statement:** the GITER ANOMALY is **CLOSED by independent re-verification** — the released artifact is the finalized binary at all four waypoints, and the raced first-build artifact is absent from the release.

---

## 6. DEVIATIONS — reported, not absorbed

**N1 — the artifact was ALREADY PROMOTED when this verification began (disposition: reported, not absorbed).**
The dispatch brief stated `LIVE = v0.28.4`. At the fence PRE (23:47:27Z) the live daemon was **v0.28.7** (`current -> releases/v0.28.7`, livez `0.28.7`, unit epoch 23:39:09Z, `NRestarts=0`), with the journal's own trail showing the user's ceremony committed at **23:40:45Z**. This is a **task-premise staleness**, not a defect: the verification itself is unaffected (the staged artifact is byte-identical to the promoted one — same sha `6f652d6b…` — so S1/S2/S3 remain exactly as meaningful). Two consequences were handled explicitly: (i) the **S3 negative control had to be the prior release** `releases/v0.28.4` (still on disk, still pre-lane) rather than "the live binary", and (ii) **S4.1's `current`/`previous` values differ from the brief** (§4). The live fence was recorded against a **fresh** PRE sha (`0fecefc3…`), never a sha quoted from an earlier epoch. *No promote was performed by this lane; `in_flight=None`, `pending_op=None` throughout.*

**N2 — two of the committed recipe's greps were vacuous on the first pass; re-run non-vacuously.**
The committed `assert_leg.sh` reads the boot log as `leg-<L>.boot.log`, but this lane writes `leg-<L>.boot-log.txt` (`.log` is gitignored by the repo — the dispatch mandated the `.boot-log.txt` naming). The mismatch made `grep` fail on a **non-existent** file, so the boot-log line dump and the vacuity-tripwire check both reported on an empty input — a textbook false-GREEN. Both were **re-run against the real log** (327 lines, 48 576 bytes, existence and size asserted first) and are reported in §2 as genuine results: the four plane/builtin lines are present verbatim and the tripwire is CLEAR. *The `assert_leg.sh`/`teardown_leg.sh` scripts themselves were left byte-identical to the committed recipe (only `RUN` changed) so the committed logic is unaltered; the two greps were re-executed separately rather than by editing the recipe script.*

**N3 — the staged smoke exercised one builtin + plane, not the full builtin set.** `MCP_DISABLE_BUILT_IN_CONTEXT7=true` (the lane rehearsal's D-2 rationale): context7 is a **stdio** builtin whose boot spawns an npm fetch (`npx -y @upstash/context7-mcp`) — network + slow + potential orphan node processes. The plane claims are independent of it. Disclosed: `readyz` reports 4 components (database / queue_freshness / services / checkpoint_saver) all true; the context7 component is not exercised. *Not a plane-behaviour gap.*

**N4 — `plugins/` was not copied into the scratch.** A frozen onefile build scans the CWD for plugins; with `plugins/` absent the registry scans **0 plugins**. This is the documented frozen-build shape, not a defect; the payload's plugin tree is separately proven intact by the manifest re-hash (4930/4930, 0 discrepancies).

**N5 — the ERROR census is 8 lines, not the skill's 7-line baseline.** Proven **inherited harness shape**, not an artifact regression: the class distribution is byte-identical to the lane's own committed rehearsal logs (L1 = 8, L4 = 8) — 3× webfetch-`uvx`-absent + 4× plane dummy-bearer handshake + 1× connect. (The skill's 7-line baseline assumes the `.env` is sourced inside a systemd unit; this lane's recipe deliberately does **not** source any `.env` — the rehearsal's rationale, which trades 3 extra webfetch ERROR lines for a credential-free environment.)

**N6 — the URL cross-check targeted the LIVE install `.env`.** The staging worktree has **no** armed `.env` (only `.env.example` / `.env.prod.example`), so the recipe's read-only sha-compare cross-check was pointed at `/home/nea/agents-ensemble/.env` (read-only, hash-compare only, the value never echoed) instead of the rehearsal's armed lane worktree. Result: **MATCH**. No `.env` was sourced into any smoke env and no `.env` was written.

**N7 — the retention census is 3 dirs, not the 5 the brief expected.** See §4.3 — the keep-3 retention already ran during the 23:40Z promote. Nothing evicted by this lane.

**N8 — two S3 tokens are *not* discriminators by themselves, and are recorded as such.** `owned_elsewhere` is present in **both** binaries (post-lane it is a `co_freevars` binding of the generator expression, pre-lane a `co_varname` of the inline code); the actual discriminator for family (d) is the helper **`_owned_elsewhere`** (0 occurrences pre-lane → present in the staged tree). Likewise `config_schema_version` and `[REDACTED]` exist in both binaries; the discriminators for families (c) and (e) are the new literals/names listed in §3. The matrix is stated with the *correct* expectation per token rather than being forced to look uniform.

**N9 — a housekeeping note on the S3 extractor.** `ZlibArchiveReader.extract()` in PyInstaller 6.x returns the **already-unmarshalled** code object; a naive `marshal.loads()` on it raises. The extractor was corrected accordingly, and the matrix was re-run to completion. An initial extractor build that silently swallowed this (`except: continue`) produced a uniformly-ABSENT matrix — that run was discarded, and the corrected run (49/49) is the one recorded. *(Recorded because "all tokens absent in both binaries" is precisely the shape a broken extractor produces, and it must never be mistaken for a pass or a fail.)*

**N10 — the boot ran inside the LIVE daemon's cgroup; cgroup isolation was AVAILABLE but NOT exercised (disclosed, not absorbed).**
The house skill for staged boot smokes states that the boot-sweep kill-switches are *"necessary but NOT sufficient"* and that **cgroup isolation is the actual control**, because a staged binary booted from a shell that shares the live daemon's cgroup can SIGTERM live same-cgroup aux children. This lane's boot did run in that shared cgroup: the owned child's `/proc/<pid>/cgroup` = `0::/system.slice/ensemble-main.service`. **Mitigations that WERE applied** (all mandated by the dispatch and by the lane's own committed recipe): the staged binary was **copied into the scratch** (so the path-derived root is the scratch, never the live install root); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` was **pinned explicitly** (never `env -u`); the promote journal resolved **inside the FAKE_HOME**; `systemd-run --user` isolation was deliberately **not** used because the dispatch mandated executing the lane's **own committed recipe**, which prescribes the plain-background form. **The host does support `systemd-run --user`** (probed: a transient probe unit ran to completion), so the stronger control was technically available — a future run of this lane should adopt it. **No live process was harmed, and the evidence is positive rather than inferential:** the live daemon pid `2400252` and the demo pid `525852` were continuous across PRE / MID / POST / POST-FINAL; the live and demo `context7`/`npm` children were still alive and correctly attributed at teardown; and the boot log contains only `maintenance boot sweep: 0 interrupted run(s)` and `Stale-pause boot sweep: {'candidates': 0, ...}` — **no prior-incarnation kills**. *Scope of the residual risk: a future staged boot in the shared cgroup COULD kill live aux children; the recommended hardening is the `systemd-run --user` transient unit (available on this host), which the lane's recipe does not currently use.*

---

## 7. Credential hygiene (fail-closed scan over the committed evidence)

| check | result |
|---|---|
| real `Bearer <token>` strings in the committed evidence | **0** (3 occurrences of the literal `Bearer ` are all `<redacted,len=38>`) |
| scratch role password in the committed evidence | **0** (redacted at the writer in `leg-s2.env.redacted`) |
| live DB address `10.44.0.2` in the committed evidence | **0** |
| `sk-…` style keys in the committed evidence | **0** |
| `PLANE_MCP_API_KEY` in the **release payload** | **0** files (NB6) |
| the only `plane_api_…` literal in the evidence | the **DUMMY** `plane_api_staged_smoke_dummy_s2` in the redacted env file — identical in kind to the rehearsal's committed `leg-l1.env.redacted` (`…_dummy_l1`) |

No real bearer entered any smoke env, log, or artifact.

---

## 8. Verdict

```
STAGED-VERIFIED: PASS-WITH-NOTES
```

**Gates:** S1 **PASS** · S2 **PASS** · S3 **PASS** · S4 **PASS** — no gate failed, no assertion mismatch remains outstanding.

**Notes (10):** N1 the artifact was already promoted live at 23:40:45Z (task-premise staleness; verification unaffected; S3's control had to be the prior release; S4.1's `current`/`previous` differ from the brief) · N2 two committed-recipe greps were vacuous on the first pass (filename mismatch) and were re-run non-vacuously · N3 context7 disabled (stdio npm fetch) — one builtin + plane exercised · N4 `plugins/` not copied (frozen-build 0-plugin scan) · N5 the 8-line ERROR census is the inherited harness shape (proved against the rehearsal's own L1/L4 logs) · N6 the URL cross-check targeted the live install `.env` (read-only, hash-compare, MATCH) · N7 retention is 3 dirs, not 5 (keep-3 already ran) · N8 two S3 tokens are non-discriminators by themselves and are labelled as such · N9 the S3 extractor needed a fix (PyInstaller 6.x returns an already-unmarshalled code object); the discarded all-ABSENT run is disclosed · N10 the boot ran in the live daemon's cgroup (kill-switch pinned; `systemd-run --user` isolation was available but not used because the lane's committed recipe prescribes the plain-background form) — no live process harmed, pids continuous.

**Unchanged / pre-existing (not this lane's scope, recorded for the reviewer):** the live daemon's context7 MCP children and the demo daemon's children were alive during the teardown census — attributed by cwd + cgroup to `ensemble-main.service` / `ensemble-demo.service`, **not** to this smoke. The 4 stale `rollback.lock.d.stale.*` dirs in `releases/` are machinery-owned.

**Evidence:** `.agents/tester/EVIDENCE/v0287-staged-2026-11-02/` (15 files) · **Branch:** `tester/v0287-staged-verification` (PUSHED, **NOT** merged — leader sweeps) · **Staging worktree** `/home/nea/ensemble-src-wt-stage-v0287` left **untouched** (porcelain 0 lines PRE and POST).
