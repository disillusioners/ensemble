# Decisions — commission `ari-tools`

Date: 2026-10-19
Author: worker via plan-creation (commission `ari-tools`, final planning wave, dispatched by planner)
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`)

> **What this file is.** The decision **ledger** for the commission: what was decided, by whom, and with what
> pointers. It records *what* and *why*, and it points at the owning phase plan for the full rationale bodies
> and file:line anchors. **The three phase plans remain the canonical detail homes** — where this ledger
> summarises, the phase doc is authoritative. Where this ledger *disagrees* with a phase doc, the phase doc wins
> until the architect rules.
>
> Companion: [plan-overview.md](plan-overview.md) (synthesis, coupling map, consolidated risks).

---

## Section A — SETTLED WITH THE USER: BINDING

Everything in this section is **BINDING**. It is not open for re-litigation by a phase builder, an architect
enrichment pass, or a reviewer. Where a phase plan appears to diverge from a binding item, the phase plan is
wrong and must be amended.

### A1 — The five binding user decisions

| # | Decision (short form as recorded in the commission) | Confirmed by | Binding consequence |
|---|---|---|---|
| **U1** | **One global board with tags — not per-chat boards.** | [phase1-plan.md D1, §6.1](phase1-plan.md) | The board schema carries **no** partitioning column at all — no `context_key`, no `tree_root_id`, no `source`, no project-scoped table. Project and channel are *attributes of an item* (a nullable `project_id` FK; the channel/source as a plain tag string, e.g. `telegram`). One table set, one namespace, visible to every Ari instance and to the leader. |
| **U2** | **Passcode-gated public links via the EXISTING mechanism.** | [phase3-plan.md D4, §0, §6](phase3-plan.md) | `view_write` and `board_link` **reuse** `ViewPasscodeService.mint`/`refresh` exactly as `view_link` does. **Zero new crypto, zero new passcode concept, zero new table, zero new gate code.** The existing 6-char code, the 10-strike lockout, the per-IP bucket and the 24 h grant cookie are inherited unchanged. |
| **U3** | **The board page is SERVER-RENDERED FROM DB** — always fresh, never a static HTML file Ari rewrites. | [phase3-plan.md §0, §4.3, D14](phase3-plan.md) | The board is rendered from the DB **on every GET** with `Cache-Control: no-store`. The `/views/public/board/index.html` path is a **route**, not a file. (Contrast `view_write` pages, which *are* static files — that is a different deliverable, not a contradiction.) |
| **U4** | **Alarm minimum horizon 30 minutes — otherwise use bash `sleep`.** | [phase2-plan.md D9, OQ scope](phase2-plan.md) | The floor is enforced by the wrapper **pre-create** (so a rejected alarm burns no label — see §C.3(i)) and the rejection text carries the bash-sleep hint verbatim. The floor applies to the **first-fire offset only**; later fires of a recurring alarm are unconstrained. |
| **U5** | **Build order 1 → 2 → 3.** | [plan-overview.md §4](plan-overview.md) | Phase 1 (board) → phase 2 (`self_alarm`) → phase 3 (live views). Phase 2 and phase 3 are technically parallelizable after phase 1 lands, but the order is binding and the single-branch/merge/stage flow follows it. |

### A2 — The leader's pre-rulings (also BINDING)

| # | Pre-ruling | Enforced where | Binding consequence |
|---|---|---|---|
| **L1** | **`board` category → ari + leader.** | [phase1-plan.md E10/E11](phase1-plan.md) | `"board"` is appended to `agents/ari/meta.json` **and** `agents/leader/meta.json` allow lists. **No other agent** may hold it. (Caveat: the *mechanism* by which this is enforced is an open architect question — see §C.1.) |
| **L2** | **`self_alarm` → ari only.** | [phase2-plan.md D10](phase2-plan.md) | `"self-alarm"` goes into `agents/ari/meta.json` **only** — never leader. Enforced **thrice**: the **privileged category** + the **allow-list** **and** a runtime `agent_id != "ari"` gate in the tool body (an allow-list is a visibility boundary, not a security boundary). The third layer is **L2a** below — it is what makes the rule structural rather than advisory. |
| **L2a** | **`self_alarm` is PRIVILEGED** — leader ruling **R-self-alarm** (architect §1.4). | [phase2-plan.md D10](phase2-plan.md) · architect §1.4 | `"self-alarm"` is **entry #10** of `PRIVILEGED_TOOL_CATEGORIES` from Phase 2's **first** commit, **on top of** L2's two layers. **Three** enforcement layers, not two: (1) the **privileged category** (opt-in — a non-privileged category is default-granted to the empty-allow universe, so `watcher` — the only in-tree agent with an empty allow list — would otherwise resolve the tool); (2) the **allow-list** (`agents/ari/meta.json` only, never leader); (3) the **runtime `agent_id != "ari"` gate** in the tool body — defence in depth, and the only layer that also closes the **literal-tool-name** bypass (`daemon/tools/instance.py:471-478`). Why the escalation is structural, not cosmetic: a watcher-reachable `self_alarm` spawns a **fresh Ari instance** whose message carries the caller's action text **verbatim** ⇒ an attacker-influenced text-execution path, not merely a visibility leak. |
| **L3** | **`view_write` → ari only.** | [phase3-plan.md D3](phase3-plan.md) | `"view-write"` is a **privileged** category granted to ari only. Leader gains **no** write authority — the existing `view-views` category (ari + leader + designer) is untouched. |
| **L4** | **Tags storage preference.** | [phase1-plan.md D2, §4.4](phase1-plan.md) | Tags are stored in a shape that supports **relational filtering** (the board filters by tag), not an embedded JSON list. Resolved during planning to a **junction table** `board_item_tags` with composite PK `(item_id, tag)`. The embedded-`JSONBType` pattern is for lists that are **not** relationally filtered. (Refinement of the pre-ruling, not a contradiction of it — §C.2 is the one place where a pre-ruling-adjacent choice genuinely diverged from house convention.) |
| **L5** | **Reports root is daemon-GLOBAL, not per-project.** | [phase3-plan.md D1, D5](phase3-plan.md) | The `reports` root is `<data_dir>/reports`, **daemon-injected** at lifespan — not a project workdir, not operator-declared. A report is not a project artefact and must survive a project's absence. |
| **L6** | **`board` is a reserved root name.** | [phase3-plan.md D10, §4.1](phase3-plan.md) | `board` is **not** a registered live-views root; it is **reserved**. An operator cannot declare a root named `board` (boot refusal), the direct lane `/views/board/index.html` 404s, and `view_link("board", …)` returns the reserved-name envelope — so the board's public link is **not** mintable by the `view-views` holders. |

### A-ADJUDICATED — the leader's post-architect rulings (BINDING)

The architect's adjudication ([architecture-recommendation.md](architecture-recommendation.md), 541 lines,
planning head `2b297db7d`) was **ACCEPTED IN FULL** by the leader: every verdict is a **binding ruling**, not a
review comment. These are recorded here in the same tier as §A1/§A2 — they close items that were open when this
ledger was first written, and each names the architect section it comes from.

| # | Ruling | Architect ref | Binding consequence |
|---|---|---|---|
| **R-C1** | **The `board` category is PRIVILEGED from Phase 1's FIRST commit.** The planner's "flip it in Phase 3" plan is **OVERTURNED**. | §1.1–§1.3 | `"board"` = **entry #9** of `PRIVILEGED_TOOL_CATEGORIES`, added in phase 1's first commit **with** the five SAME-PR exact-set pin insertions **in the same commit** — marginal cost **5 file-touches, zero extra lines**. The **privileged category AND** the in-body `agent_id ∈ {"ari","leader"}` gate are **both** required: a **literal tool-name** allow entry bypasses the category gate outright (`daemon/tools/instance.py:471-478`), so the category alone is not sufficient. The commission's privileged-set trajectory is **8 → 9 (P1: `board`) → 10 (P2: `self-alarm`) → 12 (P3: `view-write`, `board-link`)** ⇒ **THREE pin passes** over the same five files, not one. |
| **R-self-alarm** | **`self-alarm` is PRIVILEGED** — the privilege model extends to phase 2. | §1.4 | See **L2a** above: privileged category (entry #10) + allow-list + runtime gate = **three** layers. The registration test must prove the property directly: `resolve_tool_filter` yields **no** `self_alarm` for a non-ari agent with an **EMPTY** allow list — not merely that one `meta.json` names the category. |
| **R-lateness** | **Lateness cap: default `None`, NO cap in v1.** | §4.1 | The cap is **daemon-global**, so the wrapper **cannot override** it. With a cap set, a missed alarm enters a **5-second daemon-wide re-evaluation loop** (`daemon/sources/adapters/scheduler.py:559`; `SCHEDULER_ERROR_RETRY_S = 5.0`) with **no self-heal** and **no user-visible signal** (only a `skipped` history row; the schedule still reads `running`) ⇒ a late-but-real delivery becomes a silent, permanent, non-self-healing loss. The late path is self-announcing (the composed message states the scheduled instant). ⇒ the wrapper **MUST** read and **echo** `lateness_cap_seconds` (`<int\|null>`) so the agent knows whether a missed alarm would be delivered late or skipped forever. |
| **R-chat-bridge** | **No new infrastructure for a chat bridge in v1.** | §4.2 | There is **no structural path** from a fired alarm to a chat surface (the scheduler source is one-way; the receiving instance is scheduler-owned with no chat identity). ⇒ **THE BOARD is the user-visible surface of a fired alarm** — the alarm lands in a fresh Ari instance, which acts and leaves evidence **on the board**; the live board view shows the change. The contract **promises** the board and **must NOT** promise a ping, a reminder, or an interruption. Carried explicitly in phase 2's tool doc and in the `agents/ari` conventions. |
| **R-prefix** | **Migration prefix `20261020_060000` adopted** (distinct **day** prefix). | §2.6 | The file is `daemon/migrations/versions/20261020_060000_create_board_tables.sql`. A same-hour concurrent commission would collide — a duplicate prefix aborts a **fresh SQLite boot** (the runner does not catch `IntegrityError`) — and two duplicate prefixes already exist in the tree (`20260628_000002`, `20261018_000001`); do not copy the defect. The **developer re-verifies PK uniqueness at write time** (`ls daemon/migrations/versions/ \| grep '^20261020_'` → exactly this one file) **and at merge time**. |
| **R-loader** | **`daemon/loader.py` warm-scan edit — SKIPPED in v1** (deferred). | §3.3 | The optional warm-scan edit (a cold boot can pin an empty category doc into the no-TTL prompt cache) is **not** taken: the lifespan-injected `reports` root is sufficient for correctness, and skipping it keeps the phase-3 diff minimal. Recorded as a **deferred** item (plan-overview §9 — "What was deliberately NOT designed (v2 + deferred)") —
not a defect, not a gap. |
| **R-conventions** | **The `agents/ari` conventions update is STEP 4 OF THIS COMMISSION, before merge + stage — and every prompt-artefact edit is deferred to it.** | reviewer re-gate (C1) + leader ruling | **Not** a phase-5 commission, **not** a post-landing item, **not** a separate later step. The user's commission deliverable 3 ("Ari's agent files") is delivered by this step. **Phases 1–3 are code-only + `meta.json` allow-list edits** ⇒ **no phase carries a re-stamp task**, because **neither `agents/ari/meta.json` nor `agents/leader/meta.json` is manifest-covered** (the committed 161-entry manifest `agents/.prompt-artifacts.sha256` has **zero** `meta.json` rows; neither appears in the 35-entry `SEALED_SHA_BASELINES` tripwire). **But the `.md` prompt artefacts ARE covered** — ari `rule.md` / `soul.md` / `tools_note.md` / `workflow.md` and the leader equivalents (`growth.md` / `rule.md` / `soul.md` / `tools_note.md` / `workflow.md`) — so **any** edit to one of them is a **seal event**, which is exactly why they are all deferred to a **single** step with a **single** in-commit re-stamp pass. **Write lane (binding):** the file tools **HARD-REFUSE** a write to a versioned prompt artefact (`VERSIONED_PROMPT_ARTIFACTS`, `daemon/tools/prompt_artifacts.py:119-127`; fail-closed guards in `daemon/tools/filesystem.py` — `write_file` **and** `edit_file`); the **sanctioned lane** is the **bash/git escape hatch** with a **same-commit manifest re-stamp**; the **prompt-versions API is a runtime DB lane**, not the repo-commit path, and does **not** satisfy the committed manifest seal. Full content spec: [plan-overview.md §8](plan-overview.md). |

**BINDING SEQUENCE for this commission (leader ruling — supersedes any earlier phase-order statement):**

```
P1 (board)  →  P2 (self_alarm)  →  P3 (view_write + board_link)
→  STEP 4: the agents/ari conventions update (prompt content + the ONE in-commit re-stamp pass)
→  merge latest  →  bump  →  stage  →  staged verification
```

- **The conventions step (§8) runs AFTER phase 3 and BEFORE merge + stage**, so its re-stamp covers the post-phase-3 tree exactly once.
- **NO promote.** The live promote is the user's 3-factor nonce ceremony; this commission does not perform it, does not stage a `promote`, and does not touch the live daemon.
- **The re-stamp is ONE pass**: for every covered file the conventions step edits, recompute the sha256 and update its `agents/.prompt-artifacts.sha256` row **in the same commit**; re-pin the `SEALED_SHA_BASELINES` constant **only** for entries it edits **that are currently green** (the **7** pre-existing stale pins are **NOT ours to fix**); acceptance = mismatch-count-before == mismatch-count-after.
- **Write lane**: bash/git escape hatch + same-commit manifest re-stamp. The file tools hard-refuse; the prompt-versions API is a runtime DB lane, not the repo-commit path.

### A-ADJUDICATED-2 — the leader's approver-iteration-002 rulings (BINDING)

Raised by the approver's iteration-002 gate on the planning set (tracking: `/home/nea/ensemble-src/.agents/approver/ari-tools-tracking.md` — iteration 001 = **REJECTED**, 2 blocking / 10 notes; both blocking items were closed in the phase plans in wave 1 — phase-3 **BLOCKER 1** (the runtime body gate) and phase-2 **BLOCKER 2** (the D4 reuse-path double-arm)). These are **binding rulings**, recorded at the same tier as §A1 / §A2 / §A-ADJUDICATED: they close items that were open when this ledger was last written, and each names its owning document.

| # | Ruling | Source | Binding consequence |
|---|---|---|---|
| **B-5** | **The `reports` root stays mintable by a designer through `view_link` — ACCEPTABLE.** | approver iteration-002 note B5 · phase-3 §6 threat model | **No reserved-name guard is added for `reports`.** `agents/designer/meta.json` grants the `view-views` category, so a designer can mint a public-lane passcode for a `reports/**` path — **accepted for v1**. **Rationale:** (i) **the security boundary is the WRITE path** — `reports` content is written **only** by `view_write`, which is ari-only in **three** layers (privileged category + allow-list + the in-body `_is_views_authorized()` gate — phase-3 D3 / BLOCKER 1); minting a passcode grants **read** access on the public lane and never writes ⇒ a designer-mint adds **no write authority**. (ii) **minting IS the public-lane grant** — the `view-views` holders already mint grants for the seeded `designer-artifact` root, and a designer is already permitted to publish under `/views/public/designer-artifact/…`; the surface a designer-mint reaches is a page **ari chose to publish** under `reports` ⇒ a `view_write`-produced `reports` page is **no additional authority**. (iii) The dimension that *would* be dangerous — a page on the host's origin reaching into the viewer's session for **other** apps on that host — is **CLOSED** by **B-7** below (opaque origin ⇒ no host cookies, no `localStorage`, no same-origin reads). **Contrast (deliberate, not an oversight):** the **board**'s link IS kept out of the minting path — by the `board` **RESERVED root name** (phase-3 D10) — because a board passcode holder sees the **WHOLE** board (D11). `reports` pages are per-file published artefacts with no whole-surface disclosure ⇒ the reserved-name rule does not transfer. **Caveat recorded:** if `reports` ever carries sensitive content, the remedy is a reserved-name / per-root mint guard — a phase-3 surface change, **not** a v1 item. |
| **B-7** | **Phase 3 ADOPTS `Content-Security-Policy: sandbox allow-scripts` on the `reports` root — ADOPTED.** | phase-3 §6 + §6.1 (the threat-model paragraph) | **ADJUDICATED: cheap at the existing seam** — ≈10 lines in one file (`daemon/routers/live_views.py`): the two raw-serve sites build their header dicts **inline**, and `root_name` is in scope at the direct lane and one keyword parameter away at the public lane. **Why it matters:** a `/views/public/**` page shares the browser origin with every other content the viewer can reach on that host (the ensemble UI, an OAuth-gated admin surface, another app behind the same proxy hostname), so — with the raw lane shipping **no CSP** — a script in a `view_write` page could read the viewer's cookies / `localStorage` and issue **credentialed** same-origin calls to those other apps' endpoints. The raw lane's exposure is therefore a **host-sharing** exposure, not only a passcode-guessing exposure. **The disposition:** `sandbox allow-scripts` puts the document in an **OPAQUE ORIGIN** (CSP3 §6.3.2 + HTML §7.1.5) while leaving script execution **ON** (`allow-scripts`) and declaring **no** fetch directive ⇒ CDN chart.js / mermaid still load and run, and the **credential** dimension is **CLOSED**. **Scope:** the `reports` root **ONLY** — every other root stays byte-identical, and the board page's strict CSP is explicitly **UNCHANGED**; pin **T-B17** asserts both the presence on `reports` and the ABSENCE elsewhere. **RESIDUAL — recorded explicitly:** (a) the **script-execution** dimension REMAINS (ari can publish a page that runs script in a viewer's browser) — the accepted product dimension; (b) the **behavioural** cost — forms, popups, downloads, modals and **ALL top-level navigation** are **BLOCKED** in report pages (HTML §7.1.5), and same-origin XHR degrades to cross-origin ⇒ **a report page may need a product nod before implementation** if it must navigate / POST / download; (c) rollback is one dict entry at two sites (deleting the helper restores the exact pre-phase header set). |

### A-ADJUDICATED-3 — the reviewer council's implementation-deviation rulings (ACCEPTED)

Raised by the reviewer council's code pass over the **phase-1 implementation** on `feature/ari-tools` (session
`ce047910`, 2026-10-20). These are **ACCEPTED deviations** from the plan text: the plan is **superseded** at the
points named below and the **implementation is the authority**. They are recorded at the same tier as §A1 / §A2 /
§A-ADJUDICATED / §A-ADJUDICATED-2 so the ledger reads as-if-decided-so, and each names the plan anchor it
supersedes.

| # | Deviation | Source | Acceptance rationale (binding consequence) |
|---|---|---|---|
| **RD-1** | **The error hierarchy is DEFINED in `daemon/models/board.py` and RE-EXPORTED by `daemon/services/board_service.py`** — the plan's file map assigned the hierarchy to the **service** (N1 = the model module, N2 = `BoardService` **+ error hierarchy**). | reviewer council session `ce047910` (2026-10-20) · phase-1 plan §3 file map (N1/N2) | **ACCEPTED.** The plan's placement would have **created a circular import**: the service imports the model module, so a hierarchy defined in the service could not be raised by the model's single `_to_utc_iso()` (§6.1) without the model importing back from the service. The **model-local** definition lets that one normalizer raise `BoardValidationError` **directly** — **no per-call-site fix-up** — and the service re-exports all three (`BoardError`, `BoardItemNotFound`, `BoardValidationError`), so **both import paths work** (`from daemon.models.board import …` **and** `from daemon.services.board_service import …`). |
| **RD-2** | **`index=True` is DROPPED from the `project_id` Column.** | reviewer council session `ce047910` (2026-10-20) · phase-1 plan §4.1 (the `project_id` Column sketch) + §4.1 index table | **ACCEPTED.** The plan sketch showed `index=True` **on the Column AND** a named `idx_board_items_project_id` in the six-index set — **internally inconsistent**. `index=True` would emit a **7th auto-named index** (`ix_board_items_project_id`) and break the binding **"exactly six"** index set + the **N7** parity assertions. The implementation keeps the **explicit** `__table_args__` index under the **plan-mandated name** (`idx_board_items_project_id`); live PG shows **exactly 6** `idx_board_*` indexes and **no** `ix_board_items_project_id`. The plan's §4.1 sketch and its §13 D14 row are corrected to that same shape. |

---

## Section B — DESIGN DECISIONS MADE DURING PLANNING

**50 numbered decisions** across the three phase plans: **P1** 17 (`phase1-plan.md` §13), **P2** 13
(`phase2-plan.md` D1–D13), **P3** 20 (`phase3-plan.md` §11). Compiled and de-duplicated below; the phase plans
remain the canonical bodies.

### B0 — The ten highest-consequence decisions (with rationale)

These ten are carried here with a short rationale because they shape the commission's behaviour, not just its
shape. Everything else is a one-liner pointing at its owner.

| # | Decision | Owning | Rationale (summary — full body in the phase plan) |
|---|---|---|---|
| **1** | **The schema trio: tags = junction table, notes = append-only child table, job ids = embedded `JSONBType` list.** | P1-D2, P1-D3, P1-D4 | Each storage shape is chosen by its **access pattern**, not by convenience. Tags are *filtered on* ⇒ relational ⇒ junction table (one identical SQL on both drivers + an index; the embedded-JSON pattern would need PG-native `?`/`@>` vs SQLite `json_extract` — two dialects, two query shapes, no index parity). Notes are *appended to* ⇒ a child table, because appending to a JSON array rewrites the whole array — that is an edit of history, and it loses updates between concurrent appenders. Job ids are **never filtered on** in v1 and the job rows are **soft-deleted** ⇒ embedded JSONB is correct, and a hard FK would either block job deletion or cascade-delete a board item. |
| **2** | **The service is a CLASS (`BoardService`), not module functions.** | P1-D5 | The service has exactly **one** dependency (the `Engine`) and is engine-bound, like `SecretService` (wired at `daemon/manager.py:1381`). The module-singleton pattern exists for `scheduling_service`'s *lazily-configured dependency set* (source repo + registry) — the board has none of that, and a module singleton would invite global mutation. The service also owns a write lock, and a lock on an instance is natural. Unit-testability follows: the tool factory takes the service by injection. |
| **3** | **The ≥30-minute floor is checked PRE-create.** | P2-D9 | The check runs on the wrapper-computed first-fire instant **before any engine call**, so a rejected alarm creates **no row and burns no label**. The alternative ordering (create → validate → cancel) would burn the label every time the floor rejects — and a burnt label is permanent (the engine has no hard delete for scheduler rows). The floor is stated in **minutes** (user-facing policy) but compared in **seconds** against the absolute instant (`first_fire - now >= 1800 s`). |
| **4** | **Label semantics: reuse-in-place; a burned label auto-retries once as `"<label>-2"`.** | P2-D4 | The engine's duplicate label error is opaque to an agent, and its label namespace is **terminal** — a cancelled label is burned forever and `update` refuses cancelled rows. So the wrapper looks the label up **first**: no row ⇒ create; a row in `{running, stopped}` ⇒ **reuse in place** via `update_schedule` (never cancel-to-free — cancel burns the label and hides the row). A `cancelled` label ⇒ auto-retry once as `"<label>-2"` (then `-3`, cap 5) and the **effective** label is echoed. A hard error would deadlock the agent; re-naming is the only escape. |
| **5** | **`instance_mode = "new_instance"`, fixed.** | P2-D6 | The alarm is a *fresh task*, not a continuation — a rehydrated checkpoint would dilute it; a one-shot fires once, so `reuse_instance` buys no continuity; and `reuse_instance` wraps the message in `_format_continuation_message` (run-number text), meaningless for a one-shot. Which instance receives it: a **scheduler-owned** Ari instance (`source_id` = the schedule row id) — **not** any of Ari's chat instances — and the reply is **not** delivered to a chat surface (the scheduler source is one-way). |
| **6** | **Post-fire state is engine-native `stopped`; the wrapper does not cancel.** | P2-D5 | After a one-shot fires, the adapter's completion callback already sets `status='stopped'` + `enabled=False`; the row persists (history preserved) and is **not** cancelled. Cancelling would be worse on every axis: terminal, burns the label, hides the row from the default list, destroys the "it fired" evidence. Clutter control = a **status filter** (`running` = armed). Honest limit, stated in the tool doc: the wrapper **cannot observe the fire** — there is no agent-facing post-fire hook — so "alarm fired" is readable only from the schedule row's status. |
| **7** | **Board `due_at` glue = an agent-prompt convention; creation-time auto-arming deferred to v2.** | P2-D8 | Auto-arming from the board service is not clean against the actual API shape for five concrete reasons (caller-agent concept; label synthesis + the burned-label rule leaking into the board service; `due_at` change ⇒ reschedule, which cannot change the recurrence family and has a documented <1 s missed-fire window; `due_at` removal ⇒ cancel ⇒ terminal + label burn + hidden history; phase 1's board API not frozen ⇒ a merge-order dependency). So the board's `due_at` stays the **source of truth for the deadline** and the alarm is the **reminder mechanism**; the convention lives in `agents/ari/tools_note.md` + the tool's `_full_doc_` + the error hints. Phase 2 therefore depends on phase 1 **only** through the pinned `wi-<uuid8>` id format. |
| **8** | **The `reports` root is LIFESPAN-INJECTED, not operator-declared.** | P3-D1 | Its path derives from the runtime `data_dir`, which a static `config.yaml` cannot express; and an operator `live_views.roots:` block **replaces** the seed dict, so a config-declared root would silently vanish. Injection is idempotent, never overrides an operator-declared `reports` entry, and **fails soft** (a missing `app.state.data_dir` leaves the root absent rather than failing the boot). |
| **9** | **The board's GET is a NEW STATIC route declared BEFORE the public catch-all; its POST verify REUSES the existing catch-all POST unchanged.** | P3-D12 | Starlette is first-match-wins, so the mount order is load-bearing — a board route declared below the catch-all silently 404s. The gate mirrors `_public_resolve` **minus** the file resolution; the whole verify path (per-IP bucket → lock short-circuit → same-host CSRF → constant-time compare → audit row → grant cookie → 303) is inherited wholesale ⇒ **zero new gate code**. A load-bearing accident: the catch-all POST does **not** consult `is_reserved_root_name`, and that is precisely what lets the reserved name `board` work — hence a mandatory regression pin. |
| **10** | **Split security posture: the board page is zero-JS with a strict CSP; `view_write` pages keep the CDN-capable raw-lane posture.** | P3-D14 | `/views` today sends raw files (incl. `.html`) with `nosniff` + `private,max-age=60` and **no CSP** — a deliberate architect ruling ("legitimate HTML may include scripts"), which is why CDN chart.js/mermaid already works and keeps working for `view_write` pages. The **board** is different: it renders agent-authored titles/bodies/notes, so it takes `default-src 'none'; script-src 'none'; nonce'd style; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; no-store`. The strict posture is **free** (the board is a table; it needs no CDN) and means a future escaping bug **cannot execute script**. |

### B1 — Phase 1 (workbook) — complete list — [phase1-plan.md §13](phase1-plan.md)

| Ref | Decision (one line) |
|---|---|
| P1-D1 | One global table set; no `context_key`/tree/project/source partitioning column. |
| P1-D2 | `tags[]` = junction table `board_item_tags` (composite PK `(item_id, tag)`). |
| P1-D3 | `notes` = append-only child table `board_notes` (no `updated_at`/`deleted_at`/`revision` column ⇒ the schema cannot express an edit). |
| P1-D4 | `linked_job_ids` = embedded `JSONBType` list, no FK (no v1 job filter; job rows are soft-deleted). |
| P1-D5 | Service = **class** `BoardService` (one dependency: the `Engine`). |
| P1-D6 | Category `"board"` is **PRIVILEGED** — **entry #9** of `PRIVILEGED_TOOL_CATEGORIES`, from Phase 1's **first** commit (leader ruling **R-C1**, architect §1.3; the phase-1 Q1 is **RESOLVED** — see §C.1). |
| P1-D7 | All timestamps fixed-width UTC ISO-8601, microseconds always on (board-local `_now_iso`) — **deviation** from the house helper (§C.2). |
| P1-D8 | `due_at`: naive input rejected, tz-aware accepted, stored as UTC (no tz-chain in phase 1; phase 2 owns tz resolution). |
| P1-D9 | Status transitions **unrestricted** (any → any, `done` reopenable); the 4-value enum is validated at the service boundary, not at the model. |
| P1-D10 | `priority` = nullable INTEGER 1–5, service-validated, no DB CHECK (no native enums in the ORM). |
| P1-D11 | Registration in **three** registry surfaces: `DYNAMIC_TOOL_NAMES` + `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES` (decorator-only is silently invisible). |
| P1-D12 | Model registration = **late-import in `daemon/manager.py` before `create_all`** (every boot path), not the api.py lifespan (API-server-only). |
| P1-D13 | No `_ensure_postgres_columns` entry (brand-new tables — `create_all` creates them). |
| P1-D14 | `project_id` is a real FK → `projects.project_id` with `ON DELETE SET NULL`. |
| P1-D15 | `"board"` appended at the **END** of both allow arrays (minimal, symmetric, collision-resistant). |
| P1-D16 | No REST surface in phase 1 (tools-only; phase 3 adds the live view). |
| P1-D17 | The board is **not** a replacement for `todo` (todo = ephemeral per-instance; board = durable + global). |

### B2 — Phase 2 (`self_alarm`) — complete list — [phase2-plan.md](phase2-plan.md)

| Ref | Decision (one line) |
|---|---|
| P2-D1 | The wrapper exists; it is **not** a re-implementation of `task_schedule` (the value-add is the horizon gate + the bash-sleep hint, fresh-instance message composition, label-dedupe UX, and board-link injection). |
| P2-D2 | New **module pair** — `daemon/services/self_alarm_service.py` (pure helpers) + `daemon/tools/self_alarm.py` (the LLM-facing tool) — because `CATEGORY_MODULES` is a category→module map and the v2 auto-arm path reuses the pure helpers without importing a tool module. |
| P2-D3 | Small, prefix-disambiguated `when_or_every` grammar (no free-form NL; `every <duration>` is **rejected**, not mistranslated). |
| P2-D4 | Dedupe/reuse by label: **reuse in place**, never cancel-to-free; a burnt label auto-retries once as `"<label>-2"` (see §B0 #4). |
| P2-D5 | One-shot post-fire is **engine-native `stopped`**; the wrapper does not cancel (see §B0 #6). |
| P2-D6 | `instance_mode = "new_instance"`, fixed (see §B0 #5). |
| P2-D7 | Fresh-instance message contract: the wrapper stamps the prefix, injects the context, appends the agent's text **verbatim**; the "fresh instance, no shared state" line is mandatory and the scheduled instant is stated so a late delivery is detectable. |
| P2-D8 | Board `due_at` glue = **prompt convention**; creation-time auto-arm deferred to v2 (see §B0 #7). |
| P2-D9 | The ≥30-minute floor lives in the wrapper, **pre-create**; applies to the **first-fire offset only**; a recurring arm-now-imminent case is **rejected**, not auto-skipped (see §B0 #3). |
| P2-D10 | Ari-only is enforced **thrice**: **privileged category** + **allow-list** **and** runtime `agent_id` gate (leader ruling **R-self-alarm**, L2a). |
| P2-D11 | `project_id` is auto-injected, never optional (without it the fire takes the legacy `_execute_immediate` path — no JobItem, no idempotency key, no queue routing); if it cannot be resolved, the tool errors rather than arming a degraded alarm. |
| P2-D12 | The board id is **validated, not parsed** (`^wi-[0-9a-f]{8}$`), embedded verbatim ⇒ zero runtime dependency on the board service. |
| P2-D13 | Prompt surface = one new `## Self-Alarm` section in `agents/ari/tools_note.md` (agent-POV, no daemon internals) — **authored in the conventions step (§8 / step 4), NOT in phase 2**. `tools_note.md` **IS** seal-covered (committed manifest **row 28** of the 161-entry `agents/.prompt-artifacts.sha256`; the daemon boot drift-check `daemon/tools/prompt_artifacts.py::check_baseline_drift` reports it, and the file tools HARD-REFUSE writes to it) ⇒ **the edit is a seal event**, and it is the **conventions step's in-commit manifest re-stamp** that closes the loop. It is **not** one of the 35-entry `SEALED_SHA_BASELINES` **test-tripwire** entries (that tripwire pins `agents/ari/rule.md` + `agents/ari/workflow.md` + `agents/leader/rule.md`), so **no tripwire re-pin is owed for this file** — the manifest row is the whole obligation. The deferral to step 4 is **deliberate** (leader ruling **R-conventions** / **R-C1**, C8-1): phase 2 edits **no** prompt artefact at all, and the commission produces exactly **ONE** in-commit re-stamp pass. |

### B3 — Phase 3 (live views) — complete list — [phase3-plan.md §11](phase3-plan.md)

| Ref | Decision (one line) |
|---|---|
| P3-D1 | The `reports` root is **lifespan-injected**, never operator-declared (see §B0 #8). |
| P3-D2 | `reports` stores **plain `.html` files**, not a blob+sidecar substrate (the artefact's type *is* its extension; the tmp_images substrate is for opaque binaries and carries a 30-day sweep that would make "published" links vanish). |
| P3-D3 | `view_write` gets a **NEW PRIVILEGED** category `view-write` (ari-only) — ⚠️ **resolved AGAINST the commission's mechanism hint**: a non-privileged category is default-granted to the empty-allow universe, so the binding "ari-only" pre-ruling wins over the "non-privileged is cheaper" hint. |
| P3-D4 | The passcode substrate is **reused, never re-implemented** (same `mint`/`refresh` seam `view_link` uses; same typed rotation-fail envelopes). |
| P3-D5 | `view_write` writes **only** under the daemon-global `reports` root; path validation reuses `is_well_formed_rel_path` so the write path and the serve path share ONE validator, plus a write-time containment check. |
| P3-D6 | The 32 MiB cap is enforced at **WRITE time** using the same `MAX_SERVED_BYTES` constant the serve path uses. |
| P3-D7 | **Overwrite does NOT bump the passcode generation** — a refinement of the pre-ruling suggestion (§C.4). |
| P3-D8 | `refresh=True` bumps the generation (new passcode, outstanding grants revoked). |
| P3-D9 | `title` is required, echoed, **not persisted** and **not injected** into the HTML — the tool never mutates the caller's bytes. |
| P3-D10 | `board` becomes a **RESERVED root name**; it is not a registered root (prevents operator shadowing, 404s the direct lane, keeps the board's link out of the `view-views` minting path). |
| P3-D11 | **ONE** passcode row for the board: `(board, "index.html")`. Filters are query params, **not** part of the key — a passcode authorises "may this person see the board", not a filtered subset. Consequence, stated plainly: a passcode holder sees the **whole** board. |
| P3-D12 | New **static** board GET declared **before** the public catch-all; the POST verify **reuses** the existing catch-all POST unchanged (see §B0 #9). |
| P3-D13 | The board's link is minted by a dedicated `board_link` tool in its **own module + own privileged category** (`board-link`, ari + leader) — §C.4. |
| P3-D14 | The board page is zero-JS with the **strict CSP** posture; CDN scripts remain a `view_write`-page-only capability (see §B0 #10). |
| P3-D15 | `body_md` is rendered as **escaped plain text** (`<pre>`); the notes timeline uses `<details>/<summary>`. No markdown library exists daemon-side ⇒ server-side rendering of agent-authored markdown without a sanitiser would be an XSS hole. |
| P3-D16 | The board read seam is phase 1's `BoardService` via `request.app.state.manager.board_service`, adapted by a **shape-tolerant** phase-3-owned adapter — the adapter is the **only** place that knows phase 1's surface; sync service ⇒ `asyncio.to_thread`; unavailable ⇒ an explicit notice, never a 500. |
| P3-D17 | Two new **public accessors** on `LiveViewsService` (`root_path`, `public_base`) — no new authority, no reserved-name guard bypassed. |
| P3-D18 | **ZERO proxy change** — both new surfaces live under `/views/public/**`, already proxy-exempt in both deployment states; no gate-free route is added; Host preservation untouched. |
| P3-D19 | **No seal re-pin in this phase.** Two baselines cover the prompt artefacts and both are live: the **committed 161-entry manifest** `agents/.prompt-artifacts.sha256` (ari rows `:26`–`:29`; leader rows `:102`–`:106`) and the **35-entry `SEALED_SHA_BASELINES` test tripwire** (`tests/test_chart_image_delivery_e2e.py:1350-1398`). This phase edits `agents/{ari,leader}/meta.json` only — `meta.json` appears **zero** times in the manifest and in neither ari/leader row of the tripwire ⇒ **no re-stamp obligation here**. The **conventions step — step 4 of THIS commission** ([plan-overview.md §8](plan-overview.md)), before merge + stage — owns the **ONE** in-commit re-stamp pass. |
| P3-D20 | The board's direct lane is **intentionally 404** and its "Authenticated URL" line is informational only — stated in the tool output so no reader is misled. |

---

## Section C — DEVIATIONS / REFINEMENTS OF PRE-RULINGS + OPEN ITEMS FOR ARCHITECT

> **This is the section the architect enrichment pass reads first.** C1 is a genuine cross-phase conflict that
> needs a ruling. C2–C4 are refinements of pre-rulings or house conventions. C5 is the consolidated open-question
> ledger. C6 is an operator-facing migration note that must survive into the changelog/runbook.

### C1 — CROSS-PHASE TENSION: privileged vs non-privileged board category — ✅ **RESOLVED** (leader ruling **R-C1**)

> **Status: RESOLVED.** The architect's adjudication ([architecture-recommendation.md](architecture-recommendation.md)
> §1.1–§1.5) was **ACCEPTED as binding** by the leader. The ruling **supersedes** the planner's original
> recommendation ("non-privileged in phase 1, flip in phase 3") and the *"Status: ARCHITECT-TO-RULE"* line this
> section previously carried. The conflict narrative is **retained as the decision record**; every sentence that
> describes the *pre-ruling* state is marked SUPERSEDED, and **no** present-tense claim of a non-privileged
> `board` survives anywhere in the five planning documents.

**The conflict, as it stood (HISTORICAL — SUPERSEDED).** Phase 1 originally chose a **non-privileged** `"board"`
category (P1-D6 as written before the fold), disclosing the consequence as its Q1: a non-privileged category is
**default-granted** to every agent with an empty allow list, so "ari + leader" (L1) is diluted. Phase 3 argued that
restrictive semantics **require** privileged categories (P3-D3, P3-D13): its `view-write` is privileged, and it
split `board-link` into its own privileged category *on the premise that* phase 1's `"board"` was non-privileged
and therefore default-granted. **That premise is GONE** — see the ruling below.

**Side A — phase 1's original position (SUPERSEDED — retained as the decision record).**
- The board is a **benign work surface**: no secrets, no authority, no externally reachable surface. Its content
  is internal work items. *(The architect's answer: a benign surface still must not be **writable by the
  untrusted-input evaluator** — see the ruling.)*
- A non-privileged category avoids the **SAME-PR five-pin ceremony** (`daemon/tools/_tool_registry.py:226-238`) —
  five exact-set pin files that must be updated **in the same commit** as the category addition. *(Superseded: the
  ceremony costs five inserted literal lines — see correction 4 below.)*
- The default-grant consequence was treated as bounded and disclosed. *(Superseded: it is not bounded — the
  inheriting population is unbounded — see correction 3 below.)*
- Phase 1's own recommendation was: **keep non-privileged in v1**; revisit only if the board ever carries
  sensitive content. *(OVERTURNED.)*

**Side B — phase 3's position (privileged — the ruling's content, now the whole answer).**
- `resolve_tool_filter` returns "everything allowed" for an empty allow+deny pair
  (`daemon/tools/instance.py:373-434`), and the default-deny strip removes **only** privileged categories
  (`daemon/tools/instance.py:6243-6271`) ⇒ a non-privileged category **cannot enforce** an "ari + leader" list.
  The pre-ruling would become advisory only.
- The binding pre-ruling wins over the mechanism hint (P3-D3 is explicit: "the binding pre-ruling wins; the
  mechanism hint loses") — so a category whose whole point is *restrictiveness* must be privileged.
- Phase 3 already demonstrates the resolution pattern: **own module + own privileged category**
  (`board-link`), which also removes the phase-1 merge conflict on the `"board"` category entirely.

**Cost of each side, symmetric (as it stood — the ruling's answer is in the two columns' "enforcement" row).**

| | Privileged `"board"` | Non-privileged `"board"` |
|---|---|---|
| **Mechanism cost** | The SAME-PR five-pin ceremony: 5 exact-set pin files updated in the **same commit** as the category addition (`tests/unit/tools/test_upgrade_registration.py`, `tests/unit/tools/test_attestation_registration.py`, `tests/integration/test_maintenancer_spawn_resolves_tools.py`, `tests/integration/test_service_tool_flag_off_byte_identical.py::test_privileged_category_set_is_pinned`, `tests/unit/tools/test_secrets_registration.py:218`). | Zero pin work. |
| **Enforcement cost** | The category becomes **opt-in**: only agents whose allow list names it receive the 4 tools. L1 ("ari + leader") becomes **enforceable**. | The category is **default-granted**: every empty-allow agent inherits the 4 tools. L1 becomes **convention only**. *(Corrected by the architect: the inheriting population is `watcher` + tools-less custom agents — NOT worker/tester/developer/designer, whose allow lists are non-empty.)* |
| **Marginal cost in *this* commission** | **5 file-touches, zero extra lines** — the pins are exact-set assertions with **no `len()` check** anywhere, so each file takes one inserted literal line. Phase 3 no longer "already pays" the cost: the commission now makes **three** pin passes (8 → 9 → 10 → 12), one per phase. | Zero. |
| **Plan amendment cost if flipped** | Phase 1's D6 and Q1, plus the allow-list test's negative case, need a one-line amendment — **done** in this fold (phase 1 §13 D6, §12 Q1, §3.2.1). | — |

**THE RULING (leader ruling R-C1 / architect §1.3).** `"board"` is **PRIVILEGED**, added in **Phase 1's first
commit** — the architect **OVERTURNED** the planner's "flip it in Phase 3" recommendation. The reasoning:

1. The planner's arithmetic ("phase 3 already pays the pin cost ⇒ zero marginal cost there") is arithmetically true
   but answers the wrong question. The pins are cheap in absolute terms, and deferring buys a 5-file saving in
   exchange for shipping a category whose stated audience the mechanism does **not** enforce. A **silent widening**
   is the failure mode this commission is most exposed to, because the board is global by design.
2. Only a privileged category actually enforces L1 ("ari + leader"). The alternative silently widens the grant to
   the empty-allow universe — the opposite of the pre-ruling's intent.
3. Flipping `"board"` needs no new module, no new file, and no change to phase 3's read adapter.
4. The counter-argument (the board's content is internal, so the widened grant is an **authority** leak rather than
   a *secret* leak) is real but **modest and bounded** — whereas the exposure it protects against is *not* modest:
   the inheriting population includes the untrusted-input evaluator. Phase 1's own Low-impact ACL assessment
   (P1-R6) was made on the assumption that the category is opt-in — that assumption is now false.

**The four corrections of architect §1.1** (the architect's evidence base; all independently re-verified against
this worktree at fold time):

| # | Correction |
|---|---|
| **0 — the mechanism is confirmed** | A non-privileged category **cannot** enforce L1: empty allow + empty deny ⇒ everything (`daemon/tools/instance.py:401-406`); `None` ⇒ all-minus-privileged (`:6337-6340`); the default-deny strip removes **only** privileged categories (`:6243-6271`); no `tools` key at all ⇒ the same default-open path (`:6300-6304`). |
| **1 — the blast radius was wrong** | `worker` (21 allow entries), `tester` (22), `developer` (13) and `designer` (17) all carry **non-empty** allow lists ⇒ a non-privileged `board` would have reached **none** of them (`daemon/tools/instance.py:471-478`). |
| **2 — the real exposure is worse in kind, narrower in count** | Exactly **one** in-tree agent carries an empty allow list — **`agents/watcher/meta.json`** ("Security-focused tool call evaluator — intercepts every tool call from a watched instance before execution"), whose context is **attacker-influenced**; under a non-privileged category it would hold **write authority over the global board**. The second population is **unbounded**: `agent_create` writes a `meta.json` with **no `tools` key** (`daemon/tools/agent_mother.py:282-295` ⇒ `instance.py:6300-6304`), and custom agents with an empty allow/deny triple normalise to `tools = None` (`daemon/repositories/custom_agent_configs/repository.py:193-198`). |
| **3 — the "five-pin ceremony" is not a meaningful cost** | All five pins are **exact-set equality** and there is **no `len()` assertion anywhere** (grep: zero hits) ⇒ **one inserted literal line per file**; marginal cost = **5 file-touches, zero extra lines**. |

**Defence in depth is mandatory, not optional (architect §1.3).** Because a **literal tool-name** allow entry
bypasses the category gate (`daemon/tools/instance.py:471-478`), the privileged category alone is insufficient.
All four `board_*` bodies gate on `agent_id ∈ {"ari","leader"}` through **one** module-level helper — there is **no**
existing two-agent-set precedent in `daemon/tools/` (the `blueprint` precedent is single-agent,
`daemon/tools/blueprint.py:71-73`) ⇒ a shared `_is_board_authorized()` is required, not four copies.

**What the ruling does NOT change.** The schema, the four tools, the service, the phase ordering, and the
`board-link` / `view-write` categories are untouched. The **marginal cost is 5 file-touches and zero extra
lines**, and the pins ship **in the same commit** as the category entry ⇒ no red window between phases.

**Consequence for P3-D13 (kept, rationale amended — see §C.4).** `board-link` stays in its own category; the
original privilege-based justification is **GONE** and the decision survives on the two **enduring** reasons:
(a) `view_link` **cannot** mint the reserved name `board`; (b) **audience separation** — ari+leader may **mint**
the link, while the `board` data tools are what the page **reads**.

### C2 — P1-D7: fixed-width microsecond timestamps vs the house `_now_iso()`

- **The divergence.** The house helper (`daemon/repositories/project/models.py:21-30`) omits microseconds when
  they are zero ⇒ **variable width**. The board uses a **board-local** fixed-width helper —
  `datetime.now(timezone.utc).isoformat(timespec="microseconds")` → `2026-10-20T01:55:40.123456+00:00`.
- **Why.** The `due_before` filter compares **TEXT lexicographically**, which is only chronologically correct
  under a fixed-width format. At a second boundary the house format would order `…:40` **after** `…:40.500000`
  (shorter string sorts first) ⇒ a `due_before` filter could include an item that is not yet due.
- **Blast-radius discipline.** The house helper is **not** changed — no blast radius outside the board. The
  consequence is that the board and the house disagree, and **any column added to the board later must use the
  board helper**, not the house one.
- **Architect question.** Accept the local divergence (recommended — it is contained, and normalising the house
  helper would touch every timestamped table in the repo, which is out of this commission's scope), or normalise
  the house helper in a separate, explicitly-scoped change. **Not a blocker for the build.**

**✅ RESOLVED (leader ruling; architect §2.2 S-1) — the local fixed-width helper is ACCEPTED, and the missing half
is ADDED.** The divergence from the house helper stands exactly as designed (a board-local `_now_iso`,
microseconds always on). But the architect found the *other half* of the contract missing, and it is a **blocking
defect**, not a style question: `due_at` is stored fixed-width UTC, while the **`due_before` filter parameter**
accepted any tz-aware ISO string — and a raw agent string must never reach SQL. Left unhandled the filter
**silently inverts**: `'2026-10-20T08:00:00+07:00' < '2026-10-20T01:55:40.123456+00:00'` evaluates **False** although
01:00Z < 01:55Z ⇒ the filter returns the *later* items and drops the *earlier* ones, with no error and no visible
symptom.

⇒ **One `_to_utc_iso()` is used by BOTH the `due_at` write path AND the `due_before` filter** (a naive input is
rejected; the mixed-offset inversion probe and the round-trip are pinned by the phase-1 test N5). Carried as a
**MUST-FIX** implementation constraint in `phase1-plan.md` §6.1, §7.1, §8 (N5), §11 (R11) and §13 (D7/D8); the
phase-1 plan is the canonical home for the body.

### C3 — P2's six refinements of the pre-rulings (no pre-ruling is re-litigated)

| # | Refinement | What changed | Why it is a refinement, not a change |
|---|---|---|---|
| (i) | **Pre-create floor check** (P2-D9) | The ≥30-min floor is checked on the wrapper-computed first fire **before** any engine call. | The pre-ruling fixed the **threshold**, not the check point. Pre-checking means a rejected alarm burns **no** label (create → validate → cancel would burn it — and burnt labels are permanent). |
| (ii) | **Privileged category + allow-list + runtime gate** (P2-D10) | Ari-only is enforced **thrice**: the **privileged category** (entry #10, from phase 2's first commit), the **allow-list**, **and** an in-body `agent_id != "ari"` check. | A **strengthening** — and now a **three-layer** rule (leader ruling **R-self-alarm**, L2a). The allow-list alone is a visibility boundary, not a security boundary: an agent with an empty allow *and* deny list gets everything. The runtime gate is the real boundary (the `blueprint` writer-gate precedent), and the **privileged category** closes the default-grant window (a non-privileged category would be resolved by `watcher`). The third layer is also the only one that closes the **literal-tool-name** bypass (`daemon/tools/instance.py:471-478`). |
| (iii) | **Engine-native `stopped`, not auto-cancel** (P2-D5) | The post-fire action is the engine's own `status='stopped'`. | The pre-ruling allowed **either** mark-done **or** auto-cancel. Cancel is terminal + label-burning + hides the row from the default list + destroys the "it fired" evidence ⇒ mark-done is strictly better on every axis. |
| (iv) | **Prompt-convention glue** (P2-D8) | The board `due_at` → alarm link is a **convention** in `agents/ari/tools_note.md` + the tool doc, not a service coupling. | The pre-ruling did not specify the mechanism. The prompt convention is the **only** option that is clean against the actual API shape (five blockers, §B0 #7) **and** that keeps phase 2 dependent on phase 1 solely through the pinned id format. |
| (v) | **Recurring-imminent rejection** (P2-D9 / OQ-2) | A recurring alarm whose **first** fire is <30 min away is **rejected**, not auto-skipped to the next occurrence. | The pre-ruling fixed the horizon, not the recurrence case. Silently moving the user's stated schedule is worse than an explicit error; the escape hatch is an absolute ISO for the next occurrence. |
| (vi) | **Service/tool module split** (P2-D2) | A **pure** service module (`daemon/services/self_alarm_service.py`) beside the tool module. | An **internal shape choice** — no behavioural consequence. The split exists so the v2 auto-arm path reuses the pure helpers **without importing a tool module**. |

### C4 — P3's refinements

- **P3-D7 — overwrite does NOT bump the passcode generation.** ⚠️ This is an explicit **refinement of the
  pre-ruling's suggestion** ("same path = overwrite + generation bump?"). The passcode authorises *"may this
  person see THIS page"*, and the URL is meant to be **stable** — bumping on every refresh would lock a reader
  out each time Ari updates the report. Generation bumps are reserved for the **explicit** `refresh=True`
  (the lockout-recovery path), which keeps `view_write` consistent with the live board: **content changes,
  passcode stable**. An overwrite is still never silent — it requires an explicit `overwrite=True`, and the
  existing file's bytes are preserved when the flag is absent.
- **P3-D13 — `board_link` gets its own module + its own privileged category.** `daemon/tools/board_link.py`,
  category `board-link` (ari + leader). Three reasons: (1) `view_link` **cannot** mint it — `board` is a reserved
  root name — and `view-views` is reachable by designer; (2) a separate module + category removes the phase-1
  merge conflict entirely; (3) **audience separation** — ari+leader may **mint** the link, while the `board` data
  tools are what the board page **reads** (mint ≠ read).
  **⚠️ RATIONALE AMENDED (leader ruling R-C1 / architect §1.5):** the original second reason — *"phase 1's
  `"board"` category is non-privileged ⇒ default-granted ⇒ a board-link tool placed there would let any
  empty-allow agent mint the internal board's public link"* — is **GONE**: `board` is **PRIVILEGED** from Phase 1's
  first commit (§C.1). The decision survives on the two **enduring** reasons — **audience separation** and
  **reserved-name impossibility** — plus the phase-merge hygiene it cites. The `board-link` category stays
  privileged in its own right: link-minting is a distinct capability from board read/write, and keeping it separate
  keeps the two audiences independent. **Keep the split.**

### C5 — Consolidated open questions (18 source questions → 15 grouped themes)

All three plans' open questions, de-duplicated and grouped by theme. **Source counts:** P1 Q1–Q6 (6),
P2 OQ-1…OQ-6 (6), P3 OQ-1…OQ-6 (6) = **18**.

| Theme | Questions | What is being asked | Status / recommendation |
|---|---|---|---|
| **1. Privilege semantics of the board category** | **P1-Q1 + P3-OQ-2** (2) | Should `"board"` be privileged? Where should `board_link` live — its own category, or inside phase 1's `"board"`? | **✅ RESOLVED (leader ruling R-C1 / architect §1.3, §6 theme 1).** `"board"` is **PRIVILEGED from Phase 1's FIRST commit** (entry #9, five pins in the same commit) and `board-link` **stays separate** on the two enduring reasons — **audience separation** + **reserved-name impossibility** (architect §1.5). See §C.1 and §C.4. |
| **2. Author provenance** | **P1-Q5** (1) | `created_by_instance` / note `author_instance` are stamped by the **service** from the tool closure, so an agent cannot spoof them. Confirm this is the intended contract (vs. an agent-supplied author)? | **✅ RESOLVED — keep service-stamped.** The author is stamped by the **service** from the tool closure; an agent cannot spoof it. It matches the repo's provenance discipline and makes the audit trail trustworthy. |
| **3. List / render ceiling and pagination** | **P1-Q3 + P3-OQ-5** (2) | `board_list` has no pagination cursor; the board page renders ≤50 rows and fetches ≤500 for the filter options. Is 50/500 right? | **✅ RESOLVED — `limit` + truncation metadata in v1, and the render shows "showing N of M" NOW** (one template line, not a deferral) whenever `count > limit`, plus a notice when the filter-option set caps at 500. A pagination **cursor is a v2** item. |
| **4. Item mutation surface — clear-flags and deletion** | **P1-Q2 + P1-Q4** (2) | v1 cannot CLEAR `due_at` / `project_id` / `priority`; and should items be deletable at all? | **⏸ DEFERRED-to-v2.** Keep the bound `board_update` shape; `clear_fields` is a **v1.1 amendment**; any delete must be **soft** — and the child FKs now enforce the soft-delete-only position **at the schema level** (NO ACTION ⇒ the database refuses a hard item delete, architect §2.5). |
| **5. REST twin in phase 1** | **P1-Q6** (1) | Should phase 1 also expose a REST router for the board? | **✅ RESOLVED — no.** The bound phase-1 shape is **tools-only**; phase 3's view calls the service directly. |
| **6. Chat bridge for the alarm's reply** | **P2-OQ-1** (1) | The scheduler source is one-way ⇒ the human sees nothing. Should phase 3 bridge a fired alarm's summary back to the originating chat surface? | **✅ RESOLVED (leader ruling R-chat-bridge / architect §4.2) — no new infra in v1; THE BOARD is the user-visible surface.** There is **no structural path** to a chat surface; the contract promises the board (the fired alarm acts, leaves evidence on the board, and the live view shows the change) and **must NOT** promise a ping, a reminder, or an interruption. |
| **7. Recurring arm-now-imminent** | **P2-OQ-2** (1) | `daily 06:30` armed at 06:15 ⇒ first fire in 15 min ⇒ currently **rejected**. Alternative: auto-skip to the next occurrence. | **✅ RESOLVED — confirm the reject** (see §C.3(v)), with **family-aware** error text that always prints the computed next fire (the bash-sleep hint is wrong advice for a recurring alarm). |
| **8. Interval recurrence / engine change** | **P2-OQ-3** (1) | `every 1h` is rejected because `create_schedule` exposes no interval variant (the adapter has an INTERVAL type; the create path does not). Worth an engine change? | **⏸ DEFERRED-to-v2.** Reject with a hint (`cron */60 * * * *`); a real engine change is a **separate commission**. |
| **9. Lateness-cap deployment policy** | **P2-OQ-4** (1) | Should the deployment set `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`? | **✅ RESOLVED (leader ruling R-lateness / architect §4.1) — default `None`, NO cap in v1.** With a cap set, a missed alarm enters a **5-second daemon-wide re-evaluation loop** (`scheduler.py:559`) with **no self-heal** and **no user-visible signal**; the cap is daemon-global, so the wrapper **cannot override** it ⇒ the wrapper **MUST echo `lateness_cap_seconds`**. Both behaviours + the skip state's signature are documented in the tool doc (risk R3). |
| **10. Auto-arm v2 confirmation** | **P2-OQ-5** (1) | If the board service is to own `due_at → alarm`, it needs a caller-agent concept + a label/recurrence-change policy. Confirm the deferral, or commission a v2 slice? | **✅ RESOLVED — confirm the deferral** (five blockers, §B0 #7). v1 = prompt convention. |
| **11. Alarm scope semantics** | **P2-OQ-6** (1) | Does "ari-only" mean *the alarm is for ari itself* (today), or also *"ari may arm alarms **for** other agents"*? | **✅ RESOLVED — Ari-for-ari only.** "Arm for others" is a different tool and is out of scope. |
| **12. Notes hydration N+1 / the list projection** | **P3-OQ-1** (1) | Phase 1's `list_items` returns items **without** `notes[]`; the board hydrates them per row via `get_item` (bounded N+1, ≤50 reads/render). Can phase 1 include `notes[]` in the list projection cheaply? | **✅ RESOLVED — batch in Phase 1.** `list_items(..., with_notes=False)` + internal batched loaders for notes **and** tags (`WHERE item_id IN (…)`, then group in Python; ≈15–20 lines). The **tool's item shape is unchanged** (no `notes[]` in `board_list`), so the payload and the truncation behaviour are untouched; phase 3 keeps its tolerant "notes unavailable" fallback and works against **both** shapes. `list_items` / `get_item` are **frozen** (names + keyword names) as the binding phase-3 interface. Cost ≤3 queries per render vs 1+50 = 51 (worst case 201). Risk R28. |
| **13. Interstitial copy** | **P3-OQ-3** (1) | The reused interstitial says "view this document" for a board. Accept the cosmetic mismatch, add a board-specific template, or generalise `render_interstitial` with a `subject` kwarg? | **✅ RESOLVED — (a) accept** the shared interstitial; **no** board-specific template (gate-contract risk for zero functional gain). |
| **14. Board layout** | **P3-OQ-4** (1) | Flat filtered table with a `<details>` per row, vs. grouped per-project sections? | **✅ RESOLVED — a flat filtered table with a `<details>` per row** (rendering-only; no schema, service, or route impact). |
| **15. Headline link: public vs direct lane** | **P3-OQ-6** (1) | Should the board page be the public (passcode) link or the direct (OAuth) lane? | **✅ RESOLVED — the public URL is the headline**; the direct lane 404s **by design** (P3-D20) and its "Authenticated URL" line is informational only. |

**Adjudication summary: 13 RESOLVED / 2 DEFERRED-to-v2 / 0 ESCALATED** (15 themes, 18 source questions) —
architect §6. The two deferred themes are **4** (item mutation surface — v1.1/v2) and **8** (interval
recurrence / engine change — a separate commission).

### C6 — Operator migration note: an existing live-views root literally named `board`

**The hazard.** Phase 3 reserves the root name `board` (L6 / P3-D10). An operator who already declares a
`live_views.roots:` entry named `board` will **fail to boot** after the upgrade — the validator refuses reserved
names (the `public` precedent, `config.py:3098-3142`), with a message that names the offending root and suggests a
rename (`config.py:3138-3145`).

**Why this is a real population, not a hypothetical.** The operator's `roots:` block **replaces** the seed dict
(`config.py:2903-2914`) — the same mechanism behind the config-roots-replace footgun (risk R5). An operator
running a custom live-views setup is exactly the population most likely to have named a root `board`, because
"board" is the obvious name for a shared work surface. The two hazards are **coupled**: the replace footgun is
what lets the operator's root exist at all.

**Required mitigations (must survive into the phase-3 changelog + the live-view runbook
`docs/runbooks/live-view-passcode-deployment.md`):**
1. The **migration note** — "an operator with a root literally named `board` must rename it before upgrading"
   (phase-3 §4.1 already carries this line).
2. The boot-refusal **message** must name the offending root and suggest a rename (not a bare refusal).
3. A regression test mirroring the existing `public` reserved-name boot-refusal pin (phase-3 T-B13).

**Architect option (not blocking).** The phase-3 plan chooses **hard boot refusal + actionable message**.
Alternatives — auto-rename, or grandfathering the existing root with a warning — would soften the operator's
experience at the cost of a silent divergence between the operator's named root and the reserved board name.

Recommendation: **keep the hard refusal**; it is the same posture the `public` reservation already takes, and it
fails loudly at boot rather than silently later.


### C7 — Post-adjudication verification flags (fold-time evidence register)

Raised by the phase-plan fold workers while applying the architect's amendments, and checked against **this
worktree** at fold time. **No flag blocks the reviewer** — they are recorded so the approver can see what was
verified, what was corrected, and what was deliberately **not** edited.

| # | Flag | Evidence (this worktree) | Disposition |
|---|---|---|---|
| **F1** | The architect's cited precedent for the **NO-ACTION child FK** — `SecretAuditEvent.secret_id` — carries **no actual FK**: the model declares a plain `Optional[str] = Field(default=None, max_length=64, …)`, and only the *docstring* says "Foreign key to `secrets.id`". | `daemon/repositories/secrets/models.py:335-346` (the field) vs `:289-291` (the docstring claim) | **The ruling STANDS** (child FKs = NO ACTION, architect §2.5). The **precedent is corrected** to the `project_tags` junction — `ProjectTagLink`, `daemon/repositories/project/models.py:146-158` — which really does declare `Field(foreign_key="projects.project_id", primary_key=True)` with **no** `ondelete=` ⇒ the default **NO ACTION**. An **inline caveat** is carried in `phase1-plan.md` §4.2. |
| **F2** | **Anchor drift in the architect's own citations** — corrected at fold time. | `create_all` = `daemon/manager.py:751` (the architect wrote `:750`); `PRAGMA foreign_keys=ON` = `daemon/repositories/factory.py:175` (the pragma block is `:170-177`); the index-emit loop = `sqlalchemy/sql/ddl.py:1023-1025` in SQLAlchemy **2.0.48** (the "does not exist yet" table filter is `:962-964`; the architect's `:1020-1022` is off by three) | **The corrected anchors are the ones carried in the phase plans.** No decision changes; the re-verified anchors are the authority. |
| **F3** | **Pre-existing anchor drift in `phase1-plan.md` §4.4 / D9 / D10** — the "forward-compatible enum tag, frozenset-validated at the service boundary, NOT at the model" precedent is cited as `daemon/repositories/secrets/models.py:44-52`, but that range is the **Secret** model's attribute docstring (`id` / `wrapped_secret` / `public_material` / `retired_at`). The quoted text actually lives at `:59-61` (the `action` enum bullet) and `:293-296` (the `SecretAuditEvent` attribute docstring). | `daemon/repositories/secrets/models.py:44-52` vs `:59-61` / `:293-296` | **✅ RESOLVED at `10dca0921`** (reviewer re-gate pass) — the anchors are now corrected in `phase1-plan.md`: §4.4 cites `daemon/repositories/secrets/models.py:59-61` (the `action` enum bullet) **and** `:293-296` (`SecretAuditEvent.action`), and D9 cites the same pair. The stale `:44-52` citation no longer appears anywhere in the phase plans. The flag was raised at fold time and is now **closed** — no action remains for the phase-1 builder. |
| **F4** | The **five SAME-PR pin files** carry stale **prose** alongside their exact-set assertions — "`PRIVILEGED_TOOL_CATEGORIES` is exactly eight entries" / `test_privileged_categories_is_exactly_eight_entries`. The *assertions* are exact-set and fail loudly on a stale set; only the human-readable text goes stale. | `tests/unit/tools/test_upgrade_registration.py:102-103`; `tests/unit/tools/test_attestation_registration.py:143`; `tests/integration/test_maintenancer_spawn_resolves_tools.py:288,300`; `tests/unit/tools/test_secrets_registration.py:26,202,212` | **Planned as same-commit docstring updates** in the phase that adds the next entry — the docstring is corrected in the same commit as the pin change. A hygiene item, not a defect. |
| **F5** | **Label arithmetic (phase 2)** — the engine caps `label` at **128** characters, and the stored name is the 11-character `self-alarm:` prefix + the input + up to a 2-character `-N` retry suffix ⇒ the **input cap is 115**, not 128. | `daemon/services/scheduling_service.py:131` — `label: str = Field(..., min_length=1, max_length=128)` | **Folded as a new constraint** in `phase2-plan.md` (the wrapper validates **1–115**; a 116-character label gets a typed length error). Recorded here so the cap is traceable to the engine constant rather than to a hand-computed number. |

### C8 — Reviewer re-gate pass (2026-10-19) — consolidation, deferrals and standing notes

Raised by the reviewer's re-gate of the planning set (verdict **REJECT**, 1 CRITICAL + 4 warnings + 7 advisories);
the leader ruled every open item. Recorded here at the same tier as §C1–§C7 — these are **binding**, not comments.
**One iteration-002 row (C8-12) was added on 2026-10-20** (the approver's iteration-002 gate — the two new binding
rulings live in §A-ADJUDICATED-2); the rest of this table is unchanged.

| # | Item | Disposition |
|---|---|---|
| **C8-1** | **Prompt-artefact consolidation (C1①).** Every edit to a manifest-covered prompt artefact is **deferred to the conventions step** — step 4 of this commission, before merge + stage. | **BINDING.** Phases 1–3 are **code-only + `meta.json` allow-list edits**; **no phase carries a re-stamp task** (neither `meta.json` is manifest-covered). The commission produces exactly **ONE** in-commit re-stamp pass, over the files the conventions step edits. Content spec: [plan-overview.md §8](plan-overview.md). |
| **C8-2** | **The two seal baselines are DIFFERENT (C1④).** The **committed 161-entry manifest** `agents/.prompt-artifacts.sha256` and the **35-entry `SEALED_SHA_BASELINES` test tripwire** (`tests/test_chart_image_delivery_e2e.py:1350-1398`) were conflated in the research doc — the "NOT pinned" list was **wrong on 2 of its 5 entries** (`agents/ari/soul.md`, `agents/ari/tools_note.md` — both ARE manifest-covered). | **BINDING — erratum dated 2026-10-19** in [research-db-agents.md §5](research-db-agents.md), with the verified coverage rows and the measured baseline (**7** stale pins at this HEAD; the historical 8 lost `daemon/constants.py`, which a prior commission re-pinned green). Every "not pinned / no seal obligation" claim across the seven planning docs is corrected. |
| **C8-3** | **Write lane (C1③).** | **BINDING.** The file tools **HARD-REFUSE** a write to a versioned prompt artefact (`VERSIONED_PROMPT_ARTIFACTS`, `daemon/tools/prompt_artifacts.py:119-127`; fail-closed guards in `daemon/tools/filesystem.py`) — that is the posture our fencing prescribes. The **sanctioned lane** is the **bash/git escape hatch** with a **same-commit manifest re-stamp**. The **prompt-versions API is a runtime DB lane**, not the repo-commit path. |
| **C8-4** | **A-6 — the optional phase-1 T10 (`## Board` section of `agents/ari/tools_note.md`) is DEFERRED.** | **BINDING.** Originally "optional, deferrable" and owned by phase 1. Under C8-1 it is **deferred to the conventions step**, where the `## Board` section is authored together with the `## Self-Alarm` and `## Live Views` sections and the session open/close procedure — **one** prompt edit, **one** re-stamp pass. `tool_help` + `_full_doc_` already cover discovery, so phase 1 loses nothing. Recorded so the deferral is traceable rather than silent. |
| **C8-5** | **A-7 — STANDING NOTE: the `_strip_privileged_category_tools` docstring.** `daemon/tools/instance.py:6244-6267` enumerates the privileged categories as "today: `system_upgrade`, `system-log`, `ens-db`, `ens-env`" — a **stale 4-entry list**; the live set is **8** at HEAD and becomes **9 / 10 / 12** across this commission's three pin passes. | **BINDING for every phase that touches `daemon/tools/instance.py`** (phases 1, 2 and 3 all do). The stale docstring must be **corrected in the same commit** as the `instance.py` change. Preferred fix: replace the enumeration with a pointer to the registry constant so it **cannot** go stale again; if the enumeration is kept, it must match the post-phase value in that same commit. Carried as a task note in [phase1-plan.md](phase1-plan.md). |
| **C8-6** | **A-3 — phase-3's local risk ids are non-contiguous.** [phase3-plan.md §8](phase3-plan.md) runs **R36–R42, R45, R47** (R43/R44/R46 are phase-2 rows); the consolidated table is [plan-overview.md §6](plan-overview.md) (**R1–R49**). | **NOTED, not a defect.** A phase-3 row id is **not** a plan-overview row id; the two tables are not 1:1 — read the row text, not the id. Pointer added in both places. |
| **C8-7** | **W4 — the ari-only rule is enforced thrice, not twice.** | **BINDING.** **Privileged category** + **allow-list** + **runtime `agent_id` gate** (L2 / L2a / P2-D10 / §C3(ii) / R35 / R12 all now say so). |
| **C8-8** | **W2 — phase 2's privileged set is TEN entries, not nine.** | **BINDING.** The trajectory is **8 → 9 (P1) → 10 (P2) → 12 (P3)**; phase 2's pass lands the set at **10** and its stale "exactly eight entries" test docstrings are corrected to **ten** in the same commit. |
| **C8-9** | **W3 — the seal test is NOT green at HEAD.** | **BINDING.** It carries **7 pre-existing stale pins** that are **NOT ours to fix**. The acceptance form everywhere is **"no NEW stale pins beyond the measured baseline (7); mismatch count before == after"** — never "the seal test is green". |
| **C8-10** | **A-4 / A-5 — two new consolidated risk rows.** | **BINDING.** **R48** — `reports/` growth is **UNBOUNDED** (32 MiB per-page cap only; no retention policy) → [plan-overview.md §6](plan-overview.md) + a runbook note for the phase-3 landing. **R49** — the ≈**30-bit** passcode entropy residual (6 chars over a 31-char alphabet, `daemon/repositories/view_passcodes/models.py:461,464`; 10-strike ceiling at `:470`) → accepted for v1 with the **non-sensitive-content** assumption stated plainly. |
| **C8-11** | **A-2 — the tag input dedupe is a SERVICE-level, order-preserving de-duplication of the incoming tag list.** | **BINDING.** The junction table's composite PK `(item_id, tag)` dedupes **DB rows** only — a repeated tag cannot produce two stored rows — but it does **NOT** dedupe the **input list**: `tags=["a","a"]` reaches the service as two entries, and a naive per-tag INSERT would attempt the same `(item_id, tag)` twice ⇒ an `IntegrityError` on the second insert (a create that looks like an id collision but is not one). The service therefore **de-duplicates the incoming tag list before writing** — **order-preserving, first occurrence wins, case-sensitive** (the tag column is plain TEXT; there is no case folding in v1) — so the write path is idempotent for a repeated tag and the read projection never emits a duplicate tag. The dedupe happens on the **input**, not on the read path. ([phase1-plan.md](phase1-plan.md) §5.2) |
| **C8-12** | **Iteration-002 / BLOCKER 2(b) — the family/DOW mismatch path's documented residual.** The approver-mandated fresh-label route (phase-2 D4 step 2) routes a family / weekly-DOW / cron-expression mismatch **directly to a fresh-label create without calling `update_schedule`**, so the **pre-existing row is left untouched and still armed under its own schedule** ⇒ two reminders can coexist under one agent label. The D4 **step-6** pause — the duplicated-loser fix — applies **only** to the post-update fallback path, where the original had already been mutated; the **pre-route mismatch mutates nothing**, so there is no mutated original to neutralize. | **RECORDED — OPEN for the user-visible disclosure design.** The **agent-facing** half is specced: the response **discloses the untouched pre-existing row** (label + `source_id` + state) beside the newly created one and carries the per-family `fallback_reason` (`recurrence_family_mismatch` / `weekly_dow_mismatch` / `cron_expression_mismatch`); **no schedule is destroyed** — the original is never mutated, never cancelled (phase-2 D4 steps 2 + 7). The **user-visible** half — what the *human* is told, given that the board is the user-visible surface of a fired alarm (leader ruling **R-chat-bridge**) — is **OPEN**, carrying the **same caveat as the phase-2 source row** ("Recorded for approver adjudication"). Cross-refs: phase-2 **Risk 20 (= R49)** (the source row) and plan-overview **§6 R50** (the consolidated mirror — the only new consolidated id minted in iteration 002). **Not a v1 blocker.** |

---

## Amendment log (post-architect 2b297db7d)

The architect's adjudication ([architecture-recommendation.md](architecture-recommendation.md), 541 lines) landed on
this branch at `2b297db7d`; the leader **ACCEPTED every verdict as a binding ruling**. The amendments below are
**folded into the body above** — this ledger reads as-if-decided-so, not as a changelog; the log exists for
traceability only. P0 = correctness (must land before reviewer); P1 = clarity/consistency.

| ID | Pri | Folded into | What changed |
|----|-----|-------------|--------------|
| **A-01** | P0 | §C.1 (heading, status, sides, cost table, ruling) + §B1 (P1-D6) | §C.1 → **RESOLVED** (leader ruling **R-C1**): `"board"` is **PRIVILEGED from Phase 1's FIRST commit** — the planner's "flip in phase 3" is **OVERTURNED**; marginal cost = **5 file-touches, zero extra lines**; the five pins ship **in the same commit**; the four corrections of architect §1.1 are folded in (mechanism confirmed; blast-radius correction — `worker`/`tester`/`developer`/`designer` all carry **non-empty** allow lists; the real exposure — **`watcher`** (the only in-tree empty-allow agent) plus tools-less custom agents; and the "five-pin ceremony is not a meaningful cost" correction) + the mandatory defence-in-depth rule (privileged category **and** the shared `_is_board_authorized()` body gate — a literal tool-name allow entry bypasses the category gate, `instance.py:471-478`) |
| **A-02** | P0 | §C.5 (all 15 theme rows + the summary line) | Ledger adjudicated: **13 RESOLVED / 2 DEFERRED-to-v2 / 0 ESCALATED** (15 themes, 18 source questions) per architect §6. Theme 1 RESOLVED (privileged from P1); themes **4** and **8** DEFERRED-to-v2; the remaining 12 RESOLVED |
| **A-03** | P1 | §C.2 | The board-local fixed-width helper is **ACCEPTED** — and the missing half is added: **one `_to_utc_iso()` on BOTH the `due_at` write path and the `due_before` filter** (a blocking-defect fix — the mixed-offset filter silently inverts; the phase-1 plan carries it as a MUST-FIX in §6.1 / §7.1 / §8 (N5) / §11 (R11) / §13 D7-D8) |
| **A-04** | P1 | §C.4 (P3-D13 rationale) | The "phase-1 `board` is non-privileged" premise is **GONE**; `board-link` survives on the two **enduring** reasons — **audience separation** (mint ≠ read) + **reserved-name impossibility** — plus the phase-merge hygiene |
| **A-05** | P0 | §A2 (new row **L2a**) | `self-alarm` is **PRIVILEGED** (leader ruling **R-self-alarm** / architect §1.4) — **entry #10** from Phase 2's first commit, with **three** enforcement layers (privileged category + allow-list + runtime `agent_id` gate) |
| **ADJ** | — | new **§A-ADJUDICATED** | Six leader rulings recorded as binding ADJUDICATED entries, each citing its architect section: **R-C1** (board privileged from the first commit; trajectory **8 → 9 → 10 → 12**; **THREE** pin passes), **R-self-alarm**, **R-lateness** (default `None`, no cap in v1; the wrapper MUST echo `lateness_cap_seconds`), **R-chat-bridge** (no new infra; **the board is the user-visible surface**; no ping promised), **R-prefix** (`20261020_060000`; re-verify PK uniqueness at write time **and** merge time), **R-loader** (`daemon/loader.py` warm-scan edit **skipped** in v1 — deferred) |
| **F1–F5** | — | new **§C7** (verification-flags register) | Five fold-time verification flags with worktree evidence: **F1** the FK-precedent correction (`SecretAuditEvent.secret_id` carries no actual FK ⇒ corrected precedent = the `project_tags` junction), **F2** anchor drift (create_all `manager.py:751`; pragma `factory.py:175`; `ddl.py:1023-1025`), **F3** flagged-not-edited drift inside `phase1-plan.md`, **F4** stale "exactly eight entries" prose in the five pin files, **F5** the phase-2 label arithmetic (engine cap 128 ⇒ 115-character input cap) |

### Approver iteration 002 amendments (2026-10-20)

The approver's iteration-001 findings (`/home/nea/ensemble-src/.agents/approver/ari-tools-tracking.md` — iteration 001 = **REJECTED**, 2 blocking / 10 notes) were folded into the three phase plans in wave 1 and into **this** file here; the body above reads as-if-designed-so. **The three phase plans were READ-ONLY in this pass.**

| ID | Sev | Folded into | What changed |
|----|-----|-------------|--------------|
| **I2-A5** | P1 | §B2 (**P2-D13**) | The P2-D13 one-liner's **self-contradiction** is gone. It asserted that **no** seal-pinned file would be edited, while naming `tools_note.md` — which **IS** seal-covered (committed manifest row 28 of `agents/.prompt-artifacts.sha256`; the boot drift-check reports it; the file tools HARD-REFUSE writes to it). The corrected one-liner states the truth: `tools_note.md` is **manifest-covered**, the edit is therefore a **seal event**, and it is the **conventions step (§8 / step 4) re-stamp** that closes the loop; the deferral to step 4 is deliberate per **R-conventions** / **R-C1** (C8-1). It also states the **precise** tripwire fact: `tools_note.md` is **not** one of the 35-entry `SEALED_SHA_BASELINES` test-tripwire entries (those pin `agents/ari/rule.md` + `agents/ari/workflow.md` + `agents/leader/rule.md`), so **no tripwire re-pin is owed** for this file — the manifest row is the whole obligation. |
| **I2-B-5** | P1 | **§A-ADJUDICATED-2** (new row) | The **`reports` root is designer-mintable via `view_link` — ACCEPTABLE.** Recorded as a binding ruling with its rationale (the security boundary is the **write** path; minting is the public-lane grant; a designer-mint adds no write authority; the same-origin credential dimension is closed by **B-7**) and the deliberate contrast with the **board** (whose link IS kept out of the minting path by the `board` RESERVED root name — D10 / D11). |
| **I2-B-7** | P0 | **§A-ADJUDICATED-2** (new row) | Phase 3's **ADOPTED** `Content-Security-Policy: sandbox allow-scripts` on the `reports` root is now a **binding ledger entry** — adjudicated cheap at the existing seam (≈10 lines, one file), closing the **same-origin credential** exposure (opaque origin ⇒ no host cookies / `localStorage` / same-origin reads) while keeping CDN scripts working. The **residual is recorded explicitly**: the script-execution dimension remains (accepted product dimension), and **forms / popups / downloads / modals / all top-level navigation are blocked** in report pages ⇒ **a product nod may be needed before implementation**. |
| **I2-C8-12** | P1 | §C8 (new row **C8-12**) | The **BLOCKER 2(b) family/DOW mismatch residual** is on the ledger: the pre-existing row stays **armed** (the step-6 pause fixes only the post-update fallback), the agent-facing disclosure is specced, and the **user-visible** disclosure design is **OPEN** with the same caveat the phase-2 source row carries. Cross-ref: phase-2 **Risk 20 (= R49)** ⇄ plan-overview **§6 R50**. |

### Reviewer council code-pass amendments (2026-10-20)

The reviewer council's code pass over the **phase-1 implementation** on `feature/ari-tools` (session `ce047910`,
2026-10-20) raised **two implementation deviations** from the plan text. Both are **ACCEPTED** and folded into the
body above; the plan is superseded at the points named below and the implementation is the authority.

| ID | Sev | Folded into | What changed |
|----|-----|-------------|--------------|
| **RD-1** | P1 | §A-ADJUDICATED-3 (new row) | The error hierarchy (`BoardError`, `BoardItemNotFound`, `BoardValidationError`) is **defined in `daemon/models/board.py`** and **re-exported by `daemon/services/board_service.py`** — the plan's N1/N2 file map assigned it to the service, which would have **created a circular import** (the service imports the model module) and denied the model's single `_to_utc_iso()` (§6.1) a direct `BoardValidationError` raise (no per-call-site fix-up). **Both import paths work.** |
| **RD-2** | P1 | §A-ADJUDICATED-3 (new row) + phase-1 plan §4.1 + phase-1 plan §13 (D14) | **`index=True` is dropped from the `project_id` Column.** The plan sketch carried the flag AND a named `idx_board_items_project_id` — **internally inconsistent**; the flag would have emitted a **7th auto-named index** (`ix_board_items_project_id`) and broken the binding "exactly six" index set + the N7 parity assertions. The **explicit** `__table_args__` index under the plan-mandated name is the serving index. |

---

## C9 — Phase 2 (`self_alarm`) IMPLEMENTATION rows (2026-10-20)

Phase 2 landed on `feature/ari-tools` (worktree `/home/nea/ensemble-src-wt-ari-tools`, base
`99a28e4f3`). Every deviation from the binding plan ([phase2-plan.md](phase2-plan.md)) is listed
here with its EVIDENCE — the "implement intent, record evidence" path. **Nothing in this list is an
engine change**: `daemon/services/scheduling_service.py` and
`daemon/sources/adapters/scheduler.py` were consumed VERBATIM (`git diff` on both is EMPTY).
**TWELVE** deviation rows are logged here (**P2-DEV-1 … P2-DEV-12**) — the count was **ELEVEN**
until the review advisory **A1** fix added **P2-DEV-12** (the stored-agent ownership row) in
`5782434f5`; the §C9.2 heading carries the same count.

### C9.1 — T1 engine invariants, re-verified in THIS checkout

| # | Invariant | Citation (this checkout) | Verdict |
|---|---|---|---|
| (a) | A one-shot completion ⇒ `status='stopped'` **and** `enabled=False` (the row persists; it is NOT cancelled) | `daemon/sources/registry.py:514-526` — `on_complete_callback` writes `update_source_status(source_id, "stopped")` + `update_source_config(source_id, enabled=False)` | CONFIRMED |
| (b) | A cancelled label is permanently burned | `daemon/repositories/source/repository.py:212-216` — the name lookup filters ONLY on `name` and returns the first match (no status filter, no hard-delete seam on the scheduling surface); `daemon/services/scheduling_service.py:923-926` — `update_schedule` refuses a cancelled row | CONFIRMED (with the precision in **P2-DEV-11**) |
| (c) | `project_id` gates the job-queue route | `daemon/sources/adapters/scheduler.py:998` — `if trigger_type == "scheduled" and self._project_id:` ⇒ the fire takes `_execute_immediate` otherwise (no JobItem, no idempotency key, no retry) | CONFIRMED |

### C9.2 — Deviation rows (each with evidence + disposition) — TWELVE rows (P2-DEV-1 … P2-DEV-12)

| ID | Plan clause | Deviation | Evidence | Disposition |
|----|-------------|-----------|----------|-------------|
| **P2-DEV-1** | D4 step 4b — "a `stopped` row … must be re-armed with a **strictly later instant** (for a recurring row: the requested time/expression must yield a next occurrence later than the stored one)" | The strictly-later rule is implemented for the **ONE-SHOT family only**; a recurring stored row is not constrained by it. | The rule's stated purpose (R34 — "silently never delivered") needs the `scheduler:{source_id}:{run_at}` idempotency key — and the engine emits that key for **one-time schedules ONLY**: `daemon/sources/adapters/scheduler.py:1090-1094` gates it on `self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME` ("Recurring fires intentionally mint fresh JobItems — emitting the key unconditionally would collapse all future cron fires"). The plan's OWN engine-facts table says the same ("One-shot idempotency key = `scheduler:{source_id}:{run_at}` (**one-time only**)"). Applying the rule to a recurring row would also make the pause/resume flow impossible: for a paused `daily 06:30` the stored next occurrence EQUALS the requested one ⇒ the agent could never re-arm it at the same time. | **IMPLEMENTED as the one-shot-only reading**; the recurring case is unconstrained. The engine was NOT edited. |
| **P2-DEV-2** | D4 order — step 3 = the floor, step 4 = the update | On the REUSE path the strictly-later check (a pure, read-only comparison) runs **BEFORE** the floor. | The plan's own test bullet requires a **message-only re-arm** to be refused with the typed error "a fired alarm must be re-armed with a new, later time". A fired once-shot's stored instant is in the PAST, so the floor would reject the same instant FIRST with the horizon text instead. Both checks are read-only and both run **before any engine call** ⇒ "steps 1-3 never mutate the stored row" still holds. | **IMPLEMENTED**; the ordering is recorded here because it changes WHICH typed error the agent sees. |
| **P2-DEV-3** | D3 grammar — the table lists `1d30m`; the regex sketch `^(?:in\s+)?(\d+(?:\.\d+)?)\s*(s\|sec\|m\|min\|h\|hr\|d\|day)s?$` matches a SINGLE segment | Compound relative durations are accepted (the segments are summed: `1d30m` = 88200 s). | The plan's own example (`1d30m`) is unreachable under the plan's own regex ⇒ the EXAMPLE wins over the regex sketch. | **IMPLEMENTED**; the single-segment regex would have rejected a form the plan lists as accepted. |
| **P2-DEV-4** | D7 — the message template carries `Armed at: … \| tz <tz> \| id <source_id>` | On the **create** path the `\| id <source_id>` segment is omitted (the line ends at `\| tz <tz>`); every other line is verbatim. On the **reuse** path the segment IS present (the id is known). | The id does not exist before the create: the row (and its uuid) is minted inside the engine — `daemon/services/scheduling_service.py:747-755` (`repo.create_source_config(...)` generates the id). The plan's own D7 rationale names the **effective label** as the correlation handle for the fresh instance ("The header line carries the effective label … so the fresh instance can correlate it with `task_schedule_list`"). | **IMPLEMENTED**; the id segment is rendered only when the id is known. |
| **P2-DEV-5** | Task 3 — the factory signature `create_self_alarm_tools(service, *, source_repo, current_instance_id, agent_id, agent_tag)` | Two extra KEYWORD-ONLY params: `instance_repository` and `project_repository` (both default `None`). | D11 requires the caller's project from the **instance row** (`manager._instance_repository.get(current_instance_id).project_id` — the `blueprint` auto-inject pattern, `daemon/tools/blueprint.py:61-66`) and the tz chain's user-setting rung needs the project repo (`get_user_timezone_preference(project_repo)`, `daemon/services/user_timezone_utils.py:31`). The plan's signature has NO seam for either ⇒ the plan's own D3/D11 semantics are unreachable without them. | **IMPLEMENTED** as a strict SUPERSET — every parameter the plan names keeps its meaning and position. |
| **P2-DEV-6** | D3 / D9 — "a raw cron with a ≤ 30-min period (e.g. `*/30 * * * *`) is **structurally unarmable** — its first fire is always < 30 min away and the floor rejects it *every* time" | The floor threshold is UNCHANGED (`>= 1800 s`), so a **30-minute**-period cron armed EXACTLY on its period boundary yields exactly 1800 s and is **accepted**; at every other instant it is rejected. A period STRICTLY below 30 minutes is rejected unconditionally. | The plan's own floor rule is `first_fire - now >= 1800 s` and its own boundary table says "30 min ⇒ accept" ⇒ "every time" is a knife-edge overstatement for the exactly-30-minute period. | **NO change to the threshold**; the test pins BOTH facts (a non-boundary `*/30` arm is rejected; a `*/15` arm is rejected at every minute of the hour). |
| **P2-DEV-7** | D4 step 2 — the `fallback_reason` vocabulary `recurrence_family_mismatch \| weekly_dow_mismatch \| cron_expression_mismatch` | Two extra reason values: `label_burned_cancelled` (the plan's step-2 table routes a `cancelled` row to a fresh-label candidate but names no reason for it) and `label_collision_foreign_source` (a non-scheduler row under our namespaced label). The reuse-fallback path reports `reuse_assertion_failed`. | The name lookup has **no `source_type` filter** (`daemon/repositories/source/repository.py:212-216`) ⇒ a foreign row under the label is a real (if unlikely) collision; the engine can neither update nor cancel it, so it burns the name for us. | **IMPLEMENTED**; the plan's per-family trio is unchanged for the three family cases. |
| **P2-DEV-8** | D10 — "the FIVE exact-set pins" / the file map names only those five | The **sixth** exact-set assertion over the same frozenset moved in the same commit: `tests/unit/test_board_registration.py` (the phase-1 pin) — its expected set gained the tenth entry, its size assertion became `10`, and its "board is the LAST allow entry" convention now allows the phase-2 category to follow it in **ari's** array. | The board test asserts `PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET` and `len(...) == 9` over the SAME constant ⇒ leaving it untouched would ship a RED test (the same-PR rule the plan states for the five). The phase-1 "appended at the END" convention (P1-D15) is necessarily superseded by phase 2's own append. | **IMPLEMENTED** (the only file touched beyond the plan's file map, besides the two new modules + three new test files). |
| **P2-DEV-9** | D4 step 4a — "after a successful create re-read by name and report duplicates if more than one row matches (the lookup returns only the first match)" | The duplicate detection is a re-read by name; a duplicate is reported when the re-read's `source_id` DIFFERS from the created row's id. It cannot enumerate every same-name row. | `get_source_config_by_name` returns `session.exec(stmt).first()` — ONE row, no ordering guarantee, and the repository exposes **no** by-name list method ⇒ a full duplicate scan is not available on the current API. The plan's own text acknowledges "the lookup returns only the first match". | **IMPLEMENTED as far as the API allows**; the duplicate is REPORTED (its id), never cancelled (cancel burns the name and hides the evidence). |
| **P2-DEV-10** | D4 step 4b — "assert the response: `paused is False`, `status != "stopped"`, and `next_run_at_utc` equal to the wrapper-computed first fire" | The `status != "stopped"` check is evaluated against the **ENGINE's echoed** status, which is the PRE-update status unless the adapter start succeeded (then the engine re-reads the row). | `daemon/services/scheduling_service.py:1049-1052` — `status = updated.status`; the row is re-read only `if started`. A re-armed STOPPED row therefore passes the assertion whenever the engine re-read the row (the normal case) and the fallback engages when the rebuild failed (conservative and safe). The plan's own :116-118 contract states the same branch-scoped rule. | **IMPLEMENTED** as the plan states; the two engine branches are pinned by real-service tests. |
| **P2-DEV-11** | Engine facts — "no hard delete exists for scheduler rows ⇒ a cancelled label is burned forever" | Precise statement: the **repository** does expose `delete_source_config` (`daemon/repositories/source/repository.py:311`), but the scheduling **service** and its tools expose NO delete seam (create / cancel / get / list / update only) ⇒ the **agent-facing** namespace stays burned (and a hard delete would destroy the execution history the plan wants preserved). | `daemon/repositories/source/repository.py:311`; the service's public function set. | **RECORDED as a precision correction**; the wrapper's behaviour is unchanged (it never deletes and never cancels the duplicate loser). |
| **P2-DEV-12** | D4 step 2 — the gate table (family + weekly-DOW + cron-expression) has NO stored-AGENT row; review advisory A1 (scoped review of this branch) | The reuse path never checked the stored row's TARGET AGENT: `_read_stored_row` did not read `config["agent"]`, so a FOREIGN row squatting the namespaced label (reachable via the free-form `task_schedule` label, e.g. `label="self-alarm:foo"`, `agent_id="leader"`) was treated as "compatible" and reused via `update_schedule` — Ari's alarm message would fire into ANOTHER agent's instance tree. Fixed: a stored row whose agent is not exactly `"ari"` — missing/`None` INCLUDED, FAIL-CLOSED — routes to a fresh-label create with the typed `fallback_reason` `label_owned_by_other_agent` and the standard untouched-row disclosure; `update_schedule` is NEVER called on it. | `daemon/tools/self_alarm.py` (`_read_stored_row` now captures `config.get("agent")`; the gate's ownership branch beside `label_collision_foreign_source`; `SELF_ALARM_TARGET_AGENT`); the CREATE path already enforced `agent="ari"` (`_create_payload`, I2-B1); the engine REQUIRES `payload.agent` at create (`scheduling_service.py:132`) and never rewrites it on update ⇒ every legit row carries an agent, so fail-closed on a missing one is safe; `tests/unit/tools/test_self_alarm_tools.py` (a squatted `leader` row ⇒ zero `update_schedule` calls + fresh label + disclosure; a missing agent ⇒ the same route; `agent="ari"` ⇒ reuse unchanged — the over-blocking guard). | **IMPLEMENTED** (advisory A1) — the plan-gap is closed as I2-B1 INTENT: "the alarm fires INTO ari" on EVERY path, not only on the wrapper's own creates; the gate table gains the stored-agent row via this record (the plan text itself is unchanged). |

### C9.3 — Rows that are NOT deviations (recorded for the reviewer)

| ID | Clause | What was implemented as written |
|----|-------|-------------------------------|
| **P2-OK-1** | D4 steps 1-7 | The strict order is implemented verbatim: project_id resolve-first (D11) → read-only label probe → compatibility gate → floor (both branches) → update+assert / create → fallback → neutralization (pause) → report. The step-6 pause asserts **`paused is True` ONLY** — never `status == "stopped"` on a pause response (the engine echoes the stale pre-pause status) — and relies on the ENGINE's explicit stopped-status write for the row's terminal state; the ROW's `stopped` state is proven by re-reading the row **in the TESTS**, not by the wrapper. |
| **P2-OK-2** | D9 | The floor text is family-aware and ALWAYS prints the computed next fire; the bash-sleep hint appears for the ONE-SHOT family only; the recurring text offers the FOLLOWING occurrence as the escape hatch. |
| **P2-OK-3** | D10 | Three independent layers: the privileged category (entry **#10** — the set is now ten), the `agents/ari/meta.json` allow entry (ari only — `grep -rl "self-alarm" agents/*/meta.json` ⇒ exactly ONE file), and the in-body `agent_id == "ari"` runtime gate. An **empty-allow** non-ari agent resolves **no** `self_alarm` (the privileged default-deny proof). |
| **P2-OK-4** | D4 namespace | Every stored label is `self-alarm:<label>`; the input is capped at **115** chars (the engine's 128 cap minus the 11-char prefix minus the `-5` suffix); a user-supplied `self-alarm:` prefix is rejected with the "added automatically" hint. |
| **P2-OK-5** | D13 | Phase 2 edited **NO** prompt artefact and carries **NO** re-stamp task: `agents/ari/meta.json` has **zero** rows in the committed 161-entry manifest `agents/.prompt-artifacts.sha256` and is absent from the 35-entry `SEALED_SHA_BASELINES` tripwire ⇒ the allow-list edit carries no seal obligation. The `## Self-Alarm` section of `agents/ari/tools_note.md` stays with the conventions step. |
| **P2-OK-6** | Engine files | `daemon/services/scheduling_service.py` and `daemon/sources/adapters/scheduler.py` are **byte-unchanged** (`git diff` empty for both). Every perceived engine defect became a REPORTED row above, never an edit. |
| **P2-OK-7** | D10 — the five exact-set pins in the SAME commit as the registration | The **exact-set pins were never red between commits**: the frozensets (the literal `"self-alarm"` entry + the size assertion) landed **same-commit** with the registration in `25b38387e`; only the **cosmetic** "nine entries" prose (stale docstring / method names in the pin files) trailed in `5782434f5` and `258aee4d5` — **1–3 commits AFTER** D10's same-commit mandate. No frozenset member and no size assertion moved in that gap ⇒ the pins stayed green throughout. The reviewer adjudicated the sequence legitimate. **RECORDED** (advisory A1) — the mandate was met on the load-bearing half (the exact-set pins); the cosmetic naming half trailed and is closed by this commit. |

### C9.4 — Post-review pin runs (the W1 evidence — 2026-10-20)

The reviewer council's pre-merge warning **W1** was that the integration-marker pin runs executed
during implementation left **no recorded artifact**. The row below is the **fresh re-run evidence**
— both commands verbatim, with their result counts. The marker nuance is load-bearing and
**differs per file**: one file is marker-gated, the other is not. The re-runs were executed at the
reviewed pre-fold HEAD (`258aee4d5`); this fold commit touches **NEITHER** pin file, and the same
counts were **re-confirmed at this commit**.

| ID | Command (verbatim) | Result | Date | HEAD |
|----|--------------------|--------|------|------|
| **P2-PIN-1** | (1) `ENSEMBLE_SELF_ENV=dev POSTGRES_DB=ensemble_dev DAEMON_PORT=8079 .venv/bin/python -m pytest tests/integration/test_service_tool_flag_off_byte_identical.py -m integration -q` · (2) `ENSEMBLE_SELF_ENV=dev POSTGRES_DB=ensemble_dev DAEMON_PORT=8079 .venv/bin/python -m pytest tests/integration/test_maintenancer_spawn_resolves_tools.py -q` | (1) **9 passed, 1 warning in 8.02s** — every case in that file is `@pytest.mark.integration` ⇒ **deselected** by the repo's default `addopts` (`-m 'not integration and not postgres and not slow'`) ⇒ `-m integration` is **REQUIRED** there. · (2) **13 passed, 1 warning in 2.03s** — that file carries **NO** integration marker ⇒ it runs in the **DEFAULT** selection; forcing `-m integration` would **DESELECT** it. | 2026-10-20 | `258aee4d58fb59c9bbce0c7fd70320c3716fefa0` |

Both runs are **green** — the W1 artifact gap is closed by this row.
