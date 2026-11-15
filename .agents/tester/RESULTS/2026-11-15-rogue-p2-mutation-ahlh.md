# rogue-containment-p2 — Gate 1b: AH/LH mutation matrix RE-RUN evidence

Run: 2026-11-15T01:0x–01:1xZ by worker (tester-gate 1b re-run commission).
Matrix: `tests/bind_race_port_ownership_mutation_matrix.md` @ tip `a7aec8582` (sha256 449b9a9f0d411b48…, byte-identical pre/post-deletion), read IN FULL incl. the gate-8 pin-robustness addendum (§237–290) and the normative spawn-seam recorder contract (sibling matrix `tests/bind_race_rogue_containment_mutation_matrix.md` §306–338).

## Incident disclosure (venue deviation, leader-authorized)

The dispatch-designated worktree `/home/nea/ensemble-src-wt-rogue-contain` was DELETED by an external actor mid-turn (~00:51Z; anomaly 2c52097b, cluster ad9184ac, 3 reports). Test-leader incident guidance then bound this re-run: (1) NO worktree re-creation at that path, (2) NO commits to `feature/rogue-port-containment` (finalize drift-guard owns the tip; `origin/latest` verified UNMOVED at `bf4bfde6e`), (3) remaining legs against a READ-ONLY `git archive a7aec8582 | tar -x` reconstruction (gate-2 worker precedent, provenance disclosed here). Zero git mutations were performed; nothing was committed to any branch.

## Venue + provenance

- Reconstruction: `git archive a7aec8582 | tar -x` → `/tmp/rc-p2-ahlh-g1b` (NO `.git`; byte-parity cross-checks: matrix sha == pre-deletion read `449b9a9f…`; `scripts/watchdog-watcher.sh` sha `675a854a…` == the matrix's own recorded digest §123).
- Interpreter: venue `uv sync --frozen` → `.venv/bin/python` (3.14.7); every run asserted `daemon.__file__` inside the venue (module identity).
- Restore discipline (archive-venue adaptation of the git-checkout pattern): byte-exact pristine copies per file; per-leg PRE==POST sha256 verified. The known `git archive`/git-checkout silent-no-op hazard is structurally excluded: no git steps exist in the loop — apply/verify-changed/restore are all plain file ops with sha proof.
- Source digests (current tip; differ from §117–124 which were recorded at cc154c308 pre-gate-8): port_ownership.py `4ef7c2f4…a1f6`, port_holder.py `cf5dd8c8…9831`, __main__.py `b4ebe4c4…c21a0` (full shas in per-row JSON), watchdog-watcher.sh `675a854a…b0edb`.
- Spawn-seam recorder (NORMATIVE per dispatcher + contract rule 1): autouse `_spawn_seam_recorder` installed VENUE-SIDE in BOTH pin files (record-instead-of-spawn over `asyncio.create_subprocess_exec/shell`; per-pin teardown fails on ANY recorded spawn = automatic gate failure). Clean baseline WITH the seam: **42 passed in 7.42s** (matches the recorded §278 baseline).
- Env discipline: per-variable PG scrub (`POSTGRES_DB/USER/PASSWORD/HOST/PORT`, `DATABASE_URL`, `PGHOST/PGPORT/PGUSER/PGPASSWORD` — never the `env -u POSTGRES_*` glob no-op), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, `ENSEMBLE_PORT_CLAIMS_DIR=/tmp/rc-p2-ahlh-g1b-claims` (scratch). No real sockets planted; no live-shape kills anywhere; ports 9797/9798/7979/8079 untouched.
- Invocation per leg: `timeout 120 <venue>/.venv/bin/python -m pytest tests/unit/test_port_holder_identity.py tests/unit/test_port_ownership_pins.py --timeout=90 -q --tb=line` (cwd=venue, `PYTHONPATH=<venue>`), matrix's --timeout=90 inside the 120s command wrapper.

## Per-mutant table (22 executed legs + 2 recorded riders)

| Row | File mutated (sha pre→mutant→post) | Observed RED | Expected red set (matrix + gate-8 addendum) | Restore | Green-after | Spawn-seam |
|---|---|---|---|---|---|---|
| AH-M1 | `port_ownership.py` `4ef7c2f4d505…`→`a892505c1e36…`→`4ef7c2f4d505…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH1_foreign_holder_without_claim_refuses`, `test_AH4_stale_claim_plus_foreign_holder_refuses`, `test_AH8_gate_stale_holder_degrades_with_warning` | test_AH1_foreign_holder_without_claim_refuses. addendum §231: + re-aimed AH-4; observed extra AH-8-gate (attribution stub suppresses the stale-degrade WARNING — same suppression family, accepted blast radius) | TRUE | 42 passed | 0 hits |
| AH-M2 | `port_ownership.py` `4ef7c2f4d505…`→`6ddfcdac802e…`→`4ef7c2f4d505…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH10_lineage_mismatch_refuses`, `test_AH1_foreign_holder_without_claim_refuses`, `test_AH4_stale_claim_plus_foreign_holder_refuses` | test_AH1 (KeyError 'source') + test_AH10. addendum §232: + re-aimed AH-4 — observed set matches exactly | TRUE | 42 passed | 0 hits |
| AH-M3 | `port_holder.py` `cf5dd8c8405e…`→`4fc2daea8946…`→`cf5dd8c8405e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH3_tables_absent_degrade` | test_AH3_tables_absent_degrade. exact match | TRUE | 42 passed | 0 hits |
| AH-M4 | `port_ownership.py` `4ef7c2f4d505…`→`79844f64b74c…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH4_stale_claim_plus_foreign_holder_refuses` | F-1 re-aim §222: the re-aimed AH-4. exact match (pre-re-aim this row was NONE/F-1) | TRUE | 42 passed | 0 hits |
| AH-M4b | `port_ownership.py` `4ef7c2f4d505…`→`e51041f7ef1f…`→`4ef7c2f4d505…` | rc=1: 2 failed, 40 passed, 1 warning — `test_AH1_foreign_holder_without_claim_refuses`, `test_AH4_stale_claim_plus_foreign_holder_refuses` | AH-1 + the re-aimed AH-4 (§223). exact match (the original F-1 discriminator now holds) | TRUE | 42 passed | 0 hits |
| AH-M5 | `port_ownership.py` `4ef7c2f4d505…`→`3727e76dacb1…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH5_same_install_holder_non_refuse` | test_AH5_same_install_holder_non_refuse (SystemExit 78). exact match | TRUE | 42 passed | 0 hits |
| AH-M6 | `__main__.py` `b4ebe4c4f39e…`→`a4fb670783bd…`→`b4ebe4c4f39e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_gate_refusal_never_enters_bind_retry` | test_gate_refusal_never_enters_bind_retry. exact match; AH-M7 rider shares this evidence (same placement move, per matrix) | TRUE | 42 passed | 0 hits |
| AH-M8 | `port_ownership.py` `4ef7c2f4d505…`→`df5b64276150…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH8_gate_stale_holder_degrades_with_warning` | F-2 re-aim §224: test_AH8_gate_stale_holder_degrades_with_warning. exact match (pre-re-aim NONE/F-2) | TRUE | 42 passed | 0 hits |
| AH-M8b | `port_holder.py` `cf5dd8c8405e…`→`7eca3626d908…`→`cf5dd8c8405e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH8b_confirm_rejects_recycled_pid_same_inode` | F-2 re-aim §225: test_AH8b_confirm_rejects_recycled_pid_same_inode. exact match (pre-re-aim NONE/F-2) | TRUE | 42 passed | 0 hits |
| AH-M9 | `port_ownership.py` `4ef7c2f4d505…`→`2e8dc3462dcd…`→`4ef7c2f4d505…` | rc=1: 2 failed, 40 passed, 1 warning — `test_AH10_lineage_mismatch_refuses`, `test_AH9_lineage_matching_non_refuse` | test_AH9_lineage_matching_non_refuse + test_AH10_lineage_mismatch_refuses. exact match (re-aimed AH-9 still reddens) | TRUE | 42 passed | 0 hits |
| AH-M10 | `port_ownership.py` `4ef7c2f4d505…`→`c4532500211b…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH10_lineage_mismatch_refuses` | test_AH10_lineage_mismatch_refuses. exact match; AH-M19 rider shares this evidence (E2↔A2 numbering map, per matrix) | TRUE | 42 passed | 0 hits |
| AH-M11 | `port_ownership.py` `4ef7c2f4d505…`→`320f879a1893…`→`4ef7c2f4d505…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH11_old_format_claim_non_refuse`, `test_E_ii_M1_same_install_takes`, `test_promote_shape_same_install_non_refuse` | test_AH11 + 2 accepted blast-radius (test_E_ii_M1_same_install_takes, test_promote_shape_same_install_non_refuse). exact match incl. recorded blast radius | TRUE | 42 passed | 0 hits |
| AH-M12 | `port_ownership.py` `4ef7c2f4d505…`→`04757958d1d6…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH12_self_parent_unreadable_non_refuse` | test_AH12_self_parent_unreadable_non_refuse. exact match | TRUE | 42 passed | 0 hits |
| AH-M13 | `port_holder.py` `cf5dd8c8405e…`→`cfa08f94ac2a…`→`cf5dd8c8405e…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH13_hex_port_decode_and_state_filter`, `test_AH13_v4mapped_tcp6_only`, `test_AH8_recycled_pid_degrade` | test_AH13_hex_port_decode_and_state_filter + test_AH13_v4mapped_tcp6_only. observed extra: re-aimed AH-8 module pin (walk returns empty under decode-break — the gate-8 §251 blast-radius pattern) | TRUE | 42 passed | 0 hits |
| AH-M14 | `port_holder.py` `cf5dd8c8405e…`→`fc7d0c58916b…`→`cf5dd8c8405e…` | rc=1: 4 failed, 38 passed, 1 warning — `test_AH14_holder_vanishes_between_samples_no_refusal`, `test_AH14_liveness_only_sample2_would_false_confirm`, `test_AH15_liveness_only_live_pid_would_confirm`, `test_AH8b_confirm_rejects_recycled_pid_same_inode` | test_AH14_holder_vanishes_between_samples_no_refusal. observed extra (accepted blast radius): the three re-aim-added confirm-False pins (AH-14-liveness-only re-aim, AH-15-liveness-only, AH-8b) — unconditional single-sample trips every confirm-False guard | TRUE | 42 passed | 0 hits |
| AH-M15 | `port_holder.py` `cf5dd8c8405e…`→`4a3aad350347…`→`cf5dd8c8405e…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH14_liveness_only_sample2_would_false_confirm`, `test_AH15_liveness_only_live_pid_would_confirm`, `test_AH8b_confirm_rejects_recycled_pid_same_inode` | addendum §258+§226: re-aimed test_AH14_liveness_only_sample2_would_false_confirm + test_AH15_liveness_only_live_pid_would_confirm + test_AH8b (accepted overlap). EXACT match; test_AH14_holder_vanishes stayed GREEN (shielded by design — the re-aimed discriminator works) | TRUE | 42 passed | 0 hits |
| AH-M16 | `port_ownership.py` `4ef7c2f4d505…`→`8806f7bca3da…`→`4ef7c2f4d505…` | rc=1: 3 failed, 39 passed, 1 warning — `test_AH15_normalize_script_name_arg`, `test_AH12_self_parent_unreadable_non_refuse`, `test_AH9_lineage_matching_non_refuse` | test_AH15_normalize_script_name_arg; dispatcher handoff: AH-9 growth. observed set = named AH-15-normalize + AH-9 (gate-8 growth, CONFIRMED) + AH-12 (blast radius: TypeError join(None) at port_ownership.py:443 — mutant breaks the normalizer None-contract; honest behavioral consequence) | TRUE | 42 passed | 0 hits |
| AH-M17 | `port_holder.py` `cf5dd8c8405e…`→`37e87630a3d7…`→`cf5dd8c8405e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH13_v4mapped_tcp6_only` | test_AH13_v4mapped_tcp6_only. exact match | TRUE | 42 passed | 0 hits |
| AH-M18 | `port_holder.py` `cf5dd8c8405e…`→`c50d994875eb…`→`cf5dd8c8405e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_AH3_fd_eaccess_degrade` | test_AH3_fd_eaccess_degrade (pid 9009 spuriously reported). exact match | TRUE | 42 passed | 0 hits |
| LH-M1 | `__main__.py` `b4ebe4c4f39e…`→`dc5cb7b21df6…`→`b4ebe4c4f39e…` | rc=1: 1 failed, 41 passed, 1 warning — `test_LH1_write_refusal_family_record_best_effort` | test_LH1_write_refusal_family_record_best_effort (OSError propagates). exact match | TRUE | 42 passed | 0 hits |
| LH-M2 | `port_ownership.py` `4ef7c2f4d505…`→`1fdee235054c…`→`4ef7c2f4d505…` | rc=1: 1 failed, 41 passed, 1 warning — `test_LH2_surface_carries_family_source` | test_LH2_surface_carries_family_source. exact match | TRUE | 42 passed | 0 hits |
| LH-M3 | `watchdog-watcher.sh` `675a854a4304…`→`dbc79bcf3007…`→`675a854a4304…` | rc=1: 1 failed, 41 passed, 1 warning — `test_LH3_watchdog_refusal_marker_latch` | test_LH3_watchdog_refusal_marker_latch (no notify fired). exact match | TRUE | 42 passed | 0 hits |

### Riders (no separate execution, per matrix record)

- **AH-M7** rides AH-M6 (at the landed shape the holder check lives INSIDE `run_port_ownership_gate`; 'move holder check after bind' == the same single placement move — executed once, evidence shared).
- **AH-M19** rides AH-M10 (invocation-shape ⇒ non-foreign is the same content per the E2↔A2 numbering map).

Score: 22 executed legs — **22/22 REDDENED** (0 findings, 0 non-reddening rows), 22/22 restores sha-verified TRUE, 22/22 green controls 42 passed, **0 spawn-seam hits across 44 pytest invocations** (all under the structural recorder), 0 timeouts.

### The three former findings (F-1/F-2/F-3) — re-aims CONFIRMED at a7aec8582

- **F-1**: AH-M4 (claim-absence gate) and AH-M4b (holder path deleted) BOTH now redden the re-aimed `test_AH4_stale_claim_plus_foreign_holder_refuses` (real-/proc liveness, holder-attributed source assertion).
- **F-2**: AH-M8 (stale-degrade silenced) reddens the new gate pin (WARNING-text absent, mutant logs 'vanished between samples' instead); AH-M8b reddens the new AH-8b confirm pin (recycled starttime accepted ⇒ confirm).
- **F-3**: AH-M15 reddens exactly the addendum's discriminator set — the re-aimed real-live-pid control pin, the AH-15-liveness-only pin, and AH-8b (accepted overlap); the shielded-by-design `test_AH14_holder_vanishes…` stayed GREEN under AH-M15 exactly as the addendum records.

### Honest blast-radius disclosures (observed ⊇ named; none vacuous)

- AH-M1: additionally reddened `test_AH8_gate_stale_holder_degrades_with_warning` (attribution stub suppresses the stale-degrade WARNING downstream — same suppression family the addendum already accepts for AH-4).
- AH-M13: additionally reddened the re-aimed `test_AH8_recycled_pid_degrade` (walk returns empty under decode-break — precisely the gate-8 §251 blast-radius pattern).
- AH-M14: additionally reddened the three re-aim-added confirm-False pins (unconditional single-sample subsumes them).
- AH-M16: additionally reddened `test_AH12_self_parent_unreadable_non_refuse` — raw evidence: `TypeError: can only join an iterable` at port_ownership.py:443; the mutant breaks the normalizer's None-contract, a real behavioral consequence (not an artifact).

## Cleanup rider (RECORDED per POK rogue-cleanup-rider-recorded-addendum)

Scratch venue `/tmp/rc-p2-ahlh-g1b` (archive reconstruction, no .git), claims dir `/tmp/rc-p2-ahlh-g1b-claims`, and spec/runner scratch dirs removed after evidence capture; raw leg outputs (66 files: per-row red/green transcripts + summary JSON), mutant specs, and this evidence file PRESERVED in the rescue pack `/tmp/rogue-p2-g1b-evidence-rescue-20261115T0057Z/` (never deleted). Venue files verified byte-pristine (4/4 source shas == pristine pool) immediately before removal.

## Commit — DEFERRED (incident order), ready-to-apply

Per test-leader incident guidance the branch is finalize-owned; NO commit was made. Ready-to-apply AFTER the finalize drift-guard clears (leader-authorized):

```bash
mkdir -p /home/nea/ensemble-src-wt-rogue-contain/.agents/tester/RESULTS && \
cp /tmp/rogue-p2-g1b-evidence-rescue-20261115T0057Z/RESULTS/2026-11-15-rogue-p2-mutation-ahlh.md \
   /home/nea/ensemble-src-wt-rogue-contain/.agents/tester/RESULTS/ && \
git -C /home/nea/ensemble-src-wt-rogue-contain add .agents/tester/RESULTS/2026-11-15-rogue-p2-mutation-ahlh.md && \
git -C /home/nea/ensemble-src-wt-rogue-contain commit --only -m 'test: rogue-p2 gate 1b — AH/LH mutation matrix re-run evidence' -- .agents/tester/RESULTS/2026-11-15-rogue-p2-mutation-ahlh.md
```
