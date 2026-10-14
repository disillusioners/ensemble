# 2026-09-19 — mission-watch toolset reshape council review (commit 3cc635ea, base 307db932)

Independent gate (developer's internal code-review had PASSed — not taken on faith).
Mode: Deep-Review council, 2 models (agentic + coding), `code-review` skill. **VERDICT: APPROVE, 0 critical, 3 medium (non-blocking, pre-existing engine behaviors), 7 minor.**

## Durable engine facts surfaced (pre-existing — relevant to the future engine-phase, design §10)
- **HOLD firing set excludes dead_letter**: `daemon/services/work_notifier.py:364-366` fires on `{completed, failed, cancelled}` only. An already-terminal dead_letter watch registers rows that never fire (`notified=0`; strand until unwatch/boot reconcile). `watch_mission` surfaces the state in-band; legacy `watch_job` doesn't. Engine-phase candidate: give dead_letter an explicit disposition.
- **MissionResolver dead_letter stickiness**: a DEAD JobItem linked to a since-revived mission flips `terminal_reason="dead_letter"` regardless of live liveness (`daemon/services/mission_resolver.py:829-834`) — `watch_mission` on a revived-but-RUNNING mission replies "already terminal (dead_letter)". Candidate: liveness cross-check before the short-circuit.
- **Deny enforcement is exact-name** (`daemon/tools/instance.py:360-367`, enforcement `:4938-4944`): case variants match no deny entry AND no factory tool → fails closed today; only bites if a mixed-case tool name is ever registered.
- **skill.md parser-block pin mechanism** = verbatim 31-line substring fixture (`tests/unit/tools/test_mission_watch_prompts.py:49-80, :184-191`), NOT a sha256 literal — byte-equivalent (arguably stricter); "sha256 pin" wording in notes is inaccurate.

## Test substance (42/42 green, re-run by both councilors)
Real-substance harness: real MissionResolver + real watcher/task repos on file-backed SQLite, real CAS primitive (`claim_watchers_for_job_for_instances`); only `job_service` doubled. Unpinned in the new suite (rests on pre-existing engine tests): spec §9.2 fire-half (row fires at instance-terminal), §9.4 envelope byte-format/source strings, already-terminal dead_letter with terminal receipts. Adjacent green: 81 factory-index pins + 7 frozen-name tests.


## Delta closure (2026-09-19, amended commit 5f453b93)
Amendment (+73/−19, 4 files) applied the accepted findings — **APPROVE-DELTA** (single code-review worker, recovered from a wedged pytest bash via operator nudge + `timeout`-capped retry):
- M2 fixed at tool layer: `mission_actually_terminal = terminal_reason is not None AND liveness ∈ {completed, failed, cancelled}` (`daemon/tools/job_queue.py:2742-2748`) — dead_letter-since-revived now falls through to normal registration; the resolver-level sticky dead_letter flip (mission_resolver.py:829-834) remains as an engine-phase note.
- Both M2 directions pinned with real repo assertions; old W4 test renamed/rewritten to new semantics + one complementary test (29→30, count verified by --collect-only on both refs).
- Tests: 30+13=43 green (3.00s) + factory/frozen pins 88 green (1.98s). Residual minor: `watch_mission._full_doc_` at job_queue.py:2806-2807 still says "notifies immediately" without the genuinely-terminal-only nuance (pre-existing doc drift, 🟢).


## Final pass closure (2026-09-19, tidier round 0b504b5e on intact 5f453b93)
Bounded final pass — **PART 1 PASS / PART 2: ACCEPTABLE + ACCEPTABLE** (single code-review worker; 46/46 tests green, 2.81s):
- Tidier round semantically inert where it matters: `_resolve_mission_record()` helper (job_queue.py:609-649) equivalent at all 4 invocation sites; 2 new `logger.warning`s are log-and-continue (one newly surfaces a previously-silent swallow in list_watched_jobs — observability gain, no behavior change); `args_schema=WatchMissionInput` still the sole description source (:2681); M2 condition byte-identical (now :2817-2820); cap constant `MAX_WATCHES_PER_INSTANCE=50` (:68) + `_watch_cap_error` builder preserve arithmetic + message shapes at all 5 consumer sites.
- Test count lineage explained: 29→30 defs → **31 collected** (parametrized [ari]/[jober]); prompts 13→15 (2 negation/anti-pins added — survivor-pin pattern worth promoting). Removed meta tests migrated to `TestMetaPins` — no coverage loss.
- Advisory A (unwatch partial removal): pre-existing, retry-idempotent via handle re-resolve (remove_watch on removed row = False no-op); fix is engine-phase (batch delete), NOT a blocker.
- Advisory B (polymorphic mission_id): by design (A2), both branches pinned, no FE/tool coupling, absent-key is the honesty contract over null-default. Not a fix item.
- Earlier 🟢 doc-drift (watch_mission._full_doc_) CLOSED by this round at job_queue.py:2881.
- Cumulative gate: 3cc635ea APPROVE (council) → 5f453b93 APPROVE-DELTA → 0b504b5e PART 1 PASS — branch clear to merge.
