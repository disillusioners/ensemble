# Phase 1: Giter Canonical Home

## Objective

Make `giter/workflow.md` the **single canonical home** for the worktree-awareness contract — schema (per-task census keys), lifecycle, location/naming, cleanup (remove-first), reconciliation rule, **two staleness clocks (10-min claim heartbeat / 15-min census TTL)**, pre-check-before-add, conditional `-b`, and **four** documented traps (corrected `.env` per architect §5, `cwd` isolation with no-`.venv` addendum, port collision generalized, `add -b` refusal) — and wire the supporting pieces (`rule.md` guideline + `tools_note.md` command entries + bare-stash pathspec fix). When Phase 1 is complete, every other agent (Phase 2 + Phase 3) can cite this home with a one-line pointer that cannot drift.

**Authority:** revised 2026-09-06 per `architecture-recommendation.md` (D3 substrate REFUTED → mandatory `context=` + explicit KV reads; 5 protocol corrections; real KV tool names; corrected `.env` trap; per-agent byte cap raised to ~850B per U1). All references to the prior 30-min heartbeat, `wt.active.<branch>` branch-keyed schema, `git worktree prune` as primary cleanup verb, and the obsolete `action=`/`key=`/`value=` tool schema are removed.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add a new "Worktree Mode" section in `giter/workflow.md` immediately after the existing "Standard Git Operations" heading block (target insertion: between current line 75 end of "D. Conflict Resolution Flow" and line 77 "### 3. Common Scenarios") | none | Section present = **the File-1 MUST-FIT LITERAL (572B) verbatim** — it packs the schema (per-task census keys + 2 clocks), the lifecycle (reconciliation-with-C5-criterion, pre-check-before-add, claim-AFTER-add), naming/cleanup (remove-first), one traps line (corrected `.env`, agent-POV) plus the S1 awareness line and the "; else skip" when-NOT-to-use encoding; the remaining trap detail (cwd/`.venv`, port, conditional `-b`, slug derivation, claim fields) lives in the File-1 PLAN NOTES, not prompt text; no other paragraph duplicates the schema/lifecycle elsewhere in `giter/` |
| 2 | Insert a new **guideline** in `giter/rule.md` under the existing "Branch Management" block (between current line 28 "Default base branch is latest" and current line 30 "Conflict Resolution" heading) reading EXACTLY the File-2 MUST-FIT LITERAL below (82 bytes): "- >=2 fresh wt.active rows -> sibling worktree; see workflow.md -> Worktree Mode." (the ≥2-TTL-fresh per-task-key wording is carried by the Worktree Mode canonical section; the rule bullet stays lean for the 850B envelope) | Task 1 | New guideline appears under "Branch Management"; classification = **guideline**, not Cardinal; content-stability assertion (C4, plan-overview criterion 4): the `## Must` / `## Must Not` regions of `agents/giter/rule.md` are byte-identical to HEAD (`diff <(git show HEAD:agents/giter/rule.md | awk '/^## /{m=($0~/^## Must/)} m') <(awk '/^## /{m=($0~/^## Must/)} m' agents/giter/rule.md)` is empty — no Cardinal-heading grep: flat rule lists carry no `###` headers to count); the word "Cardinal" or "Must:" is not prepended to the new bullet; **O-D1.1 threshold is stated as "≥2 fresh wt.active rows for the branch → worktree mode"** (no stronger claim — deletes the prior "in addition to my own census" ambiguity) |
| 3 | Add tool-shaped command entries in `giter/tools_note.md` after the existing "### Syncing" block (current line 80-84 — re-verified against the worktree copy at feature/worktree-aware-prompts @ 4a64690e): reading EXACTLY the File-3 MUST-FIT LITERAL below (196 bytes: one `### Worktrees` block carrying list / add -b / add / remove / prune + the bare-stash pathspec fix; conditional `-b` = "new branch only", remove-first with prune as foreign-only fallback) | Task 1 | Subsection present; each entry ≤ 2 lines; references "Worktree Mode" once for full lifecycle (no duplication of schema/lifecycle) |
| 4 | Add the bare-`git stash` pathspec fix as a one-line caveat appended to the existing "### Recovery" block in `giter/tools_note.md` (current line 86-91 — re-verified against the worktree copy at feature/worktree-aware-prompts @ 4a64690e): use `git stash push -- <own files>` not bare `git stash` to avoid stranding concurrent workers' changes; cites the known gotcha precedent briefly without naming internal docs | none (golden-hour fix while the file is open) | Caveat present; one line; references "concurrent workers" in agent POV; does not name the LESSONS file or the daemon |
| 5 | Verify no system-internals leak in the new prose: `grep -nE 'meta\.json\|tools\.allow\|daemon/\|shared_context_metadata\|innate_skills\|get_tree_root_id\|seed_all\|agent_id=' agents/giter/workflow.md agents/giter/rule.md agents/giter/tools_note.md` returns zero new hits introduced by this phase (pre-existing hits in unchanged prose are out of scope). Also: `grep -nE 'action="set"\|action="delete"' agents/giter/workflow.md agents/giter/rule.md agents/giter/tools_note.md` returns zero hits (obsolete KV tool schema purged). | Tasks 1-4 | Verification grep returns zero NEW hits vs. the pre-Phase-1 stash snapshot before this phase (capture pre-edit baseline via `git stash list` to enumerate, `git show stash@{N}` to read the contents, or `git diff stash@{N}` for a textual diff — pick whichever stash ref the operator captured); obsolete `action=` schema is gone from the new prose |
| 6 | Confirm byte budget (C3-i, byte-true): per file run `git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c` (ADDED-line byte count, not line count; the 1-byte-per-line `+` prefix overhead is accepted and applied identically everywhere). Sub-caps sum EXACTLY 850 (C3-ii): workflow.md ≤ 572, rule.md ≤ 82, tools_note.md ≤ 196 | Tasks 1-4 | wc -c per file ≤ its sub-cap; sum = ≤ 850; on overrun, shrink toward the must-fit literals (they are already at the cap) — drop plan-note rationale, never the literals |
| 7 | Phase 1 closeout: re-read the inserted Worktree Mode section end-to-end and confirm (a) **schema names match the decisions verbatim** (`wt.claim.<slug>`, `wt.active.<branch>.<task-id>`); (b) **lifecycle ordered correctly** (leader pre-writes `wt.active.<branch>.<task-id>` BEFORE spawning giter → giter reconciles against `git worktree list` → pre-check-before-add → `git worktree add` (or reuse) → write `wt.claim.<slug>` AFTER the add succeeds → leader spawns editors with MANDATORY non-empty `context={"wt_path": …}` → developer commits inside the worktree → AFTER-gate merge + `git worktree remove` + `delete_keys`); (c) **two clocks are stated** (10-min claim heartbeat / 15-min census TTL); (d) **reconciliation rule is stated** (every gate entry, WITH the C5 adopt-vs-remove criterion: dirty OR branch HEAD age < 30 min → adopt + refresh heartbeat; else remove graceful; dirty-remove refusal → STOP and report); (e) **pre-check-before-add is stated** (before every `add`); (f) **conditional `-b` is stated** (use only when branch exists nowhere); (g) **corrected `.env` trap is present** (never launch `dev.sh` from inside a worktree; if a worktree daemon is needed, `set -a; source <main-repo>/.env; set +a` and bind a non-8079 port); (h) the "When NOT to use" block is explicit (single-editor or no-concurrent-editor scenarios skip the worktree entirely); (i) **real KV tool names are used** (`set_kv` / `delete_keys` / `clear_all`; read = a no-arg `shared_meta_kv` call — `get_all_as_dict` is the repository read path, never presented as a tool; not `action="set"/"delete", key=…, value=…`) | Tasks 1-4 | Read-through checklist signed off in the implementing agent's report |

---

## Coupling

- **Tight with:** Phase 2 (leader pre-writes `wt.active.<branch>.<task-id>` BEFORE spawning giter — its existence is why giter reads on entry), Phase 3 (tester + tidier pointers reference this section by name). **If Phase 1's section is renamed or relocated, Phase 2 + Phase 3 pointers MUST be updated in the same commit** (writing-guide §3 cross-reference hygiene).
- **Loose with:** Project-manager's `spawn → set_kv → send_message` discipline (`technical-analysis.md` D2 lifecycle) — the leader's pre-spawn census write intentionally overrides this for the worktree case; the override is stated in D2 lifecycle. Giter's `set_kv AFTER add` is the worktree-side analogue of the governor's write-before-spawn, reversed because the worktree is the durable anchor.
- **Independent of:** No phase depends on Phase 1's specific numbers (10/15-min clocks, `≥ 2` threshold, slug sanitization) — they're ratified values, not recommendations, but the encoding is one-literal-per-line so a future swap is a one-line edit per literal.

---

## Risks

Phase-specific risks (the project-level risk register lives in `plan-overview.md`):

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P1-A | Implementing agent over-explains the schema and blows the 850-byte cap | High | The MUST-FIT LITERALS (572/82/196 = 850 exactly) are the prompt-text spec; Task 6's wc -c added-hunk count is the hard gate; rationale lives in plan notes, never prompt text |
| P1-B | Implementing agent references `meta.json` or `daemon/` to "make it clear what KV is" | High | Verification grep in Task 5; writing-guide §1 forbids; if prose feels short, use "the shared KV store" / "the worktree partition" agent-POV |
| P1-C | Cardinal split regression — implementing agent inserts a Cardinal heading for the new rule | Medium | Content-stability assertion (C4, plan-overview criterion 4) on `agents/giter/rule.md`: `## Must`/`## Must Not` regions byte-identical to HEAD — the new bullet lands under `### Branch Management` (a sanctioned region) and the assertion proves no Cardinal-strength additions |
| P1-D | Bare-stash caveat drift — implementing agent cites a LESSONS file path instead of the agent-POV symptom | Low | Writing-guide §1 + A.6: drop the citation, state the symptom only ("a bare `git stash` strands concurrent workers' changes") |
| P1-E | Section relabel drift mid-implementation — implementing agent renames "Worktree Mode" to "Worktree Workflow" | Low | Section-name label is a stable contract; cross-phase verify will grep `Worktree Mode` literally; if a swap is needed, all three phase plans update together |

---

## Edit Specs (file-level)

### Giter sub-cap arithmetic (C3-ii — sums to EXACTLY 850)

| File | Must-fit literal bytes | Sub-cap |
|------|------------------------|---------|
| `workflow.md` (Worktree Mode) | 572 | 572 |
| `rule.md` (Branch Management bullet) | 82 | 82 |
| `tools_note.md` (Worktrees block) | 196 | 196 |
| **giter total (U1)** | **850** | **850** |

Byte counts were measured with `wc -c` on each literal block (including its trailing newline). The literals below ARE the prompt-text spec (C3-iii): the implementing agent copies them verbatim; all explanatory rationale lives in the PLAN NOTES, never in prompt text. S1 applies: the literals contain no daemon-defect names and no LESSONS/doc citations.

### File 1: `agents/giter/workflow.md`

**Target section:** INSERT a new `### Worktree Mode` immediately after the existing `#### D. Conflict Resolution Flow` block and before `### 3. Common Scenarios` (heading level may adapt to the local numbering rhythm; the section NAME "Worktree Mode" is the stable pointer target).

**Content shape:** guideline-strength (process), NOT a Cardinal rule.

**MUST-FIT LITERAL (`wc -c` = 572 bytes ≤ sub-cap 572):**

```markdown
### Worktree Mode
>=2 fresh wt.active.<branch>.<task-id> rows -> ../<repo>-wt-<slug>/; else skip
Reconcile: list --porcelain; orphan: dirty or HEAD <30 min -> adopt+heartbeat else remove; dirty-remove refusal -> STOP+report; phantom -> delete_keys.
wt.claim.<slug>: set_kv AFTER add; heartbeat per action; stale 10 min / census 15 min.
Cleanup: merge -> remove -> delete_keys claim+census (remove-first)
never rely on ambient KV surfacing; dispatch context is primary; explicit KV read is the backstop.
Traps: daemon in worktree hits prod defaults (export env explicitly)
```

**Plan notes (rationale moved OUT of prompt text per C3-iii/S1 — this is where the detail lives):**
- **Claim value fields (KV value, NOT prompt text):** `{"path", "branch", "owner", "purpose", "ts", "heartbeat_ts", "owns_task_ids"}` — `owns_task_ids` (O2) copies the originating `wt.active.<branch>.` task-ids at creation so the AFTER-gate `delete_keys` is ownership-scoped (decisions.md D2 claim schema).
- **Slug derivation (O-D2.3):** lowercase; strip `feature/`/`fix/`/`hotfix/`; non-`[a-z0-9_-]` → `-`; truncate at 48; empty or shared base (`latest`/`main`) → `task-<short-task-id>`; fresh-claim collision → append `-<4hex>`.
- **Traps detail (plan notes only — S1 forbids citations in prompt text):** (1) `.env` — `dev.sh` sources its own `$SCRIPT_DIR/.env`; launched from inside a worktree that file does not exist → empty env → prod defaults (canonical source for the plan: `LESSONS/2026-08-20-e2e-never-claimed-signature.md`); (2) `cwd` — `cd` into the worktree before `git status`/`git log`; a fresh worktree has no `.venv` (code that must RUN there needs `uv sync` first; plan-note source: `LESSONS/2026-09-05-worktree-daemon-filecheck-cwd-trap.md`); (3) port — any worktree daemon binds a non-8079 port (tester live-smoke launches uvicorn directly on an alt port); (4) conditional `-b` — carried by the tools_note literal ("add -b: new branch only").
- **Awareness rationale (S1):** auto-surface is opportunistic only — 3 latent daemon defects (snapshot cadence, spawned-child mispartition, system-default suppression; decisions.md D3 §1). The PROMPT text carries ONLY the agent-POV rule ("never rely on ambient KV surfacing; dispatch context is primary; explicit KV read is the backstop") — no defect names, no doc citations.
- **Reconciliation (C5):** the adopt-vs-remove criterion is verbatim in the literal — dirty OR branch HEAD age < 30 min → adopt + refresh heartbeat; else remove (graceful); dirty-remove refusal → STOP and report.
- **Mid-flight (O1):** "mid-flight" = an editor instance with an in-progress turn or uncommitted changes in its worktree; staleness handling never removes mid-flight (verify at next giter entry); the heartbeat writer stays giter-only — the minimal choice consistent with D2's single-writer heartbeat design.
- **When-NOT-to-use:** encoded as "; else skip" — single-editor runs and scratch tasks skip the worktree entirely.
- **Pre-existing `/private/tmp` worktrees (≤5 at run time; expected starting set: `adj-head`, `hotfix-defer-gate-base`, `m1-gate-base`, `pcfg-base`, `ens-autopromote-micro`) — NOT this protocol's job.** Foreign registrations; reconciliation ignores them; `git worktree prune` is a NO-OP there (dirs exist); cleanup lives in `phase4-plan.md`, post-merge.

**Acceptance checks (writing-guide §10 + D5 + U1):**
- Byte-true (C3-i): `git diff -U0 --no-color -- agents/giter/workflow.md | grep '^+' | grep -v '^+++' | wc -c` ≤ **572**.
- System-internals grep: zero NEW hits (D5 forbids `meta.json` / `tools.allow` / `daemon/` / `shared_context_metadata` / `innate_skills` / `get_tree_root_id` / `seed_all` / `agent_id=` / `default_agent_versions` / `skill-set.yaml`).
- Obsolete KV tool schema grep: `grep -nE 'action="set"|action="delete"' agents/giter/workflow.md` returns zero NEW hits.
- Canonical-home grep: no parallel copy of the schema block in `giter/rule.md` or `giter/tools_note.md` (the tools_note literal deliberately carries NO "Worktree Mode" pointer — the rule.md bullet carries the single section-name pointer; the protocol text is stated once).
- **S1:** the inserted section contains NO daemon-defect names and NO LESSONS/doc file citations (they live in the plan notes above only).

---

### File 2: `agents/giter/rule.md`

**Target section:** INSERT a single new bullet under the existing `### Branch Management` block.

**Content shape:** guideline-strength bullet (existing block uses plain bullets, not Cardinal headings). MUST NOT be classified as Cardinal.

**MUST-FIT LITERAL (`wc -c` = 82 bytes ≤ sub-cap 82):**

```markdown
- >=2 fresh wt.active rows -> sibling worktree; see workflow.md -> Worktree Mode.
```

**Plan notes:** the bullet is the lean detection pointer. The precise O-D1.1 threshold ("≥2 TTL-fresh `wt.active.<branch>.<task-id>` rows", per-task keys, no "in addition to my own census" ambiguity) is carried ONCE by the Worktree Mode canonical section — the writing-guide's stated-once rule plus the 850B envelope force the split. The word "Cardinal" or "Must:" is NOT prepended.

**Acceptance checks:**
- Section under which it's inserted: `### Branch Management` (verify by reading the heading two lines above).
- **Content-stability (C4):** `diff <(git show HEAD:agents/giter/rule.md | awk '/^## /{m=($0~/^## Must/)} m') <(awk '/^## /{m=($0~/^## Must/)} m' agents/giter/rule.md)` is EMPTY — the `## Must`/`## Must Not` regions are byte-identical to HEAD; the new bullet lands under `### Branch Management` (sanctioned region), proving no Cardinal-strength bullets were added. (No Cardinal-heading grep — flat rule lists carry no `###` headers to count.)
- One-line reference to "Worktree Mode" (section-name label).
- Byte-true (C3-i): wc -c on added hunks ≤ 82.

---

### File 3: `agents/giter/tools_note.md`

**Insertion A:** a new `### Worktrees` subsection after the existing `### Syncing` block. **Insertion B:** the bare-stash sentence may sit as the final sentence of insertion A OR as a one-line bullet appended to the existing `### Recovery` block (golden-hour fix while the file is open) — byte count identical either way.

**MUST-FIT LITERAL (A+B; `wc -c` = 196 bytes ≤ sub-cap 196):**

```markdown
### Worktrees
list --porcelain: pre-check + reconcile. add -b: new branch only. add: reuse. remove: cleanup (prune NO-OP). prune: foreign-only. stash push -- <files> (bare stash strands workers).
```

**Plan notes:** the five ratified command shapes compress to one line — `list --porcelain` = pre-check-before-add + reconciliation; `add -b` = conditional `-b` rule ("use only when the branch exists nowhere"); `add` = reuse of an existing branch/worktree; `remove` = graceful AFTER-gate cleanup with `prune` demoted (NO-OP on in-scope siblings whose dirs exist); `prune` = foreign-only fallback when `remove` fails. `stash push -- <files>` is the bare-stash pathspec fix in agent-POV symptom form (no LESSONS citation in prompt text — S1).

**Acceptance checks:**
- Subsection header matches existing convention (`### CommandGroup`).
- No system-internals references (no `daemon/`, no `tools.allow`).
- NO "Worktree Mode" pointer here (the rule.md bullet carries the single section-name pointer; tools_note must not duplicate schema/lifecycle or pointers — stated-once rule).
- Byte-true (C3-i): wc -c on added hunks ≤ 196.

---

## Exit Criterion

`git diff --stat agents/giter/` shows edits only on `workflow.md`, `rule.md`, `tools_note.md`; `soul.md`, `meta.json`, and `*-strategy.md` (if any) are untouched; **byte budget (C3-i/C3-ii): wc -c on added hunks — workflow.md ≤ 572, rule.md ≤ 82, tools_note.md ≤ 196; giter side sums to EXACTLY 850 (U1);** no new Cardinals — verified by the content-stability assertion (C4: `## Must`/`## Must Not` regions of `agents/giter/rule.md` byte-identical to HEAD); no system internals introduced; no obsolete `action="set"/"delete"` schema; section-name label "Worktree Mode" is stable; the inserted Worktree Mode section is the **must-fit literal (572B) verbatim** — carrying the **two clocks (10-min heartbeat / 15-min census TTL)**, the **per-task census keys**, the **reconciliation rule with the C5 adopt-vs-remove criterion (every gate entry, `<repo>-wt-*` family only)**, the **pre-check** rule, the **real KV tool names** (`set_kv` / `delete_keys`; read = no-arg call — `get_all_as_dict` is never presented as a tool), the **S1 agent-POV awareness line**, and the **corrected `.env` trap in agent-observable form**; the S1-cited defect names and LESSONS/doc citations appear ONLY in this plan's notes. The ≤5 pre-existing `/private/tmp` worktrees (5 expected names) are explicitly **out of scope** here (foreign registrations; reconciliation ignores them; Phase 4 handles cleanup).

Phase 2 (`leader` / `developer` awareness) and Phase 3 (`tester` / `tidier` pointers) can both proceed independently once Phase 1's edits are on the integration branch (canonical sequencing in `plan-overview.md`). Phase 4 (giter one-time stale-worktree sweep) is a separate commit sequenced AFTER the Phase 1-3 merge.
