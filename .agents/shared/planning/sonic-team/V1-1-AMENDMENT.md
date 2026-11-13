# Sonic Team — COMBINED v1.1+v1.2 Amendment Record (2026-11-13)

**Lane:** Emperor's combined amendment lane · **Worktree:** `/home/nea/ensemble-src-wt-sonic-v11` (branch `feature/sonic-team-v1-1`) · **Scope:** prompts + allow-lists + pins + harness docs ONLY — no staging, no daemon boot, no live contact, steady-team agent dirs untouched, shared `kb-writer` untouched.

**Ruling:** Emperor, 2026-11-13 — v1.2 is MERGED INTO v1.1: **one release, one ceremony.** The v1.1 knowledge grant to sonic-coder is REVERSED; the new `sonic-developer` layer carries the planning explore; coders keep SOURCE exploration (RAG excluded). Binding clarification (verbatim): *"sonic coder still has explore but read file/src, not using rag tool."*

**Authoritative frozen strings:** `phase1-implementation-spec-v1.1.md` (same dir). Decision ledger: `decisions.md` §"Combined v1.1+v1.2 rework" (R1–R15).

## 1. What changed (diff summary — combined shape)

| File | Change |
|---|---|
| `agents/sonic-developer/*` | **NEW agent** (meta.json + rule/soul/workflow/tools_note): `llm_model: "agentic"`; allow = instance, knowledge, bash, proc, filesystem, todo, shared_meta_kv, time, self, help, context, pok, nest-report (`nest-report` LAST, `pok` immediately before); deny = edit_file, write_file; team = `[sonic-coder]`; ONE `explore()` at plan-open, NEVER `experience()`; verbatim duty block LAST |
| `agents/sonic-coder/meta.json` | `knowledge` REMOVED from allow (zero-knowledge final tree); description + version → 1.1.0 |
| `agents/sonic-coder/rule.md` + `workflow.md` + `tools_note.md` + `soul.md` | one-shot PER MODULE flow (brief → source exploration → change → quick-test → fix → commit → module report); handoff authorship REMOVED (compact `MODULE-REPORT` message instead); RAG explore-at-start language DELETED, replaced by the two FROZEN discipline sentences (source-exploration-expected + RAG-excluded); coder now COMMITS its module branch (pathspec-limited, no push/merge) |
| `agents/sonic-leader/meta.json` | team = `[sonic-developer, sonic-coder, sonic-reviewer, sonic-tester]` (giter REMOVED per D-H); version → 1.1.0 |
| `agents/sonic-leader/rule.md` + `workflow.md` | fast path added alongside standard path (FROZEN `FAST-PATH GATE` line); one-wave verification consumes HANDOFF-v2 (standard) or module report + branch tip (fast); pre-approved-giter commit rule REPLACED by internal commit choreography (coder commits, developer merges; leader verifies read-only); write-once-at-end + optional intake explore RETAINED with the standard/fast explore split stated |
| `agents/sonic-leader/tools_note.md` + `soul.md` | lanes re-listed (developer/coder/reviewer/tester; giter noted as OUTSIDE the sonic loop); commit choreography updated |
| `agents/sonic-reviewer/*` + `agents/sonic-tester/*` | INTEGRATION level: consume HANDOFF-v2 + FULL MERGED DIFF (`base..merged`), not per-module reports; blast check over the merged file set; verdict formats unchanged; zero-knowledge UNCHANGED |
| `test/packs/sonic_agent_defs_checks.py` | 5 agents; FROZEN_TOOLS v2; EXPECTED_EFFECTIVE (developer = sonic-coder + explorer + kb-writer; leader = 4 team + explorer + kb-writer; coder/reviewer/tester empty); HANDOFF-v2 headers in developer workflow; HANDOFF_PATH three-way = developer/reviewer/tester; coder module-report template pin; blast block across THREE workflows; NEW A12 (developer never-experience ×3 artifacts), A13 (coder discipline both shapes + no-knowledge marker), A14 (fast-path gate line); mutation matrix 41 legs incl. `A3-coder-add-knowledge` (grant-reversal counter-mutation) |
| `agents/.prompt-artifacts.sha256` | regenerated: **197 → 201 rows** (4 new sonic-developer `.md`; 16 reworked sonic shas moved; zero steady-team rows touched) |
| `tests/unit/test_anomaly_report_duty_prompts.py` | 50/48 → **51/49** (fifth sonic agent carries the duty block) |
| `tests/unit/test_anomaly_report_grant_pins.py` | 48/50 → **49/51** |
| `tests/unit/test_pok_grant_pins.py` | 49/50 → **50/51** (`pok` immediately before `nest-report` preserved) |
| `tests/unit/agents/test_aegis_security_family.py` | `EXPECTED_BASELINE_ROWS` 197 → **201** |
| `.agents/shared/planning/sonic-team/phase1-implementation-spec-v1.1.md` | NEW — the combined architecture spec (supersession table §11) |
| `.agents/shared/planning/sonic-team/decisions.md` | combined-rework section R1–R15 appended (D8 flip RESPECIFIED: developer+leader only) |
| `.agents/shared/planning/sonic-team/phase3-dryrun-spec.md` | REWRITTEN for the PARALLEL dry-run: V0–V14 supersede V0–V11; two-sided coder criterion (ZERO RAG explore AND ≥1 file-exploration calls — BOTH required); ≥2 coders CONCURRENT (temporal overlap); developer ONE explore; leader exactly-one experience with designed learning (silent zero = FAIL); `fenced-launch.py` MANDATORY |
| `.agents/shared/planning/sonic-team/dryrun-boot-procedure.md` | AMENDMENT 2026-11-13 appended (parallel topology + V9–V14 evidence one-liners); validated boot steps untouched |

## 2. kb-writer lane finding — verdict CHEAP (unchanged, re-affirmed)

- **`explore()`** = one explorer-agent turn (`agents/explorer/meta.json` pins `llm_model: "quick"`); one bounded RAG query + synthesis, no fan-out. In the combined architecture the sonic lane runs at most ~1 per commission: the developer's plan-open explore (mandatory), plus ≤1 optional leader intake explore on the fast path.
- **`experience()`** = a ZERO-LLM caller turn + async kb-writer spawn (kb-writer also `quick`). Exactly ONE per commission, leader-only, completion-time, genuine-learning-gated.
- **Edge (must-know):** `agents/explorer/meta.json` carries legacy `caller_model_overrides: {"coder": null}` — keyed by the CALLING agent's literal `agent_id`. It matches ONLY the steady-team `coder` id; **sonic-developer and sonic-coder NEVER match** (their ids differ), so the sonic lane always spawns explorer on its own `quick` default; no override path is exercised. No action needed; recorded so nobody "fixes" the map for sonic.
- **Net:** the combined shape REDUCES knowledge-lane calls vs the v1.1 grant (the coder's mandatory explore is gone; writes were already leader-only-capped-at-one). Shared `kb-writer` untouched.

## 3. Verbatim discipline language (as landed)

**sonic-developer explore bound** (`agents/sonic-developer/rule.md`):

> Open every commission with exactly ONE bounded `explore()` against the knowledge base, at plan-open — BEFORE you write the plan or any module brief. Stored experience is the most valuable read in the system and your coders cannot query it: whatever the explore teaches you must ride into their module briefs. The bound is hard: exactly ONE `explore()` per commission, at plan-open — no other knowledge call, and NEVER `experience()`. You NEVER call `experience()` — the leader owns the single knowledge write at commission end.

**sonic-coder source-explore + RAG-exclusion** (`agents/sonic-coder/workflow.md`, step 1; same two sentences in `rule.md`):

> Source exploration is EXPECTED: it is a core part of module work, not ceremony, and it is unbounded within your module scope. […] The ONE exclusion is the RAG tool: no `explore()` call — the developer's plan carries the stored experience; explore the SOURCE freely with your file tools.

**sonic-leader write-once-at-end + fast path** (`agents/sonic-leader/rule.md` 7 / 2):

> The ONE exception is my write-once-at-end knowledge moment (workflow §6): at most ONE `experience()` call at commission completion, only when the commission taught something non-obvious — nothing learned means no call at all.

> The FAST PATH is the narrow exception: dispatch a single `sonic-coder` DIRECTLY, skipping the developer layer, only when the gate holds in full — `FAST-PATH GATE: single module, single coder, no new agent dir, no cross-module risk — anything larger goes through sonic-developer.` One ambiguous dimension means the standard path.

**HANDOFF-v2 section list** (authored by sonic-developer; §4.1 of the combined spec):

> `## Files touched across modules` · `## What changed` · `## What is UNTESTED` · `## Known risks` · `## How to verify` · `## Bash-raises used` · `## Blast-radius self-assessment vs predicate`

## 4. Pin lockstep (counter-mutation proven)

`test/packs/sonic_agent_defs_checks.py`: A1–A14; good fixture GREEN ×14; **41 counter-mutation legs, ALL RED** — incl. the new `A3-coder-add-knowledge` (re-granting the coder REDs: the v1.1 grant reversal is pinned), `A3-developer-drop-knowledge` / `A3-developer-deny-drop-editfile` (D-A lockstep), `A2-developer-add-reviewer` (D-H lockstep), `A4-developer-model-coding` (D-B), `A5-developer-add-multiplier`, `A7-developer-glob-delete` (blast block ×3 files), `A6-developer-drop-header` / `A6-coder-module-report-drop` (HANDOFF-v2 + module-report contracts), `A12-*` ×3 (never-experience boundaries), `A13-*` ×4 (coder discipline both shapes + no-knowledge marker + no held-knowledge row), `A14-leader-gate-corrupt` (fast-path gate).

## 5. Ceremony plan — v0.30.1 (RECIPE ONLY — NOT EXECUTED; the parallel dry-run re-proof gates it)

1. **Gate first:** parallel dry-run re-proof per the rewritten phase3 spec (V0–V14) on this branch — PASS is the precondition for any staging.
2. **Bump:** from a clean checkout of this branch tip: `.venv/bin/python scripts/bump_version.py patch` (v0.30.0 → v0.30.1).
3. **Tag:** annotated tag on the bump commit; push is giter/lane-owned (NOT this lane's act).
4. **Stage worktree:** clean stage worktree AT THE TAG (never at a dirty tip); fenced `.env`; its own `.venv` (never another checkout's).
5. **FE pre-build:** frontend build before stage.sh so the FE bundle is present.
6. **Stage:** `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.30.1 stage.sh live` — wait for the receipt (STDOUT-only; releases/state.json journals promotes only). `rollback_safe=true source=explicit=true` + `contains_contract_phase=true` are the EXPECTED tokens (legacy-tree derive-path is unusable — standing POK).
7. **Verify:** 4-way sha equality (release dir == dist == manifest == sidecar) + `generate_prompt_artifact_baseline.py --check --tree <release>` (baseline rides the release; expect 201) + stage-verify pack.
8. **Promote:** the Emperor's — NOT part of this lane, NOT assumed.

## 6. Evidence pointers (this lane)

- Pack static layer: `PASS A1–A14` (incl. A8 generator `--check --tree`, rc=0, 201/201).
- Pack self-test: `SELF-TEST: PASS` (good fixture GREEN ×14; 41 counter-mutation legs ALL RED).
- Anomaly duty pins: `tests/unit/test_anomaly_report_duty_prompts.py` + `test_anomaly_report_grant_pins.py` + `test_pok_grant_pins.py` — the 4 canary-rooted reds in duty_prompts are PRE-EXISTING and base-attributed (standing per the decisions ledger; `agents/canary` fenced — NOT touched, NOT regressed).
- Baseline: before 197 → after **201** (`--check --tree` rc=0); delta = 16 reworked sonic `.md` sha updates + 4 sonic-developer row adds; zero steady-team rows moved.
- Commits: `git log --oneline ee2b46d3b..HEAD` (this branch; targeted pathspec commits only; NO history rewrite of the landed v1.1 arc). Dry-run evidence commits on this branch: run-1 `b1d8bcf7c` · MF-1 addendum `d6fff7f1b` · re-run `a8505c39e` · V12 re-adjudication `16100e872`. Final verdict under the refined census: **V0–V14 = 15/15 PASS** (evidence-preserving re-adjudication — same transcripts, refined mapping only; no third run performed).
- Ruling + dispatch: Emperor ruling 2026-11-13 (combined v1.1+v1.2; one release, one ceremony).

## 7. Dry-run evidence record (re-run + re-adjudication, 2026-11-13)

**Evidence commits (in order):** run-1 `b1d8bcf7c` · MF-1 addendum `d6fff7f1b` · re-run `a8505c39e` · V12 re-adjudication `16100e872`. **Verdict: V0–V14 = 15/15 PASS under the refined census (evidence-preserving re-adjudication).** No third run was performed — the re-adjudication re-mapped the existing run-2 transcripts only.

**Refined-census note (definition, authoritative for V12/F15):** admissible file-exploration evidence = ANY file-read call — filesystem `read_file`/`grep_files`/`glob_files`/`list_directory` OR bash-based reads (`cat`, `head`, `tail`, `sed -n`, `grep`, `rg`, `awk`, `nl`); count each occurrence; V12 passes at census ≥ 1 per coder. ABSOLUTE and unchanged: RAG `explore` tool calls == 0 per coder. NOT exploration evidence: bash commit/merge/write commands (`git commit`/`git merge`/`git add`), `edit_file`, `write_file`. Two-sided criterion: RAG-zero AND census ≥ 1; one-sided = FAIL. Provenance: leader ruling (tester re-adjudication of V12 under the refined census, 2026-11-13) + RESULTS §11.9 (re-adjudication commit `16100e872`; coderB evidence = 5 bash reads: cat×4 + tail×1).

**Honest finding (i) — PERFORMANCE WATCH:** the developer's plan-open `explore()` (instance `66c739cf`) exceeded its 300s cap and returned no knowledge — anomaly `04408b5e`. The call WAS made at plan-open (ordering predicate satisfied, V9 PASS) and the developer proceeded correctly without it (no retry, no experience, no kb-writer spawn). Recorded as a PERFORMANCE WATCH item: the call is cheap-tier (`llm_model: "quick"`) but wall-clock heavy on a 2-module plan; the developer's correct no-retry handling kept it non-fatal. No prompt or code change implied by this record.

**Honest finding (ii) — kb-writer terminal failure, no synthetic pollution:** the async kb-writer job ran and FAILED — instance `ff63ab4e-aaa7-490d-9732-e9c119c333bd` (spawned 10:15:51Z, terminal_status=error, work_id `6adce09e`, "PP1 zero-watcher terminal fire: no watchers found, terminal report NOT delivered"); its transcript shows 0 tool calls / 0 assistant messages; no `track_id` in the daemon log. NO RAG insert landed. The marker-bearing `experience()` call (marker `SONIC-DRYRUN-V11-20261113-a7c41e9b`, leader instance `c299f5d1`, @ 10:15:22.860171Z) stands as the V13 artifact; no synthetic knowledge pollution occurred. Both outcomes were pre-declared acceptable; silence was not — hence this disclosure.

## Ceremony record — v0.30.1

- **Version:** v0.30.1 (patch bump from v0.30.0; commit `00b0af633a075a93afb153b2d6bbd44ed18f78ed`)
- **Stage command (verbatim):** `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.30.1 bash scripts/upgrade/stage.sh live --allow-stale-stage` (run from `/home/nea/ensemble-src-wt-stage-v0301`; `--allow-stale-stage` added because the freshness guard refused the non-tip tree — feature branch is ahead of `origin/latest`; documented override, journaled on the install dir)
- **Tag object sha:** `0a4c8b6f63a35efb9b7207d56ae1c25a8aded131`
- **Tag commit sha:** `00b0af633a075a93afb153b2d6bbd44ed18f78ed`
- **4-way binary sha:** `82c60f4fba39cd4a52d5bbc72981014dbadb68a555d23fc9842aa50889c012b0` (release binary == stage-wt dist binary == manifest binary_sha256 == sidecar artifact_sha256)
- **Release dir:** `/home/nea/agents-ensemble/releases/v0.30.1`
- **Baseline count:** 201 (PROMPT-BASELINE OK, rc 0)
- **Migration delta:** `daemon/__init__.py` only (version string `0.30.0` → `0.30.1`); version-only, NOT a migration; `scripts/` untouched
- **rollback_safe:** true (source=explicit)
- **Staged at:** 2026-11-13T11:12:11Z; known_schema_gen `20261112_124200_add_snapshot_agent_space_key.sql`; contains_contract_phase true
- **Promote:** NOT performed (Emperor-owned). Stage worktree `/home/nea/ensemble-src-wt-stage-v0301` kept.

### FRESHNESS OVERRIDE — TAKEN (manual audit record; system journal append FAILED)

- **Facts**: stage-tree HEAD `00b0af633` (v0.30.1 bump commit); integration-tip ref origin/latest `ee2b46d3b` (LOCAL, unpushed BY DESIGN — the commission's no-push fence makes the guard premise HEAD==tip unsatisfiable in an unpushed feature release lane); refusal class `non-tip-tree`; surviving system evidence `/tmp/stage_v0301.txt`. Verbatim log lines (from `/tmp/stage_v0301.txt`):
  - `upgrade-stage[live]: WARN: FRESHNESS OVERRIDE: tip-identity mismatch (HEAD=00b0af633a075a93afb153b2d6bbd44ed18f78ed tip=ee2b46d3b01e1d956aac7ad1c5dca5ad9f209ff8) — proceeding (operator accepted; journaled)`
  - `upgrade-stage[live]: WARN: freshness-override journal append FAILED (best-effort) — override is taken but the audit trail is incomplete; reason=non-tip-tree`
- **Adjudication**: leader accepted the override 2026-11-13 (this lane). Rationale: artifact integrity is independently proven — stage verification S1–S5 (4-way sha `82c60f4f…`, sidecar git_head == tag commit, git_dirty=false, baseline 201, content invariants, live untouched); the tag pins the exact content; finalize merges the tag commit into latest, matching house precedent (v0.30.0: latest = tag commit + 1 verification record).
- **System defect noted**: stage.sh freshness-override journal append fails for non-tip trees (audit trail incomplete) — anomaly filed by the leader.
