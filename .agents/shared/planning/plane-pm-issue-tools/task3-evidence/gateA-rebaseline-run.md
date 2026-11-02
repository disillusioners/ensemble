# GATE A — FC9 baseline RE-MEASUREMENT (plane-absent boot) — run record

**Lane:** `plane-pm-issue-tools` · **Tree:** `964778d484650f909fdb84e4e3a9484d97a1c821` (36 commits over base `941cb6321` = origin/latest)
**Executed:** 2026-11-02 17:31–17:42Z · **Executor:** worker (gate dispatch) · **Authority:** `gate-i-tools-list-recapture.md` §7 / §7.1 / §7.2 / §7.3
**Artifact:** `task3-evidence/fc9_baselines.json`

---

## 0. Outcome

| | Value |
|---|---|
| **`baseline_tokens`** (FC9 baseline leg) | **20 468** |
| `system_prompt_tokens` | 8 416 |
| `tool_schema_tokens` | 12 052 (sort_keys+compact form — the form that reproduces the historical 12 117) |
| PM resolved toolset size | **46** (historical 47) |
| `plane_mcp_tool_count` | **0** |
| `plane` mcp_servers rows in scratch DB | **0** |
| Historical baseline (08:12Z, record only) | 20 111 → **delta +357** (+422 system prompt, −65 tool schema) |

**The FC9 verdict is NOT computed here.** The present leg (plane tools PRESENT) is measured later by a different gate, which also evaluates the ceiling.

---

## 1. Tip verification

```
git -C /home/nea/ensemble-src-wt-plane-pm rev-parse HEAD
  964778d484650f909fdb84e4e3a9484d97a1c821      ← EXPECTED 964778d48 ✓
git log -1 --format='%H %s'
  964778d48 fix(plane-pm): delta hardening round 2 — presence semantics, scrub widening,
                           escape completion, create-route gate
```

**Porcelain:** `0` lines before · `0` lines after everything (the only added paths are the two
intentional artifacts under `task3-evidence/`). **No commit, no push, no merge/stage/promote.**
The main checkout `/home/nea/ensemble-src` was read-only (its venv interpreter is the one that ran the
worktree source-run — the venv is a separate install, the checkout tree was never modified).

---

## 2. The six §7.1 assertions

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | Scratch daemon boots: `/livez` 200 alive + `/readyz` 200 ready, ALL components true, `reasons=[]`, `ENSEMBLE_SELF_ENV=sandbox` pinned explicitly | **PASS** | `livez {"status":"alive","version":"0.28.5"}`; `readyz {"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"grace":{"grace_active":false},"leak_signal":{"detected":false}},"draining":false}`; `Uvicorn running on http://0.0.0.0:8971`; `Application startup complete.` |
| 2 | NO `plane` mcp_servers row in the scratch DB; boot log carries the skip line | **PASS** | `select count(*) from mcp_servers where name='plane'` = **0**; scratch DB held exactly 2 rows — `context7` (active, builtin) + `webfetch` (active, builtin) — identical to gate-i's scratch DB. Skip line **verbatim**: `17:31:30 - daemon.manager - WARNING - Builtin 'plane' skipped — missing environment configuration: PLANE_MCP_URL, PLANE_MCP_API_KEY (see server definition docs); agents with mcp_full_access=['plane']: project-manager` |
| 3 | PM's resolved toolset contains ZERO plane MCP tools; `plane_sync_project` NOW ABSENT (the expected difference) | **PASS** | `plane_mcp_tool_count = 0`; resolved `plane_`-prefixed names = `[]`; **`plane_sync_project_present = false`** — recorded explicitly as the EXPECTED DIFFERENCE; toolset 47 → **46** |
| 4 | `baseline_tokens` measured at the prompt-assembly point | **PASS** | **20 468** = 8 416 (system prompt) + 12 052 (tool schemas); components recorded separately; determinism re-run byte-identical (same `system_prompt` sha256 `26fb6c6a5e4274095c3fa456ba3850c4459fd7907a267c07a692a3ac21800111`) |
| 5 | Live fence held | **PASS** | see F10 |
| 6 | Teardown SIGTERM-only, no orphans, scratch DB + role dropped, scratch root removed, port freed | **PASS** | SIGTERM-only → `Graceful shutdown complete.` + `Application shutdown complete.`; port 8971 freed; **zero** orphan `python -m daemon`; **zero** orphan MCP stdio children attributable to this gate (both live `context7` trees started 09:23:22Z and 17:20:03Z — before the 17:31:30Z scratch boot — and belong to the demo :7979 / live :9797 daemons); scratch DB + role DROPPED (0 rows verified); scratch root + fake HOME removed |

---

## 3. Fences F1–F11

| Fence | Value / proof |
|---|---|
| **F1** scratch root | `mktemp -d /tmp/plane-pm-fc9.scratch.XXXXXX` — never `~/agents-ensemble`, never `~/ensemble-src` |
| **F2** source-run | source-run from the worktree: `cd <wt>` + `PYTHONPATH=<wt>` + `/home/nea/ensemble-src/.venv/bin/python -m daemon` — no frozen binary, no staged artifact |
| **F3** `ENSEMBLE_SELF_ENV` | `sandbox` — pinned **explicitly** in every boot env |
| **F4** boot sweep | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` — pinned **explicitly**, never `env -u` (the sweep kills live processes) |
| **F5** PG scrub | **`env -i` allowlist** — nothing ambient inherited (the worker shell is live-poisoned: `POSTGRES_DB=ensemble_prod`, `ENSEMBLE_SELF_ENV=live`). `POSTGRES_*` set **explicitly** to the scratch DB. Verified: `env \| grep -c '^PLANE_'` = **0** |
| **F6** FAKE_HOME | `mktemp -d` fake HOME + escape guard (neither scratch path under `/home/nea/agents-ensemble*` nor `/home/nea/ensemble-src*`); the daemon's promote journal resolved to **no install dir** — boot log `install_dir=<none — dev/unresolved>` ⇒ it never resolved into the live install |
| **F7** scratch PG | throwaway role + scratch DB on the **LOCAL** cluster `127.0.0.1:5432` (PostgreSQL 16.15). `ensemble_prod` **never addressed** (it does not exist on this local cluster — the live prod DB is on the remote `10.44.0.2`). Role + DB **DROPPED** at cleanup (0 rows in `pg_database` / `pg_roles`) |
| **F8** port | **8971** — free per `ss` before boot; never 9797 / 7979 / 8079. A hard preflight guard in the boot script refuses those three and refuses an already-bound port |
| **F9** teardown | **SIGTERM-only**; graceful shutdown observed; no orphan `python -m daemon`; no orphan MCP stdio children; port 8971 freed |
| **F10** live fence | live `state.json` sha256 **PRE == MID == POST** = `693998100c2ba18cb5848c165f4592fcab2fea2e5664ff29aa3a2ac0d7cc5c4a`; live daemon **pid 643626 unchanged** on :9797 (uptime 67 066s → 68 163s, continuous); `current -> releases/v0.28.4` untouched; live demo :7979 pid 525852 untouched |
| **F11** no `.env` mutation | plane-absent condition is a **per-process env override**; **zero** writes to any `.env` (live install / main checkout / worktree / scratch). The live `.env` was read-ONLY, to source credential VALUES into the `env -i` allowlist — no `.env` sha/mtime changed |

---

## 4. Delta vs the historical 20 111 (record only)

```
20 468 (new)  −  20 111 (historical 08:12Z)  =  +357
   ├─ system prompt :  8 416  −  7 994  =  +422
   │     └─ agents/project-manager/{rule.md,tools_note.md,workflow.md} rewritten by the
   │        lane's Phase-4 charter reconciliation (commit 2801a0e02, 11:18Z) — after the
   │        08:12Z measurement. The prompt's tool-NAME sections are otherwise unchanged;
   │        the now-empty 'plane' category contributes NO section at all.
   └─ tool schemas  : 12 052  − 12 117  =   −65
         └─ EXACTLY the removed plane_sync_project tool (measured: 65 schema tokens in the
            sort_keys+compact form, 84 in the plain form). The W-8 prefix-sweep ownership
            guard (95e999b20) removed it from PM's resolved toolset: 47 tools → 46.
```

**The expected difference is confirmed:** `plane_sync_project` is **ABSENT** from the current
resolved toolset. Had it still been present (the 08:12Z basis), the baseline would have been
20 533 — i.e. the ownership guard alone moved the baseline by −65.

---

## 5. Deviations from the gate-i recipe (reported, not silently absorbed)

1. **Boot-script defect (self-caught, pre-boot fix).** The first boot attempt sourced the live
   `.env` into the shell and then read `PORT` from that shell — the live `.env`'s `PORT` (9797)
   **clobbered** the scratch port, so the scratch daemon tried to bind `0.0.0.0:9797`. The bind
   **failed** (`address already in use` — the live daemon holds it), the scratch daemon exited
   cleanly, and the **live daemon was verifiably untouched** (pid 643626 continuous, `state.json`
   sha identical). The script was then rewritten with dedicated `FC9_`/`LLM_` variable names plus
   three hard preflight guards (forbidden port / path escape / prod DB). The successful boot ran on
   8971. **Recorded as an F8 near-miss**, not because it changed any number.
2. **Formula-form ambiguity (material — resolved by arithmetic).** The §7 formula string implies
   plain `json.dumps`; gate-i's own §6 counting form is `sort_keys=True, separators=(",",":")`.
   **Both were measured.** The historical 12 117 is reproducible **only** under sort_keys+compact
   (12 117 − 65 = 12 052 exactly); under the plain form the historical figure would have been
   12 117 + 84 = 12 201, which does not match the measured 13 432. ⇒ `baseline_tokens = 20 468` uses
   the sort_keys+compact form, and **the present-leg gate must use the same form** or the growth
   delta is not like-for-like. The plain-form variant (**21 848**) is recorded in the JSON so
   either can be reconciled.
3. **Skip-line format (cosmetic).** The 2026-11-02 C3 tidier fix added the `: <missing var names>`
   detail to the plane skip line — same class and meaning, different string from gate-i's.
4. **Preloaded MCP name count.** 3 preloaded MCP names (2 context7 + 1 webfetch) vs gate-i's
   "2 context7" note. **No measurement effect**: PM denies the `mcp` category, so no `mcp_*` tool is
   bound (resolved `mcp_` names = `[]`) and no MCP category appears in PM's prompt.

---

## 6. Measurements — how they were taken

**Boot (F2, source-run):** `cd /home/nea/ensemble-src-wt-plane-pm` →
`env -i <allowlist> /home/nea/ensemble-src/.venv/bin/python -m daemon` (cwd = worktree,
`PYTHONPATH=<worktree>`, `ENSEMBLE_CONFIG=<worktree>/config.yaml`).

**Harness:** the same `env -i` allowlist, entering the **same app lifespan**
(`create_app()` + `lifespan_context`) → resolved PM's toolset via the daemon's own
`create_instance_tools(manager, <uuid4>, "project-manager")` on the **DEFAULT UUID path** (the UUID
is preloaded through `manager.ensure_mcp_preloaded(...)` first, so the MCP-name lane is populated
exactly as at runtime — an unpreloaded/custom id would yield `mcp_tool_names=[]`) → PM's prompt via
`load_and_cache_prompt("project-manager", <wt>/agents/project-manager, PromptCache(), mcp_tool_names, version_tag=None)`
→ counted with the daemon's own `estimate_tokens`.

**Formula:** `baseline_tokens = estimate_tokens(PM system prompt) + Σ estimate_tokens(json.dumps(convert_to_openai_tool(t)))`
over PM's **resolved** toolset — tiktoken `cl100k_base`.

**Determinism:** two independent harness runs → byte-identical numbers and the same system-prompt
sha256.

**Tear-down:** SIGTERM-only to the scratch daemon pid; scratch DB + throwaway role dropped;
scratch root + fake HOME removed; port 8971 freed; live fence re-verified POST.

---

## 7. Handoff to the present leg (Phase-5 Step 3)

* The FC9 comparison base is **`baseline_tokens = 20 468`** (system prompt 8 416 + tool schemas 12 052),
  measured on tree `964778d48` with the plane surface absent.
* The present leg **must** use the **same serialization form** (`sort_keys=True, separators=(",",":")`)
  and the same measurement point; the plain-form variant (21 848) is recorded for reconciliation only.
* The ceiling is **not** evaluated here: `growth_tokens ≤ max(10 000, 0.25 × baseline_tokens)` AND
  `growth_pct ≤ 0.25` ⇒ both booleans + the verdict belong to the present-leg gate.
