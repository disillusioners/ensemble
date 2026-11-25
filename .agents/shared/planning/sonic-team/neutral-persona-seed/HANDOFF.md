# HANDOFF — persona-neutralization sweep (neutral-persona-seed)

Branch `fix/neutral-persona-seed` · base `0a9b8cd36` (v0.31.2) · tip at handoff `a552eaf06` (+ this handoff commit).
Scope: prose neutralization of persona titles across the shipped surface. NOTHING was merged to `latest`, pushed,
tagged, promoted, deployed or restarted — the branch stands for review.
Census + (a)/(b)/(c) detail: `CENSUS.md` (same directory). Base A/B evidence: `base-ab-evidence.md`.

Phase 1 (pre-existing, leader-verified, NOT this sweep): `3c64d47f5` — daemon/repositories/memoir/seed.py,
.agents/shared/planning/ari-memoir-store/design.md §11, tests/unit/test_memoir_persona_seed.py.

## Files touched across modules

Union of all modules (disjoint file sets; per-module grouping preserved):

- daemon/ (41 files, +81/-83) — module `sweep/nps-daemon` (38 files, comments/docstrings/SQL comments only) +
  `sweep/nps-daemon` gap (5 files: 7 runtime-visible prose strings + the two-distinct-form docstring fix).
  Includes phase-1's seed.py.
- tests/ (39 files, +234/-231) — module `sweep/nps-tests` (38 files, fixtures/docstrings) + `sweep/nps-gap-tests`
  (2 files: 1 local alias, 1 pack fixture body). Includes phase-1's seed test.
- docs/guidelines/ (3) + scripts/ (2) + agents/ (1) — module `sweep/nps-docs`; `sweep/nps-docs` gap adds
  docs/runbooks/prompt-assent.md, docs/deployment.md, docs/guidelines/README.md:75, agents/ari/tools_note.md:920.
- test/packs/ (2) + test/scenarios/ (1) — module `sweep/nps-gap-packs`.
- agents/.prompt-artifacts.sha256 (1) — module `sweep/nps-reseal` (generated baseline refresh).
- .agents/** (127 files, +683/-682) — module 4 `sweep/nps-gap-agents` (commit `59514bee7`), own commit.
- CHANGELOG.md (1, +14/-14) — module 4 (commit `706683882`), own commit.

## What changed

Behavior change: NONE. This is a prose/title-only sweep plus one generated integrity baseline refresh.
Title words change; every citation token (dates, ruling ids E-6/ER-1/A-13/R16-R19, board ids, shas, branch names,
plan paths, section numbers, env vars, tool/module/function names, numeric values) is preserved verbatim —
per-file citation-token multiset drift = 0 across all 127 .agents files + CHANGELOG; sha/date/id multiset
identical across the 4 gap-fixed doc files; markdown pipe/backtick/bold counts identical.

(a) FILES WHERE AN EXECUTABLE LINE CHANGED — one named exception, nothing else:
- tests/unit/test_memoir_constellation.py (4 lines: :239,:241,:243,:244) — a TEST-INTERNAL local alias
  `emperor` -> `star`. Forced by the strict gate (that residual is not a contract pin) and behavior-neutral:
  AST-normalized diff over all 73 tests-tree Python files shows exactly 1 file differing, and the only identifier
  delta is `emperor` removed. The original contract froze PUBLIC identifiers (module/table/column/function/
  config/env) — none touched. Phase-1's seed.py + seed test change only a string constant and assertions.
  No public identifier, signature, import, config knob, env var, schema, statement or control-flow change anywhere.
  Independent AST proof (strings masked) over daemon/: 0 of 35 files differ structurally.

(b) FILES WHERE A RUNTIME-VISIBLE STRING CHANGED:
- daemon/routers/skills.py:1641 "(operator/Emperor-visible)" -> "(operator/user-visible)"
- daemon/routers/skills.py:1916 "operator/Emperor-sanctioned closure ..." -> "operator/user-sanctioned ..." (E-6 kept)
- daemon/services/decision/registry.py:379 "Emperor has ratified the transition" -> "user has ratified the transition"
- daemon/services/pok_service.py:361 "— Emperor ruling ER-1" -> "— user ruling ER-1" (ER-1 kept)
- daemon/tools/prompt_assent.py:1609 "the Emperor's decision for a minted token" -> "the user's decision ..."
- daemon/tools/prompt_assent.py:1629 "the Emperor received at mint time" -> "the user received at mint time"
- daemon/tools/prompt_assent.py:1843 "the token the Emperor received at mint time" -> "the token the user received ..."
- tests/ (38 files): fixture string values (star names, claim bodies, display names, aliases "my dear" -> "the
  operator", star-list pair ["Emperor","the Emperor"] -> ["the user","The user"], governance ratified_by values)
- tests/helpers/prompt_assent_fixture.py:126 + prompt_assent_tool_fixture.py:171 user_name "The Emperor" -> "The user"
- test/packs/memoir_w8_boot_abort_test.sh:342 INSERT body literal -> "... the user" (row shape identical)
- agents/ari/tools_note.md:920 stars=["@empress"] -> stars=["@user"] (documentation example)
- daemon/repositories/memoir/models.py:103 docstring example -> ["the user", "The user"] (two DISTINCT surface forms,
  aligned with the tests module; the leader's accuracy fix)
- docstring-prose sub-class: 39 lines across 26 daemon files; comment sub-class 20 lines; SQL-comment sub-class
  8 lines across 5 migrations (DDL bytes untouched — verified); .agents/** 127 files + CHANGELOG = prose only.

(c) COMPLETE REMAINING-HIT LIST (75 in the 5 trees, 20 in .agents/**, 0 in CHANGELOG.md) — every one justified:
- daemon (10): repositories/memoir/models.py:188 (PIN_ACTORS enum — the API contract), models.py:222 +
  migrations/versions/20261105_102821_create_memoir_tables.sql:87 (SQL CHECK mirror of the enum),
  tools/memoir.py:700,:727,:861,:969,:970,:1339,:1350 (actor-claim help/refusal text stating the enum values).
- tests (64): 61 actor=/pinned_by= enum VALUE sites + rendered/enum assertions (pg_corpus, admission_unit,
  briefing_seam, briefing_unit, keepsake_proposals, retrieval_unit, tools_unit, wake_refresh, write_purity);
  unit/test_memoir_v2_schema.py:142 (SQL CHECK mirror); unit/test_memoir_persona_seed.py:76,:77 (the 2
  brief-mandated negative-guard asserts). These lowercase values ARE the wire contract (PIN_ACTORS is validated
  at daemon/repositories/memoir/repository.py:1731 and daemon/tools/memoir.py:863/:972).
- agents (1): agents/ari/tools_note.md:941 (actor-claim doctrine text).
- .agents/** (20): 19 actor-claim enum literals in 11 ari-memoir-store docs;
  tester/RESULTS/2026-11-05-memoir-g2-pg-corpus.md:98 (documents the lowercase enum + the refused uppercase
  form). The former plan-file PATH reference class was REMOVED by the neutral filename rename — those lines now
  cite `open-questions.md` and carry no persona token.
- EXCLUDED BY DECISION: this HANDOFF.md and CENSUS.md (report artifacts — they name the tokens by necessity);
  plugins/opendesign/** (5, vendored artwork + HASHES.sha256 integrity — leader ruling: KEEP, never edited).

Rulings recorded: CHANGELOG.md SWEPT (leader ruling: title changes, citations stay, git history preserves the
original wording — it is the most-read file and would otherwise be the last place the old title ships);
plugins/ KEPT report-only. Disclosed scope extension: docs/runbooks + docs/deployment.md + docs/guidelines/
README.md:75 + test/packs + test/scenarios + the whole .agents/** corpus + CHANGELOG.md entered scope by
leader/operator ruling after the original 5-tree commission.

## What is UNTESTED

- daemon: no daemon boot, no live run, no PG legs against the daemon itself.
- The `memoir_w8_boot_abort_test.sh` pack could NOT be executed (macOS/darwin): the pack hardcodes Linux
  `/usr/lib/postgresql/16/bin/*` and needs caller-provisioned `MEMOIR_W8_BINS_DIR` binaries (absent). Substitute
  evidence: its B5 leg reproduced against the pack's own INSERT + query (swept body -> count 1; control without
  the token -> 0; pre-sweep body -> 1; stored row shape identical).
- Full-repo suite: not run to completion (host fd soft limit 256 + a pre-existing INTERNALERROR test). Attribution
  was made by lastfailed set-diff (319 common reds, 8 base-only path artifacts, 1 tip-only crashed file).
- .agents/** + CHANGELOG: prose only, no test covers document prose.
- The two stale SEALED_SHA_BASELINES rows (daemon/constants.py, agents/leader/rule.md) were NOT repaired —
  pre-existing drift in files this sweep does not touch.

## Known risks

- A9 fence: `sonic_agent_defs_checks.py --static` FAILs on A9 (agents/-delta fence) — it was already RED at base
  with 24 entries; this sweep adds a 25th (agents/ari/tools_note.md). A3 (sonic-leader `room` allow drift,
  commit a32bdb395) is likewise pre-existing. A8 PASSES. Making A9 green needs the fence's expected set updated
  (giter/expert lane), not this sweep.
- A CHANGELOG edit touches the repo's most-read file; the sweep is title-only and citation-preserving, but the
  file's historical wording now differs from git history's older revisions (revertible: commit 706683882).
- The `.agents/**` corpus is the operator's ruling trail: titles are gone from the current face of the repo,
  citations intact, original wording recoverable from history.
- The local-alias rename in tests/unit/test_memoir_constellation.py is the only identifier touched anywhere.
- Swept prose now says "the user" where it previously said "the Emperor" — reviewers comparing against a
  published ruling transcript will see a wording delta (the ruling ids/date are unchanged).
- Unresolved / for the operator: out-of-map persona-adjacent prose left by ruling — "the sovereign ruled on many
  matters" (2 lines, tests/unit/test_memoir_constellation.py) and the "The Court"/"hello the court" fixture names.
- The wave-1 tests module's coder wedged 15+ min on a bash call before delivering its report (deliverable was
  already merged + verified); anomaly ae0188b2. No terminate/re-spawn was performed (lane rules).

## How to verify

Worktree: /Users/nguyenminhkha/workdir/ens-neutral-persona-seed (branch fix/neutral-persona-seed).
Scrub ambient prod env first; use the worktree's own .venv (`daemon.__file__` resolves inside the worktree).

1. Strict gate: `git grep -nwIi -E 'emperor|empress|empyrea|my dear|your majesty' -- daemon tests docs/guidelines
   scripts agents` -> 75 lines, each in the (c) list above. `.agents/**` -> 20 carve-out lines.
   `git grep -nwIi -E 'emperor|empress|empyrea' -- CHANGELOG.md` -> 0.
2. Unit leg: `.venv/bin/python -m pytest -q <the 33 touched tests/unit files>` -> 748 passed / 6 failed; the 6 are
   A/B-proven identical at base 3c64d47f5 (see base-ab-evidence.md): 3 seal-map/grants reds (stale rows
   daemon/constants.py + agents/leader/rule.md), 1 lane-delta red (refs/heads/feature/ari-memoir-store absent),
   2 BSD-grep path-form reds on darwin (`daemon//...` vs the test's GNU expectation).
3. PG leg: `PG_TEST_DB=ensemble_test_only .venv/bin/python -m pytest -q -o addopts= -m postgres <5 touched
   tests/postgres files>` -> 69 passed.
4. Packs: `bash test/packs/memoir_seed_unit_test.sh` -> RESULT: PASS (mutation legs still RED);
   `bash test/packs/memoir_v2_constellation_service_test.sh` -> RESULT: PASS (17 passed);
   `python3 test/scenarios/group_chat_video_team_pilot_scenario.py` -> RESULT: PASS (9 checks).
   `bash test/packs/memoir_w8_boot_abort_test.sh` -> BLOCKED on darwin (see "What is UNTESTED").
5. Integrity: `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` -> OK 217;
   `python3 test/packs/sonic_agent_defs_checks.py --static` -> PASS A8 (seal + baseline 217/20); A3/A9 pre-existing.
6. Zero-logic proof: AST-normalized diff (string constants masked) base -> tip over daemon/ and tests/ ->
   0 daemon files differ; exactly 1 tests file differs (the disclosed local alias).

## Bash-raises used

None. Every command ran under the default 600 s cap; long legs (full unit suite, base A/B clone, pack runs) were
backgrounded via proc and read from their logs. No raise was consumed.

## Complexity self-assessment

MEDIUM — as assessed after the explore + plan and BEFORE the first dispatch; unchanged by the later scope
extensions (the extensions were leader/operator rulings, not a re-planning by me). Basis: ~1,100 hit-lines across
7 trees, mechanical and map-driven, with a bright-line contractual rule and a report-don't-touch contradiction
path; 3 modules + 4 gap modules + 1 module 4, disjoint file sets, one parallel wave each. No handover was raised;
the leader/operator adjudicated every extension. Had this been assessed from the final scope (5 trees + .agents/**
+ CHANGELOG + 2 gap rounds), it would still land MEDIUM on mechanics but HIGH on review volume — recorded for the
record rather than as a softened verdict.
