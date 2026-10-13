# Architecture Recommendation — agent-base-v2 Plan Enrichment

- **Date:** 2026-10-13 (06:20Z dispatch → ~07:0xZ delivery)
- **Architect instance:** this architect (controller); validation executed by 3 skill-equipped workers:
  - `architect-worker-severance-boundaries` (structural-design) — Focus 1+3
  - `architect-worker-excision-safety` (resilience-design) — Focus 2+5+7+8
  - `architect-worker-payload-testwall` (data-flow-design) — Focus 4+6
- **Scope:** validation/enrichment of the approved agent-base-v2 migration plan against worktree `/home/nea/ensemble-src-wt-agent-base-v2` @ `885a19d4f` (branch `feature/agent-base-v2`). Read-only; no plan files modified; no code changes.
- **Method note:** all claims below carry worktree file:line evidence; 5 findings were independently converged by 2–3 workers; the architect spot-verified the 7 most load-bearing novel claims (all confirmed). Three workers read the plan independently; every worker re-grepped cites in the worktree.

---

## 1. Overall Verdict

**The plan is architecturally SOUND and execution-ready after 9 REQUIRED amendments.** The mission's load-bearing safety properties — v2 zero-touch, migrations append-only, `legacy/` scope isolation, frozen-binary neutrality, git-only reversibility, no-promote (D8) — all VALIDATED against the code. Every gap found is a **TOUCH-set omission or prose/ordering defect**, not a structural flaw: no binding decision (KEEP BOTH, DEAD live-dependency verdict, D1–D8) is challenged, and **zero USER DECISION ITEMS** arose (no contrary evidence against VERDICT 2 was found; worker A independently re-confirmed zero router mounts, zero `tools.allow` entries, coder opencode-free).

## 2. Focus-Area Validation Matrix

| # | Focus area | Verdict | Key evidence |
|---|---|---|---|
| 1 | Phase C severance design (5-commit bootability, gate placement, wiring inventory) | **GAP — fixable by amendment (R4, R5, R6, R7)** | All 8 manager.py site-groups + instance.py + _tool_registry.py + Makefile cites verified at/near stated lines; zero missed **daemon** sites; zero router mounts; contradiction is prose-only (edit lists authoritative) |
| 2 | Excision safety (KNOWN_TOOL_NAMES, migrations, jsonb) | **VALIDATED + 1 test-file gap (R2)** | `daemon/tools/_tool_registry.py:603,:764-771`; drift-detector test `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242` stays green; frozen binaries carry baked sets; `daemon/migrations/` zero opencode hits; `manager.py:6750-6751` removal behavior-neutral |
| 3 | Discovery/scope safety of `legacy/` | **VALIDATED + 1 hardening (R3)** | `AgentRegistry.discover` scans `agents/` only (`daemon/registry.py:552-575`, SKIP_DIRS :15-20); integrity test scoped to `agents/` (`tests/unit/tools/test_prompt_section_reference_integrity.py:46-63,138`); stage.sh allowlist `:395-424` excludes legacy/ by construction; `ensemble.spec` clean; **no CI exists** (`.github/` absent) |
| 4 | Release payload implications | **VALIDATED — no amendment** | `_tree_manifest` content-only + sorted (`lib.sh:1928-1935`), self-adapting recompute; zero pinned `agents_tree_sha256` literals in tests/scripts; `integrity_verify` reads the STAGED release tree (`lib.sh:2759-2840`) — repo changes invisible until next promote; no new migrations → rollback_safe derivation untouched |
| 5 | Invariants & proof design | **VALIDATED + 1 ordering hazard (R8)** | Zero `__pycache__`/symlinks/`.bak` under v2 dirs today (the one `.bak` is the tracked innate-skills one, in the MOVE set); `.gitignore` covers all boot side effects; `diff -r`+sha256+porcelain jointly close the content hole; hole found: runtime `memory.md` mutation by inner_soul |
| 6 | Test-wall strategy | **GAP — R1, R2, R9 + P1–P3** | Coder mirror is a static fixture never compared to the live map (C.4 timing safe); contamination guards stay green; pack moves unreferenced by any runner; markers non-strict; but `tests/test_tool_filter.py` is entirely missed, phaseB's pin list is largely misattributed |
| 7 | Risk register | **VALIDATED + additions below** | 8 new risk rows (§5) |
| 8 | Rollback | **VALIDATED + granularity caveat (P6)** | Every removal git-tracked; legacy/ additive; runtime DBs untouched (D7); **no irreversible operation found**; whole-C revert is the only safe C-rollback unit (reverting C.2 alone → `manager.py:78` ImportError) |

## 3. Confirmed-Safe Foundations (no action needed)

1. **KNOWN_TOOL_NAMES excision is safe in all three planes.** Source plane: after C.1+C.2 the AST source scan (`discover_source_only_tool_names`, `_tool_registry.py:428-458` raises on nothing-found; merge path `:471-503`) and the static set agree → the bidirectional drift test (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) passes. Frozen plane: PyInstaller binaries carry their own baked set; release tooling (`integrity_verify`, journal) validates tree shas only, never intra-binary identifiers — existing live/demo installs are untouched by the repo-side removal. Ledger plane: zero `KNOWN_TOOL_NAMES` references in upgrade tooling. The 4-site removal discipline (CATEGORY_MODULES row + module-map + KNOWN_TOOL_NAMES + factory call) is even documented in-repo at `daemon/tools/upgrade_tools.py:141` — C.1 satisfies it in one commit.
2. **Migrations are untouched and must stay so — confirmed.** `grep -rniE 'opencode' daemon/migrations/` → zero hits; no plan MOVE/TOUCH entry touches `daemon/migrations/`; `test_jsonb_migration.py` is a test, not a migration. `manager.py:6750-6751` rows sit in the `_ensure_postgres_columns()` DO-block string (`:6748-6770`): existing DBs already converted (0 rows match), fresh DBs never create `opencode_sessions` post-C.2 → removal is behavior-neutral on both PG and SQLite.
3. **`legacy/` cannot leak.** Not discoverable as agents (registry iterates `agents/` only), not in release payloads (stage.sh enumerated allowlist), not walked by the prompt-integrity test (agents/-scoped), not referenced by `ensemble.spec`, and no CI exists to trip.
4. **D8 holds.** No phase adds a migration file; CHANGELOG edit only; nothing trips promote machinery or the rollback_safe destructive-DDL derivation.
5. **`default_agent_versions` is content-change-blind.** Resolution keys on names/tags (`daemon/registry.py:34-49, :730-735, :904-940`; `daemon/tools/instance.py:621-651`); existing `{developer: "v2"}` rows resolve identically; base fallback now equals v2 → no observable change. Skill-seed stays a version-equal no-op (`skill_seed_service.py:285-292, :436-450`).
6. **The loader coupling is graceful, not fatal.** `expand_allow_for_innate_skills` uses `.get(skill, [])` (`instance.py:171+`) — missing map row = silent no-op; missing skill dir = `logger.warning` at `daemon/loader.py:389` (contains the skill name — relevant only if an agent still declared "opencode", impossible post-B). Post-B all 5 v2 metas declare `["todo","chart","dynamic-skill"]` (disk-verified). **The real loader is `daemon/loader.py` — the plan's §13.7 cite `daemon/services/inheritance/loader.py` is a nonexistent path (fix under R4).**

## 4. REQUIRED Amendments (plan-breaking or actively harmful if unfixed)

> Numbered R1–R9. R = fold into the phase plans before execution.

**R1 — Add `tests/test_tool_filter.py` to phaseC §13.5 TOUCH set (C.4).** Three tests assert opencode innate-skill expansion and go RED at C.1 (map-row strip): `:710-716` `test_opencode_innate_skill_adds_external_opencode_category` (`assert "external_opencode" in result`), `:750-764` `test_multiple_innate_skills`, `:827-860` `test_apply_tool_filter_grants_opencode_tools_for_innate_skill`. Rephrase to a surviving skill (e.g. `"chart"`/`"test-skill"`). The deny test `:862-887` and no-op tests `:707, :717-722` pass coincidentally — no edit. *(Converged: workers A+C; architect-verified.)*

**R2 — Expand the `tests/migration/test_jsonb_migration.py` edit spec beyond `:70-107`.** The file's opencode surface is larger than the plan's cite range; post-C.2 the module-level import at `:75` breaks pytest COLLECTION for the whole file, and these additional sites break at runtime: `:194-197` and `:223-226` (fixture bodies calling `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)`), `:497` (second method-scoped import), `:518` (`"opencode_sessions": OpenCodeSessionRecord` in `table_to_class`), `:366-367` (embedded `_DO_BLOCK_SQL` rows that mirror `manager.py:6750-6751` — must be edited in sync per the test's own header comment), `:106-107` (the 2 pair rows the plan already covers), `:68-69` (comment). Spec the edit as a full-file re-grep with this enumerated site list, not just `:70-107`. *(Worker B + architect-verified `:194-197/:223-226/:497/:518/:366-367`.)*

**R3 — Add `testpaths = ["tests"]` to `pyproject.toml [tool.pytest.ini_options]` (C.4).** The repo has **no `testpaths` and no `norecursedirs`** (`pyproject.toml:86-95`); after C.3, a bare `pytest` from repo root collects `legacy/tests/opencode/**` (12 `test_*.py` + conftest) → `ModuleNotFoundError` collection errors (`daemon.opencode` deleted in C.2). The mission's own commands are explicit-path (`pytest tests/`) so this is not mission-blocking, but it is a guaranteed post-mission dev-workflow landmine. Mechanism adjudication: **`testpaths`, not `norecursedirs`** — `norecursedirs` REPLACES pytest's default ignore set (footgun); `testpaths` is one additive line and also stops today's accidental bare-pytest collection of `test/packs/*.py`. *(Converged: workers A+B+C — only the mechanism choice differed; A's adjudication adopted.)*

**R4 — Resolve the C.1/C.2 prose contradiction in favor of the edit lists.** §13.7's C.1 edit list (`:713-716`) includes the instance.py grant-map row + Makefile; C.2's prose (`:745`) claims both were "held back from C.1" while C.2's actual edit list (`:747-751`) is git-rm-only. The edit lists are self-consistent; **the C.2 prose sentence is the stale artifact**. Code adjudication: the grant-map strip is boot-safe in every state (`.get` default — §3.6 above), and C.1's boot pre-condition (`:718`) already requires Phase B, so nothing needs to move. Edit: rewrite C.2 prose to drop the "held back from C.1 / finalizes the Makefile / drops the grant-map entry" clause; re-point Step 1.5's rationale from "grant-map" to "prompt-dir removal" (the genuinely agent-visible edit the gate fronts); fix §6.4's "ALL THREE" (it enumerates four sites); correct the loader cite `daemon/services/inheritance/loader.py:226` → `daemon/loader.py` (the former does not exist). *(Worker A; loader semantics architect-verified.)*

**R5 — Makefile edit must include the comment line.** The plan cites `Makefile:174,186` for dropping the `FILES=` entries, but `:173-174` is a **comment** ("…and opencode_sessions.db (always a local SQLite…") and `:186` is the `FILES=` line. Dropping only the FILES entries leaves the comment → the plan's own gate `grep -n 'opencode' Makefile → 0` (C.4 verification + DoD #10) FAILS. Spec: rephrase `:173-174` comment + edit `:186`. *(Worker A; architect-verified.)*

**R6 — Move C.3's zero-grep gate to C.4 and declare per-commit invariants.** §13.7:773's C.3 gate (`grep -rn 'opencode_sessions\|create_opencode_tools' daemon/ tests/` → 0) **cannot pass at C.3's own boundary**: hits legitimately remain in `test_jsonb_migration.py` (:75,:106-107 — edited in C.4), `test_innate_skills_refactoring.py` (:59-65 — C.4), `test_tool_filter.py` (R1 — C.4), and the 15 mock-strip files (C.4). Move the gate to C.4 and state the invariants explicitly: **C.1 = boot-green only** (15 mock sites + R1 tests are runtime-red), **C.2 = boot-green only** (`tests/opencode/` collection ImportError until C.3; `test_jsonb_migration.py` until C.4), **C.3 = move-only, no invariant**, **C.4 = first test-green commit**. The plan's §5 preamble "bootable at every checkpoint" remains accurate — make the deferral of test-green to C.4 explicit instead of implicit. *(Worker A; corroborated by B's collection analysis.)*

**R7 — Fix the Phase-B pair list: `agents/leader[v2]` does NOT exist.** Disk truth: the v2 siblings are `developer[v2], reviewer[v2], tidier[v2], approver[v2], planner[v2]` — no `leader[v2]`. §5 Step 0's diff loop (`:217`, includes leader) is stale and would hard-stop on the missing dir; §13.7 Step 0's "all 5 pairs + planner" (`:704`) is ambiguous (5+1?). Replace both with the explicit five pairs `developer/reviewer/tidier/approver/planner` (matches Step 1.5 `:734`, which is correct). *(Worker A; architect-verified `agents/leader[v2]` absent.)*

**R8 — Phase E must capture the base==v2 proof BEFORE any boot proof.** `daemon/tools/inner_soul.py:1043, :1107-1176` writes to `agents/<agent>/memory.md` at runtime. Post-B, base `memory.md` == v2 `memory.md`; any boot that instantiates a base agent (and runs inner_soul) mutates it → `diff -r` equality proof breaks for a NON-architectural reason. Make capture-before-boot an explicit ordering rule in phaseE (diff + manifests first, boot scans after). *(Worker B.)*

**R9 — Correct phaseB's "~12 meta pin test files" list — it is largely misattributed.** Verified dispositions: `tests/conftest.py:443` is the `sample_health_response_data` **HealthResponse/daemon-version** fixture (`:437-444`; daemon reports `0.20.1` per `daemon/__init__.py`) — flipping it to "2.0.0" would ACTIVELY BREAK the test's semantics; `tests/test_agents_api.py:40` writes a synthetic test-agent version and `:173` asserts the create-agent API's `"1.0.0"` default backfill (`daemon/routers/agents.py:119,:135`) — neither pins developer; `tests/test_agents_api.py:443` does not exist (file is 383 lines); `tests/test_registry.py:871` is a synthetic leader fixture (leader not in migration set); `tests/test_models.py:276,:289` are HealthResponse. The **real** base-agent pin set is concentrated in `tests/test_innate_skills_refactoring.py`: `:59-65` (the five `(agent, ["opencode","chart","todo"], …)` tuples), `:227`/`:271` (loose "opencode" loader examples), `:299` (`real_registry.find_skill("opencode")` — returns empty post-B, needs rephrase). Rewrite the phaseB pin list to these sites. *(Converged: workers B+C with identical dispositions; architect-verified conftest.)*

## 5. New Risk-Register Rows (add to plan-overview §Risk register)

| # | Risk | Sev | Mitigation (amendment) |
|---|---|---|---|
| 11 | `tests/test_tool_filter.py` 3 tests red at C.1 (missed TOUCH site) | 🔴 | R1 |
| 12 | `tests/migration/test_jsonb_migration.py` collection ImportError post-C.2; opencode surface extends beyond `:70-107` | 🔴 | R2 + R6 invariant statement |
| 13 | Bare `pytest` post-C.3 collects `legacy/tests/**` → ImportError storm | 🟡 | R3 (`testpaths`) |
| 14 | Phase E proof invalidated by inner_soul runtime writes to base `memory.md` | 🟡 | R8 capture-before-boot |
| 15 | phaseB pin-list misattribution → executor makes harmful edits (conftest HealthResponse) or chases nonexistent lines | 🟡 | R9 |
| 16 | C.2 prose contradiction misleads executor into moving grant-map/Makefile edits | 🟡 | R4 |
| 17 | Stale §5 Step 0 pair list (`leader[v2]` absent) → false STOP at gate | 🟡 | R7 |
| 18 | C-revert non-atomicity: partial Phase-C revert (e.g. C.2 only) leaves `manager.py:78` ImportError | 🟢 | P6 (document whole-C-only) |

## 6. RECOMMENDED Amendments (hardening, non-blocking)

- **P1** — Enumerate all opencode sites in `tests/test_innate_skills_refactoring.py` (17+: `:11,:14,:59-65,:85,:94,:98,:227,:234-236,:271,:275,:280,:297-301,:353`) in the C.4 spec, and add one sentence noting the file goes red at **Phase-B landing** (fixtures expect opencode-era base metas) — accepted red window B→C.4.
- **P2** — For coder-parity hygiene, give `tests/unit/test_wanderer_agent.py:41-44`'s stale `external_opencode` mirror the same edit as coder's `:40-43` (verified self-contained and green either way — mirror is passed explicitly, never compared to the live map).
- **P3** — `pyproject.toml:88`: **rephrase** the integration marker description instead of deleting the registration row — 5+ files use `@pytest.mark.integration` (e.g. `tests/e2e/test_mcp_tools.py:47` pytestmark); no `--strict-markers` exists so deletion only produces `PytestUnknownMarkWarning` noise, but rephrase is strictly better.
- **P4** — Phase D row: `.agents/tester/PACKS.md:922,:957,:1272,:2157` + `test/packs/regression_integration_test.sh:40` reference the moved pack basenames → stale-history classification (non-blocking; no runner enforces presence).
- **P5** — Informational note in phaseB/phaseE proof docs: the release manifest is **content-only** (`lib.sh:1928` — no modes/mtimes); `cp -pR` preserves modes but nothing verifies them — "content identity ≠ proven-by-modes", so a future maintainer doesn't over-trust the manifest.
- **P6** — Rollback doc: state that Phase C rolls back as a **unit** (C.1↔C.2 are mutually broken in partial states: wiring-without-module = import-dead-code; module-gone-with-wiring = ImportError at `manager.py:78`).
- **P7** — Fold verified behavior-neutral cosmetic mentions into C.4/Phase D: `instance.py:5823,:5860,:6246` trailing comments + grant-map header `:155-156`; `tests/job_queue/conftest.py:65`; `tests/unit/services/test_critical_notes_phase2_filters.py:11`; `test_auth_boot_preflight.py:20`; `test/packs/lcan_helpers/mock_llm.py:141-142`; `test/packs/regression_integration_test.sh:31,:40`; `regression_chat_source_integration_test.sh:8`.
- **P8** — Cite correction: instance.py factory block spans `:5815-5819` (`tools.extend` sits at `:5819`, one past the plan's range end).

## 7. Rollback & Irreversibility Ruling (Focus 8)

**No irreversible operation exists in the plan.** Every removal is a git-tracked `git rm`/`git mv`; `legacy/` writes are additive `cp -pR` copies (git-tracked, `backup/` precedent); runtime DBs are never touched (D7, gitignored at `.gitignore:70+`); boot-proof side effects (`data/`, `__pycache__/`, logs) are gitignored so rollback is never blocked by a dirty tree. Rollback sequence reverse D→C→B→A + whole-mission fresh-branch-from-`885a19d4f` is **adequate**, with two annotations: (a) Phase C reverts as a unit (P6); (b) reverting A alone orphans C/B (broken states) — the plan's "A last/skippable" note covers the forward direction; mirror it for the reverse.

## 8. User Decision Items

**NONE.** All three validation lenses independently re-confirmed the DEAD live-dependency verdict (VERDICT 2 / BRANCH 1): zero router mounts re-verified across `daemon/api.py` + 30 routers; zero `tools.allow` opencode entries; coder opencode-free with guards green; manager wiring passive; no MCP configs; nothing to escalate under the boundary rule.

## 9. Notes (per binding directive — noted, not solved)

- Base = frozen v2 copy → deliberate mirroring required on future v2 edits (drift surface).
- R8's inner_soul finding slightly sharpens the D3 wrinkle: base `memory.md` is not just prose-dual-contract — it is **runtime-mutable**; fresh-user equivalence proofs must account for that (capture ordering covers this mission).
- Duplication of `skill-set.yaml` + `skills-template/` into base doubles the skill-seed surface (no-op verified today; future skill edits must land in both).

## 10. Evidence Quality

3 workers × independent full-cite re-greps; 5 findings converged by ≥2 workers (test_tool_filter, pytest collection, pin-list misattribution, jsonb-test surface, C.1/C.2 contradiction); architect spot-verified 7/7 sampled novel claims (`test_tool_filter.py:710/:750/:827`, `expand_allow_for_innate_skills` `.get` default, jsonb `:194-197/:223-226/:497/:518/:366-367`, `agents/leader[v2]` absence, `conftest.py:437-444` HealthResponse, `loader.py:389` warning, `Makefile:173-186`). Static analysis only (read-only fence); runtime boot/pytest behavior deferred to the executor's fenced dev-lane proofs per plan risk #11.


---

## 11. R10 — Opencode-Free Active-Tree Gate (binding user directive, appended 2026-10-13)

> **Directive:** post-migration, ANY active-agent-surface opencode reference (definition, prompt, tool config, skill, workflow doc, any agent-facing surface) is a **DEFECT → FIX**, not a note. Phase E's "what still knows about legacy paths" sweep upgrades **REPORT → FIX** for all agent surfaces. Only acceptable survivors: (a) the legacy archive itself, (b) historical/audit material. Tester grep-verifies as a **GATE**. This EXTENDS R5 (Makefile comment) and R6 (C-boundary gate) to the whole active tree as the final Phase E gate. It does not contradict R1–R9. **It SUPERSEDES:** phaseC Tier-2's "optional / if unsure LEAVE" framing, recommendation P7's optional framing, and narrows Phase D's 4-bucket classification (only the explicit allowlist below survives as "leave").

### 11.1 Semantics upgrade
Stale opencode references in **active** files are defects. The prior plan classes "Tier-2 comment cleanup (optional)" and Phase D "stale→fix / historical→leave" collapse into: **FIX unless allowlisted**. D5 is unaffected in substance — it protects the *modules* `keyword_extraction.py` / `context_injection.py` (code stays); their *comment/docstring/log-string wording* now moves from optional-rephrase to mandatory-rephrase (behavior-neutral edits).

### 11.2 Mandatory-fix set (all in TOUCH sets; now REQUIRED, not optional)
- `agents/explorer/soul.md`, `agents/devops/soul.md` one-liners; `agents/_prompt_system/knowledge.md:96,:106` (verified present — live prompt-system prose every agent reads).
- Daemon comment/docstring/log-string rephrases: `daemon/config.py:168`, `daemon/services/keyword_extraction.py` (docstring/log mentions — module stays per D5), `daemon/services/context_injection.py:541,:846`, `daemon/services/context_messages.py:35,:39`, `daemon/services/critical_note_gate.py:8`, `daemon/services/waiting_children_watchdog.py:9,:752`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777` (the D5 "may not churn" hesitation is superseded for this comment-only cite; code untouched).
- `Makefile:173-174` comment + `:186` FILES (R5). `pyproject.toml:88` + `.env.example:39` (plan TOUCH). `tests/job_queue/conftest.py:65`, docstring stragglers, pack-comment stragglers (former P7 list).
- Phase D docs: `docs/agents.md`, `docs/skill-evolution.md:174`, README/AGENTS/writing-guide §9 — every active-tree doc row that is not historical/audit.

### 11.3 Gate mechanics (final Phase E tester gate; new DoD criterion)

**[FIXED per review W4 2026-10-13 — exclusion set expanded; dry-run showed ~1,600 residual lines under .agents/.kilo]**
**[FURTHER EXPANDED per iteration 002 B1 fix-pass 3 2026-10-13 — worker executed gate on worktree HEAD: 2,471 hits post-exclusion; expanded exclude set to include .agents/*/memories/working/EVIDENCE, .agents/approver/{active,agent-base-v2-tracking,opencode-native-python-tools-tracking,rename-coder-to-developer-tracking,context-injection-restructure-tracking,tester-skill-evolution-tracking}, .agents/reviewer/, .agents/tidier/notes.md, .agents/tester/QUARANTINE.md, .slim, LESSONS, docs/plans, docs/test-suite-known-issues.md, docs/optimization, copy_freely/** (full vendored subtree, not just design-systems/opencode-ai), and added the HASHES.sha256 vendored hash file under a separate path. Decomposition of the remaining 1,926 hits: every file is in MOVES (legacy/, excluded post-C.3), TOUCH (Phase B replaces, Phase C.4 edits, Phase D fixes), or allowlist (historical/audit/working-state); no un-dispositioned residue.]**
**[CORRECTED per iteration 002 B1 fix-pass 3 — approver flagged that the prior pass-3 bucket (iii) enumerated files the pathspecs ALREADY exclude (masking 23 residue files / ~69 hits in NEITHER bucket); added the missing 3 negative-guard files (test_coder_agent.py, test_devops_agent.py, test_reviewer_v2_agent.py) + 2 vendored snapshot files (snapshot_with_drift_alarm/{system,media-contract}.ts) to the exclude set. Re-executed the corrected gate: 1,842 hits / 97 distinct files. CORRECTED decomposition: bucket (i) MOVES = 26 files / 1,457 hits; bucket (ii) TOUCH = 71 files / 385 hits (incl. P7 stragglers + 13 new TOUCH from this pass); bucket (iii) ALLOWLISTED via pathspecs = 6 files / 84 hits (NOT in output; documented for audit). Total: 103 files / 1,926 hits; zero un-dispositioned residue in the gate output.]**
**[RECONCILED per iteration 002 B1 fix-pass 3 — sibling convergence: dropped `':(exclude)tests/integration/test_persistence_w1_markers.py'` from the exclude set; phaseC's convergence-addendum row keeps the file in class (c) editable-prose rephrase (sibling rephrases :15 "This test does NOT require a live OpenCode server" to a still-current equivalent). Re-executed the v4 gate (49 pathspecs): 1,843 hits / 98 distinct files. RECONCILED decomposition: bucket (i) MOVES = 26 files / 1,457 hits; bucket (ii) TOUCH = 72 files / 386 hits (71 + persistence_w1_markers.py returning from exclusion + 2 files owned by phaseC's convergence-addendum rows: test_spawn_instance_instructive_errors.py + prompt_section_reference_integrity.py:815); bucket (iii) ALLOWLISTED via pathspecs = 5 files / 83 hits (NOT in output; documented for audit). Total: 103 files / 1,926 hits; zero un-dispositioned residue in the gate output.]**

```
cd /home/nea/ensemble-src-wt-agent-base-v2
# NOTE: use `git grep` (NOT `grep -r`) — git grep walks the COMMITTED tree only.
git grep -niE 'opencode' -- . \
  ':(exclude)legacy' ':(exclude)legacy/**' \
  ':(exclude)CHANGELOG.md' \
  ':(exclude)docs/incidents' ':(exclude)docs/incidents/**' \
  ':(exclude).agents/shared/planning' ':(exclude).agents/shared/planning/**' \
  ':(exclude).agents/*/RESULTS*' \
  ':(exclude).agents/*/LESSONS*' \
  ':(exclude).agents/*/memories' ':(exclude).agents/*/memories/**' \
  ':(exclude).agents/*/working' ':(exclude).agents/*/working/**' \
  ':(exclude).agents/*/EVIDENCE' ':(exclude).agents/*/EVIDENCE/**' \
  ':(exclude).agents/tester/PACKS.md' \
  ':(exclude).agents/approver/active.md' \
  ':(exclude).agents/approver/agent-base-v2-tracking.md' \
  ':(exclude).agents/approver/opencode-native-python-tools-tracking.md' \
  ':(exclude).agents/approver/rename-coder-to-developer-tracking.md' \
  ':(exclude).agents/approver/context-injection-restructure-tracking.md' \
  ':(exclude).agents/approver/tester-skill-evolution-tracking.md' \
  ':(exclude).agents/reviewer' ':(exclude).agents/reviewer/**' \
  ':(exclude).agents/tidier/notes.md' \
  ':(exclude).agents/tester/QUARANTINE.md' \
  ':(exclude).kilo' \
  ':(exclude).slim' ':(exclude).slim/**' \
  ':(exclude)LESSONS' ':(exclude)LESSONS/**' \
  ':(exclude)backup' ':(exclude)backup/**' \
  ':(exclude)plugins/opendesign/copy_freely' \
  ':(exclude)plugins/opendesign/copy_freely/**' \
  ':(exclude)docs/mcp-kb-usage.md' \
  ':(exclude)docs/bugs' ':(exclude)docs/bugs/**' \
  ':(exclude)docs/plans' ':(exclude)docs/plans/**' \
  ':(exclude)docs/test-suite-known-issues.md' \
  ':(exclude)docs/optimization' ':(exclude)docs/optimization/**' \
  ':(exclude)plugins/opendesign/snapshot_with_drift_alarm' \
  ':(exclude)plugins/opendesign/snapshot_with_drift_alarm/**' \
  ':(exclude)tests/unit/test_coder_agent.py' \
  ':(exclude)tests/unit/test_devops_agent.py' \
  ':(exclude)tests/unit/test_reviewer_v2_agent.py' \
# GATE: zero matches. (case-insensitive 'opencode' subsumes external_opencode,
# OPENCODE_URL, opencode_sessions — no separate patterns needed)
```
- **Scope** = git-tracked active tree. `git grep` automatically skips untracked/gitignored paths (`data*/`, `.inspiration-projects/`, `__pycache__`) — untracked orphan DBs never reach the gate (D7 intact).
- **`docs/bugs` exclusion is conditional**: only the two opencode bug docs (moved to `legacy/` in C.3) plus any per-file "historical incident record" rows the executor justifies in the allowlist file; any bug doc describing *current active-stack* behavior must be FIXED, not excluded.
- The tester produces an **allowlist file** (evidence dir): one row per excluded path + one-sentence justification. Rows without justification fail the gate review.

### 11.4 Boundary cases (verified / to-verify at execution)
1. **`docs/mcp-kb-usage.md`** — upstream `sst/opencode` EXTERNAL product docs (D6). Line-by-line verify strictly-upstream context at execution; FIX or move any our-stack line; allowlist with recorded justification.
2. **`plugins/opendesign/copy_freely/design-systems/opencode-ai/**`** — vendored third-party design-system resource (provenance class copy_freely; `plugins/` IS shipped in release payloads per `stage.sh:407`, so it cannot move to legacy without breaking the OD plugin snapshot). Same treatment as #1: line-by-line strictly-upstream verification + allowlist row with justification. Not a user-decision item — provably third-party vendored.
3. **`.gitignore` opencode lines** — INTENTIONAL-KEEP row: the `opencode_sessions.db` ignore remains functional (keeps D7 orphan DBs out of `git status` post-migration). Justify in the allowlist file.
4. **`.kilo/` (66 lines across tracked files mention opencode)** — unclassified residue surfaced by the census; executor classifies at gate time (historical tool state → allowlist with justification; active config → fix).

### 11.5 Load-bearing check — RESULT: CLEAN ✅ (no user decision item)
Verified 2026-10-13 in the worktree: `grep -rniE 'opencode'` over all five v2 trees (`agents/{developer,reviewer,tidier,approver,planner}[v2]/`) returns **ZERO matches** (exit=1). The gate is satisfiable **without violating v2 zero-touch (D4)**. Base==v2 (post-B) inherits this cleanliness. No zero-touch-vs-opencode-free conflict exists.

### 11.6 Skills-surface sweep — RESULT (verified)
- `agents/_prompt_system/innate-skills/` minus the retiring `opencode/` dir: **clean**.
- All `skill-set.yaml` across `agents/`: **clean**.
- All `agents/*/skills-template/` content: **clean**.
- `agents/_prompt_system/` outside innate-skills: **2 hits** at `knowledge.md:96,:106` → mandatory-fix (§11.2). Post-C.3 the `opencode` innate-skill dir lives only in `legacy/`.

### 11.7 Interplay with R8
The gate greps the **committed tree** (`git grep`), so runtime `memory.md` mutation (inner_soul) cannot contaminate it. R8's capture-before-boot rule still governs the base==v2 *equality proof*; the two checks are independent and both stand.

### 11.8 Current-state grounding (tracked-file census by top dir, pre-migration)
`.agents` 306 (1,346 lines under `.agents/` mention opencode — per W4 dry-run: planning history + RESULTS/LESSONS audit material + tester PACKS historical references — all routed to allowlist; gate-excluded via the five expanded exclude paths) · `tests` 37 + `test` 7 (→ C.3 moves + C.4/R1/R2 edits + P7 stragglers) · `agents` 28 (→ retiring metas+dir [C], soul/knowledge fixes [§11.2]) · `plugins` 27 (boundary case #2) · `daemon` 20 (→ §11.2 rephrase set + module docstrings) · `docs` 19 (→ Phase D FIX + historical rows) · `backup` 12 (historical archive → allowlist) · plus singles: `testing-guide.md` (Phase D), `pyproject.toml` (P3), `example/SKILL.md` (C.3 → legacy), `Makefile` (R5), `LESSONS`/`.slim` (historical), `.gitignore` (boundary #3), `.env.example` (TOUCH), `.kilo` (boundary #4 — 66 lines across tracked files, per W4 dry-run). Every census bucket maps to a disposition — no orphan class remains.

**[FIXED per review W4 2026-10-13 — §11.8 census wording updated to reflect the W4 dry-run: 1,346 lines under `.agents/`, 66 lines across tracked `.kilo` files. The five expanded gate excludes (`.agents/shared/planning/**`, `.agents/*/RESULTS*`, `.agents/*/LESSONS*`, `.agents/tester/PACKS.md`, `.kilo`) close the residual surface so the gate can reach zero matches.]**

**Amendment status:** R10 recorded; extends R5/R6; supersedes P7-optionality and Tier-2-optional framing; no contradiction with R1–R9; v2 load-bearing check PASSED (§11.5).
