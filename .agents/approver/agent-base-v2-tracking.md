# Tracking: agent-base-v2 migration (agents-ensemble)

Plan: agent-base-v2 migration — promote agent v2 to THE base agents + retire ENTIRE opencode stack to legacy/ (BIG, 5 phases)
Artifact: /home/nea/ensemble-src-wt-agent-base-v2/.agents/shared/planning/agent-base-v2/
Worktree base: 885a19d4f (feature/agent-base-v2)

## Iteration 001 — REJECTED (2026-10-13T08:05Z)

Workers: 2 section-parallel, skill=plan-approval
- approve-worker-abc (716c1114-b118-41dd-844d-df6ec2d50be7): APPROVED, 0 blocking, 3 notes — Phases A+B+C structural migration verified 1:1 vs disk (roster, byte-copy mechanics, 3-layer v2 zero-touch, D5 live-dep isolation, Step 1.5 BLOCKING GATE ordering, dual-mode copy-then-verify)
- approve-worker-de-ovr (379bdcca-6275-429c-a5e5-fdb2e0807420): REJECTED, 4 blocking — Phases D+E + plan-overview + decisions + architecture-recommendation

### Blocking Issues (aggregated — all from de-ovr; none downgraded)

1. **Canonical proof recipe bug** — phaseE §5 Step 3 (lines 234-241): sed normalization `s|${name}/|base/|; s|[v2]/|base/|` produces `agents/developerbase/meta.json` ≠ base manifest `agents/developer/meta.json` → canonical fresh-user-equivalence proof FAILS on healthy tree (executed live by worker). Fix: `sed "s|agents/${name}[v2]/|agents/${name}/|"`.
2. **Whole-mission DoD unsatisfiable** — phaseE §6 (line 987), §9 (line 1034), §11 DoD #17 (line 1066): "no tests/** mutation" vs base 885a19d4f contradicts Phase C's own mandated edits (C.3 git-mv tests/opencode/ + C.4 in-place edits: test_tool_filter.py, test_jsonb_migration.py, test_innate_skills_refactoring.py, test_coder_agent.py:40-43, wanderer mirror, 3 mock files, tests/job_queue/conftest.py:65). Fix: reword to "no tests/** mutation beyond Phase C's enumerated C.3/C.4 edits" or drop tests/ from no-mutation list.
3. **Phase D DoD contradiction vs locked execution order** — phaseD §9 DoD (line 328) + §8 R6 (line 310): "git diff --stat vs 885a19d4f shows ONLY docs/" fails at D-time because cumulative diff necessarily contains A+B+C outputs (legacy/**, agents/<5 base>, daemon/**, tests/**, Makefile/pyproject). Fix: re-anchor to `git show --stat HEAD` / `git diff --stat HEAD~1..HEAD`.
4. **Phase D census gap: testing-guide.md omitted** — phaseD §4 File Inventory + Step 9 sweep (greps only docs/ README.md AGENTS.md) + Step 11 commit all omit root-level testing-guide.md, which the plan's own census (architecture-recommendation §11.8 + phaseE line 942) assigns to Phase D. File has 2 live opencode hits (:15 "requires live OpenCode server", :94 integration-marker row) — active testing guidance → mandatory FIX per binding residual-sweep rule. Miss → Phase E Step 13 fails late → rework loop. Fix: add to inventory/sweep/commit with :15/:94 edits.

All 4 fail CLOSED (false halt, never false success); all one-line-class mechanical edits; NO structural change required. Mission design, ordering, R10 gate, evidence pack, risk register, rollback all judged sound by both workers.

### Notes carried (non-blocking)
- ABC: N1 stale off-by-one count labels (phaseB Step 2 comment block); N2 phaseC §5 preamble vs §13.7 5-commit split (resolved by authoritative-source note); N3 phaseB Step 6 sed defensive no-op.
- DE+OVR: N1 "five" vs "seven" surfaces wording (phaseE 981/1029); N2 shasum vs sha256sum wording (TOOL.txt lock-in governs); N3 Makefile cite drift (:174,186 vs :173-174+:186 — R10 gate enforces regardless); N4 boot-scan env-var names (ENSEMBLE_POSTGRES_DB/ENSEMBLE_PORT not real surface names; recommend dev.sh-only boot + explicit resolved-DB assert); N5 skill-seed sqlite3 vs PG dialect hedge; N6 migrations append-only lacks explicit assert DoD row (cheap add); N7 conditional exclusions inside wholesale exclude (.kilo, docs/bugs — keep sub-step-5 cross-check mandatory).

### Next
Iteration 002 pending planner fix-pass on the 4 blockers. 2 rejections remain before escalation (max 3).

## Iteration 002 — REJECTED (2026-10-13T08:55Z)

Workers: 2 section-parallel, skill=plan-approval
- approve-worker-ab (744469d7-8c0f-4c16-a836-ec4be446d921): APPROVED, 0 blocking, 7 notes — plan-overview + decisions + architecture-recommendation + phaseA + phaseB verified against disk (HEAD 885a19d4f; v2 per-pair counts total 52; STACK-1/3/4/9/10/11 inventories match; v2 trees grep-zero; ordering constraint encoded 4×; copy-then-verify layered fail-closed)
- approve-worker-cde (8034d13a-6e2b-4f5e-97cf-e541e4a563af): REJECTED, 4 blocking — phaseC + phaseD + phaseE (proof recipes reproduced, gate command executed live, census counted)

### Iteration-001 blocker resolution check (post-verdict comparison)
- 001-B1 sed recipe: RESOLVED — CDE independently reproduced `sed "s|agents/${name}\[v2\]/|agents/${name}/|"` → 0 diff on byte-copy fixture
- 001-B2 DoD #17 wording: RESOLVED — bounded-by-enumerated-list reword verified at 4 sites (but see new 002-B4: the enumeration itself now contains wrong paths)
- 001-B3 phaseD diff anchors: RESOLVED — phase-scoped `git show --stat HEAD` / `HEAD~1..HEAD` anchors confirmed the only correct form
- 001-B4 testing-guide.md census gap: RESOLVED — not re-flagged; CDE's independent full-tree census surfaced different unaddressed files instead

### Blocking Issues (all from cde; none downgraded — each carries live-reproduction evidence)
1. **R10 FINAL TESTER GATE unsatisfiable as written** — phaseE §11 DoD #15 + Step 13 gate command (:917–933): documented `:(exclude)` set executed against actual worktree returns **2471 hits post-exclusion**; ≥11 residue categories acknowledged in allowlist/boundary text are NOT excluded by the gate (.agents/*/memories|working|EVIDENCE, active.md/tracking, .slim/, LESSONS/, docs/plans/, docs/test-suite-known-issues.md, agents/maintenancer/knowledge/, …). Fix: align gate `:(exclude)` set with the documented exemption set (incl. `design-systems/opencode-ai/**` contents + copy_freely/HASHES.sha256 handling). Fail-CLOSED (false halt at Step 13 after all prior work done).
2. **Census gap: docs/mcp-integration.md:364,366** ("Example: OpenCode Configuration") — live active-tree refs in NO disposition list (Phase D MANDATORY-FIX :184–195 ✗, allowlist :199–211 ✗, gate exclusions ✗) → leaks. Fix: add to Phase D MANDATORY-FIX.
3. **Census gap: agents/maintenancer/knowledge/01-architecture-overview.md** — live opencode refs; Phase C TOUCH (:689–706) covers only explorer/devops souls + _prompt_system/knowledge.md; Phase D MANDATORY-FIX ✗ → leaks. Fix: add to Phase C TOUCH.
4. **Phase E TOUCH/DoD #17 path errors** — cites `tests/mocks/send_message_fixtures.py:68` + `tests/test_spawn_default_unchanged.py:163`; actual files `tests/helpers/send_message_fixtures.py` + `tests/integration/test_spawn_default_unchanged.py` (Phase C §3/§13.5 has correct paths). 3 sites (phaseE header :31, §9.5 :660, §11 :1062) → bounded-footprint cross-check mismatches on a correct execution. Fix: correct the 3 path cites.

### Notes carried (non-blocking)
- AB: N1 keep verbose `:(literal)` pathspecs (do not "simplify" back to vacuous); N2 manifest arithmetic derivation block (16 = 1+2+3+2+7+1); N3 name-allowlist grep-guard in per-pair rollback; N4 "DO NOT use grep -r" callout at executor Step 13 (git grep is committed-tree); N5 Phase-B-landing red-window callout (innate-skills fixture red until C.4, same PR); N6 FAKE_HOME trap hardening (mktemp -p /tmp + trap EXIT INT TERM HUP + /tmp sweep); N7 line-count drift in plan-overview :70 (A 845→849, B 781→814 — credibility marker).
- CDE: `.agents` census drift 306 vs actual 316 files (correlates with B1 — W4 dry-run missed visible categories); KNOWN_TOOL_NAMES cite :764–771 → actual :765–772; vendored exclusion pattern `:(exclude)…/opencode-ai` without `/**` does not exclude directory CONTENTS; `copy_freely/HASHES.sha256` (24 opencode-ai rows) not excluded by any pattern.

### Next
Iteration 003 pending planner fix-pass on the 4 blockers. 1 rejection remains before escalation (max 3).

## Iteration 003 — APPROVED (2026-10-13T09:37Z)

Workers: 2 section-partitioned, skill=plan-approval (fresh prompts, ZERO iteration history — prior blockers had to re-surface on merits)
- approve-worker-gate-evidence (f5c266ff-b7eb-47f2-9d56-4773c54928ed): APPROVED, 0 blocking, 5 notes — phaseC+phaseE: R10 gate re-executed VERBATIM at HEAD 885a19d4f → 1,843 hits / 98 files exactly as documented; 26 MOVES + 72 TOUCH + 5 ALLOWLISTED reconcile to the byte; ZERO un-dispositioned residue; pathspec-set identity across the 3 authoritative restatements (arch-rec §11.3, phaseE §5.3, §5.5) verified by sort+diff; independent repo-wide sweep: 393 files w/ opencode → 98 in gate output + 295 covered by pathspecs; all cited paths/line-cites resolve incl. inner_soul.py R8, Makefile, pyproject, .env.example
- approve-worker-plan (0119360b-0827-45c6-9f5e-526078e501ec): APPROVED, 0 blocking, 7 notes — overview+phaseA/B/D+decisions+arch-rec: all mission requirements mapped to executable, independently checkable steps; ordering enforced by phaseC Step 1.5 BLOCKING GATE (not assumed); v2 zero-touch sha256-proven before/after; copy-then-verify layered fail-closed; migrations append-only asserted DoD-18; D1–D8 each carry a verifiable enforcement point; 18-row spot-check table vs worktree HEAD all confirmed (incl. agents/leader[v2] absent, tests/opencode/=12 files, daemon/opencode/=8 modules)

### Iteration-002 blocker resolution check (post-verdict comparison)
- 002-B1 R10 gate exclusion-set misalignment: RESOLVED — verbatim re-execution reproduces 1,843/98; decomposition reconciles exactly; independent sweep finds zero un-dispositioned files; gate satisfiable post-mission. Residual non-blocking: phaseC §13.8 mirror stale (31 vs 48 pathspecs) — disclaimed in-plan (phaseC:902 names phaseE Step 13 single source of truth)
- 002-B2 docs/mcp-integration.md census gap: RESOLVED — not re-flagged; independent full-tree sweep covers every file with hits
- 002-B3 agents/maintenancer/knowledge/01-architecture-overview.md census gap: RESOLVED — not re-flagged; same sweep coverage
- 002-B4 phaseE path errors (tests/mocks→tests/helpers, tests→tests/integration): RESOLVED — fresh path-resolution check found zero failing citations

### Notes carried (non-blocking)
- A1 phaseC §13.8 stale mirror: 17 load-bearing pathspecs missing from the mirror copy + narrower copy_freely scope — executor MUST use the phaseE §5.3/§5.5 verbatim command (plan already so directs)
- A2 plan-overview line-count drift (+4/+33/+70/+14/+182; no DoD pins line counts)
- A3 .agents census line-count drift 1,346→1,740 (plan-acknowledged; gate is command-based, not count-based)
- A4 tests/test_innate_skills_refactoring.py P1 cite subset is illustrative — exec-time grep is the executable source
- B1 Phase A/B manifest-helper drift (.pyc filter in A, absent in B; dynamic gate covers)
- B2 legacy/example/SKILL.md provenance recorded per D-A2
- B3 dual fix-pass taxonomy labels coexist (cosmetic)
- B4 per-commit invariants: phaseC table authoritative over plan-overview aggregate
- B5 R10 allowlist ~25 rows — executor must justify per row at execution (N9 fix-pass 2 step 5)
- AGG1 pathspec count label: worker A counted 48, worker B 49 — both independently verified SET IDENTITY across authoritative restatements; counting-method artifact, no set-membership disagreement; phaseE verbatim command governs

### Final
APPROVED at iteration 003 (budget fully consumed: 001 R, 002 R, 003 A). Plan execution-ready. Executor-bound unverified items are by-design future artifacts (evidence/ packs, post-mission gate output, fenced boot proofs). No promote as part of this plan.
