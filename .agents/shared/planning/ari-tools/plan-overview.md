# Plan Overview: `ari-tools` — Ari's self-evolution toolbox (board + self-alarm + live views)

Date: 2026-10-19
Author: worker via plan-creation (commission `ari-tools`, final planning wave, dispatched by planner)
Status: **Architect-adjudicated** (fold applied, planning head `2b297db7d`) — Ready for Review
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
| 1 | Workbook: 3 tables + `BoardService` + 4 tools in a new **privileged** `"board"` category | **8** — `daemon/models/board.py`, `daemon/services/board_service.py`, `daemon/tools/board_tools.py`, `daemon/migrations/versions/20261020_060000_create_board_tables.sql`, 4 test files | `daemon/manager.py` (late model import before `create_all`; service construction; `board_service` property) · `daemon/tools/_tool_registry.py` (`DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES` + `PRIVILEGED_TOOL_CATEGORIES`) · `daemon/tools/instance.py` (factory + `tools.extend`) · **the 5 SAME-PR privileged-set pin files** (entry #9 — the **8 → 9** pass) · `agents/ari/meta.json`, `agents/leader/meta.json` (allow-list) | [phase1-plan.md](phase1-plan.md) |
| 2 | `self_alarm`: pure service module + tool module | **5** — `daemon/services/self_alarm_service.py`, `daemon/tools/self_alarm.py`, 3 test files | `daemon/tools/_tool_registry.py` (3 entries + `PRIVILEGED_TOOL_CATEGORIES`) · `daemon/tools/instance.py` (factory + extend) · **the 5 SAME-PR privileged-set pin files** (entry #10 — the **9 → 10** pass) · `agents/ari/meta.json` (`"self-alarm"`, ari only) · `agents/ari/tools_note.md` (new `## Self-Alarm` section) | [phase2-plan.md](phase2-plan.md) |
| 3 | `view_write` tool + live board view + `board_link` tool | **7** — `daemon/tools/view_write.py`, `daemon/tools/board_link.py`, `daemon/services/board_view_service.py`, `daemon/services/board_view_pages.py`, 3 test files | `daemon/api.py` (lifespan `reports`-root injection) · `daemon/services/live_views.py` (reserved name + 2 accessors) · `daemon/routers/live_views.py` (board GET/HEAD **before** the public catch-all) · `daemon/tools/_tool_registry.py` (incl. `PRIVILEGED_TOOL_CATEGORIES`) · `daemon/tools/instance.py` · **the 5 SAME-PR privileged-set pin files** (entries #11–12 — the **10 → 12** pass) · `agents/ari/meta.json`, `agents/leader/meta.json` · `config.yaml` (doc-only note) | [phase3-plan.md](phase3-plan.md) |
| | **Total** | **20 new files** (P1 8 · P2 5 · P3 7 — architect §7.4 confirms the count) | **15 shared edit files** (the corrected set — see the note below) | |

**Shared-edit-file count (CORRECTED — architect §7.4).** The old summary row said "3 shared edit files"
(`_tool_registry.py`, `instance.py`, `agents/ari/meta.json`); the per-phase rows above always listed more, and
the architect's file map is the authority. The true shared set is:

`daemon/manager.py` · `daemon/tools/_tool_registry.py` · `daemon/tools/instance.py` · `agents/ari/meta.json` ·
`agents/leader/meta.json` · **the 5 SAME-PR privileged-set pin files** · `daemon/api.py` (phase-3 lifespan) ·
`daemon/services/live_views.py` + `daemon/routers/live_views.py` (phase 3) · `config.yaml` (doc-only) ·
`agents/ari/tools_note.md` (phase 2's prompt surface — the architect notes the old summary row omitted it
entirely).

⇒ **15 shared edit files in v1** (14 from the architect's set + `tools_note.md`).

`daemon/loader.py` — the **optional** warm-scan edit — is the **16th**, and it is **SKIPPED in v1** (leader
ruling **R-loader**: the lifespan-injected `reports` root suffices for correctness; skipping keeps the
phase-3 diff minimal). It stays on the deferred list (§8) — not a defect, not a gap.

**Migration count: exactly 1** (phase 1's `daemon/migrations/versions/20261020_060000_create_board_tables.sql`
— distinct **day** prefix, leader ruling **R-prefix**). Phases 2 and 3 add **no** table and
**no** column — phase 3's `live_view_passcodes` table already exists
(`daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql`).

---

## 4. Build-order rationale (BINDING: 1 → 2 → 3)

The user bound the build order **1 → 2 → 3**. The technical justification for that order, and the reason the
order is *not* strictly serial, both matter:

- **Phase 1 must land first** because it creates the *schema* everything else reads. Phase 2's alarm reads
  `due_at` (tz-aware, indexed) and `status`; phase 3's board page reads the item projection and the notes
  timeline. `SQLModel.metadata.create_all` does **not** ALTER existing tables
  (`daemon/manager.py:751` — the `SQLModel.metadata.create_all(self._engine)` call; the "does not ALTER"
  behaviour is documented in the `_ensure_postgres_columns` docstring at `:6596-6604`), so a column added after
  the first boot needs *both* an `_ensure_postgres_columns` entry *and* a `.sql` ALTER. Phase 1 pins the four hooks phases 2 and 3 need
  (`due_at`, `status`, `tags`, `linked_job_ids`) precisely so no later ADD is required — that is only possible
  if phase 1 is designed knowing what phases 2 and 3 read.
- **Phase 2 consumes only ONE thing from phase 1: the pinned `wi-<uuid8>` id format.** It validates the id by
  regex (`^wi-[0-9a-f]{8}$`) and embeds it verbatim in the message — it never imports a phase-1 module and
  never calls the board service. That is why phase 2's board glue is a **prompt convention** rather than a
  service coupling (see [decisions.md](decisions.md) §C.3).
- **Phase 3 couples to phase 1 through exactly ONE seam**: a shape-tolerant read adapter over
  `manager.board_service`, in a single phase-3-owned file (`daemon/services/board_view_service.py`). A phase-1
  rename or move costs one import line. **The interface behind that seam is FROZEN** — `list_items(status=…,
  project_id=…, tag=…, limit=…, with_notes=False)` and `get_item(item_id=…)`, names **and** keyword names, stated
  verbatim in `board_service.py`'s module docstring at the end of Phase 1 (architect §3.4 / phase-1 A-17). That
  makes the P1↔P3 link a **schedule** coupling as well: Phase 3 must not start before Phase 1 freezes it, and it
  must reconcile against the landed phase-1 code before merging (R9).
- **Parallelizability, stated honestly:** once phase 1 lands, phases 2 and 3 are **technically parallelizable** —
  they share no file except the append-only tail of `agents/ari/meta.json`'s allow array. The build order stays
  1 → 2 → 3 anyway, because (a) the user bound it, and (b) a single sequential order keeps one worktree, one
  branch, one merge chain and one staged release for the whole commission. Parallel execution would trade a
  binding constraint for merge-order risk with no delivery benefit.
- **Merge-ordering note:** phases 2 and 3 both append to `agents/ari/meta.json`'s allow array (phase 3 also
  edits `agents/leader/meta.json`; phase 1 edits both). All appends go at the **END** of the array
  (phase-1 D15 discipline) so the three phases produce non-overlapping hunks.
- **The privileged set is a CROSS-PHASE COUPLING — THREE pin passes, not one (A-46 / architect §7.1).** The
  commission adds **four** privileged categories across **three** phases, and `PRIVILEGED_TOOL_CATEGORIES` is
  pinned by **five exact-set assertions** in five test files (no `len()` assertion anywhere ⇒ one inserted
  literal line per file). The trajectory is **8 → 9** (P1: `board`) → **10** (P2: `self-alarm`) → **12**
  (P3: `view-write` + `board-link`): P1 makes the **8 → 9** pass, P2 the **9 → 10** pass, P3 the **10 → 12**
  pass — **three** passes over the same five files. Each pass ships **in the same commit** as its category
  entry ⇒ each phase-commit is self-consistent and there is **no red window**; but **no phase may claim the
  pins were "already paid"** — the cost is paid again in every phase that adds a category. A phase-3 merge
  must **verify that #9 (P1) and #10 (P2) landed**.

---

## 5. Cross-phase coupling map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **Phase 1** | — | **loose** — shared *id format* only (`wi-<uuid8>`); no shared module | **tight** — one read adapter over `manager.board_service`; the **frozen** `list_items` / `get_item` interface (names + keyword names, incl. `with_notes`); the pinned item + notes projection |
| **Phase 2** | loose | — | **loose** — shared *semantic* contract only (the board item id and the note shape a self-alarm links to); no code coupling |
| **Phase 3** | tight (frozen interface) | loose | — |

### The four shared surfaces (and how to keep them clean)

1. **The single-import seam (P1 ↔ P3) — and the ONE frozen interface.** `daemon/services/board_view_service.py`
   is the ONLY file that knows phase 1's surface. It is shape-tolerant (`getattr(obj, "x", None)` **or**
   `obj["x"]` — the `_row_locked` / `_row_generation` precedent) and degrades to an explicit "board backend
   unavailable" notice, **never a 500**. Phase 3 also adds **no row** to phase 1's `CATEGORY_MODULES["board"]` and
   **no file** to phase 1's `daemon/tools/board_tools.py` ⇒ zero textual merge conflict there.
   **The interface itself is frozen** (architect §3.4 / phase-1 A-17): `board_service.list_items(status=…,
   project_id=…, tag=…, limit=…, with_notes=False)` and `get_item(item_id=…)` — **names AND keyword names** — are
   stated verbatim in `board_service.py`'s module docstring at the end of Phase 1, and changing them later is a
   breaking change to a frozen interface, not a refactor. Phase 1 **batches** the notes **and** tags behind
   `with_notes=True` (≈15–20 lines, ≤3 queries per render) while the **tool's item shape stays unchanged** (no
   `notes[]` in `board_list`) ⇒ phase 3's adapter must tolerate **both** shapes and keeps its per-row
   "notes unavailable" fallback.
   **Coupling type:** a **schedule** coupling, not a code one — Phase 3 must not start before Phase 1 freezes the
   interface, and must reconcile against the landed phase-1 code before merging (R9).
2. **The shared migration-prefix namespace (P1 only, but against the world).** The runner enforces one ledger
   row per version prefix (`schema_migrations.version TEXT PRIMARY KEY`, `daemon/migrations/runner.py:211-220`).
   A duplicate prefix breaks a **fresh SQLite boot** with `UNIQUE constraint failed: schema_migrations.version`
   (the runner does **not** catch `IntegrityError`). The repo already carries pre-existing duplicates
   (`20260628_000002`, `20261018_000001`) — do not copy the defect.
   Phase 1's rule (leader ruling **R-prefix**): the file is
   `daemon/migrations/versions/20261020_060000_create_board_tables.sql` — a distinct **DAY** prefix (a same-hour
   concurrent commission would collide). The developer **re-verifies PK uniqueness at write time**
   (`ls daemon/migrations/versions/ | grep '^20261020_'` must return **exactly this one file**) **and at merge
   time** — the prefix must still be unique against whatever landed in between.
3. **The shared allow-list edits (all three phases → `agents/ari/meta.json`).** Phase 1 adds `"board"`,
   phase 2 adds `"self-alarm"`, phase 3 adds `"view-write"` + `"board-link"`; phase 1 and phase 3 also touch
   `agents/leader/meta.json` (`"board"`, `"board-link"`). **Merge-ordering note:** all edits append at the END
   of the array (phase-1 D15), and each phase's allow-list pin test is per-phase — so a phase-3 merge must not
   assume phase 1's or phase 2's entries exist, and vice versa. `agents/ari/meta.json` and
   `agents/leader/meta.json` are **not** seal-pinned (the pinned set is `.md` artefacts only), which is why all
   three phases can touch them freely.
4. **The privileged-set pin files (all three phases → the same five files).**
   `PRIVILEGED_TOOL_CATEGORIES` is asserted by **exact-set equality** in five test files
   (`tests/unit/tools/test_upgrade_registration.py`, `tests/unit/tools/test_attestation_registration.py`,
   `tests/integration/test_maintenancer_spawn_resolves_tools.py`,
   `tests/integration/test_service_tool_flag_off_byte_identical.py`,
   `tests/unit/tools/test_secrets_registration.py`) and there is **no `len()` assertion anywhere**. The
   commission adds **four** categories across **three** phases — **8 → 9** (P1 `board`) → **10**
   (P2 `self-alarm`) → **12** (P3 `view-write` + `board-link`) ⇒ **THREE pin passes over the same five
   files**. Each pass is one inserted literal line per file and ships **in the same commit** as its category
   entry ⇒ no red window. A phase-3 merge must not assume phase 1's `#9` or phase 2's `#10` landed;
   **verify at merge that both did.** (The five pins also carry stale "exactly eight entries" prose — the
   docstrings are corrected in the same commit as the pin change; see decisions.md §C7 F4.)

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
| R1 | **Duplicate migration prefix ⇒ fresh-SQLite boot failure.** The runner enforces one ledger row per version prefix; a collision yields `UNIQUE constraint failed: schema_migrations.version`. The runner does **not** catch `IntegrityError`. The repo already carries pre-existing duplicates (`20260628_000002`, `20261018_000001`). | **High** | Low | Verify prefix uniqueness **before** writing the file (`ls daemon/migrations/versions/ \| grep '^20261020_'` → **exactly** the one board file) **and re-verify at merge time** (leader ruling **R-prefix** — a distinct **day** prefix; a same-hour concurrent commission would collide). Never copy the pre-existing defect. | P1-R8 |
| R2 | **Privileged vs non-privileged category for the board. RESOLVED — the category is PRIVILEGED, so there is no default-grant.** (Was: "phase 1's `"board"` is non-privileged ⇒ default-granted to every empty-allow agent, diluting the 'ari + leader' pre-ruling".) | — | — | **CLOSED by the architect's ruling** (leader ruling **R-C1**, architect §1.3): `"board"` is **entry #9** of `PRIVILEGED_TOOL_CATEGORIES` from **Phase 1's FIRST commit**, with the five SAME-PR pins in the **same** commit. Kept as a **decision record**, not a live risk. The residual is the *forgetting* risk — mitigated by the five exact-set pins and the empty-allow default-deny assertion. Rationale + the four evidence corrections: [decisions.md](decisions.md) §C.1 | P1-R2/Q1, P3-D3/OQ-2, P3-R6 |
| R3 | **Lateness-cap / missed-alarm semantics.** Default `one_shot_max_lateness_seconds=None` ⇒ a ≥30-min alarm whose instant passed during downtime fires **very late at next boot**. With the cap set, a later boot yields a `skipped` history row, **no dispatch**, and the schedule **stays armed** — and the capped path is a **5-second daemon-wide re-evaluation loop with NO self-heal and NO user-visible signal** (`daemon/sources/adapters/scheduler.py:559`; `SCHEDULER_ERROR_RETRY_S = 5.0` — the loop re-evaluates the same past instant, writes only a `skipped` row, and the schedule still reads `running`), i.e. a late-but-real delivery becomes a **silent, permanent, non-self-healing loss**. | **High** | Medium | **RULING (leader ruling R-lateness, architect §4.1): default `None`, NO cap in v1.** The cap is **daemon-global**, so the wrapper **cannot override** it ⇒ the wrapper **MUST read and echo `lateness_cap_seconds`** (`<int\|null>`) so the agent knows whether a missed alarm would be delivered late or skipped forever; the tool's `_full_doc_` documents **both** behaviours + the skip state's signature. The late path is self-announcing — the composed message states the **scheduled** instant, so a late delivery is detectable. Phase 2 changes **no** engine semantics. | P2-Risk 1, P2-OQ-4, R9 |
| R4 | **Live-views mount order.** The board route declared *below* the public catch-all ⇒ the catch-all resolves `board` as a file and the board **silently 404s** (Starlette is first-match-wins). | **High** | Low | The board GET/HEAD is declared **immediately before** the catch-all with a loud comment restating the rule; a route-order assertion pins the resolution. | P3-R1 |
| R5 | **Config-roots-replace footgun.** An operator `live_views.roots:` block **replaces** the seed dict (`config.py:2903-2914`) ⇒ a config-declared `reports` root would silently vanish. | **High** | Low | The `reports` root is **lifespan-injected**, never operator-declared; injection is idempotent and never overrides an operator-declared entry. `config.yaml` gets a **doc-only** note. | P3-R2, P3-D1 |
| R6 | **32 MiB served-page cap.** A page larger than the served cap can never be served; a page that cannot be served must never be written. | Low | Low | The write path imports the **same** `MAX_SERVED_BYTES` constant the serve path uses (one source of truth) and refuses oversize input with a typed error naming the limit and the byte count. | P3-R8, P3-D6 |
| R7 | **Seal-pinning re-pin obligation.** Any edit to `agents/ari/{rule,workflow}.md` or `agents/leader/rule.md` requires an adjudicated seal re-pin. `agents/ari/workflow.md` is **already drifting** — **7 stale pins measured at plan time**, which are **NOT ours to fix** and must not be silently absorbed. A new edit would add an 8th. | **Medium** | Medium | **No phase in this commission edits any seal-pinned file.** Phase 2's prompt surface is `agents/ari/tools_note.md` (not pinned); phase 3 touches only `meta.json` (the seal covers `.md` artefacts only — `meta.json` appears zero times). The stale-pin count must stay at the measured baseline (7); the conventions edit + its re-pin is a **separate phase-5 commission**. | P2-scope note, P3-D19 |
| R8 | **Proxy `/views/public/**` exemption invariant.** `/views/public/**` must stay proxy-exempt in **both** deployment states (State A merge+apply / State B zero-operator), with the canonical gate daemon-side. Any new surface outside `/views/public/**` would break the contract. | **High** | Low | Both new surfaces live under `/views/public/**` already ⇒ **zero proxy change**; no gate-free route is added; Host preservation is untouched (the same-host CSRF check depends on it). The existing public-prefix exemption integration test stays green. | P3-D18, P3-§6 |
| R9 | **Cross-phase seam drift** — phase 1 renames/moves the service or changes the item shape ⇒ phase 3 breaks. | **High** | Medium | Exactly one adapter file is the coupling point; it is shape-tolerant and degrades to an explicit "unavailable" notice, never a 500. **The phase-3 builder MUST reconcile against the landed phase-1 code before merging.** | P3-R4 |
| R10 | **Schema freeze.** `create_all` does not ALTER existing tables; after the first board boot, any phase-2/3 column ADD needs *both* an `_ensure_postgres_columns` entry *and* a `.sql` ALTER. | Medium | Medium (certain, if a column is added) | Phase 1 pins the four hooks phases 2 and 3 need, so no later ADD is required. | P1-R4 |
| R11 | **Burned label / recurrence-family mismatch.** A cancelled schedule label is burned **forever** (no hard delete for scheduler rows); `update` cannot change the recurrence family. | Medium | Medium | Label lookup first ⇒ **reuse in place** for `{running, stopped}`; a **burned** label auto-retries once as `"<label>-2"` (then `-3`, cap 5) and the **effective** label is echoed; a recurrence-family change is a typed error. A hard error would deadlock the agent. *(R11 and R12 now share one enforcement model — the ari-only rule is three layers, not two — see R12.)* | P2-Risk 2, P2-D4 |
| R12 | **`self-alarm` leaks to another agent.** An agent with an empty allow **and** deny list gets everything; an allow-list is a *visibility* boundary, not a security boundary. | **High (policy)** | Low | The ari-only rule is enforced **THREE** times, not twice: the **privileged category** (`self-alarm` = entry #10, from Phase 2's **first** commit) **and** the allow-list **and** a runtime `agent_id != "ari"` gate in the tool body (the `blueprint` writer-gate precedent). The registration test proves the property directly: `resolve_tool_filter` yields **no** `self_alarm` for a non-ari agent with an **EMPTY** allow list. See **R35** for the escalation chain this closes. | P2-Risk 6, P2-D10, R35 |
| R13 | **The alarm lands in the wrong place.** The human expects a chat ping; the scheduler source is **one-way** (`SchedulerAdapter.send` only logs) ⇒ the reply never reaches the chat surface. | Medium | Medium | Stated plainly in the tool doc + the tools_note section: the alarm lands in the agent's own workspace and can act autonomously. Chat bridging is an **open product call** (OQ-1), not a code call. | P2-Risk 3, P2-OQ-1 |
| R14 | **Merge collision on `agents/ari/meta.json`** — all three phases append to the same allow array. | Low | Medium | Every append goes at the **END** of the array (phase-1 D15 discipline); each phase's allow-list pin test is per-phase. | P3-R12, P1-D15 |
| R15 | **Privileged-category pin blast** — adding privileged categories trips the SAME-PR five exact-set pins. | Medium | Low | **CORRECTED (architect §7.2): four categories across three phases ⇒ THREE pin passes, not one.** The trajectory is **8 → 9** (P1 `board`) → **10** (P2 `self-alarm`) → **12** (P3 `view-write` + `board-link`); each pass is one inserted literal line per file (there is **no `len()` assertion** anywhere) and each ships **in the same commit** as its category entry ⇒ no red window, but the cost is **re-paid in every phase** that adds a category. | P3-R6, R35 |
| R16 | **`linked_job_ids` read-modify-write** is safe only under the service write lock; a future multi-process writer needs row-level locking. | Low | Low | A `threading.RLock` held across the whole write transaction serialises all board writes; the daemon is single-process today. Flagged for any future multi-process deployment. | P1-R5 |
| R17 | **No per-item ACL** — any agent holding the category can write the global board. | Low | Medium | Consistent with the repo trust model ("the only access boundary is the per-agent `tools.allow` category gate"); the board carries no secrets and grants no authority. Revisit only if the board ever holds sensitive content. | P1-R6 |
| R18 | **`wi-<uuid8>` is 32 bits of entropy.** Birthday collision: P≈1 at ~77k items, P≈0.0001 at 1k. | Low | Low | The id is the **PRIMARY KEY** ⇒ a collision raises `IntegrityError` and the create **fails loudly** (no silent overwrite, no data loss). **CORRECTED (architect §2.2 S-2): the agent never supplies the id and never retries** — the **service** catches `IntegrityError`, confirms the **id PK** (not a bare `except`), and retries **once**; a second failure ⇒ a generic `ERROR: board operation failed`. The agent never sees a collision. The id *shape* is fixed by the binding; flagged, not changed. | P1-R1, R1 |
| R19 | **FK behaviour differs across drivers. CORRECTED (architect §2.5): the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on every connection** (`daemon/repositories/factory.py:175`; the pragma block is `:170-177`; the engine is built through that factory, `daemon/manager.py:656-660`) ⇒ FK behaviour **holds on BOTH drivers at runtime**. It is inert **only** on a bare `create_engine("sqlite://")` — so any unit-test FK assertion must enable the pragma itself or be marked PG-only. | Low | Medium | Accepted: the item survives either way and the value is informational — no code path depends on the FK's SET NULL behaviour (the project filter is by value, not by join). The phase-1 test pins the pragma explicitly rather than relying on the driver default. | P1-R3 |
| R20 | **Unbounded board growth** — no retention policy in v1; `board_notes` grows monotonically. | Low | Medium | `board_list` is bounded by `limit`; archival is a v2 topic. Add a retention decision before the board exceeds a few thousand rows. | P1-R7 |
| R21 | **`todo` confusion** — agents already have a per-instance in-memory `todo` family; the board is durable and global. | Medium | Medium | The distinction is documented in the tool docstrings, `CATEGORY_DOC` and `agents/ari/tools_note.md`: todo = ephemeral per-turn planning, board = durable cross-instance user work. | P1-R9, P1-D17 |
| R22 | **Phase-1 schema under-specifies phases 2/3** — the alarm horizon and the live view need fields not bound in phase 1. | Medium | Medium | Phase 1 pins the four hooks phases 2/3 need (`due_at` indexed + tz-aware, `status` indexed, `tags` junction, `linked_job_ids` JSONB). Any further need is an explicit additive change in a later phase, not a phase-1 rework. | P1-R10 |
| R23 | **Wrapper timezone drift** ⇒ the floor is computed against the wrong instant. | Medium | Low | The wrapper resolves tz via the engine's **public** primitives only, and a parity test pins it against `scheduling_service._resolve_schedule_timezone` across the {explicit tz, user setting, env default} matrix. | P2-Risk 5 |
| R24 | **Idempotency collapse** — re-arming at the identical instant within 24 h of a still non-terminal prior job collapses into that job (key `scheduler:{source_id}:{run_at}`) ⇒ no second delivery. | Low | Low | The wrapper **rejects** an unchanged instant on a `stopped` label; a terminal prior job (the normal case after a fire) does not block a fresh submit. | P2-Risk 8 |
| R25 | **Update-path rebuild gap (<1 s) loses a re-armed fire.** | Low | Low | Every armed instant is ≥30 min out ⇒ the window is irrelevant. | P2-Risk 9 |
| R26 | **New category breaks the frozen-binary tool universe** (the tool vanishes in prod). | High | Low | The new tool name is added to **both** `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES`; the frozen-discovery drift pin stays green. | P2-Risk 10, P1-D11 |
| R27 | **Prompt-surface drift** — the `tools_note.md` section is a second home for the grammar. | Low | Medium | One canonical home (the tools_note section); the tool doc references it; the forbidden-token + cross-ref tests enforce the closure. | P2-Risk 11 |
| R28 | **Notes N+1 on the board render — RESOLVED by batched hydration in Phase 1** (`list_items(..., with_notes=True)` + batched notes **and** tags loaders, ≈15–20 lines): the render is now **≤3 queries** per board instead of 1+50 = 51 (worst case 201 at the tool's 200 ceiling). The **tool's item shape is unchanged** (no `notes[]` in `board_list`), so the payload and truncation behaviour are untouched. | Low | Low | The phase-3 adapter keeps a **tolerant fallback** (it works against **both** shapes) and degrades per row ("notes unavailable" instead of a 500) ⇒ the N+1 survives only as the fallback path's cost. `list_items` / `get_item` are **frozen** (names + keyword names) as the binding phase-3 interface. | P3-R11, P3-OQ-1 |
| R29 | **CSP posture drift** — someone adds a CDN `<script>` to the board template "for a nicer chart". | Medium | Low | The board template ships zero external subresources; the strict header set is asserted and the body is scanned for external URLs. | P3-R3 |
| R30 | **Passcode lockout UX** — 10 strikes locks the board/report; the locked notice carries no form and no remaining-attempts hint. | Low | Medium | Documented in both tool docstrings; `refresh=True` is the single documented recovery (mirrors `view_link`). | P3-R7 |
| R31 | **Operator has a live-views root literally named `board`** ⇒ boot refusal after upgrade. | Medium | Low | Documented migration note in the phase-3 changelog + the live-view runbook; the validator's message names the offending root and suggests a rename (the `public` precedent). | P3-R9 |
| R32 | **Served-page staleness for `view_write`** — `private, max-age=60` ⇒ a reader may see the old page for up to 60 s after an overwrite. | Low | Medium | Accepted (existing lane behaviour). The **board** is `no-store` and always fresh, and an overwrite is never silent (it requires an explicit `overwrite=True`). | P3-R10 |
| **R33** | **Reusing a `stopped` label without `paused=False` silently DISARMS the alarm** — `update_schedule` preserves the previous `enabled` flag when `paused` is unset, a fired row is `enabled=False`, the rebuild is skipped, and the row stays `status="stopped"` while the boot filter skips it ⇒ a re-armed alarm that never fires. | 🔴 **High** | Low | Pass `paused=False` on every reuse; **assert** the response (`paused is false`, `status != "stopped"`, `next_run_at_utc` ±1 s); if the assertion fails, do **not** report success — fall back to a fresh label. | P2-Risk 13, P2-D4 |
| **R34** | **A message-only re-arm of a fired alarm is swallowed** — the job key is `scheduler:{source_id}:{run_at}` and a matching **non-terminal** job within 24 h collapses into the prior job ⇒ silently never delivered. | 🔴 **High** | Low | **Refuse** a re-arm that is not strictly later; state it in the tool doc ("a fired alarm must be re-armed with a new, later time"). | P2-Risk 14, P2-D4 |
| **R35** | **`self-alarm` non-privileged ⇒ `watcher` arms alarms ⇒ a fresh Ari instance executes attacker-influenced text verbatim** — a privilege-escalation chain, not a visibility leak. | 🔴 **High** | Low | **Privileged category** (entry #10) + the runtime ari gate + the five exact-set pins + the empty-allow registration test. | P2-Risk 15, P2-D10, R12 |
| **R36** | **The injected `reports` path can be RELATIVE ⇒ `view_write` is silently dead** — `data_dir` defaults to `./data`, and the resolver refuses a non-absolute root ⇒ every published page 404s behind a misleading "reports storage is not configured" error. | 🔴 **High** | Medium | `path=str((Path(_data_dir) / "reports").resolve())` + a relative-`ENSEMBLE_DATA_DIR` test. | P3-R36, P3-T-A10 |
| **R37** | **`str.format` on the CSS-bearing board template ⇒ 500 on every board GET** — CSS contains literal `{`/`}`, so the first brace raises. | 🔴 **High** | Medium | Plain string concatenation (the `_shell` precedent) or `string.Template`; **no** Jinja. | P3-R37, P3-§4.3 |
| **R38** | **`Cache-Control` merge-order trap ⇒ `no-store` lost** — merging the hardening headers with the page headers lets `private, max-age=60` win. | 🟡 **Medium** | Medium | Build the headers from `page_headers(csp_nonce)` **alone**; pin `cache-control` EXACTLY `no-store`. | P3-R38 |
| **R39** | **Render output has no byte cap** — `MAX_SERVED_BYTES` applies only to files served from disk; a server-rendered body, `body_md` and note text are unbounded TEXT. | 🟡 **Medium** | Medium | A **2 MiB** rendered-body ceiling + per-field caps (notes per item ≤ 20, note text ≤ 4 KiB, `body_md` ≤ 64 KiB) enforced **during assembly**, truncating with a **visible notice** — never a 500, never a silent cut. | P3-R39 |
| **R40** | **`board_link` hands out a URL when live-views is disabled** — the board GET is then a permanent 404. | 🟡 **Medium** | Low | Return the `view_link` **disabled envelope** instead of a dead URL. | P3-R40 |
| **R41** | **No `fsync` before `os.replace`** ⇒ a zero-length page survives a crash — and it is permanently servable, because there is no sidecar gate on `view_write` pages. | 🟡 **Medium** | Low | `flush()` + `os.fsync(fd)` **before** `os.replace`; unlink the temp file in a `finally`; same-directory temp (a `/tmp` temp ⇒ `EXDEV`); containment resolved on both sides; dirs `0o700`, file `0o600`. | P3-R41 |
| **R42** | **A future `has_root` check in `_public_post` silently breaks the board** — the reused POST verify path is the load-bearing accident that lets the reserved name `board` work. | 🟡 **Medium** | Low | A four-pin regression set (behavioural + a source scan) over the reused verify path. | P3-R42 |
| **R43** | **The default schedule list hides armed alarms** — the list is not status-filtered (running **and** stopped) and is capped at 100 ordered `created_at DESC`. | 🟡 **Medium** | High | The tool doc instructs `status="running"`; note that armed rows eventually hide behind stopped ones. | P2-Risk 16 |
| **R44** | **The label namespace is shared with non-scheduler sources** — `get_source_config_by_name` filters **only** on `name` (no `source_type` filter) ⇒ a wrapper label can collide with an MCP-server / Slack source name. | 🟡 **Medium** | Low | Namespace the wrapper labels (`self-alarm:` prefix) or verify `source_type`. | P2-Risk 17 |
| **R45** | **The create-response `status` is racing** — it moves `'stopped'` → `'starting'` → `'running'`. | 🟢 **Low** | High | Echo the **actual** status; treat `'starting'` as armed. | P2-Risk 18 |
| **R46** | **`tz_warning` erasure** — passing an explicit `timezone` short-circuits the engine's chain and suppresses the engine's UTC-fallback warning. | 🟡 **Medium** | Medium | Echo the **wrapper's own** warning. | P2-Risk 19 |
| **R47** | **Truncation is invisible** — >50 rendered rows and >500 filter values are cut with no signal. | 🟡 **Medium** | High | Render a **"showing N of M"** disclosure + a cap notice. | P3-R47 |

---

## 7. Success criteria roll-up

Each phase carries its own measurable criteria; this is the commission-level roll-up. Full criteria:
[phase1-plan.md §14](phase1-plan.md), [phase2-plan.md](phase2-plan.md), [phase3-plan.md §9](phase3-plan.md).

**Cross-instance continuity (the core promise)**
1. `board_create` → `board_update` → `board_list` → `board_get` work from an Ari instance **and** from a Leader
   instance, and both observe the **same** items (verified by a cross-instance test with two different
   `current_instance_id` values) — the global namespace proven.
2. Items, notes and tags survive a daemon restart. **Driver split (architect §2.2 S-6): the migration runner
   is SQLite-ONLY** (`daemon/migrations/runner.py:721-726`) and `create_all` runs **first on both** drivers
   (`daemon/manager.py:751`) ⇒ the **model is the source of truth on both**; the `.sql` is the persistent record
   plus the SQLite-only index fallback. The six indexes are declared in the model's `__table_args__`, so
   `create_all` creates them on **both** drivers from day one.
3. Notes are **provably** append-only: the **SQL-capture guard is the PRIMARY proof** — a
   `before_cursor_execute` listener asserts that the only statement touching `board_notes` across
   create/update cycles is `INSERT`/`SELECT` (the static source scan stays a cheap secondary), no
   update/delete code path exists, the count grows monotonically, and the child FKs are **NO ACTION**, so the
   database itself refuses a hard item delete.

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
    list grants the category. **All four categories are PRIVILEGED**, so an empty allow list means "everything
    **except** privileged" — the default-deny strip removes the privileged set
    (`daemon/tools/instance.py:6243-6271`). The gating is therefore proven against an **EMPTY** allow list (the
    population that would otherwise inherit them: `watcher` + tools-less custom agents), and the in-body
    `agent_id ∈ {"ari","leader"}` / `agent_id == "ari"` gates are proven too — a **literal tool-name** allow
    entry bypasses the category gate outright (`daemon/tools/instance.py:471-478`).
14. `view_write` is ari-only; `self_alarm` is ari-only (exactly one `meta.json` grants each category, **and**
    the privileged category is registered + pinned in the same commit); `board` is ari + leader; `board-link` is
    ari + leader. **The privileged-set trajectory is 8 → 9 → 10 → 12 across THREE pin passes** — the merge
    verifies that #9 (P1) and #10 (P2) landed before P3's 10 → 12 pass.
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
| **Chat bridge for alarm replies** — bridging a fired alarm's summary back to the originating chat surface. | [phase2-plan.md OQ-1](phase2-plan.md) | **ADJUDICATED (leader ruling R-chat-bridge / architect §4.2) — no new infrastructure in v1; the BOARD is the user-visible surface.** There is **no structural path** to a chat surface (the scheduler source is one-way; the receiving instance is scheduler-owned with **no chat context**), so a bridge would need new ownership plumbing. The v1 contract promises the board and **must NOT** promise a ping. If a public instance→job resolver ever appears, it is a **v2** note — it would not change the v1 ruling. |
| **A REST twin for the board in phase 1** | [phase1-plan.md Q6](phase1-plan.md) | The bound phase-1 shape is tools-only; phase 3 adds the live view, which may call the service directly. |
| **A reports-INDEX page** (a listing of published pages) | [phase3-plan.md](phase3-plan.md) | No consumer in this phase; it would also need a title sidecar. |
| **Server-side markdown rendering of `body_md`** | [phase3-plan.md D15](phase3-plan.md) | No markdown library exists daemon-side; rendering agent-authored markdown without a sanitiser would be an XSS hole. `body_md` is escaped plain text in a `<pre>`. |
| **Pagination cursor on `board_list`** | [phase1-plan.md Q3](phase1-plan.md) | `limit` + truncation metadata is accepted in v1; add `offset`/`cursor` when the board exceeds a few hundred items. |
| **Clearing `due_at` / `project_id` / `priority`**, and **soft-delete of items** | [phase1-plan.md Q2, Q4](phase1-plan.md) | **DEFERRED-to-v2** (ledger theme 4). The bound `board_update` shape has no clear-flag; `clear_fields` is a v1.1 amendment on demand. A soft delete is mandatory (never a hard one — the notes audit trail must survive), and the **child FKs (NO ACTION) now enforce it at the schema level**: the database refuses a hard item delete. |
| **Interval recurrence** (`every 1h`) | [phase2-plan.md OQ-3](phase2-plan.md) | **DEFERRED-to-v2** (ledger theme 8). `create_schedule` exposes no interval variant; it is rejected with a hint (`cron */60 * * * *`). A real engine change is a separate commission. |
| **The `agents/ari/{rule,workflow}.md` conventions edit + its adjudicated seal re-pin** | [phase3-plan.md D19](phase3-plan.md) | That is a **separate phase-5** commission; it owns the re-pin, and it must be sequenced so the re-pin covers the post-phase-3 tree. The phase-2 `agents/ari/tools_note.md` surface is **not** seal-pinned, so phase 2 carries no seal obligation. |
| **The optional `daemon/loader.py` warm-scan edit** — the cold-boot path can pin an empty category doc into the no-TTL prompt cache. | architect §3.3 | **DEFERRED (leader ruling R-loader).** The edit is **SKIPPED in v1**: the lifespan-injected `reports` root is sufficient for correctness, and skipping keeps the phase-3 diff minimal. It is the 16th shared edit file and is **not** part of the v1 file map. Not a defect, not a gap. |

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
- **Migration posture:** exactly one new migration file
  (`daemon/migrations/versions/20261020_060000_create_board_tables.sql` — distinct **day** prefix, leader ruling
  **R-prefix**), additive only: 3 new tables + their indexes, **no** `ALTER` of any existing table, no data
  change. The file carries **both** an `-- UP` and a `-- DOWN` section (the `-- DOWN` section is **mandatory** —
  `rollback_migration` hard-requires it, `daemon/migrations/runner.py:645-646`; it is **never auto-executed** by
  the pipeline). The staged-release manifest records the schema generation
  (`known_schema_gen = 20261020_060000_create_board_tables.sql`).
  **The delta vs the base is NON-EMPTY** (one new migration file) ⇒ the stage gate will demand an **explicit
  rollback-safety adjudication** with the reasoning recorded verbatim (the standing rule from the v0.25.4
  commission). The text is **pre-drafted below** so it is not discovered at stage time — it is expected,
  non-blocking, and the standing rule requires the *semantic* reasoning, not a reflex verdict.

  **Pre-drafted stage-time rollback-safety adjudication (finalise against the actual base at stage time):**
  *(1) The migration creates three new tables and their indexes. It does not `ALTER` any existing table, does not
  change any column type, and touches no existing row ⇒ the schema of every pre-existing table is byte-for-byte
  identical before and after. (2) The old binary is unaffected: it never imports the board models, so its
  `create_all` neither creates nor drops the board tables, and `create_all` never drops anything. (3) A rollback
  performs no down-migration, so nothing is torn down: the old binary operates against a schema that is a strict
  superset of the one it knows, which it ignores — and the board data is *retained*, not destroyed, so a
  re-promote restores it. (4) Therefore the delta is additive-only in the semantic sense, `ENSEMBLE_ROLLBACK_SAFE=1`
  is legitimate, and auto-rollback is safe and desirable — it must be able to fire on a failed gate.*
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
| [architecture-recommendation.md](architecture-recommendation.md) | the **architect's adjudication** of the 8-document design set (541 lines, planning head `2b297db7d`) — binding; §9 carries the amendment ledger (A-01…A-47) |


---

## Amendment log (post-architect 2b297db7d)

The architect's adjudication ([architecture-recommendation.md](architecture-recommendation.md), 541 lines) landed on
this branch at `2b297db7d`; the leader **ACCEPTED every verdict as a binding ruling**. The amendments below are
**folded into the body above** — this document reads as-if-designed-so; the log exists for traceability only.
P0 = correctness (must land before reviewer); P1 = clarity/consistency. The parallel fold applied the same
adjudication to the three phase plans (phase1 486 lines, phase2 543 lines, phase3 457 lines at fold time) and to
[decisions.md](decisions.md).

| ID | Pri | Folded into | What changed |
|----|-----|-------------|--------------|
| **A-43** | P0 | §6 (R2, R3, R11, R12, R15, R18, R19, R28) | **R2 → RESOLVED** (leader ruling **R-C1**: `"board"` is privileged from Phase 1's first commit — kept as a decision record, not a live risk). **R3** corrected — the capped path is a **5-second daemon-wide re-evaluation loop with no self-heal and no user-visible signal** (`scheduler.py:559`); default `None`, no cap in v1, and the wrapper MUST echo `lateness_cap_seconds`. **R18** corrected — the **service** retries once on an id collision; the agent never supplies the id and never retries. **R19** corrected — the daemon's SQLite engine sets `PRAGMA foreign_keys=ON` on every connection (`factory.py:175`) ⇒ FK behaviour holds on **both** drivers at runtime. **R15** corrected — four categories across three phases ⇒ **THREE** pin passes (8 → 9 → 10 → 12), not one. **R11/R12** reconciled — the ari-only rule is now **three** layers (privileged category + allow-list + runtime gate). **R28** resolved by batched hydration in phase 1 (≤3 queries per render). |
| **A-44** | P0 | §6 (new rows **R33–R47**) | **15 risks added with the architect's severities**: R33 `stopped`-label reuse disarms the alarm 🔴 · R34 message-only re-arm collapses 🔴 · R35 `self-alarm` non-privileged ⇒ `watcher` ⇒ Ari executes attacker-influenced text 🔴 · R36 relative `reports` path kills `view_write` 🔴 · R37 `str.format` + CSS braces ⇒ 500 on every board GET 🔴 · R38 `Cache-Control` merge-order trap 🟡 · R39 no render byte cap 🟡 · R40 `board_link` URL when live-views is disabled 🟡 · R41 no `fsync` before `os.replace` 🟡 · R42 a future `has_root` check breaks the board 🟡 · R43 default schedule list hides armed alarms 🟡 · R44 label namespace shared with non-scheduler sources 🟡 · R45 create-response `status` racing 🟢 · R46 `tz_warning` erasure 🟡 · R47 invisible truncation 🟡 |
| **A-45** | P1 | §3 (phase table + total row + the shared-edit note) | The "3 shared edit files" summary was **wrong** — the architect's file map is the authority: `daemon/manager.py` · `daemon/tools/_tool_registry.py` · `daemon/tools/instance.py` · `agents/ari/meta.json` · `agents/leader/meta.json` · **the 5 SAME-PR pin files** · `daemon/api.py` · `daemon/services/live_views.py` + `daemon/routers/live_views.py` · `config.yaml` (doc-only) · `agents/ari/tools_note.md` ⇒ **15 shared edit files in v1**; `daemon/loader.py` is the 16th and is **SKIPPED** (leader ruling **R-loader**). The phase rows now name the pin files and the **8 → 9 / 9 → 10 / 10 → 12** passes. |
| **A-46** | P1 | §4 (new bullet), §5 (heading + new item 4) | The privileged set is a **cross-phase coupling with THREE pin passes** (8 → 9 → 10 → 12), not one — the plan's "phase 3 pays the pin cost once" statement is **no longer true**. Each pass is one inserted literal line per file and ships **in the same commit** as its category entry ⇒ no red window, but the cost is re-paid in every phase that adds a category. |
| **A-47** | P1 | §9 (migration posture + the pre-drafted adjudication) | The migration posture now names the **adopted prefix** `20261020_060000`, the mandatory `-- DOWN` section, and the **non-empty** delta — which means the stage gate will demand an explicit rollback-safety adjudication. The **text is pre-drafted** (additive-only semantics: no `ALTER`, no existing-row change, `create_all` never drops, a rollback tears nothing down and retains the board data) so it is not discovered at stage time; the standing rule requires the semantic reasoning, not a reflex verdict. |
| **DRIFT** | P1 | §3 (migration count), §4 (`create_all` anchor), §5.2 (prefix rule), §7 (criteria 2, 3, 13, 14), §8 (deferred rows), §10 (doc map) | Counts and claims that the adjudication invalidated: the migration filename/prefix (the old phase-1 prefix → the adopted distinct-day `20261020_060000…`) in §3/§5.2; the `create_all` anchor (`daemon/manager.py:6597-6605` → the call at `:751`, the "does not ALTER" docstring at `:6596-6604`); §7 criterion 2 (the runner is **SQLite-only**, `create_all` runs first on both drivers, the model is the source of truth); §7 criterion 3 (the **SQL-capture guard is the primary** append-only proof, the static scan secondary); §7 criterion 13 (an empty allow list means "everything **except** privileged" — the gating is proven against the **empty** allow list, and the in-body `agent_id` gate is required because a literal tool-name allow entry bypasses the category gate); §7 criterion 14 (the privileged trajectory + merge verification of #9/#10); §8 (the chat-bridge row now carries the ruling, and themes 4 and 8 are marked DEFERRED-to-v2; the `daemon/loader.py` row is added); §10 (the architect's document added to the map). |
