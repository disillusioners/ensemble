# Research: prompt-doctrine + allow-list anchors for `project_future` tool category

- Commission: Project Future — forward-improvement wishlist tool category (32-cap FIFO eviction) for dev-team agents.
- Scope: read-only verification in worktree `/home/nea/ensemble-src-wt-project-future` (branch `feature/project-future`, base `ac9849bf7`).
- Date: 2026-11-12 (UTC). All file:line refs are from THIS worktree.
- Method: direct `read_file` / recursive `grep` over the worktree. No git mutations, no daemon contact. Caveat: single-file-scoped grep on this tool returned false negatives (see "Tooling note" at end) — every anchor below was re-verified via directory-scoped grep or direct read.

---

## Q1 — agents/leader/rule.md "Recording — Decision Table"

**VERIFIED at exactly the claimed range: `agents/leader/rule.md:127-143`.**

- `:127` — heading: `### Recording — Decision Table`
- `:129` — intro line: `**Which tool to use when you want to "remember" or "record" something.**`
- `:131` — header row + `:132` separator:

```
| Content Type | Tool | Example |
|--------------|------|---------|
```

- ALL current rows (`:133-137`, verbatim, compact):

| line | Content Type | Tool | Example |
|------|--------------|------|---------|
| :133 | **Project event** — feature shipped, bug fixed, deployment done | `project_history_add()` | "Added database tools category with connection management" |
| :134 | **Project knowledge** — architecture, patterns, gotchas, how systems connect | `experience()` | "The job queue uses a 7-state lifecycle with lock-first pattern" |
| :135 | **Persona/behavioral change** — how YOU should act | `inner_soul(intent="change", target="soul")` | "Be more concise in responses" |
| :136 | **User preference** — how the USER likes things | `inner_soul(intent="remember", target="user")` | "User prefers TypeScript over JavaScript" |
| :137 | **Self-reflection** — what YOU learned about your own behavior | `inner_soul(intent="learn", target="soul")` | "I rush too much on SMALL tasks, should trust agents more" |

- `:139` — Rule: `inner_soul` is INTENSELY PERSONAL … never for project state; if in doubt `project_history_add()` for events or `experience()` for knowledge.
- `:141` — "`inner_soul` WILL REJECT project content and tell you which tool to use instead."
- `:143` — RAG note: `experience()` requires the RAG backend; if unavailable use `project_history_add()` for both.

**leader[v2]: DOES NOT EXIST.** `agents/` directory listing shows `leader/` but NO `leader[v2]/` (siblings `planner[v2]/`, `developer[v2]/`, `reviewer[v2]/`, `tidier[v2]/`, `approver[v2]/` exist; `tester` and `leader` have no v2). ⚠ LOUD CORRECTION: any plan assuming a `agents/leader[v2]/rule.md` to patch is wrong — there is exactly ONE leader rule.md.

**Baseline coverage: YES.** `rule.md` is in `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:121`) and the committed baseline carries `agents/leader/rule.md` (row 119 of `agents/.prompt-artifacts.sha256`, sha256 `d104cb7f…`). Editing this table ⇒ baseline re-stamp + commit required.

---

## Q2 — reviewer[v2]/workflow.md advisory tier

**VERIFIED: `agents/reviewer[v2]/workflow.md:286`** carries the exact claimed row.

Surrounding section `## Severity Levels` (`:280-286`):

```
:280  ## Severity Levels
:282  | Level | Icon | Meaning |
:283  |-------|------|---------|
:284  | Critical | 🔴 | Must fix before merging / shipping |
:285  | Warning  | 🟡 | Should fix before release |
:286  | Suggestion | 🟢 | Consider for future improvements |
```

**Does suggestion-tier dead-end? YES — in prose.** The table's only prescribed fate for 🟢 is "Consider for future improvements" — no tool, no storage destination, no follow-up mechanism is named anywhere downstream. A grep across all of `agents/reviewer[v2]/*.md` for `future improvement|wishlist|wish list|backlog` returns exactly ONE hit: the `:286` row itself. Adjacent sections (`Scale Guide` :290-297) govern review sizing, not finding disposition. The severity tiering is re-stated in `agents/reviewer/rule.md:22` (rule 7) and `:94` (rule 29) — again with no destination for 🟢. This is the discoverability gap the commission targets.

**Non-v2 `agents/reviewer/`: EXISTS and carries the SAME content.** `agents/reviewer/workflow.md:286` has the identical line. Stronger: the baseline proves `reviewer/workflow.md` and `reviewer[v2]/workflow.md` are BYTE-IDENTICAL (same sha256 `24e40e96…`, baseline rows 143 and 147). Note the asymmetry: reviewer[v2]'s `tools_note.md` DIFFERS from non-v2 (`af36b4a4…` vs `167f4b90…`), while rule/soul/workflow match.

---

## Q3 — docs/agents.md tool-category table

**VERIFIED at exactly the claimed rows.** `docs/agents.md`:

- `:332` — "Tools are organized into categories. Each agent's `meta.json` controls which categories it can access."
- `:334` — section heading `### Tool Categories`
- Header + separator (`:336-337`):

```
| Category | Internal Key | Description | Key Tools |
|----------|--------------|-------------|-----------|
```

- Example rows to mirror for a `project_future` spec:

```
:346  | **Critical Notes** | `critical_notes` | Project-scoped lessons and insights | `project_cn_add`, `project_cn_list`, `project_cn_remove`, `project_cn_pin`, `project_cn_supersede`, `project_cn_backfill_embeddings` |
:347  | **Project History** | `project_history` | Chronological project event tracking | `project_history_add`, `project_history_list`, `project_history_search`, `project_history_delete` |
```

(docs/agents.md totals 504 lines; section continues through `### Tool Access by Agent` :354+.)

**docs/ is OUTSIDE agents/ — confirmed, not baseline-hashed.** `docs/agents.md` is a top-level `docs/` path; the baseline generator hashes only `tree / "agents"` (`scripts/protection/generate_prompt_artifact_baseline.py:105`) and every baseline line is `agents/<dir>/<artifact>` — zero `docs/` rows. A docs/agents.md row addition does NOT require a baseline re-stamp (still needs a commit per repo conventions).

Row spec implication: a new row would be
`| **Project Future** | `project_future` | <description> | `project_future_add`, `project_future_list`, … |`
following the `internal key == category string == allow-list entry` convention.

---

## Q4 — ALLOW-LIST CENSUS (key deliverable)

Method: recursive grep of `project_history` and `critical_notes` (and `project_cn`) over `agents/**/meta.json` (all meta.json scanned; results below are exhaustive, not sampled), plus direct reads of developer/leader/project-manager meta.json.

| agent-dir | exists? | has `project_history`? | has `critical_notes`? |
|-----------|---------|------------------------|------------------------|
| developer | ✓ | ✗ | ✗ |
| developer[v2] | ✓ | ✗ | ✗ |
| reviewer | ✓ | ✗ | ✗ |
| reviewer[v2] | ✓ | ✗ | ✗ |
| tidier | ✓ | ✗ | ✗ |
| tidier[v2] | ✓ | ✗ | ✗ |
| approver | ✓ | ✗ | ✗ |
| approver[v2] | ✓ | ✗ | ✗ |
| planner | ✓ | ✗ | ✗ |
| planner[v2] | ✓ | ✗ | ✗ |
| tester | ✓ | ✗ | ✗ |
| architect | ✓ | ✗ | ✗ |
| devops | ✓ | ✗ | ✗ |
| leader | ✓ | ✓ (`meta.json:15`, category key) | ✓ (`meta.json:15`, category key) |
| leader[v2] | **✗ DOES NOT EXIST** | — | — |
| project-manager (bonus, not in the ask) | ✓ | ✓ but VERB-STYLE (`project_history_list/search` :25-26, `project_history_add` :31; `project_history_delete` explicitly DENIED :84) | ✓ but VERB-STYLE (`project_cn_list` :27, `project_cn_add` :32, `project_cn_remove` :33) |

**Nobody in the dev-team roster except leader (category-style) and project-manager (verb-style) can currently record project history or critical notes.** The entire dev spine — developer(+v2), reviewer(+v2), tidier(+v2), approver(+v2), planner(+v2), tester, architect, devops — has NO access to either category. `project_future` would be the first persistent-recording channel any of them hold.

**"Dev team" structure (team_members):**
- `agents/leader/meta.json:17` — leader spawns: `planner, developer, reviewer, tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer, doc-writer, maintenancer, designer, gaia, aegis`.
- `agents/developer/meta.json:19` and `agents/developer[v2]/meta.json:19` — identical: `["coder", "worker"]` (developer orchestrates coder+worker; both have `snapshot_enabled: true`).
- Structural conclusion: leader is the sole parent of the named dev-team roster; developer(+v2) adds the coder/worker coding layer below it. A `project_future` grant rolled out to the leader team_members set + developer side would cover the full dev team.

**Allow-list key shapes observed (no exotic key names):** every inspected meta.json uses `tools.allow` / `tools.deny` arrays of category keys or tool verbs:
- leader: `tools.allow` = category keys incl. `"critical_notes", "project_history"` (`:15`).
- developer / developer[v2]: `tools.allow` = category keys, NO recording categories (`developer/meta.json:16`, `developer[v2]/meta.json:16`); both also `tools.deny: ["edit_file", "write_file"]`.
- project-manager: `tools.allow` = INDIVIDUAL TOOL VERBS (not the category key — see census row above) + `tools.deny` incl. `experience`, `project_history_delete` (`:82-94`).

---

## Q5 — Baseline machinery (`scripts/protection/generate_prompt_artifact_baseline.py`, 166 lines)

**Invocation (argparse `:65-100`, usage docstring `:18-27`):**
- Regenerate: `python3 scripts/protection/generate_prompt_artifact_baseline.py` (no args; writes the baseline file).
- Verify: `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <dir>` (read-only; `--baseline` overrides path, default `<tree>/agents/.prompt-artifacts.sha256` `:91-99`).
- Exit codes: 0 = clean or baseline absent; 1 = drift; 2 = agents tree missing (`:25`, `:112-117`).
- It single-sources the artifact set by loading `daemon/tools/prompt_artifacts.py` BY FILE PATH via importlib (`:40-58`) — never re-states the set (avoids dragging the langchain stack; comment `:40-47`).

**WHAT it hashes:** `VERSIONED_PROMPT_ARTIFACTS` frozenset (`daemon/tools/prompt_artifacts.py:119-133+`): `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `tools.md` (legacy fallback), `skill.md` (legacy), plus `memory.md` and `growth.md` (joined per User Directive #3/#5B 2026-10-14, `:126-133`). Pattern is STRICT depth-2 `<agents_root>/<agent_dir>/<artifact>` (`:241-250` `_depth2_match`, `:355-370` `iter_versioned_artifact_paths`); depth-3+ correctly misses. On disk today the 181 rows carry only: growth.md, rule.md, soul.md, tools_note.md, workflow.md (no tools.md/skill.md/memory.md files exist at depth-2).

**Baseline location:** `agents/.prompt-artifacts.sha256` (`BASELINE_FILENAME`, prompt_artifacts.py:352; generator docstring `:4`, `:19`). Format: sorted `<sha256>  agents/<dir>/<artifact>` lines, sha256sum-compatible, no comments (`:26-27`, `:337-338`).

**Row count — claim VERIFIED:** exactly **181 lines** covering **47 distinct agent dirs** (full-file read; dirs: _baby_template, _inner_soul, _mother, aegis, approver, approver[v2], architect, ari, blueprinter, canary, charter, coder, cracker, critic, designer, developer, developer[v2], devops, doc-maintainer, doc-writer, experiencer, explorer, gaia, giter, governor, hacker, image-comparator, image-reader, jober, kb-importer, kb-writer, leader, maintenancer, planner, planner[v2], project-manager, reviewer, reviewer[v2], sketcher, skill-keeper, sweeper, tester, tidier, tidier[v2], wanderer, watcher, worker). This also matches the live "PROMPT-BASELINE 181/181" figure in project metadata.

**meta.json NOT hashed — confirmed.** meta.json is not in the frozenset and the matcher keys on artifact FILENAMES only, so allow-list edits alone never touch the baseline. Conversely ANY edit to rule.md / workflow.md / soul.md / tools_note.md / growth.md under any agent dir (incl. [v2] dirs and `_`-prefixed dirs) changes a hash ⇒ `--check` reports drift ⇒ regenerate (`python3 scripts/protection/generate_prompt_artifact_baseline.py`) + commit the baseline alongside the prompt edit. Boot-time drift check warns via `check_baseline_and_warn` (`prompt_artifacts.py:522`, `:565-587`); stage-time check is read-only + journaled, never gating (generator docstring `:9-12`).

---

## Corrections & surprises (loud)

1. ⚠ **`agents/leader[v2]/` DOES NOT EXIST** — no second leader rule.md to patch. Plans must target `agents/leader/rule.md` only.
2. ⚠ **project-manager's allow list does NOT use the category keys** — it grants individual tool verbs (`project_history_add/list/search`, `project_cn_add/list/remove`) and denies `project_history_delete` + `experience`. A string-census for the category `project_history` would MISS pm's real access; any `project_future` rollout precedent should note both styles exist (category-key style = leader; verb style = pm).
3. **The claimed anchors were all ACCURATE**: leader rule.md table `:127-143` ✓; reviewer[v2] workflow `:286` row ✓; docs/agents.md `:346-347` rows ✓; baseline "181 rows / 47 dirs" ✓.
4. **reviewer vs reviewer[v2] asymmetry**: workflow.md/rule.md/soul.md byte-identical across the pair, tools_note.md differs. Same pattern for developer pair (rule/soul/workflow identical, tools_note differs) and planner/tidier/approver pairs (all four artifacts identical).
5. **coder's baseline entry lacks rule.md** (only soul.md + workflow.md, rows 45-46); image-comparator and skill-keeper carry only soul.md. `_prompt_system/` exists on disk but has ZERO baseline rows (no depth-2 files named in the frozenset).
6. **🟢 Suggestion tier is a confirmed dead-end**: the only "future improvements" text in the whole reviewer[v2] prompt set is the severity-table row itself; no wishlist/backlog destination exists anywhere in reviewer prompts — the exact gap `project_future` addresses.
7. **Severity tables also live in reviewer skills templates** (`agents/reviewer/skills-template/*.md` — e.g. code-review.md:54, pr-review.md:57, plan-review.md:53, security-review.md:55, business-logic-review.md:56, architecture-review.md:53, review-strategy.md:231) and non-v2 `agents/reviewer/rule.md:22,:94` — a doc/doctrine change to severity semantics would fan out wider than the one workflow.md table.

## Tooling note (for reproducibility)

Single-file-scoped grep (`path=<file>`) silently returned "no matches" for content that recursive grep (`workdir=<dir>`, `include=<pattern>`) and direct `read_file` both found (e.g. "Recording" in agents/leader/rule.md). All line numbers above were confirmed by direct reads, not by the faulty file-scoped mode.
