# Group-Chat P6 — Independent Tester Gate, FOCUSED RE-GATE @ `5d13a1c6c`

- **Lane**: `feature/group-chat-p6` @ **`5d13a1c6c`** = 4 fix commits over `e6d984272` (`9593a8d47` F-3 · `04f8e07c5` F-1/F-2/F-4 · `10edd36fc` A-P6-26 doc · `5d13a1c6c` F-3 manager.py sanctioned-lane markers) + `cd32c855c` (tester-docs commit — mine, `.agents/tester/` only). Worktree `/home/nea/ensemble-src-wt-group-chat-p6`; tree clean before/after every leg; **validate-only — no fixes, no code edits**.
- **Prior gate**: ❌ FAIL — net-new = 6 deterministic lane-attributable reds + finding F-3 (SSE room-resolution exposure) + advisory F-4. Full record: `RESULTS/2026-11-16-group-chat-p6-tester-gate.md`.
- **RE-GATE VERDICT: ✅ PASS-WITH-NOTES** — **the P6 testing axis is CLOSED**: the net-new is gone (6/6 green + sibling-miss sweep clean + needle census clean), F-3's content exposure is CLOSED (adversarially), F-4's WRITE side is exact. **0 net-new. 0 blocking findings.** Residual items are 9 carried notes (2 doc-only, 1 disclosed producer gap, 1 contract anchor, 5 hygiene/record items) — none behavior-risky.
- **Workers**: 12 dispatches (11 re-gate legs + 1 prior-gate docs commit), **0 re-dispatches, 0 failures, 0 terminations**. Evidence roots: `/tmp/gc_p6_gate/{regate_*,gc_p6_regate_leg1,gc_f4_regate}` + `/tmp/probes/` (F-3 probes) + `/tmp/gc_p6_base` (base extraction for byte-equality).

## §1 F-1 + F-2 — the 6 net-new reds → **CLOSED**

- **6/6 previously-net-new nodes PASS** (14.08 s). Node-id drift disclosed: F-1 renamed three of them (`…reads_117`→`…reads_119`, `…stays_39`→`…reads_40`, `…stays_35`→`…reads_36`); the semantic equivalents were run.
- **Sibling-miss sweep — COMPLETE, 0 stale copies.** Live census independently derived: declared routes **119**, mounts **40** (37 unconditional + 2 auth-ON-gated + 1 gateway-flag-gated WS), class-manifest families **36**. Executable pin inventory: `test_metrics_query_scope_cage.py:52-54` (3 constants) · `test_router_scope_manifest.py:339,466` · `test_router_class_manifest.py:411` · `test_post_restart_arm_notify_no_parallel.py:68-150` (**order-exact 40-entry list-form pin**) — **7/7 MATCH**. The census-pin sibling-miss class that caused the original FAIL is closed.
- **Retired-needle census — 0 live hits.** All 7 guardian needles (`COMPLETION_CHECK_NOTE_TEXT`, `_make_completion_check_note_message`, `_COMPLETION_CHECK_NOTE_TITLE`, `_fused_hint_citation`, `marker_hint_message`, `completion_check_note`, `Completion Check Note`) → **0 hits in `daemon/`**; the only whole-tree hits are test fixtures / historical docs / planning records (the classification the guardians allow). F-2 needed no witness carve-out.
- 9 files / **100 passed / 0 failed** (incl. `test_room_p6_wiring_pin.py` 5P, `test_room_sse_resolution.py` 14P, `test_source_gc_repository.py` 39P).

## §2 F-3 (my anomaly `de77fd54`) — SSE room-resolution exposure → **CONTENT EXPOSURE CLOSED**

Adversarial, in-process, no ports. Base tree extracted from `git archive e6d984272` for byte-comparison.

| Probe | Verdict | Evidence |
|---|---|---|
| P1 outsider on the room branch | ✅ CLOSED | exactly **1** event (`room_created`, content-free); leaked content events `[]` — no checkpoint / tool_result / message / question_pack / context_usage |
| P2 smuggling ×6 | ✅ 6/6 refused | `broadcast_room_event` requires a `room_` prefix **and** rejects content-bearing keys (`message, messages, content, text, text_body, body, tool_output, pack, checkpoint, html`) → WARNING + dropped; return `False`, 0 delivered per attempt |
| P3 identity | ✅ (documented anchor) | ack keys `['channel','instance_id','resolved_from','room_id']`; the owner's `instance_id` remains the single designated disclosure per the fix's contract — **no content, no further identity** |
| P4 owner instance stream | ✅ byte-for-byte identical | base `e6d984272` vs `5d13a1c6c` — sha256 `946584871f7d558b63123945c7d27151fbf4ef5e28d9d31f6302bc963a47d064`; 5 events (`connected, checkpoint, tool_result, message, context_usage`); owner ack still 3 keys |
| P5 room-branch replays | ✅ skipped | owner branch replays `context_usage` + `question_pack`; room branch emits **neither** |
| P6 pin non-vacuity | ✅ mutation-proven | counter-mutation (re-adding instance-key registration in a scratch copy) **reddened** `test_an_outsider_room_stream_carries_zero_content_bearing_events`; the second pin asserts the 3-key owner ack + content flow |
| P7 producer gap | ✅ confirmed (carried) | spawn-transaction join (`instance_lifecycle.py:~5667` `join_room_in_session`) never calls the notifying `RoomChatService.join_room` → **no `room_joined` broadcast**; follow-up hook = the post-spawn read-back point (A-P6-26) |

## §3 F-4 — `room_gc.py` docstring → **WRITE side EXACT; READ side residual over-claim (note N-1)**

Measured (scratch SQLite, dual-layer timeout):

| Scenario | Measured | Doc |
|---|---|---|
| (a) KV **read** fault — primitive | `read_attempts` 0→1→2→3 (retried), no latch, recovers to `True` | ✅ true |
| (a′) KV **read** fault — **sweep cycle** | `read_attempts` 1→1 (**no re-read**); sweep #1 dry (purged 0), sweep #2 purges | ❌ "RETRIED on the next sweep" is false in the cycle |
| (b) KV **write** fault | mirror pinned unconditionally; no re-read; room purged; **exactly 1** `room_purged` event (ids+counts only) | ✅ EXACT (`:197-206`) |
| (c) restart | fresh mirror re-reads KV (1→2), dry run **re-armed**, nothing purged | ✅ EXACT |

Root cause of the residual: `execute()` calls `_mark_dry_run_done()` at the end of the same dry-run sweep (`:236-239`), which pins the mirror unconditionally (`:208-209`) → `_load_dry_run_flag()` short-circuits (`:172-173`). The M1 cage pins exercise the **primitive** directly, hence their green. **Doc-only; fail-safe outcome correct** (faulted sweep purged nothing; restart re-arms). Text pre-dates F-4 (`git show 04f8e07c5`).

## §4 Re-run legs at the fix target — **all PASS**

| Leg | Result |
|---|---|
| `gc_p6_room_family` (21 files) | ✅ **385/385**, 0 red, 334.5 s (third confirming measurement: 377 @coder, 385 @prior gate, 385 @re-gate) |
| `gc_p6_pilot_scenario` | ✅ **9/9** (C9's room-channel path unaffected by F-3) |
| `gc_p6_room_mutation_matrix` (solo) | ✅ **6/6** legs, markers exact, byte-identity attested, tree clean before/after |
| SSE-adjacent corpus (3 files) | ✅ **86/86** (room_sse_resolution 14 + live_event_hub 57 + injection_sse 15) incl. both new F-3 pins |
| Fault-path cages | ✅ **12/12** (M1 ×3 · M2 ×6 · M3 ×3) |
| TestP11 file | ✅ **39/39**; manager.py-marker pin **non-vacuous** (5 hunks / 73 added lines, all 5 carry the marker; the 4th commit's prefix is what turns it green against the *committed* tree) |
| FE modal leg | ✅ **28/28** across the 3 room specs (room.service 17 · room-dialog 8 · chat.chatrooms 3) |
| FE whole-tree jest | ✅ **4876/4885** — the same **9** base reds, no new; attribution now proven against production sources too (empty diffs for the specs **and** `settings.component.ts` / `schedule-create-dialog.component.ts` / `jobs-grouping.model.ts` / `jobs-filter-state.model.ts` / `utils/timezone-format.ts`); cause = EST-vs-EDT + relative-"ago" |
| tsc | ✅ CLEAN (exit 0, 0 output) |
| `gc_p6_lane_scope` | ✅ **218P / 1S / 1F** — the ONLY red is EXACTLY `tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files`, confirmed present in BOTH prior gate legs' `failed_ids` (base-attributable per A-P6-18); its 96-item offender list is **byte-identical** to the `e6d984272` run (zero new offenders); +2 nodes = the 2 F-3 pins |

## §5 Carried notes (no behavior risk)

- **N-1 🟠 (doc-only, lane-owned)**: `room_gc.py:40-41,146-147,159-164,184-186` — "a FAILED flag READ is RETRIED on the next sweep" is true for the primitive, false in the sweep cycle (§3). One-line re-word; the design record already discloses it (`decisions.md:524`, A-P6-25).
- **N-2 🟢 (non-executable prose)**: 7 stale census prose mentions in test docstrings (`test_router_scope_manifest.py:20-21,294-295,327`; `test_post_restart_arm_notify_no_parallel.py:156,164-165,167,183-184`; `test_router_class_manifest.py:409-410`) — cannot redden any test; suggested re-word to `40 = 37 + 2 + 1` and append the `35 → 36` chain step.
- **N-3 🟠 (disclosed functional gap)**: F-3 producer gap — spawn-transaction joins never broadcast `room_joined` on the room channel (§2 P7; follow-up hook named, A-P6-26).
- **N-4 🟢 (contract anchor)**: the room-branch ack discloses the owner's `instance_id` — accepted by the fix's own contract ("the owner's identity surfaces only in the resolution ack"); **content exposure is closed**.
- **N-5 🟢 (hygiene)**: two `test_room_post_cap.py` files exist — `tests/unit/services/` (9 differently-named tests) vs `tests/unit/tools/` (the 6 M2 fault-injection nodes); disjoint sets; the authoritative list is the `tools/` path.
- **N-6 🟢 (record correction)**: the 3 FE room specs hold **28** it() blocks (17 = `room.service.spec.ts` **alone**); all three are **new P6 files** (absent at base `858a85e31`), so "17 at base AND HEAD" was never a coherent premise — 17 is stable `e6d984272 → HEAD`.
- **N-7 🟠 (ops)**: project POK store at its 256-entry terminal cap; archiving `sqlmodel-select-import-gotcha` needs curator authority (anomaly `868e088d`). Facts routed to RAG meanwhile.
- **N-8 ✅ (recorded fact)**: the self-inflicted `git checkout --` incident — **LOST-WORK ASSESSMENT = NO**. `checkout --` leaves no reflog trace; 3 reflog `reset`s are pre-series and content-preserving; no stash from the fix wave (newest 2026-11-03); `git fsck` shows zero unreachable commits in the window → recovery was by re-application, not stash-pop; all F-1..F-4 fixes verified present in the committed tree at `5d13a1c6c` (incl. all four "allegedly-lost" `messages.py` elements; file byte-identical `5d13a1c6c`↔HEAD). Cost = re-work time, not work loss.
- **N-9 🟢**: room-family leg = 5:34 (registered cap 1500 s; shard only if a future gate pushes it up).

## §6 Pre-existing-red ledger (unchanged by the fix series)

T1 QUARANTINE rows honored (AM1 route-set family ×3; skill-evolution A/B; launcher 8b-c2 documentary); A-P6-18 metrics-family offender base-proven (red at BOTH legs) — unchanged; A-P6-20 api-router census pair base-proven. **No new quarantines** — every red in this re-gate was deterministic and attributed.

## Overall Status

- Net-new: **0** (was 6) · F-1 ✅ · F-2 ✅ · F-3 ✅ (content) · F-4 ✅ (write side)
- Re-run legs: **all PASS** (room family 385 · scenario 9/9 · mutation 6/6 · SSE-adjacent 86 · cages 12 · TestP11 39 · FE 28/28 + 4876P · tsc clean · lane scope PASS)
- **P6 testing axis: CLOSED** — verdict ✅ PASS-WITH-NOTES; the 9 carried notes are non-blocking (N-1/N-2 doc-only, N-3 a disclosed + named-follow-up gap, N-4 an accepted contract anchor).
- Release-gate readiness: the P6 phase has no open tester-blocking findings; the staged-release lane may proceed on this evidence.
