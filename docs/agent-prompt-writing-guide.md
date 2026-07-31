# Agent Prompt Writing Guide

**Audience:** humans and agents (e.g., `_mother`) authoring or editing agent prompts under `agents/<id>/`.

This guide codifies the conventions learned from reviewing the v2 agent prompts. Every rule below exists because a real prompt violated it and caused drift, contradiction, or leaked system internals into the agent's identity. Follow it before opening a PR that touches any `.md` under `agents/`.

---

## 1. The Core Principle: Write As The Agent, Not About The System

Agent prompts **build the agent**. They are read by the agent as its own self-concept and operating manual. Write in **first person** ("I", "my") and describe what the agent **does, sees, and is allowed to do** — never describe the system that hosts it.

### The forbidden layer: system internals

An agent must never be told about the machinery that loads, validates, or runs it. If a concept only matters to a daemon developer or a loader implementation, it does not belong in a prompt file. Concretely, **never** reference these inside prompt prose:

- `meta.json` (the file itself — the agent does not edit meta.json)
- `tools.allow` / `tools.deny` (the `meta.json` object — agents state what they *can* do, not which keys configure it)
- `daemon/tools/_tool_registry.py`, `daemon/tools/instance.py`, or any `daemon/` path
- `skill-set.yaml` as a file the agent reads or bumps (the agent may say "my skill versions stay consistent" — see §6)
- `innate_skills` (the `meta.json` field — the agent just "has" skills)
- `auto_load` field mechanics ("auto-loads at runtime" is fine; naming the yaml key is not)
- seeder / `seed_all` / `SkillSeedService` / seeding pipeline
- `agent_id=` plumbing, `developer[v2]`-key vs `agent_id=developer` mismatches (these are loader bugs, not agent knowledge)
- version-tag / registry resolution / `get_version` / `resolve_to_id`
- test file paths (`tests/unit/...`)
- `default_agent_versions` config concept

#### Do / Don't

| ❌ Don't (system POV) | ✅ Do (agent POV) |
|---|---|
| "The grant in `meta.json` `tools.allow` is broad; this allow-list narrows it." | "My direct use is read-only and bounded to this allow-list; everything else is dispatched." |
| "Real signature (verified from `daemon/tools/instance.py`):" | "Signature:" |
| "Validated against `daemon/tools/_tool_registry.py`. Adding a non-existent category is a fail-fast." | (drop entirely — the agent doesn't add tool categories) |
| "If the skill bank evolves a skill, bump `skill-set.yaml` in lockstep with the `.md` frontmatter." | "Keep my skill versions consistent: the `.md` frontmatter version is the source of truth, and any manifest that lists a skill must match it." |
| "Auto-loads via `skill-set.yaml`'s `auto_load: true` (not in `meta.json` `innate_skills`)." | "My own planning skill auto-loads at runtime (separate from my innate `todo`/`chart`/`dynamic-skill`)." |

### What *can* be referenced (agent-facing concepts)

These are legitimate because the agent calls them directly or reasons about them operationally:

- Tools it actually invokes (`spawn_instance`, `send_message`, `load_skill=`, `git status`)
- The **skill bank** as "where skills live" (the agent runs `skill_search`/`skill_view`/`skill_feedback` on it)
- Symptoms of system failures the agent must handle ("a skill may silently fail to load at runtime" — the agent needs the symptom, not the loader internals)
- Its own files it genuinely introspects (`skill-set.yaml` only if the agent actually reads it for self-checks — see §6)
- Cross-agent contracts it participates in (`.agents/shared/active.md` ESCALATED status — but only the observable contract, not who writes it internally)

When in doubt: **if the agent cannot act on the information, delete it.**

---

## 2. File Roles — One Concern Per File

The prompt is composed in order: `soul` → `rule` → skills → `tools_note` → `workflow` → `memory`. Each file owns exactly one concern. Don't duplicate content that belongs to another file.

| File | Owns | Does NOT own |
|------|------|--------------|
| `soul.md` | Identity, personality, tone, output templates (top-level shape) | Step-by-step process, tool lists, tier tables |
| `rule.md` | Hard constraints the agent never violates | Workflow sequencing, prose restating rules |
| `workflow.md` | Step-by-step process, dispatch snippets, fan-in mechanics | Identity, immutable rules |
| `tools_note.md` | Tool-by-tool reference (what each tool does, when to use/avoid) | Dispatch patterns, tier selection, rule restatements |
| `*-strategy.md` (auto-loaded skill) | The *single canonical home* for repeated artifacts: tier tables, skill-selection guides, output formats | Identity |
| `memory.md` | Long-term knowledge, calibration tables, trigger checklists | Process mechanics |

### The canonical-home rule (most important dedup principle)

If an artifact (a tier table, a dispatch snippet, an output template, a contract string) appears in more than one file, **pick one canonical home and make the others link**. Otherwise the copies drift, and drift is how contradictions are born.

| ❌ Don't | ✅ Do |
|---|---|
| Restate the tier table in `soul.md`, `workflow.md`, `tools_note.md`, and the strategy skill | Put it once in `dev-strategy.md`; `soul.md:60` says "→ see dev-strategy §Scope" |
| Copy the `skill_feedback` contract verbatim into 4 files | Put it once in the strategy skill; dispatch prompts reference it |
| Full Dev Plan template in both `soul.md` and `dev-strategy.md` | `soul.md` holds the shape; the canonical fields live in the strategy skill |

#### The "stated once" trap

Do **not** write "this contract is stated once, in `X.md`; I do not maintain parallel copies" unless the copies are actually removed. A false single-source claim is worse than honest duplication — it actively misleads readers into trusting one copy while three others drift. If duplication is genuinely necessary (e.g., a worker reads its own dispatch prompt and can't see the canonical file), say so honestly: "canonical copy at `X.md`; worker-prompt mirrors are illustrative, keep them in sync."

---

## 3. `rule.md`: Cardinal Rules + Guidelines

Flat 30-rule lists dilute the load-bearing invariants. Models obey short top-of-context rules better than long enumerated walls. Split:

- **Cardinal Rules (≤7, top of file)** — the non-negotiables. "NEVER write code directly.", "ALWAYS dispatch.", "End turn after dispatch." These are the rules the agent must survive context compression.
- **Guidelines (the rest)** — style, scope-routing, naming, fan-in mechanics. Numbered but explicitly secondary.

Delete duplicates. If `rule.md:14` and `rule.md:30` say the same thing, they were the same rule — collapse to one.

### Cross-reference hygiene

If you renumber `rule.md`, **sweep every `§N` pointer in sibling files the same commit.** Stale positional refs (`rule.md §9` now pointing at an unrelated rule) are the most common regression from a cardinal-split refactor.

Prefer **semantic labels** that survive renumbering:

| ❌ Fragile | ✅ Stable |
|---|---|
| "rule §9" | "Cardinal #3" / "Guideline #19 – Read-Only Discipline" |
| "rule.md §14" | "rule.md → Skill-Bank & Fallback" (section name) |

After any `rule.md` change, run a grep for `rule.md §` / `rule §` across the agent's directory and verify every hit still resolves.

---

## 4. Tool Permission Boundaries: Statements, Not System Reasoning

Agents hold tools and operate within boundaries. State the boundary operationally — what the agent may and may not do with each tool — not the system that enforces it.

| ❌ System reasoning | ✅ Operational statement |
|---|---|
| "The grant in `meta.json` `tools.allow` is broad; this allow-list narrows it." | "I hold `bash` + `filesystem` but my direct use is read-only to this allow-list; everything else is dispatched." |
| "`convene_council_with_skill` is the public entry point for any agent with `council` in `tools.allow`." | "`convene_council_with_skill` is my entry point for Deep-Review; `spawn_councilor` is identity-guarded to the governor and I cannot call it." |
| Listing every allowed tool with "validated against the registry" prose | A compact table of category → why I hold it (no registry mention) |

### Allow-list vs deny-list: which to document where

- Use an **allow-list table** in `rule.md` / `tools_note.md` for the operational boundary (what the agent does, row per tool).
- Put a **machine-enforced `tools.deny`** in `meta.json` for safety-critical prohibitions (`edit_file`, `write_file`, `apply_patch`, `git_commit`, `db_*`) — so a worker whose skill fails to load still cannot mutate state. Prose in a skill file is bypassed the moment the skill bank misses; `meta.json` deny is not.
- Don't describe the meta.json deny mechanism in prose. The agent doesn't need to know it exists.

---

## 5. Tone & Voice Directive

Every agent should have a short tone block (typically in `soul.md`) covering:

- **Voice to caller** — terse/structured, evidence-cited, no preamble.
- **Voice in dispatch prompts** — imperative, self-contained (the worker reads only its own message).
- **Per-severity framing** (where the agent emits severity labels) — e.g. "🔴 non-negotiable, state the risk concretely; 🟢 invites, doesn't demand".

Without this, outputs drift per run: a 🔴 reads too soft, a 🟢 reads too aggressive, the lead asks for rewrites.

---

## 6. Skills: My Skills vs Dispatched Skills

The most common ownership confusion: an agent auto-loads a planning skill (e.g. `dev-strategy`, `review-strategy`) AND dispatches execution skills onto workers (e.g. `code-fix`, `code-review`). These are two different ownership relationships. State the boundary explicitly:

> My own `*-strategy` skill is for my planning only; never embed it in a worker dispatch. Execution skills (`code-fix`, `git-commit`, …) are pulled by workers via `load_skill="..."` — they are never auto-loaded for me.

### Version consistency

Skill versions feed `skill_feedback` attribution. If the `.md` frontmatter says `1.0.0` but the manifest says `1.2.0`, the agent emits wrong attribution data. State the rule agent-facing:

> Keep my skill versions consistent: the `.md` frontmatter version is the source of truth; any manifest listing a skill must match it.

Don't name the manifest file (`skill-set.yaml`) in the bump instruction unless the agent genuinely edits it — and if it does, scope the reference to "my own `skill-set.yaml` structure" only where the agent actually reads it.

---

## 7. Async Dispatch: END TURN And Escape Valves

### END TURN contract

After `send_message` (or `convene_council_with_skill`), **END YOUR TURN**. State the *why* so the agent obeys rather than "helpfully" polling:

> Holding the turn open blocks report delivery and deadlocks the run. The system resumes my turn automatically when each instance reports.

State it **once** in `workflow.md`; other files reference it. Do not copy the full paragraph into 4 files.

### Fan-in escape valve

A dispatcher that fans out to N workers must define what happens when one never reports. Without an escape valve, a single crashed worker dead-ends the whole run silently. Every dispatcher's `workflow.md` should define a ladder:

```
1. Confirm stuck (worker error/crash report, or staleness signal)
2. Re-dispatch ONCE (spawn a replacement with the same load_skill)
3. If still empty/stuck → mark node [incomplete], deliver partial + ### Gaps
4. Max re-dispatch = 1 (two failures = escalate, not retry)
```

Cardinalize the "never silently incomplete" rule. State the cap explicitly ("max 1 re-dispatch") so the agent doesn't loop forever on a flaky worker.

### Batching

For parallel fan-out, state whether END TURN is per-dispatch or per-batch. Pick one and document it:

> For LARGE scope I may spawn 2–3 workers in one wave and then END TURN once (after the batch). Per-dispatch END TURN is NOT required for parallel fan-out within a single wave.

---

## 8. Skill-Bank Fallback Paths

When a dispatched `load_skill` fails to resolve (skill bank miss, version mismatch, seeding gap), the agent must not silently dispatch a skill-less worker and trust it. Define a fallback **within the agent's own tier**:

> If `load_skill="code-review"` fails (skill bank missing), spawn a second `coder` (or `worker` without `load_skill`) with a detailed manual-review prompt and flag the run as `DEGRADED — skill bank miss (code-review)` in the Dev Report.

### The org-chart rule for fallbacks

A fallback must stay within the agent's `team_members`. Do **not** invent a fallback that spawns an agent not in `team_members` — that references an unreachable peer and the spawn silently fails. Two valid fallback shapes:

1. **Detect and surface** — flag `DEGRADED` in the report, retry the load once, or hand back to the caller.
2. **Peer-within-tier** — spawn another `coder`/`worker` with a manual prompt.

Escalation across agents (e.g., developer → reviewer) is the **caller's** job (leader decides), not the agent's. Don't write "spawn a `reviewer` agent instance" into a developer prompt — `reviewer` is a peer of leader, not a child of developer.

---

## 9. v1 → v2 Migration

When an agent is versioned (`agents/foo[v2]/` alongside `agents/foo/`), record what changed. A one-line deprecation note in `meta.json` or a `memory.md` block prevents the next author from relitigating settled decisions:

> v2 changes: dispatcher pattern (was direct implementer in v1); cardinal rule split; fan-in escape valve added. Activation: `default_agent_versions` records the switch.

If the agent reuses a v1 file (e.g., `reviewer[v2]` pointing at `agents/reviewer/memory.md`), **create a v2-local copy** and repoint the references. Cross-version file dependencies silently break when the v1 dir is removed.

---

## 10. Pre-Commit Checklist

Before committing changes to any agent prompt:

- [ ] **No system internals** — grep the agent dir for `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions`. If any appear in prompt prose, reword to agent POV (see §1).
- [ ] **One canonical home per repeated artifact** — no verbatim table/snippet/template duplicated across files. Cross-references use section names or stable labels.
- [ ] **No false "stated once" claims** — if you write "I do not maintain parallel copies," verify the copies are actually gone.
- [ ] **`rule.md` has ≤7 Cardinal rules**; the rest are Guidelines; no literal duplicates.
- [ ] **Cross-references resolve** — after any `rule.md` renumber, grep `rule.md §` / `rule §` and confirm every hit still points at the intended rule. Prefer `Cardinal #N` / `Guideline #N` / section-name labels.
- [ ] **Tone directive present** in `soul.md` (caller voice + dispatch voice + per-severity framing if applicable).
- [ ] **Fan-in escape valve defined** in `workflow.md` for any dispatcher (stuck-worker ladder, max-re-dispatch cap).
- [ ] **Skill versions consistent** — `.md` frontmatter matches the manifest; no drift.
- [ ] **Fallbacks stay within `team_members`** — no "spawn `<peer agent>`" instructions for agents not in `team_members`.
- [ ] **No adapted-from / migrated-from / verified-from provenance** in prose — these are authorship annotations, not agent knowledge.
- [ ] **Tests still pass** — `pytest tests/unit/test_<agent>_v2_agent.py` (if present) and spawn/team-member tests.

---

## Appendix: Common Violation Patterns

Each of these was found in a real v2 prompt and corrected. Use as a reverse-pattern catalog.

### A.1 "Broad grant narrows it"
```
❌ The grant in `meta.json` `tools.allow` is broad; this allow-list is the
   operational contract that narrows it. (Workers I dispatch get their own
   read-only enforcement inside each review skill — e.g. code-review.md.)
✅ I hold `bash` + `filesystem` but my direct use is read-only and bounded
   to this allow-list; everything else is dispatched.
```

### A.2 "Verified from daemon path"
```
❌ Real signature (verified from `daemon/tools/instance.py:901-956`):
✅ Signature:
```

### A.3 "Auto-load mechanism internals"
```
❌ Auto-loads via `skill-set.yaml`'s `auto_load: true` (not listed in
   `meta.json` `innate_skills` — those are todo/chart/dynamic-skill;
   the two mechanisms are distinct).
✅ My own planning skill auto-loads at runtime (separate from my innate
   todo/chart/dynamic-skill).
```

### A.4 "Bump the manifest file"
```
❌ If the skill bank evolves a skill, bump `skill-set.yaml` in lockstep
   with the `.md` frontmatter.
✅ Keep my skill versions consistent: the `.md` frontmatter version is
   the source of truth; any manifest listing a skill must match it.
```

### A.5 "Spawn a peer agent not in team_members"
```
❌ If `load_skill="code-review"` fails, spawn a `reviewer` agent instance.
✅ If `load_skill="code-review"` fails, spawn a second `coder` with a
   manual-review prompt and flag `DEGRADED — skill bank miss` in the report.
```

### A.6 "Adapted from" / "Migrated from" provenance
```
❌ — adapted from `agents/tester/workflow.md` line 67
❌ These are migrated from `agents/tidier/memory.md` (v1).
✅ (drop the line entirely — authorship provenance is not agent knowledge)
```

### A.7 Stale positional cross-reference
```
❌ re-dispatch once … (rule.md §9)
   [after renumbering, §9 is now "Skill must match category", not "never poll"]
✅ re-dispatch once … (Cardinal #3 — End turn after dispatch)
```

### A.8 Triplicated table
```
❌ Skill Selection Guide appears in soul.md, workflow.md, AND review-strategy.md.
✅ Put it once in review-strategy.md (the auto-loaded canonical skill);
   soul.md and workflow.md say "→ see review-strategy §Skill Selection".
```

---

## Appendix: File Quick Reference

| File | Required? | Max length guidance | One-line purpose |
|------|-----------|---------------------|------------------|
| `meta.json` | Yes | — | Metadata, tools, team_members (system-facing; not prompt prose) |
| `soul.md` | Yes | ~2k chars | Identity, personality, tone, output template shape |
| `rule.md` | Yes | ≤7 Cardinal + Guidelines | Hard constraints; never-violate invariants at top |
| `workflow.md` | Optional | — | Step-by-step process, dispatch snippets, fan-in, escape valve |
| `tools_note.md` | Optional | — | Tool-by-tool reference; operational allow-list tables |
| `skill-set.yaml` | If skills | — | Skill manifest (data; minimal prose) |
| `skills-template/*.md` | If skills | — | One file per skill: contract, focus areas, mandatory format |
| `memory.md` | Optional | ~2k words | Long-term knowledge, calibration tables, trigger checklists |
| `memories/` | Optional | per-file | Timestamped observations |

Prompt composition order (from `daemon/loader.py`): `soul` → `rule` → innate skills → tools doc → `tools_note` → `workflow` → `memory` → recent memories → knowledge → project-experience. Put each artifact where its composition-phase reader expects it.
