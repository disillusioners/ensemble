# Phase 4: Giter One-Time Stale-Worktree Cleanup Sweep (NEW)

## Objective

Remove the pre-existing `/private/tmp` worktree registrations — **≤5 at run time** — that are outside the `../<repo>-wt-*` family and therefore intentionally ignored by the in-flow reconciliation rule (D2 lifecycle, `decisions.md` D4 cleanup, architect file §4). **Runtime-resilient (C1):** the sweep re-enumerates `git worktree list` at run time; the 5 known names below are the EXPECTED STARTING SET, not a fixed truth — an entry that is already unregistered or dir-missing at run time is logged "already-resolved" and skipped, and the completion gate is "all REMAINING registered entries proceed=Y". Each remaining entry is `git worktree remove`'d individually after per-entry verification. **`git worktree prune` is a NO-OP here** (the expected entries' directories exist; prune only drops registrations whose directory is gone — architect file §4). Sequencing: **AFTER the feature merge commit lands** (Phase 1-3 must be in `latest` first; per U3).

**Expected starting set (≤5 at run time; re-enumerated — not a fixed truth):**
1. `/private/tmp/adj-head`
2. `/private/tmp/hotfix-defer-gate-base`
3. `/private/tmp/m1-gate-base`
4. `/private/tmp/pcfg-base`
5. `/private/tmp/ens-autopromote-micro`

**Authority:** per user answer U3 (in-scope as `phase4-plan.md`) + architect file §4 (5 entries verified; prior plan's "4 stale" count is wrong). Per user answer U4, the 3 latent KV daemon defects are a SEPARATE follow-up — NOT this phase.

**Why not in-flow reconciliation:** giter's reconciliation rule (D2 lifecycle) is scoped to the `../<repo>-wt-*` family. The 5 pre-existing entries are foreign registrations; including them in the rule would risk touching active work that happens to be outside the family. The architectural separation is intentional: the new feature's contract applies to its own worktrees; pre-existing foreign registrations are addressed by a one-time operator sweep with explicit per-entry verification.

**Inherits from:** Phase 1-3 must have merged to `latest` first (so the operator does not race the feature merge). Phase 4 is **independent** of Phases 1-3's content (it touches no agent prompt files); its only dependency is the merge ordering.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Re-enumerate at run time (C1)** via `git worktree list --porcelain` from the main checkout (parent of the sibling-dir family) — do NOT trust the 5-name expected set as fixed truth. The live enumerated set is the target set (≤5 expected). For each expected entry NOT present in the live enumeration (unregistered) or whose directory is missing (`test -d` fails): log "**already-resolved**" and CONTINUE (not an error). Verify each remaining path is a `/private/tmp/...` path that is NOT inside the `../<repo>-wt-*` family and NOT the main checkout. Read the post-merge feature state — Phases 1-3 must be in `latest` — to confirm this sweep is post-merge. | Phases 1-3 merged to `latest` | Live enumerated set captured for the verification log; expected-but-absent entries logged "already-resolved" and skipped; remaining set ⊆ expected set; the main checkout is excluded from the target set |
| 2 | **Per-entry verification (BEFORE any `remove`)** — for EACH of the ≤5 REMAINING registered entries (already-resolved entries are excluded), verify: (a) the path is registered with `git worktree list`; (b) the directory exists (`test -d <path>`); (c) the path is NOT the main checkout (`git rev-parse --show-toplevel` ≠ `<path>`); (d) the path is NOT inside the `../<repo>-wt-*` family (sibling-dir convention); (e) the path is NOT a parent of the main checkout (would indicate a confused symlink); (f) `cd <path> && git status` shows a CLEAN working tree (no uncommitted changes, no untracked work worth saving). **If dirty on (f), STOP and report** — do NOT remove; the operator decides whether to salvage, commit, or discard. | Task 1 | For each of the 5 entries, a per-entry verification row is logged: `path | registered=Y/N | dir_exists=Y/N | is_main=Y/N | in_repo_wt_family=Y/N | is_parent_of_main=Y/N | status_clean=Y/N | proceed=Y/N`. **Completion gate (C1): all REMAINING registered entries must show `proceed=Y`** before any `remove` is invoked; any `proceed=N` blocks the sweep and surfaces to the operator |
| 3 | **`git worktree remove` per entry** — for each REMAINING entry that passed Task 2's verification, run `git worktree remove <path>`. The primary verb is `remove` (graceful) per architect §4. Do NOT use `git worktree prune` — prune is a NO-OP here (dirs exist) and the prior plan's "prune fallback" framing was REVERSED by the architect's verification. | Task 2 | All REMAINING entries show "removed" in the per-entry log (already-resolved entries logged separately); no `remove` command fails. If any `remove` fails, the entry is logged with the failure output and the operator is prompted (do NOT auto-fall-back to `prune --force`; the entry must be diagnosed). The verification log preserves the exact `remove` invocation and the resulting `git worktree list` row for each entry. |
| 4 | **Re-list and confirm** — after the removes, run `git worktree list --porcelain` from the main checkout and confirm: (a) none of the ≤5 expected `/private/tmp` paths appear in the listing; (b) the main checkout is still present and unchanged; (c) no NEW foreign worktrees were added (sanity check that the remove operations did not corrupt the worktree registry). **Report** the final state: which entries were removed, which were logged "already-resolved", which (if any) were skipped due to dirty status, and the per-entry before/after diff. | Task 3 | Final `git worktree list` output captured; the 5 target entries are absent; the main checkout is unchanged; the report is preserved as a planning artifact (or appended to the feature's verification summary) |

---

## Coupling

- **Independent of:** Phases 1, 2, 3. Phase 4 touches no agent prompt files; it operates on the worktree registry only. Its only dependency is the merge ordering (the operator must not race the feature merge).
- **Loose with:** Phase 1's reconciliation rule (D2 lifecycle, `decisions.md` D4 cleanup) — the rule's scope is `../<repo>-wt-*` family; this phase's scope is the ≤5 pre-existing `/private/tmp` entries (5-name expected starting set, runtime re-enumerated). The two scopes are disjoint by design (architect file §4: "Scope: only the `<repo>-wt-*` family — never touch foreign registrations").
- **No coupling to:** Any agent's prompt (no `agents/<name>/*.md` edits in this phase). The sweep is purely a registry-level operation.

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P4-A | **Dirty working tree on a target entry** — the entry has uncommitted work worth saving; `git worktree remove` would discard it | High | Task 2's `git status` check is the gate; **if dirty, STOP and report** rather than remove. The verification log preserves the dirty state for the operator. No `remove --force` is used. |
| P4-B | **Wrong directory targeted** — a typo or path misread leads to removing the wrong worktree | High | Task 1's enumeration + Task 2's `is_main=Y/N` and `in_repo_wt_family=Y/N` checks are the gate. The ≤5 expected paths are listed in the Objective as the starting set and re-enumerated at run time (C1); the per-entry log requires explicit path-matching before any `remove`. |
| P4-C | **Entry is the main checkout** — `git rev-parse --show-toplevel` returns the target path; removing it would destroy the working tree | Critical | Task 2's `is_main=Y/N` check returns `proceed=N` for this case. The operator is prompted, not auto-skipped. |
| P4-D | **Entry is a parent of the main checkout** — confused symlink or mis-mounted path | High | Task 2's `is_parent_of_main=Y/N` check catches this; `proceed=N` and report. |
| P4-E | **`git worktree remove` fails** — file in use, permission denied, etc. | Medium | Task 3's per-entry failure log captures the output; the operator is prompted. No auto-fall-back to `prune --force`. The entry is left registered; the operator decides the next step. |
| P4-F | **A new foreign worktree was added between Task 1 and Task 4** — registry churn during the sweep | Low | Task 4's "no NEW foreign worktrees were added" sanity check catches unexpected churn; the operator is prompted. |
| P4-G | **Sweep runs BEFORE Phase 1-3 merge** — operator races the feature merge | Medium | Phase 4's "Inherits from" clause is explicit: Phases 1-3 must be in `latest` first. The verification log records the current `git rev-parse --short HEAD` of the main checkout and compares against the post-Phase-1-3 merge commit. |
| P4-H | **`git worktree prune` confusion** — implementing agent falls back to `prune` instead of `remove` | Low | Task 3's acceptance explicitly forbids the prune fallback for the in-scope entries (dirs exist; prune is a NO-OP). The verification log records which verb was used. |

---

## Exit Criterion

`git worktree list --porcelain` from the main checkout no longer shows any of the ≤5 expected `/private/tmp` paths; **every REMAINING registered entry showed `proceed=Y` before its remove** (C1 completion gate); entries already unregistered or dir-missing at run time were logged "already-resolved"; the main checkout is unchanged; the per-entry verification log is preserved (path, registered, dir_exists, is_main, in_repo_wt_family, is_parent_of_main, status_clean, proceed, remove-output) for each remaining entry; the report identifies any entry that was skipped (dirty or other gate failure) and surfaces it to the operator; no `meta.json`, no `soul.md`, no daemon-side files were touched; the only modified files are inside `.agents/shared/planning/worktree-aware-prompts/` (the verification log / report, if persisted there).

The feature is complete at this point. The merge to `latest` is unblocked at Phase 3's exit; Phase 4 lands as a separate post-merge commit (its prompt file independence makes it safe to land in its own commit, sequenced after the Phase 1-3 merge).
