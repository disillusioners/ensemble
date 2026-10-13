# agent-base-v2 — Decision Ledger

Planner-maintained record of binding decisions (caller-issued) and planning-time decisions (worker-resolved, evidence-cited). Sources: user directives via leader 2026-10-13; leader decision set D1–D8; exploration addendum relay (VERDICT 1/2 + consolidated sets + ordering constraint); worker plan files in this directory.

## 1. User directives (binding, chronological)

| ID | Decision | Source |
|---|---|---|
| U1 | Binding clarification: KEEP BOTH base and v2. Base := a COPY of v2 content (base version=v2). v2 tree untouched, still the live path — existing users + ensemble's own operation must notice nothing; v2 checksums identical before/after proven. Only retirement: opencode-era base + comm service → legacy archive (copy-then-verify). Acceptance: fresh user on base == user on v2. Duplication/drift implications NOTED, not solved. | Mission dispatch 2026-10-13 |
| U2 | Scope addition: opencode session/API coding-worker integration lane (distinct from comm service per user) also retires — locate, remove from active use, copy-then-verify backup into the SAME legacy dir. | Leader relay 2026-10-13 |
| U3 | FINAL SCOPE STATEMENT (supersedes U2's two-lane framing): "Remove the opencode stack (related modules) in ensemble and backup to legacy." Unit of work = ENTIRE opencode stack (comm lane, session/API lane, shared modules, InstanceManager-coordinated OpenCode resources, MCP configs, agent-facing surfaces, sweep finds). BOUNDARY RULE: live-used dependency (e.g. coder) → isolate, keep module, flag with options (re-point to native vs retire agent) as USER decision item; dead deps removed with the rest. | Leader relay 2026-10-13 |

## 2. Leader decisions (D1–D8, binding on the plan)

| ID | Decision |
|---|---|
| D1 | Base replacement scope = the 5 agents with v2 counterparts (approver, developer, planner, reviewer, tidier); untagged base dirs content-REPLACED by byte-identical v2 copies; other 29 base agents + 4 infra dirs unchanged |
| D2 | Archive BEFORE overwrite: 5 opencode-era base dir contents + opencode stack → root-level `legacy/` mirroring source shape (precedent `backup/`, git-tracked; excluded from releases since stage.sh copies only enumerated trees) |
| D3 | Copies BYTE-IDENTICAL to v2 — NO copy-side edits (memory.md dual-contract prose travels as-is; noted wrinkle) |
| D4 | v2 ZERO-TOUCH — no file under `agents/*[v2]/` modified; proofs = per-file sha256 manifests before/after + `git status --porcelain` empty for v2 paths |
| D5 | `keyword_extraction` module STAYS in daemon/ (reused by attestation/graph/opendesign) |
| D6 | `docs/mcp-kb-usage.md` is a FALSE POSITIVE (upstream sst/opencode product) — untouched |
| D7 | Runtime DBs (`data*/opencode_sessions.db`) are boot-recreated state — untouched |
| D8 | No promote — rides next promote ceremony (fast pipeline); no live-system contact; read-only GET allowed only for default_agent_versions evidence |

## 3. Exploration addendum verdicts (authoritative, 2026-10-13)

| ID | Verdict |
|---|---|
| V1 | ONE retirement unit, TWO named roles: `daemon/opencode/` is a single package (port of opencode_skill Go binary); "comm service" = protocol layer inside the session lane (`server.py`; sole consumer `external_opencode.py:20-21`); single import graph / SQLite store / manager init (`manager.py:1427-1436`); `__init__.py:13` router claim STALE (zero mounts) |
| V2 | LIVE-DEPENDENCY: NO surface requires isolation → FULL-STACK REMOVAL. Evidence: coder opencode-free by design (contamination guards keep passing); opencode innate skill only in the 5 retiring base metas; zero tools.allow entries; runtime dead (passive wiring, recovery disabled `manager.py:3325-3338`, no granted caller); no MCP configs anywhere; port 4095 idle; `~/.opencode_skill`/`~/.opencode` absent; `keyword_extraction.py`+`context_injection.py` live-shared → STAY |
| OC | ORDERING CONSTRAINT: stack removal lands WITH or AFTER Phase B (innate-skill loader coupling), never before; Phase A pure-copy safe anytime |

## 4. Planning-time decisions (worker-resolved; definitive text in the cited phase plan)

| ID | Decision | Rationale (cited) | Source |
|---|---|---|---|
| D-A1 | 7 `opencode-native-tools` planning docs → `legacy/.agents/shared/planning/opencode-native-tools/` (planning/archive convention considered and REJECTED) | Co-location with the archived code preserves provenance; addendum-locked | phaseA |
| D-A2 | `example/SKILL.md` INCLUDED unconditionally (STACK-11) — the ported Go binary's contract spec (`name: opencode-skill`) | Addendum verdict: documents the opencode_skill API surface | phaseA |
| D-A3 | Hash tool = `shasum -a 256` exclusively; manifest line format matches `lib.sh:1928 _tree_manifest` / `:1944 _tree_hash_of_lines` | Evidence comparable with release tooling (`agents_tree_sha256`); `evidence/TOOL.txt` locks it | phaseA/phaseE |
| D-A4 | Archive transport = `cp -pR <src>/. <dst>/` per source path | Preserves perms+mtime+symlinks (D3 byte-identical); matches stage.sh copy family | phaseA |
| D-A5 | Preflight = manifest snapshot of sources (pre-archive) + of legacy dest (post-archive) → diff → archive | Two-snapshot verification; v2 captured before Phase B for D4 proof | phaseA |
| D-A6 | Classification LOCKED by addendum; executor does NOT extend the move-set — unexpected opencode-tied file = EXECUTION ANOMALY → halt Phase A + re-engage user | Never silently archive | phaseA |
| D-A7 | `retained-live` terminal state = EMPTY (schema kept for forward-compat) | V2 evidence | phaseA |
| D-A8 | Ordering constraint operationalized: Phase C wiring excision executes WITH or AFTER Phase B; Phase C implements Step 1.5 BLOCKING GATE (`diff -r` base vs `[v2]` empty ×5) before C.2 | OC | phaseA/phaseC |
| D-C1 | 5-commit sequence C.1 (wiring severance, bootable) → 1.5 gate → C.2 (stack removal, single commit) → C.3 (bulk move) → C.4 (TOUCH edits) → C.5 (boot proof) | No intermediate import-removed-module state; bootable checkpoints | phaseC |
| D-C2 | Branch machinery RETAINED but BRANCH 1 (full removal) selected by evidence; exception path documented as USER DECISION ITEM, never silent | U3 boundary rule + V2 | phaseC §13.3 |
| D-C3 | `tests/test_innate_skills_refactoring.py` + `tests/migration/test_jsonb_migration.py:70-107` EDITED IN PLACE (fixtures/import/row-count), not moved | Shared tests must keep running; opencode-specific rows drop 17→15 | phaseC C.4 |
| D-C4 | `tests/unit/test_coder_agent.py` — edit ONLY the `TOOL_CATEGORIES` mirror (:40-43); contamination guards (:144,:168,:369+,:510,:253) keep passing | Coder stays opencode-free by design | phaseC |
| D-C5 | `daemon/opencode/` removed as ONE unit (no module split) | V1 single package | phaseC §13.4 |
| D-D1 | Docs edit list: `docs/agents.md` §2 (+§3/§7 examples), `docs/skill-evolution.md:174`, writing-guide §9, CHANGELOG `[Unreleased]` (NO version bump — D8), conditional README/AGENTS.md on sweep | Docs-only boundary; code comments belong to C | phaseD |
| D-D2 | Docs-consistency classification = 4 buckets (stale→fix / false-positive→leave / historical→leave / intentional→leave); output feeds Phase E's audit | | phaseD |
| D-E1 | Evidence layout `evidence/{pre-mission-v2-manifests, post-mission-v2-manifests, base-vs-v2-diffs, legacy-reverify, boot-scan, default-agent-versions, skill-seed, test-wall, before-after-map, residual-references-audit, definition-of-done, rollback}` | | phaseE |
| D-E2 | Residual-references audit = 7 surfaces (incl. MCP-shaped files, docs/plans+bugs, `.agents/`); DoD = zero unclassified hits; MCP surface hit count = 0 | V2(d): no MCP configs exist | phaseE Step 9.6 |
| D-E3 | Whole-mission DoD = **17 criteria** (renumbered contiguous per fix-pass W6; was miscounted 15 pre-R10): ordering-constraint commit-graph check, coder test-wall invariant, R10 FINAL TESTER GATE + allowlist, no-live-system-contact; boundary-rule exception surfaces as OPEN USER DECISION if triggered | | phaseE §11/§12 |

## 5. Noted, not solved (per U1)

- Base = frozen copy of v2 → future v2 edits must be deliberately mirrored or base drifts.
- ~12 per-agent meta pin test files pin v2 values after Phase B (maintenance surface).
- `developer[v2]`/`reviewer[v2]` `memory.md` dual-contract prose travels as-is (D3 wrinkle).
- Repo-root stray `opencode_sessions.db` (orphan test artifact): record in evidence, do not delete.

## 6. Forward-compat (single open mechanism)

O6 (phaseE): if executor verification contradicts V2 (a live caller surfaces), a `retained-live` row is added to the before/after map AND a USER DECISION ITEM is filed — the exception DoD criterion is then OPEN, never implicitly done.

## 7. Architecture fold pass (2026-10-13 — source: `architecture-recommendation.md`, same dir)

Architect verdict: plan SOUND and execution-ready after amendments; TOUCH-set omissions and prose defects only; zero USER DECISION ITEMS; all load-bearing properties validated (v2 zero-touch, migrations append-only, legacy/ isolation, frozen-binary neutrality, git-only reversibility, D8).

| ID | Decision / disposition |
|---|---|
| U4 (R10) | BINDING user acceptance-criterion addition: active tree OPENCODE-FREE post-migration. Residual sweep upgraded REPORT→FIX for ALL agent surfaces; only survivors = legacy archive + historical/audit material via justified allowlist rows. Supersedes phaseC Tier-2 "optional" framing, P7 optionality, and phaseD's 4-bucket classification (only the explicit §11.3 allowlist survives as "leave"). D5 intact in substance (modules stay; wording mandatory). Tester GATE = phaseE Step 13 (`git grep -niE 'opencode'` + exclusion set → zero matches; `evidence/r10-allowlist.md` one row per exclusion + one-sentence justification; unjustified rows fail). Boundary cases: mcp-kb-usage.md strictly-upstream verify→allowlist; plugins/opendesign/copy_freely/design-systems/opencode-ai/** vendored third-party (stage.sh:407 payload — NOT a legacy move) verify+allowlist; .gitignore opencode lines intentional-keep (D7); .kilo/ executor-classified at gate time. §11.5 load-bearing PASSED: all five v2 trees grep-zero → gate satisfiable without D4 violation. |
| R1 | `tests/test_tool_filter.py` (:710-716,:750-764,:827-860; deny/no-op tests untouched) → phaseC C.4 TOUCH |
| R2 | `test_jsonb_migration.py` full-file surface (:68-69,:75,:106-107,:194-197,:223-226,:366-367,:497,:518; assert 17→15 pairs) → phaseC |
| R3 | `testpaths = ["tests"]` added to pyproject.toml — mechanism adjudicated testpaths NOT norecursedirs → phaseC C.4 |
| R4 | C.1/C.2 prose contradiction resolved in favor of edit lists; Step 1.5 rationale re-pointed to prompt-dir removal; §6.4 four sites; loader cite corrected to `daemon/loader.py` (warning :389) → phaseC |
| R5 | Makefile edit = rephrase :173-174 comment + :186 FILES → phaseC (own grep gate requires it) |
| R6 | C.3 zero-grep gate moved to C.4; per-commit invariants: C.1/C.2 boot-green only, C.3 move-only, C.4 FIRST test-green → phaseC |
| R7 | Pair enumerations fixed to the explicit five (developer/reviewer/tidier/approver/planner); `agents/leader[v2]` nonexistent; attestation-leader refs are name pins, unrelated → phaseC (+phaseB confirmed already clean) |
| R8 | Capture-before-boot: base==v2 equality proof (diff+manifests) BEFORE any boot proof — inner_soul (`daemon/tools/inner_soul.py:1043,:1107-1176`) writes base memory.md at runtime → phaseE. Interplay with R10 (§11.7): gate greps committed tree; the two checks independent, both stand |
| R9 | Pin list corrected: REAL pins `tests/test_innate_skills_refactoring.py:59-65,:227,:271,:299`; ⛔ EXCLUSION `tests/conftest.py:443` (HealthResponse daemon-version fixture — editing BREAKS it; negative-check required); de-listed non-pins: `test_agents_api.py:40,:173` (synthetic/API default), `:443` nonexistent, `test_registry.py:871` (synthetic leader), `test_models.py:276,:289` (HealthResponse) → phaseB + phaseE |
| P1–P8 | ALL ACCEPTED (planner discretion): P1 innate-skills-refactoring full site enumeration + accepted red window B→C.4; P2 wanderer mirror edit :41-44 (same as coder); P3 pyproject.toml:88 marker REPHRASE (registration row stays); P4 PACKS.md + regression_integration_test.sh:40 stale-history rows via allowlist; P5 content-only-manifest informational note (phaseA/B/E); P6 Phase C reverts as a UNIT (partial revert = manager.py:78 ImportError); **P7 SUPERSEDED to MANDATORY by R10** (former-optional cosmetic stragglers now required); P8 instance.py factory cite :5815-:5819 |
| RR11–18 | Risk-register rows 11–18 added (test wall, collection, bare-pytest, proof invalidation, pin misattribution, prose contradiction, pair list, revert atomicity) → phase plans + this overview (rows 11–19 incl. R10 gate row) |

## 8. Fix pass (2026-10-13 — review verdict REJECTED-for-execution-readiness; all mechanical text fixes; no structural change)

Review found 2 critical + 6 warnings + 8 suggestions; ALL applied (folds confirmed per-worker with grep evidence):

| ID | Fix |
|---|---|
| C-1 | phaseC move-vs-edit contradiction (6 stale sites vs authoritative §13.5/§13.7 keep-in-tree design for `test_innate_skills_refactoring.py` + `test_jsonb_migration.py`): §9 DoD #13 rewritten to "edited in place, zero opencode refs post-C.4"; §5 Step 3 git-mv lines deleted; §3 Phase-A-produces row corrected (STACK-1..11 only); §2/§4.2/§10 scrubbed; superseded-by-§13 notes at §5/§9 heads |
| C-2 | phaseB inventory counts corrected to disk truth (developer=8, planner=11, reviewer=14, tidier=10, approver=9, TOTAL 52; was off-by-one on 4/5 pairs) + gate converted to dynamically derived per-pair counts (base==v2 count equality at runtime) so it cannot drift; 49/=50 self-contradiction resolved |
| W1 | phaseA DoD-A1: `find legacy -mindepth 1 -maxdepth 1` (was recursive ~88 entries vs "21" → false halt); exact top-level entry count stated |
| W2 | phaseB Step 6b: `rm -f "agents/$name/$rel"` (was worktree-root deletion → base-only files silently survived) |
| W3 | phaseB Step 8a: literal pathspecs ×5 `:(literal)agents/<name>[v2]` (was vacuous glob character-class); sha256 manifests remain primary zero-touch proof |
| W4 | R10 gate exclusion set EXPANDED (pre-authorized, each + allowlist row + justification per the user's historical/audit-material exemption): `.agents/shared/planning/**`, `.agents/*/RESULTS*`, `.agents/*/LESSONS*`, `.agents/tester/PACKS.md`, `.kilo` — dry-run at HEAD showed ~1,600 residual lines (1,346 under .agents; .kilo 66 lines not "3 files") under the prior set. Verbatim command updated consistently at all 3 sites (phaseE Step 13 canonical; architecture-recommendation.md §11.3 edited IN PLACE with a stamped fix marker per leader authorization; phaseC §13.8 cross-reference) |
| W5 | phaseE wrong-file cite ×3 corrected: `INNATE_SKILL_TOOL_CATEGORIES` lives at `daemon/tools/instance.py:155-167` (not constants.py) |
| W6 | phaseE §11 DoD renumbered contiguous 1-17 (#7 was missing); D-E3 updated to 17 |
| S1–S8 | All folded: S1 manifest 16 + label scrub; S2 risk #13 dedupe + register alignment; S3 rollback exception note; S4 ensemble_dev DB pre-flight; S5 literal 26-site count; S6 DoD-A11 gitignore RESOLVED keep (R10 §11.4 #3); S7 never-`git clean -fdx` caution (phaseA/E); S8 evidence scaffold intentionally-unpopulated note |

## 9. Fix pass 2 (2026-10-13 — approver iteration 001 REJECTED → all 4 blocking + 9 notes applied; tracking .agents/approver/agent-base-v2-tracking.md)

| ID | Fix |
|---|---|
| B1 | phaseE §5 Step 3 canonical proof recipe corrected: single asymmetric `sed "s|agents/${name}\[v2\]/|agents/${name}/|"` (old two-pass recipe could not normalize v2 paths → guaranteed false mission-failure). **Independently reproduced by planner: 0 diff lines ×5 pairs on byte-copy fixtures** |
| B2 | Whole-mission DoD tests-boundary reworded at 4 sites (§6 :1014-1015, §9 :1062, §11 #17 :1094): "tests/** mutations bounded to Phase C's enumerated C.3/C.4 list — NOT a defect; any mutation BEYOND the list is a defect" (option: enumerated cross-ref) |
| B3 | phaseD §8 R6 + §9 DoD re-anchored to phase-scoped `git show --stat HEAD` / `git diff --stat HEAD~1..HEAD` (cumulative-diff check necessarily fails at D-time) |
| B4 | `testing-guide.md` (:15, :94 LIVE opencode hits — active guidance, NOT allowlist-eligible) added to phaseD inventory/Step-0/Step-9 sweep + classification + Step-11 commit + DoD |
| B1–B4 / N1–N9 | (see §9 above — all applied and verified) |

## 10. Convergence pass (2026-10-13 — approver iteration 002/3; planner-independent gate execution drove two disposition rounds)

| ID | Decision / disposition |
|---|---|
| 002-B1 | R10 FINAL TESTER GATE aligned with the FULL documented exemption set (48 pathspecs). Iteration-002 worker execution: prior set left 2,471 hits; aligned set leaves 1,843 hits / 98 files at HEAD. New exclude classes: agent working-state (`.agents/*/memories|working|EVIDENCE`), approver instance/tracking state (6 named files), `.agents/reviewer/**`, tidier/tester historical notes, `.slim`, root `LESSONS`, `docs/plans`, `docs/test-suite-known-issues.md`, `docs/optimization`, vendored subtrees `plugins/opendesign/{copy_freely,snapshot_with_drift_alarm}/**` (provenance-verified), `.kilo` — each with allowlist row + justification. |
| 002-B2 | `docs/mcp-integration.md:364,:366` → Phase D MANDATORY-FIX (active doc; not allowlist-eligible) |
| 002-B3 | `agents/maintenancer/knowledge/01-architecture-overview.md:6,:16` → Phase C TOUCH C.4 (agent-facing surface; not allowlist-eligible) |
| 002-B4 | phaseE path cites corrected ×6 (`tests/helpers/send_message_fixtures.py`, `tests/integration/test_spawn_default_unchanged.py`) |
| NEG-GUARD CLASS | Structural semantics documented in-plan: negative-guard tests (coder/devops/reviewer_v2 — the opencode STRING is the assertion) reach gate-zero ONLY via justified allowlist excludes (3 files / 78 hits). NOT masked; stated plainly for the approver. |
| CONVERGENCE-R1 | Planner-independent gate execution caught a FALSE empty-residue claim (worker bucket (iii) had enumerated already-excluded files, masking 16 uncovered test files + 2 vendored leaks). Two disposition rounds resolved: 12 files/21 sites → Phase C C.4 TOUCH rephrase; 6 docstring sites → C.4 cosmetic; 3 guard files → allowlist; 2 vendored → exclude. Final planner verification: 48 pathspecs → 1,843 hits / 98 files → 26 MOVES + 72 TOUCH + **0 residue**. |
| RECONCILIATION | Cross-worker classification divergences resolved in favor of edit-over-exclude: `test_spawn_instance_instructive_errors.py` + `test_prompt_section_reference_integrity.py:815` → TOUCH (editable strings); `test_persistence_w1_markers.py:15` exclude DROPPED (docstring edited instead). `test_prompt_section_reference_integrity.py` is never excluded — it IS the integrity sweep. |
