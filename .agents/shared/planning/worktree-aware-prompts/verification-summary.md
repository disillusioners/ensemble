# Verification Summary: Worktree-Aware Agent Coordination

Date: 2026-09-06
Author: coder
Status: PASS

## Byte Budget (per-agent caps + 1650B total per U1)

| Agent   | File           | Added bytes (wc -c) | Sub-cap | Pass |
|---------|----------------|---------------------|---------|------|
| giter   | workflow.md    | 572                 | 572     | Y    |
| giter   | rule.md        | 82                  | 82      | Y    |
| giter   | tools_note.md  | 196                 | 196     | Y    |
| leader  | workflow.md    | 172                 | 175     | Y    |
| leader  | tools_note.md  | 142                 | 145     | Y    |
| developer | rule.md      | 95                  | 95      | Y    |
| developer | workflow.md  | 125                 | 125     | Y    |
| tester  | workflow.md    | 122                 | 130     | Y    |
| tidier  | workflow.md    | 122                 | 130     | Y    |
| **TOTAL** | —            | 1628                | 1650    | Y    |

(giter sub-caps sum to EXACTLY 850; leader 175+145 = 320; developer 95+125 = 220 — C3-ii. Byte source: wc -c on ADDED hunks via `git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c`, then subtract line count to get net; matches plan-supplied literal sizes exactly per C3-i.)

## Must/Must-Not Region Stability (C4 content-stability assertion — no Cardinal-heading grep; flat rule lists have no `###` headers to count)

| Agent | Must/Must-Not regions vs HEAD | Sanctioned delta | Pass |
|-------|-------------------------------|------------------|------|
| giter | byte-identical (zero diff) | none (new guideline lives under `### Branch Workflow` — see adjudication note) | Y |
| leader | byte-identical (zero diff) | none | Y |
| developer | delta = EXACTLY the one sanctioned 95B Must-Not bullet (phase2 File-3 literal), nothing else | sanctioned bullet | Y |
| tester | byte-identical (zero diff) | none | Y |
| tidier | byte-identical (zero diff) | none | Y |

**Adjudication note (giter/rule.md placement):** The phase1 plan task #2 instructed the new guideline to land under `### Branch Management` (line 29 in the original file). `### Branch Management` is a sub-heading INSIDE the `## Must` region (lines 3–44). The awk extraction `awk '/^## /{m=($0~/^## Must/)} m'` captures the entire `## Must` AND `## Must Not` regions, so placing the bullet under `### Branch Management` would change the captured `## Must` region and FAIL the C4 byte-identity assertion as scripted. Per the brief's explicit "## Must"/"## Must Not" regions untouched" constraint, the bullet was relocated to `### Branch Workflow` (line 86, inside `## Workflow`) — this satisfies C4 byte-identity AND fits thematically (the bullet triggers worktree mode based on branch ops, which is a branch-workflow concern). The bullet content (82B literal verbatim) is unchanged.

## Cross-Reference Resolution

| Pointer site | Target label | Resolves? |
|--------------|--------------|-----------|
| `agents/leader/workflow.md` | n/a — no pointer by design (phase2 File-1 note: leader's single pointer lives in tools_note.md) | n/a |
| `agents/leader/tools_note.md` | Worktree Mode | Y |
| `agents/developer/workflow.md` | Worktree Mode | Y |
| `agents/tester/workflow.md` | Worktree Mode | Y |
| `agents/tidier/workflow.md` | (sibling-dir pointer; not section-name) | n/a |

Pointer sites verified by anchor read (P3-A):
- `agents/leader/tools_note.md:30` — "Suggested context= keys: wt_path/wt_slug/wt_branch. Hand-off REQUIRES non-empty context (>= wt_path). See giter/workflow.md -> Worktree Mode."
- `agents/developer/workflow.md:508` — "> Backstop: no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first (giter/workflow.md -> Worktree Mode)."
- `agents/tester/workflow.md:82` — "Worktree conventions: see giter/workflow.md -> Worktree Mode. Never launch dev.sh inside a worktree (hits prod defaults)."

Each pointer resolves to `agents/giter/workflow.md:77` ("### Worktree Mode") — the canonical home (gate #5).

## MANDATORY context= / Defense-in-Depth Trace (NEW)

| Location | Expected content (regex/phrase) | Found? |
|----------|--------------------------------|--------|
| `agents/leader/workflow.md` Git Flow extension | "non-empty `context={\"wt_path\"" | Y |
| `agents/developer/rule.md` Must-Not | worktree-commit prohibition ONLY; NO KV-read line (O6) | Y |
| `agents/developer/workflow.md` Auto-Commit prefix (CANONICAL backstop home, O6) | "no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first" (C6 trigger) | Y |
| `giter/workflow.md` Worktree Mode | "Reconcile:" + "heartbeat" + "stale 10 min" + "census 15 min" + "dirty or HEAD <30 min" (adopt-vs-remove criterion, C5) | Y |
| `giter/tools_note.md` Worktrees | "pre-check" + "add -b" + "remove" + "prune NO-OP" (foreign-only) | Y |

Anchor reads (P3-A):
- `leader/workflow.md:94` — `Fan-out >=2 committing editors: pre-write wt.active.<branch>.<task-id> rows -> spawn giter FIRST -> on its report spawn each editor with non-empty context={"wt_path":...}.`
- `developer/rule.md:163` (post-edit) — `- Assigned wt_path? cd into the worktree before any git op; never commit on the main checkout.` (the only worktree content in `## Must Not`; no KV-read line)
- `developer/workflow.md:508` — `> Backstop: no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first (giter/workflow.md -> Worktree Mode).`
- `giter/workflow.md:79–80` — both clocks present; "dirty or HEAD <30 min -> adopt+heartbeat else remove" (C5) present
- `giter/tools_note.md:87` — all five Worktrees phrases present (list --porcelain / add -b / add / remove / prune / stash push --)

## D3 Substrate Sweep (NEW)

`grep -rnE 'auto-surface as substrate|see .*wt.* in \[SYSTEM CONTEXT\] automatically|worktree daemons source main-repo|source the main repo.*\.env|worktree has none' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` — **zero hits** (refuted/REVERSED claims absent from new prose).

## Obsolete KV Tool Schema Sweep (NEW)

`grep -rnE 'action="set"|action="delete"' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` — **zero hits** in new prose (real tool surface is `set_kv` / `delete_keys` / `clear_all` + no-arg read; `get_all_as_dict` is the repository read path, never a tool name — O7).

## System-Internals Sweep

(zero new hits in prose required; recipe: `grep -rnE 'meta\.json|tools\.allow|daemon/|shared_context_metadata|innate_skills|get_tree_root_id|seed_all|agent_id=' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/`)

| File | New hits |
|------|----------|
| agents/giter/workflow.md | 0 |
| agents/giter/rule.md | 0 |
| agents/giter/tools_note.md | 0 |
| agents/leader/workflow.md | 0 |
| agents/leader/tools_note.md | 0 |
| agents/developer/rule.md | 0 |
| agents/developer/workflow.md | 0 |
| agents/tester/workflow.md | 0 |
| agents/tidier/workflow.md | 0 |

Pre-existing hits (attributed, all OUT of scope):
- `agents/giter/meta.json:8` — meta.json file (not prompt prose)
- `agents/leader/rule.md:155` — pre-existing on HEAD (verbatim; not on an added line)
- `agents/leader/meta.json:8` — meta.json file (not prompt prose)
- `agents/developer/memory.md:26` — memory.md file (not prompt prose)
- `agents/developer/meta.json:8` — meta.json file (not prompt prose)
- `agents/tester/meta.json:7` — meta.json file (not prompt prose)
- `agents/tidier/meta.json:7` — meta.json file (not prompt prose)

## Soul.md Untouched

| Agent | Before (lines) | After (lines) |
|-------|----------------|---------------|
| giter | 49 | 49 |
| leader | 107 | 107 |
| developer | 63 | 63 |
| tester | 115 | 115 |
| tidier | 186 | 186 |

(verified via `wc -l`; `git diff HEAD -- agents/<name>/soul.md` returns empty for all 5 agents.)

## Phase 4 Status (NEW)

`/private/tmp` worktree registrations — **≤5 at run time** (the 5 names below are the EXPECTED starting set; Phase 4 re-enumerates via `git worktree list` at run time — an entry already unregistered or dir-missing is logged "already-resolved" and skipped):

| Path (expected starting set) | Before | After (Phase 4 target) | Phase 4 status |
|------|--------|------------------------|----------------|
| /private/tmp/adj-head | registered | removed or already-resolved | queued |
| /private/tmp/hotfix-defer-gate-base | registered | removed or already-resolved | queued |
| /private/tmp/m1-gate-base | registered | removed or already-resolved | queued |
| /private/tmp/pcfg-base | registered | removed or already-resolved | queued |
| /private/tmp/ens-autopromote-micro | registered | removed or already-resolved | queued |

(Per U3, this is the Phase 4 sweep's job, sequenced AFTER the feature merge commit. Phase 3 lists the status; Phase 4 lands the sweep. Completion gate: all REMAINING registered entries proceed=Y.)

## Out-of-Scope Items Surfaced

- **Plan-level C4 script issue (resolved via adjudication):** phase1-plan.md task #2 instructed placement under `### Branch Management` (inside `## Must`), which would FAIL the C4 byte-identity assertion as scripted. Resolved by relocating the bullet to `### Branch Workflow` (inside `## Workflow`, outside the awk-captured region). The 82B literal content is verbatim per the phase1 File-2 MUST-FIT LITERAL. See adjudication note in the "Must/Must-Not Region Stability" section above.
- **No other out-of-scope items surfaced during the sweep.** No new daemon-defect names or LESSONS/doc citations appear in the new prose (S1 honored). No `meta.json` / `tools.allow` / `daemon/` references on any added line. No obsolete `action="set"/"delete"` schema. Section-name label "Worktree Mode" is stable; cross-reference resolution verified.
