# Tool API Design — Self-Restart / Self-Upgrade (Phase 2)

- **Initiative:** self-restart-upgrade-phase2
- **Branch:** `plan/self-restart-upgrade-phase2` @ `653e8e71`
- **Owner:** W2 (deepest-design docs)
- **Siblings:** W1 owns `plan-overview.md` + `phaseN-plan.md` (P2.1 release/upgrade pipeline, P2.2 agent-facing tools, P2.3 rollout ladder + drills); W3 owns `test-strategy.md`, `promotion-ladder.md`, `decisions.md`. **Do not create sibling files.**
- **Phase IDs (FIXED):** P2.1 (release/upgrade pipeline per ADR-004/005/009/012), P2.2 (agent-facing tools per ADR-015-extended), P2.3 (rollout ladder + drills + carry-overs).
- **Baseline ADR:** ADR-015 in `.agents/shared/planning/auto-restart-upgrade/decisions.md` (read end-to-end; this initiative EXTENDS it — deviations flagged explicitly in §11).
- **Status:** Draft (design only — markdown, no implementation). Seams marked ⟪SEAM: architect enrichment⟫ are load-bearing and require the architect to finalize before P2.2 implementation.

---

## Hard Constraint (encoded structurally — see §3, §4, §6)

> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

This constraint is encoded **structurally**, not by operator discipline:
1. `target_env` is an enum bound to the running daemon's own environment (§3 self-match rule) — **cross-env targeting is refused at the tool layer**, eliminating the 7979↔9797 typo class for tool-driven actions.
2. LIVE actions require the three-factor confirmation gate (§4) AND the env-target guard (§3) AND, where the change crosses schema migrations, an explicit USER-GATED escalation in the risk register (R-SR05).
3. The execution path (§6) never issues raw kills / `lsof`/port kills — restart uses the `stop-ensemble.sh` SINGLE-TERM contract + launcher re-exec; upgrade uses the P2.1 promote pipeline with health gates.

---

## §1 Tool Inventory

Baseline per ADR-015: `system_upgrade` (executes promote pipeline) + `release_info` (read-only). This initiative ADDS two tools and flags the deviations.

| # | Tool | Category | Purpose | Gate | Phase | Deviation |
|---|------|----------|---------|------|-------|-----------|
| 1 | `system_upgrade` | `system_upgrade` | Execute the P2.1 promote pipeline (resolve target → pg_dump preflight → stop → flip → start → gate → commit \| auto-rollback). Returns armed/preflight result synchronously; terminal outcome read via `upgrade_status`. | LIVE: 3-factor (§4). demo/dev: free. | P2.2 | **D-2** (ADR-015 said "returns the terminal result" — process-death forces arm-then-poll; see §6) |
| 2 | `release_info` | `system_upgrade` | Read-only: recent git tags / release versions, deployed version (`current` + journal), changelog summary, `rollback_safe`/quarantine status. | None | P2.2 | none (ADR-015 baseline; can ship early per ADR-015 phase note) |
| 3 | `system_restart` | `system_upgrade` | Execute a restart (degenerate promote: target=current release). Mode: `graceful-now` (executes at end-of-turn via the deferred-pause seam — D-FA1.4; the historical `after-turn` variant was dropped as redundant with the converged trigger design). | LIVE: **refused outright this initiative (A2/§3.1; future opt-in = 3-factor §4)**. demo/dev: free. | P2.2 | **D-1** (ADR-015 has NO restart tool — this initiative adds it) |
| 4 | `upgrade_status` | `system_upgrade` | Read-only: poll a pipeline run by `run_id` (phase transitions, journal tail, rollback counters, terminal outcome). Also resolves the post-restart outcome of a `run_id` issued by `system_upgrade`/`system_restart`. | None | P2.2 | **D-3** (NEW — needed because `system_upgrade`'s terminal result is only observable post-restart; see §6) |

### Decision: add `upgrade_status`? — YES (justification)

The mandate asked whether a status/progress tool is needed since `system_upgrade`'s result may only be observable post-restart. **Decision: YES, add it as a dedicated tool.** Justification:

1. **Process-death forces arm-then-poll.** A synchronous `system_upgrade` that blocks to the terminal state would die mid-call when the daemon restarts; the in-flight tool call is LOST (turn frozen at last committed node boundary; not re-executed on `is_retry` resume — verified §6). So `system_upgrade` MUST return at the armed/preflight point. The terminal outcome (committed/rolled-back/refused/halted) then needs a read path → `upgrade_status(run_id)`.
2. **Run-ID correlation across death.** `system_upgrade`/`system_restart` return a `run_id`; `upgrade_status(run_id)` resolves the outcome on a *later* turn (possibly post-restart). This is the cross-process-death join key — `release_info` is snapshot-oriented and has no run-scoped view.
3. **Separation of concerns.** `release_info` = "what releases exist" (stable, cacheable, cheap). `upgrade_status` = "what is happening right now in MY run" (live journal tail, elapsed, ETA-ish, per-run counters). Folding them overloads `release_info`'s contract.
4. **No gate, no side effects** — pure journal read, safe to poll at any cadence.

Rejected alternative: fold into `release_info` via a `view: journal-tail` parameter. Rejected because of (2) — the run-id join is the load-bearing feature and a parameter overload hides it.

> **Naming note.** A `system_health` tool already exists in the `system` category (`daemon/tools/system.py:502` — env/config introspection). `upgrade_status` is distinct: it reads the pipeline journal, not daemon health. No collision.

---

## §1a Sibling Alignment Notes (W1's plan landed after this doc was drafted — conflicts flagged per mandate, architect reconciles)

W1's `phase2-plan.md` + `plan-overview.md` landed while this doc was written. Three resolutions differ from W2's recommendations; each is flagged here so reconciliation is explicit, not silent:

| # | Topic | W2 (this doc) | W1 (`phase2-plan.md`) | Reconciliation stance |
|---|-------|---------------|----------------------|----------------------|
| A1 | `upgrade_status` tool | ADD as a dedicated tool (§1 decision: run-id correlation across process death is load-bearing) | D1: NO separate tool — fold into `release_info` (journal reads incl. `in_flight` txn, counters, `history`) | Both are journal reads; the load-bearing feature is **run-scoped correlation**, not the tool boundary. If folded (W1 wins), `release_info(section=journal, run_id=...)` MUST carry the run-id filter + journal tail — otherwise Ari loses the cross-death join key. Architect decides the boundary; the §2.4 payload shapes apply either way. |
| A2 | Live `system_restart` | Gate behind the 3-factor gate (§2.2, §4) | D6: **refused outright this initiative** — live restart is USER-GATED; refusal message points to the manual procedure; gate design reserved for a future opt-in | **W1's stance is the safer reading of the hard constraint ("live pids must remain untouched") and W2 accepts it as the P2.2 default.** This doc's §2.2 signature intentionally KEEPS `user_confirmed`/`nonce` params so the future opt-in flips a config flag, not a schema. Validation asserts REFUSAL paths for live restart (W1 T7). |
| A3 | Restart gate for demo | `user_confirmed` param present (no gate) | D6: no `user_confirmed` param needed for restart (recoverable-by-design via Phase-1 semantics) | Compatible: param exists but is a no-op for demo/dev (ignored). Keeping it makes the live-opt-in (A2) schema-stable. |

---

## §2 Parameters, Returns, Errors

Conventions (verified): tools return **strings**; error style `"Error: ..."` (question tool uses `"ERROR:"` at `question_tools.py:150` — normalize to `"Error:"` for new tools unless the codebase-wide convention diverges; ⟪SEAM⟫ — pick one and apply consistently). Payloads are **line-oriented, LLM-friendly** (not JSON) — matches `release_info`/`system_health` precedent and the project's "structured string" convention.

### 2.1 `system_upgrade`

```
system_upgrade(
    target_env: "dev" | "demo" | "live" | "sandbox",   # MUST equal self-env (§3 self-match rule — applies to ALL tools incl. reads, reviewer ruling 2026-08-22)
    version: str | None = None,            # default: latest staged/tagged release
    user_confirmed: bool = False,          # required for live (§4); ignored for demo/dev
    dry_run: bool = True,                  # DEFAULT TRUE per ADR-022(b)/D-FA2.2 (ratified): a hallucinated parameter set must never execute a real promote on the first call — real execution requires explicit dry_run=false. Preflight issues the nonce for live (§4)
    nonce: str | None = None,              # live confirmation: copy of nonce from a prior dry_run
) -> str
```

**Returns (illustrative payloads):**

dry_run=true, live (preflight, issues nonce):
```
UPGRADE PREFLIGHT (dry-run) — env=live target=1.2.3
current=1.2.2 (releases/1.2.2, rollback_safe=true)
target staged: releases/1.2.3 manifest rollback_safe=true known_schema_gen=14
journal: current=1.2.2 previous=1.2.1 rollbacks_24h=0/3 quarantine=[1.2.0]
lock: free
PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip current→1.2.3 → start → gate (/livez ≤60s, /readyz ≤120s, 300s soak  [corrected from 180s — deploy.sh phase-5 budget is canonical; architect 2026-08-22]) → commit | auto-rollback
CONFIRMATION REQUIRED (live): nonce CONFIRM-7K2M-QX4T — the user must reply with this nonce; then call system_upgrade(user_confirmed=true, nonce="CONFIRM-7K2M-QX4T"). Nonce single-use, expires in 15min.
```

armed (post-gate, demo):
```
UPGRADE SCHEDULED — run_id=r-20260822-0942-a1b2 env=demo target=1.2.3 mode=promote
executes: after this turn completes (deferred — §6)
watch: upgrade_status(run_id="r-20260822-0942-a1b2") for phase transitions; terminal state readable post-restart
journal: releases/state.json txn opened (started_at=2026-08-22T09:42:31Z, owner=exec-pid-...)
```

**Errors (refusal variants — all begin `Error: UPGRADE REFUSED`):**
```
Error: UPGRADE REFUSED (live) — reason=user-confirmation-missing: this turn was not triggered by a user message carrying nonce CONFIRM-7K2M-QX4T. Relay to the user: reply with the nonce to authorize.
Error: UPGRADE REFUSED (live) — reason=nonce-mismatch: nonce CONFIRM-... does not match the pending nonce for target 1.2.3.
Error: UPGRADE REFUSED (live) — reason=nonce-expired: nonce issued at 09:30Z, TTL 15min elapsed. Re-run dry_run to obtain a fresh nonce.
Error: UPGRADE REFUSED — reason=env-self-match: target_env=live but self-env=demo. Tools cannot target a different environment than the running daemon (hard constraint §3).
Error: UPGRADE REFUSED — reason=rollback-cap-exceeded (3/24h) — halted-for-human; see release_info(section=journal). ADR-005 D2.
Error: UPGRADE REFUSED — reason=pipeline-busy run_id=r-... phase=gating (per-env lock held; retry via upgrade_status).
Error: UPGRADE REFUSED — reason=target-not-staged: releases/1.2.3 not found. Run release_info(section=releases).
Error: UPGRADE REFUSED — reason=manifest-unsafe: target manifest rollback_safe=false (drop-release) — halt-for-human.
```

### 2.2 `system_restart`

```
system_restart(
    target_env: "dev" | "demo" | "live" | "sandbox",   # MUST equal self-env (§3 self-match rule)
    reason: str,                           # free-text, journaled (audit trail)
    user_confirmed: bool = False,          # required for live (§4)
    mode: "graceful-now" = "graceful-now",      # single mode: executes at end-of-turn via the deferred-pause seam (D-FA1.4; `after-turn` dropped as redundant)
    nonce: str | None = None,              # live confirmation (§4)
    dry_run: bool = True,                  # DEFAULT TRUE per ADR-022(b)/D-FA2.2 — real execution requires explicit dry_run=false
) -> str
```

**Returns:**
```
RESTART SCHEDULED — run_id=r-... env=demo mode=graceful-now reason="config reload"
executes: after this turn; expected downtime 15-90s (SINGLE-TERM + launcher re-exec + boot preflight)
post-restart: ask me to run upgrade_status(run_id="r-...") or release_info(section=current)
journal: releases/state.json pending-op opened (kind=restart, started_at=..., owner=exec-pid-...)
```

**Errors:** mirror `system_upgrade` refusal variants with `RESTART REFUSED`; add:
```
Error: RESTART REFUSED — reason=unknown-mode: mode must be graceful-now.
Error: RESTART REFUSED — reason=restart-under-burst-abort: daemon is in burst-abort hold (exit-1 latch); restart would mask the failure. Resolve the burst condition first.
```

### 2.3 `release_info`

```
release_info(
    target_env: "dev" | "demo" | "live" | "sandbox" = self-env,   # self-match applies to reads too (§3.2, reviewer ruling 2026-08-22); cross-env read → env-self-match refusal
    section: "releases" | "current" | "journal" | "changelog" | "all" = "all",
    version: str | None = None,            # changelog filter / specific release detail
) -> str
```

**Returns (section=all):**
```
RELEASE INFO — env=demo
current=1.2.2 (via releases/current → releases/1.2.2)
journal: current=1.2.2 previous=1.2.1 in-flight=none rollbacks_24h=0/3 quarantine=[1.2.0] last_txn=2026-08-22T07:11Z committed
releases:
  1.2.3  staged=2026-08-22T09:00Z rollback_safe=true known_schema_gen=14
  1.2.2  promoted=2026-08-20T14:00Z rollback_safe=true known_schema_gen=14
  1.2.1  previous (rollback target, pinned — not evictable)
changelog:
  1.2.3 — auto-restart phase 2 tool surface; exit-code 74 (restart-me) added
  1.2.2 — auto-restart phase 1 launcher + /livez + /readyz
```

No errors beyond `Error: release_info — unknown section/version`.

### 2.4 `upgrade_status`

```
upgrade_status(
    target_env: "dev" | "demo" | "live" | "sandbox" = self-env,   # self-match applies to reads too (§3.2, reviewer ruling 2026-08-22)
    run_id: str | None = None,             # default: latest run for self-env
    tail: int = 20,                        # journal lines to include
) -> str
```

**Returns (in-flight):**
```
UPGRADE STATUS — run_id=r-... env=demo phase=gating (started 09:42:31Z, elapsed 41s)
journal tail:
  09:42:31 txn-open promote 1.2.2→1.2.3
  09:42:33 pg_dump ok (2.1s, retained 2 snapshots)
  09:42:35 stop complete (clean exit 0, SINGLE-TERM)
  09:42:38 flip current → 1.2.3 (atomic rename)
  09:42:39 start: launcher re-exec
  09:42:41 /livez ok (version=1.2.3)
  09:42:43 /readyz polling (db ok, queue ok)
rollback_cap=0/3 cooldown=none
next: /readyz green → 300s soak → commit
```

**Returns (terminal):**
```
UPGRADE STATUS — run_id=r-... env=demo TERMINAL
outcome=committed version=1.2.3 (verified /livez=1.2.3)
post-restart: /livez=ok /readyz=ready (database ok, queue_freshness ok)
rollbacks_24h=0/3
journal ref: releases/state.json txn[r-...] committed_at=09:47:31Z
```

**Terminal variants:** `outcome=rolled-back` (with reason + which gate failed + quarantine flag), `outcome=refused` (gate refused pre-arm — nonce/env/cap), `outcome=halted-for-human` (rollback cap exceeded or manifest unsafe).

---

## §3 Permission Model

### 3.1 Per-environment authorization matrix

| Action | demo | dev | sandbox | live |
|--------|------|-----|---------|------|
| `release_info` (read) | free | free | free (self-resident daemon) | **self-match + U4 per-request approval** ¹ |
| `upgrade_status` (read) | free | free | free (self-resident daemon) | **self-match + U4 per-request approval** ¹ |
| `system_upgrade` dry_run | free | free | free (self-resident) | **self-match + initiating-request approval** — the initiating HUMAN request ("upgrade live to X") itself constitutes the U4 per-request approval for the preflight; recorded in the ledger + nonce-mint metadata (`issued_to_instance`, `confirmed_source`). No separate read-approval step precedes it (2026-08-22 reconciliation of this cell with ladder U4) |
| `system_upgrade` armed (execute) | free | free | free (self-resident) | **3-factor gate (§4)** + env-target guard |
| `system_restart` (execute) | free | free | free (self-resident) | **3-factor gate (§4)** + env-target guard — **refused this initiative** (A2) |
| live promotion crossing schema changes | — | — | — | **USER-GATED** (R-SR05) — explicit user-confirmed action, manifest `rollback_safe` is the interim gate |

¹ **Reviewer ruling 2026-08-22 (canonical — resolves the three-doc contradiction on live reads):** the env self-match rule applies to **ALL tools including reads**. `target_env` is the 4-value enum `dev|demo|live|sandbox` on every signature. A daemon's own Ari may address **only its own env** — this is precisely how sandbox drills (test-strategy D7) run: a sandbox-resident daemon's Ari targets `sandbox`, a dev-resident one targets `dev`. Cross-env reads and actions are BOTH refused with `env-self-match`. A **live** daemon's own Ari may read live (`release_info`/`upgrade_status`) under U4's per-request user approval (ladder §5); a demo/dev/sandbox-resident Ari **cannot address live at all** — structurally, not by convention. (Supersedes the earlier "reads free everywhere / sandbox n/a" cells and the old footnote text: "Sandbox instances have their own port + throwaway PG (per hard constraint) but the tools run *inside* a daemon and target install dirs by env enum `demo|live`.")

**"Free"** means: no confirmation gate, but still journaled (audit trail), lock-protected (one pipeline per env), and subject to all safety interlocks (§5).

### 3.2 Self-match rule (env-target guard — structural)

`target_env` MUST equal the running daemon's own environment — **this applies to ALL tools including the read tools** (reviewer ruling 2026-08-22; see §3.1 ¹). **Self-env resolution — D-FA2.3, RATIFIED (2026-08-22): a staged marker `ENSEMBLE_SELF_ENV=dev|demo|live|sandbox` in `INSTALL_DIR/.env` is MANDATORY**, staged by `deploy.sh`/`scripts/upgrade/stage.sh` alongside the existing `.env.prod`/`.env.demo` staging (ADR-014 mechanism; P2.1 T2 stages it). **The PORT-derivation fallback is REJECTED** — it reintroduces the exact R-SR11 typo class this guard exists to kill. **Marker absent → every ACTOR tool refuses fail-closed (`env-marker-absent`, S-31); the read tools (`release_info`, `upgrade_status`) still answer.** A `target_env` ≠ self-env mismatch is refused with `env-self-match` (§2.1).

This structurally prevents dev-Ari from touching live and vice versa, **eliminating the 7979↔9797 typo class for tool-driven actions** (the port-confusion risk R-SR11 remains for scripts/humans, not for the tool). The live daemon's own Ari is the only path to live tool actions, and only with the §4 gate.

### 3.3 Registration-side authorization (the category mechanics — verified)

New category `system_upgrade` is registered through the existing per-instance factory + category-registry pattern. **Verified mechanics** (with corrected line refs — the task brief cited `:206-236` for `CATEGORY_MODULES`; the AST-scan helper lives there, but the dict itself is at `:423-457`):

| Step | File:Line (verified) | What |
|------|----------------------|------|
| Decorator | `daemon/tools/_tool_registry.py:75` | `@register_tool_category("system_upgrade")` on each `@tool` function — registers category docs/metadata |
| Category→module map | `daemon/tools/_tool_registry.py:423-457` | `CATEGORY_MODULES["system_upgrade"] = "daemon.tools.upgrade_tools"` — the AST-scan walker (`:199-239`) discovers `@tool` names inside the factory |
| Frozen-binary fallback | `daemon/tools/_tool_registry.py:491+` | `KNOWN_TOOL_NAMES` frozenset — regenerate via the documented `uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(...))"`; drift test `tests/unit/tools/test_frozen_tool_name_discovery.py` must pass |
| Dynamic-tool validation | `daemon/tools/_tool_registry.py:23-64` | Add the 4 tool names to `DYNAMIC_TOOL_NAMES` (factory-created, not import-time registered) — needed for startup validation + frozen binary |
| Allow-list expansion | `daemon/tools/instance.py:284-289` | `tools.allow` entries resolve category names → tool sets (and individual names pass through); empty allow = ALL categories (`:276-281`) — see R-SR16 |
| **CRITICAL list-append** | `daemon/tools/instance.py:~1895-2073` | `create_instance_tools()` must `tools.extend(create_upgrade_tools(...))` — **decorator-only = never constructed = silently invisible** (the known gotcha; see `:1930` `tools.extend(job_tools)` precedent) |
| Agent allow-list | `agents/ari/meta.json` (verified: 14 entries, no `edit_file`/`write_file`) | Add `"system_upgrade"` (category name resolves via `:284-289`) |
| Meta lookup caveat | (critical-notes pattern) | ALL meta lookups MUST use `get_version(id, tag)` with fallback to `get_resolved()` — affects `tools.allow`, `team_members`, `path`, `skill_injection` |

### 3.4 ADR-015 delta: ari-only this phase

ADR-015 named **ari + jober** for `tools.allow`. **This phase adds the category to `agents/ari/meta.json` ONLY.** Jober is deferred to a later phase. Rationale: ari is the conversational front door (user-facing); jober is a system-job runner whose upgrade authority is a separate trust decision. Flag as **deviation D-4** (§11). Jober's current `tools.allow` (verified: 9 entries — `job`, `help`, `self`, `time`, `project`, `knowledge`, `mcp`, `context`, `shared_meta_kv`; notably NO `bash`) means jober cannot today execute pipeline scripts anyway — adding the category later is a deliberate, gated trust expansion.

### 3.5 Deny-by-default — and the empty-allow leak (R-SR16)

ADR-015 claims `tools.allow` is default-deny (agents without the category never see the tools). **Verified nuance:** `instance.py:276-281` — *"No allow list means everything is potentially allowed"* — an agent with `tools.allow` absent or empty gets ALL categories including `system_upgrade`. This is a real permission leak for any agent created without an explicit allow-list. **Mitigation (recommendation):** treat `system_upgrade` (and `system_restart`) as **opt-in-only regardless of empty-allow default** — special-case in `create_instance_tools()`: the `system_upgrade` category is excluded from the empty-allow universe and only constructed when explicitly present in `tools.allow`. This is structural (no deny rules needed) and matches the hard constraint's intent. Logged as R-SR16. ⟪SEAM: architect to confirm the special-case placement in `create_instance_tools()` does not regress existing empty-allow agents.⟫ **[ARCHITECT, 2026-08-22: RESOLVED — the only empty-allow agent today is `watcher` (worker has 14 explicit entries; explorer 8 + deny); excluding the category regresses NOBODY and is the desired outcome for watcher. Mechanism: `PRIVILEGED_TOOL_CATEGORIES` frozenset in `_tool_registry.py`, consumed in the empty-allow branch at `instance.py:276-281`. See architecture-recommendation.md FA2/D-FA2.5.]**

---

## §4 USER CONFIRMATION GATE MECHANICS — the load-bearing section

**Requirement (non-negotiable):** enforcement is **server-side**; a fabricated `user_confirmed=true` from the LLM must NOT unlock LIVE; demo/dev need no gate. Define refusal/timeout semantics.

### 4.1 Verified signals

| Signal | Location | Strength |
|--------|----------|----------|
| `MessageQueue.type` HUMAN vs AGENT vs SYSTEM | `daemon/repositories/message_queue/models.py:19-25, :49` | **Strongest origin discriminator — with a verified correction [2026-08-22 council]:** `MessageType.HUMAN` at `instance_messaging.py:1310-1319` is the **else-branch DEFAULT** (any source without an `internal_*` prefix mints HUMAN — `cascade_resume`, `internal_invoke_and_wait:`, `agent:` rows are HUMAN-typed today). "LLM cannot enqueue HUMAN rows" holds today only by caller-discipline (agent paths use `internal_agent:` prefixes). **Hardening (decided): positive source whitelist `USER_ORIGIN_SOURCES` at the stamp site** — architecture-recommendation.md FA3/D-FA3.1 |
| User-originated trigger marker | (none today) | ADR-015 OQ9 open — no server-side attribute records "this turn was triggered by a genuine user message" |
| `resume_processing_job` answer machinery | `daemon/manager.py:7525+` (`awaiting_answer` suspension reason) | Partially exists — the resume-with-answer path is plumbed but the `POST /api/instances/{id}/answer` endpoint is NOT implemented (no route in `daemon/api.py`) |
| Question-tool precedent | `daemon/tools/question_tools.py:122-189` (ask tool + SSE `question_pack` + deferred-pause seam `graph.py:3359-3520`) | **Partially implemented**: the ask-side + pause routing is real code; the answer endpoint + frontend wizard are NOT (question-tool Phase 2 pending) |

### 4.2 Options compared

#### (a) Two-factor per ADR-015 — `user_confirmed` param AND server-side user-origin marker

**Mechanics:** the message-processing path, when dequeuing a HUMAN-type `MessageQueue` row, stamps `manager._user_origin_window[instance_id] = (turn_id, expires_at)`. The tool checks: `user_confirmed=true` AND a user-origin window covering THIS turn.

**Enforcement:** server-side. A fabricated `user_confirmed` from the LLM fails the second factor (no marker → refuse).

**Strengths:** ADR-015-consistent (this initiative extends ADR-015; deviating from its chosen mechanism needs stronger justification); single round-trip; cheap.

**Weaknesses:** verifies the TURN was user-originated, **not that the user confirmed THE ACTION**. Gap: a user-originated turn that unrelatedly mentions "upgrade" + an over-eager LLM calling the tool — the user said the word but didn't confirm the action. Mitigations: prompt-level instruction for Ari to restate + confirm; `dry_run` preflight that returns the plan + nonce (binding the action); the marker must be **per-turn** (not session-wide) to avoid stale authorization.

**Residual:** single-host trust model — an attacker with daemon-process access could forge the marker. Bounded by the hard constraint (single-user host).

#### (b) Challenge-nonce — tool persists pending action + nonce; subsequent HUMAN message must contain it

**Mechanics:** `system_upgrade(dry_run=true)` issues a nonce (8-char base32, e.g. `CONFIRM-7K2M-QX4T`), persists `pending_action{nonce, target, env, issued_at, TTL=15min, single_use}` to **disk** (journal / dedicated state file — NOT `MessageQueue` metadata, which is ephemeral — see R-SR10). Ari tells the user "reply with this nonce to authorize". The user replies (HUMAN message containing the nonce). On the next tool invocation with `user_confirmed=true, nonce="..."`, the tool verifies: (1) `user_confirmed=true`, (2) the triggering turn originated from a genuine HUMAN `MessageQueue` row, (3) the triggering HUMAN message **content contains the nonce**, (4) nonce matches the persisted pending action, (5) nonce not expired + single-use.

**Enforcement:** strongest — the unlock is a verifiable artifact of a genuine HUMAN message containing an unguessable nonce. The LLM cannot fabricate it **[aligned to §4.1's dated correction, 2026-08-22]:** the LLM sees the nonce in its own tool result, but the origin marker is stamped **only for whitelisted sources** (`USER_ORIGIN_SOURCES` at the stamp site, D-FA3.1) — agent-originated enqueues use `internal_agent:`/`internal_*` prefixes and do not stamp the marker, so a self-echoed nonce in an AGENT/internal-origin message fails check (2). (The earlier claim that "only the API POST /chat path stamps HUMAN" was imprecise — HUMAN is the else-branch default at `instance_messaging.py:1310-1319`; the whitelist is what makes the marker structural.)
**Strengths:** action-binding (the user confirmed THIS upgrade to THIS version, not just "said upgrade"); auditable (nonce + pending-action persisted); composes with (a) as a third factor.

**Weaknesses:** two round-trips; UX is clunkier (user must echo a nonce — though copy-paste mitigates); nonce appears in chat (shoulder-surfing irrelevant on single-user host); TTL + single-use bookkeeping; pending-action state must persist on disk (not `MessageQueue` — R-SR10).

#### (c) Question-tool-style interactive confirm — SSE `question_pack` + `POST /answer`

**Mechanics:** `system_upgrade` returns a pending confirm; frontend renders a confirm UI (mirrors the question-tool wizard); user clicks; `POST /api/instances/{id}/answer` resumes the instance with the answer injected as a HumanMessage.

**Enforcement:** real interactive gate; origin = HTTP POST from the user's browser session (the API is unauthenticated single-user — the "user" origin is the browser, fine on single-host trust model).

**Strengths:** best UX (one click, no nonce typing); the answer endpoint is the canonical "genuine user action" surface.

**Weaknesses:** **dependency on question-tool Phase 2** — `POST /api/instances/{id}/answer` is NOT implemented (no route in `daemon/api.py`). Blocking dependency on another initiative's delivery. Also: SSE requires the daemon up — fine for confirmation (which happens BEFORE restart), but the frontend wizard is additional work.

### 4.3 Recommendation

**Baseline: (a) two-factor per ADR-015** for BOTH live upgrade and live restart (ADR-015-consistent; this initiative extends ADR-015 — deviating from its chosen mechanism without stronger evidence is unjustified). **Augment with (b) action-binding nonce for LIVE only** (defense against the "user mentioned upgrade in passing" gap of (a)). **(c) is the P2.3+ UX evolution** once the answer endpoint lands (question-tool Phase 2).

So the LIVE gate is **3 checks**:
1. `user_confirmed: true` param
2. Triggering turn originated from a genuine HUMAN `MessageQueue` row (server-side marker — option (a))
3. The triggering HUMAN message **content contains the action nonce** issued by the tool's `dry_run` preflight (option (b) — action-binding)

**U4 consistency (2026-08-22):** the initiating HUMAN request ("upgrade live to 1.2.3") constitutes the U4 per-request approval for the live `dry_run` preflight itself — the nonce mint records it (`issued_at`, `issued_to_instance`, target, env) in the journal/ledger. The dry_run performs no mutation (preflight + nonce issue only), so the approval is spent on the armed call's 3-factor gate, not the preflight.

**Flow:**
```
user → Ari (HUMAN msg): "upgrade live to 1.2.3"
Ari → system_upgrade(target_env=live, version=1.2.3, dry_run=true)
  tool: preflight + issues nonce CONFIRM-7K2M-QX4T + persists pending_action to disk
  tool returns: "PREFLIGHT ... CONFIRMATION REQUIRED: nonce CONFIRM-7K2M-QX4T — reply with it to authorize"
Ari → user: "Reply with CONFIRM-7K2M-QX4T to authorize upgrade live 1.2.2→1.2.3"
user → Ari (HUMAN msg, contains nonce): "CONFIRM-7K2M-QX4T"
Ari → system_upgrade(target_env=live, version=1.2.3, user_confirmed=true, nonce="CONFIRM-7K2M-QX4T")
  tool: checks 1+2+3 → passes → arms pipeline (§6) → returns "SCHEDULED run_id=..."
```

**Nonce spec:** 8-char base32, prefixed `CONFIRM-`; single-use; TTL 15min; persisted to `releases/state.json` pending-action record (disk, survives death — R-SR10). On consumption: pending-action cleared, nonce logged to journal (audit trail survives `MessageQueue` wipe).

### 4.4 Refusal / timeout semantics

| Outcome | Trigger | Ari relays |
|---------|---------|------------|
| `user-confirmation-missing` | live execute call without a HUMAN-origin turn carrying the nonce | "Relay to the user: reply with the nonce to authorize." |
| `nonce-mismatch` | nonce param ≠ persisted pending nonce | "Nonce mismatch — re-run dry_run." |
| `nonce-expired` | issued_at + 15min < now | "Nonce expired — re-run dry_run for a fresh one." |
| `env-self-match` | target_env ≠ self-env | "Tools cannot target a different environment than the running daemon." |
| `rollback-cap-exceeded` | rollbacks_24h ≥ 3 (ADR-005 D2) | "Halted-for-human — rollback cap exceeded; see release_info(section=journal)." |
| `pipeline-busy` | per-env lock held | "Pipeline busy run_id=... — retry via upgrade_status." |
| `manifest-unsafe` | target `rollback_safe=false` | "Halt-for-human — target manifest unsafe." |
| `nonce-already-used` | nonce consumed | "Nonce already used — re-run dry_run." |

All refusals are structured strings Ari relays verbatim; Ari does NOT retry autonomously (hard req #1 — the LLM never decides go/rollback).

⟪SEAM: architect enrichment — (1) the exact plumbing of the user-origin marker (where in `instance_messaging.py` message-processing to stamp `_user_origin_window`; how the tool accesses the triggering message content without leaking other turns' content); (2) the nonce store location (journal extension vs dedicated `pending_actions.json` — must survive `MessageQueue` wipe + process death); (3) multi-instance scoping (the marker must be instance-scoped to Ari's instance — child instances must not inherit Ari's user-origin window); (4) whether the nonce check reads the triggering message from the in-memory bus or from the `MessageQueue` row (the row is the durable source but is wiped at startup — see R-SR10).⟫

---

## §5 Safety Interlocks

Each interlock is enumerated with its enforcement location. Tools NEVER issue raw kills / `lsof`/port kills.

| # | Interlock | Enforcement location | Notes |
|---|-----------|---------------------|-------|
| 1 | Health-gated pipeline routing only | restart = `stop-ensemble.sh` SINGLE-TERM + launcher re-exec via daemonized executor (§6); upgrade = P2.1 promote with gates | NEVER `kill -9`, NEVER `lsof -ti:PORT | xargs kill`. `stop-ensemble.sh` verified: SINGLE-TERM launcher-only, `CHILD_STOP_WAIT_S=70`, `WAIT_S` clamp 10..600, anchored cmdline match, ports report-only |
| 2 | Auto-rollback on `/readyz` fail | promote pipeline (P2.1) + `rollback.lock.d` | manifest `rollback_safe` gate → repoint `current`→`previous` → restart → quarantine → cooldown 10min (ADR-005 D2) |
| 3 | Rollback cap 3/24h → halt-for-human | journal counters + promote gate | ADR-005 D2; entry-side: promote preflight refuses at ≥3 (D-FA4.2 — the rollback itself always executes); tool surfaces `rollback-cap-exceeded` refusal |
| 4 | Concurrency lock (one pipeline per env) | `INSTALL_DIR/releases/rollback.lock.d` — **mkdir-based lock directory (D-FA5.1 canonical)**: mkdir = atomic acquire; contents `owner` (pid), `run_id`, `heartbeat` (epoch, refreshed ~30s); stale >300s → `mv` to `rollback.lock.stale.<pid>` → re-acquire; protocol implemented identically in `scripts/upgrade/lib.sh` + the Python journal module (P2.2 T4) + `deploy.sh` acquires too (manual deploys serialize with the pipeline) | tool preflight acquires; second invocation returns `pipeline-busy` with the active `run_id` |
| 5 | Env-target guard | tool preflight + pipeline | `target_env` must equal self-env (§3.2); live requires `ENSEMBLE_SELF_ENV=live` + `ENSEMBLE_DEPLOY_LIVE`-equivalent gate + 3-factor confirmation |
| 6 | Burst-abort interaction | executor + launcher exit-code mapping + journal | A restarting daemon under burst budget ≠ upgrade restart: the executor's stop produces a CLEAN shutdown (exit 0 path — no crash-counter tick). Intentional restart zeroes uptime → next crash starts a fresh budget (ADR-011 #2); journal records `kind=restart|promote` so a post-restart crash isn't misattributed. `restart-under-burst-abort` refusal if the daemon is in exit-1 latch hold |
| 7 | Idempotency of double-invocation | `run_id` + lock + nonce single-use | second call while active returns `pipeline-busy run_id=...` (not error); nonce single-use; boot sweep executes at most one pending-op |
| 8 | Drain phase-gating | promote pipeline (P2.1) | P2.3 promotes **drain-free** (ADR-009 M3); drain slots in at a later phase. Tools launch drain-free promotes initially |
| 9 | pg_dump preflight | promote pipeline (P2.1) | snapshot at promote preflight (timeout + skip, retention 2) — ADR-007; interim rollback-safety gate until `daemon_meta` lands |
| 10 | Version verify | `/livez` returns version (`daemon/api.py:1719-1733`) | post-restart `/livez` version must match `manifest.binary_version` — anti-gaming (R-SR15) |

---

## §6 Process-Death Sequencing (restart-specific design)

### 6.1 Verified constraints

- **All tools are in-process.** A tool call in-flight when the daemon stops is LOST: the turn freezes at the last committed node boundary; the tool call is NOT re-executed on `is_retry` resume (LangGraph checkpoint semantics).
- **`MessageQueue` is ephemeral.** `clear_all(preserve_in_flight=True)` at startup (`daemon/manager.py:596`). No tool precedent survives process death; no in-memory marker survives.
- **Bash tool kills process groups.** `daemon/tools/bash.py:138-160` — `_kill_process` SIGTERMs the entire process group so backgrounded children (e.g., `nohup ... &`) are terminated too. A tool that spawns restart/upgrade via a child in its own process group dies with the daemon. **Daemonization is required** (double-fork + `os.setsid` + `execv`) to escape the group — Phase-1 lesson: `nohup`'d children orphan/terminate because they share the group.
- **Deferred-pause pattern seam exists.** `graph.py:3359-3520` (question-tool): graph node sets a marker → post-graph callback runs the cascade OUTSIDE the task, `asyncio.shield`'d. Marker pop must be OUTSIDE the identity guard (C2 torn-state fix pattern).
- **Launcher does NOT respawn on clean exit (verified).** `launcher.sh:~470-490`: exit 0 (clean) → launcher exits 0, does NOT loop; exit 78 (refuse) → exits 78, no restart; only 75 (tempfail) and 1 (crash) loop. **There is NO in-contract "restart-me" exit code today.** An intentional restart = stop (launcher exits cleanly per SINGLE-TERM) + detached re-exec of `launcher.sh` by an external executor.

### 6.2 Options compared

#### (i) Tool returns "scheduled" + restart deferred post-turn via deferred-pause seam

**Mechanics:** tool arms the restart (marker + journal pending-op) → returns "SCHEDULED run_id=..." → Ari completes its report → post-graph callback (deferred-pause seam, `asyncio.shield`'d) fires the stop+respawn.

**Ari experiences:** tool returns "restart scheduled after this turn"; Ari's turn completes cleanly; report delivered; no frozen tool call.

**What breaks:** the post-graph callback executes the restart AFTER the turn — but turn-mirror finalization / report delivery may still be in flight. The launcher's SINGLE-TERM + bounded wait (`CHILD_STOP_WAIT_S=70`, uvicorn `timeout_graceful_shutdown`) gives in-flight finalization its window. **Residual:** if the post-graph callback crashes or the daemon dies before firing, the in-memory marker is lost → restart never happens → Ari said "scheduled" but nothing happened.

#### (ii) Two-phase: tool arms a marker ON DISK, returns final answer, launcher/boot step executes

**Mechanics:** tool persists `pending-op{run_id, nonce, env, mode, target, armed_at}` to `releases/state.json` → returns "armed, executes after this turn / at next stop" → an executor (boot step, or a daemonized process) reads the marker and executes.

**Ari experiences:** "restart armed, will execute after this turn".

**What breaks:** pure (ii) (marker only, execute at next natural stop) gives unbounded latency — not "restart now" semantics. The marker needs an execution trigger; that trigger is either the post-turn callback (→ becomes (i)) or a daemonized executor (→ becomes (iii)). **But (ii) has a killer property:** the marker SURVIVES process death (idempotent, re-armed on boot — ADR-012 sweep pattern). It composes with (i)/(iii) as the persistence layer.

#### (iii) Tool daemonizes stop+respawn itself, returns before death

**Mechanics:** tool double-forks + `os.setsid` + `execv`s a restart-executor (env-allowlisted, pid-filed, journal-logged). The executor: waits briefly for turn completion → SINGLE-TERM stop → [upgrade: pg_dump → flip] → detached re-exec `launcher.sh` → health gate → journal commit.

**Ari experiences:** tool returns "SCHEDULED" before death; the executor survives.

**What breaks:** race — the daemon may die before the tool result is committed to the checkpoint → lost result, Ari's turn frozen, report never delivered (this is exactly why (i)/(ii) defer the death). Also: the executor inherits the daemon's exported env (incl. `.env` contents — API keys!) → credential leak surface (R-SR09); orphan accountability (its lifetime is bounded ≤~3min, but a pid-file + journal entry make it observable).

### 6.3 Recommendation — hybrid (ii)+(i) with (iii) as execution mechanism

**No `system_*` tool ever blocks across its own process's death.** Everything is **arm → return → poll**.

```
1. Tool validates gate (live: 3-factor §4; demo: free) + acquires per-env pipeline lock (§5.4)
   + opens journal pending-op (run_id, nonce-consumed, env, mode, target, armed_at) — ALL ON DISK
   (survives death; R-SR10).
2. Tool RETURNS structured "SCHEDULED run_id=r-... (executes after this turn)" — never blocks.
3. Post-turn: deferred-pause seam (post-graph callback, asyncio.shield'd — question-tool precedent
   graph.py:3359-3520) fires the daemonized pipeline executor (double-fork + os.setsid + execv,
   env-allowlisted, pid-filed).
4. Executor: SINGLE-TERM stop (stop-ensemble.sh contract) → [upgrade: pg_dump preflight → flip]
   → detached re-exec launcher.sh → health gate → journal commit/quarantine/rollback.
5. Boot sweep (ADR-012 pattern): pending-op found at boot with owner dead + age > window →
   converge (execute-or-clear) — belt-and-braces for lost step 3.
6. Ari reports outcome on the NEXT turn via upgrade_status(run_id) / release_info.
```

**Why this hybrid:** (ii) gives persistence (survives death — the in-memory marker of (i)-pure is the failure mode); (i) gives the post-turn trigger (clean turn completion, report delivered, no frozen tool call); (iii) is the execution mechanism that survives the daemon's death (the only part that MUST be daemonized — the executor, not the trigger).

**Rejected alternatives' risks (R-SR02):**
- (iii)-pure (immediate daemonized stop, tool returns "before death"): race — daemon may die before the tool result is committed → lost result, frozen turn, undelivered report.
- (i)-pure (in-memory marker only): lost marker if death precedes the callback → silent no-restart; Ari said "scheduled" but nothing happened.
- (ii)-pure (marker only, execute at next natural stop): unbounded restart latency; not "restart now" semantics.

### 6.4 Alternative execution mechanism — exit-code extension ⟪SEAM⟫

An architecturally cleaner alternative to the daemonized executor: **add a "restart-me" exit code to the launcher contract** (ADR-010 amendment — e.g., exit 74 → launcher loops once immediately, no backoff, no burst-budget tick, journaled as intentional). Then the tool arms the journal pending-op + returns; the post-turn callback triggers daemon self-exit with code 74; the launcher respawns in-process; the boot step sees the pending-op and completes the pipeline (for upgrade: post-flip restart already handled by promote; for restart: nothing more needed).

**Trade-off:** (B) keeps everything in-process after arming — no orphan-able external process, no env-leak surface (R-SR09). But it changes ADR-010's exit-code contract (0/75/78/1 → +74) and couples the restart path to launcher changes (W1/P2.1 territory). ⟪SEAM: architect + W1 to decide (A) daemonized executor vs (B) exit-code 74 — ⟫ **[RESOLVED 2026-08-22: **daemonized executor for BOTH restart and promote** (option A generalized — one mechanism); **exit-74 deferred** as a future ADR (ADR-010 amendment + capability-probe + `launcher-not-74-aware` refusal design preserved). Rationale: R-SR06 ship-ordering + pre-74 bootstrapping window; the deferred-pause trigger fires at exact turn-end so the waiter race that motivated 74 does not exist. See architecture-recommendation.md FA1/D-FA1.3.]**

### 6.5 What Ari experiences / user sees / outcome reported

| Phase | Ari | User | Outcome source |
|-------|-----|------|----------------|
| Pre-arm | calls tool; receives SCHEDULED + run_id | sees Ari's "restarting now, back in ~1-2 min" | tool return (run_id) |
| During pipeline | (turn complete; Ari idle or in a later turn polling) | sees daemon-down (frontend SSE reconnect) | `upgrade_status(run_id)` journal tail |
| Post-restart | user asks "did it work?" → Ari calls `upgrade_status(run_id)` / `release_info(section=current)` | sees "daemon restarted" nudge (frontend reconnect) | journal terminal entry + `/livez` version verify |

**Sub-minute UX caveat:** `ReportDeliveryRecovery` is periodic-only (300s interval, 10min age-bound — `daemon/services/report_delivery_recovery.py:136, :275`; NO boot sweep) → **do NOT rely on it for sub-minute outcome reporting**. The outcome is reported on the NEXT user interaction (pull model: `upgrade_status`). Push-notification when the daemon returns (NotificationBroadcaster SSE reconnect — R-SR12) is a ⟪SEAM⟫ for P2.3.

### 6.6 Upgrade progress observable DURING the pipeline

`system_upgrade` does NOT block through the restart (§6.3). Ari's turn completes; the pipeline runs in the daemonized executor (spans daemon death). Progress is observable via `upgrade_status(run_id)` — journal tail (phase transitions: `txn-open` → `pg_dump` → `stop` → `flip` → `start` → `/livez` → `/readyz` → `soak` → `commit`/`rollback`). Ari polls (or the user asks "how's it going?"). Terminal state (committed/rolled-back/refused/halted) readable post-restart. This is **deviation D-2** from ADR-015's literal "returns the terminal result" — the terminal result IS returned, but as the result of `upgrade_status(run_id)`, not of the original blocking call. Justified by the verified process-death facts (§6.1).

---

## §7 Observability — what Ari reports to the user

| Stage | What Ari reports | Data source |
|-------|------------------|-------------|
| Pre-flight | current version, target, `rollback_safe`, journal state, lock state, planned phases | `release_info(section=current\|journal)`, `manifest.json`, `rollback.lock` |
| Progress | phase transitions, elapsed, pg_dump result, flip, start, `/livez`/`/readyz` polling | `upgrade_status(run_id)` → `releases/state.json` journal tail |
| Terminal | committed / rolled-back / refused / halted-for-human + WHY (which gate failed, quarantine flag) | `upgrade_status(run_id)` terminal entry |
| Post-restart health | `/livez` (version), `/readyz` (composite: db SELECT1, queue-freshness, services), version verify vs `manifest.binary_version` | `daemon/api.py:1719-1733` (`/livez`), `:1735-1779` (`/readyz`), `manifest.json` |
| Rollback-cap counters | `rollbacks_24h` / 3, cooldown remaining | `releases/state.json` journal |
| Launcher state | last exit code, uptime, burst count | `.launcher-state` (atomic) |

**`/readyz` composite (verified):** `SELECT 1` (500ms timeout) + queue-freshness (max-age over `Task.last_heartbeat_at`) + bus-started flags + schema check + draining flag; 10s background refresher; handler is O(1) memory read; `ENSEMBLE_READINESS_FORCE_DEGRADED` one-way knob (read per refresh tick — `daemon/services/readiness.py:48-67`). Anti-gaming (R-SR15): the gate must sample `/readyz` post-restart fresh (not the pre-restart cached value); version verify via `/livez` prevents a stale-binary reporting green.

---

## §8 Registration Checklist (zero-archaeology)

Ordered file-touch list for a new `system_upgrade` category. A developer implementing P2.2 follows this verbatim.

```
1. CREATE daemon/tools/upgrade_tools.py
   - factory: create_upgrade_tools(manager, current_instance_id, agent_id, agent_tag) -> list
   - per @tool function: @register_tool_category("system_upgrade") ABOVE @tool
     (decorator registers category docs/metadata — _tool_registry.py:75)
   - CATEGORY_NAME = "System Upgrade"; CATEGORY_DOC = "..." (short docstring for LLM context)
   - tools: system_upgrade, system_restart, release_info, upgrade_status (§1)

2. CATEGORY_MODULES entry — daemon/tools/_tool_registry.py:423-457
   "system_upgrade": "daemon.tools.upgrade_tools",
   (the AST-scan walker at :199-239 discovers @tool names inside the factory)

3. REGENERATE KNOWN_TOOL_NAMES — daemon/tools/_tool_registry.py:491+
   run: uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"
   paste the 4 new names into the frozenset
   (frozen-binary fallback — prod runs the PyInstaller binary where source files
    are bytecode-only; without this, startup validation emits false "unknown tool"
    warnings; drift test tests/unit/tools/test_frozen_tool_name_discovery.py MUST pass)

4. DYNAMIC_TOOL_NAMES — daemon/tools/_tool_registry.py:23-64
   add: "system_upgrade", "system_restart", "release_info", "upgrade_status"
   (factory-created, not import-time registered; needed for startup validation + frozen binary)

5. CRITICAL list-append — daemon/tools/instance.py create_instance_tools() (~:1895-2073)
   upgrade_tool_list = create_upgrade_tools_if_available(manager, current_instance_id, agent_id, agent_tag=version_tag)
   tools.extend(upgrade_tool_list)
   ⚠️ GOTCHA: decorator-only = never constructed = silently invisible.
   Precedent: :1930 tools.extend(job_tools); :1993 tools.extend(question_tool_list).

6. tools.allow — agents/ari/meta.json (verified: 14 entries)
   add "system_upgrade" to tools.allow (category name resolves via instance.py:284-289)
   ⚠️ jober DEFERRED (deviation D-4 vs ADR-015's ari+jober)

7. EMPTY-ALLOW LEAK MITIGATION — daemon/tools/instance.py create_instance_tools()
   special-case: system_upgrade category excluded from the empty-allow universe (§3.5, R-SR16)
   only constructed when explicitly present in tools.allow

8. META LOOKUP — use get_version(id, tag) with fallback to get_resolved()
   (critical-notes pattern; affects tools.allow, team_members, path, skill_injection)

9. DOCS entry — CATEGORY_DOC + docs/ tool docs if the project maintains a tool catalog

10. TESTS
    - allow-expansion unit test (ari sees 4 tools; jober/worker/explorer see 0)
    - frozen-name drift test (tests/unit/tools/test_frozen_tool_name_discovery.py passes)
    - gate refusal tests (live without HUMAN-origin → refuse; demo → free)
    - nonce lifecycle (issue → consume → single-use → expire)
```

---

## §11 Deviations from ADR-015 (explicit flag list)

This initiative EXTENDS ADR-015. The following deviations are introduced by the Phase-2 design (justified by verified facts):

| ID | Deviation | Justification | Phase |
|----|-----------|---------------|-------|
| **D-1** | NEW `system_restart` tool (ADR-015 has none) | Restart is a first-class operational action distinct from upgrade (config reload, knob clear, recovery); folding into `system_upgrade` overloads its contract. Restart = degenerate promote (target=current) — same execution mechanism, distinct entry semantics | P2.2 |
| **D-2** | `system_upgrade` does NOT block to terminal result; returns armed/preflight + terminal via `upgrade_status` | Process-death forces it (§6.1): a synchronous tool cannot return a terminal result across its own restart — the ToolMessage would be lost. The terminal result IS returned, as the result of `upgrade_status(run_id)` | P2.2 |
| **D-3** | NEW `upgrade_status` tool | Needed because `system_upgrade`'s terminal result is only observable post-restart (§1 decision) | P2.2 |
| **D-4** | `tools.allow` for ari ONLY this phase (ADR-015 named ari+jober) | Ari is the conversational front door; jober's upgrade authority is a separate trust decision; jober has no `bash` today (cannot execute pipeline scripts) | P2.2 |
| **D-5** | Env-target permission model (demo/dev free vs live gated) | ADR-015 had no env dimension; the hard constraint requires structural live-protection | P2.2 |
| **D-6** | Nonce action-binding added on top of ADR-015's two-factor | OQ9 resolution proposal (not final) — closes the "user mentioned upgrade in passing" gap of pure two-factor (§4.2(a)) | P2.2 |
| **D-7** | Pipeline is scripts-based (deploy.sh/promote per W1 P2.1), not `make` targets | ADR-009 named `make stage/promote/rollback`; W1's P2.1 plan supersedes with scripts (per the task mandate "scripts-not-make pipeline"). Consistency note: the promote semantics are unchanged; only the invocation surface differs | P2.1 |
| **D-8** | Phase placement: P2.2 (this initiative), NOT ADR-015's "Phase 7" | ADR-015 placed tooling at Phase 7 of the ORIGINAL auto-restart-upgrade initiative; this Phase-2 initiative implements it early (the promote pipeline P2.1 is the prerequisite) | P2.2 |

---

## §12 Open Questions (for architect / W1 / W3)

1. **Exit-code 74 (restart-me) vs daemonized executor** (§6.4) — gates §6.3 step 3-4. ⟪SEAM: architect + W1⟫ **RESOLVED 2026-08-22 — daemonized executor for both; 74 deferred (future ADR).**
2. **`ENSEMBLE_SELF_ENV` marker** (§3.2) — ~~W1 P2.1 to add to `.env.prod`/`.env.demo` staging alongside ADR-014 mechanism. ⟪SEAM: W1⟫~~ **RESOLVED (D-FA2.3, ratified 2026-08-22):** marker is MANDATORY, staged into `INSTALL_DIR/.env` by `deploy.sh`/`stage.sh` (P2.1 T2 carries the staging task + acceptance); the PORT-derivation fallback is **REJECTED**; marker absent → every actor tool refuses fail-closed (`env-marker-absent`, S-31) while read tools still answer. No W1 seam remains.
3. **Empty-allow special-case placement** (§3.5, R-SR16) — confirm no regression on existing empty-allow agents. ⟪SEAM: architect⟫ **RESOLVED 2026-08-22 — only `watcher` is empty-allow today; no regression possible; see architecture-recommendation.md FA2.**
4. **User-origin marker plumbing** (§4.4) — where to stamp, how the tool reads the triggering message content safely. ⟪SEAM: architect⟫
5. **Nonce store location** (§4.4) — journal extension vs dedicated `pending_actions.json`. Must survive `MessageQueue` wipe + process death. ⟪SEAM: architect⟫
6. **Push-notification UX post-restart** (§6.5) — NotificationBroadcaster SSE reconnect nudge. P2.3 scope. ⟪SEAM: W3 test-strategy + frontend⟫
7. **`"Error:"` vs `"ERROR:"` convention** (§2) — normalize across new tools. ⟪SEAM: low-priority consistency⟫
8. **Sandbox target support** (§3.1) — possible later extension; out of P2.2 scope.

---

## References

- ADR-015 — `.agents/shared/planning/auto-restart-upgrade/decisions.md:200-212` (baseline; this initiative extends it)
- ADR-004/005/009/010/011/012/014 — same file (release layout, auto-rollback, Makefile→scripts, exit codes, boot DB outage, journal sweep, port separation)
- Question-tool precedent — `daemon/tools/question_tools.py:122-189`, `daemon/graph.py:3359-3520` (deferred-pause seam), `.agents/shared/planning/question-tool/plan-overview.md` (answer endpoint — planned, NOT implemented)
- W1 docs (by filename, not created here): `plan-overview.md`, `phase1-plan.md` (P2.1), `phase2-plan.md` (P2.2), `phase3-plan.md` (P2.3)
- W3 docs (by filename, not created here): `test-strategy.md`, `promotion-ladder.md`, `decisions.md`
- Verified code: `daemon/tools/_tool_registry.py:75,423-457,491+,23-64`; `daemon/tools/instance.py:276-289,~1895-2073`; `daemon/repositories/message_queue/models.py:19-25,49`; `daemon/services/instance_messaging.py:1319`; `daemon/manager.py:596,478-501,7525+`; `daemon/services/stale_task_recovery.py:637-795`; `daemon/services/report_delivery_recovery.py:136,275`; `daemon/services/readiness.py:48-67`; `daemon/tools/bash.py:138-160`; `launcher.sh:151-174,349-374,~470-490`; `scripts/deploy.sh:276,282`; `scripts/stop-ensemble.sh:35,68-71`; `daemon/api.py:1719-1779`; `agents/ari/meta.json`
