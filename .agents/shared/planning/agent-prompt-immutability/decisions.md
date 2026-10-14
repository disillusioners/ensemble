# Key Design Decisions: agent-prompt-immutability

Date: 2026-10-13
Worktree: `/home/ensemble-worktrees/agent-prompt-immutability`
Branch: `feature/agent-prompt-immutability` (base `latest @ e9cdc25c6`)
Status: **overnight-conservative-defaults** — every fork ships with a default ALREADY DECIDED, alternatives documented with trade-offs, and a morning-review hook. The user (asleep) reads THIS document first.

Author: planner[v2] via technical-analysis worker
Prior art cited:
- `.agents/shared/planning/agent-prompt-immutability/research-findings-prompt-loading.md` (e)
- `.agents/shared/planning/agent-prompt-immutability/research-findings-inner-soul.md` (e)
- `.agents/shared/planning/agent-prompt-immutability/research-findings-db-api.md` (e)
- `.agents/shared/planning/agent-versioning/` (D15/D16 version-tag invariants)
- `.agents/shared/planning/inner-soul-reform/` (RAG dual-state discipline)
- `.agents/shared/planning/agent-prompt-immutability/phaseA-plan.md` (this same commission)

---

## 0. Mission & Reading Order

The user's directive is binding and explicit: agent prompt `.md` files under `agents/**` become **immutable templates** (never edited at runtime); prompt/persona updates become **DB rows versioned per (agent, artifact)** with **approval states**; effective prompt = `selected > latest_approved > template_file`; `inner_soul` is rewritten to submit DB drafts; existing agents unregressed.

This document is the **fork register + compatibility analysis**. Phases A/B/C hang off it. Section 1 is the fork register (read top-to-bottom, every fork carries a default and a morning-review hook); section 2 is compatibility & migration (proves "no regressions"); section 3 is the risk register; section 4 is open questions for the architect.

---

## 1. Fork Register (F1–F9 leader-specified + D-* surfaced by research)

### F1 — Seeding Strategy

**Question.** When the DB-versioning layer lands, what initial state populates the `prompt_versions` table — and does the on-disk template file stay live as a fallback?

**Decision.** **Overrides-only in DB.** The template file stays live as the fallback. The first DB row for any `(agent_id, version_tag, artifact)` is `version_code = 1` and ships in state `draft` (or `pending_approval` if the agent self-submits). No bulk seeding.

**Rationale.** Full-content seeding (option A below) would create a phantom v1 that *masks* every future template release: once a release ships, the user's auto-approved v1 keeps the old content until they explicitly edit. Hash-guarded lazy-seed (option B) couples the baseline to runtime file state, which can drift across releases — and gives the "first override" a confusing identity (`approved` from a template fingerprint rather than a user-submitted draft). The overrides-only model keeps template files as the canonical baseline so releases upgrade naturally; DB rows exist *only when the user (or an agent on the user's behalf) actually wants to deviate*.

**Alternatives + trade-offs.**

| Option | Mechanism | Pro | Con |
|---|---|---|---|
| **A. Full seed on first boot** | Walk `agents/**.md`, insert each as `state=approved`, content-hashed | Old runtime edits survive as v1; no historical loss | Stale v1 wins over future releases; template updates invisible until user re-approves |
| **B. Hash-guarded lazy seed** | On boot, fingerprint each template; if fingerprint differs from last-seen, auto-supersede | Catches release-time template drift gracefully | Couples baseline to runtime state; an admin editing the template on disk would unknowingly trigger a supersede; identity confusion (`approved` with no submitter) |
| **C. Overrides-only** *(chosen)* | DB rows = user/agent-submitted only | Templates upgrade naturally; first DB row = first real intent | No historical carryover; any past `inner_soul` file edits become invisible (mitigated by `D-SEED-EDGE` below) |

**Morning-review hook.** Confirm the user is OK losing the history of any past `inner_soul`/`agent_modify` runtime file edits — they remain in `git log` and `history/*_soul_change.md`, but won't be carried into DB rows. If they want carryover, switch to option B (lazy seed with release-fingerprint detection) before Phase A merges. *No dissent from planner; this is the cleanest model.*

---

### F2 — Artifact Scope

**Question.** Which files under `agents/**` are governed by immutability + DB versioning?

**Decision.** **Prompt `.md` files only.** Concretely: `{soul.md, rule.md, workflow.md, tools_note.md (+ tools.md fallback, + legacy skill.md)}`. Explicitly **excluded**: `meta.json`, `skill-set.yaml`, `skills-template/*`, `growth.md`, `memories/*.md`, `memory.md`, `_prompt_system/**`. **Fold-in amendment (S5/G-3, leader-accepted)**: `user.md` is no longer excluded — it JOINS the versioned set per phaseB D5 (see the amended `D-ARTIFACT-DISPOSITION` below). See `D-ARTIFACT-DISPOSITION` for the carve-out reasoning on `memory.md` / `growth.md` (the fork most likely to flip in the morning).

**Rationale.** `meta.json` is config (never a prompt section, never in `load_agent_prompts` per `loader.py:418`), and `agent_mother` already refuses it (not in `modifiable_files` at `agent_mother.py:359`). `skill-set.yaml` and `skills-template/*` are owned by the **skill-evolution** subsystem; touching them violates the prior decision in `planning/skill-evolution/` and breaks the skill-keeper write path. `growth.md` is config knobs (parsed by `_load_growth_rules` at `inner_soul.py:1369-1396`); `memories/*.md` is observation index (loader.py:314-360 filenames only); `_prompt_system/**` is shared scaffolding (knowledge, innate-skills, project-experience), not per-agent. Including any of them in scope creeps into other subsystems.

**Alternatives + trade-offs.**
- **A. Include everything under `agents/**`** (broad) — confuses persona state (memories) with templates; breaks skill evolution contract.
- **B. Include `growth.md`** — pointless; it's already config and already parsed.
- **C. Prompt `.md` only** *(chosen)* — clean boundary; one disagreement surface (`memory.md`); cleanly composes with `D-ARTIFACT-DISPOSITION`.

**Morning-review hook.** None — boundary is mechanically derivable from `loader.py:418` `prompt_files` set. Cross-check that no future agent adds a new boot section without updating this fork.

---

### F3 — Version Codes

**Question.** What identifies a single DB-versioned row for a given `(agent, artifact)`?

**Decision.** **Monotonic integer per `(agent_id, version_tag, artifact)`.** `latest_approved = max(version) WHERE state='approved'`. The selection pointer is separate from the version integer (a `version_id TEXT` primary key, monotonic-integer `version` for ordering + lineage).

**Rationale.** Direct precedent: `Skill.generation` at `daemon/repositories/skill/models.py:44-49` with `UniqueConstraint("project_id", "name", "generation")`. Monotonic integers keep the "latest approved" query trivial (`MAX(version) WHERE state='approved'`), let lineage tables use `(parent_version, child_version)` edges, and compose with the F9 identity key. UUIDs would force an extra index lookup for the hot path; UUIDs ARE used as `version_id` for stable external references (audit, REST URLs) but the user-facing "v3, v4, v5…" identity is the integer.

**Alternatives + trade-offs.**
- **A. UUID primary key only** — stable external refs; but "latest approved" needs a secondary index; lineage edges need UUID comparisons.
- **B. Content-hash version code** — version = sha256(content); natural dedup; but loses lineage (two changes with same content collide) and ordering breaks (no monotonic).
- **C. Monotonic integer + UUID `version_id`** *(chosen)* — matches `Skill.generation` precedent; supports both lineage and stable external refs.

**Morning-review hook.** None unless the user wants `version = semver-like` strings instead of monotonic integers (semver complicates `latest_approved` and lineage; recommend reject).

---

### F4 — Approval States & Authority

**Question.** What states does a prompt version go through, and who can transition between them?

**Decision.** **`draft → pending_approval → approved | rejected`.** Agents (via `inner_soul` rewrite or `agent_mother.agent_modify` rewrite) submit drafts and recall drafts/pending versions — **NEVER approve**. The human user (authenticated via OIDC session) is the sole approver. `approved` rows are **append-only** (no further transitions out); `rejected` rows are terminal. Full audit trail (`prompt_audit_events` table) records every transition with `actor_subject` (from `AuthContext.subject`) and `actor_auth_method`.

**Rationale.** Three drivers:

1. **State machine**: direct precedent is `JobStateMachine.VALID_TRANSITIONS` set-of-tuples at `daemon/services/job_state_machine.py:53-62` — formal transitions + `atomic_transition` repo method (job_state_machine.py:29-34) + `InvalidTransitionError`. Re-skin, don't reinvent.
2. **Authority**: human = `auth_method == "session"` per the auth model (db-api findings §3); bearer = machine lane. The R3 source-forgery ticket (critical note 257488dc) means **approval identity MUST be stamped from `AuthContext.subject`, never from request body fields** (jobs_crud.py:595 / messages.py:691). Agents originate work via internal lanes (`internal_agent:` source via `send_message`), not HTTP — they cannot satisfy the "session" check even if they tried.
3. **Append-only approved rows**: matches the `SkillLineage` precedent (composite PK DAG, models.py:13-17) — version history is preserved; rollback means `set_selection` to an older `approved` row, not mutation.

**Alternatives + trade-offs.**
- **A. Agents approve their own drafts** — defeats the entire purpose (agents are the mutation source); audit trail meaningless.
- **B. Multi-approver (e.g. two-person)** — heavy; project has one operator (the user); not justified.
- **C. `draft → pending_approval → approved | rejected` with session-only approver + append-only approved** *(chosen)* — minimal state machine; correct authority model; preserves history.

**Morning-review hook.** Confirm scope of "approver = user only" — does the user want any scoped delegate (e.g. a "governor" agent as second approver)? Default is **no**. *No dissent.*

---

### F5 — Selection Mechanism

**Question.** How does the user choose *which* approved version is currently effective for an `(agent, artifact)`?

**Decision.** **Explicit user-settable pointer per `(agent_id, version_tag, artifact)`.** Implemented as an `is_selected` boolean flag on the `PromptVersion` row, set transactionally (clear-all-then-set-one per key) via the repo's `set_selection(agent_id, version_tag, artifact, version_id, actor)`. The pointer defaults to **no selection** — in which case the F6 precedence falls through to "latest approved" and then "template file".

**Rationale.** Precedent: `Skill.is_active` with transactional toggle (`SkillRepository.get_active_variant` at models.py:93-67). The "no-selection default" matches `Skill` (which has no implicit "active = latest" fallback — skills stay inactive until activated). NULL-key uniqueness **cannot enforce** "one pointer per key" because both SQLite and PostgreSQL treat NULLs as distinct in UNIQUE columns (`skill/models.py:44-49`); the repo enforces it transactionally.

**Alternatives + trade-offs.**
- **A. `is_selected` boolean, transactional toggle** *(chosen)* — matches `Skill` precedent; clean semantics; SELECT-by-key queries are trivial.
- **B. Separate `selection_pointers` table** — `(agent_id, version_tag, artifact) → version_id` row per key; one row per key via UNIQUE — but the same NULL-distinct problem; extra table for marginal clarity.
- **C. Implicit "active = latest approved"** — no user choice; loses the rollback-to-older-approved case the user explicitly listed.

**Morning-review hook.** None — matches `Skill` precedent exactly. Cross-check during Phase A: confirm `set_selection` is wrapped in one transaction that clears all `is_selected=True` for the key before setting the new one (atomicity invariant).

---

### F6 — Precedence Rule

**Question.** When the loader resolves an artifact's effective content, what is the priority order?

**Decision.** **`selected_version.content > latest_approved.content > template_file.content`.** Implemented as a single helper `service.get_effective(agent_id, version_tag, artifact) -> content | None`, called from the loader resolution hook between `load_agent_prompts` and `compose_system_prompt` (between `loader.py:920` and `loader.py:926`). When the helper returns `None`, the hook is a NO-OP and the file-based behavior is preserved byte-for-byte.

**Rationale.** This is the **single rule** the entire architecture hangs on. Three properties must hold:
1. **`draft` and `pending_approval` rows NEVER affect the effective prompt.** A pending change is invisible until approved (otherwise the system is lying about its current state to running instances — destructive).
2. **`rejected` rows NEVER affect the effective prompt.** Same property; rejected is terminal.
3. **No DB rows = template file fallback (provably unregressed).** This is the F1+No-Override No-Regression guarantee the phase A test `test_no_db_rows_yields_identical_prompt_to_pre_phase_a` exists to prove.

The `selected > latest_approved > file` order matches the user's directive ("user-selected > latest-approved > template") and gives operators a fast rollback path (`clear_selection` returns them to "latest approved"; older approved versions stay selectable).

**Alternatives + trade-offs.**
- **A. `selected > latest_approved > template`** *(chosen)* — matches directive; rollback via clear_selection; rejected/draft invisible.
- **B. `latest_approved > selected > template`** — surprising (explicit user selection loses to "newer approved"); defeats the purpose of explicit selection.
- **C. `template > DB overrides`** — defeats the entire feature; template files win, no override ever visible.

**Morning-review hook.** None unless the user wants `draft` rows to be visible (e.g. to a separate "preview" UI) — that requires a separate read API and does not change precedence.

---

### F7 — UI Surface

**Question.** Is the approval workflow UI a mandatory core deliverable, or a follow-up?

**Decision.** **Daemon-side approval REST API = mandatory core** (Phase B). **Minimal Angular frontend page (review/approve list + diff view + approve/reject buttons) = Phase 2, gated** — ship only after the daemon API is stable, gated on a separate user green-light. **No frontend at all in Phase A.**

**Rationale.** Phase A ships the schema + repo + service + resolution hook + guards — pure daemon. Phase B ships the API + state machine endpoints + AuthContext-gated routes (`prompts:approve` scope — see `D-AUTH-OFF` and db-api findings §3). The user CAN approve via `curl` against the API from day one. The frontend is a natural fit (db-api findings §5: same Material components, same service/page pairing, no restructuring), but shipping it before the API stabilizes risks UI-driven schema churn. Phase 2 gate = a deliberate checkpoint.

**Alternatives + trade-offs.**
- **A. Daemon API only, no frontend** *(chosen for Phase A; Phase B equivalent)* — operators use `curl`/Postman; faster to ship; no UI churn.
- **B. Frontend-first** — user can click "approve" immediately; but couples Phase A schema decisions to UI ergonomics; risk of rework.
- **C. Daemon + minimal Angular UI together** *(chosen for Phase 2)* — once API is stable, frontend lands in one vertical slice.

**Morning-review hook.** Confirm the user is OK with `curl`-based approval during the interim (Phase A → Phase 2). If user wants UI on Phase B's day-one ship, switch plan order (UI first, then daemon API). *No dissent; this is a sequencing decision.*

---

### F8 — Immutability Enforcement

**Question.** How do we prevent runtime writes to the versioned-immutable artifacts at `agents/<name>/<artifact>.md`?

**Decision.** **Runtime writes FAIL LOUDLY with a typed error the agent can act on.** The `inner_soul` rewrite removes its file-write surface for the versioned-immutable set entirely. All agent-facing writer sites get a guard:

1. **`filesystem.write_file` / `filesystem.edit_file`** (`filesystem.py:882-905` / `1235-1270`): single guard inside `_resolve_within_workdir` at `filesystem.py:889` — covers BOTH absolute paths (closes the `filesystem.py:205-209` bypass) and relative paths. Refusal shape: `ERROR: REFUSED — {artifact} is an immutable prompt artifact for agent '{name}'; submit a prompt-version draft via the prompt-versions API instead. (file_path=…)`. Agents branch on this; the redirect hint is actionable.

2. **`agent_mother.agent_modify`** (`agent_mother.py:349-393`): shrink `modifiable_files` at `agent_mother.py:359` to ONLY the carved-out artifacts (the decision in `D-ARTIFACT-DISPOSITION`). Existing refusal shape preserved (callers don't see a contract change).

3. **Preserved exceptions** (NOT guarded): `agent_mother.agent_create` (template copy from `_baby_template`), `routers/agents.py` POST `:106-122` (template copy), `agent_mother.agent_delete` + `routers/agents.py` DELETE (move to `_trash`) — these are creation/deletion, not content edits.

4. **`bash` tool — documented accepted-risk** (`D-BASH-ESCAPE` below). No code-level guard possible without OS-level fence.

**Rationale.** Four precedents in priority order:
1. **Best**: `doc_write.py` (`daemon/tools/doc_write.py:1-60`) — path allowlist (`docs/`, `doc/`, top-level `*.md`) + denylist (`.agents/`, `daemon/`, `frontend/`, `node_modules/`) + realpath containment + atomic write + flock + mechanical tool-absence (`doc-maintainer`'s `tools.allow` omits `write_file`/`edit_file` entirely).
2. **`agent_mother` refusals** (`agent_mother.py:295-299` / `:352-356` system-agent protection; `:301`/`:359` fixed allowlists) — already the existing partial-immutability implementation (growth.md/meta.json already read-only there).
3. **WorkspaceGuard + walk-tool hardening** (`filesystem.py:192-244` relative paths; `:247-266` `_resolve_search_root` closed the absolute-path bypass after the 2026-09-23 16GB-walk incident) — direct precedent for closing the `write_file` absolute-path bypass.
4. **Fail-loud refusal convention** — upgrade lane fail-closed gates (env allowlist stripping, argv-only operator flags), RAG tool closed-fail authorization: refuse with a reason token + journal/log line, never silently degrade.

**Alternatives + trade-offs.**
- **A. Silent log + allow** — defeats the purpose; agent never knows the write "failed" in the sense of not persisting.
- **B. Throw exception** — untyped; agents can't reliably branch; harder to redirect to the API.
- **C. Typed error with API-redirect hint** *(chosen)* — agent knows the write was refused, knows WHY, knows the correct alternative (DB draft), can re-attempt with intent.

**Morning-review hook.** None — this is the standard project convention.

---

### F9 — DB Version Identity: `agent_id` vs `(agent_id, version_tag)`

**Question.** *(Planner-surfaced — not in leader F1-F8 list.)* Are DB versions keyed per `agent_id` or per `(agent_id, version_tag)`?

**Decision.** **Keyed by resolved agent identity `(agent_id, version_tag)`.** Tagged sibling copies (`agents/developer[v2]/`, `instances.agent_tag`) MUST NOT have their overrides leak into base `agents/developer/`, and vice versa. `PromptCache` invalidation honors the version-tagged key.

**Rationale.** Verified worktree: `_make_key` at `daemon/loader.py:748-774` already keys the existing `PromptCache` by `{agent_id}[{version_tag}]::{normalized_mcp}` (D15, registered in `.agents/shared/planning/agent-versioning/decisions.md`). The DB row key MUST compose with this: a tagged copy's override must invalidate only the tagged cache entry, not the base. `AgentMetadata.path` (`registry.py:267`) and `instances.agent_dir` (`instance_lifecycle.py:1921`) already carry the version-tagged resolution.

The alternative (agent_id-only with tag folded in) breaks D15: a `developer[v2]` write would invalidate `developer`'s base cache, causing a reload that picks up the wrong template. The composed key matches D16 (resolver invariant from agent-versioning).

**Alternatives + trade-offs.**
- **A. `(agent_id, version_tag)` composite key** *(chosen)* — composes with D15/D16; tagged siblings isolated; matches how the rest of the system already thinks about identity.
- **B. `agent_id`-only, fold tag in at query time** — DB row only has `agent_id`; tag derived at resolve; simpler DB; but invalidation gets ambiguous (a `developer[v2]` write must NOT clear `developer`'s base cache); cache-key extension would need separate state.
- **C. Separate table per `version_tag`** — heavy; one schema per tag in the system; schema management nightmare.

**Morning-review hook.** None — this is mechanical composition with the existing D15 invariant.

---

### D-ARTIFACT-DISPOSITION — Which Artifacts Join the Versioned-Immutable Set

**Question.** *(Research surfaced this fork — not in leader F1-F8 list. Most likely to flip in the morning.)* Beyond F2's `{soul, rule, workflow, tools_note, tools, skill}` baseline, does `memory.md` (boot prompt §8 AND runtime-written by `inner_soul` with compaction) join the set? What about `user.md` / `growth.md`?

**Decision.** **Conservative default: carve `memory.md` + `growth.md` OUT; `user.md` IN per S5 (phaseB D5, leader-accepted).**

| Artifact | Carve decision | Reason |
|---|---|---|
| `soul.md`, `rule.md`, `workflow.md`, `tools_note.md` (+ `tools.md` fallback), `skill.md` (legacy) | **IN** | Prompt sections 1, 2, 7, 6, 3 — boot-loaded, agent-runnable via `inner_soul`/`agent_mother`, immutable baseline |
| `memory.md` | **OUT** (file) | Section 8, BUT runtime-written by `inner_soul` (`inner_soul.py:1107-1162`) with 2000-word cap, 80% compaction, exact-dup skip, atomic flock/rename. Blanket immutability would break `inner_soul` and have no natural DB migration story (memory state ≠ template override) |
| `user.md` | **IN** (DB-versioned per phaseB D5 — S5 amendment, leader-accepted) | NOT prompt-loaded, BUT two-write-path coverage (`agent_mother.agent_modify` :349-393 + inner_soul :1279-1310) requires every versioned artifact to go through DB or `_mother` retains a direct file-write bypass — bypass closure is the regime's point. The operator reads it via `agent_mother.agent_read`; enforcement value, not prompt-visibility. **Behavior change**: `inner_soul target=user` becomes a pending-approval submission. |
| `growth.md` | **OUT** (file) | Config knobs (`_load_growth_rules` `inner_soul.py:1369-1396`); never loaded into boot prompt. Already read-only at `agent_mother.py:359` |
| `meta.json` | **OUT** | Config only; already read-only at `agent_mother.py:359` |
| `memories/*.md` | **OUT** | Index-only in boot prompt §9 (filenames, `loader.py:314-360`); content read on-demand by `access_memory` tool; migration to DB breaks the index-then-fetch pattern that `loader.py:518-520` composes |
| `_prompt_system/**` | **OUT** | Shared scaffolding (critical-notes, knowledge, project-experience, innate-skills), not per-agent |
| `skill-set.yaml`, `skills-template/*` | **OUT** | Owned by skill-evolution subsystem; F2 carves them out |

**Rationale.** Three properties the carve preserves:
1. **`inner_soul` semantics remain backward-compatible for `memory`/`user`/`workflow`** — agents that today use `inner_soul(target="memory", ...)` continue to work; the file-write path for `memory.md` and `user.md` and `workflow.md` is **removed** for `workflow` and `user` (both join the versioned set and become DB drafts — `user.md` per the S5 amendment) but **preserved** for `memory.md`.
2. **Phase A schema is artifact-agnostic** — adding `memory.md` later (carving IN) is a config-table change, not a migration. This is the load-bearing property: if the morning flips this fork, no DB migration needs to be redone.
3. **Memory's "memory state" semantics are preserved** — `memory.md` is observation residue, not template content. Treating it as an immutable template would be a category error.

**Alternatives + trade-offs.**
- **A. Carve everything OUT (current behavior + light guard)** — does nothing useful; leaves `soul.md`/`workflow.md`/`rule.md` mutable.
- **B. Carve everything IN** — breaks `inner_soul`'s `target=memory` path; memory state has no DB model; regression.
- **C. Carve `memory`/`user`/`growth` OUT** *(chosen)* — preserves inner_soul semantics for the runtime-mutable files; focuses the feature on the actually-template-shaped files; Phase A schema stays artifact-agnostic so flips are cheap.

**Morning-review hook.** *This is the highest-likelihood morning flip.* If the user wants `memory.md` joins the versioned set:
1. Phase A's `modifiable_files` shrink in `agent_mother` becomes `{soul, rule, workflow, tools}` — no `memory` removal.
2. `inner_soul` rewrite must add a `target="memory"` → DB draft submission branch (memory state becomes a per-(agent, version_tag, artifact) row in `prompt_versions`).
3. The RAG-redirect layer must stay in front (knowledge content goes to RAG, not to memory DB rows).
4. The `access_memory` tool continues to read `agents/<name>/memory.md` from disk — needs migration to read from DB rows instead (loses loader preview during transition; non-trivial).

If the user wants `user.md` joins: **it now joins BY DEFAULT** (S5 amendment, phaseB D5); the morning flip direction for `user.md` is carve-OUT back to file — same pattern, simpler (no boot prompt section to redirect).

If the user wants `growth.md` joins: probably no — it's pure config.

**Dissent (planner).** The conservative carve is right for Phase A. But there is a subtler alternative the user might prefer: **move ALL of `memory.md`/`user.md`/`growth.md`/`meta.json` to a separate file-immutability model** (read-only at agent-runtime, write-only via `_mother` and explicit human edit), leaving the DB-versioning project to focus narrowly on the prompt-artifact set. That would be cleaner semantically but doubles the surface area of "immutability" — recommend keeping the conservative carve for now, deferring the question to Phase C.

---

### D-AGENT-MOTHER — Second Direct-Write Path

**Question.** *(Research-surfaced.)* `agent_mother.agent_modify` (`agent_mother.py:349-393`) full-replaces `soul/workflow/rule/user/memory/tools .md`. If only `inner_soul` is rewired, `_mother` remains a bypass.

**Decision.** **Rewire `agent_mother.agent_modify` to submit DB drafts for the versioned-immutable set.** The `modifiable_files` allowlist at `agent_mother.py:359` shrinks to the **enumerated residual set `["memory.md"]`** (fold-in S17: `rule.md` + `tools.md` fallback JOIN the versioned set — OQ3 default = join; `user.md` removed per the S5 amendment; `meta.json`/`growth.md` remain refused as today; `_`-agent refuses unchanged). For each allowed artifact in the versioned set, `agent_modify` becomes `service.create_draft(...)` + (optionally) `service.submit_for_approval(...)`. The `agent_create` template copy at `:188-218` remains legal (creation, not content edit).

**Rationale.** Without this rewire, the `_mother` agent — which is the ONLY agent that can modify `rule.md` today (per the no-`inner_soul`-target analysis at inner-soul findings §2(d)) — bypasses the entire approval workflow. `rule.md` is a prompt section. Leaving this hole is incoherent.

**Alternatives + trade-offs.**
- **A. Strip `_mother` write access entirely** — loses `rule.md` modifiability; `_mother` becomes read-only.
- **B. `_mother` becomes draft-submitter** *(chosen)* — keeps `_mother` useful, keeps the approval workflow coherent.
- **C. Move `_mother` writes to a separate "system action" lane with its own audit** — heavy; multiplies approval surfaces.

**Morning-review hook.** None unless the user wants `_mother` to retain raw write access to specific carve-out artifacts (e.g. `meta.json` for ops emergencies). The default already preserves `meta.json` and `growth.md` access.

---

### D-AUTH-OFF — Approval API Posture When Auth Disabled

**Question.** *(Research-surfaced.)* `@require_scope` is inert and `auth_context` absent when auth OFF (`daemon/auth/decorators.py:27-32`; routes not registered when auth OFF — `api.py:3629` / `:3636`). What is the approval endpoint's posture?

**Decision.** **Fail-closed: approval requires auth.** When `auth_config` is absent (`request.app.state.auth_config` not set), the endpoints return **503 `AUTH_NOT_CONFIGURED`** with a clear message ("approvals require authentication; configure auth before submitting or approving prompt versions") — **including reads** (the audit log exposes human principals' subject strings). **Fold-in amendment (S8/G-3)**: the earlier `403 AUTH_DISABLED` wording here was WRONG; phaseB D3 wins — auth-OFF is misconfiguration, not authorization failure (`routers/auth.py:96` `503 LOGIN_NOT_CONFIGURED` precedent). Local-loopback deployments cannot bypass.

**Rationale.** Approving a prompt version is a **mutating privileged action** — it changes the effective prompt for every future spawn of the target agent. Letting an unauthenticated request approve is the worst-case footgun. The precedent for fail-closed: upgrade lane (env allowlist stripping at `daemon/tools/upgrade_journal.py:986-990`), RAG tool closed-fail authorization, decision `crit:open-evaluator-privileges-must-fail-closed`.

**Alternatives + trade-offs.**
- **A. Auth OFF → approvals silently allowed** *(REJECTED)* — silent privilege grant; localhost deployments become attack surfaces.
- **B. Auth OFF → fail-closed** *(chosen)* — refuses approvals; forces operator to configure auth.
- **C. Auth OFF → local-operator-as-approver via `loopback` subnet check** — clever; but introduces a network-stack dependency; violates "no new secret-shaped configuration" principle; hard to test.

**Morning-review hook.** None — fail-closed is the project convention for privileged actions. If the user explicitly wants local-loopback approvals, we can re-open this with a clearer threat model.

---

### D-BASH-ESCAPE — `bash` Tool Cannot Be Code-Level Guarded

**Question.** *(Research-surfaced.)* `bash("echo > agents/ari/soul.md")` (or heredoc, or `tee`, or any shell command) cannot be stopped by code-level path checks.

**Decision.** **Documented accepted-risk.** No code-level guard. The protection layers are: (1) the `inner_soul` rewrite REMOVES the file-write surface for the versioned-immutable set — the legitimate way for an agent to update its persona becomes DB-draft-submission, not file-editing; (2) drift-check at promote-time surfaces any unintended file changes (the existing promote integrity check, post-phase A, can be extended to flag changes to `agents/<name>/<versioned-set>.md`); (3) `agent_mother` can `audit agents/` (read-only) to detect post-hoc drift. **OS-level fence** (chmod `-w`, read-only release tree, immutable attribute) is documented as the deployment-time hardening option, NOT a code-level concern.

**Rationale.** No code-level guard can reliably stop a shell that has `tee`/`dd`/`cp`/`mv`/`>`/`>>`/heredoc/encoded-payload tricks. Adding guard complexity here is theater. The right place to enforce is at the legitimate-write-site layer (`inner_soul` rewrite) and the drift-detection layer (promote integrity + audit). This matches the test-fencing precedent from the 2026-10-12 live-deletion incidents (critical notes).

**Alternatives + trade-offs.**
- **A. Code-level guard in bash tool** — impossible; rejects all `>` anywhere; false positives everywhere.
- **B. OS-level fence (chmod / read-only mount)** — strongest; deployment-time decision; out of scope for code changes.
- **C. Documented accepted-risk + drift detection + legitimate-write-site removal** *(chosen)* — focuses defense on the correct layer.

**Morning-review hook.** None — the user already ratified the "no bash fence" stance in the test-fencing critical notes. Cross-check during Phase A: confirm `inner_soul`'s file-write surface for `soul.md`/`workflow.md`/`rule.md`/`tools_note.md` is fully removed (not just the public method — verify the `write_text`/`atomic_write` calls at `:1084/:1158/:1237/:1295/:1327` are gone for those targets).

---

### D-WATCHER-CACHES — Two Process-Global Never-Invalidated Caches

**Question.** *(Research-surfaced.)* `graph.py` has `_WATCHER_SOUL_PROMPT_CACHE` (`:10665-10706`) and `_WATCHER_BUILDER_PROMPT_CACHE` (`:10713-10747`) — read-once-at-process-import, never invalidated. They bypass the loader pipeline entirely.

**Decision.** **Accepted-risk for Phase A; rework in Phase D (or follow-up).** Watcher agents (`agents/watcher/`) are system-internal observability agents, not user-versioned agents — the user doesn't iterate on watcher's prompt. The risk is bounded: if a watcher prompt change ships in a release, the old prompt persists in-process until restart. The release-deploy pattern already restarts the daemon, so the staleness window is bounded to one daemon lifetime.

**Rationale.** Fixing these requires either threading the resolution hook into watcher initialization (touches watcher internals; out of scope) OR invalidating the module-level caches on DB-version changes (touches `graph.py` internals; surface area). Neither is load-bearing for the feature's stated goal ("user-editable persona prompts with approval workflow"). Watcher is system-internal — observer, not observed.

**Alternatives + trade-offs.**
- **A. Rewire watcher to use resolution hook** — correct; heavy; out of Phase A scope; recommend Phase D.
- **B. Documented accepted-risk** *(chosen)* — bounds the staleness; release restart clears it; user-facing flows unaffected.
- **C. Disable watcher** — breaks observability; not justified.

**Morning-review hook.** None unless the user wants watcher prompt versioning in scope (rare; watcher is system-owned).

---

### D-RESTORE-DRIFT — Checkpoint Restore Re-reads Current Files

**Question.** *(Research-surfaced.)* Restore path at `instance_lifecycle.py:4559-4567` re-reads CURRENT template files (mtime-checked) — a resumed instance can silently see newer file content than at spawn time (drift window).

**Decision.** **Resolution hook covers restore path by construction.** Phase A's hook at `loader.py:920-926` (between `load_agent_prompts` and `compose_system_prompt`) is called from BOTH spawn (`instance_lifecycle.py:2096-2104`) AND restore (`instance_lifecycle.py:4559-4567`) AND message reconstruction (`persistence.py:1053-1157`) — all three callers route through `manager.load_and_cache_prompt` (re-exported at `manager.py:26`, which tests patch). Once the hook returns the DB-resolved content, restore no longer reads on-disk mtime as the source of truth; it reads the DB. For overridden artifacts the drift window is closed — no new code at the restore site. **S19 three-case caveat (fold-in)**: a restored instance sees (a) **byte-identical** behavior when no DB rows exist (the no-regression guarantee — no-override agents KEEP today's template-drift semantics), (b) **NEW approved content** if an override was approved mid-session (human-gated behavior change; release-notes item), (c) **no on-disk drift** for overridden artifacts (DB resolution wins over file mtime).

**Rationale.** This is a *property* of the resolution hook, not a separate behavior change. Closing the drift window is the whole point of the feature: a selected-version override is honored from spawn through restore through message reconstruction. Test strategy: `test_restore_does_not_drift_after_template_mtime_change` mutates `agents/<x>/soul.md` between spawn and restore, asserts the restored prompt is unchanged.

**Compatibility note.** A restored instance's prompt CAN change mid-session — specifically, if an override is approved between spawn and restore, the restored instance sees the new effective prompt. This is a behavior change: today, restore sees only on-disk files. The change is **desirable** (the user explicitly chose the new version) but it IS a behavior change. Phase A's `test_no_db_rows_yields_identical_prompt_to_pre_phase_a` proves the no-override case is unchanged.

**Alternatives + trade-offs.**
- **A. Don't fix restore path** — leaves drift window open; defeats the feature.
- **B. Restore sees ONLY the spawn-time snapshot** — needs to persist the resolved content at spawn; losing the "new override → resumed instance picks it up" property.
- **C. Resolution hook covers restore by construction** *(chosen)* — clean; restored instance sees current DB state (consistent with spawn semantics).

**Morning-review hook.** None unless the user wants to preserve "frozen at spawn" semantics (option B). Option B is a meaningful design choice — pinned instances for reproducibility — but it requires snapshotting the resolved prompt at spawn, which the system does not currently do. *No dissent; this is the natural behavior of the feature.*

---

### D-SEED-EDGE — Existing Runtime Edits Become Invisible

**Question.** *(Research-surfaced.)* F1's overrides-only default means existing on-disk state — for agents whose `soul.md` was runtime-edited historically (e.g. release-tree incidents, `inner_soul` sessions that wrote `soul.md`) — becomes the template baseline. Any such history is invisible to DB versioning.

**Decision.** **Accept the loss; document the migration story.** Past runtime edits remain in `git log` (for committed agents) and in `agents/<name>/history/*_soul_change.md` (audit trail). Phase A does not seed them into DB. If the user wants a particular agent's runtime history preserved as DB rows, the workflow is: (1) operator pulls the historical `soul.md` from git or `history/`, (2) creates a draft via the API with that content, (3) submits and approves it — at which point the historical state becomes a v1 `approved` DB row.

**Rationale.** Bulk-seeding at boot would either require (a) heuristic detection of "this file was runtime-edited" (unreliable; `git log` requires the install tree to be a git checkout), or (b) always-seeding (option A of F1; rejected for staleness risk). The per-agent migration workflow above is heavy for a one-time event but only needed for the small set of agents with historical drift. Operators can identify drift with `git log -- agents/<name>/<artifact>` (committed history) or by checking `agents/<name>/history/*_soul_change.md` existence (audit trail).

**Alternatives + trade-offs.**
- **A. Bulk seed on first boot** — see F1 option A; rejected for staleness.
- **B. Operator-driven migration per agent** *(chosen)* — preserves user's intent; cheap; one-time cost.
- **C. Migration wizard (UI)** — UI-driven bulk import; out of scope for Phase A; Phase 2 candidate.

**Morning-review hook.** *This is the second-most-likely morning flip (after D-ARTIFACT-DISPOSITION).* If the user wants zero-loss carryover, the workflow is: enumerate agents with audit-trail files (`ls agents/*/history/`), surface those agents' "current soul.md vs initial-template" diff to the user, prompt one-by-one. The path is operationally manageable; the question is whether the user wants it auto or manual. *Default: manual; auto-seeding is rejected.*

---

### D-IDENTITY-STAMPING — Internal Submitters vs HTTP Approvers

**Question.** *(Research-surfaced.)* `inner_soul` and `agent_mother` draft submissions happen in-daemon (not via HTTP); HTTP approvers arrive via `AuthContext`. How do we stamp actor identity?

**Decision.** **Submitter identity = `(instance_id, agent_id, version_tag)` tuple** recorded in **split columns** `submitter_instance_id` / `submitter_agent_id` / `submitter_version_tag` (fold-in S3/O-12 — a single `submitted_by` blob is too narrow); approver side = `approver_subject` / `approver_auth_method`. `prompt_audit_events` gains a frozenset-validated UPPERCASE `event_type` enum (`CREATED, SUBMITTED, APPROVED, REJECTED, RETRACTED, ARCHIVED, SELECTED, CLEARED, AGENT_CREATE_TEMPLATE_COPIED`). **Approval identity = `AuthContext.subject` (string) + `AuthContext.auth_method` (enum)**, stamped from the request handler at transition time. Audit row payload keeps both verbatim (JSON column) for forensic reconstruction.

**Rationale.** Two classes of action have two different identity models:
1. **Submit (draft creation)** — happens in process. The submitter is an *agent instance*, not an HTTP user. Recording `(instance_id, agent_id, version_tag)` gives the audit trail enough to trace back: "this draft was submitted by instance X of agent Y in version-tag Z, which is owned by user U (from the instance's parent chain)". Caller-thread-friendly.
2. **Approve / reject** — happens via HTTP. The R3 source-forgery ticket (critical note 257488dc) is exactly the lesson: body-supplied identity is forgeable. The approval handler MUST read `AuthContext.subject` and stamp it. Never accept `body.approved_by` / `body.actor` from the request.

**Alternatives + trade-offs.**
- **A. Single identity model for both** — submitter = approver = `AuthContext.subject` — breaks because in-process submitters have no `AuthContext`.
- **B. Submitter = `(instance_id, agent_id, version_tag)`, approver = `AuthContext.subject` + `auth_method`** *(chosen)* — matches the two-class reality; composes with the audit-trail reconstruction need.

**Morning-review hook.** None — this is the natural decomposition. Cross-check Phase B audit row column shape includes both `(instance_id, agent_id, version_tag)` and `(subject, auth_method)` regardless of action type (so forensics works in either direction).

---

## 2. Compatibility & Migration Analysis

This section proves "existing agents unregressed" for the user's stated mission. Every claim is cited; every test that proves the claim is named.

### 2.1 Zero-Regression Guarantee (The Phase A Test That Must Pass)

**Claim.** For any agent with **no DB rows in `prompt_versions`**, the Phase-A-enabled loader produces a prompt **byte-identical** to the pre-Phase-A loader.

**Why it holds.** Phase A's resolution hook (`loader.py:920-926`) is a NO-OP when `service.get_effective(...)` returns `None`. The hook sits between `load_agent_prompts(agent_dir)` and `compose_system_prompt(...)`; if no DB override exists, the file-based content is passed through unchanged. The cache (`PromptCache` at `loader.py:737-819`) stores the post-resolution prompt under the existing key (`{agent_id}[{version_tag}]::{normalized_mcp}`); zero DB rows means the cache is populated by the same content as today.

**Test that proves it.** `test_no_db_rows_yields_identical_prompt_to_pre_phase_a` in `tests/test_prompt_version_resolution.py` — snapshot comparison against a golden prompt generated by the pre-Phase-A loader. Parametrized over all 41 meta-bearing agent dirs (`research-findings-inner-soul.md` §3).

**Agent count.** 43 dirs exist on disk under `agents/`; 41 are meta-bearing (excludes `_prompt_system/` and `_inner_soul/`; the behavioral scan included `_baby_template`) — all 41 inherit the no-regression guarantee; the test parametrization covers all of them. *(Count clarification fold-in O-8: "41" and "43" both appear across the composite — 43 = on-disk dirs, 41 = meta-bearing scanned.)*

### 2.2 Inner_soul + Access_Memory Holders (28 of 41)

**Claim.** The 28 agents that hold `inner_soul + access_memory` (the `self` category — `daemon/tools/_tool_registry.py:578`) continue to function.

**Holder list.** Explicit `"self"` in `tools.allow` (27): `_mother, approver, approver[v2], architect, ari, blueprinter, coder, designer, developer, developer[v2], devops, giter, governor, image-reader, jober, leader, planner, planner[v2], project-manager, reviewer, reviewer[v2], skill-keeper, tester, tidier, tidier[v2], wanderer, worker`. Default-grant via empty allow (1): `watcher` (`agents/watcher/meta.json:22` `"allow": []`).

**Compatibility behavior.**
- `target="memories"` — preserved (file-based; F2 carve).
- `target="memory"` — preserved (file-based; D-ARTIFACT-DISPOSITION carve).
- `target="user"` — REMOVED file write; becomes DB submission per S5/phaseB D5 (pending-approval; behavior change). Phase B ships this.
- `target="soul"` — REMOVED file write; becomes `service.create_draft(...)`. Phase B ships this; Phase A's inner_soul rewrite is incremental (Phase B scope).
- `target="workflow"` — REMOVED file write; becomes `service.create_draft(...)`. Phase B.
- Compound requests split on `AND`/semicolons/sentence boundaries (`inner_soul.py:842-879`) — preserved.
- 3-stage classification with project pre-check ALWAYS running (`:882-985`) — preserved.
- G2 anti-laundering (`:642-650`): REJECT decided on classification targets, not resolved targets — preserved.
- RAG redirect ordering (`:626-631` redirect BEFORE REJECT) — preserved.
- 2000-char request cap, memory word cap + 80% compaction, soul char cap, growth override knobs — preserved.
- Locking/flock/atomic rename — preserved for memory/user; DB-tx for soul/workflow drafts.

**Compatibility consequence.** The 28 inner_soul holders lose the ability to write to `soul.md`/`workflow.md` directly (the most common inner_soul target after `memory`). They gain the ability to submit DB drafts. The migration is **transparent to the agent's reasoning** (the response grammar `✓/⚠/📋/⛔` is preserved per `inner_soul.py:1345-1366`), but **textually different** for soul/workflow (returns "submitted draft" instead of "wrote soul.md"). Phase B inner_soul rewrite is responsible for the textual update; Phase A doesn't change inner_soul semantics.

### 2.3 Non-inner_soul Agents (13 of 41)

**Claim.** The 13 agents that do NOT hold `inner_soul` are unaffected by the inner_soul rewrite.

**Non-holder list.** `_baby_template, charter, critic, doc-maintainer, doc-writer, experiencer, explorer, gaia, image-comparator, kb-importer, kb-writer, maintenancer, sketcher`.

**Compatibility behavior.** Zero changes to their tool surface. The loader-resolution hook is also transparent (NO-OP for no DB rows). `access_memory` is only granted alongside `inner_soul` (the `self` category bundles them at `_tool_registry.py:578`), so none of these agents have `access_memory` either — no `memories/*.md` access to migrate.

### 2.4 `.agents/{id}/memories/` Layout — Already Retired

**Claim.** The old `.agents/{agent-id}/memories/` layout has zero code references and zero migration burden.

**Why.** Verified worktree: `glob agents/**/memories/*.md` returns ZERO files in the worktree (findings §2(a)). All references in `daemon/` use the resolved-agent-path layout (`agents/{name}/memories/`). The `.agents/{id}/memories/` directory in live installs is orphan runtime state from an earlier migration; nothing reads it.

**Compatibility consequence.** Nothing to migrate. Orphan directories can be cleaned up in a separate follow-up; not a Phase A concern.

### 2.5 RAG Dual-State Constraint

**Claim.** The `inner_soul` rewrite must work in BOTH RAG-ON and RAG-OFF states (the constraint from `.agents/shared/planning/inner-soul-reform/`).

**Why.** RAG is gated by `LIGHTRAG_HOST` env (`daemon/rag/config.py:81-88`); when unset, `is_rag_enabled()` returns False. Many installs run RAG-off (the default for dev). The redirect layer (`inner_soul.py:230-276`) MUST work in both states.

**Compatibility consequence for the rewrite.**
- RAG-OFF → `inner_soul` keeps its current behavior for `memory`/`user` targets (file-based, unchanged) AND redirects soul/workflow drafts to DB.
- RAG-ON → `inner_soul` redirects knowledge-classified content to `experience()` / `explore()` (unchanged from today, per `_should_redirect_to_rag` at `inner_soul.py:230-276`). Soul/workflow drafts still go to DB.
- The redirect layer runs BEFORE the REJECT check (`:626-631`); this ordering must be preserved in the rewrite.

**The ProjectContentRejectionHint logic** (`:304-329`) — the REJECT response suggests `project_history_add(...)` or `experience(...)` as the next step. The rewrite must keep these hints accurate in the post-Phase-B world (e.g. when an agent submits a soul draft that contains project knowledge, the rejection hints should still point at `project_history_add`).

### 2.6 SQLite / PostgreSQL Dual-Driver Reality

**Claim.** The new `prompt_versions` and `prompt_audit_events` tables work on BOTH engines without `_ensure_postgres_columns` mirror.

**Why.** Per `research-findings-db-api.md` §1, **brand-new tables need NO `_ensure_postgres_columns` mirror** — `SQLModel.metadata.create_all` creates them on fresh AND existing PG databases at next boot. The `_ensure_postgres_columns` path is only for adding columns/indexes to *existing* tables. The `.sql` migration file is for SQLite only (the runner is SQLite-only by design; `daemon/migrations/runner.py:693-718`); the SQLModel model must simultaneously be PG-valid.

**Migration file conventions to follow.**
- Filename: `YYYYMMDD_HHMMSS_create_prompt_versions.sql` — unique stamp (verify no existing `20260628_000002` collision; db-api findings §1 documents one cautionary case).
- Header: DUAL-DRIVER NOTES (PG via `metadata.create_all`, SQLite via runner).
- `CREATE TABLE IF NOT EXISTS` for both tables.
- Index names byte-identical to `__table_args__` (the canonical source per db-api findings §1).
- `-- DOWN` reverse-drops both tables.
- Templates to copy: `20261011_000001_create_stored_watches.sql` (full template) + `20261007_000001_create_drift_events.sql` (audit-flavor template). Recommend grouped in ONE file (skill-tables precedent, `20260710_000001_create_skill_tables.sql` = 6 tables).
- Timestamps: ISO TEXT via `_now_iso()` (the majority pattern, `skill/models.py:66-73`).
- JSON columns: `JSONBType` (`daemon/repositories/infra/types.py`).
- Booleans: INTEGER 0/1 (`skill/models.py:116-118`).
- PKs: TEXT (no UUIDs as PKs in this codebase).

**Pitfalls avoided.** No semicolons in comments/strings (naive splitter; `runner.py:707-718`); no PG-only constructs in `.sql`; verify the `20260714_000001_widen_job_queue_type_constraint.sql` lesson (`DROP CONSTRAINT IF EXISTS` is invalid on SQLite).

### 2.7 Version-Tag Composition with Directory Siblings

**Claim.** Phase A's `(agent_id, version_tag)` DB key composes with D15 (`PromptCache` version-tag keying) and D16 (resolver invariant) from `.agents/shared/planning/agent-versioning/decisions.md`.

**Why.**
- `PromptCache._make_key` at `loader.py:748-774` already keys by `{agent_id}[{version_tag}]::{normalized_mcp}` (D15). The DB row key `(agent_id, version_tag, artifact, version)` composes with this: a tagged agent's `prompt_versions` rows are isolated from base `prompt_versions` rows.
- `AgentMetadata.path` (`registry.py:267`) and `instances.agent_dir` (`instance_lifecycle.py:1921`) carry the version-tagged resolution. `Registry._parse_agent_dir_name` (`registry.py:31-43`) parses the `[v2]` suffix; `version_tag` is a first-class identity attribute.
- The F9 fork decision resolves the planner-surfaced concern: tagged siblings do not leak.

**Composition invariant.** For any `(base_agent_id, version_tag)` pair, the effective prompt for an artifact is `selected_overrides(base_agent_id, version_tag) > latest_approved(base_agent_id, version_tag) > template(base_agent_id, version_tag)`. Selecting a `developer[v2]` override has zero effect on `developer` base; clearing `developer[v2]` selection leaves `developer` base untouched.

### 2.8 The Four Known In-Scope Bugs Folded In

Phase A fixes these opportunistically (each is on the critical path; not addressed later).

**Bug 1. `invalidate` missing `version_tag` — 6 sites.**
- `daemon/tools/agent_mother.py:380` — `manager.prompt_cache.invalidate(agent_name)` → thread `version_tag`.
- `daemon/tools/inner_soul.py:1088, :1162, :1247, :1304, :1336` — same threading fix.
- After Phase A's guard rejects direct file writes, these invalidate sites become rare (DB-driven changes only) but still need to be correct for any internal events that touch the cache.

**Bug 2. `_get_system_prompt_tokens` drops `version_tag` — `instance_messaging.py:1063`.**
- Verified: `_prompt_cache.get(meta.agent_id, mcp_tool_names)` — no version_tag parameter.
- Fix: thread `version_tag` from the surrounding call site (already in scope per `instance_lifecycle.py:4540-4567`). Token accounting for tagged agents is currently subtly wrong; opportunistic fix.

**Bug 3. Watcher process-global caches — `graph.py:10665-10747`.**
- `_WATCHER_SOUL_PROMPT_CACHE` and `_WATCHER_BUILDER_PROMPT_CACHE` are module-level read-once, never invalidated.
- Accepted-risk for Phase A (`D-WATCHER-CACHES`); Phase D reworks watcher integration to use the resolution hook.

**Bug 4. `BASE_DIR/agents` vs registry-resolved path divergence — non-frozen installs.**
- `loader.py:19-21` / `registry.py:1217-1219` use `Path(__file__).parent.parent` for non-frozen; `agent_mother.py:48,140,424` / `routers/agents.py:67,154` use `BASE_DIR / "agents"`.
- In frozen installs, both should agree; in dev-checkout installs, the bases can differ.
- Phase A's guard resolves BOTH bases (the realpath containment comparison normalizes via canonicalization). The guard covers both styles.

### 2.9 Migration Sequencing

**Phase A (this commission) — DB + hook + guards.**
- Schema + repo + service + cache extension + loader hook + writer guards — all conforming to the **frozen Phase A→B contract `phase-contract.md` (amendments A1–A8)**.
- Net user-visible behavior: zero change for agents with no DB rows; zero change for non-versioned artifacts (memory/user/growth/meta).
- Testable invariant: `test_no_db_rows_yields_identical_prompt_to_pre_phase_a`.

#### Phase A contract amendment journal

**A7 retry-budget amendment — 2026-10-13 (dispatcher-approved post-hoc).** The frozen contract's A7 cell originally specified `retry ≤ 3` for the `INSERT-with-retry-on-IntegrityError` clause on the `prompt_versions.UNIQUE(agent_id, version_tag, artifact, version)` constraint. The repository shipped (`daemon/repositories/prompt_version/repository.py:372`) with the bounded constant `_MAX_INSERT_RETRIES = 8`, used in both `create_draft` (`:478`) and `create_and_submit` (`:1287`) IntegrityError retry loops. The R1 reviewer (cycle 1) adjudicated **APPROVE on technical merits** (the dispatch mechanism — recompute max under retry, exhaustion re-raises; bounded; no silent degradation — is correct and is the cited mechanism); the dispatcher has post-hoc signed off. Rationale: a 10-thread contention test suite (added in the same land) drove 8 retries to completion within the recompute-max loop in observed runs; truncating to ≤3 would fail-close at the seam the contract was designed to keep open. Documented as a contract-cell amendment; `phase-contract.md` A7 cell now reads "bounded retry = `_MAX_INSERT_RETRIES = 8`" with the amended-on date stamp. Phase B consumes A7 verbatim — no change.

**Phase B — Approval state machine + inner_soul rewrite + agent_mother rewrite.**
- `VALID_TRANSITIONS` set + `atomic_transition` repo method (job-state-machine precedent, `daemon/services/job_state_machine.py:53-62`).
- REST endpoints (fold-in G-3 — **canonical 12-endpoint family per addendum S4**; the earlier kebab-case `POST /api/prompt-versions` and 2-segment `GET /api/effective/{agent}/{artifact}` sketches here were STALE and are killed): `GET /api/prompts/versions` (list), `POST /api/prompts/versions` (create-draft — REQUIRED by the D-SEED-EDGE operator migration), `GET /api/prompts/versions/{id}`, `GET /api/prompts/versions/{id}/diff`, `POST /api/prompts/versions/{id}/submit`, `POST /api/prompts/versions/{id}/approve`, `POST /api/prompts/versions/{id}/reject`, `POST /api/prompts/versions/{id}/retract`, `POST /api/prompts/selections`, `DELETE /api/prompts/selections/{agent_id}/{version_tag}/{artifact}`, `GET /api/prompts/audit`, `GET /api/prompts/effective/{agent_id}/{version_tag}/{artifact}`.
- Auth-gated with `@require_scope("prompts:approve")` (new scope; minting touches `daemon/auth/scopes.py`).
- Audit rows stamped from `AuthContext.subject`/`auth_method`.
- `inner_soul` rewrite (fold-in S5/A5): `target="soul"`, `target="workflow"`, and `target="user"` call `service.create_and_submit(...)` (the A5 composite with the C4 composition rule) instead of file write; preserve all other behavior; preserve response grammar.
- `agent_mother.agent_modify` rewrite: same pattern for `soul/rule/workflow/tools/user`.
- D-IDENTITY-STAMPING columns populated.
- Test corpus: `test_prompt_approval_state_machine.py` (state transitions), `test_prompt_approval_auth.py` (AuthContext stamping, scope enforcement, auth-OFF fail-closed), `test_prompt_approval_idempotency.py` (no double-approval, no selection after reject).

**Phase C — Frontend review/approve UI.**
- New Angular service `frontend/src/app/services/prompt-version.service.ts` mirroring `skill-bank.service.ts` (signals, `{items,total}` unwrap).
- New page component: review/approve list + diff view + approve/reject buttons + select-this-version.
- Natural Material component fit; no restructuring.
- Gated on user green-light post-Phase-B.

**Phase D — Watcher + drift detection + restore-path hardening (optional, may be follow-up).**
- D-WATCHER-CACHES rework: thread watcher initialization through the resolution hook.
- Drift detection at promote-time: flag changes to `agents/<name>/<versioned-set>.md` since last approved release.
- Audit-tooling: `agent_mother` read-only audit on `agents/` to detect post-hoc drift; alarm on any drift.

### 2.10 What Phase A Does NOT Change

- `agent_mother.agent_create` — template copy from `_baby_template`; legal under immutability.
- `routers/agents.py` POST `:106-122` — template copy; same.
- `agent_mother.agent_delete` + `routers/agents.py` DELETE — move to `_trash`; same.
- `bash` tool — no code-level change.
- `_prompt_system/**` — out of scope.
- `meta.json` — out of scope.
- `skill-set.yaml` + `skills-template/*` — out of scope.
- Watcher internals — accepted-risk.
- Memory subsystem (RAG) — unchanged.
- Checkpoint shape — `system_prompt` baked at spawn/restore stays a single string; no checkpoint schema change.
- Skill evolution subsystem — untouched.
- Opencode lane — already retired to `legacy/`; no touch.
- Deployment / release / upgrade pipeline — no manifest or upgrade-journal changes.

---

## 3. Risk Register

Each risk carries severity (H/M/L), mitigation, owning phase.

| # | Risk | Sev | Mitigation | Owning phase |
|---|------|-----|-----------|--------------|
| R1 | **`D-ARTIFACT-DISPOSITION` morning flip breaks Phase A schema assumptions** (user wants `memory.md` IN) | M | Phase A schema + resolution hook are **artifact-agnostic**; flipping this fork is a config-table change, not a migration. If `memory.md` joins, `inner_soul` rewrite must add `target="memory"` → DB draft branch (Phase B extension). | A schema is safe; B has the rewrite burden |
| R2 | **`D-RESTORE-DRIFT` behavior change visibility** (restored instance sees new effective prompt if override approved mid-session) | M | Documented as **desirable** behavior (user explicitly chose the new version). Phase A test `test_restore_does_not_drift_after_template_mtime_change` proves the no-DB-rows case is unchanged. Communicate to user in release notes. | A |
| R3 | **25k+ test wall exposure** (cache-key tests + inner_soul test packs) | H | The cache-invalidation fix touches 6 `invalidate` sites + `_get_system_prompt_tokens`; existing tests may rely on the (broken) base-key-only behavior. Mitigation: run the inner_soul test pack + skill-seed test pack + cache-key test pack FIRST after the threading fix; fix the tests that assert the (broken) old behavior; add new tests for the correct behavior. The remediation is bounded (6 sites, one new behavior). | A |
| R4 | **Release-tree write incidents recurrence until guards land** (incidents 2026-10-01 per project history) | H | Phase A's guard at `filesystem.py:889` closes the absolute-path bypass for `write_file`/`edit_file` (the incident vector). `agent_mother.agent_modify` is re-stricted to the carved-out set. The bash escape hatch remains; OS-level fence is deployment-time, not code-time. **Until Phase A ships, no new immutability is enforced** — operators should manually `chmod -w agents/` if the release-tree incident pattern is active. | A |
| R5 | **Approval deadlocks when user is away** (agents blocked on pending versions; user is asleep / unavailable) | M | Three mitigations: (a) `pending_approval` rows are **invisible** to the effective prompt (F6); the system keeps running on the previous approved version or template. (b) Agents can **recall** a pending draft they submitted (F4 state machine: `pending_approval → draft`). (c) Operator can `curl`-approve from anywhere (no UI dependency for the daemon API). Combined: no deadlock, just delayed approval. | B |
| R6 | **`bash` tool cannot be code-level fenced** (the 2026-10-12 incident class) | M | Documented accepted-risk. Legitimate-write-site removal (`inner_soul` rewrite) reduces attack surface. Drift detection at promote-time surfaces unintended changes. OS-level fence is deployment-time hardening. | A documented; B reduces surface; deployment is owner's call |
| R7 | **Watcher process-global caches stay stale** (`graph.py:10665-10747`) | L | Accepted-risk for Phase A. Watcher is system-internal, not user-versioned; bounded staleness window to one daemon lifetime; release-deploy pattern restarts the daemon. Phase D reworks. | A documented; D rewires |
| R8 | **Auth-OFF posture** (`@require_scope` inert, `auth_context` absent) | M | Fail-closed: approvals require auth (D-AUTH-OFF). Operators who want local-loopback approvals must explicitly configure auth — a one-time setup cost, not a feature regression. | B |
| R9 | **Inner_soul response-grammar drift** (soul/workflow drafts return different text — "submitted draft" vs "wrote file") | L | `inner_soul`'s `✓/⚠/📋/⛔` response grammar (`inner_soul.py:1345-1366`) is preserved; only the per-target sub-line text changes. Agents that parse this text are typically the human operator, not an LLM; the `tool docs` are the API contract. Phase B's inner_soul rewrite bumps the docs atomically with the change. | B |
| R10 | **Cached prompt content drift after a draft is approved mid-instance-life** (running instance sees stale prompt) | M | **Fold-in reword (C1/G-4a)**: the fix is **fingerprint-bust-on-get** — the cache-hit equality check (`loader.py:915`) recomputes the synthetic `db_versions_fingerprint` from the single `get_effective_rows` SELECT on every call, so ANY DB change (approve/reject/select/clear) self-heals every cache variant, including the `{agent}[{tag}]::{mcp_csv}` MCP keys that the single-key `invalidate` (`loader.py:809-819`) cannot reach. Approval-handler `invalidate` calls remain as belt-and-braces only. Running instances that have NOT yet spawned/restored keep their existing prompt (prompts are baked at spawn/restore per `loader.py:822-932`). | B |
| R11 | **D-SEED-EDGE morning flip — user wants zero-loss carryover of historical runtime edits** | L | Operator-driven per-agent migration (workflow in `D-SEED-EDGE`). Cheap; one-time; not blocking. | A documented; manual migration if requested |
| R12 | **Prompt-cache TTL during the resolution-hook transition** | L | The hook is NO-OP for no DB rows (today). Existing caches remain valid. The new "resolved-version fingerprint" cache dimension (phaseA-plan T7) is added BEFORE the hook starts taking effect — no race. | A |
| R13 | **Frozen-install topology: agents/ already de-facto immutable in releases** (release artifacts, retention-evicted) | L | Phase A's guard is uniform across frozen and non-frozen installs. In frozen installs, the guard is double-protection (the install tree is already read-only via OS-level packaging). In dev-checkout, the guard is the primary protection. | A |
| R14 | **Approval authority ambiguity for shared-test / multi-operator deployments** | L | Default is "user only" (F4). The shared-test case (multiple humans approving) is out of scope; if the user later wants multi-approver, the `VALID_TRANSITIONS` set can be extended with a `pending_multi_approval` state and a quorum check. | B (extension if requested) |

---

## 4. Open Questions for the Architect

1. **`memory.md` disposition** — see `D-ARTIFACT-DISPOSITION`. Default is carve-OUT. Architect should weigh the "memory state as template" question and decide.
2. **Historical-runtime-edits carryover** — see `D-SEED-EDGE`. Default is manual operator migration. Architect should confirm or escalate to bulk-seed.
3. **Watcher cache rewire scope** — see `D-WATCHER-CACHES`. Default is accepted-risk for Phase A; Phase D reworks. Architect should confirm Phase D scope (in or out of this commission).
4. **`agent_mother.agent_create` template-copy path** — currently legal under immutability (creation, not content edit). Should the template copy go through the audit trail (so new-agent-creation is logged) or stay as today (silent copy)?
5. **Selection-pointer "audit" — when the user changes selection, is that a logged event?** Default: yes (`prompt_audit_events` row). Confirm or simplify.
6. **Restore-path "frozen at spawn" option** — see `D-RESTORE-DRIFT`. Default is "DB-resolved at restore". The alternative (snapshot the resolved prompt at spawn) is a meaningful design choice — confirm we don't want it.
7. **`memories/*.md` migration to DB** — see `D-ARTIFACT-DISPOSITION` and `inner_soul` findings §6.6. Default: keep file-based. The migration is non-trivial (loses loader preview pattern). Defer unless user wants it.
8. **`growth.md` `soul_requires_approval` knob** — see `inner_soul` findings §1.7. Dead code today. **Fold-in ruling (SCOPE strike)**: knob-wiring is STRUCK from task lists — the knob stays dead code pending morning review; only the `ProjectContentRejectionHint` update ships (phaseB T7).
9. **Skill-evolution write path** (`skill-set.yaml` / `skills-template/*` runtime writes) — see F2 + findings open question. F2 says out of scope. Confirm: does skill-keeper write anything at runtime, or is skill-set.yaml purely human-edited?
10. **Audit row column shape** — see `D-IDENTITY-STAMPING`. Columns: split `submitter_instance_id/agent_id/version_tag` for submitters, `approver_subject/auth_method` for approvers, plus the `event_type` enum. Architect should confirm the column set is complete for forensics.
11. **Front-end gating** — see F7. Default: Phase C gated on user green-light post-Phase-B. Architect should confirm.

---

## 5. References

### Findings (this commission)
- `.agents/shared/planning/agent-prompt-immutability/research-findings-prompt-loading.md` — loader/cache/guard inventory
- `.agents/shared/planning/agent-prompt-immutability/research-findings-inner-soul.md` — inner_soul rewrite target mapping
- `.agents/shared/planning/agent-prompt-immutability/research-findings-db-api.md` — DB/API conventions

### Plan (this commission)
- `.agents/shared/planning/agent-prompt-immutability/phaseA-plan.md` — Phase A foundation (DB + hook + guards)
- `.agents/shared/planning/agent-prompt-immutability/decisions.md` — THIS DOCUMENT

### Prior art (in-tree planning)
- `.agents/shared/planning/agent-versioning/decisions.md` — D15 (PromptCache version_tag keying), D16 (resolver invariant)
- `.agents/shared/planning/inner-soul-reform/` — RAG dual-state constraint + rejection semantics

### Worktree file:line citations (load-bearing claims verified)
- `daemon/loader.py:737-819` — PromptCache class (`_make_key` at `:748`, `invalidate` at `:809`)
- `daemon/loader.py:822-932` — `load_and_cache_prompt` choke point (cache-miss reload at `:920`)
- `daemon/loader.py:405-435` — `load_agent_prompts`
- `daemon/loader.py:438-533` — `compose_system_prompt` (sections 1-11)
- `daemon/loader.py:314-360` — `load_recent_memories` (filenames-only index)
- `daemon/loader.py:418` — `prompt_files` (the versioned-immutable set baseline)
- `daemon/tools/inner_soul.py:545-818` — `create_inner_soul_tool` factory
- `daemon/tools/inner_soul.py:882-1025` — 3-stage classification
- `daemon/tools/inner_soul.py:230-276` — RAG redirect layer
- `daemon/tools/inner_soul.py:1107-1162` — `memory.md` write path (carve-out reason)
- `daemon/tools/inner_soul.py:1237/:1327/:1295/:1158` — soul/user/memory file writes (removed in Phase B)
- `daemon/tools/inner_soul.py:1088/:1162/:1247/:1304/:1336` — invalidate sites (bug fix)
- `daemon/tools/agent_mother.py:349-393` — `agent_modify` (Phase B rewrite target)
- `daemon/tools/agent_mother.py:359` — `modifiable_files` allowlist (Phase A shrinks)
- `daemon/tools/agent_mother.py:380` — invalidate site (bug fix)
- `daemon/tools/filesystem.py:882-905` — `write_file` (Phase A guard at `:889`)
- `daemon/tools/filesystem.py:1235-1270` — `edit_file` (Phase A guard)
- `daemon/tools/filesystem.py:205-209` — absolute-path bypass (closed by Phase A)
- `daemon/services/instance_messaging.py:1047-1069` — `_get_system_prompt_tokens` (bug fix at `:1063`)
- `daemon/services/instance_lifecycle.py:2096-2104` — spawn site (hook transparent)
- `daemon/services/instance_lifecycle.py:4559-4567` — restore site (drift window closed by hook)
- `daemon/registry.py:267` — `AgentMetadata.path`
- `daemon/registry.py:535-541` — `AgentRegistry` constructor
- `daemon/registry.py:1217-1219` — `base_dir` resolution (frozen vs non-frozen)
- `daemon/registry.py:31-43` — `_parse_agent_dir_name` (D15)
- `daemon/migrations/runner.py:66-136` — migration parse/naming/checksum
- `daemon/migrations/runner.py:503-624` — transactional apply + idempotent tolerance
- `daemon/migrations/runner.py:693-718` — SQLite-only runner docstring
- `daemon/repositories/skill/models.py:44-49` — NULL-distinct UNIQUE (precedent)
- `daemon/repositories/skill/models.py:93-97` — `get_active_variant` (precedent for selection)
- `daemon/repositories/skill/models.py:119-121` — `Skill.status` free-text (contrast with formal machine)
- `daemon/services/job_state_machine.py:53-100` — `VALID_TRANSITIONS` + `atomic_transition` (precedent)
- `daemon/auth/decorators.py:27-32/:74-124` — `@require_scope` inert-OFF behavior
- `daemon/auth/context.py:36-60` — `AuthContext` shape
- `daemon/middleware/auth.py:1-43/:219` — `AuthMiddleware` + `auth_context` attach
- `daemon/rag/config.py:81-88` — `is_rag_enabled` (RAG dual-state)
- `daemon/graph.py:10665-10747` — watcher process-global caches (accepted-risk)
- `daemon/manager.py:1171-1173` — repo construction pattern (Phase A inserts `prompt_version_repo` here)
- `daemon/manager.py:26` — `load_and_cache_prompt` re-export (tests patch here)

### Critical notes (cited)
- 2026-10-12 live-install deletion — `test_journal_splice.sh` §2 → drives D-BASH-ESCAPE accepted-risk
- 257488dc — R3 source-forgery → drives D-IDENTITY-STAMPING (AuthContext-only)

### Migration templates
- `daemon/migrations/versions/20261011_000001_create_stored_watches.sql` — full CRUD template
- `daemon/migrations/versions/20261007_000001_create_drift_events.sql` — audit/event template

### Tables of agents
- 43 agent dirs on disk; 41 meta-bearing (per `research-findings-inner-soul.md` §3; excludes `_prompt_system/`, `_inner_soul/`)
- 28 hold `inner_soul + access_memory` (the `self` category)
- 13 do not hold either
- 5 tagged siblings: `approver[v2]`, `developer[v2]`, `planner[v2]`, `reviewer[v2]`, `tidier[v2]` (D15/D16 territory)

---

## 6. Decision Summary (TL;DR for the user, morning review)

| Fork | Default | Most likely morning flip? |
|------|---------|---------------------------|
| F1 Seeding | overrides-only in DB | No |
| F2 Artifact scope | prompt .md only | No |
| F3 Version codes | monotonic int + UUID id | No |
| F4 Approval states | draft→pending_approval→approved\|rejected | No |
| F5 Selection | `is_selected` bool, transactional | No |
| F6 Precedence | selected > latest_approved > template | No |
| F7 UI | daemon API = core; UI = Phase 2 gated | No |
| F8 Immutability | typed error + API-redirect hint | No |
| F9 DB identity | `(agent_id, version_tag)` composite | No |
| D-ARTIFACT-DISPOSITION | carve `memory`/`growth` OUT; `user.md` IN (S5, phaseB D5) | **YES — highest likelihood** (memory.md flip) |
| D-AGENT-MOTHER | rewire to DB drafts | No |
| D-AUTH-OFF | fail-closed 503 `AUTH_NOT_CONFIGURED` (S8) | No |
| D-BASH-ESCAPE | documented accepted-risk | No |
| D-WATCHER-CACHES | accepted-risk; Phase D reworks | No |
| D-RESTORE-DRIFT | hook covers restore; S19 three-case caveat | No |
| D-SEED-EDGE | manual operator migration | **YES — second-most likely** → **RESOLVED-DEAD 2026-10-14** (see §7 binding block) |
| D-IDENTITY-STAMPING | submitter tuple + AuthContext subject/method | No |

---

## 7. User Directive 2026-10-14: migration policy reversal (BINDING — supersedes preservation language)

**Verbatim intent.** DISCARD existing persona/memory customizations — do
NOT migrate them into the new DB version store; persistence starts EMPTY
at go-live (intended first-time-persistence semantic, NOT data loss).

**Origin / scope.** Binding user directive delivered 2026-10-14
overnight. Lands on branch `feature/agent-prompt-immutability` ahead of
Phase A/B merge; commits land sweep (`3c307e8d`) before docs amendment.
The legacy repository state of pre-directive content is preserved (git
history intact + archive tree under
`legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/`);
this directive governs the **new DB-versioning surface**, NOT the
archive.

### Rulings (R1–R6, verbatim-intent)

| ID  | Ruling                                                                                                                                                                                                  |
|-----|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| R1  | **F1 STANDS** — template file itself = baseline initial version (no DB row); `D-SEED-EDGE` RESOLVED-DEAD: no seeding, no edges, empty DB at go-live.                                                       |
| R2  | Sweep **INCLUDES** live memories surface: tracked `agents/{name}/memory.md` files get ARCHIVED (never deleted) into `legacy/` per repo convention — committed repo-side sweep on this branch. The memories MECHANISM stays (`access_memory` + future file writes continue); only accumulated pre-directive content goes. |
| R3  | `.agents/{id}/memories` (zero code refs) — archive whatever is tracked.                                                                                                                                |
| R4  | RAG — **NO KB purge**. Document the no-consume boundary: the new persona paths must never query/consume persona-class RAG entries (the rewrite already doesn't — verify + document for morning review).   |
| R5  | Repo-committed `agents/*.md` AS-SHIPPED = baseline by fiat. No "pristine reset" chase.                                                                                                                  |
| R6  | `D-ARTIFACT-DISPOSITION` (memory.md file-vs-DB mechanics) morning fork UNCHANGED — do not expand into it.                                                                                                |

### Migration policy reversal — fork disposition deltas

The following changes to the §1 fork register and §6 decision summary
are ratified by the directive (and supersede the prior preservation
language):

- **F1 OVERRIDES-ONLY** → STANDS. No code added.
- **D-SEED-EDGE** *(§1 / §6)* → **RESOLVED-DEAD.** The "second-most
  likely morning flip" in §6's decision table is REMOVED: the
  per-agent operator-migration workflow is no longer on the table.
  Empty DB at go-live is the desired steady state.
- **D-ARTIFACT-DISPOSITION** *(§1 / §6)* → unchanged per R6; the
  memory.md carve-out default (file) is preserved verbatim — the
  directive does not move the morning fork in either direction.
- **D-ARTIFACT-DISPOSITION **(`memory.md` carve-out) read by
  R2: sweep archives accumulated `memory.md` content; the file
  itself is then empty/regenerated-by-future-writes, which is the
  desired "first-time-persistence" state.
- **No new fork D-* surfaced.** The directive does not add an
  eleventh / twelfth fork to the §1 register; it ratifies the
  *outcome* of the existing F1 fork + seals D-SEED-EDGE.

### Archive inventory (commit `3c307e8d`)

| Source class                            | Pattern                                  | Count |
|-----------------------------------------|------------------------------------------|-------|
| A — `agents/<name>/memory.md`           | active-agent `memory.md`                 | 11    |
| B — `.agents/{id>/memories/*.md`        | orphan R3 layout (zero code refs)         | 177   |
| C — `agents/{name>/memories/*.md`       | runtime-only (per `D-ARTIFACT-DISPOSITION`) | 0 (verified, by-construction) |
| **Total archived**                      |                                          | **188** |

Per-agent counts (B): `reviewer` 82, `developer` 82, `tester` 5,
`charter` 4, `leader` 2, `tidier` 1, `coder` 1.

Per-agent counts (A): `_baby_template`, `_mother`, `architect`,
`developer`, `developer[v2]`, `giter`, `leader`, `maintenancer`,
`reviewer`, `reviewer[v2]`, `tester`.

Destination: `legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/`
(parallels `legacy/.agents/shared/planning/opencode-native-tools/`
convention; avoids polluting `legacy/agents/` whose tree holds
retired-agent bundles — an unrelated category).

Reversibility: archive-not-delete per R2; full `git revert` restores
all 188 files byte-exact at original paths.

### RAG no-consume boundary (R4 — verified)

Static-grep on `daemon/repositories/prompt_version/` and
`daemon/services/prompt_version_service.py`:
- `prompt_versions` + `prompt_audit_events` + `prompt_selections`
  tables ONLY; no `rag_*` / `experience` / `kb_*` reads on the
  persona path.
- `inner_soul` (`daemon/tools/inner_soul.py`) classifies targets
  via `_should_redirect_to_rag` (`:230-276`) which is scoped to
  non-project, non-persona targets — `memory`/`memories` are CARVED
  OUT and never redirected; this is `inner_soul`'s own historic
  boundary, NOT rewrite-side leak surface.

Conclusion: the persona paths (`create_and_submit`, `get_effective`)
never query or consume persona-class RAG entries. **No KB purge
required.**

### Boot-tolerance proofs

Verified empirically (fenced eval under dev env, this branch):

- `daemon/loader.py:565` `load_recent_memories` returns `""` when
  `memories/` is absent — boot §9 safely skipped.
- `daemon/loader.py:663-668` `load_agent_prompts` silently skips
  missing `memory.md` — boot §8 safely skipped for the 11 affected
  agents.
- `daemon/tools/access_memory.py:73-74` `access_memory` returns
  `"No memories/ directory found."` when memories/ is absent — no
  exception, no crash.

### Clean-start behavioral test

New: `tests/test_prompt_version_resolution.py::TestUserDirectiveCleanStart`
(one narrative test, sqlite-only) — proves:

- empty DB starts with **zero rows**;
- `get_effective` returns `None` (file fallback path) at empty state;
- `inner_soul(target="soul")` produces a single `pending_approval`
  row at `version=1`, file untouched (byte-identical + mtime-equal);
- pre-approval effective is still `None` (pending is NOT effective);
- session-lane `approve` transitions the row to `approved`;
- post-approval `get_effective` returns the approved row content;
- **post-approval effective ≠ template file content** (the empty-slate
  invariant at runtime).

Pack run (this branch):
- `tests/test_prompt_version_resolution.py + tests/test_inner_soul_db.py`
  → **91 passed**, including the new clean-start test and all
  pre-existing precedence / state-machine / inner_soul tests.
  PostgreSQL parametrized legs skip (no scratch PG fixture wired on
  this sandbox host); SQLite leg covers the assertion surface per the
  existing SC8/SC9 convention.

### Acceptance impact (companion to plan-overview §8 amendment)

- **§1 F1** + **§6 D-SEED-EDGE** → ratified outcome delivered.
- **phaseB-plan.md deliverables 5/6** (memory reconciliation table +
  F1 OVERRIDES-ONLY seeding) → amended to reflect the resolved-dead
  state. See companion amends in `memory-reconciliation-table.md` /
  `plan-overview.md §8`.

### Supersedence note

This block supersedes any §1/§2/§6 language that suggests a
preservation or operator-migration workflow for persona customizations
at Phase B go-live.

**End of §7.**


---

## 8. User Directive 2026-10-14 (overnight, landing): SURFACES layer

The SURFACES layer (User Directive #2 frontend specs + Directive
#3 + #4 + #5B wiring + directive-specified tests + ALL FOUR docs
blocks) lands as a single review-fold-in. The followings blocks
are frozen at fold-in (overnight, dispatcher-approved post-hoc;
morning-ratification expected) and supersede any §1-§7 language
that conflicts.

### 8.1 Block #2 — Frontend specs (Ud#2 endpoint growth + designer-skip rationale)

The frontend Persona UI (Phase C, gated) consumes the 15-endpoint
canonical family. The Ud#2 deltas (12 → 15) — the
PATCH/DELETE/pending-count endpoints — were the only Phase C
contract work. Five points + the phasing:

1. **PATCH /api/prompts/versions/{version_id}** — body
   ``{content}``; scope ``prompts:write``; draft/pending rows
   ONLY. Approved / rejected / archived rows surface as
   ``409 VERSION_IMMUTABLE``; unknown id → 404. The leader
   ruling: EDIT = draft/pending content ONLY — approved
   rows are append-only immutable history; rejected is
   terminal (resubmit = new row); archived is superseded.
   Hard-delete of approved history is a MORNING FORK and
   is deliberately NOT built.
2. **DELETE /api/prompts/versions/{version_id}** — same
   scope + state rule + 409/404 mapping. The repo
   hard-deletes the row and stamps a ``DELETED`` audit
   event whose payload carries identity + content_hash +
   content byte length (forensics survive the row).
3. **GET /api/prompts/pending-count** — scope
   ``prompts:read``; returns ``{"count": N}`` — the
   Persona UI badge feed; drains as approve/reject land.
   A PLAIN count (the A8 effective fingerprint does not
   apply to a badge). **CORE-only by design** (Ud#3
   SUPERSEDES the original scope intent): MEMORY rows
   are born-effective at ``state='approved'`` and never
   enter the ``pending_approval`` state, so they do
   not contribute to the badge. The repo's
   ``count_pending`` predicate
   ``state == 'pending_approval' AND artifact_class = 'CORE'``
   is the fence.
4. **List endpoint artifact_class filter** (Ud#3, 2026-10-14) —
   ``GET /api/prompts/versions?artifact_class=CORE|MEMORY``
   restricts the listing to the chosen class. The
   serialization now carries the ``artifact_class`` field
   on every row (default ``"CORE"`` when the column is
   missing on legacy rows). 422 on anything outside
   ``['CORE', 'MEMORY']``; the permissive filter allowlist
   still rejects slashes / whitespace / injection chars.
5. **MemoryClassError → 4xx mapping** (Ud#3) — the
   state-machine endpoints (approve / reject / submit /
   retract / PATCH / DELETE) surface
   :class:`MemoryClassError` (the typed refusal when a
   state-machine op targets a MEMORY-class row) as
   ``409 MEMORY_CLASS_MISMATCH``. The class becomes the
   contract token; clients branch on the error string.

**Phasing**: P1 = the 12-endpoint family (T5 already landed).
P1.5 = the 3 deltas + artifact_class filter (the SURFACES
layer). The frontend C build (Phase C, gated) consumes
the 15-endpoint family plus the ``artifact_class`` field
on serialization; no further endpoint growth is in scope
for the morning review.

**CRUD-vs-A1 reconciliation**: the A1 frozen contract
states that ``rejected`` and ``approved`` are TERMINAL
states; the PATCH/DELETE endpoint family reuses the
``rejected``/``approved``/``archived`` fence via
``409 VERSION_IMMUTABLE`` (the leader ruling). The
EDIT scope is ``draft`` / ``pending_approval`` ONLY;
no rejected → draft re-open. Resubmit after reject =
a NEW row at ``max(version)+1`` for the key; the
rejected row stays in the lineage.

**Designer-skip rationale**: the designer agent is the
operator-UI consumer; it does NOT consume the
``prompts:*`` scope or any of the 15 endpoints. The
persona-class UI sits behind the designer's
``_full_doc_`` tool surface (the inner_soul /
access_memory pair); the prompt-version surface is
operator-only. The desen's ``od.*`` tools (the
opendesign plugin) are out of scope.

### 8.2 Block #3 — Approval scope split (CORE vs MEMORY class boundary)

Five points + the binding supersessions:

1. **Two write paths, one substrate**: ``inner_soul`` runs
   two write paths side by side on a target-by-target
   basis. The dispatch is class-aware (CORE →
   ``create_and_submit``; MEMORY →
   ``create_memory_version``). The same D10 audit
   identity is threaded through both paths.
2. **Class-boundary ruling (binding)**: the
   ``VALID_TRANSITIONS`` state machine governs CORE
   rows ONLY. MEMORY rows are transition-free
   (``state='approved'`` from the creator). Any
   state-machine op (approve / reject / submit /
   retract / PATCH / DELETE) on a MEMORY row surfaces
   as :class:`MemoryClassError` and is mapped to
   ``409 MEMORY_CLASS_MISMATCH`` at the router.
3. **Supersession of D5** (the Phase B tentative CORE
   binding for user.md): user.md is MEMORY-class now
   (directive overturns D5). The file is NEVER
   prompt-loaded (F2 boundary preserved); the WRITE
   side joins the MEMORY class so the
   ``agent_mother.agent_modify`` bypass closes.
   User preferences take effect on the next prompt
   reload.
4. **Supersession of the memory.md carve-out** (D6):
   memory.md and growth.md JOIN the versioned-immutable
   set (Ud#3 SUPERSEDES D6 / R7). The writer substrate
   is the MEMORY creator; the on-disk file is the
   cold-boot F6 fallback (read by ``access_memory``
   when no DB row exists). The 80% compaction /
   dedup semantics live INSIDE the
   ``_update_memory_md`` composer; the
   post-compaction row materializes as a NEW memory
   version, the pre-compaction content stays in
   history.
5. **Per-slug memory chaining** (Ud#3, memories/<slug>
   keying): ``memories/`` writes use the key
   ``memories/<slug>`` where ``<slug>`` is the legacy
   timestamped filename WITHOUT the ``.md`` extension.
   Consecutive writes on the same slug chain as
   version 1, 2, 3, ... on the same key — the
   collision-counter that derived
   ``<filename>-<n>.md`` is retired; the version
   chain IS the lineage. The loader's
   selection pointer + latest row govern which
   version is "live".

**Phase-contract amendment journal** (binding,
directive-authorized, like the A7 precedent — dated,
dispatcher-approved-pending-morning-ratification):

* **A1 amendment** — ``VALID_TRANSITIONS`` is
  CORE-only. MEMORY rows bypass the state machine.
  The contract table in ``phase-contract.md`` A1
  needs a one-line annotation: the transitions
  are the CORE-class valid set; MEMORY-class
  rows are transition-free (no
  VALID_TRANSITIONS entry; the state column on
  a MEMORY row is just the landed / recallable
  indicator). The ``InvalidTransitionError``
  remains the CORE-only error.
* **A5 amendment** — the ``create_and_submit``
  composite is the CORE path; the
  ``create_memory_version`` creator is the MEMORY
  path. The contract table's A5 row needs a
  one-line note: ``create_and_submit`` is for
  CORE artifacts (state machine +
  ``pending_approval``); ``create_memory_version``
  is for MEMORY artifacts (transition-free,
  born-effective). Both are members of the
  service facade; consumers pick by class.
* **A5 F5 amendment (2026-10-14,
  dispatcher-ratified, leader-gate F5
  approval-as-content-regression finding)** —
  the **composition base for the mutator path**
  inside ``create_and_submit`` is the **prior
  active (draft|pending) row's content** when one
  exists for the same key, NOT
  ``get_effective()`` (which excludes pending
  rows). Formula:
  ``base = prior_active.content if present else
  get_effective() else file_fallback``. Only the
  **no-prior-active branch** falls through to
  the F6 effective chain (selected >
  latest_approved) and the file template; the
  base resolution runs INSIDE the
  ``engine.begin()`` block under the per-key
  advisory lock so the read is consistent with
  the INSERT. The ``str`` content path is
  unchanged. MEMORY-class rows are unaffected
  (``create_memory_version`` is transition-free
  and not part of this path).
  **Rationale** (closes the F5 regression):
  composing on ``get_effective``-only made
  approval a content-regression path — two
  consecutive un-approved mutator submissions
  on a CORE key landed ``v2.content = f(template)
  + delta₂`` and approving ``v2`` silently lost
  ``delta₁`` (the exact regression the C4 "two
  calls = two appends" intent exists to
  prevent). The prior-active base preserves
  ``delta₁`` through the pending window;
  ``get_effective`` stays correct for the
  no-prior-active branch (first submission,
  post-approval re-submission). The contract
  table's A5 row now carries the dated F5
  amendment cell in ``phase-contract.md``;
  consumers (Phase B, inner_soul,
  agent_mother.agent_modify) compose on the
  new base without code change (the repo
  abstracts the seam).
  **Landed in commit ``5bfe1b308``**
  ("fix(prompts): F5 create_and_submit
  composition base — prior active row, not
  get_effective", 2026-10-14 06:02Z) — journal
  pointer added post-landing by the F2/F5
  fix-cycle coder session per the dispatcher
  directive (the fix-cycle dispatch of
  2026-10-14 mandates the amendment entry
  record the landing commit sha; the original
  entry predates its own sha by construction).
* **Pending-count is CORE-only by design** — the
  ``count_pending`` repo method predicate carries
  ``AND artifact_class = 'CORE'``; the
  ``pending-count`` endpoint docstring documents
  the same. The MEMORY path is born-effective; the
  badge source is unchanged.

### 8.3 Block #4 — Preserved classification routing (Directive #4 + RAG retirement ruling CORRECTION)

The classification routing is **preserved VERBATIM in
behavior** — the SURFACES layer does NOT add new
classification logic; it only BUILDS OUT the existing
routing with a documented destination map. The
correction of the §7 (D-SEED-EDGE RESOLVED-DEAD)
language about the RAG retirement ruling: nothing was
built to retire the RAG path, and the directive's
classification routing is BUILD-TO-SPEC, not
rework.

**Principle**: the classification layers in
``_classify_request`` / ``_should_redirect_to_rag``
stay verbatim in behavior. The persona-prefix flag
(Stage 1) sets ``persona_intent_matched``; the
project pre-check (Stage 2) REJECTs project content
unless the dual-match (persona + project) gets
resolved by a persona category (Stage 3); the
normal semantic classification (Stage 3) maps the
request to a target.

**Routing map** (the content-class × target ×
destination matrix):

| Content class | Target | RAG-ON destination | RAG-OFF destination |
|---|---|---|---|
| **Project content** (architecture / gotchas / deploy status) | n/a (REJECT) | RAG via ``experience()`` (queryable via ``explore()``) | existing project-content rejection/hint path (suggests ``project_history_add`` / ``experience``, NEVER silent agent-memory storage) |
| **USER-related content** ("User prefers TypeScript") | ``user.md`` (or implicit) | MEMORY-class direct persist via ``create_memory_version`` (no approval) | same (MEMORY class is RAG-state-agnostic) |
| **Dual-match (persona + project)** | category-matched persona target (identity / personality / user_preference / user_identity / workflow) | CORE submission (soul / workflow) for the resolved persona target | same |
| **CORE targets** (soul / workflow) | explicit | CORE submission via ``create_and_submit`` (state machine + ``pending_approval``) | same |
| **MEMORY targets** (memory / memories / user / growth) | explicit | MEMORY-class direct persist via ``create_memory_version`` (no approval) | same |

**Target-vs-content-class precedence**: the explicit
``target=`` argument routes first (the existing
code's order is preserved); the classification is
secondary. This keeps the existing test suite's
expectations intact.

**Correction of the D-SEED-EDGE RAG retirement ruling**
(DIRECTIVE #4 of 2026-10-14): the SURFACES layer
does NOT retire the RAG redirect; nothing was built
to retire it. The Phase B rewrite's
``_should_redirect_to_rag`` path is the canonical
PROJECT-class-on-RAG-ON destination; the directive
documents it as the binding destination. RAG-OFF
uses the existing project-content rejection/hint
path (this is the "preservation-spec" the directive
calls out — suggesting the right tools, NEVER
silent agent-memory storage).

**RAG no-consume boundary** (R4, verified): the
persona paths (``create_and_submit``,
``create_memory_version``, ``get_effective``) never
query or consume persona-class RAG entries. The
classifier itself (``_should_redirect_to_rag``) is
the only RAG-write surface, and it writes to the
KB via ``experience()`` — a different (KB) lane
from the persona-class rows.

### 8.4 Block #5 — DB-born customs + auto-reset (R-A1/A2/B1-B4 + selection-survival + morning forks)

**R-A1 (DB-born customizations)**: the directive's
"first-time-persistence semantic" is the steady
state. Pre-directive content was swept per R2; new
writes land as DB rows; the file under the agent
dir is the cold-boot F6 fallback only. The
``prompt_versions`` table starts EMPTY at go-live
and grows on agent / operator writes.

**R-A2 (Custom agents have no template fallback)**:
the A2 ruling stands — custom agents are not in the
registry's ``list_all``; the boot auto-reset walker
is registry-only. The lifecycle coder owns the
custom-agent identity; the SURFACES layer does not
land an explicit custom-agent ``is_custom_agent``
flag (the resolution-side hook in ``loader.py`` is
already in place — Ud#5A landing).

**R-B1 (initial-baseline seeding)** — the
``record_initial_baselines_for_all_predefined_agents``
boot call seeds CORE-class baselines for every
predefined agent in the registry. The seeded rows
carry the shipped-template sha256 per
``(agent_id, version_tag, artifact)``. Custom
agents short-circuit to ``[]``. MEMORY-class
artifacts are NEVER recorded (the
``_validate_artifact_class_core_only`` path
rejects them).

**R-B2 (auto-reset on template drift)** — the
``detect_drift_for_all_predefined_agents`` boot
call walks every predefined agent's CORE
artifacts, compares the live file sha256 against
the recorded baseline, and on a hit:
  * stamps a ``TEMPLATE_RESET`` audit row
    (CORE-only, ``payload.via='detect_template_drift'``);
  * re-points the baseline to the new content.
  * preserves the selection pointer if the drifted
    key has a ``is_selected=1`` row (the
    R-B3 selection-preservation check).

**Selection-survival** (R-B3): the auto-reset
walk re-points the baseline but does NOT touch
the ``is_selected`` pointer on the agent's
approved rows. A drift hit on the template file
with an existing selection surfaces
``selection_preserved=True`` in the audit
payload; the agent continues to resolve against
the selected approved row until a human
re-approves. The on-disk drift is operator-visible
(WARN at boot) and NEVER boot-blocking (allow-
stale parity).

**R-B4 (boot orchestration order)**: the
``api.py`` lifespan calls
``record_initial_baselines_for_all_predefined_agents``
FIRST (idempotent — lazy-seeds any missing
baselines) and THEN
``detect_drift_for_all_predefined_agents`` (the
detect pass lands the audit events + baseline
re-points for any drifts found). Both calls are
wrapped in a non-fatal try/except — WARN + continue,
NEVER block boot. Ordered AFTER the
``PromptVersion.metadata.create_all(...)`` /
``PromptAuditEvent.metadata.create_all(...)`` /
``PromptTemplateBaseline.metadata.create_all(...)``
calls (so the schema is in place when the
baselines are seeded). The mirror line in
``_ensure_postgres_columns`` for the
``artifact_class`` column lands ADD-ONLY +
idempotent (the same convention as the rest of
the ``_ensure_postgres_columns`` body) — fresh
PG databases get the column from
``SQLModel.metadata.create_all()`` via the
``PromptVersion`` SQLModel declaration; existing
databases receive the column via the mirror
ADD COLUMN.

**Morning forks** (the deferred-debt register):

* **D-1**: per-plugin staleness policy refinement
  (auto-follow-tags / freeze-with-alarm /
  prompt-on-stale). Out of scope here; per the
  plugin_staleness_policy note, the
  post-v0.18.0 design is the morning fork.
* **D-2**: memory.md disposition flip
  (carve-out re-disposition for memory.md vs
  memory.md-as-MEMORY). The directive
  SUPERSEDES the prior D6 carve-out — the
  morning fork is sealed; the file is
  versioned-immutable.
* **D-3**: user.md disposition. The directive
  SUPERSEDES D5 — the morning fork is sealed;
  user.md is MEMORY-class.
* **D-IDENTITY-STAMPING** (DECIDED, worker review
  2026-10-14, dispatcher-ratified): auth-OFF POST
  ``/api/custom-agents`` stamps ``'anonymous-dev'``
  actor on creation — ACCEPTED because R-A1 makes
  creation the legitimate first-write (no
  approval-gate bypass risk; v1 lands approved so
  the badge never moves). Audit row carries
  ``'anonymous-dev'`` per D-IDENTITY-STAMPING for
  traceability. Documents the deliberate divergence
  from the prompts family (D3 fail-closed) —
  different surface, different posture.

**End of §8.**
