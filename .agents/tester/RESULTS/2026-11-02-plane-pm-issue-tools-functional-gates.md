# plane-pm-issue-tools — Functional Gates (Phase 5 + Phase 6 functional legs) — AGGREGATE REPORT

**Date:** 2026-11-02 · **Tester lane dispatch** (leader commission) · **Branch:** `feature/plane-pm-issue-tools`
**Tree measured:** base `941cb6321` (origin/latest) → commission tip `964778d48` → final HEAD after the gate cycle `ea1489b2f`
**Commit chain (all branch-side, NOT pushed):** A′ `262dff12c` → evidence `b8d2ece53` → tier-1 `d246fa05d` → fix-cycle `31869158e` → tier-2 `eae2059de` → smoke-rehearsal `ea1489b2f`
**Doctrine:** tiered-gate (ensure.md :11-28) — recipes loaded from `.agents/tester/skills/` canon at deterministic paths; Tier-1 ≤30-min hard; Tier-2 ≤90-min advisory + projection guard + pre-exclusion registry.

## Scope Decision

- Phase5-plan §7's "P2/P3/P4 packs re-run here" obligation was satisfied by GATE C's HEAD leg (same packs, same fences, dual-side) instead of duplicating the runs inside GATE B — disclosed at dispatch time.
- Gate E ran S3+S4 as **rehearsals on the source tree** (no staged artifact exists pre-stage, by design); S1/S2 were written as ready-to-execute checklists for the separate staged-verification dispatch. Not a scope reduction — S1/S2 are structurally impossible before giter stages.

## GATE A — FC9 RE-BASELINE — ✅ PASS

- Re-measured on `964778d48` with the IDENTICAL gate-i §7 formula (binding §7.3 re-baseline instruction): **baseline_tokens = 20,468** (system prompt 8,416 + tool schemas 12,052; PM resolved toolset 46; serialization form **sort_keys+compact** — the only form reproducing the historical arithmetic).
- All six §7.1 assertions PASS (livez/readyz 200 all-true; plane row count 0; `plane_mcp_tool_count=0` with `plane_sync_project` now ABSENT — the ownership guard's −65; measurement point verified; live fence; SIGTERM teardown). F1–F11 all held (port 8971; state.json sha `693998100c…` PRE==MID==POST).
- Historical 20,111 delta explained: +422 charter prose (Phase-4) −65 removed tool.
- Evidence: `task3-evidence/fc9_baselines.json` + `task3-evidence/gateA-rebaseline-run.md`.
- **FC9 serialization trap (binding for every future leg):** the comparison form is `estimate_tokens(json.dumps(convert_to_openai_tool(t), sort_keys=True, separators=(",",":")))` — the plain-dumps variant (21,848) is NOT the comparison base.

## GATE B — TASK-3 E2E + FC9 VERDICT + A′ — ✅ PASS

- Step 0 arming PASS (5 `PLANE_*` names seeded, mode 0600, slug asserted `e2e`, `PLANE_SYNC_ENABLED` not seeded, main `.env` byte-unchanged).
- Fenced worktree source-run daemon on :8972 (scratch PG; dev marker; sweep=0 pinned; FAKE_HOME; per-var scrubs): resolution `env=dev source=explicit-marker slug=e2e allowed=True`; plane row `config_schema_version=3`, header `x-workspace-slug: e2e` (bearer redacted); **177 tools** (SC1 ≥100 ✔); `plane_create_work_item` present; `plane_sync_project` absent; loud-skip WARNING absent in armed state.
- **FC9 (pre-committed rule):** baseline 20,468 → present **60,635** → growth **40,167** → pct **1.962429** → (a) False, (b) False ⇒ **VERDICT: BREACH** ⇒ **A′ executed** (the mandated default, not a judgement call).
- **A′** (`262dff12c`, ONE atomic commit): 28-literal keep-set grant (from `gate-i-keep-set.json`) replacing the `"plane"` category entry; charter prose 4 files naming 28/28 verbs; `agents/.prompt-artifacts.sha256` regenerated **161/161** (`PROMPT-BASELINE: OK`); `SEALED_SHA_BASELINES` re-pin (`357263bd…`); FC8 minimum-toolset + gate-(i) drift detector (4 nodes, pack 15/15). **A′.7 re-measure: present_tokens 60,635 → 29,313** (toolset 223→74; −51.7%).
- **THE TASK:** real PM instance (model `agentic`, 177 plane tools) → `plane_list_projects` → `plane_create_work_item{is_draft=true}` — one create call, no delete, no `nea`. **Created: id `f445a1cd-f55b-48cf-945e-8043e2a0055c`, identifier `E2E-8`**, project `0e9e0143-c9ad-4f58-9114-0d8b549ddea6`, workspace `e2e` (`80859836-bd99-4b6e-bebe-cd29a0cc9dda`), `is_draft=true`, created `2026-11-02T18:03:45Z`. Plane's create API returns no URL field — handle = identifier + activities path.
- **Step 5 read-back:** PASS with disclosed platform constraint — Plane's external REST work-item endpoints EXCLUDE drafts (`Issue.issue_objects`; LIST+DETAIL; MCP proxies the same route — all probed) ⇒ byte-exact REST content read-back of a draft is **structurally impossible**; proved instead via the independent activities lane + the server's own row serialization (HTML→text round-trip byte-identical, sha256 `76de12f8…` both sides). No second Plane write performed.
- **Negatives 5/5 PASS** (typed reasons verbatim): N-1 `workspace 'nea' is production-only; this environment resolves to dev` · N-2 `PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'` · N-3 `workspace 'other' is not permitted in a non-production environment; expected 'e2e'` · N-4 four-part P15a (no NEW row; `config_schema_version` unchanged "3" — heal deferred; PM toolset 46 with 0 `plane_*`; WARNING + agent-gap line; `mcp_tool_names` no stale entry) · N-5 P15b mechanism-level (resolver never permitted `nea`; P2-D closed; no observational `nea` read — DROPPED per G2 NB9).
- **Step 6-R RESTORE PASS** (finally-style): slug `e2e` asserted by value; modes 0600; main `.env` sha `31612413…` byte-unchanged; re-boot ⇒ plane row present/active/slug `e2e`.
- X5.1–X5.15 all MET. Live fence: `693998100c…` PRE==MID==POST; :9797 pid 643626 + :7979 pid 525852 continuous; `current → releases/v0.28.4` untouched.
- Evidence: `RESULTS/2026-11-02-plane-pm-task3-verification.md` + 7 `task3-evidence/` artifacts.

## GATE C — TIER-1 BLAST-RADIUS (dual-side vs base) — ✅ PASS (after one fix cycle)

- Corpus: lane's 7 runtime packs + BR-A 7 pre-existing files + adjacent plane/MCP packs + the A′-touched surface. Slicing proof 1,006 nodes at HEAD / 729+277 at BASE; wall ~17 min (inside the 30-min cap).
- Digests (bare-nodeid form): BASE `65dca363…` (39) vs HEAD pre-fix `c40f153f…` (40) → **net-new 1**: `test_no_forbidden_system_tokens[tools_note.md]` — the A′ charter prose had introduced `meta.json`/`tools.allow` daemon-internal tokens into the agent prompt (banned by the prompt-writing guide). **FIX CYCLE** (`31869158e`, <20-line prose reword preserving all 28 verbs + the `plane_sync_project` ownership statement + 161/161 re-stamp): node GREEN; P11 verb check 9/9; seal tripwire 19/19; **post-fix net-new = 0**. Two stale test-side expectations fixed en route (`test_all_allow_entries_resolve` +14; `test_quarantine_skips_plane_too` +5) with fingerprints.
- 39 base reds stable 3× both legs; 4 configure-builtin-stub packs attributed (21 errors, both legs — routed, not ours to fix). 7 packs registered in PACKS.md (`d246fa05d`).
- Evidence: `RESULTS/2026-11-02-plane-pm-tier1-blast-radius.md` + `EVIDENCE/plane-pm-tier1-2026-11-02/`.

## GATE D — TIER-2 WHOLE-TREE (dual-side vs base) — ✅ PASS-WITH-NOTES, NET-NEW = ∅

- Corpus 32,728 collected (1,145 deselected) HEAD / 32,450 BASE; two mandatory ignores; closure exact both legs; UNPARSED=0.
- Digests: base `093e906e…` (450) vs head `2b349a4b…` (443). Raw diff: 19 net-new candidates / 26 fixed / 424 pre-existing. **Adjudication: ALL 19 candidates resolved to FLAKY/order-sensitive under TRUE solo isolation (114 solo measurements ×3 reps ×2 legs) — zero head-deterministic reds.** Batched 3× runs showed intra-batch interference and were superseded by solo classification. Pre-existing 424: 39 cited from Tier-1 (digest matched) + 385 re-proven 3× both legs (byte-identical rep digests `dd166c19…`).
- Base-debt attributed: 32 configure-builtin-stub nodes, 2 TestPromptComposition reds, 4-sealed-artifact chart red, agent-base-v2 ×2, 39 plane-family Tier-1.
- Disclosures: perf-6 byte-identical both legs (pre-exclusion registry pin `regression_performance`); 17 exception-suffix summary lines corrected in extraction (counts unaffected); 90-min/leg advisory exceeded by the 3× isolation phase (~30 min/leg) — loudly disclosed, completed.
- **Incident:** the first worker WEDGED silently mid-HEAD-leg (driver+pytest died at shard005, no processes, no records — environmental). Terminated per the fan-in escape valve; the ONE replacement RESUMED from preserved scratch state (BASE leg intact, shards 000-004 intact; shard005 re-ran clean in 91 s with tallies identical to base shard005 — proving the death environmental). Worktree teardown proven; main-checkout porcelain sha byte-identical pre/post.
- Evidence: `RESULTS/2026-11-02-plane-pm-tier2-whole-tree.md` (incl. RESUME section) + `EVIDENCE/plane-pm-tier2-2026-11-02/` (31 files + manifest) + PACKS.md Gate D row (`eae2059de`).

## GATE E — PHASE-6 S3/S4 STAGED-SMOKE REHEARSAL — ✅ PASS 4/4 (0 vacuous)

- Four fenced source-run boots, DUMMY credentials only (per-leg dummy bearers; real URL; the ARMED worktree `.env` never sourced, read-only, sha cross-checked): **L1** dev+e2e ⇒ row created, header `e2e` (control) · **L2** dev+nea ⇒ NO row, typed WARNING `workspace 'nea' is production-only…` — POLICY path, not a skip · **L3** dev+unset ⇒ NO row, typed WARNING `PLANE_MCP_WORKSPACE_SLUG is unset…` — not a skip · **L4** live-classification+nea, SAFE FORM (explicit `ENSEMBLE_SELF_ENV=live`, every derived path scratch-bound — journal inside FAKE_HOME; `ensemble_prod` never addressed) ⇒ row created, header `nea`. **No work item created in any leg.**
- Vacuity tripwire: env-name censuses prove creds PRESENT in the negative legs (25/25/24 names; no missing-credential skip reason anywhere); warm-pool control (L1 primed 2 servers incl. `plane: 0 tools` — expected with a dummy bearer — vs 1 in L2/L3).
- S4 fence: state.json sha `b8d83f27…` PRE==MID==POST==post-commit; :9797 pid 2065195 + :7979 pid 525852 continuous; `current` unchanged. Ceremony inputs recorded (promote command form; ADJ-4 re-mint outstanding, user-owned).
- Teardown: all scratch DBs/roles dropped (0 remaining), ports freed, no orphans (surviving context7 children proven to belong to the live daemon by cwd+cgroup).
- S1/S2 written as ready-to-execute checklists (RESULTS §8) — NOT run (no staged artifact pre-stage, by design). Staged-dispatch adoptions: **binary-copy L4** (strictly stronger than source-run) + `env -i` allowlist + `MCP_DISABLE_BUILT_IN_CONTEXT7=true` (recommended) — the harness in `EVIDENCE/plane-pm-smoke-2026-11-02/` is re-runnable.
- Disclosures: D-5 — one accidental READ-ONLY GET pair against :9797 during the first assertion (`/bin/sh` `source` failure fell back to ambient `PORT=9797`; no mutation; fence shas/pid/uptime prove it; all subsequent assertions ran under `bash` with an absolute port). PACKS.md deliberately untouched (a rehearsal is not a pack).
- Evidence: `RESULTS/2026-11-02-plane-pm-phase6-smoke-rehearsal.md` + `EVIDENCE/plane-pm-smoke-2026-11-02/` (49 artifacts + harness scripts). Commit `ea1489b2f`.

## NET-NEW TABLES (summary)

| Gate | Corpus | BASE reds | HEAD reds | NET-NEW (deterministic) | FLAKY bucket |
|---|---|---|---|---|---|
| C (Tier-1) | lane blast radius (1,006 nodes) | 39 | 39+1→39 post-fix | **0** (after fix cycle `31869158e`) | — (all stable 3×) |
| D (Tier-2) | whole tree (~32.5-32.7k nodes) | 450 | 443 | **∅** (19 candidates all solo-flake/order-class) | 19 (documented) |

## ROUTED / OPEN ITEMS

1. **FC9 condition (b) is unachievable by construction for any plane grant** (plane schema growth 7,881 > headroom 5,117 vs the 20,468 baseline; post-A′ the surface sits at 29,313 tokens — growth 8,845 / 43.2%: condition (a) now satisfied, (b) still False). Routed to the architect — not an implementer's call to waive or re-derive.
2. **Plane exposes no draft-readable REST endpoint** (drafts excluded from `Issue.issue_objects`; MCP proxies the same route). Draft verification used the activities lane + server row serialization. Spec decision routed.
3. **MCP-gateway bearer plaintext exposure re-confirmed** (`GET /api/mcp-servers` returns the Plane bearer unredacted; `redact_secrets` misses `config.headers`) — every lane artifact redacted; the surface itself is not. Owner: MCP-gateway/security commission.
4. Pre-existing (routed, not ours): 4 configure-builtin-stub packs (32 nodes whole-tree); 2 TestPromptComposition reds; 4-sealed-artifact chart red; agent-base-v2 ×2; `McpStdioConfig` warmup WARNING (not lane-attributable).
5. ADJ-4 live/demo credential re-mint — user-owned; live efficacy waits on it.
6. The lane's fenced worktree `.env` is ARMED (five `PLANE_*` keys, slug `e2e`, mode 0600) — the Step 6-R sanctioned exit state.

## VERDICT

**ALL FIVE GATES PASS. GO for staging** (giter may stage; the separate staged-verification dispatch then executes S1–S4 against the frozen artifact using the committed checklists + harness). No promote — the user's 3-factor ceremony owns it.
