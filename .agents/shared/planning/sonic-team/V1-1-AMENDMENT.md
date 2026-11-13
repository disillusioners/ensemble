# Sonic Team v1.1 — Knowledge-Grant Amendment (2026-11-13)

**Lane:** Emperor's fast amendment lane · **Worktree:** `/home/nea/ensemble-src-wt-sonic-v11` (branch `feature/sonic-team-v1-1`, base `ee2b46d3b`) · **Scope:** prompts + allow-lists + pins + harness docs ONLY — no staging, no daemon boot, no live contact, steady-team agent dirs untouched (`agents/leader|developer|reviewer|tester|ari` verified unmodified; ari/rule.md seal `8d6ca164…` untouched).

**Ruling:** Emperor, 2026-11-13 — v1's zero-knowledge default (D8) superseded for sonic-coder + sonic-leader; reviewer + tester UNCHANGED.

## 1. What changed (diff summary)

| File | Change |
|---|---|
| `agents/sonic-coder/meta.json` | `tools.allow` + `"knowledge"` (positioned before `pok`; `nest-report` stays LAST — grant-pack pins preserved) |
| `agents/sonic-leader/meta.json` | same grant |
| `agents/sonic-coder/workflow.md` | Step 1 Explore = explore-at-start discipline (see §3 for the verbatim language) |
| `agents/sonic-coder/rule.md` | Write discipline reworded: ONE opening `explore()` allowed; `experience()`/kb-writer still forbidden (contradiction fix) |
| `agents/sonic-coder/tools_note.md` | `knowledge` row added (read-once boundary; writes belong to sonic-leader) |
| `agents/sonic-leader/workflow.md` | Intake (e): bounded explore-on-decomposition (optional, ONE call); new §6 "Knowledge moment — write-once-at-end"; Final Report renumbered §7 |
| `agents/sonic-leader/rule.md` | Rule 6 reworded: the single completion-time `experience()` call replaces "zero experience() calls" (contradiction fix); rule 7 report-scrutiny untouched |
| `agents/sonic-leader/tools_note.md` | `knowledge` row added to the held-category table; removed from the absent list (contradiction fix) |
| `test/packs/sonic_agent_defs_checks.py` | A3 lockstep: FROZEN_TOOLS (+knowledge on leader/coder), EXPECTED_EFFECTIVE (leader/coder gain implicit `explorer`+`kb-writer` via `TOOL_REQUIRED_AGENTS["knowledge"]`), mutation legs re-aimed (§5) |
| `agents/.prompt-artifacts.sha256` | regenerated: 197 → 197 rows (no new files); exactly the 6 touched sonic `.md` shas moved |
| `.agents/shared/planning/sonic-team/decisions.md` | knowledge-grant amendment appended (D8 SUPERSEDED record; historical phase1 spec NOT rewritten) |
| `.agents/shared/planning/sonic-team/phase3-dryrun-spec.md` | V9–V11 presence assertions + §9 amendment (transcript-fetch pin, timing attribution, learning-designed scenario) |
| `.agents/shared/planning/sonic-team/dryrun-boot-procedure.md` | AMENDMENT 2026-11-13 block: literal `GET /api/instances/{id}/messages` fetch + count/timing one-liners + cost facts |
| `agents/sonic-reviewer/*`, `agents/sonic-tester/*` | UNCHANGED (still zero-knowledge: no `knowledge` in either allow list) |

## 2. kb-writer lane finding — verdict CHEAP

- **`explore()`** = one explorer-agent turn. Explorer's own pin: `llm_model: "quick"` (`agents/explorer/meta.json`). One bounded RAG query + synthesis; no fan-out.
- **`experience()`** = a ZERO-LLM caller turn + async kb-writer spawn (kb-writer also pinned `quick`). The caller does not block on an LLM generation for the write itself.
- **Edge (must-know):** `agents/explorer/meta.json` carries legacy `caller_model_overrides: {"coder": null}` — the map is keyed by the CALLING agent's literal `agent_id` (`instance_lifecycle.py` generalized explorer-scoped overrides; key = parent/caller id). It matches ONLY the steady-team `coder` agent id. `sonic-coder` NEVER matches (its id is `sonic-coder`) — so the sonic lane always spawns explorer on its own `quick` default; no override path is exercised. No action needed; recorded so nobody "fixes" the map for sonic.
- **Net:** the v1.1 grant adds at most ~2 cheap `quick`-lane calls per commission (1 mandatory coder explore; ≤1 leader explore + ≤1 leader experience). This is not the v1 kb-writer flood D8 feared (975 spawns = 46.4% of all instances) — the caps are prompt-enforced AND the writes are capped at one per commission by rule 6 + workflow §6.

## 3. Verbatim discipline language (as landed)

**sonic-coder explore-at-start** (`agents/sonic-coder/workflow.md`, step 1):

> **Explore.** Open EVERY commission with exactly ONE bounded `explore()` call against the knowledge base — before reading any code. Stored experience is the most valuable read in the system: it is where prior commissions' decisions and gotchas live, and the call is cheap. The bound is hard: ONE `explore()`, not a research loop — no iterating, no re-querying, no follow-up knowledge calls. Then read the dispatched task context and the code it names. Exit when you can state, in one sentence, the change you intend to make and the files it touches.

**sonic-leader write-once-at-end** (`agents/sonic-leader/workflow.md`, §6):

> At commission completion — after the commit dispatch resolves — you get exactly ONE knowledge moment: at most ONE `experience()` call, made ONLY when the commission taught something non-obvious (a gotcha, a reusable pattern, a correction to what the knowledge base believed). Nothing learned → record nothing: the absent call is the correct outcome for a routine run. The cap is hard — one call per commission, at the end. No per-lane knowledge ceremony, no mid-run writes, and no asking the coder or verifier lanes to record experience on your behalf.

## 4. Pin lockstep (counter-mutation proven)

`test/packs/sonic_agent_defs_checks.py`:

- `FROZEN_TOOLS` leader/coder allow + `"knowledge"` (reviewer/tester unchanged).
- `EXPECTED_EFFECTIVE`: leader `{sonic-coder, sonic-reviewer, sonic-tester, giter, explorer, kb-writer}`; coder `{explorer, kb-writer}` — `TOOL_REQUIRED_AGENTS["knowledge"] = ["explorer", "kb-writer"]` (`daemon/tools/_auth.py`) makes these implicit members by design, not drift.
- Mutation legs: the v1 leg `A3-leader-add-knowledge` became the GOOD state, so it was replaced by **4 legs**: `A3-leader-drop-knowledge`, `A3-coder-drop-knowledge`, `A3-reviewer-add-knowledge` (reviewer stays zero-knowledge), `A3-effective-drop-required-row` (pops the `TOOL_REQUIRED_AGENTS` mapping — proves the effective-set pin is a POSITIVE shape, not prose). Self-test: good fixture GREEN on A1–A11; all legs RED (27 legs, `SELF-TEST: PASS`).

## 5. Ceremony plan — v0.30.1 (RECIPE ONLY — NOT EXECUTED; the dry-run re-proof gates it)

1. **Gate first:** dry-run re-proof per the amended phase3 spec (V0–V11) on this branch — PASS is the precondition for any staging.
2. **Bump:** from a clean checkout of this branch tip: `.venv/bin/python scripts/bump_version.py patch` (v0.30.0 → v0.30.1).
3. **Tag:** annotated tag on the bump commit; push is giter/lane-owned (NOT this lane's act).
4. **Stage worktree:** clean stage worktree AT THE TAG (never at a dirty tip); fenced `.env`; its own `.venv` (never another checkout's).
5. **FE pre-build:** frontend build before stage.sh so the FE bundle is present.
6. **Stage:** `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.30.1 stage.sh live` — wait for the receipt (STDOUT-only; releases/state.json journals promotes only).
7. **Verify:** 4-way sha equality (release dir == dist == manifest == sidecar) + `generate_prompt_artifact_baseline.py --check --tree <release>` (baseline rides the release) + stage-verify pack.
8. **Promote:** the Emperor's — NOT part of this lane, NOT assumed.

## 6. Evidence pointers

- Pack static layer: `PASS A1–A11` (incl. A8 generator `--check --tree`, rc=0, 197/197).
- Pack self-test: `SELF-TEST: PASS` (good fixture GREEN ×11; counter-mutation legs RED ×27, incl. the 4 new A3 legs).
- Anomaly duty pins: `tests/unit/test_anomaly_report_duty_prompts.py` + `test_anomaly_report_grant_pins.py` (both green post-edit; duty block byte-identical, still LAST section in both touched rule.md files).
- Baseline: before 197 → after 197 (`--check --tree` rc=0); delta = 6 sonic `.md` sha updates, zero row adds/removes.
- Commits: `git log --oneline ee2b46d3b..HEAD` (this branch; targeted pathspec commits only).
- Ruling + dispatch: Emperor ruling 2026-11-13 (sonic v1.1 fast amendment lane).
