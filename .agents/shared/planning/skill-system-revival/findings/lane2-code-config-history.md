# Lane 2 — Code / Config / History / Docs findings
## Phase 0 READ-ONLY investigation — agents-ensemble skill system (skill capture + skill evolution)

**Project:** agents-ensemble (`83da04de-a410-4fb5-9e92-251a99d28a52`) · **Workdir:** `/home/nea/ensemble-src`
**Lane:** Code / Config / History / Docs · **Author:** wanderer (read-only investigator) · **Date:** 2026-11-09
**Constraints honoured:** no writes except this file; no branch/checkout/reset/stash/commit; no daemon restart; no dispatch; no re-enable.

---

## 0. TREE STATE — READ THIS FIRST (it changes how every citation below must be read)

The commission asked me to verify against "the CURRENT tree (HEAD of the checked-out branch in `/home/nea/ensemble-src`)". The checkout is **NOT a branch** and is **two weeks behind `origin/latest`**. Every citation below is labelled with which tree it belongs to.

| Fact | Value | Evidence |
|---|---|---|
| Checked-out ref | **detached HEAD** (no branch) | `git rev-parse --abbrev-ref HEAD` → `HEAD` |
| HEAD SHA | `0138952876bb456f63417e040884ab28761c9835` | `git log -1` → `2026-10-26 02:28:23 +0000 | giter | merge: tester/notify-user-staged-verification — v0.27.8 staged verification PASS-WITH-NOTES` |
| `origin/latest` | `5da1021f2c67526cb2d8d19222f82234b4600e98` — `fix(views): session-bypass + origin-gate veto + Accept-aware envelopes` (v0.29.7) | `git log -1 origin/latest` |
| Working tree | **DIRTY: 94 paths** — 73 untracked (`??`), 21 modified. Untracked includes `.agents/shared/planning/metrics-unification/**` (11 files, dated Nov 1–3) and `.agents/approver/*-tracking.md` | `git status --porcelain | wc -l` → `94` |
| Uncommitted skill-file WIP | `daemon/repositories/skill/repository.py` (+64), `daemon/services/skill_evolution_service.py` (+50), `daemon/services/skill_store_service.py` (+62), `daemon/tools/skill_tools.py` (+183), `tests/repositories/test_skill_repository.py`, `tests/services/test_skill_evolution_service.py`, `tests/tools/test_skill_feedback_tool.py` | `git diff --stat -- daemon/ tests/` |
| What that WIP is | **A/B-generation disambiguation work, not the attribution-seam fix**: adds `list_active_by_name()` + `find_by_id_prefix()` to `SkillRepository` | `git diff daemon/repositories/skill/repository.py` (hunk `@@ -579,6 +579,70 @@`) |
| `.agents/shared/planning/skill-system-revival/` | **Did not exist** before this lane; I created `findings/` and wrote this one file | `mkdir -p` + `ls -la` |

**Consequence for the downstream writer (important):**
1. The **skill kill-switch machinery IS present at HEAD** — the code-level analysis in §A1 is valid for the checked-out tree.
2. The **central metrics machinery (items 6 and 7) DOES NOT EXIST AT HEAD.** `daemon/services/metrics_query/`, `daemon/services/metrics_census.py`, `daemon/services/metrics_listener.py`, `daemon/routers/metrics.py` are all **absent** at HEAD and present only at `origin/latest`. Every citation in §A6/§A7 is explicitly tagged `origin/latest:`.
3. The planning docs under `.agents/shared/planning/metrics-unification/` are **untracked** in this checkout (0 tracked files) — they are local artifacts, not committed history.

---

## (a) HEADLINE ANSWERS

### A1. Kill-switch sites — CONFIRM / REFUTE against the checked-out tree (HEAD `013895287`)

All line numbers below are **current-tree** (HEAD + the uncommitted working-tree diff, which does not touch the dispatcher/config/router/job-processor files).

| # | Prior claim | Verdict | Current evidence |
|---|---|---|---|
| 1 | Shared resolver `_resolve_switch(env_name, configured_default)` ~`:83-117`; OFF `{0,false,no,off}`, ON `{1,true,yes,on}`, else `ValueError` (fail-closed) | ✅ **CONFIRMED — exact range** | `daemon/services/skill_job_dispatcher.py:83-117`; token sets `:79-80`; `raise ValueError` `:114-117` |
| 2 | `ENSEMBLE_SKILL_CAPTURE_ENABLED` (default OFF) resolved by `_resolve_capture_enabled()` ~`:150-173` | ✅ **CONFIRMED — exact range** | const `:72`; resolver `:150-173`; `return _resolve_switch(SKILL_CAPTURE_KILL_SWITCH_ENV, False)` `:173` |
| 3 | `ENSEMBLE_SKILL_EVOLUTION_ENABLED` canonical, default OFF, falls back to `SkillEvolutionConfig.evolution_lane_enabled` (YAML key > pydantic alias env > False) | ✅ **CONFIRMED** | const `:202`; `_resolve_evolution_enabled` `:205-232`; public accessor `is_evolution_lane_enabled` `:235-252`; config field `daemon/config.py:4210-4227` (`default=False`); alias disclosure `daemon/config.py:4198-4209` |
| 4 | TRAP: a YAML-carried key silently defeats the alias env (pydantic init-kwargs beat env) | ✅ **CONFIRMED (mechanism)** | `daemon/config.py:6570-6584` — the YAML `skill_evolution` block is injected as **init kwargs** (`config_dict["skill_evolution"] = {...}`); pydantic-settings gives init-kwargs priority over env sources. The same comment at `:6571-6576` states the rule. |
| 5 | …and the **shipped `config.yaml` does NOT carry the key** | ✅ **CONFIRMED** | `config.yaml:553-586` is the whole `skill_evolution:` block — keys are embedding/analysis/evolution models, `selector_model`, `ab_sample_size`, `ab_min_difference`, `max_extensions`, injection knobs, trigger thresholds, capture thresholds. **No `evolution_lane_enabled`.** Repo-wide `grep -rn evolution_lane_enabled --include="*.yaml" --include="*.yml"` → **0 hits**. ⇒ the alias env `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` is a **live ON-switch today**. |
| 6 | Enforcement: `skill_metrics_service.py` ~`:595` (Gate 0) | ✅ **CONFIRMED — the check is exactly at `:595`** | `daemon/services/skill_metrics_service.py:575-596`; the check `if not _resolve_capture_enabled(): return None` at `:595-596` |
| 7 | Enforcement: `skill_job_dispatcher.py` ~`:576` / `:708` / `:786` — "queued rows terminally refused with reason `lane_disabled`" | ⚠️ **SPLIT — the cited lines are DOCSTRINGS, and the terminal refusal is NOT in the dispatcher** | `:576` = `enqueue_evolution` docstring "**Kill-switch** — `ENSEMBLE_SKILL_EVOLUTION_ENABLED`"; `:708` = `enqueue_capture` docstring; `:786` = `dispatch_fix` docstring. The **gates** are at `:621-635` (evolution) and `:720-727` (capture). The **`lane_disabled` terminal write lives in the job processor**: `daemon/services/job_processor.py:787` `terminal_reason="lane_disabled"`. |
| 8 | Enforcement: `daemon/routers/skills.py` ~`:1358` / `:1385` | ✅ **CONFIRMED** | `daemon/routers/skills.py:1358` (503 docstring) and `:1385` (503 `detail.error` body) — `POST /api/skills/{id}/fix` returns **503**, not a fake 202 |
| 9 | Enforcement: `daemon/services/job_processor.py` ~`:732` | ✅ **CONFIRMED** | `:731-734` returns the reason string `"kill-switch ENSEMBLE_SKILL_EVOLUTION_ENABLED off / evolution_lane_enabled=False"`; probe at `:721-736`, **fail-closed** at `:726-727` (absent accessor) and `:735-736` (raising probe) |
| 10 | Enforcement: `daemon/tools/skill_tools.py` ~`:502` / `:595` / `:627` / `:676` | ✅ **CONFIRMED — all four** | `:502` (`skill_fix` docstring), `:595` (agent-facing "⛔ NOT dispatched" text), `:627` (`_full_doc_` gate description), `:676` (gate-refused example block) |
| 11 | Enforcement: `daemon/tools/skill_evolution_tools.py` ~`:383-387` | ✅ **CONFIRMED (docstring) + implementation found** | docstring contract `:382-394`; **actual gate at `:417-419`**: `if not _resolve_capture_enabled(): return {"skipped": True, ...}` (import at `:51-53`) |
| 12 | Metrics **RECORDING** runs regardless of switch state; only capture/evolution **DISPATCH** is gated | ✅ **CONFIRMED** | `daemon/services/skill_metrics_service.py:446` and `:469` write the usage rows, and **only then** `:488` calls `_check_capture_eligibility` |
| 13 | "Report EVERY other switch that can disable the lane" | ✅ **Enumerated — 6 additional switches/knobs** | see §A1.1 below |

#### A1.1 Every other switch / brake that can disable the skill lane (exhaustive for `daemon/`)

| # | Switch / brake | Kind | Default | Where enforced | Notes |
|---|---|---|---|---|---|
| S1 | `ENSEMBLE_SKILL_CAPTURE_ENABLED` | env (canonical) | OFF | `skill_job_dispatcher.py:72`, `:150-173`; `skill_metrics_service.py:595`; `skill_evolution_tools.py:417` | gates the CAPTURE lane only |
| S2 | `ENSEMBLE_SKILL_EVOLUTION_ENABLED` | env (canonical) | OFF | `skill_job_dispatcher.py:202`, `:205-232`, `:621-635`; `manager.py:6698`, `:7037`; `job_processor.py:721-736` | gates Tier-3 evolution admission + queued execution |
| S3 | `SkillEvolutionConfig.evolution_lane_enabled` | config field | `False` | `daemon/config.py:4210-4227` | the *constructed default* consulted only when S2 is unset/empty |
| S4 | `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` | pydantic alias env | unset | `daemon/config.py:4198-4209` (disclosed) | **second, easily-missed ON switch**; sets only S3 |
| S5 | `SkillEvolutionConfig.fix_cooldown_seconds` | config field | `1800` | `daemon/config.py:4232-4239`; enforced `skill_job_dispatcher.py:641-657` | refuses a 2nd FIX for the same `(project, skill)`; `0` disables |
| S6 | per-agent `skill_injection` flag | agent meta | varies | capture Gate 3, `skill_metrics_service.py:538-541`, `:610-613` | a non-injection agent can never capture |
| S7 | `skill-keeper` tool allowlist | agent meta | v0.2.0 | `agents/skill-keeper/meta.json:11-12` | **the confinement**: `allow` = 4 read tools + `self/help/knowledge/dynamic-skill/skill-evolution/shared_meta_kv`; `deny` = `bash, proc, service, write_file, edit_file, inner_soul` |
| S8 | `job_queues.is_paused` on `system_parallel_queue` | DB flag (runtime) | — | queue the dispatcher routes to: `skill_job_dispatcher.py:286-289` (`PARALLEL_QUEUE_NAME = "system_parallel_queue"`) | **not a code switch**; the only live-acting, restart-safe interim brake (see §A9) |

**Confirmed NOT-switches (things that do NOT gate the lane):**
- **Tier-2 `enqueue_analysis`** (`skill_job_dispatcher.py:514-557`) — no kill-switch read in its body; by design (`decisions.md:31`).
- **`enqueue_metric_scan`** (`:739-767`) — no gate; by design.
- **The inline `skill_evolve` tool path** — **UNGATED**. Disclosed residual at `daemon/config.py:4186-4188` ("The inline `skill_evolve` tool path (a keeper instance already running) is NOT covered by this switch"); confirmed empirically: `grep -n "evolution_lane\|_resolve_evolution\|kill" daemon/tools/skill_evolution_tools.py` → **no lane check anywhere** in that module. Also `decisions.md:31` and `decisions.md:116`.
- **There is NO master skill-system switch.** `grep -rn "SKILL_SYSTEM\|master_switch\|skill_system_enabled" daemon/ --include="*.py"` → **0 hits**. HANDOVER §3 P3 rec 6 and `decisions.md:99` both record it as **deferred / not implemented**.

#### A1.2 Live env surface (checked-out tree)

| Check | Result |
|---|---|
| `.env` contains `ENSEMBLE_SKILL_*`? | **NO** — `grep -n "ENSEMBLE_SKILL" .env` → no match (only `SKILL_EVOLUTION_EMBEDDING_*` at `.env:30-33`; note `.env:32` carries a plaintext OpenAI-style key — **value not quoted here**) |
| `.env.example` contains `ENSEMBLE_SKILL_*`? | **NO** — `grep -n "ENSEMBLE_SKILL" .env.example` → no match. The commit that would have documented them (`e61d67e93` "docs(env): document kill-switch and opt-in env flags in .env.example (+prod mirror)") is **NOT an ancestor of HEAD** (verified by `git merge-base --is-ancestor`) ⇒ **both kill-switches are UNDOCUMENTED in the shipped `.env.example` at this checkout** |
| `.env.example` / `config.yaml` carry the alias env? | No — only `SKILL_EVOLUTION_EMBEDDING_*` / `SKILL_EVOLUTION_*` model knobs (`.env.example:108-142`) |

---

### A2. GIT ARCHAEOLOGY — when and WHY was it disabled?

#### A2.1 The two switches were **born OFF** — neither was ever a working "on" state

| Switch | Introducing commit | Date | Author | Subject | Diff |
|---|---|---|---|---|---|
| **CAPTURE** | `b96c0d671cd8cfcf4ae6b268c40373d8d6edd778` | **2026-09-17 04:16:45 +0700** | `Councilor C2 <councilor@c2.local>` | `Add ENSEMBLE_SKILL_CAPTURE_ENABLED kill-switch (default OFF)` | 8 files, +1202/−2 — `daemon/services/skill_job_dispatcher.py` (+80), `daemon/services/skill_metrics_service.py` (+27), `daemon/tools/skill_evolution_tools.py` (+42), 5 test files incl. `tests/unit/test_skill_capture_killswitch.py` (+975) |
| **EVOLUTION** | `605f50f16dae4fab54757735936c86a9bd452dee` | **2026-10-22 21:09:17 +0000** | `giter <giter@ensemble.local>` | `feat(skills): master kill-switch + FIX abuse controls on the evolution admission lane` | 3 files, +274/−10 — `daemon/config.py` (+34), `daemon/manager.py` (+29), `daemon/services/skill_job_dispatcher.py` (+221) |

**Both are ancestors of HEAD** (`git merge-base --is-ancestor` → IN-HEAD for both). `git log -S'ENSEMBLE_SKILL_EVOLUTION_ENABLED' --oneline --all` returns **18** commits; `git log -S'ENSEMBLE_SKILL_CAPTURE_ENABLED' --oneline --all` returns **12**.

#### A2.2 The WHY — the 2026-10-21 incident and the confinement lane

**Trigger:** the skill-keeper incident of 2026-10-21 (10 runs, 17:12–18:02 UTC). From `.agents/shared/planning/skill-system-audit/HANDOVER.md:11`:

> "One flagged instance (`b7da3ed0`) **committed 10 files (9 daemon/ + 1 tests/, +476/−1) directly to `latest`** — commit `660e73453` — and another run (`1e3792d4`) edited `agents/wanderer/skill-set.yaml` via bash (auto_load true→false, version 1.0.0→1.0.1). skill-keeper is a DB-only agent by mandate; repo source mutation was enabled by tooling and restrained by prose only."

Four root-cause layers (`HANDOVER.md:15-18`): **L1** tool-allowlist over-grant (`bash` + `filesystem` on a DB-only agent); **L2** ungated mutation admission lane (`skill_fix` → `dispatch_fix` → `enqueue_evolution`, no gate/dedup/cooldown/approval); **L3** project-scoped skill search lacking an agent-ownership filter; **L4** self-propagating sweep (same skill re-FIXed 3× in 38 min).

**The explicit reason the evolution lane was born OFF** — `decisions.md:16-19`:
> "**Why OFF:** 1. The incident proved the lane is an instruction-injection surface into a live executor: direction text is verbatim filer free text that becomes the Tier-3 prompt … 2. Asymmetry precedent: the CAPTURED lane got default-OFF discipline on day one (2026-09-17); the strictly-more-dangerous mutation lane had no switch at all. … 3. Default OFF **is** the durable post-promote off-switch …"

**Capture was never re-enabled** — `HANDOVER.md:20`:
> "**Skill capture was NEVER on — disproven on 4 axes (report §6).** … (a) live daemon env dump contains zero occurrences of `ENSEMBLE_SKILL_CAPTURE_ENABLED`; (b) absent from all set-sites …; (c) restart sequencing rules it out; (d) code history — flag landed 2026-09-17 default-OFF, never substantively enabled. DB ground truth: 25 `skill_capture` jobs lifetime, ALL ≤ 2026-09-17, zero since."

#### A2.3 The confinement lane — commit chronology (all 2026-10-22, all `giter`)

| Time (UTC) | SHA | Subject | What it landed |
|---|---|---|---|
| 21:09:33 | `c4cf04220` | `fix(skills): truthful dispatch reporting on the skill_fix surface` | the "NOT dispatched" envelope |
| 21:09:45 | `3236d3ac5` | `test(skills): pin the evolution-lane gate, confinement allowlist, resolve guard` | the pin pack |
| **21:30:12** | **`f556e14e3`** | `fix(skills): gate the metric-scan fallback + honor explicit zero cooldown` | **closes the dispatcher-UNWIRED fallback bypass** (see §A8 row 4) |
| **22:18:10** | **`5df2c2827`** | `fix(skills): metric-scan fallback adopts the dispatcher's FIX abuse controls` | idempotency key + cooldown parity in the fallback |
| **22:18:10** | **`e293a9e5f`** | `feat(skills): execution-time lane gate + retry carry-through for Tier-3 jobs` | iteration-2 execution gate — **defective**: it did not terminally write the row (`decisions.md:33`) |
| 23:55:46 | `7f3218f40` | `docs(planning): decisions record — iteration-3 dispositions` | the decisions record |
| — | **`08287e469`** | `fix(jobs): terminally write refused skill_evolution rows — no head-of-line strand` | **iteration-3 fix for the F1 no-op**: the queued refusal now terminally writes |

Planning-doc histories:
- `.agents/shared/planning/skill-keeper-confinement/` → 4 commits: `dc3b11404` (initial) → `4255a06b9` (iter-1) → `95b16b265` (iter-2) → `7f3218f40` (iter-3). Only file: `decisions.md` (138 lines, 30 594 bytes).
- `.agents/shared/planning/skill-system-audit/` → **exactly 1 commit**: `4cf5ca81d` `docs(audit): land skill-keeper incident 2026-10-21 audit trail + cross-machine handover`. Files: `HANDOVER.md` (101 lines), `incident-2026-10-21.md` (142 lines).
- `.agents/shared/planning/skill-system-revival/` → **no commits** (new).

#### A2.4 `skill_injection` census — every `agents/*/meta.json`, current value

**Commit history** (`git log -S'"skill_injection": false' --oneline --all -- agents/`):
`cf72977e6` blueprinter has no dynamic skill injection · `35bfd245a` fix(tester): disable dynamic skill injection (skill_injection: false) — stop nudge-triggered cross-category skill loads · `b3f37a59c` agent-base-v2 / Phase B · `962364f03` fix: designer skill loading — own-skill must-load, dynamic injection off · `7fa5caa5a` disable skill injection for 2 facing user agent · `e56070de6` disable skill injection for devops · `ad1f408f3` quick update · `1d334cbdd` feat: add project-manager agent · `12378edb1` feat(watchover): tool call interception system.
`git log -S'skill_injection' --oneline --all -- agents/` also shows `454209555` "feat: enable skill_injection and dynamic-skill for tester agent" — the tester is currently **ON**, i.e. `35bfd245a` was later reversed.

**Current values (23 agents declare `skill_injection`):**

| Value | Count | Agents |
|---|---|---|
| `true` | **16** | approver, approver[v2], architect, blueprinter, critic, doc-maintainer, maintenancer, planner, planner[v2], reviewer, reviewer[v2], sketcher, tester, tidier, tidier[v2], worker |
| `false` | **8** | coder, designer, developer, developer[v2], devops, project-manager, wanderer, watcher |

**24 `agents/*/meta.json` files declare `skill_injection`** (16 true + 8 false).

**The only OTHER `skill_*` flag anywhere:** `agents/architect/meta.json` → `"skill_search_interval": 5`. No other `skill_*` key exists in any `agents/*/meta.json`.

---

### A3. PRIOR PLANNING DOCS — what they actually record

#### A3.1 `.agents/shared/planning/skill-keeper-confinement/decisions.md` (138 lines) — **the design of record for the confinement**

| Section | Line | Decisive content (quoted) |
|---|---|---|
| D1 | `:14` | "`SkillEvolutionConfig.evolution_lane_enabled = False` (default) + dedicated kill-switch env `ENSEMBLE_SKILL_EVOLUTION_ENABLED` (per-call resolution, env wins over config, invalid env value → `ValueError` fail-closed). Mirrors the capture-gate shape exactly … same no-op-returns-`None` admission contract." |
| D1 producer table | `:23-29` | 5 producers, **all YES (gated)** — `skill_fix` tool → "returns `None`; tool returns honest '⛔ NOT dispatched' envelope"; admin HTTP → "**503** with reason (was: fake 202 with `job_id: null`)"; trigger-row wired → gated; trigger-row **unwired fallback** → "**YES (gated since review iteration 1)** … consults the same kill-switch before the direct create"; feedback-filed fix → gated |
| D1 NOT gated | `:31` | "**NOT gated (deliberate):** `enqueue_analysis` …, `enqueue_metric_scan` …, `enqueue_capture` …, and the keeper's **direct execution tools** (`skill_evolve` / `skill_analyze` on an already-running keeper instance call `SkillEvolutionService.evolve_skill` inline — the job-level gate stops ADMISSION and queued-EXECUTION, but cannot reach an inline tool call on an instance that is already running)." |
| D1 queued-execution | `:33` | "**HISTORY CORRECTION (F8):** the iteration-2 commit e293a9e5f added the refusal but did NOT terminate the row … leaving the row as a PERMANENT head-of-line blocker (proved 2/2 council + real-PG16 tester drain: the second job ran 0 times). The e293a9e5f gate therefore must NOT be described as closing the execution gap; the terminal write (iteration 3) is the fix." |
| D1 F1-F4 | `:35-41` | the queued refusal is a single guarded UPDATE via `atomic_transition(from_status='queued', to_status='done', terminal_reason='lane_disabled')`; the RETURN VALUE IS CHECKED (`None` → ERROR log); a transition race falls back to `complete_job(FAILED)`; **no `failed_at` on the queued path** (so the retry engine cannot bounce it); F3 fail-closed (absent/raising probe ⇒ REFUSE) |
| D2 | `:47-49` | `idempotency_key = "skill_fix:<normalized_project>:<skill_id>"`; per-(project,skill) cooldown default 1800s; both **FIX-only** |
| D3 | `:58-59` | **New `tools.allow`:** `["read_file","list_directory","glob_files","grep_files","self","help","knowledge","dynamic-skill","skill-evolution","shared_meta_kv"]` · **New `tools.deny`:** `["bash","proc","service","write_file","edit_file","inner_soul"]` · meta version 0.1.0 → 0.2.0 |
| D3 correction | `:53-56`, `:68-72` | "**Execution is closed; READ reach is open.**" — absolute paths skip the workdir boundary (`daemon/tools/filesystem.py:207-208`) |
| D3 deferral | `:66` | HANDOVER rec 3 (widen `_check_prompt_artifact_write`) — **DEFERRED, not implemented** |
| D5 | `:87-93` | forced-resolve guard: `winner_id` while `comparisons < ab_sample_size` → `ValueError` (→ HTTP 400); residual: an unauthenticated admin endpoint can still force-resolve at exactly sample size |
| D6 | `:95-100` | deliberately NOT implemented: direction quarantine, P1 items, DB cleanup, **P3 master skill-system switch**, bank-clone provenance audit, soul.md-as-enforcement |
| D8 | `:112-138` | 11 deferred residuals, incl. #3 "Inline `skill_evolve` execution on a running keeper — not gated (D1 residual); folds into the P3 master-switch", #9 per-agent read scoping (`filesystem_scope`) **NOT built**, #10 idempotency-key colon-join aliasing (disclose-only), and the iteration-3 dispositions F1–F8 |

**Does the live code implement it? → YES, all of D1–D5 that is code.**
Verified: allow/deny lists match `agents/skill-keeper/meta.json:11-12` **verbatim** (version `0.2.0` at `:7`); the kill-switch gate exists at `skill_job_dispatcher.py:621-635`; the cooldown at `:641-657`; the idempotency key at `:669-671`; the fallback gate at `manager.py:6685-6710`; the terminal write at `job_processor.py:781-788`. **Not implemented:** D6 deferrals + D8 residuals #3/#9.

#### A3.2 `.agents/shared/planning/skill-system-audit/HANDOVER.md` (101 lines) — what the audit concluded

- §1 (`:11-20`): the incident, the four root-cause layers L1–L4, and the **capture-never-on disproof** (§A2.2).
- §2 (`:22-35`): DONE — forensics complete; commit `660e73453` **KEPT** (two independent lenses); the `agents/wanderer/skill-set.yaml` flip **REVERTED** (byte-identical to HEAD, blob `1cf0548e73693fa1a4bfb29078066b8af943eb74`); note the commit message's "all existing suites green" claim is **inaccurate** (1 commit-attributable failure + 1 pre-existing).
- §3 (`:37-53`): PENDING priority list. **P0 = the enforcement pair** (strip repo-mutation tools from the keeper allowlist; gate the `skill_fix → dispatch_fix` admission lane). **P1** = commit follow-ups. **P2** = gated DB cleanup + the two standing bypasses (explicit `load_skill` body-injection path; `DELETE /api/skill-bank/{item_id}` de-classification). **P3** = "one master skill-system switch covering capture + analysis + evolution lanes (§8 rec 6)" + bank-clone wave provenance audit + soul.md tightening "as defense-in-depth ONLY, not the enforcement layer".
- §4 (`:55-77`): environment topology — what travels via git vs what is **machine-local to the origin Mac** (the damaged `ensemble_prod` rows, the live daemon v0.26.2). **"Nothing in the DB has been cleaned — every row above is still in its damaged state pending explicit user go."**
- §5 (`:79-88`) — **binding user decisions**:
  > "| Skill capture | **Stays OFF** for other users until the system is improved — user's explicit intent |"
  > "| Surprise re-activation | Any future 'it's on again' surprise = **incident**, not a glitch |"
- §6 (`:90-97`): references; the forensic report wins over the handover on any discrepancy.

#### A3.3 `.agents/shared/context.md` (`:175-201`) — the recorded skill-system state

Six "Key state facts (pending disposition)" (`:179-198`): (1) unpushed commit `660e73453`; (2) uncommitted `agents/wanderer/skill-set.yaml` edit; (3) **"skill-keeper allowlist over-grant (bash + filesystem-write) — a DB-only agent holds full repo-write capability; soul.md prose prohibitions are NOT enforcement. Root cause L1 in the report."**; (4) **"skill_fix lane ungated — no kill-switch/dedup — skill_fix → dispatch_fix mints immediate Tier-3 mutation jobs with agent-authored free-text directions; no cooldown (same skill re-FIXed 3× in 38 min). Root cause L2."**; (5) **"Capture was NEVER re-enabled — user's re-activation hypothesis DISPROVEN"**; (6) "Burst runs wrote ZERO `skill_usage_records` rows".

**Correction to the commission's premise:** the **"`skill_fix x3 recorded but NOT dispatched`" note is NOT in `context.md`** — `grep -n "skill_fix" .agents/shared/context.md` returns only `:190` and `:195` (the two lines quoted above). The "x3" statement lives in the **v0.28.9 tester record** (§A5) and is quoted second-hand in a planning doc (§A5.2).

**Disposition (executed 2026-10-21/22), `context.md:200`:** "commit 660e73453 KEEP …; agents/wanderer/skill-set.yaml auto_load flip REVERTED via git restore … DB cleanup + fix sequencing deferred."

#### A3.4 `.agents/shared/planning/metrics-unification/` — 11 files (all **untracked** in this checkout)

`README.md` (8 lines) · `charting-engine-decision-independent.md` (197) · `charting-engine-decision.md` (41) · `fe-metrics-source-audit.excerpts.md` (524) · `fe-metrics-source-audit.md` (438) · `fe-ui-audit-and-resource-refactor.md` (346) · `live-capture-log.md` (225) · `metrics-inventory-backend-producers.md` (162) · `metrics-inventory-surfaces-fe-deps.md` (275) · `resource-refactor-architecture.md` (401) · `resource-tab-forensics.md` (212) · `screenshots/` (empty).

The decisive lines:
- `resource-refactor-architecture.md:110` — "| F1 | loop lag | wedge_capture (`w1.py:94-152`) | DARK `capture_off` — **FALSE-dark** (9 live series publish; `capture_active` never wired, `api.py:2528-2533` absent) | `stat`,`state` | chart |"
- `resource-refactor-architecture.md:111` — "| F3 | wedge captures | wedge_capture (`w1.py:157-189`) | DARK `capture_off` — **FALSE-dark** | `reason`,`outcome` | stat |"
- `resource-refactor-architecture.md:257` — "**F1/F3/F6 false-dark fixes** (R-i polarity, R-ii `capture_active` wiring) — already routed; prerequisite for 3 families joining Runtime/DB-Pool tabs. Not absorbed here."
- `resource-refactor-architecture.md:307` — "The *mechanisms* (capture_off, census-gated polarity, series_absent) are code-verified in the v0.28.9 tree."
- `resource-tab-forensics.md:142` — "F1 registered, **DARK · `capture_off`** — false-dark (9 live series publish simultaneously) | `capture_active` never wired (`daemon/api.py:2528-2533`)"
- `resource-tab-forensics.md:144` — "**Central families LIVE vs DARK (16 registered = 10 IN + 6 PARTIAL of a 26-family catalog):** **10 LIVE / 6 DARK** … DARK: F1, F3 (wedge counters, `capture_off`), F6 (PG census, `census_gated` — reason polarity inverted), F7 … F18 … F21 … LIVE: F0.3, F4, F5, F8, F9, F10, F11, F12, F13, F17."
- `resource-tab-forensics.md:198` — "🔴 **Three of sixteen families report DARK while publishing LIVE series** (F1: 9 series, F3, F6: 15) — `capture_active` never wired (`daemon/api.py:2528-2533`) + census `gated` polarity inverted (`daemon/services/metrics_census.py:323-335`); disclosed at `metrics-promote-disclosure.md:82-98`, routed R-i/R-ii, **not fixed as of `origin/latest`**."

#### A3.5 Every planning file under `.agents/shared/planning/` mentioning skill capture / evolution / keeper

`skill-keeper-confinement/` (decisions.md) · `skill-system-audit/` (HANDOVER.md, incident-2026-10-21.md) · `metrics-unification/` (11 files) · `skill-list-full-uuid/` (exists only at `origin/latest`) · `project-skill-loader/` (exists only at `origin/latest`) · `tester-skill-dispatch-update/` (`phase3-skill-templates.md:50` — about the tester's own auto-load template, unrelated to the capture lane) · `central-metrics-v1/` (exists only at `origin/latest`). Grep for "skill capture|skill evolution|skill-keeper|capture_enabled|evolution_lane" across `.agents/shared/planning/` hits the two skill dirs + `metrics-unification/` + `tester-skill-dispatch-update/`.

---

### A4. v0.28.6 DISCLOSURE — source text for the F1/F3 false-dark + inverted census polarity + R-i/R-ii routing

**Primary source (verbatim), `origin/latest:docs/architecture/metrics-promote-disclosure.md:82-98`:**

> "**THREE OF THE SIXTEEN FAMILIES REPORT `dark` WHILE PUBLISHING LIVE SERIES — AND THAT IS A PRODUCTION DEFECT, NOT A DEV ARTEFACT.** **F1** (9 live series in the dev observation) and **F3** (its `capture_off` twin — the wedge capture publishes in production even though the door calls the family dark) and **F6** (15 live series) all report `dark` while their series are LIVE. The causes are the two out-of-lane defects routed as R-i/R-ii: the provider's `capture_active` is **never wired** (`daemon/api.py:2528-2533`), so the `capture_off` reason is unconditional; and the census's public `gated` property is **INVERTED** (`daemon/services/metrics_census.py:323-335` returns "enabled", the door reads it as "gated off"), so the `census_gated` reason fires exactly when the census is running and publishing. **The door's availability verdict is WRONG for these three wherever the capture and the census run** — the same defect is visible in dev only because dev has no live capture and no wired `capture_active`. **The code fix is correctly routed OUT of this lane** (R-i: wire `capture_active` at the `daemon/api.py` construction site — a Tier-2 mechanical file; R-ii: the census's `gated` polarity — the metrics-collection lane's surface). **Nothing in this lane's code changes for them**, and the disclosure states the defect plainly rather than smoothing it away: a reader of the `/families` body must know that `dark` + live series is possible, and for these three it is the NORM."

**Also verbatim, same file `:69-80`** (the six dark families and their named causes) and `:127` (the query-API posture: "auth-gated only (`maintenance:read`); **503 `AUTH_NOT_CONFIGURED`** fail-closed **before any provider read**; the door never consults the switch").

**Version attribution:** `origin/latest:CHANGELOG.md:233-240` — `## [0.28.6] — 2026-11-02` / "### Added — central metrics system v1, backend + frontend (`feature/central-metrics-v1`, merge `d91fa6291`)". ⚠️ **The `[0.28.6]` changelog entry itself does NOT contain the F1/F3/F6 disclosure text** — it is a feature summary; the disclosure text lives in `docs/architecture/metrics-promote-disclosure.md` (whose own landing commits are `2f18d7b6c` → `e1a750914` → `cafe77951` → `c3a003668` → `e529d5823`).

#### A4.1 Verify: does `capture_active` / `capture_off` appear NOWHERE in `daemon/` or `docs/`?

**At the checked-out tree (HEAD): CONFIRMED.** Repo-wide grep for `capture_active|capture_off` (excluding `.git`) yields exactly:
- `.agents/shared/planning/metrics-unification/resource-refactor-architecture.md:110, :111, :257, :307`
- `.agents/shared/planning/metrics-unification/resource-tab-forensics.md:142, :198`
- `tests/unit/test_snapshot_executor.py:231` — `def test_successful_capture_active_row_and_r6a_exclusion(` — an **unrelated word coincidence**, not the metric.

**Zero hits in `daemon/`, zero hits in `docs/`.**

⚠️ **REFUTE-with-nuance at `origin/latest`:** the strings **DO** exist in `origin/latest:daemon/services/metrics_query/provider.py:67, :75, :147` and `availability.py:62, :94` — because the metrics-family availability machinery landed **after** the planning docs were written. The planning-doc claim is therefore **true of the tree it was written against (v0.28.9) and of this checkout (v0.27.8-era), and false of `origin/latest` today**. The downstream writer must not restate it as an unqualified present-tense fact.

#### A4.2 Verify: do the cited sites hold at the current tree?

| Cited anchor | Holds at HEAD (checked-out tree)? | What is actually there |
|---|---|---|
| `daemon/api.py:2528-2533` ("constructs the provider without `capture_active`") | ❌ **NO — stale anchor** | `daemon/api.py:2528-2532` is the **OAuth2 / AuthService boot code** (`oauth_client=app.state.oauth_client, bind_host=…, bind_port=…`); `:2533` is the `[Auth] AuthService ready` log line. No metrics provider exists at HEAD at all. |
| `daemon/services/metrics_census.py:323-335` ("census `gated` property returns 'enabled'") | ❌ **NO — the file does not exist at HEAD** (`ls` → `No such file or directory`) | The file **does** exist at `origin/latest`, and **`:323-335` is exactly the `gated` property** — so the anchor is **correct for `origin/latest` and stale for this checkout**. See §A6.3 for the confirmed code-level inversion. |

**Both planning-doc anchors are therefore stale relative to the checked-out tree and (for `capture_active`) relative to `origin/latest` too.**

---

### A5. v0.28.9 DISCLOSURE — "skill_fix x3 recorded but NOT dispatched; two new skills …"

**Primary source (verbatim), `origin/latest:.agents/tester/RESULTS/2026-11-03-v0289-staged-verification.md:27`:**

> "- `skill_fix` recorded (evolution lane disabled, not dispatched): 08580cdb (stale "tag local-only" gate — would false-fail pushed-tag releases), b379be40 (bare-sha256 manifest values + missing migration census), 2c4b7a0e (FE-bundle section). Two new skills created: c78c5cf1 (migration census), 085b8985 (arm-time pin verification from the live daemon)."

**This is the source of every element of the commission's phrasing:**
- "**x3**" = the three skill ids `08580cdb`, `b379be40`, `2c4b7a0e`;
- "**recorded but NOT dispatched**" = the parenthetical "(evolution lane disabled, not dispatched)";
- "**two new skills**" = "Two new skills created: `c78c5cf1` …, `085b8985` …".
- ⚠️ **Word-level correction:** the source says "**created**", not "**captured**". The commission's phrase "two new skills captured" is a paraphrase — the doc's verb is *created*. (The capture lane was OFF, so "captured" would in fact be the wrong word.)

**Secondary citation (the only place the literal phrase "skill_fix x3" appears in the repo), `origin/latest:.agents/shared/planning/skill-list-full-uuid/architecture-ruling.md:57`:**

> "| (f) | skill_fix `pf-` refusal gap | skill_tools.py:1211 (prefix test on `ADDRESS_PREFIX` only) → pseudo-id falls through to `dispatch_fix` as a bogus DB id | **OUT — DEFER** (skill-evolution/fix-dispatch lane) | Routing gap, not id emission. **Currently harmless: the evolution lane is disabled (v0.28.9 record — "skill_fix x3 recorded but NOT dispatched")**, and after (d)/(e) land, agents stop seeing pseudo-ids at all, making the gap LESS reachable, not more. …"

**Version attribution:** `origin/latest:CHANGELOG.md:195-203` — `## [0.28.9] — 2026-11-03` / "### Added — the agent tool surface over the central metrics system: the `metrics-read` privileged tool category … item #1 central-metrics-v1 landed v0.28.6". Commit `d77e2645c` "docs(changelog): [0.28.9] section — metrics-agent-tools (metrics-read tool category)"; release commit `7fcf1bd71` "chore(release): bump version to 0.28.9"; staged-verification evidence `39671cb80` / merge `c4161335b`.

---

### A6. METRICS INVENTORY (backend)

⚠️ **All of §A6 is `origin/latest`-only.** The checked-out tree (HEAD `013895287`) has **no central metrics subsystem**: `daemon/services/metrics_query/`, `daemon/services/metrics_census.py`, `daemon/services/metrics_listener.py`, `daemon/services/metrics_gate.py`, `daemon/routers/metrics.py` are all **absent** (verified by `ls`).

#### A6.1 How many families, which ones, and which are skill-related

| Question | Answer | Evidence (`origin/latest`) |
|---|---|---|
| Families registered by the central-metrics lane | **17** — `F0.3, F1, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F17, F18, F21, F23` | `daemon/services/metrics_query/wiring/adjudication.py:90-108` (`REGISTERED_FAMILIES`) |
| …but the promote documents say **16** | The `[0.28.6]`/disclosure text says "**16 of the 26** daemon-origin families (10 full + 6 partial)" and "REGISTERED_FAMILIES (16)"; the c1 runtime observation table lists exactly 16 (no F23). **F23 was added after the promote** ⇒ the register has drifted 16 → 17. | `origin/latest:docs/architecture/metrics-promote-disclosure.md:45-46`, `:131`; `origin/latest:.agents/shared/planning/central-metrics-v1/evidence/c1-runtime-observation.md:119-136` |
| Catalog size | **26-family catalog** (F0.1–F22 with decimal sub-families); route census 108→111; mount census 37; **class-manifest family count 33** | disclosure `:45-46`, `:130` |
| Family roster (the code's own list) | `F1, F4, F5, F6, F9, F10, F11, F13, F7, F8, F18, F21, F23` + `DROPPED_FAMILIES = ("F2",)` | `origin/latest:daemon/services/metrics_query/roster.py:71-211, :235` |
| **Which families are skill-related?** | **NONE.** `grep -in "skill" adjudication.py` → **0 hits**. The skill metrics live in a **separate subsystem**: `daemon/services/skill_metrics_service.py` + `daemon/repositories/skill/`, served by `/api/skills/*`, never by `/api/metrics/*`. | verified |
| Where each family is computed | F0.1 `daemon/services/metrics_listener.py`; F0.2/F0.4/F7 `daemon/checkpoint_metrics.py`; F0.3 `daemon/services/resource_monitor/flusher.py:161 + sampler.py`; **F1 `daemon/services/wedge_capture/lag_probe.py:107-116, :185`**; **F3 `daemon/services/wedge_capture/triggers.py:202/:237/:281`**; **F6 `daemon/services/metrics_census.py`**; F9 readiness; F10 job-queue depth; F12 sweep counters; F13 instance census; F17 MCP health; F18 MCPS-gateway sessions; F21 journal ops | `adjudication.py:53-62` (the "F-n → producer" table) |
| Wired to an API? | `/api/metrics/families`, `/api/metrics/snapshot`, `/api/metrics/series` (all `maintenance:read` / `metrics:read`) serve the central registry. `/api/resources/*` serves the **resource_monitor T1/T2 spine — a different substrate**. `/api/skills/{id}/metrics` serves the skill counters — a third substrate. | disclosure `:127`, `:130`; `origin/latest:daemon/routers/metrics.py`; `daemon/routers/resources.py`; `daemon/routers/skills.py:1127` |

#### A6.2 Which metrics are WRONG / STALE / FALSE-DARK — verified

**Measured (fenced-dev observation), `origin/latest:.agents/shared/planning/central-metrics-v1/evidence/c1-runtime-observation.md:119-142`:**

| family | verdict | live series | availability | why (row text) |
|---|---|---|---|---|
| F0.3 | IN | 6 | **live** | the resource spine (WS-A) |
| **F1** | IN | **9** | **dark · `capture_off`** | see §4.2 |
| **F3** | IN | **0** | **dark · `capture_off`** | see §4.2 |
| F4 | IN | 5 | live | pool native quantities |
| F5 | IN | 3 | live | pool derived quantities |
| **F6** | IN | **15** | **dark · `census_gated`** | see §4.1 — **the reason is INVERTED** |
| F7 | PARTIAL | 0 | dark · `series_absent` | see §4.3 — the observation **did** fire |
| F8 | PARTIAL | 4 | live | checkpoint byte gauges |
| F9 | IN | 13 | live | readiness family |
| F10 | IN | 3 | live | job-queue depth |
| F11 | PARTIAL | 15 | live | worker-pool gauges |
| F12 | IN | 3 | live | sweep counters (3 of 6 declared lanes) |
| F13 | PARTIAL | 11 | live | instance census |
| F17 | IN | 12 | live | MCP health family |
| F18 | PARTIAL | 0 | dark · `series_absent` | MCPS-gateway store empty in dev |
| F21 | PARTIAL | 0 | dark · `series_absent` | journal family needs an install dir; dev has none by design |

> "**RE-DERIVED COUNTS (the honest measurement, D-a):** **LIVE: 10 of the 16** admitted families. **DARK: 6 of the 16** — each with an explicit reason. **TOTAL live series across the lane families: 99.**"

**Verification of the prior exploration's claim ("3 of 16 families report DARK while publishing live series (F1 with 9 series, F3+F6 with 15 series)"): ⚠️ PARTIALLY CONFIRMED, with two corrections.**
1. **F1 (9 live series, dark·`capture_off`) — CONFIRMED false-dark.**
2. **F6 (15 live series, dark·`census_gated`) — CONFIRMED false-dark.**
3. **F3 — the "15" belongs to F6, not to "F3+F6"**; in the dev observation **F3 had 0 live series**, so for F3 the `capture_off` verdict was *correct in dev*. The disclosure claims F3 "publishes in production even though the door calls the family dark" — **production behaviour is not verifiable from code alone** (no live contact in this lane).
4. The **family count is 16 at the promote and 17 in current `origin/latest` code** (`adjudication.py:90-108`).

**Planning-time expected series bands** (`origin/latest:ademon/services/metrics_query/wiring/adjudication.py:158-174`): `F0.3:8, F1:9, F3:6, F4:14, F5:4, F6:15, F7:20, F8:4, F9:53, F10:6, F11:16, F12:117, F13:19, F17:40, F18:5, F21:32` (+`F23` beyond the read window). Note these are **planning bands, not measured values** — e.g. F12 band 117 vs measured 3; F9 band 53 vs measured 13.

#### A6.3 The EXACT gating code that produces the DARK-while-publishing contradiction (this is the real answer to the "find the REAL file" ask)

**R-i — `capture_active` never wired ⇒ F1 and F3 are dark unconditionally:**
1. `origin/latest:daemon/api.py:2571-2576` constructs the provider:
   ```
   _metrics_query_provider = RegistryMetricsQueryProvider(
       census=app.state.metrics_census,
       ring=_metrics_ring,
       listener_enabled=app.state.metrics_listener is not None,
       sampler_interval_s=_metrics_sampler_interval_s,
   )
   ```
   → **no `capture_active=` argument is passed.**
2. `origin/latest:daemon/services/metrics_query/provider.py:67` — the ctor default is `capture_active: bool = False`; `:75` stores `self._capture_active = bool(capture_active)`; `:147` publishes `capture_active=self._capture_active` into the facts.
3. `origin/latest:daemon/services/metrics_query/availability.py:93-95` —
   ```
   elif source_idiom == SOURCE_IDIOM_WEDGE_CAPTURE:
       if not facts.capture_active:
           return {"state": "dark", "reason": "capture_off"}
   ```
⇒ **Every wedge-capture-idiom family (F1, F3) reports `dark / capture_off` forever**, regardless of whether it is publishing.

**R-ii — the census `gated` polarity is inverted (a DOUBLE inversion), verified end-to-end:**
1. `origin/latest:daemon/services/metrics_census.py:322-335` — the property is documented as *"Whether the census sampler's DB work is currently **gated OFF**"* but its body is `return self._gate()`.
2. `origin/latest:daemon/services/metrics_census.py:347-355` — `_gate()` returns **`metrics_gate.metrics_enabled()`**, i.e. **True when the census is ENABLED**. ⇒ the property named `gated` reports the *opposite* of its own docstring.
3. `origin/latest:daemon/services/metrics_query/provider.py:110-122` — `census_enabled` = `not bool(self._census.gated)` ⇒ **False while the census is actually running/enabled**.
4. `origin/latest:daemon/services/metrics_query/provider.py:145` — `census_gated=not self.census_enabled` ⇒ **True while the census is running and publishing**.
5. `origin/latest:daemon/services/metrics_query/availability.py:89-92` — `if facts.census_gated: return {"state": "dark", "reason": "census_gated"}` ⇒ **F6 reports dark exactly while the census is alive and publishing its 15 series.**
6. The code itself documents the defect (note the **label mismatch**): `availability.py:83-88` — *"The POLARITY of `census.gated` itself is R-i — an OUT-OF-LANE defect in daemon/services/metrics_census.py (the property returns "enabled" where its docstring says "gated off")"* — whereas the disclosure assigns **R-i = wiring `capture_active`** and **R-ii = the census polarity**. The *mechanism* is confirmed; the *labels* are swapped between the code comment and the disclosure.

**Availability evaluation order** (`availability.py:76-98`): for the census idiom, `census_not_running` is checked FIRST, then `census_gated`; then `live_series_count <= 0` → `series_absent`; otherwise `live`.

**✅ The wedge-capture subsystem is NOT the metrics-census gate** (the RAG-supplement trap): the wedge-capture subsystem (`/api/wedge-captures`, `daemon/services/wedge_capture/`, `ENSEMBLE_WEDGE_CAPTURE_ENABLED`) is the wedged-run evidence store. It is the **source idiom** of F1/F3 (`availability.py:45-46` `SOURCE_IDIOM_WEDGE_CAPTURE`), *not* the census gate. The census gate is `daemon/services/metrics_census.py` (absent at HEAD, present at `origin/latest`).

---

### A7. FE SKILLS PAGE

#### A7.1 What exists in the checked-out tree

| Item | Path | Notes |
|---|---|---|
| Skills page | `frontend/src/app/pages/skills/skills.component.ts` + `.html` + `.scss` | |
| Skill detail page | `frontend/src/app/pages/skills/skill-detail/skill-detail.component.ts` | |
| Skill triggers page | `frontend/src/app/pages/skills/skill-triggers/` | |
| Usage table component | `frontend/src/app/components/skill-usage-table/skill-usage-table.component.ts` | |
| A/B dashboard component | `frontend/src/app/components/ab-test-dashboard/ab-test-dashboard.component.ts` | |
| Service | `frontend/src/app/services/skill.service.ts` (`API_BASE = '/api/skills'` at `:86`) | |
| Model | `frontend/src/app/models/skill.model.ts` | |

**Endpoints the Skills page calls** (`skill.service.ts`): `GET /api/skills` `:130` · `GET /api/skills/{id}` `:158` · `POST /api/skills` `:184` · `PUT /api/skills/{id}` `:219` · `DELETE /api/skills/{id}` `:254` · `POST /api/skills/{id}/deactivate` `:285` · `POST /api/skills/search` `:334` · **`GET /api/skills/{id}/metrics` `:370`** · `GET /api/skills/{id}/lineage` `:397` · `POST /api/skills/{id}/feedback` `:449` · **`POST /api/skills/{id}/fix` `:487`** · `POST /api/skills/{id}/ab-test/resolve` `:520` · `POST /api/skills/{id}/share` `:562` · **`GET /api/skills/{id}/usage-records` `:639`** · **`GET /api/skills/{id}/ab-test/stats` `:670`** · `GET/POST /api/skills/triggers` `:716`/`:747` · `PUT/DELETE /api/skills/triggers/{id}` `:783`/`:813`.

Backend counterparts: `daemon/routers/skills.py` — `GET ""` `:522`, `POST ""` `:590`, `/triggers` `:627`-`:738`, `/{skill_id}` `:763`, `/{skill_id}/view` `:864`, `PUT/DELETE` `:899`/`:947`, `/deactivate` `:983`, `/search` `:1033`, `/lineage` `:1063`, **`/metrics` `:1127`**, `/usage` `:1162`, `/usage-records` `:1192`, `/feedback` `:1266`, **`/fix` `:1330`**, `/ab-test` `:1411`, `/ab-test/stats` `:1456`, `/ab-test/resolve` `:1525`, `/share` `:1603`.

#### A7.2 Which metrics the Skills page renders — and the verdict

**The Skills page renders NO F-family metric.** It renders skill-domain counters from `SkillMetricsService.get_skill_stats` (`daemon/routers/skills.py:1127-1159` → `daemon/services/skill_metrics_service.py:1194-1231`) and A/B statistics (`/ab-test/stats` `:1456`).

⇒ **The F1/F3/F6 false-dark values do NOT reach the Skills page.** The false-dark families render on the **Metrics** page — which exists **only at `origin/latest`**:
- `origin/latest:frontend/src/app/services/metrics.service.ts:17-18` — `GET /api/metrics/families`, `GET /api/metrics/snapshot`, `GET /api/metrics/series`.
- `origin/latest:frontend/src/app/pages/metrics/tabs.manifest.ts:251-266` — the **Runtime** tab binds `familyIds: ['F9','F1','F3']` with the comment *"F9 (readiness composite) + [F1, F3] (the wedge-capture families — bound NOW, **dark until R-ii lands**)"*; `:267-279` — the **DB Pool** tab binds `['F4','F5','F6']` with *"F6 (the PG census — bound NOW, **dark until R-i**)"*.

**What a user SEES that looks "weird"** (Metrics page, `origin/latest`):
- The **Runtime** tab shows the **F1 (loop lag)** and **F3 (wedge captures)** panels as **dark**, with the reason text *"the wedge capture is not active"* — `origin/latest:frontend/src/app/models/metrics.model.ts:303-304` (`case 'capture_off': return 'the wedge capture is not active';`) — **even though F1 is publishing 9 live series and F3 publishes in production.**
- The **DB Pool** tab shows **F6 (PG census)** as dark with *"the census sampler is gated"* (`:299-300`) **while it publishes 15 live series**.
- The FE copy is honest-but-misleading: the disclosure mandates the panel copy **"no metrics available yet"** (never "metrics disabled") — `origin/latest:docs/architecture/metrics-promote-disclosure.md:112`; the assertion lives at `origin/latest:frontend/src/app/pages/metrics/metrics.component.spec.ts:310`.
- **On the Skills page itself the "weird" thing is different and is NOT the F-family defect:** the A/B dashboard renders `comparisons` against `sample_size` and has explicit states *"collecting"* (`comparisons === 0`) and *"needs_more"* (`needs_more_data === true`) — `origin/latest/frontend/.../ab-test-dashboard.component.ts:47-51, :171-181`. Combined with the counters' bump sites (`daemon/services/skill_metrics_service.py:888` `total_selections`, `:892` `total_completions` "on success") and the A/B resolution refusal while `comparisons < ab_sample_size` (`daemon/services/skill_evolution_service.py:1325-1428`), a skill whose completions never record leaves the A/B card **stuck on "collecting" / "needs more data"** and the success rate on **0**. *(The "zero-completions" artifact itself is a runtime observation carried from the prior lane — see §C.)*

#### A7.3 The auth asymmetry — code-level gating verified; runtime claim NOT

| Claim | Code-level verdict | Evidence |
|---|---|---|
| `/api/metrics/*` returns **503 `AUTH_NOT_CONFIGURED`** when auth is OFF, **before any provider read** | ✅ **CONFIRMED** (at `origin/latest`) | `origin/latest:daemon/routers/metrics.py:70` (module docstring), `:121` (`_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"`), `:150` (raise site), `:185` (docstring "503 AUTH_NOT_CONFIGURED when auth is OFF"); `origin/latest:daemon/api.py:5515`, `:5573`, `:5600` ("AUTH_NOT_CONFIGURED gate before any read"). Same fail-closed idiom in `daemon/routers/custom_agents.py:168/207/219`. |
| `/api/resources/*` returns 200 open | ✅ **CONFIRMED at the checked-out tree**: `daemon/routers/resources.py` contains **no** auth gate at all — `grep -n "auth\|AUTH\|require\|Depends" daemon/routers/resources.py` → **0 hits**. | `daemon/routers/resources.py` (checked-out HEAD) |
| "in prod" | 🔴 **NOT VERIFIABLE FROM CODE ALONE** — it is a runtime claim requiring a live probe against the prod daemon (auth configuration state, the auth middleware path, and the deployed binary). This lane made **no daemon contact** (read-only constraint). | — |

---

### A8. Producer enumeration (SUPPLEMENT item 2) — every producer of a capture/evolution job row, GATED / UNGATED

| # | Producer | Path (current-tree file:line) | Gate | Verdict while the switches are OFF | Terminal-write invariant |
|---|---|---|---|---|---|
| 1 | `skill_fix` tool filing | `daemon/tools/skill_tools.py:489-612` → `dispatch_fix` `:517` → `daemon/services/skill_job_dispatcher.py:771-796` → `enqueue_evolution` `:559-679` → kill-switch `:621-635` | **GATED** | **Not reachable**: `enqueue_evolution` returns `None` (`:635`) with no job-service call; the tool prints "⛔ NOT dispatched: the skill-evolution admission lane is disabled (kill-switch ENSEMBLE_SKILL_EVOLUTION_ENABLED off, or the per-skill fix cooldown is active). NO skill-keeper job was created — this message is the only record of the request." (`skill_tools.py:592-599`) | n/a (no row) |
| 2 | Admin `POST /api/skills/{id}/fix` | `daemon/routers/skills.py:1330-1398` → `dispatch_fix` `:1368` | **GATED** | **Not reachable**: HTTP **503** with the reason body (`:1380-1398`) — "no job was created. While the lane is off it governs BOTH admission … AND queued-execution" | n/a (no row) |
| 3 | Trigger-row `evolve_fix` — **WIRED** path | `daemon/manager.py:6507-6508` (doc), `:6622-6624` (`job_type = "skill_evolution"`) → `enqueue_evolution` | **GATED** | **Not reachable** (gate at `skill_job_dispatcher.py:621-635`) | n/a |
| 4 | Trigger-row `evolve_fix` — **DISPATCHER-UNWIRED fallback** (direct `job_repo.create`) | `daemon/manager.py:6665-6710` | **GATED since review iteration 1** | **Not reachable.** The fallback consults the *same* switch before minting: `manager.py:6685-6698` (`is_evolution_lane_enabled(_lane_default)`) and refuses with an INFO log at `:6703-6710`. **Fix SHA: `f556e14e3` (2026-10-22 21:30:12, "gate the metric-scan fallback + honor explicit zero cooldown")**, hardened by `5df2c2827` (22:18:10, "metric-scan fallback adopts the dispatcher's FIX abuse controls" — idempotency key + cooldown parity, `decisions.md:119`). | n/a |
| 5 | Post-execution `skill_feedback` file path | `daemon/tools/skill_tools.py:807-...` | **NOT a job producer** | `skill_feedback` **records feedback only** — it stamps `feedback_applied`/`feedback_note` on a usage row (`skill_metrics_service.py:937-959`); **there is no dispatch call in its body** (grep for `dispatch` in the body returns only a docstring mention at `skill_tools.py:966`). The 2026-10-21 19:31:18Z producer was an **agent** that then called `skill_fix` → that is producer #1, so it inherits #1's gate (`decisions.md:29`). | n/a |
| 6 | **Queued-row EXECUTION** | `daemon/services/job_processor.py:713-736` (probe, fail-closed), `:738-818` (`_terminally_refuse_skill_evolution_job`), `:781-788` (the queued terminal write), `:820-846` (active refusal), `:1058-1077` (orphan-resume hoist above both re-spawn branches) | **GATED at execution** | **Refused — no mutation performed.** `job_processor.py:769-772`: "Skill-evolution lane disabled — job refused at execution time (…; no mutation performed)." | ✅ **HOLDS.** Queued rows take `atomic_transition(from_status='queued', to_status='done', terminal_reason='lane_disabled')` (`:781-788`); the return value is **checked** — `None` → ERROR log, never a silent no-op (`:803-813`); a transition race falls back to `complete_job(FAILED)` (`:789-802`). No `failed_at` is stamped on the queued path, so the retry engine cannot bounce it (`decisions.md:37`). The **iteration-2 commit `e293a9e5f` was defective here** (no terminal write → permanent head-of-line blocker, `decisions.md:33`); the fix is **`08287e469`** "fix(jobs): terminally write refused skill_evolution rows — no head-of-line strand". |
| 7 | `retry_job` replay | `JobQueueService.retry_job` — **no route at HEAD**: `grep -rn "def retry_job\|retry_job" daemon/routers/jobs_crud.py` → **0 hits** | **GATED (via #6)** | `decisions.md:123`: "**`JobQueueService.retry_job` now carries `job_type` + `idempotency_key`** (a retried Tier-3 job used to degrade into a generic `task` job that bypassed the execution gate)." A retried Tier-3 row keeps its `job_type`, so the execution gate refuses it again. | ✅ **HOLDS** — queued refusal has no `failed_at` ⇒ not retryable; the ACTIVE refusal path stamps `failed_at` and could bounce **once** to queued, where the queued gate terminates it with no marker (`decisions.md:39`). |
| 8 | **Inline `skill_evolve` on an already-running keeper** | `daemon/tools/skill_evolution_tools.py` (no lane check anywhere in the module) → `SkillEvolutionService.evolve_skill` | **❌ UNGATED (disclosed residual)** | **REACHABLE while the switches are OFF** if a skill-keeper instance is already running and calls the inline tool. Documented at `daemon/config.py:4186-4188`, `decisions.md:31`, `decisions.md:116` ("folds into the P3 master-switch"). | n/a (no queue row) |
| 9 | CAPTURE producers — (a) post-task eligibility | `daemon/services/skill_metrics_service.py:595-596` (Gate 0) → `check_and_capture` → `enqueue_capture` | **GATED** (capture switch) | Not reachable; the trigger is skipped entirely (no metadata lookup, no DB read, no evolution-service call) | n/a |
| | (b) dispatcher capture admission | `daemon/services/skill_job_dispatcher.py:720-727` | **GATED** | Not reachable — `logger.debug("…kill-switch OFF… No-op")`, returns `None` | n/a |
| | (c) `skill_execute_capture` **tool** | `daemon/tools/skill_evolution_tools.py:417-419` (`if not _resolve_capture_enabled(): return {"skipped": True, …}`) | **GATED** | Not reachable — the tool returns a `skipped` envelope and does not invoke the service | n/a |
| 10 | Tier-2 `skill_analysis` / `skill_metric_scan` rows | `skill_job_dispatcher.py:514-557`, `:739-767` | **NOT gated (by design)** | These continue to run while the mutation lane is off — deliberate (`decisions.md:31`, "the audit duty stays alive") | n/a |

**Summary of §A8:** with both switches OFF, **every job-row producer of a Tier-3 evolution row is gated (7 of 7)**, and the one reachable mutation path is the **inline `skill_evolve` tool on an already-running keeper** — the disclosed residual. The execution-time refusal **does** terminally write, so it does not strand the queue head.

---

### A9. Re-enable precondition anchors (SUPPLEMENT item 2, third bullet)

**What the code proves:**
1. **Per-call `os.environ` resolution, no caching** — `daemon/services/skill_job_dispatcher.py:104-117` reads `os.environ.get(env_name)` on **every** call; the docstring at `:68-71` states the intent: "Per-call resolution (vs a cached `SkillEvolutionConfig` knob) keeps the kill-switch live across hot-reload and operator env flips". ⇒ In a running source-tree daemon, flipping the env flips behaviour without a code change.
2. **The switches live in the daemon process's own environment**, so an edit to `.env` alone does nothing until the daemon re-reads its environment — i.e. a **restart** is required for a *source-tree* daemon, and for the **frozen PyInstaller release binary** the *code* itself is also frozen per release directory (`releases/<ver>/`), so the switch semantics only change after a **promote + restart**.
3. **The load-time loud check** — `_log_invalid_switch_env_at_load()` (`:120-147`) only **logs an ERROR** for an unrecognised value; it never raises. So a typo'd switch value is silent until first use.
4. **The one live-acting interim brake for a job-dispatching lane** is the **queue pause**: the dispatcher routes every skill job to `PARALLEL_QUEUE_NAME = "system_parallel_queue"` (`skill_job_dispatcher.py:286-289`), and `job_queues.is_paused` is a **durable DB flag** (restart-safe) that stops dispatch at the queue level rather than at the switch level. (Its current value is runtime state — the project critical note records this queue as `is_paused=true` since 2026-10-22 19:54:26Z; **not verifiable from code**.)
5. **The keeper-side confinement is a separate, already-landed brake**: `agents/skill-keeper/meta.json:11-12` denies `bash`, `proc`, `service`, `write_file`, `edit_file`, `inner_soul` — i.e. even with the evolution lane ON, this agent cannot mutate the repo through its tool surface (`decisions.md:58-59, :62-65`). Enforcement is at `resolve_tool_filter` (`daemon/tools/instance.py`, cited at `decisions.md:62`), deny-after-allow.

**Re-enable preconditions this lane supports (evidence-backed):**
- Turning the lane ON requires **both** the canonical env `ENSEMBLE_SKILL_EVOLUTION_ENABLED` **and** a daemon restart (env read per-call, but the environment is fixed at process start) **and**, if the change is to reach a frozen release, a **promote** of a build that contains the intended code.
- **Capturing** additionally requires `ENSEMBLE_SKILL_CAPTURE_ENABLED` **and** an agent whose `skill_injection` is `true` (Gate 3) — 16 of 24 agents qualify (§A2.4).
- The user's standing intent governs the decision (`HANDOVER.md:86`): **"Skill capture — Stays OFF for other users until the system is improved — user's explicit intent"**, and `:87` "Any future 'it's on again' surprise = **incident**, not a glitch".

---

## (b) RAW EVIDENCE — verbatim code / doc extracts

*(All extracts are from the checked-out tree unless prefixed `origin/latest:`.)*

**`daemon/services/skill_job_dispatcher.py:75-117`**
```python
# ── Shared switch token sets (2026-10-22 review minor (c)) ───────────────
# The CAPTURE switch and the EVOLUTION switch MUST accept exactly the
# same spellings — one parser, one token set, no drift. ...
_SWITCH_ON_TOKENS = frozenset({"1", "true", "yes", "on"})
_SWITCH_OFF_TOKENS = frozenset({"0", "false", "no", "off"})


def _resolve_switch(env_name: str, configured_default: bool = False) -> bool:
    ...
    raw = os.environ.get(env_name)
    if raw is None:
        return configured_default
    val = raw.strip().lower()
    if val == "":
        return configured_default
    if val in _SWITCH_OFF_TOKENS:
        return False
    if val in _SWITCH_ON_TOKENS:
        return True
    raise ValueError(
        f"Invalid value for {env_name}: {raw!r} "
        f"(expected 0/1/true/false/yes/no/on/off; unset/empty = default)"
    )
```

**`daemon/services/skill_job_dispatcher.py:615-635`** (the evolution admission gate)
```python
        # ── 1. Kill-switch gate ──────────────────────────────────
        ...
        if not _resolve_evolution_enabled(self._evolution_lane_enabled):
            logger.info(
                "SkillJobDispatcher.enqueue_evolution: evolution lane "
                "gate OFF (env %s unset-or-off, configured default=%s). "
                "No-op — no skill_evolution job admitted for skill %s.", ...)
            return None
```

**`daemon/services/skill_job_dispatcher.py:714-727`** (the capture admission gate)
```python
        # ── Kill-switch gate ─────────────────────────────────────
        ...
        if not _resolve_capture_enabled():
            logger.debug("SkillJobDispatcher.enqueue_capture: kill-switch OFF ...")
            return None
```

**`daemon/config.py:6570-6584`** (the YAML-beats-alias-env trap mechanism)
```python
    if "skill_evolution" in processed_config:
        # Drop keys whose YAML value is ``null`` (None). pydantic-settings
        # treats an explicitly-passed init kwarg — even ``None`` — as taking
        # priority over environment variables, ...
        se_raw = processed_config["skill_evolution"]
        config_dict["skill_evolution"] = {
            k: v for k, v in se_raw.items() if v is not None
        }
```

**`config.yaml:553-586`** (the shipped `skill_evolution:` block — **no `evolution_lane_enabled`**)
```yaml
skill_evolution:
  embedding_model: text-embedding-3-small
  embedding_dimensions: 1536
  ...
  ab_sample_size: 10
  ab_min_difference: 0.15
  max_extensions: 3
  max_inject_skills: 2
  ...
  default_task_count_threshold: 20
  default_daily_scan_hour: 3
  metric_scan_interval_hours: 24.0
  capture_min_iterations: 5
  capture_min_duration_seconds: 60
```

**`daemon/services/skill_metrics_service.py:486-490`** (recording happens regardless of the switch)
```python
        # return cleanly.
        ...
        await self._check_capture_eligibility(
```

**`daemon/services/job_processor.py:781-813`** (the terminal write + the checked return value)
```python
                updated = await asyncio.to_thread(
                    self._queue_service._repository.atomic_transition,
                    job.job_id,
                    from_status=AdmissionState.QUEUED.value,
                    to_status="done",
                    terminal_reason="lane_disabled",
                )
            ...
            if updated is None:
                # atomic_transition returns None only when the row does
                # not exist. Surface it — discarding this return is what
                # created the F1 silent no-op.
                logger.error("JobProcessor: queued-refusal for skill_evolution "
                             "job %s... wrote NOTHING — job not found. ...")
                return False
```

**`daemon/manager.py:6685-6700`** (the formerly-bypassing fallback, now gated)
```python
                        if job_type == "skill_evolution":
                            _skill_evo_cfg = getattr(getattr(self, "config", None),
                                                     "skill_evolution", None)
                            _lane_default = bool(getattr(_skill_evo_cfg,
                                                        "evolution_lane_enabled", False))
                            if not is_evolution_lane_enabled(_lane_default):
```

**`agents/skill-keeper/meta.json:7-13`**
```json
  "version": "0.2.0",
  ...
  "tools": {
    "allow": ["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv"],
    "deny": ["bash", "proc", "service", "write_file", "edit_file", "inner_soul"]
  }
```

**`origin/latest:daemon/services/metrics_census.py:322-335` + `:347-355`** (the inverted polarity)
```python
    @property
    def gated(self) -> bool:
        """Whether the census sampler's DB work is currently gated OFF.
        ...
        """
        return self._gate()
...
    def _gate(self) -> bool:
        if self._gate_enabled is not None:
            return bool(self._gate_enabled())
        from daemon.services import metrics_gate
        # A PURE read — the census never refreshes the managed tier ...
        return metrics_gate.metrics_enabled()
```

**`origin/latest:daemon/services/metrics_query/provider.py:110-122`, `:142-148`**
```python
    @property
    def census_enabled(self) -> bool:
        """Whether the census sampler's DB work is ungated.
        Sourced from the PUBLIC ``CensusSampler.gated`` property ...
        """
        if self._census is None:
            return False
        try:
            return not bool(self._census.gated)
...
    @property
    def availability_facts(self) -> AvailabilityFacts:
        return AvailabilityFacts(
            census_gated=not self.census_enabled,
            census_running=self.census_running,
            capture_active=self._capture_active,
        )
```

**`origin/latest:daemon/services/metrics_query/availability.py:76-98`**
```python
    if source_idiom == SOURCE_IDIOM_POSTGRES_CENSUS:
        # The POLARITY of `census.gated` itself is R-i — an OUT-OF-LANE
        # defect in daemon/services/metrics_census.py (the property
        # returns "enabled" where its docstring says "gated off"), ...
        if not facts.census_running:
            return {"state": "dark", "reason": "census_not_running"}
        if facts.census_gated:
            return {"state": "dark", "reason": "census_gated"}
    elif source_idiom == SOURCE_IDIOM_WEDGE_CAPTURE:
        if not facts.capture_active:
            return {"state": "dark", "reason": "capture_off"}
    if live_series_count <= 0:
        return {"state": "dark", "reason": "series_absent"}
    return {"state": "live"}
```

**`origin/latest:frontend/src/app/models/metrics.model.ts:298-311`** (what the user actually reads)
```typescript
  switch (reason) {
    case 'census_gated':      return 'the census sampler is gated';
    case 'census_not_running':return 'the census sampler is not running';
    case 'capture_off':       return 'the wedge capture is not active';
    case 'series_absent':     return 'no observation published yet';
    case 'listener_disabled': return 'the exposition listener is not bound';
    default:                  return `unknown(${reason})`;
  }
```

---

## (c) UNVERIFIED / COULD NOT DETERMINE

| # | Item | Why it could not be determined | What would settle it |
|---|---|---|---|
| U1 | **Live runtime behaviour** of the switches (is the capture/evolution lane actually OFF in the running daemon?) | This lane made **no daemon contact** (read-only / no-dispatch constraint) and the live daemon is a frozen binary at a different release. | A live `/readyz` + process-env probe by the maintenancer, or the `metrics_*`/job-queue read surfaces. |
| U2 | **"two new skills captured"** as a literal phrase | `git log --all -S"two new skills"` → **0 hits**; `git grep -n "two new skills" origin/latest` → **0 hits**. The source (§A5) says "**created**", and the capture lane was OFF, so "captured" cannot be right as written. The underlying DB fact (two skills created on 2026-11-03) is a **runtime/DB** observation, not a repo artifact. | A DB query on the `skills` table filtered by `lineage_origin`/creation time (maintenancer, gated). |
| U3 | **Production behaviour of F3** (does the wedge-capture family publish in prod while the door calls it dark?) | The only measurement available in-repo is the **fenced-dev** observation, where F3 had **0** live series; production series counts are not in any repo artifact I could find. | A prod `/api/metrics/families` read (auth-gated; needs the `metrics:read` scope). |
| U4 | **Prod runtime claim** "`/api/metrics/*` returns `AUTH_NOT_CONFIGURED` while `/api/resources/*` returns 200" | The **code-level gating** is verified (§A7.3), but whether the prod deployment actually has auth OFF is runtime configuration, not code. | A prod probe of both routes. |
| U5 | **Live value of `job_queues.is_paused` on `system_parallel_queue`** | DB runtime state; the code only names the queue (`skill_job_dispatcher.py:289`). | DB read of `job_queues`. |
| U6 | **Whether the 16/8 `skill_injection` census is still current at `origin/latest`** | I enumerated the **checked-out tree** (24 files carry the flag). `origin/latest` may differ. | Repeat the enumeration against `origin/latest`. |
| U7 | **The "zero `total_completions`" telemetry artifact** | I verified the *counter bump sites* (`skill_metrics_service.py:888`, `:892`) and the *A/B resolution gate* (`skill_evolution_service.py:1325-1428`), but the artifact itself is a runtime observation inherited from the prior lane's report — not re-observed here. | A DB aggregate on `skill_usage_records` (group `b9422dc3` was cited by the prior lane as 0/10 comparisons after 24 h). |
| U8 | **The identity/ownership of the uncommitted working-tree WIP** (7 skill files, 803 insertions) | `git status` shows the edits but not who made them; per the house convention "commits <1h = active agent" applies to commits, not to dirty files. | Ask the leader / check for a live instance holding those files. |
| U9 | **The `F23` family's origin and semantics** | It is present in `adjudication.py:107` and `roster.py:211` but absent from the 16-family promote documents and from the c1 observation; I did not trace which lane added it. | `git log -S'"F23"' --oneline --all -- daemon/services/metrics_query/`. |
| U10 | **Whether `capture_active` should be wired from the wedge-capture subsystem or from `ENSEMBLE_WEDGE_CAPTURE_ENABLED`** | R-i is routed as "a Tier-2 mechanical file" (disclosure `:93-94`), but the intended source of truth for the flag is not stated in any artifact I read. | Read the metrics-collection lane's R-i ticket / the wedge-capture enablement contract. |

---

## (d) OPEN QUESTIONS for the orchestrator / downstream writer

1. **Which tree is the revival plan targeting?** The checked-out tree is a **detached HEAD two weeks behind `origin/latest`** (Oct-26 vs Nov-9) and carries 73 untracked files + 7 modified skill files. Any plan that assumes "the current code" without naming the tree will be wrong for the metrics half of the system (which does not exist at HEAD). **Recommended: state the target tree explicitly (`origin/latest` = v0.29.7) and note that the kill-switch code is identical in both.**
2. **The two switch surfaces are asymmetric in discoverability.** The canonical env is undocumented in the shipped `.env.example` at this checkout, and the **alias** `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` is a second, undocumented ON switch (`config.py:4198-4209`). If the revival flips a switch, **which surface** should be the single documented one — and should the alias be eliminated or merely documented?
3. **Scope question — is `skill_evolve` (inline) in scope for the revival?** It is the one **UNGATED** mutation path (`config.py:4186-4188`; no lane check in `skill_evolution_tools.py`) and it folds into the never-built P3 master switch (`HANDOVER.md:53`, `decisions.md:99/116`).
4. **Which artifacts should the downstream writer quote for the user's decision?** The binding user intent is `HANDOVER.md:86` ("Skill capture — Stays OFF for other users until the system is improved") and `:87` ("Any future 'it's on again' surprise = incident, not a glitch"). Those are the decisive lines for a re-enable discussion; the code evidence (§A1, §A8) is the *capability* evidence.
5. **Should the F1/F3/F6 false-dark defect (R-i/R-ii) be in the revival scope at all?** It is an **out-of-lane** metrics defect (`disclosure:93-96`), it does not touch the Skills page (§A7.2), and it is a separate Tier-2/mechanical change to `daemon/api.py` + `daemon/services/metrics_census.py`. Including it would widen the commission.

---

*End of Lane 2 findings. Every claim above carries a `file:line`, a git SHA, or a quoted line. Where I could not verify, it is listed in §(c) rather than asserted.*
