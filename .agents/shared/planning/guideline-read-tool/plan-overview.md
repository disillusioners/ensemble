# Plan Overview: guideline-read-tool

Date: 2026-11-13T18:40Z
Author: planner[v2] via plan-creation worker
Status: Draft — design Emperor-FIXED (ruling 2026-11-13); architect refines the runtime-resolution seam before implementation

## Objective

Give privileged agents a read-only window onto the shipped guideline corpus: a new privileged tool category `guidelines` with two flat tools — `guidelines_list()` (parsed summary per file) and `guideline_read(slug)` (full verbatim content) — served from THE RELEASE PAYLOAD (`releases/<v>/guidelines/`), staged by `stage.sh` from `docs/guidelines/`, with the doctrine carried in the tool itself. A single sentence that, when true, marks the feature complete: *an allow-listed agent can list and verbatim-read every guideline that shipped with the running release, and no agent outside `[ari, leader, devops, sonic-leader]` can see the category at all.*

## Scope

### In Scope

- `daemon/tools/guidelines.py` — new module: both tools, fail-soft parser, `GUIDELINES_AUTHORIZED_AGENTS` frozenset, doctrine text in docstring/`CATEGORY_DOC`/`_full_doc_`.
- Full registration inventory: 4 edits in `daemon/tools/_tool_registry.py` (privileged entry #22, CATEGORY_MODULES row, DYNAMIC_TOOL_NAMES, KNOWN_TOOL_NAMES regen) + SAME-PR prose re-derivation; `daemon/tools/instance.py` import+extend; `daemon/loader.py` warm-list. NO `_auth.py` entry (not agent-backed).
- Allow-list: 4 exact `meta.json` diffs (ari, leader, devops, sonic-leader) + the full pin-census update (same-commit duty) + NEW `tests/unit/tools/test_guidelines_registration.py`.
- Release seam: `scripts/upgrade/stage.sh` gains the guidelines payload copy + 7th top-level tree sha (`guidelines_tree_sha256`) + per-file `guidelines_manifest` map + MANDATORY stage-local re-hash leg. Manifest consumer audit (result: zero consumer changes — all tolerate extra keys). Floor-constant advancement ordering. `rollback_safe=1` adjudication.
- Doctrine homes: tool-side (both lines verbatim in help text) + agent-side (`## Guidelines` sections in the 4 allowed agents' `tools_note.md`) + prompt-artifact sha256 baseline regen duty.
- Census/ceremony docs: registry SAME-PR derivation line, `.agents/shared/metrics/tool-categories.json`, `decisions.md`, `.agents/approver/guideline-read-tool-tracking.md`, tester RESULTS — including the FIRST-privileged-grant-to-a-sonic-agent ceremony note.
- Test matrix (registration pins, allow gates, parser happy/fail-soft, verbatim round-trip, stage-smoke) + release recipe v0.30.3 (executor = giter, later phase; NO promote).

### Out of Scope

- Any DB copy, table, migration, scheduler, RAG entry, or caching layer — the design forbids them; the tool reads files, period.
- Any write path: the tools NEVER write, never mutate any cache — "a window onto stone".
- Repo-path reads at runtime in live: the runtime reads ONLY the install dir. (Dev-mode fallback shape = architect's open question Q3.)
- Guideline CONTENT changes: `docs/guidelines/` (5 files) is already landed at this base (merge `5316e0cd3`) and is untouched.
- `lib.sh` / `executor_family.sh` edits: guidelines is NOT an executor-family member (never add it to `executor_family_paths` — would trip the coverage gate); integrity coverage comes from the stage-local re-hash leg, not `lib.sh`'s loop (which stays `agents frontend`, lib.sh:4102).
- Kill-switch: likely none (pure read surface) — architect's call, recorded either way (Q4). The plan presents the input, not the decision.
- Promote: NO promote — the Emperor's ceremony.

### Scope boundary justifications

- This is a READ-ONLY window onto 5 files. Every mechanism that could add state (DB mirror, cache, watcher) is excluded by design — the release payload IS the source of truth, so the tool cannot serve any guideline newer than the running release (INTENDED — state in docs/help).
- The runtime-resolution seam is deliberately NOT over-designed here: research evidence is presented; the architect refines before implementation (Phase 1 gate).

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Tool module + registration | `daemon/tools/guidelines.py` (both tools + doctrine) fully registered and wired | 6 | tight with 2 (SAME-PR: one commit); loose with 3 (payload layout contract) | pending |
| 2 | Allow-list + pin census | 4 grants + every pin bumped 21→22 + new registration pin file | 7 | tight with 1 (SAME-PR: one commit) | pending |
| 3 | Release payload seam | stage.sh ships `guidelines/` + manifest keys + local re-hash leg | 7 | loose with 1 (layout contract: flat `guidelines/` at release root) | pending |
| 4 | Doctrine + ceremony docs | agent-side doctrine, sha regen, approver/tester/census records, final sweep | 6 | loose with 1 (doctrine text), loose with 2 (census proves the 4 grants) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | **tight** (SAME-PR rule: registry entry #22 + ALL pin bumps in ONE commit) | loose (layout contract `releases/<v>/guidelines/`) | loose (doctrine text duplicated in tools_note) |
| Phase 2 | tight | — | independent | loose (allow census = evidence for ceremony docs) |
| Phase 3 | loose | independent | — | independent |
| Phase 4 | loose | loose | independent | — |

**Hard cross-phase constraint:** the house SAME-PR rule (`_tool_registry.py:375-445`) requires the registry entry, the meta.json grants, and EVERY pin bump to land in the same commit. Phases 1+2 are therefore one commit when giter executes; the phase split is work-organization only.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **THE SEAM — frozen-binary payload resolution**: `Path(__file__)`-derived base lands inside the PyInstaller bundle (anti-pattern `plugin_registry.py:63-64`); the reader must resolve `<exe-dir>/guidelines/` under `releases/<v>/` | High | Medium | Phase 1 presents both patterns (anti vs positive, `loader.py:446-448`, `prompt_refresh.py:660/:745`) + injectable base-dir param; architect rules before implementation (Q1). Registration pins do not depend on resolution, so the rest of the plan is seam-independent |
| 2 | Stale pin census — the prose census (research + this plan) can drift; `test_memoir_registration.py:377-384` self-pins the count-pin LITERAL string AND the pin-file census | Medium | Medium | Derive-by-grep is BINDING at implementation (`_tool_registry.py:441-443`); memoir derivation gets literal→22 + file-list + stale-flavor negative updated same commit |
| 3 | Decorator-only registration is SILENTLY INVISIBLE; extend-order mistakes bypass the privileged strip or the help scan | High | Low | notify-user legs a–j pinned in the new test file: decorator ABOVE `@tool` (`notify_user.py:503-504`), extend before help(:7620)/scan(:7625)/filter(:7628), strip reads live constant (:7633) |
| 4 | `loader.py` warm-list miss → cold-boot docs render without the category (W1-FIX-NOW defect class) | Medium | Medium | Explicit warm-list task (Phase 1 T5, metrics pattern `loader.py:521-528`); boot smoke asserts zero "neither a known category nor a known tool" WARNINGs |
| 5 | Stage-local re-hash leg omitted → a copy glitch ships silently (`lib.sh` `integrity_verify` loops only `agents frontend`, :4102) | High | Low | The leg is a MANDATORY Phase 3 task mirroring plugins `stage.sh:747-753`; stage-smoke negative leg proves refusal on corruption |
| 6 | Floor constants not advanced (D6-8 cadence) — gates pass MECHANICALLY either way (existence-only coverage), but the stamp would vouch for stale scripts content | Medium | Medium | Release recipe orders the advance into the release-bump commit (both constants, one commit, post-merge pre-stage); disclose either way at ceremony |
| 7 | Prompt-artifact edits without sha regen → baseline drift tripwire | Medium | Low | Phase 4 pairs the 4 `tools_note.md` sections with `agents/.prompt-artifacts.sha256` regen (`scripts/protection/generate_prompt_artifact_baseline.py`) + `SEALED_SHA_BASELINES` row re-pin with dated attribution, SAME commit |
| 8 | Manifest key addition breaks a consumer | Low | Low | Consumer audit COMPLETE (Phase 3): every consumer tolerates extra keys (see phase3-plan §Consumers); no test pins the manifest key set |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `guidelines_list` serves all 5 files parsed | Call the tool in dev; assert 5 entries: 3 canonical (slug==filename stem, status=="draft" cut at first ` · `, non-empty purpose) + README/TEMPLATE auxiliary fail-soft | 5/5 entries, zero exceptions |
| 2 | Fail-soft parsing | Feed synthetic malformed/empty/no-§1 files (test fixtures); call `guidelines_list` | Never raises; auxiliary fallback values per parse contract |
| 3 | `guideline_read` verbatim + typed refusal | Round-trip `guideline_read(slug)` == file content for all 5 slugs; `guideline_read("nope")` | Content identical; refusal names ALL valid slugs, never raises |
| 4 | Allow gates | Construct instance tools for each of: sonic-leader (GRANTED), sonic-coder/-developer/-reviewer/-tester + every unlisted agent (DENIED) | Guidelines tools present ONLY for the 4; strip removes them for all others; in-body gate refuses unknown/unlisted `agent_id` with a typed token |
| 5 | Registration pins green | `uv run pytest tests/unit/tools/test_guidelines_registration.py` + the full census (6 EXPECTED sets, 5 inline pins, all count pins ==22, memoir derivation, order pin, grant pins) | All green; grep-derived census count == implemented count |
| 6 | Boot clean | Boot the daemon; grep log for "neither a known category nor a known tool" / category-missing warnings | Zero warnings naming `guidelines`; category docs render on cold boot (warm-list works) |
| 7 | Payload seam | Run the hermetic stage-smoke: staged tree contains `guidelines/` (5 files); `manifest.json` has `guidelines_tree_sha256` (7th tree sha) + `guidelines_manifest` matching an independent `find|sed|sort`-ordered re-hash | Hashes match; corrupt-a-file negative leg refuses the swap-in (exit 1) |
| 8 | Consumer zero-touch | Re-run the consumer audit greps (phase3-plan §Consumers) post-implementation | No consumer file modified |
| 9 | Doctrine present | `tool_help("guidelines_list"/"guideline_read")` + rendered category docs contain BOTH fixed lines verbatim; 4 `tools_note.md` files carry the `## Guidelines` obligation section | Both lines verbatim; 4/4 sections; sha manifest regenerated in same commit |
| 10 | Census/ceremony complete | `tool-categories.json` contains `guidelines` in sorted position; registry entry #22 comment + SAME-PR derivation carry ruling date + sonic-first note; approver tracking file exists | All present; tester RESULTS proves the 4-agent census |

## Component Map (commission items → artifacts)

| Item | Home |
|------|------|
| A. Tool module + registration | phase1-plan.md |
| B. Allow-list + pin census + new test file | phase2-plan.md |
| C. stage.sh + manifest + consumers + floor + rollback_safe | phase3-plan.md + release-recipe.md |
| D. Doctrine (two lines + homes + sha duty) | phase1-plan.md T1/T2 (tool-side) + phase4-plan.md T1/T2 (agent-side) |
| E. Census/ceremony docs + sonic-first note | phase4-plan.md + decisions.md D11 |
| F. Test matrix | test-matrix.md (+ per-phase acceptance) |
| G. Release recipe v0.30.3 | release-recipe.md |
| H. Risks + architect open questions | decisions.md (D7, D8, Q1–Q4) |

## Binding Invariants (from the fixed design — not re-litigable)

1. Privileged category `guidelines`, allow-list EXACTLY `[ari, leader, devops, sonic-leader]` via `meta.json tools.allow`. sonic-leader is the FIRST privileged-category grant to a sonic agent — must appear in the ceremony record.
2. Two flat read-only tools only. `guidelines_list` parsing TRIVIAL and FAIL-SOFT. `guideline_read` full verbatim; missing slug → typed refusal naming valid slugs.
3. SOURCE OF TRUTH = THE RELEASE PAYLOAD. stage.sh copies `docs/guidelines/` → `releases/<v>/guidelines/`; manifest gains the 7th top-level tree sha + per-file map (payload conventions; source-less payload rules apply). Runtime reads ONLY the install dir. No DB copy, no repo-path reads at runtime in live, no drift channel.
4. Doctrine lives IN THE TOOL: help text carries exactly these two lines — "Fixed files are the authority; this tool only reads them" and "Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md)". No POK entries, no critical notes.
5. Scope discipline: NO DB tables, NO migrations, NO scheduler, NO RAG, NO caching layer.

## Research Insights

All anchors verified in-worktree @ `5316e0cd3` by 3 explorer passes (research-guideline-content.md, research-registration-pattern.md, research-release-seam.md — committed alongside this plan) and re-spot-verified by this planner:

- Content shapes: 3 canonical `guideline-*` files (H1==stem @L1; L3 ONE `**Status:** … · **Feature:** … · **Audience:**` line, separator = middle-dot U+00B7 space-padded; `## 1. Purpose & doctrine` @L5; purpose ¶ @L7) + 2 auxiliary shapes (README: standalone multi-line `**Status:** CANONICAL` block L3-5, different H1; TEMPLATE: two H1s, per-line fields L12-14). NO YAML front matter anywhere. Parse contract in phase1-plan T1.
- Registration anatomy: `PRIVILEGED_TOOL_CATEGORIES` @`_tool_registry.py:446` (21 members verified; `prompt-assent` #21 closes at :822) → new entry #22. SAME-PR prose + 3 derivation greps @:441-443. `CATEGORY_MODULES` notify-user row @:1296. `DYNAMIC_TOOL_NAMES` :23 (notify_user :150); `KNOWN_TOOL_NAMES` :1565 (notify_user :1650), regen cmd :1553, drift pin `test_frozen_tool_name_discovery.py`. Decorator order pinned `notify_user.py:503-504`. instance.py extend: prompt-assent currently last @:7102-7107; help :7620 / scan :7625 / filter :7628 / strip :7633. `METRICS_AUTHORIZED_AGENTS` frozenset template @`metrics_read.py:207-209`. `_auth.py` needs NO entry (TOOL_REQUIRED_AGENTS :35-51 is agent-backed-spawn only). loader warm-list :489-546 (metrics :521-528).
- Allow-list diffs verified verbatim: ari `meta.json` allow array ends `"prompt-assent"` @L48 (append `"guidelines"` after, before L49 `]`); leader L15 / devops L12 / sonic-leader L15 single-line arrays (append at end). Denied sonic set: sonic-coder/-developer/-reviewer/-tester — none of the five sonic agents holds ANY privileged category today (sonic-leader's `midflight` is NON-privileged, `midflight_report.py:62` docstring) ⇒ the sonic-first claim is SUPPORTED. Zero `guidelines` category-name collisions repo-wide.
- Pin census (grep-derived at this base — binding re-derive at implementation): 11 files pin the exact set (6 `EXPECTED_PRIVILEGED_SET` defs + 5 inline frozensets); 12 count-pin assert sites (`== 21`) across 11 files + the memoir derivation self-pin at `test_memoir_registration.py:377-384` (pins the literal string AND the file census AND the stale-`20`-flavor negative — update all three faces). ari allow-array ORDER pin @`test_board_registration.py:310`; grant pins `test_anomaly_report_grant_pins.py:99/:317`; aegis census `tests/unit/agents/test_aegis_security_family.py:171` (verify pin shape); stale docstring `test_service_tool_flag_off_byte_identical.py:819` (+ inline pin :842).
- Release seam: stage.sh copies NO docs tree today (verified). Insertion points: plugins guard :318-321; plugins copy :450; walks :507-513; heredoc tree shas :714-717 + maps :718-721; local re-hash legs :747-767 (plugins exemplar). `lib.sh` `integrity_verify` loop `agents frontend` @:4102; `_tree_manifest` find|sed|sort @:3116; `_tree_hash_of_lines` @:3232. Consumers ALL tolerate extra top-level keys (promote.sh:496/:924, rollback.sh:174, lib.sh:7840, `upgrade_tools.py` `.get`-style :798-836, `release_manifest.py` reads only `agents_manifest` — tolerance documented :22-23); status.sh/ledger_check/journal_read zero manifest refs; no test pins the key set. Frozen anti-pattern `plugin_registry.py:63-64`; positive `sys.frozen → Path(sys.executable).parent` @`loader.py:446-448`, `manager.py:4815-4820`, `api.py:169-171`; most robust anchor `resolved_dir.parent.parent` @`prompt_refresh.py:660/:745`. Test injection precedents: constructor-arg `release_root` (`test_release_manifest.py`), monkeypatch (`test_prompt_refresh_primitive.py`), hermetic mktemp + synthetic git repo (`tests/test_executor_family_payload.sh`).
- Floor guard: `PIPELINE_SCRIPTS_FLOOR_COMMIT` @`upgrade_tools.py:4145` == `PIPELINE_SCRIPTS_REV` @`lib.sh:95` (`087f782a…` today). Coverage gate is EXISTENCE-only (`executor_family.sh:48-50`; stage.sh:674-699) — content edits do NOT trip it; D6-8 cadence (stage.sh:633-641) still OBLIGES advancing BOTH constants to the merge commit in the release-bump commit. Arm-time guard `upgrade_tools.py:4210-4390` (ancestry :4352-4367).
- rollback_safe: full-history DDL grep ALWAYS fires (stage.sh:356-363 → `contains_contract_phase=true` expected); unset+match → exit 78 (:382-388); explicit `=1` legit when tag-to-tag delta is DDL-empty (v0.21/22/23 precedents; in-file rule :379-385). Receipt stdout-only (POK `stageverify-01`) — verify via release manifest.
- Doctrine homes: tool-side docstring/CATEGORY_DOC/`_full_doc_` (`notify_user.py:1-117/:149-169/:538`); agent-side `## Notify-User` precedent @`agents/ari/tools_note.md:820`; sha regen via `scripts/protection/generate_prompt_artifact_baseline.py` → `agents/.prompt-artifacts.sha256`; `SEALED_SHA_BASELINES` tripwire @`tests/test_chart_image_delivery_e2e.py:1425` (35-file dict; re-pin ONLY changed rows with dated attribution, house pattern :1402-1411). Runtime prompt-artifact writes refused (`daemon/tools/prompt_artifacts.py::check_write` at two `filesystem.py` sites) — file edits in-branch are the release-time source.

## Open Questions

Carried for the architect (details + evidence in decisions.md; these are the ONLY genuinely blocked decisions — everything else is decided):

1. **THE SEAM** — final frozen-binary reader shape (Q1 in decisions.md): must resolve `<install>/releases/<v>/guidelines/` under PyInstaller. Plan presents options (a) `sys.frozen → Path(sys.executable).parent` fork, (b) registry-resolved agents root (`resolved_dir.parent.parent`), each with an injectable base-dir param for tests. Architect picks.
2. **Floor-guard interaction** (Q2): architect confirms the D6-8 advance ordering (release-bump commit, both constants, post-merge pre-stage) and the stale-scripts-pin consequence for the NEXT ceremony's pinned scripts worktree.
3. **Dev-checkout fallback** (Q3): shape that keeps the tool testable in dev without violating "no repo-path reads at runtime" — repo-path dev fallback (`<repo>/docs/guidelines`, loader-fork derived) vs explicit override param. Architect decides.
4. **Kill-switch y/n** (Q4): likely none (pure read, no write path, no ledger); architect's call, recorded either way.

If the architect answers are delayed: Phases 2–4 are seam-independent and may proceed; only Phase 1 T2 (the reader) gates on Q1/Q3.
