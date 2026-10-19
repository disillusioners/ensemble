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
| **L2** | **`self_alarm` → ari only.** | [phase2-plan.md D10](phase2-plan.md) | `"self-alarm"` goes into `agents/ari/meta.json` **only** — never leader. Enforced twice: the allow-list **and** a runtime `agent_id != "ari"` gate in the tool body (an allow-list is a visibility boundary, not a security boundary). |
| **L3** | **`view_write` → ari only.** | [phase3-plan.md D3](phase3-plan.md) | `"view-write"` is a **privileged** category granted to ari only. Leader gains **no** write authority — the existing `view-views` category (ari + leader + designer) is untouched. |
| **L4** | **Tags storage preference.** | [phase1-plan.md D2, §4.4](phase1-plan.md) | Tags are stored in a shape that supports **relational filtering** (the board filters by tag), not an embedded JSON list. Resolved during planning to a **junction table** `board_item_tags` with composite PK `(item_id, tag)`. The embedded-`JSONBType` pattern is for lists that are **not** relationally filtered. (Refinement of the pre-ruling, not a contradiction of it — §C.2 is the one place where a pre-ruling-adjacent choice genuinely diverged from house convention.) |
| **L5** | **Reports root is daemon-GLOBAL, not per-project.** | [phase3-plan.md D1, D5](phase3-plan.md) | The `reports` root is `<data_dir>/reports`, **daemon-injected** at lifespan — not a project workdir, not operator-declared. A report is not a project artefact and must survive a project's absence. |
| **L6** | **`board` is a reserved root name.** | [phase3-plan.md D10, §4.1](phase3-plan.md) | `board` is **not** a registered live-views root; it is **reserved**. An operator cannot declare a root named `board` (boot refusal), the direct lane `/views/board/index.html` 404s, and `view_link("board", …)` returns the reserved-name envelope — so the board's public link is **not** mintable by the `view-views` holders. |

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
| P1-D6 | Category `"board"` is **NON-privileged** (default-grant consequence disclosed as Q1 — see §C.1). |
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
| P2-D10 | Ari-only is enforced **twice**: allow-list **and** runtime `agent_id` gate. |
| P2-D11 | `project_id` is auto-injected, never optional (without it the fire takes the legacy `_execute_immediate` path — no JobItem, no idempotency key, no queue routing); if it cannot be resolved, the tool errors rather than arming a degraded alarm. |
| P2-D12 | The board id is **validated, not parsed** (`^wi-[0-9a-f]{8}$`), embedded verbatim ⇒ zero runtime dependency on the board service. |
| P2-D13 | Prompt surface = one new `## Self-Alarm` section in `agents/ari/tools_note.md` (agent-POV, no daemon internals); **no** edit to any seal-pinned file. |

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
| P3-D19 | **No seal re-pin in this phase** — the seal covers `.md` artefacts only (`meta.json` appears zero times in it); the phase-5 conventions edit owns the adjudicated re-pin. |
| P3-D20 | The board's direct lane is **intentionally 404** and its "Authenticated URL" line is informational only — stated in the tool output so no reader is misled. |

---

## Section C — DEVIATIONS / REFINEMENTS OF PRE-RULINGS + OPEN ITEMS FOR ARCHITECT

> **This is the section the architect enrichment pass reads first.** C1 is a genuine cross-phase conflict that
> needs a ruling. C2–C4 are refinements of pre-rulings or house conventions. C5 is the consolidated open-question
> ledger. C6 is an operator-facing migration note that must survive into the changelog/runbook.

### C1 — CROSS-PHASE TENSION: privileged vs non-privileged board category — **ARCHITECT-TO-RULE**

**The conflict, stated plainly.** Phase 1 chose a **non-privileged** `"board"` category (P1-D6), disclosing the
consequence as its Q1: a non-privileged category is **default-granted** to every agent with an empty allow list,
so "ari + leader" (L1) is diluted. Phase 3 argues that restrictive semantics **require** privileged categories
(P3-D3, P3-D13): its `view-write` is privileged, and it splits `board-link` into its own privileged category
*precisely because* phase 1's `"board"` is non-privileged and therefore default-granted.

**Side A — phase 1's position (non-privileged).**
- The board is a **benign work surface**: no secrets, no authority, no externally reachable surface. Its content
  is internal work items.
- A non-privileged category avoids the **SAME-PR five-pin ceremony** (`daemon/tools/_tool_registry.py:226-238`) —
  five exact-set pin files that must be updated **in the same commit** as the category addition.
- The default-grant consequence is bounded and disclosed: anyone who can reach an empty-allow agent can read and
  write the global board.
- Phase 1's own recommendation: **keep non-privileged in v1**; revisit only if the board ever carries sensitive
  content.

**Side B — phase 3's position (privileged).**
- `resolve_tool_filter` returns "everything allowed" for an empty allow+deny pair
  (`daemon/tools/instance.py:373-434`), and the default-deny strip removes **only** privileged categories
  (`daemon/tools/instance.py:6243-6271`) ⇒ a non-privileged category **cannot enforce** an "ari + leader" list.
  The pre-ruling would become advisory only.
- The binding pre-ruling wins over the mechanism hint (P3-D3 is explicit: "the binding pre-ruling wins; the
  mechanism hint loses") — so a category whose whole point is *restrictiveness* must be privileged.
- Phase 3 already demonstrates the resolution pattern: **own module + own privileged category**
  (`board-link`), which also removes the phase-1 merge conflict on the `"board"` category entirely.

**Cost of each side, symmetric.**

| | Privileged `"board"` | Non-privileged `"board"` |
|---|---|---|
| **Mechanism cost** | The SAME-PR five-pin ceremony: 5 exact-set pin files updated in the **same commit** as the category addition (`tests/unit/tools/test_upgrade_registration.py`, `tests/unit/tools/test_attestation_registration.py`, `tests/integration/test_maintenancer_spawn_resolves_tools.py`, `tests/integration/test_service_tool_flag_off_byte_identical.py::test_privileged_category_set_is_pinned`, `tests/unit/tools/test_secrets_registration.py:218`). | Zero pin work. |
| **Enforcement cost** | The category becomes **opt-in**: only agents whose allow list names it receive the 4 tools. L1 ("ari + leader") becomes **enforceable**. | The category is **default-granted**: every empty-allow agent (worker, tester, developer, designer, …) inherits the 4 tools. L1 becomes **convention only**. |
| **Marginal cost in *this* commission** | **Zero** — phase 3 already adds two privileged categories (`view-write`, `board-link`) and therefore already pays the five-pin cost **in the same commit**. Adding `"board"` to that same set costs **no additional pin work**. | Zero. |
| **Plan amendment cost if flipped** | Phase 1's D6 and Q1, plus the allow-list test's negative case (which already uses a non-empty allow list), need a one-line amendment. | — |

**Recommendation (evidence-based — the architect rules, not this ledger):** make `"board"` **privileged**, in the
**same commit** that adds `view-write` and `board-link`.
1. The five-pin cost is **already being paid** in that commit ⇒ the marginal cost of privileged is zero, which
   removes the only argument for the non-privileged side (Phase 1's own reasoning for choosing non-privileged was
   that it *avoids* the ceremony).
2. Only a privileged category actually enforces the leader's own pre-ruling L1 ("ari + leader"). The non-privileged
   choice silently widens the grant to the empty-allow universe — the opposite of the pre-ruling's intent, and a
   silent widening is the failure mode this commission is most exposed to (the board is global by design).
3. Phase 3 has already established the module/category separation pattern; flipping `"board"` needs no new
   module, no new file, and no change to phase 3's read adapter.
4. The counter-argument is symmetric and modest: the board's content is internal, so the widened grant is not a
   *secret* leak — it is an **authority** leak (any empty-allow agent can write the global board). Phase 1 itself
   classifies the absence of per-item ACL as a Low-impact risk (P1-R6) — but that risk assessment was made on the
   assumption that the category is opt-in.

**Status: ARCHITECT-TO-RULE.** Until ruled: phase 1's plan (non-privileged) stands as written, and phase 3's
privileged `board-link` / `view-write` categories stand as written. A ruling of "privileged" triggers a small
amendment to `phase1-plan.md` D6/Q1 — it does **not** change the schema, the tools, the service, or the phase
ordering.

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

### C3 — P2's six refinements of the pre-rulings (no pre-ruling is re-litigated)

| # | Refinement | What changed | Why it is a refinement, not a change |
|---|---|---|---|
| (i) | **Pre-create floor check** (P2-D9) | The ≥30-min floor is checked on the wrapper-computed first fire **before** any engine call. | The pre-ruling fixed the **threshold**, not the check point. Pre-checking means a rejected alarm burns **no** label (create → validate → cancel would burn it — and burnt labels are permanent). |
| (ii) | **Allow-list + runtime gate** (P2-D10) | Ari-only is enforced by the allow-list **and** an in-body `agent_id != "ari"` check. | A **strengthening**. The allow-list is a visibility boundary, not a security boundary: an agent with an empty allow *and* deny list gets everything. The runtime gate is the real boundary (the `blueprint` writer-gate precedent). |
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
  root name — and `view-views` is reachable by designer; (2) phase 1's `"board"` category is **non-privileged** ⇒
  default-granted ⇒ a board-link tool placed there would let **any** empty-allow agent mint the internal board's
  public link; (3) a separate module + category removes the phase-1 merge conflict entirely. **This decision is
  entangled with §C1** — if the architect rules `"board"` privileged, `board-link` could in principle fold into it;
  the phase-3 plan's recommendation is to keep them separate regardless (the mint tool and the data tools have
  different audiences: ari+leader may mint the link, but the data tools are what the board page reads).

### C5 — Consolidated open questions (18 source questions → 15 grouped themes)

All three plans' open questions, de-duplicated and grouped by theme. **Source counts:** P1 Q1–Q6 (6),
P2 OQ-1…OQ-6 (6), P3 OQ-1…OQ-6 (6) = **18**.

| Theme | Questions | What is being asked | Status / recommendation |
|---|---|---|---|
| **1. Privilege semantics of the board category** | **P1-Q1 + P3-OQ-2** (2) | Should `"board"` be privileged? Where should `board_link` live — its own category, or inside phase 1's `"board"`? | **MERGED — this is §C1. ARCHITECT-TO-RULE.** Phase 3's answer to the second half is already implemented in its plan (own module + own privileged category, P3-D13). |
| **2. Author provenance** | **P1-Q5** (1) | `created_by_instance` / note `author_instance` are stamped by the **service** from the tool closure, so an agent cannot spoof them. Confirm this is the intended contract (vs. an agent-supplied author)? | Recommended: keep service-stamped. It matches the repo's provenance discipline and makes the audit trail trustworthy. |
| **3. List / render ceiling and pagination** | **P1-Q3 + P3-OQ-5** (2) | `board_list` has no pagination cursor; the board page renders ≤50 rows and fetches ≤500 for the filter options. Is 50/500 right? | Recommended: accept `limit` + truncation metadata in v1; add `offset`/`cursor` when the board exceeds a few hundred items. Phase 3's render ceiling is a **rendering** choice over the same bounded set. |
| **4. Item mutation surface — clear-flags and deletion** | **P1-Q2 + P1-Q4** (2) | v1 cannot CLEAR `due_at` / `project_id` / `priority`; and should items be deletable at all? | Recommended: keep the bound `board_update` shape now (a `clear_fields` param is a v1.1 amendment); if deletion is needed, add a **soft** delete — never a hard one, because the notes audit trail must survive. |
| **5. REST twin in phase 1** | **P1-Q6** (1) | Should phase 1 also expose a REST router for the board? | Recommended: **no** — the bound phase-1 shape is tools-only; phase 3's view calls the service directly. |
| **6. Chat bridge for the alarm's reply** | **P2-OQ-1** (1) | The scheduler source is one-way ⇒ the human sees nothing. Should phase 3 bridge a fired alarm's summary back to the originating chat surface? | A **product** call, not a code call: the receiving instance is scheduler-owned with **no chat context**, so there is no originating surface to bridge to without new ownership plumbing. **Deferred** — deliberately not designed (§8 of the overview). |
| **7. Recurring arm-now-imminent** | **P2-OQ-2** (1) | `daily 06:30` armed at 06:15 ⇒ first fire in 15 min ⇒ currently **rejected**. Alternative: auto-skip to the next occurrence. | Recommended: **confirm reject** (see §C.3(v)). |
| **8. Interval recurrence / engine change** | **P2-OQ-3** (1) | `every 1h` is rejected because `create_schedule` exposes no interval variant (the adapter has an INTERVAL type; the create path does not). Worth an engine change? | Out of phase-2 scope. Rejected with a hint (`cron */60 * * * *`). A real engine change is a separate commission. |
| **9. Lateness-cap deployment policy** | **P2-OQ-4** (1) | Should the deployment set `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`? | **No phase-2 change, documented only.** With the cap set, a missed alarm becomes a permanently-armed skip-loop that never fires without an operator update; without it, a missed alarm fires very late. The trade-off is deployment policy, and it must be documented either way (risk R3). |
| **10. Auto-arm v2 confirmation** | **P2-OQ-5** (1) | If the board service is to own `due_at → alarm`, it needs a caller-agent concept + a label/recurrence-change policy. Confirm the deferral, or commission a v2 slice? | Recommended: **confirm the deferral** (five blockers, §B0 #7). v1 = prompt convention. |
| **11. Alarm scope semantics** | **P2-OQ-6** (1) | Does "ari-only" mean *the alarm is for ari itself* (today), or also *"ari may arm alarms **for** other agents"*? | Recommended: the former. "Arm for others" is a different tool and is out of scope. |
| **12. Notes hydration N+1 / the list projection** | **P3-OQ-1** (1) | Phase 1's `list_items` returns items **without** `notes[]`; the board hydrates them per row via `get_item` (bounded N+1, ≤50 reads/render). Can phase 1 include `notes[]` in the list projection cheaply? | If yes, the N+1 disappears. The phase-3 adapter is **tolerant of both shapes** and degrades per row ("notes unavailable"), so this is an optimisation, not a blocker. Risk R28. |
| **13. Interstitial copy** | **P3-OQ-3** (1) | The reused interstitial says "view this document" for a board. Accept the cosmetic mismatch, add a board-specific template, or generalise `render_interstitial` with a `subject` kwarg? | Recommended: **(a) accept** — the interstitial is shared by four other paths and diverging it adds gate-contract risk for zero functional gain. |
| **14. Board layout** | **P3-OQ-4** (1) | Flat filtered table with a `<details>` per row, vs. grouped per-project sections? | A **rendering-only** change — no schema, no service, no route impact. Flat table recommended. |
| **15. Headline link: public vs direct lane** | **P3-OQ-6** (1) | Should the board page be the public (passcode) link or the direct (OAuth) lane? | The public URL is the headline; the direct lane 404s **by design** (P3-D20) and its "Authenticated URL" line is informational only. |

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
