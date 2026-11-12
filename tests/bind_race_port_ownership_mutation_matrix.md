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
must-RED list, not a green-on-mutant core assertion.
