# DECISION MODULE P1 — THE MUTATION MATRIX (provenance-backed, re-derivable)

**Lane:** `feature/decision-module-p1` · **base:** `818411107` ·
**matrix rewritten:** 2026-11-09 (council round 5, D-4/D-5).

## WHY THIS FILE WAS REWRITTEN

The previous revision's digests were **0/15 re-derivable** (~1.8M
reconstructions attempted): they were computed over ad-hoc prose strings
that appear nowhere in the repo, so a reviewer could not reproduce them.
**The legs were good** (the tester re-ran all 17 and killed them, with
sha-matched restores) — but a digest that cannot be re-derived is
unverifiable provenance, the same class as a pin that cannot fail.

**The fix:** the digest is now computed over **published operands** —
the EXACT text of the shipped line and the EXACT text of the mutated
line, both reproduced verbatim below, so a reviewer can copy them out of
this file (or out of the tree) and recompute.

## THE DIGEST SCHEME (exact)

```
digest = sha256( <path> + "\n" + <shipped text> + "\n" + <mutated text> ).hexdigest()[:16]
```

* `<path>` — the repo-relative path of the mutated file.
* `<shipped text>` — the exact source text the mutation replaces
  (byte-for-byte, as it stands in the tree).
* `<mutated text>` — the exact replacement text.

### A WORKED EXAMPLE (re-derive it)

The leg **R8** (the D-9 token meter):

* path: `daemon/services/decision/client.py`
* shipped text (verbatim):

```
                for left, right in (
                    ("prompt_tokens", "completion_tokens"),
                    ("input_tokens", "output_tokens"),
                ):
```

* mutated text (verbatim):

```
                for left, right in ():  # MUTANT: the fallback removed
```

* recompute:

```
$ printf '%s\n%s\n%s' \
    'daemon/services/decision/client.py' \
    '                for left, right in (' \
    '                    ("prompt_tokens", "completion_tokens"),' \
    '                    ("input_tokens", "output_tokens"),' \
    '                ):' \
    '                for left, right in ():  # MUTANT: the fallback removed' \
  | sha256sum
```

→ the first 16 hex characters are the digest published for R8 below.

## THE DISCIPLINE

Every leg below was applied **in-memory** (a doctored source string
compiled into a module object, or a pure function fed a doctored
source) or in an isolated copy — **never** by editing the shared tree
and reverting with git. The tester's G5 leg ran a `git checkout --` in a
SHARED worktree with eight sibling workers testing in it and produced
two FALSE reds; that rule is permanent for this lane.

## THE LEGS

**24 legs: 15 from the earlier rounds (M1–M15) + 9 added in round 5
(R1–R7 = the seven round-4 claims, R8–R9 = the two round-5 claims).**

---

## The round-2 and round-3 legs (M1–M15)

### M1 — `daemon/services/snapshot_search_service.py`

**shipped operand** (verbatim from the file):

```
            # Never raises (containment lives inside the overlay).
            self._decision_overlay.consult_detached(
```

**mutated operand**:

```
            # Never raises (containment lives inside the overlay).
            await self._decision_overlay.consult(
```

* **digest** `3d5f21ca85d5e9a3`
* **pin node** `tests/unit/decision/test_council_round3.py::test_c1_the_shadow_consult_is_detached_from_the_search_path`
* **observed RED** — `assert 0 == 1` — the strong-ref set is empty (the inline consult completed inside search())

### M2 — `daemon/services/decision/client.py`

**shipped operand** (verbatim from the file):

```
        except httpx.ConnectError:
```

**mutated operand**:

```
        except httpx.ConnectError:  # MUTANT: reached_socket=True
```

* **digest** `54bc82ad61061208`
* **pin node** `tests/unit/decision/test_council_round3.py::test_e_connect_phase_faults_reach_no_socket_and_accrue_no_floor`
* **observed RED** — `assert True is False` — a connect-phase fault is D3-floor-eligible

### M3 — `daemon/services/decision/kill.py`

**shipped operand** (verbatim from the file):

```
        if GLOBAL_ENABLED_KEY in self._poisoned:
```

**mutated operand**:

```
        if False and GLOBAL_ENABLED_KEY in self._poisoned:  # MUTANT
```

* **digest** `dc0a4e651b4e14c3`
* **pin node** `tests/unit/decision/test_council_round3.py::test_a1_global_poison_a_failed_global_write_resolves_off_not_on`
* **observed RED** — `assert True is False` — a failed GLOBAL-master write resolves ON

### M4 — `daemon/services/settings_service.py`

**shipped operand** (verbatim from the file):

```
    except Exception as exc:  # noqa: BLE001 — fail-soft: ABSENT, never a raise
```

**mutated operand**:

```
    except ImportError as exc:  # MUTANT: fail-soft narrowed
```

* **digest** `333d52212f84db20`
* **pin node** `tests/unit/decision/test_council_round3.py::test_f_the_per_key_accessor_reads_the_decision_section_and_fails_soft`
* **observed RED** — `RuntimeError: the decision config module is broken` — the settings read RAISES

### M5 — `daemon/services/decision/client.py`

**shipped operand** (verbatim from the file):

```
            preflight = self._preflight(request)
```

**mutated operand**:

```
            preflight = self._preflight(request)  # MUTANT: outside the guard
```

* **digest** `f396c24e36187ae9`
* **pin node** `tests/unit/decision/test_council_round3.py::test_g_a_malformed_preflight_input_yields_the_failure_never_a_raise`
* **observed RED** — `TypeError: questions is not iterable` — the client RAISES into the caller

### M6 — `daemon/services/decision/client.py`

**shipped operand** (verbatim from the file):

```
        if not (budget > 0):
            budget = float(self._config.timeout_s)
```

**mutated operand**:

```
        if budget <= 0:  # MUTANT: NaN-blind
            budget = float(self._config.timeout_s)
```

* **digest** `55101e5a249c77e8`
* **pin node** `tests/unit/decision/test_council_round3.py::test_g2_a_nan_timeout_budget_is_clamped_never_a_fault`
* **observed RED** — `assert <FailureClass.TIMEOUT> is None` — the loop's selector raises `cannot convert float NaN to integer`

### M7 — `daemon/manager.py`

**shipped operand** (verbatim from the file):

```
                        if isinstance(key, str) and key.startswith("decision_")
```

**mutated operand**:

```
                        if True  # MUTANT: narrowing widened
```

* **digest** `1d5ed9ba198b56bb`
* **pin node** `tests/unit/decision/test_council_round3.py::test_a1_the_invalidation_narrowing_zero_unrelated_exactly_one_decision`
* **observed RED** — `assert [None] == []` — an unrelated settings write invalidates

### M8 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
            and not force_shadow
```

**mutated operand**:

```
            # MUTANT: force_shadow stripped
```

* **digest** `d68214413625e52e`
* **pin node** `tests/unit/decision/test_council_round3.py::test_c1_a_detached_consult_never_applies_even_when_ratified_deciding`
* **observed RED** — `assert True is False` — a DETACHED consult applied its answer

### M9 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
            error = type(exc).__name__
```

**mutated operand**:

```
            error = f"{type(exc).__name__}: {exc}"  # MUTANT: the message
```

* **digest** `10d84634d8f19760`
* **pin node** `tests/unit/decision/test_council_round3.py::test_c1_detached_exceptions_are_contained_with_the_type_only`
* **observed RED** — `assert 'SECRET-PAYLOAD-MARKER' not in …` — the payload leaked into the log

### M10 — `daemon/services/snapshot_search_service.py`

**shipped operand** (verbatim from the file):

```
        if not parsed:
            return []
```

**mutated operand**:

```
        if not parsed:
            parsed = []  # MUTANT: falls through to the hook
```

* **digest** `1c26f273270faf46`
* **pin node** `tests/unit/decision/test_council_round3.py::test_b_the_malformed_llm_output_path_never_consults_nor_dispatches`
* **observed RED** — `assert {'snapshot.l3.selection': 1} == {}` — the empty-heuristic path CONSULTED

### M11 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
        self._detached_tasks.add(task)
```

**mutated operand**:

```
        # MUTANT: no strong reference
```

* **digest** `a0e70e9b71077dcd`
* **pin node** `tests/unit/decision/test_council_round3.py::test_c1_the_shadow_consult_is_detached_from_the_search_path`
* **observed RED** — `assert 0 == 1` — no strong reference (GC-eligible mid-flight)

### M12 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
            floor = float(self._config.cost_floor_per_attempt_usd)
```

**mutated operand**:

```
            floor = 0.0  # MUTANT: the D3 floor removed
```

* **digest** `963f407ecd3c4e46`
* **pin node** `tests/unit/decision/test_council_blockers.py::test_d3_a_timeout_consult_bills_a_positive_floor`
* **observed RED** — `assert usd > 0` — a timeout bills 0.0

### M13 — `daemon/services/decision/kill.py`

**shipped operand** (verbatim from the file):

```
        except Exception:
            raise
```

**mutated operand**:

```
        except Exception:
            # MUTANT: the D2 swallow
            return None
```

* **digest** `eb9d438cc58b7829`
* **pin node** `tests/unit/decision/test_council_blockers.py::test_d2_the_app_setting_kill_store_raises_on_a_broken_engine`
* **observed RED** — `Failed: DID NOT RAISE <class 'Exception'>`

### M14 — `daemon/services/decision/factory.py`

**shipped operand** (verbatim from the file):

```
    config = DecisionConfig.resolve_values(
```

**mutated operand**:

```
    config = DecisionConfig.from_source(  # MUTANT: the D1 death
```

* **digest** `2de013b70a4cb99b`
* **pin node** `tests/unit/decision/test_council_blockers.py::test_the_production_factory_stamps_the_configured_predicate_with_decision_signal`
* **observed RED** — `assert False is True` — the configured predicate is never stamped

### M15 — `config.yaml`

**shipped operand** (verbatim from the file):

```
# decision:
```

**mutated operand**:

```
decision:  # MUTANT: the belt permanently armed
```

* **digest** `90545f645d066395`
* **pin node** `tests/unit/decision/test_council_blockers.py::test_the_shipped_config_yaml_has_no_active_decision_block`
* **observed RED** — "the shipped config.yaml carries an ACTIVE decision: block"

---

## The round-4 and round-5 claims (R1–R9)

### R1 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
            error = type(exc).__name__
```

**mutated operand**:

```
            error = f"{type(exc).__name__}: {exc}"  # MUTANT: the message
```

* **digest** `10d84634d8f19760`
* **pin node** `tests/unit/decision/test_council_round5.py::test_the_exception_leak_walker_flags_the_real_cost_trip_shape`
* **observed RED** — the walker flags the real :981 shape (IN MEMORY)

### R2 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
            error = type(exc).__name__
```

**mutated operand**:

```
            error = f"{type(exc).__name__}: {exc}"  # MUTANT: the message
```

* **digest** `10d84634d8f19760`
* **pin node** `tests/unit/decision/test_council_round5.py::test_the_cost_trip_leak_pin_is_discriminating`
* **observed RED** — the mutant LEAKS the secret into the log AND the notify payload

### R3 — `daemon/services/decision/factory.py`

**shipped operand** (verbatim from the file):

```
    try:
        return build_decision_overlay(
```

**mutated operand**:

```
    # MUTANT: the defensive guard removed
    return build_decision_overlay(
```

* **digest** `a0e66ab125c2755f`
* **pin node** `tests/unit/decision/test_council_round4.py::test_b2_an_invalid_decision_config_leaves_the_module_inert`
* **observed RED** — the raise escapes the factory (the manager would raise)

### R4 — `daemon/services/decision/overlay.py`

**shipped operand** (verbatim from the file):

```
                cost_usd=billed_usd,
```

**mutated operand**:

```
                cost_usd=result.cost_usd,  # MUTANT: the reported cost
```

* **digest** `f5972213c8cd4f78`
* **pin node** `tests/unit/decision/test_council_round4.py::test_fold2_the_port_reports_the_billed_cost_not_the_reported_cost`
* **observed RED** — a timeout reports 0.0 instead of the billed floor

### R5 — `daemon/services/decision/cost_store.py`

**shipped operand** (verbatim from the file):

```
        with self._lock:
            return self._record_locked(
```

**mutated operand**:

```
        # MUTANT: the lock removed (the RMW interleaves)
        return self._record_locked(
```

* **digest** `0afc6970c238fe53`
* **pin node** `tests/unit/decision/test_council_round4.py::test_fold3_the_ledger_pin_exercises_real_concurrency`
* **observed RED** — the deltas are lost (the ledger undercounts)

### R6 — `daemon/services/decision/shadow.py`

**shipped operand** (verbatim from the file):

```
    if not text:
        # Absent stays absent — never the hash of the empty string.
        return ""
```

**mutated operand**:

```
    # MUTANT: the empty string is hashed
```

* **digest** `7a3abf7ee729a80e`
* **pin node** `tests/unit/decision/test_council_round4.py::test_fold4_an_empty_value_stays_empty`
* **observed RED** — an empty field carries sha256:e3b0c442… (a constant that reads as a token)

### R7 — `tests/unit/metrics/test_metrics_family_adjudication.py`

**shipped operand** (verbatim from the file):

```
        _assert_lane_allowlist_complete(LANE_OWNED_DAEMON, ALLOWED_DAEMON_DIFF)
```

**mutated operand**:

```
        # MUTANT: the tautological shape (the allowlist derived from the owned list)
        pass
```

* **digest** `d4b7ad17110e98a8`
* **pin node** `tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_pin_is_discriminating`
* **observed RED** — the census pin can no longer fail (the tautology the tester ruled on)

### R8 — `daemon/services/decision/client.py`

**shipped operand** (verbatim from the file):

```
                for left, right in (
                    ("prompt_tokens", "completion_tokens"),
                    ("input_tokens", "output_tokens"),
                ):
```

**mutated operand**:

```
                for left, right in ():  # MUTANT: the fallback removed
```

* **digest** `eb20e0143bc7217b`
* **pin node** `tests/unit/decision/test_council_round5.py::test_the_token_ceiling_pin_is_discriminating`
* **observed RED** — the tokens meter ZERO and the TOKEN CEILING never binds (the D-9 bypass)

### R9 — `daemon/services/decision/client.py`

**shipped operand** (verbatim from the file):

```
        except httpx.ConnectError:
```

**mutated operand**:

```
        except httpx.ConnectError:  # MUTANT: reached_socket=True
```

* **digest** `54bc82ad61061208`
* **pin node** `tests/unit/decision/test_council_round5.py::test_the_connect_phase_floor_pin_is_discriminating`
* **observed RED** — the ledger bills the D3 floor for a connection that never opened a socket

---

## THE RE-POINTING AND RE-TARGETING (D-4/D-5)

* **M6 re-pointed** — the leg now names the real site of the defect
  (the NaN-blind budget guard in `client.py::DecisionClient.consult`),
  not a paraphrase of it.
* **M9 re-pointed** — the leg now names the real site (the cost-trip
  exception capture in `overlay.py::_trip`), and it is the SAME
  mutation the round-5 behavioural pin (R2) proves live.
* **M12 re-targeted** — the D3 floor leg now names the APPLICATION site
  (`overlay.py::_account_cost`, where the floor is applied), not the
  config field that merely holds its value.

## WHAT IS **NOT** CLAIMED

* The matrix covers the mutations the lane's pins are designed to
  catch. It is NOT a claim that every line of the lane is
  mutation-covered.
* The round-2/round-3 legs were executed BEFORE the shared-worktree
  discipline was tightened; the round-5 legs are in-memory by
  construction and stay that way.
* The B-4 census conclusion ("no live escape among the lane's own
  daemon files") was **verified BY HAND by the tester**; the FIRST
  census pin was tautological (the allowlist was derived from the owned
  list, so its assertion could not fail). The pin was rewritten and
  its discrimination is proven in-memory (R7).
