# bind_race rogue-containment mutation matrix

Mutation-proven pin suite for the spawn-boundary scrub, rider (d) metrics
derive, DB-resolution guard, and pkill overlay guard (component B +
component A overlay).

Venue discipline (mutation-02 POK): scratch worktree at branch tip, own
venv; restore via `git restore <file>`; verify sha256 PRE==POST. Never
touch the canonical checkout.

## Mutants

| Mutant | File | Edit | Must RED |
|---|---|---|---|
| B-M1 | daemon/services/spawn_env_policy.py | Remove "PORT" from SPAWN_BOUNDARY_STRIP_ENV | `test_strip_env_exact_ten_names`, `test_scrub_strips_poisoned_ambient_with_overlay_wins`, `test_counter_mutation_would_redden` |
| B-M2 | daemon/services/spawn_env_policy.py | Remove overlay-merge block in `apply_spawn_boundary` (`if overlay: out.update(overlay)`) | `test_apply_boundary_strips_inherit_and_overlay_wins`, `test_scrub_service_spawner_env_kwarg` |
| B-M3 | daemon/tools/service_spawner.py | Remove `env=_apply_spawn_boundary(...)` kwarg from Popen (revert to inherit) | `test_scrub_service_spawner_env_kwarg` |
| B-M4 | daemon/api.py | Revert metrics derive to `getattr(config.limits, "metrics_listener_port", 9798)` | `test_derive_scratch_main_plus_one` (source pin) |
| B-M5 | daemon/ensemble_config.py | Remove ambient predicate (or fence-var check) in `assert_db_resolution_sanctioned` | `test_guard_fires_on_ambient_poison`, `test_guard_fence_var_allows` |
| B-M6 | daemon/tools/bash.py | Remove live-pattern check from `_pkill_overlay_violation` (always None) | `test_name_family_refused_unfenced`, `test_port_family_refused_unfenced` |
| B-M7 | daemon/services/spawn_env_policy.py | Change count assertion target 10 -> 9 (count gate bypass) | `test_strip_env_exact_ten_names` |

## Non-vacuity control

Clean tree => all pins GREEN. Counter-mutations redden only their listed
nodes; unlisted REDs are investigated.

## Restore verification

`git restore <file>` then `sha256sum` must equal the recorded digest for
each mutated file. Recorded digests at tip 715f4eedc:

```
65cd0ea7edc4fe9a718ca98ac24458e812af2af8b8883a488fbed2d4844ec094  daemon/services/spawn_env_policy.py
103a422bb460ca25e0b6ea8cd99b3c6fb295d66c1c8b68e481e653636f97a981  daemon/tools/service_spawner.py
d3579eb9bade9177eda79c7ad97d9b32aa2e2e55a9d8b202e7c6617eccbeee39  daemon/api.py
3d4e5cef8c42745621c4e4d53d0d14a165abb681b5cc1bf633a553b4badbebd6  daemon/ensemble_config.py
e3c2afc41b9554b08e55bccf7bf532875b7b418e5069d040591fef7d9acf3dc0  daemon/tools/bash.py
```
