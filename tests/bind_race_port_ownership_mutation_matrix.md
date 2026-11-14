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

## A2 AH/LH mutation-matrix execution (rogue-containment-p2, S6)

Venue: scratch worktree `/tmp/rc-p2-va2-a7f3`, DETACHED @ **`cc154c308`**
(A2 tip; A2 landed `daemon/services/port_holder.py` 345 lines + gate/lineage/alert
extensions), `uv sync --frozen`, `daemon.__file__` verified inside the venue,
per-variable PG scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
-u POSTGRES_HOST -u POSTGRES_PORT`), `timeout 300` + `--timeout=120`, pin files
`tests/unit/test_port_holder_identity.py` (9 pins) +
`tests/unit/test_port_ownership_pins.py` (30 pins incl. the A2 gate-level +
lineage + LH extensions). Restore `git checkout -- <file>`, sha256 PRE==POST
every row, green re-run after restore every row.

Pre-mutation digests (all verified byte-exact post-restore):

```
390cc9d90784ca357b98543397fa0addb854dd2a03622a383b262b007023bb06  daemon/services/port_holder.py
c651276eba3c5430ca5f32f04f30e512f8f516e8a7a9a39c4688516f3a4352ca  daemon/services/port_ownership.py
455b155e0e118ce0b7740830f360814ae8724abecbbdb850ba3dc5af94a037c1  daemon/__main__.py
675a854a4304712b5b47da5ad13e4a6a1bc78334813ebec88f585825f38c0edb  scripts/watchdog-watcher.sh
```

**Clean-tree control:** both pin files GREEN unmutated — **39 passed**.

### Rows (row × mutation × pins reddened × restore × green-after)

| Row | File mutated | Counter-mutant (ONE behavior) | Pins REDDENED | Restore | Green-after |
|---|---|---|---|---|---|
| AH-M1 | port_ownership.py | gate attribution loop `holder_install = h.install_dir` → `= None` (first site: every holder unattributable ⇒ degrade) | `test_AH1_foreign_holder_without_claim_refuses` | TRUE | 39 passed |
| AH-M2 | port_ownership.py | `write_refusal_marker` payload `"source"` key renamed ⇒ field dropped from the record | `test_AH1_…` (KeyError 'source'), `test_AH10_lineage_mismatch_refuses` | TRUE | 39 passed |
| AH-M3 | port_holder.py | both-tables-absent degrade `return []` → `raise RuntimeError` (degrade flipped to refuse) | `test_AH3_tables_absent_degrade` | TRUE | 39 passed |
| AH-M4 | port_ownership.py | holder-refusal block gated on claim ABSENCE (`if positive_foreign and existing_claim is None`) — claim-presence semantics | **NONE — 39 passed ⇒ FINDING F-1** | TRUE | 39 passed |
| AH-M4b (F-1 proof) | port_ownership.py | holder path deleted entirely (`if False and positive_foreign`) | `test_AH1_…` ONLY; **`test_AH4_stale_claim_plus_foreign_holder_refuses` STAYS GREEN** ⇒ AH-4's verdict is claim-covered, not holder-isolated | TRUE | 39 passed |
| AH-M5 | port_ownership.py | same-install branch appends holder to `positive_foreign` (same-install ⇒ foreign) | `test_AH5_same_install_holder_non_refuse` (SystemExit 78 raised) | TRUE | 39 passed |
| AH-M6 | __main__.py | `run_port_ownership_gate` call moved AFTER the `bind_socket_with_retry` try/except (post-ladder rewire; syntax-verified `ast.parse`) | `test_gate_refusal_never_enters_bind_retry` ("bind must NOT be reached" AssertionError) | TRUE | 39 passed |
| AH-M7 | — (rides AH-M6) | at the landed shape the holder check lives INSIDE `run_port_ownership_gate`; "move holder check after bind" = the same single placement move — executed once, evidence shared with AH-M6 | same as AH-M6 | — | — |
| AH-M8 | port_ownership.py | gate stale-degrade branch removed (`if not live:` → `if False and not live:`) | **NONE — 39 passed ⇒ FINDING F-2** | TRUE | 39 passed |
| AH-M8b (F-2 probe) | port_holder.py | `confirm_holder` fresh-match drops `h.starttime == starttime` | **NONE — 39 passed ⇒ FINDING F-2** | TRUE | 39 passed |
| AH-M9 | port_ownership.py | `_lineage_foreign` final comparison inverted (`!=` → `==`) | `test_AH9_lineage_matching_non_refuse`, `test_AH10_lineage_mismatch_refuses` | TRUE | 39 passed |
| AH-M10 | port_ownership.py | lineage check skipped in `try_claim` (`if False and _lineage_foreign(...)`); **E2-side AH-M19 (invocation-shape ⇒ non-foreign) is the same content per the E2↔A2 numbering map — rides this row** | `test_AH10_lineage_mismatch_refuses` | TRUE | 39 passed |
| AH-M11 | port_ownership.py | absent lineage treated as foreign (`if not recorded_sha: return True`) | `test_AH11_old_format_claim_non_refuse` + 2 accepted blast-radius (`test_E_ii_M1_same_install_takes`, `test_promote_shape_same_install_non_refuse` — old-format same-install take-over paths share `_lineage_foreign`) | TRUE | 39 passed |
| AH-M12 | port_ownership.py | unreadable self-parent ⇒ foreign (`if self_argv is None: return True`) | `test_AH12_self_parent_unreadable_non_refuse` | TRUE | 39 passed |
| AH-M13 | port_holder.py | hex port decode broken (`int(port_hex, 16)` → `int(port_hex)`) | `test_AH13_hex_port_decode_and_state_filter`, `test_AH13_v4mapped_tcp6_only` | TRUE | 39 passed |
| AH-M14 | port_holder.py | single-sample confirm (early `return True` before the re-walk) | `test_AH14_holder_vanishes_between_samples_no_refusal`, `test_AH14_liveness_only_sample2_would_false_confirm` | TRUE | 39 passed |
| AH-M15 | port_holder.py | sample-2 liveness-only (re-walk match result overwritten: `match = holder` — trusts sample 1, skips socket re-verification) | **NONE — 39 passed ⇒ FINDING F-3** | TRUE | 39 passed |
| AH-M16 | port_ownership.py | lineage tuple hashes FULL argv (normalizer bypassed: `tok = " ".join(argv)`) | `test_AH15_normalize_script_name_arg` (`h1 == h2 == h3` fails; `full != h1` fails) | TRUE | 39 passed |
| AH-M17 | port_holder.py | tcp6 skipped (`for fname in ("tcp",)`) — E2-side AH-10 / tcp4-only parse | `test_AH13_v4mapped_tcp6_only` (both-tables leg misses the `::ffff:` holder) | TRUE | 39 passed |
| AH-M18 | port_holder.py | fd-dir EACCES FABRICATES a holder (`holders[pid] = HolderRef(pid, first-unmatched-inode)`) — E2-side AH-11 | `test_AH3_fd_eaccess_degrade` (pid 9009 spuriously reported) | TRUE | 39 passed |
| LH-M1 | __main__.py | best-effort family-record guard removed (nested try/except collapsed to bare `raise`) | `test_LH1_write_refusal_family_record_best_effort` (OSError propagates) | TRUE | 39 passed |
| LH-M2 | port_ownership.py | `surface_refusal_marker` ERROR line drops family/source/evidence (format string + 3 args removed) | `test_LH2_surface_carries_family_source` | TRUE | 39 passed |
| LH-M3 | watchdog-watcher.sh | leg (d) removed from the main sequence (`check_refusal_marker` call commented out) | `test_LH3_watchdog_refusal_marker_latch` (no notify fired) | TRUE | 39 passed |

Score: **16 executed rows (+2 riders, +1 F-1 proof leg) — 13 REDDENED, 3
findings (F-1, F-2, F-3)**, zero restore failures, zero green-after failures.

### Findings (failed-to-redden — reported, not papered over)

* **F-1 (AH-4 pin conflation):** `test_AH4_stale_claim_plus_foreign_holder_refuses`
  monkeypatches `po.pid_alive_with_starttime` to global-True, so its planted
  foreign CLAIM is "live" and `try_claim` refuses via the CLAIM path with 78 —
  the verdict is over-determined. Even with the holder path DELETED (AH-M4b),
  AH-4 passes. The holder-evidence leg cannot be isolated by ANY holder-path
  mutant. Re-aim: assert `marker["source"] == "socket-holder"` (forces the
  holder path) or drop the global liveness monkeypatch. (POK
  `mutation-01-guards-need-counter-mutation` — positive-shape discipline.)
* **F-2 (AH-8 pin absence-shaped):** the module pin
  `test_AH8_recycled_pid_degrade` asserts `isinstance(res, list)` — true under
  ANY behavior; neither the gate's stale-degrade branch (AH-M8) nor the
  confirm-match starttime (AH-M8b) has a tight pin. Removal of both is green
  corpus-wide. Re-aim: gate-level pin with a foreign holder + dead starttime
  asserting NO refusal AND the specific "starttime mismatch (stale)" WARNING
  line; module-side: assert the holder IS reported by the walk (current
  intent) AND the gate filters it (needs the gate seam).
* **F-3 (AH-M15 distinction untestable on the landed fixture):**
  `confirm_holder`'s TAIL real-`/proc` liveness check
  (`pid_alive_with_starttime(4242, 123)` — fake-dead pid) returns False and
  shields every liveness-only mutant from confirming; the AH-14 module
  fixture cannot distinguish liveness-only from full-rewalk. AH-M14's
  unconditional single-sample DOES redden the false-confirm class. Re-aim:
   fixture with a REAL live pid (e.g. `os.getpid()` + a monkeypatched
  re-walk that drops the inode) so liveness-only confirms (RED) while
  full-rewalk refuses (GREEN).

### Spawn-safety audit (dispatcher addendum)

No AH/LH counter-mutant touches a process-spawning surface (no `bash.py`
pkill/kill overlay — that is the HY family's territory, sibling lane). The
only subprocess usage in the red legs is LH-3's own invocation of
`scripts/watchdog-watcher.sh` — unchanged by the LH-M3 mutant (which only
removes the leg-(d) call), with `WATCHDOG_NOTIFY_CMD="echo notified >> …"`
— a scratch sentinel that cannot match any real process. Zero real
pkill/kill reach from any executed red leg.

### Process note (discipline violation, disclosed)

One batch ran two mutation drivers CONCURRENTLY against the same scratch
tree (AH-M6's first, malformed attempt alongside AH-M17) — AH-M17's log
transiently recorded 2 extra SyntaxError-import failures from the mid-edit
`__main__.py`. Both affected rows (AH-M17, AH-M18) and the earlier
parallel-batch rows (AH-M2, AH-M3, AH-M5) were RE-RUN SERIALLY; all serial
evidence above is the authoritative set. AH-M6's first attempt also produced
a syntactically-invalid move (inserted inside the bind `try:` body) — caught
via `ast.parse` and re-executed with a corrected anchor; the recorded AH-M6
evidence is from the valid second execution.
