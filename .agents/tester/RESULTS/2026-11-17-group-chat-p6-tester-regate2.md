# Group-Chat P6 — Independent Tester Gate, FOCUSED RE-GATE #2 @ `7e0cd2db0`

- **Lane**: `feature/group-chat-p6` @ **`7e0cd2db0`** (approver-blocker remediation commits over the `5d13a1c6c` lineage). Worktree `/home/nea/ensemble-src-wt-group-chat-p6`; tree clean before/after every leg; **validate-only — no fixes, no code edits, no commits by the gate** (tester docs committed separately, `--only` idiom).
- **Lineage**: gate FAIL net-new 6 @ `e6d984272` → re-gate #1 PASS-WITH-NOTES @ `5d13a1c6c` (9 notes incl. N-1 doc drift) → **this re-gate #2 @ `7e0cd2db0`** after the approver's gate (1 code blocker + 4 ledger-text blockers, all remediated by the lane).
- **VERDICT: ✅ PASS — P6 TESTING AXIS CLOSED.** All 13 legs green, **0 findings** (record notes only, none blocking). The approver's GC fail-safe blocker — which was my N-1 doc-drift finding promoted to code — is fixed in code, measured end-to-end, and mutation-proven.
- **Workers**: 13 dispatches, **0 re-dispatches, 0 failures, 0 terminations**. Evidence: `/tmp/gc_p6_gate/rg2_*`, `/tmp/scratchpad/`, `/tmp/probe/`, `/tmp/gc_p6_reagate_check3/`, `/tmp/scratch-ledger-gate/`, `/tmp/gc_p6_room_mutation*`.

## §1 GC FAIL-SAFE (the approver's code blocker) — ✅ PASS (a–d)

The production sweep cycle now RETRIES the durable-flag read and converges (measured, scratch SQLite, production path):

| Tick | Fault | Read attempts | Latch `_loaded/_done/_read_authoritative` | Rooms | `room_purged` |
|---|---|---|---|---|---|
| 1 | read faulted | 1 | F/F/F | intact | 0 |
| 2 | read faulted | 2 (**re-read**) | F/F/F | intact | 0 |
| 3 | cleared | 3 (**re-read**) | T/T/T + durable `{dry_run_completed: true}` | intact (dry completes) | 0 |
| 4 | — | 3 (short-circuit) | pinned | **purged** (3 tables → 0) | **exactly 1** |

(a) no delete on the faulted tick ✅ (b) latch NOT pinned ✅ (c) convergence: next sweep re-reads → dry run completes with the durable record present → next tick REAL purge ✅ (d) the M1 pin `test_a_faulted_flag_read_never_latches_the_dry_run_on_the_production_path` covers execute(); **mutation-proven** — re-enabling the unconditional latch (removing the `_dry_run_read_authoritative` guard) in a scratch `git archive` copy (with `daemon.__file__` asserted) REDs the pin (`a faulted flag read must NOT latch the dry-run mirror`); restored → green. The N-1 docstring drift is resolved: the docstring sequence now matches the measured cycle.

## §2 `room_close` (NEW verb) — ✅ PASS (5/5)

Non-owner → typed `reason=not-the-owner` refusal, state unchanged (`active`, `closed_at=None`) ✅ · owner close → `state="closed"` + ISO-8601 `closed_at` ✅ · closed = READ-ONLY (post → typed `reason=room-closed`, no row) ✅ · **P-close 14d arm E2E**: close → age 15d → dry run → real purge of all 3 tables + exactly **1** content-free `room_purged` + `get_room → None` / typed not-found ✅ · `room_closed` SSE frame delivered content-free on the room channel; guard drops non-`room_`-prefix and content keys ✅. Pins: `test_room_close.py` (9), `test_room_close_producer.py` (9) — all green.
**Record note N-R1:** the room channel's reachable producer set is **5 kinds** — `{room_created, room_joined, room_left, room_closed, room_purged}` (a full lifecycle channel; the hub guard enforces prefix + content-free, not a fixed allowlist). The commission brief's `{created, closed, purged}` was under-inclusive wording, not a defect.

## §3 SPAWN-PATH JOIN BROADCAST — ✅ PASS (the carried gap is CLOSED)

Broadcast fires AFTER the spawn commit (`instance_lifecycle.py:2511` → hook `:2571` → `RoomChatService.notify_spawn_join` `service.py:359` → manager → hub); frame `{'event_type':'room_joined','data':{'room_id','instance_id','role'}}` — content-free (guard intersection `[]`); a RAISING notifier is swallowed + logged (`[DEBUG] room lifecycle notifier failed …`) while the spawn commits fully (instance row + both JSONB flags + membership row + member_name). Pins 33/33.

## §4 CAP SINGLE IMPLEMENTATION — ✅ PASS

Exactly ONE consume site (`daemon/tools/room.py:755` `post_cap.charge`); the duplicate inline arithmetic is gone (0 matches); the old consuming `check` replaced by `peek`+`charge`; 5th post → typed `post-cap` (no row), turn reset works, failed (closed-room) AND faulting (RuntimeError) appends do NOT consume; M2 pins 6/6.

## §5 MUTATION PACK — ✅ PASS 9/9 + SELFTEST-ON-COPY PROOF

9/9 legs (GC-M01..M09, incl. the 3 new: size-ceiling, joined-room-name module, shared-primitive module), each RED via its named marker and restored byte-identically; manifest over 5 files attests byte-identity; independent `--verify` CLEAN; `--selftest` non-vacuous (detects a planted byte in the COPY; PRE-PLANT sha recorded mid-run proves the manifest precedes the plant). **Corruption-risk proof:** SIGTERM landed inside the planted window → `daemon/tools/room.py` sha `f502308a…87b07b` **UNCHANGED**; the planted byte visibly resides in the `/tmp` COPY (`room.py.probe`); copy mechanism at `group_chat_room_mutation_matrix_test.py:526-530` (production only READ); tree porcelain empty, import OK.

## §6 LEDGER GATE — ✅ PASS 5/5 (mutation-proven)

`tests/unit/services/test_group_chat_ledger_gate.py` — 5 nodes pin `decisions.md` (A-P6-11/12/22/2) + `plan-overview.md` (the §9 PRE-APPEND plan row) + A-P6 numbering integrity. Mutation proof: flipping `SUPERSEDED BY A-P6-26` → `A-P6-99` in a scratch copy REDs exactly the A-P6-11 node; byte-identical restore → 5 passed.

## §7 FE — ✅ PASS

**140/140** (chat.component 119 + room.service 17 + chat.chatrooms 4) + modal `room-dialog` 8/8 · **icon visibility pinned**: `chat.html:89` wraps the button in `@if (renderedInstance())` — the SAME signal the click handler guards — so the button is ABSENT with no selection (visible ≡ actionable); the spec asserts hide → show → disappear, WATCHOVER unaffected · tsc CLEAN (exit 0) · **AOT/prod build PASS** (33.1 s; only pre-existing SCSS budget warnings).

## §8 Re-run legs + net-new — ✅ ALL PASS

| Leg | Result |
|---|---|
| room family (CORRECTED composition) | ✅ **24 files / 420 nodes / 0 red** (6:09; prior 385@21-file tips — growth is new coverage; the PACKS "21 files" label was stale and is corrected in this gate's PACKS block) |
| TestP11 | ✅ 39/39; marker node green; every `manager.py` hunk over `858a85e31..HEAD` carries a sanctioned-lane marker |
| fault-path cages | ✅ **15/15** — M1 grew 3 → 6 (production-path fail-safe pin + write-side pin + purge-failure isolation); no missing nodes |
| SSE-adjacent | ✅ **111/111** (86 baseline + 25 new: spawn-join 7, close-producer 9, room_close 9) |
| pilot scenario | ✅ 9/9 (C9: 12 conversation rows, exactly 1 purge event, purged read + SSE 404) |
| language_check standalone | ✅ 124/124 (2.5 s) |
| tool-sweep net-new vs base `858a85e31` | ✅ **0** — `tests/unit/tools/` corpus (5,213 nodes at HEAD, +25 vs base): failed sets **byte-identical** both sides (24 = 24, intersection 24, head_only **0**) |

## Notes (record-only, 0 blocking)

- **N-R1** room channel reachable set = 5 lifecycle kinds (see §2) — brief-wording correction.
- **N-R2** PACKS.md **main-checkout copy drift** (known class): the worktree copy is authoritative for the extended 9-leg mutation row; cross-check before citing from main.
- **N-R3** the `tests/unit/tools/` corpus needs ≥1500 s (the 600 s guard was insufficient — house shard-sizing precedent applies); junitxml only flushes on clean exit, so a timeout-killed run yields no digest.
- **N-R4** POK 256-cap still open for curator action (anomaly `868e088d`); facts routed to RAG.
- **N-R5** house-trap recurrence (no impact): `/bin/sh` dash rejects `PIPESTATUS` — wrap rc-capture in bash scripts.

## Overall Status

- 13/13 legs green · 0 findings · 0 net-new · the approver's 1 code + 4 ledger blockers all verified remediated (ledger via the mutation-proven gate node).
- **P6 testing axis: CLOSED — verdict ✅ PASS.** Release-gate lane may proceed on this evidence.

## Documentation Updated

- [x] RESULTS/2026-11-17-group-chat-p6-tester-regate2.md (this file)
- [x] PACKS.md — re-gate-#2 block + the stale 21-file room-family label CORRECTED (24 files @ `7e0cd2db0`)
- [ ] QUARANTINE.md — unchanged (no new quarantines)
- [ ] rules/ensure.md — user-maintained, untouched; no contradictions
