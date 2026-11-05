# FUNCTIONAL GATE — commission `aegis-security-family` (Phase 5, plan-locked Gates A–H)

**Date:** 2026-11-05
**Verifier:** tester (this lane's Phase-5 functional gate)
**Branch / tree verified:** `feature/aegis-security-family` @ `eff1ff27fa237476af00878a3538428c47cdb30d` (worktree `/home/nea/ensemble-src-wt-aegis-security-family`)
**True pre-fix base:** `1f6898ae2889ff002b4656486f6be3ee1048b649` (= `origin/latest` at lane start; ancestor-verified). Lane = **24 commits** over base (`git rev-list --count`); committed diff = 47 files, +12235/−11.
**Plan (locked, authority):** `.agents/shared/planning/aegis-security-family/phase5-plan.md` + `plan-overview.md` §"Verification gates" (:145-157).
**Verdict: GO.** No lane-attributable blocker. Net-new deterministic reds = **0**. Two gates carry *disclosed pre-existing base debt* (B, D-2) and one carries a *disclosed stale-predicate tolerance* (H) — none is lane-attributable.

---

## 1. Summary

| Item | Value |
|---|---|
| Plan gates executed | 8 (A–H) + baseline/AM-11 legs + guide-§10 token sweep |
| Gate verdicts | **A PASS · B PASS-W-DISCLOSURE · C PASS · D PASS-W-DISCLOSURE · E PASS · F PASS · G PASS · H PASS-W-DISCLOSURE** |
| Lane-caused net-new deterministic reds | **0** |
| Pre-existing base reds disclosed (by name) | 4 sealed-artifact entries + 5 prompt-section-reference ids (+ their 4 base-leg id mirrors) |
| Non-deterministic (load/flake) HEAD-only ids | 45 (44 green 3/3 in isolation + 1 known `manager_shutdown` flake) |
| Base-only ids (environment artefacts) | 63 (all classified: worktree-name guard, conftest mock lockstep, checkout-dependent, flaky, id mirrors) |
| Lane suite | `tests/unit/agents/test_aegis_security_family.py` — **395 collected / 395 passed / 0 failed** at HEAD (dev claim confirmed exactly) |
| Mutation proofs | 2/2 (T29 legality cell, T30 per-bullet presence) — both RED→restore→GREEN |
| Boot-scan | PASS (4/4 ids registered in a real source-mode boot, zero parse failures) |
| Scope | **Full plan scope executed** — no reduction (the plan is locked) |
| Quick fixes by tester | **none** (verification-only phase; the only writes are `.agents/tester/**` documentation) |
| Repo-file modifications by verification | **none** (evidence + this report + PACKS.md + LESSONS only) |
| Live contact | **none** (:9797 / `/home/nea/agents-ensemble` / `ensemble_prod` / `releases/` / `state.json` untouched; boot used a scratch port + scratch DATA_DIR + `ensemble_dev`) |

## 2. Per-gate results

### Gate A — no behaviour regression → **PASS** (net-new deterministic reds = 0)

House method (FAILED-list **diff** of identical commands, not a raw count), executed per `ensure.md` Gate-Tiers doctrine:

* **Tier mapping:** no core-seam path in the diff (`daemon/graph.py|manager.py|api.py|instance_messaging.py|task_processor.py|job_queue_service.py` — lane diff touches **0 `daemon/` files**), so the mechanical Tier-2 trigger does not fire; the **locked plan** nevertheless mandates whole-collectable-suite scope, which is Tier-2 *form* (≤90 min advisory, fail-loud). Both legs ran the recipe form from `.agents/tester/skills/whole-tree-base-proven-diff/` + `failed-digest-equality-gate/` (mandatory recipe load, performed before any run). Release-Gate live-daemon E2E items were **not** run — out of the locked plan's gate set, and this diff is agents-only (see §6 OQ-3).

| Leg | Tree | Collected | P / F / E / S / xf | Closure | Shards | Digest |
|---|---|---|---|---|---|---|
| HEAD | `eff1ff27f` (lane worktree) | 33,843 (1,145 deselected) | 33,037 / 444 / 22 / 334 / 6 | **exact** | 11 (greedy ≤3300 nodes) | `/tmp/aegis-head-ids.txt` — 466 BARE-IDs, sha256 `2ae2ea8e173693703f0a9e198d732d4c0293da82e7c51267da4bdc50e67e7cc0` |
| BASE | `1f6898ae2` (separate worktree `/tmp/aegis-base-wt`) | 33,324 (1,145 deselected) | 32,507 / 455 / 25 / 331 / 6 | **exact** | 11 (rule re-derived at base) | `/tmp/aegis-base-ids.txt` — 480 BARE-IDs, sha256 `a7dc46fdfdd32fe11baa093c8939aa36dc8e9f02c99933b26fe9dfe08dd89198` |

* Same command form both legs: per-variable env scrub (never the `env -u POSTGRES_*` glob no-op) + `OPENAI_BASE_URL=http://127.0.0.1:9/v1` stub + `QUEUE_LLM_RETRY_*=1` + `PYTHONDONTWRITEBYTECODE=1`; `timeout 1750` per shard; `-p no:cacheprovider -q --tb=line -rEf --timeout=30 -n auto`; no `-x`; verdict greps on FILES.
* No shard death, no rc=124, no mid-shard timeout → **zero gaps**; projection guard: 1,035-1,101 nodes/min measured → ~32 min projected vs 90-min advisory (no breach). HEAD wall ≈35 min, BASE wall ≈71 min (isolation runs dominated).
* **C1(d) ledger — the pre-registered fold:** `test_maintenancer_agent.py::TestLeaderTeamMembership::test_leader_team_members_length_16` — **GREEN at base** (roster 16 == pin 16) and **GREEN at HEAD** (roster 17 == pin 17 via the in-commit C1(b) fold). Absent from both digests → **no flip signature**. (Cosmetic: the function *name* still says `length_16` — owner-lane item R5.)
* **49 HEAD-only ids**, fully classified:
  * **4 deterministic** → `test_prompt_section_reference_integrity` ids `workflow.md12`, `workflow.md19`, `rule.md4`, `workflow.md4` — **path-proven pre-existing**: they resolve to `agents/developer/workflow.md`, `agents/developer[v2]/workflow.md`, `agents/charter/rule.md`, `agents/charter/workflow.md`, all **byte-identical git blobs at both revs** (ea144bff/ea144bff/50b5ae87/38a04ba9) and untouched by the lane diff. Their BASE-leg mirrors (`workflow.md11/17`, `rule.md3`, `workflow.md3`) resolve to **the same four files**. The id shift is pytest's name-indexed dedup over an `rglob` of `agents/` — the lane's four new agents shift the index. → same pre-existing debt surfaced under shifted ids; **zero lane attribution**.
  * **45 non-deterministic** → 44 green 3/3 in isolation (shard-composition/load artefacts) + 1 known flake (`test_manager_shutdown.py::…test_durable_sweep_sibling_alive_skip_and_dead_writer_swept` — matches the `manager_shutdown` flake-ledger entry).
* **63 base-only ids** → all environment/base-leg artefacts (worktree-name-in-temp-dir guard ×5, conftest `_mock_modules`/`LANGGRAPH_MOCK_KEYS` lockstep ImportError ×3, documented checkout-family artifact ×1, checkout-dependent ×1, context-sensitive-both-legs ×1, id-mirror ×4, flaky ×2, remainder green-in-isolation). None net-new.
* Headline: **net-new deterministic reds = 0**.

### Gate B — sealed artifacts untouched → **PASS-WITH-DISCLOSURE** (pre-existing red, base-proven)

* Lane obligation **satisfied**: `git diff --stat 1f6898ae2..HEAD -- tests/test_chart_image_delivery_e2e.py` = **empty** (the lane never touched the sealed test file), and **no sealed entry references any aegis path** (all 33 other entries match).
* The leg itself is **red on 4 of 35** `SEALED_SHA_BASELINES` entries — `agents/ari/workflow.md`, `agents/ari/rule.md`, `daemon/sources/registry.py`, `daemon/constants.py`. Blob-level proof: each is **byte-identical BASE↔HEAD** (`git diff --quiet` clean for all four) → the **seal map was already stale at the true base** (known base-proven seal-tripwire class). The id is red on **both** legs (absent from both comm outputs) → confirmed empirically on the base leg, not inferred.
* Plan note: the plan expected "Green"; that expectation was **factually stale vs the true base**. Adjudicated as base debt under the plan's own Gate-A doctrine ("a red that is red at the base too is pre-existing debt and is disclosed, not fixed"). Re-pin owed to the seal/release lane → **R1**.

### Gate C — plane read-holder census unchanged → **PASS**

* `tests/unit/tools/test_plane_agent_surface_census.py` → **15 passed**.
* `DISCLOSED_READ_HOLDERS` read **from the file** (not inferred from a green): `frozenset({"watcher"})` — the aegis family was **not** whitelisted (P5-R4 avoided; the E3/E4 folds removed the exposure instead).
* Four new metas, verified by direct JSON read:

| agent | tools.allow | tools.deny | deny_spawn | team_members | mcp_full_access |
|---|---|---|---|---|---|
| aegis | **15** | **6** | `['knowledge']` | `[hacker, cracker, sweeper, worker]` | none |
| hacker / cracker / sweeper | 6 each | 2 each (`write_file`, `edit_file`) | none | `[]` (leaf) | none |

No empty allow-list, no `mcp_full_access` anywhere → matches DP1 exactly.

### Gate D — report-integrity fold + repo-wide prompt sweep → **PASS-WITH-DISCLOSURE** (pre-existing debt)

* **D-1** `tests/unit/test_report_integrity_prompts.py` → **52 passed, 23 skipped** (aegis's scrutiny guidance found by the dynamic check; the three children exempt — empty teams).
* **D-2** `tests/unit/tools/test_prompt_section_reference_integrity.py` → **5 failed / 1582 passed**; all five implicate files **byte-identical BASE↔HEAD** (`agents/developer{,[v2]}/workflow.md` bare-`.md` tokens ×3, `agents/charter/skills-template/install-mermaid-cli.md` ×1, odd-fence-count in `agents/charter/rule.md` + `workflow.md` ×2). Collector census: 261 `.md` files scanned, of which **exactly 20 are lane-added** and the other 241 are **byte-identical BASE↔HEAD** → **the lane's 20 new files produce ZERO failing ids**. Base leg corroborates the same four files under shifted ids (Gate A). Charter/developer prompt debt → **R2**.

### Gate E — boot-scan + T29 legality matrix + T30 installer-guard pins → **PASS**

| Leg | Invocation | Result |
|---|---|---|
| registry (T28) | `pytest tests/unit/agents/test_aegis_security_family.py -q -k registry` | **6/6 pass** — all four ids resolve; each `tools` block round-trips `ToolFilter.model_validate` (fail-open guard = the load-bearing assertion: a malformed block parses to `None` = ALL TOOLS) |
| legality (T29) | same file `-k legality` | **22/22 pass** — **20 asserted cells = 5 families × 4 personas**; cracker = synthetic benches; aegis/hacker/sweeper = no real-credential ingestion/transcription |
| fences + library-contract (T30) | same file `-k "fences or library_contract"` | **71/71 pass** — per-bullet presence 27 nodes (9 bullets × 3 bodies) + checksum EXECUTED / unset-pin refused / no docker·apt·sudo / trap-based lock release / attempt cap + corpus-path guard + sandbox-root write gate + readiness 3-state probe + reversal section + T23 secondary (lib header keywords 15) |
| mutation 1 (T29 approver acceptance) | delete `agents/hacker/rule.md:10` "No external targets, ever" cardinal | **RED as predicted**: `test_legality_cardinal_pinned_verbatim[hacker-sandbox-zero-external]` + the anchor self-proof; the other 20 cells stayed green → **cell-level specificity**; restore → 22/22 green |
| mutation 2 (T30 approver acceptance) | delete the NO-Docker bullet from all three installer `.md` bodies | **RED as predicted**: 3× `test_fences_bullet_present_in_each_body` + the A-7 **count** clause — while the cross-body **identity** clause still held (read-only decomposition: `lists[0]==lists[1]==lists[2]` True). **Stronger than the plan prose**: the uniform deletion is caught by two independent layers (the plan expected byte-identity to survive → it does, as a clause, but a second clause fires). Restore → 71/71 green |
| isolated boot-scan | real source-mode daemon boot, scratch port 18090, scratch DATA_DIR, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned** (verified from the daemon's own `/proc/<pid>/environ`), `POSTGRES_DB=ensemble_dev` (≠ prod, asserted pre-boot), `pg_stat_activity` cross-check | **PASS** — banner `Starting Ensemble v0.28.11`; census `GET /api/agents` = 43 agents with `aegis`/`cracker`/`hacker`/`sweeper` present and `agent_dir` pointing at the **worktree** tree; **0** `Failed to parse meta.json` / `Failed to parse tools config` / `Failed to create AgentMetadata`; exactly **1** warning (maintenancer `git_commit` deny entry) — **base-proven** (`git diff --stat BASE..HEAD -- agents/maintenancer/` empty); graceful SIGTERM teardown in 2 s, port freed, PID gone, scratch dir removed, live 9797 uptime continuous; **1** inert dev-DB row (aegis-strategy skill-bank seed) disclosed |

### Gate F — no tool-category drift (F-1 proof) → **PASS**

`git diff --stat 1f6898ae2..HEAD -- .agents/shared/metrics/tool-categories.json` = **empty** (byte-unchanged) and `tests/unit/tools/test_tool_category_artifact.py` → **9 passed**. The F-1 trap class (stale committed artifact reddening a whole lane) is avoided — the lane registers **no** new category.

### Gate G — manifest delta re-derived from the real tree → **PASS**

`find agents -type f | wc -l` = **366** (= 338 + 28) and `git diff --name-status 1f6898ae2..HEAD -- agents/ | awk '$1=="A"' | wc -l` = **28** additions (7 files per new agent). Both numbers re-derived from the tree, not trusted from the plan.

### Gate H — scope + privacy (lane-own committed diff) → **PASS-WITH-DISCLOSURE** (one stale-predicate tolerance)

| Leg | Result |
|---|---|
| anchors | PASS — HEAD `eff1ff27f`, branch correct, base is ancestor, lane = 24 commits, diff 47 files (+12235/−11) |
| scope complement | PASS-W-DISCLOSURE — complement (13) = planning set (12) **+ `.agents/tidier/notes.md`** |
| secret scan (R5 fail-closed) | PASS — `git diff 1f6898ae2..HEAD > /tmp/aegis-diff.txt` rc=0 (12,567 lines / 897,110 B) then `grep -nE 'sk-(proj|ant)-|BEGIN [A-Z ]*PRIVATE KEY'` → **zero hits**; broader shape sweep → zero. Conclusive, not vacuous |
| worker byte-identity | PASS — `git diff --stat BASE..HEAD -- agents/worker/` empty; tree object `bbf3b8a6672eb61eb396321b9efa39a1d6d4462f` identical at both revs (6 files) |
| artifact scan | PASS — zero corpus/crash/dump/log/db/screenshot paths; one disclosed name-hit = `planning/aegis-security-family/research-findings.md` (a planning **document**, not an artifact); content-level vocabulary hits are the lane's **off-repo runtime layout prose** (findings live at `$HOME/.local/state/aegis/engagements/<id>/`) |
| foreign-file hygiene | PASS — `install-audit.jsonl` is **working-tree-only** (0 hits in the committed diff), sha `ab417083…` unchanged pre/post, `git status --porcelain` identical at start and end (single foreign entry) |

**The one tolerance (disclosed, pre-authorized):** `.agents/tidier/notes.md` arrived in commit `9a9216377` (tidier craftsmanship-pass note, **~5 h after the Gate H plan texts were frozen**). The plan's own texts name only `.agents/approver/**` as a carve-out (phase5-plan.md:38, :113-116; plan-overview:156; phase4-plan.md:61 G4.7 "14 vs 12"), and the plan's frozen arithmetic is now stale by exactly one path (15 vs 12). Evidence that it is a **house-conventional lane-review artifact, not creep**: the tidier's declared write scope is `.agents/tidier/` only (`agents/tidier/rule.md:22`), `notes.md` is its canonical aggregated review record (`tools_note.md:64`, `workflow.md:287`), the content is lane-scoped (13 lines), and the **immediately preceding lane** (central-metrics-v1) appended the identical artifact in the identical shape (`c12f1fc8c`, +13 lines). The commissioning dispatch names "the tidier notes entry" as part of the tolerated set. **The locked plan was NOT amended** (not the tester's to change) → the deviation is surfaced here instead (§6 OQ-2).

## 3. Baseline + prompt-surface legs (dispatch items)

* **Baseline 177/177 + AM-11:** `generate_prompt_artifact_baseline.py --check` → exit 0 (`PROMPT-BASELINE: OK — 177 versioned artifact(s) match`); **file exists** (AM-11: rc=0 alone proves nothing) and `wc -l` = **exactly 177** ✓.
* **Guide §10 token sweep (two classes) over the lane's 20 new `.md` files:** Class A = system internals (10 tokens, taken from the house constant `SYSTEM_INTERNAL_TOKENS`); Class B = path/cross-reference tokens (bare `.md` filenames + the bare-`agents/`-prefix regex `BARE_AGENTS_PREFIX_RE`). **0 hits / 0 hits**; supplementary §10 item-10 marker sweep → 0 hits.
* **Seal static leg:** `git diff --stat BASE..HEAD -- tests/test_chart_image_delivery_e2e.py` → empty (see Gate B).

## 4. Disclosed residuals carried (verify-presence cheap; NOT fixed here)

Accepted-as-disclosed by leader/council and carried unchanged: rare bash trap-parse failure in the signal path (~0.08%; EXIT-trap lock release shipped as mitigation, T30-pinned) — *not independently re-verified in this gate*; trailing-dot wart class (kernel-refused, hygiene); W-2 diagnosis-split now pinned; corpus-containment family `/bin/sh`-only shape; the tidier's routed + deferred sets. **No new residual was introduced by this gate.**

## 5. Routed items (owners named; nothing fixed in this lane)

| # | Item | Owner |
|---|---|---|
| R1 | `SEALED_SHA_BASELINES` map stale for 4 entries (ari/workflow.md, ari/rule.md, daemon/sources/registry.py, daemon/constants.py) — base-proven tripwire; needs the seal re-pin ceremony (a release-lane write) | release / seal lane |
| R2 | Charter + developer prompt debt: 3 bare-`.md` token hits + 2 odd-fence-count files; plus **param-id instability** (parametrize on repo-relative path so future agent additions stop shifting ids and re-opening this diff every time) | prompt-debt lane |
| R3 | `daemon/persistence.py:326` checkpointer log prints **file-config** values while the effective DSN is env-first → a **false prod-contact signal** for any future fence that trusts that line (use `pg_stat_activity`) | mechanical-file lane |
| R4 | Orphan base worktree `/tmp/ensemble-src-wt-aegis-base` (registered, duplicate of the base sha) left by the wedged first base-leg worker | reaped by this lane's cleanup |
| R5 | `test_leader_team_members_length_16` — function name stale while the pin now asserts 17 (cosmetic) | owner of `tests/unit/test_maintenancer_agent.py` |
| R6 | `/tmp/aegis-base-wt` intentionally left registered as the **replay fixture** (`git worktree remove` rc=128: untracked `.venv`; not forced) — removal is a deliberate, documented retention, not an oversight | this lane (retained) |

## 6. Open questions for the orchestrator (leader) — decisions routed back through you

1. **Gates B and D-2 are red legs whose reds are provably base-proven.** My adjudication: accept as **disclosed pre-existing debt** (consistent with the plan's own Gate-A doctrine and with the house "red-at-base ≠ net-new" rule) → **GO**. The alternative is to require a **seal re-pin ceremony** before Phase 6 — note that a re-pin edits `tests/test_chart_image_delivery_e2e.py`, which is inside Gate H's allowed scope but would change the lane-own diff after this gate measured it (a Gate H re-run would be needed). Charter/developer prompt remediation (R2) is out of this lane's scope either way.
2. **Gate H predicate staleness:** the plan's Gate H tolerance list is one path behind the tree it governs (the tidier commit postdates the freeze). I did not amend the locked plan. Your call: (a) planner folds a `.agents/tidier/notes.md` tolerance line into `phase5-plan.md`/`phase4-plan.md` (Gate H then passes as written), (b) accept as disclosed (current verdict), or (c) have the lane rewrite/drop `9a9216377` — which would rewrite history of a commit that is 2nd-newest and carries gate-anchored content.
3. **ensure.md Release Gate** (live-daemon E2E items: parent→child workflow, pause/resume, terminate/revive, 3-level cascade) was **not** executed — it is not in the locked plan's gate set and this diff touches 0 `daemon/` files (no core seam). If you want promote-blocking live coverage, that is a Phase-6/pre-stage call, not a Phase-5 deviation.

## 7. Method deviations from the plan's literal text (intent honoured, deviations disclosed)

| # | Plan text | Executed | Why |
|---|---|---|---|
| D1 | Gate H uses `git diff <BASE>` (working-tree-inclusive) | **committed-range `git diff <BASE>..<HEAD>`** for every Gate H leg | The worktree carries a **foreign** uncommitted WIP (`install-audit.jsonl`) that the working-tree form would falsely flag as creep; the gate's stated scope is the **committed** lane-own diff (phase5-plan.md:38 "this branch's own commits, pre-merge"). Foreign-file working-tree-only status proved separately. |
| D2 | Gate A: one command `.venv/bin/python -m pytest -q --timeout=30` | identical **scope** (default selection) executed via the ensure.md Tier-2 recipe form (11 shards × `timeout 1750`, `-n auto`, per-test `--timeout=30`) | ensure.md Gate-Tiers: mandatory recipe load; "consumers must NOT hand-roll slow runners"; budget discipline. Same FAILED-list semantics, same deselected families. |
| D3 | base leg | separate worktree at the **exact** base sha (plan P5-R1) | as specified ✓ (a second, duplicate base worktree from the wedged first attempt was left by that worker → R4). |
| D4 | expectation "the 5 prompt-section ids appear in NEITHER comm output" | holds at **path level**, provably NOT at literal-id level | pytest dedups duplicate param names with a numeric suffix over a **name-indexed** `rglob` list → adding 4 agents shifts the index. Path-level equivalence proven on both legs (same four files, byte-identical blobs). |
| D5 | dispatch: "base `f5d9f1640`, 11 lane commits" | plan-pinned base `1f6898ae2` (ancestor-verified); lane = **24** commits | `f5d9f1640` is itself a lane commit (close-condition fold), not the base; the dispatch figure was stale. Both A legs and Gate H used the plan-pinned base. |

## 8. ensure.md validation status (scoped by blast radius)

| Requirement | Status |
|---|---|
| No regressions in changed packs / no behaviour regression (Gate A) | ✅ PASS — net-new deterministic reds 0 |
| Gate-tiers doctrine: tier chosen BEFORE the run, mandatory recipe load, node-cap + measured-rate projection guard, pre-exclusion registry with named compensating pins, proof form (declared set → collect-only slicing → identical commands both legs → red-at-base ≠ net-new → 3× flaky isolation → FAILED-digest comparison) | ✅ honoured on the A legs; recipe bodies read before the first run |
| Core: `concurrency_atomic_unit_test` etc. | ➖ out of blast radius (0 `daemon/` files changed) |
| Release Gate (live-daemon E2E) | ➖ not run — see OQ-3 |
| Quarantine-aware | ✅ `.agents/tester/QUARANTINE.md` consulted; no quarantined node appeared in either digest |
| No `-x` | ✅ honoured |

**ensure.md Improvement Notice (user-owned file — surfaced, never edited):** the plan's Gate B and Gate D-2 acceptance criteria ("Green") were written against an assumed-clean base that does not match the true base `1f6898ae2` (4 stale seal entries; 5 charter/developer prompt-debt ids). Recommend the plan text adopt the Gate-A doctrine wording ("green OR provably base-red, disclosed") so future lanes are not blocked by debt they do not own.

## 9. Fence discipline (this gate)

* Live install untouched throughout (:9797 / `/home/nea/agents-ensemble` / `ensemble_prod` / `releases/` / `state.json`); the one boot used a **scratch port (18090)**, a **scratch DATA_DIR**, `ensemble_dev`, and `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (verified from `/proc/<pid>/environ`; the sibling lane's live pytest workers all survived the boot).
* Per-variable env scrub on every invocation; ambient was **live-poisoned** in the agent shell (`POSTGRES_DB=ensemble_prod`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`) and was neutralised each time.
* Foreign `install-audit.jsonl` preserved: the HEAD-leg run appended 27 KMS-audit rows to the lane copy and the BASE-leg run grew the base copy — **both restored byte-exact** from pre-run snapshots (sha re-verified); `git status --porcelain` identical at every worker's start/end.
* No `git add -A`, no `git stash`, no `git clean`, no `checkout --` in the lane worktree; no commits/pushes by any verification worker; no production or prompt file modified by the verification lane.

## 10. Instance roster

| Instance | Role | Outcome |
|---|---|---|
| `b315a958` | Pack S — static legs (baseline/seal/B/C/D/F/G/§10) | 7/9 green; 2 base-proven reds (B, D-2) |
| `4ff5e4af` | Gate A HEAD leg | PASS (33,843 collected, digest + shard map) |
| `314a9b10` | Gate A BASE leg (1st) | **TERMINATED** — wedged 1h48m during venv provisioning (no output; base worktree left healthy) |
| `6f749f53` | Gate A BASE leg (replacement) + digest diff | PASS — base digest + comm both directions + 3× isolation |
| `611cd37b` | Gate E green legs + mutation legs | PASS (3/3 + 2/2) |
| `24f9e506` | Gate E isolated boot-scan | PASS |
| `f204cb2d` | Gate H scope + privacy | 4/5 legs PASS; complement leg red on the tidier notes path (adjudicated) |

## 11. Artifacts (local, on the gate host)

`/tmp/aegis-head-full.log` · `/tmp/aegis-head.txt` (GREP-FULL 519) · `/tmp/aegis-head-ids.txt` (canonical 466, sha `2ae2ea8e…`) · `/tmp/aegis-head-gateA/` (shards, progress, isolation logs) · `/tmp/aegis-base-full.log` · `/tmp/aegis-base-ids.txt` (canonical 480, sha `a7dc46fd…`) · `/tmp/aegis-base-gateA/GATE-A-BASE-LEG-REPORT.md` · `/tmp/aegis-netnew-head-only.txt` · `/tmp/aegis-base-only.txt` · `/tmp/aegis-verdict/sweep_digest_verdict.log` · `/tmp/aegis-iso/{head,base}.results.tsv` · `/tmp/aegis-boot.log` · `/tmp/aegis-census.json` · `/tmp/aegis-e1-*.log` · `/tmp/aegis-mut{1,2}-*.log` · `/tmp/aegis-scope-complement.txt` · `/tmp/aegis-scope-planning.txt` · `/tmp/aegis-diff.txt`

Replay recipe (no re-collection needed):
```
cd /tmp/aegis-base-wt && .venv/bin/python -m pytest -p no:cacheprovider \
  $(cat /tmp/aegis-base-gateA/shards/shard-NN.txt) -q --tb=line -rEf --timeout=30 -n auto
# fence: per-variable env -u POSTGRES_*/PG*/ENSEMBLE_* + OPENAI_BASE_URL=http://127.0.0.1:9/v1
#        + QUEUE_LLM_RETRY_*=1 + PYTHONDONTWRITEBYTECODE=1 ; timeout 1750
# diff:  LC_ALL=C comm -13 /tmp/aegis-base-ids.txt /tmp/aegis-head-ids.txt
#        LC_ALL=C comm -23 /tmp/aegis-base-ids.txt /tmp/aegis-head-ids.txt
```

---

## Overall Status

* Gate A: ✅ PASS (net-new deterministic reds **0**)
* Gate B: ✅ PASS-W-DISCLOSURE (4 base-proven seal-map reds → R1)
* Gate C: ✅ PASS
* Gate D: ✅ PASS-W-DISCLOSURE (5 base-proven prompt-debt ids → R2)
* Gate E: ✅ PASS (registry + boot-scan + T29/T30 + 2/2 mutation proofs)
* Gate F: ✅ PASS · Gate G: ✅ PASS
* Gate H: ✅ PASS-W-DISCLOSURE (one stale-predicate tolerance → OQ-2)
* **VERDICT: GO** — Phase 6 (giter staging) may proceed. No lane-attributable blocker found. Nothing was staged by this lane.
