# Plan Overview: Agent-Prompt Immutability

Date: 2026-10-13
Branch: `feature/agent-prompt-immutability` (base `latest @ e9cdc25c6`)
Worktree: `/home/ensemble-worktrees/agent-prompt-immutability`
Status: **Complete — overnight-conservative-defaults** (user asleep; every fork decided with a default, alternatives documented, morning-review hooks in place). **Amended 2026-10-13 by the review fold-in (see §0).**
Planner instance: planner[v2] (dispatcher); deliverables authored by 3 explorer + 3 worker instances (see Instances below)

---

## 0. Binding Review Amendments (fold-in, 2026-10-13)

The composite passed reviewer verdict **APPROVE-CONDITIONAL**; the condition — a pre-code documentation fold-in landed as ONE commit before any Phase A code — is executed and **binding**:

- **`architecture-recommendation.md`** (architect addendum): verdict ENDORSED w/ upgrades; **Approach B (plan + amendments) RECOMMENDED**. Its §3 register (C1–C7 criticals, S-register significants, §6 test-wall strategy) is binding on the phase plans.
- **`phase-contract.md`** (fold-in G-1): the **frozen Phase A→B contract, amendments A1–A8** — 13 repo methods, `actor` threading, amended `VALID_TRANSITIONS` skeleton (rejected terminal), `create_and_submit` composite + C4 composition rule, sync hook signature (repo, NOT async facade), fail-open hook posture, `UNIQUE(agent_id, version_tag, artifact, version)` + INSERT-retry, single-SELECT effective read (fingerprint-bust-on-get).
- **Leader-accepted D-1**: Phase-A inner_soul 2-site write shim (soul/workflow sites route through the shared guard) → **phaseA T11**.
- **Leader-accepted D-2 (extended)**: this doc-fix bundle = the **explicit FIRST Phase A commit** — decisions.md endpoint-family reconciliation (canonical 12-endpoint family), S8 403→503, S5 user.md carve-in, S19 restore-drift caveat, C1 R10 reword, S17 `modifiable_files=[memory.md]`, migration-count 92, plus the O-item register.
- **SCOPE strike (S18)**: `soul_requires_approval` knob-wiring STRUCK from task lists (knob stays dead code pending morning review); only the rejection-hint update ships.
- **D-3 (unchanged by triage)**: `memory.md` disposition + `D-SEED-EDGE` carryover remain the two user-flavored morning forks; both endorsed as-defaulted — the only forks the morning review is expected to move.

---

## 1. Objective

Make agent prompt `.md` files under `agents/**` **immutable base/template content** (runtime writes fail loudly), move all prompt/persona updates into a **DB version store** keyed `(agent_id, version_tag, artifact)` with monotonic version codes and **user-approval states** (`draft → pending_approval → approved | rejected`), resolve the effective prompt by **`selected > latest_approved > template file`**, rewrite **`inner_soul`** (and the `agent_mother.agent_modify` second write path) to submit DB drafts instead of writing files — with **existing agents provably unregressed** and the 25k+ test wall green or fully classified.

Testable as one sentence: *delete an agent's `soul.md` from disk and it still spawns with the same prompt (latest-approved DB version); an agent's `inner_soul(target="soul")` call produces a `pending_approval` row a human can approve over HTTP; runtime write attempts against the file fail loudly; agents that never used overrides behave byte-identically.*

## 2. Deliverables & Reading Order

All files in this directory. **Read `decisions.md` first** (it is the fork register the morning review starts from), then phases in execution order.

| # | File | Lines | Author | What it is |
|---|------|-------|--------|------------|
| 1 | `decisions.md` | 667 | technical-analysis worker | Fork register (F1–F9 leader forks + 8 research-surfaced D-* forks, each: decision/rationale/alternatives/morning-hook), compatibility & migration proof (§2), risk register (§3), architect open questions (§4), TL;DR morning table (§6) |
| 2 | `phaseA-plan.md` | 219 | plan-creation worker | **Foundation**: migration + SQLModel + repository + service facade + resolution hook at the loader choke point (all 3 call sites) + fail-loud immutability guards + PromptCache/invalidation fixes (10 tasks, 13 success criteria) |
| 3 | `phaseB-plan.md` | 240 | plan-creation worker | **Workflow**: approval state machine (rejected terminal per A1), 12-endpoint REST API (S4 canonical family; human-only approve via `auth_method=="session"`), inner_soul rewrite (semantics preserved verbatim + C4 composition), `agent_mother.agent_modify` rewire, memory reconciliation, overrides-only seeding |
| 4 | `phaseC-plan.md` | 181 | plan-creation worker | **Gated minimal UI**: 3 explicit gate conditions (+1b auth-ON env, +2b tsc toolchain); list/detail/diff/approve/reject/selection following skill-bank patterns; zero new npm deps; fallback = Phase B REST is the complete approval path |
| 5 | `architecture-recommendation.md` | 183 | architect (review addendum) | **Binding** review verdict: C1–C7 criticals, S-register, §5 trade-off matrix (Approach B), §6 test-wall strategy, §8 D-1/D-2 leader triage |
| 6 | `phase-contract.md` | — | fold-in G-1 (planner as doc owner) | **Frozen Phase A→B contract A1–A8** |
| 7–9 | `research-findings-{prompt-loading,inner-soul,db-api}.md` | — | 3 explorer instances | Worktree-verified evidence base (file:line) all plans cite |

> **Numbering note:** each phase file keeps a self-contained D#/T#/R#/C# sequence (Phase A its own; Phase B+C share a continuous D1–D21/T1–T25 space; decisions.md uses F*/D-*/R1–R14). Treat risk/task IDs as file-scoped.

## 3. Phase Index

| Phase | Name | Objective | Depends on | Coupling |
|-------|------|-----------|------------|----------|
| **A** | Foundation | Schema/migrations/repository/resolution service/immutability guards + cache-invalidation correctness (incl. 6 pre-existing bug fixes) | nothing (root); `decisions.md` artifact-disposition is config-level only (schema is artifact-agnostic by design) | tight with B (API contract: `get_effective`, `atomic_transition(actor)`, audit-event shape) |
| **B** | Workflow | Approval service + REST API + inner_soul + agent_mother rewrites + memory reconciliation + seeding | Phase A API contract (fail-fast blockers if absent) | tight with A; loose with C (REST contract only) |
| **C** | Minimal UI (GATED) | Review/approve surface for human operators | Gate 1: Phase B REST merged; Gate 2: Phase B tests green on both engines; Gate 3: remaining overnight budget + explicit dispatcher signal | loose (consumes REST only) |
| **D** | Follow-ups (documented, not scheduled) | Watcher process-global caches (`graph.py:10665-10747`), bash OS-level fence, `memory.md` disposition flip execution if morning review changes it | morning review | — |

## 4. Component Map (execution surfaces)

| Component | Sites | Phase |
|-----------|-------|-------|
| DB schema | new `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_prompt_version_tables.sql` (grouped file, 2+ tables) + `daemon/repositories/prompt_version/{models,repository}.py` | A |
| Service | new `daemon/services/prompt_version_service.py` (async facade, no engine) + wiring `daemon/manager.py:1171-1173` + factory/re-export | A |
| Resolution hook | `daemon/loader.py` between `:920` (cache-miss reload) and `:926` (compose) — covers spawn `instance_lifecycle.py:2096`, restore `:4559` (closes the restore drift window), reconstruction `persistence.py:1053-1157` | A |
| Cache correctness | `daemon/loader.py:748/:809-819` key+invalidate; fixes at `agent_mother.py:380`, `inner_soul.py:1088/:1162/:1247/:1304/:1336`, `instance_messaging.py:1063` | A |
| Immutability guards | shared `check_write()` helper in a new single-source module `daemon/tools/prompt_artifacts.py` (`VERSIONED_PROMPT_ARTIFACTS` frozenset + refusal builder + `GUARD_ERROR` fail-closed posture), invoked at exactly TWO post-resolve call sites — `write_file` post-`filesystem.py:889` pre-open AND `edit_file` post-`:1243` pre-write — never on read paths (`read_file`/`list_directory` share the resolver); absolute AND relative forms covered — closes the `:205-209` bypass; `agent_mother.py:359` allowlist shrink; legal exceptions preserved (agent_create / routers POST template copy / delete→`_trash`) | A |
| Approval + API | new `daemon/routers/prompts.py` registered at `daemon/api.py:3575-3620`; scopes `prompts:read/submit/approve/write/audit` (S4: 5 scopes, all in `SCOPE_TAXONOMY`); auth-OFF fail-closed 503 | B |
| inner_soul rewrite | `daemon/tools/inner_soul.py` soul/workflow → DB drafts (auto-submit default); soul history audit file → DB audit events; memory/memories unchanged; classification/redirect/reject grammar preserved verbatim | B |
| agent_mother | `daemon/tools/agent_mother.py:349-393` rewire to DB submission | B |
| Frontend | new `prompt-version.service.ts` + `/prompts` routes/pages (skill-bank patterns) | C (gated) |

## 5. Fork Summary (full detail in `decisions.md` §1)

Conservative defaults standing overnight: **F1** overrides-only seeding (no boot seeding) · **F2** prompt `.md` only (skill-set.yaml/skills-template untouched) · **F3** monotonic int per `(agent, version_tag, artifact)` · **F4** draft→pending→approved/rejected, approver = authenticated human ONLY, approved rows append-only, rejected terminal (A1) · **F5** explicit selection pointer · **F6** precedence `selected > latest_approved > template`, non-approved never effective · **F7** daemon-side approval core, UI gated · **F8** fail-loud typed refusals · **F9** (planner-surfaced) versions keyed by resolved agent identity incl. `version_tag` — no tag↔base leak · **D-ARTIFACT-DISPOSITION** carve-out of `memory.md`/`growth.md` + `user.md` carved IN per S5/phaseB D5 (memory.md = highest morning-flip likelihood; schema is artifact-agnostic so a flip is config, not migration) · **D-AUTH-OFF** fail-closed 503 `AUTH_NOT_CONFIGURED` · **D-BASH-ESCAPE** accepted-risk + OWNED drift leg (phaseA T13 sha256 baseline, non-blocking per allow-stale) · **D-WATCHER-CACHES** accepted-risk → Phase D · **D-RESTORE-DRIFT** hook covers restore with the S19 three-case caveat (no-DB-rows agents keep today's drift semantics; mid-session approvals surface at restore — human-gated behavior change) · **D-SEED-EDGE** manual operator migration for historical carryover (second-highest flip likelihood) · **D-IDENTITY-STAMPING** split submitter columns (instance/agent/tag) / approver=AuthContext.subject.

## 6. Top Risks (deduplicated across the three registers — full tables in each file)

1. **Artifact-disposition morning flip** (`memory.md` in/out of versioning) — mitigated by artifact-agnostic schema; flip = config change (decisions R1).
2. **25k+ test-wall exposure** — blast radius bounded: 6 invalidate sites, inner_soul test packs, cache-key tests, skill-seed boot-scan; run those packs first and classify tests asserting old (buggy) behavior (decisions R3, phaseA/phaseB test sections).
3. **Phase A/B contract drift on landing split** — Phase B T1–T3 fail-fast with explicit blockers if Phase A misses `actor` param / `get_effective` / audit shape (phaseB R14).
4. **Release-tree persona-write incidents until guards land** — known recurrence class (2026-10-01 ×2); guards close it (decisions R4).
5. **Approval deadlocks while user away** — mitigated by F6: pending versions are invisible to resolution; agents continue on the old effective prompt (decisions R5).
6. **Restore-path behavior change** — restored instances now see DB-resolved content (today: drift to newer files); release-notes item (D-RESTORE-DRIFT).
7. **bash escape hatch** — cannot be guarded in-tool; accepted-risk + drift detection + documented OS-fence alternative (D-BASH-ESCAPE).
8. **Watcher process-global caches bypass the pipeline** — accepted-risk, Phase D (D-WATCHER-CACHES).

## 7. Research Insights That Shaped the Plan

- **Single choke point**: `load_and_cache_prompt` (`daemon/loader.py:822-932`), exactly 3 call sites (spawn / checkpoint restore / message reconstruction); prompt is read per spawn+restore, NOT per turn — so the resolution layer hooks one function, not a scatter of readers.
- **Restore drift window exists today**: checkpoint restore re-reads CURRENT files — an instance resumed after a template edit silently sees different content than at spawn. The DB resolution closes this by construction.
- **Blanket immutability is wrong**: `memory.md` is both boot-prompt §8 AND runtime-written with compaction; `user.md` is never prompt-loaded; `growth.md` is config-only. The disposition fork (carve-outs) exists because of this.
- **Two write paths, not one**: besides `inner_soul` (6 write surfaces incl. the soul history audit file), `agent_mother.agent_modify` writes prompt `.md` directly — an inner_soul-only rewrite would leave a bypass.
- **Absolute-path bypass**: `write_file`/`edit_file` absolute paths bypass the workdir boundary (`filesystem.py:205-209`) — the guard must sit inside/after the resolver to cover both forms; `bash` remains an accepted-risk escape hatch.
- **Memory landscape is already consolidated**: `agents/{name}/memories/` (agent-name-keyed, filename-index-only in prompts, access_memory fetch), `memory.md` (agent-local state), RAG KB (shared project knowledge, dual-state by `LIGHTRAG_HOST`); `.agents/{id}/memories` has ZERO code references — nothing to migrate.
- **Blast radius**: 43 agent dirs on disk (41 meta-bearing — excludes `_prompt_system/`/`_inner_soul/`); 28/41 scanned agents hold inner_soul+access_memory; 13 do not.
- **Dual-driver migration truth**: the SQL runner is SQLite-only by design; PG truth = SQLModel `create_all` (fresh) + `_ensure_postgres_columns` (existing). New tables need no ensure-mirror; index/constraint names must be byte-identical between model and `.sql`.
- **Auth lanes are clean**: `auth_method == "session"` = human (OIDC), `"bearer"` = machine; identity must be stamped from `AuthContext`, never body-asserted (R3 ticket 257488dc).
- **Reusable precedents**: `JobStateMachine.VALID_TRANSITIONS` (approval states), `Skill.generation`+lineage+`is_active` (versioned rows + pointer), `drift_events` (audit table), `doc_write.py` (allowlist/denylist guard discipline).

## 8. Acceptance Criteria Mapping (mission → where proven)

| Mission acceptance | Proven by |
|---|---|
| Agent `.md` provably immutable at runtime | Phase A guards + parametrized guard-bypass tests (incl. absolute-path attempts) |
| DB versioning with approval states working | Phase A schema/transition skeleton + Phase B service + transition happy/illegal tests |
| Resolution precedence tested | Phase A three NAMED resolution-path tests (selected / latest-approved / template), SQLite+PG parity |
| inner_soul submitting DB versions | Phase B rewrite + semantics-parity test pack |
| Existing agents unregressed | Phase B 28-agent no-regression gate + decisions §2 zero-regression proof + no-override snapshot test |
| 25k+ wall green or classified | Risk #2 mitigation (bounded blast radius, pack-first ordering) |

## 9. Instances

| Instance | Agent | Role |
|---|---|---|
| `1775e153` | explorer | prompt-loading research |
| `611a081d` | explorer | inner_soul + memory landscape research |
| `88abfebd` | explorer | DB/migrations/routers/auth conventions research |
| `640a7568` | worker (plan-creation) | phaseA-plan.md |
| `d27c4b62` | worker (plan-creation) | phaseB-plan.md + phaseC-plan.md |
| `4a13085a` | worker (technical-analysis) | decisions.md |
| planner[v2] | — | dispatch, fan-in tracking, this synthesis |
