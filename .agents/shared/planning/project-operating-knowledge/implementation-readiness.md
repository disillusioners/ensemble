# POK — Implementation-Readiness Note (planner-light)

**Pass:** planner-light verification @ `feature/project-operating-knowledge` 6b571771d (Emperor GO 2026-11-07). Method: all 5 design docs read in full (plan-overview v3.4, architecture-recommendation v3.4, decisions v3.4, discharge-round-1/2); the 3 Emperor carries verified at the cited lines (ER-1.2 = decisions.md:116; ari seam = arch §4.5.1:124; universal read = arch §4.6:130); design code-anchors spot-re-verified at THIS tree; slug regex independently reproduced (8/8 parse as §11 claims: `pipeline`×3 + `deploy`×2 families, 3 unordered incl. `model-catalogs-19-vs-22-not-drift` correctly NOT matching; all ≤44c). **The design IS the plan — verify, not redesign.**

## 1. Verdicts

| Phase / sub-item | Verdict | Basis |
|---|---|---|
| **1 — core surface** | **READY** | all sub-items READY; 2 minor items (G-2, G-4) + 1 drift constraint (D-1) |
| 1.1 Schema | **READY** | arch §4.1 + A-2/A-7: full column set verbatim (incl. `audience` CHECK, `audience_agents`); `UNIQUE PARTIAL INDEX uq_pok_active_slug (project_id, slug) WHERE status='active'` — dual-active structurally impossible; embeddings companion; two additive `.sql` (one per table, house rule); NO FK → rollback-inert; keying `project_id != SYSTEM_DEFAULT_PROJECT_ID` (A-5, mirrors `is_system_default` `context_messages.py:2248` — anchor verified) |
| 1.2 Tools | **READY** | A-4/A-5/A-9/A-12: `pok_upsert` (any project-linked agent; audience stated at capture, default `operation`; supersede-of-pinned → leader/PM only `pok_pinned_requires_curator`); `pok_curate` leader/PM-only (pinned/disputed/archived/audience/audience_agents); `disputed` suppresses injection, stays readable; `pok_query` one-step, server-side scoped, universal (ER-1.4). Category `pok` non-privileged → granted to ALL 46 registered agents (45 explicit `meta.json` grants; `watcher` inherits) via B4 sweep; authorization differences are in-body role gates — **this is the "which agents get which tool" answer**. ⚠ D-1 drift below |
| 1.3 Injection | **READY** | A-11 + §4.5/§4.5.1/§4.9: block `## Project Operating Knowledge`; audience filter FIRST → admission within visible set; pinned ∪ family-ordered (drag 2×3=6≤8); once-per-instance flag on success only; stable id `pok:{project_id}`; never-raise (`:5083` swallow — verified); 30d decay `[stale Nd]`; two-layer fence (write-time `pok_body_structural_token` + render-time `escape_for_context_block` `:699` — verified); budget on ESCAPED body ≤240 (W5), block 2,900 wrapper-inclusive (B1). Working-agent sites code-anchored (turn-1 `:2230-2335`, refresh `:2179/:2287/:2328`, landing `instance_messaging.py:4825-4830`). **G-2**: ari seam site is design-level only — minor, see §4 |
| 1.4 Metadata cap | **READY** | §4.8/A-14/A-36: 12 named key families verbatim (membership = the named list + `plugin_*` prefix family — implementable); non-allowlisted 2000c/key + 16384c block + omitted-count tail; NO new read tool; AC6 fail-if-absent/truncated. Renderer target verified: `_format_project_json_section` `context_messages.py:1118`. **G-4**: measurement = AC12 two-basis byte recompute (279,810B / 408,809B live), not an injected-token-delta metric — minor, see §4 |
| **2 — guidelines tier (D3)** | **NOT READY — by design** | D3 PENDING. Only the fold INTENT exists (arch §2 row 11 + §8-iv one-liner; plan §5-2.2). No trust-tier field, no schema/tool-surface delta, no concrete board-item-update semantics. **G-1** — BLOCKER for Phase 2 work; NOT a Phase-1 blocker (Phase 2 is post-dogfood, D3-gated) |
| **3 — seeding + hygiene (D5)** | **PARTIAL** | Seeding: **READY** — arch §11 + plan §4.5: 8 slugs, audience tags frozen, domains, bodies 54–93c leader-curated from the transcript via `pok_upsert` (NOT SQL backfill), pin `pipeline-03` via `pok_curate`, views computed (ops 8 / dev 3 / worker 3), regex re-check reproduced by this pass; the 7-fact binding superset carries the walkthrough substance (digest verification = fact 3 / `pipeline-02`). Hygiene: **NOT READY — by design** (D5 PENDING; **G-3**) |

**Task-phase mapping note:** the commission's "Phase 3 seeding" = design Phase 1 dogfood (§4.5) + Phase 1.5 live seeding; "hygiene" = design §5 item 2.6. Verified per substance.

## 2. Binding constraints the developer MUST carry (traceable)

1. **Schema verbatim §4.1/A-2** — column set incl. `audience` CHECK + `audience_agents`; unique partial active-slug index; NO FK; **two additive `.sql`, one per table, unique prefixes, CHECKs mirrored in both the .sql and the SQLModel table** (house rule: every new table = own file).
2. **PG registration (B7)** — import both models in `daemon/manager.py` pre-create_all block (~:928-987 family, verified present) BEFORE create_all; AC9 = scratch-PG boot shows BOTH tables.
3. **Stage-time rollback adjudication recorded** (standing rule) — seed reasoning: additive-only + no FK + zero old-tree references → rollback-inert (arch §4.1).
4. **Scope predicate (A-5)** — default project: no block + typed refusals both tools (AC10 both directions).
5. **Universal read is an INVARIANT (ER-1.4/C-4)** — `pok_query` NEVER filters by audience; audience gates ACTIVE injection ONLY.
6. **Retrieval = selector reuse (B5/A-12)** — `critical_notes_selector.py` + orchestrator (both verified present), configurable weights; pinned rows bypass threshold; zero POK-invented weights.
7. **⚠ D-1 REGISTRATION DRIFT (this pass)** — the design's `VOCABULARY_VERSION 6→7` / `artifact 60→61` are STALE at this tree: `prompt-reload` (9fbb3ad46) consumed both (verified: `checkpoint_metrics.py:249` = 7; tool-categories.json = 61/7). **POK bumps 61→62 and 7→8; re-derive mechanically at implementation time.** `pok` verified absent (free). House generator ONLY for tool-categories.json (F-1 trap class); NO `TOOL_REQUIRED_AGENTS` entry.
8. **B4 sweep verbatim (§7/A-16)** — 46-agent denominator (45 explicit + watcher inheritance) re-derived mechanically; watcher excluded (empty `allow` [] verified — inherits, never append); `"pok"` inserted BEFORE `"nest-report"` (LAST) in the 45 non-empty allows; HEAD exceptions (ari insert-before, memoir last, canary append-end); `.prompt-artifacts.sha256` re-stamp SAME commit; re-pins `tests/unit/metrics/test_metrics_vocabulary_amendment.py` + `tests/unit/tools/test_tool_category_artifact.py` (both verified present) + grant tripwire `tests/unit/test_anomaly_report_grant_pins.py` (verified present).
9. **Per-view admission (§4.5.1/A-11)** — audience filter FIRST; per-view budget 8/2,900 wrapper-inclusive (47c wrapper, `:75-76` verified); F3 audience-invisible = excluded-for-view (survivors contiguous; zero survivors → no render); F5 rider; families atomic; caps 256-active/2-pin **PER PROJECT not per audience** (ER-1 consistency note) [active cap 256 — Emperor directive 2026-11-10].
10. **Audience defaults = design-time constants in code (§4.5.1 + plan §8)** — operation→{ari,leader,devops}; implementation→{leader,developer}; both→all; `audience_agents` REPLACES the default set when present; mapping by factory-captured `agent_id`; attribution ≠ authorization.
11. **Ari seam gates (ER-1.3/§4.5.1)** — operation-audience view per dispatch (fresh read); same budget/fence/family; no block on the default project (AC24 negative leg).
12. **Fence (A-29)** — write-time: newline / leading `#` / `[SYSTEM CONTEXT:` / ``` → typed refusal; render-time: fixed list-item template + `escape_for_context_block` + newline flattening; bodies single-paragraph fenced-verbatim.
13. **Metadata companion UNAFFECTED by ER-1 (§4.8)** — allowlist 12 families verbatim incl. `ari_plane_tracking_policy` … `staged_release_v0290_ceremony_notes`; caps + omitted-count tail; no new read tool.
14. **Dogfood (plan §4.5)** — all slugs via `pok_upsert` (`provenance=observed`), leader-curated from the transcript, fenced dev daemon; slugs freeze AFTER dogfood; AC1–AC24 legs (AC22 audience views, AC23 universal read, AC24 ari seam).
15. **Live boundary** — dev-true until Phase 1.5 (leader/devops; gated on next promote carrying the schema); AC21.
16. **Gates** — packs + Tier-2 whole-tree dual-side (mechanical, no Tier-1 leg); net-new deterministic reds = 0.

## 3. Phase→task map (order)

| # | Task | Target (design-named) |
|---|---|---|
| 1 | Migration | two additive `.sql` in `daemon/migrations/versions/` |
| 2 | Models + repo + service | SQLModel pair; selector reuse (`critical_notes_selector.py` + orchestrator); embeddings infra |
| 3 | PG registration | `daemon/manager.py` pre-create_all import block |
| 4 | Tools | `pok_upsert`/`pok_query`/`pok_curate`; two-layer gate (category + in-body factory-captured `agent_id`); role gates leader/PM |
| 5 | Registration ceremony | house generator (61→62); `VOCABULARY_VERSION` (7→8, D-1); B4 sweep 45×meta.json (+ watcher inheritance); baseline re-stamp; re-pins + tripwire |
| 6 | Injection builder + sites | `context_messages.py` builder/kind/`__all__`/code-site insert; fence `:699`; once-flag landing `instance_messaging.py:4825-4830` |
| 7 | Ari seam (G-2 discovery first) | Ari dispatch/work-context assembly site + §4.5.1 gates |
| 8 | Metadata allowlist | `_format_project_json_section` (`:1118`) |
| 9 | P-1 | dev llmproxy project row (`project_create`) |
| 10 | Dogfood | 8 slugs + classifications + pin `pipeline-03` (plan §4.5 table) |
| 11 | Gates | packs; Tier-2 dual-side; ACs |

Then: **Phase 1.5** (leader/devops): live row → live 8-slug seeding → fresh leader spawn + ari-dispatch verification (AC21/AC24). **Phase 2** blocked on G-1 (D3 un-park). **Phase 3 hygiene** blocked on G-3 (D5 brief).

## 4. Genuine gaps → BLOCKER items for the leader (not redesigned around)

- **G-1 (Phase 2 — D3):** zero mechanism content for the guidelines trust tier — no trust-tier field, no schema/tool-surface statement, no concrete "update parked board item wi-d2abe4f7" semantics. Should have been in: **arch §8-iv expansion (the D3 un-park round)**. Un-park D3 with content before Phase 2 work.
- **G-2 (Phase 1.3 — MINOR, bounded discovery):** the ari seam is specified "Seam (design level)" with full gates but NO file:line anchor — unlike the ~7 working-agent touch-points (code-sited). Developer must LOCATE "where Ari's dispatch/work context is assembled for a target project" (bounded search: Ari dispatch/wake paths) and pin the site; §4.5.1 gates + AC24 are the acceptance. Should have been in: **arch §4.5.1 ari seam / plan §4.2 file table**. Not phase-blocking.
- **G-3 (Phase 3 hygiene — D5):** design says only "Critical-notes hygiene | D5" (plan §5-2.6); grep-confirmed ZERO content on stale/migratable llmproxy notes or deploy-day corrections (wrong flag name, config.json non-surface, PORT gotcha). Should have been in: **plan §5-2.6 expansion / a D5 lane brief**. Seeding (G-3-free) may proceed.
- **G-4 (Phase 1.4 — MINOR):** measurement is AC12's two-basis byte recompute, not an "actual injected-token delta before/after" metric. If the leader wants the delta framing as the gate, add one line to **§4.8/AC12**. Not blocking as written.

— planner-light pass complete; design set untouched (frozen); this note is the lane's only edit.
