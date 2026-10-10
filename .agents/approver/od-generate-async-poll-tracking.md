# Approver Tracking: od-generate-async-poll

Plan: fix the 120s proxy read-window ceiling that kills long OD generation calls (130-170s budget; Cloudflare 524 at 120s). Artifact: .agents/shared/planning/od-generate-async-poll/architecture-decision.md (252 lines, hybrid decision + implementation brief). Dual worktrees: ensemble /home/nea/ensemble-src-wt-od-generate-async-poll + proxy /home/nea/Code/opensource-projects/llm-supervisor-proxy-wt-od-generate-async-poll, branch feature/od-generate-async-poll.

## Iteration 001 — REJECTED (2026-10-10T~21:20Z)

Dispatch: 2 workers, partitioned by section, fresh context, read-only, both worktrees available for claim verification.
- Worker A approve-worker-decision (61b38619) / decision-approval / §0-5, §10-11, Appendix → APPROVED, 0 blocking, 5 notes.
- Worker B approve-worker-plan (3b2119e5) / plan-approval / §6-9 + constraint compliance → REJECTED, 1 blocking, 5 notes.

### Blocking (1, carried from Worker B — not downgraded)

1. **§6.2 atomic budget-chain touch-list incomplete for the max_tokens default** (§6.2 touch-list line 158; compounding §3 line 69).
   - Expected: per the plan's own atomicity contract (§6.2 "all links move in ONE changeset"; §8 risk-1 "any unreconciled link silently becomes the new killer"), the atomic changeset owns EVERY site carrying the max_tokens default.
   - Found: touch-list cites only `generate.py:101` (dataclass default). Worker B verified additional live 64000 sites: `generate.py:1093` (`raw.get("max_tokens", 64000)` in `execute_dict` — the OPERATIVE default for the production StructuredTool→execute_dict path per the plan's own §3 call-path description), `generate.py:1096-1099` (invalid-value fallback resets to literal 64000), `ports.py:92` (Port input schema `"default": 64000`). Consequence: bumping :101 alone leaves tool-path callers at 64000 → inner timeout stays ≈173s → 200k target silently unmet while ALL specified tests stay green (§7.6 chain-invariant test never asserts the max_tokens default). Exactly the Oct 9 failure family the plan exists to kill.
   - Required fix: (a) add generate.py:1093, :1096-1099, ports.py:92 to the §6.2 touch-list and state which site is operative for schema-default injection; (b) add a max_tokens-default assertion to §7.6.
   - Approver held at blocking (no downgrade): evidence-backed, strikes the core deliverable of Phase 2 against the plan's own atomicity contract; a silent no-op on the production path is a correctness/safety failure, not style.

### Notes (non-blocking, deduped across workers)

1. WriteTimeout arithmetic slip §6.2 line 153-154 (BOTH workers, deduped): "max(5min, 3×900s) = 2700s < 30min clamp ✓" — 2700s exceeds the 30min clamp (main.go:307-315) → actual 1800s. Safety conclusion survives (1800 ≥ 660 wait ≥ 600 wall); correct the parenthetical. Fork-(b) row at line 146 is correct.
2. §4 scoreboard totals don't reproduce (A): A coding prints 2.20, computes 2.40 from its own axis scores; B totals unreproducible (moot — refuted); C reproduces exactly. Ranking unchanged under any consistent recomputation (C leads ≥1.4 both scenarios). Correct printed totals.
3. §5.1 "inherits the 45s watchdog for free" overstated (A): watchdog wired only at the LangChain chokepoint (llm_stream_watchdog.py:68-70); od.generate raw client bypasses it. Reusable via optional §6.1(a) (get_or_build_watchdog_clients, graph.py:3978) — reword.
4. "Per-model STREAM_DEADLINE raise" unsupported (A; §5.4-2 line 108, §6.6 line 189, §8 line 218): proxy has a single GLOBAL StreamDeadline (config.go:71/:179/:410); config explicitly declines per-model deadline fields. Flip mitigation is global-raise-or-code-change — reword honestly (broader blast radius than stated).
5. §6.4 omits concrete mapping for live-mode in-band SSE error envelope (A; race_coordinator.go:~400-431): 0-byte guard emits well-formed terminal SSE error envelope → raw SDK sees choice-less terminal chunk → IndexError (HA-transient w/ backup — verified live) or empty content → Gate 2 truncation → Phase-3 retry. End state handled; pin the concrete mapping in the Phase-1 TTFB boundary-pair test.
6. §7.6 testability wrinkle (B): chain-invariant test asserts proxy-side values (StreamDeadline, MGT, IdleTerminationTimeout) not readable from ensemble tests at runtime — specify code-assertable vs documentation-pinned links or the assertion is vacuous.
7. Phase-3 test gate under-specified (B): §9 truncation-path test doesn't pin the exactly-ONE re-attempt bound from §6.3 — add retry-count assertion.
8. Citation drift, non-load-bearing (B): wall-clock constant at generate.py:630 (plan: :621); 420s test pin also at test_opendesign_b_element.py:1381 outside cited :418/:1236-1242; extraction region spans ~:1003-1063 (plan: :1035-1063).
9. §10-Q3 resolvable now (BOTH workers, deduped): IDLE_TERMINATION_ENABLED (config.go:481-483) and IDLE_TERMINATION_TIMEOUT (config.go:484-489) env overrides EXIST — close the open question.
10. Strengths on record: hard constraints verified honored (no live-proxy deploy/restart — user-gated instructions-only; 100% backward compat — no new endpoints, relaxation-only raises, only od.generate factory edited; no promote). Evidence density exceptional: ~40+ file:line claims verified by B, dozens by A (timer table, streaming machinery, call path, exactly-57 adapter tests, worktree SHAs 4f70415d2/b2660a6 match). Decision core (Option C, B refuted on verified grounds, A parked with concrete reopen triggers, flip assumptions with Phase-0 detection) held up under independent fresh-eyes verification with zero blocking findings.

### Verdict
REJECTED (1 blocking). Fix is small and mechanical: extend the §6.2 touch-list (4 sites total + operative-site statement) + add max_tokens assertion to §7.6. Notes 1-4 are one-line doc corrections worth folding into the same amendment.

Next: iteration 002 upon amended artifact.

## Iteration 002 — APPROVED (2026-10-10T21:14Z)

Dispatch: 2 workers, same partition as 001, cold context (no rejection history; acceptance requirements framed as caller-stated).
- Worker A approve-worker-decision (9392595f) / decision-approval / decision core → APPROVED, 0 blocking.
- Worker B approve-worker-plan (7a6db6fd) / plan-approval / implementation brief + closure requirements → APPROVED, 0 blocking.

### Iteration-001 blocking — CLOSED (independently confirmed by BOTH workers)
§6.2 now carries all four live 64000 sites with per-site operative-role analysis: ports.py:92 schema default (incl. maximum:200000 at :91; operative via args_schema passed verbatim at plugin_tool_factory.py:298-303 + None-drop in _tool_fn), generate.py:1095 raw-dict fallback, :101 dataclass, :1096-1101 invalid/clamp resets. §7 item 6 asserts the 200000 default at every injection site (omitted-arg StructuredTool, execute_dict({}), dataclass, max_tokens=0/"bogus" exercising both reset branches) PLUS the no-residual-64000-literal pin; code-assertable vs doc-pinned split present and honest. Iteration-001 notes also verified addressed in v1.2: WriteTimeout parenthetical now clamp-correct (2700→1800s); scoreboard totals reproduce; watchdog "not for free" + global-only STREAM_DEADLINE reworded; SSE error-envelope mapping pinned (§7 items 1/4/10); exactly-1 retry bound pinned; §10-Q3 env overrides closed (config.go:481-489).

### Notes (non-blocking, deduped across workers)
1. Citation drift (both workers; B most specific): §1 primary cite :965 stale (actual :934, doc's own parenthetical correct); execute_dict def :1071 (cited :1076); raw fallback :1095 (cited :1093); clamp span :1096-1101 (cited :1096-1099). All anchors resolve by content grep; re-pin line numbers at edit time.
2. No-residual pin vs comment literal (B): generate.py:927 comment contains literal "64000" (and :930-933 wall-clock comment goes stale at Phase 2) — a naive source-text-scan pin fails unless the Phase-2 changeset also updates those comments or the pin is AST-scoped. Recommend adding comment updates to the Phase-2 checklist.
3. Minor path/anchor imprecisions (B): plugin_tool_factory.py lives under daemon/plugin_subsystem/; test_llm_failover_v2.py at tests/unit/; _set_test_hooks :1161-1191 (cited :1176-1207); _OD_FAILOVER_INACTIVE_NOTE :635-639 (cited :634-639). All resolve by content; none affect instructions.
4. Unverified minors, declared non-load-bearing (A): semaphore cap value (=4) not traced to its constant; live-env OPENAI_BASE_URL_BACKUP presence not inspected (doc itself flags the stale failover note as fix-while-in-file).

### Verdict
APPROVED — 0 blocking from either worker; closure of the iteration-001 blocking verified on code-level evidence (four sites exact; §7.6 assertions present incl. no-residual pin). Constraint compliance re-verified: DESIGN ONLY header; proxy deploy/restart user-gated instructions-only (§6.7); backward compatibility structural (zero proxy code changes, only od.generate factory edited, relaxation-only env raises); no ensemble promote.
