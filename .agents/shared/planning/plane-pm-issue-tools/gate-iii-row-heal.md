# Gate (iii) — live `mcp_servers` row state + scratch heal drill — lane `plane-pm-issue-tools` Phase 1

**Executed:** 2026-11-02 ~08:20–09:15Z · **Scope:** rows 1–3 (row 4 — the refusal/WARNING pins — is re-run in Phase 2/3 where its code lives, per `phase1-plan.md` §6).
**Bearer handling (P-G3):** every read below selects the Authorization header through `left(auth,7) || '<redacted,len=N>'` **in SQL** — only the literal prefix `Bearer ` and the length ever leave the database. **No bearer value appears in this file, in any log quoted here, or in any command echo.**

---

## 1. Row 1 — the LIVE row, SELECT-only (the "before" snapshot)

**Read:** one SELECT against the live DB (`mcp_servers`, `name='plane'`), bearer redacted in SQL. **No writes. The live daemon was not restarted. The live DB was not modified.**

| field | value |
|---|---|
| `name` | `plane` |
| `is_builtin` / `is_active` | `true` / `true` |
| `config_schema_version` | **`"2"`** |
| `config.headers['x-workspace-slug']` | **`nea`** ← **the stale-slug hazard (P4) CONFIRMED on the live row** |
| `config.headers.Authorization` | `Bearer <redacted, len=49>` — the known-**expired** live token (R1/ADJ-4) |
| `config.url` | `https://mcp.ensem.dev/plane/http/api-key/mcp` |
| `config.env` | *(empty — no operator env keys)* |

**Consequence recorded:** when Phase 2's E4 bumps the definition's `schema_version` `"2"`→`"3"`, the live row (still `"2"`) drifts ⇒ the heal rebuilds `headers` from the live env (`nea`) — byte-identical slug, **sanitized** structure (the 2026-08-13 quote-leak class stays closed). The live row's `nea` slug is **correct for live** (ADJ-1 live-exempt); it is stale only in the sense that the heal has not yet fired against a `"3"` definition.

---

## 2. Rows 2–3 — the scratch heal drill (BEFORE/AFTER snapshots)

**Environment (F1–F11, all asserted — same fence table as the FC9 baseline boot):** fresh `mktemp -d` scratch root; **source-run from the worktree** (`env -i` allowlist + `PYTHONPATH=$WT` + `python -m daemon`, `ENSEMBLE_SELF_ENV=sandbox`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, FAKE_HOME + escape guard, **LOCAL** scratch PG `127.0.0.1:5432` on a throwaway role — `ensemble_prod` never addressed; port **8971**; SIGTERM-only teardown, no orphans; **dummy** plane key + dummy URL — no real credential anywhere in the drill).

**Drill shape (recorded interpretation — see the deviation note in the commission report):** the plan's row-2 literal "bump plane's `schema_version` `"2"` → `"3"`" describes **E4's Phase-2 code change to the definition**, which does not exist on this tree. The equivalent drift path on the pre-Phase-2 tree is exercised in the opposite direction: the ROW is seeded to version **`"1"`** (older than the definition's `"2"`) with a stale slug — the same `_bootstrap_builtin_servers` drift branch fires, the same `_heal_config_preserving_env` runs, and the schema version **ADVANCES** (`1 → 2`), which is the plan's own assertion ("the schema version advanced").

| snapshot | `config_schema_version` | `headers['x-workspace-slug']` | Authorization | `config.env` | active |
|---|---|---|---|---|---|
| **BEFORE** (seeded stale) | `1` | `nea` (stale) | `Bearer <redacted,len=31>` (dummy) | `OPERATOR_SENTINEL=survives-heal` | t |
| **AFTER** (heal boot) | **`2`** ⬆ advanced | **`e2e`** ⬅ the **resolved** slug (from the boot env) | `Bearer <redacted,len=31>` (dummy, rebuilt from env) | `OPERATOR_SENTINEL=survives-heal` ✅ **SURVIVED** | t |

**Captured boot log line (the heal):**

```
09:11:27 - daemon.manager - WARNING - Built-in MCP server 'plane' config refreshed due to schema version change (1 → 2) — operator-set env keys PRESERVED, template defaults rebuilt
```

### Pass criteria (phase1-plan.md §5)

| # | Criterion | Verdict |
|---|---|---|
| (i) | After the bump, `config.headers['x-workspace-slug']` == the resolved slug — the heal re-materialises `headers` from the current env (`manager.py::_heal_config_preserving_env`, only the `env` axis preserved) | **PASS** — `nea` → **`e2e`** |
| (ii) | An operator-set `config.env` sentinel **survives** the heal | **PASS** — `OPERATOR_SENTINEL=survives-heal` present before AND after |
| (iii) | Refused resolution ⇒ no row, no tools, a WARNING naming the agent(s) | **PARTIAL — by design at Phase 1**: the no-row/no-tools leg is **PROVEN** (the FC9 baseline boot: zero `plane` rows, zero plane MCP tools). The **WARNING form does not exist on this tree** — the current skip line is **INFO** and names neither the agents nor the env vars: `Builtin 'plane' skipped — missing environment configuration (see server definition docs)` (captured at 09:00:14 on the FC9 boot). The loud-skip WARNING naming agents + env vars is **Phase-3 code (P3/P5)** and is re-asserted there. |
| (iv) | The live daemon is untouched by the drill | **PASS** — live read SELECT-only; the drill ran on a scratch install |

**Failure action (i):** not triggered — the heal DOES re-point the slug ⇒ E4 does **not** need the connect-time header validation added at Phase 2.

---

## 3. Row 4 — routed to Phase 2/3

The refusal-path WARNING pins (P3/P5) and the heal pins (P8) require Phase-2/3 code; per `phase1-plan.md` §6 they are re-run there as part of the pin packs. This gate records the CURRENT (INFO-form) skip line above as the baseline those pins will flip to WARNING.

---

## 4. Fence proof (F10 — live untouched)

| check | value |
|---|---|
| live `state.json` sha256 **PRE == MID == POST** | `1262190e16f85d6ee682de10066545bbdba3b78b74d5f93a383398644099852a` (identical across every boot) |
| live daemon pid | **643626** (unchanged); uptime continuous 35 544 s → 37 684 s; livez `0.28.4` throughout |
| live port `:9797` / demo `:7979` | both live-listening and untouched (scratch port was **8971**) |
| `ensemble_prod` | **never addressed** (all drill traffic went to the LOCAL cluster `127.0.0.1:5432`, a different PostgreSQL server) |
| `.env` mutation | **none** (the drill's env changes are per-process overrides; the scratch slug/env ride in the boot env, never a file) |
| teardown | SIGTERM-only ×2, graceful shutdown observed, **no orphans** (no `python -m daemon`, no MCP stdio children), port freed |
| worktree | `git status --porcelain` clean apart from the lane's own planning dir |
