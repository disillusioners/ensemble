# Memory Reconciliation Table — agent-prompt-immutability Phase B

**Date:** 2026-10-13 (T11 deliverable)
**Source:** `phaseB-plan.md` §"Memory Reconciliation Table (artifact → store → owner)" (lines 140-158) — carried verbatim into this standalone artifact for morning review.
**Dispositions extended by:** D5 (user.md joins DB-versioning) and D6 (memory.md carve-out).
**Open questions:** see the `Owner / open question` column — Phase A carved-out items and disposition forks captured there.

This is the consolidated artifact-level disposition table that
codifies WHERE each agent-prompt artifact lives at runtime in the
Phase B world (DB-versioned vs file fallback vs RAG KB vs shared-
context). Columns:

- **Artifact** — the on-disk identifier (with `.md` stripped where the loader convention omits it).
- **Store** — the effective content source at runtime: `DB-versioned (selected → latest approved → file)` per F1, `FILE` (carve-out, unchanged), `RAG KB`, or `shared scaffolding`.
- **Writer (today)** — the call site that writes the artifact today (the pre-Phase-B behavior; frozen for diff context).
- **Writer (Phase B)** — the call site / service after Phase B ships.
- **Reader** — the call site that consumes the effective content.
- **Prompt-visible?** — does the loader surface this content into the system prompt (compose order).
- **Approval required (Phase B)?** — does a Phase B submission of a new draft require human approve / reject per F4.
- **Owner / open question** — Phase B owner for disposition decisions; "open question" rows track the deferred decisions (D5, D6, R7/R11 fork).

| Artifact | Store (effective content source) | Writer (today) | Writer (Phase B) | Reader | Prompt-visible? | Approval required (Phase B)? | Owner / open question |
|----------|----------------------------------|----------------|-------------------|--------|------------------|-------------------------------|------------------------|
| `soul.md` | DB-versioned (selected → latest approved → file fallback per F1) | inner_soul (`:1237`), agent_mother (`:377`) | DB submission via `prompt_version_service.create_and_submit` | loader (compose §1) | YES (section 1) | YES (F4) | Phase B owner |
| `rule.md` | DB-versioned | agent_mother only (`:377`) | DB submission via `prompt_version_service.create_and_submit` | loader (compose §2) | YES (section 2) | YES (F4) | Phase B owner; carve-out removed (was in `modifiable_files` :359) |
| `workflow.md` | DB-versioned | inner_soul (`:1327`), agent_mother (`:377`) | DB submission | loader (compose §7) | YES (section 7) | YES (F4) | Phase B owner |
| `tools_note.md` (fallback `tools.md`) | DB-versioned | agent_mother (`:377`) | DB submission | loader (compose §6) | YES (section 6) | YES (F4) | Phase B owner; legacy `tools.md` is the fallback file |
| `skill.md` (legacy single-skill) | DB-versioned (per Phase A conservative default; rare in wild) | (none active) | DB submission (rare) | loader (compose §3) | YES (section 3) | YES (F4) | Phase B owner; multi-skill via `innate_skills` / `skill-set.yaml` is the modern pattern |
| `memory.md` | FILE (carve-out per D6) | inner_soul (`:1158`), agent_mother (`:377`, still in modifiable_files) | UNCHANGED — atomic rename with compaction (`:1107-1177`) | loader (compose §8) | YES (section 8) | NO (carve-out) | **D6 open question** — Phase B artifact-agnostic; config flip moves it to DB-versioned if `decisions.md` chooses (no schema change) |
| `user.md` | DB-versioned (per D5) | inner_soul (`:1295`), agent_mother (`:377`) | DB submission | `agent_mother.agent_read` ONLY (`:301/:341`); NOT loader | NO (never loaded) | YES (F4 — same as soul/workflow for two-write-path coverage) | **D5 owner ratification** — Phase B owner; disposition is DB-versioned despite non-prompt-visibility to close `_mother` bypass |
| `memories/<filename>.md` | FILE (per D7) | inner_soul (`:1084`) | UNCHANGED — `write_text` with collision counter | `access_memory` (`:32-96`), loader index (filenames only, `:518-520`) | NO (filenames only — `## Recent Memories`) | NO (file-based carve-out) | Phase B owner; migration to DB is a separate commission |
| `growth.md` | FILE (config, never boot prompt) | humans / `agent_mother.agent_read` | UNCHANGED — config file | `_load_growth_rules` (`:1369-1396`) | NO (config) | NO (config) | Phase B owner; out of immutability scope per Phase A boundary |
| `_prompt_system/*` (critical-notes, knowledge, project-experience, innate-skills) | FILE (shared scaffolding) | humans / `_prompt_system/` static content | UNCHANGED | loader (compose §10, §11), innate-skills resolver | YES (§10/§11), runtime-injected (innate-skills) | NO (out of scope) | Phase B owner; not in agent-prompt-immutability scope |
| `meta.json` | FILE (config, already read-only at `agent_mother.py:359`) | humans / `agent_create` (template copy) | UNCHANGED | registry, loader, `_apply_tool_filter` | NO (config) | NO (config) | Phase B owner; out of scope |
| `skill-set.yaml` + `skills-template/*` | FILE (out per F2) | humans / `coder` tooling | UNCHANGED | dynamic-skill subsystem | YES (via innate-skills resolution) | NO (out per F2) | Phase B owner; explicitly out of immutability scope |
| RAG KB (experience → rag_insert_text) | DB (LightRAG / shared-context mirror) | inherent; `experience` (knowledge_tools.py:874-919) | UNCHANGED | `explore` (knowledge_tools.py:679-870), `rag_query*` (rag_tools.py) | YES (via shared-context injection, graph.py:660-754) | NO (out of scope) | Phase B owner; out of immutability scope |

---

## D5 — `user.md` joins DB-versioning

**Disposition:** `user.md` moves from FILE → DB-versioned despite its non-prompt-visibility.

**Rationale:**
- The `_mother` write path (`agent_mother.agent_modify :377`) was a silent bypass — a write to `user.md` did not invalidate any prompt cache, so `user.md` mutations could persist while runtime reads continued to see the prior `user.md` content (Phase A R7/R11 fork).
- DB-versioning forces every `user.md` write through `prompt_version_service.create_and_submit` (the same A5 path as soul/workflow), which stamps an audit event AND triggers cache invalidation via the T7 invalidation sites — closes the `_mother` bypass by construction.
- The reader is `agent_mother.agent_read` ONLY (`:301/:341`) — `user.md` was never loaded into the prompt composition in the first place (per research inner-soul §2(c)), so adding DB-versioning does NOT change the prompt content path.
- Approval required (F4): YES, because approval is the human-side counterweight to the `_mother` write path (no approval = no DB row = file fallback fires).

**Open question (owner = Phase B):** does `user.md` need a separate `USER_ARTIFACT_KEY` entry in the conservative set, or does it share the soul path? The Phase A conservative set covers `soul / rule / workflow / tools_note / tools / skill` (loader.py:77-84) — `user.md` is NOT in the set because it never appears in the loader's prompts dict. Resolution: it doesn't need to enter `_HOOK_ARTIFACT_KEYS` because the DB-versioned content lives in `agent_mother.agent_read`'s fetch path, NOT the loader's prompts dict.

## D6 — `memory.md` carve-out (deferred to `decisions.md` worker)

**Disposition:** `memory.md` stays FILE in Phase B (atomic rename + 80% compaction per `inner_soul.py:1107-1177` unchanged).

**Rationale:**
- `memory.md` has BOTH a boot-prompt-visible role (loader compose §8, the run-time memory section) AND a runtime-write path (inner_soul atomic write). The disposition fork R7/R11 considered three options:
  1. FILE (Phase B default — kept) — atomic rename + compaction stays as-is.
  2. DB-versioned — every memory write becomes a draft → approve → select cycle; doubles the round-trips for routine memory edits (the inner_soul hot path fires on memory compaction, not approval).
  3. DB-stored but NO approval — defeats the F4 invariant ("every DB write is a draft until a human approves").
- The default = (1) FILE because the failure mode of "memory write blocked on human approval" is worse than the current "memory write is silent" (memory compaction is supposed to be invisible). If morning review picks (2), it's a config flip — no schema change.
- The deferral is a worker on `decisions.md` D6, not a Phase B blocker.

**Open question (owner = `decisions.md` worker):** does the morning session ratify (1) FILE or escalate to (2) DB-versioned? Deferred per `phaseB-plan.md` D6.

---

## Cross-references

- **T10** — F1 OVERRIDES-ONLY seeding default; the resolution hook's docstring (loader.py:148-156) carries the same one-paragraph note inline.
- **T13** — `inner_soul` semantics-parity tests pin memory.md + memories/ write paths to remain unchanged (covers D6 default).
- **R7 / R11** — disposition fork records in `decisions.md` that pre-date Phase B and feed this table.

## Addendum — User Directive 2026-10-14 (binding migration policy reversal)

This addendum records three facts that supplement (do NOT replace) the
matrix above. Full directive text + rulings R1–R6 live at
`decisions.md §7`.

1. **Mechanism unchanged per R6 (D-ARTIFACT-DISPOSITION preserved).**
   The carve-out default for `memory.md` (row 6 above — file) and
   the carve-out default for `memories/` (row 8 above — file) are
   unchanged. The `inner_soul` write paths (`_update_memory_md`,
   `_update_memories`) and the `access_memory` read path are unchanged.
   The directive does NOT re-open the disposition fork; it confirms
   the file default and clears pre-directive accumulated content.

2. **Accumulated content archived per the directive (commit `3c307e8d`).**
   - `agents/<name>/memory.md` — 11 files → archive (table row 6,
     pre-directive content cleared).
   - `.agents/{id}/memories/*.md` — 177 files → archive (orphan layout,
     zero code refs per R3).
   - `agents/<name>/memories/` — 0 tracked files by-construction; new
     release trees start clean (table row 8 unchanged).
   - Total archived: 188 files (preserved with git history; see
     `legacy/agent-prompt-immutability-sweep/2026-10-14-user-directive/README.md`
     for the manifest + reversibility note).

3. **RAG no-consume boundary per R4 (verified).** The persona-path
   `create_and_submit` / `get_effective` surface
   (`daemon/repositories/prompt_version/` +
   `daemon/services/prompt_version_service.py`) does NOT query or
   consume persona-class RAG entries. The `inner_soul` RAG-redirect
   path (`daemon/tools/inner_soul.py:230-276`) is non-project only
   and explicitly carves out `memory` / `memories` — that boundary
   predates this directive and is preserved. **No KB purge required.**

The matrix above + the deferred decisions referenced (D5 / D6) are
otherwise unchanged by this directive. F1 OVERRIDES-ONLY (`decisions.md
§1`) now ships the sealed state (no seeding, no edges, empty DB at
go-live) — `D-SEED-EDGE` is RESOLVED-DEAD per the directive.

**End of addendum.**