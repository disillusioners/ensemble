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
2be680f42a130d9029c5021e9244bcb39eeff5059af6cb7732250ec80fc69544  daemon/tools/proc_tools.py
036fa3f2ede7f6454c9fc13b06678a7ba88f2f8cc3b82c0162221f56b51fbe40  daemon/services/metrics_listener.py
```

## Digest refresh — latest-absorption merge (tip `becc9884c`, 2026-11-12)

`origin/latest 6b644f770` (79 commits) merged into the lane. Digests
re-recorded at the new tip; **3 of 8 MOVED** — `api.py` (ram-guard
lifespan wiring), `bash.py` + `proc_tools.py` (RAM arm added upstream AND
the merge integration seams). The other five are ours-only files the
merge did not touch.

| file | old (`25d6c56d7`) | new (`becc9884c`) | moved |
|---|---|---|---|
| daemon/services/port_ownership.py | `617dae98…b6f2a` | `617dae98…b6f2a` | no |
| daemon/services/spawn_env_policy.py | `11580101…58a67` | `11580101…58a67` | no |
| daemon/tools/service_spawner.py | `b3d31acd…cae532` | `b3d31acd…cae532` | no |
| daemon/api.py | `27cbeb9e…2ecc6e` | `e93ee522…77a4c` | **YES** |
| daemon/ensemble_config.py | `6673c46b…5c224` | `6673c46b…5c224` | no |
| daemon/tools/bash.py | `a49c7abc…b41edd` | `4b1f45d8…8e9a76` | **YES** |
| daemon/tools/proc_tools.py | `2be680f4…fc69544` | `aa206ae5…148d98b` | **YES** |
| daemon/services/metrics_listener.py | `036fa3f2…51fbe40` | `036fa3f2…51fbe40` | no |

Re-run at the merged tip (venue `/tmp/rg3-mut`, detached @ `becc9884c`,
`PYTHONPATH=<venue>` verified, per-variable scrub, claims dir under /tmp,
`--timeout=90`, `timeout 240`, restore via `git checkout --`,
PRE==POST bundle sha every leg — all TRUE; clean control 96 passed on the
7-file pin set; venue porcelain empty at close; venue removed):

| Leg | File mutated | Observed RED | Restore |
|---|---|---|---|
| B-M8 | proc_tools.py | **exactly the 2 claimed** (`test_proc_run_build_child_env_is_scrubbed`, `test_proc_run_real_spawn_channel`) | TRUE |
| B-M9 | bash.py | **exactly the 3 claimed** B6 pins | TRUE |
| B-M13 | (planted `docs/runbooks/upgrade-drills.md`) | **exactly the 1 claimed** (`test_no_transcript_markers_in_tracked_text_files`) | TRUE |
| B-M14 | ensemble_config.py | **exactly the 4 claimed** | TRUE |

Zero vacuous pins at the merged tip. The B-M8/B-M9 mutant anchors
survived the merge verbatim (the scrub call and the ambient-only fence
block were not rewritten by the resolution).

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

**N9 (post-matrix):** the proc_run overlay-guard import fallback was made
loud (ERROR on every affected call) and pinned by
`test_proc_run_guard_import_failure_is_loud_fail_open`; the proc_tools digest
above is the final-tip value (the N9 edit landed after the mutant run and does
not touch any mutated seam).

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

---

# BF + HY mutation matrices — rogue-containment-p2 (S6 execution, component E2)

Executed 2026-11-14 at **pin sha `26acaf702`** (the B2 landing commit; the venue was
detached at this FIXED sha so the concurrent A2 coder's later commits on the shared
branch are invisible to the run). Executor: developer-verify lane (BF+HY families;
AH rows live in `tests/bind_race_port_ownership_mutation_matrix.md` per the E2
§"Mutation-matrix artifacts" routing — § SEQUENCING step 3's "both files" is
satisfied by the AH executor on that second file).

## Venue

- `git worktree add --detach /tmp/rc-p2-vb2-k7f3 26acaf702`; `uv sync --frozen`.
- Module identity proven before ANY run:
  `<venue>/.venv/bin/python -c "import daemon, tests; print(daemon.__file__)"`
  ⇒ `/tmp/rc-p2-vb2-k7f3/daemon/__init__.py` (inside the venue).
- Per-row run command: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_AUTH_ALLOWED_ORIGINS
  -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET
  -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_ENABLED
  -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE -u ENSEMBLE_AUTH_ISSUER
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 timeout 300 .venv/bin/python -m pytest
  <explicit test files> -q --timeout=120 --tb=short` (per-variable scrubs — NEVER
  `env -u POSTGRES_*`; the ENSEMBLE_AUTH_* scrubs fence the known ambient-issuer
  environmental red, pre-existing, not a matrix row). No whole-tree pytest runs;
  no live DBs; no real binds; the demo-port token and both live-port tokens never
  bound.
- Restore per row: `git -C <venue> restore <file>`; sha256 PRE == POST verified
  against the venue census below for EVERY mutated file.

## Pre-mutation digests (venue tip 26acaf702)

```
9c0bc42054398b8a653d30a51531c30be2e22c680d129886c28a3a4defcf9042  daemon/ensemble_config.py
a81c3c8a4fa07fc5454ff028e7c0ff81db7f1fa89a1045f8b5cf893a3a3cfd57  daemon/tools/bash.py
dc57fdf28c6f543efefc65246937433b13c91ca790568a35d454714b5a8c815f  daemon/tools/proc_tools.py
28919557fa6376ffe05644576279b0e4efd272ede2c0bb790be304b5d4191490  daemon/repositories/factory.py
bbf2170d1be61ce32a27cfc8a74f7fbbcd5bc8c9f51f2b850398360e62ba7361  daemon/auth/cli.py
```

## Clean-tree control (non-vacuity)

All four pin files, unmutated, at `26acaf702`:
**73 passed** (`tests/unit/test_prod_dsn_fence.py` 26 + `test_db_resolution_guard.py` 18
+ `test_pkill_overlay_guard.py` 16 + `test_pkill_overlay_pins.py` 13).
Re-confirmed green after EVERY row's restore and once more at venue close
(73 passed, tracked porcelain empty, all five digests == census).

## Family BF — tier-1 prod-DSN fence (pin files: test_prod_dsn_fence.py + test_db_resolution_guard.py, 44 nodes)

Row runs execute BOTH files so control pins are exposed to the mutant too.

| Row | Mutant applied (one behavior neutered) | File | Observed RED (failing node ids) | Red evidence (assertion substring) | Restore | Green after |
|---|---|---|---|---|---|---|
| **BF-M1** | tier-1 prod-name branch DELETED: `if resolved_postgres_db_name(ensemble_config) == "ensemble_prod":` → `if False:` | ensemble_config.py | **14**: `test_p1_tier1_prod_name_refused_for_non_live_identities[sandbox|dev|demo]`, `test_p3_env_wins_prod_override_refused`, `test_p4_sanctioned_live_identity_proceeds_with_info[explicit-explicit|auto-derived]`, `test_p4b_real_live_shape_env_wins_sanctioned`, `test_p5_unresolved_identity_degrades_loudly_and_proceeds`, `test_p6_identity_resolver_raise_fails_closed_exit_78`, `test_p7_ambient_allow_does_not_exempt_prod_name`, `test_tier1_flip_side_by_side`, `test_boot_preflight_refuses_prod_dsn_pre_tcp`, `test_cli_resolve_engine_refuses_and_writes_no_artifact`, `test_prod_dsn_predicate_strings_present` | 8× `Failed: DID NOT RAISE <class 'SystemExit'>`; 3× `assert 'DB RESOLUTION SANCTIONED (rogue-containment)' in ''`; 1× `assert 'resolved_postgres_db_name(ensemble_config) == "ensemble_prod"' in ...` (strings pin — the predicate literal left the source); 1× `RuntimeError: engine must not be constructed on refuse path` (P10 preflight sentinel) | TRUE | 44 passed |
| **BF-M2** | live carve-out REMOVED: `if marker == "live":` → `if False:` (live falls through to the None-degrade path) | ensemble_config.py | **3**: `test_p4_sanctioned_live_identity_proceeds_with_info[explicit-explicit]`, `[auto-derived]`, `test_p4b_real_live_shape_env_wins_sanctioned` | `assert 'DB RESOLUTION SANCTIONED (rogue-containment)' in 'WARNING ... DB RESOLUTION DEGRADED (rogue-containment) ... proceeding WITHOUT positive live proof ...'` | TRUE | 44 passed |
| **BF-M3** | env-wins DELETED in `resolved_postgres_db_name`: `return os.environ.get("POSTGRES_DB", config.postgres.db).strip().lower()` → `return config.postgres.db.strip().lower()` | ensemble_config.py | **4**: `test_p3_env_wins_prod_override_refused`, `test_p4b_real_live_shape_env_wins_sanctioned`, `test_p7_ambient_allow_does_not_exempt_prod_name`, `test_env_wins_positive_control_scratch_name` | 3× `AssertionError: assert 'ensemble' == 'ensemble_prod'` (predicate now reads the FILE tier); + P7's resolved-name leg | TRUE | 44 passed |
| **BF-M4** | ALLOW short-circuits the prod check: `if os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") == "1": return` inserted at the top of the prod-DSN gate | ensemble_config.py | **2**: `test_p7_ambient_allow_does_not_exempt_prod_name`, `test_prod_dsn_predicate_strings_present` | `Failed: DID NOT RAISE <class 'SystemExit'>` (ALLOW=1 bypassed the fence) + the strings pin's forbidden-read-shape assert (`assert 'os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW"' not in guard_src` → present) | TRUE | 44 passed |
| **BF-M5** | CLI exemption RESTORED: `assert_prod_dsn_sanctioned(ensemble_config)` deleted from `resolve_engine` | auth/cli.py | **2**: `test_cli_resolve_engine_call_site_ordering`, `test_cli_resolve_engine_refuses_and_writes_no_artifact` | `ValueError: substring not found` (structural ordering) + `RuntimeError: engine must not be constructed on refuse path` (behavioral — the CLI proceeded to engine construction on a prod-name json) | TRUE | 44 passed |
| **BF-M6** | fail-OPEN exception handler: the prod-dsn guard's `except Exception: ... _sys.exit(78)` → `return` | ensemble_config.py | **1**: `test_p6_identity_resolver_raise_fails_closed_exit_78` | `Failed: DID NOT RAISE <class 'SystemExit'>` (resolver raise proceeded silently) | TRUE | 44 passed |
| **BF-M7** | factory env-wins line REWRITTEN: `db = os.environ.get("POSTGRES_DB", pg.db)` → `db = pg.db` (the both-sites pin's NAMED counter-mutant, component-B §110 site) | repositories/factory.py | **1**: `test_prod_dsn_predicate_strings_present` | `assert 'db = os.environ.get("POSTGRES_DB", pg.db)' in inspect.getsource(create_postgres_engine)` → literal gone | TRUE | 44 passed |
| **BF-M8** | old unconditional tier-1⇒sanctioned short-circuit REINTRODUCED: `return` immediately after the `is_postgres` early-return (guard fully dead for postgres shapes) | ensemble_config.py | **13**: P1[sandbox|dev|demo], P3, P4×2, P4b, P5, P6, P7, `test_tier1_flip_side_by_side`, `test_cli_resolve_engine_refuses_and_writes_no_artifact`, `test_boot_preflight_refuses_prod_dsn_pre_tcp` | 8× `DID NOT RAISE SystemExit`; 3× SANCTIONED-line missing; 1× engine-sentinel RuntimeError. (The strings pin stays green — the predicate literal remains in source.) BF-1 reddens as required (P1×3 + tier1_flip) | TRUE | 44 passed |
| **BF-M9** | live carve-out DROPPED (two-site): `if marker == "live":` → `if marker == "live" and False:` AND `if marker in ("dev", "demo", "sandbox"):` → `if marker in ("dev", "demo", "sandbox", "live"):` | ensemble_config.py | **3**: `test_p4_sanctioned_live_identity_proceeds_with_info[explicit-explicit]`, `[auto-derived]`, `test_p4b_real_live_shape_env_wins_sanctioned` | 3× `SystemExit: 78` — the BF-2 catastrophe (a false live refusal downs live and every promote leg) | TRUE | 44 passed |

**BF-M9 first-cut mutant was a NO-OP (recorded honestly):** adding `"live"` to the
refuse tuple ALONE is unreachable-dead — the `if marker == "live"` sanction branch
returns before the tuple check is ever reached, so the tuple-only mutant ran GREEN
(44 passed under mutation). The corrected two-site mutant (carve-out recognition
neutered + live in the refuse tuple) is the faithful "drop the carve-out ⇒ live
shape refuses" shape and reddens exactly the 3 live-shape pins. No pin change
needed; the lesson is for mutant AUTHORS, not the pins.

**BF-7 strings-pin nuance (recorded honestly):** under BF-M3 the strings pin did
NOT redden although the env-wins literal left the helper's CODE —
`inspect.getsource` includes the helper DOCSTRING, which quotes the literal, so the
substring assert is prose-satisfiable for that particular mutant. The pin DOES
redden under its named counter-mutants (BF-M7 code-site drift, BF-M4 forbidden
read-shape). Same weakness class as POK `mutation-01` (raw-text pins are
prose-satisfiable); noted as residual, not a matrix failure.

**Pre-proof count deltas (independent re-derivation, per commission):** B2's
pre-proof claimed BF-M1 = 4 red (P1×3 + BF-8) and BF-M2 = 3 red (P4×2 + P4b).
This run confirms BF-M2 = 3 exactly; BF-M1's faithful branch-deletion reddens 14
(every pin that requires the fence to FIRE or SPEAK on a tier-1 shape — P3, P4×2,
P4b, P5, P6, P7, P10-preflight, the CLI behavioral leg and the strings pin are
all legitimate collateral of branch deletion, not false reds). A1's pre-proof
(HY-M1 = 5, HY-M3 = 1) is confirmed exactly.

## Family HY — A1 hygiene, pkill overlay (pin files: test_pkill_overlay_guard.py + test_pkill_overlay_pins.py, 29 nodes)

Row runs execute BOTH files.

| Row | Mutant applied (one behavior neutered) | File | Observed RED (failing node ids) | Red evidence (assertion substring) | Restore | Green after |
|---|---|---|---|---|---|---|
| **HY-M1** | demo-port tuple entry REMOVED from the bash port-token loop (`("7979", r"\b7979\b")` deleted) | tools/bash.py | **5**: `test_7979_token_refused_unfenced_HY1`, `test_7979_allowed_with_ambient_fence_HY1`, `test_port_family_refused_unfenced`, `test_overlay_fence_does_not_allow_live_pattern_B6`, `test_list_form` | 5× `AssertionError: assert None == '7979'` (predicate returns None where "7979" required) | TRUE | 29 passed |
| **HY-M2** | delegation SEVERED: the function-local bash import + delegated call in `create_proc_tools` replaced with a local no-op (`_proc_pkill_violation = None`) | tools/proc_tools.py | **2**: `test_proc_lane_delegates_to_bash_predicate_HY2` (both faces), `test_proc_refusal_text_stays_ambient_only_HY4` (collateral — see INCIDENT below) | structural: `assert 'from daemon.tools.bash import _pkill_overlay_violation' in src` → import gone; behavioral: refusal text was `"Started process: proc-<id> ..."` instead of the refusal (stubbed shared function never consulted) | TRUE | 29 passed |
| **HY-M3** | dead env-overlay advertisement RESTORED in the bash refusal string (`in the daemon environment` → `in the tool env (env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"})`) | tools/bash.py | **1**: `test_bash_refusal_text_names_ambient_channel_only_HY3` | forbidden-phrase assert: `'env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}' is contained here: tool env (env={...})` | TRUE | 29 passed |
| **HY-M4** | overlay remedy ADDED to the proc refusal text (same advertisement) | tools/proc_tools.py | **1**: `test_proc_refusal_text_stays_ambient_only_HY4` | forbidden-phrase assert fired (identical shape to HY-M3) | TRUE | 29 passed |

### ⚠️ INCIDENT (disclosed, contained, auto-recovered) — HY-M2 red leg spawned a REAL process

The HY-2/HY-4 pins invoke the REAL tool coroutines; their docstrings assert
inertness ("the refusal returns BEFORE any spawn"). Under the HY-M2 mutant the
refusal does NOT fire, so the HY-4 pin's `proc_run(command="pkill -f <live-name>")`
executed a REAL pkill against the LIVE-DAEMON NAME PATTERN (timeout: none) as an
orphaned child of the pytest process. The live daemon's process cmdline carries
that name, so the stray pkill SIGTERM'd the LIVE daemon mid-run; systemd
auto-restarted it (livez green immediately after: `{"status":"alive"}`, fresh
uptime, self-reported v0.30.9), and the executor's in-flight shell died with the
daemon's process tree (that run's restore leg was interrupted; completed
immediately after, restore verified TRUE). No other infrastructure was harmed;
both ensemble listeners were present and serving after the restart. Exact-name
values of the live daemon are deliberately not spelled in this artifact — the
agent-side overlay fence treats them as live patterns, and this document should
stay paste-safe. **Lesson for future HY-M2 runs:** stub or cap the spawn path
(e.g. run HY-M2 against a proc_tools factory whose Popen is monkeypatched) — a
predicate-neutering proc mutant converts the "inert" text pins into real-spawn
hazards. Text-only mutants (HY-M3/HY-M4) keep the refusal firing and are safe,
as observed.

### Spawn-seam contract (normative — STRUCTURAL, not prose; added 2026-11-14)

ANY counter-mutant that can bypass a refusal gate on a spawn-capable tool (bash
tool, proc tool, or any future tool whose refusal precedes a process spawn)
MUST be executed under a **spawn-seam recorder (record-instead-of-spawn)**: the
tool's process-spawn surface (`asyncio.create_subprocess_exec` /
`asyncio.create_subprocess_shell` — resolved as module attributes of `asyncio`
at call time by `daemon/tools/bash.py` and `daemon/tools/proc_tools.py`) is
replaced per-pin by an **autouse fixture living INSIDE the pin file** that
captures argv and returns an inert stub (never execs, never forks, never
signals). Prose caution alone ("the refusal returns BEFORE any spawn") is
NON-COMPLIANT — that sentence is the exact assumption the incident falsified.

Contract rules:

1. **Seam in the pin file, not the harness.** The recorder is a module-local
   autouse fixture in each pin file (never `tests/conftest.py`), so every venue
   that checks out the pins carries the seam — kill-proof by construction, not
   by run-time discipline.
2. **Live shapes stay live.** The seam neuters the SPAWN, never the GUARD: pin
   command patterns must remain live-shaped (they still trip the refusal under
   green code) and must still REDDEN under bypassing mutants.
3. **Zero-exec proof rides the red.** The recorder's per-pin teardown FAILS any
   pin that recorded a spawn attempt, naming the recorded argv — a bypassing
   mutant yields a RED pin whose failure output is itself the evidence that
   nothing was executed.
4. **Row validity.** A refusal-gate counter-mutant row is valid only when its
   red evidence shows BOTH the refusal-text/assertion failure AND the recorder
   proving zero executed processes.

Current compliance: `tests/unit/test_pkill_overlay_pins.py` (HY-2/HY-3/HY-4
invoke real tool coroutines — recorder active on every pin) and
`tests/unit/test_pkill_overlay_guard.py` (pure-predicate pins today; recorder
held as a family tripwire). Both carry the autouse `_spawn_seam_recorder`
fixture; the full family runs 29/29 green under it.

## Row verdict roll (S6)

13/13 rows RED under their named counter-mutants (BF-M1..M9, HY-M1..M4) ·
13/13 restores sha256-verified PRE == POST (all five mutated files byte-identical
to the venue census at close) · clean-tree control 73 passed unmutated ·
venue removed after the run. **Zero rows failed to redden.** (The BF-M9 no-op
first-cut was a mutant-construction error, corrected and re-run — both attempts
are recorded above.)
