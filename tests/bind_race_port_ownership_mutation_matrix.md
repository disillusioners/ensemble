# bind_race port-ownership mutation matrix (component A, fix-round 2)

Mutation-proven pin suite for `daemon/services/port_ownership.py`, its gate
wiring, and `tests/unit/test_port_ownership.py` /
`tests/unit/test_port_ownership_pins.py`.

**Venue (mutation-02 POK):** scratch worktree `/tmp/rogue-mut-r2` at tip
`e5110224 1`, `PYTHONPATH=<venue>` (verified `daemon.__file__` inside the
venue), per-variable env scrub, claims dir under /tmp, all runs bounded
(`--timeout=90`), restore via `git checkout -- .` with sha256 PRE == POST.

## Pre-mutation digest

```
617dae9867f2dcb53f3d5ff4342b70f09f60bb45f27ea95bac8c387aaa4b6f2a  daemon/services/port_ownership.py
9c381fec62b673441c34802573f4c3e048c23e891dba2a3ce5a1b120acb140e3  daemon/__main__.py
27cbeb9ecf63dfa4e91d620919721f3552a0f17516693f90630d82d2802ecc6e  daemon/api.py
```

## Digest refresh — latest-absorption merge (tip `becc9884c`, 2026-11-12)

`origin/latest 6b644f770` (79 commits: prompt-assent P1.5,
ram-auto-termination, live-views loopback) was merged into the lane.
Digests re-recorded at the new tip; **1 of 3 MOVED** (`api.py` — the
ram-auto-termination lifespan wiring; `port_ownership.py` / `__main__.py`
are ours-only files the merge did not touch).

| file | old (`25d6c56d7`) | new (`becc9884c`) | moved |
|---|---|---|---|
| daemon/services/port_ownership.py | `617dae98…b6f2a` | `617dae98…b6f2a` | no |
| daemon/__main__.py | `9c381fec…140e3` | `9c381fec…140e3` | no |
| daemon/api.py | `27cbeb9e…2ecc6e` | `e93ee522…77a4c` | **YES** |

Re-run at the merged tip (venue `/tmp/rg3-mut`, detached @ `becc9884c`,
`PYTHONPATH=<venue>` with `daemon.__file__` verified inside the venue,
per-variable env scrub, claims dir under /tmp, `--timeout=90`,
command-level `timeout 240`, restore via `git checkout --`,
PRE==POST bundle sha every leg — all TRUE; clean control: the same
7-file pin set, **96 passed**; venue porcelain empty at close; venue
removed):

| Leg | File mutated | Observed RED | Restore |
|---|---|---|---|
| P-M7 | port_ownership.py | **7** — the claimed 3 (`test_B1_two_process_foreign_claim_is_visible_and_refused`, `test_claims_dir_is_host_global_not_install_scoped`, `test_N3_gate_never_writes_under_the_install_root`) + 4 accepted blast-radius | TRUE |
| P-M9 | port_ownership.py | **exactly the 3 claimed** | TRUE |
| P-M10 | __main__.py | **exactly the 1 claimed** (`test_gate_refusal_never_enters_bind_retry`) | TRUE |
| P-M12 | api.py | **exactly the 1 claimed** (`test_B3_daemon_never_releases_claim_eagerly`; the mutant anchor — the lifespan `yield` block — survived the merge intact) | TRUE |

Zero vacuous pins at the merged tip.

## Mutants (observed reds — 2026-11-12 execution)

| Mutant | Edit (surviving behaviour removed) | Observed RED |
|---|---|---|
| P-M1 | `is_live_foreign` final `return True` → `False` (drop the foreign gate) | 6: `test_E_ii_refuse_foreign_live`, `test_E_iii_M4_plant_foreign_refuses`, `test_try_claim_refuse_foreign`, `test_gate_refuses_live_foreign_claim_and_writes_marker`, `test_B1_two_process_...`, `test_is_live_foreign_foreign_live` |
| P-M2 | remove the starttime mismatch check | 2: `test_E_ii_M2_dead_pid_stale_takes`, `test_pid_alive_with_starttime_wrong_starttime` |
| P-M3 | `if is_live_foreign(...)` → `if False and ...` | 5 (refusal family + the two-process pin) |
| P-M4 | invert the verdict (`not is_live_foreign`) | 9 (both installation classes) |
| P-M5 | claims-dir create failure → `raise OSError` | 1: `test_E_iii_M5_degrade_claims_dir_create_fails` |
| P-M6 | same-install `==` → `!=` | 12 (whole foreign-ness blast radius) |
| **P-M7** | `_claims_dir_for` → install-scoped (`<install>/data/port-claims`) — **the B1 regression** | 7 incl. `test_B1_two_process_foreign_claim_is_visible_and_refused`, `test_claims_dir_is_host_global_not_install_scoped`, `test_N3_gate_never_writes_under_the_install_root` |
| **P-M8** | `validate_claim_port` → always `None` (N8 disabled) | 3: `test_gate_port_zero_refuses_claim_loudly`, `test_try_claim_port_zero_writes_nothing`, `test_validate_claim_port_edges` |
| **P-M9** | `surface_refusal_marker` → immediate `False` (B5 reader disabled) | 3: `test_gate_surfaces_prior_refusal_once`, `test_surface_refusal_marker_reports_once_and_rotates`, `test_surface_refusal_marker_unparseable_is_loud` |
| **P-M10** | delete the `run_port_ownership_gate(host, port)` call in `__main__` (B7(ii) wiring) | 1: `test_gate_refusal_never_enters_bind_retry` |
| **P-M11** | restore the canonical-LIVE fallback in `resolve_self_install_dir` (the N3 defect) | 1: `test_N3_self_install_dir_never_falls_back_to_live_root` |
| **P-M12** | re-introduce an aliased `release_claim` call into the api lifespan (B3) | 1: `test_B3_daemon_never_releases_claim_eagerly` |
| **P-M13** | `_normalize_claim_host` → raw host (keying) | 8 (wildcard/loopback collision + claim readback family) |

Causal notes:
* P-M7 is the reviewer's exact blindness shape; the pin that catches it is the
  TWO-PROCESS end-to-end test resolving via `XDG_RUNTIME_DIR` (NOT the
  `ENSEMBLE_PORT_CLAIMS_DIR` override), so install-scoping is causal.
* P-M10 covers "the gate exists but nothing calls it" (the reviewer's second
  mutation gap); P-M11/P-M12 cover the N3/B3 regressions.
* P-M1/P-M3/P-M6 red sets are the accepted natural blast radius of the shared
  foreign-ness predicate.

## Non-vacuity control

Clean tree => `tests/unit/test_port_ownership.py` +
`tests/unit/test_port_ownership_pins.py` GREEN (44 passed at the venue tip).

## Restore verification

`RESTORE_EQUAL` = true for every mutated file (byte-identical pre/post).
Scratch venue removed; `git worktree list` shows no `/tmp/rogue-mut-r2`.

**P-M12 re-verified at the final tip** (`cf391ccda`) after the api.py comment
rewording, with a clean-baseline control: clean tree => `-k B3` GREEN (1
passed); mutant applied => RED (1 failed); restore sha ==
`27cbeb9ecf63dfa4e91d620919721f3552a0f17516693f90630d82d2802ecc6e`.
This closes the vacuity risk of the first attempt (whose venue predated the
comment change).

## Residuals (carried, not closed)

* **Same-UID claim deletion** — any process as the same uid may unlink or
  overwrite a claim file. The boundary is the uid.
* **Same-install H1** — a rogue executing the same install's binary resolves
  the same `install_dir` and is non-foreign by design (D2).
* **PORT=0** — the gate refuses to claim (loud ERROR) and boot proceeds
  without ownership enforcement (N8 policy); `*-0.json` files can no longer
  be created.
