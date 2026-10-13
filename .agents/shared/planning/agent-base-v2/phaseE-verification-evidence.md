# Phase E — Verification Evidence Pack (whole-mission DoD)

Owner scope: **Phase E** of the agent-base-v2 mission. Aggregates the verification evidence that proves the whole mission met its Definition of Done. Does NOT modify code, agents, docs, or the legacy archive — Phase E is **evidence-only**. Reads anything; writes only to `.agents/shared/planning/agent-base-v2/evidence/` and to the working notes. Siblings: `phaseA-legacy-archive.md`, `phaseB-base-copy.md`, `phaseC-opencode-excision.md`, `phaseD-references-docs.md` (parallel workers; this plan references them by filename and never duplicates their content).

**Controlling scope (final, 2026-10-13 + exploration addendum relay + architecture-recommendation.md R1–R10 + P5):** the unit of work is the **ENTIRE opencode stack** as a SINGLE package — the addendum's VERDICT 1 collapses the prior "two lanes" framing into **one retirement unit with two named roles** inside the package:
- **Session/API role** — the bulk of `daemon/opencode/` (single import graph: `external_opencode.py` → `server.py` → `registry/session_manager` → `client`; single SQLite store; single manager init at `daemon/manager.py:1427-1436`)
- **Communication role** — the protocol layer INSIDE the session lane (`daemon/opencode/server.py` action dispatch + question poll/relay; SOLE consumer `daemon/tools/external_opencode.py:20-21`)

The `daemon/opencode/__init__.py:13` claim "used by the ensemble HTTP router" is **STALE** (zero router mounts across 30 routers + `daemon/api.py`); it does not gate removal. The package is a line-by-line port of the `opencode_skill` Go binary (`server.py:168+` ports `server.go:230-507`).

**Stack components (final, post-addendum):**
- `daemon/opencode/` (8 modules) — entire package
- `daemon/tools/external_opencode.py` — sole consumer of the package
- `agents/_prompt_system/innate-skills/opencode/` (`skill.md` + `.bak`) — the agent-facing innate skill
- `tests/opencode/` (12 files) — opencode tests
- `test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` — opencode test packs
- `.agents/shared/planning/opencode-native-tools/` (7 docs) — planning records
- `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` — bug records
- `example/SKILL.md` — example skill referencing the stack

ALL of the above is removed from the active codebase and backed up to `legacy/` via copy-then-verify. **Boundary rule verdict (per VERDICT 2 of the addendum):** NO surface requires isolation. **Full stack removal, no boundary-rule exception.** The `retained-live` terminal state remains in the schema for forward-compatibility but is expected EMPTY for this mission — that expectation is recorded in the LIVE-DEPENDENCY VERDICT (Step 4.5).

**TOUCH (edited-in-place, git-preserved) — coupling surfaces that DO stay in the active codebase but lose their opencode wiring:**
- `daemon/manager.py` (lines 78, 1420-1436, 2895-2910, 3325-3338, 5948-5956, 6750-6751, 13033, 13078-13103 — opencode init, coordination, startup recovery DISABLED, cleanup)
- `daemon/tools/instance.py` (lines 155-167, 259, 5815-5818 — opencode-related spawn wiring)
- `daemon/tools/_tool_registry.py` (lines 603, 764-771 — opencode tool registrations)
- `Makefile` (lines 174, 186)
- `tests/unit/test_coder_agent.py` (lines 40-43 — coder opencode-free BY DESIGN contamination guards at :144, :168, :369+, :510, :253 must KEEP passing)
- `tests/unit/test_innate_skills_refactoring.py`
- `tests/migration/test_jsonb_migration.py` (lines 70-107)
- mocks: `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126`, `tests/integration/test_spawn_default_unchanged.py:163`
- `pyproject.toml:88`, `.env.example:39`
- comment rephrases: `daemon/config.py:168`, `keyword_extraction.py`, `context_injection.py:541,846`, `context_messages.py`, `critical_note_gate.py:8`, `waiting_children_watchdog.py`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777`
- cosmetic one-liners: `agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md`
- docs refresh: `docs/agents.md` §2, `docs/skill-evolution.md:174`, `README.md` (Phase D's edits)

**LEAVE ALONE (pre-classified audit hits):**
- `agents/coder/` — coder is opencode-free by design; the contamination guards in `tests/unit/test_coder_agent.py` must KEEP passing (not an exception; a constraint on Phase C and Phase E's test wall)
- `daemon/services/keyword_extraction.py` + `daemon/services/context_injection.py` — **LIVE-SHARED infra** (D5) that stays; live callers attested: `attestation_report_judge.py:35-328`, `daemon/graph.py:10794`, `plugins/opendesign/generate.py:650`. Not opencode-stack.
- `docs/mcp-kb-usage.md` (D6) — upstream sst/opencode product, not our service
- `.agents/*/RESULTS|LESSONS` — Tier-5 historical records
- `data/`, `data_dev/` DBs — boot-recreate dies with code; any repo-root stray DB is an orphan test artifact (note in report, not a defect)
- `.inspiration-projects/opencode_skill_src/` — gitignored inspiration reference; not in active codebase

**MCP config finding (per VERDICT 2):** **NO MCP configs anywhere reference opencode.** `daemon/mcp/` is zero (only the three built-ins `context7`, `plane`, `webfetch`); `config.yaml` is zero. The Phase E before/after map's stream v (MCP configs) is therefore empty; the residual-references audit's surface for MCP configs is expected to return zero hits.

**Ordering constraint (binding):** stack removal is coupled to base-retirement via the innate-skill loader. While the 5 old-base agents still declare `innate_skills: ["opencode"]`, both `agents/_prompt_system/innate-skills/opencode/` AND `daemon/tools/instance.py:155-167` (the innate-skill grant map) must still resolve. **Removal lands WITH or AFTER Phase B, never before.** Phase E's DoD verifies the mission executed in that order (Phase B's commit precedes the opencode-stack move in the branch's commit graph).

## 1. Objective

Produce a checkable Definition-of-Done evidence pack proving:

1. **v2 zero-touch** — the five `agents/<name>[v2]/` directories are byte-identical to their pre-mission state (mission risk #4 / dispatch D4).
2. **base == v2** — the five `agents/<name>/` (base) directories are byte-identical to their matching `[v2]` dirs (mission acceptance criterion).
3. **legacy/ archive intact (FULL STACK)** — Phase A's archive of the opencode-era base + the ENTIRE opencode stack (comm lane + session/API coding-worker integration lane + shared modules + InstanceManager-coordinated resources + MCP configs + agent-facing surfaces + any other surface the leader's exploration addendum identifies) survived later phases.
4. **Full test wall green** — every pinned test passes against the post-Phase-B/C/D tree (mission risk #7).
5. **Boot-scan clean** — the fenced dev-lane daemon boots post-Phase-C with no opencode errors (mission risk #3). Covers the FULL STACK: zero `daemon/opencode/*` boot log, zero `daemon/tools/external_opencode.py` boot log, zero opencode MCP transport boot log (per addendum VERDICT 2: NO MCP configs anywhere), zero `agents/_prompt_system/innate-skills/opencode/` skill-loading log. Exact file list per addendum VERDICT 1 (single package, two roles) — see §12 O1 RESOLVED.
6. **`default_agent_versions` runtime invariant** — the value of `GET /api/settings/default-agent-versions` is unchanged pre vs post mission (mission risk #5).
7. **Skill-seed no-op** — skill seeding under the new base id is a version-equal no-op, with no duplicate rows / side effects (mission risk #4).
8. **Drift/duplication posture recorded** — the base := frozen-mirror-of-v2 steady state and its maintenance implications are documented (NOT solved).
9. **BEFORE/AFTER MAP (whole-mission; FULL STACK with three terminal states)** — what existed where before the mission → where it lives after. First-class evidence artifact: `evidence/before-after-map.md` (and a machine-readable companion `evidence/before-after-map.json` if desired). The map uses THREE terminal states: **(archived-to-legacy)** / **(edited-in-place, git-preserved)** / **(retained-live, USER DECISION ITEM)**. See Step 9.5 for the row schema.
10. **FINAL RESIDUAL-REFERENCES AUDIT (whole-mission; FULL STACK)** — after archival + excision + agent-facing-surface removal, sweep the whole repo + docs + tests for ANY remaining reference to `legacy/` paths, opencode modules/tools/skills/sessions, MCP configs, agent-facing surfaces, InstanceManager-coordinated resources, and archived files. Every hit classified: must-fix leftover / known false positive (D6 — `docs/mcp-kb-usage.md`) / historical record (Tier-5 `.agents/*/RESULTS|LESSONS`, `docs/plans/`, `docs/bugs/`) / intentional (`keyword_extraction` D5, runtime DBs D7, `legacy/` itself, the planning dir at `.agents/shared/planning/agent-base-v2/`). See Step 9.6 for the sweep method and DoD.
11. **LIVE-DEPENDENCY VERDICT (Step 4.5)** — per-surface verdict table (live-used vs dead) with sources (leader's exploration addendum when relayed + executor verification); coder agent explicitly covered. The verdict **GATES** Phase C's branch selection: full-stack removal vs isolated-exception (boundary-rule path). If any surface is "live-used" and the boundary rule is invoked, the mission DoD surfaces the exception as an **OPEN USER DECISION** that is never implicitly marked done.

Storage: **`.agents/shared/planning/agent-base-v2/evidence/`** in the worktree (committed on `feature/agent-base-v2`). Subdirs per evidence category so the directory is the report. Every evidence file must be reproducible from a documented recipe so a future investigator can re-run.

## 2. Scope

### In scope — files this phase may create / write

- `.agents/shared/planning/agent-base-v2/evidence/` tree (created in this phase if not already)
  - `pre-mission-v2-manifests/` — sha256 manifests of the five v2 dirs captured BEFORE Phase B started
  - `post-mission-v2-manifests/` — sha256 manifests of the five v2 dirs captured AFTER mission end
  - `base-vs-v2-diffs/` — one diff file per agent showing `base == v2` (empty diff is the proof)
  - `legacy-reverify/` — re-verified sha256 manifests of Phase A's archive at mission end
  - `boot-scan/` — fenced dev-lane boot log + opencode-residue grep
  - `default-agent-versions/` — pre + post GET /api/settings/default-agent-versions responses (JSON)
  - `skill-seed/` — pre + post skill-bank counts + per-agent side effects table
  - `test-wall/` — pytest output + summary table (which packs ran, pass counts)
  - `drift-notes.md` — drift/duplication posture (NOT solved)
  - `definition-of-done.md` — whole-mission DoD checklist
  - `rollback.md` — rollback story

### Out of scope — explicitly NOT touched

- `agents/**` — read only (manifests and diffs compute against these; never modify).
- `legacy/**` — read only (re-verify manifests against Phase A's copy-verify outputs).
- `daemon/**` — read only (boot-scan reads code via runtime, no edits).
- `tests/**` — read only (test wall runs the suite as-is; no edits).
- `docs/**` — read only (the docs-consistency sweep output from Phase D is consumed; no further edits).
- The main checkout `/home/nea/ensemble-src` — DIRTY; never touch.
- Any live system beyond the single read-only GET in step 6.

## 3. Dependencies & Ordering

| Position | Phase | Phase E needs from it |
|---|---|---|
| Before E | **Phase A (legacy archive)** | The legacy/ tree must exist with copy-verify manifests. Phase E re-verifies those manifests in step 3. |
| Before E | **Phase B (base copy)** | `agents/<base>` must equal `agents/<base>[v2]`. Phase B's checksum manifests feed Phase E's `base-vs-v2-diffs` evidence (Phase E may re-derive independently to double-check). |
| Before E | **Phase C (opencode excision)** | Daemon code must be opencode-free. Phase E's boot-scan catches any leftover; Phase E's `default-agent-versions` GET reflects Phase C's removal. |
| Before E | **Phase D (docs refresh)** | Phase E re-greps `docs/`, `README.md`, `AGENTS.md` for residual `opencode` claims about our service and uses Phase D's docs-consistency sweep table as the pre-E baseline. |
| Concurrent | (none) | E is the final phase; no downstream phases consume E. |

**E must run LAST.** Its outputs (especially `boot-scan/` and `default-agent-versions/`) are the post-mission evidence; running them pre-D would capture pre-D state, not post-mission state.

### R8 — Capture-before-boot ordering rule (binding, architecture-recommendation.md §5 risk #14 + R8 amendment) [ADDED 2026-10-13]

**The base==v2 equality proof (Step 3: `diff -r` + per-agent manifests) MUST be captured BEFORE any boot proof (Step 5: boot-scan).**

**Why this rule exists:** `daemon/tools/inner_soul.py:1043, :1107-1176` writes to `agents/<agent>/memory.md` at RUNTIME. Post-Phase-B, base `memory.md` == v2 `memory.md` (the v2 dir was copied into base, including the memory.md file). Any boot that instantiates a base agent (and runs `inner_soul`) mutates `memory.md` → the `diff -r` equality proof breaks for a NON-architectural reason (runtime mutation, not a content defect). Capturing the equality proof BEFORE any boot guarantees the proof reflects the committed tree state, not a runtime-mutated state.

**Ordering rule (encoded):**
1. Step 1 (pre-mission v2 manifests) runs first.
2. Step 2 (post-mission v2 manifests) runs second, BEFORE Step 3.
3. Step 3 (base==v2 diff + per-agent manifests) runs third, BEFORE Step 5.
4. Step 4 (legacy/ archive re-verify) runs fourth.
5. Step 4.5 (LIVE-DEPENDENCY VERDICT) runs fifth.
6. Step 5 (boot-scan) runs SIXTH — AFTER all equality proofs are captured.
7. Steps 6–8 run after Step 5 (the boot-scan's fenced dev lane is the only runtime interaction; subsequent steps read its outputs).
8. Step 9.5 (BEFORE/AFTER MAP) and Step 9.6 (RESIDUAL-REFERENCES AUDIT) include a verification note that the equality proof was captured before the boot-scan.
9. Step 13 (FINAL TESTER GATE — R10) runs LAST; it greps the COMMITTED tree (`git grep`), so runtime `memory.md` mutation cannot contaminate it. R8's capture-before-boot governs the EQUALITY PROOF; the R10 gate greps the COMMITTED tree — the two checks are independent and BOTH stand (architecture-recommendation.md §11.7).

**Cross-reference (R10 §11.7):** "The gate greps the COMMITTED tree (`git grep`), so runtime `memory.md` mutation (inner_soul) cannot contaminate it. R8's capture-before-boot rule still governs the base==v2 *equality proof*; the two checks are independent and both stand."

## 4. File Inventory (exact paths)

| Output path | Purpose | Tool |
|---|---|---|
| `evidence/pre-mission-v2-manifests/<name>[v2].manifest.txt` | sha256 manifest per v2 dir (pre-mission) | `find … \| sort` → `sha256sum` (GNU) OR `shasum -a 256` (BSD); pick ONE and use everywhere — see §5 step 0 for the lock-in. |
| `evidence/post-mission-v2-manifests/<name>[v2].manifest.txt` | sha256 manifest per v2 dir (post-mission) | same |
| `evidence/post-mission-v2-manifests/<name>[v2].digest.txt` | tree digest (manifest's own hash) | sha256sum on the manifest |
| `evidence/base-vs-v2-diffs/<name>.diff.txt` | per-agent diff base vs v2 (empty = proof) | recursive sha256 per file; sorted-join with diff |
| `evidence/legacy-reverify/<archived-path>.manifest.txt` | re-verified archive manifests | same recipe as pre-mission |
| `evidence/boot-scan/dev-lane-boot.log` | fenced dev-lane boot log | `dev.sh` (worktree-local); bash capture |
| `evidence/boot-scan/opencode-residue-grep.txt` | opencode-residue grep over boot log + daemon code post-Phase-C | `grep -rn "opencode" daemon/` + grep over boot log |
| `evidence/default-agent-versions/pre.json` | pre-mission GET response | `curl http://localhost:8079/api/settings/default-agent-versions` |
| `evidence/default-agent-versions/post.json` | post-mission GET response | same |
| `evidence/default-agent-versions/compare.txt` | byte-diff of pre vs post | `diff pre.json post.json` (empty = invariant) |
| `evidence/skill-seed/pre-bank-counts.json` | pre-mission skill bank counts | `SELECT count(*) FROM skill_bank …` (read-only) |
| `evidence/skill-seed/post-bank-counts.json` | post-mission counts | same |
| `evidence/skill-seed/per-agent-side-effects.md` | table per-agent: new / updated / unchanged / errors | derived from `skill_seed_service.py:285-292` execution output |
| `evidence/test-wall/<pack>.log` | pytest output per pack | `pytest tests/...` |
| `evidence/test-wall/summary.md` | summary table — pack name, tests, passed, failed, time | derived |
| `evidence/docs-consistency-sweep-final.md` | post-D residual `opencode` grep in `docs/`, `README.md`, `AGENTS.md` (covers BOTH retirement streams) | `grep -rn "opencode" docs/ README.md AGENTS.md` (union of all three greps from Phase D step 9) |
| `evidence/before-after-map.md` | BEFORE/AFTER map (whole-mission; both retirement streams) — what existed where pre-mission → where it lives post-mission. Companion `evidence/before-after-map.json` for the machine-readable representation. | hand-authored; sourced from Phase A plan + leader's exploration addendum (when it lands) + git archaeology |
| `evidence/residual-references-audit.md` | FINAL RESIDUAL-REFERENCES AUDIT — sweep of the whole repo + docs + tests for `legacy/` paths, opencode modules/tools/skills/sessions, archived files. Every hit classified. | `grep` per the patterns in §5 step 9.5; classification per the four buckets (must-fix / false-positive / historical / intentional) |
| `evidence/r10-allowlist.md` | **R10 ALLOWLIST FILE** — one row per excluded path + one-sentence justification. **Negative-guard allowlist class (B1 fix-pass 3 iteration 002, post-reconciliation 2026-10-13):** `tests/unit/test_coder_agent.py` (43 hits, contamination guards at :144/:148/:168/:173/:253/:264/:268/:269/:369/:384 — D7 invariant: coder must NOT depend on opencode; strings ARE the assertion; removing the strings removes the guard), `tests/unit/test_devops_agent.py` (31 hits, `test_no_opencode_skill_content_in_system_prompt` at :337 + `test_devops_does_not_have_opencode_tools` at :534 + deny-list fixtures), `tests/unit/test_reviewer_v2_agent.py` (4 hits, `test_opencode_not_in_innate_skills` at :130 — D7 invariant: reviewer[v2] must NOT depend on opencode). **NOTE (post-reconciliation 2026-10-13):** `tests/integration/test_persistence_w1_markers.py` was REMOVED from the negative-guard class — it is an editable ACTIVE test file; phaseC's convergence-addendum row rephrases :15 ("This test does NOT require a live OpenCode server") to a still-current equivalent; the file is now in bucket (ii) TOUCH. **Vendored snapshot allowlist class:** `plugins/opendesign/snapshot_with_drift_alarm/prompts/daemon/system.ts` (4 hits), `plugins/opendesign/snapshot_with_drift_alarm/prompts/daemon/media-contract.ts` (1 hit) — both are vendored OD-plugin snapshot files OUTSIDE the copy_freely subtree (verified by the snapshot_with_drift_alarm provenance class). **P7 stragglers (cross-ref phaseC §13.5):** `test/packs/lcan_helpers/mock_llm.py:141-142`, `test/packs/regression_integration_test.sh:31,:40`, `test/packs/regression_chat_source_integration_test.sh:8`, `tests/integration/test_auth_boot_preflight.py:20`, `tests/unit/services/test_critical_notes_phase2_filters.py:11`. **Other mandatory rows:** `docs/mcp-kb-usage.md` (D6), `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` (moved to `legacy/docs/bugs/` per addendum MOVE list), `docs/bugs/agent-call-wrong-tool-spawn-session.md` (historical bug record), `docs/plans/**` (historical), `docs/test-suite-known-issues.md` (historical pointer), `docs/optimization/workflow-context-load-optimization.md` (B1 fix-pass 3 — historical investigation report, Tier-5), `agents/maintenancer/knowledge/01-architecture-overview.md` (B3 — Phase C TOUCH, NOT allowlist; the file is NOT excluded). **B1 fix-pass 3 expanded allowlist rows:** `.agents/*/memories/**` (agent working-state), `.agents/*/working/**` (agent working-state), `.agents/*/EVIDENCE/**` (Tier-5 audit material), `.agents/approver/active.md` (instance state — NOT a code/doc surface), `.agents/approver/agent-base-v2-tracking.md` (instance state), `.agents/approver/opencode-native-python-tools-tracking.md` (historical tracking record), `.agents/approver/rename-coder-to-developer-tracking.md` (historical tracking record), `.agents/approver/context-injection-restructure-tracking.md` (historical tracking record), `.agents/approver/tester-skill-evolution-tracking.md` (historical tracking record), `.agents/reviewer/**` (historical review records), `.agents/tidier/notes.md` (historical), `.agents/tester/QUARANTINE.md` (historical), `.slim/**` (runtime cartography), `LESSONS/**` (root-level lessons records), `backup/**` (root-level archive), `plugins/opendesign/copy_freely/**` (vendored third-party, full subtree), `plugins/opendesign/snapshot_with_drift_alarm/**` (vendored OD-plugin snapshot with drift-alarm provenance class). **Rows without justification FAIL gate review.** | hand-authored; consumed by Step 13 FINAL TESTER GATE |
| `evidence/drift-notes.md` | drift/duplication posture | hand-authored |
| `evidence/definition-of-done.md` | whole-mission DoD checklist | hand-authored from this plan + sibling phases' evidence |
| `evidence/rollback.md` | rollback story | hand-authored from this plan |

## 5. Step-by-Step Operations

**⚠️ Evidence-hygiene caution (S7, 2026-10-13):** **NEVER run `git clean -fdx` in the worktree.** The worktree holds UNTRACKED evidence artifacts (everything under `evidence/` is untracked until Step 12 commits it) AND the orphan `opencode_sessions.db` record (a D7 orphan DB that the `.gitignore` `opencode_sessions.db` ignore keeps out of `git status` post-migration). `git clean -fdx` would erase both — the untracked evidence would be lost, and the orphan DB record (useful for proving D7 was honored) would also be deleted. Use `git clean -fdn` (dry-run, list-only) to inspect what WOULD be deleted before any cleanup; or use the targeted `git clean -fdx -- <specific-path>` form when the operator is confident. Default posture: **do not run `git clean` at all in the worktree.**

### Step 0 — Tool lock-in (mandatory before any evidence capture)

All sha256 commands in this phase use **ONE** tool:
- GNU/Linux (the worktree host per project): use `sha256sum`
- BSD/macOS: use `shasum -a 256`

Do NOT mix. Mixing produces different aggregate digests and breaks the diff between pre-mission and post-mission. Detect via `uname -s`; record the choice in `evidence/TOOL.txt`:

```
uname -s > evidence/TOOL.txt
which sha256sum >> evidence/TOOL.txt  # GNU — preferred
# or: which shasum >> evidence/TOOL.txt  # BSD fallback
```

**N4 fix-pass 2 (canonical statement — applies to every dialect mention in this plan):** `evidence/TOOL.txt` is the **single source of truth** for the sha256 tool choice. The pick is recorded ONCE in Step 0; every subsequent sha256 invocation in Phase E references `evidence/TOOL.txt` (not ad-hoc `sha256sum` vs `shasum -a 256` choices). The locked-in tool governs; any in-script or ad-hoc dialect variance is a defect. The four-step verification for the lock-in: (1) `uname -s` recorded; (2) `which <tool>` recorded; (3) all manifests use that tool; (4) `git diff` between pre and post manifests is empty only if the same tool was used throughout.

This matches the recipe family at `scripts/upgrade/lib.sh:1928` (`_tree_manifest`) and `:1944` (`_tree_hash_of_lines`) — both call into the same single-hash tool.

### Step 1 — Pre-mission v2 manifests (capture IMMEDIATELY after this plan commits, BEFORE Phase B runs)

If Phase B has already started, the pre-mission manifests cannot be re-captured from history — instead, use `git show 885a19d4f:agents/<name>[v2]/…` to re-derive them. The recipe is the same; the input source changes.

Recipe per v2 dir (the 5: `approver[v2]`, `developer[v2]`, `planner[v2]`, `reviewer[v2]`, `tidier[v2]`):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/pre-mission-v2-manifests

AGENT="developer[v2]"   # repeat for each of the 5
OUT="evidence/pre-mission-v2-manifests/${AGENT}.manifest.txt"

# Exclude __pycache__ explicitly; include dotfiles (find, not glob).
( cd "agents/${AGENT}" && \
  find . -type f ! -path '*/__pycache__/*' | sed 's|^\./||' | sort \
) | while IFS= read -r rel; do
    sha=$(sha256sum "agents/${AGENT}/${rel}" | awk '{print $1}')
    printf '%s  agents/%s/%s\n' "$sha" "${AGENT}" "${rel}"
done > "$OUT"

# Aggregate digest (matches lib.sh:1928 / :1944 family).
sha256sum "$OUT" | awk '{print $1}' > "${OUT%.manifest.txt}.digest.txt"
```

Verification: each manifest has one line per file; lines start with a 64-hex-char sha + two spaces + `agents/<dir>/<relpath>`; the digest file contains a single 64-hex-char hash.

Repeat for all five v2 dirs. Commit the pre-mission manifests to `feature/agent-base-v2` BEFORE Phase B begins. The pre-mission dir lives on the branch and is the reference for the v2 zero-touch proof.

### Step 2 — Post-mission v2 manifests (capture AFTER Phase C/D finish)

Same recipe as Step 1, output to `evidence/post-mission-v2-manifests/`. Run AFTER Phase C is committed and BEFORE the final mission commit.

Verification per dir: `diff evidence/pre-mission-v2-manifests/<name>[v2].manifest.txt evidence/post-mission-v2-manifests/<name>[v2].manifest.txt` must be EMPTY for all five v2 dirs. If any diff is non-empty, the v2 zero-touch contract has failed and the mission is closed.

Second-order proof (dispatch D4):

```
git status --porcelain -- 'agents/*[v2]*'
```

must be empty (no worktree mutations inside the five v2 dirs).

### Step 3 — base == v2 proof (one diff per agent)

For each of the five v2 dirs, compute a parallel manifest of the corresponding base dir (`agents/<name>/`) and diff against the matching v2 manifest:

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/base-vs-v2-diffs

for name in approver developer planner reviewer tidier; do
    base_manifest="evidence/base-vs-v2-diffs/${name}.base.manifest.txt"
    diff_out="evidence/base-vs-v2-diffs/${name}.diff.txt"

    ( cd "agents/${name}" && \
      find . -type f ! -path '*/__pycache__/*' | sed 's|^\./||' | sort \
    ) | while IFS= read -r rel; do
        sha=$(sha256sum "agents/${name}/${rel}" | awk '{print $1}')
        printf '%s  agents/%s/%s\n' "$sha" "${name}" "${rel}"
    done > "$base_manifest"

    # B1 (fix-pass 2, 2026-10-13) — canonical path normalization: the v2 manifest
    # uses the prefix `agents/<name>[v2]/`; the base manifest uses `agents/<name>/`.
    # The single sed substitution rewrites the v2 prefix to the base prefix
    # ASYMMETRICALLY (no independent `s|<name>/|base/|` pass that would corrupt
    # paths). One substitution; one direction. Verified empty ×5 on a healthy
    # byte-copy fixture (cp -r v2 to temp, then diff the normalized v2 manifest
    # against the temp manifest).
    diff \
      <(sed "s|agents/${name}\[v2\]/|agents/${name}/|" "evidence/pre-mission-v2-manifests/${name}[v2].manifest.txt") \
      "$base_manifest" \
      > "$diff_out" || true
done
```

Verify each `evidence/base-vs-v2-diffs/<name>.diff.txt` is **EMPTY** (zero bytes). Empty diff = base == v2. If non-empty, mission acceptance fails. **Verification evidence (B1 fix-pass 2):** on a healthy byte-copy fixture (cp -r `agents/<name>[v2]/` to a temp dir, then manifest the temp dir as `agents/<name>/` and diff against the v2 manifest through the corrected normalization), all 5 pairs produce **0 diff lines** (developer=0, reviewer=0, tidier=0, approver=0, planner=0). The corrected recipe is provably canonical for the post-Phase-B state where `agents/<base>` IS the v2 byte-copy.

Note: this diff compares STRUCTURE + CONTENT, not just file names. If Phase B added or removed a file in either dir, the diff is non-empty even when the captured manifests are equal.

### Step 4 — legacy/ archive re-verification

Phase A's recipe produced sha256 manifests for the archived opencode-era base + comm service. Phase E re-runs the same recipe on the archive as it sits at mission end (post-Phase-B, post-Phase-C). The post-mission manifests must equal Phase A's pre-mission manifests byte-for-byte; this proves nothing later phase silently mutated the archive.

**Manifest arithmetic derivation block (iteration 002 carried note (e) — N2):** the per-archive manifest line counts are:

| Archive path | Per-archive file count |
|---|---|
| `legacy/daemon/opencode/` (8 modules: `__init__.py`, `server.py`, `client.py`, `registry.py`, `repository.py`, `session_manager.py`, `state.py`, `constants.py`) | 8 |
| `legacy/daemon/tools/external_opencode.py` | 1 |
| `legacy/agents/_prompt_system/innate-skills/opencode/` (`skill.md` + `skill.md.bak`) | 2 |
| `legacy/tests/opencode/` (12 files) | 12 |
| `legacy/example/SKILL.md` | 1 |
| `legacy/test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` (3 packs) | 3 |
| **Sum (daemon + tools + skill + tests + example + packs)** | **8 + 1 + 2 + 12 + 1 + 3 = 27** |

Plus the 7 opencode planning docs at `.agents/shared/planning/opencode-native-tools/` and the 2 opencode bug docs at `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` — these are moves via git-mv; their post-mission sha256 is verified by `git show feature/agent-base-v2:legacy/<path>` and `git show 885a19d4f:<pre-mission-path>`. The arithmetic is a sanity check for the executor; if the per-archive manifest line count does NOT match the table, an archive is missing a file (or has an extra one) and Phase A's copy is suspect.

Recipe:

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/legacy-reverify

# Phase A's manifest path (read from phaseA-legacy-archive.md when available;
# otherwise point at the archive root).
ARCHIVE_ROOT="legacy"   # adjust per Phase A's actual path
for archived_path in "$ARCHIVE_ROOT"/agents/* "$ARCHIVE_ROOT"/daemon/opencode "$ARCHIVE_ROOT"/daemon/services/opencode; do
    [ -d "$archived_path" ] || continue
    label=$(echo "$archived_path" | sed 's|/|_|g')
    OUT="evidence/legacy-reverify/${label}.manifest.txt"
    ( cd "$archived_path" && \
      find . -type f ! -path '*/__pycache__/*' | sed 's|^\./||' | sort \
    ) | while IFS= read -r rel; do
        sha=$(sha256sum "${archived_path}/${rel}" | awk '{print $1}')
        printf '%s  %s/%s\n' "$sha" "${archived_path}" "${rel}"
    done > "$OUT"
done

# Compare each manifest against Phase A's pre-mission manifest (from phaseA-legacy-archive.md).
# diff must be empty.
```

Note: the exact archive layout depends on Phase A's plan. Phase E references `phaseA-legacy-archive.md` for the canonical paths and adjusts the loop above to match.

Verification: each diff against Phase A's manifests is empty.

### Step 4.5 — LIVE-DEPENDENCY VERDICT (evidence input to Phase C's branch selection; boundary rule) [BINDING SCOPE FINAL 2026-10-13 + ADDENDUM]

**This is the artifact that gates Phase C's branch selection.** The boundary rule from the controlling scope statement says: if any ACTIVE, live-used agent (e.g., the **coder** agent) depends on the opencode stack for real work, do NOT break it mid-migration — isolate the dependency, keep the needed module in place, and flag it as a USER DECISION ITEM. The LIVE-DEPENDENCY VERDICT is the evidence that this decision was made deliberately, not silently.

**The addendum's VERDICT 2 has resolved this in advance:** **NO surface requires isolation → FULL STACK REMOVAL, no boundary-rule exception.** This step captures the verdict with sources and the per-surface evidence, so the audit trail shows the decision was made from real evidence rather than a silent assumption.

**Sources (priority order):**
1. **Primary — leader's exploration addendum (RECEIVED 2026-10-13).** VERDICT 2 is the authoritative classification. Coder is opencode-free BY DESIGN (contamination guards in `tests/unit/test_coder_agent.py:144, :168, :369+, :510, :253` are the constraint, not a dependency). No surface is live-used.
2. **Secondary — executor verification (re-runs the addendum's evidence patterns at exec time to assert the verdict is reproducible).**

**Artifact: `evidence/live-dependency-verdict.md`** (and a JSON companion `evidence/live-dependency-verdict.json`). Per-surface table with the following schema (every row is RESOLVED — the addendum's classification is the source of truth, and the executor's grep re-confirms):

| Surface | Stack role (per VERDICT 1) | Verdict | Sources (addendum VERDICT § / executor check) | Phase C branch (full-stack / isolated-exception) | Boundary-rule flag (live-used active agent) | User decision item |
|---|---|---|---|---|---|---|
| `daemon/opencode/` (8 modules) | session/API role (comm role is a layer inside) | **dead** | addendum VERDICT 1, 2; executor `grep -rn "from daemon.opencode" --include='*.py' daemon/` (after Phase C) = empty; zero router mounts across 30 routers + `daemon/api.py` (the `__init__.py:13` claim is STALE) | full-stack | none | none |
| `daemon/tools/external_opencode.py` | SOLE consumer of the package (comm role) | **dead** | addendum VERDICT 1, 2; executor `grep -rn "from daemon.tools.external_opencode" --include='*.py' --exclude-dir=legacy .` = empty (after Phase C) | full-stack | none | none |
| `daemon/opencode/__init__.py` | shared module (within the package) | **dead** | addendum VERDICT 1; __init__.py:13 "used by the ensemble HTTP router" is STALE | full-stack | none | none |
| `daemon/manager.py` opencode blocks (lines 78, 1420-1436, 2895-2910, 3325-3338, 5948-5956, 6750-6751, 13033, 13078-13103) | InstanceManager-coordinated | **dead** | addendum VERDICT 2; executor `grep -n "opencode" daemon/manager.py` after Phase C = zero hits; startup recovery already DISABLED (manager.py:3325-3338) | full-stack (with edited-in-place TOUCH on the file) | none | none |
| `daemon/tools/instance.py` (lines 155-167, 259, 5815-5818) | TOUCH surface | **dead** | addendum VERDICT 2; executor grep = zero hits (after Phase C edit) | full-stack (edited-in-place) | none | none |
| `daemon/tools/_tool_registry.py` (lines 603, 764-771) | TOUCH surface | **dead** | addendum VERDICT 2; executor grep = zero hits (after Phase C edit) | full-stack (edited-in-place) | none | none |
| `daemon/config.py:168` (comment rephrase) | TOUCH surface | **dead** | addendum VERDICT 2; comment-only rephrase | full-stack (edited-in-place) | none | none |
| `Makefile:174,186` | TOUCH surface | **dead** | addendum VERDICT 2; executor `grep -n "opencode" Makefile` after Phase C = zero hits | full-stack (edited-in-place) | none | none |
| `pyproject.toml:88`, `.env.example:39` | TOUCH surface | **dead** | addendum VERDICT 2; executor grep = zero hits | full-stack (edited-in-place) | none | none |
| `agents/_prompt_system/innate-skills/opencode/skill.md` (+ `.bak`) | agent-facing surface (innate skill) | **dead** | addendum VERDICT 2; "opencode" innate skill ONLY in the 5 retiring base agents' `meta.json` (post-Phase-B the base agents are v2 content, which does NOT declare `["opencode"]`); no other agent references it | full-stack | none | none |
| `agents/coder/` (entire agent) | (not part of the opencode stack) | **coder is opencode-free BY DESIGN** | addendum VERDICT 2; coder contamination guards at `tests/unit/test_coder_agent.py:144, :168, :369+, :510, :253` must KEEP passing (constraint, not a dependency) | n/a (coder stays live; the opencode-free guarantee is a Phase E test wall invariant) | none | none |
| Any `meta.json` that listed `innate_skills: ["opencode", ...]` pre-Phase-B | agent-facing surface (agent meta) | **dead** | addendum VERDICT 2; post-Phase-B's v2 copy, the base agents' `meta.json` files do NOT declare `["opencode"]`; executor `grep -rln "opencode" agents/*/meta.json agents/*\[v2\]/meta.json --exclude-dir=legacy` = empty (after Phase B) | full-stack (edited-in-place via the v2 content copy) | none | none |
| `tests/opencode/` (12 files) | tests (any opencode test) | **dead** | addendum VERDICT 2; tests archive to `legacy/tests/opencode/` (uniform single tree, per O2 RESOLVED) | full-stack (archived) | none | none |
| `test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` | opencode test packs | **dead** | addendum VERDICT 2; archive to `legacy/test/packs/...` | full-stack (archived) | none | none |
| `.agents/shared/planning/opencode-native-tools/` (7 docs) | opencode planning records | **dead** | addendum VERDICT 2; archive to `legacy/.agents/shared/planning/opencode-native-tools/` | full-stack (archived) | none | none |
| `docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}` | opencode bug records | **dead** | addendum VERDICT 2; archive to `legacy/docs/bugs/...` | full-stack (archived) | none | none |
| `example/SKILL.md` (opencode reference) | example artifact | **dead** | addendum VERDICT 2; archive to `legacy/example/SKILL.md` | full-stack (archived) | none | none |
| `daemon/services/keyword_extraction.py` + `daemon/services/context_injection.py` | (NOT part of the opencode stack; LIVE-SHARED infra) | **intentionally kept live** | addendum VERDICT 2 (LEAVE ALONE); live callers attested: `attestation_report_judge.py:35-328`, `daemon/graph.py:10794`, `plugins/opendesign/generate.py:650` | n/a (these stay live; not part of the opencode stack) | none | none |
| MCP configs wiring opencode | (per addendum: NO MCP configs anywhere reference opencode) | **N/A — zero surfaces** | addendum VERDICT 2; `daemon/mcp/` is zero (only built-ins `context7`, `plane`, `webfetch`); `config.yaml` is zero; `grep -rn "opencode" --include='*.json' --include='*.yaml' --include='*.yml' --include='*.toml' --exclude-dir=legacy .` for MCP-shaped files = empty | n/a (the MCP stream is empty) | none | none |
| `docs/mcp-kb-usage.md` | (NOT part of the opencode stack; D6) | **intentionally kept (D6 false positive)** | addendum VERDICT 2 (LEAVE ALONE); documents upstream sst/opencode product, not our service | n/a (D6) | none | none |
| `~/.opencode_skill` + `~/.opencode` runtime artifacts | runtime dead | **dead** | addendum VERDICT 2; port 4095 not listening; no caller | n/a (runtime) | none | none |
| `data/`, `data_dev/` DBs (opencode_sessions table) | runtime dead | **dead / boot-recreate dies with code** | addendum VERDICT 2; any repo-root stray DB is an orphan test artifact (note in report, not a defect) | n/a (DBs) | none | none |
| `.inspiration-projects/opencode_skill_src/` | gitignored inspiration reference | **not in active codebase** | addendum VERDICT 2; gitignored | n/a (gitignored) | none | none |
| `agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md` (cosmetic one-liners) | TOUCH surface (cosmetic) | **dead** | addendum VERDICT 2; cosmetic one-liner edits | full-stack (edited-in-place) | none | none |
| `tests/unit/test_coder_agent.py:40-43` (coder contamination guards MUST KEEP passing) | (test wall invariant) | **test wall invariant** | addendum VERDICT 2; Phase E test wall asserts these pass; this is a constraint, not an opencode dependency | n/a (test wall) | none | none |
| `tests/unit/test_innate_skills_refactoring.py`, `tests/migration/test_jsonb_migration.py:70-107` | TOUCH surface (tests) | **dead** | addendum VERDICT 2; edited in place | full-stack (edited-in-place) | none | none |
| mocks: `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126`, `tests/integration/test_spawn_default_unchanged.py:163` | TOUCH surface (mocks) | **dead** | addendum VERDICT 2; edited in place | full-stack (edited-in-place) | none | none |
| comment rephrases (other): `context_injection.py:541,846`, `context_messages.py`, `critical_note_gate.py:8`, `waiting_children_watchdog.py`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777` | TOUCH surface (comments) | **dead** | addendum VERDICT 2; comment-only rephrases | full-stack (edited-in-place) | none | none |
| `daemon/api.py` + 30 routers (HTTP router mounts) | (verifying the stale `__init__.py:13` claim) | **zero mounts** | addendum VERDICT 1; `__init__.py:13` "used by the ensemble HTTP router" is STALE — no router actually mounts the package | n/a (the stale claim is verified false) | none | none |

**Coder agent verdict (mandatory coverage, RESOLVED):** the coder is **NOT** live-used. Coder is opencode-free BY DESIGN. The contamination guards in `tests/unit/test_coder_agent.py:144, :168, :369+, :510, :253` are the test wall's invariant — they MUST KEEP PASSING after Phase C and Phase B. The boundary rule is NOT invoked for the coder (no USER DECISION ITEM). The Phase E test wall asserts these guards pass; if any fails, the mission is closed and Phase B/C is wrong, not Phase E.

**JSON companion** (recommended for downstream auditor): `evidence/live-dependency-verdict.json`:

```json
{
  "scope": "opencode-stack-full (single package; session/API role + comm role layer inside; per addendum VERDICT 1)",
  "addendum_relay_received": "2026-10-13",
  "verdict_2_resolution": "NO surface requires isolation → FULL STACK REMOVAL, no boundary-rule exception",
  "sources": {
    "primary": "leader exploration addendum VERDICT 1 + VERDICT 2 (RECEIVED 2026-10-13)",
    "secondary": "executor grep verification (re-runs the addendum's evidence patterns at exec time)"
  },
  "coder_agent_covered": true,
  "coder_verdict": "opencode-free BY DESIGN; contamination guards at tests/unit/test_coder_agent.py:144,:168,:369+,:510,:253 must KEEP passing (test wall invariant, not a dependency)",
  "boundary_rule_exceptions": [],
  "boundary_rule_exception_count": 0,
  "retained_live_surfaces_in_opencode_stack": [],
  "retained_live_surfaces_outside_opencode_stack": [
    "daemon/services/keyword_extraction.py (D5 LIVE-SHARED infra; live callers: attestation_report_judge.py:35-328, daemon/graph.py:10794, plugins/opendesign/generate.py:650)",
    "daemon/services/context_injection.py (D5 LIVE-SHARED infra; live callers: attestation_report_judge.py:35-328, daemon/graph.py:10794, plugins/opendesign/generate.py:650)"
  ],
  "mcp_configs_finding": "NO MCP configs anywhere reference opencode (per addendum VERDICT 2); daemon/mcp/ is zero (only built-ins context7, plane, webfetch); config.yaml is zero",
  "ordering_constraint": "stack removal is coupled to base-retirement via the innate-skill loader; while the 5 old-base agents declare 'opencode', innate-skills/opencode/ + daemon/tools/instance.py:155-167 (the innate-skill grant map) must still resolve; removal lands WITH or AFTER Phase B, never before",
  "open_item_status": "O1 RESOLVED (one unit, two roles); O2 RESOLVED (uniform single legacy/tests/opencode/ tree); O3 moot (addendum landed before finalization); O4 RESOLVED (legacy/ root confirmed)"
}
```

**DoD for the verdict artifact:**
- Every surface in the addendum's MOVE / TOUCH / LEAVE ALONE lists has a row.
- Every row's Verdict column is one of: `dead` / `intentionally kept live` / `N/A — zero surfaces` / `test wall invariant`.
- The `boundary_rule_exceptions` array is empty (no boundary-rule path invoked for this mission — full stack removal is safe).
- The coder row is present and resolved (`opencode-free BY DESIGN`; contamination guards are a test wall invariant).
- The MCP finding is recorded as "NO MCP configs anywhere reference opencode."
- The ordering constraint is recorded.

**Verification of the verdict artifact itself:**
- `git show feature/agent-base-v2:evidence/live-dependency-verdict.md` exists and is well-formed.
- Every row's Sources column is non-empty.
- Coder row exists and is resolved.
- The `boundary_rule_exceptions` array is empty (the boundary rule was considered; not invoked).

**Phase C dependency on this artifact:** Phase C's branch selection is **full-stack removal** (no isolated-exception path). The before/after map (Step 9.5) reflects full-stack removal for every move surface; edited-in-place TOUCH surfaces are also reflected; LEAVE ALONE surfaces are NOT in the map (they stay live by definition).

**No "pending addendum" markers remain** — the addendum landed 2026-10-13 and is incorporated above. The previously-parameterized sections (rows, JSON schema fields, sweep patterns) finalize as role-named rows of one move.

### Step 5 — Boot-scan (fenced dev lane; READ-ONLY against the daemon code)

The daemon boots in the worktree's fenced dev lane. NEVER ambient-env boot. NEVER live contact (env-poison incident history ×6).

**N6 fix-pass 2 (canonical boot instruction):** the boot uses **`./dev.sh` ONLY** (the worktree's own dev-lane script, which assembles the env internally). **No hand-rolled env assembly** (`export ENSEMBLE_POSTGRES_DB=...` / `export ENSEMBLE_PORT=...` patterns are NOT real surface names — they are advisory shapes the dev.sh script handles internally). The dev.sh script enforces the fence; the executor does NOT pre-set env vars. After boot, an **explicit assert** that the resolved DB is `ensemble_dev` (NOT `ensemble_prod`):

```bash
# Assert the resolved DB is the dev lane, never prod.
resolved_db=$(curl -fsS http://localhost:8079/api/settings/runtime-info 2>/dev/null | jq -r '.database' || \
              curl -fsS http://localhost:8079/livez | head -1)
# Robust path: query the boot log for the resolved-DB banner.
grep -E "Connecting to (database|db).*ensemble_dev" evidence/boot-scan/dev-lane-boot.log > /dev/null || \
  { echo "FAIL: resolved DB is not ensemble_dev" >&2; exit 1; }
```

**Recipe (N6 fix-pass 2 — dev.sh-only boot):**

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/boot-scan

# dev.sh assembles the env internally; do NOT pre-set ENSEMBLE_* vars.
# dev.sh fences to ENSEMBLE_SELF_ENV=dev, ensemble_dev DB, PORT=8079,
# worktree-local data pins — all per the wt.claim.agent-base-v2 fence.

# Start daemon in background via dev.sh.
./dev.sh > evidence/boot-scan/dev-lane-boot.log 2>&1 &
DAEMON_PID=$!

# Wait for /livez GREEN.
for i in $(seq 1 60); do
    if curl -fsS http://localhost:8079/livez > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Wait for /readyz GREEN.
for i in $(seq 1 30); do
    if curl -fsS http://localhost:8079/readyz > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# N6 assert: resolved DB MUST be ensemble_dev (dev lane), never ensemble_prod.
grep -E "Connecting to (database|db).*ensemble_dev" evidence/boot-scan/dev-lane-boot.log > /dev/null || \
  { echo "FAIL: resolved DB is not ensemble_dev — possible live-system contact" >&2; \
    kill "$DAEMON_PID" 2>/dev/null; exit 1; }

# Capture boot log; opencode-residue grep.
grep -i "opencode" evidence/boot-scan/dev-lane-boot.log > evidence/boot-scan/opencode-residue-grep.txt || true

# Also grep daemon code for residual opencode imports (Phase C's contract).
grep -rn "opencode" daemon/ > evidence/boot-scan/daemon-opencode-residue.txt 2>&1 || true

# Tear down.
kill "$DAEMON_PID" 2>/dev/null || true
wait "$DAEMON_PID" 2>/dev/null || true
# NOTE: do NOT `rm -rf "$data_root"` here — dev.sh's data pin is worktree-local
# and we want to keep it for the S7 evidence-hygiene caution. Use `git clean -fdn`
# to inspect before any cleanup, OR leave the data_root in place (the worktree's
# .gitignore covers it). NEVER run `git clean -fdx` (S7).
```

Verification:
- `evidence/boot-scan/dev-lane-boot.log` shows `daemon.api - INFO - Starting Ensemble v…` banner (BEFORE preflight and bind).
- `evidence/boot-scan/opencode-residue-grep.txt` is **EMPTY** — no opencode log lines from our boot.
- `evidence/boot-scan/daemon-opencode-residue.txt` matches Phase C's expected residual (only benign imports of the `opencode` word that survive excision, e.g. historical-comment-only references) — Phase E references `phaseC-opencode-excision.md` for the canonical "expected residual" list.
- `/livez` and `/readyz` both returned 200 within the wait windows.

### Step 6 — `default_agent_versions` runtime evidence (the ONLY permitted API contact)

Pre-mission GET, then post-mission GET. Pre must equal post (mission risk #5).

Recipe (this is the ONLY permitted live-system contact per D8):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/default-agent-versions

# PRE-mission GET — capture BEFORE Phase B runs.
curl -fsS http://localhost:8079/api/settings/default-agent-versions \
    > evidence/default-agent-versions/pre.json

# POST-mission GET — capture AFTER Phase C is committed.
curl -fsS http://localhost:8079/api/settings/default-agent-versions \
    > evidence/default-agent-versions/post.json

# Byte-diff.
diff evidence/default-agent-versions/pre.json \
     evidence/default-agent-versions/post.json \
     > evidence/default-agent-versions/compare.txt || true
```

Verification: `compare.txt` is **EMPTY** — byte-for-byte equal pre and post. The value (a JSON dict like `{"developer": "v2", "reviewer": "v2", …}`) is unchanged; v2 remains the live lane. If a Phase C excision accidentally flipped the default-version key, this diff catches it.

Note: this GET is read-only and uses the worktree-local dev-lane daemon from Step 5. No live demo/live prod contact.

### Step 7 — Skill-seed no-op check (mission risk #4)

After Phase B replaces base with v2 content, skill seeding under the base id is a version-equal no-op (per `daemon/services/skill_seed_service.py:285-292` — `_parse_agent_dir_name` resolves base id; `seed_agent` is then called on the matching dir). Phase E verifies no double-seed rows / no side effects.

**N7 fix-pass 2 (dialect resolution — the fence decides, no hedge):** the worktree claim `wt.claim.agent-base-v2` sets `ENSEMBLE_SELF_ENV=dev` and resolves the DB to **`ensemble_dev`**, which is a **SQLite** lane (per the project's dev lane convention; the prod lane `ensemble_prod` is PostgreSQL). The recipe below uses `sqlite3` ONLY — there is no PG lane for this mission. Verification: `grep -E "ensemble_dev|sqlite" "$ENSEMBLE_DATA_ROOT/.resolved-db" 2>/dev/null || echo "FAIL: dev lane not resolved to ensemble_dev (SQLite)"` runs BEFORE the queries; if the lane is not `ensemble_dev`/SQLite, the recipe halts.

Recipe (DB read-only; N7 fix-pass 2 — sqlite3 ONLY):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/skill-seed

# N7 pre-flight: verify the dev lane resolved to ensemble_dev (SQLite).
grep -E "ensemble_dev|sqlite" "$ENSEMBLE_DATA_ROOT/.resolved-db" 2>/dev/null || \
  { echo "FAIL: dev lane not resolved to ensemble_dev (SQLite)" >&2; exit 1; }

# Use the worktree dev-lane daemon (from Step 5/6) for the queries.
# Capture counts BEFORE running the seeding pass for the migrated agents.
# If the daemon is already booted from Step 5/6, the counts reflect the
# state at that point; otherwise re-boot with the same fence.

# PRE counts (read-only; do NOT seed yet).
sqlite3 "$ENSEMBLE_DATA_ROOT/ensemble_dev.db" \
  "SELECT agent_id, count(*) FROM skill_bank GROUP BY agent_id;" \
  > evidence/skill-seed/pre-bank-counts.json 2>&1 || true

# POST counts — run the seeding pass explicitly (one-shot; idempotent),
# then re-read counts.
# (The seeding entry point lives in `daemon/services/skill_seed_service.py` —
#  grep the actual entry at exec time; the dispatcher-supplied line
#  :285-292 / :436 is a hint, not an anchor.)

# Then re-read counts.
sqlite3 "$ENSEMBLE_DATA_ROOT/ensemble_dev.db" \
  "SELECT agent_id, count(*) FROM skill_bank GROUP BY agent_id;" \
  > evidence/skill-seed/post-bank-counts.json 2>&1 || true

# Per-agent side-effects table: derive from the seeding output (new / updated
# / unchanged / errors per agent_id). Hand-author the table in
# evidence/skill-seed/per-agent-side-effects.md from the run log.
```

Verification:
- `pre-bank-counts.json` == `post-bank-counts.json` (same per-agent row counts).
- `per-agent-side-effects.md` shows `updated: 0`, `new: 0`, `errors: 0` for the five migrated agents (only `unchanged` rows expected; seeded entries are version-equal no-ops).

### Step 8 — Full test wall (mission risk #7) — with **P5 informational note + Phase-B red-window callout (iteration 002 carried note (c))**

The test wall runs the pinned tests in the worktree. Pre/post wall is the same recipe (no test code edits); the post-wall is the proof.

**Phase-B-landing red-window callout:** the test wall has an **accepted red window** between Phase B's base-copy landing and Phase C.4's in-place edits landing (architecture-recommendation §6 P1 + R1 + R2). Specifically: `tests/test_innate_skills_refactoring.py` (P1, 17+ sites), `tests/test_tool_filter.py` (R1, 3 tests), and `tests/migration/test_jsonb_migration.py` (R2, full-file) ALL go RED at B's commit boundary — these files expect opencode-era base meta content, but Phase B replaces base with v2 content (which lacks `opencode`). The red window closes at C.4 (in-place rephrases). **The test wall in Phase E runs POST-C.4 — the red window is closed by then.** The wall is meant to be GREEN at mission end; any RED in Phase E means C.4 was wrong or the test rephrases were missed, NOT that the red window re-opened.

**P5 informational note (architecture-recommendation.md §6 P5; accepted):** the release manifest is **content-only** (`lib.sh:1928-1935` — `_tree_manifest` emits `sha256  <dir>/<relpath>` lines, then `_tree_hash_of_lines` aggregates the manifest; **no modes/mtimes are verified**). `cp -pR` preserves modes but nothing in the release pipeline verifies them. "Content identity ≠ proven-by-modes" — a future maintainer must not over-trust the manifest. The test wall's equality proof (Step 3) and the legacy/ archive re-verify (Step 4) both follow this content-only recipe; the proof is robust to mtime drift but does not catch mode regressions. Phase E's test wall asserts the PINS pass (functional behavior) — the manifest catches the file content; a mode regression is caught by the boot-scan (Step 5) and the runtime tests.

Pinned tests (grep-verify the names at exec time; **per R9 architecture-recommendation.md, the real pin set is below**):

- **The REAL base-agent pin set (per R9):** `tests/test_innate_skills_refactoring.py:59-65` (the five `(agent, ["opencode","chart","todo"], …)` tuples), `:227`/`:271` (loose "opencode" loader examples), `:299` (`real_registry.find_skill("opencode")` — returns empty post-B, needs rephrase). Post-Phase-B, these pin files reflect v2 content (which lacks `opencode`); the pins rephrase in C.4. **The prior plan's "~12 meta pin test files" estimate was largely misattributed** (R9). Concretely, the **misattributed rows are NOT in scope for Phase E to edit**:
  - `tests/conftest.py:443` is the `sample_health_response_data` **HealthResponse/daemon-version** fixture (`:437-444`; daemon reports `0.20.1` per `daemon/__init__.py`). **EXCLUSION WARNING — editing this fixture BREAKS the test's semantics.** Phase E's test wall MUST NOT touch `tests/conftest.py:443`.
  - `tests/test_agents_api.py:40` writes a synthetic test-agent version; `:173` asserts the create-agent API's `"1.0.0"` default backfill (`daemon/routers/agents.py:119,:135`). Neither pins developer.
  - `tests/test_agents_api.py:443` does not exist (file is 383 lines).
  - `tests/test_registry.py:871` is a synthetic leader fixture (leader not in migration set).
  - `tests/test_models.py:276,:289` are HealthResponse.
- **`tests/test_tool_filter.py`** (R1 from architecture-recommendation.md) — three tests assert opencode innate-skill expansion and go RED at C.1 (map-row strip): `:710-716` `test_opencode_innate_skill_adds_external_opencode_category` (`assert "external_opencode" in result`), `:750-764` `test_multiple_innate_skills`, `:827-860` `test_apply_tool_filter_grants_opencode_tools_for_innate_skill`. Rephrase to a surviving skill (e.g. `"chart"`/`"test-skill"`) in C.4. The deny test `:862-887` and no-op tests `:707, :717-722` pass coincidentally — no edit.
- **`tests/migration/test_jsonb_migration.py`** (R2) — the file's opencode surface extends beyond `:70-107`; post-C.2 the module-level import at `:75` breaks pytest COLLECTION for the whole file. Additional sites: `:194-197` and `:223-226` (fixture bodies calling `OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)`), `:497` (second method-scoped import), `:518` (`"opencode_sessions": OpenCodeSessionRecord` in `table_to_class`), `:366-367` (embedded `_DO_BLOCK_SQL` rows that mirror `manager.py:6750-6751` — must be edited in sync per the test's own header comment), `:106-107` (the 2 pair rows the plan already covers), `:68-69` (comment). The full-file re-grep spec lives in C.4.
- **`tests/unit/tools/test_prompt_section_reference_integrity.py`** — sweeps all prompt files. v2 content passes today; copies should too. Assert: PASS.
- **`tests/test_chart_image_delivery_audit.py:60-79`** — hardcodes 20 agents incl. all 5 `[v2]` (verified). Phase B's copy keeps both base and `[v2]` dirs on disk, so the test's hardcoded list stays valid. Assert: PASS.
- `tests/unit/agents/test_sketcher_agent.py:612-622`
- `tests/unit/plugin_subsystem/test_tier1_wiring.py:155-175`
- `tests/unit/test_reviewer_v2_agent.py` (pin file)
- `tests/test_registry.py:433-840`
- `tests/job_queue/test_retry_versioned_agent.py` (pins `agent_dir="./agents/developer[v2]"`)
- All version-tag packs:
  ```
  pytest tests/test_agent_versioning_api.py \
         tests/unit/tools/test_spawn_instance_default_version.py \
         tests/unit/tools/test_version_tag_tool_resolution.py \
         tests/unit/tools/test_spawn_councilor_default_version.py \
         tests/unit/test_restore_preserve_version_tag.py
  ```
- Plus the broader unit suite (smoke — not exhaustive):
  ```
  pytest tests/unit/ -q
  ```

Recipe:

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence/test-wall

for pack in \
    tests/unit/tools/test_prompt_section_reference_integrity.py \
    tests/test_chart_image_delivery_audit.py \
    tests/unit/agents/test_sketcher_agent.py \
    tests/unit/plugin_subsystem/test_tier1_wiring.py \
    tests/unit/test_reviewer_v2_agent.py \
    tests/test_registry.py \
    tests/job_queue/test_retry_versioned_agent.py \
    tests/test_agent_versioning_api.py \
    tests/unit/tools/test_spawn_instance_default_version.py \
    tests/unit/tools/test_version_tag_tool_resolution.py \
    tests/unit/tools/test_spawn_councilor_default_version.py \
    tests/unit/test_restore_preserve_version_tag.py; do
    pytest "$pack" -q > "evidence/test-wall/$(basename "$pack" .py).log" 2>&1 || true

    # FENCING (live-path guardrail):
    # Per the project critical note, test scripts must NEVER resolve
    # live-shaped paths (HOME-derived install dirs). If a pack has its
    # own helper scripts, fence them via FAKE_HOME mktemp + escape guard.
    # The pinned tests above do not have such scripts (they use pytest
    # fixtures), but if any pack pulls in a helper, fence it before
    # running. (Documented in this plan; not a separate step.)
done

# Smoke: unit suite.
pytest tests/unit/ -q > evidence/test-wall/unit-suite.log 2>&1 || true

# Hand-author summary.
# evidence/test-wall/summary.md — one row per pack: pass/fail counts, time.
```

Verification:
- Every pinned pack's log shows 0 FAILED. Pytest exit code 0.
- `unit-suite.log` shows the unit suite green with 0 FAILED.
- If a pin file FAILS after Phase B's update, Phase B's pin update was wrong — the test wall catches it.

Fencing reminder (project critical note): no test script in this wall resolves `HOME`-derived live install paths. The dev lane is fenced via `ENSEMBLE_SELF_ENV=dev`, worktree-local data pins, and `mktemp` FAKE_HOME for any helper scripts. The 2 live-install deletions of 2026-10-12 happened when a fixture ran `export HOME="$HOME"` + `rm -rf $HOME/agents-ensemble` — the wall's tests do not run that pattern, but if any pack needs an env override, it must fence through `mktemp -d` + escape guard. Document this in the per-pack log header if applicable.

**FAKE_HOME trap hardening (iteration 002 carried note (e) — N6):** the FAKE_HOME pattern is not just `mktemp -d` — it must include explicit `trap EXIT INT TERM HUP` cleanup, an explicit `/tmp` parent (NOT `$TMPDIR` which may resolve to a non-`/tmp` location in some shells), and a per-run sweep of any helpers that escape the temp dir:

```bash
# FAKE_HOME trap hardening (N6).
fake_home=$(mktemp -d -p /tmp/ens-evidence-fakehome-XXXXXX)
trap 'rm -rf "$fake_home" 2>/dev/null || true' EXIT INT TERM HUP
export HOME="$fake_home"
# /tmp sweep: any helper scripts that wrote outside $fake_home get cleaned
# here (NOT the worktree — see S7 evidence-hygiene caution re git clean -fdx).
```

A helper that escapes `fake_home` and writes to `$HOME` (which is the trap) gets cleaned at EXIT. A helper that escapes to the worktree root (NOT under `fake_home`) is a defect — the trap cannot catch it; the executor must inspect the per-pack log header for `/tmp`-prefixed vs. worktree-prefixed paths.

### Step 9 — Final docs-consistency sweep (post-D residual)

After Phase D commits, re-grep. The sweep covers the FULL STACK and uses the same union-of-three-greps as Phase D's step 9 (per addendum VERDICT 1: one package, two roles):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence

# Stream (i)+(ii) — broad
{ grep -rn "opencode" docs/ README.md AGENTS.md; \
  echo "---"; \
  grep -rnE "opencode (session|sessions|API|api|coding-worker|coding worker|integration lane)" docs/ README.md AGENTS.md; \
  echo "---"; \
  grep -rnE "coding.worker" docs/ README.md AGENTS.md; \
} > evidence/docs-consistency-sweep-final.md 2>&1 || true
```

Classify each remaining hit using Phase D's docs-consistency table as the baseline:
- "stale→fix" hit still present = Phase D missed it — flag in the post-mission commit message; mission is not done.
- "false-positive→leave" hit still present = OK; Phase D verified it.
- "historical→leave" hit still present = OK.
- "intentional→leave" hit still present = OK (reserved for cases like `keyword_extraction` if it surfaces in a doc context).

Verification: zero unclassified residual hits. The committed file should look like Phase D's table + a final line "Residual `opencode` hits post-Phase-D: <count> (all classified per Phase D table)."

### Step 9.5 — BEFORE/AFTER MAP (whole-mission; FULL STACK; three terminal states; FINAL per addendum) [BINDING SCOPE FINAL 2026-10-13 + ADDENDUM]

First-class evidence artifact. Hand-author `evidence/before-after-map.md` (and a JSON companion `evidence/before-after-map.json` if a machine-readable form is wanted for the auditor). The map covers the **ENTIRE opencode stack** as a single package (per addendum VERDICT 1). Every row has one of THREE terminal states:
- **(archived-to-legacy)** — the standard path: pre-mission surface mirrored to `legacy/<mirror-path>` via copy-then-verify (Phase A); active codebase no longer references it (Phase C).
- **(edited-in-place, git-preserved)** — surface NOT whole-file archived; file edited to remove its opencode wiring; pre-edit version preserved in git history.
- **(retained-live, USER DECISION ITEM)** — surface kept live because a live-used active agent depends on it. **Expected EMPTY for this mission** (per addendum VERDICT 2 — full stack removal, no boundary-rule exception). The schema retains the terminal state for forward-compatibility.

#### MOVE → legacy/ (archived-to-legacy; per addendum)

| Pre-mission path | Stack role | Terminal state | Post-mission path | Move mechanism | sha256 manifest evidence |
|---|---|---|---|---|---|
| `daemon/opencode/` (8 modules — `__init__.py`, `server.py`, `client.py`, `registry.py`, `repository.py`, `session_manager.py`, `state.py`, plus any other modules in the package) | session/API role + comm role layer (single package per addendum VERDICT 1) | (archived-to-legacy) | `legacy/daemon/opencode/` | Phase A copy + Phase C excision (no daemon-side imports) | `evidence/legacy-reverify/daemon_opencode.manifest.txt` |
| `daemon/tools/external_opencode.py` | SOLE consumer of the package (comm role) | (archived-to-legacy) | `legacy/daemon/tools/external_opencode.py` | Phase A copy + Phase C excision | `evidence/legacy-reverify/daemon_tools_external_opencode.manifest.txt` |
| `agents/_prompt_system/innate-skills/opencode/skill.md` (+ `.bak`) | agent-facing surface (innate skill) | (archived-to-legacy) | `legacy/agents/_prompt_system/innate-skills/opencode/` | Phase A copy + Phase C excision of the active path (the active path is removed because the 5 old-base agents' `innate_skills: ["opencode"]` declarations are dropped via the Phase B v2 copy, which lacks `opencode`) | `evidence/legacy-reverify/innate_skills_opencode.manifest.txt` |
| `tests/opencode/` (12 files) | opencode tests | (archived-to-legacy) | `legacy/tests/opencode/` (uniform single tree per O2 RESOLVED) | Phase A copy | `evidence/legacy-reverify/tests_opencode.manifest.txt` |
| `test/packs/opencode_native_tools_unit_test.sh`, `test/packs/ab/top_opencode_unit_test.sh`, `test/packs/regression_opencode_e2e_test.sh` | opencode test packs | (archived-to-legacy) | `legacy/test/packs/...` | Phase A copy | `evidence/legacy-reverify/test_packs_opencode.manifest.txt` |
| `.agents/shared/planning/opencode-native-tools/` (7 docs) | opencode planning records | (archived-to-legacy) | `legacy/.agents/shared/planning/opencode-native-tools/` | Phase A copy | `evidence/legacy-reverify/agents_planning_opencode_native_tools.manifest.txt` |
| `docs/bugs/opencode-session-manager-memory-leak.md`, `docs/bugs/external-opencode-wait-latency.md` | opencode bug records | (archived-to-legacy) | `legacy/docs/bugs/...` | Phase A copy | `evidence/legacy-reverify/docs_bugs_opencode.manifest.txt` |
| `example/SKILL.md` (opencode reference) | example artifact | (archived-to-legacy) | `legacy/example/SKILL.md` | Phase A copy | `evidence/legacy-reverify/example_SKILL_md.manifest.txt` |

#### TOUCH (edited-in-place, git-preserved; per addendum)

| Pre-mission path (file stays in active codebase) | Stack role | Terminal state | Move mechanism | Pre-edit version evidence |
|---|---|---|---|---|
| `daemon/manager.py` (lines 78, 1420-1436, 2895-2910, 3325-3338, 5948-5956, 6750-6751, 13033, 13078-13103) | InstanceManager opencode coordination blocks | (edited-in-place, git-preserved) | Phase C in-place edit; pre-edit version in `git show 885a19d4f:daemon/manager.py` | git history |
| `daemon/tools/instance.py` (lines 155-167, 259, 5815-5818) | spawn wiring | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `daemon/tools/_tool_registry.py` (lines 603, 764-771) | tool registrations | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `daemon/config.py:168` | comment rephrase | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `Makefile:174,186` | opencode references | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `pyproject.toml:88`, `.env.example:39` | opencode references | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `tests/unit/test_coder_agent.py:40-43` (plus the contamination guards at :144, :168, :369+, :510, :253 that MUST KEEP passing) | coder opencode-free test (test wall invariant) | (edited-in-place, git-preserved) | Phase C in-place edit; Phase E test wall asserts the contamination guards pass | git history + Phase E test wall log |
| `tests/unit/test_innate_skills_refactoring.py` | test | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `tests/migration/test_jsonb_migration.py:70-107` | migration test | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126`, `tests/integration/test_spawn_default_unchanged.py:163` | mocks | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| comment rephrases: `daemon/services/keyword_extraction.py` (live-shared infra; comment-only), `daemon/services/context_injection.py:541,846`, `daemon/services/context_messages.py`, `daemon/services/critical_note_gate.py:8`, `daemon/services/waiting_children_watchdog.py`, `daemon/loader.py:226`, `daemon/repositories/task/repository.py:1777` | comment-only edits | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| cosmetic one-liners: `agents/explorer/soul.md`, `agents/devops/soul.md`, `agents/_prompt_system/knowledge.md` | cosmetic edits | (edited-in-place, git-preserved) | Phase C in-place edit | git history |
| docs refresh: `docs/agents.md` §2, `docs/skill-evolution.md:174`, `README.md` (Phase D's) | user-facing docs | (edited-in-place, git-preserved) | Phase D in-place edit | git history |

#### LEAVE ALONE (NOT in the map; surfaces stay live by definition per addendum)

| Surface | Reason |
|---|---|
| `agents/coder/` (entire agent) | opencode-free BY DESIGN (test wall invariant) |
| `daemon/services/keyword_extraction.py` + `daemon/services/context_injection.py` | LIVE-SHARED infra (D5); live callers: `attestation_report_judge.py:35-328`, `daemon/graph.py:10794`, `plugins/opendesign/generate.py:650` |
| `docs/mcp-kb-usage.md` | D6 false positive (upstream sst/opencode product) |
| `.agents/*/RESULTS|LESSONS` | Tier-5 historical records |
| `data/`, `data_dev/` DBs | boot-recreate dies with code; any repo-root stray DB is an orphan test artifact (note in report, not a defect) |
| `.inspiration-projects/opencode_skill_src/` | gitignored inspiration reference |

#### EXPECTED-EMPTY terminal state (retained-live, USER DECISION ITEM)

The `retained-live` terminal state schema remains in the map for forward-compatibility. **For this mission it is expected EMPTY** — the addendum's VERDICT 2 has resolved the boundary-rule question: NO surface requires isolation; full stack removal is safe. The `evidence/before-after-map.json` companion carries `boundary_rule_exceptions: []` and `retained_live_surfaces_in_opencode_stack: []`. If at exec time the executor's verification contradicts the addendum (e.g., a new live caller surfaces), a new row is added to this section AND a USER DECISION ITEM is filed (the mission DoD criterion for the boundary-rule exception is then an OPEN USER DECISION, not "done").

**Verification of the map itself:**
- Every pre-mission path in MOVE is verifiable as a tree at `git show 885a19d4f:<path>` (HEAD before the mission) AND as a tree at `legacy/<path>` at mission end (the archive is committed; the post-mission path is `git show feature/agent-base-v2:legacy/<path>`).
- Every pre-mission path in TOUCH is verifiable at `git show 885a19d4f:<path>` (pre-edit) AND at `git show feature/agent-base-v2:<path>` (post-edit, with opencode wiring removed). The diff is committed.
- Every row's `Terminal state` is one of the three values.
- The `EXPECTED-EMPTY` section is empty (for this mission); the schema reserves the slot.
- The coder row is present in LEAVE ALONE with the opencode-free test wall invariant noted.
- The MCP stream finding (NO MCP configs anywhere) is recorded in the JSON companion.
- The ordering constraint (Phase B before opencode-stack removal) is recorded (see §12 O5 ordering).
- **R8 capture-before-boot verification note (architecture-recommendation.md §5 risk #14 + R8 amendment):** the base==v2 equality proof in Step 3 was captured BEFORE the boot-scan in Step 5 (per the ordering rule in §3). The map's pre-mission paths reference the v2 dirs and the post-mission base dirs; if the boot-scan had run before the equality proof, runtime `inner_soul` writes to `agents/<agent>/memory.md` would have mutated the base dir, breaking the diff for a non-architectural reason. Verify: the `git log --oneline feature/agent-base-v2` ordering shows Step 3's evidence file (`evidence/base-vs-v2-diffs/<name>.diff.txt`) was committed before Step 5's evidence file (`evidence/boot-scan/dev-lane-boot.log`).

**JSON companion** (recommended for downstream auditor): `evidence/before-after-map.json` with shape:

```json
{
  "scope": "opencode-stack-full (single package; session/API role + comm role layer inside; per addendum VERDICT 1)",
  "addendum_relay_received": "2026-10-13",
  "terminal_states": {
    "archived_to_legacy": "the standard path: file is mirrored to legacy/<path> and the active codebase no longer references it",
    "edited_in_place_git_preserved": "the file is edited in place to remove its opencode wiring; the pre-edit version is preserved in git history",
    "retained_live_user_decision": "the file is kept live in the active codebase because a live-used active agent depends on it; flagged as USER DECISION ITEM — expected EMPTY for this mission"
  },
  "streams": {
    "i_agents_base_copy": {
      "description": "Phase B: agents/<base> == agents/<base>[v2] (byte copy); v2 zero-touch",
      "rows": [
        {"pre": "agents/developer", "post": "agents/developer (overwritten, byte == [v2])", "terminal_state": "n/a (agents/ stream)", "manifest": "evidence/base-vs-v2-diffs/developer.diff.txt (empty)"},
        {"pre": "agents/developer[v2]", "post": "agents/developer[v2] (unchanged)", "terminal_state": "n/a (kept live)", "manifest": "evidence/post-mission-v2-manifests/developer[v2].manifest.txt == pre"}
      ]
    },
    "ii_opencode_stack_move": {
      "description": "Phase A + Phase C: opencode stack archived to legacy/ (single move; session/API role + comm role layer inside)",
      "terminal_state": "archived-to-legacy",
      "rows": [
        {"pre": "daemon/opencode/ (8 modules)", "post": "legacy/daemon/opencode/", "manifest": "evidence/legacy-reverify/daemon_opencode.manifest.txt"},
        {"pre": "daemon/tools/external_opencode.py", "post": "legacy/daemon/tools/external_opencode.py", "manifest": "evidence/legacy-reverify/daemon_tools_external_opencode.manifest.txt"},
        {"pre": "agents/_prompt_system/innate-skills/opencode/ (skill.md + .bak)", "post": "legacy/agents/_prompt_system/innate-skills/opencode/", "manifest": "evidence/legacy-reverify/innate_skills_opencode.manifest.txt"},
        {"pre": "tests/opencode/ (12 files)", "post": "legacy/tests/opencode/ (uniform single tree)", "manifest": "evidence/legacy-reverify/tests_opencode.manifest.txt"},
        {"pre": "test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}", "post": "legacy/test/packs/...", "manifest": "evidence/legacy-reverify/test_packs_opencode.manifest.txt"},
        {"pre": ".agents/shared/planning/opencode-native-tools/ (7 docs)", "post": "legacy/.agents/shared/planning/opencode-native-tools/", "manifest": "evidence/legacy-reverify/agents_planning_opencode_native_tools.manifest.txt"},
        {"pre": "docs/bugs/{opencode-session-manager-memory-leak.md, external-opencode-wait-latency.md}", "post": "legacy/docs/bugs/...", "manifest": "evidence/legacy-reverify/docs_bugs_opencode.manifest.txt"},
        {"pre": "example/SKILL.md (opencode reference)", "post": "legacy/example/SKILL.md", "manifest": "evidence/legacy-reverify/example_SKILL_md.manifest.txt"}
      ]
    },
    "iii_opencode_stack_touch": {
      "description": "Phase C: TOUCH surfaces edited in place; git history preserves pre-edit version",
      "terminal_state": "edited-in-place, git-preserved",
      "rows": [
        {"pre": "daemon/manager.py (opencode coordination blocks)", "post": "daemon/manager.py (blocks removed)", "pre_edit_evidence": "git show 885a19d4f:daemon/manager.py"},
        {"pre": "daemon/tools/instance.py (lines 155-167, 259, 5815-5818)", "post": "daemon/tools/instance.py (lines removed)", "pre_edit_evidence": "git history"},
        {"pre": "daemon/tools/_tool_registry.py (lines 603, 764-771)", "post": "daemon/tools/_tool_registry.py (registrations removed)", "pre_edit_evidence": "git history"},
        {"pre": "Makefile:174,186 / pyproject.toml:88 / .env.example:39 / daemon/config.py:168 (comment)", "post": "edited", "pre_edit_evidence": "git history"},
        {"pre": "tests/unit/test_coder_agent.py:40-43 (contamination guards MUST KEEP passing at :144, :168, :369+, :510, :253)", "post": "edited; test wall asserts guards pass", "pre_edit_evidence": "git history + Phase E test wall log"},
        {"pre": "tests/unit/test_innate_skills_refactoring.py / tests/migration/test_jsonb_migration.py:70-107 / mocks (send_message_fixtures.py:68, test_manager_shutdown.py:126, test_spawn_default_unchanged.py:163)", "post": "edited", "pre_edit_evidence": "git history"},
        {"pre": "comment rephrases (keyword_extraction.py, context_injection.py:541,846, context_messages.py, critical_note_gate.py:8, waiting_children_watchdog.py, loader.py:226, task/repository.py:1777)", "post": "edited (comments)", "pre_edit_evidence": "git history"},
        {"pre": "cosmetic one-liners (agents/explorer/soul.md, agents/devops/soul.md, agents/_prompt_system/knowledge.md)", "post": "edited (cosmetic)", "pre_edit_evidence": "git history"},
        {"pre": "docs refresh (docs/agents.md §2, docs/skill-evolution.md:174, README.md — Phase D's)", "post": "edited (docs refresh)", "pre_edit_evidence": "git history"}
      ]
    },
    "iv_opencode_stack_retained_live": {
      "description": "EXPECTED EMPTY for this mission (per addendum VERDICT 2 — full stack removal, no boundary-rule exception)",
      "terminal_state": "retained-live, USER DECISION ITEM",
      "rows": []
    },
    "v_mcp_configs": {
      "description": "Per addendum VERDICT 2: NO MCP configs anywhere reference opencode; daemon/mcp/ is zero (only built-ins context7, plane, webfetch); config.yaml is zero. The stream is empty.",
      "rows": []
    },
    "vi_leave_alone": {
      "description": "Surfaces that are NOT part of the opencode stack and stay live by definition (per addendum LEAVE ALONE list)",
      "rows": [
        {"surface": "agents/coder/", "reason": "opencode-free BY DESIGN (test wall invariant)"},
        {"surface": "daemon/services/keyword_extraction.py + daemon/services/context_injection.py", "reason": "D5 LIVE-SHARED infra; live callers: attestation_report_judge.py:35-328, daemon/graph.py:10794, plugins/opendesign/generate.py:650"},
        {"surface": "docs/mcp-kb-usage.md", "reason": "D6 false positive (upstream sst/opencode product)"},
        {"surface": ".agents/*/RESULTS|LESSONS", "reason": "Tier-5 historical records"},
        {"surface": "data/, data_dev/ DBs", "reason": "boot-recreate dies with code; repo-root stray DB = orphan test artifact (note in report)"},
        {"surface": ".inspiration-projects/opencode_skill_src/", "reason": "gitignored"}
      ]
    }
  },
  "boundary_rule_exceptions": [],
  "boundary_rule_exception_count": 0,
  "mcp_configs_finding": "NO MCP configs anywhere reference opencode",
  "ordering_constraint": "stack removal coupled to base-retirement via the innate-skill loader; removal lands WITH or AFTER Phase B, never before",
  "open_item_status": "O1 RESOLVED; O2 RESOLVED; O3 moot; O4 RESOLVED"
}
```

### Step 9.6 — FINAL RESIDUAL-REFERENCES AUDIT (whole-mission; FULL STACK; FINAL per addendum) [BINDING SCOPE FINAL 2026-10-13 + ADDENDUM]

After Phase A's archive commit AND Phase C's excision commit AND Phase D's docs refresh, sweep the WHOLE repo + docs + tests for ANY remaining reference to:
- `legacy/` paths (anything under `legacy/` mentioned by a non-archive file)
- opencode stack: `daemon/opencode/`, `daemon/tools/external_opencode.py`, `agents/_prompt_system/innate-skills/opencode/`, the opencode innate skill, opencode tools, opencode sessions, the `opencode_sessions` table, the `OpenCodeSessionManager` class
- TOUCH surfaces' opencode wiring: `daemon/manager.py` opencode blocks, `daemon/tools/instance.py` lines 155-167/259/5815-5818, `daemon/tools/_tool_registry.py` lines 603/764-771, `Makefile:174,186`, `pyproject.toml:88`, `.env.example:39`, the relevant mocks and test files
- archived files: anything that references a path that now lives under `legacy/`
- MCP-shaped files: any `*.json`, `*.yaml`, `*.yml`, `*.toml` referencing opencode (per addendum VERDICT 2: expected ZERO hits in the MCP surface)

**Sweep method** (one pass; classify each hit):

```
cd /home/nea/ensemble-src-wt-agent-base-v2
mkdir -p evidence

# Surface 1: legacy/ path references in the rest of the repo.
{ echo "=== Surface 1: legacy/ path references ==="; \
  grep -rn "legacy/" --include='*.py' --include='*.md' --include='*.sh' --include='*.json' --include='*.yaml' --include='*.yml' --include='*.ts' --include='*.html' \
    --exclude-dir=legacy --exclude-dir=.git --exclude-dir=data --exclude-dir=node_modules . ; \
  echo "=== Surface 2: opencode stack references (daemon-side) ==="; \
  grep -rnE "daemon/opencode|daemon/tools/external_opencode" \
    --include='*.py' --include='*.md' \
    --exclude-dir=legacy --exclude-dir=.git --exclude-dir=data . ; \
  echo "=== Surface 3: opencode tool/skill/session references ==="; \
  grep -rnE "opencode_session|OpenCodeSessionManager|opencode_sessions|innate_skills.*opencode|opencode.*innate_skill" \
    --include='*.py' --include='*.md' --include='*.json' --include='*.yaml' \
    --exclude-dir=legacy --exclude-dir=.git --exclude-dir=data . ; \
  echo "=== Surface 4: TOUCH surfaces' opencode wiring (residual) ==="; \
  grep -nE "opencode" daemon/manager.py daemon/tools/instance.py daemon/tools/_tool_registry.py Makefile pyproject.toml .env.example 2>/dev/null || true; \
  echo "=== Surface 5: MCP-shaped files referencing opencode (per addendum: expected ZERO) ==="; \
  grep -rn "opencode" --include='*.json' --include='*.yaml' --include='*.yml' --include='*.toml' \
    --exclude-dir=legacy --exclude-dir=.git --exclude-dir=data --exclude-dir=node_modules . 2>/dev/null || true; \
  echo "=== Surface 6: docs/plans/ and docs/bugs/ (Tier-5 historical) ==="; \
  grep -rn "opencode" docs/plans/ docs/bugs/ 2>/dev/null || true; \
  echo "=== Surface 7: .agents/ (Tier-5 historical + planning dir) ==="; \
  grep -rn "opencode" .agents/ 2>/dev/null || true; \
} > evidence/residual-references-audit.md 2>&1 || true
```

**Classification buckets (binding; defined here, used across both audits) — FINAL per addendum:**

1. **must-fix leftover** — a hit in active code/docs/tests that indicates the retirement is incomplete.
2. **known false positive** — `docs/mcp-kb-usage.md` (D6 — upstream sst/opencode product).
3. **historical record** — Tier-5 `.agents/*/RESULTS|LESSONS`, `docs/plans/`, `docs/bugs/`.
4. **intentional** — `legacy/` itself; runtime DB-resident opencode-table references (D7); `keyword_extraction` / `context_injection` LIVE-SHARED infra (D5); `agents/coder/` opencode-free test wall; the planning dir at `.agents/shared/planning/agent-base-v2/`; data/ and data_dev/ DBs (boot-recreate dies with code; repo-root stray DB is an orphan test artifact — note in report); `.inspiration-projects/opencode_skill_src/` (gitignored).

**Audit table** (commit as `evidence/residual-references-audit.md`; this is the post-classification view; the addendum's MOVE / TOUCH / LEAVE ALONE lists are the seed):

| File:line | Surface | Hit snippet | Classification | Action | Reason |
|---|---|---|---|---|---|
| `legacy/**` (any line) | n/a (in legacy/) | n/a | intentional | None | The archive itself; surface 0 |
| `daemon/opencode/server.py` (if not removed) | Surface 2 | `from daemon.opencode.server import ...` | must-fix leftover | Phase C re-runs excision; the file should be in `legacy/daemon/opencode/`, not in `daemon/opencode/` | Addendum VERDICT 1 + 2: package is dead; full-stack removal |
| `daemon/services/instance_messaging.py` (if it still imports opencode) | Surface 2 | `from daemon.opencode.client import ...` | must-fix leftover | Phase C re-runs excision | (same) |
| `daemon/manager.py` (any residual opencode line after Phase C) | Surface 4 | opencode init / coordination / cleanup code | must-fix leftover | Phase C re-runs excision; lines 78/1420-1436/2895-2910/3325-3338/5948-5956/6750-6751/13033/13078-13103 should be removed | Addendum TOUCH list: file is edited-in-place |
| `daemon/tools/instance.py` (residual opencode at lines 155-167/259/5815-5818) | Surface 4 | opencode-related spawn wiring | must-fix leftover | Phase C re-runs the in-place edit | (same) |
| `daemon/tools/_tool_registry.py` (residual opencode at lines 603/764-771) | Surface 4 | opencode tool registrations | must-fix leftover | Phase C re-runs the in-place edit | (same) |
| `Makefile:174,186` (residual) | Surface 4 | opencode references | must-fix leftover | Phase C re-runs the in-place edit | (same) |
| `pyproject.toml:88`, `.env.example:39` (residual) | Surface 4 | opencode references | must-fix leftover | Phase C re-runs the in-place edit | (same) |
| `tests/test_*.py` (if any test still references opencode imports) | Surface 3 | `import opencode` | must-fix leftover | Phase C re-runs excision; if test was supposed to be archived, Phase A re-runs the archive move | Addendum MOVE list: `tests/opencode/` (12 files) is the archive target |
| `tests/helpers/send_message_fixtures.py:68`, `tests/test_manager_shutdown.py:126`, `tests/integration/test_spawn_default_unchanged.py:163` (residual) | Surface 4 | opencode references in mocks | must-fix leftover | Phase C re-runs the in-place edit | Addendum TOUCH list (mocks) |
| `tests/unit/test_coder_agent.py:40-43` (residual) | Surface 4 | opencode references in test | must-fix leftover (BUT see note) | Phase C re-runs the in-place edit; the contamination guards at :144/:168/:369+/:510/:253 MUST KEEP passing (test wall invariant) | Addendum TOUCH list (test wall invariant) |
| `tests/unit/test_innate_skills_refactoring.py`, `tests/migration/test_jsonb_migration.py:70-107` (residual) | Surface 4 | opencode references in tests | must-fix leftover | Phase C re-runs the in-place edit | Addendum TOUCH list |
| `frontend/jobs.component.ts:2389` (if it has any opencode name pin) | Surface 3 (via JSON/YAML/TS) | opencode name pin | must-fix leftover IF opencode-related; intentional if just a v2 agent_dir pin (verify) | Phase C review | Frontend pin is verify-safe per mission rules |
| `*.mcp.json`, `mcp_servers` rows, `daemon/mcp/`, `config.yaml` referencing opencode | Surface 5 (MCP) | opencode transport in MCP | n/a (per addendum: NO MCP configs anywhere reference opencode; expected ZERO) | None | Addendum VERDICT 2: `daemon/mcp/` is zero (only built-ins `context7`, `plane`, `webfetch`); `config.yaml` is zero |
| `docs/mcp-kb-usage.md:12` | Surface 2 (via docs) | `~/.config/opencode/opencode.json` | known false positive | None | D6 — upstream sst/opencode product, not our service |
| `docs/mcp-kb-usage.md:34,46,48,50,73` (any) | Surface 2 (via docs) | opencode config / schema / oh-my-opencode-slim | known false positive | None | D6 — upstream product |
| `docs/agents.md:37,39,41,42,43,100,154,159,178,416,427` (if residual) | Surface 2 (via docs) | various opencode claims | must-fix leftover (if Phase D's edit missed a hit) | Phase D re-runs the docs sweep | Phase D sweep target |
| `docs/skill-evolution.md:174` (if residual) | Surface 2 (via docs) | `"innate_skills": ["opencode", "dynamic-skill"]` | must-fix leftover (if Phase D's edit missed) | Phase D re-runs the docs sweep | (same) |
| `docs/test-suite-known-issues.md:43` (if residual) | Surface 4 | `tests/opencode/test_integration.py (2)` | historical record | None | Post-Phase-A pointer to `legacy/tests/opencode/...`; out of Phase D scope |
| `docs/plans/decouple-execution-plan.md:*`, `docs/plans/decouple-review.md:59`, `docs/plans/cleanup-old-architecture.md:250`, `docs/plans/skill-metrics-improvement.md:442` (any) | Surface 6 | opencode-era file paths | historical record | None | Past planning record; not in mission scope |
| `docs/bugs/agent-call-wrong-tool-spawn-session.md:*` (if any remains) | Surface 6 | agent_id="opencode" tool call | historical record | None | Past bug record |
| `docs/bugs/opencode-session-manager-memory-leak.md` (if residual; was archived per addendum) | Surface 6 | session manager memory leak | historical record (and in `legacy/docs/bugs/`) | None | Past bug record; archived to `legacy/docs/bugs/` per addendum MOVE list |
| `docs/bugs/external-opencode-wait-latency.md` (if residual) | Surface 6 | `daemon/tools/external_opencode.py` wait latency | historical record (and in `legacy/docs/bugs/`) | None | Past bug record; archived to `legacy/docs/bugs/` per addendum MOVE list |
| `.agents/tester/RESULTS/2026-09-22-*.md`, similar Tier-5 historical files (any) | Surface 7 | opencode-era run | historical record | None | Tier-5 historical record; out of mission scope |
| `.agents/shared/planning/opencode-native-tools/` (if residual; was archived per addendum) | Surface 7 | opencode planning records | historical record (and in `legacy/.agents/shared/planning/opencode-native-tools/`) | None | Archived per addendum MOVE list |
| `.agents/shared/planning/agent-base-v2/phase*.md` (any line) | Surface 7 | this phase plan | intentional | None | The planning dir is part of the mission; references to `legacy/` are expected |
| `.agents/architect/memory/2026-10-01-keyword-extraction.md` | Surface 7 | `keyword_extraction` (D5) | intentional (D5) | None | D5 `keyword_extraction` is intentionally kept (LIVE-SHARED infra) |
| `daemon/services/keyword_extraction.py`, `daemon/services/context_injection.py` (any opencode-token reference) | Surface 3 (via code) | opencode-token references in LIVE-SHARED infra | intentional (D5) | None | Per addendum: D5 LIVE-SHARED infra; live callers attested (`attestation_report_judge.py:35-328`, `daemon/graph.py:10794`, `plugins/opendesign/generate.py:650`) |
| `agents/coder/meta.json` (or any coder file) referencing opencode | Surface 3 (via code) | opencode references in coder | must-fix leftover (if found) | Phase C re-runs; test wall invariant (contamination guards) | Addendum LEAVE ALONE: coder is opencode-free BY DESIGN |
| `data/postgres/ensemble_dev.db` (any opencode_* table reference) | Surface 3 (via DB) | `SELECT * FROM opencode_sessions` | intentional (D7) | None | Runtime DBs may carry opencode-skill seeds (D7); boot-recreate dies with code; any repo-root stray DB is an orphan test artifact (note in report) |
| `data/*.db`, `data_dev/*.db` (any opencode-table) | Surface 3 (via DB) | opencode-table | intentional (D7) | None | (same) |
| `~/.opencode_skill`, `~/.opencode` (runtime artifacts) | n/a (runtime) | runtime artifacts | intentional (runtime) | None | Per addendum: port 4095 not listening; no caller; runtime dead |
| `.inspiration-projects/opencode_skill_src/` | Surface 7 (gitignored) | gitignored inspiration reference | intentional (gitignored) | None | Not in active codebase |

**DoD for the residual-references audit:**
- Every hit in the seven surfaces is classified into one of the four buckets.
- Every "must-fix leftover" is REMEDIATED before the audit is considered passing.
- "known false positive" / "historical record" / "intentional" hits are ACCEPTED with a one-line reason.
- **Surface 5 (MCP) is expected ZERO** per addendum VERDICT 2. If a hit surfaces, it is a must-fix leftover (or it contradicts the addendum; in that case the row's Classification updates and a USER DECISION ITEM is filed).

**Verification (whole-mission):** the final paragraph of `evidence/residual-references-audit.md` states: "Residual-references audit complete. <N> total hits across the seven surfaces. <N_mustfix> must-fix (all remediated), <N_falsepos> known false positive, <N_historical> historical, <N_intentional> intentional. Zero unclassified hits. Surface 5 (MCP) hit count: 0 (matches addendum VERDICT 2)."

**No "pending addendum" markers remain** — the addendum landed 2026-10-13 and the audit's classification table incorporates its MOVE / TOUCH / LEAVE ALONE findings directly.

### Step 10 — Drift/duplication notes (NOT solved; recorded for the report)

Hand-author `evidence/drift-notes.md` with:

- **Steady state** — base agents are now a frozen mirror of v2 content (post-Phase-B). The five v2 dirs remain THE live lane (instances resolve to `[v2]` per `default_agent_versions` runtime invariant).
- **Drift hazard** — any future edit to a v2 dir that is NOT mirrored to its base produces silent base drift; the `base-vs-v2-diffs/<name>.diff.txt` evidence files become non-empty. Maintenance discipline: edits to v2 content must decide explicitly whether the base mirrors (default: yes, until a v3 emerges) or diverges (then re-version).
- **D3 wrinkle** — `developer[v2]/memory.md` and `reviewer[v2]/memory.md` may carry dual-contract prose (some content written for the v2 contract, some v1-era prose that survived the v2 promotion). The Phase B copy inherits this as-is. Future memory edits must remain version-aware; cross-version sharing is the historical mode and now INVERTED (base is the v2 mirror, not the other way).
- **Maintenance surface** — the ~12 per-agent meta pin test files now pin v2 values in the base dir (Phase B's update). These pins become an upgrade-friction point: future v2 content edits that should propagate to base must keep the pin files updated too. Recorded as a known cost, not solved here.

### Step 11 — Whole-mission Definition of Done

Hand-author `evidence/definition-of-done.md` aggregating every phase's DoD:

| # | Criterion | Evidence file | Pass criterion |
|---|---|---|---|
| 1 | v2 zero-touch | `post-mission-v2-manifests/<name>[v2].manifest.txt` vs `pre-mission-v2-manifests/<name>[v2].manifest.txt` | All five diffs EMPTY |
| 2 | base == v2 | `base-vs-v2-diffs/<name>.diff.txt` | All five diffs EMPTY |
| 3 | legacy/ archive intact (BOTH retirement streams) | `legacy-reverify/*.manifest.txt` vs Phase A pre-mission manifests | All diffs EMPTY; covers comm service (ii) and session/API lane (iii) |
| 4 | Full test wall green | `test-wall/*.log` | 0 FAILED across the pinned packs + unit suite |
| 5 | Boot-scan clean (BOTH retirement streams) | `boot-scan/dev-lane-boot.log` + `opencode-residue-grep.txt` + `daemon-opencode-residue.txt` | Livez+readyz GREEN; zero opencode boot log; daemon residue matches Phase C's expected residual for both streams |
| 6 | `default_agent_versions` invariant | `default-agent-versions/compare.txt` | EMPTY (byte-for-byte pre == post) |
| 7 | Skill-seed no-op | `skill-seed/per-agent-side-effects.md` | All five migrated agents: updated=0, new=0, errors=0 |
| 8 | Docs-consistency sweep clean (FULL STACK, both retirement streams) | `docs-consistency-sweep-final.md` | Zero unclassified `opencode` hits in `docs/`, `README.md`, `AGENTS.md` |
| 9 | Drift/duplication posture recorded | `drift-notes.md` | Hand-authored |
| 10 | BEFORE/AFTER MAP (whole-mission; FULL STACK with three terminal states) | `before-after-map.md` (+ optional `.json`) | Map covers agents/ + opencode stack; every row has one of three terminal states (archived-to-legacy / edited-in-place, git-preserved / retained-live-expected-empty); the retained-live section is empty (addendum VERDICT 2: no boundary-rule exception) |
| 11 | Final residual-references audit (whole-mission; FULL STACK) | `residual-references-audit.md` | All hits across the seven surfaces classified into the four buckets; zero unclassified; zero unaddressed must-fix leftovers; Surface 5 (MCP) hit count is 0 (matches addendum VERDICT 2) |
| 12 | **Ordering constraint** (Phase B before opencode-stack removal) | (cross-cutting — verified via commit graph) | `git log --oneline feature/agent-base-v2` shows Phase B's commit precedes the opencode-stack move; the base-copy landed WITH or BEFORE the opencode excision (innate-skill loader constraint) |
| 13 | **Coder opencode-free test wall invariant** | `test-wall/test_coder_agent.py.log` (or similar) | Contamination guards at `tests/unit/test_coder_agent.py:144, :168, :369+, :510, :253` PASS after Phase B/C/D (test wall invariant, not a dependency) |
| 14 | **Ensemble unregressed** (acceptance criterion) | (cross-cutting — verified via test wall + boot-scan + default-agent-versions) | Test wall green; boot-scan clean; `default_agent_versions` invariant; no live-system contact beyond the two fenced dev-lane interactions; v2 zero-touch; base == v2 |
| 15 | **R10 FINAL TESTER GATE (binding user directive)** [ADDED 2026-10-13] | `evidence/r10-allowlist.md` + `evidence/r10-gate-output.txt` | `git grep -niE 'opencode' -- . [excludes]` returns 0 matches; allowlist file is committed with one row per excluded path + one-sentence justification per row (R10 §11.3); boundary cases (R10 §11.4 #1–#4) handled with per-file justification; `docs/bugs` exclusion is conditional (only archived + historical); mandatory-fix set (R10 §11.2) all applied |
| 16 | No live-system contact beyond boot-scan evidence + single read-only GET | (cross-cutting) | Audit trail: only the Step 5 daemon boot + Step 6 GET ran against the worktree dev lane |

Hand-author `evidence/rollback.md` with:

- **Per-phase rollback**: `git revert <sha>` of the offending phase's commit. Each phase is a discrete commit on `feature/agent-base-v2`.
- **Whole-mission rollback**: fresh branch from `885a19d4f`. Phase A's `legacy/` archive, Phase B's base copy, Phase C's daemon changes (TOUCH + MOVE), Phase D's docs edits, Phase E's evidence files — all additive, none are un-droppable. The mission is cleanly abandonable by deleting the branch.
- **Legacy/ + plan docs + evidence are additive and safe**: deleting them on rollback does not lose anything from `885a19d4f`.
- **Ordering constraint (binding)**: stack removal is coupled to base-retirement via the innate-skill loader. While the 5 old-base agents still declare `innate_skills: ["opencode"]`, both `agents/_prompt_system/innate-skills/opencode/` AND `daemon/tools/instance.py:155-167` (the innate-skill grant map) must still resolve. **Removal lands WITH or AFTER Phase B, never before.** If rollback needs to revert Phase C while keeping Phase B, the rollback MUST also re-instate the opencode stack (or the innate-skill loader will fail to resolve `"opencode"` for the now-v2-content base agents — wait, post-Phase-B the base agents are v2 content, which does NOT declare `["opencode"]`, so the loader won't need the skill). The correct rollback order is: reverse Phase D first (restore docs), then reverse Phase C (re-instate opencode stack, if it was removed), then reverse Phase B. Reverse Phase A last (or skip; the legacy/ archive is additive and safe to leave).
- **NO promote performed** (D8). Zero live exposure. The mission rides the next promote ceremony via the fast pipeline (`scripts/upgrade/stage.sh` manifest machinery self-adapts: `agents_tree_sha256` and `agents_manifest` recompute automatically because they're computed from the tree at stage time — no pinned values to update). The free runtime verification: pre-mission `default-agent-versions` value carries through to the post-promote boot unchanged.

### Step 12 — Commit the evidence pack

```
cd /home/nea/ensemble-src-wt-agent-base-v2
git add .agents/shared/planning/agent-base-v2/
git commit -m "evidence: verification pack for agent-base-v2 migration (Phase E)"
```

### Step 13 — FINAL TESTER GATE (R10 BINDING GATE; architecture-recommendation.md §11) [ADDED 2026-10-13]

**This is the new DoD criterion for the whole mission.** Post-migration, ANY active-tree `opencode` reference (definition, prompt, tool config, skill, workflow doc, any agent-facing surface) is a **DEFECT → FIX**, not a note. R10 supersedes the prior "what still knows about legacy paths" sweep's REPORT-only semantics for agent surfaces and upgrades it to a TESTER GATE.

**Gate mechanics (verbatim from architecture-recommendation.md §11.3; exclusion set expanded per review W4 2026-10-13 + iteration 002 B1 fix-pass 3 2026-10-13):**

```
cd /home/nea/ensemble-src-wt-agent-base-v2
# NOTE: use `git grep` (NOT `grep -r`) — git grep walks the COMMITTED tree only,
# automatically skips untracked/gitignored paths (data*/, __pycache__,
# .inspiration-projects/). `grep -r` would sweep untracked paths and the
# orphan opencode_sessions.db record (D7). Tracked-tree semantics.
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

**Reconciliation (fix-pass 3 iteration 002, post-`test_persistence_w1_markers.py` drop):** the prior pass excluded `tests/integration/test_persistence_w1_markers.py` as a negative-guard comment. The plan no longer excludes it — phaseC's convergence-addendum row keeps the file in class (c) (editable ACTIVE test docstring; sibling rephrases :15 "This test does NOT require a live OpenCode server" to a still-current equivalent). The file is now in bucket (ii) TOUCH. Post-drop gate: 1,843 hits / 98 distinct files.

**GATE: zero matches.**

**Scope = git-TRACKED active tree.** `git grep` automatically skips untracked/gitignored paths (`data*/`, `.inspiration-projects/`, `__pycache__`); untracked orphan DBs never reach the gate (D7 intact).

**Case-insensitive 'opencode' subsumes:** `external_opencode`, `OPENCODE_URL`, `opencode_sessions`, `OPENCODE_*` — no separate patterns needed. The single regex covers the entire textual surface.

**Boundary cases (R10 §11.4 — verified / to-verify at execution; exclusion set expanded per W4 review):**

1. **`docs/mcp-kb-usage.md`** — upstream `sst/opencode` EXTERNAL product docs (D6). Line-by-line verify strictly-upstream context at execution; FIX or move any our-stack line; allowlist row with recorded justification (one-sentence). Without justification → FAIL gate review.
2. **`plugins/opendesign/copy_freely/design-systems/opencode-ai/**`** — vendored third-party design-system resource (provenance class `copy_freely`; `plugins/` IS shipped in release payloads per `stage.sh:407`; cannot move to `legacy/` without breaking the OD plugin snapshot). Same treatment: line-by-line strictly-upstream verification + allowlist row with justification. **NOT a user-decision item** — provably third-party vendored.
3. **`.gitignore` opencode lines** — INTENTIONAL-KEEP row: the `opencode_sessions.db` ignore remains functional (keeps D7 orphan DBs out of `git status` post-migration). Justify in the allowlist file.
4. **`.kilo/` (66 lines across tracked files mention opencode, per W4 dry-run)** — unclassified residue surfaced by the census; executor classifies at gate time (historical tool state → allowlist with justification; active config → fix).
5. **`.agents/shared/planning/**`** — planning history (sibling phase plans, planning directories). Per W4 dry-run, this subtree is historical/audit material; gate-excluded via `':(exclude).agents/shared/planning' ` and `':(exclude).agents/shared/planning/**'`. Allowlist row with one-sentence justification: "planning history; out of mission scope for editing."
6. **`.agents/*/RESULTS*`** — per-agent RESULT records (Tier-5 historical audit material). Gate-excluded via `':(exclude).agents/*/RESULTS*'`. Allowlist row: "Tier-5 RESULT records; historical audit material; out of mission scope."
7. **`.agents/*/LESSONS*`** — per-agent LESSONS records (Tier-5 historical audit material). Gate-excluded via `':(exclude).agents/*/LESSONS*'`. Allowlist row: "Tier-5 LESSONS records; historical audit material; out of mission scope."
8. **`.agents/tester/PACKS.md`** — test-pack registry file; references moved pack baselines at `:922,:957,:1272,:2157` (P4 stale-HISTORY). Gate-excluded via `':(exclude).agents/tester/PACKS.md'`. Allowlist row: "tester PACKS.md; P4 stale-HISTORY references to retired pack basenames; no runner enforces presence (architecture-recommendation §6 P4)." **This row is BOTH gate-excluded AND allowlisted — no contradiction** (per W4 review consistency check in phaseD).

**Conditional `docs/bugs` exclusion:** only the two opencode bug docs (moved to `legacy/docs/bugs/` in C.3) plus per-file historical-incident rows justified in the allowlist. **Any `docs/bugs` row describing CURRENT active-stack behavior is FIXED, not excluded.** The executor reviews each `docs/bugs` row at exec time and either justifies it as historical (allowlist) or marks it for fix (mandatory).

**Allowlist file mechanics:**
- The committed allowlist file is `evidence/r10-allowlist.md` (hand-authored, executor's job).
- One row per excluded path + one-sentence justification.
- **Rows without justification FAIL gate review.**
- The allowlist consumes Phase D's classification table (the docs surface hits) and adds the cross-tree hits (plugins, .gitignore, .kilo, docs/bugs, .agents/tester/PACKS.md P4 rows, test/packs/regression_integration_test.sh:40, **and per W4 review: `.agents/shared/planning/**`, `.agents/*/RESULTS*`, `.agents/*/LESSONS*`, `.agents/tester/PACKS.md`, `.kilo`**).
- The W4-expanded allowlist rows (one row per new exclude) are:
  - `.agents/shared/planning/**` — **planning history**; out of mission scope for editing.
  - `.agents/*/RESULTS*` — **RESULTS-LESSONS audit material**; Tier-5 historical; out of mission scope.
  - `.agents/*/LESSONS*` — **RESULTS-LESSONS audit material**; Tier-5 historical; out of mission scope.
  - `.agents/tester/PACKS.md` — **tester PACKS historical references**; P4 stale-HISTORY (architecture-recommendation §6 P4); no runner enforces presence. **BOTH gate-excluded AND allowlisted — no contradiction** (per W4 review consistency check in phaseD).
  - `.kilo` — **historical tool state**; 66 lines across tracked files (per W4 dry-run); not active config.

**R10 grounding (architecture-recommendation.md §11.5–§11.8 — load-bearing checks PASSED):**
- **§11.5 — Load-bearing check:** `grep -rniE 'opencode'` over all five v2 trees (`agents/{developer,reviewer,tidier,approver,planner}[v2]/`) returns **ZERO matches** (verified 2026-10-13). The gate is satisfiable **without violating v2 zero-touch (D4)**. Base==v2 (post-B) inherits this cleanliness. No zero-touch-vs-opencode-free conflict exists.
- **§11.6 — Skills-surface sweep:** `agents/_prompt_system/innate-skills/` minus the retiring `opencode/` dir is **clean**; all `skill-set.yaml` across `agents/` are **clean**; all `agents/*/skills-template/` content is **clean**; `agents/_prompt_system/` outside innate-skills has **2 hits** at `knowledge.md:96,:106` → mandatory-fix. Post-C.3 the `opencode` innate-skill dir lives only in `legacy/`.
- **§11.7 — R8 interplay:** the gate greps the COMMITTED tree (`git grep`), so runtime `memory.md` mutation (inner_soul) cannot contaminate it. R8's capture-before-boot rule still governs the base==v2 *equality proof*; the two checks are independent and both stand.
- **§11.8 — Census:** `.agents` 306 (1,346 lines under `.agents/` mention opencode — per W4 dry-run: planning history + RESULTS/LESSONS audit material + tester PACKS historical references — all routed to allowlist; gate-excluded via the five expanded exclude paths) · `tests` 37 + `test` 7 (→ C.3 moves + C.4/R1/R2 edits + P7 stragglers) · `agents` 28 (→ retiring metas+dir [C], soul/knowledge fixes [§11.2]) · `plugins` 27 (boundary case #2) · `daemon` 20 (→ §11.2 rephrase set + module docstrings) · `docs` 19 (→ Phase D FIX + historical rows) · `backup` 12 (historical archive → allowlist) · plus singles: `testing-guide.md` (Phase D), `pyproject.toml` (P3), `example/SKILL.md` (C.3 → legacy), `Makefile` (R5), `LESSONS`/`.slim` (historical), `.gitignore` (boundary #3), `.env.example` (TOUCH), `.kilo` (boundary #4 — 66 lines across tracked files, per W4 dry-run). **Every census bucket maps to a disposition — no orphan class remains.**

**Step 13 sub-steps:**

1. Pre-flight: confirm `evidence/r10-allowlist.md` is committed with one row per excluded path + one-sentence justification per row. Rows without justification FAIL this step.
2. Run the verbatim gate command. Output: `git grep -niE 'opencode' -- . [excludes]` with the listed exclusions.
3. GATE check: zero matches. If non-zero, every match is either (a) a mandatory FIX the responsible phase missed, or (b) a row that belongs in `evidence/r10-allowlist.md` (but the executor forgot to add it). The mission is closed until the gate is clean.
4. Verification: the gate's output is committed to `evidence/r10-gate-output.txt` with the timestamp and the count of matches (zero).
5. **Cross-check vs. Phase D's docs-consistency sweep: MANDATORY (N9 fix-pass 2 — this sub-step cannot be skipped).** Every allowlist row in `evidence/r10-allowlist.md` is justified per R10 §11.3 (one sentence) AND its classification in Phase D's table matches. The cross-check is **mandatory** for the **`.kilo/`** (R10 §11.4 #4, executor-classified) and **`docs/bugs`** (conditional — only archived + historical; any active-stack row is a defect) exclusions; the wholesale-exclude pattern does NOT exempt the executor from verifying that the allowlist justification is present and consistent with Phase D's classification. If the cross-check finds an allowlist row WITHOUT justification OR a row whose classification in Phase D is MANDATORY FIX (not historical), the mission is closed until the allowlist is corrected.

**R10 supersession notes (architecture-recommendation.md §11):**
- R10 EXTENDS R5 (Makefile comment) and R6 (C-boundary gate) to the whole active tree as the final Phase E gate.
- R10 SUPERSEDES: phaseC Tier-2's "optional / if unsure LEAVE" framing, recommendation P7's optional framing, and Phase D's 4-bucket classification (only the explicit allowlist survives as "leave").
- R10 does NOT contradict R1–R9.

**DoD for the FINAL TESTER GATE:**
- `evidence/r10-allowlist.md` is committed with one row per excluded path + one-sentence justification per row.
- The gate command runs and produces zero matches.
- `evidence/r10-gate-output.txt` is committed (count = 0, timestamp).
- Every active-tree opencode reference in the post-mission tree is either fixed or allowlisted with justification.

**Negative-guard allowlist class (structural-tension callout, fix-pass 3 iteration 002; post-reconciliation 2026-10-13):** some files in the active tree carry `opencode` references by DESIGN — the strings ARE the assertion, not a leak. The test method `tests/unit/test_coder_agent.py::test_innate_skills_does_not_contain_opencode` (line 144) asserts `"opencode" not in meta.get("innate_skills", [])`. Removing the string removes the guard. The same pattern holds for `tests/unit/test_devops_agent.py::test_no_opencode_skill_content_in_system_prompt` (line 337) and `test_devops_does_not_have_opencode_tools` (line 534) and `tests/unit/test_reviewer_v2_agent.py::test_opencode_not_in_innate_skills` (line 130 — D7 invariant: reviewer[v2] must NOT depend on opencode). **The gate reaches zero over these strings ONLY via justified allowlist excludes** — the allowlist file documents each with the justification "intentional-negative contamination guard; removing the string removes the guard." The matching gate `:(exclude)tests/unit/test_coder_agent.py` / `:(exclude)tests/unit/test_devops_agent.py` / `:(exclude)tests/unit/test_reviewer_v2_agent.py` pathspecs handle this. Same pattern: `plugins/opendesign/snapshot_with_drift_alarm/prompts/daemon/system.ts` and `media-contract.ts` are vendored OD-plugin snapshot files OUTSIDE the copy_freely subtree (verified by the snapshot_with_drift_alarm provenance class); allowed by `':(exclude)plugins/opendesign/snapshot_with_drift_alarm/**'`. **NOTE (post-reconciliation 2026-10-13):** `tests/integration/test_persistence_w1_markers.py` was REMOVED from the negative-guard class — its line 15 ("This test does NOT require a live OpenCode server") is editable ACTIVE test docstring, not a string-IS-the-assertion guard; phaseC's convergence-addendum row keeps the file in class (c) editable-prose rephrase; the file is now in bucket (ii) TOUCH. Excluding an editable ACTIVE test file would be the weaker disposition.

**B1 fix-pass 3 iteration 002 — CORRECTED decomposition of the FINAL gate's TRUE output (post-reconciliation 2026-10-13):** the FINAL gate (with the v4 exclude set: 48 pathspecs — v3 set MINUS the dropped `':(exclude)tests/integration/test_persistence_w1_markers.py'`) was EXECUTED on the current worktree HEAD `885a19d4f`. **Result: 1,843 hits across 98 distinct files.** Every file in the output decomposes into EXACTLY ONE of three disposition buckets (no masking; bucket (iii) is now the set of files the pathspecs DO exclude, documented for audit, not files in the output):

| Bucket | Files | Hits | Description | Post-mission state |
|---|---|---|---|---|
| **(i) MOVES to legacy/ (excluded by `:(exclude)legacy`)** | 26 | 1,457 | `tests/opencode/*` (11 files: test_tools, test_server, test_session_manager, test_registry, test_table_creation, test_integration, test_error_propagation_edge_cases, test_client, test_repository, test_state, conftest.py), `daemon/tools/external_opencode.py`, `daemon/opencode/*` (8 modules: __init__, server, client, registry, repository, session_manager, state, constants), `agents/_prompt_system/innate-skills/opencode/skill.md` + `skill.md.bak`, `example/SKILL.md`, `test/packs/{opencode_native_tools_unit_test.sh, ab/top_opencode_unit_test.sh, regression_opencode_e2e_test.sh}` (3 packs) | Post-Phase-C.3, all files live in `legacy/`; the gate's `:(exclude)legacy` pathspec excludes them. **0 hits post-mission.** |
| **(ii) EDITED IN PLACE (TOUCH: Phase B replaces + Phase C.4 + Phase D FIX + Phase D B2 + Phase D B4 + B3 sibling + P7 stragglers + new TOUCH from this pass + persistence_w1_markers.py returning from exclusion + 2 files owned by phaseC's convergence-addendum rows)** | 72 | 386 | Phase B replaces (5 base agent dirs): `agents/{developer,reviewer,planner,approver,tidier}/{meta.json, rule.md, workflow.md, soul.md, tools_note.md, memory.md}` (post-B replaced by v2 content; no opencode). Phase C.4: `daemon/manager.py` (39), `daemon/tools/instance.py` (10), `daemon/tools/_tool_registry.py` (9), `daemon/services/{context_messages(3), waiting_children_watchdog(2), critical_note_gate(1)}.py`, `daemon/repositories/task/repository.py` (1), `daemon/loader.py` (1), `daemon/config.py` (1), `daemon/services/{keyword_extraction(4), context_injection(2)}.py` (D5 LIVE-SHARED, comment-only rephrase), `tests/test_innate_skills_refactoring.py` (28, P1 — 17+ sites), `tests/test_tool_filter.py` (27, R1 — 3 tests), `tests/migration/test_jsonb_migration.py` (16, R2 — full-file re-grep), `tests/unit/test_wanderer_agent.py` (18, P2 — wanderer mirror), plus the 3 mock-strip files: `tests/helpers/send_message_fixtures.py:68` (2, B4 fix-pass 3 — correct path), `tests/test_manager_shutdown.py:126` (1), `tests/integration/test_spawn_default_unchanged.py:163` (1, B4 fix-pass 3 — correct path). **P7 stragglers (cross-ref phaseC §13.5):** `test/packs/lcan_helpers/mock_llm.py:141-142` (2), `test/packs/regression_integration_test.sh:31,:40` (2), `test/packs/regression_chat_source_integration_test.sh:8` (1), `tests/integration/test_auth_boot_preflight.py:20` (1), `tests/unit/services/test_critical_notes_phase2_filters.py:11` (1). **New TOUCH (this pass):** `test/packs/wc_wake_off_bytecompat_probe_test.py:293` (patch of non-existent `create_opencode_tools`), `tests/test_council_tools.py:55` (same patch), `tests/test_governor_integration.py:61,:955` (patches), `tests/test_governor_recursion_acceptance_walk.py:35` (stale comment), `:336` (patch), `tests/test_spawn_instance_instructive_errors.py:26,:42,:50,:362,:368` (uses "opencode" as test agent id — rephrase to a still-existing agent), `tests/test_spawn_team_members.py:42` (stale comment), `:58` (patch), `tests/tools/test_auto_kill_integration.py:992` (patch of non-existent `_shutdown_opencode_registry`), `tests/tools/test_send_message_context_param.py:84` (stale comment), `:106` (patch), `tests/tools/test_send_message_load_skill.py:55` (stale comment), `:77` (patch), `tests/unit/test_governor_recursion_guard.py:170` (patch), `tests/unit/tools/test_prompt_section_reference_integrity.py:815` (stale comment — **owned by phaseC's convergence-addendum rows**), `tests/unit/tools/test_spawn_councilor_default_version.py:126` (patch), `tests/unit/tools/test_version_tag_tool_resolution.py:432,:1104,:1292` (patches). **Persistence_w1_markers.py (returning from exclusion, post-reconciliation):** `tests/integration/test_persistence_w1_markers.py:15` (1, comment "This test does NOT require a live OpenCode server" — editable ACTIVE test docstring; phaseC's convergence-addendum row keeps the file in class (c) editable-prose rephrase; **not allowlist-eligible**). Phase D FIX: `docs/agents.md` (11, R10 §11.2), `docs/skill-evolution.md` (1, R10 §11.2), `docs/mcp-integration.md:364,:366` (2, B2 fix-pass 3 — active MCP-integration doc), `testing-guide.md:15,:94` (2, B4 fix-pass 2 — root-level active guidance). Phase C TOUCH (B3): `agents/maintenancer/knowledge/01-architecture-overview.md:6,:16` (2, sibling worker adds to Phase C TOUCH — comment-only rephrase, NOT allowlist-eligible). Phase D cosmetic: `agents/explorer/soul.md` (1), `agents/devops/soul.md` (1, R10 §11.2). Misc TOUCH: `Makefile:173-174,:186` (2, R5), `pyproject.toml` (1), `.env.example` (1), `.gitignore` (1, kept for D7). **Cross-file dependency note (post-reconciliation 2026-10-13):** bucket (ii) includes 2 files owned by phaseC's convergence-addendum rows — `tests/test_spawn_instance_instructive_errors.py` (5 hits) and `tests/unit/tools/test_prompt_section_reference_integrity.py:815` (1 hit). These are being added to phaseC §13.5 by the sibling worker in parallel; the planning artifact acknowledges the cross-file dependency and the dispositions are consistent (all 6 hits / 2 files in bucket (ii) editable-prose or patch — NOT allowlist). | Post-TOUCH, all 72 files are opencode-free. **0 hits post-mission.** |
| **(iii) ALLOWLISTED via the gate's pathspecs (already excluded from output; documented for audit)** | 5 | 83 | Negative-guard class (3 files, 78 hits): `tests/unit/test_coder_agent.py` (43, contamination guards at :144/:148/:168/:173/:253/:264/:268/:269/:369/:384 — D7 invariant: coder must NOT depend on opencode; strings ARE the assertion; removing the strings removes the guard), `tests/unit/test_devops_agent.py` (31, `test_no_opencode_skill_content_in_system_prompt` at :337 + `test_devops_does_not_have_opencode_tools` at :534 + deny-list fixtures at :546-551), `tests/unit/test_reviewer_v2_agent.py` (4, `test_opencode_not_in_innate_skills` at :130 — D7 invariant: reviewer[v2] must NOT depend on opencode). Vendored snapshot class (2 files, 5 hits): `plugins/opendesign/snapshot_with_drift_alarm/prompts/daemon/system.ts` (4, OD-plugin snapshot with drift-alarm provenance class), `plugins/opendesign/snapshot_with_drift_alarm/prompts/daemon/media-contract.ts` (1, same). **NOTE (post-reconciliation 2026-10-13):** `tests/integration/test_persistence_w1_markers.py` (1 hit) was REMOVED from the negative-guard class; the file is now in bucket (ii) TOUCH (sibling rephrases the editable docstring). | Post-mission: each file is on the allowlist with a one-sentence justification (intentional-negative contamination guard; OR vendored third-party snapshot). The gate's pathspecs exclude each. **0 hits post-mission.** |

**Decomposition total (post-reconciliation 2026-10-13): 26 + 72 + 5 = 103 distinct files; 1,457 + 386 + 83 = 1,926 hits (PRE-v4 pathspecs); 1,843 hits in the FINAL gate output (post-v4); 83 hits excluded by the v4 pathspecs. ZERO un-dispositioned residue.** Every file in the FINAL gate output (the 1,843 hits across 98 files) is in either bucket (i) MOVES or bucket (ii) TOUCH; the 83 hits / 5 files in bucket (iii) are NOT in the output (they're excluded by the v4 pathspecs but documented here for audit). This is the post-reconciliation corrected decomposition — the prior pass-3 bucket (iii) erroneously listed files the pathspecs ALREADY excluded; this time the bucket is the audit-trail record of those pathspecs' effect, not a re-statement of files-in-the-output.

**Re-execution evidence (B1 fix-pass 3 iteration 002; post-reconciliation 2026-10-13):**
```
# Command (this is the FINAL gate, verbatim, post-reconciliation — 48 pathspecs):
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
  ':(exclude)plugins/opendesign/snapshot_with_drift_alarm' \
  ':(exclude)plugins/opendesign/snapshot_with_drift_alarm/**' \
  ':(exclude)docs/mcp-kb-usage.md' \
  ':(exclude)docs/bugs' ':(exclude)docs/bugs/**' \
  ':(exclude)docs/plans' ':(exclude)docs/plans/**' \
  ':(exclude)docs/test-suite-known-issues.md' \
  ':(exclude)docs/optimization' ':(exclude)docs/optimization/**' \
  ':(exclude)tests/unit/test_coder_agent.py' \
  ':(exclude)tests/unit/test_devops_agent.py' \
  ':(exclude)tests/unit/test_reviewer_v2_agent.py' \
# NOTE: 48 pathspecs total (the v3 set MINUS ':(exclude)tests/integration/test_persistence_w1_markers.py')

# Output: 1,843 hits, 98 distinct files (post-reconciliation 2026-10-13)
# Decomposition:
#   bucket (i) MOVES:                       26 files, 1,457 hits
#   bucket (ii) TOUCH:                     72 files,   386 hits (incl. persistence_w1_markers.py returning from exclusion + 2 sibling-owned files)
#   bucket (iii) ALLOWLISTED (excluded):    5 files,    83 hits (NOT in output; documented for audit)
#   TOTAL:                                103 files, 1,926 hits (pre-v4 pathspecs) / 1,843 hits (post-v4, in output)
#   EMPTY RESIDUE ASSERTION: zero un-dispositioned files in the output
```

Commit on `feature/agent-base-v2`. Single commit (or split per evidence category if a single commit is too large; either is acceptable). Do NOT push.

## 6. Per-Step Verification

| Step | Verification command | Expected outcome |
|---|---|---|
| 0 | `cat evidence/TOOL.txt` | Lists `uname -s` + the chosen `sha256sum`/`shasum` path |
| 1 (pre) | `ls evidence/pre-mission-v2-manifests/` | 5 manifest files + 5 digest files |
| 2 (post) | `for f in evidence/post-mission-v2-manifests/*.manifest.txt; do diff evidence/pre-mission-v2-manifests/$(basename $f) $f; done` | No output (all diffs empty) |
| 2 (second-order) | `git status --porcelain -- 'agents/*[v2]*'` | Empty |
| 3 | `for f in evidence/base-vs-v2-diffs/*.diff.txt; do [ ! -s $f ] && echo "$f EMPTY" || echo "$f NON-EMPTY"; done` | All five EMPTY |
| 4 | `for f in evidence/legacy-reverify/*.manifest.txt; do diff <(Phase A pre-mission manifest) $f; done` | No output (all diffs empty) |
| 5 | `grep -i "opencode" evidence/boot-scan/dev-lane-boot.log \| wc -l` | 0 |
| 6 | `diff evidence/default-agent-versions/pre.json evidence/default-agent-versions/post.json` | No output |
| 7 | `diff evidence/skill-seed/pre-bank-counts.json evidence/skill-seed/post-bank-counts.json` | No output |
| 8 | `grep -h "FAILED\|FAILED" evidence/test-wall/*.log \| wc -l` | 0 |
| 9 | `cat evidence/docs-consistency-sweep-final.md` | All residual hits classified per Phase D table (covers BOTH retirement streams i + ii) |
| 9.5 | `cat evidence/before-after-map.md` (or `.json`) | Map covers all three streams (i agents/, ii comm service, iii session/API lane); every pre/post path verifiable via `git show` |
| 9.6 | `cat evidence/residual-references-audit.md` | Audit covers all seven surfaces (N3 fix-pass 2: was incorrectly stated as "five"; the actual surface count is 7 — legacy/ paths, daemon-side opencode, opencode tool/skill/session, TOUCH surfaces' opencode wiring, MCP-shaped files, docs/plans + docs/bugs, .agents/); every hit classified into the four buckets; zero unclassified; zero unaddressed must-fix leftovers |
| 13 | `cat evidence/r10-allowlist.md` (committed) + `git grep -niE 'opencode' -- . [excludes]` returns 0 matches + `evidence/r10-gate-output.txt` shows count=0 | FINAL TESTER GATE satisfied: allowlist file present with one-sentence justification per row; gate command produces zero matches; gate output committed |
| R8 (cross-step) | Steps 1, 2, 3, 4, 4.5 run BEFORE Step 5 (boot-scan); verification: `git log --oneline feature/agent-base-v2` shows the order in which Phase E's recipe ran (executor can re-derive from the timestamps on the evidence files in `evidence/`) | R8 capture-before-boot ordering rule satisfied |
| 10 | `ls evidence/drift-notes.md evidence/definition-of-done.md evidence/rollback.md` | All three exist |
| 11 | `git log --oneline feature/agent-base-v2 \| grep Phase\ E` | ≥1 commit |
| 12 | `git diff --stat 885a19d4f..HEAD -- agents/ daemon/ tests/ legacy/` | (Cross-check the mission) — show the byte-level footprint of A/B/C/D in their trees. **`tests/**` mutations are EXPECTED and ENUMERATED** — see the Phase C.3/C.4 test-edits list in the B2 fix-pass 2 reworded DoD #17 (Phase E §11): the no-mutation rule excludes the tests that Phase C must edit (C.3 git-mv of `tests/opencode/`; C.4 in-place: `test_tool_filter.py`, `test_jsonb_migration.py` full-file re-grep, `test_innate_skills_refactoring.py`, `test_coder_agent.py:40-43`, wanderer mirror `test_wanderer_agent.py:41-44`, 3 mock-strip files `tests/helpers/send_message_fixtures.py:68` / `tests/test_manager_shutdown.py:126` / `tests/integration/test_spawn_default_unchanged.py:163`, `tests/job_queue/conftest.py:65`). |
| cross-check | `git diff --stat 885a19d4f..HEAD` | Mission impact bounded to: `agents/<5 base>/*` (Phase B), `legacy/**` (Phase A), `daemon/**` (Phase C code sites), `docs/**` + `CHANGELOG.md` + `README.md` + `AGENTS.md` + `testing-guide.md` (Phase D), `.agents/shared/planning/agent-base-v2/**` (Phase E + sibling plans). **`tests/**` mutations are bounded to the Phase C.3/C.4 enumerated list** (see B2 DoD #17); no `frontend/**` mutation; no `release/**` mutation; no live system contact. |

## 7. Rollback

The evidence pack itself is additive — rollback of the Phase E commit is `git revert <sha>` or `git reset --hard <pre-E-sha>`. The mission rollback (Phase E does not own mission rollback; the mission plan does) is a fresh branch from `885a19d4f`. Phase E's evidence files are diagnostic outputs, not state mutations.

**Per-pair rollback grep-guard (iteration 002 carried note (a)):** when rolling back a single pair (e.g., `developer`), use a NAME-ALLOWLISTED grep-guard before any destructive operation to ensure the rollback does not accidentally touch the WRONG agent. The name-allowlist pattern:

```bash
# Name-allowlist grep-guard (per-pair rollback).
# Step 1: confirm the only opencode hits inside the target agent dir are
# the expected ones (pre-edit references + the v2 copy's content).
git grep -niE 'opencode' -- "agents/${name}/" "agents/${name}[v2]/" || true

# Step 2: confirm the rollback is the ONLY commit touching the target agent
# (no cross-agent leak from a sibling phase).
git log --oneline -- "agents/${name}/" | head -5

# Step 3: only then proceed with the destructive operation.
git checkout <pre-phase-sha> -- "agents/${name}/"
```

The name-allowlist ensures per-pair rollback does not leak to sibling agents. Without the guard, `git checkout <pre-phase-sha> -- "agents/${name}/"` is still safe (pathspec is explicit), but the guard catches operator error (e.g., a typo'd `agents/develper/` would silently succeed).

## 8. Risks & Mitigations

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Mixing `sha256sum` and `shasum -a 256` breaks the pre/post comparison | medium | Step 0 tool lock-in recorded in `evidence/TOOL.txt`. |
| R2 | Pre-mission manifests captured AFTER Phase B starts (lost reference state) | high | Step 1 mandates pre-mission capture BEFORE Phase B runs; if Phase B has already run, re-derive from `git show 885a19d4f:agents/<name>[v2]/…` (same recipe). |
| R3 | Live-system contact in Step 5 boot-scan or Step 6 GET | high (D8) | Step 5 uses the worktree-local `dev.sh` (fenced dev lane); Step 6 GETs only `http://localhost:8079/...` which is the dev lane. Never curl against `localhost:9797` (live) or `localhost:7979` (demo). |
| R4 | Live-path escape from a test wall fixture (critical note: 2 live-install deletions 2026-10-12) | high | Step 8 fences any helper scripts through `mktemp -d /tmp/ens-evidence-XXXXXX` FAKE_HOME + escape guard. No test fixture is permitted to `rm -rf $HOME/...` against a live install. |
| R5 | `default_agent_versions` GET shows a drift because of a Phase C wiring mistake | high | Step 6's byte-diff catches it; if the diff is non-empty, mission is closed. |
| R6 | Skill-seed double-seed surfaces as a row-count delta | medium | Step 7's per-agent side-effects table + row-count diff catches it; if either is non-empty, mission is closed. |
| R7 | Boot-scan log contains an opencode line from a library we depend on (not our service) | low | Step 5's `daemon-opencode-residue.txt` cross-references Phase C's expected residual. Benign library hits are documented; our-service hits are not. |
| R8 | Legacy archive mutated by a later phase (Phase B/C/D accidentally) | medium | Step 4 re-verifies Phase A's manifests against the archive as it sits at mission end; any mutation surfaces as a non-empty diff. |
| R9 | Test wall fails because a pin file was updated incorrectly in Phase B | medium | Phase B's pin update is the contract; if a pin fails, Phase B is wrong, not Phase E. The evidence log captures the failure for Phase B's remediation. |
| R10 | Per-agent pin count drifts (more or fewer than ~12) between plan-write and exec | low | Step 8 re-runs `grep -rln 'developer\[v2\] …' tests/` at exec time; uses whatever the grep returns. The ~12 figure is an estimate. |
| R11 | Cited line numbers (e.g. `daemon/services/skill_seed_service.py:285-292, 436`) drift between plan-write and exec | low | Step 7 mandates grep-verify at exec time. Plan cites ranges, not anchors. |
| R12 | `tests/opencode/` archive path differs from the recipe in Step 4 | low | Step 4 reads `phaseA-legacy-archive.md` for the canonical archive root; if Phase A's path differs, the recipe adapts. |
| R13 | Frontend pin `jobs.component.ts:2389` name is referenced by a test | low | Step 8's test wall runs and will report failures. Phase E does not edit the frontend. |
| R14 | The leader's exploration addendum (RESOLVED 2026-10-13) — closed in the current revision pass | n/a | O1 RESOLVED. The addendum landed before finalization; the parameterized sections (rows, JSON schema fields, sweep patterns) finalize as role-named rows of one move. No "pending addendum" markers remain. |
| R15 | A "must-fix leftover" hit in the residual-references audit indicates the retirement is incomplete | high | The audit is part of the whole-mission DoD (criterion #11). A must-fix hit blocks mission closure; the responsible phase (A, C, or D) re-runs and Phase E re-runs the audit. |
| R16 | The seven surfaces in the residual-references audit miss a real opencode footprint (e.g., a config file, a YAML, a SQL migration) | medium | The surfaces are intentionally broad: `*.py`, `*.md`, `*.sh`, `*.json`, `*.yaml`, `*.yml`, `*.ts`, `*.html`, plus `docs/plans/`, `docs/bugs/`, `.agents/`. The optional extension surface is the `data/` dir (runtime DBs — classified as intentional D7) and `legacy/` itself (the archive — classified as intentional). Surface 5 (MCP) is expected zero per addendum VERDICT 2. If a new surface surfaces during audit, the sweep pattern is extended additively. |
| R17 | Phase E's two new artifacts (before/after map + residual-references audit) become stale as the mission progresses (e.g., a must-fix leftover is remediated post-audit) | low | The map and audit are committed at mission end. If remediation happens post-commit, a follow-up commit re-asserts the audit and the map. The map's pre-path column always references `git show 885a19d4f:<path>` (HEAD before the mission), which is immutable. |
| R18 | Ordering constraint violated (Phase C opencode removal lands BEFORE Phase B base copy) | high | The commit graph on `feature/agent-base-v2` is verified (whole-mission DoD criterion #12): `git log --oneline` shows Phase B's commit precedes the opencode-stack move. The rollback section documents the correct reverse order (D → C → B → A). |
| R19 | Coder contamination guards FAIL after Phase B/C (coder is opencode-free BY DESIGN, but the test wall asserts the guards pass) | high | The test wall (Phase E step 8) runs `tests/unit/test_coder_agent.py` and asserts the contamination guards at :144, :168, :369+, :510, :253 PASS. If they fail, Phase B or Phase C is wrong, not Phase E. Whole-mission DoD criterion #13. |
| R20 | Addendum contradicts a row Phase E committed (e.g., a new live-used surface surfaces during executor verification) | low | Addendum landed and was incorporated. The rows in Step 4.5 / 9.5 / 9.6 are the addendum's source-of-truth. If executor verification surfaces a contradiction, the row's Verdict / Classification updates; no row is deleted. The `retained-live` terminal state is reserved for any such new exception. |
| R21 | **Phase E proof invalidated by inner_soul runtime writes to base `memory.md` (R8, architecture-recommendation.md §5 risk #14 + R8 amendment)** | medium | R8 capture-before-boot ordering rule (§3 of this plan): the base==v2 equality proof (Step 3: `diff -r` + per-agent manifests) MUST be captured BEFORE any boot proof (Step 5: boot-scan). Reason: `daemon/tools/inner_soul.py:1043, :1107-1176` writes to `agents/<agent>/memory.md` at RUNTIME. Post-B, base `memory.md` == v2 `memory.md`; any boot that instantiates a base agent (and runs `inner_soul`) mutates it → the `diff -r` equality proof breaks for a NON-architectural reason. The ordering rule in §3 enforces this; the verification note in Step 9.5 records the satisfaction. Cross-reference R10 §11.7: the FINAL TESTER GATE greps the COMMITTED tree (`git grep`), so runtime `memory.md` mutation cannot contaminate the gate either — the two checks are independent and BOTH stand. |
| R22 | **Active-tree `opencode` residue survives as "note" → FINAL TESTER GATE failure at promote review (R10 binding gate, architecture-recommendation.md §11)** | high | R10 §11 binding user directive: post-migration, ANY active-tree `opencode` reference is a DEFECT → FIX, not a note. The mandatory-fix set (R10 §11.2) is in Phase D's docs/refresh, Phase C's TOUCH list, and the broader rephrase set in §11.2. The FINAL TESTER GATE (Phase E Step 13) runs `git grep -niE 'opencode' -- . [excludes]` and asserts zero matches. The `evidence/r10-allowlist.md` file documents every "leave" row with one-sentence justification per R10 §11.3; rows without justification FAIL gate review. The gate's `evidence/r10-gate-output.txt` shows count=0 at mission end. |

## 9. Definition of Done (Phase E)

Checkable:

- [ ] Every step 0–13 produces its expected output.
- [ ] Every "EMPTY" verification in §6 passes (per-step verification row).
- [ ] `evidence/definition-of-done.md` and `evidence/rollback.md` are hand-authored.
- [ ] `evidence/drift-notes.md` is hand-authored with the three wrinkles (drift hazard, D3 dual-contract, ~12 maintenance pins).
- [ ] `evidence/before-after-map.md` is hand-authored (and the optional `.json` companion if desired). Map covers all three streams (i) agents/, (ii) comm service, (iii) session/API lane. (Stream (iii) rows may be partial pending the leader's addendum — see §12 O1.)
- [ ] `evidence/residual-references-audit.md` is hand-authored with the full classification table across the seven surfaces (N3 fix-pass 2: was "five"; corrected to "seven" per Step 9.6); every hit classified into the four buckets; zero unclassified; zero unaddressed must-fix leftovers.
- [ ] **R8 capture-before-boot ordering rule satisfied** (§3): Step 3's evidence file (`evidence/base-vs-v2-diffs/<name>.diff.txt`) was captured BEFORE Step 5's evidence file (`evidence/boot-scan/dev-lane-boot.log`). The R8 verification note in Step 9.5 records the satisfaction.
- [ ] **R10 allowlist file committed** at `evidence/r10-allowlist.md` with one row per excluded path + one-sentence justification per row (mandatory rows per R10 §11.3 + boundary cases R10 §11.4 #1–#4 + P4 stale-HISTORY rows + docs/bugs historical rows).
- [ ] **R10 FINAL TESTER GATE satisfied** (Step 13): `git grep -niE 'opencode' -- . [excludes]` returns 0 matches. `evidence/r10-gate-output.txt` is committed with count=0 and timestamp.
- [ ] Phase E commit(s) on `feature/agent-base-v2` (NOT pushed).
- [ ] `git diff --stat 885a19d4f..HEAD` shows a bounded footprint (no live-system contact, no `frontend/**` mutation, no `release/**` mutation). **`tests/**` mutations are bounded to the Phase C.3/C.4 enumerated test-edits list — NOT a defect** (the no-mutation rule excludes Phase C's mandated edits). The Phase C.3/C.4 test-edits list: **C.3 git-mv of `tests/opencode/` (entire 12-file tree to `legacy/tests/opencode/`); C.4 in-place edits: `tests/unit/tools/test_tool_filter.py` (R1 — rephrase 3 tests at :710-716, :750-764, :827-860 to use a surviving skill), `tests/migration/test_jsonb_migration.py` (R2 — full-file re-grep with the enumerated site list :75/:194-197/:223-226/:497/:518/:366-367/:106-107/:68-69), `tests/unit/test_innate_skills_refactoring.py` (P1 — enumerated 17+ sites :11/:14/:59-65/:85/:94/:98/:227/:234-236/:271/:275/:280/:297-301/:353), `tests/unit/test_coder_agent.py:40-43` (with the contamination guards at :144/:168/:369+/:510/:253 MUST KEEP passing), `tests/unit/test_wanderer_agent.py:41-44` (P2 — wanderer mirror, self-contained), 3 mock-strip files `tests/helpers/send_message_fixtures.py:68` / `tests/test_manager_shutdown.py:126` / `tests/integration/test_spawn_default_unchanged.py:163`, and `tests/job_queue/conftest.py:65` (P7 — docstring straggler). Any `tests/**` mutation BEYOND this list is a defect.
- [ ] Audit trail records: only the Step 5 daemon boot (fenced dev lane) + Step 6 GET (single read-only call against the same dev lane) ran against the runtime. No live/demo/ambient-env contact.
- [ ] `evidence/docs-consistency-sweep-final.md` shows zero unclassified `opencode` hits in `docs/`, `README.md`, `AGENTS.md` (covers the FULL STACK).
- [ ] **R9 test wall pin set correct**: `tests/test_innate_skills_refactoring.py:59-65,:227,:271,:299` are the REAL pins; `tests/conftest.py:443` is the HealthResponse fixture and is NOT edited (EXCLUSION WARNING — R9).

## 10. Cross-references (siblings — by filename only)

- `phaseA-legacy-archive.md` — Phase A archives the ENTIRE opencode stack (per addendum MOVE list: `daemon/opencode/` 8 modules, `daemon/tools/external_opencode.py`, `agents/_prompt_system/innate-skills/opencode/`, `tests/opencode/` 12 files, the 3 test packs, the 7 opencode-native-tools planning docs, the 2 opencode bug docs, `example/SKILL.md`); produces the pre-mission archive manifests that Phase E re-verifies in step 4.
- `phaseB-base-copy.md` — Phase B makes base == v2 content; produces the checksum manifests that Phase E re-derives and diffs in step 3. **Ordering constraint: opencode-stack removal lands WITH or AFTER Phase B, never before** (whole-mission DoD criterion #12 verifies this in the commit graph).
- `phaseC-opencode-excision.md` — Phase C excises opencode daemon wiring (TOUCH edits in place: `manager.py` opencode blocks, `tools/instance.py` opencode lines, `tools/_tool_registry.py` opencode registrations, `Makefile`, `pyproject.toml`, `.env.example`, comment rephrases, cosmetic one-liners, the relevant mocks and test files). Phase C's MOVE side: the opencode stack is archived to `legacy/`. Phase C's boot proof feeds Phase E's boot-scan.
- `phaseD-references-docs.md` — Phase D refreshes user-facing docs; produces the docs-consistency sweep table that Phase E re-applies as the post-mission baseline in step 9. The Phase D table covers the FULL STACK (comm role + session/API role + shared modules + InstanceManager-coordinated resources + agent-facing surfaces + addendum-identified surfaces); Phase E re-runs the union of the sweep patterns and asserts zero unclassified residuals in the docs surface.

## 11. Whole-mission Definition of Done (consolidated; for the report) [UPDATED 2026-10-13 + ADDENDUM + R10 GATE]

The mission is complete when ALL of these hold, all simultaneously:

1. v2 zero-touch (5 of 5).
2. base == v2 (5 of 5).
3. **legacy/ archive intact (FULL STACK)** — opencode-era base + the ENTIRE opencode stack archived (per addendum MOVE list).
4. Full test wall green (0 FAILED across pinned packs + unit suite).
5. Boot-scan clean (livez/readyz GREEN; zero opencode boot log; daemon residue matches Phase C's expected residual for the FULL STACK).
6. `default_agent_versions` byte-invariant (pre == post).
7. Skill-seed no-op for all five migrated agents.
8. Docs-consistency sweep clean (zero unclassified `opencode` hits in user-facing docs; covers the FULL STACK).
9. Drift/duplication posture recorded.
10. BEFORE/AFTER MAP committed at `evidence/before-after-map.md` (covers the FULL STACK with three terminal states; the `retained-live` section is EMPTY for this mission).
11. Final residual-references audit committed at `evidence/residual-references-audit.md` (zero unclassified hits; zero unaddressed must-fix leftovers; Surface 5 MCP hit count is 0).
12. **Ordering constraint verified** — commit graph shows Phase B's commit precedes the opencode-stack move (whole-mission DoD criterion #12).
13. **Coder opencode-free test wall invariant** — contamination guards at `tests/unit/test_coder_agent.py:144, :168, :369+, :510, :253` PASS (whole-mission DoD criterion #13).
14. **Ensemble unregressed** — the active codebase boots clean, the test wall is green, v2 is the live lane, and no live-system contact beyond the two fenced dev-lane interactions.
15. **R10 FINAL TESTER GATE** [ADDED 2026-10-13] — `git grep -niE 'opencode' -- . [excludes]` returns 0 matches; `evidence/r10-allowlist.md` is committed with one row per excluded path + one-sentence justification per row; mandatory-fix set (R10 §11.2) is applied; boundary cases (R10 §11.4 #1–#4) handled with per-file justification; v2 load-bearing check (R10 §11.5) PASSED; skills-surface sweep (R10 §11.6) clean (2 mandatory-fix hits at `_prompt_system/knowledge.md:96,:106`); R8 interplay (R10 §11.7) — gate greps committed tree, independent of R8 capture-before-boot; census (R10 §11.8) — every tracked-file bucket maps to a disposition, no orphan class. **Census drift acknowledgment (iteration 002 carried note (d)):** the `.agents` 306 count in architecture-recommendation §11.8 + phaseE Step 13 is a HEAD-time snapshot; the actual count drifts to 316 (and counting) as new tracking / result files are added. The gate is **command-based, not count-based**: pass criterion is `git grep` returns 0 matches given the (executable) exclude set, NOT a specific file-count delta. If the executor's dry-run shows a different count, that's expected; the exclude set governs, not the count.
16. Evidence pack committed at `.agents/shared/planning/agent-base-v2/evidence/` on `feature/agent-base-v2`.
17. `git diff --stat 885a19d4f..HEAD` footprint bounded as in §9 cross-check (no `frontend/**` mutation, no `release/**` mutation). **`tests/**` mutations are bounded to the Phase C.3/C.4 enumerated test-edits list** (see §9 DoD for the list: C.3 git-mv `tests/opencode/`; C.4 in-place `test_tool_filter.py` (R1), `test_jsonb_migration.py` (R2 full-file), `test_innate_skills_refactoring.py` (P1), `test_coder_agent.py:40-43`, `test_wanderer_agent.py:41-44` (P2), 3 mock-strip files, `tests/job_queue/conftest.py:65`). Any `tests/**` mutation BEYOND this list is a defect.
18. **N8 fix-pass 2 — migrations-append-only assert:** `git diff --stat 885a19d4f..HEAD -- daemon/migrations/` returns **empty output** (no new migration files added, no edits to existing migrations — the mission is migrations-free; D8's "no promote" rule is upheld by zero changes to the schema audit trail). Verification: `git diff --stat 885a19d4f..HEAD -- daemon/migrations/` should print zero lines. If non-empty, Phase C or another phase accidentally touched a migration file — investigate; this is a hard defect.

## 12. Open Items — RESOLVED (addendum landed 2026-10-13)

The leader's exploration addendum (authoritative classification + live-vs-dead verdicts) **landed 2026-10-13** and is incorporated into the current revision of this plan. The previously-parameterized sections (Step 4.5 rows, Step 9.5 rows, Step 9.6 surfaces, Step 11 DoD, §11 whole-mission DoD, §9 Phase E DoD, the Risks table) finalize as role-named rows of one move. **No "pending addendum" markers remain.**

### O1 — RESOLVED (per VERDICT 1)

The addendum's VERDICT 1 collapsed the prior "two lanes" framing into ONE retirement unit with TWO named roles inside a single package:
- **Session/API role** — the bulk of `daemon/opencode/` (single import graph: `external_opencode.py` → `server.py` → `registry/session_manager` → `client`; single SQLite store; single manager init at `daemon/manager.py:1427-1436`)
- **Communication role** — the protocol layer INSIDE the session lane (`server.py` action dispatch + question poll/relay; SOLE consumer `external_opencode.py:20-21`)

The `daemon/opencode/__init__.py:13` claim "used by the ensemble HTTP router" is STALE (zero router mounts across 30 routers + `daemon/api.py`). The package is a line-by-line port of the `opencode_skill` Go binary (`server.py:168+` ports `server.go:230-507`). Streams (i)/(ii)/(iii) in the prior plan collapse to ONE move with TWO named roles. Step 4.5 / 9.5 / 9.6 incorporate the resolution.

### O2 — RESOLVED

The `tests/opencode/` archive is a single uniform tree at `legacy/tests/opencode/`. The Step 9.5 row uses the uniform path; the manifest evidence file is `evidence/legacy-reverify/tests_opencode.manifest.txt`.

### O3 — moot

The addendum landed BEFORE finalization (this revision pass). The plan finalizes with the addendum incorporated; no follow-up delta commit is required for the addendum itself.

### O4 — RESOLVED

The `legacy/` archive root is confirmed. Step 4's re-verify uses `legacy/` as the root; Step 9.5's MOVE rows use `legacy/<mirror-path>` as the post-mission path.

### O5 — Ordering constraint (binding; record in evidence/rollback.md and the commit-graph check)

Stack removal is coupled to base-retirement via the innate-skill loader. While the 5 old-base agents declare `innate_skills: ["opencode"]`, both `agents/_prompt_system/innate-skills/opencode/` AND `daemon/tools/instance.py:155-167` (the innate-skill grant map) must still resolve. **Removal lands WITH or AFTER Phase B, never before.** Phase E's whole-mission DoD criterion #12 verifies the commit graph: `git log --oneline feature/agent-base-v2` shows Phase B's commit precedes the opencode-stack move. The rollback section in this plan (§11) documents the correct reverse order: reverse Phase D first (restore docs), then reverse Phase C (re-instate opencode stack), then reverse Phase B; reverse Phase A last or skip (the legacy/ archive is additive and safe to leave in place).

### O6 — Forward-compatibility: `retained-live` terminal state is expected EMPTY for this mission

The `retained-live, USER DECISION ITEM` terminal state is reserved in the before/after map schema for forward-compatibility. **For this mission it is expected EMPTY** (addendum VERDICT 2: no boundary-rule exception; full stack removal is safe). If at exec time the executor's verification contradicts the addendum (e.g., a new live caller surfaces), a new row is added to the `retained-live` section AND a USER DECISION ITEM is filed (the mission DoD criterion for the boundary-rule exception is then an OPEN USER DECISION, not "done"). Phase E's DoD assertion #10 records this expectation explicitly.