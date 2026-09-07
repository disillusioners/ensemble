# Plan Overview: Worktree-Aware Agent Coordination

Date: 2026-09-06
Author: planner[v2] via plan-creation worker
Status: **RATIFIED / FINAL** — ready for implementation. All 11 forks (O-D1.1 … O-D5.3) closed in `decisions.md`; architect verdict + 5 protocol corrections absorbed from `architecture-recommendation.md`; user answers U1–U4 recorded.
Companion artifacts (already on disk):
- `decisions.md` — design baseline (D0–D5, all RATIFIED/FINAL; closed-forks table; user answers U1–U4)
- `architecture-recommendation.md` — architect verdict, refutation evidence, fork adjudications
- `technical-analysis.md` — architecture, failure modes, integration points, coupling/risk map (revised)
- `phase1-plan.md` / `phase2-plan.md` / `phase3-plan.md` / `phase4-plan.md` (NEW) — implementation phases

---

## Objective

Concurrent edit-capable agents (developer, giter, leader-as-orchestrator) running on the same repo coordinate via separate git worktrees **using prompt-only changes** (zero daemon code, zero `meta.json` edits), so concurrent fan-outs no longer risk stepping on the shared checkout and losing work.

**Measurable single-sentence definition-of-done (revised per architect §2):** *When a leader spawns ≥ 2 editors that will commit, the leader pre-registers `wt.active.<branch>.<task-id>` rows and spawns giter FIRST; giter creates a sibling-dir worktree and writes `wt.claim.<slug>` AFTER the add succeeds; the leader then spawns each editor with a MANDATORY non-empty `context={"wt_path": …, "wt_slug": …, "wt_branch": …}` so the worktree path lands in `[SYSTEM CONTEXT: Task Context]` via durable enqueue; the developer `cd`s into the worktree, commits, and giter merges + removes + `delete_keys` at the AFTER-gate — with reconciliation at every giter entry as the missed-AFTER-gate safety net — all on existing daemon primitives, no new code.*

---

## Scope

### In Scope
- Prompt-level edits to **5 agents**: `giter`, `leader`, `developer`, `tester`, `tidier`.
- New "Worktree Mode" canonical home in `giter/workflow.md` (D0): schema (per-task census keys), lifecycle, location/naming, cleanup (remove-first + reconciliation), **two staleness clocks (10-min claim heartbeat / 15-min census TTL)**, pre-check-before-add, and **four** documented traps (corrected `.env`, `cwd`, port, conditional `-b`).
- **Mandatory** explicit `context={"wt_path": …, "wt_slug": …, "wt_branch": …}` hand-off from leader at every concurrent-editor spawn (D3/O-D3.1/U2). One-line awareness pointers / context= extensions in the other four agents (the "stated once" canonical-home rule).
- **Defense-in-depth:** developer's explicit KV read at the Auto-Commit gate when context has no `wt_path` AND ≥1 fresh `wt.claim.*` row exists for this branch (concrete trigger, C6).
- One `giter/rule.md` guideline in the Branch Management section (no new Cardinal anywhere — Cardinal cap ≤7 already saturated per D5/O-D5.1).
- One `giter/tools_note.md` extension: add `git worktree` / `git worktree add` (with conditional `-b`) / `git worktree add` (without `-b` for reuse) / `git worktree remove` / `git worktree list` entries (tool-shaped per writing-guide §4), plus a known-gotcha fix for the bare `git stash` pathspec strand that concurrent workers have hit before. (`git worktree prune` is documented as a NO-OP on live-dir worktrees (architect-verified) — applies to foreign/missing-dir registrations only; never a fallback when remove fails on a live-dir worktree — remove failure → STOP and report.)
- A final verification phase that runs grep-based compliance sweeps and a byte-budget check (`wc -c` on changed hunks).
- **Phase 4 (NEW):** one-time giter sweep of the pre-existing `/private/tmp` worktrees — **≤5 at run time** (the 5 known names — `adj-head`, `hotfix-defer-gate-base`, `m1-gate-base`, `pcfg-base`, `ens-autopromote-micro` — are the EXPECTED starting set, re-enumerated via `git worktree list` at run time; an entry already unregistered or dir-missing at run time is logged "already-resolved" and skipped) — sequenced AFTER the feature merge commit. Each entry is `git worktree remove`'d individually after per-entry verification. U3 in-scope.

### Out of Scope
- **No daemon code changes** (D5 "Daemon-Change Verdict: NONE", with the §1 caveat from D3) — all required primitives ship: `shared_meta_kv` (real tool names: `set_kv` / `delete_keys` / `clear_all`), non-empty-context durable enqueue routing in `send_message`, `git worktree` CLI. If a future need arose (dedicated `[SYSTEM CONTEXT: Worktree]` header, KV TTL), that becomes a separate proposal.
- **No `meta.json` changes** — all 5 relevant agents already hold `shared_meta_kv` (verified via `meta.json`).
- **No soul.md changes** — soul is identity/tone; this is process. (See D5: soul ~2k char implicit cap; keeping it under cap is one reason soul stays untouched.)
- **No tester-worktree-playbook canonization** — tester's `/tmp/<gate>-base` scratch is a different concern (throwaway per-test sandboxes, not coordination worktrees per D4). We only add a one-line pointer to giter's Worktree Mode for the regression-proof convention.
- **No `.gitignore` entries** — sibling-dir convention is the discipline (D4).
- **No automated prune tooling** — convention-only.
- **No new Cardinal rules anywhere** (per writing-guide §3: ≤7 Cardinals per `rule.md`; keeping all five `rule.md` files lean).
- **No fix for the 3 latent KV daemon defects** (architect §1a — snapshot cadence, spawned-child mispartition, system-default suppression). Per U4, these are logged for a SEPARATE follow-up task the leader spawns AFTER this feature ships; in this plan they are referenced in ONE line each here ("Follow-ups") and in `technical-analysis.md` (defect note) and nowhere else.

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | giter canonical home | Make giter the source of truth for the worktree-awareness contract (schema, dual clocks, reconciliation, pre-check, conditional `-b`, corrected `.env`, four traps, real KV tool names) | 7 | tight with Phase 2 (leader/developer cite it), tight with Phase 3 (tester/tidier cite it) | pending |
| 2 | Leader + Developer awareness | Leader pre-registers `wt.active.<branch>.<task-id>` rows, spawns giter FIRST, then dispatches editors with MANDATORY non-empty `context={"wt_path": …}`; Developer commits inside the assigned worktree (KV-read backstop if context lacks `wt_path`) | 6 | tight with Phase 1 (both agents cite "see giter Worktree Mode") | pending |
| 3 | Tester + Tidier pointers + verification | One-line pointers in the remaining two agents; full consistency sweep + byte-budget (~1650B total, U1) + guide compliance; verification of MANDATORY hand-off + reconciliation rule | 6 | tight with Phase 1 (both cite it); Phase 3 verification is the cross-phase gate | pending |
| 4 | Giter one-time stale-worktree sweep (NEW) | One-time cleanup of the ≤5 pre-existing `/private/tmp` worktrees (5 expected names — `adj-head`, `hotfix-defer-gate-base`, `m1-gate-base`, `pcfg-base`, `ens-autopromote-micro` — re-enumerated via `git worktree list` at run time) — sequenced AFTER the feature merge commit; per-entry `git worktree remove` after per-entry verification; `git worktree prune` is a NO-OP here (dirs exist) | 4 | independent of Phases 1-3; lands after the merge | pending |

**Phase ordering rationale:** Phase 1 establishes the canonical home; Phase 2/3 agents must cite it. Phase 3's verification sweep is the cross-phase gate that catches drift before any merge. Phase 4 is a small, standalone post-merge sweep; its work is entirely giter's and is sequenced to land only after Phases 1-3 are in `latest`.

---

## Coupling Map

|              | Phase 1 (giter)  | Phase 2 (leader/developer) | Phase 3 (tester/tidier + verify)              | Phase 4 (cleanup sweep) |
|--------------|------------------|----------------------------|----------------------------------------------|-------------------------|
| Phase 1      | —                | tight (canonical → cite)   | tight (canonical → cite)                     | independent             |
| Phase 2      | tight (cite)     | —                          | independent (verify sweeps all phases equally) | independent             |
| Phase 3      | tight (cite)     | independent                | —                                            | independent             |
| Phase 4      | independent      | independent                | independent                                  | —                       |

- **Tight coupling:** Phases 2 & 3 each contain prose that points back to Phase 1 ("see giter Worktree Mode"). If Phase 1's section is renamed or relocated, both Phase-2/3 pointers must be updated in the same commit (writing-guide §3 "Cross-reference hygiene"; also the project's lesson: `rule.md` cardinal-split regression class).
- **Loose coupling:** Phase 2's leader → developer awareness hand-off (leader writes `wt.active.<branch>.<task-id>` per planned editor BEFORE spawning giter; developer reads via mandatory `context=` and falls back to an explicit KV read at the Auto-Commit gate). Same KV partition, but no schema coupling beyond the documented key names. The leader pre-write intentionally overrides project-manager's `spawn → set_kv → send_message` ordering for this case (D2 lifecycle; phantom cost bounded by 15-min TTL).
- **Independent:** Phase 3's tester/tidier pointers are independent of Phase 2's leader/developer edits (they each cite Phase 1 only). Phase 4 is independent of Phases 1-3 entirely (it is post-merge cleanup of pre-existing foreign worktree registrations, not part of the new feature's contract).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Prompt drift across mirrors** — giter's Worktree Mode evolves but other agents' pointers keep stale wording | High | Medium | Writing-guide §2 canonical-home rule + Phase 3 verification grep: every "Worktree Mode" pointer in non-giter agents must literally read "see giter/workflow.md → Worktree Mode" (no parallel copies of the schema/lifecycle text) |
| 2 | **KV staleness misfire → unnecessary worktree** — leader wrote `wt.active.<branch>.<task-id>` then crashed; giter sees a stale row and creates a worktree that wasn't needed | Low | Medium | D2 **10-min claim heartbeat / 15-min census TTL** (two clocks, not the prior 30-min single-clock). Stale handling is always verify-at-next-giter-entry, never mid-flight removal. D1 Option-B heuristic fallback ("if main checkout is dirty AND any fresh active row exists, still create a worktree — cheap insurance"); giter's `git worktree list` pre-check reuses orphans, never errors on stranded claims (Failure Mode 1, 2 in `technical-analysis.md`) |
| 3 | **Leader drops the mandatory `context=` hand-off** — leader prompt drifts; editor spawns without `wt_path`; developer commits on the main checkout and collides with a sibling | High | Medium | D3/O-D3.1/U2 make the `context=` MANDATORY. Two layers: (a) primary — leader's prompt explicitly says "non-empty `context={"wt_path": …}` on every concurrent-editor spawn"; (b) defense-in-depth — developer's Auto-Commit gate explicitly reads the KV for `wt.claim.*` if `wt_path` is missing from context (same pattern the governor's `council_manifest` restore uses) |
| 4 | **Over-budget prose** — byte budgets from D5 are aspirational without enforcement; implementing agent pads with examples and example-session examples | High | High | Phase 3 verification runs `wc -c` on each new hunk; per-agent limits enforced (giter ~850, leader ~320, developer ~220, tester ~130, tidier ~130; **total ≤ 1650 bytes per U1**); reject-and-rewrite on overruns. The ~320B delta from the prior ~1330B cap is fully traceable to the architect's evidenced corrections (TTL lines, pre-check rule, mandatory context line, corrected `.env` rule, conditional `-b`) — no bloat |
| 5 | **Cardinal cap breach in any `rule.md`** — implementing agent adds "Worktree Cardinal" to keep rule "findable" but blows the ≤7 cap | Medium | Medium | D5/O-D5.1: NO new Cardinals; guideline-strength with workflow.md pointer is the canonical pattern; Phase 3 runs the **content-stability assertion** (plan-overview criterion 4): `## Must` / `## Must Not` regions of giter/leader/tester/tidier `rule.md` byte-identical to HEAD, and developer's `rule.md` delta exactly the one sanctioned 95B Must-Not bullet — no other additions means no new Cardinal-strength bullets, and the feature's diffs touch only sanctioned regions |
| 6 | **System internals leak into prompt prose** — implementing agent references `meta.json`, `daemon/`, `tools.allow`, `shared_context_metadata` to "clarify the contract" | High | Medium | Writing-guide §1 forbids them; Phase 3 grep `grep -rn -E "meta\.json\|tools\.allow\|daemon/\|shared_context_metadata\|innate_skills\|get_tree_root_id" agents/<edited>/` returns zero hits inside prose |
| 7 | **Cross-reference breaks if `giter/workflow.md` §Worktree Mode is renamed** | Medium | Low | Section title is a stable label; rename requires sweeping three other agents' pointers in the same commit; verification grep checks each pointer's section-name label |
| 8 | **Stale worktrees from prior session not cleaned** — ≤5 pre-existing registrations in `/private/tmp` (expected starting set: `adj-head`, `hotfix-defer-gate-base`, `m1-gate-base`, `pcfg-base`, `ens-autopromote-micro`; prior plan said 4; architect verified 5) survive feature close | Low | Medium | **Phase 4 (NEW) — one-time giter sweep, sequenced after the merge commit; runtime-resilient (C1):** re-enumerate at run time via `git worktree list`; an expected entry already unregistered or dir-missing at run time is logged "already-resolved" and skipped; per-entry verification on the REMAINING registered entries (registered + dir exists + not the main checkout + no uncommitted work worth saving) + `git worktree remove <path>`. Completion gate: **all REMAINING registered entries proceed=Y**. `git worktree prune` is a NO-OP here (dirs exist); remove is the tool. Giter's reconciliation rule (D2 lifecycle) deliberately ignores foreign paths; it does not address these entries, so the sweep is the canonical fix |
| 9 | **`git stash` pathspec strand (known gotcha)** — if implementing agent adds a `git stash` example in giter's Worktree Mode, bare `git stash` would strand concurrent workers' changes | Low | Low | Phase 1 includes the explicit bare-stash pathspec fix (`git stash push -- <own files>`) in `tools_note.md` while we're already touching the file (golden-hour fix per writing-guide §10 "while you're there") |
| 10 | **Test false-healthy on existing grep tooling** — repo grep has returned false negatives before | Medium | Low | Phase 3 verification specs REQUIRE explicit `read_file` for known-anchored verification, not blind `grep -rn`; one anchor per claim |
| 11 | **`.env` trap reversal lost in edit** — implementing agent copies the prior "worktree daemons source main-repo `.env`" rule, which is REVERSED per architect §5 and would send every worktree daemon to `ensemble_prod` | High | Medium | D4 carries the corrected rule verbatim: "never launch `dev.sh` from inside a worktree; if a worktree daemon is needed, `set -a; source <main-repo>/.env; set +a` and bind a non-8079 port." Source citation corrected: `LESSONS/2026-08-20-e2e-never-claimed-signature.md` (NOT `RESULTS/2026-08-20:59`). Phase 1 acceptance + Phase 3 grep enforce |
| 12 | **Pre-existing foreign worktrees confused with in-flow worktrees** — giter's reconciliation rule scope is `../<repo>-wt-*` family; pre-existing `/private/tmp` entries are outside that family and must be addressed by Phase 4, not by reconciliation | Low | Low | Phase 1 documents the scope explicitly; Phase 4 enumerates the foreign set separately; verification reads the per-entry path to confirm it falls in the pre-existing `/private/tmp` set, not the `<repo>-wt-*` family |

---

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | **Byte budgets honored per agent (U1)** | Added-hunks byte count per file (wc -c on ADDED lines, not line counts): `git diff -U0 --no-color -- <file> | grep '^+' | grep -v '^+++' | wc -c` — the 1-byte-per-line `+` prefix overhead is accepted and applied identically everywhere; the method is spelled out identically in phase1 Task 6, phase2 Task 6, phase3 Task 4 + snippet step 2 | giter ≤ 850 bytes (sub-caps workflow 572 / rule 82 / tools_note 196 — sum exactly 850); leader ≤ 320 (workflow 175 / tools_note 145); developer ≤ 220 (rule 95 / workflow 125); tester ≤ 130; tidier ≤ 130; **total ≤ 1650 bytes** (U1) |
| 2 | **Guide-compliance checklist passes** | Run the §10 Pre-Commit Checklist from `docs/agent-prompt-writing-guide.md` per edited agent | Zero checkboxes hit `false` on the system-internals / cardinal-cap / no-stated-once-trap / cross-reference-resolve / canonical-home items |
| 3 | **Zero diff outside the D5-ratified per-agent file surface + the planning dir** | `git diff --stat` after plan implementation | Touched files are limited to the D5-ratified per-agent surfaces: `agents/giter/{workflow.md, rule.md, tools_note.md}`, `agents/leader/{workflow.md, tools_note.md}`, `agents/developer/{rule.md, workflow.md}`, `agents/tester/workflow.md`, `agents/tidier/workflow.md` (soul.md untouched; no `meta.json`, no `docs/`, no `daemon/`, no `tests/`); plus the planning dir `.agents/shared/planning/worktree-aware-prompts/` itself (plan artifacts and the verification summary) |
| 4 | **No new Cardinal-strength content in any `rule.md`** | Content-stability assertion, per file (C4). For **giter / leader / tester / tidier**: the `## Must` / `## Must Not` regions extracted from HEAD vs worktree must be byte-identical — `diff <(git show HEAD:agents/<name>/rule.md \| awk '/^## /{m=($0~/^## Must/)} m') <(awk '/^## /{m=($0~/^## Must/)} m' agents/<name>/rule.md)` empty. For **developer**: the sanctioned Must-Not bullet is the ONE allowed delta — the rule.md diff must add exactly the 95B phase2 File-3 literal and nothing else (this replaces the vacuous Cardinal-heading grep: flat rule lists carry no `###` headers to count) | Empty diff for giter/leader/tester/tidier; developer's rule.md delta = exactly the sanctioned bullet; feature diffs touch only sanctioned regions; no cardinal-split refactor |
| 5 | **One canonical home for the protocol** | Read giter's Worktree Mode + grep the four other agents' pointers | The KV schema (per-task census keys + claim), lifecycle, reconciliation rule, dual clocks, pre-check rule, four traps, and conditional `-b` appear ONLY in `giter/workflow.md`; the others say "see giter/workflow.md → Worktree Mode" (no duplicate paragraphs) |
| 6 | **No system internals leak into prompt prose** | `grep -nE 'meta\.json\|tools\.allow\|daemon/\|shared_context_metadata\|innate_skills\|get_tree_root_id\|seed_all\|agent_id=\|default_agent_versions' agents/giter/ agents/leader/ agents/developer/ agents/tester/ agents/tidier/` | Zero hits in *prose* (a hit inside a fenced code block that quotes daemon output is allowed if the surrounding sentence is agent-POV) |
| 7 | **Soul.md count unchanged** | `wc -l agents/<name>/soul.md` before/after | Identical line counts |
| 8 | **Phase 3 compliance sweep is reproducible** | Run the verification snippet from the Phase 3 plan; all checks report `PASS` | All seven acceptance checks in Phase 3 report PASS (including the updated 1650B cap, the 10-min/15-min dual-clock literal check, and the reconciliation/pre-check phrase sweep) |
| 9 | **Cross-references survive a hypothetical rename** | Each pointer to "giter/workflow.md → Worktree Mode" uses the **section-name label** (not line number) | Every pointer is a stable label, per writing-guide §3 |
| 10 | **Behavior parity against leader/developer with no worktree** | When `wt.active.<branch>.<task-id>` count (TTL-filtered) is 0 or 1 (single editor), giter does NOT create a worktree (Phase 2 leader never spawns giter with `force_worktree`) | Logged in giter's Worktree Mode "When NOT to use" subsection |
| 11 | **Leader's mandatory `context=` hand-off is wired (D3/U2)** | Read the leader's Git Flow extension + read the developer Auto-Commit prefix | Leader's Git Flow section explicitly says "non-empty `context={"wt_path": …}` on every concurrent-editor spawn"; developer's Auto-Commit section explicitly says "if no `wt_path` in context AND ≥1 fresh `wt.claim.*` row for this branch, read the KV first" (defense-in-depth; concrete trigger per C6) |
| 12 | **Giter's reconciliation rule is wired** | Read giter's Worktree Mode section + run the Phase 1 acceptance checklist | The reconciliation rule (every giter entry: enumerate `git worktree list` for `<repo>-wt-*` family; adopt or remove orphans; delete phantom claims) is present and the pre-check-before-add rule is present; the corrected `.env` rule is present; conditional `-b` is present |
| 13 | **Phase 4 stale-worktree sweep complete (U3)** | After Phase 4 lands, `git worktree list` no longer shows any of the ≤5 expected `/private/tmp` entries (entries already unregistered or dir-missing at run time were logged "already-resolved"); the sweep's per-entry verification log is preserved | Zero REMAINING expected entries (post re-enumeration, excluding already-resolved) remain registered; **all REMAINING registered entries showed proceed=Y before their remove** (already-resolved entries — unregistered or dir-missing at run time — are logged separately and excluded from the gate, consistent with phase4-plan.md Task 2); each removed entry's `git status` was clean (or the operator was prompted to intervene) |

---

## Research Insights

Direct quotes / paraphrases from `decisions.md` / `architecture-recommendation.md` / `technical-analysis.md` that shaped this plan:

- **D0 canonical home = giter/workflow.md** — selected because giter is the only write-side actor in the protocol (writes `wt.claim.*`); others read via explicit tool call or mandatory `context=` hand-off. The writing-guide binds one canonical home per cross-agent contract (`:66-78`).
- **D1 detection = KV census (per-task keys)** — leader writes `wt.active.<branch>.<task-id>` BEFORE spawning giter (O-D1.1: trigger = `≥ 2 TTL-fresh rows for this branch`; the "giter is one actor" ambiguity is deleted from prompt encoding). Sticky-phantom risk is bounded by the 15-min read-side TTL.
- **D2 KV protocol** — `wt.claim.<slug>` (giter writes AFTER `git worktree add` succeeds, O-D2.2 retained with two mandatory companions) + `wt.active.<branch>.<task-id>` (leader writes per-task pre-spawn); **two staleness clocks (10-min claim heartbeat / 15-min census TTL, O-D2.1)**; slug = sanitized branch + fallback + collision suffix (O-D2.3); pre-check-before-add + reconciliation rule at every giter entry (closes the crash windows per architect §4); LWW conflict semantics benign by design.
- **D3 awareness = mandatory `context=` hand-off + explicit KV reads (Option B, REFUTED auto-surface)** — architect file §1a refutes the prior auto-surface substrate on 3 independent daemon defects (snapshot cadence; spawned-child mispartition; system-default suppression). The ratified design is two-channel: (a) **PRIMARY** — leader's non-empty `context={"wt_path": …}` on every concurrent-editor spawn (forces durable enqueue routing, immune to all 3 defects; O-D3.1 mandatory per U2); (b) **DEFENSE-IN-DEPTH** — explicit KV read at git decision gates (governor `council_manifest` restore pattern). Auto-surface is opportunistic only and never load-bearing.
- **D4 worktree conventions** — sibling-dir `../<repo>-wt-<slug>/` (O-D4.1); cleanup is **remove-first** then `delete_keys` (architect §4 — `git worktree prune` is a NO-OP when the directory exists, so it is not the primary verb); **four** traps (corrected `.env` per architect §5, `cwd` isolation with the new "no `.venv`" addendum, port collision generalized, conditional `-b` refusal per O-D4.2 with corrected content); **5** pre-existing `/private/tmp` worktrees addressed by Phase 4 (U3), NOT by the in-flow reconciliation rule (which is scoped to `<repo>-wt-*` family).
- **D5 prompt surface** — giter gets canonical + 1 guideline (~850B); leader gets Git Flow extension + `context=` extension (~320B, MANDATORY hand-off per U2); developer gets rule.md Must-Not + Auto-Commit prefix carrying the KV-read backstop (~220B; backstop canonically lives in workflow.md per O6, rule.md keeps only the Must-Not); tester gets one-line pointer + `.env` reminder (~130B); tidier gets one-line pointer + `.env` caution (~130B); **~1650B total (U1)**; **NO** new Cardinals; soul.md untouched; real KV tool names (`set_kv` / `delete_keys` / `clear_all`).
- **Daemon-Change Verdict: NONE** (retained, with the §1 caveat from D3) — explicit channels sidestep the 3 latent daemon defects; those defects are logged for a separate follow-up (U4) but do NOT block this feature.
- **Cardinal-cap reasoning** — current giter `rule.md` already has ~7 Cardinals; adding a new one is borderline; guideline + workflow pointer is the canonical pattern. No leader Cardinal for the mandatory hand-off (O-D5.3 verdict: defer; add giter-side reconciliation instead).
- **Send-before-write discipline (overridden for census)** — project-manager's `spawn → set_kv → send_message` pattern is intentionally overridden for the census pre-write: detection requires the row to exist before giter reads, and the phantom cost is bounded by the 15-min TTL. The override is stated in D2 lifecycle so the rationale is explicit.
- **Failure modes** — schema designed so concurrent writers cannot produce a corrupt state; the reconciliation rule (every giter entry) is the safety net for the missed-AFTER-gate failure mode and for the crash-between-remove-and-delete window; corrected `.env` trap closes the prod-defaults-leak failure mode.

---

## Closed Forks

All 11 forks (O-D1.1 … O-D5.3) are closed. See `decisions.md` → "Closed Forks — All 11 Ratified" for the verdict values and source citations. The "Open Questions" escalation table is gone; there is nothing to escalate.

---

## Follow-ups (OUT of this plan, surfaced here per U4)

- **U4 — Fix the 3 latent KV daemon defects** (architect §1a: snapshot cadence, spawned-child mispartition `_persistent_parent_id=None`, system-default suppression) — leader spawns this as a SEPARATE task AFTER the worktree-aware-prompts feature has merged. The defects silently degrade *all* KV-based ambient awareness protocols, including governor `council_manifest` restore visibility. **This plan is complete without that fix** because the explicit-channel design sidesteps them; the fix is hygiene, not a blocker.

---

## Out-of-scope Deferrals (do NOT add to this plan)

These were surfaced by technical analysis and explicitly excluded here:

- **Dedicated `[SYSTEM CONTEXT: Worktree]` header** — fails the prompt-only constraint (D3 §C); revisit only if the explicit-channel design proves insufficient.
- **Tester-worktree-playbook canonization** — different concern (per-test scratch).
- **Auto-prune tooling** — convention-only.
- **Cardinal split / renumber of any `rule.md`** — non-blocking; cross-reference hygiene would force a sweep beyond byte budget.
- **`.gitignore` entries for worktree sibling dirs** — sibling-dir is the discipline.
- **Updates to `agents/tester/LESSONS/` or `RESULTS/`** — separate documentation backlog.
- **Any change to `docs/agent-prompt-writing-guide.md`** — the guide is the ground truth; we are its consumer here.
- **Fix for the 3 latent KV daemon defects** (architect §1a) — see "Follow-ups" above; SEPARATE task (U4), leader-spawned AFTER the feature merges.

---

## Plan Inventory

- `phase1-plan.md` — giter canonical home (workflow.md Worktree Mode + rule.md guideline + tools_note.md command entries + bare-stash pathspec golden-hour fix; with reconciliation rule, dual clocks, pre-check-before-add, conditional `-b`, corrected `.env`, real KV tool names, giter cap ~850B). **Carries the MUST-FIT LITERAL CONTENT LISTS** for giter's three files (exact prompt lines + recorded `wc -c` byte counts; sub-caps sum to exactly 850).
- `phase2-plan.md` — leader (Git Flow awareness extension + MANDATORY non-empty `context={"wt_path": …}` hand-off + `wt_path/wt_slug/wt_branch` keys, ~320B) + developer (Auto-Commit worktree prefix + rule.md Must-Not + KV-read backstop, ~220B).
- `phase3-plan.md` — tester pointer + tidier pointer + cross-file consistency sweep + byte-budget (~1650B total, U1) + guide-compliance verification pass (with updated 10-min/15-min dual-clock literal check and reconciliation/pre-check phrase sweep).
- `phase4-plan.md` (NEW) — giter one-time stale-worktree cleanup sweep of the 5 pre-existing `/private/tmp` entries (`adj-head`, `hotfix-defer-gate-base`, `m1-gate-base`, `pcfg-base`, `ens-autopromote-micro`); sequenced AFTER the feature merge commit; per-entry `git worktree remove` after per-entry verification.

**Canonical sequencing (single source of truth — `phase2-plan.md` and `phase3-plan.md` point here; this paragraph replaces the three formerly-conflicting sequencing clauses):** Phases 1-3 land on ONE integration branch — Phase 1 first; Phases 2 and 3 in any order AFTER Phase 1 (both cite Phase 1's canonical home, so Phase 2's edits are sequenced only until Phase 1's edits are on the integration branch). Phase 3's verification sweep GATES the merge. ONE merge commit to `latest` carries Phases 1-3 together (no single phase lands to `latest` alone). Phase 4 is a SEPARATE post-merge commit, sequenced after the Phase 1-3 merge commit lands.
