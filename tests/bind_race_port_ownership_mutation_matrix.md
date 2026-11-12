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
/home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/test_port_ownership_pins.py tests/unit/test_port_ownership.py -q
# apply mutant (edit named file/line), re-run, expect listed REDs, then:
git restore daemon/services/port_ownership.py
sha256sum daemon/services/port_ownership.py   # == recorded digest
```

Pre-mutation sha256 (recorded at tip 715f4eedc):

```
942e62bdecdb8629444497a9568a1167c33532acce93c70c0b0e76c27a6e65d8
```

## Mutants

| Mutant | File | Edit (surviving behaviour removed) | Must RED |
|---|---|---|---|
| M1 | daemon/services/port_ownership.py | In `is_live_foreign`, replace the install_dir comparison with `False` (treat foreign install_dir as non-foreign) — removes the foreign-ness gate | `test_E_ii_refuse_foreign_live`, `test_E_ii_M1_same_install_takes` (cross) |
| M2 | daemon/services/port_ownership.py | In `pid_alive_with_starttime`, make a mismatched starttime still return True (remove starttime check) | `test_E_ii_refuse_foreign_live`, `test_E_ii_M2_dead_pid_stale_takes` |
| M3 | daemon/services/port_ownership.py | Remove the `is_live_foreign` foreign-ness check in `try_claim` (always take) | `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses` |
| M4 | daemon/services/port_ownership.py | In `try_claim`, refuse when a foreign claim is present is inverted (refuse on absent claim) | `test_E_iii_happy_take_claim` |
| M5 | daemon/services/port_ownership.py | Make `try_claim` raise instead of degrade when `claims_dir.mkdir` fails | `test_E_iii_M5_degrade_claims_dir_create_fails` |
| M6 | daemon/services/port_ownership.py | In `is_live_foreign`, treat same-install as foreign (`==` -> `!=`) | `test_promote_shape_same_install_non_refuse`, `test_promote_shape_M6_foreignness_flip_would_refuse` |

## Non-vacuity control

Clean tree (no mutant) => all pins GREEN. Each mutant reddens only its listed node(s);
any unlisted RED is investigated before sign-off.

## Restore verification

`git restore daemon/services/port_ownership.py` then `sha256sum` must equal the
recorded digest. Any mismatch => re-clone the scratch venue.
