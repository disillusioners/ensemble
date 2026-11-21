# sonic-cpu-l1-results.md — TEST-VERIFY v0.31.23 (cpu-throughput-arc2 lane L1 cheap-overhead package)

Date: 2026-11-21 (UTC) · Tester: sonic · Tree: `/tmp/sonic-cpu-l1-tip` @ `b71742fe3` = tag `v0.31.23^{}`
Base: `0b5624dc9` (v0.31.22) at `/tmp/sonic-cpu-l1-base` (A/B harness, removed post-run)
Handoff: `.agents/shared/planning/sonic-team/cpu-l1/HANDOFF.md` (7 sections read)

## TEST: PASS

All 7 gate-trail items adjudicated GREEN; zero new reds inside the blast radius; live fence intact.
Registered pack: `cpu_l1_sonic_pins_gate_test` (one row appended to `.agents/tester/PACKS.md` IN THIS
HARNESS TREE — the pinned scripts-pin checkout was NOT dirtied; the row needs the leader's standard
merge sweep to enter the shared gate vocabulary, same as prior tester-evidence lanes).

## Per-item adjudication

1. **Pinned packs at the tip — GREEN.** 213 nodes: ram-guard config 54 + guard suite 48 (102P/4.1s);
   log guards 8 (8P + grep count == 21 + independent AST walk: ZERO unguarded eager f-string debug
   sites); orjson parity 19 + instances bytes 2 (21P/6.5s); gzip 25 + auth-off exact-set 9 (34P/14.1s
   under the 7-var auth scrub); wire leg 1P. Structure greps: guards 21; poll-default 1000 at all 4
   sites (validator map / Field / OFF-path / ctor); `orjson>=3.11` in pyproject + uv.lock; gzip
   registered after CORS (stack order → auth outermost, gzip sees CORS-set headers).
2. **Mutation-meaningful — PROVEN (4 mutants, 5 pins RED each-side, sha-verified restore).**
   - #5: unwrap one `isEnabledFor` guard (:991 → `if True:`) → `test_zero_unguarded_fstring_debug_sites`
     + `test_guarded_family_matches_canonical_set` RED.
   - #2 (instances leg): `OPT_SORT_KEYS` added to `_WIRE_ORJSON_OPTIONS` → `test_production_scale_payload_is_byte_identical`
     + golden-bytes + no-revalidation RED.
   - #2 (metrics door leg): `OPT_SORT_KEYS` added to `_DOOR_ORJSON_OPTIONS` → `test_100kb_plus_envelope_is_byte_identical_to_the_oracle`
     + `test_aware_utc_datetimes_render_as_z` RED.
   - #1 (bonus, kill-decision surface): poll default 1000→200 at the validator map → empty-string-env
     + `TestM1PollDefaultPins::test_empty_string_env_is_default_1000` RED.
3. **Pre-existing reds — CONFIRMED base-identical by direct A/B at `0b5624dc9`** (not merely
   accepted-from-documentation):
   - `tests/unit/metrics/`: **6F+1E, node-for-node IDENTICAL base≡tip** (family_adjudication
     diff-confinement, family_w3 journal, auth_matrix ×3, scope_cage ×2). NOTE: the handoff's
     "2 in tests/unit/metrics/" UNDERCOUNTS the directory population — the two named reds are real
     and pre-existing, but 4 auth-matrix + 1 scope_cage nodes also redden (all base-identical ⇒
     lane-innocent; census-accuracy nit for the handoff record, not a verdict defect).
   - `test_api_router_extraction`: same 2F at both (api_size QUARANTINE row :101 standing; api.py
     6561 → 6832 = +271, exactly the disclosed lane growth).
   - ui_prefs + agents_api family: 27F, **IDENTICAL node sets base≡tip** (QUARANTINE rows :17/:80/:98/:111).
4. **Quick-check 117/0 — REPRODUCED exactly**: six-file combined run = `117 passed, 0 failed`
   (54+8+19+2+25+9 = the four module surfaces + the closed seam node, matching the handoff's
   decomposition; registered pack adds the 48-node guard suite + wire leg = 166).
5. **Stage trail — CONFIRMED.** Zero `daemon/migrations/versions/` files in `0b5624dc9..b71742fe3`
   (empty diff; manifest `known_schema_gen=20261119_000001` = pre-existing head). `rollback_safe=true
   source=explicit=true` journaled in `/tmp/cpu-l1-stage2-log.txt` (STAGE_EXIT=0, "NO flip performed).
   4-way sha: release-dir binary `3b8d47e3…7194` == manifest (re-hashed by me, matching leader) ==
   dist-at-stage-time (manifest computed from dist by stage gates at 09:47:07Z; floor `e8695f17`
   validated ancestor + scripts-pin rev == floor) == provenance sidecar (log1: "wrote provenance
   head=b71742fe33e2 dirty=false"; L2 stamp validated). DISCLOSED LIMIT: the physical dist binary +
   sidecar are unrecoverable for byte re-hash — the stage worktree `wt-cpu-l1-stage` was reclaimed
   post-lane; the dist/sidecar legs rest on the stage-gate chain (STAGE_EXIT=0) + log lines, not my
   own byte comparison.
6. **Live fence — INTACT.** Live PID 1208450 `/proc/exe` = `releases/v0.31.22/ensemble-prod`;
   livez reports `"version":"0.31.22"`; no promote/flip/commit event for v0.31.23 anywhere in
   state.json history (only `stage-scripts-advance` 0b5624dc9→b71742fe3 + mcp quarevents).
   CAVEAT: `releases/current` symlink reads `v0.31.10` (mtime Nov 19 17:10, PRE-lane) while
   state.json `current` = v0.31.22 and the running exe = v0.31.22 — a pre-existing install-state
   divergence, filed as nest anomaly; it did NOT move during this lane and does not affect the
   v0.31.23 fence, but it should be reconciled BEFORE the next promote ceremony (flip-preflight
   drift-check surface).
7. **Measurement evidence — honestly labeled, corroborated.** #1 steady-CPU figure is labeled
   "BY FREQUENCY ARITHMETIC" with live re-measure owed post-promote in HANDOFF + DECISIONS-l1.md +
   CHANGELOG (NOT laundered); baseline 17.9% independently re-derived live at 17.87% in the
   preserved analysis-phase RESULTS (sonic-cpu-throughput-arc2-results.md). #2: byte-identity proven
   by pins + my re-derivation on merged code (bytes identical; 3.61x on a synthetic 82 KB/250-row
   payload vs handoff's 4.7x on the real 157 KB body — same class). #2b: gzip L1 ratio 20.5x on a
   synthetic 87 KB payload (handoff 30.0x on 1.8 MB — size-dependent, same class). #5: guard
   semantics proven; ns-scale numbers accepted as disclosed micro-benchmarks. M2's `metrics_read.py`
   stdlib-keep is pinned (`test_the_metrics_read_tool_was_left_on_stdlib`).

## Verdict basis

`TEST: PASS` — the merged change does what the handoff says at the seams I could break; every
mutation thrown at the new pins tripped loudly; every red in the touched scope reproduces
node-identically at base; zero migrations; live untouched at v0.31.22.

## Artifacts

- Pack: `/tmp/sonic-cpu-l1-tip/test/packs/cpu_l1_sonic_pins_gate_test.sh` (166P, rc=0)
- Registry row: `/tmp/sonic-cpu-l1-tip/.agents/tester/PACKS.md` (last row, tagged sonic)
- Logs: `/tmp/sonic-l1-{pack1,pack2,pack3,pack4,quickcheck117,metrics-tip,metrics-base,uiprefs-tip,uiprefs-base,pack-run,corroboration}.*`
- Stage logs: `/tmp/cpu-l1-stage-log.txt`, `/tmp/cpu-l1-stage2-log.txt`
