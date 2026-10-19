# Approver Tracking: resource-cost-control

Plan set: /home/nea/ensemble-src-wt-resource-cost-control/.agents/shared/planning/resource-cost-control/
(plan-overview.md, decisions.md, phase1..8-plan.md, architecture-recommendation.md, approach-comparison.md)
Branch: feature/resource-cost-control | base latest @ 8b8b32dcc

## Iteration 001 — 2026-10-18T~11:4xZ — REJECTED

Dispatch: 2 section-parallel workers (large multi-phase plan, 12 files / ~1900 lines) + fan-in todo_graph.
- approve-worker-observability-core  6eafa07b-314d-4c0a-8491-cd2f0d7f20f6  (overview + decisions + phase1-4) → REJECTED
- approve-worker-optimize-frontend    336807b4-6b11-4d3f-bf99-fa3482de34f6  (phase5-8 + arch-rec + approach-comparison) → APPROVED

Verdict: REJECTED (any blocking from any worker ⇒ REJECTED). Skills used: plan-approval (both).

### Blocking issues (as raised by worker 1; no upgrades, no new blocking introduced)
1. O4 mechanism / fix site inverted vs code at HEAD (8b8b32dcc). Cited docs: plan-overview.md:51,:111; decisions.md:44 (XC1),:98 (F.2); architecture-recommendation.md:224; approach-comparison.md:115.
   Expected: reset block ~registry.py:839-846 is the broken reset site, last_success_time bumped only in _handle_message (~:858).
   Found: last_success_time + backoff=2.0 set UNCONDITIONALLY at daemon/sources/registry.py:781-782 (start-coupled); :839-846 is the crash-arm conditional reset, already correct; _handle_message (def :858) has zero last_success_time refs. Implementing O4 as written modifies the wrong block and leaves the real lever (:782) untouched. Also "registry.py" cited bare w/o directory (4 candidates in-tree).
   Evidence note: worker 2 independently verified the same code facts (:781-782 unconditional bump; :839-846 conditional crash guard) — findings converge on facts; worker 2's scope read of P7 D5 task text judged the fix SHAPE correct ("gate reset on successful start AND sufficient runtime"). Reconciliation: correct/align the overview/decisions/arch mechanism description and the cited fix site; confirm D5 text targets :782.
2. Memory-cgroup operator guidance contradicts the plan's own measurements. plan-overview.md:106 (§6) prescribes MemoryHigh=6G / MemoryMax=8G; plan-overview.md:32 (§1) states cgroup memory.current oscillates 5.3-6.0 GB steady (incl. reclaimable cache). 6G MemoryHigh sits inside the documented normal band → recurring reclaim/throttle on a healthy unit at band peaks. Fix: raise MemoryHigh above the band (e.g. 7G) or document the reclaim tradeoff. MemoryMax=8G consistent.

### Non-blocking notes carried forward (union of both workers; deduped)
- POST /api/resources/profile (§2.8) not explicitly owned by any phase; P6 D2 lists only GET /control-capabilities. Confirm v1 stub reading; add one line to P6 D2/T2.
- plan-overview.md:134/summary typo "14 h" should be "14 d" (LR1 binding decision is correct).
- Anchor drift (re-pin mandate covers): manager eviction sites (claimed :5707/:6003/:6025/:6065 vs HEAD :5723/:6043/:6065/:6105); manager.instances/_graph_tasks :542/:546 (cited :541/:545); INSTANCE_CACHE_TTL_HOURS decl :191; launcher.sh MALLOC export :1658; phase3-plan.md:142 stale mount anchor :3894 → :4030; phase3-plan.md:20,:139 message_metadata PK :75-76; phase1-plan.md:57 PG NO-OP comment :188; phase1-plan.md:45,:83 snapshots.py detail shape (:105-107/:322-337 correct precedents); phase2-plan.md:99 R6 pre-7c7280926 refs (:422/:474).
- Unresolvable citation daemon/.../aio.py:181-230 (no aio.py under daemon/) — intended target is langgraph checkpoint postgres saver in site-packages; re-point.
- Acceptance gates hardcode /home/nea/ensemble-src/.venv/bin/python (main checkout, editable .pth → wrong branch) in phase3-plan.md:86-93 / phase4-plan.md:101-106, contradicting "never reuse main .venv"; worktree already has its own .venv. Also phase1/2 gates say bare `python -m pytest` — unify.
- Env-name ambiguity RESOURCE_MONITOR_INSTANCE_CAP (arch :108/:203) vs RESOURCE_MONITOR_INSTANCE_TOP_N (AD9, phase2 D12).
- approach-comparison §8 matrix minor score drift (O3 4.35 vs 4.40 etc.); ordering unchanged.
- FE gate port RESOURCES_GATE_PORT=14202 vs snapshots 14201 — correct, no collision.
- phase8 docs count "11 runbooks" vs 12 on disk.
- decisions.md XC5 wording "MAY be superseded" is imprecise: cancellation arm now unregisters (:483) but success path still does not; downstream ownership (P2) correct.
- Cardinality / misc cosmetic: bash fallthrough ":429-462" vs ":429-458"; decisions.md header "plan-overview TBD by planner"; §0 says scope lock "§6" vs decisions §E.
- In-flight, not a plan defect: O1 partially landed in worktree (systemd unit Environment=MALLOC_ARENA_MAX=2, launcher.sh:1658-1659, __main__.py:48 setdefault, new daemon/services/malloc_tuner.py + test); scripts/upgrade/adopt-unit.sh not yet modified.

Next: iteration 002 — re-verify the amended plan set (blocking #1 + #2 must be resolved). Standing mandate: every phase plan carries re-pin-at-implementation.

## Iteration 002 — 2026-10-18T~15:xxZ — REJECTED (2nd rejection; iteration 003 remains before escalation)

Dispatch: same 2-worker partition, fresh cold prompts (no rejection history, no leader rulings leaked; prompts required plan-vs-landed-code alignment checks). Worktree HEAD moved during review b3cd5e5c1 → a94000ae5 → c86462dc3 (O8a/O8b landed mid-review).
- approve-worker-obs-core-it2   013997e3-ce0b-440c-b8e8-6f40c44b8d46  (overview + decisions + phase1-4) → APPROVED, 0 blocking
- approve-worker-opt-fe-it2     69618940-6733-49c3-baec-2f39c59504d3  (phase5-8 + arch + approach-comparison) → REJECTED, 3 blocking

### Iteration-001 blocker resolution check
- Blocker #1 (O4 mechanism inverted) → RESOLVED. Both workers independently verified the corrected narrative (start-coupled unconditional reset :781-782 = the bug; :839-846 = verified-correct crash guard) AND the landed fix (778a49cb2 + 8fb77a7b5, registry.py:826-831 gate `prior_run_elapsed >= success_threshold`, 4 mutation-verified tests incl. growing-backoff storm reproducer). Residual non-blocking deltas: storm test landed as tests/test_sources_registry_backoff.py (plan named test_opt_o4_backoff_reset.py); post-fix line cites drifted (~:806-815 → :826-832; ~:871-874 → :889-902).
- Blocker #2 (MemoryHigh=6G inside 5.3-6.0G band) → RESOLVED. MemoryHigh=7G derivation verified sound (1G headroom over band top); drop-in commands syntactically valid, operator-gated. Non-blocking arithmetic-label nit: P8 D9 "OS ≈ 1.09G" vs decisions §G(b) 0.94G — same conclusion.
- Folded notes → resolved (profile-stub ownership P6 D2b/T2b/AC7/R5; aio.py re-pointed — no contrary finding; venv hardcodes removed; env name unified RESOURCE_MONITOR_INSTANCE_CAP; XC5 hardened REQUIRED; O5 bash cites re-pinned :413/:427/:483/:429-458 — all verified exact).

### NEW blocking issues (worker 2, iteration 002; no upgrades, no approver-introduced blocking)
1. phase7-plan.md O1/O3 forward-work directives contradict the code that ALREADY LANDED on this branch. Expected: phase7 either carries forward work matching the landed mechanism or reconciles it the way O4 was handled (currency note + re-pinned sites). Found: O1 landed at a64e82c2c with different semantics (launcher default-only `: "${MALLOC_ARENA_MAX:=2}"` — ".env wins", vs plan's export-precedence; adopt-unit.sh env-splice site D1-site-2 NOT implemented — loop ~:305-312 stages only PORT/POSTGRES_*; D2 runtime knobs gc.freeze/mallopt/60-min-trim-thread NOT landed — only malloc_tuner.trim_malloc_heap hourly idle-gated). O3 landed at 60f382bd1 on LimitsConfig (config.py:616/:625, env LIMITS_INSTANCE_CACHE_TTL_HOURS / LIMITS_INSTANCE_CACHE_MAX_ENTRIES, ge=0, no le=72) vs plan's DAEMON_-prefixed section precedent + Field(6, ge=1, le=72). phase7 CURRENCY note (line 22) records O4 landing + O3 line drift but is SILENT on O1's landing (plan-overview §7 records it — internal inconsistency). P8 D1(c) names DAEMON_INSTANCE_CACHE_TTL_HOURS — the un-landed spelling. Executing T1/T2/AC1-AC2 as written → second conflicting config path (vs AD14 single-ladder) + tests failing against landed code. SCOPE NOTE: this is NOT the leader-adjudicated O3 6h-default question (that is resolved; plan reads 6h + LRU 64 everywhere — worker 1 verified) — the defect is mechanism-home/env-spelling/bounds/precedence/currency-note/knob-name.
2. GET /api/resources/self-cost (arch §2.8 line 185, §4 line 257; consumed by P5 §2 getSelfCost(), T3, T7, AC6) is owned by NO phase. P1 delivers /live + /history only (phase1 title/§1/T8/D7/AC11; plan-overview phase map "first 2 endpoints"); P3 /traceback; P4 /anomalies×2; P6 /control-capabilities + POST /profile. Grep across all phases finds only the self-cost budget/regression test (P1 T9/AC7), never the endpoint deliverable → 404 at P5 integration. WORKER CONFLICT (surfaced, not silently dropped): worker 1's endpoint table claims "/self-cost → P1" without citation; worker 2's grep sweep found no owner. Kept worker 2's variant (most specific: grep evidence + FE consumer consequence). Remedy: assign to P1 (sampler already computes the fields), align P1 endpoint count + P8 runbook list. PATTERN NOTE: same defect class as iter-001 N1 (POST /profile unowned, now fixed for profile) — ownership sweep must cover ALL §2.8 rows, not one member at a time.
3. phase8-plan.md D3/D5/T3/AC3 CHANGELOG premise is FALSE: D3 "appended under the EXISTING ## [Unreleased] heading"; D5 claims "[Unreleased] section exists (verified)"; AC3 fence asserts [Unreleased] names resource-cost-control. At HEAD, CHANGELOG.md:8 top section = ## [0.23.0] — 2026-10-18; no active [Unreleased] (only historical :157/:205/:214). Plan's last edit (14:31Z) postdates v0.23.0 staging. One-line remedy: "create [Unreleased] if absent, else append" + fence wording.

### Non-blocking notes (union, deduped)
- Stale-but-superseded anchors covered by re-pin mandate (F.2 baseline re-drifted at HEAD c86462dc3): api.py watchdog :1799-1819→:1912-1940; mount :3894→:4030; eviction :5650/:5945→:5808/:6216; config.py :4201→:4252, :5541→:5592; DAEMON_ section cite :546→:559; _handle_message :858→:914; warmup_pool :69→:82, :544/:552/:596→:566/:574; chart_tools :57-58→:56-57; manager.py:655→:738.
- O8 landed AFTER the plan set froze (a94000ae5 sweep + fenced test; c86462dc3 audit note) — plan-overview §7 "remaining forward work" should drop O8.
- §2.8 "<20 MB fixed" vs §2.9 self_rss_budget_mb default 50 — wording ambiguity (test floor 20 MB vs runtime budget knob 50 MB); gates themselves consistent.
- P5 §7 attributes control-UI FE to P6 but P6 has zero FE deliverables — say "future FE work, owner TBD".
- P5 D6 precedent cites ±1-3 lines + bare filename (job-queue-indicator.component.ts at frontend/src/app/components/job-queue-indicator/; substance verified).
- docs/runbooks count = 12 not 11; maintenance-console.md header precedent verified.
- FE gate precondition: frontend/node_modules absent in worktree — name npm-install precondition in P5 T8 / P8 T5.
- P7 D12 self-citation (content lives in D11); P2 R1 cites "P1 AC10" but P1 self-cost gate is AC7.
- O1 adopt-unit.sh inheritance verification + O8b audit verdict already dispatched in the code lane (leader context, not plan defects).
- Landed-state positives (both workers): O1 three landed sites verified; O3 6h+LRU64 verified at manager.py:199/:205 + config knobs; O4 faithful + mutation-proven; O8a PID-recycle-guarded + FAKE_HOME-fenced; AD15 no-raw-args honored; 12-knob surface reconciled (plan corrects the "11" miscount).

Next: iteration 003 — (a) fold landed O1/O3 state into phase7 exactly as O4 was folded (currency note + re-pin + remaining-forward-work list), fix P8 D1(c) knob names; (b) assign GET /self-cost to P1 + align endpoint counts and P8 runbook list; (c) [Unreleased] one-line wording fix. All three are targeted plan edits, not redesigns. Max-3 budget: 2 used.

## Iteration 003 — 2026-10-18T~16:4xZ — REJECTED (3rd rejection → ESCALATED; max iterations reached)

Frozen HEAD 4d572f561 (code frozen; plan files last edited 15:41-16:05Z).
- approve-worker-obs-core-it3  24b61ec7-ce92-46bb-8eb4-5cdbb93a7861  (overview + decisions + phase1-4) → REJECTED, 2 blocking
  (NOTE: first attempt hit openai rate-limit execution_error; revived ONCE per escape-valve guidance; resumed from checkpoint and completed.)
- approve-worker-opt-fe-it3    57634b82-a88a-4f90-a06b-8d7daa029112  (phase5-8 + arch + approach-comparison) → APPROVED, 0 blocking

### Iteration-002 blocker resolution check (all three RESOLVED, both workers concur)
1. O1/O3 (and O4/O8) landed state folded into phase7:22 CURRENCY + NEW :31 REMAINING-FORWARD (O1✅/O3✅/O4✅/O8✅/O7⏳/O2⏳/lock-reap⏳); D1 rows corrected to landed semantics (systemd static :106; launcher default-only :1658 staged-value-wins; __main__:48 setdefault; malloc_tuner on existing MaintenanceService loop; gc.freeze/mallopt/thread DROPPED); D3/D4/T2 → LimitsConfig Field(6, ge=0)/Field(64, ge=1) + LIMITS_INSTANCE_CACHE_*; DAEMON_ spelling + ge=1..le=72 GONE from live sites; P8 D1(c) knob names fixed. VERIFIED in source by both workers (config.py:585/:615/:624 — note opt-fe cited :615/:624, obs-core cited :616/:625; 1-line discrepancy in the two workers' reads, immaterial).
2. GET /api/resources/self-cost → P1; §2.1 ownership table 8/8 single-owned (P1×3 / P3×1 / P4×2 / P6×2), zero double-ownership; P8 runbook list aligned. VERIFIED by both.
3. CHANGELOG [Unreleased] premise fixed at all 5 sites → "create if absent, else append" (create path asserted by AC3 fence). VERIFIED by both (head -25 CHANGELOG.md).

### NEW blocking issues (iteration 003; worker 1; no upgrades, no approver-introduced blocking)
1. F.2 false freeze-re-pin claim (doc-only). decisions.md §F.2 lines 94/103 claim manager.py :541 (self.instances) / :545 (_graph_tasks) / :655 (app_settings late-import) re-pinned "at HEAD 4d572f561 (freeze)"; actual :592 / :628 / :738. Propagated to phase1 D2 (:20), phase2 D3 (:23) + D5 (:40), plan-overview (:134). Dict-type signatures correct; lines wrong. CHAR: doc-only, does NOT block code start (~15 min fix, frozen tree). CONFLICTING SEVERITY: opt-fe worker found the SAME lines (:592/:628) and judged it re-pin-at-implementation non-blocking; kept as blocking because the authoritative F.2 table asserts freeze-level verification it does not have (same class as iteration-002 #1).
2. mcp_child stamp seam names a mechanism that does not exist (feasibility). phase2 D5 (:25) + T9 (:65); architecture-recommendation §2.3 stamp-seam table (:90): mcp_child stamps at "daemon/mcp/warmup_pool.py Popen" (+ connection_manager.py:56). At HEAD: ZERO Popen/create_subprocess* in daemon/mcp/*.py — MCP children are spawned inside the mcp library via mcp.stdio_client, wrapped at daemon/mcp/stdio_wrapper.py:258 (factory=lambda: mcp.stdio_client(server_params)). T9's testable outcome ("stamped child in RAM dict + T3 history") is unimplementable as specified. CHAR: absorbable DURING implementation (T9 = 1/12 tasks, best-effort/server-scoped; escape paths exist: R5 pgid fallback, D11 nullable instance_id) — but phase2 D5/T9 + arch §2.3 rows must be corrected first so the implementer does not grep for a nonexistent Popen.

### Non-blocking notes (union, deduped; from both workers)
- Anchor drift under the re-pin mandate: manager.py:5935→:5933 (_run_malloc_trim); chart_tools :56-57→:57-58; job-queue-indicator setInterval :574→:577 / clearInterval :582; db_path... ; config.py :4201→:4252 / :5541→:5592 (phase1 D1 contradicts F.2); config.yaml :187→:189; bash R6 pgid ":321"→:409-412, M3 comment :327-331→:424-426; phase3 mount :3894→:4030; message_metadata PK :69-70→:75-76; boot-wipe manager ~:860→~:962; task/models PG-no-op comment ~:192-194→:188; notifications.py :41-43→:43-45; snapshots.py :93-95 is a string-detail raise (dict shape at :105-107/:197/:227/:324/:337).
- phase2 D2/D3 venv path says python3.13; worktree venv is python3.14 (3.13 = main checkout's, which phase3/4 forbid).
- approach-comparison §9 line 119 + arch §2.3 line 88 carry the stale bash row (register :321 / unregister only :327-333); arch §3 O5 :237 + XC5 carry the corrected one.
- 20MB test-floor vs SELF_RSS_BUDGET_MB=50 distinction lives in phase1 AC7 but not propagated to plan-overview §5 invariant #1.
- P8 D9/§8.1 arithmetic "≈9.94G" vs decisions G(b) 9.79G (OS 0.94 vs ~1.09) — labeled approx; same conclusion.
- phase2 §7 (:110) still calls O4 a "separate commission item" (stale framing).
- decisions.md line 3 header "plan-overview TBD by planner" (pre-planner artifact).
- opt-fe notes: chart_tools guard cite :23-28 = comment block (runtime sites :35/:247); runbooks=12 verified; design-tokens absent verified; no asyncio.Semaphore in chart_tools + no lock reap in dependency_bus verified at HEAD (remaining-forward = exactly {O2, DependencyBus reap} confirmed).

### Final state
ESCALATED — max 3 iterations reached (3 rejections). Iteration-002's three blockers were all resolved and verified; iteration-003's two new blockers are documentation-level (one doc-only, one spec-row feasibility with a designed escape path). Neither blocks implementation START on the evidence; both are one-pass fixes on the frozen tree. Leader proceeds with implementation per TrueAuto and carries the two corrections.
