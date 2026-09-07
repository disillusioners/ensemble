# Phase 3: Tester + Tidier Pointers + Verification Sweep

## Objective

Add the final two one-line pointers (`tester` + `tidier`) so all five relevant agents carry the worktree-awareness contract at the awareness level, and then run the **full cross-file compliance sweep** that catches drift, byte-budget overruns, system-internals leakage, canonical-home violations, and cross-reference breakage before any merge to `latest`.

**Authority:** revised 2026-09-06 per `architecture-recommendation.md` and the user answers (U1 ~1650B total cap; U3 Phase 4 stale-worktree sweep; U4 KV daemon defects as SEPARATE follow-up). Verification checks updated: the "30-min heartbeat literal" check becomes the **10-min/15-min dual-clock literal**; the duplication sweep adds **reconciliation/pre-check phrases**; obsolete `action=` schema is purged from all sweep expectations.

**Inherits from:** Phase 1 ("Giter Canonical Home"); Phase 2 ("Leader + Developer Awareness"). Phase 3's pointer tasks are independent of Phase 2; the verification sweep is the cross-phase gate that catches issues from all three phases.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add a one-sentence pointer in `agents/tester/workflow.md` (target insertion: append to or embed in the existing "Dispatch Pattern (infrastructure task, no skill)" block at line 70-81, in the part that mentions `git diff --name-only`). Point to `giter/workflow.md` → Worktree Mode for the worktree regression-proof convention. **Add a one-line `.env` non-collision reminder** for tester live-smoke on the alt port (architect §5: tester live-smoke launches uvicorn on an alt port directly; never launch `dev.sh` from inside a worktree). | Phase 1 | Pointer present in dispatch section; one-line reference to "Worktree Mode" section-name; no schema/lifecycle restatement; `.env` reminder present (one line) |
| 2 | Add a one-line pointer in `agents/tidier/workflow.md` (target insertion: append to the existing "Investigate" block under `### 3. Investigate` at line 29-38). Reading: "If a worktree was used for the feature, review inside `../<repo>-wt-<slug>/`, not the main checkout." **Add a one-line `.env` caution** for tidier verification runs that might spawn a daemon. | Phase 1 | Pointer present in Investigate; one-line reference to sibling-dir naming pattern (no schema/lifecycle restatement); `.env` caution present (one line) |
| 3 | **Cross-file canonical-home sweep.** Verify that the WORKTREE-AWARE contract appears in prose only inside `giter/workflow.md → Worktree Mode`. Read each of the five edited agents and grep for any of these substrings that would indicate a duplicated prose block: `wt.claim.<slug>` schema body, `wt.active.<branch>.<task-id>` schema body (per-task, NOT branch-keyed), the **two clocks** (10-min claim heartbeat / 15-min census TTL, NOT 30-min), the **reconciliation rule** (every gate entry, `<repo>-wt-*` family only), the **pre-check-before-add** rule, the **conditional `-b`** rule, the **corrected `.env` trap** ("never launch `dev.sh` from inside a worktree"), the **4 traps** (NOT 3), the lifecycle phrases "leader pre-writes" / "merge + cleanup" / "AFTER-gate", the **MANDATORY non-empty `context={"wt_path": …}`** hand-off wording, the **defense-in-depth explicit-KV-read** backstop. Any hit outside `giter/workflow.md` is a duplication violation; replace with a section-name reference. **EXEMPT from this sweep (sanctioned per D5; deletes the prior self-condemning-mechanical-run hazard):** the one-line `.env` non-collision cautions in `agents/tester/workflow.md` (122B literal, phase3 File-1) and `agents/tidier/workflow.md` (122B literal, phase3 File-2) — they are the D5-ratified agent-POV reminders, NOT duplications of giter's Worktree Mode prose. A hit for "never launch dev.sh inside a worktree" inside those two sanctioned 122B lines is NOT a violation; the sweep must skip them. | Tasks 1, 2 | Verification grep results: each cited phrase either (a) appears ONLY in `giter/workflow.md` OR (b) appears in a different agent ONLY inside a sentence that says "(see) giter/workflow.md → Worktree Mode" — no literal duplicates; tester/tidier sanctioned 122B `.env` cautions explicitly exempt (NOT flagged as duplications) |
| 4 | **Byte-budget enforcement sweep (C3-i, byte-true).** Per file run `git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c` (ADDED-line byte count, not line counts; the 1-byte-per-line `+` prefix overhead is accepted and applied identically everywhere). Per-agent caps (sub-caps per phase1/phase2 tables): giter ≤ 850 (572/82/196), leader ≤ 320 (175/145), developer ≤ 220 (95/125), tester ≤ 130, tidier ≤ 130 — **total ≤ 1650 (U1)**. If over, identify the offending file and shrink toward its must-fit literal | Phases 1 + 2 + Tasks 1, 2 | Sum ≤ 1650 AND each per-agent cap respected; violations flagged in the verification summary and shrunk before merge |
| 5 | **System-internals compliance sweep.** Run `grep -rnE 'meta\.json\|tools\.allow\|daemon/\|shared_context_metadata\|innate_skills\|get_tree_root_id\|seed_all\|agent_id=\|default_agent_versions\|skill-set\.yaml' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` against the **post-edit** files. Each hit must be either (a) pre-existing on the unchanged baseline (excluded) OR (b) inside a fenced code block quoting daemon output (allowed) OR (c) flagged as a violation requiring rework. Use `git diff agents/.../*.md` to attribute each hit to this feature vs. baseline. **ALSO:** `grep -rnE 'action="set"|action="delete"' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` against the post-edit files — zero hits required (obsolete KV tool schema purged; the real surface is the `shared_meta_kv` tool: `set_kv` / `delete_keys` / `clear_all` + no-arg read — `get_all_as_dict` is the repository read path, never presented to agents as a tool per O7). | Phases 1 + 2 + Tasks 1, 2 | Net-new system-internals hits inside prose = 0; obsolete `action=` schema absent from new prose; if any positive, the editor reworks the offending line into agent-POV |
| 6 | **D3 substrate sweep.** Run `grep -rnE 'auto-surface as substrate\|see .*wt.* in \[SYSTEM CONTEXT\] automatically\|worktree daemons source main-repo\|source the main repo.*\.env\|worktree has none' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` against the post-edit files — zero hits required (the prior refuted/REVERSED claims must not appear in the new prose). | Phases 1 + 2 + Tasks 1, 2 | Zero hits; if any positive, the editor reworks the offending line |
| 7 | **Final report.** Produce a concise "Worktree-Aware Coordination prompts — Verification Summary" capturing the byte budget usage per agent (wc -c added hunks), the C4 content-stability result per `rule.md` (Must/Must-Not zero-diff; developer sanctioned-bullet-only), the cross-reference count (each pointer to "Worktree Mode"), and any out-of-scope items surfaced during the sweep. The report is the gating artifact for the merge to `latest`. | Tasks 3-6 | Report present at `.agents/shared/planning/worktree-aware-prompts/verification-summary.md` (created during this task) and includes: per-agent byte diff (wc -c added hunks; must respect per-agent sub-caps and the 1650B total per U1), C4 content-stability result per `rule.md` (zero-diff Must/Must-Not for giter/leader/tester/tidier; developer = sanctioned bullet only), pointer-resolve diff (each Phase 2/3 pointer's "Worktree Mode" target must resolve via `grep -nF 'Worktree Mode' agents/giter/workflow.md`), system-internals hit list (must be empty or annotated), D3 substrate sweep result (must be zero hits), and the MANDATORY `context=` + defense-in-depth lines traced to their leader/developer locations |

---

## Coupling

- **Tight with:** Phase 1 (the section-name label "Worktree Mode" is the single contract; Tasks 1 and 2 must point there). Same rename-cost warning as Phase 2.
- **Tight with:** Phase 2 (Task 3's sweep verifies Phase 2 prose doesn't duplicate Phase 1's content — it's a cross-phase check).
- **Independent:** Tasks 1 and 2 (the agent pointers) don't depend on Phase 2; only Task 3-7 verification depends on Phase 2 having landed.
- **Cross-phase gating role:** Task 3-7 are the **only** verification gate in this plan; they make the three-phase plan safe to land in any order on the integration branch.

---

## Risks

Phase-specific risks (the project-level risk register lives in `plan-overview.md`):

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P3-A | Implementing agent's verification grep produces false-negatives (the LESSONS file says "false negatives have happened before") | Medium | Task 3 / 5 verification specs REQUIRE explicit `read_file` re-read of one known anchor per claim; not blind `grep -rn`; one `read_file` per file per claim |
| P3-B | Tester's dispatch section is large enough that a misplaced pointer is buried | Low | Task 1 anchor specifies the insertion site (`git diff --name-only` block, lines 70-81) — verify by reading line 78 in the post-edit file |
| P3-C | Tidier Investigate is brief; the pointer risks overshooting the byte cap | Low | Task 2's draft is ~80 chars; **tidier cap is now ~130B (U1)**, includes the `.env` caution; Phase 3 verification counts bytes |
| P3-D | Sequencing drift — a phase lands to `latest` alone and ships a half-feature | Medium | **Sequencing is governed by plan-overview.md → Canonical sequencing (single source of truth):** Phases 1-3 land on ONE integration branch (Phase 1 first; Phases 2/3 in any order after Phase 1); Phase 3's verification sweep gates the merge; ONE merge commit to `latest` carries Phases 1-3; Phase 4 is a separate post-merge commit. While integrating, `git status` is clean except for `agents/<edited>/*.md` + `.agents/shared/planning/worktree-aware-prompts/` |
| P3-E | Content-stability assertion blind spots (e.g., a Cardinal-strength bullet added OUTSIDE the Must/Must-Not regions, or extra unsanctioned additions in developer's rule.md) | Low | The C4 assertion (snippet step 3) covers both: zero-diff on Must/Must-Not regions for giter/leader/tester/tidier, AND developer's rule.md diff must equal exactly the one sanctioned 95B bullet — any additional added line anywhere in a rule.md fails |
| P3-F | Tester pointer uses `"git worktree"` as a verb in prose, leaking the daemon's CLI tool name | Low | Writing-guide §1 calls this borderline (CLI tools agents invoke are OK to name; the issue is daemon-internal surfaces); `git worktree` is allowed; verify in Task 5 sweep |
| P3-G | Final summary file lives outside `agents/` — does it violate the "zero diff outside `agents/*/prompts/* + plans/`" success criterion? | Low | The summary file lives under `.agents/shared/planning/worktree-aware-prompts/` (the same planning directory the plan lives in); this is `plans/`, not `agents/` — explicit exception in plan-overview.md scope |
| P3-H | Verification misses the obsolete `action=` schema in the new prose | Low | Task 5 sweeps for `action="set"\|action="delete"`; zero hits required |
| P3-I | Verification misses the refuted/REVERSED `.env` trap in the new prose | Low | Task 6 sweeps for `auto-surface as substrate`, `worktree daemons source main-repo`, `source the main repo.*\.env`, `worktree has none`; zero hits required |
| P3-J | Phase 4 sweep task isn't linked from Phase 3's verification gating | Low | Phase 3's exit criterion (below) explicitly lists the Phase 4 status as a required field of the verification summary, so the operator can see whether the post-merge sweep is queued/landed |

---

## Edit Specs (file-level)

### File 1: `agents/tester/workflow.md`

**Target section:** Append a one-sentence pointer to the existing "Dispatch Pattern (infrastructure task, no skill)" block (current line 70-81), specifically after the closing parenthetical at line 81. The intent is for testers to look at giter's Worktree Mode when they need to enforce the worktree regression-proof convention. **Plus** a one-line `.env` non-collision reminder (the corrected rule per architect §5 — never launch `dev.sh` from inside a worktree; if a worktree daemon is needed, `set -a; source <main-repo>/.env; set +a` and bind a non-8079 port).

**Content shape:** one-sentence pointer appended to the dispatch block + one-line `.env` reminder.

**MUST-FIT LITERAL (`wc -c` = 122 bytes ≤ cap 130):**

```markdown
Worktree conventions: see giter/workflow.md -> Worktree Mode. Never launch dev.sh inside a worktree (hits prod defaults).
```

**Plan notes:** the `.env` reminder is agent-observable symptom only (S1 — no architect citations, no LESSONS paths in prompt text; the corrected-rule rationale lives in decisions.md D4). Tester's live-smoke daemon binds a non-8079 port directly (uvicorn), never `dev.sh` inside a worktree.

**Acceptance checks:**
- Section-name label "Worktree Mode" appears in tester/workflow.md exactly once.
- `.env` reminder present (corrected rule; NOT the prior "worktree daemons source main-repo `.env`" reversal).
- No schema/lifecycle restatement.
- Byte-true (C3-i): `git diff -U0 --no-color -- agents/tester/workflow.md | grep '^+' | grep -v '^+++' | wc -c` ≤ 130 (the literal above is 122B).

---

### File 2: `agents/tidier/workflow.md`

**Target section:** Append a one-sentence pointer inside the existing `### 3. Investigate` block (current line 29-38), at the end of the SMALL-scope subsection or the MEDIUM+ subsection (whichever the implementing agent chooses — both are acceptable). **Plus** a one-line `.env` caution for tidier verification runs that might spawn a daemon.

**Content shape:** one-sentence pointer + one-line `.env` caution.

**MUST-FIT LITERAL (`wc -c` = 122 bytes ≤ cap 130):**

```markdown
If a worktree was used, review inside ../<repo>-wt-<slug>/, not the main checkout. Never launch dev.sh inside a worktree.
```

**Plan notes:** sibling-dir naming kept (helps the reviewer locate the directory); `.env` caution in agent-observable form — the prior draft's in-prompt citation of "architect §5" and the REVERSED-rule warning are plan-note material only (S1).

**Acceptance checks:**
- Sibling-dir naming pattern referenced (helps the reviewer locate the right directory).
- `.env` caution present (corrected rule).
- No schema/lifecycle restatement.
- Byte-true (C3-i): `git diff -U0 --no-color -- agents/tidier/workflow.md | grep '^+' | grep -v '^+++' | wc -c` ≤ 130 (the literal above is 122B).

---

### File 3 (verification artifact): `.agents/shared/planning/worktree-aware-prompts/verification-summary.md`

**Target:** NEW FILE created during Task 7.

**Content shape:** structured summary table (the gating artifact for merge).

**Sections the implementing agent must populate:**

```markdown
# Verification Summary: Worktree-Aware Agent Coordination

Date: <YYYY-MM-DD>
Author: <implementing agent>
Status: PASS / FAIL

## Byte Budget (per-agent caps + 1650B total per U1)

| Agent   | File           | Added bytes (wc -c) | Sub-cap | Pass |
|---------|----------------|---------------------|---------|------|
| giter   | workflow.md    | NNN                 | 572     | Y/N  |
| giter   | rule.md        | NNN                 | 82      | Y/N  |
| giter   | tools_note.md  | NNN                 | 196     | Y/N  |
| leader  | workflow.md    | NNN                 | 175     | Y/N  |
| leader  | tools_note.md  | NNN                 | 145     | Y/N  |
| developer | rule.md      | NNN                 | 95      | Y/N  |
| developer | workflow.md  | NNN                 | 125     | Y/N  |
| tester  | workflow.md    | NNN                 | 130     | Y/N  |
| tidier  | workflow.md    | NNN                 | 130     | Y/N  |
| **TOTAL** | —            | NNN                 | 1650    | Y/N  |

(giter sub-caps sum to EXACTLY 850; leader 175+145 = 320; developer 95+125 = 220 — C3-ii. Byte source: wc -c on ADDED hunks, C3-i.)

## Must/Must-Not Region Stability (C4 content-stability assertion — no Cardinal-heading grep; flat rule lists have no `###` headers to count)

| Agent | Must/Must-Not regions vs HEAD | Sanctioned delta | Pass |
|-------|-------------------------------|------------------|------|
| giter | byte-identical (zero diff) | none (new guideline lives under `### Branch Management`) | Y/N |
| leader | byte-identical (zero diff) | none | Y/N |
| developer | delta = EXACTLY the one sanctioned 95B Must-Not bullet (phase2 File-3 literal), nothing else | sanctioned bullet | Y/N |
| tester | byte-identical (zero diff) | none | Y/N |
| tidier | byte-identical (zero diff) | none | Y/N |

## Cross-Reference Resolution

| Pointer site | Target label | Resolves? |
|--------------|--------------|-----------|
| `agents/leader/workflow.md` | n/a — no pointer by design (phase2 File-1 note: leader's single pointer lives in tools_note.md) | n/a |
| `agents/leader/tools_note.md` | Worktree Mode | Y/N |
| `agents/developer/workflow.md` | Worktree Mode | Y/N |
| `agents/tester/workflow.md` | Worktree Mode | Y/N |
| `agents/tidier/workflow.md` | (sibling-dir pointer; not section-name) | n/a |

## MANDATORY context= / Defense-in-Depth Trace (NEW)

The verification agent must read these specific lines and confirm:

| Location | Expected content (regex/phrase) | Found? |
|----------|--------------------------------|--------|
| `agents/leader/workflow.md` Git Flow extension | "non-empty `context={\"wt_path\"" | Y/N |
| `agents/developer/rule.md` Must-Not | worktree-commit prohibition ONLY; NO KV-read line (O6) | Y/N |
| `agents/developer/workflow.md` Auto-Commit prefix (CANONICAL backstop home, O6) | "no wt_path in context AND >=1 fresh wt.claim.* row -> read shared KV first" (C6 trigger) | Y/N |
| `giter/workflow.md` Worktree Mode | "Reconcile:" + "heartbeat" + "stale 10 min" + "census 15 min" + "dirty or HEAD <30 min" (adopt-vs-remove criterion, C5) | Y/N |
| `giter/tools_note.md` Worktrees | "pre-check" + "add -b" + "remove" + "prune NO-OP" (foreign-only) | Y/N |

## D3 Substrate Sweep (NEW)

`grep -rnE 'auto-surface as substrate|see .*wt.* in \[SYSTEM CONTEXT\] automatically|worktree daemons source main-repo|source the main repo.*\.env|worktree has none' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` must return **zero hits** (refuted/REVERSED claims must not appear in the new prose).

## Obsolete KV Tool Schema Sweep (NEW)

`grep -rnE 'action="set"|action="delete"' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` must return **zero hits** in the new prose (real tool surface is `set_kv` / `delete_keys` / `clear_all` + no-arg read; `get_all_as_dict` is the repository read path, never a tool name — O7).

## System-Internals Sweep

(zero new hits in prose required; see tasks 5 above for the grep
recipe — pre-existing hits excluded)

| File | New hits |
|------|----------|
| agents/<each>/... | 0 |

## Soul.md Untouched

| Agent | Before (lines) | After (lines) |
|-------|----------------|---------------|
| giter | 49 | 49 |
| leader | NN | NN |
| developer | NN | NN |
| tester | NN | NN |
| tidier | NN | NN |

## Phase 4 Status (NEW)

`/private/tmp` worktree registrations — **≤5 at run time** (the 5 names below are the EXPECTED starting set; Phase 4 re-enumerates via `git worktree list` at run time — an entry already unregistered or dir-missing is logged "already-resolved" and skipped):

| Path (expected starting set) | Before | After (Phase 4 target) | Phase 4 status |
|------|--------|------------------------|----------------|
| /private/tmp/adj-head | registered | removed or already-resolved | queued / landed / n/a |
| /private/tmp/hotfix-defer-gate-base | registered | removed or already-resolved | queued / landed / n/a |
| /private/tmp/m1-gate-base | registered | removed or already-resolved | queued / landed / n/a |
| /private/tmp/pcfg-base | registered | removed or already-resolved | queued / landed / n/a |
| /private/tmp/ens-autopromote-micro | registered | removed or already-resolved | queued / landed / n/a |

(Per U3, this is the Phase 4 sweep's job, sequenced AFTER the feature merge commit. Phase 3 lists the status; Phase 4 lands the sweep. Completion gate: all REMAINING registered entries proceed=Y.)

## Out-of-Scope Items Surfaced

(list any cross-cutting concerns found during the sweep that
deserve their own backlog entry — e.g., a stale worktree found at
`git worktree list` that should be pruned; a docs/ gap in the
writing-guide; etc.)
```

**Acceptance checks:**
- File exists at the specified path.
- All cells populated (no `TBD`).
- All Pass columns show `Y`.

---

## Verification Snippet

Reproducible sweep commands — for the editor's verification agent to run **after** all three phases have been integrated. Each command's exit behavior is described inline. **Note:** as called out in risk P3-A and the LESSONS file pattern, repo grep has returned false negatives before; this snippet is for sanity, NOT for evidence — every claim below needs explicit `read_file` re-read of the relevant file at the relevant line anchor.

```bash
# 1. Touch surface discipline: nothing outside agents/<edited>/ + plans/
git diff --stat -- 'agents/*/' '.agents/shared/planning/worktree-aware-prompts/'

# 2. Byte budget (C3-i, byte-true): wc -c on ADDED hunks per file (NOT line counts).
#    The 1-byte-per-line '+' prefix overhead is accepted and applied identically everywhere.
for f in agents/giter/workflow.md agents/giter/rule.md agents/giter/tools_note.md \
         agents/leader/workflow.md agents/leader/tools_note.md \
         agents/developer/rule.md agents/developer/workflow.md \
         agents/tester/workflow.md agents/tidier/workflow.md; do
  printf '%-40s %6d\n' "$f" "$(git diff -U0 --no-color -- "$f" | grep '^+' | grep -v '^+++' | wc -c)"
done
# Sub-caps (C3-ii, sums EXACT to per-agent caps): giter 572/82/196 (=850),
# leader 175/145 (=320), developer 95/125 (=220), tester 130, tidier 130. TOTAL <= 1650 (U1).

# 3. C4 content-stability: no new Cardinal-strength content in any rule.md.
#    (No Cardinal-heading grep — flat rule lists carry no '###' headers to count.)
extract_must() { awk '/^## /{m=($0~/^## Must/)} m' "$1"; }
for d in giter leader tester tidier; do
  f=agents/$d/rule.md
  diff <(git show HEAD:"$f" | extract_must /dev/stdin) <(extract_must "$f") >/dev/null \
    || echo "C4 FAIL: $f Must/Must-Not region changed"
done
# developer: the ONLY sanctioned delta is the single 95B Must-Not bullet (phase2 File-3 literal):
added=$(git diff -U0 -- agents/developer/rule.md | grep '^+' | grep -v '^+++' | sed 's/^+//')
[ "$added" = "- Assigned wt_path? cd into the worktree before any git op; never commit on the main checkout." ] \
  || echo "C4 FAIL: agents/developer/rule.md has unsanctioned additions"
# → zero C4 FAIL lines

# 4. System-internals sweep in prompt prose (any hit MUST be pre-existing)
grep -rnE 'meta\.json|tools\.allow|daemon/|shared_context_metadata|innate_skills|get_tree_root_id|seed_all|agent_id=' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/

# 5. Canonical-home: "Worktree Mode" section label is present in giter once
grep -nF 'Worktree Mode' agents/giter/workflow.md
# → must show at least one hit (the section heading)

# 6. Every pointer resolves
grep -nF 'Worktree Mode' agents/leader/tools_note.md agents/developer/workflow.md agents/tester/workflow.md
# → all hit counts match plan; for each, open the file and confirm the
#   sentence reads "(see) giter/workflow.md → Worktree Mode" not a copy-paste

# 7. Soul.md untouched
for d in giter leader developer tester tidier; do
  printf '%-12s %s\n' "$d" "$(wc -l < agents/$d/soul.md)"
done
# → each value must equal the pre-feature value (or `git diff agents/$d/soul.md`
#   returns empty)

# 8. Dual-clock literal check (O-D2.1 — replaces the prior 30-min heartbeat check).
#    The 572B workflow.md literal phrases the clocks as "stale 10 min" (claim
#    heartbeat) and "census 15 min" — "heartbeat" appears BEFORE "10 min", and
#    the 15-min clock is paired with "census" (no "TTL" word exists in the
#    literal). Each phrase must hit separately because the regex `10 min.*heartbeat`
#    would NOT match the literal's "heartbeat ... 10 min" ordering, and `15 min.*TTL`
#    would never match (no "TTL" string).
grep -nF 'heartbeat' agents/giter/workflow.md   # >=1 hit
grep -nF '10 min'   agents/giter/workflow.md   # >=1 hit
grep -nF '15 min'   agents/giter/workflow.md   # >=1 hit
grep -nF 'census'   agents/giter/workflow.md   # >=1 hit (confirms 15 min is the census clock)
# → all four present in giter's Worktree Mode (the dual-clock 10/15-min literal
#   is the O-D2.1-verified form).
# Negative check: the OLD "30 min heartbeat" phrase must be GONE.
#    The 572B literal has "HEAD <30 min -> adopt+heartbeat" (the adopt-vs-remove
#    age threshold) — `30 min.*heartbeat` would falsely match that, so we use a
#    literal-phrase check that requires adjacency (no `.*` between).
grep -nE '30 min heartbeat|30-min heartbeat|30min heartbeat' agents/giter/workflow.md
# → zero hits (the literal "HEAD <30 min" is followed by "-> adopt+heartbeat",
#   not "30 min heartbeat" as a continuous phrase)

# 9. Reconciliation + pre-check phrases (O-D2.2 companion).
#    The 572B workflow.md literal has "Reconcile:" (capitalized) — case-insensitive
#    match is required (default grep is case-sensitive; `-i` flips it).
#    "pre-check" / "add -b" / "remove" / "prune NO-OP" live in the 196B TOOLS_NOTE
#    literal, NOT in workflow.md, so we split the checks across the two giter files.
grep -niE 'Reconcile|reconciliation' agents/giter/workflow.md   # >=1 hit (the Reconcile line)
grep -nF  'pre-check'                agents/giter/tools_note.md  # >=1 hit (list --porcelain line)
grep -nF  'add -b'                agents/giter/tools_note.md  # >=1 hit (conditional -b rule)
grep -nF  'prune' agents/giter/tools_note.md  # >=1 hit (prune NO-OP / foreign-only)
# → at least one of each phrase present across the two giter files

# 10. D3 substrate sweep (refuted/REVERSED claims must be absent from new prose)
grep -rnE 'auto-surface as substrate|see .*wt.* in \[SYSTEM CONTEXT\] automatically|worktree daemons source main-repo|source the main repo.*\.env|worktree has none' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/
# → zero hits

# 11. Obsolete KV tool schema sweep (action=/key=/value= form must be absent)
grep -rnE 'action="set"|action="delete"' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/
# → zero hits
```

**Interpretation:**
- All seven exit behaviors must agree with the matching Success Criterion in `plan-overview.md`.
- **Sanity vs. evidence:** the snippet is the sanity check; the verification-summary.md is the evidence. Every line in the summary table must trace to either a wc -c added-hunk reading (C3-i method) or an explicit `read_file` re-read of the cited line.

---

## Exit Criterion

Phase 3 is complete when:
1. `tester/workflow.md` and `tidier/workflow.md` each carry the one-line pointer + the `.env` reminder/caution (Tasks 1 + 2).
2. The cross-file canonical-home sweep finds no duplicated prose blocks across the five agents (Task 3).
3. **The byte-budget enforcement sweep reports total added bytes ≤ 1650 AND each per-agent cap is respected** (Task 4 — U1; the prior 1330 cap is superseded).
4. The system-internals sweep reports zero new hits in prose (Task 5).
5. **The D3 substrate sweep reports zero hits** (Task 6 — refuted/REVERSED claims must be absent from new prose).
6. **The obsolete `action="set"/"delete"` schema sweep reports zero hits** in new prose (Task 5 second half).
7. The verification-summary.md file is created at the specified planning path with all `Pass` columns showing `Y` AND the "Phase 4 Status" table shows the ≤5 expected `/private/tmp` entries (5-name expected starting set) as "queued" (Phase 4 lands separately, post-merge; runtime re-enumeration + "already-resolved" logging per C1) (Task 7).
8. `git status` shows the only modified files are inside `agents/<five>/` and `.agents/shared/planning/worktree-aware-prompts/`. No `meta.json`, no `soul.md`, no daemon-side files.

The merge to `latest` is unblocked at this point. Phase 4 (giter one-time stale-worktree sweep) lands as a separate commit sequenced AFTER the Phase 1-3 merge.
