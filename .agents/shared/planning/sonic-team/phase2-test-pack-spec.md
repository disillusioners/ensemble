# Phase 2 — Verification: Pack Spec (`sonic_agent_defs_unit_test`)

**Plan set:** ENSEMBLE-31 "sonic team v1" · **Phase:** 2 of 4 (canonical numbering: 0 preflight, 1 content build, 2 verification, 3 dry-run, 4 staging)
**Owner:** W3 (plan fan-out) · **Status:** SPEC — nothing here has been executed
**Branch:** `feature/sonic-team-v1` @ base `ac9849bf7ce7922c080ed09cfc49ce2d987f266d` (= origin/latest at plan time)
**Inputs:** `findings-C-risk.md` §2b (pack corpus/registry), §2c (pins/QUARANTINE), §2d (ceremony gates) · `findings-B-infra.md` §1 (registry/discovery), §2b (recursion), §5 (llm lanes)
**Cross-refs (restate by reference, never re-invent):** `phase1-implementation-spec.md` owns the frozen contracts — 7 handoff field names, `HANDOFF.md` path convention, blast-radius glob list, the Ari note text, meta.json values (`llm_model:"agentic"` ×4; `recursion_limit_multiplier:7` on sonic-coder only; sonic-leader `team_members {sonic-coder, sonic-reviewer, sonic-tester, giter}`).

## 1. Purpose and gate semantics

One gate-able pack covering the four new agent definitions + the one steady-team edit (Ari's `rule.md`). **Aegis lesson (binding):** a pinned condition that lives in NO registered pack cannot be verified by any gate — the pin is invisible (POK `packconv-03`: a script with no registry row never enters the pack runner; the runner IS the tester's procedure, `findings-C-risk.md` §2b). This pack is therefore *registered* (§4) and self-contained: it runs from any clean checkout of the branch (and from `latest` post-merge) with `bash test/packs/sonic_agent_defs_unit_test.sh`.

Phase 2 gate rule: **pack PASS is a precondition of Phase 3** (dry-run) and Phase 4 (staging). Pack FAIL ⇒ nothing enters the dry-run until fixed and re-run.

## 2. Pack file spec

### 2.1 Files shipped

| File | Role |
|---|---|
| `test/packs/sonic_agent_defs_unit_test.sh` | Registered pack entry point (bash; dual-layer timeout; `RESULT:` contract) |
| `test/packs/sonic_agent_defs_checks.py` | Self-contained checker (stdlib-only static layer + `--real` resolver layer + `--self-test` mutation matrix) |

House naming/style precedent: `test/packs/embed_consumers_unit_test.sh` (header block, `ROOT=` resolution, `FILES`/assert arrays, `RESULT: PASS|FAIL|TIMEOUT`), `test/packs/adjacent_chat_unit_test.sh` (Layer-1/Layer-2 timeout comment), `test/packs/authz_auto_derive_unit_test.sh`.

### 2.2 Bash skeleton (exact conventions to implement)

```bash
#!/usr/bin/env bash
# Sonic Team (ENSEMBLE-31) — agent-definition contract pack.
# Pack: sonic_agent_defs_unit_test
# Gate: feature/sonic-team-v1 @ base ac9849bf7 (ancestor-pinned, not branch-name-pinned).
# Scope: agents/sonic-{leader,coder,reviewer,tester}/{meta.json,*.md} +
#        agents/ari/rule.md (note + exactly-1 Plane) + agents/.prompt-artifacts.sha256
#        + tests/unit/test_ari_pm_terminology_pins.py + the ari/rule.md row of
#        tests/test_chart_image_delivery_e2e.py::SEALED_SHA_BASELINES.
# Dual-layer timeout: outer `timeout 300` at invocation + inner 240s guard.
# Invocation contract: timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT"

# Drift pin (ancestor form — this pack must also run on latest post-merge):
git merge-base --is-ancestor ac9849bf7 HEAD || {
  echo "RESULT: FAIL (DRIFT — base ac9849bf7 not ancestor of HEAD)"; exit 1; }

PACK="sonic_agent_defs_unit_test"
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)

# Layer A — static contract checks + mutation self-test (stdlib only).
python3 test/packs/sonic_agent_defs_checks.py --static --self-test || { echo "RESULT: FAIL (static)"; exit 1; }

# Layer B — REAL resolver + terminology pins (venv required; uv run lazily syncs).
timeout 180 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
  uv run python test/packs/sonic_agent_defs_checks.py --real || { echo "RESULT: FAIL (real-resolver)"; exit 1; }
timeout 120 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
  uv run python -m pytest tests/unit/test_ari_pm_terminology_pins.py --tb=short -q \
  || { echo "RESULT: FAIL (ari terminology pins)"; exit 1; }

END=$(date +%s); echo "Pack inner runtime: $((END-START))s"; echo "RESULT: PASS"; exit 0
```

**Self-contained decision:** A1/A2/A4/A5/A6/A7/A8-static/A9 are **pure `python3` stdlib** over the tree (no daemon import — mirrors the `generate_prompt_artifact_baseline.py` doctrine that a bare host `python3` must not drag the langchain stack; `scripts/protection/generate_prompt_artifact_baseline.py:36-50`). A2-real/A3-real call the **real resolvers via `uv run python`**: `get_registry()` is module-level and config-free (`daemon/registry.py:1571-1585`) and `daemon/tools/_auth.py` is stdlib-only at top level (`_auth.py:21` is its only top-level import; `TOOL_REQUIRED_AGENTS` at `_auth.py:35-59`, `_check_team_membership` at `_auth.py:90+`). **If `uv run` fails, the pack FAILS — it must not skip a resolver leg** (skip-on-unavailable is exactly the aegis blind-spot class).

Env-scrub convention: the 7-var per-variable scrub shown above (`embed_consumers_unit_test.sh:51` precedent). `env -u POSTGRES_*` is a silent NO-OP — never use the glob form (project critical note; `findings-C-risk.md` §4b comment).

## 3. Assertions

Every row carries a **counter-mutation** that must redden it (POK `mutation-01-guards-need-counter-mutation`: absence-only pins are re-aimed at positive shapes; a leg that stays green under its own counter-mutation is vacuous). Space/glob negatives are scoped to non-comment lines where a mandated comment carries the token (POK `ac-comment-token-grep-collision`).

| ID | Assertion | Exact check | Counter-mutation (must REDden) | Failure message shape |
|---|---|---|---|---|
| A1 | meta.json parse ×4 | For each `agents/sonic-{leader,coder,reviewer,tester}/meta.json`: `json.load` succeeds; required keys present — `id` (the only pydantic-required field besides registry-derived `path`, `daemon/registry.py:443-460`), plus the Phase-1 frozen key set (`name`, `tools`, `team_members`, `innate_skills`, `llm_model`). `id` equals the directory name. | Drop `"id"` / inject a trailing comma in `sonic-leader/meta.json` (copy) | `FAIL A1 sonic-leader/meta.json: <json error / missing key>` |
| A2 | team_members resolution | Static, with **absent → `[]` normalization** (phase1 §3.5): `sonic-leader.team_members == ["sonic-coder","sonic-reviewer","sonic-tester","giter"]` (exact set); the other three normalize to `[]`. Real: `get_registry()` discovers all four as `AgentMetadata`; `_check_team_membership("sonic-leader", m) is None` for each of the 4; `_check_team_membership("sonic-coder","worker")` returns an error (deny-by-default). | Add `"sonic-planner"` to leader's list; add `"worker"` to `sonic-coder.team_members` | `FAIL A2 team_members: <actual set>` |
| A3 | tool allow/deny resolution ×4 | Static against the FROZEN `[sonic-tools-v1]` arrays (phase1 §3.5, exact element sequences; JSON whitespace free; **absent keys normalize to `[]`**): leader.allow `["instance","bash","filesystem","time","self","help","context","shared_meta_kv","midflight","pok","nest-report"]`, leader.deny `["edit_file","write_file"]`, coder.allow `["bash","proc","filesystem","time","self","help","context","shared_meta_kv","pok","nest-report"]`, coder.deny `[]`, reviewer.allow `["bash","filesystem","time","self","help","context","pok","nest-report"]`, reviewer.deny `["proc","instance","service","midflight","shared_meta_kv","infra","mcp","secrets","edit_file","write_file"]`, tester.allow `["bash","proc","filesystem","time","self","help","context","pok","nest-report"]`, tester.deny `["instance","service","midflight","shared_meta_kv","infra","mcp","secrets"]`. Real resolution: effective spawn set = `team_members ∪ implicit(allowed categories ∩ TOOL_REQUIRED_AGENTS, `_auth.py:35-59`) − denied` — phase1 §3.5 expectation: **leader = exactly the 4 (ZERO implicit members: no allowed category maps to a `TOOL_REQUIRED_AGENTS` row), others = ∅**. High-risk negatives: coder cannot spawn `worker`; leader cannot spawn `leader`/`developer`/`planner`; reviewer cannot spawn anything. | Add `"knowledge"` to leader allow (implicit `explorer`+`kb-writer` ⇒ effective set grows ⇒ RED); drop `"bash"` from reviewer allow ⇒ RED; delete a deny entry (e.g. reviewer `edit_file`) ⇒ RED | `FAIL A3 allow/deny drift: <agent> <key>` / `FAIL A3 effective spawn set: expected <E> got <G>` |
| A4 | `llm_model == "agentic"` ×4 | Static: `meta["llm_model"] == "agentic"` for all four (findings-B §5: `agentic` is in default `allowed_models`, `daemon/config.py:487`). Also assert `agentic` ∈ the in-tree default selectable set (plan-overview R15: a narrowed `OPENAI_SELECTABLE_MODELS` operator override would silently drop the pin to the default pool — the pack records the in-tree default; a live config narrowing is an operator-visible property, not a repo property). | Change `sonic-reviewer` to `"coding"` | `FAIL A4 sonic-reviewer.llm_model=coding` |
| A5 | recursion multiplier | Static: `sonic-coder.meta["recursion_limit_multiplier"] == 7`; for the other three the key is **absent** (`meta.get(...) is None`). Field is real: `daemon/registry.py:509-521`. | Remove from coder; add `7` to reviewer | `FAIL A5 multiplier: coder=<x> others=<y>` |
| A6 | handoff-artifact schema presence | Static, exact frozen values (phase1 §4.0 `[sonic-handoff-v1]`, landed): `agents/sonic-coder/workflow.md` contains the 7 **Title-Case `##` headers** verbatim — `## Files touched` · `## What changed` · `## What is UNTESTED` · `## Known risks` · `## How to verify` · `## Bash-raises used` · `## Blast-radius self-assessment vs predicate` — and the line `HANDOFF_PATH = HANDOFF.md` (worktree-root-relative; the ABSOLUTE path is passed in every dispatch). `HANDOFF.md` literal also present in `agents/sonic-reviewer/workflow.md` and `agents/sonic-tester/workflow.md` (phase1 A8), byte-identical to the coder's literal (three-way equality). | Delete one header (`## What is UNTESTED`) from coder workflow; change the literal in reviewer | `FAIL A6 missing handoff header "<h>"` / `FAIL A6 HANDOFF path drift: <a> vs <b>` |
| A7 | blast-radius predicate globs — **all 14, both files** | Static: extract the block between the `# ---- FROZEN [sonic-blast-radius-v1] ----` markers from `agents/sonic-leader/workflow.md` AND `agents/sonic-coder/workflow.md`; assert the 14 lines in exact order: `daemon/graph.py` · `daemon/manager.py` · `daemon/config.py` · `daemon/api.py` · `daemon/services/**` · `daemon/repositories/**` · `daemon/tools/**` · `daemon/migrations/versions/**` · `daemon/sources/**` · `daemon/mcp/**` · `daemon/routers/**` · `scripts/**` · `agents/**/*.md` · `agents/.prompt-artifacts.sha256`; `diff` each extracted block to phase1 §4.0 and to each other ⇒ **zero diff** (phase1 A9). No subset enumeration. | Delete one glob line (`daemon/routers/**`) ⇒ RED; reorder two lines ⇒ diff non-zero ⇒ RED | `FAIL A7 blast block drift: <file> (n/14, first diff <a> vs <b>)` |
| A8 | Ari `rule.md` pin survival | (a) Whole-file case-insensitive `plane` count in `agents/ari/rule.md` == **exactly 1** and that hit is the backing note (`_BACKING_NOTE_TOKEN = "backed by Plane"`, `tests/unit/test_ari_pm_terminology_pins.py:74`); `workflow.md`/`soul.md` == 0 — enforced by RUNNING that test file (Layer B). (b) Seal consistency: `ast.literal_eval` the `SEALED_SHA_BASELINES` dict from `tests/test_chart_image_delivery_e2e.py:1425` and assert `SEALED_SHA_BASELINES["agents/ari/rule.md"] == sha256(agents/ari/rule.md)` (current pinned value at base: `fc0a7542157d79d16011a48d7e79d31d33fe1376ca5fc5c694d47e670bcfdf10` — Phase 1's note edit MUST re-pin this row, prior value was `8063c9ce…`; `decisions.md` DEV-2). (c) Baseline: `agents/.prompt-artifacts.sha256` contains one row per `agents/sonic-*/{soul,rule,workflow,tools_note}.md` (16 rows) and total line count == **197** = 181 + 4×4 (`wc -l` at base: 181). Note DEV-1: the Phase-1 regen ALSO repairs 8 pre-existing drift rows (ari/rule.md, developer+developer[v2] rule+workflow, leader/rule+workflow, tester/workflow) — the count stays 197; after regen the generator's `--check` must exit 0 (that is the repair proof). | Append `<!-- Plane -->` to `ari/rule.md`; revert the seal row to `fc0a7542…` after the note edit; delete one sonic baseline row | `FAIL A8 rule.md plane count=<n>` / `FAIL A8 seal stale for agents/ari/rule.md` / `FAIL A8 baseline rows=<n> expected 197` |
| A9 | steady team untouched | `git diff --name-only ac9849bf7..HEAD -- agents/` → the set must equal `{agents/ari/rule.md} ∪ {agents/.prompt-artifacts.sha256} ∪ {agents/sonic-*/…}`; i.e. after excluding `agents/sonic-` and `agents/.prompt-artifacts.sha256`, exactly `agents/ari/rule.md` remains. (Note the baseline file lives **under** `agents/` — it is an allowed edit, not a steady-team change.) Also assert the sonic dirs ARE present in the diff (positive shape — an empty diff must not pass). | Edit `agents/leader/rule.md` (steady team) ⇒ extra path ⇒ RED | `FAIL A9 unexpected agents/ edit: <path>` |
| A10 | escalation line — full frozen sentence | Static: `agents/sonic-leader/rule.md` contains the complete frozen line from phase1 §4.0 `[sonic-escalation-v1]`: `ESCALATION: <path> matches <glob> — returning commission to Ari (steady-team routing).` — i.e. beyond the `'ESCALATION: '` prefix (phase1 A10 checks the prefix only). Tonight the full-line fidelity is ALSO covered by content review (plan-review advisory #2); a diff-based full-block check is recorded as an implementation-time hardening item (not blocking). | Corrupt the suffix (`returning commission to the agent`) ⇒ RED on the full-line check | `FAIL A10 escalation line drift` |
| A11 | Ari note verbatim | Static: `agents/ari/rule.md` contains the heading `### Sonic Lane (Opt-In)` + these three frozen anchors (phase1 §5.1): ``Delegation defaults stay with the steady team (`leader`).`` · `opt-in by POSITIVE selection only` · `escalates the commission back to me with its reason`; the note region contains ZERO case-insensitive `plane` and no `## Cardinal`. Full-body byte-diff vs phase1 §5.1 is the content-review check tonight; a diff-based check is the implementation-time hardening item (plan-review #2). | Rename the heading / drop an anchor / inject `Plane` into the note ⇒ RED | `FAIL A11 Ari note heading/anchor missing` / `FAIL A11 plane in Ari note` |

Scope note for A8(a): the negative greps are whole-file **by design** (the test's own invariant, its docstring) and are scoped to the three Ari prompt files only — the pack must not grep `Plane` across new sonic files (a mention there is harmless and must not redden).

## 4. PACKS.md registry row (packconv-03: no row ⇒ never runs)

**Naming alias (binding consistency note):** the plan ledger and overview call this the **`sonic_agent_defs` pack** (`decisions.md` D9; `plan-overview.md` §4/§8). The script + PACKS.md row id follow the dispatch and the house suffix convention: **`sonic_agent_defs_unit_test`** (`test/packs/sonic_agent_defs_unit_test.sh`). Both refer to the same pack; the registry row carries the full script name.

Append ONE row to `.agents/tester/PACKS.md` (row format observed at `PACKS.md:22` and e.g. `:2948`; newest-first within the section, keep-both on conflict). Draft:

```
| `sonic_agent_defs_unit_test` (NEW — ENSEMBLE-31 sonic team v1) | `cd $WT && timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh` (self-cap 240 s; static layer stdlib-only; real-resolver layer via `uv run`; per-variable `POSTGRES_*` scrub — never the glob form) | the 4 new `agents/sonic-*/` definitions (meta.json parse/team_members/tool-resolution/llm_model=agentic/coder-only ×7 multiplier/handoff schema/blast-radius globs) + `agents/ari/rule.md` note + exactly-1-`Plane` + seal re-pin + prompt baseline 181→197 — A1–A11 (incl. escalation full line + Ari-note verbatim), each with a counter-mutation leg | <run date> | <⏳ registered — first run pending / ✅ PASS n/n — commit <sha>> |
```

Registration AC: `grep -c 'sonic_agent_defs_unit_test' .agents/tester/PACKS.md` ≥ 2 (row + invocation text) and the row is inside a table section. Do NOT rewrite existing rows; the file is 950 KB — append only.

## 5. Mutation-proof plan (non-vacuity)

1. `--self-test` runs the mutation matrix **by default** (cheap, ~1 s): each assertion function is fed synthetic in-memory fixtures with ONE planted defect and must raise/return FAIL. No repo bytes touched (precedent: `tests/unit/test_ari_pm_terminology_pins.py` header — "each plants ONE defect into an in-memory copy … no repo bytes are touched").
2. Leg list = the counter-mutation column of §3 (A1–A11), one leg per assertion minimum. A leg that does **not** redden its assertion is a FAIL of the self-test (not a skip).
3. For A2/A3 the real-resolver layer is itself the positive shape (a pure static mirror is prose-satisfiable); the counter-mutation for the resolver legs is the meta fixture planted in the static layer, and the real leg asserts the same expectation through `_check_team_membership`.
4. Disclose honestly: if `uv`/venv is unavailable in a given tree, the pack FAILS with `RESULT: FAIL (real-resolver)` — record that as a gate-blocking condition, never as coverage.

## 6. Runtime budget, invocation, acceptance criteria

- Budget: static layer < 5 s; real layer ~5–15 s warm venv; terminology pins ~5–10 s (23 nodes). **Inner 240 s / outer 300 s.** Cold `uv sync` in a fresh worktree can exceed the inner budget — warm the venv once (`uv sync` per `dev.sh` lazy rule) before the gate and disclose it (VERIFY-AT-IMPL).
- Invocation: `timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh` from the worktree root (or `cd $WT && …`).
- ACs:
  - AC-P2-1: pack script + checker exist at the two paths above; `bash -n` clean; registered row present (§4 AC).
  - AC-P2-2: `--self-test` green and every §3 counter-mutation leg demonstrably RED when planted (evidence: self-test output listing each leg id).
  - AC-P2-3: pack PASS on `feature/sonic-team-v1` tip **and** on `latest` after the Phase-4 merge (ancestor drift pin, not branch-name pin).
  - AC-P2-4: A8 baseline line count == 197 and `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` exits 0 on the branch tip.
  - AC-P2-5: no repo bytes are mutated by any run (working tree clean before/after; `git status --porcelain` empty).

## 7. Deviations & evidence conflicts

1. **Drift-pin form deviates from the house sample.** `embed_consumers_unit_test.sh:31-37` pins branch NAME equality. This pack deliberately uses `merge-base --is-ancestor ac9849bf7 HEAD` (like the ancestry half of that precedent) because it must also gate the post-merge `latest` tree and the staged tree; a branch-name pin would make the pack unrunnable at exactly the moment the gate matters.
2. **Dispatch said "`Ari rule.md` pin survival: exactly-1 'Plane', seal pins updated" — the seal is NOT in the terminology test.** The sealed hashes live in `tests/test_chart_image_delivery_e2e.py:1425 SEALED_SHA_BASELINES` (consumed by `TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts`, `:1612`), and the current `agents/ari/rule.md` pin at base is `fc0a7542…`, **not** the `8063c9ce…` quoted in the project's critical-note metadata (the metadata is stale by one re-pin, 2026-11-12 vsss pass). The pack asserts the FUTURE value equals the file's actual sha (self-consistent), so no version of this conflict can silently pass.
3. **A2/A3 "exact set" vs implicit members — RESOLVED by phase1 §3.5.** The frozen `[sonic-tools-v1]` allow lists contain NO agent-backed category (`knowledge`/`council`/`design`/`chart`/`image` are all absent) ⇒ **ZERO implicit members**; effective spawn sets are leader = exactly the 4, the other three = ∅. The earlier implicit-expansion assumption is retired. A3 now asserts the frozen arrays verbatim with **absent keys normalized to `[]`** (phase1 §3.5 normalization rule; review advisory #13 — a naive `== []` against a missing `deny` key KeyErrors; `sonic-coder` carries `deny: []` BY OMISSION).
4. **A6/A7 frozen constants — RESOLVED (phase1 landed 20:22).** A6 now asserts the exact Title-Case `##` headers + `HANDOFF_PATH = HANDOFF.md` from `[sonic-handoff-v1]`; A7 asserts ALL **14** globs of `[sonic-blast-radius-v1]` extracted between the markers in BOTH `sonic-leader/workflow.md` and `sonic-coder/workflow.md`, diffed to phase1 §4.0 and to each other (zero diff) — no subset enumeration (review advisory #1/#1b). A10/A11 (escalation full line; Ari note heading+anchors) are now concrete; their full-body/diff-based forms are recorded implementation-time hardening items (review advisory #2).
5. **Baseline count derivation assumes 4 artifacts per sonic agent.** `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py:119-130`) also contains `tools.md`, `skill.md`, `memory.md`, `growth.md`; the 181→197 arithmetic holds only if each sonic agent ships exactly `{soul, rule, workflow, tools_note}.md` (v1 = minimal 4-artifact set, `decisions.md` open decision (g)). If Phase 1 adds e.g. `memory.md`, the expected count changes — VERIFY-AT-IMPL by running the generator once and pinning its actual count (the pack's constant must be updated together with the row, never a floor). Separately, the regen repairs 8 PRE-EXISTING drift rows (`decisions.md` DEV-1: 8/181 rows drift at `ac9849bf7` — vsss-era re-pins did not regenerate the baseline); that repair changes row VALUES, not the count, and `--check` rc=0 afterwards is its proof.
6. **Pack-id alias** — ledger/overview say `sonic_agent_defs`; the dispatch + house naming say `sonic_agent_defs_unit_test` (script/row id). Recorded in §4; the morning report should cite both so the tester does not search for a non-existent `sonic_agent_defs.sh` (POK `prompt-assent-03-paths-use-assent-not-assist` is the same class of path/name trap).
