# Final Gate Report — designer-critic-orchestration release vehicle

**Date:** 2026-10-10 (UTC)
**Branch:** `feature/designer-critic-orchestration` @ `35d7e9c5d3422254de039426b41d3528946b8ca6` (8 commits atop base `0c040e5f8` = v0.18.5 bump)
**Worktree:** `/home/nea/ensemble-src-wt-designer-critic-orchestration`
**Interpreter:** `/home/nea/ensemble-src/.venv/bin/python3` (worktree has no venv; `import daemon` verified resolving to the worktree)
**Role:** Final pre-merge verification gate — full re-execution of the plan's verification surface; no prior-lane outputs trusted.
**Workers:** wt-dco-nit2 (`ffe65a92`), wt-dco-ri (`16de66cd`), wt-dco-full (`58517235`), wt-dco-gates (`eaf1ef8c`), wt-dco-regit (`6ea12d17`), wt-dco-conv (`6094b170`) — 6 parallel, 1 reuse follow-up, 0 re-dispatches.

---

## VERDICT: ✅ VERIFIED — ready for merge, with one 🟠 important pre-merge adjudication item (§ Findings N1/N2)

The commissioned verification surface is fully green. One branch-caused collateral finding exists OUTSIDE the commissioned surface (2 stale test assertions in a third test file the plan fenced behind hard-escalation) — leader must adjudicate before executing the merge (recommended actions in § Action Needed). Nothing in the commissioned battery blocks.

---

## Battery 1 — verification-surface.md §1 (Nit-2 pytest + supplemental)

**Nit-2 invocations (worker wt-dco-nit2, ~12.9s total, all `timeout 300`-wrapped, pytest-timeout 30s inner):**

| # | -k pattern | Summary (verbatim) | Exit | Verdict |
|---|---|---|---|---|
| 1 | `critic_meta` | `1 passed, 76 deselected in 0.40s` | 0 | PASS |
| 2 | `critic_tools_resolve` (WRITE-LEAK GATE) | `1 passed, 76 deselected in 0.72s` | 0 | PASS |
| 3 | `critic_deny_wins` | `1 passed, 76 deselected in 0.77s` | 0 | PASS |
| 4 | `critic_team_implied` | `1 passed, 76 deselected in 0.60s` | 0 | PASS |
| 5 | `designer_od_generate_removed` | `1 passed, 76 deselected in 0.45s` | 0 | PASS |
| 6 | `parity_runs_v2_schema` | `1 passed, 76 deselected in 0.41s` | 0 | PASS |
| 7 | `agent_registry_scan` | `1 passed, 76 deselected in 4.76s` | 0 | PASS |
| 8 | Canonical batch (all 7) | `7 passed, 70 deselected in 4.78s` | 0 | PASS (exactly 7) |

**Supplemental report-integrity (worker wt-dco-ri, ~9s):**

| -k | Summary | Exit | Verdict |
|---|---|---|---|
| `critic` | `1 skipped, 70 deselected in 0.29s` — skip reason verbatim: `'critic' has no team_members — not a parent agent` | 0 | PASS (SC-10; leaf skip = expected green) |
| `designer` | `1 passed, 70 deselected in 0.30s` | 0 | PASS |

Expected stderr noise (`Agent 'maintenancer': deny entry 'git_commit'…`) observed on registry probes and correctly not flagged. No `exists('critic')` AssertionError anywhere — the plan's "fails until phase 2" note is stale, critic registered.

## Battery 2 — Registry boot discovery (worker wt-dco-regit)

- SC-9 probe: `get_registry().exists('critic')` → **exit 0** (PASS). Sketcher positive control → exit 0 (PASS).
- Anchor cites verified **zero drift**: `daemon/registry.py:1191` (`def exists`) / `:1207` (`def get_registry`).
- Designer registry entry (via `get_registry().get('designer')`, `registry.py:742`): `team_members = ["worker", "sketcher", "critic"]` verbatim — matches leader expectation exactly. Exhaustive `od\.[a-z_]+` scan over the entire entry dump: **NONE**.
- Compensating note: full daemon sandbox boot-scan SKIPPED — see § Unexecutable.

## Battery 3 — Full both-file pytest + neighbors (worker wt-dco-full)

| Invocation | Result | Exit | Verdict |
|---|---|---|---|
| `tests/unit/agents/test_sketcher_agent.py` | 52 passed / 0 failed | 0 | PASS (see ±1 drift note) |
| `tests/unit/plugin_subsystem/test_designer_rewire.py` | 25 passed / 0 failed | 0 | **PASS — SC-12** (25 ≥ 24) |
| `tests/unit/probe_designer_od_lane_binding.py` (direct script run) | `PASS T1…PASS T2…PASS T3…PASS T4…RESULT: PASS (all lanes preload)` | 0 | PASS — coupling-map claim satisfied (T1 = the fails@base discriminator) |
| `tests/unit/agents/` (neighbor dir) | 52 passed / 0 failed (contains only the sketcher file) | 0 | PASS |
| `tests/unit/plugin_subsystem/` (neighbor dir) | 580 passed / 6 failed / 2 errors | 1 | FAIL — decomposition below |

Drift note: expected 53+24; actual 52+25 — sum 77 matches the commissioned total exactly; redistribution across files, not loss.
Drift note 2: the probe file is a stdlib `__main__` script, NOT pytest-collectible (pytest exit 5, 0 collected) — executed directly per its docstring; this is the coupling-map evidence form.

Neighbor-dir failure decomposition (8 non-passing):
- **P1–P4 = pre-existing, base-proven** — exact match to deferred-debt (b)'s documented 4 plugin_subsystem families (hash-count literal `test_snapshot_class_byte_fidelity`, policy no_change + MANIFEST dup ×2 in `test_sync_runner`). Out of scope; correctly not counted against the gate.
- **N1/N2 = NEW, branch-caused** — see § Findings.
- **N3/N4 = environmental, NOT branch-caused** — `test_sync_runner.py:1663` hardcodes `.venv/bin/python` in a subprocess; worktree has no `.venv`. File absent from the branch's 58-file change inventory → identical failure on base in this worktree.

## Battery 4 — Critic tool resolution (write-leak gate)

- Pytest: `critic_tools_resolve` + `critic_deny_wins` PASS (Battery 1).
- Standalone probe (worker wt-dco-regit, same seam as the test: `create_image_tools` + `create_compare_tools` → `scan_tools_for_full_docs` → `resolve_tool_filter`):
  ```
  RESOLVED_SET: ['compare_images', 'explain_image', 'image_get', 'image_list', 'read_file']
  EQUALS_EXPECTED: True
  image_save ABSENT: True / write_file ABSENT: True / edit_file ABSENT: True
  ```
  **PASS** — exact 5-tool enumeration; the `image` category alone would resolve `image_save` in; the explicit deny strips it (deny-wins). Critic meta deny list (9): `bash, proc, instance, service, midflight, shared_meta_kv, infra, mcp, image_save`.

## Battery 5 — Convention compliance spot-audit (worker wt-dco-conv: 12 PASS / 0 FAIL / 4 NOTE)

- Cardinal/guideline split: critic rule.md = 7 numbered Cardinals + Guidelines (a)–(d) — cap respected.
- One canonical home: verdict schema canonical home = `agents/critic/workflow.md:21` `## Review` ("CANONICAL HOME OF THE VERDICT SCHEMA…quotes the planning-dir doc verbatim (by section…)"); schema doc self-declares FROZEN PIN; rule.md binds by section name, no filename tokens.
- System-internals closure greps (`daemon/|_tool_registry|\.py:` and the broader internals set + cross-reference `\.md` set): **0 hits** across all critic prose.
- `skill_injection: true` (R15 ✓); `llm_model: "vision"` (R6 ✓); meta.json 18-line inventory sane.
- tools_note.md documents declared-vs-effective team (image-comparator auto-extension) + D6 deny rationales for `mcp` and `image_save`.
- NOTE: no YAML frontmatter on critic prompt files — consistent with v2 norm (guide §6 scopes frontmatter to skill files; designer files also lack it).
- Static gates worker (wt-dco-gates) independently: SC-7 critic soul closure-grep 0 hits with designer-soul positive control also 0 (pattern not false-green).

## Battery 6 — Reviewer-carried runtime items

- **(a) pinned_spec_sha seam: PASS.** `daemon/tools/compare_tools.py:1224` (parameter), docstring `:1265-1271` (cite :1265-1269 anchor correct, cosmetic +2-line extension), use site `:1323-1326`, validation `:1105-1107`, D6 hard-rule wire declared `:1063`/`:1288`. Field token `pinned_spec_sha` matches EXACTLY across `agents/critic/rule.md:9`, `workflow.md:41/:46`, `critic-verdict-schema.md:23/:40/:42` (6 references, zero divergence).
- **(b) KV round_count re-bind: PASS (static).** `agents/designer/rule.md:63-66` Guideline (e) "KV Round-Counter — revival-safe iteration budget" carries all three mandated behaviors verbatim: pin `round_count` per page in `shared_meta_kv` at first sketcher dispatch; record `(critic_instance_id, verdict_sha)` per iteration; on revival re-bind from KV — NEVER reset. Encoded as Guideline (e) (not a numbered Cardinal), matching plan cap-7 form; designer rule.md = 7 Cardinals + Guidelines (a)–(h). **Runtime KV revival proof deferred to promote-time smoke** (out of static scope by design).
- `_auth.py` anchors (semantics correct, cite drift): `TOOL_REQUIRED_AGENTS` actual `:35-51` with `design → ["image-comparator"]` at `:50` (cited :38-50); auto-extension loop actual `:153-158` (cited :155-161). Both NOTEs, no action.

## Battery 7 — Zero-regression posture (worker wt-dco-regit)

- `git status --porcelain` → ONLY ` M .agents/approver/active.md` (known lane residue). PASS.
- HEAD `35d7e9c5d…` ✓; `git rev-list --count 0c040e5f8..HEAD` = **8** ✓; base present (v0.18.5 bump) ✓.
- **Unpushed** ✓ (no upstream tracking ref; `git ls-remote origin feature/designer-critic-orchestration` empty).
- SC-13: **PASS with NOTE** — 4 phase commits identifiable: `a29ae3391` P1 / `7530712b4` P2 / `dd949c4c2` P3 / `ff7e288bd` P4-by-content (carries phase4-ac-battery.txt + phase4-grep-results.txt + both test-file updates); + 3 review-cycle (`6b9eec0e6`, `36ecf5473`, `35d7e9c5d`) + 1 docs (`8867b9fa8`) = 8. NOTE: only 3 carry explicit phase-N labels; P4 landed under a "planning-reconcile" message.
- SC-14: **PASS** — main-checkout `sha1sum /home/nea/ensemble-src/agents/designer/meta.json` = `06b8bf641911ebd1abb5f734b5ea872e9617eb2b` (exact); change committed on branch (`0c040e5f8..HEAD --stat` shows `3 +--`); working tree vs HEAD for that path empty.
- SC-16: **PASS** — `_tool_registry.py` diff empty; **zero daemon/ files changed anywhere on the branch**.
- Changed-file inventory: 58 files = `.agents/` 41 (38 A planning + 1 A approver-tracking + 2 M od-generate-agent-lane) + `agents/` 15 (critic 5 A, designer 7 M, sketcher 3 M) + `tests/` 2 (the commissioned test files). No daemon/, scripts/, frontend/, pyproject.toml, uv.lock.

## Static SC gates summary (worker wt-dco-gates: 26 PASS / 2 strict-literal)

SC-1 ✓0 · SC-2 ✓index 2 · SC-3 ✓0/0 · SC-4 ✓0 · SC-5 ✓dual-form (literal-pipe 0; alternation 3 lines, all five enum tokens verbatim at rule.md:15/:35/:38) · SC-6 ✓exit 0 · SC-7 ✓0+control · SC-8 ✓7 Cardinals; **SC-8b → PASS with NOTE** (4 amendment contents verbatim — 4 token occurrences concentrated on 2 lines (rule.md:9 dense Cardinal-#1 bullet + :40); plan's "≥4 hits (one per amendment)" assumed one-line-per-amendment authorship; content complete, metric shape missed; corroborated by wt-dco-conv's independent :9 quote) · SC-9 ✓(Battery 2) · SC-10 ✓(Battery 1) · SC-11 ✓7 hits all inside Orchestration section (103–~137) · SC-12 ✓25 passed (Battery 3) · SC-13 ✓+NOTE (above) · SC-14 ✓ · SC-15 ✓design idx 2 / view-views null · SC-16 ✓ · SC-17 ✓SUPERSEDED marker + 112 lines · SC-18 ✓0/0 · SC-19 ✓3 hits + 46 lines (≤60) · SC-20 ✓7 Cardinals + all 3 controls (rule.md:65/:70/:72/:74) · SC-21 ✓10 hits.
Manual §4: file existence ✓ (5 files, no skill-set.yaml) · line counts ✓ 86/79/124 · **Manual-3 lockstep → PASS with NOTE** (workflow.md:1 hit = `od.generate` inside an explicit NEGATION clause "There is no direct `od.generate` lane from designer to OD" at :105 — reinforces the kill; commissioned pytest `designer_od_generate_removed` (scope meta/soul/rule/tools_note/skill) PASSES, confirming the negation is outside every asserted zero-tolerance set) · cardinality ✓7.

## Unexecutable (with reasons)

1. **Sandbox daemon boot-scan** (optional, best-effort): SKIPPED — the mandated `ensemble_sandbox` DB does not exist on the only reachable PG server (10.44.0.2 hosts only `ensemble_demo`/`ensemble_prod`); ambient shell env leaks live-prod `POSTGRES_*`; provisioning the DB would require a write on the production PG server, outside a read-only final gate. **Compensating evidence:** SC-9 subprocess probes + `agent_registry_scan` + resolved-set probe all exercise the identical `get_registry()` discovery path a boot would. Needs (if wanted at promote time): sandbox DB provisioning or a host with a local PG.
2. **Runtime KV revival re-bind proof**: out of static scope by task design → promote-time smoke item (static Cardinal/Guideline text fully verified).

## Findings

**🟠 N1/N2 — branch-caused stale-architecture tests (the one adjudication item):**
`tests/unit/plugin_subsystem/test_tier1_wiring.py::test_designer_shaped_allow_resolves_all_four` (:215) and `::test_designer_allow_includes_all_four` (:337) still assert designer `tools.allow` binds all 4 `od.*` Port tools — the exact architecture this commission killed (phase-1 `a29ae3391`). Tests are provably stale (premise removed by user directive), but they leave 2 red tests caused-by-branch. The plan's phase-4 owned-test-task fenced edits to the two commissioned files with "hard-escalates non-workflow edits elsewhere" — no escalation or deferred-debt entry exists for this file. Workers correctly declined to quick-fix at the final gate.

**⚪ N3/N4 — environmental errors:** `test_sync_runner.py:1663` hardcoded `.venv/bin/python` subprocess path; worktree has no `.venv`. Not branch-caused (file absent from branch inventory; identical on base in this worktree). Candidate for a future interpreter-resolution hardening pass.

**⚪ P1–P4 — pre-existing, base-proven:** exact match to deferred-debt (b) (4 plugin_subsystem families). No action on this branch.

**NOTEs (no action / awareness):** SC-8b metric shape; Manual-3 negation clause (corroborated green); SC-13 phase-4 labeling; `_auth.py` cite drifts (actual `:35-51`/`:50` and `:153-158`); critic prompt files carry no YAML frontmatter (consistent with v2 norm); `probe*_stderr.txt` transient scratch (sibling worker wt-dco-regit, self-cleaned, git re-verified clean).

## ensure.md Validation

- **Core Critical #1** (no regressions in changed packs): **PASS** — all packs in the change set green (both commissioned test files, report-integrity, lane-binding probe).
- **Core Critical #2/#3** (concurrency/async-loop packs): **OUT OF BLAST RADIUS — justified**: zero daemon/ changes on the branch (58-file inventory), agents-tree prompts + test files only.
- **Core Critical #4** (dev.sh `--timeout-graceful-shutdown 10`): **PASS** (dev.sh:142).
- **Important** (async-caller awaits, deadlock scenario): out of scope — no async conversions in the change set.
- **Release Gate: NOT TRIGGERED** — blast radius = agents-tree prompts + 2 test files + planning docs; no cross-module daemon refactor; promote explicitly out of scope (plan constraint C3; verification surface §5: every gate unit-level, zero live calls required).
- No contradictions between ensure.md and execution (all validations ran as scoped ad-hoc packs with dual-layer timeouts; no bare pytest, no `-x`).

## Scope Decision

Full commissioned verification surface executed (no reduction) — this WAS the commissioned battery. Best-effort additions beyond it: neighbor dirs (`tests/unit/agents/`, `tests/unit/plugin_subsystem/`), the OD lane-binding probe script, standalone resolved-tool-set probe, source-anchor verification — per the leader's brief.

## Action Needed (leader adjudication)

- [ ] 🟠 **N1/N2 before merge:** either (a) authorize a small test-only follow-up commit updating the 2 stale `test_tier1_wiring.py` assertions to the new architecture (sketcher holds the od.* ports; designer holds none), or (b) record an explicit deferred-debt + escalation entry naming them. Do not merge silently over 2 branch-caused red tests.
- [ ] ⚪ Promote-time smoke (already flagged): runtime KV revival re-bind; optional full daemon boot-scan (needs sandbox DB provisioning).
- [ ] ⚪ Future commission: interpreter-resolution hardening for hardcoded `.venv/bin/python` subprocess paths (N3/N4 family).

## Documentation Updated

- [x] RESULTS/2026-10-10-designer-critic-orchestration-final-gate.md — this report
- [x] PACKS.md — ad-hoc pack records for this commission appended
- [x] LESSONS/2026-10-10-architecture-kill-branch-test-sweep.md — the N1/N2 pattern
- [ ] rules/ensure.md — untouched (user-owned, read-only); no contradictions found

## Code Changes Summary

None by the tester lane (final gate, report-only posture; workers applied zero quick fixes). Worktree at report time: HEAD `35d7e9c5d`, status clean except known `.agents/approver/active.md` lane residue.

---

### Overall Status
- Nit-2 battery: ✅ PASS (8/8) | Supplemental report-integrity: ✅ PASS (2/2) | Static SC gates: ✅ PASS (SC-1..SC-21, 2 metric-shape NOTEs) | Registry + tool resolution: ✅ PASS | Git posture: ✅ PASS | Convention audit: ✅ PASS (0 FAIL) | Probe script: ✅ PASS (4/4) | Neighbor suites: ⚠️ 4 pre-existing + 2 environmental + **2 branch-caused stale (N1/N2, adjudication item)**
- **Testing Complete: ✅ VERIFIED — ready for merge, conditional on leader's N1/N2 adjudication (recommended: option (a) small test-only follow-up commit pre-merge).**
