# Tier-1 Blast-Radius Gate — plane-pm-issue-tools (GATE C)

- **Lane worktree**: `/home/nea/ensemble-src-wt-plane-pm` (branch `feature/plane-pm-issue-tools`)
- **HEAD verified**: `b8d2ece53fcccd3de4b73c9176052c2ac664eb77` (subject: `test(plane-pm): task-3 e2e verification evidence + fc9 verdict`)
  - descends from `964778d48` ✓ (verified `git merge-base --is-ancestor`)
  - descends from BASE `941cb6321` ✓
- **BASE SHA**: `941cb63215f0fe0948f4d48f93821aec4c169527` (origin/latest at gate time)
- **Base worktree**: `/home/nea/ensemble-src-wt-plane-pm-base` (throwaway detached, `941cb6321`, CLEAN porcelain before/after)
- **Interpreter**: `/home/nea/ensemble-src/.venv/bin/python` (Python 3.13.15) with `cd` into the tree (worktrees carry no `.venv`); cwd `sys.path[0]=''` shadows the editable `.pth`
- **Date**: 2026-11-02
- **Recipe**: `.agents/tester/skills/blast-radius-leg/` (read per doctrine, never `skill_search`) + `.agents/tester/skills/failed-digest-equality-gate/`
- **Digest form**: **BARE-ID** — sorted node-id list, `FAILED `/`ERROR ` prefix stripped, one per line, trailing newline. Cross-gate comparisons valid within this form only.
- **Raw artifacts**: `.agents/tester/EVIDENCE/plane-pm-tier1-2026-11-02/` (committed)

## 0. Import proof (mandatory, both legs)

| Probe | Result |
|---|---|
| BASE cwd `/home/nea/ensemble-src-wt-plane-pm-base` | `/home/nea/ensemble-src-wt-plane-pm-base/daemon/__init__.py` ✓ (inside BASE tree) |
| TIP cwd `/home/nea/ensemble-src-wt-plane-pm` | `/home/nea/ensemble-src-wt-plane-pm/daemon/__init__.py` ✓ (inside TIP tree) |
| NEUTRAL cwd `/tmp` (shared venv `.pth` target) | `/home/nea/ensemble-src/daemon/__init__.py` (main checkout — disclosed: worktrees carry no `.venv`, so the shared main-checkout venv is used; cwd-shadowing selects the tree under test; the neutral probe proves the `.pth` resolves to a known tree and is not silently wrong) |

## 1. Fence self-test (mandatory)

Ambient env on this host is prod-poisoned (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, plus `SSL_CERT_FILE`/`SSL_CERT_DIR`/`POSTGRES_*` set). Per-variable `env -u` scrub (the `env -u POSTGRES_*` glob is a SILENT NO-OP — bash does not glob env names).

- (a) Assertion MECHANISM fires: `POSTGRES_DB=ensemble_prod` → `FENCE BREACH` rc=90; `POSTGRES_HOST=10.44.0.2` → rc=91; `PGPASSWORD=x` → rc=92; `DATABASE_URL=x` → rc=93.
- (b) Scrub neutralises ambient poison: `POSTGRES_DB=ensemble_prod POSTGRES_HOST=10.44.0.2 PGPASSWORD=poison ./run_leg.sh …` → `FENCE OK … POSTGRES_DB=<unset>` rc=0, test passes.

## 2. Mechanical derivation → declared set

Changed test files (`git diff --name-status 941cb6321..HEAD -- tests/**`): 18 added (D1 lane-owned) + 12 modified (D2/D3) + 0 deleted.

**D1 — lane-owned (HEAD-only, absent at BASE)** — must be 0 failed/0 error at HEAD; expected-by-construction at BASE, neither net-new nor fixed:

| File | Expected collected |
|---|---|
| `tests/unit/test_plane_workspace_resolver.py` | 79 |
| `tests/unit/test_plane_workspace_enforcement.py` | 35 |
| `tests/unit/test_plane_loud_skip_observability.py` | 33 |
| `tests/unit/test_plane_mcp_row_heal.py` | 6 |
| `tests/unit/test_plane_test_connection_report.py` | 19 |
| `tests/unit/test_plane_operator_route_gates.py` | 9 |
| `tests/unit/test_plane_bearer_echo_scrub.py` | 19 |
| `tests/unit/test_plane_tool_classifier_coverage.py` | (FC11) |
| `tests/unit/test_plane_sync_unavailable_reason.py` | (P2-L) |
| `tests/unit/test_plane_catalog_artifact_integrity.py` | (P11-related) |
| `tests/unit/test_plane_env_example_defaults.py` | (P30) |
| `tests/unit/test_plane_prompt_verb_check.py` | (P11) |
| `tests/unit/tools/test_plane_prefix_sweep_ownership.py` | (P1) |
| `tests/unit/tools/test_plane_agent_surface_census.py` | (FC7, A′-touched) |

**D2 — dual-side comparable (exist at BOTH revs)** — carries the FAILED-digest diff (19 files):

- BR-A 7 breaking files: `test_plane_sync.py`, `test_plane_sync_phase3.py`, `test_plane_sync_phase4.py`, `tests/integration/test_plane_sync_endpoint.py` (plan-authoritative; task3 evidence's BR-A list substituted `test_plane_tool_classifier_coverage.py`, which is D1), `test_mcp_quote_sanitization.py`, `test_mcp_service.py`, `test_plane_identifier.py`
- A′-touched: `tests/test_chart_image_delivery_e2e.py` (FC8 + gate-(i) drift detector)
- Phase-3 amend: `tests/unit/tools/test_dynamic_toolset_expansion.py`, `tests/unit/tools/test_mcp_quarantine_registry.py`
- Modified: `tests/unit/test_mcp_builtin_availability_gate.py`, `tests/unit/test_plane_mcp.py`
- Adjacent packs: `tests/api/test_plane_settings.py` (plane_settings), `tests/unit/test_plane_domain_access.py` (plane_domain_access), `tests/unit/test_project_manager_agent.py` (project_manager_agent)
- **D3 — 4 routed pre-existing packs (configure-builtin stub)**: `tests/unit/test_configure_builtin_route_readback.py`, `tests/unit/test_configure_builtin_idempotency.py`, `tests/unit/test_builtin_mcp_servers.py`, `tests/unit/test_context7_builtin.py` (attribution-only; NOT fixed by this lane)

**Exclusions (pre-exclusion registry, both legs)**: `tests/performance/test_message_api_cost.py` (>300s solo), e2e cells needing a live `:8079` (none in this set), `tests/packs/g7` (sys.exit(0) trap, never collected). None of these are in the declared set.

**BR-A #4 note**: `tests/integration/test_plane_sync_endpoint.py` carries `pytestmark = pytest.mark.integration`; the repo's global `addopts = -m 'not integration and not postgres and not slow and not conformance'` deselects all 11 nodes on BOTH legs (740 = 729 collected + 11 deselected). Symmetric → digest unaffected; supplementary run with `-m integration` = 7/7 on BOTH legs (see §5).

## 3. Slicing proof (`--collect-only -q`)

| Leg | Files | Collected | Deselected |
|---|---|---|---|
| BASE, D2+D3 (19 files) | 19 | **729** | 11 |
| HEAD, D2+D3 (19 files) | 19 | **730** | 11 |
| HEAD, D1 (14 files) | 14 | **277** | 0 |

`executed == declared` on both legs (19 + 14 = 33 declared test files; 729+277 = 1006 nodes at HEAD). Per-file attribution of the +1 collected-node delta: `tests/unit/test_mcp_quote_sanitization.py` 38 → 39 (the BR-A pin re-point, P2-C). All other 18 files: 0 delta. Deselected count is identical on both legs (11).

D1 (lane-owned) at HEAD: 277 collected, 0 failed, 0 error — lane-owned pins all green.

## 4. Dual-side run (BASE first, then HEAD, never concurrently)

Fence: per-variable `env -u` scrub + in-child assertion (verified). Command shape: `timeout 300 python -m pytest <files> -q --tb=short -rfE -p no:randomly --timeout=120`. Chunked 13 files/chunk.

| Leg | Wall | Result |
|---|---|---|
| BASE (D2+D3) | 72s | 18 FAILED + 21 ERROR (39 reds) |
| HEAD (D2+D3, pre-fix) | 72s | 21 FAILED + 21 ERROR (42 reds) |
| HEAD (D2+D3, post-fix) | 74s | 19 FAILED + 21 ERROR (40 reds) |
| HEAD (D1) | 18s | 277 passed, 0 failed, 0 error |

## 5. Digest comparison (BARE-ID form)

| | sha256 | nodes |
|---|---|---|
| BASE digest | `65dca363616c6bfda354e9fc6cbca86507db4df6a2980b0ccc04dd1f33f4b828` | 39 |
| HEAD digest (post-fix) | `c40f153f6493e3060402a8e7ffc121a1155ed7c1bc6de087ec30f64f964074eb` | 40 |

- **NET-NEW (head-only)**: **1 node** (see §6.1)
- **FIXED (base-only)**: **0**
- **PRE-EXISTING (intersection)**: **39 nodes** (byte-identical on both legs; stable across 3× re-runs on BOTH legs — see §7)

Supplementary (BR-A #4, `-m integration`): 7/7 on both legs, identical → no net-new.

## 6. NET-NEW adjudication

### 6.1 Remaining NET-NEW (1 node) — GENUINE violation, NOT fixed by this gate (production-side fix required)

```
tests/unit/test_project_manager_agent.py::TestConventionCompliance::test_no_forbidden_system_tokens[tools_note.md]
```

- **Cause**: A′ commit `262dff12c` (A'.2) rewrote `agents/project-manager/tools_note.md` and introduced the daemon-internal tokens `meta.json` and `tools.allow` (`'meta.json'@L24`, `'tools.allow'@L23`, `'tools.allow'@L37`). The FORBIDDEN_TOKENS house convention (project-manager agent only) bans these; PM was clean at BASE (`941cb6321`).
- **Classification**: genuine violation of a standing house convention by the lane's own charter prose — NOT a stale test expectation. The correct fix is a PRODUCTION-side reword of two phrases in `agents/project-manager/tools_note.md` (e.g. "literal tool names, not the whole `plane` category" instead of "literal tool names in `tools.allow`"). I did NOT edit production code, per the gate's hard constraints.
- **Routed to**: lane owner / architect. The agent prompt must not name the `meta.json`/`tools.allow` schema keys (Cardinal hygiene). This is a 2-line reword available to the lane owner; this gate does not block on it but records it as a finding.
- **Stability**: reproducible 3/3 on HEAD (netnew r1/r2/r3 = 3 FAILED each, identical node set).

### 6.2 Fixed net-new (2 nodes) — stale expectations caused by the lane's own charter/A′ changes (test-side, <20 lines each)

**Fix A — `test_all_allow_entries_resolve`** (file `tests/unit/test_project_manager_agent.py`, 14 lines added)
- **Cause**: A′.1 replaced the `"plane"` category with 28 literal `plane_*` verb names in `agents/project-manager/meta.json`. The test resolves allow entries against `_force_register_all_tool_modules()`; the plane MCP server is not registered in the test env, so the 28 names don't resolve.
- **Fix**: accept the keep-set daemon names (`gate-i-keep-set.json::keep_set_daemon_names`, 28 names) as resolvable in addition to the registered tool names. Typo detection for all other names is preserved.
- **Fingerprint**: +14 lines / 0 deleted in `tests/unit/test_project_manager_agent.py`.
- **Verified**: node now passes on HEAD (501 passed, was 500); absent from the post-fix digest (0 hits).

**Fix B — `test_quarantine_skips_plane_too`** (file `tests/unit/tools/test_mcp_quarantine_registry.py`, 5 lines added)
- **Cause**: E3 (plane-pm) added the workspace-resolver gate to `PlaneServerDefinition.is_available()`. In the (non-live, unset-slug) test env the resolution is REFUSED → `is_available()` is False for the wrong reason, so the pre-quarantine `is True` assertion fails.
- **Fix**: mirror the plane lane's own availability cases — set `ENSEMBLE_SELF_ENV=dev`, `PLANE_MCP_WORKSPACE_SLUG=e2e`, `POSTGRES_DB=ensemble_dev`, `POSTGRES_HOST=localhost`, and call `reset_resolution_cache()`. The quarantine-then-unavailable assertion is unchanged.
- **Fingerprint**: +5 lines / 0 deleted in `tests/unit/tools/test_mcp_quarantine_registry.py`.
- **Verified**: node now passes on HEAD (189 passed, was 188); absent from the post-fix digest (0 hits).

**Re-run verification**: HEAD leg re-run post-fix → 19 FAILED + 21 ERROR (was 21 + 21) and D1 277 passed; both fixed nodes absent from the digest. Base leg unchanged (the fixed nodes passed at BASE originally, and no base-tree mutation was performed).

## 7. Flaky adjudication (3× isolated, sequential, no code change, no xdist)

- NET-NEW (3 nodes) × 3 on HEAD: 3/3 FAILED each, identical node set across r1/r2/r3 → STABLE reds (not flakes).
- Pre-existing (39 nodes) × 3 on BASE: 18 FAILED + 21 ERROR each run, identical set across r1/r2/r3 → STABLE.
- Pre-existing (39 nodes) × 3 on HEAD: identical to BASE per-run set (cmp byte-identical, `sha256 65dca363…`).
- **No flaky bucket.** `netnew_pre_fix.txt` (sha `59530b79…`) records the pre-fix 3-node set for audit.

## 8. Routed pre-existing packs (configure-builtin stub) — attributed, NOT fixed

The 4 packs appear red on both legs (21 errors on each leg, identical). They are pre-existing on the BASE leg (base-leg proof: the errors are identical in count and form on both legs) and are a separate commission's concern (configure-builtin route). I did not edit them.

- `tests/unit/test_configure_builtin_route_readback.py` (7 nodes)
- `tests/unit/test_configure_builtin_idempotency.py` (17 nodes)
- `tests/unit/test_builtin_mcp_servers.py` (88 nodes)
- `tests/unit/test_context7_builtin.py` (25 nodes)

## 9. PACKS.md registration (this gate owns it)

Registered the 7 Phase-2/3 runtime packs in `.agents/tester/PACKS.md` (new section, newest-first, keep-both — no existing row deleted). Rows: `plane_workspace_resolver_unit_test`, `plane_workspace_enforcement_unit_test`, `plane_loud_skip_observability_unit_test`, `plane_mcp_row_heal_unit_test`, `plane_test_connection_report_unit_test`, `plane_operator_route_gates_unit_test`, `plane_bearer_echo_scrub_unit_test`. Each row: pack name | invocation (exact command incl. fences + timeout) | scope | last run | status. Plus a supplementary note for `test_plane_sync_endpoint.py` (integration marker).

## 10. Budget

- Setup (worktree create + import proof + fence self-test + collect): ~8 min
- Legs: 72s + 72s + 74s + 18s ≈ 4.6 min
- Flaky probes: 3:44
- Total window ≈ 17 min — within the 30-min Tier-1 HARD cap. No re-scope needed.

## 11. Fences verified

- Main checkout `/home/nea/ensemble-src` porcelain sha256 before = `92c853fc…` (recorded); worktree metadata added only (the new worktree is tracked in the main repo's `.git/worktrees/`, which is gitignored state, not working-tree porcelain). The foreign modification `.agents/shared/planning/designer-agent/install-audit.jsonl` (mtime 2026-11-02 19:11:58Z, another lane's file) was left UNTOUCHED — never staged, never `git checkout --`'d, never `git clean`'d.
- No daemon boots, no DB connections (suites are SQLite/mock), no processes killed, ports 9797/7979/8079 untouched, `ensemble_prod` never addressed (fence held in every leg — `POSTGRES_DB=<unset>` in every log).
- No merge / push / stage / promote. Branch-side commits only (2 commits: A′ `262dff12c` already present + evidence commit below).
- `.agents/tester/rules/` not touched.
- Teardown: base worktree `/home/nea/ensemble-src-wt-plane-pm-base` removed via `git worktree remove --force` + `git worktree prune` (verified `git worktree list | grep plane-pm-base` → 0).

## 12. RESULT

**PASS-WITH-ONE-ROUTED-FINDING.**

- NET-NEW (post-fix, head-only) = **1** — the `tools_note.md` forbidden-token genuine violation, fully attributed and routed to the lane owner (production-side 2-line reword). It is a real convention breach introduced by A′, not a stale expectation; I did not fix it (hard constraint: no production-code changes).
- Two stale-expectation net-new reds (A′.1 allow-resolution; E3 quarantine gate) were quick-fixed (<20 lines each) on the test side, re-run on both legs, and are now green; fingerprints recorded (+14 / +5 lines).
- 39 pre-existing reds (21 configure-builtin-stub errors + 18 failures) are byte-identical on both legs and stable across 3× re-runs; attributed, not fixed.
- D1 lane-owned (14 files, 277 nodes) all green at HEAD.
- BR-A #4 integration file: 7/7 on both legs (supplementary, `-m integration`).
- Slicing proof: 729/730 (D2+D3) + 277 (D1) = 1006 nodes at HEAD; 729 at BASE; executed == declared.

## 13. Commit

`test(plane-pm): tier-1 blast-radius gate evidence + pack registration` — evidence file + raw artifacts + PACKS.md rows (one commit). SHA recorded in the report-back.

---

## 14. Fix cycle — `test_no_forbidden_system_tokens[tools_note.md]` (GATE C continuation)

**Root cause**: A′ commit `262dff12c` (A′.2) rewrote `agents/project-manager/tools_note.md` and introduced the daemon-internal tokens `meta.json` and `tools.allow` (3 occurrences: `'meta.json'@L24`, `'tools.allow'@L23`, `'tools.allow'@L37`). The `FORBIDDEN_TOKENS` house convention (project-manager agent only) bans these; PM was clean at BASE (`941cb6321`). Genuine convention breach, not a stale expectation.

**Fix** (production-side prose reword, <20 lines, no daemon behavior change): `agents/project-manager/tools_note.md`, 3 insertions / 3 deletions:
- L23: `(literal tool names in \`tools.allow\`, NOT the whole \`plane\` category)` → `(the literal verb names I am allowed to call, NOT the whole \`plane\` category)`
- L24: `agents/worker/meta.json`'s \`plane_sync\` allow entry` → `agents/worker`'s \`plane_sync\` allow entry`
- L37: `My \`tools.allow\` names these 28 verbs **literally** (not the \`plane\` category), so a verb outside the keep-set is not bound into my prompt at all — the grant is the vocabulary.` → `I am granted these 28 verbs **literally** (not the whole \`plane\` category), so a verb outside the keep-set is never bound into my vocabulary at all — the grant is the vocabulary.`

**Preserved (verified)**: all 28 keep-set `plane_*` verb names still present in `tools_note.md` (0 missing); `plane_sync_project` ownership statement intact; no new forbidden tokens of any class (`meta.json`, `tools.allow`, `tools.deny`, `daemon/`, `_tool_registry`, `skill-set.yaml`, `agent_id=`, `seed_all`, `innate_skills`, `default_agent_versions` all absent post-fix).

**Regenerated baseline**: `python3 scripts/protection/generate_prompt_artifact_baseline.py` → `PROMPT-BASELINE: GENERATED — 161 artifact(s)`; `--check` → `PROMPT-BASELINE: OK — 161 versioned artifact(s) match` (rc=0). `agents/.prompt-artifacts.sha256` updated (+1 line in the diff).

**Re-verification**:
- `test_no_forbidden_system_tokens[tools_note.md]`: 0 failed → **1 passed** (was 1 failed) ✓
- Full `tests/unit/test_project_manager_agent.py`: 63 passed, 2 failed — the 2 remaining are the pre-existing `TestPromptComposition` reds (`test_soul_cites_rule_for_severity_framing`, `test_workflow_cites_soul_and_rule`), byte-identical at BASE (pre-existing, attributed, not fixed).
- `tests/unit/test_plane_prompt_verb_check.py` (P11, scans the 4 charter files incl. tools_note.md): **9/9 PASS** — reword did not break verb membership.
- Seal tripwire `tests/test_prompt_artifact_baseline.py`: **19/19 PASS** (regenerated baseline correct).
- Seal tripwire `tests/test_chart_image_delivery_e2e.py`: 1 failed (pre-existing — `TestPhaseDDoesNotModifySealedArtifacts`, 4 sealed artifacts `agents/ari/workflow.md`, `daemon/sources/registry.py`, `daemon/constants.py`, `agents/ari/rule.md` differ; attributed to other lanes, NOT introduced by this edit — `tools_note.md` is not in the sealed set).

**Post-fix net-new verdict: 0.** The `[tools_note.md]` param is green; the only remaining reds in the pack scope are the 2 pre-existing `TestPromptComposition` reds (attributed, not fixed). Combined with the earlier fix cycle (2 test-side fixes), the full net-new set is now empty.

**Fingerprint**: tools_note.md 3 insertions / 3 deletions (6 lines); baseline file 1 line changed. No daemon production-code behavior change. No other files modified.
