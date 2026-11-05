# Phase 5: The All-Agents Duty Prompt, the Explicit Grant, and the Baseline/Seal/Pin Ceremony

Date: 2026-11-04T22:40Z
Author: plan-creation worker (nest-anomaly-report lane, board item wi-2d691ed2)
Status: Draft — plan of record for phase 5
Worktree: `/home/nea/ensemble-src-wt-nest-anomaly-report` (base `1f6898ae2`)
Claim: `wt.claim.nest-anomaly-report`

## Objective

Every agent in the nest can report an operational anomaly, and every agent is *told* it can.

At the end of this phase, all **41** in-repo agents that carry a `meta.json` simultaneously hold
(a) an explicit `"nest-report"` entry in `tools.allow` — directly, or by inheritance through
the empty-allow default universe — and (b) a canonical-home duty line naming the
`report_anomaly` tool, byte-identical across all agents, in exactly one artifact per agent.

**Testable completion sentence:** the duty-prompt completeness walk and the grant-completeness
walk both pass over the live `agents/` tree (41 = 39 walk + 2 underscore specials each), the sealed-artifact tripwire reports
0 stale rows after the re-pin, and the prompt-artifact baseline verifies clean at 161/161.

## Scope

### In Scope

- The **duty text** (3 sentences, one canonical English form) inserted into exactly one
  canonical-home artifact per agent — 41 files.
- The **explicit grant**: `"nest-report"` appended to the `tools.allow` array of every
  `meta.json` that has a non-empty allow list — 40 files.
- The **baseline re-stamp** of `agents/.prompt-artifacts.sha256` (161 entries) in the same
  commit as the edits.
- The **seal re-pin** of every `SEALED_SHA_BASELINES` row whose file this phase edits — 17 rows.
- The **new pins**: a duty-prompt registry-completeness pin and a grant-completeness pin, both
  in the `tests/unit/test_report_integrity_prompts.py` shape.

### Out of Scope (with reason)

- **`tools_note.md` for all 35 agent dirs** — the tool's own description + the `help` surface
  serve discovery; the writing guide §2:53-64 assigns "tool-by-tool reference" to
  `tools_note.md`, but adding 35 near-duplicate reference blocks would create a second copy of
  a contract that the tool help already owns (guide §1:49 "if the agent cannot act on the
  information, delete it"). The duty line names the tool and its four arguments in plain words,
  which is the actionable half; the reference half is served at call time. **Scoped out,
  disclosed.**
- **Any change to `PRIVILEGED_TOOL_CATEGORIES`** (17 entries,
  `daemon/tools/_tool_registry.py:367`) — `nest-report` is **NON-privileged**. The verified
  census is **11 exact-set comparison sites + 6 `len(...) == 17` count sites + 1 pack string**
  (the mutation-matrix pack's literal) — all **untouched**. A non-privileged category is what makes the empty-allow inheritance
  work; making it privileged would require an explicit grant for every empty-allow agent AND
  would change 17 assertion sites. **Ruling R4 stands: non-privileged, zero privileged-set edits.**
- **A code-level default-grant** — rejected. The two fences exist precisely to prevent silent
  default grants: the R-SR16 fence in `daemon/tools/_tool_registry.py:294-309` and the Council
  Note 6 fence in `daemon/tools/instance.py:220-229`. A novel default-allow mechanism would
  punch through both.
- **Any `agents/` edit outside the 41 duty files and 40 grant files** — no drift fixes, no
  reformatting, no "while we are here" edits.

## The Duty Text (canonical — byte-identical in every file)

**Ruling P5-D1 — placement.** A uniform top-level section header `## Reporting Anomalies`
appended as the **last** top-level section of the artifact.

*Why a new uniform section rather than an existing one:* the 39 `rule.md` files carry **71
distinct first-level `##` section headers** between them. The most common are
`## Open questions for the orchestrator` (24 files), `## Must` (14), `## Must Not` (13) and
`## Guidelines` (14) — but **no header is shared by all 39**, and none of the common ones has
the semantics of a standing reporting duty (`## Must Not`, `## Never`, `## Cardinal Rules` are
all wrong homes). A uniform new section gives the pin **one anchor**, keeps the duty text
byte-identical and machine-verifiable, and places the duty at maximum distance from the
Cardinal block at the top of the file — so it cannot crowd out a Cardinal
(`docs/agent-prompt-writing-guide.md:82-89`, "≤7 Cardinal rules"; the count is **unchanged**).

**Ruling P5-D2 — the text.** First-person, agent POV, names only the tool, names the four
arguments in plain words (never the literal parameter identifiers), states the
one-report-per-distinct-problem discipline, and states that evidence collection is automatic.
No filename, path, category name, allow-list token, or system-internal token appears
(`docs/agent-prompt-writing-guide.md:106-108`, §10 pre-commit checklist line 1).

```markdown
## Reporting Anomalies

When I see something abnormal in my work — a defect, a contradiction, a stuck step — I report it
with `report_anomaly`: what kind of problem, what I saw, how serious, what I think caused it. One
report per distinct problem, never the same one twice. The tool collects the evidence itself.
```

Three sentences, three rendered lines, ≈285 characters. It is **byte-identical** in all 41
files — no per-agent variants, no "as applicable" clauses.

**Argument mapping (kept OUT of the prompt — the guide forbids implementation tokens in prose).**
The tool signature is fixed by the master plan at `report_anomaly(category, description,
severity, suspected_cause)` (`plan-overview.md:11`). The duty text maps 1:1 and deliberately
does not use those identifiers, so a phase-1 rename of an argument cannot invalidate the prompt:

| Tool argument (plan) | Plain words in the duty text |
|---|---|
| `category` | "what kind of problem" |
| `description` | "what I saw" |
| `severity` | "how serious" |
| `suspected_cause` | "what I think caused it" |

**Task 5.0 (reconciliation).** Before the edit lands, phase 1 confirms this mapping still
matches the shipped argument names. A mismatch is resolved by editing **this table**, never by
diverging the prompt text.

**Example placement — `agents/worker/rule.md` (its current sections are `# Rules`,
`## Must` (:3), `## Open questions for the orchestrator` (:193), `## Must Not` (:202),
`## Core Principles` (:246)).** The block is appended at end-of-file, after `## Core Principles`:

```markdown
## Core Principles
... (unchanged, verbatim) ...

## Reporting Anomalies

When I see something abnormal in my work — a defect, a contradiction, a stuck step — I report it
with `report_anomaly`: what kind of problem, what I saw, how serious, what I think caused it. One
report per distinct problem, never the same one twice. The tool collects the evidence itself.
```

Note the placement is **after** `## Core Principles`, not before it — the duty is a standing
obligation, not a core principle, and appending keeps the file's existing top-of-file
load-bearing zone untouched.

## The Per-Agent Edit Inventory (41 duty files — exactly one per agent)

**Ruling P5-D3 — canonical home.** `rule.md` for every agent that has one (it is the
hard-constraint home, `docs/agent-prompt-writing-guide.md:53-64`), because the duty is a
standing obligation and not a process step; `workflow.md` for `coder` (it has no `rule.md`);
`soul.md` for `image-comparator` and `skill-keeper` (neither `rule.md` nor `workflow.md`).
This mirrors the per-agent canonical-home shape of the report-integrity precedent
(`tests/unit/test_report_integrity_prompts.py:80-93`, `D_HOME_FILES`).

### Group A — `rule.md` (38 files)

Every agent that has a `rule.md`, minus `_inner_soul` (see P5-D5).

| # | Agent dir | Artifact | Action | In seal? |
|---|---|---|---|---|
| 1 | `_baby_template` | rule.md | append duty section | no |
| 2 | `_mother` | rule.md | append duty section | no |
| 3 | `approver` | rule.md | append duty section | **yes** |
| 4 | `approver[v2]` | rule.md | append duty section | **yes** |
| 5 | `architect` | rule.md | append duty section | **yes** |
| 6 | `ari` | rule.md | append duty section | **yes** |
| 7 | `blueprinter` | rule.md | append duty section | no |
| 8 | `charter` | rule.md | append duty section | **yes** |
| 9 | `critic` | rule.md | append duty section | no |
| 10 | `designer` | rule.md | append duty section | no |
| 11 | `developer` | rule.md | append duty section | **yes** |
| 12 | `developer[v2]` | rule.md | append duty section | no |
| 13 | `devops` | rule.md | append duty section | **yes** |
| 14 | `doc-maintainer` | rule.md | append duty section | no |
| 15 | `doc-writer` | rule.md | append duty section | **yes** |
| 16 | `experiencer` | rule.md | append duty section | no |
| 17 | `explorer` | rule.md | append duty section | no |
| 18 | `gaia` | rule.md | append duty section | no |
| 19 | `giter` | rule.md | append duty section | no |
| 20 | `governor` | rule.md | append duty section | **yes** |
| 21 | `image-reader` | rule.md | append duty section | no |
| 22 | `jober` | rule.md | append duty section | no |
| 23 | `kb-importer` | rule.md | append duty section | no |
| 24 | `kb-writer` | rule.md | append duty section | no |
| 25 | `leader` | rule.md | append duty section | **yes** |
| 26 | `maintenancer` | rule.md | append duty section | **yes** |
| 27 | `planner` | rule.md | append duty section | **yes** |
| 28 | `planner[v2]` | rule.md | append duty section | no |
| 29 | `project-manager` | rule.md | append duty section | no |
| 30 | `reviewer` | rule.md | append duty section | **yes** |
| 31 | `reviewer[v2]` | rule.md | append duty section | **yes** |
| 32 | `sketcher` | rule.md | append duty section | no |
| 33 | `tester` | rule.md | append duty section | no |
| 34 | `tidier` | rule.md | append duty section | **yes** |
| 35 | `tidier[v2]` | rule.md | append duty section | **yes** |
| 36 | `wanderer` | rule.md | append duty section | no |
| 37 | `watcher` | rule.md | append duty section | no |
| 38 | `worker` | rule.md | append duty section | no |

### Group B — `workflow.md` (1 file)

| # | Agent dir | Artifact | Action | Why this home | In seal? |
|---|---|---|---|---|---|
| 39 | `coder` | workflow.md | append duty section | no `rule.md` exists (measured: `rule.md` missing from `coder`, `image-comparator`, `skill-keeper`) | no — `agents/coder/soul.md` is the sealed coder row and is **not** touched |

### Group C — `soul.md` (2 files)

| # | Agent dir | Artifact | Action | Why this home | In seal? |
|---|---|---|---|---|---|
| 40 | `image-comparator` | soul.md | append duty section | no `rule.md`, no `workflow.md` (measured) | no |
| 41 | `skill-keeper` | soul.md | append duty section | no `rule.md`, no `workflow.md` (measured). It does carry a `## Rules` section (`agents/skill-keeper/soul.md:152`) — but a **uniform** section anchor across all 41 files wins over per-file tidiness, because the pin needs one anchor shape. | no |

**Invariant: 41 duty files ↔ 41 `meta.json`-bearing agents, one duty artifact per agent — and the pin asserts the decomposition `41 = 39 walk + 2 underscore specials` (39 non-underscore dirs walked + `_baby_template` + `_mother`), because the id-collapsed registry walk reaches only 34 of the 41 (see Pin 1).**
`41 = 38 rule.md + 1 workflow.md + 2 soul.md`.

### Exclusions (explicit, with evidence)

| Dir | Duty? | Grant? | Evidence |
|---|---|---|---|
| `_inner_soul` | **NO** | n/a (no `meta.json`) | no `meta.json`; `daemon/registry.py:19-25` `SKIP_DIRS` lists `_inner_soul` — it is shared scaffolding, not an agent. It has a `rule.md` (and 3 baseline rows), but a duty addressed to "I report" has no addressee. |
| `_prompt_system` | **NO** | n/a | no `meta.json`, no prompt artifacts at all (measured: 0 of soul/rule/workflow/tools_note), in `SKIP_DIRS`. Contributes 1 baseline row (`_prompt_system/innate-skills/chart/skill.md`) — **not** a prompt surface, untouched. |
| `watcher` | **YES** (rule.md) | **NO** — see P5-D6 | `tools.allow == []` → inherits the default universe. |

### v2 mirror pairing (5 pairs — explicit)

The 5 `[v2]` mirrors are `approver[v2]`, `developer[v2]`, `planner[v2]`, `reviewer[v2]`,
`tidier[v2]`. Measured byte-identity **before** the edit:

| Pair | soul.md | rule.md | workflow.md | tools_note.md | meta.json |
|---|---|---|---|---|---|
| `approver` ↔ `approver[v2]` | SAME | SAME | SAME | SAME | SAME |
| `developer` ↔ `developer[v2]` | SAME | SAME | SAME | **DIFF** | **DIFF** |
| `planner` ↔ `planner[v2]` | SAME | SAME | SAME | SAME | SAME |
| `reviewer` ↔ `reviewer[v2]` | SAME | SAME | SAME | **DIFF** | **DIFF** |
| `tidier` ↔ `tidier[v2]` | SAME | SAME | SAME | SAME | SAME |

**Ruling P5-D6.** Because every pair's `rule.md` is byte-identical **today**, the duty edit
must **preserve** that: the base and the mirror get the **same bytes**, and a new pin asserts
byte-identity of the `rule.md` pair for all 5 mirrors after the edit. For `meta.json`, the
pairs for `developer` and `reviewer` are already divergent (`developer` base lacks `image-gen`
that the mirror has; `reviewer` base has `image-gen` that the mirror lacks) — so the grant pin
asserts **containment** (`"nest-report" ∈ tools.allow`), never byte-equality, for `meta.json`.

## The Grant (40 `meta.json` files)

**Ruling P5-D7 — the grant is an explicit `tools.allow` entry, appended at the END of each
array.** A category-name entry expands to the whole category
(`daemon/tools/instance.py:563-569` — "Expand allow list (categories → individual tools)").
Order discipline: append after the last entry, never reorder (the `ari` trailing-entry
precedent; reordering churns 40 files and breaks the ari-array order pin).

### Edit set — 40 files (41 `meta.json` minus `watcher`)

Alphabetical, 4 columns × 10 rows. Every row: append the literal string `"nest-report"` as
the **last** element of the existing `tools.allow` array; change nothing else in the file.

| # | Agent dir | # | Agent dir | # | Agent dir | # | Agent dir |
|---|---|---|---|---|---|---|---|
| 1 | `_baby_template` | 11 | `designer` | 21 | `governor` | 31 | `project-manager` |
| 2 | `_mother` | 12 | `developer` | 22 | `image-comparator` | 32 | `reviewer` |
| 3 | `approver` | 13 | `developer[v2]` | 23 | `image-reader` | 33 | `reviewer[v2]` |
| 4 | `approver[v2]` | 14 | `devops` | 24 | `jober` | 34 | `sketcher` |
| 5 | `architect` | 15 | `doc-maintainer` | 25 | `kb-importer` | 35 | `skill-keeper` |
| 6 | `ari` | 16 | `doc-writer` | 26 | `kb-writer` | 36 | `tester` |
| 7 | `blueprinter` | 17 | `experiencer` | 27 | `leader` | 37 | `tidier` |
| 8 | `charter` | 18 | `explorer` | 28 | `maintenancer` | 38 | `tidier[v2]` |
| 9 | `coder` | 19 | `gaia` | 29 | `planner` | 39 | `wanderer` |
| 10 | `critic` | 20 | `giter` | 30 | `planner[v2]` | 40 | `worker` |

**Not edited:** `watcher` (P5-D8 — inherits; editing would strip its inherited tools).

*(The authoritative list is the live census — 41 `meta.json` minus `watcher` — and the pin
walks the tree; this table is the reviewed inventory and MUST be re-derived from the live tree
before the edit lands (task 11).)*

**Ruling P5-D8 — `watcher` is NOT edited.** Its `tools.allow` is `[]`. The empty-allow branch
(`daemon/tools/instance.py:551-561`) grants **every non-privileged category** — which, because
`nest-report` is non-privileged, **already includes it**. Appending one entry would convert
an empty allow into a 1-entry allow and therefore **silently strip every other tool watcher
currently inherits**. The correct grant for `watcher` is *inheritance*, and the grant-completeness
pin must encode that exception explicitly.

**Ruling P5-D9 — `_baby_template` and `_mother` ARE granted.** `_baby_template` is the clone
template (`daemon/tools/agent_mother.py:254`, `daemon/routers/agents.py:77`,
`daemon/routers/custom_agents.py:6`) — granting it means **every future agent inherits the
category at creation**, which is the mechanical guarantee behind "all agents". `_mother` is a
live system agent (`"system": true` in its `meta.json`).

**Ruling P5-D10 — runtime-created custom agents (no repo `meta.json`) inherit** through the
same empty-allow default universe. No repo edit is possible or needed; the grant pin must not
assert on them.

## The TWO-PATH Inheritance Guarantee (leader item 6)

The "all agents" requirement is satisfied by **two independent paths**, and **neither path
depends on a future contributor remembering anything**.

**Path 1 — agents cloned at runtime follow the `_baby_template` clone path.**
`daemon/tools/agent_mother.py:254` copies `agents/_baby_template` into the new agent's dir at
creation (also `daemon/routers/agents.py:77`, `daemon/routers/custom_agents.py:6`). `_baby_template`
is **in the 40-file grant edit set**, so its `tools.allow` carries `"nest-report"` and every
cloned agent inherits the key **in its own `meta.json`**, at creation, automatically. The key
rides the template — a future agent author gets the grant without knowing this lane existed.

**Path 2 — any agent created with an empty or absent `tools.allow` inherits every
NON-privileged category** via the default universe (`daemon/tools/instance.py:551-561` — the
branch that expands every category except those in `PRIVILEGED_TOOL_CATEGORIES`). `nest-report`
is **non-privileged by R4** (unchanged by R9, which renames the key only — the privilege
posture is a separate ruling), so Path 2 yields the grant too, with **no repo edit at all**.

**The residual — recorded explicitly, not hand-waved.** Exactly **one** population cannot be
reached by a repo edit: **DB-custom agents created with an EXPLICIT `tools.allow` array** (and
no repo `meta.json`). Their array was written by whoever created them, and the repo ceremony
cannot touch a DB row.

- **Size: unknown and DB-dependent** — it is a live-database census, not a repo count. The
  lane does **not** guess a number; the count is obtained by a read-only census at gate time
  (the same read-only discipline as the prompt-version census).
- **Disposition: disclosed in the morning report; NOT a staging blocker.** The population is
  by definition the set of custom agents an operator explicitly configured with a narrow
  allow-list — for those, the absence of `report_anomaly` is the operator's explicit
  configuration, not a lane defect. The disclosure names the count and the remedy (an
  operator adds `"nest-report"` to that agent's array), and it is **not** a reason to hold the
  stage.
- **A repo-created agent with an explicit array is NOT in this residual** — every repo agent's
  array is in the 40-file edit set.

## The Three Allow-Array Pins Phase 5 MUST Re-Pin

The append changes an agent's `tools.allow` array, and **three pins assert the exact content of
specific agents' arrays**. They were found by sweeping every `allow`-reading test in the tree;
they are the phase-5 equivalent of the seal re-pin, and they are **not optional** — each goes
red on the append and must be updated in the SAME commit as the grant.

| # | Pin | Assertion | Why the append breaks it | Required update |
|---|---|---|---|---|
| **PIN-A1** | `tests/unit/test_board_registration.py:285` `trailing_by_agent["ari"]` (asserted at `:291`) | `trailing = allow[allow.index(BOARD_CATEGORY) + 1 :]` must equal exactly `["self-alarm", "view-write", "board-link", "mcp_rpc", "wedge-capture", "notify-user"]` — i.e. **only categories appended AFTER `board` may follow it** | ari's array ends with `notify-user`; appending `"nest-report"` makes the trailing set `[..., "notify-user", "nest-report"]` | add `"nest-report"` to the list, with a comment recording **the prior value and the reason**, exactly as the file's own precedent comments do ("the set grew by one entry again" — ari-voice 2026-10-25). The pin's **intent** is unchanged: `nest-report` IS a category appended after the board. `BOARD_ALLOW_AGENTS = ("ari",)` (`:118`) ⇒ **ari is the only affected agent**. |
| **PIN-A2** | `tests/unit/test_gaia_agent.py:30` `GAIA_TOOLS_ALLOW = ["bash", "proc", "filesystem", "help", "system"]` (asserted at `:138`, `:220`, `:509`, `:516`, `:575`, `:629` — six sites) | gaia's `meta.json` allow **equals** the constant | appending makes the file's array 6 entries and the constant 5 | add `"nest-report"` to the constant. All six assertion sites follow the constant; **no other site changes**. |
| **PIN-A3** | `tests/unit/agents/test_sketcher_agent.py:72` `EXPECTED_ALLOW` (asserted at `:131`) | sketcher's allow **equals** the constant exactly (6 entries) | appending makes it 7 | add `"nest-report"` to the constant. **Also amend the `FORBIDDEN_ALLOW` fence's docstring** (`:81-90`) and the `test_closed_allow_is_the_fence` docstring (`:133-140`): they state "the closed allow IS the fence — no shell, no filesystem, no write surface". Adding a write category makes "no write surface" **false**. The assertion itself stays green (`nest-report` is not in `FORBIDDEN_ALLOW`), but the **documented intent** must be corrected honestly — a false intent claim is worse than an honest one (`docs/agent-prompt-writing-guide.md:74`). **This is the sketcher's deliberate closed-allow fence being widened — it is disclosed as a morning item (U-7), not silently absorbed.** |

**Pins verified as SAFE (no update needed).**
- `tests/unit/test_wanderer_agent.py:293`, `tests/unit/test_devops_agent.py:256`,
  `tests/unit/test_coder_agent.py:204` — `agent.tools.allow == meta["tools"]["allow"]`: both
  sides change together.
- `tests/unit/test_wanderer_agent.py:179-200` — subset check + `len(allowed) >= len(CONSTANT)`:
  an append is safe by construction.
- `tests/unit/test_maintenancer_agent.py:221` — `REQUIRED_ALLOW` subset check: safe.
- `tests/unit/test_devops_agent.py:214` — 8-standard-tools subset check: safe.
- `tests/unit/test_ari_user_ruling_allow_sets.py:96-136` — asserts the four ruled categories
  are ari-only; `nest-report` is not one of them: safe.
- `tests/unit/plugin_subsystem/test_tier1_wiring.py:156-160` — reads `SKETCHER_ALLOW` from the
  real `meta.json`: dynamic, safe.
- `tests/unit/test_chart_image_delivery_audit.py:453-455` — `"image" in charter.meta.json`:
  containment, safe.
- Every `len(tools) == N` assertion in the tree (`tests/test_project_tools.py:670`,
  `tests/test_job_queue_tools.py:41`, `tests/job_queue/test_jober_watch_integration.py:686`, …)
  counts **factory-resolved** tool sets, not an agent's allow array: unaffected.

## The Ceremony (step by step)

**All steps are mechanical except the three marked ⚙ VERIFY. NONE requires a human approval
turn** — see the mechanism note below.

| # | Step | Class | Command / action | Acceptance |
|---|---|---|---|---|
| 1 | Edit the 41 duty files (append the uniform section) | mechanical | scripted append; the section text is the single constant | 41 files changed; the pin asserts 41 = 39 walk + 2 underscore specials, all carrying the section |
| 2 | Edit the 40 grant files (append `"nest-report"`) | mechanical | JSON edit preserving key order and array order; `json.dumps(indent=2)` matching the house formatting | 40 files changed; `allow` length +1 each |
| 3 | ⚙ **VERIFY** — duty-text closure grep | ⚙ verify | grep the agent dirs for forbidden tokens per `docs/agent-prompt-writing-guide.md:246-260`: `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions`, and the `.md` filename forms | zero hits attributable to the duty block (pre-existing hits enumerated and excluded) |
| 4 | ⚙ **VERIFY** — v2 mirror byte-sync | ⚙ verify | sha256 the 5 `rule.md` pairs | each pair byte-identical |
| 5 | Regenerate the prompt-artifact baseline | mechanical | `python3 scripts/protection/generate_prompt_artifact_baseline.py` (no flags — regenerate mode; the script's own usage at `scripts/protection/generate_prompt_artifact_baseline.py:17-25`) | `agents/.prompt-artifacts.sha256` = 161 entries, all paths unchanged (no file added or removed — only content changed) |
| 6 | ⚙ **VERIFY** — baseline check | ⚙ verify | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <repo root>` | exit 0, 161/161 clean. **Never** `cd agents/ && sha256sum -c` — the manifest keys are `agents/`-prefixed, so that idiom yields 161 spurious FAILEDs (LESSONS 2026-10-20 L2) |
| 7 | Re-pin the 17 sealed rows | mechanical+judgement | recompute each row's content-sha256 from the **real file bytes**; write the new value **and** an attribution comment recording the prior value and the reason; **only the changed rows keep new values** (the 18 untouched rows keep their values verbatim) | 17 rows updated, 18 rows byte-unchanged, membership still 35 |
| 7b | **Re-pin the 3 allow-array pins (PIN-A1/A2/A3)** | mechanical+judgement | `tests/unit/test_board_registration.py:285` `trailing_by_agent["ari"]` (add `"nest-report"`, with a prior-value + reason comment); `tests/unit/test_gaia_agent.py:30` `GAIA_TOOLS_ALLOW` (add `"nest-report"`); `tests/unit/agents/test_sketcher_agent.py:72` `EXPECTED_ALLOW` (add `"nest-report"`) **and amend the two docstrings that claim "no write surface"** | all three pins green; the ari trailing set grows by exactly one entry; the two other constants grow by exactly one entry; the sketcher docstrings state the widened fence honestly |
| 8 | Add the two new pins | mechanical | new test file (see below) | new pins green |
| 9 | Run the phase-5 pin set | mechanical | the new pin file + `tests/test_chart_image_delivery_e2e.py` (the tripwire) + the prompt-artifact baseline check | all green; tripwire reports 0 stale |
| **C8** | **COMMIT C8 — "P5a duty"**: steps 1, 4, 5, 6, 7, 8, 9 land **together** | mechanical | edit the 41 prompt `.md` files, regenerate the baseline, re-pin the 17 seal rows, add the new pins | the tree is **GREEN at the C8 boundary**: baseline `--check` exit 0, sealed tripwire 0 stale, duty-pin walk 41 = 39 walk + 2 underscore specials |
| 10 | ⚙ **VERIFY** — DB-masking census | ⚙ verify | read-only SELECT against `prompt_versions` (see caveat 1 below) | census result recorded in the morning report; any masking row listed |
| **C9** | **COMMIT C9 — "P5b grants"**: steps 2, 3b, 3c, 7b land **together** | mechanical | append `"nest-report"` to the 40 allow arrays, re-pin the 3 allow-array pins, amend the sketcher docstrings | the tree is **GREEN at the C9 boundary**: grant-completeness walk 40/40, the 3 allow-array pins green, no baseline re-stamp (see AM-3 below) |

**AM-17 — the ceremony lands as TWO ordered commits, not one 81-file commit.**
The single-commit shape is **REJECTED** by the architect's adjudication 6
(`architecture-recommendation.md` §7): it is bisect-hostile and unnecessary, because the duty
pin and the grant pin are **independent**. Two green boundaries beat one giant commit.

| Commit | Contents | Atomic because | Boundary gate |
|---|---|---|---|
| **C8** — P5a duty | the 41 prompt `.md` files + the baseline re-stamp + the 17 seal re-pins | the baseline row and the file content it pins must **never disagree across a boundary** (house rule; the ari-voice precedent, v0.27.8) | baseline `--check` exit 0; sealed tripwire 0 stale; duty-completeness walk 41 = 39 walk + 2 underscore specials |
| **C9** — P5b grants | the 40 `meta.json` grants + the 3 allow-array pins + the sketcher doc amendment | the grant-completeness walk is **all-or-nothing** — a partial grant set is worse than none | grant-completeness walk 40/40; the 3 allow-array pins green; **no baseline re-stamp** (AM-3) |

**AM-3 (settlement — recorded here because the ceremony is where it bites).**
1. **`.agents/shared/metrics/tool-categories.json` carries ZERO per-agent grant info.** Its
   payload is `{categories, count, vocabulary_version, reserved, source}`, derived from
   `registry.CATEGORY_MODULES` + the AST-scanned `@register_tool_category` call sites
   (`tests/unit/tools/test_tool_category_artifact.py:190-209`). **Phase 5's 40-file grant
   ceremony therefore requires NO artifact regeneration.** (Phase 1's 57→58 @ v4 and phase 3's
   58→59 @ v5 regens remain mandatory, each in the SAME commit as its bump.)
2. **The prompt-artifact baseline covers ONLY the versioned `.md` artifacts**
   (`daemon/tools/prompt_artifacts.py:119-140` — `VERSIONED_PROMPT_ARTIFACTS`) — **`meta.json` is
   NOT in the set.** Therefore **phase 3's three `meta.json` edits (C5) need NO re-stamp**, and
   phase 5's C9 grant commit needs no baseline row either. The baseline is re-stamped **exactly
   once** in the whole lane: in **C8**.

### Seal inventory reconciliation (VERIFIED against `tests/test_chart_image_delivery_e2e.py:1425-1505`)

The sealed set is **35 rows**: **28 agent rows + 7 daemon rows**. This table reconciles every
agent row against **my** edit set (41 duty files + 40 `meta.json` grants).

| Sealed artifact | Rows | Which agents | In my edit set? | Re-pin? |
|---|---|---|---|---|
| `rule.md` | **16** | approver, approver[v2], architect, ari, charter, developer, devops, doc-writer, governor, leader, maintenancer, planner, reviewer, reviewer[v2], tidier, tidier[v2] | **all 16** — each agent's duty home is its `rule.md` | **16 re-pins** |
| `meta.json` | **1** | charter | **yes** — charter is in the 40-file grant set | **1 re-pin** |
| `workflow.md` | 3 | charter, ari, project-manager | **NO** — all three have a `rule.md`, which is their duty home | untouched |
| `soul.md` | 3 | charter, coder, wanderer | **NO** — charter → `rule.md`; coder → `workflow.md`; wanderer → `rule.md` | untouched |
| `tools_note.md` | 2 | developer[v2], planner[v2] | **NO** — `tools_note.md` is ruled OUT of scope for this lane | untouched |
| `skills-template/*.md` | 2 | charter (2 files) | **NO** | untouched |
| `_prompt_system/innate-skills/chart/skill.md` | 1 | — | **NO** | untouched |
| 7 `daemon/**` rows | 7 | sources/base, sources/registry, sources/dispatcher, discord adapter, slack adapter, telegram adapter, constants.py | **NO** — no daemon file is touched by phase 5 | untouched |

**The `coder` question, verified (leader item 4):** `agents/coder/workflow.md` is **NOT** a
sealed row. The **only** sealed coder row is `agents/coder/soul.md` — and coder's duty home is
`workflow.md`, so `coder/soul.md` is **not** touched. **The total therefore stays at 17**
(16 `rule.md` + 1 `charter/meta.json`); it does **not** move to 18.

**Re-pin total: 17. Untouched sealed rows: 35 − 17 = 18.** The anti-blanket-re-stamp pin
(the phase-5 grant pin file) asserts that the 18 untouched rows **keep their original
values** — so a wholesale re-stamp that silently legalised a change to a sealed segment
fails the gate.

### Acceptance criteria for the ceremony (AC-5.1 … AC-5.5)

These are the **explicit acceptance criteria** for the ceremony block above (leader item 9).
Each is mechanically checkable and each has a named owner step.

**AC-5.1 — Closure-grep AC (forbidden-token closure).**
After the edit, run:

```
grep -rEn "\.md|meta\.json|tools\.allow|daemon/|agents/|_tool_registry|innate_skills|agent_id=" \
  agents/*/rule.md agents/*/workflow.md agents/*/soul.md
```

over the **41 edited files**. **Expected: ZERO hits attributable to the duty section.** The
duty text names ONLY the tool (`report_anomaly`) and the trigger — it must carry no filename,
path, category-key, or system-internal token (`docs/agent-prompt-writing-guide.md:15-27`,
`:91-117` forbidden-token closure; §10 pre-commit checklist line 1). Pre-existing hits
elsewhere in those files (operational paths, tool parameter values) are enumerated in the
report and excluded — but **no hit may originate inside the duty section**. The category key
(`nest-report`) is a category name, not a tool name, so it must **not** appear in the prompt
either.

**AC-5.2 — Cardinal-count-first AC (count first, edit second).**
For **every** edited `rule.md`, the agent's Cardinal count is **COUNTED BEFORE the edit**
(mechanically: count the entries under the `^## Cardinal` section) and asserted **≤7 after**
(`docs/agent-prompt-writing-guide.md:82-89`). The duty line lands **under Guidelines, never as
a new Cardinal** — the count must be **identical** before and after, because the duty section
is a **new top-level section**, not a new bullet inside the Cardinal block. The before/after
counts are recorded per file in the gate report.

**AC-5.3 — the 3 no-`rule.md` agents: home selection is justified by baseline-count growth.**
`coder` → `workflow.md`; `image-comparator` → `soul.md`; `skill-keeper` → `soul.md`.
**Creating a `rule.md` for them was REJECTED**: a new file in an agent dir is a **NEW entry**
in the versioned-artifact baseline (`agents/.prompt-artifacts.sha256`, 161 entries — the
baseline walks every versioned artifact under every agent dir), so three new files would take
the baseline **161 → 164** and grow the ceremony's baseline re-stamp, its `--check` surface,
and its stage-time drift check by three entries. The chosen homes keep the baseline count at
**161**: only the **hash of an existing file** changes. (The census basis: `rule.md` is absent
from `coder`, `image-comparator`, `skill-keeper`; `workflow.md` is absent from
`image-comparator` and `skill-keeper`.)

**AC-5.4 — v2 mirror pairs move together (pin id cited).**
The 5 pairs (`approver`, `developer`, `planner`, `reviewer`, `tidier`) must have byte-identical
`rule.md` **after** the edit, exactly as they are before. The enforcing pin is the
**byte-sync pin in the new phase-5 pin file** (`tests/unit/test_anomaly_report_duty_prompts.py`
— the v2-pair leg), with mutation leg **M-P5-6** (perturb one mirror → the pin REDs). The
pre-edit byte-identity census (all 5 pairs identical today) is in the *v2 mirror pairing*
table above.

**AC-5.5 — generated-once AC.**
The duty text is emitted by **ONE template constant** and applied **mechanically** to all 41
files — a scripted append (or a scripted `sed`/Python edit driven by a single constant in the
phase-5 task list), **never** a per-agent hand-write. The byte-identical assertion (the
duty-prompt completeness pin, which reads the marker out of all 41 files and compares the
**exact bytes**) is the mechanical proof that no hand-edit crept in; a hand-written variant
that differs by one character fails that pin.

**AC-5.6 — census re-run-at-landing AC (AM-17 / architect §9).**
The `prompt_versions` census is **DONE** (see Caveat 1: zero `approved`+`is_selected` rows
anywhere; zero `artifact='rule'` rows of any state; 6 rows / 2 keys total). The ceremony step
is now a **RE-RUN AT LANDING** of the same SELECT-only query, asserting **nothing has changed
since the fresh census**. If a new `approved`+`is_selected` row, or any new `artifact='rule'`
row, appeared in the interval, the census result is **re-baselined** and the affected pairs are
named in the morning report **before C8 lands**. The re-run is read-only and carries **zero
user dependency**; the only user-owned item is the **disposition** of the pre-existing
`(reviewer, workflow)` `pending_approval` row (morning item **U-2b**).

### Why no human approval turn is needed (the mechanism)

- Runtime writes to prompt artifacts are **refused**: `daemon/tools/filesystem.py:1121`
  (`write_file`) and `:1527` (`edit_file`) both call
  `daemon/tools/prompt_artifacts.py` `check_write` (`:265-323`), which refuses any file under
  an agents root that is in `VERSIONED_PROMPT_ARTIFACTS` (`:119-140`). **That regime guards
  only the daemon's agent tool layer** — it does not constrain a repository commit.
- The repo files **are** the live source the loader serves: `daemon/loader.py:896-926`
  `load_agent_prompts` reads `soul.md`, `rule.md`, `workflow.md`, `tools_note.md` (falling back
  to `tools.md`), `memory.md`, `skill.md` from disk.
- **T10 — overrides-only seeding** (`daemon/loader.py:333-343`): "The template file stays the
  LIVE FALLBACK — there is NO boot-time full-seed and NO hash-guarded lazy-seed". The DB
  precedence is `selected_version → latest_approved → template_file`, and only for CORE-class
  artifacts.
- The prompt cache reloads on file mtime change: a repo commit changes the **served** prompt on
  the next cache miss — **no daemon restart, no human approval turn**.
- The boot-time baseline drift check is **WARN-only, non-blocking**
  (`daemon/api.py:1262-1277`, "drift is operator-visible (WARN) and NEVER boot-blocking").

**Therefore: the repo-side edit + same-commit re-stamp IS the sanctioned path.** Precedents:
ari-voice (v0.27.8), ari-tools (v0.26.0), metrics-agent-tools (v0.28.9).

### Caveat 1 — the DB-masking census (DONE — re-confirm at landing)

**The census has been RUN (SELECT-only, read-only, zero writes). The fresh result:**

| Finding | Result |
|---|---|
| `approved` + `is_selected=1` rows **anywhere** | **ZERO** — `is_selected=0` on every row |
| `artifact='rule'` rows of **any** state, for any agent | **ZERO** — **no planned `rule.md` edit can be shadowed** |
| Table total | **6 rows / 2 keys** |

**The 2 keys, and what each one does and does not affect:**

| Key | Rows | Classification | Effect on the edit set |
|---|---|---|---|
| `(ari, user)` | 4 approved | **MEMORY class, DB-effective** — the `user.md` artifact | **`user.md` is OUTSIDE the edit set** (phase 5 touches `rule.md` / `workflow.md` / `soul.md` only) ⇒ **no effect** |
| `(reviewer, workflow)` | 2 | **v1 archived, v2 `pending_approval` since 2026-10-19** | the **latent conditional shadow** — it shadows ONLY if the lane edits `reviewer/workflow.md` **AND** that row is later approved. The lane's duty home for `reviewer` is **`reviewer/rule.md`**, and `rule` has **zero** rows of any state ⇒ **no shadow today**. |

**Disposition of the `(reviewer, workflow)` row is USER-OWNED** (morning item **U-2b**): re-approve
through the prompt-version API, or accept the file-fallback. The lane takes **no** action on
that row and **never** deletes a user-owned override.

**The ceremony step becomes a RE-RUN-AT-LANDING acceptance criterion (AC-5.6).** Step 10 is no
longer "run a census of unknown outcome" — it is: **re-run the same SELECT at landing** and
confirm **nothing has changed since the fresh census**. If any NEW `approved`+`is_selected` row
or any new `artifact='rule'` row appeared in the interval, the census result is **re-baselined**
and the affected pairs are added to the morning report before C8 lands. The re-run is
**SELECT-only** and takes **zero user dependency**.

**Census query (SELECT-only — never a write, never a state transition):**

```sql
SELECT agent_id, version_tag, artifact, version, state, is_selected
FROM prompt_versions
WHERE state IN ('approved', 'pending_approval')
  AND artifact IN ('rule', 'workflow', 'soul')
ORDER BY agent_id, version_tag, artifact, version;
```

*(The artifact key is the **bare name** — `'rule'`, `'workflow'`, `'soul'` —
`daemon/repositories/prompt_version/models.py:255-262`. The full-table check for
`is_selected=1` and for `artifact='rule'` runs beyond this filtered slice — the fresh census
covered **every** row in the table.)*

### Caveat 2 — ceremony size

81 files change in one commit (41 duty + 40 grants), plus the baseline manifest, plus the seal
file. That is the size of the ceremony; it is not reducible without dropping agents from "all
agents". Mitigation: the pin walk is the authority, not a hand list, so a missed file is caught
mechanically rather than by review.

## The Two New Pins

**New file: `tests/unit/test_anomaly_report_duty_prompts.py`** — modelled on
`tests/unit/test_report_integrity_prompts.py`.

Pin 1 — **duty-prompt completeness (dynamic walk over DIRS, not collapsed ids).**

**The id-collapsed walk CANNOT reach 41/41 — and the gap is 7 real files.** The
report-integrity precedent's `_iter_agent_ids()`
(`tests/unit/test_report_integrity_prompts.py:145-157`) does two things that make it
structurally unable to cover this edit set:

1. it **skips every underscore dir** (`entry.name.startswith("_")` at `:147`), and
2. it **collapses the `[v2]` suffix to the base id** and then resolves the **operative dir**
   (`_operative_dir` at `:135-142`), so a `[v2]` pair is checked at ONE dir.

Measured against the live tree: that walk yields **34 ids → 34 operative dirs**, covering only
**34 of the 41** duty files. The **7 uncovered** duty files are:

| Uncovered by the id-collapsed walk | Why |
|---|---|
| `_baby_template` (rule.md) | underscore dir — skipped outright |
| `_mother` (rule.md) | underscore dir — skipped outright |
| `approver` (rule.md) | the base counterpart of a `[v2]` pair — the walk resolves to `approver[v2]` |
| `developer` (rule.md) | base counterpart — the walk resolves to `developer[v2]` |
| `planner` (rule.md) | base counterpart — the walk resolves to `planner[v2]` |
| `reviewer` (rule.md) | base counterpart — the walk resolves to `reviewer[v2]` |
| `tidier` (rule.md) | base counterpart — the walk resolves to `tidier[v2]` |

**This is not cosmetic.** Phase 5 edits BOTH members of each `[v2]` pair (the base `rule.md`
**and** the `[v2]` `rule.md` are two separate files, and the base one is the frozen mirror per
`docs/agent-prompt-writing-guide.md:234-242`), and the underscore two carry the duty for
**clones** (P5-D9's whole purpose is propagating the duty to agents created later). An
unpinned file is a **legalized silent loss** — nothing would catch a dropped duty line in any
of the 7.

**The fix — the walk enumerates DIRS, not collapsed ids.**

```
walk_dirs = every dir under agents/ that carries a meta.json,
             EXCLUDING the underscore dirs EXCEPT the two named specials
             (_baby_template, _mother), and EXCLUDING _inner_soul / _prompt_system
             (no meta.json — they are not agents)
```

For each walked dir, resolve **that dir's own** canonical home (the dir's own `rule.md`, else
its own `workflow.md`, else its own `soul.md`) and assert the duty marker is present. **No
`[v2]` collapsing and no operative-dir resolution** — a base dir and its `[v2]` mirror are
checked **independently**, because they are independent files.

**The assertion form (the pinned count):** **`41 = 39 walk + 2 underscore specials`.**
- **39 walk** = the 39 non-underscore `meta.json`-bearing dirs (34 base + 5 `[v2]` mirrors).
- **2 underscore specials** = `_baby_template` + `_mother`.
- Total **41 duty files**, each pinned independently.

The pin asserts the walked count is **39** and the specials count is **2** (so a newly added
agent dir cannot silently change the total), and that all **41** carry the byte-identical duty
block.

Pin 2 — **grant completeness.** For every `agents/*/meta.json` (including the underscore
specials that carry one), assert:
- if `tools.allow` is **non-empty** → `"nest-report" ∈ tools.allow`, and it is the **last**
  element (the order discipline);
- if `tools.allow` is **empty/absent** → the agent inherits via the default universe, and the
  pin asserts the **inheritance** invariant instead (no explicit entry required) — the
  documented exception.

**Mutation legs (mandatory):**
- **M-P5-1** — remove the duty block from one agent's canonical home (e.g. `agents/giter/rule.md`)
  → Pin 1 reds. Restore → greens.
- **M-P5-2** — remove `"nest-report"` from one `meta.json` allow array (e.g.
  `agents/gaia/meta.json`) → Pin 2 reds. Restore → greens.
- **M-P5-3** — move `"nest-report"` from the last position to the middle of one array → the
  order assertion reds (proves the order pin is not vacuous).
- **M-P5-4** — add a NEW agent dir with a `meta.json` and no duty line → Pin 1 reds
  ("adding a new agent without the guidance fails" — the registry-completeness property the
  report-integrity precedent exists to enforce).
- **M-P5-5** — perturb one byte of one edited `rule.md` → the sealed tripwire reds *and* the
  baseline `--check` reds (proves the two fences are live).
- **M-P5-6** — flip one of the 5 v2 mirror `rule.md` files so the pair diverges → the
  byte-sync pin reds.

**New file: `tests/unit/test_anomaly_report_grant_pins.py`** or the same file — the grant
completeness + order + inheritance-exception pins, plus a **sealed-tripwire-zero-stale** leg
asserting the 17 re-pinned rows match the real bytes and the 18 untouched rows still match
their original values (guards against a blanket re-stamp that would silently legalise a change
to a sealed segment).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Reconcile the duty-text argument mapping with phase 1's shipped signature | phase 1 landed | the mapping table above matches the shipped `report_anomaly` signature; mismatch resolved here, never by diverging the prompt |
| 2 | Append the uniform duty section to the 41 canonical-home artifacts | 1 | the pin asserts **41 = 39 walk + 2 underscore specials**, every file carrying the byte-identical block; 0 extra files touched |
| 3 | Append `"nest-report"` to the 40 non-empty allow arrays | phase 1 category registration landed (the entry must resolve to a real category) | 40/40 arrays carry the entry as the last element; `watcher` untouched |
| 3b | **Coordinate with phase 3** — phase 3 appends `"nest-report-read"` to `agents/{maintenancer,devops,wanderer}/meta.json`; phase 5 appends `"nest-report"` to the same three files | phase 3's task 6 landed | the three files carry **both** entries, each appended at the end, in a defined order. **Ruling: phase 3's edit lands FIRST, phase 5's append goes after it** (so ari-style trailing-order pins and the three-file arrays are edited once, not twice). If phase 5 lands first, phase 3's append must be re-applied on top — never a blind re-append that reorders an array. |
| 3c | **Re-pin the 3 allow-array pins (PIN-A1/A2/A3)** | 3 | the ari trailing set, `GAIA_TOOLS_ALLOW`, and sketcher's `EXPECTED_ALLOW` each grow by exactly one entry; the sketcher docstrings are amended honestly |
| 4 | Run the forbidden-token closure grep over the 41 edited artifacts | 2 | zero new hits; pre-existing hits enumerated and excluded in the report |
| 5 | Regenerate the prompt-artifact baseline | 2, 3 | 161 entries, paths unchanged |
| 6 | Re-pin the 17 sealed rows with prior value + attribution | 2, 3 | 17 new values from real bytes; 18 rows unchanged; membership 35 |
| 7 | Verify the baseline with `--check --tree` | 5 | exit 0 |
| 8 | Write the duty + grant pin file(s) with the 6 mutation legs | 2, 3 | pins green; each mutation leg observed RED→GREEN with a recorded digest |
| 9 | **RE-RUN the DB-masking census at landing** (SELECT only) — the census itself is **DONE** (zero `approved`+`is_selected` rows; zero `rule` rows; 6 rows / 2 keys) | — | the re-run confirms **nothing changed since the fresh census**; any new `approved`+`is_selected` or `rule` row ⇒ re-baseline + list the pairs in the morning report **before C8** |
| 10 | Register the new packs in `.agents/tester/PACKS.md` | 8 | pack rows registered with the mechanical node count |
| 11 | Re-derive the edit inventories from the live tree and reconcile against this plan | 2, 3 | zero drift; any drift recorded as a plan correction (the brief's counts were off: 42 dirs → **43**, 39 `meta.json` → **41**) |
| **C8** | **Land commit C8 — "P5a duty"**: steps 1, 2 (duty half), 4, 5, 6, 7, 8, 9 land together | 1, 4, 5, 6, 7, 8 | tree GREEN at the boundary: baseline `--check` exit 0 (161/161), sealed tripwire 0 stale, duty-completeness walk **41 = 39 walk + 2 underscore specials**, the 5 v2 pairs byte-identical |
| **C9** | **Land commit C9 — "P5b grants"**: steps 3, 3b, 3c land together | C8, phase 3's C5 landed | tree GREEN at the boundary: grant-completeness walk 40/40 (watcher inherited), the 3 allow-array pins green, **no baseline re-stamp** (AM-3: `meta.json` is not in the versioned-artifact set) |

## Coupling

- **Tight with phase 1** — the category key string `nest-report` must be byte-identical to
  the key phase 1 registers in `daemon/tools/_tool_registry.py` (`CATEGORY_MODULES` /
  `@register_tool_category`), because the allow entry expands by exact category key
  (`daemon/tools/instance.py:563-569`). A mismatch silently degrades to "unknown item → treated
  as a literal tool name" (`instance.py:567-568`) and the agent gets **no** tool.
- **Tight with phase 1** — the phase-1 `tools.allow` registration checklist
  (`daemon/tools/upgrade_tools.py:150-184`) governs the *code* registration; this phase governs
  the *per-agent* grants. Both must agree on the category key.
- **Loose with phase 3** — **phase 3** (not phase 4) adds the **privileged** `nest-report-read`
  category (`phase3-plan.md` task 5; entry **#18** of `PRIVILEGED_TOOL_CATEGORIES`, with all
  18 privileged-set assertion sites updated in ONE commit) and appends `"nest-report-read"` to
  `agents/{maintenancer,devops,wanderer}/meta.json`. **Phase 5 does NOT grant `nest-report-read`
  to any agent** — a privileged category needs its own adjudication, and R4's grant ruling
  names `nest-report` only. If a later phase needs a per-agent `nest-report-read` grant, it is
  a **separate ceremony** — recorded as an open coupling, not merged here.
- **Tight with phase 3 (edit order)** — the three `meta.json` files phase 3 touches
  (`maintenancer`, `devops`, `wanderer`) are also in phase 5's 40-file grant set. **Ruling:
  phase 3's edit lands FIRST; phase 5's append goes after it.** A blind double-append would
  reorder an array and break the trailing-order discipline.
- **Loose with phase 4** — phase 4 delivers the FE tab; it surfaces cluster counts and does
  not touch the prompt surfaces or the allow arrays.
- **Independent of phases 2 and 3** — the clustering service, the F23 family, and the
  read tools share no file with the prompt surfaces.
- **Tight with phase 6** — phase 6's gate ladder runs this phase's pins; the release posture
  depends on the `agents/` tree having changed (a FULL-payload release, not daemon-only).

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | A DB `prompt_versions` row masks the duty-prompt file edit | **Now LOW for the edit set** — the census is **DONE**: zero `approved`+`is_selected` rows anywhere; zero `artifact='rule'` rows of any state; 6 rows / 2 keys total | Low (residual) | **Re-run the census at landing (AC-5.6)** — a new row could appear before C8 lands. The only masking-capable row is `(reviewer, workflow)` `pending_approval` since 2026-10-19, whose **disposition is user-owned** (morning item U-2b); `reviewer`'s duty home is `rule.md`, which has zero rows ⇒ no shadow today |
| 2 | A `meta.json` is missed in the 40-file edit set | High (one agent silently lacks the tool) | Medium | The grant-completeness pin walks the tree — a miss is a red, not a silent gap; the hand table is advisory, the pin is authority |
| 3 | The `watcher` allow array is edited | High (watcher loses every inherited tool) | Low | P5-D8 explicit ruling + the inheritance-exception pin |
| 4 | A v2 mirror pair diverges | Medium (the served prompt differs by version tag) | Medium | The byte-sync pin over all 5 pairs + M-P5-6 mutation leg |
| 5 | The seal re-pin is done as a blanket re-stamp (all 35 rows rewritten) | High (a sealed-segment change is legalised silently) | Medium | The re-pin pin asserts the 18 untouched rows still carry their **original** values |
| 6 | The baseline lands in a different commit from the edits | Medium (a red baseline between commits) | Low | Step 5 is bound to the same commit as steps 1-2; the `--check` in step 6 is the pre-commit gate |
| 7 | The duty text accidentally contains a forbidden token | Medium (prompt-prose violation) | Low | Step 3 closure grep + the guide §10 checklist; the text is fixed and contains no tokens |
| 8 | `nest-report` is later found to be privileged, invalidating the empty-allow inheritance | High (watcher loses the tool) | Low | The non-privileged ruling is recorded in `decisions.md` R4 (phase-1 owned) and asserted by phase 6's release posture |
| 9 | The prompt cache does not pick up the file change | Medium (the duty is not served) | Low | The loader reloads on mtime change; a post-commit cache check is part of phase 6's staged boot smoke |
| 10 | An exact-constant allow-array pin is missed (ari trailing set, gaia, sketcher) | High (a red pin at gate time, or a silently stale fence) | Medium | The three pins are enumerated by name with their exact locations (PIN-A1/A2/A3) and re-pinned in the SAME commit as the grant (ceremony step 7b) |
| 11 | The sketcher's deliberate closed-allow fence ("no write surface") is widened without disclosure | Medium (a documented security intent becomes false) | Medium | The two sketcher docstrings are amended honestly in the same commit; the widening is disclosed as morning item U-7 — the alternative (excluding sketcher) would violate the "all agents" ruling |
| 12 | Phase 3 and phase 5 both edit 3 `meta.json` files | Medium (a double-append reorders an array) | Medium | Fixed edit order: phase 3 first, phase 5 appends after (task 3b) |

## Exit Criterion

Phase 5 is done when **all** of the following hold, on the merged tree:

1. The duty-prompt completeness walk reports **41 = 39 walk + 2 underscore specials** agents
   carrying the duty in their own canonical home (the pin walks **dirs**, not collapsed ids —
   the id-collapsed registry walk reaches only 34 of the 41 and would leave the 5 `[v2]` base
   counterparts + the 2 underscore specials unpinned), and the mutation leg M-P5-4 (new agent
   dir) is observed **RED**.
2. The grant-completeness walk reports **40/40** explicit grants present as the last array
   element, `watcher` inherited (and mutation leg M-P5-2 observed **RED**).
3. `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <root>`
   exits **0** (161/161 clean).
4. The sealed tripwire (`tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts`)
   reports **0 stale** rows, with the 17 re-pinned rows matching real file bytes and the 18
   untouched rows unchanged.
4b. The three allow-array pins are green: `tests/unit/test_board_registration.py` (ari
   trailing set = `[..., "notify-user", "nest-report"]`), `tests/unit/test_gaia_agent.py`
   (6 sites), `tests/unit/agents/test_sketcher_agent.py` — and the sketcher's two docstrings
   no longer claim "no write surface".
5. The 5 v2 mirror `rule.md` pairs are byte-identical (M-P5-6 observed **RED** when perturbed).
6. The DB-masking census has been **re-run at landing** (SELECT only) and confirms **nothing
   changed since the fresh census** — zero `approved`+`is_selected` rows, zero `artifact='rule'`
   rows, 6 rows / 2 keys total; the `(reviewer, workflow)` `pending_approval` row's
   **disposition** is named in the morning report as **U-2b** (user-owned).
6b. **Both commits are green at their own boundary**: **C8** (duty + baseline re-stamp + the 17
   seal re-pins) and **C9** (40 grants + the 3 allow-array pins + the sketcher doc amendment)
   are **two separate commits**, not one — and the per-commit smoke set (phase 6's AM-17 ladder)
   is green after each.
7. The pack registrations exist in `.agents/tester/PACKS.md` with mechanical node counts.

**Phase 5 ships NO code change** — it is a prompt-surface + configuration change plus tests.
