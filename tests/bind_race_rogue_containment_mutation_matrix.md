# bind_race rogue-containment mutation matrix (component B, fix-round 2)

Mutation-proven pin suite for the **spawn-boundary scrub**, **proc_run seam**,
**pkill-overlay fence**, **DB-resolution guard**, **metrics-port derive** and
**transcript-marker gate**. Regenerated 2026-11-12 during fix-round 2 after the
original artifact was corrupted by a self-overwriting heredoc (B4); it now
contains ONLY this body — the port-ownership matrix lives in
`tests/bind_race_port_ownership_mutation_matrix.md`.

**Venue (mutation-02 POK):** scratch worktree `/tmp/rogue-mut-r2`
(`git worktree add --detach ... e5110224 1`), `PYTHONPATH=<venue>` with
`daemon.__file__` verified inside the venue, per-variable env scrub (never
`env -u POSTGRES_*`), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`,
`ENSEMBLE_PORT_CLAIMS_DIR` under /tmp, all runs bounded (`--timeout=90`).
Restore via `git checkout -- .`; sha256 PRE == POST verified for every
mutated file. Scratch tree removed after the run.

## Pre-mutation digests (venue tip e5110224 1)

```
617dae9867f2dcb53f3d5ff4342b70f09f60bb45f27ea95bac8c387aaa4b6f2a  daemon/services/port_ownership.py
1158010107f9e1a610488378a0c62218335c390983bbd6a2ce7c857b6b558a67  daemon/services/spawn_env_policy.py
b3d31acd83738f816ac3f3ab68a3c57b24801e89a1b51e9befb31fae44cae532  daemon/tools/service_spawner.py
27cbeb9ecf63dfa4e91d620919721f3552a0f17516693f90630d82d2802ecc6e  daemon/api.py
6673c46b535e091476ad94f44ecbcff3e10365d7f503dc623def489b9375c224  daemon/ensemble_config.py
a49c7abcaf560a2da1e2d7a2e0b75c9889561709e09480579b76581cdcb41edd  daemon/tools/bash.py
b7fd92cc58e12b318499c2d27badf9632422adecb80e5e792973a73a1ca15769  daemon/tools/proc_tools.py
036fa3f2ede7f6454c9fc13b06678a7ba88f2f8cc3b82c0162221f56b51fbe40  daemon/services/metrics_listener.py
```

## Mutants (observed reds — 2026-11-12 execution)

| Mutant | File | Edit (surviving behaviour removed) | Observed RED |
|---|---|---|---|
| B-M1 | spawn_env_policy.py | remove `"PORT"` from the exact strip set | collection error (module-level count assert 11 != 12) — loud, unmissable |
| B-M2 | spawn_env_policy.py | `if overlay: out.update(overlay)` → `if False:` | `test_apply_boundary_strips_inherit_and_overlay_wins`, `test_scrub_strips_poisoned_ambient_with_overlay_wins` |
| B-M3 | service_spawner.py | drop `env=_apply_spawn_boundary(...)` from `Popen` | `test_scrub_service_spawner_env_kwarg` |
| B-M4a | metrics_listener.py | remove the `derived > MAX_METRICS_PORT` guard (N8 overflow) | `test_derive_overflow_at_65535_disables_loudly` |
| B-M4b | api.py | revert to `getattr(config.limits, "metrics_listener_port", int(port)+1)` | `test_api_source_uses_the_single_derive_helper` |
| B-M5 | ensemble_config.py | remove the fence-var early return | `test_guard_fence_var_allows`, `test_predicate_strings_present` |
| B-M6 | bash.py | `_pkill_overlay_violation` → always `None` | 15 reds across both overlay files (name/unit/path/port/list/systemctl/substitution/xargs + the B6 pins) |
| B-M7 | spawn_env_policy.py | count assert 12 → 11 | collection error |
| **B-M8** | proc_tools.py | `_build_child_env` → `env = os.environ.copy()` (the B2 regression) | `test_proc_run_build_child_env_is_scrubbed`, `test_proc_run_real_spawn_channel` |
| **B-M9** | bash.py | restore overlay-as-fence (`del env` removed, overlay OR'd in) | `test_env_overlay_fence_is_IGNORED_B6`, `test_env_overlay_fence_without_ambient_still_refused`, `test_overlay_fence_does_not_allow_live_pattern_B6` |
| **B-M10** | bash.py | keep the fence key in the child overlay (`spawn_env_overlay = env`) | `test_bash_overlay_fence_key_is_stripped_from_child` |
| **B-M11** | spawn_env_policy.py | strip prefix `("PG",)` → `("ZZ",)` | 8 reds (3 bash real-spawn channels, 2 proc_run, service_spawner, `test_pg_prefix_family_stripped`, `test_pg_test_exemption_survives`) |
| **B-M12** | spawn_env_policy.py | remove the `PG_TEST_` exemption | 7 reds (both real-spawn families + `test_pg_test_exemption_survives`) |
| **B-M13** | (planted) | inject `<\|end\|>` into a tracked text artifact | `test_no_transcript_markers_in_tracked_text_files` (B4 gate) |
| **B-M14** | ensemble_config.py | N5 fail-closed `_sys.exit(78)` → `pass` | `test_guard_internal_error_fails_CLOSED_exit_78`, `test_guard_does_not_call_create_engine`, `test_guard_fires_on_ambient_poison`, `test_main_boot_preflight_call_site_exits_78_before_load` |

Causal notes:
* B-M1/B-M7 trip the module-level count assert → collection-level detection
  (kept deliberately: the loudest possible signal).
* B-M4b reddens the source pin only; B-M4a covers the behavioural edge.
* B-M6's 15-red blast radius is expected: one pure function backs every
  overlay assertion in both files.
* B-M8 is the reviewer's exact regression shape (reverting the proc_run
  scrub) — it now reddens two pins, including a REAL spawned-child env probe.

## Non-vacuity control

Clean tree => `tests/unit/test_spawn_boundary_scrub.py`
`tests/unit/test_spawn_env_policy.py` `tests/unit/test_pkill_overlay_guard.py`
`tests/unit/test_pkill_overlay_pins.py` `tests/unit/test_db_resolution_guard.py`
`tests/unit/test_metrics_port_derive.py` `tests/unit/test_transcript_marker_gate.py`
all GREEN at the venue tip (160 + 19 + 1 passed across the fix-round-2 focused
runs).

## Restore verification

`RESTORE_EQUAL` = true for every mutated file (all 9 digests byte-identical to
the pre-mutation values above). No scratch tree left behind
(`git worktree list` shows no `/tmp/rogue-mut-r2`).

## House-gate evidence — CANONICAL claim set (N6)

### (a) Full-pack PRE baseline — **NON-COMPARABLE** (different test universe)

Artifact: `/tmp/rogue-containment-baseline-clean.log`
sha256 `b57680585aab7fea56e8216d48f5e7997f29f47610a1fc015d2cfc40eb4c605c`
Summary line: `541 failed, 25265 passed, 113 skipped, 5 xfailed, 2234 warnings, 125 errors in 857.98s`
(exit 3). The post-side full-pack artifact
`/tmp/rogue-post-run2.log` (sha256 `d0125e518de483fc31f23e37c5fb30c1277014b6af49e4565f0b2b5cfc030e50`)
reports `380 failed, 5358 passed, 134 skipped, 1220 deselected, 121 errors`.
**The universes differ** (25k vs 5.9k executed; deselection counts 2234 vs
1220), so a 541→380 delta is **NOT** a "regressions fixed" claim. Labeled
NON-COMPARABLE and not used for adjudication.

### (b) SAME-INVOCATION cohort — the only comparable claim

Both artifacts come from the identical command
(`pytest -q -n 8 --dist=loadfile ... tests/unit/services tests/unit/tools tests/unit/test_*.py`,
per-variable env scrub, `ENSEMBLE_SELF_ENV=dev`, claims dir under /tmp):

| side | tree | result line | failed-id artifact |
|---|---|---|---|
| PRE | `d5da2c766` | `301 failed, 20839 passed, 78 skipped, 1 xfailed, 771 warnings, 37 errors in 912.18s` | `/tmp/rogue-pre-failed.txt` sha256 `083605df89b25ea5760639e81bbbdafb716138c98342a70ca8e5f1b430887caa` |
| POST | `1a691248e` | `300 failed, 20899 passed, 78 skipped, 1 xfailed, 753 warnings, 37 errors in 930.58s` | `/tmp/rogue-post-failed.txt` sha256 `f781fdf23683261a50ff428a4bfab1b3b5172b9cf93a8b96f05a31116e045f66` |

Log artifacts: PRE `/tmp/rogue-pre.log` sha256
`a08827ad1c3ff473d8510524d2e45f91cdc8c8463218e3d45d03f03ffcf7a627`; POST
`/tmp/rogue-post.log` sha256
`1b90458a273459a93cde2e7ae0b4f2e80cb435e3fab24f939af0a00fa6c35a3b`.

Set diff (sorted unique failed ids): **9 net-new** (POST-only), **10 resolved**
(PRE-only, reverse-flakes).

Net-new (all in ONE file, all pass in isolation at the POST tip — 20/20 in
`tests/unit/tools/test_prompt_reload_tool.py` on re-run):

```
tests/unit/tools/test_prompt_reload_tool.py::TestInfoLine::test_fingerprint_changed_true_on_db_change
tests/unit/tools/test_prompt_reload_tool.py::TestInfoLine::test_info_line_on_success
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_on_forced_reload
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_on_no_change
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_on_reloaded
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_on_reloaded_db
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_on_unproven
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_marker_token_equals_structured_outcome_everywhere
tests/unit/tools/test_prompt_reload_tool.py::TestMarkerContract::test_zero_marker_on_refusals_and_failed
```

Resolved (reverse-flakes, not lane-adjacent): 4 × `test_filesystem_walk_guardrails`
/ `test_filesystem_workdir`, `test_loop_repairer::test_repair_summary_failure_returns_original_messages`,
2 × `test_paused_auto_resume_fallback`, `test_scheduler_deferred_mechanism_probe`,
`test_symptom_repair_engine`.

**Adjudication:** ZERO lane-adjacent net-new ids. The 9 net-new are a
`--dist=loadfile` ordering/load artifact in one pre-existing file (green when
run in isolation, green in the focused fix-round-2 runs). Error counts are
identical (37 / 37).

### (c) Superseded figures

Earlier revisions of this artifact cited `pre 561 / post 380 / 243 net-new`.
Those came from a mixed-universe run whose pre-side artifact
(`/tmp/rogue-lf-run.log`) no longer exists, and the reviewer's walk showed the
two sides did not execute the same test set. **They are withdrawn.** The only
canonical claim set is (a) as NON-COMPARABLE + (b) as the comparable cohort
above. No `243` / `380` / `561` figure is used for adjudication anywhere in
this lane.
