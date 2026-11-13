# Phase 1 Implementation Spec — Sonic Team COMBINED v1.1+v1.2 (2026-11-13)

**Status:** AUTHORITATIVE for the combined rework lane. This file SUPERSEDES the
superseded v1/v1.1 wording listed in §11 — the older `phase1-implementation-spec.md`
stands as the v1 historical record and is NOT rewritten.
**Ruling:** Emperor, 2026-11-13 — v1.2 is MERGED INTO v1.1: one release, one ceremony.
**Worktree:** `/home/nea/ensemble-src-wt-sonic-v11` · **Branch:** `feature/sonic-team-v1-1`
**Base:** `ee2b46d3b` (4 landed v1.1 commits: 7971799f4 feat / ad23f0c2f test /
2666dccc5 docs / 5e22bc61e fix — partially superseded by this spec, edited FORWARD,
no history rewrite).
**Binding clarification (verbatim):** *"sonic coder still has explore but read file/src,
not using rag tool."* — the sonic-coder KEEPS source exploration (read/grep/glob/bash
inspection) as a CORE, EXPECTED part of module work; the ONLY exclusion is the RAG
`explore()` tool call (knowledge category).

---

## 1. Combined architecture — 5-agent inventory

| Agent | Layer | llm_model | Role (one line) |
|---|---|---|---|
| `sonic-leader` | 1 intake/orchestration | `agentic` | Intake, blast-radius gate, standard-vs-fast routing, ONE-WAVE verification, verdict report, write-once-at-end knowledge moment |
| `sonic-developer` | 2 planning/integration (NEW) | `agentic` | ONE bounded `explore()` at plan-open, module split, parallel coder dispatch, dependency-order merge, HANDOFF-v2 authorship |
| `sonic-coder` | 3 execution leaf | `coding` | ONE-SHOT per module: source-explore → code → quick-test → fix → commit → compact module report |
| `sonic-reviewer` | 4 verification leaf | `agentic` | INTEGRATION-level review: HANDOFF-v2 + full merged diff (`base..merged`) |
| `sonic-tester` | 4 verification leaf | `coding` | INTEGRATION-level scoped testing: HANDOFF-v2 + merged tree, blast-radius-scoped packs |

### 1.1 Topology

```text
                       Ari (front door)
                          │ commission
                          ▼
                  ┌───────────────┐
                  │ sonic-leader  │  intake: blast gate → effort estimate → route
                  └───────┬───────┘
            STANDARD PATH │            │ FAST PATH (D-E gate)
            (default)     ▼            ▼ (genuinely small only)
              ┌──────────────────┐   ┌──────────────┐
              │ sonic-developer  │   │ sonic-coder  │  single module, single coder
              │ ONE explore() at │   └──────┬───────┘
              │ plan-open (D-G)  │          │ one-shot: code → test → commit
              └───────┬──────────┘          ▼
     PARALLEL module  │ module briefs     module report (no HANDOFF.md)
     ┌────────┬───────┴──────┬────────┐    │
     ▼        ▼              ▼        ▼    │
 sonic-coder sonic-coder sonic-coder  ... │
 (wt-A)     (wt-B)      (wt-C)            │
   │commit    │commit      │commit         │
   └──────────┴─────┬──────┴───────────────┘
                    ▼ module reports wake the developer
          developer merges branches IN DEPENDENCY ORDER (D-MERGE)
          into the feature branch → authors HANDOFF-v2 → reports up
                    │
                    ▼
        sonic-leader verifies HANDOFF-v2 → ONE-WAVE:
        sonic-reviewer + sonic-tester (INTEGRATION level, base..merged)
                    │
                    ▼
        verdicts PASS → leader reports (write-once-at-end knowledge moment)
```

### 1.2 Team membership (D-H, frozen)

- `sonic-leader` `team_members = ["sonic-developer", "sonic-coder", "sonic-reviewer", "sonic-tester"]` — EXACTLY four sonic members. `giter` is REMOVED from the sonic team: the in-loop commit/merge choreography is now internal (coder commits its module branch; developer merges in dependency order per D-MERGE). Release-lane git operations (merge-to-latest, staging, worktree cleanup) remain OUTSIDE the sonic loop, owned by the steady git lanes as before.
- `sonic-developer` `team_members = ["sonic-coder"]`
- `sonic-coder` / `sonic-reviewer` / `sonic-tester` `team_members = []` (leaves)

### 1.3 Knowledge posture (combined ruling)

| Agent | `explore()` | `experience()` |
|---|---|---|
| `sonic-leader` | OPTIONAL intake explore — carries the gap on the FAST path (D-C) | write-once-at-end, at most ONE, only on genuine learning (retained v1.1) |
| `sonic-developer` | MANDATORY, exactly ONE per commission, at plan-open, BEFORE writing the plan/briefs (D-G) | NEVER — the leader owns the single knowledge write |
| `sonic-coder` | NEVER (RAG excluded per the clarification) | NEVER |
| `sonic-reviewer` / `sonic-tester` | NEVER | NEVER |

**Standard/fast split (D-C, stated explicitly):** on the STANDARD path sonic-developer
carries THE explore of the commission; on the FAST path the developer layer is skipped,
the leader is the only knowledge-capable agent in the loop, and its optional intake
explore covers the gap. The v1.1 leader knowledge language stands IN FULL.

**Coder RAG exclusion (narrow wording — the clarification):** the coder loses ONLY the
RAG `explore()` call. Source exploration with file tools is expected and unbounded
within module scope. No prompt wording may discourage source exploration.

---

## 2. sonic-developer — the new layer

### 2.1 Tools (D-A, frozen; `nest-report` LAST, `pok` immediately before)

```json
"tools": {
  "allow": ["instance", "knowledge", "bash", "proc", "filesystem", "todo",
            "shared_meta_kv", "time", "self", "help", "context", "pok", "nest-report"],
  "deny": ["edit_file", "write_file"]
}
```

Rationale (state in prompts, pin in pack): `instance` = parallel module dispatch;
`knowledge` = the single bounded explore (NEVER `experience` — prompt-pinned AND
pack-static-pinned); `bash` + `proc` = worktree/merge machinery incl. long merges;
`filesystem` = read/integration reading with write-tools denied; `todo` = module-report
fan-in tracking; `shared_meta_kv` = `wt.active.*` / `wt.claim.*` census rows;
`time`/`self`/`help`/`context`/`pok` = house-keeping parity with siblings;
`nest-report` = anomaly duty.

`edit_file`/`write_file` DENIED: source authorship belongs to coders in their module
worktrees. The developer reads, merges, integrates — never authors source. Its ONE
file write is `HANDOFF.md` (§4), authored through a single bash heredoc at the task
worktree root (the deny makes the write-tools path impossible by construction).

### 2.2 llm_model (D-B): `agentic`

v1.0 split leader/reviewer=`agentic` (judgment) vs coder/tester=`coding` (execution).
The developer PLANS, SPLITS, ORDERS MERGES, INTEGRATES — the judgment class. Execution
stays on coders (`coding`).

### 2.3 Workflow shape

`plan-open explore → module split → briefs → PARALLEL dispatch → integrate (merge
order) → HANDOFF-v2 authorship → report up`. Exactly ONE `explore()` per commission at
plan-open (D-G); briefs relay its findings to coders; NO further knowledge calls by the
developer; NEVER `experience()`.

### 2.4 Prompt artifacts (4 files + meta.json — same set as siblings)

`meta.json` · `rule.md` (duty block LAST) · `soul.md` · `tools_note.md` · `workflow.md`.

---

## 3. sonic-coder rework — one-shot per module

### 3.1 What changes (edit-forward)

| v1.1 (landed) | v1.2 (this spec) |
|---|---|
| `explore()` RAG call MANDATORY at open | REMOVED — RAG excluded; source exploration EXPECTED (clarification) |
| writes `HANDOFF.md` (7-section author) | REMOVED — returns a COMPACT MODULE REPORT instead (authorship moved UP to the developer) |
| `tools.allow` carries `"knowledge"` | REMOVED — final tree: coder has NO knowledge category |
| never commits | COMMITS its module branch (pathspec-limited, in its own worktree) |

### 3.2 One-shot module flow (frozen order)

`module brief → source exploration → change → quick-test → fix (within budget) →
commit → compact module report → END TURN`.

### 3.3 FROZEN [sonic-coder-discipline-v2] — the two pinned sentences

Both shapes are pinned char-for-char in the coder's `workflow.md` AND `rule.md`
(pack A13; counter-mutations required):

1. **Source-exploration-expected (positive shape):**
   `Source exploration is EXPECTED: it is a core part of module work, not ceremony, and it is unbounded within your module scope.`
2. **RAG-excluded (exclusion shape):**
   `The ONE exclusion is the RAG tool: no `explore()` call — the developer's plan carries the stored experience; explore the SOURCE freely with your file tools.`

### 3.4 FROZEN [sonic-module-report-v2] — compact module report (message, not file)

```text
MODULE-REPORT: <module-slug>
branch: <branch-name> @ <tip-sha>
files: <exact paths touched, comma-separated>
changed: <1-3 lines, what the module change does>
self-test: <what ran → pass/fail, honest>
untested: <list, or none>
risks: <list, or none>
raises: <bash-raises consumed, or none>
blast: <no predicate match | ESCALATED: <path> matches <glob>>
```

The developer aggregates module reports into HANDOFF-v2 sections; reviewer/tester do
NOT consume module reports (§6).

### 3.5 Blast-radius predicate — retained, unchanged

The FROZEN `[sonic-blast-radius-v1]` 14-glob block stays byte-identical and now lives
in THREE workflow files: `sonic-leader`, `sonic-coder`, AND `sonic-developer` (the
developer escalates blast-path work back to the leader at plan time — it can also
refuse a module brief that would touch a blast path).

---

## 4. HANDOFF-v2 contract (D-D mechanics unchanged; authorship moves UP)

```text
handoff_path = <task-worktree>/HANDOFF.md   # ABSOLUTE path in EVERY dispatch context
AUTHOR:      sonic-developer (integration-level author)
CONSUMERS:   sonic-reviewer + sonic-tester at INTEGRATION level (base..merged)
```

### 4.1 FROZEN [sonic-handoff-v2] — the seven sections (exact Title-Case, in order)

```text
## Files touched across modules
## What changed
## What is UNTESTED
## Known risks
## How to verify
## Bash-raises used
## Blast-radius self-assessment vs predicate
```

Section semantics for an INTEGRATION-level author (redefined from v1's coder author):

1. `## Files touched across modules` — the UNION of every module report's file list,
   plus merge machinery files if any; per-module grouping preserved inside the section.
2. `## What changed` — the integrated behavior change across modules, not per-module
   narration (the module reports hold that detail).
3. `## What is UNTESTED` — aggregation of every module's `untested:` PLUS whatever the
   merge itself left unexercised (integration seams are the developer's honesty duty).
4. `## Known risks` — module risks PLUS merge/integration risks (conflict resolutions,
   ordering hazards).
5. `## How to verify` — integration-level reproduction: the commands a verifier runs on
   the MERGED tree.
6. `## Bash-raises used` — the developer's OWN bash-raises consumed by the merge
   machinery (long merges, big syncs), in the FROZEN `[sonic-bash-rule-v1]` shape:
   `bash rule: default <=600 s per command; a longer command requires an explicit raise from sonic-leader; hard max 1800 s (the tool-layer cap).`
7. `## Blast-radius self-assessment vs predicate` — the integration-level assessment:
   whether ANY merged path matches FROZEN `[sonic-blast-radius-v1]`, and which glob.

Each section is present even when empty (write `none`).

---

## 5. PARALLEL MERGE STRATEGY [sonic-merge-strategy-v1] (D-MERGE — the central design problem)

**Problem:** N sonic-coders on ONE repo in ONE commission → conflicting working trees,
interleaved commits, unattributable breakage.

**DECIDED shape:** one worktree + one branch PER MODULE; developer-driven merge in
dependency order.

1. **Isolation:** every module brief assigns a dedicated sibling worktree
   (`<repo>-wt-<slug>`) and a module branch, cut from the feature branch base sha.
   Coder file sets are DISJOINT by construction (the developer's split duty) → merges
   are mostly clean.
2. **House machinery, deliberately reused:** `<repo>-wt-<slug>` sibling worktrees;
   `wt.active.*` / `wt.claim.*` KV census rows (the DEVELOPER writes the census rows
   for module worktrees via `shared_meta_kv`; the coder refreshes its claim at phase
   boundaries); pathspec-limited commits (`git add` explicit paths only, never
   `git add -A`); `merge --no-ff` for every module merge (merge IS the commit point);
   NO `git stash` anywhere in the lane; contended-merge side-car-rescue pattern if a
   module worktree ever carries foreign WIP (rescue to
   `<path>.<commission>-rescue-<date>`, never drop).
3. **Branch hygiene:** module branches are cut with an explicit base sha (`git
   worktree add -b <branch> <path> <base-sha>` — no remote-tracking start point, so no
   auto-upstream); module branches are NEVER pushed.
4. **Merge order (D-F slot):** the developer assigns each module a merge-order slot in
   its brief (dependency order: foundational modules first). The developer merges in
   slot order, each as `merge --no-ff`, and runs the tree's relevant quick checks
   between merges.
5. **Conflict policy:** a merge conflict BOUNCES to the OWNING coder — the developer
   `send_message`s the owning sonic-coder with the conflict evidence (paths, both
   sides); the coder resolves IN ITS OWN module branch (new commit) and reports; the
   developer retries the merge. The developer NEVER hand-resolves source conflicts
   (it authors no source). Disjoint file sets mean a conflict is itself evidence of
   TWO owners: bounce to BOTH owning coders with the conflict evidence, or
   re-partition the overlapping files and re-brief.
6. **Failure:** if a coder turn dies mid-module, its worktree stays isolated — the
   developer merges the surviving modules, records the failed module in HANDOFF-v2
   `## What is UNTESTED` / `## Known risks`, and reports the gap up. No re-dispatch
   loop inside the developer (the leader owns any re-dispatch decision).

---

## 6. INTEGRATION-LEVEL verification (reviewer + tester rework)

Both verification lanes operate at INTEGRATION level. They consume:

- the developer's `HANDOFF.md` (HANDOFF-v2, absolute path in dispatch context), and
- the FULL MERGED DIFF `git diff <base>..HEAD` on the feature branch.

They do NOT consume per-module reports (those are the developer's aggregation input).

- **sonic-reviewer:** read order = HANDOFF-v2 FIRST → full merged diff → verify each
  `## How to verify` claim against the merged code → blast check over the merged file
  set (any match ⇒ `REVIEW: NEEDS-REVISION` with
  `blast-radius: <path> matches <glob>`). Verdict format unchanged
  (`REVIEW: PASS` / `REVIEW: NEEDS-REVISION`). Bash stays read-only (prompt-enforced
  residual; write tools mechanically denied).
- **sonic-tester:** derive blast radius from `## Files touched across modules` + the
  merged diff; run blast-radius-scoped packs on the MERGED tree (scoped-test doctrine
  unchanged, timeout-wrapped, QUARANTINE-aware). Verdict `TEST: PASS` / `TEST: FAIL`;
  RESULTS file only when load-bearing (path convention unchanged).
- **Write-surface residuals, BOTH disclosed:** the reviewer's residual is its bash
  (read-only by prompt; write tools mechanically denied). The tester's counterpart is
  broader and bounded ONLY by prompt: it holds filesystem write (the RESULTS file and
  `/tmp`) AND a git-capable bash. Its git surface is read-only inspection plus the
  RESULTS write its rules bound — no merge, no push, no tag, no `git stash`, and no
  commits outside its role (the sonic-coders own module-branch commits).

---

## 7. FAST PATH (D-E)

```text
FAST-PATH GATE: single module, single coder, no new agent dir, no cross-module risk —
anything larger goes through sonic-developer.
```

- **Shape:** leader → single sonic-coder DIRECT (skipping the developer layer). The
  leader composes a module-style brief (same labeled fields as §8 minus the merge-order
  slot, which is trivially 1) and dispatches it.
- **Reporting:** the coder returns the SAME compact module report shape (to the
  leader).
- **Verification:** reviewer/tester consume `git diff <base>..<module branch tip>` +
  that module report. NO `HANDOFF.md` on the fast path (no cross-module integration
  surface to document; HANDOFF-v2 authorship presupposes ≥2 modules or a developer
  planning turn).
- **Commit/merge:** the coder commits its module branch; the branch tip IS the merged
  state for a single-module commission (the leader verifies the tip read-only).
- **Knowledge (D-C):** the leader's OPTIONAL intake explore covers the gap left by the
  skipped developer explore; its write-once-at-end moment is unchanged.

---

## 8. MODULE BRIEF [sonic-module-brief-v2] (D-F — developer → coder, frozen labeled fields)

```text
SONIC-MODULE-BRIEF — <module-slug>
TASK: <one-paragraph statement of the module's change>
FILES: <exact disjoint file list>
FENCES: <blast-radius reminders, style fences, scope limits>
WORKTREE: <repo>-wt-<slug> (absolute path), branch <branch-name>, base <base-sha>
MERGE ORDER: <slot> — merge after <modules>, before <modules>
REPORT: reply to sonic-developer with the FROZEN [sonic-module-report-v2] shape
```

The brief also relays the developer-explore findings the module needs (the coder
cannot query the knowledge base — §1.3).

---

## 9. Pack pin plan (lockstep contract for `test/packs/sonic_agent_defs_checks.py`)

- `SONIC_AGENTS` → five agents (leader, developer, coder, reviewer, tester).
- `FROZEN_TEAMS` per §1.2. `FROZEN_TOOLS`: developer per §2.1; coder allow WITHOUT
  `knowledge`; reviewer/tester unchanged.
- `EXPECTED_EFFECTIVE`: leader = 4 team + `explorer` + `kb-writer`; developer =
  `sonic-coder` + `explorer` + `kb-writer`; coder/reviewer/tester = empty.
- `EXPECTED_LLM_MODEL` + developer `agentic`.
- Baseline: 197 → **201** rows (4 new sonic-developer `.md`); sonic baseline paths
  16 → **20**. meta.json is NOT baseline-tracked.
- HANDOFF pins move to the DEVELOPER workflow (7 v2 headers); `HANDOFF_PATH` literal
  three-way = developer / reviewer / tester. Coder workflow pins the
  `[sonic-module-report-v2]` block INSTEAD of handoff headers.
- Blast block asserted in THREE workflows (leader, coder, developer), zero-diff.
- NEW static pins (positive-shape + counter-mutation each):
  - **A12** sonic-developer artifacts prohibit `experience()` (three pinned boundary
    sentences: rule.md / workflow.md / tools_note.md).
  - **A13** sonic-coder artifacts assert source-exploration-expected + RAG-excluded
    (both §3.3 shapes in rule.md AND workflow.md; tools_note no-knowledge marker).
  - **A14** sonic-leader artifacts pin the FROZEN fast-path gate line (combined spec
    §7/D-E shape) in BOTH rule.md AND workflow.md.
- Mutation matrix: every new pin carries a counter-mutation leg that REDS it
  (`mutation-01-guards-need-counter-mutation`); the v1.1 legs that assert the coder
  HAS knowledge are replaced (coder-add-knowledge is now the counter-mutation).
- Sibling lockstep: `tests/unit/test_anomaly_report_duty_prompts.py` 50/48 → **51/49**;
  `test_anomaly_report_grant_pins.py` 48/50 → **49/51**; `test_pok_grant_pins.py`
  49/50 → **50/51**; `tests/unit/agents/test_aegis_security_family.py`
  `EXPECTED_BASELINE_ROWS` 197 → **201**.

---

## 10. Dry-run harness shape (parallel V-criteria — detail in phase3 spec)

The dry-run re-proof judges the PARALLEL architecture: developer exactly ONE RAG
explore in its first working turn; ≥2 sonic-coders RUNNING CONCURRENTLY (temporal
overlap asserted from instance-evidence timestamps); integration merge on the feature
branch; reviewer/tester at integration level; leader exactly-one `experience()` at
completion with the scenario DESIGNED so a learning occurs (silent zero = FAIL); coder
transcripts ZERO RAG explore calls AND ≥1 file-exploration calls (read/grep/glob) —
BOTH required; reviewer/tester zero knowledge. All evidence via
`GET /api/instances/{id}/messages` (flat `tool_calls[].name`) with per-message
`created_at` timing attribution — a verdict without timestamps is incomplete evidence,
not a PASS. `fenced-launch.py` MANDATORY for any boot.

---

## 11. Supersession table (v1/v1.1 wording reconciled by this spec)

| Superseded wording (where it lived) | v1.2 replacement |
|---|---|
| coder explore-at-start RAG mandate (coder workflow step 1, rule.md write-discipline, tools_note knowledge row; v1.1 amendment §3) | §3.3 two pinned sentences; RAG excluded; source exploration expected |
| coder HANDOFF authorship (coder workflow step 4; A6 headers in coder workflow) | §4 authorship = sonic-developer; coder returns §3.4 module report |
| coder `"knowledge"` in `tools.allow` (v1.1 grant) | REMOVED — coder zero-knowledge (§1.3) |
| leader ONE-WAVE after coder handoff (leader rule 2) | §1.1/§7: STANDARD (developer first) vs FAST path routing |
| leader pre-approved giter commit dispatch (leader rule 5, workflow §5) | §1.2: commit choreography internal (coder commits, developer merges); giter out of the sonic team |
| leader→coder direct dispatch as THE path | demoted to the FAST path behind the D-E gate |
| per-module reviewer/tester consumption (handoff = single-module artifact) | §6: INTEGRATION level — HANDOFF-v2 + `base..merged` |
| D8 flip "coder+leader knowledge" (decisions.md v1.1 amendment) | RESPECIFIED: developer + leader ONLY (coder/reviewer/tester zero-knowledge) |
| HANDOFF_HEADERS 7 v1 names in coder workflow | HANDOFF-v2 §4.1 in DEVELOPER workflow |
| baseline 197 / sonic rows 16 | **201 / 20** |
| dry-run V9–V11 v1.1 presence assertions | §10 parallel V-criteria (phase3 spec carries the detail) |

RETAINED UNCHANGED: leader write-once-at-end + optional intake explore + soul.md item-5
fix (5e22bc61e); anomaly duty blocks on all FIVE sonic agents (`nest-report` in every
tools.allow, LAST; `pok` immediately before); blast predicate 14 globs byte-identical;
`HANDOFF_PATH` literal; escalation line; Ari note + seal pins; kb-writer cheap-lane
verdict (report-only — shared `kb-writer` untouched).

---

## Amendment — v0.30.2 roster & support-parity (2026-11-13)

**Status:** POINTER-STYLE SUPERSESSION NOTES ONLY — this section amends the combined
spec above without rewriting it (history stands). Authoritative decision rows:
`decisions.md` §"v0.30.2 roster & support-parity amendment" (R16–R20). Lane:
`feature/sonic-team-v0302`, base `c1d81593e`, Emperor ruling 2026-11-13.

What changed vs the body of this spec (the body's wording stands as the v0.30.1 record):

- **§1.2 Team membership (superseded in part by R16/R20):** `sonic-leader`
  `team_members = ["sonic-developer", "sonic-coder", "sonic-reviewer", "sonic-tester",
  "giter", "devops"]`; `sonic-developer` `team_members = ["sonic-coder", "giter"]`.
  Giter's removal (D-H) is PARTIALLY superseded — giter returns as ESCAPE HATCH
  (workflow/worktree expertise); the internal commit choreography (§5 D-MERGE) and the
  release-lane-git-outside-the-loop rule stand unchanged. Devops on the leader is
  "rarely invoked by design — infra/deploy escape hatch" (R17).
- **§2.1 + §9 tools (superseded in part by R18):** `image` added to the allow list of
  sonic-leader / sonic-developer / sonic-coder / sonic-reviewer (inserted immediately
  before `pok` — grant-order invariant preserved), with `image_save` appended to each
  deny list; sonic-coder gains its first `deny` key `["image_save"]`. Effective result
  = the read trio `{explain_image, image_get, image_list}` (critic pattern);
  `image-reader` joins each grantee's effective spawn set via
  `TOOL_REQUIRED_AGENTS["image"]` — pinned in EXPECTED_EFFECTIVE, not hidden.
  `compare_images`/`design`, `generate_image`, `generate_chart`/`charter`,
  `sketcher`/`critic`, `wanderer`: OMITTED (R19 — includes sonic-coder's other
  steady-coder gaps: snapshot/instance/service/midflight, not directed → no change).
- **§9 pack pins (lockstep):** FROZEN_TOOLS + EXPECTED_EFFECTIVE updated for the four
  touched agents (sonic-tester unchanged); counter-mutation legs added for EVERY new
  grant (giter/devops spawn-set membership, image allow per agent, image_save deny per
  agent, image-reader effective-set membership, leader-add-design guard).
- **Baseline:** regenerated at 201 rows UNCHANGED (no new `.md` files; shas move for
  the touched sonic artifacts). Duty/grant/pok/aegis sibling pin counts unchanged
  (51/49, 49/51, 50/51, 201) — no new agents.
- **Version:** content-only delta stage v0.30.2; zero migrations; no Ari edits (seal +
  terminology pins untouched); sonic-tester untouched.
