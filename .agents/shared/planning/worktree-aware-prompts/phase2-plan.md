# Phase 2: Leader + Developer Awareness

## Objective

Encode the awareness side of the worktree contract in the two agents that actually drive or perform the concurrent-edit flow — the **leader** (pre-registers per-task census rows BEFORE spawning giter, spawns giter FIRST, then dispatches editors with the MANDATORY non-empty `context={"wt_path": …, "wt_slug": …, "wt_branch": …}` per U2) and the **developer** (commits inside the assigned worktree when context carries `wt_path`; falls back to an explicit shared-KV read at the Auto-Commit gate when context lacks `wt_path` AND ≥1 fresh `wt.claim.*` row exists for this branch — the defense-in-depth backstop per D3 §2 with the C6 concrete trigger). Both agents cite Phase 1's canonical home rather than restating it.

**Authority:** revised 2026-09-06 per `architecture-recommendation.md` (D3 substrate REFUTED; MANDATORY `context=` hand-off per O-D3.1/U2; per-task census keys; explicit KV-read defense-in-depth for developer). Per-agent byte caps revised per U1 (leader ~320B, developer ~220B — combined ~540B vs the prior ~450B).

**Sequencing:** pointer only — see **plan-overview.md → Canonical sequencing** (single source of truth). Phase 2's edits are sequenced only **until Phase 1's edits are on the integration branch**; Phases 1-3 land in ONE merge commit; Phase 4 is a separate post-merge commit. (Cross-phase dependency: both awareness agents reference Phase 1's "Worktree Mode" section name.)

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Extend the Git Flow section in `agents/leader/workflow.md` (target insertion: append a 1-sentence awareness note after the existing `### Key Rules` block at current lines 90-93) specifying: when a fan-out will produce ≥ 2 editors that each commit, the leader (a) writes per-task `wt.active.<branch>.<task-id>` rows for each planned editor, (b) requests giter FIRST with the worktree-create intent, (c) WAITS for giter's completion report, and (d) then spawns each editor with MANDATORY non-empty `context={"wt_path": <path>, "wt_slug": <slug>, "wt_branch": <branch>}` — extends the existing "Git Setup is NOT Parallelizable" rule rather than adding a parallel rule | Phase 1 | New note present in the Git Flow section; reads as an extension of the existing "CRITICAL: Git Setup is NOT Parallelizable" section (no duplicate paragraph); reference to giter's "Worktree Mode" is by section-name label (not line number); **MANDATORY `context=` wording is explicit (D3/O-D3.1/U2); the per-task census pre-write BEFORE giter spawn is stated** |
| 2 | Extend the `### Passing Task Context (optional)` block in `agents/leader/tools_note.md` (current line 20-29) by appending `wt_path` / `wt_slug` / `wt_branch` to the "Suggested keys" list + a one-line "non-empty context required for worktree awareness" guard (empty `{}`/`None` does not force enqueue — always include at least `wt_path`; this is the durable-enqueue routing requirement) | Phase 1 | Updated "Suggested keys" line lists the three new keys alongside the existing `files` / `notes` / `plan_ref`; **the "non-empty context required" guard is present**; one-line reference to "Worktree Mode" once (section-name label) |
| 3 | Add a `Must-Not` bullet in `agents/developer/rule.md` (target insertion: under the existing `## Must Not` heading; bullet before the closing "## Core Principles" section) reading EXACTLY the File-3 MUST-FIT LITERAL below (95 bytes). **S2: this is the ONLY developer rule.md artifact — the former third artifact (auto-commit-area guideline) is DROPPED. O6: the KV-read backstop does NOT appear here** — its canonical home is developer's workflow.md (Task 4); rule.md keeps only the worktree-commit Must-Not | Phase 1, Task 1 of Phase 2 (so the leader is actually passing the path before developer is told to honor it) | New `Must-Not` bullet present = the 95B literal verbatim; NO KV-read backstop line in rule.md (O6); NO third artifact (S2); no "Worktree Mode" pointer in rule.md (the single developer pointer lives in workflow.md); content-stability assertion (C4, plan-overview criterion 4): `diff <(git show HEAD:agents/developer/rule.md | awk '/^## /{m=($0~/^## Must/)} m') <(awk '/^## /{m=($0~/^## Must/)} m' agents/developer/rule.md)` is empty (bullet lands under `## Must Not` — a sanctioned region — so the assertion holds) |
| 4 | Add the backstop prefix in `agents/developer/workflow.md` Auto-Commit section (target insertion: immediately after `## Auto-Commit on Successful Review` heading at current line 507, BEFORE the existing intro line at 509) reading EXACTLY the File-4 MUST-FIT LITERAL below (125 bytes). **C6 concrete trigger:** the backstop fires ONLY when "no `wt_path` in context AND ≥1 fresh `wt.claim.*` row for this branch" — no vague "concurrent editing suspected". **O6: this is the CANONICAL home of the KV-read backstop** | Phase 1, Task 3 of Phase 2 | Prefix present = the 125B literal verbatim; the C6 trigger wording ("no wt_path in context AND >=1 fresh wt.claim.* row") is present; single use of "Worktree Mode" section-name in developer's files; no duplication of the schema/lifecycle/trap text |
| 5 | Verify no system-internals leak in the new prose: `grep -nE 'meta\.json\|tools\.allow\|daemon/\|shared_context_metadata\|innate_skills\|get_tree_root_id\|seed_all\|agent_id=' agents/leader/workflow.md agents/leader/tools_note.md agents/developer/rule.md agents/developer/workflow.md` returns zero NEW hits vs. pre-Phase-2 baseline. Also: `grep -nE 'action="set"|action="delete"' agents/leader/workflow.md agents/leader/tools_note.md agents/developer/rule.md agents/developer/workflow.md` returns zero hits. | Tasks 1-4 | Grep returns zero NEW hits (pre-existing hits in unchanged prose are out of scope); obsolete `action=` schema is gone from the new prose |
| 6 | Confirm byte budget (C3-i, byte-true): per file run `git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c` (ADDED-line byte count, not line count; the 1-byte-per-line `+` prefix overhead is accepted and applied identically everywhere). Sub-caps sum EXACTLY to the per-agent caps (C3-ii): leader workflow.md ≤ 175 + tools_note.md ≤ 145 = **320**; developer rule.md ≤ 95 + workflow.md ≤ 125 = **220** | Tasks 1-4 | wc -c per file ≤ its sub-cap; leader = 320, developer = 220 exactly; on overrun, shrink toward the must-fit literals (they are already at/near the caps) |

---

## Coupling

- **Tight with:** Phase 1 (every Phase 2 prose references giter's "Worktree Mode" by section-name). If Phase 1's section is renamed, all four Phase 2 prose inserts MUST be updated in the same commit.
- **Tight within Phase 2:** Task 3 (developer's Must-Not) **logically depends on** Task 1 (leader passing the path) — the developer rule only matters if the leader is delivering the path. The plan encodes Task 1 + Task 3 + Task 4 inside the same commit so the contract lands atomically; the Depends-On column makes the rationale explicit even though we're shipping together.
- **Loose with:** project-manager's `send_message → set_kv` ordering (`agents/project-manager/workflow.md:82,91-92` referenced in `technical-analysis.md`); leader's pre-write of `wt.active.<branch>.<task-id>` (per-task keys) intentionally overrides this for the worktree case (D2 lifecycle) — detection requires the row to exist before giter reads, and the phantom cost is bounded by the 15-min read-side TTL. Same pattern governor uses for `council_manifest` in spirit, but the write order here is `set_kv` BEFORE `send_message` (because the row is the detection signal, not the recovery signal).
- **Independent of:** Phase 3 (tester/tidier pointer phase — each cites Phase 1 independently).

---

## Risks

Phase-specific risks (the project-level risk register lives in `plan-overview.md`):

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P2-A | Implementing agent adds a Cardinal in developer `rule.md` to "make the worktree rule findable" — breaches the ≤7 cap | Medium | Task 3's explicit "Must-Not" placement (a plain bullet, NOT a Cardinal); content-stability assertion (C4, plan-overview criterion 4) on `agents/developer/rule.md`: `## Must`/`## Must Not` regions byte-identical to HEAD |
| P2-B | Leader example block in `tools_note.md` adds 4 lines of context example that pads the budget | Low | Task 2 keeps the suggested-keys line tight (≤ 1 line added); the "non-empty context required" guard is one additional line; Task 6 enforces the 320B/220B per-agent caps (U1) |
| P2-C | Developer's `Must-Not` references `meta.json` or `tools.allow` to explain which channels surfaces the path — leaks system internals | Medium | Task 5 verification grep; writing-guide §1 forbids; use "the dispatch context" / "your [SYSTEM CONTEXT] block" agent-POV |
| P2-D | Leader Git Flow note duplicates giter's Worktree Mode (paragraph-level) | High | Writing-guide §2 canonical-home rule; Phase 3 grep: identical prose-block > 50 chars found inside leader/workflow.md AND inside giter/workflow.md fails the check |
| P2-E | Developer's prefix in workflow.md adds MULTIPLE references to "Worktree Mode" (e.g., once in Must-Not AND once in workflow.md) | Low | Phase 3 grep: each agent points to "Worktree Mode" exactly once (in the canonical-cite file); **developer has EXACTLY ONE pointer — in `workflow.md` (the canonical cite file for developer per S2/O6); rule.md has NO "Worktree Mode" pointer (rule.md keeps only the worktree-commit Must-Not per S2 — the 95B literal)** |

---

## Edit Specs (file-level)

### Phase-2 sub-cap arithmetic (C3-ii — sums EXACTLY to the per-agent caps)

| Agent | File | Must-fit literal bytes | Sub-cap |
|-------|------|------------------------|---------|
| leader | `workflow.md` | 172 | 175 |
| leader | `tools_note.md` | 142 | 145 |
| **leader total** | | **314** | **320** |
| developer | `rule.md` | 95 | 95 |
| developer | `workflow.md` | 125 | 125 |
| **developer total** | | **220** | **220** |

Byte counts measured with `wc -c` on each literal block (including trailing newline). The literals ARE the prompt-text spec (C3-iii); rationale lives in PLAN NOTES. S2: the developer has EXACTLY two artifacts (rule.md Must-Not + workflow.md Auto-Commit prefix). O6: the KV-read backstop is canonical to workflow.md only.

### File 1: `agents/leader/workflow.md`

**Target section:** INSERT a new `### Worktree-Aware Fan-Out` (or extend the existing `### When Git Flow Applies` table by a row, if it fits the table shape) immediately AFTER the `### Key Rules` block (current end: line 93).

**Content shape:** guideline-strength (process), NOT a Cardinal. Extension of the existing "Git Setup is NOT Parallelizable" rule, not a parallel new rule.

**MUST-FIT LITERAL (`wc -c` = 172 bytes ≤ sub-cap 175):**

```markdown
Fan-out >=2 committing editors: pre-write wt.active.<branch>.<task-id> rows -> spawn giter FIRST -> on its report spawn each editor with non-empty context={"wt_path":...}.
```

**Plan notes:** the literal encodes the four steps (a) per-task census pre-write BEFORE giter spawn (D2 lifecycle), (b) giter FIRST with worktree-create intent, (c) WAIT for giter's completion report, (d) MANDATORY non-empty `context={"wt_path": …}` per U2/O-D3.1. "Extends Git Setup is NOT Parallelizable" is realized by placement (inside/next to that section), not by restating it. No auto-surface claims; no system internals; the "non-empty dict forces durable enqueue routing" rationale is plan-note-only (it is daemon mechanism).

**Acceptance checks:**
- This literal carries NO "Worktree Mode" pointer (byte budget); the leader's single pointer lives in the tools_note literal (File 2). `grep -cF 'Worktree Mode' agents/leader/workflow.md` stays at its Phase-1 baseline.
- No system internals (writing-guide §1).
- **MANDATORY `context=` wording is explicit** (D3/O-D3.1/U2) — "non-empty context={"wt_path":...}".
- **Per-task census pre-write BEFORE giter spawn is stated** (D2 lifecycle).
- **No "auto-surface" / "see wt_path in [SYSTEM CONTEXT] automatically" claims.**
- Byte-true (C3-i): wc -c on added hunks ≤ 175.

---

### File 2: `agents/leader/tools_note.md`

**Target section:** Extend the existing "Passing Task Context (optional)" block (current line 20-29).

**Content shape:** list-extension (add to existing "Suggested keys" line) + one-line "non-empty context required" guard.

**MUST-FIT LITERAL (`wc -c` = 142 bytes ≤ sub-cap 145; carries the leader's single "Worktree Mode" pointer):**

```markdown
Suggested context= keys: wt_path/wt_slug/wt_branch. Hand-off REQUIRES non-empty context (>= wt_path). See giter/workflow.md -> Worktree Mode.
```

**Plan notes:** "non-empty context" is the durable-enqueue routing requirement (empty `{}`/`None` does not force enqueue — plan-note rationale only, daemon mechanism stays out of prompt text per writing-guide §1). The pre-existing example block at line 31-41 is unchanged; any opt-in extension counts against the 320B leader cap.

**Acceptance checks:**
- "Suggested keys" line lists the three new keys alongside the existing three.
- **Non-empty-context requirement is present.**
- Exactly ONE "Worktree Mode" pointer in the leader's files — it is THIS literal's closing sentence (section-name label).
- Byte-true (C3-i): wc -c on added hunks ≤ 145.

---

### File 3: `agents/developer/rule.md`

**Target section:** INSERT one bullet under the existing `## Must Not` heading (which starts at line 117), at the end of the `Must Not` list (before the `## Core Principles` heading at line 164).

**Content shape:** plain bullet under the `Must Not` heading (matches the section's bullet-list convention).

**MUST-FIT LITERAL (`wc -c` = 95 bytes = sub-cap 95):**

```markdown
- Assigned wt_path? cd into the worktree before any git op; never commit on the main checkout.
```

**Plan notes:** **S2** — this is one of EXACTLY two developer artifacts (the former third artifact — an extra auto-commit-area guideline — is DROPPED). **O6** — the KV-read backstop does NOT live here; its canonical home is File 4 (workflow.md, process-shaped). The bullet is pure never-violate constraint. No "Worktree Mode" pointer here (the single developer pointer is in File 4).

**Acceptance checks:**
- Bullet appears under `## Must Not`.
- **NO defense-in-depth KV-read line here** (O6 — it is canonical to workflow.md).
- No "Worktree Mode" pointer here.
- No system internals (writing-guide §1).
- **Content-stability (C4):** for developer the sanctioned delta IS this bullet (it lands inside `## Must Not` by design), so the assertion is: the rule.md diff adds EXACTLY the 95B literal and NOTHING else — `git diff -U0 -- agents/developer/rule.md | grep '^+' | grep -v '^+++' | sed 's/^+//'` equals the literal string verbatim (no other additions anywhere in the file → no new Cardinal-strength bullets). (For giter/leader/tester/tidier the stricter zero-diff form applies — see plan-overview criterion 4.)

---

### File 4: `agents/developer/workflow.md`

**Target section:** INSERT the backstop prefix at the very top of the existing `## Auto-Commit on Successful Review` block (line 507-510), between the heading and the existing intro line.

**Content shape:** paragraph/blockquote (process, NOT a Cardinal).

**MUST-FIT LITERAL (`wc -c` = 125 bytes = sub-cap 125; canonical home of the O6 backstop):**

```markdown
> Backstop: no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first (giter/workflow.md -> Worktree Mode).
```

**Plan notes:** **C6 concrete trigger** — "no wt_path in context AND >=1 fresh wt.claim.* row" replaces the vague "concurrent editing is suspected". The read itself is a no-arg `shared_meta_kv` call (partition read) prefix-scanning `wt.claim.*` — agent-POV, no tool-surface internals. **O6** — this is the ONLY developer location of the backstop; rule.md (File 3) keeps only the Must-Not.

**Acceptance checks:**
- Prefix uses blockquote so it visually front-loads.
- **The C6 trigger wording ("no wt_path in context AND >=1 fresh wt.claim.* row") is present.**
- One-line reference to "Worktree Mode" (the single developer pointer).
- No duplication of schema/lifecycle prose.
- Byte-true (C3-i): wc -c on added hunks ≤ 125.

---

## Exit Criterion

`git diff --stat agents/leader agents/developer` shows edits only on `workflow.md` and `tools_note.md` (leader) and `rule.md` and `workflow.md` (developer); `soul.md`, `meta.json`, `tools.allow`-relevant files, and any strategy skill are untouched; **Phase-2 byte budget (C3-i/C3-ii, wc -c on added hunks): leader ≤ 175 + 145 = 320 added bytes; developer ≤ 95 + 125 = 220 added bytes (sub-caps sum EXACTLY to the per-agent caps, U1);** the MANDATORY non-empty `context={"wt_path": …}` hand-off is in leader's Git Flow extension; **the defense-in-depth KV-read backstop with the C6 concrete trigger ("no wt_path in context AND >=1 fresh wt.claim.* row") is in developer's Auto-Commit prefix ONLY (O6 — not in rule.md); developer has EXACTLY two artifacts (S2 — the third artifact is dropped);** content-stability assertion (C4) passes on both `rule.md` files with the sanctioned bullets as the only accounted deltas; no system internals introduced; no obsolete `action="set"/"delete"` schema.

Refer to `plan-overview.md` Canonical sequencing for the ordering of Phases 2-4.
