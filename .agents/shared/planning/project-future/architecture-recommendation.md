# Architecture Recommendation: Project Future — dev-team forward-improvement wishlist

Date: 2026-11-12 (UTC)
Commission: "Project Future" — architectural enrichment of the approved-draft plan
Worktree: `/home/nea/ensemble-src-wt-project-future` (branch `feature/project-future`, HEAD `576d56a9d`, base `ac9849bf7` == origin/latest)
Scope reviewed: `plan.md` (612 lines), `decisions.md` (D1–D15), `research-{tool-lane,data-layer,prompt-docs}.md`
Verdict: **APPROVE-WITH-ADJUSTMENTS** (10 required folds + 2 optional; no design reversal)

---

## Status — DEGRADED (disclosed)

The three-worker fan-out was **partially unavailable**. The provider returned
`insufficient_balance` (402) fleet-wide:

| Node | Skill | Outcome |
|------|-------|---------|
| A — data layer (areas 1–2) | `data-flow-design` | **ERROR (402)** — no report delivered |
| B — surface/registration (areas 3–5) | `structural-design` | **ERROR (402)**, revived once per recovery ladder, errored again — no report |
| C — doctrine/risk (areas 6–7) | `resilience-design` | **COMPLETE** — report delivered and folded in below |

Consequence: areas 1–5 were adjudicated **architect-direct** using read-only tools
(grep/sed/sha256sum/python one-liners) under the same evidence standard the workers
were given; area 6–7 fuses worker C's evidence with my own. Every claim below cites
`file:line` verified **in this worktree today**. Where a claim could not be verified it
is marked UNVERIFIED. This is not the normal fan-out mode and is disclosed as a gap
(see `## Gaps`).

---

## Verdict table

| # | Focus area | Verdict |
|---|-----------|---------|
| 1 | Storage + FIFO determinism (seq vs alternatives) | **SOUND** — `seq` + `UNIQUE(project_id, seq)` is the correct choice; two documentation/claim fixes |
| 2 | Transactional shape / concurrency | **ADJUST** — design is correct but for an incomplete reason; the cap invariant silently depends on ORM `StaleDataError`. Add an explicit rowcount guard |
| 3 | Tool surface + API shape | **SOUND** — 3 verbs, envelopes, hard-delete, no `include_evicted` all confirmed |
| 4 | Category + registration | **SOUND** — non-privileged inheritance verified; single seam verified; MCP adapter is a no-op; one verified import requirement |
| 5 | Allow-list set (14 files) | **SOUND** — set matches the tree; `giter`/`coder` are the only arguable follow-ups |
| 6 | Prompt doctrine + baseline | **SOUND** — twins identical, edit is exactly minimal, **0** `tools_note` edits needed, 3 hashes confirmed |
| 7 | Risk-table gaps | **ADJUST** — 6 net-new risks missing (incl. the one that matters: the delete-refactor class) |

---

## 1 — Storage + FIFO determinism: SOUND

### Alternative-rejection table (ordering key)

| Alternative | Deterministic under same-timestamp ties? | Exact insertion order? | Concurrency | Portability (SQLite + PG) | Verdict |
|---|---|---|---|---|---|
| `created_at` alone (the `prune_excess` template, `pending_repository.py:451`) | **No** — tie → arbitrary subset | No | n/a | Yes | **Reject** (this is exactly the weakness the plan hardens) |
| `(created_at, id)` composite | Yes (deterministic) | **No** — uuid4 tiebreak is random *and* wall-clock skew can order a newer row first | n/a | Yes | **Reject** |
| Global bigint sequence / `IDENTITY` | Yes | Yes (per-project is a subsequence) | Atomic at DB (no MAX read) | **PG yes; SQLite no** clean non-PK autoincrement | **Reject** (portability; keep as PG-only fallback if the race ever bites) |
| SQLite `rowid` / PG `ctid` | Mostly | **No** — `VACUUM` renumbers rowids; `ctid` is unstable | n/a | No | **Reject** |
| UUIDv7 PK (time-ordered) | Partially (1 ms window, random tail) | No — arbitrary within the millisecond | No race, no extra column | Python 3.14 ships `uuid.uuid7()` | **Reject** — larger tie window than microsecond `seq`, weaker than "exact" |
| **`seq` + `UNIQUE(project_id, seq)` (planned)** | **Yes** | **Yes** | MAX+1 race → unique violation (fail-closed) | Uniform on both | **ACCEPTED** |

**Why it wins:** the only key that is *exactly* insertion order on both drivers without
depending on a clock is an app-assigned monotonic ordinal. `UNIQUE` is not
over-constraining — it is the correctness backstop for the concurrent MAX+1 read
(see area 2). Seq gaps after eviction are harmless: gaps only shrink the live set and
`ORDER BY seq` is unaffected (`plan.md:230-252`).

### Finding 1a (ADJUST, doc): `seq` is monotone over LIVE rows, not forever

`MAX(seq)+1` **reuses the top slot** after the newest row is removed:
rows `1..5` → `remove(seq=5)` → next add computes `MAX=4` → new row gets `seq=5`.
Ordering and FIFO remain correct (the new row is the newest and still has the max
seq), but:
- D2's "monotonic per project" is strictly true only among live rows;
- an eviction-disclosure `seq` is **not** a durable identifier across removals.

**Fold:** in D2 and `_FULL_DOCS`, state *"`seq` is monotone non-decreasing among live
rows; removing the newest row may reuse its seq. `id` is the durable identity — never
correlate disclosures by `seq`."* No code change.

### Finding 1b (ADJUST, claim): "the table NEVER holds 33 rows"

True **after any add** on the writer path (see area 2). A table drifted by direct DB
writes can exceed 32 until the next add; the plan already says it self-heals
(`plan.md:221`). Reads are deliberately uncapped — hiding rows would be silent loss —
and `total` is exposed so drift is observable (`repository.py:1388-1400` shape).
**Fold:** reword `plan.md:529` to *"after any add the table holds ≤32; a drifted table
self-heals on the next add; reads are uncapped by design and expose `total`."*

---

## 2 — Transactional shape / concurrency: ADJUST

### Concurrency posture comparison

| Shape | Correct? | Portable (SQLite + PG)? | Cost | Verdict |
|---|---|---|---|---|
| Planned: count → prune → `MAX(seq)+1` → insert, one session | Yes, **but only because of two implicit guards** | Yes | 0 | **Keep** + harden |
| Single-statement `INSERT … SELECT COALESCE(MAX(seq),0)+1` | **No** — still races under READ COMMITTED | Yes | 0 | Reject (does not fix, and cannot carry prune+disclosure) |
| `SELECT … FOR UPDATE` on project rows | Partial — locks nothing on an **empty** project; `FOR UPDATE` + aggregate is invalid | PG-shape only | Medium | Reject |
| `pg_advisory_xact_lock(hashtext(project_id))` | Yes | **No** — needs a dialect branch | Medium | Reject (breaks the uniform shape) |
| Bounded retry on `IntegrityError` | Yes | Yes | ~15 lines | **ADOPT (additive)** |
| Per-project counter table + `UPDATE … RETURNING` | Yes | `RETURNING` support varies | High (new table, new lifecycle) | Reject (over-engineering at cap 32) |

**What the plan gets right:** `UNIQUE(project_id, seq)` genuinely prevents the
"two adds on a 31-row table → 33 rows" outcome — the loser's insert raises and the
whole transaction (deletes **and** insert) rolls back. Fail-closed is correct.

**What the plan misses (the important finding):** the cap invariant and the
**no-false-disclosure** contract are *also* protected by a second, undocumented guard —
SQLAlchemy's ORM `StaleDataError`. Walk the interleaving:

1. Table at 32. T1 and T2 both read `count=32`, both select victim `R1 (seq=1)`.
2. T1 `DELETE R1`, inserts `seq=33`, commits.
3. T2's `session.delete(R1)` flushes after T1 commits → the ORM DELETE matches **0
   rows** → SQLAlchemy raises `StaleDataError` → T2 rolls back.

Without that exception, T2 would compute `MAX(seq)=33` (post-commit re-read under READ
COMMITTED), insert `seq=34`, commit — **33 rows** — and return `evicted=[R1]`, an item
it never deleted. That is a **user-law disclosure violation** (false disclosure), not
just a capacity bug.

`prune_excess` (the template the plan clones) deletes with raw `text("DELETE … WHERE id
IN :ids")` (`pending_repository.py:462-470`), which **silently deletes 0 rows and
reports success**. A future "simplification" of the planned `for v in victims:
session.delete(v)` into that idiom would remove the guard with no test failing.

### Adjustment 2a (required): explicit rowcount guard

Replace the per-victim ORM delete with a Core delete whose rowcount is checked, e.g.:

```python
if victims:
    victim_ids = [v.id for v in victims]
    result = session.execute(
        delete(ProjectFutureEntry).where(ProjectFutureEntry.id.in_(victim_ids))
    )
    if (result.rowcount or 0) != len(victim_ids):
        session.rollback()
        raise ConcurrentWishlistUpdate(project_id)   # domain error, see 2b
```

This makes the false-disclosure path **impossible by construction**, removes the
reliance on ORM flush-time magic, and becomes directly testable (delete a victim from a
second session, then run the add). Add a comment naming `StaleDataError` /
0-row-delete as the failure class so nobody "optimizes" it back. Pin with **AC25**.

### Adjustment 2b (required, small): translate the race into a stable error

The planned `except Exception as exc: return {"error": str(exc)}` (`plan.md:331`)
surfaces dialect internals (`integrity constraint name, table, pgcode` — the exact
problem the house already solved once). Precedent:
`daemon/repositories/mcp_server/repository.py:146-205` — catch `IntegrityError`,
`rollback()`, translate the UNIQUE case to a clean message, re-raise the rest.
**Fold:** catch `IntegrityError` **and** the guard exception in `add_future_entry` /
the tool, rollback, and return `{"error": "Concurrent wishlist update for project
'<pid>' — please retry"}`. Optionally retry once inside the repo before surfacing.
Contract stays a single `{"error": str}` dict (A7 preserved).

**Posture verdict:** keep fail-closed as the outer contract (it is honest and preserves
the cap); add the guard + translation so the *common* outcome is a clean retry, not a
dialect stack fragment.

---

## 3 — Tool surface + API shape: SOUND

Verified against the sibling (`project_history.py:130-306`, `repository.py:1285-1400`):

- **Three verbs are right.** ≤32 items needs no search (`project_history_search` exists
  for an unbounded log, not a 32-slot list); every row *is* a future idea, so no
  `entry_type` enum. `add/list/remove` covers the whole lifecycle. ✔
- **Envelopes are consistent:** list `{entries,total,limit,offset}` exactly matches
  `repository.py:1393-1400`; `limit=max(1,min(limit,100))`, `offset=max(0,offset)`
  match `project_history.py:236-237`; errors are always a single `{"error": str}`. ✔
- **Removal shape:** `{"success": true, "removed": {...}}` is a justified enrichment of
  history's `{"success": true, "deleted_entry_id": …}` (`project_history.py:302`) — the
  wishlist retiree needs to see *what* was retired. ✔
- **Absent-id remove returns an error** (not idempotent success) — matches
  `project_history.py:300`; consistency beats idempotency here, and the error is
  informative when an item was just evicted by a concurrent add. ✔
- **Hard delete — CONFIRMED.** A soft-delete tombstone would either consume the 32
  slots (defeating the cap) or force a filter into every read (complexity for no
  benefit). Eviction disclosure already surfaces loss. ✔
- **No `include_evicted` — CONFIRMED** (it presupposes soft-delete). ✔

**Optional (🟢):** add `"cap": 32` to the list envelope so the tool is fully
self-describing to the agent reading it. Not required — `_FULL_DOCS` states the cap.

---

## 4 — Category + registration: SOUND

- **Non-privileged is correct and verified.** `resolve_tool_filter`
  (`daemon/tools/instance.py:431-470`) returns `None` (all tools) only when **both**
  allow and deny are empty. Census of `agents/**/meta.json`: the **only live agent with
  an empty `tools.allow` and no deny is `watcher`** (`allow: []`, `deny: null`);
  `_inner_soul` / `_prompt_system` are underscore templates. So the plan's claim "every
  dev agent has a NON-EMPTY allow" **holds**, and `watcher` inherits `project_future`
  exactly as it inherits `project_history` today — no new behavior class. A15/W5:362
  states this honestly.
- **Single construction seam verified.** `create_project_history_tools` is imported at
  `instance.py:281` and called **only** at `:6651`; `daemon/tools/__init__.py` has no
  factory re-export surface (`grep` → only `create_project_tools`). Mirroring that seam
  is sufficient.
- **Ordering law verified.** The extends sit before `create_help_tool` (:7620),
  `scan_tools_for_full_docs` (:7625) and `_apply_tool_filter` (:7628); category
  expansion for the filter comes from the scan, so a late extend is silently invisible.
  PO7 remains the only guard for that class. ✔
- **MCP adapter — no entry needed (gap closed).** `daemon/mcp/tool_adapter.py` does not
  enumerate categories; it only privilege-stamps prefix overrides
  (`:134-196`, `:317-346`). A non-privileged category needs nothing there.
- **create_all lane verified.** `daemon/repositories/__init__.py:5` already imports
  `daemon.repositories.project.models`, and `manager.py:57` imports `daemon.repositories`
  before `SQLModel.metadata.create_all(self._engine)` (`:1034`). Appending the class to
  `models.py` therefore registers it; the proposed extra import block is
  documentation-grade (mission-record precedent, `repositories/__init__.py:95-107`),
  not load-bearing. **Do not add a second `api.py` lane** — that is the watcher-model
  workaround for modules imported *after* create_all (`api.py:1218-1232`), which does
  not apply here.
- **Verified requirement:** `Index` is **not** in `models.py`'s SQLAlchemy import line
  today (`CheckConstraint, Column, ForeignKey, Integer, String, UniqueConstraint`).
  The plan's "if not already imported" is therefore a **mandatory** edit, not
  conditional.
- **REST parity zero confirmed.** No API router or UI contract for this lane; the
  repository methods are the additive seam (D6).
- **Non-privileged choice requires no `PRIVILEGED_TOOL_CATEGORIES` edit** — leaving the
  five-pin exact-set assertions (`instance.py:7644-7656`) untouched. ✔

---

## 5 — Allow-list set (14 files): SOUND

Census verified directly:

- All 14 named dirs exist. Twins exist exactly where claimed; `tester[v2]`,
  `architect[v2]`, `devops[v2]`, `leader[v2]` **do not exist**. ✔
- `agents/leader/meta.json` `team_members` = 17 agents: planner, developer, reviewer,
  tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer,
  doc-writer, maintenancer, designer, gaia, aegis. The plan's set = leader + the 8
  dev-spine members (five doubled by twins) = **14**. ✔
- **PM exclusion confirmed correct:** `agents/project-manager/meta.json` uses
  verb-style allow (`project_history_list`, `project_cn_add`, …) plus a deny list
  (incl. `edit_file`, `bash`). A category-key grant would fight its idiom — follow-up,
  as planned. ✔
- **Arguable exclusions (🟢 follow-up, not blocking):** `giter` (a leader team member
  doing dev-adjacent git work) and `coder` (does implementation work, though not in
  leader's `team_members`). Both could legitimately file wishes. Widening is one
  `meta.json` each; do not expand v1.

---

## 6 — Prompt doctrine + baseline: SOUND

Worker C (resilience-design) verified independently; I confirmed each point:

- **Twins byte-identical:** both `agents/reviewer/workflow.md` and
  `agents/reviewer[v2]/workflow.md` = `24e40e96a055441e47079be9abf70257cdf0b898ecfc18d35e4f6d13437ce9ac`.
  `:286` carries the exact old row in each. ✔
- **The routing text is unique to those two lines:** `grep -rn "future improvements"
  agents/reviewer/` returns **only** `workflow.md:286` and `[v2]/workflow.md:286`.
  The 7 `skills-template/*.md` files carry the severity *scale* (`code-review.md:54`
  etc.) but **not** the routing cell — so excluding them leaves no dead-end. ✔
- **`reviewer/rule.md:22` / `:94`** are severity-ordering and classification rules, not
  routing text — excluding them creates no self-contradiction. ✔
- **Leader table:** headers `:131-132`, rows `:133-137`; no `leader[v2]`. ✔
- **Baseline machinery:** 181 rows = 47 soul + 45 workflow + 44 rule + 40 tools_note +
  5 growth; **0** `memory.md` / `skill.md` rows on disk; `meta.json` and `docs/` are
  outside the hashed set; `--check --tree` exists
  (`scripts/protection/generate_prompt_artifact_baseline.py:60-63,111-137`). ✔
- **`tools_note.md` edits needed: 0** across all 14 granted agents. `tools_note.md` is
  hashed (40 rows), and only `leader/tools_note.md:59-80` enumerates recording verbs
  (`project_cn_*`) — every statement remains true after the grant; nothing routes
  "future ideas" anywhere. Therefore **exactly 3 hashes change** (leader rule.md row
  119; reviewer workflow.md rows 143/147). **Pre-balance confirmed.** ✔
  *(Correction to my pre-dispatch premise: my narrower grep missed `project_cn_*`; the
  broader sweep found it and confirmed it does not go stale.)*

### Finding 6a (ADJUST, small): the leader tiebreak routes a wish to the wrong lane

The row alone is not enough. `agents/leader/rule.md:139` says *"If in doubt, use
`project_history_add()` for events or `experience()` for knowledge."* A forward-looking
idea ("we should adopt X") sits on the **Project knowledge** row's boundary
(`:134` — "architecture, patterns, gotchas"), and an uncertain leader resolves it to
`experience()` — the wrong lane, defeating W6a.

**Fold into W6a (same file ⇒ still exactly 1 hash change):**
1. Extend the new row's negation:
   `| **Future improvement idea** — something you wish existed later (NOT a lesson, NOT an event, NOT existing knowledge — knowledge describes what IS; a wish describes what could BE) | `project_future_add()` | "Add a streaming retry path for long LLM calls" |`
2. Extend the `:139` Rule sentence: `… use project_history_add() for events,
   experience() for knowledge, or project_future_add() for a wish about what the
   project could become.`

Also fold into W6d an item **(viii): wishlist items do NOT feed the Blueprint pending
queue — they are not structural events** (prevents a reader "fixing" the deliberate
skip).

---

## 7 — Risk-table gaps: ADJUST (6 net-new rows)

Existing rows 1–9 are sound. Missing:

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 10 | **Delete-refactor class** — the cap + no-false-disclosure invariant depends on the ORM delete raising `StaleDataError`; rewriting it to the `prune_excess` `text("DELETE … WHERE id IN")` style (which silently deletes 0 rows) breaks both | Low (post-merge refactor) | **High** (cap violation + false user-law disclosure) | Adjustment 2a: Core delete + explicit `rowcount != len(victims)` guard + AC25; comment naming the class |
| 11 | **PG model-side `VARCHAR` vs doctrine comment** — `max_length=300/5000` makes create_all emit `VARCHAR(300)/(5000)` on PG; the migration's `TEXT` only applies to the SQLite runner. The header's "never VARCHAR, **on either driver**" is false | High (certain on PG) | Low (equivalent semantics; length check unreachable via two-layer truncation; sibling precedent `models.py:490-491`) | Reword the W2 comment; scope AC20's zero-VARCHAR grep to the `.sql` file it actually greps |
| 12 | **Raw dialect error string surfaced to the agent** — `str(IntegrityError)` embeds constraint/table/pgcode | Low | Low (opaque UX, no secrets) | Adjustment 2b translation (`mcp_server/repository.py:146-205` precedent) |
| 13 | **`created_at` example/response shape mismatch** — `session.refresh` re-reads the stored value; SQLite and PG (`TIMESTAMP WITHOUT TIME ZONE`) both drop tzinfo, so `isoformat()` has **no `+00:00`**, contradicting the example at `plan.md:541` | Medium (every add) | Low (no AC pins the offset; FIFO never reads `created_at`) | Fix the example to naive UTC + note in the Data Model Summary |
| 14 | **Empty-allow inheritance** — every empty-allow agent (in practice only `watcher`) silently gains 3 write tools | High (structural) | Low (32-capped, project-scoped, disclosed; mirrors `project_history` status quo) | Keep documented in W5; per-agent deny remains available; AC24 pins both directions for granted agents |
| 15 | **Cross-lane migration-prefix collision** — parallel staged lanes; newest prefix here `20261112_124200`. v0.29.21 ships zero migrations; v0.29.22's inventory is UNVERIFIED from this worktree | Very Low (same-second + guard) | Low (pre-apply fail-loud) | Real-UTC stamp at creation + `ls daemon/migrations/versions | grep <prefix>` pre-commit; the guard (`runner.py:395-417`) fails before any apply |

**Explicitly answered (no row needed):**
- **PG partial index — none required.** The cap is per-project **total**; there is no
  subset predicate. `UNIQUE(project_id, seq)` serves both the FIFO scan and the race
  guard directly. ✔
- **JSONB vs JSON — non-issue.** The plan deliberately omits any metadata column
  (`plan.md:504`), which is right for a wishlist. If one is ever added, use `JSONBType`
  (house idiom), never `JSON`.
- **C8 Blueprint hook not copied — correct.** Verified the hook enqueues only
  `entry_type ∈ {feature, milestone}` (`project_history.py:161-162`), gated on
  per-project `BLUEPRINT_ACTIVE` metadata and skipping the system default project.
  Enqueueing wishlist ideas would poison blueprint incremental updates. ✔
- **create_all ↔ migration drift on PG — safe.** Brand-new table; the model carries both
  index names (`__table_args__` unique + `index=True` auto-name `ix_project_future_project_id`);
  no naming convention is configured on the metadata, so default names hold. The `.sql`
  never runs on PG. Note-only: model↔DDL index-name lockstep is enforced by review, not
  programmatically (optional hardening: grep both names in `models.py` inside AC20).

---

## Consolidated verdict: APPROVE-WITH-ADJUSTMENTS

The plan is architecturally sound. No design reversal is needed. Fold the following
into `plan.md` before the developer executes.

### Required

1. **[Area 2 — blocking] Victim-delete hardening.** In `add_future_entry`, replace the
   per-victim `session.delete(v)` loop with a Core `delete()` by id set and **assert
   `rowcount == len(victims)`**, rolling back and raising otherwise. Add a comment
   naming `StaleDataError` / 0-row-delete as the failure class, and add **AC25**
   (a concurrent/stale victim delete must abort the transaction, leave count ≤ cap, and
   return no false `evicted` entry).
2. **[Area 2 — blocking] Translate the race error.** Catch `IntegrityError` (and the
   guard exception) in the repository, `rollback()`, and surface a stable message
   (e.g. `{"error": "Concurrent wishlist update for project '<pid>' — please retry"}`),
   mirroring `daemon/repositories/mcp_server/repository.py:146-205`. Optional: one
   bounded retry. Keep the single-`{"error": str}` contract.
3. **[Area 7] Append risk rows 10–15** from the table above to the plan's Risk Table.
4. **[Area 7] Reword the W2 comment** claiming TEXT-never-VARCHAR "on either driver";
   state the PG create_all reality (`VARCHAR(300)/(5000)` via `max_length`) and the
   sibling precedent; scope AC20's zero-VARCHAR grep explicitly to the `.sql`.
5. **[Area 1] Scope the "NEVER holds 33 rows" claim** to *"after any add"*, and note
   reads are uncapped by design (`total` exposes drift).
6. **[Area 1] Document `seq` reuse** after removing the newest row in D2 + `_FULL_DOCS`:
   monotone over live rows; `id` is the durable identity.
7. **[Area 6] Strengthen the leader table edit** (same file ⇒ still exactly 3 hashes):
   add the "NOT existing knowledge …" clause to the new row and extend the `:139`
   tiebreak to mention `project_future_add()`. Add W6d item (viii) — wishlist items do
   not feed the Blueprint queue.
8. **[Area 6] Fix the `created_at` example** to naive UTC (no `+00:00`) and note the
   driver tz-drop in the Data Model Summary row.
9. **[Area 6] Add W7 step 0:** `git status --short` in the worktree must show **only**
   the three intended prompt edits before the baseline regen; anything else in the tree
   changes the hash count.
10. **[Area 4] W1 accuracy:** state that `Index` is **not** currently imported in
    `daemon/repositories/project/models.py` — the import edit is mandatory.

### Optional (🟢, leader's call)

11. Make `add_future_entry(..., max_entries: int)` **required** (no default) so the two
    `32`s cannot drift; the tool already passes it.
12. Add `"cap": 32` to the list envelope; consider `entry_id` (family consistency with
    `project_history_delete(entry_id=…)`) instead of `item_id` for LLM-call habit.

### Follow-ups (do not expand v1)

- `giter` / `coder` grant candidates; PM exclusion confirmed correct.
- Reviewer severity-tier doctrine sweep (7 `skills-template/*.md`) — leave the wording
  divergence documented; it is not a contradiction.
- REST parity and search remain additive later (D6 / follow-up #5).

**Confidence: High.** The remaining uncertainty is in the two areas the fan-out could
not cover (see Gaps); every adjustment above is backed by direct file evidence in this
worktree.

---

## Gaps

- **Area 1–2 (worker A, `data-flow-design`) — NO REPORT.** Instance
  `c7c4c449-5d23-4268-ba67-cae49ba9b78a` errored on provider `insufficient_balance`
  (402). Analysis above is architect-direct. Residual risk: a concurrency interleaving
  I did not enumerate (e.g. multi-writer bursts >2) is unverified beyond the two-writer
  analysis in area 2.
- **Area 3–5 (worker B, `structural-design`) — NO REPORT.** Instance
  `78fcbb09-16c9-4436-a094-457cb88faf0d` errored, was revived once per the recovery
  ladder, and errored again. No replacement spawned (same known provider class).
  Analysis above is architect-direct with full census evidence.
- **Area 6–7 (worker C, `resilience-design`) — COMPLETE**, folded in above.
- **UNVERIFIED:** v0.29.22's migration inventory (its branch is not in this worktree;
  fetching exceeds the read-only fence). Cross-lane collision is fenced by the
  pre-apply guard regardless.

---

## Open questions

None blocking. One item for the leader's judgment: **Adjustment 2b's retry** — the
recommendation is translate-and-fail-closed; a single in-repo retry is offered if the
leader prefers the capture lane to be frictionless. Either is correct; the translation
is the part that matters.
