# Tidier Report — Code Quality / Maintainability Pass
## skill-system-revival Phase 1 (ENSEMBLE-27)

- **Reviewed SHA (pinned):** `cfe4480be9868059a4012e0eabe8f46a5cc9a799` (merged tip)
- **Diff base:** `424494082c36eeaf9cb68b6b76820b75af95b630` (origin/latest) — 68 commits · 113 files · +27,740/−264
- **Method:** 3 parallel craftsmanship reviewers (tidier-readable-code / tidier-static-hygiene / tidier-robustness), reports merged + deduplicated by the tidier dispatcher. Every finding below was confirmed by at least one worker with file:line evidence at the pinned SHA (repo-wide greps, AST scans, or venv probes — not estimates).
- **Worktree HEAD at review time:** `ef9a768de` (delta vs pinned SHA: `.agents/approver/active.md` only; all line numbers resolve against the pinned SHA).
- **Boundary:** correctness / security / pin-vacuity belong to the parallel code reviewer; cross-scope observations are listed at the end, not adjudicated here.

---

## VERDICT: CHANGES-REQUESTED

**Blocking set (must land before promote): H1–H3.** All three are small, localized patches (a docstring or a single `except` branch each) with outsized truthfulness/risk impact — the lane's own history (2026-10-22 route fix, M2) treats this class as must-fix.

**H4 (reconcile decomposition) is leader's-call:** strongly recommended with the lane or as an immediate fast-follow; it is a maintainability ceiling, not a behavior defect.

Everything else is backlog-able (19 Medium, 18 Low). Volume is normal for a +27.7k-line lane; much of the lane is genuinely well-organized (see final section).

---

## HIGH (4)

### H1 — Docstring documents a CLI override that does not exist *(Readability — a docstring that lies)*
`daemon/services/skill_deactivation_manifest.py:25`, echoed at `daemon/services/skill_deactivation.py:790`
- **Problem:** Both docstrings state the operator script refuses declared artifacts "unless the operator passes the explicit `--allow-declared-artifact` override." `scripts/audit/skill_stale_deactivate.py` defines no such flag (`build_parser()` registers only `--db-url/--apply/--reactivate/--receipt/--data-dir/--json`); the override exists only as a Python-API kwarg (`skill_deactivation.py:803`).
- **Impact:** An operator following the instruction hits argparse "unrecognized arguments" with no documented escape; a maintainer believes the operator path is escapable when it is not.
- **Fix:** Either add `--allow-declared-artifact` (`action="store_true"`) to `build_parser()` and forward it in `main()`, or reword both docstrings to name the Python-API-only override explicitly.

### H2 — The `skill_fix` TOOL path swallows the designed burst-cap refusal into a generic error note, with a false durability claim *(Error Handling)*
`daemon/tools/skill_tools.py:1250-1256` (envelope `:1277-1325`); refusal raised at `daemon/services/skill_job_dispatcher.py:934-936`
- **Problem:** A blanket `except Exception` catches `SkillFixBurstCapRefused`; the designed-refusal branch is skipped (`dispatch_error is None` guard), and the envelope renders "Note: dispatcher raised during dispatch (…)" plus "request is still recorded above for the next skill-keeper run" — **untrue**: no job exists and nothing will pick it up. The sibling REST route handles the same exception structurally (`daemon/routers/skills.py:1461-1478`, HTTP 429, M2-pinned).
- **Impact:** The agent-facing surface named by §9 E1 / DS-20 is the one that misreports a designed refusal as a dispatcher fault and asserts a pickup that will never happen. No pin catches it (M-E1f pins the dispatcher envelope only).
- **Fix:** Add `except SkillFixBurstCapRefused` **before** the generic handler; render a distinct "⛔ NOT dispatched: … no job was created and nothing will be picked up" line mirroring the route; extend the M-E1f pin to drive the tool and assert the false "still recorded" sentence is absent.

### H3 — E4 reconciliation mutates rows BEFORE persisting the receipt (its only reversal + suppression record) *(Error Handling — failure-path data-loss risk)*
`daemon/services/skill_deactivation.py:1143-1166` (`write_skill_pair` at `:1146/:1148`, `save_receipt` at `:1166`; `_atomic_write_json` re-raises `OSError` at `:582-589`)
- **Problem:** The apply loop flips `is_active`/`status` first; `save_receipt` runs only after the whole loop and does not catch `OSError`. Disk-full / permission / rotation failure ⇒ rows flipped, no receipt entry, no suppression key.
- **Impact:** The receipt is simultaneously the audit trail, the process-once suppression store (R3.5), and the ONLY input to `reactivate()` (`:1224`) — there is no DB-side copy by design (DS-10). Losing it makes a row's prior pair unrecoverable; acceptance #4 silently stops holding on exactly the failure path. The boot caller contains the exception (`daemon/manager.py:5748-5754`), but containment ≠ repairability.
- **Fix:** Persist-before-apply (the `touched` entries already carry `before`/`after`), then rewrite after the loop; or on `save_receipt` failure raise a typed `DeactivationReceiptLost` that the boot caller logs at CRITICAL with run_id + the in-memory `touched` list (enough for manual repair). At minimum: `logger.critical` before propagating instead of the generic WARNING downgrade.

### H4 — `SkillStaleDeactivator.reconcile` is a 356-line method with five inlined near-identical rule blocks *(Code Smells — SRP)*
`daemon/services/skill_deactivation.py:829-1186` (R1 `:861`, R2 `:911`, R3 `:958`, R5 `:1035`, DECLARED `:1081`, APPLY `:1120`)
- **Problem:** One method owns six inline sections, each repeating the same four-step template (predicate filter → suppression check → record construction → `_candidate_row` append), five ~40-line copies that already diverge (R1/R3 build `RefusalEntry`, R2/R5 build `ObservationEntry`; inconsistent `before=` usage), plus the receipt write, manifest write, and run bookkeeping. (Found independently by two of three reviewers.)
- **Impact:** A rule change means reading 356 lines; a pin exercising one rule cannot isolate it.
- **Fix:** Extract `_apply_r1/_apply_r2/_apply_r3/_apply_r5/_apply_declared` + `_apply_pass` and a shared `_record_outcome(...)` builder. The rule-block comments already mark the seams.

---

## MEDIUM (19)

### M1 — Duplicate dual-column deactivation machinery: an orphaned second writer + two value-object types for the same pair *(Code Smells / Duplication)*
`daemon/repositories/skill/repository.py:457` (`deactivate_with_receipt`, `DeactivationResult` `:76`, `SkillDeactivationRefused` `:100`) vs `daemon/services/skill_deactivation.py:454` (`write_skill_pair`, `SkillPair` `:197`)
- Repo-wide grep: zero production callers for the repository method (its own tests + a "Deliberately NOT reusing" comment at `daemon/routers/skills.py:945` only); production uses `write_skill_pair`. Two value objects carry the same `(is_active, status)` pair with the same JSON shape but different names/fields/mutability.
- **Fix:** One writer: either `write_skill_pair` delegates to `deactivate_with_receipt`, or delete the repository method + its two types and keep the pair type in one place (`daemon/repositories/skill/models.py`). Do not leave both.

### M2 — Dead gauge: `within_identity_key_divergence` has no production consumer *(Dead code)*
`daemon/repositories/skill/repository.py:974` — only references are its definition and `tests/unit/test_skill_counter_drift_gauge.py`.
- **Fix:** Wire it to the counter-drift surface it was built for, or drop it + its test until a consumer exists.

### M3 — Unused imports across the lane's new/changed files *(File Hygiene)*
Production: `daemon/services/skill_deactivation.py:86` (`asdict`), `:100-102` (`_SWITCH_OFF_TOKENS`, `_SWITCH_ON_TOKENS` — misleading: the module delegates to `_resolve_switch`); `scripts/audit/skill_stale_deactivate.py:75` (`os`).
Tests: `tests/unit/test_skill_stale_deactivation.py:44/:49` (`RULE_R1`), `:57` (`is_open_ab_shadowed`); `tests/unit/test_skill_fix_burst_guard.py:50` (`AdmissionState`); `tests/unit/routers/test_skill_patch_dual_column.py:34` (`AsyncMock`); `tests/unit/metrics/test_metrics_capture_active_wiring.py:47` (`pytest`); `tests/unit/metrics/test_metrics_census_polarity.py:39` (`pytest`); `tests/unit/metrics/test_metrics_capture_lane_read.py:27` (`json`), `:34` (`TestClient`); `tests/services/test_skill_flakiness_fixture_pins.py:209` (`fake_embedding_service`, `llm_config`).
- **Fix:** Delete the names. Do NOT touch the `# noqa: F401` model imports in the five test files that carry them — deliberate side-effect imports (create_all model registration), correctly marked.

### M4 — Burst-cap default defined in two places *(Config — single source of truth)*
`daemon/config.py:5199/:5209` (`fix_burst_cap=3`, `fix_burst_window_seconds=86400`) vs `daemon/services/skill_job_dispatcher.py:296-297` (`DEFAULT_FIX_BURST_CAP=3`, `DEFAULT_FIX_BURST_WINDOW_SECONDS=86400`); fallback at `daemon/manager.py:8053-8060`.
- Two sources of truth; the module constants govern every direct `SkillJobDispatcher(...)` construction (15+ in tests; direct-wiring documented at `skill_job_dispatcher.py:347`).
- **Fix:** Define once (move constants into `daemon/config.py` or import `SkillEvolutionConfig` in the dispatcher) and delete the duplicates; `manager.py` falls back to the config field only.

### M5 — The three new knobs are absent from operator env documentation; plan text contradicts the code *(Config/Env)*
`daemon/config.py:5199/:5209` + `daemon/services/skill_deactivation.py:126` vs `.env.example:122-184` (every other `SKILL_EVOLUTION_*` knob listed) and `.env.example:470-471` / `.env.prod.example:192` (both stale-deactivation siblings listed). Also `phase1-plan.md:457` says "**no new env var**" — contradicted by the working pydantic `env_prefix` alias (verified: `SKILL_EVOLUTION_FIX_BURST_CAP=7` → `fix_burst_cap=7`).
- **Fix:** Add the three rows to `.env.example` (+ prod skill block) with default + absent-semantics; fix the plan line. Note (verified by probe): an EMPTY value (`KEY=`) raises pydantic `ValidationError` — inherited behavior, but if documented as env keys, either add a pre-validator or document them yaml-only and drop the env-alias sentence from the config comment.

### M6 — `Any` overuse where concrete types are available (~20 sites) *(Type Cleanliness)*
`daemon/services/skill_deactivation.py:455,460,483,501,504,528,548,554,566,591,619,652,658,669-671,690,723-725,798-802,1189,1332,1337` — `path: Any` (should be `Path`), `data_dir: Any` (`str | Path | None`), `skill_repo: Any`/`ab_test_repo: Any` (`SkillRepository`/`SkillABTestRepository`), `row/skill: Any` (`Skill`, already imported at `:93`), `-> Any` (`DeactivationResult | None`).
- **Impact:** the module's whole public contract is untyped to a checker.
- **Fix:** Replace with the concrete types (keep `dict[str, Any]` at JSON payload boundaries — correct there). Zero gaps exist on the other six new modules; this brings the seventh to par.

### M7 — `skill_deactivation.py` (1380 lines, six responsibilities) warrants a split; seam + direction *(File cohesion — recommend only)*
`daemon/services/skill_deactivation.py:1` — house band met (justifying top-level docstring; 1000–3000 needs-comment rule satisfied), but six responsibilities in one file: rule constants + kill-switch resolver (`:110-184`), six value objects (`:191-447`), dual-column writers (`:449-521`), receipt persistence/rotation (`:523-624`), candidate detection (`:626-766`), orchestrator (`:768-1326`).
- **Fix (recommend only):** two stdlib-only leaf modules following the existing `skill_deactivation_manifest.py` precedent: `skill_deactivation_types.py` (constants + dataclasses, ~340 lines) and `skill_deactivation_receipt.py` (`resolve_receipt_path`/`load_receipt`/`save_receipt`/`rotate_receipt`/`_atomic_write_json`, ~100 lines). Dependency direction to preserve: `types ← receipt ← skill_deactivation` (no back-edge, no cycle).

### M8 — `repository.py` grew 2176 → 2532 (85% of the way to the 3000 refactor line) *(File cohesion — recommend only)*
`daemon/repositories/skill/repository.py:205` — +356 lines inside `SkillRepository` (`:205-1090`); the file holds six repository classes.
- **Fix (recommend only):** split per repository class with `repository.py` kept as a re-export shim (no import-site changes); minimum viable: extract the lane's deactivation additions (`:76-130` + `:457-560`) into `daemon/repositories/skill/deactivation_repository.py`, importing models from `.models` — never from `skill_deactivation.py` (would invert the current edge).

### M9 — Anomaly vocabulary + canonicalisation implemented twice, with divergent behavior *(Duplication)*
`daemon/services/skill_capture_observability.py:566` (`_ANOMALY_CATEGORIES_FALLBACK`, byte-identical to `daemon/services/anomaly_service.py:170`) and `_canonical_category` (`:618`) which does NOT lowercase or apply `CATEGORY_SYNONYMS`, while the canonical `AnomalyService.normalize_category` (`anomaly_service.py:745`) does both.
- **Fix:** One vocabulary module (e.g. `anomaly_vocab.py`); have `_canonical_category` delegate to the shared canonicaliser. The M-1b parity pin compares the fallback lists but not the two canonicalisation behaviors — extend it.

### M10 — The "counter + sink record" idiom is hand-rolled at 7 sites; one site re-implements the module's own helper *(Duplication)*
`daemon/services/skill_job_dispatcher.py:1063-1076` duplicates `observability.record_capture_lane_disabled_refusal()` (`skill_capture_observability.py:301`) — same counter key, stage, outcome, refusal_reason, and the helper already accepts the `run_id` the dispatcher holds. Same idiom at `skill_job_dispatcher.py:689/:714/:886/:920`, `job_processor.py:818/:867` (those two byte-identical 12-line twins), `skill_evolution_service.py:1397`.
- **Fix:** One `record_refusal(*, stage, reason, **extra)` helper called from all 7; at minimum replace the dispatcher block with the existing helper, and extract `_record_execution_refusal(job)` for the job_processor twins.

### M11 — Three "refusal envelopes" shaped three different ways; two literal tokens for one refusal *(Error Handling / Naming)*
`daemon/repositories/skill/repository.py:100-130` (`SkillDeactivationRefused`: class attr `reason="open_ab_test"`), `daemon/services/skill_deactivation.py:773-779` (`PairResolutionRefused`: instance attr, message sometimes just the token), `daemon/services/skill_job_dispatcher.py:315-326` (`SkillFixBurstCapRefused(RuntimeError)`: no token; route hard-codes `"reason": "fix_burst_cap"` at `daemon/routers/skills.py:1476`). Additionally `REFUSAL_OPEN_AB_TEST = "skipped_open_ab_test"` (`skill_deactivation.py:151`) names the same refusal as `repository.py:117` with a different token.
- **Fix:** `SkillLaneRefused(Exception)` base carrying `reason`/`detail`; subclass all three; set `reason = "fix_burst_cap"` on the burst-cap class and have the route read `e.reason`; one token per refusal concept. Keep message prefixes (pins assert them).

### M12 — The 8-hex id prefix that E3 removed is re-introduced by the E1 refusal message (agent-facing) and 4 log sites *(Naming/convention)*
`daemon/services/skill_job_dispatcher.py:704` (agent-facing: `skill_fix` renders `str(e)` into the envelope per `skill_tools.py:1253/:1321-1324`) + log sites `:668/:677/:916`. Commit `90eeefa22` in this same lane removed exactly this pattern with the rationale at `skill_tools.py:1266-1270` ("the 8-hex form is NOT `skill_view`-consumable").
- **Fix:** Drop `short_id`; interpolate the full id (the structured sink record already carries it at `:722-726`).

### M13 — The two agent-facing lane-refusal envelopes open with different verbs *(Naming/convention)*
`daemon/tools/skill_evolution_tools.py:100` ("⛔ NOT executed:") vs `daemon/tools/skill_tools.py:1308` ("⛔ NOT dispatched:") — the former's docstring claims it mirrors the latter.
- **Fix:** Pick one verb ("NOT dispatched") or extract a shared template with a `{verb}` placeholder.

### M14 — `_make_read_scope_aware` repeats the safety-critical token discipline four times *(Code Smells)*
`daemon/tools/instance.py:2466` (branches `:2549-2600`): each of 4 branches hand-writes get → refusal → set/set → try → call → finally reset/reset. The finally-pair is the part that must never be missed; a missed reset leaks caller identity/trusted workdir into the next tool call — a silent confinement failure.
- **Fix:** One `@contextmanager _bound_scope(agent_id, workdir)`; each branch reduces to three lines.

### M15 — `SkillJobDispatcher.enqueue_evolution` grew to 221 lines *(Code Smells)*
`daemon/services/skill_job_dispatcher.py:787` — four admission gates + idempotency pre-check + dedup detection + four observability blocks + enqueue; gate order exists only in comment banners.
- **Fix:** Extract `_admission_gates(skill_id, is_fix, run_id) -> str | None`; keep the method to enqueue + accounting.

### M16 — Test doubles re-declared across 3–4 new test files *(Test hygiene)*
`_StubQueueRepo` ×3 (`tests/unit/test_skill_revival_fix_wave1_pins.py:66`, `tests/unit/test_skill_fix_burst_guard.py:131`, `tests/services/test_skill_capture_observability.py:95`); `_RecordingReporter` ×2; `_run_in_fresh_interpreter` ×2 (`:373` + `tests/unit/test_skill_revival_wave2_pins.py:50`); `make_skill` ×2 with already-diverging signatures (`tests/services/test_skill_identity_dedupe.py:47` + `tests/services/test_skill_injection_budget.py:52`).
- **Fix:** Hoist to `tests/conftest.py` or `tests/_skill_doubles.py` (the lane already edits `tests/services/conftest.py`, so a shared home is established); one `make_skill` carrying the union of fields.

### M17 — `load_receipt`'s "corrupt ⇒ empty receipt" contract misses structurally-corrupt JSON; the operator rollback path is unguarded *(Error Handling)*
`daemon/services/skill_deactivation.py:554-563` catches only `(OSError, ValueError)`; probed on the venv: `{"entries": {"a": 1}}` and `{"refusals": "oops"}` raise raw `AttributeError` that escapes. Unguarded callers: `reactivate()` (`:1206`) and `scripts/audit/skill_stale_deactivate.py:204-225` — a hand-edited receipt aborts the operator's rollback with a raw traceback, at the worst moment, where sibling code raises typed `PairResolutionRefused`.
- **Fix:** Catch `(OSError, ValueError, TypeError, AttributeError, KeyError)` or `isinstance(item, dict)`-validate before `from_json`; in the script wrap and `return 1` with "receipt is corrupt — pass --receipt with a valid file" (idiom already at `skill_counter_reconcile.py:429-447`).

### M18 — Two of the burst cap's three degraded branches are silent; sibling confinement gates in the same lane fail CLOSED *(Error Handling — posture consistency)*
`daemon/services/skill_job_dispatcher.py:661-670` (absent ledger → admit, WARNING only) and `:671-679` (repo lacks `count_fix_admissions_since` → admit, WARNING only) vs `:680-699` (count raised → admit + `append_run_record(outcome="cap_check_error")` ✔) — the first two are PERMANENT wiring degradations, less observable than the transient one. Contrast: confinement probes fail closed on absent accessor / raising probe (`job_processor.py:714-737`, `skill_evolution_tools.py:136-145`).
- **Impact:** operator sees "the cap is enabled" at boot (`manager.py:8064-8071`) while it is inert.
- **Fix:** Emit `append_run_record(outcome="cap_degraded")` + `record_deviation()` (or a one-shot anomaly row) in the two silent branches; record the intended posture (fail-open for availability, ALWAYS recorded) in `decisions.md` next to DS-20 so the divergence from fail-closed confinement is a ruling, not an accident.

### M19 — A negative burst-cap/window value silently DISARMS the control *(Error Handling / config validation)*
`daemon/config.py:5199-5215` (no `ge=` constraint) → `skill_job_dispatcher.py:408-409` clamps `max(0, …)` silently; probed: `SKILL_EVOLUTION_FIX_BURST_CAP=-1` accepted, clamped to 0 = "cap disabled" (`:936`). Off-by-absence is correct (verified); only the negative case is wrong.
- **Fix:** `Field(default=3, ge=0)` and `Field(default=86400, ge=1)`; if clamping stays, log a WARNING naming the raw value.

---

## LOW (18)

- **L1** Dead constants, zero repo references: `CANONICAL_ACTIVE` (`daemon/services/skill_deactivation.py:165`; sibling `CANONICAL_INACTIVE` IS used ×7 — asymmetric pair) and `MANIFEST_VERSION` (`skill_deactivation_manifest.py:48`). Delete, or wire them (canonical pair at `restore_skill_pair:500`; stamp the version into emitted manifests).
- **L2** Missing type hints on the ContextVar confinement seam (10 sites): `daemon/tools/filesystem.py:259/:267/:277/:285` (setters lack `-> contextvars.Token`, resetters lack annotated `token`), `daemon/tools/instance.py:2466/:2516/:2551/:2565/:2584/:2598`. The setter/resetter pair is load-bearing for B3 — annotate `contextvars.Token` exactly.
- **L3** Stale predicate narrative in repointed tests: `tests/integration/test_skill_cross_phase_flow_c.py:21/:223` and `tests/integration/test_skill_capture.py:246/:495/:544` still narrate `has_applied_for_instance`; the gate now calls `has_genuinely_evolved_for_instance`. Reword + rename `test_…_when_skill_already_applied`.
- **L4** `short_id` still names a full id: `daemon/tools/skill_tools.py:774` — rename to `echo_id` to match `:1271/:1505` (the name invites re-truncation).
- **L5** Same entry list under two JSON keys: `DeactivationReceipt.to_json` emits `"entries"` (`skill_deactivation.py:385`) vs `ReconciliationReport.to_json` `"touched"` (`:438`) for the same `DeactivationEntry` objects. One key, or document the difference.
- **L6** Unreadable/malformed `meta.json` ⇒ read scope resolves "undeclared" (unbounded) silently, no log: `daemon/tools/filesystem.py:316-337` (`except (OSError, ValueError): continue`). Add a per-agent `logger.warning` so the open direction is audible. (The cache-staleness half was already fixed at worktree HEAD `ef9a768` per council F-5; the silent `continue` survives.)
- **L7** Test writes into the checkout's real `data/`: `tests/unit/test_skill_fix_burst_guard.py` never calls `configure_sink(tmp_path)`; confirmed `data/skill-capture-observability/skill-capture-20261110.jsonl` carries test rows in the worktree (gitignored — invisible to `git status`). Copy the autouse fixture from `tests/services/test_skill_capture_observability.py:59-69`; **delete the leaked jsonl before merge**.
- **L8** Two silent swallows in the dedupe install path: `daemon/services/skill_identity_dedupe.py:274-281` (`pass`, no log) vs `:282-288` (logs). Add the matching log line.
- **L9** Unattributed `agent_id` swallow without the sibling's debug log: `daemon/tools/skill_evolution_tools.py:526-533` — mirror `skill_tools.py:288-290` or reuse its `_get_agent_id`.
- **L10** One raw-text anchor assertion satisfiable by a docstring edit: `tests/unit/test_skill_keeper_read_scope.py:417` (`"current_trusted_workdir()" in anchor_source`). Use the AST walk the neighboring `:405-414` assertions use.
- **L11** `__all__` in 2 of 7 new modules (`skill_identity_dedupe.py:50`, `skill_roster.py:51` have it; `skill_deactivation.py`, `skill_capture_observability.py`, `skill_deactivation_manifest.py` don't). Consider adding to the two with real external consumers.
- **L12** Underscore-private cross-module imports (3 new sites: `skill_roster.py:47`, `skill_deactivation.py:103`, joining 2 pre-existing): the single-resolution-point property of `_resolve_switch` is an undeclared convention. Consider promoting to `daemon/services/skill_switches.py`.
- **L13** 7th copy of the data-dir precedence chain: `skill_capture_observability.py:373` (docstring admits "Mirrors the project-wide precedence chain"). Consider one `resolve_data_dir()`.
- **L14** E4 brake docstring says "fail-closed on misconfiguration" (`skill_deactivation.py:141`) while the plan (§9 acceptance 10 / M-E4k) says "inert, boot continues" — behavior verified to match the PLAN (resolver raises, boot step contains at `manager.py:5749-5756`). One-sentence docstring amendment naming both layers.
- **L15** Production-unreachable staged surfaces (roster's six public functions; `configure_sink`/`set_anomaly_reporter`) are deliberate Phase-1 design (DS-25) — add the one-line "unreachable in Phase 1 by design" marker so they aren't read as dead or wired prematurely.
- **L16** Lane grows pre-existing >3000-line monoliths (+114 `manager.py`, +187 `instance.py`, +52 `routers/skills.py`). Consider: E4.1 boot step (`manager.py:5714-5763`) → `skill_bootstrap_hooks.py`; read-scope wrapper (`instance.py:2466-2607`, 142 self-contained lines) → `daemon/tools/read_scope.py`.
- **L17** `capture_active: bool | Callable[[], bool]` value-dependent type (`daemon/services/metrics_query/provider.py:67`) — consider resolver-only shape like the sibling `publisher_cadence`. (Marked uncertain — bool-compat is deliberate/documented.)
- **L18** Roster boolean parse: the same `{"true","yes"}` set in two orders, four times (`daemon/services/skill_roster.py:273-276`) — one `_truthy(cell)` helper.

---

## What is well-organized (measured, not courtesy)

- **The dialect split is exemplary:** ONE builder (`build_fix_admission_count_statement`, `daemon/repositories/job_queue/repository.py:33`) with a two-dialect compile pin — no hand-rolled second copy anywhere in the diff.
- **Switch resolution is genuinely single-point:** `_resolve_switch` (`skill_job_dispatcher.py:85-119`) is the only switch reader; `skill_deactivation.py:147` delegates; no hand-rolled parse survives (verified by two reviewers independently).
- **The capture-gate predicate repoint is clean:** `has_genuinely_evolved_for_instance` defined once, both seams call it, and the retired `has_applied_for_instance` is re-documented in place with its NOT-A-CAPTURE-GATE-PREDICATE note (intentional keep, honored).
- **Identity dedupe logic lives once**; both injection seams call `inject_skills_deduped` (`instance_messaging.py:4409`, `context_messages.py:2328`).
- **Commit discipline:** all 67 non-merge commits match `type(scope): desc`.
- **Test-pack shells** share one house skeleton (env scrub, budget, log dir, timeout, exit-code contract); the strongest pins are AST-based and name the mutation each detects (`test_skill_revival_fix_wave1_pins.py:96/:186/:270`).
- **New service docstrings explain why**, not what (`skill_capture_observability.py`, `skill_roster.py`, `skill_identity_dedupe.py`, `skill_deactivation_manifest.py`, both audit scripts).
- **100% parameter+return annotations on all public functions in 6 of 7 new modules; zero `cast()`, zero `# type: ignore`.** No import-time side effects; no cycles introduced; frontend deltas are comment-only.
- **Fail-soft boundaries carry their intent in-line:** the D-workstream sinks' `except Exception  # noqa: BLE001` blocks each state "observability NEVER breaks the lane"; the metrics soft-fail split (`skill_metrics_service.py:640-658`) documents why ValueError is swallowed there and still propagates at the tool seam.
- **Off-by-absence semantics verified correct for all three knobs** (probed on the venv): unset → documented defaults (`False` / `3` / `86400`); invalid stale-switch token → `ValueError` (fail-closed) with fail-soft boot containment exactly as plan §9/M-E4k specifies; explicit `0` survives to disable.
- **Resource safety is clean:** atomic receipt writes (temp + `os.replace` + fsync, unlink-on-any-exception, correct single `except BaseException` re-raise), no bare `open()`, all repository methods under session context managers, contextvar resets in `finally` on every wrapper path.

---

## Deferred to the parallel code reviewer (one line each — not adjudicated here)

1. `skill_job_dispatcher.py:661-702` — burst-cap count TOCTOU (read outside the insert's transaction; concurrency can admit > cap) and whether the count includes the row being admitted.
2. `skill_deactivation.py:1156-1166` — `receipt.entries.extend(touched)` not deduped per `(skill_id, rule_id)`; repeated `--apply` could compound entries/re-suppress.
3. `skill_deactivation.py:829-1186` — R1→R3→R5 ordering and the R1-is-refusal-only invariant (correctness of the rule chain itself).
4. `daemon/tools/filesystem.py` + `instance.py` read-scope confinement — whether the anchor is airtight (security control; only the typing/seam hygiene was reviewed here).
5. `SkillFixBurstCapRefused(RuntimeError)` blast radius — catchable by unrelated `except RuntimeError` safety nets.
6. `reactivate()` `already_reactivated` vs `would_reactivate` semantics under `apply=False` (`skill_deactivation.py:1206-1232`).
7. Full-id echo (`skill_tools.py:1268`) — whether any consumer parses `skill '<id>'` out of the rendered string.
8. Council F-1/F-2 (symlink exfiltration; 3 net-new reds) — already on the code-review lane's plate per the council report; not re-derived here.

---

## Coverage & iteration

- **Skills dispatched:** 3 (tidier-readable-code, tidier-static-hygiene, tidier-robustness), one per worker; all three reported at the pinned SHA. The first robustness worker wedged mid-turn (terminated; anomaly `2e2d020c`); one replacement was dispatched per the fan-in escape valve and delivered the full report. **No coverage gaps.**
- **Iteration:** 001 of the shared 3-iteration cap.
- **Pre-existing items noted but NOT filed as findings** (outside the diff): `tests/integration/test_skill_evolution_e2e.py:632` `time.sleep(0.05)` wall-clock wait (integration dir deselected; G was scoped to `tests/services/`); `tests/unit/metrics/test_metrics_query_routes.py:339-362` latency assertions (wall-clock by design, labelled NFR-1).

*Counts: 🔴 4 High · 🟡 19 Medium · 🟢 18 Low (deduplicated from 3 worker reports: 4/24/21 raw).*
