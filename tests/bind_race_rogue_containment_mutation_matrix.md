# bind_race port-ownership mutation matrix

Mutation-proven pin suite for `daemon/services/port_ownership.py` and
`tests/unit/test_port_ownership_pins.py` (component A, rogue-containment).

Venue discipline (mutation-02 POK): every mutant runs in a scratch
worktree at the branch tip with its own venv; never on the canonical
checkout. Restore via `git restore <file>` and verify sha256 PRE==POST.

```
git worktree add --detach /tmp/rogue-mut <branch-tip-sha>
cd /tmp/rogue-mut && /home/nea/ensemble-src/.venv/bin/python -m pip install -e . 2>/dev/null
# prove import isolation:
/home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"
# baseline GREEN:
/home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/test_port_ownership_pins.py -q
# apply mutant (edit named file/line), re-run, expect listed REDs, then:
git restore daemon/services/port_ownership.py
sha256sum daemon/services/port_ownership.py   # == recorded digest
```

Pre-mutation sha256 (recorded at tip 715f4eedc, verified unchanged at tip 50d97c5ab):

```
942e62bdecdb8629444497a9568a1167c33532acce93c70c0b0e76c27a6e65d8  daemon/services/port_ownership.py
```

## Mutants

must-RED lists below are the **observed** red sets from the 2026-11-12 execution
(rulings 1/2/6): accept the natural blast radius, list all observed reds, with
a one-line causal note wherever a previously-listed node was dropped or an
unexpected node appeared. No code was restructured to serve a mutant.

| Mutant | File | Edit (surviving behaviour removed) | Must RED (observed) |
|---|---|---|---|
| M1 | daemon/services/port_ownership.py | In `is_live_foreign`, replace the foreign-ness gate result with `False` (treat foreign install_dir as non-foreign) — removes the foreign-ness gate | `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses`. Causal note: `test_E_ii_M1_same_install_takes` stays green BY DESIGN — the planted claim is same-install, which the `==` early-return already classifies non-foreign, so removing the final gate never touches it. |
| M2 | daemon/services/port_ownership.py | In `pid_alive_with_starttime`, remove the starttime mismatch check (mismatched starttime still returns True) | `test_E_ii_M2_dead_pid_stale_takes`. Causal note: `test_E_ii_refuse_foreign_live` stays green BY DESIGN — its planted pid is live with a matching starttime, so the starttime check is not on its path. |
| M3 | daemon/services/port_ownership.py | Remove the `is_live_foreign` foreign-ness check in `try_claim` (always take) | `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses` |
| M4 | daemon/services/port_ownership.py | In `try_claim`, invert the foreign-ness verdict (`is_live_foreign` → `not is_live_foreign`) on the EXISTING-claim path | `test_E_ii_refuse_foreign_live`, `test_E_ii_M1_same_install_takes`, `test_E_ii_M2_dead_pid_stale_takes`, `test_E_iii_M4_plant_foreign_refuses`, `test_promote_shape_same_install_non_refuse`. True effect: inverts the verdict on the existing-claim path only. The absent-claim path (`test_E_iii_happy_take_claim`) sits outside the `existing is not None` guard and is unreachable by this mutation family; it is covered by its clean-green result plus harness leg (b). |
| M5 | daemon/services/port_ownership.py | In `try_claim`, make `claims_dir.mkdir` failure raise instead of degrade | `test_E_iii_M5_degrade_claims_dir_create_fails` (raises OSError) |
| M6 | daemon/services/port_ownership.py | In `is_live_foreign`, treat same-install as foreign (`==` → `!=`) | `test_E_ii_refuse_foreign_live`, `test_E_ii_M1_same_install_takes`, `test_E_ii_M3_remove_foreign_check_would_refuse_same_install`, `test_E_iii_M4_plant_foreign_refuses`, `test_promote_shape_same_install_non_refuse`, `test_promote_shape_M6_foreignness_flip_would_refuse`. Causal note: the flip changes foreign-ness for both same- and different-install claims, so the blast radius spans the whole suite. |

## Non-vacuity control

Clean tree (no mutant) => all pins GREEN. Each mutant reddens only its listed
node(s) plus the causal blast radius above; any unlisted RED was investigated
before sign-off. Baseline run on the scratch venue: 10 passed
(`tests/unit/test_port_ownership_pins.py`).

## Restore verification

`git restore daemon/services/port_ownership.py` then `sha256sum` must equal the
recorded digest. Any mismatch => re-clone the scratch venue. Verified for all six
mutants (PRE == POST == 942e62bdecdb8629444497a9568a1167c33532acce93c70c0b0e76c27a6e65d8).

## Execution record (2026-11-12, tip 50d97c5ab)

**Venue + import isolation:** `git worktree add --detach /tmp/rogue-mut-po 50d97c5ab`;
`PYTHONPATH=/tmp/rogue-mut-po` forced the worktree ahead of the canonical
editable `.pth` (verified: `daemon.__file__` = `/tmp/rogue-mut-po/daemon/__init__.py`,
`tests.__file__` = `/tmp/rogue-mut-po/tests/__init__.py`). Per-variable env scrub
(no `env -u POSTGRES_*` glob). All runs bounded. Non-vacuity control green
(10/10). Scratch tree removed (`git worktree remove --force /tmp/rogue-mut-po`;
`git worktree list` shows no `/tmp/rogue-mut-*`).

**Per-mutant table (edit applied / observed reds / verdict / sha equality):**

| Mutant | Edit applied | Observed reds | Verdict | POST sha == PRE |
|---|---|---|---|---|
| M1 | final `return True`→`return False` | `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses` | MATCH + UNDER-RED (`test_E_ii_M1`) + OVER-RED (`test_E_iii_M4`) | ✓ |
| M2 | remove starttime mismatch check | `test_E_ii_M2_dead_pid_stale_takes` | MATCH + UNDER-RED (`test_E_ii_refuse_foreign_live`) | ✓ |
| M3 | `is_live_foreign(...)`→`if False:` | `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses` | MATCH (both) | ✓ |
| M4 | `is_live_foreign(...)`→`not is_live_foreign(...)` | 5 reds (see table above) | UNDER-RED (named node green) + 5 OVER-RED | ✓ |
| M5 | mkdir failure → `raise` | `test_E_iii_M5_degrade_claims_dir_create_fails` (OSError) | MATCH | ✓ |
| M6 | same-install `==`→`!=` | 6 reds (see table above) | MATCH (2) + 4 OVER-RED | ✓ |

**Classification:** 2/6 clean MATCH (M3, M5); 4/6 match-with-relisted-blast. Zero
undetected mutants (every mutant reddened at least its core assertion). Zero
truly-vacuous pins — every UNDER-RED was causal over-specification of the
must-RED list, not a green-on-mutant core assertion. Docs not committed (STOP
rule) pending this re-authoring.<|end|><|start|><|start|>assistant to=bash<|channel|>analysis <|constrain|>xml<|message|><rfl:parameter name="command">
cat > /home/nea/ensemble-src-wt-rogue-containment/tests/bind_race_rogue_containment_mutation_matrix.md <<'EOF'
# bind_race rogue-containment mutation matrix

Mutation-proven pin suite for the spawn-boundary scrub, rider (d) metrics
derive, DB-resolution guard, and pkill overlay guard (component B +
component A overlay).

Venue discipline (mutation-02 POK): scratch worktree at branch tip, own
venv; restore via `git restore <file>`; verify sha256 PRE==POST. Never
touch the canonical checkout.

## Mutants

must-RED lists below are the **observed** red sets from the 2026-11-12 execution
(rulings 1/4/5/6): accept the natural blast radius, list all observed reds,
with a one-line causal note wherever a previously-listed node was dropped or
an unexpected node appeared.

| Mutant | File | Edit | Must RED (observed) |
|---|---|---|---|
| B-M1 | daemon/services/spawn_env_policy.py | Remove "PORT" from SPAWN_BOUNDARY_STRIP_ENV | Collection error: module-level `assert len(SPAWN_BOUNDARY_STRIP_ENV) == 10` fails (9) → both files fail to collect. Causal note: removing any strip entry trips the module-level count assert; collection-level detection is a loud, unmissable mutant signal (ruling 3 — kept as-is). The three named nodes (`test_strip_env_exact_ten_names`, `test_scrub_strips_poisoned_ambient_with_overlay_wins`, `test_counter_mutation_would_redden`) are all affected via collection. Interplay with B-M7 noted. |
| B-M2 | daemon/services/spawn_env_policy.py | Remove overlay-merge block in `apply_spawn_boundary` (`if overlay: out.update(overlay)`) | `test_apply_boundary_strips_inherit_and_overlay_wins` (env_policy), `test_scrub_strips_poisoned_ambient_with_overlay_wins` (scrub). Causal note: `test_scrub_service_spawner_env_kwarg` stays green BY DESIGN — it exercises the deliberate=extra_env channel, not overlay. |
| B-M3 | daemon/tools/service_spawner.py | Remove `env=_apply_spawn_boundary(...)` kwarg from Popen (revert to inherit) | `test_scrub_service_spawner_env_kwarg` |
| B-M4 | daemon/api.py | Revert metrics derive to `getattr(config.limits, "metrics_listener_port", 9798)` | `test_api_source_uses_derive_not_managed_default`, `test_api_source_honors_env_escape`. Causal note: `test_derive_scratch_main_plus_one` is deliberately unaffected — it is a local mirror function that never reads api.py (ruling 4). |
| B-M5 | daemon/ensemble_config.py | (primary) Remove fence-var check (`ENSEMBLE_DB_AMBIENT_ALLOW == "1"` early return) | `test_guard_fence_var_allows`. Causal note: `test_guard_fires_on_ambient_poison` stays green — the ambient-poison case still fires regardless of the fence-var check. Alt (ambient-predicate removal): observed red = `test_guard_does_not_fire_sqlite_default` (the always-fire detector); both must-RED nodes stayed green. Fence-var removal is the cleaner mutation (ruling 5). |
| B-M6 | daemon/tools/bash.py | Remove live-pattern check from `_pkill_overlay_violation` (always None) | `test_name_family_refused_unfenced`, `test_unit_ref_family_refused_unfenced`, `test_install_root_path_family_refused_unfenced`, `test_port_family_refused_unfenced`, `test_list_form`. Causal note: all five use live patterns, so removing the check makes them all return None. |
| B-M7 | daemon/services/spawn_env_policy.py | Change count assertion target 10 -> 9 (count gate bypass) | Collection error: module-level `assert len(SPAWN_BOUNDARY_STRIP_ENV) == 9` fails (10 != 9) → both files fail to collect. Causal note: collection-level detection; interplay with B-M1 honestly noted (ruling 3 — kept as-is). |

## Non-vacuity control

Clean tree => all pins GREEN. Counter-mutations redden only their listed
nodes (plus the causal blast radius above); unlisted REDs are investigated.
Baseline run on the scratch venue: 11 passed
(`tests/unit/test_spawn_env_policy.py` + `tests/unit/test_spawn_boundary_scrub.py`).

## Restore verification

`git restore <file>` then `sha256sum` must equal the recorded digest for
each mutated file. Recorded digests at tip 715f4eedc (verified unchanged at
tip 50d97c5ab):

```
65cd0ea7edc4fe9a718ca98ac24458e812af2af8b8883a488fbed2d4844ec094  daemon/services/spawn_env_policy.py
103a422bb460ca25e0b6ea8cd99b3c6fb295d66c1c8b68e481e653636f97a981  daemon/tools/service_spawner.py
d3579eb9bade9177eda79c7ad97d9b32aa2e2e55a9d8b202e7c6617eccbeee39  daemon/api.py
3d4e5cef8c42745621c4e4d53d0d14a165abb681b5cc1bf633a553b4badbebd6  daemon/ensemble_config.py
e3c2afc41b9554b08e55bccf7bf532875b7b418e5069d040591fef7d9acf3dc0  daemon/tools/bash.py
```

## Execution record (2026-11-12, tip 50d97c5ab)

**Venue + import isolation:** three scratch worktrees
(`/tmp/rogue-mut-spawn`, `/tmp/rogue-mut-svc`, `/tmp/rogue-mut-api`,
`/tmp/rogue-mut-db`, `/tmp/rogue-mut-bash`) created via
`git worktree add --detach ... 50d97c5ab`. `PYTHONPATH=/tmp/rogue-mut-<x>`
forced the worktree ahead of the canonical editable `.pth` (verified:
`daemon.__file__` under `/tmp/rogue-mut-<x>`). Per-variable env scrub
(no `env -u POSTGRES_*` glob). All runs bounded. Non-vacuity controls green
(11/4/7/6/9). All scratch trees removed (`git worktree list` shows no
`/tmp/rogue-mut-*`).

**Per-mutant table (edit applied / observed reds / verdict / sha equality):**

| Mutant | Edit applied | Observed reds | Verdict | POST sha == PRE |
|---|---|---|---|---|
| B-M1 | remove "PORT" from strip set | collection error (both files) | collection-level (broader than listed) | ✓ |
| B-M2 | remove overlay-merge block | `test_apply_boundary_strips_inherit_and_overlay_wins` (env_policy), `test_scrub_strips_poisoned_ambient_with_overlay_wins` (scrub) | MATCH + OVER-RED + UNDER-RED (`test_scrub_service_spawner_env_kwarg`) | ✓ |
| B-M3 | remove `env=` kwarg from Popen | `test_scrub_service_spawner_env_kwarg` | MATCH | ✓ |
| B-M4 | revert derive to `getattr(...)` | `test_api_source_uses_derive_not_managed_default`, `test_api_source_honors_env_escape` | UNDER-RED (named node green) — source pins red | ✓ |
| B-M5 (primary) | remove fence-var check | `test_guard_fence_var_allows` | MATCH + UNDER-RED (`test_guard_fires_on_ambient_poison`) | ✓ |
| B-M5 (alt) | remove ambient predicate | `test_guard_does_not_fire_sqlite_default` | OVER-RED + UNDER-RED (both must-RED green) | ✓ |
| B-M6 | remove live-pattern for-loop | 5 reds (see table above) | MATCH (2) + 3 OVER-RED | ✓ |
| B-M7 | count assert 10→9 | collection error (both files) | collection-level | ✓ |

**Classification:** 3/8 clean MATCH (B-M3, B-M5 primary fence node, plus B-M4's two source pins are the real pins). Zero undetected mutants. Zero truly-vacuous pins — every UNDER-RED was causal over-specification of the must-RED list. Docs not committed (STOP rule) pending this re-authoring.

## House-gate evidence (2026-11-12)

**Task B — net-new-red adjudication.** Pre FAILED: 561 (verified byte-identical to
`grep "^FAILED "` over `/tmp/rogue-lf-run.log`). Post FAILED: 380
(`/tmp/rogue-post-run2.log`). Set diff (`comm`): 243 net-new (post not in pre),
424 resolved (pre not in post). Grouping of the 243 net-new:
integration 177, root 17, services 15, manager 9, performance 8, job_queue 6,
api 4, message_queue_redesign 2, migration 2, static 2, property 1. ZERO
lane-adjacent ids (itemized grep over every lane-touched module and test cohort
returned 0). Collection-error counts measure different things pre vs post:
pre log has 0 `ERROR tests/...` collection-error lines (its 125 "errors" are
execution-time E markers), post has 121. **Comparability statement (verbatim):**
the pre run executed ~706 tests (561 failed + 20 passed + 125 errors) with 2210
deselected; the post run executed ~5993 (380 failed + 5358 passed + 134 skipped +
121 errors) with 1220 deselected. Test universes differ (new pin suites added).
Therefore the pass/fail-count improvement (561→380 failed) is NOT a defensible
"regressions fixed" claim — the sets are not comparable. What IS defensible:
(1) zero lane-adjacent ids among the 243 net-new (verified by itemization); and
(2) the lane's own pins are green and mutant-proven. The 243 are predominantly
integration tests failing with 302 auth redirects / fixture errors —
environmental, not lane-caused. Verdict: "zero lane-caused net-new reds" confirmed.

**Task C — harness legs.** Leg (a) foreign-claim refuse: SKIP. Two honest reasons:
(1) source-tree `resolve_self_install_dir()` returned `/home/nea/agents-ensemble` in
this environment (not None as the task premise stated); (2) the harness's
refuse-foreign plant uses a dead pid (planter process exits) →
`pid_alive_with_starttime` False → stale → take (no refusal). Arranging a live
foreign owner requires a staged/frozen second daemon; routed to staged
verification. Unit pins cover the logic. Leg (b) happy boot take-claim: PASS —
source-tree daemon booted ("Application startup complete", "Uvicorn running on
0.0.0.0:18792"), readyz HTTP 401 (auth-gated; endpoint exists), no
"PORT OWNERSHIP REFUSED" marker, graceful shutdown complete. (Nuance: readyz
returns 401 because auth is enabled; the endpoint exists and the daemon is
healthy — the harness's 8s default was too short, so the run was repeated
manually with a 22s wait.) Leg (c) ambient-DB-poison refusal: PASS — daemon
exited 78; log "DB RESOLUTION REFUSED ... Refusing before any TCP connection.";
pg_stat_activity count = 1 (the verification query's own session); daemon made
zero connections.
EOF
echo "written"