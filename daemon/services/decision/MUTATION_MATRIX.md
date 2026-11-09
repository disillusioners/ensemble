# DECISION MODULE P1 — THE MUTATION MATRIX (provenance-backed)

**Lane:** `feature/decision-module-p1` · **base:** `818411107` ·
**matrix written:** 2026-11-09 (council round 4, fold-now item 6).

**WHY THIS FILE EXISTS.** The lane claimed "N mutations killed" in its
reports, but the number lived only in prose — the claim was
*reconstructible* from the chat history, not *readable* from the repo.
A reviewer (the tester) reconstructed all 11 and killed them, but could
not verify the count from the tree. This file makes the claim
provenance-backed: every leg names the file, the function, the exact
mutation, the digest that identifies it, and the PIN NODE that kills
it. **15 legs: 11 from council round 3 + 4 from council round 2 (the
tester's "+3 extra" plus the shipped-yaml leg).**

**THE DISCIPLINE.** Every leg below was applied **in the worktree and
reverted in the same worktree**, in a lane-owned file, with the suite
re-run after each revert. From council round 4 on, the rule is stricter
and is now permanent for this lane: **NO discrimination proof may
revert through `git checkout --` / `git stash` / `git reset` /
`git clean`** — the tester's G5 leg ran a `git checkout --` in a SHARED
worktree with eight sibling workers testing in it and produced two
FALSE reds (a traceback showed a mutant line absent from HEAD). In-memory
proofs (a monkeypatched or subclassed copy) are the only sanctioned
form.

**THE DIGEST** is `sha256("<file>|<function>|<shipped text>|<mutated text>")[:16]`
— it identifies the leg exactly, so a reviewer can re-derive it without
the chat history.

---

## Round 3 — the detached consult, the leaks, the fail-closed fixes

| Leg | Digest | File · function | Mutation | Killed by (pin node) | Observed RED |
|---|---|---|---|---|---|
| **M1** | `ee4f6135722a30cc` | `daemon/services/snapshot_search_service.py` · `_llm_select` (the S1 hook) | the hook's `await self._decision_overlay.consult(` → `self._decision_overlay.consult_detached(` (the pre-round-3 INLINE await) | `tests/unit/decision/test_council_round3.py::test_c1_the_shadow_consult_is_detached_from_the_search_path` | `assert 0 == 1` — the strong-ref set is empty (the inline consult completed inside `search()`) |
| **M2** | `04bbaf16f7db3b01` | `daemon/services/decision/client.py` · `DecisionClient.consult` | the `httpx.ConnectError` branch deleted → the fault falls to the broad `httpx.HTTPError` handler (`reached_socket=True`) | `…::test_e_connect_phase_faults_reach_no_socket_and_accrue_no_floor` | `assert True is False` — a connect-phase fault is D3-floor-eligible |
| **M3** | `d38f8c3d422846b3` | `daemon/services/decision/kill.py` · `KillChannelResolver.read_state` | the `if GLOBAL_ENABLED_KEY in self._poisoned: return SwitchState(readable=False)` branch deleted (the poison stays INERT) | `…::test_a1_global_poison_a_failed_global_write_resolves_off_not_on` | `assert True is False` — a failed GLOBAL-master write resolves ON |
| **M4** | `ed06f38f520c3be3` | `daemon/services/settings_service.py` · `_raw_yaml_leaf` | the `except Exception` fail-soft guard narrowed to `except ImportError` (the tidier's shape) | `…::test_f_the_per_key_accessor_reads_the_decision_section_and_fails_soft` | `RuntimeError: the decision config module is broken` — the settings read RAISES |
| **M5** | `7ece2bc4f09eff10` | `daemon/services/decision/client.py` · `DecisionClient.consult` | `preflight = self._preflight(request)` moved back OUTSIDE the never-raises guard | `…::test_g_a_malformed_preflight_input_yields_the_failure_never_a_raise` | `TypeError: questions is not iterable — malformed input` — the client RAISES into the caller |
| **M6** | `465e85a426e8e5f3` | `daemon/services/decision/client.py` · `DecisionClient.consult` | the NaN-blind clamp `if not (budget > 0):` → `if budget <= 0:` | `…::test_g2_a_nan_timeout_budget_is_clamped_never_a_fault` | `assert <FailureClass.TIMEOUT> is None` — a NaN budget crashes the loop's selector (`ValueError: cannot convert float NaN to integer`) |
| **M7** | `c1c48e2f3951a2e9` | `daemon/manager.py` · `InstanceManager.set_settings_service` | the A1 narrowing `if key.startswith("decision_") else None` widened to invalidate on EVERY key | `…::test_a1_the_invalidation_narrowing_zero_unrelated_exactly_one_decision` | `assert [None] == []` — an unrelated settings write invalidates |
| **M8** | `835394c30523c6dd` | `daemon/services/decision/overlay.py` · `DecisionOverlay.consult` | the `and not force_shadow` clause removed from the apply gate | `…::test_c1_a_detached_consult_never_applies_even_when_ratified_deciding` | `assert True is False` — a DETACHED consult applied its answer |
| **M9** | `b4316cdcb73deb7a` | `daemon/services/decision/overlay.py` · `DecisionOverlay._detached_payload` | the log line's `type(exc).__name__` → `exc` (the message) | `…::test_c1_detached_exceptions_are_contained_with_the_type_only` | `assert 'SECRET-PAYLOAD-MARKER' not in …` — the payload leaked into the log |
| **M10** | `2d453434f78b02ec` | `daemon/services/snapshot_search_service.py` · `_llm_select` | `if not parsed: return []` → `if not parsed: parsed = []` (the empty parse falls through to the hook) | `…::test_b_the_malformed_llm_output_path_never_consults_nor_dispatches` | `assert {'snapshot.l3.selection': 1} == {}` — the empty-heuristic path CONSULTED |
| **M11** | `bfdeee70568d34ff` | `daemon/services/decision/overlay.py` · `DecisionOverlay.consult_detached` | `self._detached_tasks.add(task)` removed (the bare `create_task` — GC-eligible mid-flight) | `…::test_c1_the_shadow_consult_is_detached_from_the_search_path` | `assert 0 == 1` — no strong reference |

---

## Round 2 — the council blockers D1–D3 + the shipped-yaml leg

| Leg | Digest | File · function | Mutation | Killed by (pin node) | Observed RED |
|---|---|---|---|---|---|
| **M12** | `553b21dc26cbef6b` | `daemon/services/decision/config.py` · `DecisionConfig.cost_floor_per_attempt_usd` | the D3 floor removed (a timeout bills `0.0`) | `tests/unit/decision/test_council_blockers.py::test_d3_a_timeout_consult_bills_a_positive_floor` | `assert usd > 0, "a timeout must bill > 0 (the D3 floor)"` |
| **M13** | `2759f806ea66c7f6` | `daemon/services/decision/kill.py` · `AppSettingKillStore._read_sync` | the D2 re-raise → `return None` (the read error swallowed) | `…::test_d2_the_app_setting_kill_store_raises_on_a_broken_engine` | `Failed: DID NOT RAISE <class 'Exception'>` |
| **M14** | `2a9d8d2cd8cc68f6` | `daemon/services/decision/factory.py` · `build_decision_overlay` | `DecisionConfig.resolve_values(...)` → `DecisionConfig.from_source(...)` (the D1 death — the configured predicate is never stamped) | `…::test_the_production_factory_stamps_the_configured_predicate_with_decision_signal` | `assert False is True` |
| **M15** | `514d4cb52a608e4a` | `config.yaml` · the shipped `decision:` block | the commented-out block re-armed as an ACTIVE `decision:` block (the belt permanently on) | `…::test_the_shipped_config_yaml_has_no_active_decision_block` | "the shipped config.yaml carries an ACTIVE decision: block — it must be absent or fully commented out (the belt would be permanently armed)" |

---

## Round 4 — the pins that are IN-MEMORY (no tree write at all)

| Leg | Proof | File · function | Mutation (in memory) | Killed by | Observed RED |
|---|---|---|---|---|---|
| **M16** | in-memory | `tests/unit/metrics/test_metrics_family_adjudication.py` · `_assert_lane_allowlist_complete` | the allowlist doctored (one lane-owned file removed) | `…::test_the_daemon_diff_pin_is_discriminating` | `pytest.raises(AssertionError)` — the rewritten pin REDS |
| **M17** | in-memory | `…::_assert_no_new_daemon_offenders` | an unrecorded path planted in the changed set | `…::test_the_daemon_diff_pin_is_discriminating` | `pytest.raises(AssertionError)` — the new-offender leg REDS |

---

## WHAT IS **NOT** CLAIMED

* The matrix covers the mutations the lane's pins are designed to catch.
  It is NOT a claim that every line of the lane is mutation-covered.
* The round-2 legs (M12–M15) were executed BEFORE the shared-worktree
  discipline was tightened; the round-4 legs (M16–M17) are in-memory by
  construction and stay that way.
* The B-4 census conclusion ("no live escape among the lane's own
  daemon files") was **verified BY HAND by the tester**; the earlier
  PIN was tautological (the allowlist was derived from the owned list,
  so its assertion could not fail). The pin is rewritten and its
  discrimination is proven in-memory (M16/M17).
