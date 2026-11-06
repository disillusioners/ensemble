# Approver Tracking: canary-injection-defense

Plan set: .agents/shared/planning/canary-injection-defense/ (plan-overview, requirements, decisions, technical-analysis, architecture-recommendation, phase0–6)
Worktree: /home/nea/ensemble-src-wt-canary-injection-defense @ feature/canary-injection-defense (planning set HEAD c8325a0d5, base 97b76e20e)

---

## Iteration 001 — 2026-11-05T20:30Z–21:0xZ — VERDICT: REJECTED

Dispatch: 3 section-partitioned workers, skill `plan-approval` each, cold context (large-plan exception; fan-in via todo_graph w-requirements / w-architecture / w-phases — all done).

Worker verdicts:
- approve-worker-requirements (845e4dc6): APPROVED — 0 blocking, 5 notes. Cross-doc arithmetic (D1–D7, baseline 177→181, T29 20→25, boot 43→44, tree 366→378) all consistent.
- approve-worker-architecture (7dc51aab): REJECTED — 3 blocking.
- approve-worker-phases (a4a95ff5): REJECTED — 4 blocking.

### Blocking issues carried into the verdict (aggregated, no downgrades, no upgrades, no additions)

1. [arch-1 / completeness+feasibility] architecture-recommendation.md §5.1/§5.2/§11 — D5 re-derivation ledger declared COMPLETE but misses the CHILDREN overload: 17 tests parametrized ("agent", CHILDREN) resolve via _lib_path/_skill_body_path → INSTALLER_SKILLS (3 keys only) → KeyError 'canary' on ~18 legs; T23 primary test_md_fences_bullet_lists_byte_identical (:615) arity-broken by a 4th element; CHILD_ALLOW row's literal "+ canary" reds (proc removed by D7) and the naive repair re-installs the NC-4 defect. Required: named ledger row for the LEAVES/INSTALLER_CHILDREN constant split + T23 arity.
2. [arch-2 / safety] architecture-recommendation.md §7.4 (CAG-06), §6.2, §6.3 — "campaign scratch root" (the reference point for 2 of 4 allowlisted action classes in the ONLY containment control) occurs twice, both in the allowlist quote, and is NEVER defined (no path/creation/teardown/assertion); five-item pre-flight omits §6.2's scratch-project clause → target spawnable with live project_id under a hostile payload.
3. [arch-3 / consistency+safety] architecture-recommendation.md §3.6 vs requirements.md SK-11 (:108) — monotonicity contradiction: ruling pins hostile-only downgrade refusal while the binding requirement says "may only raise a verdict or raise confidence" → suspect→clean laundering via --llm-verdict clean --llm-confidence high accepted by the scanner; corrupts F14 leakage metric. Fix: refuse ANY downgrade (one sentence + one pin) or amend SK-11 on the record; also state no verdict value is an authorization token.
4. [phases-1 / consistency] plan-overview.md §one-commit pin ceremony + S2 vs phase4-plan.md task 6 + §one-commit composition — the two binding enumerations of the SAME commit disagree on .agents/tester/PACKS.md (in phase4, absent from overview which closes "and nothing outside this enumerated set") → P4 commit compliant with phase4 false-fails overview S2 and vice versa. One-line fold.
5. [phases-2 / consistency] phase1-plan.md Gate G1.2 vs phase1 tasks 1/3 + SK-16 — G1.2 asserts "no confidence field" for degrade while tasks/SK-16 rule confidence ALWAYS present with none as the legal degrade value (byte-identity acceptance requires it) → gate as worded unsatisfiable / tests the opposite shape. Reword G1.2 to "visible via confidence: none".
6. [phases-3 / feasibility] plan-overview.md §Merge-Overlap opening premise + phase0-plan.md task 7 / Gate G0.5 — the memoir-branch premise is stale TODAY: the verification command refs a nonexistent origin ref and prints a vacuous 0 through the pipe (the exact vacuous-clean trap the plan set bans); true overlap is 3 agents/ paths (.prompt-artifacts.sha256, ari/meta.json, ari/tools_note.md; memoir tip b60502671 2026-11-05T20:41Z postdates the plan set) → G0.5 cannot pass as specified. Mitigating: no aegis-path overlap; origin/latest still == base.
7. [phases-4 / safety] phase6-plan.md task 3 + plan-overview Merge-Overlap item 6 — agents/.prompt-artifacts.sha256 now conflicts on BOTH branches and has NO resolution rule; the only named rule (PACKS.md keep-both) is actively wrong for a hash baseline (double-key corruption). Prescribe: take either side, regenerate once on the merged tree (P6 task 2), never keep-both.

### Notes (non-blocking, from workers — not verdict-affecting)
- D4 migration-trigger arithmetic dead (24 KiB table inside 20 000-char body cap; NFR-05/phase1 task 5 vs §4.1) — align numbers.
- Mirror byte-identity vs per-recipient auto_load frontmatter (T22/M7): state whether mirror omits the key or the pin normalises it.
- §5.5 decomposition parenthetical 8+3+2=379 vs total 378 (total correct, parts wrong).
- T38 absent from §5.1/§11 pin inventory lists (r2 fold added it; leg-count disagreement).
- bare python3 unstated host dependency — screen unusable (fail-closed suspect) on hosts without it; consider pre-flight assertion.
- `self` posture-escape channel (inner_soul/access_memory durable self-modification) unexamined by D7 review — cheap tightening: drop `self` from canary allow.
- Inertness reviewer unnamed (self-certification by composer is weakest link of the only containment control).
- Provenance fields caller-declared not attested — one line in SK-24 non-guarantees closes it.
- No explicit rollback statement in primary pair (implicit house answer adequate for stage-only lane).
- Stale cross-refs (phase3 "tasks 2 and 9" under D1-B dormant path); overview Gate A "Unchanged" lists 177→181 count; duplicate corpus authorship P1 task 4 vs P4 task 4; P6 task 10 vacuous dirname corroboration; phase-local greps of lane-end state; S17 listed between S13/S14; P5↔P0 "independent" vs "loose"; decisions.md §1 "six decision slots (D1–D6)" stale prose (D7 exists); hacker/cracker skill-coverage gap disclosed (R6b); rollback implicit for staged-only lane.
- Minor: §5.2 citation tests/unit/test_maintenancer_agent.py; technical-analysis.md :537-542 broken paragraph.

### Worker credit (context for next iteration)
- Requirements layer APPROVED clean — do not re-litigate D1–D7/arithmetic there.
- Both REJECTING workers state all blockers are cheap surgical folds, no structural rework; factual foundation independently verified strong (~45 quantitative claims re-derived across the two, all held — failures are in gate wording, ledger enumeration, and a stale premise).

Next: Status IN_PROGRESS, Iteration 002 (cap 3).

---

## Iteration 002 — 2026-11-05T21:2x–21:4xZ — VERDICT: REJECTED (review target e559548bf)

Target: HEAD `e559548bf` (4fe4d995a B1–B7 fold → cc6303308 AM-1/AM-2 → e559548bf AM-3). AM-3 landed mid-review at 21:26:35Z; workers pinned e559548bf and reported no drift start→end.

Dispatch: 3 fresh cold workers, `plan-approval` each; fan-in w2-requirements / w2-architecture / w2-phases.
- approve2-worker-architecture (3091db87): REJECTED — 10 blocking.
- approve2-worker-requirements (1959cc6b): REJECTED — 6 blocking.
- approve2-worker-phases (2563d03d): REJECTED — 4 blocking. (Instance errored once on upstream rate-limit; revived ONCE from checkpoint per escape valve; completed on the revive.)

### Confirmed closed (verified, not trusted)
- B4 one-commit enumerations: byte-identical, 19/19 lines, diff rc=0, PACKS.md present both sides; S2 closing clause no longer contradicted. [phases (A)]
- B5 G1.2 `confidence: none` coherent with tasks 1/3 + SK-16; byte-identity acceptance satisfiable. [phases (B)]
- B6 memoir premise: local-ref command verified executable, returns 3 paths (`.prompt-artifacts.sha256`, `ari/meta.json`, `ari/tools_note.md`), no vacuous `| wc -l`. [phases (C)]
- B7 rule present and specific in phase6 task 3 + G6.6 + overview 2b; keep-both nowhere applied to the hash baseline. [phases (D)]
- SK-11 (B3) in requirements: any-downgrade, both axes, matrix pin T38, "no verdict value is an authorization token" — exemplary and fully landed. [requirements]
- AM-1: canary allow {bash, filesystem, time, help}, `self` dropped, no orphaned requirement, T29 arithmetic unaffected. [architecture]
- Census: 366 files / 177 rows / 43→44 / 20→25 cells / roster 17 — all re-derived and holding. [all three]

### Blocking issues carried (aggregated; merges noted; one downgrade)

1. [arch1+phases1 MERGED] B1 ledger incomplete — `test_children_have_nomanifest` unaccounted (sweeper gains manifest ⇒ reds on INSTALLER_CHILDREN; canary ⇒ reds on FAMILY_CHILDREN; neither constant names (hacker,cracker)); `FAMILY` roster tuple untouched ⇒ ~22 legs red via TONE_HEADINGS KeyError, cardinal ==7 vs P3 "≤7", default_queue never set, second aegis team literal (:3391-3408). LEAVES/FAMILY_CHILDREN naming split (3 constants, not 4); phase5 gate greps the FAMILY_CHILDREN spelling only.
2. [arch2] B1 split absent from `architecture-recommendation.md` — zero occurrences of LEAVES/INSTALLER_CHILDREN/FAMILY_CHILDREN/ALLOW_BY_AGENT; §5.2 still "+ canary"; §5.2 :348-369 hides test_children_allow_set_exact (:357-359).
3. [arch6+req5 MERGED] T38/M8 inventory not landed — arch §5.1 "T31–T37", §11 re-check "M1–M7", §5.4 M1–M7; decisions §C.5/§F.1 "T31–T37"; overview claims "T38 in every pin list" unsubstantiated (only delegation row decisions:737); technical-analysis:608 still records T38 absent.
4. [arch3+req6 MERGED] AM-3 not propagated — six narrowing sites (§5.3:335, §9 F4:554, T38(d), phase5 Gate E, technical-analysis:470-471, decisions:743 live divergence); technical-analysis:592 calls AM-3 a "candidate … not repaired"; decisions §C-bis.5 :732 still asserts arch doc "NOT edited" (falsified by §3.6 AM-3 line :241-243; :743 says RESOLVED) ⇒ self-contradiction on a current-state claim. Consequence: `--llm-verdict clean --llm-confidence high` over heuristic `suspect` passes pin and gate — the exact B3 defect.
5. [arch4] phase3 task 1 says deny_spawn "mirrors the family's [\"knowledge\"] default (keeps the KB lookup)" vs §7.1/CAG-03 absent-by-construction; tree: only aegis carries knowledge+deny_spawn, hacker/cracker/sweeper neither. Either instruction produces a defect (T35 exact-four red, or dead-weight deny under closed allow).
6. [arch5; req note merged in] AM-2 budget rests on a non-existent 20,000-char body cap; real bound is `skill_view` truncation at 8,000 chars (skill_tools.py :100-1004 DB, :428-432 file) — aegis/worker (auto_load:false) would not fully see an 8,001–18,000-char body; table budget ≤8,000 = whole display window. Budgets are conventions, not system caps.
7. [arch8] scratch root conflicts with off-repo mandate — task 7/G3.9 `$DATA_DIR/aegis-engagements/...` vs DERIVATION 8 "nothing under the repo" + G3.7 `$HOME/.local/state/aegis/engagements/<id>/`; `$DATA_DIR` defaults inside repo (api.py:516-519), gitignored but present during campaign.
8. [arch9] `scratch_root_removed_at` absent from CAG-09 15-key schema, T36 pin, AC-CAG.4; CAG-14 "every exit path" vs task 7/G3.9 "post-campaign" only (happy-path teardown passes).
9. [arch10] §5.5 decomposition 8+3+2=379 vs stated 378 (canary skill-set inside the 8); technical-analysis:607 claims folded — not.
10. [req1] DOC-07 BINDING "the skills are untouched by this build for the five existing personas" while D2-B seeds aegis/worker/sweeper (3 of those five); AC-DOC.3 pins only soul/workflow/tools_note ⇒ false clause unpinned and unsatisfiable.
11. [req2] CER-06/AC-DOC.6/DOC-10 (+arch:317, phase4:140) "worker diff = exactly one file" — verified reality 3 files (worker skill-set.yaml modified, empty skills-template dir gains mirror, rule.md) ⇒ T25 reds at HEAD by construction; obvious repair breaks D2-B.
12. [req3] X-10 "one new test file or one new module … owner's choice" re-opens ruled D5-A (no sibling test file); following X-10 reds S2's enumerated-commit set.
13. [req4] §11 row 2 still "Six recipients" (pre-ruling) vs D2-B four; fold-record extinguishment claim unsubstantiated (rows 2,3,5 remain pre-ruling).
14. [phases2] P6 task-table cycle — task 2 "re-derive on merged tree" Depends On "—" precedes task 3 merge; task 3's own rule says regenerate once (task 2) after merge; G6.6 inherits.
15. [phases4] G0.5 stale measured literal — asserts memoir tip `b60502671`; actual `db6ec53f6`; premise survives (3 paths re-measured) but gate as written REDs; plan's own doctrine is re-derive-not-pin.

### Downgraded (judgment band, reason stated)
- phases#3 (B7 stale record lines in decisions:736 + technical-analysis:596 "must be added there") → NOTE: record-only, normative artifact (phase6:40, G6.6, overview 2b) correct and audited; no execution risk beyond reader confusion. Recorded as a note with the rest.

### Notes worth folding while editing
- §4.1 "18 rules" vs "≤18 rules"; T38/Gate E prose vs decisions; task-count drift (P1/P3 list 10 but have 11 rows incl 5b/10b); S11/R8 omit PACKS.md; phase2 DERIVATION 3 "0 elsewhere" self-contradictory; mirror pin strips only ^auto_load: (blank-line allowance unhandled); Gate H privacy grep can false-fail on path words; approver bookkeeping commit path unstated; phase4 DERIVATION 3 slot literals now taken (v0.28.13 exists on another lane — origin/latest at 17413bca5, 39 behind); P6 entry STOP will fire on any lane not just memoir (generalise R1/R11); phase3 task 10b Depends On incomplete; doc ref precision nits (phase0:5-65 vs :89; phase2 order of ## Must/## Open questions/## Must Not; :2487 vs :2490); phase0 DERIVATION 5 pipefail under /bin/sh; SK-11 `note:` field vs SK-16 closed list; C.1/§C.1 leftover pre-AM-1 allow set; editorial duplications.

Next: IN_PROGRESS, Iteration 003 (FINAL — cap 3).

---

## Iteration 003 (FINAL, cap 3) — 2026-11-05T22:5xZ — VERDICT: APPROVED (APPROVED-WITH-NOTES) — review target 24ba60462

Replacement approver (prior instance died on an infrastructure quota error); SAME final call.
Chain: fold 4c6fdd248 (B1–B15) → micro-fold 950b16202 (C1–C6/N1–N3) → micro-fold 24ba60462 (MC-1/MC-2/OPTIONAL-1, 3 files +3/−3)
→ independent plan council MICRO-CLEAR at 24ba60462. Worktree clean at 24ba60462 apart from the approver's own bookkeeping.

Method disclosure: NO fresh worker dispatch — iteration 003 is the cap, and the input is the two worker-produced blocker
sets (6 cold workers) plus the council pass at this exact SHA. This pass re-verified all 15 iteration-002 blockers ON DISK
with its own commands plus the high-risk factual premises the plan now rests on.

### All 15 iteration-002 blockers — CLOSED (verified by command, not by fold record)
1. B1 ledger: FOUR source constants named (FAMILY/CHILDREN/CHILD_ALLOW/TONE_HEADINGS :75/:76/:89/:131); split targets
   FAMILY_ROSTER(5)/INSTALLER_CHILDREN(3)/FAMILY_CHILDREN(4)/DOCTRINE_ONLY(2)/MANIFEST_BEARING(4)/ALLOW_BY_AGENT/
   TONE_HEADINGS(5)/CARDINAL_COUNT_BY_AGENT (phase4 :157/:169/:170/:172). FAMILY_ROSTER 22 legs re-summed correctly
   (15 parametrize + 4 non-parametrized + 3 LEGALITY_CELLS) and every site cross-checks against the file: 25
   `parametrize("agent", CHILDREN)` sites enumerated match the tree EXACTLY (25), plus :613/:618 (T23) and :707/:956.
   test_children_have_no_manifest (`def` at :565, real name confirmed) is split into two named-tuple legs
   (test_doctrine_only_children_have_no_manifest / test_seeded_agents_have_one_injection_screen_entry). LEAVES stated
   not to exist / not introduced. Base facts verified: aegis+worker skill-set.yaml exist, sweeper/hacker/cracker absent,
   sweeper/skills-template exists, canary absent ⇒ sweeper's +1 standalone manifest is real.
2. B1-split now IN arch §5.2 (INSTALLER_CHILDREN 5 refs, FAMILY_CHILDREN 4, ALLOW_BY_AGENT 4, FAMILY_ROSTER 5); the
   per-leg pin table names test_children_allow_set_exact (:357-359) which was previously hidden.
3. T38/M8 inventory landed: arch :29/:300/:361/:665 + §11 item 6 (T31–T38, M1–M8 M7 mandatory); decisions/requirements
   carry T31–T38 with the one surviving "T31–T37" string explicitly labelled retired wording.
4. AM-3 propagated to all six narrowing sites (arch §3.6 :241-243, §5.3 :374, §9 F4 :603, phase4 T38(d), phase5 Gate E,
   technical-analysis :473) + decisions :748/:768/:775/:780/:796; the "arch NOT edited / AM-3 candidate" status is CLOSED.
   T38(d)/Gate E carry the discriminating `suspect` + `--llm-verdict clean --llm-confidence high` case.
5. deny_spawn ["knowledge"] pinned-AS-RULED, the false "keeps the KB lookup" justification removed; tree truth confirmed
   (only aegis carries it).
6. AM-2 → AM-4: the 8,000-char bound is REAL — verified in daemon/tools/skill_tools.py:428-432 and :1001-1004, and NO
   20,000-char cap exists anywhere in the file. Data-file shape (injection-rules.json, canonical once, Path(__file__)
   .with_name, own version) + body ≤8,000 HARD pin landed in arch §4.1/§5.3/§5.4, phase1 tasks 5/6/G1.5/G1.8, phase4
   T32/T37, requirements SK-04/NFR-05, phase6 G6.1b (exactly one copy ships).
7. Scratch root moved OFF-repo: $HOME/.local/state/aegis/engagements/<engagement-id>/campaign/<payload-id>/; $DATA_DIR
   explicitly unusable — premise VERIFIED (daemon/api.py:516-519 defaults to "./data" inside the repo).
8. scratch_root_removed_at in CAG-09 (fifteen keys), CAG-14, AC-CAG.4, T36; teardown on EVERY exit path (trap/finally,
   incl. signal), pre-fire 0700 + test -d/-w assertion, G3.9 reconciliation so a CAG-09-faithful campaign PASSES.
9. Arithmetic re-summed: 9 canary-dir (incl. the AM-4 data file) + 3 mirror bodies + 1 standalone manifest = 13 ⇒
   366 + 13 = 379, consistent across arch §5.5/:681-683, decisions :683, phase4 table/G4.6, phase5 Gate G, phase6
   G6.1b. Base count VERIFIED in-tree: agents/ = 366 files.
10. DOC-07 "skills untouched" retracted (3 of 5 personas are seeded); AC-DOC.3 scoped; 3-file worker reality stated.
11. CER-06/AC-DOC.6/T25: "exactly one file" retracted; pin is ZERO DELETIONS in rule.md with skill-set.yaml + mirror
    explicitly allowed. (agents/worker unchanged 1f6898ae2→97b76e20e — verified, so T25 stays green at base.)
12. X-10: family file grows, NO sibling test file, §11 r5 binding.
13. §11 row 2 → four seeded recipients; the r2 "extinguishment complete" claim annotated as a historical correction and
    now true at HEAD — independently grepped: "Six recipients"/"skills are untouched"/"exactly one file"/"one new test
    file" have ZERO live claims (all remaining hits are retraction/historical text).
14. P6 order fixed: task 2 merge → task 3 regenerate (Depends On 2) → G6.6 requires the regeneration commit to postdate
    the merge; .prompt-artifacts.sha256 take-either-side + regenerate-once, keep-both banned.
15. G0.5/P6 STOP generalized to ANY lane moving latest; slot re-derived live; memoir tip literals removed from assertions.

Also verified at base: baseline = 177 rows; T26 asserts default_queue=="system_parallel_queue" over FAMILY (canary needs
its own — plan sets it) and T27 over CHILDREN (canary's "coding" satisfies either constant choice); aegis meta
team_members = 4 today (T4 5-set correct) and the second team literal exists at :3399-3408.

### NOT blocking (recorded; the two highest-value one-line cleanups named for the implementer)
- phase4 :102 (an explicitly LABELED historical fold note) still says the new agents/ set is "the 12 files" while the
  authoritative table four lines below says 13 (AM-4's 9th canary file). Follow the table; gates re-derive.
- arch §5.2 T21 row + §11 item 6 parenthetical "installer children assert no-manifest; sweeper/canary positive-manifest" is
  self-contradictory as literal prose (sweeper IS an installer child). The binding resolution is phase4 (a2)'s two named
  tuples (DOCTRINE_ONLY / MANIFEST_BEARING) — implement that verbatim. Arch §5.2's blockquote table also omits those two
  target names while §5.2 calls the block "complete".
- decisions :765 (AM-2 row) lacks the "superseded by AM-4" marker its technical-analysis twin carries; technical-analysis
  :608 still says "Fixed by AM-2(a) ... inside the 20 000-char body cap". Both are fold-record cells; AM-4 corrections sit
  at decisions :377 and technical-analysis :790. The T37/G1.8 HARD 8,000 pin cannot be bypassed by believing them.
- decisions §C.5 keeps the name `LEAVES` as an explicit alias for FAMILY_ROSTER ("content identical, authoring lane fixes
  one name"); arch/phase4/overview consistently use FAMILY_ROSTER and state LEAVES does not exist. Alias is disclosed.
- phase3 :51 mentions "T27's extension to canary" while G4.4's closed diff list does not name T27 (its :707 CHILDREN
  reference IS in the B1 ledger). Either choice is green; G4.4's wording is narrower than the ledger.
- P6 task 8 / G6.2 still phrase the merged migration delta as memoir-specific ("+1 memoir's") although B15's STOP rule is
  generalized; the operative clause ("reasoned, not assumed … matches the actual merged delta") governs.
- phase3 :61 "no queue/model grants" vs task 1's required default_queue/llm_model: read as "no special grants" (task 1's
  explicit acceptance governs).

### Verdict
APPROVED. No genuine blocker remains: every iteration-001/002 defect is closed on disk; the plan's load-bearing factual
premises (8,000-char skill_view window, absence of a 20,000-char cap, $DATA_DIR-inside-repo, 366 agents files, 177
baseline rows, sweeper's missing manifest, aegis's 4-member team, base constants) were re-measured this pass and all hold.
The residual items above are labeled-record/prose residues that the mechanical pins (T37 HARD 8,000, G1.8, T31–T38,
M1–M8, S2/G4.4, G6.1b) cannot let through as a shipped defect.
