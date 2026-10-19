# Plan Overview: `ari-tools` — Ari's self-evolution toolbox (board + self-alarm + live views)

Date: 2026-10-19
Author: worker via plan-creation (commission `ari-tools`, final planning wave, dispatched by planner)
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`)
Commission: **ari-tools** — user-approved 2026-10-19.

> **This document is a SYNTHESIS, not a re-derivation.** The canonical detail lives in the three phase
> plans and the decisions ledger. Where this document states *what*, the phase doc carries *why + anchors*.
> Every claim below is cited to its owning document; nothing here re-litigates a settled decision.

---

## 1. Commission context — the problem

Ari today is a set of **separate trees**. Every Ari instance — one per chat surface, one per wake-up, one
per spawned task — is a fresh, memoryless tree root. Three concrete consequences were reported by the user:

1. **No cross-instance continuity.** Work Ari does in one chat is invisible to Ari in the next. There is no
   durable surface where "what I promised to do" survives the instance boundary. The existing `todo` family
   is an in-memory, per-instance DAG (`daemon/services/todo_manager.py:186-214`) — it dies with the instance.
2. **No self-wake ergonomics.** "Remind me in 40 minutes" requires Ari to know the scheduling engine's payload
   grammar, the label namespace, the label-burn rules and the timezone-resolution chain. The engine exposes
   `task_schedule`, but that is a raw engine, not a tool an agent can use without a briefing.
3. **No rich-content sharing over Discord/Telegram.** Ari cannot hand a customer a live, always-current page.
   Chat surfaces carry text; a table, a status board or a formatted report has no home.

Phase 3 adds a fourth, compounding problem: **the human's chat is the only surface where a person can look**,
so any board or report that lives only in the DB is invisible to the owner unless it is also published.

### 1.1 What the commission builds (three deliverables, one branch)

| Phase | Deliverable | One-line |
|---|---|---|
| 1 | **The Workbook** | A DB-backed, globally-namespaced work board exposed as 4 agent tools (`board_create` / `board_update` / `board_list` / `board_get`). |
| 2 | **`self_alarm`** | A policy-enforcing tool that arms a reminder to itself ≥30 minutes out, delivered into a fresh Ari instance. |
| 3 | **Live views** | `view_write` (publish rich HTML as a passcode-gated public link) + the flagship server-rendered live board view. |

---

## 2. Objectives per phase

### Phase 1 — The Workbook (cross-instance persistent work board)

Deliver a DB-backed work board that is **one global namespace** — not one board per chat, not one per project.
Items carry a stable id (`wi-<uuid8>`), title, markdown body, a 4-value status enum, tags, a nullable project FK,
a nullable tz-aware due date, linked job ids, and author/created/updated provenance. **Notes are append-only**:
the service exposes exactly one notes mutation (an INSERT); there is no update/delete code path anywhere.
Channel and project are *attributes of an item* (tags / FK), never separate boards.
→ [phase1-plan.md](phase1-plan.md)

### Phase 2 — `self_alarm` (alarm/timer wrapper for Ari)

Give Ari ONE tool that arms a reminder to itself ≥30 minutes out, without Ari needing to know the scheduling
engine's payload grammar, label lifecycle or timezone-resolution rules. The wrapper enforces the floor
**pre-create** (a rejected alarm burns no label), resolves the timezone once server-side using the engine's
own public primitives, composes a **self-contained** message (the receiver is a fresh instance with no memory
of the request), and optionally links a board item by id. The engine is reused verbatim — no engine change.
→ [phase2-plan.md](phase2-plan.md)

### Phase 3 — `view_write` + the live board view

Two deliverables riding the **existing** passcode-gated public-link substrate (zero new crypto, zero new tables,
zero proxy change): (A) `view_write(path, html, title)` — publish an HTML page under a daemon-global `reports`
root and get back a public URL + 6-char passcode in one call; (B) the **flagship**: `/views/public/board/index.html`,
a zero-JS, strictly-CSP'd page server-rendered from the DB on every GET, so the board the customer sees is
always the board that exists. Plus `board_link()` to mint that URL.
→ [phase3-plan.md](phase3-plan.md)

---

## 3. Phase breakdown

| Phase | Deliverable | New files | Key edit sites | Owning plan doc |
|---|---|---|---|---|
| 1 | Workbook: 3 tables + `BoardService` + 4 tools in a new `"board"` category | **8** — `daemon/models/board.py`, `daemon/services/board_service.py`, `daemon/tools/board_tools.py`, `daemon/migrations/versions/20261019_190000_create_board_tables.sql`, 4 test files | `daemon/manager.py` (late model import before `create_all`; service construction; `board_service` property) · `daemon/tools/_tool_registry.py` (`DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES`) · `daemon/tools/instance.py` (factory + `tools.extend`) · `agents/ari/meta.json`, `agents/leader/meta.json` (allow-list) | [phase1-plan.md](phase1-plan.md) |
| 2 | `self_alarm`: pure service module + tool module | **5** — `daemon/services/self_alarm_service.py`, `daemon/tools/self_alarm.py`, 3 test files | `daemon/tools/_tool_registry.py` (3 entries) · `daemon/tools/instance.py` (factory + extend) · `agents/ari/meta.json` (`"self-alarm"`, ari only) · `agents/ari/tools_note.md` (new `## Self-Alarm` section) | [phase2-plan.md](phase2-plan.md) |
| 3 | `view_write` tool + live board view + `board_link` tool | **7** — `daemon/tools/view_write.py`, `daemon/tools/board_link.py`, `daemon/services/board_view_service.py`, `daemon/services/board_view_pages.py`, 3 test files | `daemon/api.py` (lifespan `reports`-root injection) · `daemon/services/live_views.py` (reserved name + 2 accessors) · `daemon/routers/live_views.py` (board GET/HEAD **before** the public catch-all) · `daemon/tools/_tool_registry.py` (incl. `PRIVILEGED_TOOL_CATEGORIES`) · `daemon/tools/instance.py` · `agents/ari/meta.json`, `agents/leader/meta.json` · `config.yaml` (doc-only note) | [phase3-plan.md](phase3-plan.md) |
| | **Total** | **20 new files** | 3 shared edit files (`_tool_registry.py`, `instance.py`, `agents/ari/meta.json`) | |

**Migration count: exactly 1** (phase 1's `create_board_tables.sql`). Phases 2 and 3 add **no** table and
**no** column — phase 3's `live_view_passcodes` table already exists
(`daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql`).

---

## 4. Build-order rationale (BINDING: 1 → 2 → 3)

The user bound the build order **1 → 2 → 3**. The technical justification for that order, and the reason the
order is *not* strictly serial, both matter:

- **Phase 1 must land first** because it creates the *schema* everything else reads. Phase 2's alarm reads
  `due_at` (tz-aware, indexed) and `status`; phase 3's board page reads the item projection and the notes
  timeline. `SQLModel.metadata.create_all` does **not** ALTER existing tables
  (`daemon/manager.py:6597-6605`), so a column added after the first boot needs *both* an
  `_ensure_postgres_columns` entry *and* a `.sql` ALTER. Phase 1 pins the four hooks phases 2 and 3 need
  (`due_at`, `status`, `tags`, `linked_job_ids`) precisely so no later ADD is required — that is only possible
  if phase 1 is designed knowing what phases 2 and 3 read.
- **Phase 2 consumes only ONE thing from phase 1: the pinned `wi-<uuid8>` id format.** It validates the id by
  regex (`^wi-[0-9a-f]{8}$`) and embeds it verbatim in the message — it never imports a phase-1 module and
  never calls the board service. That is why phase 2's board glue is a **prompt convention** rather than a
  service coupling (see [decisions.md](decisions.md) §C.3).
- **Phase 3 couples to phase 1 through exactly ONE seam**: a shape-tolerant read adapter over
  `manager.board_service`, in a single phase-3-owned file (`daemon/services/board_view_service.py`). A phase-1
  rename or move costs one import line.
- **Parallelizability, stated honestly:** once phase 1 lands, phases 2 and 3 are **technically parallelizable** —
  they share no file except the append-only tail of `agents/ari/meta.json`'s allow array. The build order stays
  1 → 2 → 3 anyway, because (a) the user bound it, and (b) a single sequential order keeps one worktree, one
  branch, one merge chain and one staged release for the whole commission. Parallel execution would trade a
  binding constraint for merge-order risk with no delivery benefit.
- **Merge-ordering note:** phases 2 and 3 both append to `agents/ari/meta.json`'s allow array (phase 3 also
  edits `agents/leader/meta.json`; phase 1 edits both). All appends go at the **END** of the array
  (phase-1 D15 discipline) so the three phases produce non-overlapping hunks.

---

## 5. Cross-phase coupling map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **Phase 1** | — | **loose** — shared *id format* only (`wi-<uuid8>`); no shared module | **tight** — one read adapter over `manager.board_service`; the pinned item + notes projection |
| **Phase 2** | loose | — | **loose** — shared *semantic* contract only (the board item id and the note shape a self-alarm links to); no code coupling |
| **Phase 3** | tight | loose | — |

### The three shared surfaces (and how to keep them clean)

1. **The single-import seam (P1 ↔ P3).** `daemon/services/board_view_service.py` is the ONLY file that knows
   phase 1's surface. It is shape-tolerant (`getattr(obj, "x", None)` **or** `obj["x"]` — the `_row_locked` /
   `_row_generation` precedent) and degrades to an explicit "board backend unavailable" notice, **never a 500**.
   Phase 3 also adds **no row** to phase 1's `CATEGORY_MODULES["board"]` and **no file** to phase 1's
   `daemon/tools/board_tools.py` ⇒ zero textual merge conflict there.
2. **The shared migration-prefix namespace (P1 only, but against the world).** The runner enforces one ledger
   row per version prefix (`schema_migrations.version TEXT PRIMARY KEY`, `daemon/migrations/runner.py:211-220`).
   A duplicate prefix breaks a **fresh SQLite boot** with `UNIQUE constraint failed: schema_migrations.version`.
   The repo already carries a pre-existing duplicate (`20261018_000001_*` appears twice) — do not copy it.
   Phase 1's rule: verify `ls daemon/migrations/versions/ | grep '^20261019_19'` is **empty** *before* writing
   the file, and bump the seconds if not.
3. **The shared allow-list edits (all three phases → `agents/ari/meta.json`).** Phase 1 adds `"board"`,
   phase 2 adds `"self-alarm"`, phase 3 adds `"view-write"` + `"board-link"`; phase 1 and phase 3 also touch
   `agents/leader/meta.json` (`"board"`, `"board-link"`). **Merge-ordering note:** all edits append at the END
   of the array (phase-1 D15), and each phase's allow-list pin test is per-phase — so a phase-3 merge must not
   assume phase 1's or phase 2's entries exist, and vice versa. `agents/ari/meta.json` and
   `agents/leader/meta.json` are **not** seal-pinned (the pinned set is `.md` artefacts only), which is why all
   three phases can touch them freely.

### What is NOT a coupling

- **Phase 2 ↔ the scheduling engine:** the engine is reused verbatim — `daemon/services/scheduling_service.py`
  and `daemon/sources/adapters/scheduler.py` are **unchanged** by phase 2.
- **Phase 3 ↔ the proxy/infra layer:** **zero** proxy change (both new surfaces live under `/views/public/**`).
- **Phase 3 ↔ the DB schema:** phase 3 adds no table and no column.

---

## 6. Consolidated risk table

Deduplicated across the three phase plans; where two phases describe the same hazard, the **highest-severity**
and **most-specific** variant is kept and the other phase's id is noted in the row.

| # | Risk | Impact | Likelihood | Mitigation | Source |
|---|---|---|---|---|---|
| R1 | **Duplicate migration prefix ⇒ fresh-SQLite boot failure.** The runner enforces one ledger row per version prefix; a collision yields `UNIQUE constraint failed: schema_migrations.version`. The repo already carries a pre-existing duplicate (`20261018_000001_*`). | **High** | Low | Verify prefix uniqueness **before** writing the file (`ls daemon/migrations/versions/ \| grep '^20261019_19'` → empty); bump the seconds if not. Never copy the pre-existing defect. | P1-R8 |
| R2 | **Privileged vs non-privileged category for the board.** Phase 1's `"board"` is **non-privileged** ⇒ default-granted to every empty-allow agent, diluting the "ari + leader" pre-ruling; phase 3 argues restrictive semantics **require** privileged categories (its `view-write` is privileged, and it splits `board-link` out for exactly this reason). Making it privileged costs the SAME-PR five-pin ceremony. | **Medium (policy)** | High (certain for non-privileged) | **ARCHITECT-TO-RULE** — the two sides, the cost of each, and an evidence-based recommendation are laid out in [decisions.md](decisions.md) §C.1. Note the pins must be updated in the SAME commit anyway if phase 3's `view-write` + `board-link` land (P3-R6). | P1-R2/Q1, P3-D3/OQ-2, P3-R6 |
| R3 | **Lateness-cap / missed-alarm semantics.** Default `one_shot_max_lateness_seconds=None` ⇒ a ≥30-min alarm whose instant passed during downtime fires **very late at next boot**. With the cap set, a later boot yields a `skipped` history row, **no dispatch**, and the schedule **stays armed** — a permanently-armed skip-loop that never fires without an operator update. | **High** | Medium | Document the exact behaviour in the tool's `_full_doc_` + the tools_note section; the composed message states the **scheduled** instant so a late delivery is detectable. Phase 2 changes **no** engine semantics (OQ-4 is a deployment-policy call). | P2-Risk 1, P2-OQ-4 |
| R4 | **Live-views mount order.** The board route declared *below* the public catch-all ⇒ the catch-all resolves `board` as a file and the board **silently 404s** (Starlette is first-match-wins). | **High** | Low | The board GET/HEAD is declared **immediately before** the catch-all with a loud comment restating the rule; a route-order assertion pins the resolution. | P3-R1 |
| R5 | **Config-roots-replace footgun.** An operator `live_views.roots:` block **replaces** the seed dict (`config.py:2903-2914`) ⇒ a config-declared `reports` root would silently vanish. | **High** | Low | The `reports` root is **lifespan-injected**, never operator-declared; injection is idempotent and never overrides an operator-declared entry. `config.yaml` gets a **doc-only** note. | P3-R2, P3-D1 |
| R6 | **32 MiB served-page cap.** A page larger than the served cap can never be served; a page that cannot be served must never be written. | Low | Low | The write path imports the **same** `MAX_SERVED_BYTES` constant the serve path uses (one source of truth) and refuses oversize input with a typed error naming the limit and the byte count. | P3-R8, P3-D6 |
| R7 | **Seal-pinning re-pin obligation.** Any edit to `agents/ari/{rule,workflow}.md` or `agents/leader/rule.md` requires an adjudicated seal re-pin. `agents/ari/workflow.md` is **already drifting** — **7 stale pins measured at plan time**, which are **NOT ours to fix** and must not be silently absorbed. A new edit would add an 8th. | **Medium** | Medium | **No phase in this commission edits any seal-pinned file.** Phase 2's prompt surface is `agents/ari/tools_note.md` (not pinned); phase 3 touches only `meta.json` (the seal covers `.md` artefacts only — `meta.json` appears zero times). The stale-pin count must stay at the measured baseline (7); the conventions edit + its re-pin is a **separate phase-5 commission**. | P2-scope note, P3-D19 |
| R8 | **Proxy `/views/public/**` exemption invariant.** `/views/public/**` must stay proxy-exempt in **both** deployment states (State A merge+apply / State B zero-operator), with the canonical gate daemon-side. Any new surface outside `/views/public/**` would break the contract. | **High** | Low | Both new surfaces live under `/views/public/**` already ⇒ **zero proxy change**; no gate-free route is added; Host preservation is untouched (the same-host CSRF check depends on it). The existing public-prefix exemption integration test stays green. | P3-D18, P3-§6 |
| R9 | **Cross-phase seam drift** — phase 1 renames/moves the service or changes the item shape ⇒ phase 3 breaks. | **High** | Medium | Exactly one adapter file is the coupling point; it is shape-tolerant and degrades to an explicit "unavailable" notice, never a 500. **The phase-3 builder MUST reconcile against the landed phase-1 code before merging.** | P3-R4 |
| R10 | **Schema freeze.** `create_all` does not ALTER existing tables; after the first board boot, any phase-2/3 column ADD needs *both* an `_ensure_postgres_columns` entry *and* a `.sql` ALTER. | Medium | Medium (certain, if a column is added) | Phase 1 pins the four hooks phases 2 and 3 need, so no later ADD is required. | P1-R4 |
| R11 | **Burned label / recurrence-family mismatch.** A cancelled schedule label is burned **forever** (no hard delete for scheduler rows); `update` cannot change the recurrence family. | Medium | Medium | Label lookup first ⇒ **reuse in place** for `{running, stopped}`; a **burned** label auto-retries once as `"<label>-2"` (then `-3`, cap 5) and the **effective** label is echoed; a recurrence-family change is a typed error. A hard error would deadlock the agent. | P2-Risk 2, P2-D4 |
| R12 | **`self-alarm` leaks to another agent.** An agent with an empty allow **and** deny list gets everything; an allow-list is a *visibility* boundary, not a security boundary. | **High (policy)** | Low | The ari-only rule is enforced **twice**: the allow-list **and** a runtime `agent_id != "ari"` gate in the tool body (the `blueprint` writer-gate precedent); a registration test asserts exactly one `meta.json` grants the category. | P2-Risk 6, P2-D10 |
| R13 | **The alarm lands in the wrong place.** The human expects a chat ping; the scheduler source is **one-way** (`SchedulerAdapter.send` only logs) ⇒ the reply never reaches the chat surface. | Medium | Medium | Stated plainly in the tool doc + the tools_note section: the alarm lands in the agent's own workspace and can act autonomously. Chat bridging is an **open product call** (OQ-1), not a code call. | P2-Risk 3, P2-OQ-1 |
| R14 | **Merge collision on `agents/ari/meta.json`** — all three phases append to the same allow array. | Low | Medium | Every append goes at the **END** of the array (phase-1 D15 discipline); each phase's allow-list pin test is per-phase. | P3-R12, P1-D15 |
| R15 | **Privileged-category pin blast** — adding privileged categories trips the SAME-PR five exact-set pins. | Medium | Low | All five pins updated in the **same commit** as the category addition. | P3-R6 |
| R16 | **`linked_job_ids` read-modify-write** is safe only under the service write lock; a future multi-process writer needs row-level locking. | Low | Low | A `threading.RLock` held across the whole write transaction serialises all board writes; the daemon is single-process today. Flagged for any future multi-process deployment. | P1-R5 |
| R17 | **No per-item ACL** — any agent holding the category can write the global board. | Low | Medium | Consistent with the repo trust model ("the only access boundary is the per-agent `tools.allow` category gate"); the board carries no secrets and grants no authority. Revisit only if the board ever holds sensitive content. | P1-R6 |
| R18 | **`wi-<uuid8>` is 32 bits of entropy.** Birthday collision: P≈1 at ~77k items, P≈0.0001 at 1k. | Low | Low | The id is the **PRIMARY KEY** ⇒ a collision raises `IntegrityError` and the create **fails loudly** (no silent overwrite, no data loss); the agent retries with a fresh id. The shape is fixed by the binding; flagged, not changed. | P1-R1 |
| R19 | **FK behaviour differs across drivers.** `project_id → projects.project_id ON DELETE SET NULL` is enforced by PG; SQLite ignores FKs unless `PRAGMA foreign_keys=ON` (off by default) ⇒ a deleted project leaves a dangling `project_id` on SQLite. | Low | Medium | Acceptable: the item survives either way and the value is informational — no code path depends on the FK's SET NULL behaviour (the project filter is by value, not by join). | P1-R3 |
| R20 | **Unbounded board growth** — no retention policy in v1; `board_notes` grows monotonically. | Low | Medium | `board_list` is bounded by `limit`; archival is a v2 topic. Add a retention decision before the board exceeds a few thousand rows. | P1-R7 |
| R21 | **`todo` confusion** — agents already have a per-instance in-memory `todo` family; the board is durable and global. | Medium | Medium | The distinction is documented in the tool docstrings, `CATEGORY_DOC` and `agents/ari/tools_note.md`: todo = ephemeral per-turn planning, board = durable cross-instance user work. | P1-R9, P1-D17 |
| R22 | **Phase-1 schema under-specifies phases 2/3** — the alarm horizon and the live view need fields not bound in phase 1. | Medium | Medium | Phase 1 pins the four hooks phases 2/3 need (`due_at` indexed + tz-aware, `status` indexed, `tags` junction, `linked_job_ids` JSONB). Any further need is an explicit additive change in a later phase, not a phase-1 rework. | P1-R10 |
| R23 | **Wrapper timezone drift** ⇒ the floor is computed against the wrong instant. | Medium | Low | The wrapper resolves tz via the engine's **public** primitives only, and a parity test pins it against `scheduling_service._resolve_schedule_timezone` across the {explicit tz, user setting, env default} matrix. | P2-Risk 5 |
| R24 | **Idempotency collapse** — re-arming at the identical instant within 24 h of a still non-terminal prior job collapses into that job (key `scheduler:{source_id}:{run_at}`) ⇒ no second delivery. | Low | Low | The wrapper **rejects** an unchanged instant on a `stopped` label; a terminal prior job (the normal case after a fire) does not block a fresh submit. | P2-Risk 8 |
| R25 | **Update-path rebuild gap (<1 s) loses a re-armed fire.** | Low | Low | Every armed instant is ≥30 min out ⇒ the window is irrelevant. | P2-Risk 9 |
| R26 | **New category breaks the frozen-binary tool universe** (the tool vanishes in prod). | High | Low | The new tool name is added to **both** `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES`; the frozen-discovery drift pin stays green. | P2-Risk 10, P1-D11 |
| R27 | **Prompt-surface drift** — the `tools_note.md` section is a second home for the grammar. | Low | Medium | One canonical home (the tools_note section); the tool doc references it; the forbidden-token + cross-ref tests enforce the closure. | P2-Risk 11 |
| R28 | **Notes N+1 on the board render** — `list_items` omits notes; the board hydrates them per row via `get_item` (≤50 rows ⇒ ≤50 bounded reads per render). | Low | Low | Bounded and degraded per row ("notes unavailable" instead of a 500); OQ-1 asks phase 1 whether the list projection can include notes cheaply. | P3-R11 |
| R29 | **CSP posture drift** — someone adds a CDN `<script>` to the board template "for a nicer chart". | Medium | Low | The board template ships zero external subresources; the strict header set is asserted and the body is scanned for external URLs. | P3-R3 |
| R30 | **Passcode lockout UX** — 10 strikes locks the board/report; the locked notice carries no form and no remaining-attempts hint. | Low | Medium | Documented in both tool docstrings; `refresh=True` is the single documented recovery (mirrors `view_link`). | P3-R7 |
| R31 | **Operator has a live-views root literally named `board`** ⇒ boot refusal after upgrade. | Medium | Low | Documented migration note in the phase-3 changelog + the live-view runbook; the validator's message names the offending root and suggests a rename (the `public` precedent). | P3-R9 |
| R32 | **Served-page staleness for `view_write`** — `private, max-age=60` ⇒ a reader may see the old page for up to 60 s after an overwrite. | Low | Medium | Accepted (existing lane behaviour). The **board** is `no-store` and always fresh, and an overwrite is never silent (it requires an explicit `overwrite=True`). | P3-R10 |

---

## 7. Success criteria roll-up

Each phase carries its own measurable criteria; this is the commission-level roll-up. Full criteria:
[phase1-plan.md §14](phase1-plan.md), [phase2-plan.md](phase2-plan.md), [phase3-plan.md §9](phase3-plan.md).

**Cross-instance continuity (the core promise)**
1. `board_create` → `board_update` → `board_list` → `board_get` work from an Ari instance **and** from a Leader
   instance, and both observe the **same** items (verified by a cross-instance test with two different
   `current_instance_id` values) — the global namespace proven.
2. Items, notes and tags survive a daemon restart (PG via `create_all` at manager boot; SQLite via the `.sql`
   migration through the runner).
3. Notes are **provably** append-only: no update/delete code path exists, the count grows monotonically, the
   static source scan is green.

**Self-wake ergonomics**
4. The 30-minute floor holds: 29 min ⇒ `ERROR` (carrying the bash-sleep hint) and **no engine call**;
   30 min ⇒ accepted; 30 min + 1 s ⇒ accepted.
5. The armed instant is exactly the floored instant — wrapper first-fire vs the engine's `next_run_at_utc` echo
   is ≤1 s drift for `once`/`daily`/`weekly`/`cron`.
6. A fired one-shot leaves a `stopped` row (not cancelled) and the label is not silently reusable — a later
   same-label create takes the `-2` path.
7. The reminder is actionable by a **fresh instance**: the composed message carries the prefix, the label, the
   scheduled instant (local **and** UTC), the board id + the `board_get` instruction, the fresh-instance note and
   the agent's action text.

**Rich-content sharing**
8. `view_write` publishes a page end-to-end: ari calls it, a fresh client opens the returned public URL, enters
   the passcode, and the served page's sha256 matches the tool's input **byte-for-byte**.
9. The board page is **always fresh** from the DB: two GETs around a DB write render different content.
10. The board page is **inert**: zero external subresources, `script-src 'none'` + `default-src 'none'` present.
11. User content cannot inject markup: a title/note carrying `<script>` / `"><img onerror=…>` appears only in
    escaped form.
12. The board gate leaks nothing: with no minted row the response is the **uniform 404** envelope, identical to
    any other miss.

**Discipline**
13. The 4 board tools + `self_alarm` + `view_write` + `board_link` are visible **only** to agents whose allow
    list grants the category (allow-list gating proven against a **non-empty** allow list; an empty allow list
    means "everything allowed").
14. `view_write` is ari-only; `self_alarm` is ari-only (exactly one `meta.json` grants each category); `board`
    is ari + leader; `board-link` is ari + leader.
15. Zero seal violations: the seal test is green and `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md`
    are byte-identical; the stale-pin count stays at the measured baseline (7).
16. Zero new failures in the regression run; the frozen-tool-name drift pin stays green; **no** release is
    staged or promoted by any phase.

---

## 8. What was deliberately NOT designed (v2 + deferred)

These are recorded as **deferred with reasons**, not as gaps. Each is listed in its owning phase plan.

| Deferred item | Where | Why deferred |
|---|---|---|
| **Job-lifecycle auto-update** — when a linked job reaches `DONE`/`DEAD`, auto-append a system note and flip the item's status. | [phase1-plan.md §9](phase1-plan.md) | (a) There is no lifecycle-event seam for an arbitrary table — the job-completion path lives beside the report-delivery sweep, and touching it risks the report-delivery path. (b) Auto-writing notes changes the **authorship model** of an append-only audit trail (system author vs instance author) and needs a documented convention before any row is written. (c) It requires the job-completion path to reach `BoardService`. Deferring until phase-1 traffic exists keeps the phase-1 diff small and the audit trail's provenance unambiguous. |
| **Board `due_at` → alarm creation-time auto-arm** — the board service arms a `self_alarm` when a due date is set. | [phase2-plan.md D8](phase2-plan.md) | Five concrete blockers: `create_schedule` needs a caller-agent concept the board tools do not have (the board is granted to ari **and** leader, the alarm to ari only); label synthesis + the burned-label rule would leak into the board service; a `due_at` change ⇒ reschedule, which cannot change the recurrence family and has a documented <1 s missed-fire window; `due_at` removal ⇒ cancel ⇒ terminal + label burn + hidden history; and phase 1's board API is not frozen, so coupling to it creates a merge-order dependency. ⇒ **v1 uses an agent-prompt convention**; the board's `due_at` stays the source of truth for the deadline, the alarm is the reminder mechanism. |
| **Chat bridge for alarm replies** — bridging a fired alarm's summary back to the originating chat surface. | [phase2-plan.md OQ-1](phase2-plan.md) | A **product** call, not a code call: the scheduler source is one-way (`SchedulerAdapter.send` only logs) and the receiving instance is scheduler-owned with **no chat context** — there is no "originating chat surface" to bridge to without new ownership plumbing. Left as an open architect question. |
| **A REST twin for the board in phase 1** | [phase1-plan.md Q6](phase1-plan.md) | The bound phase-1 shape is tools-only; phase 3 adds the live view, which may call the service directly. |
| **A reports-INDEX page** (a listing of published pages) | [phase3-plan.md](phase3-plan.md) | No consumer in this phase; it would also need a title sidecar. |
| **Server-side markdown rendering of `body_md`** | [phase3-plan.md D15](phase3-plan.md) | No markdown library exists daemon-side; rendering agent-authored markdown without a sanitiser would be an XSS hole. `body_md` is escaped plain text in a `<pre>`. |
| **Pagination cursor on `board_list`** | [phase1-plan.md Q3](phase1-plan.md) | `limit` + truncation metadata is accepted in v1; add `offset`/`cursor` when the board exceeds a few hundred items. |
| **Clearing `due_at` / `project_id` / `priority`**, and **soft-delete of items** | [phase1-plan.md Q2, Q4](phase1-plan.md) | The bound `board_update` shape has no clear-flag; a soft delete (never a hard one — the notes audit trail must survive) is a v1.1 amendment on demand. |
| **Interval recurrence** (`every 1h`) | [phase2-plan.md OQ-3](phase2-plan.md) | `create_schedule` exposes no interval variant; it is rejected with a hint (`cron */60 * * * *`). A real engine change is out of phase-2 scope. |
| **The `agents/ari/{rule,workflow}.md` conventions edit + its adjudicated seal re-pin** | [phase3-plan.md D19](phase3-plan.md) | That is a **separate phase-5** commission; it owns the re-pin, and it must be sequenced so the re-pin covers the post-phase-3 tree. |

---

## 9. Landing note

**Normal stage flow — no special ceremony for this commission:**

```
worktree (feature/ari-tools)  →  merge --no-ff into latest  →  push
                              →  bump_version.py (exact 4-file set)
                              →  stage.sh  (staged release dir + manifest)
                              →  staged verification (independent tester/reviewer pass)
```

- **NO live promote.** The live promote is the **user's 3-factor nonce ceremony** and this commission does not
  perform it, does not stage a `promote`, and does not touch the live daemon. Every phase plan says the same
  thing independently ("promote remains the user's nonce ceremony"); that constraint is binding on the whole
  commission, not just on the last phase.
- **Migration posture:** exactly one new migration file (`20261019_190000_create_board_tables.sql`), additive
  only, **no** down-migration. The three-phase delta vs the base is **one** migration file ⇒ the rollback-safety
  heuristic is satisfied by inspection, and the staged-release manifest records the schema generation.
- **Seal posture:** no phase touches a seal-pinned file ⇒ the prompt-artifact seal is **not** re-pinned by this
  commission. If the separate phase-5 conventions edit is commissioned later, it owns its own re-pin.
- **Proxy / infra posture:** **zero** proxy change. Both new public surfaces ride `/views/public/**`, which is
  already proxy-exempt in both deployment states.
- **Merge-order note:** all three phases land on the **same branch** and merge to `latest` **once**, in build
  order 1 → 2 → 3. `agents/ari/meta.json` is appended to three times — once per phase — always at the END of
  the allow array.

---

## 10. Document map

| Document | Role |
|---|---|
| [plan-overview.md](plan-overview.md) | **this file** — synthesis, coupling map, consolidated risks, roll-up |
| [decisions.md](decisions.md) | the decision ledger — binding user decisions, compiled design decisions, deviations/tensions/open items |
| [phase1-plan.md](phase1-plan.md) | canonical detail for the workbook (17 numbered decisions, 6 open questions) |
| [phase2-plan.md](phase2-plan.md) | canonical detail for `self_alarm` (D1–D13, 6 open questions) |
| [phase3-plan.md](phase3-plan.md) | canonical detail for the live views (20 numbered decisions, 6 open questions) |
| [research-db-agents.md](research-db-agents.md) | research — migration/repository/tag precedents, ari/leader agent surfaces, seal exposure |
| [research-scheduler-tools.md](research-scheduler-tools.md) | research — the `task_schedule` engine, tool-registration pattern, allow-list syntax |
| [research-liveviews.md](research-liveviews.md) | research — the live-views subsystem, passcode mechanism, storage substrate, proxy invariant |
