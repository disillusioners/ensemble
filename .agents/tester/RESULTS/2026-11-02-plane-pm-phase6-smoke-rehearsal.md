# GATE E — Phase-6 staged-smoke REHEARSAL (S3 legs L1–L4, source-run form) — lane `plane-pm-issue-tools`

**Executed:** 2026-11-02 22:38Z → 22:59Z · **Tree:** `eae2059deb1762e7233d0e2357fcfa8d71f7f896` (`test(plane-pm): tier-2 whole-tree gate evidence`), branch `feature/plane-pm-issue-tools`, worktree `/home/nea/ensemble-src-wt-plane-pm` (clean at start; `git status --porcelain` empty).
**Binding recipe:** `.agents/shared/planning/plane-pm-issue-tools/phase6-plan.md` §3 **G14** (the S1–S4 table, the S3 four-legs table, the vacuity tripwire, the G2-fold NB6 credential-provenance rule, the G2-fold NB9 L4 SAFE FORM, and the absolute Boot-smoke rule).
**Raw artifacts:** `.agents/tester/EVIDENCE/plane-pm-smoke-2026-11-02/` (45 files — boot logs, per-leg assertion/row/teardown outputs, the env-name censuses, the fence captures, the harness scripts, and the redacted per-leg env files).

**What this is.** A **REHEARSAL** of the G14 staged verification's **S3** lane, run against the **worktree SOURCE tree** (source-run form). **No staged artifact exists yet** (giter has not staged) — so **S1** (artifact integrity) and **S2** (in-binary content) are **NOT RUNNABLE here**; they are recorded below as ready-to-execute checklists for the post-stage dispatch. The real S1–S4 run happens after giter stages.

**Headline verdict:** **4/4 legs PASS. 0 VACUOUS. 0 FAIL. The vacuity tripwire is CLEAR on both negative legs (the refusal was reached via the POLICY path, never the missing-credential skip).** The live fence held (state.json sha PRE == MID == POST).

---

## 0. Verdict summary

| Leg | Classification | `ENSEMBLE_SELF_ENV` | slug | URL + key | Expected | Observed | Verdict |
|---|---|---|---|---|---|---|---|
| **L1** | positive, non-live | `dev` | `e2e` | PRESENT (dummy) | row created; slug header `e2e` | row created (schema_version `3`), slug header `e2e`, 1 row | **PASS** (control) |
| **L2** | negative, non-live | `dev` | `nea` | PRESENT (dummy) | no row; POLICY refusal `workspace 'nea' is production-only…` | 0 rows; typed POLICY WARNING verbatim; plane absent from the warm pool | **PASS** (not vacuous) |
| **L3** | negative, non-live | `dev` | **unset** | PRESENT (dummy) | no row; POLICY refusal `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'` | 0 rows; typed POLICY WARNING verbatim | **PASS** (not vacuous) |
| **L4** | positive, LIVE classification | `live` | `nea` | PRESENT (dummy) | row created; slug header `nea`; every derived path inside the scratch | row created, slug header `nea`, 1 row; journal resolved inside the FAKE_HOME | **PASS** (safe form) |

**The L1 → L2/L3 control is live and non-vacuous:** the SAME constructed env shape (identical 25-name allowlist; only the slug value differs) produced **a row in L1** and **no row in L2/L3**. If the credentials had been absent, L2/L3 would have taken the skip path — the tripwire below proves they did not.

---

## 1. The four boot lines (VERBATIM from the boot logs)

```
### L1 (dev + e2e)
22:47:22 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=dev source=explicit-marker slug=e2e
22:47:22 - daemon.manager - INFO - Created built-in MCP server: plane

### L2 (dev + nea)  — the POLICY refusal
22:51:48 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=dev source=explicit-marker slug=(account-default)
22:51:48 - daemon.manager - WARNING - Builtin 'plane' skipped — workspace refused (POLICY): workspace 'nea' is production-only; this environment resolves to dev; agents with mcp_full_access=['plane']: project-manager

### L3 (dev + slug unset)  — the POLICY refusal
22:53:57 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=dev source=explicit-marker slug=(account-default)
22:53:58 - daemon.manager - WARNING - Builtin 'plane' skipped — workspace refused (POLICY): PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'; agents with mcp_full_access=['plane']: project-manager

### L4 (live + nea)  — the live-exempt branch
22:56:38 - daemon.mcp.builtin_servers.plane - INFO - Plane workspace resolution: env=live source=explicit-marker slug=nea
22:56:38 - daemon.manager - INFO - Created built-in MCP server: plane
```

Both negative legs reached the refusal through **`workspace refused (POLICY)`** — the typed reason strings (`REASON_PRODUCTION_ONLY` / `REASON_UNSET_SLUG`, `daemon/plane_workspace.py:131-143`) are interpolated **verbatim** by the manager's skip WARNING, which is the only reason interpolator (R-3).

**Independent negative-surface proof (no plane tool surface in L2/L3):** the MCP warm pool primed **2** servers in L1 (`primed 0/2 MCP server schema(s) (plane: 0 tools, webfetch: 0 tools)`) and **1** in L2/L3 (`primed 0/1 MCP server schema(s) (webfetch: 0 tools)`) — the plane server is absent from the pool entirely in the refused legs. The REST view `GET /api/mcp-servers` contains **no** `plane` entry in L2/L3 (raw artifact: `leg-l2.mcp-servers.json`, `leg-l3.mcp-servers.json`).

> **Note on the L1 `plane: 0 tools` line — this is EXPECTED and is NOT a failure.** With the mandated dummy bearer the MCP handshake fails (`Failed to create session for 'plane': Connection closed`) ⇒ `list_tools()` returns an empty list. Per the G2-fold iter-003 ruling, **L1 does NOT assert a resolved `plane_*` tool list** (that would be a vacuous assertion); the tool-list behaviour is covered by the Phase-5 census with a real key. **L1 asserts the ROW + the resolved slug header.**

---

## 2. L1 — the row + slug-header proof (bearer REDACTED)

**SQL (the Authorization is redacted IN SQL — only the `Bearer ` prefix and the length leave the database):**

```
select name, is_builtin, is_active, config_schema_version,
       coalesce(config->'headers'->>'x-workspace-slug','<absent>'),
       left(coalesce(config->'headers'->>'Authorization',''),7) || '<redacted,len=' || length(...) || '>',
       coalesce(config->>'transport','<absent>'), coalesce(config->>'url','<absent>')
  from mcp_servers where name='plane';
```

| leg | row? | `is_builtin` | `is_active` | `config_schema_version` | `headers['x-workspace-slug']` | `Authorization` | transport | url |
|---|---|---|---|---|---|---|---|---|
| **L1** | 1 row | `true` | `true` | **`3`** | **`e2e`** | `Bearer <redacted,len=38>` (the DUMMY) | `streamable-http` | `https://mcp.ensem.dev/plane/http/api-key/mcp` |
| **L2** | **0 rows** | — | — | — | — | — | — | — |
| **L3** | **0 rows** | — | — | — | — | — | — | — |
| **L4** | 1 row | `true` | `true` | **`3`** | **`nea`** | `Bearer <redacted,len=38>` (the DUMMY) | `streamable-http` | `https://mcp.ensem.dev/plane/http/api-key/mcp` |

**REST view (`GET /api/mcp-servers`, redacted artifact `leg-l1.mcp-servers.json`):**

```json
{ "name": "plane", "is_active": true, "is_builtin": true, "config_schema_version": "3",
  "config": { "url": "https://mcp.ensem.dev/plane/http/api-key/mcp", "args": [],
              "headers": { "Authorization": "Bearer <redacted,len=38>", "x-workspace-slug": "e2e" },
              "transport": "streamable-http" } }
```

**`config_schema_version` = `"3"`** — the Phase-2 E4 bump (`"2"` → `"3"`) is materialised in the row, i.e. the healed/created config carries the **current** definition version.

**No work item was created in any leg.** The slug is a **header value**, not a data write: the Plane REST sync layer is a **no-op** in every leg — `PlaneSyncWatchdogService: Plane sync not configured (PLANE_BASE_URL / PLANE_API_KEY not set) — watchdog no-op (no re-drives…)` (`leg-l4.boot-log.txt`). The only outbound plane traffic is the MCP **schema-discovery handshake**, which fails on the dummy bearer and resolves to zero tools.

---

## 3. THE VACUITY TRIPWIRE (mechanical, every negative leg)

**Mechanism (two independent assertions per leg, both in `assert_leg.sh`):**

1. **The key-NAME census of the constructed env** (names only — no value is ever printed; the census file is `leg-<L>.envnames.txt`).
2. **The observed ABSENCE of a missing-credential skip reason in the boot log** — the literal `missing environment configuration` string (the `CAUSE_UNAVAILABLE` branch, `daemon/manager.py:3209-3221`) must NOT appear.

| leg | `PLANE_MCP_URL` | `PLANE_MCP_API_KEY` | `PLANE_MCP_WORKSPACE_SLUG` | env-name count | `missing environment configuration` in log | tripwire |
|---|---|---|---|---|---|---|
| L1 | PRESENT | PRESENT (dummy) | PRESENT (`e2e`) | 25 | absent | **CLEAR** |
| **L2** | **PRESENT** | **PRESENT (dummy)** | PRESENT (`nea`) | 25 | **absent** | **CLEAR → not vacuous** |
| **L3** | **PRESENT** | **PRESENT (dummy)** | **ABSENT (by design)** | 24 | **absent** | **CLEAR → not vacuous** |
| L4 | PRESENT | PRESENT (dummy) | PRESENT (`nea`) | 25 | absent | **CLEAR** |

**How the credentials are guaranteed PRESENT (the credential-provenance rule, G2-fold NB6):** each leg's env is built by an **`env -i` allowlist** — the ambient environment (the worker shell is live-poisoned: `POSTGRES_DB=ensemble_prod`, `ENSEMBLE_SELF_ENV=live`) is **structurally** excluded, and the three plane vars are set **explicitly per leg**:

* `PLANE_MCP_URL` = the **real, public** Plane URL (`https://mcp.ensem.dev/plane/http/api-key/mcp` — public in `.env.example:222`). The URL used was **cross-checked by sha256 against the armed worktree `.env`** (after the one-outer-quote-pair strip the daemon itself performs): **MATCH** on all four legs (`setup-l*.setup-log.txt` → `public plane url vs armed worktree .env: MATCH`).
* `PLANE_MCP_API_KEY` = the **per-leg dummy literal** `plane_api_staged_smoke_dummy_l1` … `_l4` — asserted by a pre-boot check in `boot_leg.sh` (`grep -qx "PLANE_MCP_API_KEY=plane_api_staged_smoke_dummy_<leg>"`).
* `PLANE_MCP_WORKSPACE_SLUG` = per leg; **structurally absent** in L3 (the var is never added to the `env -i` argv — not `unset`, not `env -u`).

**No real bearer entered any smoke env, log, or artifact.** Proof: a scan of the four boot logs finds **0** occurrences of the dummy key, **0** `Bearer <token>` strings, **0** DSNs, and **0** occurrences of the scratch role password. The armed worktree `.env` was **read-only** (the URL cross-check) and was **never sourced** into a smoke env and never written.

**The control condition (the plan's "if L1 does not produce a row with the same credentials, L2/L3 prove nothing") is SATISFIED:** L1 produced the row with the identical dummy credential shape. → L2/L3 are meaningful.

---

## 4. L4 — the SAFE FORM and its disclosures

| Element | Required (G2-fold NB9) | Observed |
|---|---|---|
| **The binary** | copied into the scratch root ⇒ the path-derived root IS the scratch | **SOURCE-RUN FORM (rehearsal deviation — see §7 D-1):** `cd <worktree> && /home/nea/ensemble-src/.venv/bin/python -m daemon`. No frozen binary exists pre-stage. The path-derived root of the source-run is the **worktree**, never the live install root. |
| **The environment** | `ENSEMBLE_SELF_ENV=live` pinned EXPLICITLY | `ENSEMBLE_SELF_ENV=live` in the `env -i` allowlist. **Proven by the resolver's own line:** `Plane workspace resolution: env=live source=explicit-marker slug=nea` — `source=explicit-marker` proves the **marker** classified it, NOT the own-DB arm. |
| **The scrub** | per-variable env scrub + `mktemp` FAKE_HOME + escape guard | `env -i` (structural — a per-variable scrub is unnecessary when the environment is built from an explicit allowlist); `FAKE_HOME=/tmp/plane-pm-smoke.l4.home.JPXiKV`; **escape guard asserted** — neither the scratch root nor the fake home nor `$FAKE_HOME/agents-ensemble` resolves under `/home/nea/agents-ensemble*` or `/home/nea/ensemble-src*` (`setup_leg.sh`, fail-closed exit 94). |
| **The workspace** | slug `nea` (a header value, not a data write) | slug `nea` ⇒ the row's `x-workspace-slug` header is `nea`. No work item created (§2). |
| **The credentials** | the dummy | `plane_api_staged_smoke_dummy_l4`. |
| **The DB** | scratch PG on a throwaway role; `ensemble_prod` never addressed | throwaway role `plane_pm_smoke_l4_*` + DB `ens_smoke_l4_*` on the **LOCAL** cluster `127.0.0.1:5432`; the daemon's engine is built from `POSTGRES_*` (the scratch), and the resolver's own-DB arm is short-circuited by the explicit marker. **`ensemble_prod` was never addressed** — it does not even exist on the local cluster (the prod DB lives on the remote host). |
| **The fence** | live `state.json` sha PRE == MID == POST; live pid + uptime continuous; live `current` symlink unchanged | **HELD** — see §5. |
| **The port** | neither 9797 nor 7979 | **8951** (scratch). |

**The live promote journal resolved INSIDE the FAKE_HOME — proof:**

```
22:56:46 - daemon.services.upgrade_journal_sweep - INFO - UpgradeJournalSweepService started: …, install_dir=/tmp/plane-pm-smoke.l4.home.JPXiKV/agents-ensemble
22:56:39 - daemon.mcp.init_guard - WARNING - MCP state-transition journal write failed (server=webfetch state=degraded): journal absent at /tmp/plane-pm-smoke.l4.home.JPXiKV/agents-ensemble/releases/state.json
```

The live install dir is derived purely from `Path.home()` (`daemon/tools/upgrade_tools.py::_install_root_candidates`), so the FAKE_HOME redirects it into the scratch. **The live journal `/home/nea/agents-ensemble/releases/state.json` was never opened for write** — its sha is byte-identical PRE/MID/POST (§5). In the non-live legs (L1–L3) the sweep resolves `install_dir=<none — dev/unresolved>` — no install dir at all.

### ⚠️ L4 DISCLOSURE (must survive into the staged artifact)

> **L4 is NOT a live-boot test.** The **classification** is live (`ENSEMBLE_SELF_ENV=live`, proven by `source=explicit-marker`); the **install is scratch** (scratch root, FAKE_HOME, scratch PG, scratch port). What L4 proves: *with the live classification explicitly asserted and every filesystem/data path still inside the scratch, the plane row materialises with the slug header `nea` — i.e. the live-branch behaviour is unchanged.* What it does NOT prove: that a live install was booted, that the live promote journal was exercised, or that the live MCP endpoint accepts the bearer. In the staged run, L4 additionally gets the **binary-copy** form (the staged binary copied into the scratch root ⇒ the path-derived root IS the scratch), which is strictly stronger than the source-run form used here.

---

## 5. S4 — the live fence (PRE / MID / POST) and the ceremony inputs

**The triple** (raw: `fence-pre.txt`, `fence-mid-l1.txt`, `fence-post.txt`):

| | PRE (22:40:59Z) | MID (after L1, 22:51:12Z) | POST (after L4 teardown, 22:58:38Z) |
|---|---|---|---|
| **live `state.json` sha256** | `b8d83f27c6c3a1d9fc9871d671a1c0842ae916bb3d30bc1ef249e4d8ead8f0cf` | **identical** | **identical** ✅ |
| **live `current` symlink** | `releases/v0.28.4` | `releases/v0.28.4` | `releases/v0.28.4` ✅ |
| **live `:9797` pid** | 2065195 | 2065195 | 2065195 ✅ |
| **live `:9797` uptime** | 5624.9 s | 6237.9 s | 6683.7 s ✅ (continuous, no restart) |
| **live livez** | `alive 0.28.4` | `alive 0.28.4` | `alive 0.28.4` ✅ |
| **demo `:7979` pid** | 525852 | 525852 | 525852 ✅ (untouched) |
| **demo `:7979` uptime** | 134255.5 s | 134868.5 s | 135314.3 s ✅ (continuous) |
| **demo livez** | `alive 0.16.3` | `alive 0.16.3` | `alive 0.16.3` ✅ |

A **fourth, post-commit re-check** (`fence-post-final.txt`, 23:03:27Z) recorded the same sha `b8d83f27…` with the live pid 2065195 still continuous (uptime 6972.6 s) and the demo pid 525852 still continuous (uptime 135603.2 s) — i.e. the fence held through the evidence write and the commit itself.

**The state.json sha is byte-identical across the whole rehearsal ⇒ the live promote journal was never written ⇒ no live ceremony nonce could have been pruned.**

**Ceremony inputs to record (for the giter stage + the user's ceremony):**

* **Promote command form:** `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v<slot> bash scripts/upgrade/promote.sh live --f2-verified-closed` — the slot is **re-derived at stage time** from the live census (W9); the plan's `v0.28.6` is an estimate only.
* **Outstanding precondition (ADJ-4):** the **live/demo credential re-mint** — the live Plane bearer is expired, so **live efficacy waits on the user**. Until then the live row carries the stale/expired bearer; this is an **operator** item, not a lane item.
* The staged artifact must additionally carry the **S1 payload assertion**: the release payload contains **no `PLANE_*` key material** (G2-fold NB6).

---

## 6. The scratch teardown proof (per leg)

| leg | scratch DB dropped | scratch role dropped | port freed | orphan `python -m daemon` | orphan MCP stdio children of this rehearsal | scratch + FAKE_HOME removed |
|---|---|---|---|---|---|---|
| L1 | `DROP DATABASE` (remaining 0) | `DROP ROLE` (remaining 0) | yes | none | none (see note) | yes |
| L2 | `DROP DATABASE` (remaining 0) | `DROP ROLE` (remaining 0) | yes | none | none (see note) | yes |
| L3 | `DROP DATABASE` (remaining 0) | `DROP ROLE` (remaining 0) | yes | none | none (see note) | yes |
| L4 | `DROP DATABASE` (remaining 0) | `DROP ROLE` (remaining 0) | yes | none | none (see note) | yes |

**Final census:** `pgrep -af "python -m daemon"` → **empty**; `ss -ltn | grep :8951` → **free**; `ls -d /tmp/plane-pm-smoke.*` → **none**; `pg_roles where rolname like 'plane_pm_smoke%'` → **none**; `pg_database where datname like 'ens_smoke%'` → **none**.

**Note on the `npm exec @upstash/context7-mcp` processes observed in the teardown census.** They are **NOT orphans of this rehearsal**: their `cwd` is `/home/nea/agents-ensemble` (the LIVE install) and their cgroup is `/system.slice/ensemble-main.service` (the LIVE systemd unit) — i.e. they are the **live daemon's** context7 stdio children. This rehearsal's own daemon ran with `MCP_DISABLE_BUILT_IN_CONTEXT7=true` (see §7 D-2) and spawned no stdio child at all.

**Teardown was SIGTERM-only.** Each daemon's `cmdline` (`/home/nea/ensemble-src/.venv/bin/python -m daemon`), `cwd` (the worktree) and **bound port (`127.0.0.1:8951` only)** were inspected **before** the signal (the "never kill a process without inspecting its bound port" rule). Each leg's log ends with `Graceful shutdown complete` / `Application shutdown complete` / `Finished server process [<pid>]`.

**`.env` invariants (no writes anywhere):**

| file | mode | mtime | sha256 (16) |
|---|---|---|---|
| worktree `.env` (ARMED — Gate B seeded it; **left exactly as-is**) | 600 | 2026-11-02 18:21:48Z (Gate B — **before** this rehearsal) | `ad09944b471cfdfe` |
| main checkout `.env` | 600 | 2026-11-01 09:22:49Z | `316124135557b241` |
| live install `.env` | 600 | 2026-11-01 20:27:18Z | `060f0f16618d7575` |

---

## 7. DEVIATIONS — reported, not absorbed

**D-1 — L4 used the SOURCE-RUN form, not the binary-copy form.** *Impact:* the staged run must copy the staged binary into the scratch root (G2-fold NB9) — the source-run form cannot, because no frozen binary exists pre-stage. *Assessment:* the source-run form is **strictly weaker** than the binary-copy form on exactly one axis — the path-derived root. Mitigation proven in this rehearsal: the **only** live-root-derived path in the live classification is the install dir, and it is a pure function of `Path.home()`, so the FAKE_HOME redirects it into the scratch (`install_dir=/tmp/…/agents-ensemble`). The staged run re-proves this with the copied binary.

**D-2 — `MCP_DISABLE_BUILT_IN_CONTEXT7=true` was set in every leg's env.** *Rationale:* context7 is a **stdio** builtin whose boot spawns `npx -y @upstash/context7-mcp` — an npm **fetch** at boot (network + slow + potential orphan node processes). The plane claims are **independent** of context7, and the disable switch is the documented per-builtin kill-switch. *Impact on the staged run:* **recommend the same** — it keeps the smoke deterministic and orphan-free. *Disclosed:* the staged smoke therefore exercises **one** builtin (webfetch) + plane, not the full builtin set. *Not* a plane-behaviour gap.

**D-3 — `HOST=127.0.0.1` was pinned in every leg.** *Rationale:* the scratch daemon binds **loopback only** (config.yaml's default is `0.0.0.0`), so the scratch HTTP surface is not exposed on the host's external interfaces. The staged run should do the same.

**D-4 — `RAG_IS_REQUIRED=false` + no `OPENAI_*` variables in the env.** *Rationale:* the boot-smoke asserts the **plane** behaviour; the LLM lane is irrelevant to it, and the rehearsal ran with **no LLM credential present at all** (the cleanest possible credential posture). *Observed:* `/livez` and `/readyz` both 200 with **all four components true** (`database`, `queue_freshness`, `services`, `checkpoint_saver`) ⇒ no LLM/RAG key is required for the boot-smoke's readiness gate. *Disclosed:* the staged run may add the LLM lane if it prefers a closer-to-production shape; **it must not add the Plane real key** (NB6).

**D-5 — one accidental read-only GET against the LIVE daemon during the first assertion attempt.** *Cause:* the first assertion ran under `/bin/sh` (the tool shell is **not** bash), so `source` failed → `$PORT` was empty → the shell fell back to the **ambient** `PORT=9797` and the two `curl`s hit the **live** daemon's `/livez` and `/readyz`. *Impact:* **two read-only GETs**; no mutation, no state change (the live fence sha/pid/uptime are unchanged and continuous — §5). *Lesson (this is the house hazard in its purest form):* **`$PORT` must never be read from the ambient shell** — the leg env file must be sourced by an explicit `bash` interpreter, and every curl must use an **absolute** port from the leg env file. *Corrected:* every later assertion ran `bash assert_leg.sh <leg>` with the port taken from the leg env file; the final fence proves the live daemon was never disturbed.

**D-6 — two aborted L1 setup attempts** (a buggy awk URL cross-check, then a buggy tripwire `grep`) created two scratch roots + two throwaway roles/DBs before the first successful boot. Both were **dropped and removed** at cleanup (final census: zero residual roles/DBs/dirs). No daemon was booted in either attempt (the tripwire/preflight exited **before** any bind).

**D-7 — the scratch `data/ensemble.json` records the auto-detect DEFAULTS** (`db=ensemble`, `user=ensemble`, `password=""`), not the env values. This is the **pre-existing** auto-detect persistence path (`daemon/ensemble_config.py::load_or_create` → `Config(database="postgres")` with default `PostgresConfig`). The **engine** is built from `POSTGRES_*` (the scratch) — proven by the row being found in the scratch DB. *Consequence for the fence:* the file-pin vector (`data/ensemble.json`) was **not** the poison vector here (`database=postgres`, consistent with the env), and the scratch file is inside the scratch. *Not this lane's code; recorded for the reviewer.*

**D-8 — `proc_stop` could not be used for the teardown.** The `exec` chain (shell → `bash` → `env` → `python`) breaks the process-manager's PID-ownership marker, so `proc_stop` **aborted the kill** (it did not signal anything — the safety behaviour is correct). Each daemon was therefore SIGTERMed **directly** after inspecting its `cmdline` + `cwd` + **bound port** (`127.0.0.1:8951` only) — the house rule is satisfied. *For the staged dispatch:* expect the same and use the same procedure (the staged binary is a single process; `proc_stop` may work there).

**D-9 — observed, NOT attributed to this lane:** at boot the plane row draws `WARNING - MCP server 'plane' row config invalid for warmup pool: 2 validation errors for McpStdioConfig` (the row is `streamable-http` but the warm pool validates a stdio shape). Pre-existing daemon behaviour, present in L1/L4 (the legs where a row exists). Recorded for the reviewer; **not** a leg failure.

---

## 8. S1 + S2 — ready-to-execute CHECKLISTS (NOT RUN HERE: no staged artifact exists pre-stage)

These are the recipes for the post-stage dispatch, so the real staged verification is a fast re-run of the S3 recipe above.

### S1 — artifact integrity (run against `releases/<slot>`)

1. **4-way sha256** of the binary: `releases/<slot>/ensemble-prod` **==** the stage-worktree `dist/` binary **==** `manifest.json.binary_sha256` **==** the provenance sidecar's `artifact_sha256`. (Re-hash on disk — never trust the sidecar's recorded value.)
2. **Provenance sidecars**: both present; `git_head` == the bump commit; `git_dirty=false`.
3. **Tag state**: the tag exists, is annotated, and peels to the bump commit; assert the **local-only / pushed** state matches the house convention at stage time.
4. **Source-less payload**: `releases/<slot>` contains **no** `scripts/`, **no** `.env`, **no** `*.build-provenance.json`, no `daemon/` source tree.
5. **Manifest delta** vs the live release: exactly the expected scalars (`binary_sha256`, `binary_version`, `version`, `staged_at`, and any tree-hash that moved); the per-file maps re-hash with 0 discrepancies.
6. **Prompt-artifact baseline**: `161/161` clean at the staged tree (`PROMPT-BASELINE: OK`).
7. **PLANE_* payload assertion (NB6):** `grep -rIl "PLANE_MCP_API_KEY" releases/<slot>` finds **no** key material — the payload carries **no** `PLANE_*` secret. (The URL is public and may appear in source; the **key** must not.)
8. **Migration census**: the embedded `.sql` set — count + distinct prefixes + the delta vs the live release.

### S2 — in-binary content (bytecode/raw-string level, NOT source level)

1. **New resolver symbols PRESENT in the staged binary** and **ABSENT in the live binary**: e.g. `resolve_plane_workspace`, `normalize_workspace_slug`, `REASON_PRODUCTION_ONLY`, `REASON_UNSET_SLUG`, `x-workspace-slug`, `workspace refused (POLICY)`, `CAUSE_WORKSPACE_REFUSED`. (Check both the compiled binary's embedded strings and the packaged module bytes.)
2. **`schema_version` bump present**: the definition's `config_schema_version` is `"3"` in the staged binary (and the live binary still says `"2"`).
3. **The loud-skip code path present**: the skip WARNING string (`skipped — workspace refused (POLICY)`) is in the staged binary's strings.
4. **Negative control**: the **live** binary reads **GREEN** on the *absence* assertions (i.e. it does NOT contain the new symbols) — this proves the assertions can discriminate.
5. **The kill-switch present** (if the lane ships one): `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`-style knob names, or the lane's own knob, are present in the staged binary and absent in the live one.

> **Do not assert a resolved `plane_*` tool list in S1/S2/S3** — with the mandated dummy bearer the handshake fails by design (G2-fold iter-003). The tool-list behaviour belongs to the Phase-5 census with a real key.

### S3 — the staged binary boot (the recipe this rehearsal validated, with the binary-copy upgrade)

Same four legs (L1–L4) and the same `env -i` allowlist, **plus**: the staged binary is **copied into the scratch root** (never booted in place) and booted as `<scratch>/ensemble-prod`; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly; the banner must show the staged version (`Starting Ensemble v<slot>`); `/livez` + `/readyz` 200 with all components true; the DB row assertions per §2; the tripwire per §3; SIGTERM-only teardown; no orphans; the scratch DB/role dropped; the live fence PRE == MID == POST.

---

## 9. Re-run recipe (what the staged dispatch should copy)

The harness is in `.agents/tester/EVIDENCE/plane-pm-smoke-2026-11-02/`:

| script | role |
|---|---|
| `setup_leg.sh <leg> <self_env> <slug_mode>` | scratch root + FAKE_HOME + escape guard + throwaway PG role/DB + free port + the per-leg env file (mode 600) |
| `boot_leg.sh <leg>` | the `env -i` allowlist + the **key-name census** + the dummy-key preflight + `exec` of the daemon |
| `assert_leg.sh <leg>` | the endpoints + the DB row (bearer redacted in SQL) + the tripwire + the boot-log lines |
| `teardown_leg.sh <leg>` | the orphan census + the port check + the DB/role drop + the scratch removal |
| `fence.sh <label>` | the live-fence capture (PRE / MID / POST) |

**Per-leg ports used in this rehearsal:** L1 = **8951**, L2 = **8951**, L3 = **8951**, L4 = **8951** (sequential — one leg at a time, each torn down before the next; the port was verified free before each boot and freed after each teardown). *Never 9797 / 7979 / 8079 / 8088.*

**To re-run the staged smoke:** `setup_leg.sh` → `boot_leg.sh` (as a background process) → poll `/livez` → `assert_leg.sh` → SIGTERM → `teardown_leg.sh` → `fence.sh mid-<leg>` — per leg, **one boot + assertions per call** (the operational guard from the tier-2 wedge: no giant single driver).
