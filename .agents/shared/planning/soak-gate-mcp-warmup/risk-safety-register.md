# Risk + Safety Register — soak-gate-mcp-warmup

**Commission:** soak-gate-mcp-warmup · **Branch:** `feature/soak-gate-mcp-warmup` · **Base:** `d5a4e4ad9`
**Worktree:** `/home/nea/ensemble-src-wt-soak-gate` · **Status:** PLANNING ONLY.

Every entry: **Risk** | **Mechanism** (how it could happen, code-cited) | **Mitigations** (design decisions that prevent it) | **Pin** (the test that proves the mitigation) | **Residual** (what remains even when the pin is green). **NORMATIVE PREDICATES ARE NOT RESTATED HERE:** the never-green trigger, the grace classes/aggregation/counter kinds, the grace ceiling, the leak signature, the DNS bound, the AC thresholds and the A3 release rule live in **`requirements.md` §4.1–§4.7** (the single normative home); entries below **POINT** to those anchors and keep only the execution-layer risk, mitigation and pin detail. A restated predicate here would be a drift risk and is not permitted. Anchors were verified in this worktree. The governing safety property is the **never-green rollback** (Category 5): anything that conflicts with it is narrowed, never weakened (decisions §5; requirements §6).

---

## Category 1 — What could MASK a real broken release

### R-MASK-1 — The grace engages on a hard red
- **Mechanism:** a grace window that holds *any* transient-shaped 503 would also hold a component that is genuinely red (an exception, not a timeout). Today `_probe_once_classified` folds all 503s into `transient` (`scripts/upgrade/lib.sh:2986-3001`), and the rc-3 suppression (`:3022-3028`) silences transients on age alone.
- **Mitigations:** the §4 class table is normative — `hard_component_red`, `boot_incomplete`, `identity_mismatch`, `not_serving`, `pool_leak`, `unknown` are **never** grace-eligible; the counter RESETS on them and fails fast; the daemon-side classifier keys off the `_guarded` timed-out flags vs exception paths (`readiness.py:477-521`), not off free-text reasons (premise 23: the legacy reason string is stale).
- **Pin:** class-table unit pin (`tests/unit/tools/test_readiness_failure_class.py` — every row, both arms); A5(iii) fail-fast pin (hard red ⇒ return 1 on the first observation, ≤ ~20s, even inside an active grace).
- **Residual:** an `unknown` classification (unreadable 503 body) fails **closed** (never held) — a rare false rollback of a busy daemon is accepted over any chance of masking.

### R-MASK-2 — The grace renders the daemon "healthy"
- **Mechanism:** implementing the grace as "ready = true but degraded" would make `/readyz` answer 200 while a component is down, lying to *every* consumer, not just the gate.
- **Mitigations:** A1.1 — the daemon's verdict is untouched: `status` stays `"degraded"`, `/readyz` stays **503** (`daemon/api.py:4809-4879`), `components` keeps the failing component `false`; the grace is additive `detail` keys consumed by gate-side policy only.
- **Pin:** A5(iv)(a) detail-payload pin — during an active grace: `status == "degraded"`; a dedicated assertion that the readyz HTTP code is 503 while `detail.grace.grace_active == true`.
- **Residual:** none — this is a hard invariant, not a tuned one.

### R-MASK-3 — A suppressed reason string
- **Mechanism:** a grace implementation that empties or filters `detail.reasons` while held would hide *why* the daemon is degraded, defeating both the operator and the gate's diagnosis path.
- **Mitigations:** A1.5 — `detail.reasons` and `components` keep their current semantics unchanged while a grace is held; "a suppressed reason string is a defect" is stated in decisions §4 invariant 2.
- **Pin:** `tests/unit/tools/test_readyz_grace_detail.py` — with the grace active and a component degraded, `detail.reasons` is non-empty **and contains the real component reason**; a suppressed string is a FAIL.
- **Residual:** none.

### R-MASK-4 — The leak class being grace-held
- **Mechanism:** the 22:10–22:50Z class is self-sustaining (probe leak → pool pressure → timeout → retry → more leaks); a time-only grace would engage on exactly this signature and mask it indefinitely (decisions §2 premise 9).
- **Mitigations:** the leak predicate is **normative in `requirements.md` §4.4 (P4)** — the ORACLE is the P2 psycopg saver; the P1 SQLAlchemy signal is **ADVISORY** and MUST NOT arm the leak class (`overflow() > 0` is non-zero on a busy-healthy pool by design); the streak arm fires ONLY when the pool objects are UNAVAILABLE (S9); measured from pool objects, NOT from `pg_stat_activity` (an extra query on the poisoned pool would amplify the disease). The class's grace-ineligibility and disengaging behaviour are the §4.0/§4.2 semantics.
- **Pin:** leak-predicate pin over a synthetic monotonic `checkedout()` sequence (`test_readiness_failure_class.py`); A5(iv)(b) leak-refusal pin — injected signature ⇒ grace does not engage (or disengages next tick) AND reasons still reported.
- **Residual:** thresholds (3 ticks) are a starting proposal, not measured (decisions §9 Q2) — the predicate is deliberately conservative (fail-closed to `pool_leak` ⇒ no grace), so an over-eager threshold costs grace availability, never safety.

### R-MASK-5 — The counter HOLD applied to a non-grace-eligible transient
- **Mechanism:** if HELD were granted on class-membership alone (without checking grace-active and leak-false), a stream of ineligible transients would freeze the counter above 0 and could eventually ride to cap-reach with greens < 3 but a "non-zero history".
- **Mitigations:** the observation-kind rule is normative — see the pointer below; the entry's own concern is that a HOLD granted outside those conditions would freeze the counter above 0 and let a stream of ineligible transients ride to cap-reach with a non-zero history. (The bounded-holds arithmetic that keeps a never-green daemon at 0 is part of the same normative rule.)
> Normative source: requirements.md §4.2 (P2 — grace eligibility classes, aggregation, counter observation kinds).
- **Pin:** counter-semantics pin stream (b): `green, held, held, held` never reaches 3 and returns 1 at cap-reach (`tests/packs/promote_soak_grace_counter_unit_test.sh`).
- **Residual:** none — the bound is arithmetic, not observational.

### R-MASK-6 — The `_PROBE_STATUS` aliasing letting a soft-green count

> Normative source: requirements.md §4.2 (P2 — counter observation kinds) — the aliasing is the DEFECT that breaks §4.2's per-probe evaluation; the entry below is the defect mechanism and its pin, not a restatement of the rule.
- **Mechanism:** verified today: the counter reads the global `_PROBE_STATUS` twice (`lib.sh:5216-5221`) and the second (readyz) probe overwrites it (`:5181`), so *livez busy-amnesty soft-green + readyz hard green INCREMENTS* the counter — contradicting the documented invariant and making the gate more permissive than documented (decisions §2 premise 4).
- **Mitigations:** B5 — each probe's status is captured into a distinct local immediately after its own call (`livez_status` at `:5162`, `readyz_status` at `:5182`); the counter evaluates the locals; the fix lands in the SAME change as the new grace semantics (an unreviewable counter would poison the new logic). **Normative source: requirements.md §4.2 (P2 — counter observation kinds)** — the per-probe locals are how this entry's fix realises §4.2's per-probe evaluation.
- **Pin:** counter pin (d) — both directions: soft-green livez + hard-green readyz does NOT increment; hard-green livez + soft-green readyz resets.
- **Residual:** none; the grep gate at phase-1 exit (`_PROBE_STATUS` no longer referenced at the counter site) is a second, mechanical fence.

### R-MASK-7 — An unbounded extension (grace as a soak-length exploit)
- **Mechanism:** a grace that re-arms on every transient could extend the soak indefinitely, converting "never-green ⇒ rollback" into "eventually the operator gives up".
- **Mitigations:** the bounds are **normative in `requirements.md` §4.3 (P3)** — one observation-relative clock (`first_grace_eligible_observation + GRACE_HORIZON_S = 120.0` s) never extended; `GRACE_EXTENSION_CAP_S = 180.0` s spent, not refunded on re-arm; the soak's absolute bound `soak_entry + 120 + 180 = soak_entry + 300s`; the outer `PROMOTE_TOTAL_DEADLINE_S = 900` (`lib.sh:109`) dominates everything.
- **Pin:** A5(iv)(c) bound pin — absolute-ceiling expiry ⇒ plain degraded, `grace_active == false`; extension-arithmetic pin asserting all four `min()` terms of `effective_cap` (including `ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S`).
- **Residual:** the constants are shell-side, not env-tunable, first cut (decisions §9 Q6) — tuning later is a deliberate act, not an accident.

### R-MASK-8 — An older daemon + newer gate (missing grace block) read as "grace silently active"
- **Mechanism:** a gate parsing a payload with no `detail.grace` could misread absence as "no objection".
- **Mitigations:** reversal property (decisions §3a) — absent `grace` block ⇒ class `unknown` ⇒ **fail-closed, no grace**; the current soak behaviour is preserved verbatim.
- **Pin:** degraded-mode compat pin in the counter pack: a body without `detail.grace` exercises the pre-change path exactly (no HOLD, no extension).
- **Residual:** none.

### R-MASK-9 — C4's `recoverable: true` being read as "safe to wait" by a future consumer
- **Mechanism:** `detail.mcp.<server>.recoverable` answers "may the gate wait for this?" (decisions §3h H4); a future consumer could conflate an MCP server's recoverability with *readiness* recoverability and hold the soak on a broken server's account.
- **Mitigations:** B2's pin makes MCP a non-dependency of readiness *a tested property*; `detail.mcp` is advisory (additive detail, never feeds `status`/`components`/`reasons`); the class table governs the gate, not the mcp map.
- **Pin:** `tests/unit/test_readiness_mcp_independence.py` (no MCP symbol in the readiness components) + `tests/integration/test_mcp_degrade_alone.py` (broken server ⇒ readiness components unaffected).
- **Residual:** a future requirement that gates readiness on MCP would need to defeat the pin — that is the intended tripwire.

### R-MASK-10 — Conservative mode accidentally weakened
- **Mechanism:** `ENSEMBLE_SOAK_CONSERVATIVE=1` (`lib.sh:5065-5069`, `:5115-5149`) is the operator's day-one "no grace, no amnesty" escape hatch; a refactor that let the new grace code leak into the strict loop would remove the only pre-code mitigation for the next promote.
- **Mitigations:** B6 — conservative stays **byte-identical** (strict flag `_PROBE_SUPPRESSION_STRICT=1`, flat 300s, sleep 30, no early exit); the new semantics extend only the ADAPTIVE path.
- **Pin:** phase-1 exit grep — the conservative block is byte-identical by HASH, not by line number (`git show d5a4e4ad9:scripts/upgrade/lib.sh` vs `git show HEAD:scripts/upgrade/lib.sh`, extracting the strict block and comparing digests — the block is `:5115-5149` in the base; the hash form survives line drift); counter pack runs once with `ENSEMBLE_SOAK_CONSERVATIVE=1` asserting v0.18-identical outcomes.
- **Day-one operational mitigations (existing today, no code needed):** besides `ENSEMBLE_SOAK_CONSERVATIVE=1`, `MCP_POOL_ENABLED=false` (kills pool registration at `daemon/manager.py:3022-3024` AND the warm task at `:3565`) and `MCP_DISABLE_BUILT_IN_CONTEXT7=true` both exist today and remove the MCP churn from the next promote entirely.
- **Residual:** none.


### R-MASK-11 — R-2: the leak class keeps causing healthy-release rollbacks until the follow-up lands (ACCEPTED tradeoff, stated)
- **Mechanism:** with non-goal #3 still live, the **CONFIRMED** P1 mechanism — the readiness module's own documented **timed-out-probe connection retention** (`readiness.py:40-44`: “a timed-out to_thread leaves the underlying thread running … bounded leakage, acceptable at the 10s refresh cadence”) — plus the RCA-observed `idle in transaction` backends — **correctly** fires the `pool_leak` class (per `requirements.md` §4.4). **The “leaks a transaction per tick” phrasing is NOT the confirmed mechanism** (the probe's SQL is `FROM task` and `with engine.connect()` closes the connection at `readiness.py:799`; decisions §2 premise 10) — do not repeat it; and per §4.4 the P1 signal is ADVISORY, so the leak class is armed by the P2 oracle → the grace refuses BY DESIGN (R-MASK-4) → a healthy-but-leaky install under load still ends at cap-reach with `consecutive_green < 3` and rolls back. The design deliberately prefers a recurring false rollback of a healthy release over any chance of masking the self-sustaining leak.
- **Mitigations:** the interim objective is carried by C1/C2 (the MCP churn — the other co-cause — is quarantined/pre-checked), A3 (the resume burst is deferred), A4 (ghost-waiter bound + pool-state visibility), and the day-one knobs (R-MASK-10); the pool-state detail makes the rollback diagnosis instant (`detail.pool_state` + `detail.leak_signal` in the journal/reports).
- **Pin:** none possible for the tradeoff itself — it is the safety property working as specified; A5(i)'s healthy-leg is constructed leak-free so the gate proves the grace on the healthy shape (V2/R-1).
- **Residual:** the recurrence persists until the DB-transaction-discipline commission lands (requirements §7 #3 owner: "must land before A1's leak-refusal is relaxed"); frequency estimated by the V1 leak-rate measurement (phase-3 exit gate).


### R-MASK-12 — N-d(iii): the pre-existing 60s restart-anchored boot grace is class-blind and expires before a late burst
- **Mechanism:** the shipped boot grace (`scripts/upgrade/lib.sh:157-158`, armed at `:4266`, evaluated at `:3184-3212`) suppresses **ALL** transients for 60s after a restart — regardless of failure class (the unsafe shape CONSTRAINT 1 forbids, kept because it is already shipped and pinned) — and it **expires on elapsed time**, so a burst that peaks later (the incident's own shape: the daemon was ~75s old at the first green, the burst peaked later) lands OUTSIDE its window.
- **Mitigations:** the new class-gated grace (A1) is the COVER — it is armed on a grace-eligible class, refuses the leak class, and is bounded; conservative mode (`ENSEMBLE_SOAK_CONSERVATIVE=1`) disables both for operators who want the strict shape.
- **Pin:** the class-gate pin (a grace-eligible transient is HELD; a grace-INELIGIBLE one still RESETs) plus the conservative-mode byte-identity pin.
- **Residual:** **the 60s class-blind window remains shipped** until the gate-side change is promoted — during it, a genuinely broken daemon is suppressed for up to 60s (the never-green trigger still fires afterwards, so the release is still rolled back — the window delays, never cancels, the rollback). It also means the OLD grace is not itself leak-refusing: the leak class can be suppressed for up to 60s after a restart.

---

## Category 2 — What could LEAK A CHILD PROCESS

### R-LEAK-1 — The unbounded `stdio_wrapper.py:306` wait never reaching the cleanup block
- **Mechanism:** `await self._ready.wait()` (`daemon/mcp/stdio_wrapper.py:306`) has no timeout; a hung handshake parks `TaskScopedStdioClient.__aenter__` forever, so the caller never reaches `warmup_pool.py:333-344`'s `except BaseException` cleanup — the `npm exec` + node chain survives.
- **Mitigations:** E2b — a **bounded** handshake wait (the bound itself is normative in `requirements.md` §4.6 (P6); the only relational property kept here is that it stays **strictly below** the warmup pool's 60 s outer budget, which remains the authority) wired into the existing `except BaseException` cancel-and-await path (`:300-312`), so expiry cancels the background task and propagates into the caller's cleanup.
- **Pin:** `tests/unit/tools/test_stdio_wrapper_bounded_wait.py` (bounded return + task cancelled + `< 60` invariant); B3 pid-liveness leg of `tests/integration/test_mcp_child_liveness.py` (0 surviving children after a forced handshake hang).
- **Residual:** a child that ignores SIGKILL is beyond any in-process reaper — the boot sweep remains the last line of defence across incarnations.

### R-LEAK-2 — `warmup_pool.py:275` `__aenter__` outside the timeout scope
- **Mechanism:** `streams_cm.__aenter__()` sits **before** `async with asyncio.timeout(60):` (`:273-278`), so the 60s budget never covers the handshake; a hung spawn blocks unbounded and the celebrated cleanup block is unreachable for exactly the failure it was written for.
- **Mitigations:** E2a — the `async with` opens **before** the `__aenter__` call; the handshake now consumes bounded budget and expiry raises into the `except BaseException` cleanup (`session.stop()` + `streams_cm.__aexit__`).
- **Pin:** forced-hang integration leg in `test_mcp_child_liveness.py` asserting the cleanup block executes (session closed) AND the pid census is 0 — **on the timeout/cancellation leg, not only the clean-failure leg**; phase-2 pins must stay green through phase 3 (regression fences).
- **Residual:** **R-LEAK-2's bound is NECESSARY BUT NOT SUFFICIENT (explorer G1, R-LEAK-7):** bounding the wait does not fix the structural orphan path below. See R-LEAK-7 for the load-bearing residual.

### R-LEAK-3 — The boot reaper's `starttime` predicate is structurally ineligible for within-incarnation children
- **Mechanism:** `boot_subprocess_sweep.py:36-45` requires a candidate's `starttime` to be **strictly older** than this process's own — everything the current incarnation spawned is younger by construction, so the existing reaper can NEVER reap this incident's children; naively "extending the sweep" would need a new predicate + new PID-recycle reasoning on a hot path.
- **Mitigations:** narrowed-B3 ruling — prevention first (the C1 quarantine stops the spawn attempts entirely), plus a **scoped** live reaper for ONE quarantined server's children (using `stdio_wrapper.py`'s registered `[(pid, start_time)]` pairs — exact pids, not a census); the general sweep is NOT extended.
- **Pin:** quarantine pin asserts no further spawn attempts after OPEN; scoped-reaper leg asserts the quarantined server's registered children are gone while unrelated children survive.
- **Residual:** children of a server that was **not** quarantined (e.g. a one-off failure below threshold) rely on the normal `__aexit__` teardown — which R-LEAK-1/2 now bound.

### R-LEAK-4 — A quarantined server's children surviving quarantine
- **Mechanism:** quarantine stops *future* spawns; a child mid-handshake at the moment of quarantine could outlive the decision.
- **Mitigations:** the quarantine action in `init_guard` runs the scoped reaper for that server as part of the transition (kill bounded to the server's own registered chain), then flips the registry entry.
- **Pin:** `test_mcp_init_guard.py` mutation leg — kill-call observed on quarantine for exactly the target server's pids.
- **Residual:** a fork-bombing child that re-parents itself escapes pid registration — accepted; the boot sweep still catches it next incarnation (documented 4.2-day context7 precedent, `boot_subprocess_sweep.py:21-23`).

### R-LEAK-5 — A failed init leaving a phantom pooled connection (pool-shaped leak)
- **Mechanism:** a partially-created pooled entry (spawned but never initialised) could linger as a "healthy-looking" pool slot that serves nothing — today's only visibility is a WARN/`logger.error` (`warmup_pool.py:184-188`).
- **Mitigations:** phase-2 cleanup reachability (R-LEAK-2) + the pool census assertion: after a failed init, `get_stats()` returns to its pre-attempt values.
- **Pin:** `test_mcp_child_liveness.py` pool-census leg (delta zero).
- **Residual:** a leak INSIDE psycopg_pool's own accounting would evade this pin — out of repo scope.

### R-LEAK-6 — The "unclosed aiohttp session" claim silently treated as fixed
- **Mechanism:** the RCA asserts an aiohttp-session leak, but `grep -rn aiohttp daemon/mcp/` returns **zero** hits (aiohttp lives in the Slack/Discord/Telegram adapters) — claiming a fix would be inventing a result.
- **Mitigations:** N-2 narrowing — this commission fixes only the provable leaks (children, session/streams); the aiohttp clause is routed to a follow-up with the mechanism explicitly marked unverified.
- **Pin:** scope grep at phase-2 exit (`grep -rn aiohttp daemon/mcp/` → 0) recorded in the exit gate — the claim cannot even be expressed against this tree.
- **Routing hint for the follow-up (explorer):** aiohttp enters via **discord-py 2.7.1** (`uv.lock:458-466` pulls `aiohttp`); the daemon's aiohttp sites are the source adapters — `discord/adapter.py:45,:1854`, `slack/adapter.py:823`, `telegram.py:123`/`:361`/`:690` (telegram's sessions are **long-lived**, closed only at `:382`/`:405`). The “unclosed aiohttp session” warnings point THERE, not at MCP.
- **Residual:** the real session-class leak (if any) remains open in the follow-up lane — now with a concrete place to look.


### R-LEAK-7 — The STRUCTURAL orphan path: cancellation during `__aenter__` skips the SDK shutdown entirely (G1)
- **Mechanism:** verified at `daemon/mcp/stdio_wrapper.py`: a cancellation landing during `await streams_cm.__aenter__()` (`_run` at `:386`) hits `except asyncio.CancelledError: raise` (`:387-388`) and re-raises BEFORE the `finally:` at `:412-414` can install `await streams_cm.__aexit__(None,None,None)` — so the SDK's process-group shutdown (stdin close → killpg SIGTERM → killpg SIGKILL) NEVER runs and any spawned child survives. `warmup_pool.py:333-344`'s `except BaseException` cleanup cannot repair it: after G1 the wrapper's task is already cancelled, so `__aexit__` (`stdio_wrapper.py:352-363`) awaits a dead task and returns. Supporting holes: **G3** the reap hard-cancel (`warmup_pool.py:694-709`) fires while `_run` sits at `await self._close.wait()` (`:411`), whose finally's own `__aexit__` re-raises CancelledError (`:415-416`); **G4** `managed_session.py:73-77` re-raises caller-cancellation and the `except Exception: pass` at `warmup_pool.py:336-339` does NOT catch CancelledError ⇒ the `streams_cm.__aexit__` at `:341` is skipped; **G5** shutdown drain reaches only queue-resident connections (`warmup_pool.py:765-786`) — acquired/transferred ones close only via `close_all_connections()` (`daemon/manager.py:14761`); **G6** failed-transfer release is best-effort (`mcp_service.py:140-151`).
- **Mitigations:** the phase-2 G1 guarantee — the inner transport `__aexit__` is `finally`-wrapped (or an explicit child-tree kill performed) on the cancellation/timeout path; the bounded waits (R-LEAK-1/2) remain necessary (they make the path REACHABLE and bounded) but are not the fix. **Pinned-SDK caveat:** the explorer read the upstream SDK from `master`, not the pinned `mcp==1.27.1` (`uv.lock:1212-1214`) — the mechanism must be re-verified against the pinned version at implementation (phase-2 step 0).
- **Pin:** the pid-liveness pin's **cancellation/timeout leg**: injected cancellation mid-handshake (and bounded-wait expiry) must yield 0 surviving children — proving the shutdown/killpg path executed despite the re-raise at `:387-388`.
- **Residual:** a child that ignores SIGKILL, or an SDK whose pinned version shuts down differently than `master` (hence step 0's re-verification); the boot sweep remains the last-resort reaper across incarnations.


### R-LEAK-8 — W-3: the deferred `/proc`-scan variant is a named REJECTED risk
- **Mechanism:** an "eventually-consistent" (fire-and-forget) `on_ready` scan looked like the cheaper off-loop shape, but a deferred scan that finds a **dead** child returns `[]` ⇒ the teardown reaper has no handle for exactly the child that needs reaping; and the existing pin `tests/test_resource_pid_registry.py:505-510` requires child registration BEFORE `__aenter__` returns — a deferred shape breaks that contract and would have to weaken the pin.
- **Mitigations:** council-ruled shape adopted instead: `on_ready` widened to `Callable[[], Optional[Awaitable[None]]]` — the hook scans off-loop (`to_thread`) and the transport AWAITS the awaitable BEFORE `_ready.set()` (work off-loop, await on-loop). Fire-and-forget is not shipped.
- **Pin:** phase-2 pin (a): registration observable immediately after `__aenter__` returns; a fire-and-forget shape fails the pin by construction.
- **Residual:** the await adds the scan duration to the handshake window — bounded by the same 60s outer budget and the per-spawn scan count (E3).


### R-LEAK-9 — N-d(i): DNS false-positive — a resolvable-but-dead host passes the pre-check and still hangs
- **Mechanism:** C2's pre-check is a **DNS resolution** probe: a host that RESOLVES but is unreachable (firewalled, dead upstream, blackholed) passes the pre-check ⇒ the child is spawned anyway ⇒ the package's own init fetch hangs to its 30 s timeout → the 3×10 s retry chain runs → children spawn/hang repeatedly until the breaker opens. The pre-check therefore proves **resolvability, not reachability** (it is deliberately a probe, never a pinned connection — F1).
- **Mitigations:** C1's init-path breaker is the BACKSTOP — after N (3) failed init attempts the server is quarantined for the boot and the spawn stops; the per-attempt cost is bounded by the 60s outer budget; the per-boot quarantine + `detail.mcp` surface make the stall visible.
- **Pin:** the C1 quarantine pin (a server whose command always fails is quarantined after N attempts, with no further spawns) — this is the leg that covers the false-positive class.
- **Residual:** **a resolvable-but-dead host still costs N × (30s hang + child spawn) per boot** before quarantine — the DNS pre-check cannot shorten that; the residual is bounded and visible, not eliminated. A TCP/TLS reachability probe would close it but is out of scope (it would re-introduce a network dependency into the boot path).

---

## Category 3 — What could STARVE THE EVENT LOOP AGAIN

### R-STARVE-1 — The DNS pre-check run on the loop
- **Mechanism:** `getaddrinfo` blocks; a "cheap synchronous check" shortcut on the loop thread would stall the whole daemon for the resolver's duration — recreating the starvation this commission fixes (decisions §3f alternative explicitly rejected).
- **Mitigations:** F3/E3 — the pre-check runs via `asyncio.to_thread`, bounded at 2s, non-fatal, per-attempt.
- **Pin:** `tests/unit/tools/test_dns_precheck.py` asserts the to_thread hop (monkeypatched executor) AND the 2s bound; the loop-lag pin runs with a slow-resolver fake to prove responsiveness.
- **Residual:** a pathological resolver (2s of thread-blocked resolution per attempt × ≤3 pre-quarantine attempts) is bounded and off-loop — negligible.

### R-STARVE-2 — The `/proc` scan staying on the loop
- **Mechanism:** `_stamp_children` (`stdio_wrapper.py:494-500`) does sync `read_bytes`/`read_text`/`list(iterdir())` (`:116`/`:132`/`:179`) under a `threading.Lock` **on the loop thread**, inside the spawn path, O(`/proc` entries) per spawned child — the verified loop-blocking site.
- **Mechanism (extended, explorer-confirmed framing):** the starvation is **cumulative churn, not one long call** — alongside the scan, the spawn path runs **synchronous secret/KMS marker resolution ON the loop** (`resolve_secret_markers` `daemon/services/secret_resolver.py:300` + `resolve_env` `daemon/services/kms_resolver.py:149`, plain `def`, called inline at `warmup_pool.py:256-268` and `connection_manager.py:229-237`) and in-loop per-line JSON-RPC parsing + schema adaptation (`warmup_pool.py:315-324`).
- **Mitigations:** E1 — `on_ready` becomes a thin scheduler handing the scan to `asyncio.to_thread`; one scan per spawned child (count unchanged); registration complete BEFORE `_ready.set()` (W-3 awaited shape — the deferred variant is R-LEAK-8, rejected); **the sync secret/KMS resolution hops to a thread** (fail-closed preserved — phase-2 step 1). Off-loop precedent: the loader-cache warm (`run_in_executor(None, …)`, `daemon/manager.py:3910`); capped-executor precedents `plane_sync_service.py:253` (2), `question_pause_set.py:137` (1); **no `set_default_executor` anywhere** — `run_in_executor(None, …)` shares asyncio's default pool.
- **Pin:** `test_stdio_wrapper_bounded_wait.py::test_on_ready_schedules_off_loop` (an inline call FAILS the test) + the loop-lag integration pin with warmup in flight.
- **Residual:** the scan still costs a thread-slot per spawn — bounded by the default executor and by the quarantine (a dead server stops spawning after N attempts).

### R-STARVE-3 — A new off-loop mechanism introducing unbounded thread growth
- **Mechanism:** moving work off the loop via unbounded `to_thread`/executor submission could exhaust threads or queue unboundedly — a different starvation.
- **Mitigations:** E3 rules — the scan is one `to_thread` per spawned child (same count as today); the DNS pre-check is per-attempt with attempts bounded by the breaker threshold (3) before quarantine; no new pools/loops/threads are created.
- **Pin:** census assertions in the pins (exactly one scheduled scan per spawn; pre-check attempts ≤ threshold); loop-lag pin stays under bound with multiple servers warming.
- **Residual:** a pathological spawn rate (many servers × retries) is capped by the quarantine — that is the design's answer, not a thread budget.

### R-STARVE-4 — The warmup task still being ON the loop despite being a task
- **Mechanism:** `self._warmup_task = loop.create_task(...)` (`daemon/manager.py:2045/:3137/:3565-3566`) is background *scheduling*, not off-loop execution — a task runs on the single asyncio thread; `session.initialize()` and tool discovery still execute there (bounded 3×10s inside the 60s outer budget).
- **Mitigations:** D-5 resolution (decisions §3e alternatives) — the targeted fix (thread hop + bounded waits) rather than thread-ifying the warmup; the AC is the loop-lag pin, which measures the outcome, not the mechanism.
- **Pin:** `tests/integration/test_mcp_warmup_loop_lag.py` — the **§4.6 (P6) thresholds** (p95 event-loop scheduling lag ≤ 250 ms AND max ≤ 500 ms) (periodic heartbeat task while the R-1 concurrent burst — MCP warmup + job-recovery + auto-continue resume — runs) AND `/livez`+`/readyz` within budget with a hanging server warming. **Rationale:** the smallest readiness probe budget is the 0.5s `SELECT 1` probe (`readiness.py:45`), which needs multiple loop turns to service, so lag must stay well under 500ms for the probe to finish in budget.
- **Residual:** bounded on-loop CPU (JSON parsing in discovery) remains; if a future incident shows the bound is too loose, the thread-ified-warmup alternative is pre-evaluated and rejected for cause — revisit with data, not reflex.

### R-STARVE-5 — The global `_schema_cache_lock` re-serialising discovery
- **Mechanism:** one global lock (`daemon/services/mcp_service.py:203`) + per-instance `_preload_locks` (`:194-195`) serialise discovery across all servers/instances; a slow server's discovery delays every other instance's lazy load — out of B1's named sites but part of the same family.
- **Mitigations:** indirect — the quarantine stops a dead server from *ever* holding the lock chain (the incident's actual amplifier); the ×4 cold-start multiplier is bounded by quarantine + pool-size knob already existing (`MCP_POOL_DEFAULT_POOL_SIZE`, `daemon/config.py:2894`).
- **Pin:** none in this lane (explicitly out of scope); the degrade-alone pin proves one broken server no longer dominates the warm path.
- **Residual:** the global lock itself remains — routed as a noted residual for a future warm-path performance lane; not a safety property.


### R-STARVE-6 — ONE hanging spawn wedges MORE than itself (ordering hazard: A5/A8/A9)
- **Mechanism:** `pool.warmup()` (`daemon/manager.py:3158`) must **return** before `start_health_check` (`:3161`) and `eager_warm_schemas()` (`:3170`) start — and the `except Exception` at `:3162` never fires on a hang (a hang raises nothing), so one hanging server prevents the health check AND the schema warm from EVER starting. **A8:** a replenish stuck in the unbounded spawn holds one of only **2** semaphore slots forever (`warmup_pool.py:108`, `:569`) — the pool stops replenishing anything else. **A9:** the initial warm is an UNCAPPED `asyncio.gather` over all servers (`:190-193`); `Semaphore(2)` covers replenishment only.
- **Mitigations:** phase 2 bounds the spawn path (bounded handshake, E2a budget) so a hang becomes a bounded failure that lets `_warmup_and_report` progress; the pin additionally asserts the ordering property (the two starters fire while a server is deliberately hanging); the quarantine (phase 3, C1) stops a dead server from consuming the semaphore repeatedly.
- **Pin:** phase-2 exit-gate item 5 (hang-wedge assertion: `start_health_check` + `eager_warm_schemas` start with a hanging server warming).
- **Residual:** the uncapped initial gather remains (bounded in practice by server count + the breaker after phase 3); a per-server spawn deadline is the natural follow-up if the bound proves loose.

---

## Category 4 — What could TOUCH LIVE STATE

### R-LIVE-1 — An unfenced test boot hitting `ensemble_prod` (env-poison)
- **Mechanism:** ambient `POSTGRES_*` in the agent shell has hit LIVE prod 6 times (2026-09-21 → 10-04); `env -u POSTGRES_*` is a **silent no-op** (bash does not glob env names), so an "unset" ambient `POSTGRES_DB=ensemble_prod` stays visible to a test boot.
- **Mitigations:** house rule enforced in every pack/pin — per-variable scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT`) **or** explicit values; scratch DB created/dropped per run; `ENSEMBLE_SELF_ENV=dev`; explicit non-live `PORT`.
- **Pin:** each pack's header assertion: `POSTGRES_DB != ensemble_prod` and `PORT` not in the live/demo set (9797/7979/8079-Prod) before any boot; A5 harness commands use the scrub form verbatim.
- **Residual:** a future contributor bypassing the packs — the fence lives in the packs AND the pins, and the register entry exists to make the bypass reviewable.

### R-LIVE-2 — A test resolving a HOME-derived (live-shaped) install dir
- **Mechanism:** the 2026-10-12 incident: a test script derived `$HOME/agents-ensemble` and deleted the live install (twice). Any pin that resolves install paths from the real `$HOME` can destroy live state.
- **Mitigations:** mktemp **FAKE_HOME** for every test process + an escape guard that refuses to run when a resolved path lands under the real `$HOME`.
- **Pin:** each pack's escape-guard self-test (a deliberately live-shaped resolution must abort the pack); A5 harness command shape includes `HOME="$(mktemp -d)"`.
- **Residual:** none for pack-mediated runs; ad-hoc shell experiments remain the operator's discipline.

### R-LIVE-3 — A staged boot smoke binding the LIVE promote journal / pruning a live ceremony nonce
- **Mechanism:** a staged binary's path derivation resolves the **live install root** (`daemon/api.py` + `upgrade_tools.py` resolve 'sandbox' from the frozen binary's path), so an un-isolated smoke binds the live `releases/state.json` and its 90s gc tick could prune a live ceremony's nonce (2026-10-21 finding).
- **Mitigations:** phase-3 protocol — scratch copy of the binary + explicit `PORT` + explicit `ENSEMBLE_CONFIG` (+ FAKE_HOME), the prior-lane pattern; the smoke self-asserts its install root is under the scratch dir and refuses otherwise.
- **Pin:** step 2 of the phase-3 exit gate — live `state.json` sha before == after the smoke; scratch-root self-assertion in the smoke script.
- **Residual:** none for the scripted smoke; manual staging experiments are out of the plan's reach by construction (the protocol is the only sanctioned path).

### R-LIVE-4 — The promote-txn hold reading/writing `state.json`

> Normative source: requirements.md §4.7 (P7 — A3 release rule) — the hold module named in the pin below is the implementation of §4.7's predicate; the entry is the read/write-safety risk around it, not a restatement of the rule.
- **Mechanism:** A3's anchor is the shell-owned `in_flight` record in `<install_dir>/releases/state.json`; a hold implementation that *wrote* anything (a marker, a counter, a heartbeat) would mutate promote-machinery-owned state from the daemon — a new writer with unknown interleaving against `promote.sh`/`journal_close_txn` (`lib.sh:1115-1118`).
- **Mitigations:** C1 — READ-ONLY readers only: `journal_owner_heartbeat_fresh` (`daemon/tools/upgrade_journal.py:281-319`, documented read-only, fail-closed on torn/OSError) and `_journal_read` (`upgrade_tools.py:693-723`, non-mutating); the daemon never writes `kind="promote"` (premise P5 — only `kind="restart"`, `upgrade_tools.py:2891-2902`); hold counters are in-process. **Normative source: requirements.md §4.7 (P7 — A3 release rule)** — the read-only constraint is how this entry keeps the hold a pure consumer of §4.7's predicate.
- **Pin:** `tests/unit/tools/test_promote_txn_hold.py` read-only leg — writer-spy on the journal module + scratch-file sha unchanged across hold ticks with a synthetic promote txn (run as `uv run python -m pytest …`; `python` does not exist on this host and the worktree has no `.venv` — `uv` is off-PATH at `/home/nea/.local/bin/uv`).
- **Residual:** none; the pin makes the read-only property mechanical.

### R-LIVE-5 — The shell fix consumed from the working tree before it is committed
- **Mechanism:** the shell half is working-tree-sourced (`_resolve_scripts_dir` env → repo checkout → install dir, `daemon/tools/upgrade_tools.py:1919-1942`; the frozen binary carries no `scripts/`), so the next promote would execute UNCOMMITTED lib.sh changes — an uncommitted fix is not a fix (decisions B7).
- **Mitigations:** delivery mechanics — the lib.sh change is committed in the phase-1 commit series on the branch; staging happens only after the branch is merged; stage-time `git status` hygiene.
- **Pin:** phase-1 exit gate grep on committed objects (`git grep <counter locals> d5a4e4ad9..HEAD -- scripts/upgrade/lib.sh` — verify against OBJECTS, not the working tree); the A5 packs source lib.sh from the checkout and assert the fix's markers are present.
- **Residual:** an operator running promote from a dirty shared checkout between merge and stage — mitigated by house merge discipline (clean worktree = commit point).

### R-LIVE-6 — The A4 sizing knob creating a DB connection-budget outage
- **Mechanism:** `max_size` above the database's `max_connections` budget (shared with P1's 5+10, other clients, superuser_reserved) converts a saturation fix into a new outage class (requirements A4 safety clause).
- **Mitigations:** D2 formula clamped `min=5, max=20`; shipped default 10 = 7 writers + 1 probe + 2 headroom (derived, not guessed); the knob is env/yaml opt-in beyond default; the implementer must state the DB-side budget in the lane report; P1 is NOT resized (the poisoned pool's repair is a non-goal).
- **Pin:** config-resolution + clamp pins (`tests/unit/tools/test_checkpoint_pool_config.py`); ghost-waiter pin asserts the timeout bounds queue growth at the new size.
- **Residual:** an operator setting `LIMITS_CHECKPOINT_POOL_MAX_SIZE=20` against a tiny DB — the clamp is a guardrail, not a permission slip; documented in config.yaml.

### R-LIVE-7 — Pin/registry state leaking across tests (module-level quarantine registry)
- **Mechanism:** the per-boot registry is module-level; a test that quarantines a server leaks that state into later tests in the same process, producing order-dependent reds (house: order/load-sensitive reds need 3× isolated runs before classification).
- **Mitigations:** registry exposes `clear_quarantine`/reset; the pin fixtures reset the registry in setup/teardown; breaker instances are per-server-keyed and rebuilt by the guard fixture.
- **Pin:** `test_mcp_quarantine_registry.py` runs its scenarios twice in-process (state cleared between) and the full unit suite passes under random order (`-p no:randomly` NOT used — i.e. default order plus a shuffled rerun leg).
- **Residual:** a foreign test importing the registry without the fixture — covered by the suite-level shuffled rerun.

### R-LIVE-8 — DNS pre-check pins making real network egress
- **Mechanism:** a pin resolving `api.context7.com` for real couples the test gate to live DNS and the internet; a NXDOMAIN *today* would flip a pin's verdict for environmental reasons (the 10-09/10-26 precedent of environmental false failures).
- **Mitigations:** all C2 pins use a fake resolver seam (monkeypatched `getaddrinfo`); no pin performs real egress; the real-host behaviour is covered once in the staged smoke (clearly marked, best-effort).
- **Pin:** `test_dns_precheck.py` asserts the fake seam is in place (a real-resolution attempt fails the test).
- **Residual:** the one optional smoke leg — explicitly non-gating.


### R-LIVE-9 — S5: the `mcp_set_env` routed defect leaves an operator bypass open (residual recorded)
- **Mechanism:** `mcp_set_env` (`daemon/tools/infra.py:1527`) writes `mcp_servers.config.env` with **no `is_builtin` guard** — unlike the HTTP API (`routers/mcp_servers.py:760-768`, 403 `BUILTIN_SERVER_PROTECTED`). With C3's schema landed, the sanctioned template path works — but the tool remains an unsanctioned route to the same value: an operator (or agent) can bypass the template pin and set a stale/wrong base out-of-band.
- **Mitigations (this lane):** adjudication 1 / decisions §3g G6 — the tool keeps its behaviour; the hole is recorded as a separately-owned defect; the sanctioned path is documented and the tool's writes remain marker-safe (KMS chain intact).
- **Closure shape (recorded for the owning lane):** an **allow-list of operator-pinned keys for built-ins** — NEVER a blanket ban: the BYOK self-provisioning chain (`kms_attach` → `config.env` markers) depends on this tool.
- **Named owner:** the **agent-tool-surface consistency lane** — the `daemon/tools/infra.py` follow-up, **the same lane as the BYOK self-provisioning chain audit** (so the guard shape is decided where the chain's audit already lives).
- **Pin:** none in this lane (deliberately out of scope); phase-3's gate asserts `git diff` shows the tool untouched (adjudication-1 respect).
- **Residual:** the bypass remains open until the owning lane closes it with the allow-list shape.

### R-LIVE-10 — C-1: the hold's no-next-pass loss shape (rejected design, recorded)
- **Mechanism (the wrong shape, for the record):** a release trigger of "re-evaluated on the next pass" is FALSE for the boot pass — it runs EXACTLY ONCE per boot (`daemon/api.py:2703`), and during a promote the daemon restarts BEFORE the gates, so the pass always runs with the txn live and defers everything; deferred candidates would then fall to StaleTaskRecovery's force-cancel-retry (~boot+10min), which **force-cancels RUNNING work** (a live-state mutation) and **loses the checkpoint resume**. The sister pass `sweep_orphaned_live_turns` (`auto_continue_boot_pass.py:726`, wired `api.py:2708`) is worse still: its orphans are TASK-LESS, so StaleTaskRecovery cannot reap them at all.
- **Mitigations:** adjudicated option (b) — the hold module owns a per-seam deferred-candidates list and re-consults the promote-txn predicate on a bounded cadence (read-only `state.json` read, default 5s, only while deferrals outstanding); on flip-to-false it releases through the ORIGINAL seams (checkpoint-resume path, never a cold dispatch); composes with fail-open-on-stale; the seam list is FOUR (boot pass, runtime resume dispatch, eligible-pending sweep, orphan sweep).
- **Pin:** the C-1 pin — a deferred candidate RESUMES VIA THE CHECKPOINT PATH after the txn closes (checkpoint reuse asserted, no cold dispatch observed); plus the read-only pin (zero journal writes).
- **Residual:** a promote txn that dies without closing holds deferrals until the 60s heartbeat window goes stale (fail-open) — by design; the 5s release cadence adds ≤5s latency to post-promote resumes.


### R-LIVE-11 — BLOCKING 3: the schema-version heal DESTROYS the operator-set env (context7 base + KMS markers)
- **Mechanism:** the built-in schema-version heal at `daemon/manager.py:2855-2867` is a **WHOLE-CONFIG REPLACE** — `update_mcp_server(existing.id, config=default_config, …)` where `default_config` is `definition.build_config({})` (its own log line says "config reset to defaults due to schema version change"). The hazard is documented in-repo at `daemon/mcp/warmup_pool.py:858-880`: the row's `config.env` is the SOURCE OF TRUTH and “a stale definition would silently drop those keys”. Consequence: the moment C3 bumps context7 to schema_version "3", the operator's pinned `CONTEXT7_API_URL` and any KMS-marker env keys are **DROPPED on the first boot after the upgrade**, and C2's DNS pre-check then validates the **DEFAULT** host — the exact silent-wedge the pre-check exists to prevent.
- **Mitigations (adjudicated):** ENV-PRESERVING MERGE in the heal — operator-set env keys and KMS-marker keys are preserved across the schema-version change; only template-derived defaults are rebuilt; **C2 reads the EFFECTIVE post-heal env** (row env × definition defaults, per the pinned precedence at `warmup_pool.py:858-880`).
- **Pin:** the 2→3 heal pin — `config.env` survives the schema-version heal with the operator value intact (operator-set `CONTEXT7_API_URL` + a KMS-marker key still present after the heal; only template-derived defaults rebuilt).
- **Residual:** the heal's merge must also preserve the row's non-env config (args/timeout) shape correctly — a merge bug here is silent; the pin asserts env specifically, the args shape is covered by the C3 schema pin.

### R-LIVE-12 — BLOCKING 4: a daemon-side journal write can DROP `in_flight` and release A3's deferred resumes mid-promote
- **Mechanism:** `journal_history_append` (`daemon/tools/upgrade_journal.py:391`) is an **UNLOCKED read→mutate→whole-document write** (`:398` read, `:405` write; the caller-discipline note is at `:352-353`: "P2.3 caller discipline still serializes journal writes externally"). C4's MCP state-transition journal emit takes a **stale snapshot** of `state.json`; if the shell's promote txn has advanced since that snapshot, the write lands an `in_flight`-LESS document ⇒ A3's `promote_txn_live()` reads `in_flight` absent ⇒ **the hold releases deferred resumes mid-promote** — the exact burst A3 exists to suppress. R-LIVE-4 covers only the READ path.
- **Mitigations (adjudicated):** SUPPRESSION — the C4/init_guard state-transition journal emit is suppressed while `promote_txn_live()` is true; a suppressed write is **BUFFERED in memory (bounded: one entry per server+state, newest wins)** and flushed when the predicate flips false or at graceful shutdown — **never dropped**. Lock-acquire is **REJECTED**: the journal lock belongs to the shell ceremony; daemon-side acquisition mid-promote is a new cross-process hazard, and the transition is deferrable observability.
- **Pin:** the suppression pin — a daemon-side MCP state transition during a LIVE promote txn must **not** drop `in_flight` (the journal's `in_flight` is byte-identical across the transition; the buffered entry flushes on flip-to-false / graceful shutdown).
- **Residual:** during a promote the transition is invisible in the journal until the flush (bounded by the ceremony) — observability is deferred, never the safety property; the buffer is bounded so it cannot grow without limit.


### R-LIVE-13 — N-d(ii): the 5s release re-consult write-path (bounded, read-only — but named)
- **Mechanism:** A3's release driver re-consults the promote-txn predicate every 5s while deferrals are outstanding, each tick a **read** of `<install_dir>/releases/state.json` while the SHELL is writing that same file (the promote txn's own `journal_write` is atomic temp+rename, and the daemon's readers are torn-safe). The risks: (a) a torn/intermediate read could make `promote_txn_live()` return False (the accessor is fail-closed on a torn journal) ⇒ a **premature release** of deferred resumes mid-promote; (b) a read cadence that collides with the shell's write cadence can observe a stale snapshot.
- **Mitigations:** the read path is READ-ONLY and torn-safe (`journal_owner_heartbeat_fresh` returns False on a torn journal — fail-closed, `upgrade_journal.py:281-319`); the cadence is bounded (5s) and only runs WHILE deferrals are outstanding (it stops the moment the lists empty); the release itself goes through the original checkpoint-resume seams (bounded, resumable) rather than a cold dispatch; the **release hysteresis of `requirements.md` §4.7 (P7)** — two consecutive false predicate reads ≥1 driver tick (5 s) apart — is the guard against a single torn read flipping the release (this register REFERENCES it; it does not define it).
- **Pin:** the A3 hold pin (deferred candidate resumes via the CHECKPOINT path after the txn closes; no txn ⇒ byte-identical behaviour) + the read-only pin (zero journal writes by the daemon).
- **Residual:** a single torn read that survives the hysteresis (or a shell that holds the file unwritten for >5s) can still delay a release by one tick — bounded and safe (deferral, never loss).

---

## Category 5 — What could WEAKEN THE NEVER-GREEN ROLLBACK (governing safety property)

> The property: cap-reach with `consecutive_green < 3` ⇒ `return 1` ⇒ auto-rollback (`lib.sh:5237-5244`), plus quarantine on the rollback route. Every design element above is re-examined here for how a WRONG implementation would weaken it, and the pin that guards each.

### R-NG-1 — HOLD counted as a green
- **Mechanism:** implementing HOLD as "advance by a fraction" or "advance after K holds" would let a never-green release accumulate a counter and survive cap-reach — the direct violation N-5 exists to prevent.
- **Mitigations:** the counter kinds are **normative in `requirements.md` §4.2 (P2)** — HOLD never advances and `consecutive_hold` resets to 0 on a GREEN; a green only counts behind ≤ `MAX_HOLDS_PER_GREEN = 2` consecutive holds, so the worst case is 3 greens over 9 observations and a never-green daemon ends at 0.
- **Pin:** counter pin stream (b) (`green, held, held, held` ⇒ 1 at cap-reach); stream (a) proves the legitimate path (`green, green, held, green` ⇒ 0).
- **Residual:** none — arithmetic.

### R-NG-2 — Grace without a hard bound
- **Mechanism:** a grace whose expiry extends on each new eligible observation never lapses — the ceiling is **normative in `requirements.md` §4.3 (P3): ONE observation-relative clock (`first_grace_eligible_observation + GRACE_HORIZON_S = 120.0` s), never extended, no boot-relative ceiling; the soak's absolute bound is `soak_entry + 120 + 180 = soak_entry + 300s`**.
- **Mitigations:** A1.3 — `grace_expires_at` set once from the first eligible observation, never extended; re-arm only after full lapse; extension cap spent-not-refunded; 900s outer deadline.
- **Pin:** A5(iv)(c) bound pin + grace-state-machine expiry tests (`test_readiness_failure_class.py`).
- **Residual:** none.

### R-NG-3 — Grace re-arming forever (flapping accumulation)
- **Mechanism:** re-arming on every lapse would let a flapping daemon accumulate unbounded total grace across the soak.
- **Mitigations:** the bound is **normative in `requirements.md` §4.3 (P3)** — one observation-relative clock, `GRACE_HORIZON_S = 120.0` s never extended; the soak's absolute bound is `soak_entry + 120 + 180 = soak_entry + 300s`, so the total added window is bounded regardless of re-arm count.
- **Pin:** state-machine test: three arm/lapse cycles assert total granted ≤ `GRACE_EXTENSION_CAP_S`.
- **Residual:** none.

### R-NG-4 — The leak predicate failing OPEN (no pool objects ⇒ eligible anyway)
- **Mechanism:** if the pool objects are unavailable (SQLite seam, test harness) and the predicate defaults to "no leak detected", a real leak on a mis-instrumented install would be grace-held — the exact R-MASK-4 hole through the predicate's back door.
- **Mitigations:** **normative in `requirements.md` §4.4 (P4)**: the leak signature's ORACLE is the P2 psycopg saver; the P1 SQLAlchemy signal is **ADVISORY** (`overflow() > 0` is non-zero on a busy-healthy pool by design ⇒ it MUST NOT arm the leak class; it is visible in `detail.pool_state`); the per-component streak arm fires **ONLY when the pool objects are UNAVAILABLE** (S9 qualification) — an unqualified streak would classify the incident's own sustained-busy shape as a leak and gut the grace; measured from pool objects, never `pg_stat_activity`.
- **Pin:** leak-predicate pin includes the "pool objects unavailable + streak" leg asserting `pool_leak` classification.
- **Residual:** a leak whose streak is interrupted by a fluke recovery tick within every 3-tick window — conservative tuning (decisions §9 Q2) keeps this under review; the reason strings stay visible either way.

### R-NG-5 — The mid-soak version gate weakened by the grace
- **Mechanism:** `gate_version` runs only on a green tick today (`lib.sh:5198-5208`, the C2-council rationale: no version evidence during a transient storm); a careless grace implementation could also suppress the version check or let the grace "hold" a version drift.
- **Mitigations:** the grace touches only the transient/503 class path; `identity_mismatch` is never grace-eligible (class table); the version check's green-tick gating and its `lib.sh:5204-5208` pin semantics are untouched (the older boot-grace comment reference is not the version-drift pin); the `identity_mismatch` class semantics are normative in `requirements.md` §4.0/§4.2.
- **Pin:** phase-1 counter pack includes a version-drift leg (drift on a green tick ⇒ immediate 1, grace or not); class-table pin asserts `identity_mismatch: NO`.
- **Residual:** none.

### R-NG-6 — The auto-rollback/quarantine branch itself altered
- **Mechanism:** the cap-reach `return 1` → ADR-005 rollback → quarantine (`promote.sh:696`; quarantine cleared only by re-stage, `lib.sh:1221-1234` (`journal_quarantine_clear` at `:1224`, its comment at `:1221-1223`; `:1218` is the tail of `journal_quarantine`)) is the property's enforcement arm; refactors that reroute the return or soften the quarantine would silently disarm it.
- **Mitigations:** B4 — the branch keeps exactly two outcomes (success / return 1); the only new journal event is additive (`soak_grace_extended`); `soak_deferred` (`:5088-5093`) untouched.
- **Pin:** **A5(ii) is the outranking gate** — rollback AND quarantine journal events present, `current` → `previous`, `previous` not quarantined; phase-3 gate asserts `git diff` shows no hunks in the quarantine/clear helpers.
- **Residual:** none — sub-case (ii) failing rejects the whole change.

### R-NG-7 — Conservative mode eroded (escape hatch lost)
- **Mechanism:** if the strict path changed, the operator would lose the only zero-code mitigation available **before any daemon code ships** (decisions §4's mitigation note).
- **Mitigations:** B6 byte-identical rule (see R-MASK-10).
- **Pin:** conservative-block diff grep + the conservative leg of the counter pack.
- **Residual:** none.

### R-NG-8 — The `soak_deferred` path abused as a silent skip
- **Mechanism:** `soak_deferred` returns 0 (soak skipped, gates remain source of truth); an implementation that granted extensions by *shrinking into* the deferral path would let a bad release pass unaudited.
- **Mitigations:** the deferral branch's arithmetic (`ceremony_remaining_s - 60 < floor`, `:5086-5093`) is untouched; extensions emit the NEW `soak_grace_extended` journal event so any grace-ridden soak is auditable (decisions §3b consequences).
- **Pin:** counter pack asserts: no `soak_grace_extended` without a HOLD; deferral leg unchanged; extension leg journals the event with the granted amount.
- **Residual:** none.

### R-NG-9 — a re-introduced fairness conditional in the never-green path (the withdrawn "fairness window" design; fairness observations are DIAGNOSTIC ONLY)
- **Mechanism:** the never-green trigger is **normative in `requirements.md` §4.1 (P1)** — cap-reach with `consecutive_green < 3` ⇒ return 1 ⇒ auto-rollback (+ quarantine), **UNCONDITIONAL**, with exactly two cap-reach outcomes; the ceremony-truncation case is the PRE-SOAK deferral (`lib.sh:5088-5093`), NOT a cap-reach outcome; **fairness observations are DIAGNOSTIC ONLY and must not influence the return value**. The risk this entry guards is a re-introduced conditional: an implementation that lets a fairness/diagnostic field influence the return value would ADD a success path to the rollback.
- **Mitigations:** the §4.1 reading is carried verbatim in the implementation: cap-reach returns 1 in **ALL** `<3-greens` cases; any fairness/diagnostic value is journal-line output only (**not a new success path**). The §4.1 grace-eligible-never-green leg is pinned (a grace-eligible daemon that never greens still rolls back).
- **Pin:** A5(ii)/(iii) integration legs assert return codes and elapsed-time bounds directly; the journal line's fair-window fields are asserted present and truthful.
- **Residual:** the flag is deliberately diagnostic-only in v1 — any future semantics change requires re-opening this register entry.

### R-NG-10 — Grace/extension constants drifted between shell and daemon layers
- **Mechanism:** the ceiling/extension numbers are **normative in `requirements.md` §4.3 (P3)**; the divergence risk this entry guards is that the two LAYERS carry them separately — the shell owns the gate-side ceiling/extension constants while the daemon owns the grace *state*. A silent divergence between the layers (e.g. the daemon's expiry computed on a different horizon than the shell's) would let the gate HOLD observations the daemon no longer considers graced — or vice versa — eroding the bound's meaning.
- **Mitigations:** the shell consumes the daemon-published `detail.grace` block (single source of truth for active/expiry); shell constants govern only the *extension* arithmetic, which is shell-owned by construction (decisions §9 Q6); both constants live in exactly one file per layer with the cross-reference in comments.
- **Pin:** counter pack pins the extension arithmetic against the constants as-written; the detail-payload pin asserts `grace_expires_at` is authoritative (a shell-side recomputation would fail the pin).
- **Residual:** an operator editing one layer's constant without the other — visible in the journal line (granted vs horizon), reviewed at promote.

### R-NG-11 — S6: every safety-critical pin leg carries per-leg planted-bug ⇒ RED mutation evidence
- **Mechanism:** a pin that passes on the old code (vacuous green) or passes for the wrong reason provides zero protection; the highest-risk legs are exactly the ones that are hardest to instrument (the G1 cancellation path; the leak-refusal path).
- **Mitigations:** the verification report must include, PER LEG, planted-bug → RED → restore → GREEN mutation evidence — explicitly called out for **the G1 cancellation leg** (plant: remove the `finally`-wrapped `__aexit__`/kill ⇒ the liveness pin must go RED) and **the leak-refusal leg** (plant: make the predicate ignore the monotonic-rise signal ⇒ A5(iv)(b) must go RED).
- **Pin:** the mutation-evidence section of the verification report itself (a leg without a recorded mutation cycle is an incomplete gate).
- **Residual:** mutation quality varies; the reviewer checks the RED was the *predicted* RED, not any red.
