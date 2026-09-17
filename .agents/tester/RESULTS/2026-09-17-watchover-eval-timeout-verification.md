# Watchover eval-timeout fix — verification report

**Date:** 2026-09-17
**Branch:** `feature/fix-watchover-eval-timeout` @ `0a68db6f` (base `d9470aa0` = latest tip; `merge-base --is-ancestor` verified, exit 0)
**Commits under test:** `41968e89` (restore 90s cap, quick-model wiring, bound initial delta) → `497b106f` (reviewer hardening W1/W2/trivial batch) → `0a68db6f` (M1 coercion guard)
**Footprint (verified verbatim via `git diff --stat d9470aa0..HEAD`):** exactly 5 files, 1920 insertions / 46 deletions — `agents/watcher/meta.json` (−1), `daemon/graph.py` (+506), `daemon/services/watcher_context_builder.py` (16±), `tests/unit/test_watchover_decision.py` (79±), `tests/unit/test_watchover_eval_timeout_fix.py` (NEW, 1364+)
**Worker instances (10 total):** preflight `de6a5f47` · family `5e4d10a3` · graph+loop `9de6a874` · attestation p1/p2/p3 `36d8c2d8` / `4c7e5995` / `4e1e97f4` · concurrency `54c170c5` · closure `e0297007` · M1 mutation `d72a91ae` · static `9b96e78b`

---

## VERDICT: ✅ PASS — merge-ready from testing

- **Zero failures anywhere** across 1,077 unique green tests (watchover family 303 incl. the 35 new; graph/loop/attestation slice 676; concurrency 98P/74S/0F) plus focused pin re-runs and mutation-check runs.
- **Original symptom CLOSED** (Debug gate): the prod-1de88ec2 mechanical shape (10s meta budget + heavyweight instance model → 100% LD-2 fail-open inert gate) is provably gone at BOTH call sites; LD-2 fail-open semantics themselves are **byte-identical to base** (locked, by design).
- **M1 guard proven load-bearing** by mutation (red under stripped guard, green on restore, repo byte-identical afterward).
- **No new env flags, no pydantic knobs, no locked-semantics drift** on the full branch diff.
- **Activation:** prod = frozen PyInstaller → **rebuild + restart required** (per commit bodies). Post-activation soak items listed in §9.

---

## 1. Scope Decision

Verification-only mandate: requester-defined slices (watchover family + graph/loop/attestation) + ensure.md Core scoped to the change set + concurrency pack (graph.py touched). Full suite NOT run — not warranted: 5-file footprint, watchover-subsystem-scoped change, no cross-module refactor. Release Gate not triggered (not a big/critical/architecture change per blast radius).

## 2. Test surface — authoritative counts (mandate #1)

All runs: repo root, `uv run python -m pytest` only (bare `pytest` = broken Homebrew PATH), `timeout 300` outer guard, project per-test timeout inner guard untouched, no `-x`.

| # | Exact command | Verbatim summary | Exit | Runtime | Worker |
|---|---|---|---|---|---|
| 1 | `timeout 300 uv run python -m pytest tests/unit/test_watchover_*.py tests/unit/test_watcher_*.py -q --tb=short --durations=5` | `303 passed in 13.83s` | 0 | 16s wall | 5e4d10a3 |
| 2 | `timeout 300 uv run python -m pytest tests/unit/test_watchover_eval_timeout_fix.py -q --tb=short` | `35 passed in 0.26s` | 0 | 2s wall | 5e4d10a3 |
| 3 | `timeout 300 uv run python -m pytest tests/unit/test_graph*.py tests/unit/test_loop_*.py -q --tb=short --durations=5` | `97 passed in 5.40s` | 0 | 5.40s | 9de6a874 |
| 4 | attestation partition 1 (8 files: conditional_gate_outcomes … fused_judge_truncation) | `94 passed in 1.38s` | 0 | 1.38s | 36d8c2d8 |
| 5 | attestation partition 2 (8 files: gate … marker_wiring) | `255 passed in 1.95s` | 0 | 1.95s | 4c7e5995 |
| 6 | attestation partition 3 (7 files: nudge_inject … user_answer_pending_decide) | `230 passed in 1.26s` | 0 | 1.26s | 4e1e97f4 |
| 7 | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | `98 passed, 74 skipped, 55 warnings in 8.71s` → `RESULT: PASS` | 0 | 8.71s | 54c170c5 |

**Totals:** watchover family **303/303**; new-file solo **35/35**; attestation **94+255+230 = 579/579** (exact match to preflight `--collect-only` 579); graph+loop **97/97**; broader slice (mandate composition graph+loop+attestation) **676/676**; concurrency **98P/74S/0F** (baseline parity: historical 98P/74S/0F).

### Count-drift settlement (301 / 303 / 326)

- **303 = authoritative** for the family at branch tip — matches dev's own claim in the `0a68db6f` body.
- **301** = family at `497b106f` (before the +2 M1 `TestMetaTypeCoercion` tests) — per the `497b106f` commit body. Explained, superseded.
- **326** = **not reproducible** by the mandate command at tip (303 + 23 unexplained by any glob run here). Drift artifact; superseded by 303.
- **35** (new-file solo) = matches all claims exactly.
- Dev "broader" claims (694/972) used *different compositions* (e.g. 694 = graph+watcher+attestation+loop, excluding `test_watchover_*`); the mandate's composition is authoritative above.

### Baseline comparison (pre-existing families)

**Zero failures across the entire run surface → zero baseline comparisons needed.** The known pre-existing red families (context7/webfetch `blueprint` mock-gap; manager-decomposition kwarg-pin rot; QUARANTINE.md consolidated rows) never fired: the diff demonstrably adds nothing to any of them within the executed slices. No known-anchor node appeared (the 2026-09-14 `test_watcher_repository_concurrent.py` ×2 class no longer exists in the glob — watcher* inventory is 1 file / 18 tests).

## 3. Original-symptom closure — Debug gate (mandate #2)

**Verdict: ORIGINAL-SYMPTOM CLOSED.** Full pin-map (33 rows) in closure worker `e0297007`'s report; condensed:

| Claim | Pinned by (node id, key assertion) | Verdict |
|---|---|---|
| (a) meta.json 10s override GONE | `TestMetaJsonTimeoutOverride::test_meta_json_does_not_override_timeout_seconds` — `assert "timeout_seconds" not in meta` (read fresh from disk) | PINNED |
| (a) default 90 at EVAL call site | `test_default_timeout_is_90_seconds` (`WATCHOVER_TIMEOUT_SECONDS_DEFAULT == 90`), `test_constructor_uses_default_when_no_meta_override` (`evaluator._timeout_seconds == 90`), `TestTimeoutFloor::test_wait_for_timeout_argument_reaches_call_site` (spies `daemon.graph.asyncio.wait_for`; fails if wrapper removed or timeout arg changed) | PINNED |
| (a) default 90 at SNAPSHOT-REGEN call site | scratch GAP-1: observed `wait_for` timeouts `[90, 90]` (eval + regen) | SCRATCH-VERIFIED |
| (b) "quick" → `model_keywords` mirror at LLM ctor | `TestModelOverrideReachesLlmCall::test_model_override_applied_to_llm_construction` — `captured[0].get("model") == "quick-mirror"`; factory end-to-end `TestCreateWatchoverCheckNodeResolvesModel::test_factory_resolves_quick_to_model_keywords` | PINNED |
| (b) unset → instance model unmodified (W1: branches 1+2 → `""`) | `test_empty_requested_returns_empty_string_for_no_op`, `test_quick_with_no_model_keywords_returns_empty_string`; scratch GAP-4 end-to-end: captured ctor kwargs `model='agentic'` with `model_keywords=""` | PINNED + SCRATCH-VERIFIED |
| (b) branch 3 literal-not-allowed → WARN + daemon-global | `test_specific_model_name_falls_back_when_not_in_allowed` — result `"agentic"` + WARNING asserts | PINNED (documented trade-off, §9) |
| (b) snapshot model distinct + fallback | `test_snapshot_model_override_distinct_from_eval`, `test_snapshot_override_falls_back_to_model_override` | PINNED |
| (c) override ≥15 honored / <15 clamped+WARN | `test_above_floor_timeout_honored` (45→45), `test_exactly_at_floor_honored` (15→15), `test_sub_floor_timeout_clamped_at_construction` (5→15 + WARNING), `test_zero_timeout_clamped`; scratch GAP-2 `[45, 45]` / GAP-3 `[15, 15]` at BOTH call sites | PINNED + SCRATCH-VERIFIED |
| LD-2 fail-open on TimeoutError — LOCKED, preserved | `test_watchover_decision.py::TestWatchoverEvaluatorEvaluate::test_infra_error_timeout_fails_open` (`verdict=="allow"`, `error_type=="infra"`), `test_infra_error_connection_fails_open`, `test_infra_error_emits_degraded_sse` — pre-existing pins, untouched, still pass | PINNED |

Focused pin re-runs (exact commands in worker report): claim-(a) core 5 passed · resolver 9 passed · ctor+factory 5 passed · floor+coercion 6 passed · LD-2 pins 3 passed · seed/regen/meta-cache 10 passed · full new file 35 passed.

**Scratch verification** (`/tmp/watchover_closure_check/closure.py`, 383 lines, imports real `daemon.graph`, patches `ThinkingChatOpenAI` + `asyncio.wait_for` with `wraps=` — zero network, zero repo writes; deleted after run):
`cd <repo> && PYTHONPATH=$PWD timeout 300 uv run python /tmp/watchover_closure_check/closure.py` →
```
[GAP-1 default] observed wait_for timeouts: [90, 90]        → PASS (regen call site uses default 90s)
[GAP-2 above-floor] observed wait_for timeouts: [45, 45]    → PASS (regen honors 45s override)
[GAP-3 sub-floor] observed wait_for timeouts: [15, 15]      → PASS (regen uses clamped 15s)
[GAP-4 quick-no-mirror] captured kwargs: model='agentic'    → PASS (branch 1+2 preserves instance model end-to-end)
ALL SCENARIOS PASSED
```

**Incident-mechanical shape:** the 10s-budget + heavyweight-model combination is gone — effective budget 90s at both call sites; quick-mirror routing when configured; instance model preserved when unset. A >10s evaluator LLM call now lands INSIDE budget; if a call still exceeds the effective budget, LD-2 fail-open fires exactly as designed (locked).

**Coverage note (new finding):** the snapshot-regen `wait_for(timeout=...)` call site is pinned only *transitively* (via `_timeout_seconds` field pins) in the 35-test suite — the direct call-site spy pin exists only for the eval path. Scratch GAP-1/2/3 proved the regen path live today; recommend a permanent regen-path pin (§9).

## 4. M1 spot-check — mutation proof (mandate #3)

**Verdict: GUARD EXERCISED** (worker `d72a91ae`).

- `_coerce_meta_int` at `daemon/graph.py:9279`; both tests: `TestMetaTypeCoercion::test_non_numeric_string_timeout_falls_back_to_default`, `::test_explicit_null_timeout_falls_back_to_default`.
- Methodology note: `-k "coerce"` selects **zero** tests (substring ≠ class name — the ZERO-SELECTED trap); worker switched to `-k "TestMetaTypeCoercion"` and proved selection via `--collect-only` (2 selected / 33 deselected).
- Baseline: `2 passed, 33 deselected in 0.16s`.
- Mutation (python atomic read→replace→write; single-occurrence verified): guard stripped to bare `return int(watcher_config.get(key, default))`.
- Red run: **both nodes FAILED** with the exact original exception shapes at the mutated line — `ValueError: invalid literal for int() with base 10: 'fast'` and `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` (`graph.py:9329`).
- Restore: `git checkout -- daemon/graph.py`; `git diff` empty; `diff` vs `/tmp` backup empty; md5 identical (`354afa44…`); sanity re-run green (`2 passed in 0.14s`); final `git status --porcelain` = same 11 preflight entries, **no `daemon/graph.py` row**.

## 5. Static checks — flags & locked semantics (mandate #4)

**Verdict: ALL PASS** (worker `9b96e78b`, read-only).

- **Footprint:** exactly the 5 files; numstat 1920+/46− matches commit-body claim.
- **Env-flag census:** `git diff d9470aa0..HEAD -- daemon/ | grep ^+ | grep -E "os\.environ|getenv|environ\.get|ENSEMBLE_[A-Z_]+"` → **empty** (both broad and focused). Zero new flag reads — fix/flag policy (7d5285aa) satisfied.
- **Pydantic knobs:** `daemon/config.py` NOT in diff (name-list grep empty). No schema change.
- **Locked semantics:** all 5 LD-2 references in the diff are **comment text only**. The actual fail-open decision block (`except _INFRA_ERROR_TYPES` → `WatcherVerdict(verdict="allow", error_type="infra", …)` + `failing OPEN (no count)` log) is **byte-identical** base↔HEAD: `diff <(git show d9470aa0:daemon/graph.py | sed -n 9705,9758p) <(sed -n 10109,10162p daemon/graph.py)` → exit 0 (line shift only; additions sit above it). Changes near the path are constructor floor-clamp, factory model wiring, first-call seed — none alter the OPEN-on-TimeoutError decision.
- **ensure.md Core #4:** `grep -n "timeout-graceful-shutdown" dev.sh` → 2 matches (line 99 comment; line 102 command with `--timeout-graceful-shutdown 10`).
- **ensure.md Important #1 (await callers):** 8/8 actual call sites awaited (get_queue_stats ×5 incl. facade; _get_system_prompt_tokens ×2; _compute_context_usage ×1); 0 questionable; remainder are defs/docstrings.

## 6. ensure.md validation (blast-radius scoped)

| Requirement | Status | Evidence |
|---|---|---|
| Core #1 — no regressions in changed packs | ✅ PASS | §2: family 303/303, slice 676/676, new-file 35/35 |
| Core #2/#3 — concurrency/sync-DB integrity | ✅ PASS | `concurrency_atomic_unit_test` 98P/74S/0F in 8.71s (baseline parity) |
| Core #4 — dev.sh graceful shutdown | ✅ PASS | §5 grep evidence |
| Important #1 — await callers | ✅ PASS | §5, 8/8 |
| Important #2 — parent→child→complete no blocking | ✅ PASS | covered by concurrency pack (observer race, cascade, finalize) |
| Nice-to-have #3 — no dead code from fix | ✅ (by pin) | meta.json `timeout_seconds` removal proven gone by fresh-disk-read pin test |
| Release Gate | ⏭ NOT RUN | not a big/critical/architecture change (§1); no contradiction notices — ensure.md methods compatible with pack/timeout rules this run |

## 7. Execution discipline

- 10 workers, all reported with verbatim evidence; zero re-dispatches; zero junk reports. One pack (attestation, 579 collected) pre-split into 3 partitions per the >400 split flag — all finished ≤1.95s.
- Dual-layer timeout on every invocation (`timeout 300` outer; project per-test timeout inner; concurrency script internal timer). No invocation approached any cap (max 13.83s).
- Verification-only: **no production code modified, no commits, no quick fixes** (per mandate). M1 mutation was single-file, restored, md5-proven. Scratch lived in `/tmp` only and was deleted.
- Worktree dirt (11 entries: foreign `.agents/*` + untracked `test/packs/fe_*`) untouched throughout; zero overlap with fix footprint; final `git status --porcelain` identical to preflight in all three workers that touched nothing (closure, M1 post-restore, static).
- Quarantine: **no new entries; no known-anchor fired.** Foreign worktree artifacts declared out of scope per mandate.

## 8. Overall status

- Unit/pack surface: ✅ PASS (1,077 unique green + focused re-runs)
- Original-symptom closure (Debug gate): ✅ CLOSED
- M1 mutation proof: ✅ GUARD EXERCISED
- Flags/locked semantics: ✅ CLEAN
- ensure.md (scoped): ✅ 6/6 applicable PASS
- **Testing complete: ✅ READY (merge-ready from testing). Activation = rebuild + restart (frozen PyInstaller prod).**

## 9. Deferred backlog appendix (mandate #5)

**Tidier pass (0 High / 6 Medium / 13 Low — `NEEDS WORK` non-blocking; scratch `.agents/tidier/.scratch_watchover_pass.md`):**
- **M1** — unguarded `int()` coercion (graph.py:9496/:9536/:9541) — **FIXED in `0a68db6f`** (mutation-proven, §4).
- **M2** — W1 fallback contract documented 3× (graph.py:9149-9227 / :9411-9457 / :10288-10308; ~80-line collapse + TL;DR rider).
- **M3** — test-helper fork ×4 (`test_watchover_eval_timeout_fix.py:59-176` vs `decision.py:58-191`; `_bypass_first_call_seed` portion = caller-known deferred; H-L7 merged in).
- **M4** — cryptic round-ids (#7/#9(i)/W1/W2) in test docstrings (:329/:539/:568/:594/:631/:1066/:1229).
- **M5** — 1248-line test file lacks size-justification header (:1-30; 1000-3000 band rule).
- **M6** — `_get_llm` two cache branches near-verbatim dup (graph.py:9577-9599).
- **13 Lows** — count confirmed in scratch; **not individually enumerated there** (14-line summary file). Fidelity gap: enumerate on request from tidier if needed.

**Reviewer residual 🟡 — meta-cache initial-value pin:** routed cross-scope→reviewer per scratch ("meta-cache sentinel pin"). Implementation landed as trivial-batch **#7 in `497b106f`** (`test_meta_cache_starts_as_none_after_explicit_reset`, replacing the tautological hasattr check). Listed here as residual per requester; the landed pin passes (included in the 35).

**Branch-3 trade-off (W1 OBSERVATION, report-only):** `requested="literal"` not in `allowed_models` → WARN + daemon-global `config.llm.model` fallback. Same override character as the W1-fixed branches, but reviewer accepted as documented trade-off (WARNING preserves operator visibility; fail-closed would conflict with LD-2 fail-open). Docstring graph.py:9197-9214.

**New from this verification (tester-added):**
- Snapshot-regen `wait_for` call-site timeout is transitively pinned only (field-level) in the 35-test suite; direct spy pin exists for eval path only (`test_wait_for_timeout_argument_reaches_call_site`). Recommend adding a permanent regen-path pin (small, test-side).
- "326" family-count claim unreproducible at tip — ledger drift artifact; 303 authoritative.

**Post-activation soak items (rebuild+restart pending):**
- Prod log watch: `[Watchover] configured timeout_seconds=… clamping` WARNINGs should be ABSENT for watcher (no override shipped); any coercion WARNING (`cannot be coerced` / `is null`) = operator meta typo signal, not failure.
- Effective-budget/model routing in prod: evaluator/snapshot calls should follow the quick mirror when `OPENAI_MODEL_KEYWORDS` is pinned; LD-2 `[Watchover] infra error … failing OPEN` lines should drop to ~zero under normal load (they were 100% of batches in the incident).
- B4-style wedge-heal/regression watch unaffected (out of scope here).

## 10. Gaps

None. All 10 dispatched workers reported with verbatim evidence; no re-dispatches; no incomplete nodes.
