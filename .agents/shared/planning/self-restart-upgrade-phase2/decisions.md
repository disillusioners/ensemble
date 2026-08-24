# Self-Restart / Self-Upgrade Phase 2 — Decision Log (ADR-style)

> **HARD CONSTRAINT (user directive — governs every decision below):**
> "NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine."

- **Date:** 2026-08-22 · **Author:** W3 (decisions.md) — continues the numbering of `.agents/shared/planning/auto-restart-upgrade/decisions.md` (ADR-001…015)
- **Siblings (do not author):** `plan-overview.md` + `phaseN-plan.md` (W1), `tool-api-design.md` + `risk-register.md` (W2); companion `test-strategy.md` + `promotion-ladder.md` (this dir)
- **Status:** all entries below are **RECOMMENDATIONS (rec)** pending review — Phase 2 introduces deliberate deviations from APPROVED ADRs (005, 009, 012, 015); every deviation is flagged.

Format: **Context → Options → Decision/Recommendation → Consequence**, each with *recommended default* and *what breaks if chosen otherwise*. ⚠ = needs user decision at review.

---

## ADR-016 (rec ⚠ review): `system_restart` tool added beyond ADR-015's two tools

**Context.** ADR-015 shipped two tools (`system_upgrade` + `release_info`, category `system_upgrade`, ari+jober). Phase 2's goal is Ari as the front door for restart + self-upgrade; restart is a distinct operation (no version change, no flip, no rollback window) and currently has no agent surface — an operator wanting a conversational restart would have to misuse `system_upgrade` or shell out. ADR-015's constraint stack still applies: no LLM in the recovery path; tools are front doors to deterministic machinery.

**Options.** (a) Two tools only — restart via `system_upgrade` no-op promote (abuses semantics, drags the 10-min rollback window into a simple restart); (b) add `system_restart` as a third tool in the same category; (c) no agent restart — operator-only.

**Decision (rec).** **(b).** `system_restart` joins `system_upgrade` + `release_info` in category `system_upgrade`. Constraints: **health-gated routing, never a raw kill** — the tool drives the same bounded-stop machinery Phase 1 built (SIGTERM + bounded wait via the launcher; `stop-ensemble.sh` SINGLE-TERM ownership pattern is the precedent), then requires the launcher respawn to pass `/livez` within the standard ≤60s budget before the tool reports success. Restart result sequencing must survive the restart itself (⟪SEAM: result-delivery contract — W2 `tool-api-design.md`⟫).

**Recommended default:** three tools, `system_restart` health-gated as above.
**If the user declines (two tools / none):** restart remains operator-only this phase and D7 (`test-strategy.md` §3) reduces to a scripted-restart drill; Ari cannot offer "restart yourself" conversationally — a stated Phase 2 goal degrades.

---

## ADR-017 (rec ⚠ NEEDS USER DECISION): env-target permission model — demo/dev/sandbox FREE (user directive), LIVE enforced 3-factor runtime gate

**Context.** ADR-015's two-factor gate (param `user_confirmed: true` + server-side user-originated trigger marker) was designed for ONE target: the install the agent runs in. Phase 2 spans four environments (dev 8079 / demo 7979 / sandbox / live 9797) and the hard constraint puts live out of bounds for all initiative work. A uniform gate would either over-gate rehearsals (drills blocked on confirmation theater) or under-gate live (unacceptable).

**Options.** (a) Uniform two-factor everywhere (safe for live, friction-locks every drill); (b) env-target model: demo/dev/sandbox free, live requires the enforced runtime confirmation gate; (c) config-file opt-in list per env.

**Decision (rec).** **(b).** Mechanism per ratified D-FA2.3/D-FA2.4: the daemon's own env is resolved from the **staged `ENSEMBLE_SELF_ENV=dev|demo|live|sandbox` marker in `INSTALL_DIR/.env`** (staged by `deploy.sh`/`stage.sh` — P2.1 T2; ADR-014 mechanism); `target_env` is the same 4-value enum and **must equal the marker value** (check-order step 3 — self-match runs BEFORE any live-gate logic). The **PORT-derivation fallback is REJECTED** (it reintroduces the 7979↔9797 typo class this gate exists to kill); **marker absent → every ACTOR tool refuses fail-closed (`env-marker-absent`, S-31) — read tools still answer**. (Scripts keep the install-dir + port + DB triple assertion as their own separate layer, `test-strategy.md` §5.) Marker target ∈ {demo, dev, sandbox} → executes freely (**user directive: "Demo/dev actions may proceed freely per normal flow"** — no human-confirmation gate on non-live targets). Marker target = live → **enforced 3-factor runtime gate (D-FA3.1 ruled naming: `user_confirmed: true` param + server-side user-originated marker + action-binding nonce)**, refusal otherwise — **a fabricated `user_confirmed` must NOT unlock live** (unit-tested, `test-strategy.md` §1 P2.2). U5 in `promotion-ladder.md` §5.

**Recommended default:** (b) with the `ENSEMBLE_SELF_ENV` staged marker as the fail-closed derivation source (D-FA2.3; PORT fallback rejected).
**If the user picks (a):** every demo drill needs a live-style confirmation ritual — D4/D5/D7 become slower and the confirmation surface is exercised so often its weight erodes; nothing else breaks. If the user picks (c): an operator-editable list becomes a silent privilege escalation vector — a stale list entry pointing at 9797 re-opens the live hole; not recommended.

---

## ADR-018 (rec ⚠ review): pipeline as env-parameterized scripts, NOT make-target wrapping — supersedes the make-promote framing of ADR-009/015

**Context.** ADR-009 defined `make stage/promote/rollback` (with `install` alias); ADR-015's `system_upgrade` was framed as "runs the same sequence as `make promote`". Verified hazard: `make build`/`make pyinstaller`/`make install` chain through **ensure-latest** (`git checkout latest && git pull`) which yanks a feature branch out from under the build — `scripts/deploy.sh:19-22` exists precisely because of this ("NEVER `make pyinstaller`/`make build`/`make install` … the ensure-latest chain would yank the feature branch", verified). D3 (APPROVED) demoted ensure-latest for staging, but the Makefile remains the wrong chassis for a script-invoked, env-parameterized, agent-callable pipeline.

**Options.** (a) Keep make targets as the pipeline, tools shell into `make promote ENV=demo`; (b) env-parameterized scripts under `scripts/` (extending `deploy.sh`'s pattern) as the canonical pipeline; make targets become thin deprecated wrappers or are retired; (c) a new Python pipeline module invoked by both.

**Decision (rec).** **(b).** Canonical pipeline = `scripts/` stage/promote/rollback with explicit `--target {demo|sandbox|live}` + `--version`, target-triple assertions built in (script-layer discipline, `test-strategy.md` §5 — the TOOL layer uses the `ENSEMBLE_SELF_ENV` marker per ADR-017/D-FA2.3), exit-75/78/1 semantics preserved. Supersedes the *make framing* of ADR-009 (whose substance — stage/promote/rollback decomposition, SIGTERM-not-kill-9, explicit VERSION, fail-if-not-at-tag — carries over unchanged). `system_upgrade` invokes the script, never `make`.

**Recommended default:** (b); retire the make wrappers during P2.1 to avoid two doors.
**If the user insists on (a):** every tool-invoked promote must neutralize ensure-latest — a footgun that has already bitten once (`deploy.sh:19-22`); also make targets complicate the env-target assertion story. If (c): more code paths to drill for the same guarantee; acceptable but slower to ship.

---

## ADR-019 (rec ⚠ review): ari-only tool exposure this phase — jober deferred (delta from ADR-015's ari+jober)

**Context.** ADR-015 approved `system_upgrade` + `release_info` for **ari + jober**. Phase 2 exposes the tools while jober's operational role in upgrade flows is still undefined; each additional allow-listed agent widens the blast radius of the env-target gate and doubles the drill matrix.

**Options.** (a) ari + jober now (ADR-015 as written); (b) ari only this phase, jober deferred to a later phase with its own drills; (c) ari + jober but jober read-only (`release_info` only).

**Decision (rec).** **(b).** ari-only in `tools.allow` for Phase 2; default-deny keeps the tools invisible to jober and everyone else (ADR-015 registration mechanics — `tools.allow` is the canonical signal). Jober's exposure is a one-line `meta.json` + drill addition later.

**Recommended default:** (b).
**If the user picks (a):** the drill matrix doubles (jober-driven restart/upgrade legs) and the two-factor marker must be validated across two agents' session semantics — schedule cost, not correctness cost. If (c): minimal delta, but a half-exposed category invites confusion about jober's role; either full deferral or full exposure is cleaner.

---

## ADR-020 (rec ⚠ review): agent tools shipped BEFORE drain/migration-guard phases — M3 drain-free sanction; manifest `rollback_safe` as interim gate; `daemon_meta` remains future

**Context.** ADR-015 placed agent tooling in Phase 7 (after drain/observer). Phase 2 pulls the tools forward on top of Phase 1 infra only. Sanctions already exist in the approved log: **M3 amendment — "Phase 3 promotes drain-free; drain slots in at Phase 4"** (ADR-009), and **ADR-007 M5 — "until `daemon_meta` lands (Phase 5), the same rollback-safety rule is enforced by the release `manifest` (`rollback_safe`) from Phase 2 — two enforcement layers, one rule, no phase-ordering hole."** Both verified in `.agents/shared/planning/auto-restart-upgrade/decisions.md`.

**Options.** (a) Hold tools until drain + migration guard land (original ordering); (b) ship tools now, drain-free, with manifest `rollback_safe` as the only rollback-safety gate; (c) ship tools restricted to restart-only until drain lands.

**Decision (rec).** **(b).** `system_upgrade` launches drain-free promotes (bounded SIGTERM stop; in-flight work resumes from node boundaries — the documented restart semantics, `test-strategy.md` §3 note). Rollback safety rides the manifest gate per ADR-007/M5 until `daemon_meta` (future phase). Risk acceptance must be explicit: a restart during in-flight work loses an in-flight tool call (known semantics) — surfaced to Ari as upgrade-time context, not hidden.

**Recommended default:** (b) — matches the user's stated goal of conversational upgrade control now.
**If the user picks (a):** Phase 2 shrinks to pipeline-only (P2.1/P2.3) and ari tooling slips behind two more phases — the initiative's headline capability delays. If (c): restart-only tools still need the full gate machinery; little saved, most of the value deferred.

---

## ADR-021 (rec ⚠ NEEDS USER DECISION): N — clean demo cycles before live eligibility

**Context.** `promotion-ladder.md` S3 requires N consecutive clean demo cycles (objective definition in `test-strategy.md` §4.1) before live promotion becomes USER-GATED-available. N trades wall-clock rehearsal cost against confidence.

**Options.** N = 2 (fast) · N = 3 · N = 5 (heavy).

**Decision (rec).** **N = 3.** Each cycle exercises ≥2 periodic recovery ticks; 3 matches the rollback-cap 3/24h symmetry; ≈3 × (gates 3min + soak 5min + observation) per release. **Default if user silent: 3.** Staleness: any release/manifest change mid-count resets to 0.

**If the user picks otherwise:** N=2 halves rehearsal coverage of the ~10-min ReportDeliveryRecovery lag window (a cycle could pass without ever witnessing one recovery tick); N=5 multiplies drill wall-clock ~×1.7 for marginal signal — both workable, both worse trades.

---

## ADR-022 (rec): `dry_run` default for `system_upgrade`

**Context.** The pipeline has a dry-run precedent (`deploy.sh` DRY_RUN=1 — plans without side effects, verified). An agent-facing upgrade tool with a silent live default is a hazard; a mandatory dry-run flag adds a round trip.

**Options.** (a) `dry_run` default false; (b) default true — first call plans (target, version, rollback safety, gates), user/ari confirms, second call with `dry_run: false` executes; (c) no dry_run parameter.

**Decision (rec).** **(b) default true.** Cheap, matches deploy.sh semantics, and gives the env-target gate a natural rehearsal surface. On demo/sandbox the second call is friction-free; on live it stacks with ADR-017's 3-factor LIVE gate (D-FA3.1).

**If picked otherwise (a):** one hallucinated parameter set executes a real promote — the exact failure class ADR-015's two-factor exists to prevent; (c) removes the rehearsal surface entirely.

---

## ADR-023 (rec): does an `upgrade_status`/progress tool exist? — YES, minimal, journal-derived

**Context.** A long promote (gates + 300s soak ≈ 8+ min) exceeds any sensible tool-call wait; Ari needs a way to answer "how's the upgrade going?" mid-flight. ⟪SEAM: W2 `tool-api-design.md` owns the exact call contract⟫

**Options.** (a) No status tool — `system_upgrade` blocks until terminal state; (b) `system_upgrade` returns a txn id immediately + a separate `upgrade_status(txn_id?)` read-only poller (journal-derived); (c) status folded into `release_info`.

**Decision (rec).** **(b)** — `system_upgrade` returns early with a txn handle; `upgrade_status` polls (default: latest in-flight txn), reporting journal state machine position (staged → flipped → gating → soaking → committed/rolled_back/halted). Third+ tool joins the same category and allow-list resolution (unit-tested, `test-strategy.md` §1 P2.2).

**If picked otherwise (a):** tool calls time out or hold turns for ~10 min (longer than the 5-min pack caps conceptually) and mid-flight questions are unanswerable; (c) overloads a stable read-only tool with mutable-progress semantics.

---

## ADR-024 (rec ⚠ confirm): journal-sweep rollback COUNTS as an auto-rollback (ADR-012) — confirmed carry-over into Phase 2 implementation

**Context.** ADR-012's consequence already states: "the sweep counts as an auto-rollback (cooldown + counters apply)". Phase 2 implements the sweep (filling the `launcher.sh:151-174` stub, verified — the contract comment is embedded there). This entry exists to make the confirmation explicit at implementation time, because a sweep-rollback that bypassed the cap would let a pathological promote-die loop circumvent D2.

**Options.** (a) Sweep rollbacks exempt from the 3/24h cap; (b) counted like any auto-rollback (ADR-012 as written).

**Decision (rec).** **(b) — confirm ADR-012 unchanged.** The sweep's rollbacks increment journal counters, respect the 10-min cooldown, and can themselves trigger halt-for-human.

**If picked otherwise (a):** a promote that dies mid-flip repeatedly would sweep-rollback outside the budget — an unbounded hidden rollback path exactly where flapping protection matters most.

---

## ADR-025 (rec ⚠ NEEDS USER DECISION): alert channel — SSE-only vs +watchdog-watcher extension

**Context.** Abort-class events must reach a human. In-daemon SSE (`NotificationBroadcaster`, `daemon/services/notification_broadcaster.py:19`, verified) works only while the daemon lives — burst abort and daemon-death are precisely when it doesn't. The `scripts/watchdog-watcher.sh` precedent (launchd agent, `/livez` only, 300s interval, absent >10min → notify — verified `watchdog-watcher.sh:5-7,174-177`) covers daemon-down but nothing else. ADR-008 planned a watchdog-watcher doubling as observer (Phase 6).

**Options.** (a) SSE-only this phase; (b) SSE + extend watchdog-watcher to also watch `.launcher-state`/journal halt markers (daemon-down coverage incl. burst abort + cap halt); (c) full observer (Phase 6 scope) pulled forward.

**Decision (rec).** **(b)** — small extension of an existing, plist-shipped script (journal halt/burst markers are files the watcher can read without the daemon). (a) leaves burst-abort and cap-halt-during-death unalertable; (c) imports the LLM-observer phase early for marginal gain.

**If the user picks (a):** the two most severe abort classes (daemon dead, cap-halted) rely on the user noticing via Ari on next recovery — acceptable only if the user accepts silent-halt risk. Note U6 in `promotion-ladder.md` §5: any watcher pointed at live is itself USER-GATED.

---

## ADR-026 (rec): retry policy for failed promotes — manual re-trigger, no auto-retry

**Context.** A promote can fail pre-flip (preflight, checksum, lock) or post-flip (gate → auto-rollback per ADR-005). Auto-retrying pre-flip failures risks hammering a broken pipeline; auto-retrying post-rollback promotes would circumvent the cooldown/cap design.

**Options.** (a) Manual only — ari/operator re-calls `system_upgrade` after inspecting `upgrade_status`; (b) auto-retry with cap (e.g. 2 retries, only for pre-flip transient classes); (c) auto-retry everything.

**Decision (rec).** **(a) manual this phase.** Post-rollback retry is already governed by cooldown+cap; pre-flip failures are cheap to re-trigger conversationally. Revisit (b) only if drills show a noisy transient class (e.g. pg_dump preflight timeouts — ADR-007 timeout+skip already mitigates).

**If picked otherwise (b)/(c):** a new counter dimension (promote retries) must join the journal and the drill matrix, and (c) directly fights D2's anti-flapping intent — not recommended.

---

## ADR-027 (rec): version smoke mechanism — `/livez` version field, not a `--version` flag

**Context.** Promote must verify the daemon that came up is the version staged ("version verify", ADR-005 gate). `/livez` already returns `"version"` in its payload (verified live on demo: `/livez` 200 `{"status":"alive","uptime_seconds":…,"version":"0.10.5"}` — RESULTS file §3 demo probe). A `--version` CLI flag would need frozen-binary plumbing (arg parsing before boot) for zero added guarantee.

**Options.** (a) Assert `/livez` payload `version == manifest.binary_version` post-restart; (b) `ensemble-prod --version` exec; (c) both.

**Decision (rec).** **(a).** The smoke must run against the RUNNING daemon anyway (a correct file that boots wrong must still fail the gate) — the HTTP payload is the only source that proves what's actually serving.

**If picked otherwise (b)/(c):** file-level truth without runtime truth can pass a gate where the symlink points right but the process is stale — exactly the class the smoke exists to catch; (c) adds plumbing cost for redundancy.

---

## ADR-028 (rec): rollback of the ROLLBACK — flip-forward recovery, manual + gated, never automatic

**Context.** If a rollback lands on a `previous` that is itself bad (or was evicted/quarantined), the system rests on last-known-good-or-worse. "Undo the rollback" = flip forward to a fixed version. Automatic forward-flipping would create a promote/rollback oscillation outside D2's accounting.

**Options.** (a) Automatic flip-forward when the rollback target fails its gate; (b) manual flip-forward: halt-for-human, ari relays, user picks the version, promote runs the normal gate; (c) not addressed.

**Decision (rec).** **(b).** The rollback target failing its gate is itself a rollback-class event → counters/cooldown/cap apply; at cap or on second failure → halt-for-human with the release list (versions + `rollback_safe` + quarantine flags from the journal via `release_info`). Recovery = user-chosen version through the standard promote gate. Eviction safety already pins `previous` (ADR-004).

**If picked otherwise (a):** an auto flip-forward loop across versions is flapping with extra steps, ungoverned by D2's cap semantics; (c) leaves the worst-case resting state undocumented.

---

## ADR-029 (minted 2026-08-23, P2.2 Dispatch B): daemonized executor for restart AND promote; exit-74 deferred

**Context.** Both actor tools (`system_restart`, `system_upgrade`) must execute past their own process's death — a promote stops the daemon it runs on, and the tool call in-flight at a stop is lost (verified semantics). Architect fork (D-FA1.3): (A) daemonized external executor vs (B) launcher exit-code 74 "restart-me" extension.

**Decision.** **(A), generalized:** ONE mechanism — `subprocess.Popen(..., start_new_session=True)` (≡ double-fork + `setsid` on macOS/PyInstaller; assumption #2 closed by the Dispatch-B sandbox drill), env-allowlisted (`PATH HOME INSTALL_DIR PORT POSTGRES_DB PG* TMPDIR` — no `.env` passthrough, R-SR09), stdio → `data/upgrade.log`, PID journaled in the pending_op, **never registered in `BashProcessRegistry`** (must survive tool-harness teardown — static-asserted). Payloads: `restart.sh` (new, T7) and `promote.sh`. **Exit-74 deferred** to a future ADR (design preserved: ADR-010 amendment + launcher capability probe + `launcher-not-74-aware` refusal); rationale: R-SR06 ship-ordering, pre-74 bootstrapping window, and the post-turn trigger (ADR-031-adjacent D-FA1.4) removes the waiter race 74 was meant to kill.

**Consequence.** The executor survives daemon SIGKILL mid-gate (T5 drill asserts); orphan accountability rides the journal pending_op (`owner_pid`, `expires_at`) + the ADR-012 sweep backstop; residual env-leak surface bounded by the allowlist. Exit-74 remains a one-line-ish future opt-in.

---

## ADR-030 (minted 2026-08-23, P2.2 Dispatch B): launcher travels in the staged payload (D-FA4.1 amendment record)

**Context.** The launcher enforces the exit-code/burst/journal-sweep contract the releases assume; an old launcher running a new binary's contract is drift (R-SR06).

**Decision.** `launcher.sh` joins the staged release trio (`stage.sh` copies it; the manifest gains `launcher_sha256`; `promote.sh` swaps `INSTALL_DIR/launcher.sh` in the stopped window). Implemented in P2.1; minted here because the upgrade pipeline's correctness (promote-time launcher swap, restart-via-current-launcher) depends on it and the P2.2 executor invokes the swapped launcher.

**Consequence.** Launcher/binary skew self-heals at the next promote; the restart executor (`restart.sh`) re-execs `INSTALL_DIR/launcher.sh` knowing it matches the current release's manifest.

---

## ADR-031 (minted 2026-08-23, P2.2 Dispatch A/B): PRIVILEGED_TOOL_CATEGORIES — the system_upgrade category is opt-in-only

**Context.** `resolve_tool_filter` treats an absent/empty `tools.allow` as "everything potentially allowed" — a permission leak for privileged categories (R-SR16): `watcher` (the only empty-allow agent today) would default-receive restart/upgrade authority.

**Decision.** `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade"})` in `daemon/tools/_tool_registry.py`, consumed by the empty-allow branch + default-allow paths of `daemon/tools/instance.py`: the category NEVER joins the default universe; agents reach it ONLY via an explicit `tools.allow` entry naming the category or one of its tools. Architect-resolved (D-FA2.5/FA6): excluding it regresses nobody and is the desired outcome for `watcher`.

**Consequence.** Default-deny for restart/upgrade authority is structural (no deny rules); ari's explicit `"system_upgrade"` entry (T3) is the only exposure this phase; adding agents later is a deliberate, reviewable one-line trust expansion.

---

## ADR-032 (minted 2026-08-23, P2.2 Dispatch B): USER_ORIGIN_SOURCES whitelist — structural user-origin for the live gate

**Context.** `MessageType.HUMAN` is the else-branch DEFAULT of the source classification (`instance_messaging.py:1310-1319`): `cascade_resume`, `internal_invoke_and_wait:`, `agent:` rows are HUMAN-typed today. "LLM cannot enqueue HUMAN rows" held only by caller discipline — insufficient for the live 3-factor gate (R10/R-SR07). The else-branch mis-typing itself is DEFERRED (separate follow-up defect, wide blast radius — do not fix in this initiative).

**Decision.** Positive source whitelist, frozen from an enumeration of the ACTUAL dispatch-path source formats (assumption #1 closed): exact `"api"` (`routers/messages.py:391` — the web-UI chat path) + the five human-channel prefixes `telegram:` `webhook:` `whatsapp:` `discord:` `slack:` (`sources/registry.py:869` builds `"<source_id>:<user_id>"`; SourceType enum `daemon/models/source.py:17`). NOT whitelisted: `scheduler` (machine), every `internal_*`, `cascade_resume`, `agent:*`. The whitelist gates AT THE TOOL/STAMP SITE — the per-turn user-origin window (`manager._user_origin_windows`, D-FA3.2) is stamped ONLY for whitelisted sources and CLEARED for every other source, so agent-originated turns never inherit stale authorization. Live constant: `daemon/tools/upgrade_journal.USER_ORIGIN_SOURCES`.

**Consequence.** A fabricated `user_confirmed=true` fails factor 2; a self-echoed nonce in an AGENT/internal-origin message fails factors 2+3 — the gate's anti-forgery becomes structural. Fail-closed residual: a source whose configured `source_id` does not start with its type name (free-form pattern `^[a-zA-Z0-9_-]+$`) gets NO marker — rename the source or use the web UI. Single-host trust model otherwise unchanged (D-FA3.4).

---

## Pre-Freeze Assumption-Closure Checklist (added 2026-08-22 per review)

The 4 unverified assumptions of `architecture-recommendation.md` §7, wired to their owning tasks — **this checklist is executed as the FIRST P2.2 task (T0 — assumption closure) before any other P2.2 work; a red item freezes the dependent design, it does not proceed on hope.** Fail-closed direction throughout: a wrong assumption refuses, never bypasses.

| # | Assumption | Owner task | Verification step (objective) | Status |
|---|-----------|-----------|-------------------------------|--------|
| 1 | `USER_ORIGIN_SOURCES` prefix strings — exact external-channel source prefixes (`telegram:`/`discord:`/`slack:` forms) | P2.2 T8 (gate implementation) | Enumerate the ACTUAL adapter source prefixes from the `daemon/sources/` dispatch paths; verify real formats against `instance_messaging.py:1781` docstring (`"telegram:user:1"`) and `routers/messages.py:391` (stamps `"api"`); unit asserts: every whitelisted prefix stamps the origin marker, every `internal_*` source does not | CLOSED 2026-08-23 (Dispatch B) — frozen in `daemon/tools/upgrade_journal.py` (ADR-032); enumeration = api + telegram:/webhook:/whatsapp:/discord:/slack: |
| 2 | Executor daemonization primitive — `subprocess.Popen(..., start_new_session=True)` ≡ double-fork+setsid on macOS/PyInstaller; no `BashProcessRegistry`-adjacent teardown reaches it | P2.2 T5 (daemonized launcher) | Sandbox run: child survives (a) tool-harness teardown and (b) daemon SIGKILL; static assertion the child is NOT registered in `BashProcessRegistry` (grep/static check in the unit pack) | CLOSED 2026-08-23 (Dispatch B sandbox drill — see the Dispatch-B report: child survived harness teardown + parent SIGKILL; grep clean) |
| 3 | `watchdog-watcher.sh` extension surface — can read `.launcher-state` + `releases/state.json` (file-format stability) | P2.3 T8 (alerting wiring) — **HARD dependency on P2.1 T4: `releases/state.json` does not exist until the pipeline writes it** | Sandbox test: the watcher extension detects a halt/burst scenario from the real `.launcher-state` + journal files alone (no daemon) | open → close at T8 (P2.3) |
| 4 | e2e-gate additivity — the post-graph trigger consumer stays OUTSIDE the graph | P2.2 PR review (per-PR, at merge time) | PR-time diff-based confirmation against the `ensure.md:44-53` trigger paths (`claim_pending_task`, `turn_transitions`, `reconcile_turn_mirror`, `job_processor`, `job_locks`): if the diff touches any of them, the FULL e2e release gate runs (`test-strategy.md` §2); the design intent is additive-only so the gate should NOT trigger | open → close per PR |

**ADR minting note (per review edit #1):** ADR-029 (daemonized executor / exit-74 deferred), ADR-030 (launcher in staged payload), ADR-031 (`PRIVILEGED_TOOL_CATEGORIES`), ADR-032 (`USER_ORIGIN_SOURCES` whitelist) are **to be minted here by the implementer** — renumbered +1 from the architect's original 028…031 proposal (collision with the minted ADR-028). **Numbering check is part of the minting task: confirm the current max ADR number in this file and mint sequentially above it — never reuse a minted number.**

---

## User Rulings — 2026-08-22 (P2.1 implementation dispatch, developer[v2])

- **ADR-009 D3 "fetch" descope — ACCEPTED (user-ruled):** version resolution uses the LOCAL checkout only; NO network fetch of release artifacts anywhere in the release path.
- **ADR-017 3-factor LIVE gate — RATIFIED as planned (user-ruled):** env-target model as recommended — demo/dev/sandbox free (user directive), live = enforced 3-factor runtime gate (D-FA3.1: user_confirmed param + HUMAN-origin marker + action-binding nonce), env derivation fail-closed from the staged ENSEMBLE_SELF_ENV marker (PORT-derivation fallback stays rejected).
- **ADR-021 — N=3 confirmed (user-ruled):** three clean demo cycles gate live eligibility.
- **ADR-025 — ACCEPTED with the daemon-down alerting gap explicitly documented (user-ruled):** option (b) SSE + watchdog-watcher extension is accepted. Known accepted gap: until the P2.3 T8 watcher extension lands, daemon-down abort classes (burst abort, cap-halt-during-death) are NOT alerted — the daemon being down is precisely when SSE cannot deliver. This gap is accepted for the current window and closes in P2.3.
- **ADR-016…020 and ADR-024 deviations — ACCEPTED as reviewed (user-ruled).** (ADR-016 third tool system_restart; ADR-018 scripts-not-make chassis; ADR-019 ari-only exposure; ADR-020 tools before drain/migration-guard with manifest rollback_safe interim gate; ADR-024 sweep-rollback counts toward 3/24h cap.)

---

## Standing Rulings — P2.1 final fix cycle (2026-08-23, reviewer-directed)

- **ADR-033 · Halt-semantics ruling (deviation #3 — standing):** halt paths boot-and-continue on the degraded current BY DESIGN. Under launchd KeepAlive, blocking boot = crash-loop → burst-abort → dark env, strictly worse than degraded-but-known serving. The correct posture is: serve degraded current + journal `halt` events + notify; the dangerous direction is already guarded by the rollback_safe gate (and the M4 quarantine gate). Confirmed across all four rollback paths (promote auto-rollback, adopt_stale_txn, launcher sweep, manual rollback refusal).
- **ADR-034 · Splice escape-discipline note (for P2.2 authors):** the Python journal module that P2.2 introduces MUST preserve the journal splice's escape discipline — a field divergence requires ≥2 occurrences and is only synthesizable by hand-editing; a single-occurrence assert is logged as P2.3 hardening (do NOT tighten the splice to single-occurrence semantics; the ≥2 discipline is the deliberate safety margin against false-positive torn writes).

## P2.2 Fix Pass (2026-08-23) — reviewer MAJORs + minors; deviations & ledger

**Context.** Independent reviewer REJECTED (narrow) on 2 unanimous MAJORs in the 3-factor LIVE gate: (MAJOR-1) `job_create` accepted a caller-supplied `source` verbatim for agent callers (the agent-override only fired on the exact default `"api"`), forging factor 2 with zero human involvement; (MAJOR-2) the nonce was not action-bound to `version` — an armed call naming a different version than the dry_run that minted the nonce passed all 3 factors. Both fixed in the gate + at the dispatch seam; live-safety (previously verified HOLDING twice) untouched.

- **Gate hardening landed (code):** `job_create` forces `source=agent:<caller>` whenever `caller_agent_id` is set (mirrors `job_continue`); the gate enforces `issued_to_instance == current_instance_id` (new token `nonce-instance-mismatch` — also closes reviewer N2: the field was recorded at mint but never checked) and `(kind, env, target)` action-binding vs the armed call (new token `nonce-action-mismatch`, §4.2(b)/§4.3); unparseable `ttl_expires_at` fails CLOSED (M1); a non-silent dispatch with `message_source=None` now CLEARS the user-origin window via the real stamp site (M2 — stamp already treats None as non-whitelisted/clearing); `_json_escape` escapes ALL control chars < 0x20 as `\u00XX` (N4 — a raw control char inside a JSON string is invalid for every strict reader; an LLM-controlled `--reason` could TORN the journal); `restart.sh` requires an explicit target arg or `TARGET` env, exit 78 otherwise (M6 — the silent `${TARGET:-demo}` default aimed no-arg calls at the REAL demo install); tests HOME-isolated (M3 — the last real-home/live-path read removed from the suite).
- **⚠ DEVIATION (M4, recorded per fix-pass instruction): live reads gate on SELF-MATCH ONLY.** `promotion-ladder.md` §5 U4 specifies self-match **+ explicit user approval per live-read request**; the implemented read tools (`release_info`, `upgrade_status`) enforce the self-match rule (a non-live-resident Ari is refused `env-self-match` outright; marker absent → reads fail-open per ADR-017) but do **NOT** implement the per-request approval step. **U4 implementation = P2.3 ladder scope.** Residual accepted this phase: a LIVE-resident Ari can read its own live observability (journal/releases/changelog state) without a per-request human approval — read-only, own-env only, no pipeline mutation. Noted in `promotion-ladder.md` U4 row.
- **Ledger carry-overs (documented, deliberately NOT implemented in this pass):**
  - **N1** — nonce refusal tokens could carry structured sub-tokens for telemetry; current flat strings stand.
  - **N5** — armed-call output says "UPGRADE SCHEDULED"; "ARMED/DEFERRED" wording is clearer; cosmetic, deferred.
  - **N6** — default-version pick uses lexical sort (`sorted()[-1]`), so 1.2.9 beats 1.2.10; explicit `version=` unaffected; deferred.
  - **OBS residual (§4.2(a))** — single-host trust model: an agent WITH bash can POST to the unauthenticated local API and forge an `api`-source dispatch (the whitelist cannot distinguish a genuine web-UI human from a localhost forger). Documented residual of D-FA3.4 single-host model; closes only with an auth boundary on the local API (out of P2.2 scope).
  - **T7–T9 e2e drills** — P2.3 DR-5 scope (see RESULTS/2026-08-23-p2-2-daemonized-executor-survival.md scope note).

## P2.2 Tidy Fix cycle-3 (2026-08-23, review-council-p2p2-tidy) — MAJOR-1 fixed; nits → P2.3 carry-over

**Context.** Reviewer cycle-3 on b9bee5cd (`refactor(p2.2): tidier fix-now`): ONE MAJOR only, everything else in b9bee5cd confirmed. **MAJOR-1**: the item-A twin-helper reroute was NOT behavior-neutral — the deleted local `_lock_run_id` accepted `Path | None` (explicit None → None guard); the journal original `journal_lock_run_id` takes `Path` only, so `upgrade_status` on dev (`install_dir=None`) returned `Error: upgrade_status failed: TypeError…` where the parent commit rendered the honest `journal: none (no staged install dir…)`. Missed by the 134-pack + 835-suite: no test exercised `upgrade_status` with `install_dir=None`. **Fixed journal-side (Option A, dispatcher-chosen)**: `upgrade_journal.lock_run_id` widened to `Path | None` + None guard — mirrors the conventionally None-hardened read helpers in upgrade_tools.py AND hardens the journal-side actor call sites (~:1393, ~:1706) which were only refusal-guarded; `upgrade_tools.py:1143` untouched. Regression: `test_no_install_dir_reads_render_honestly` (both read tools, mutation-lethal).

- **P2.3 carry-over ledger (reviewer cycle-3 nits, deliberately NOT implemented in this pass):**
  - **NIT-1** — cooldown conjunct domain pin; deferred.
  - **NIT-2** — unwind-after-lock-release ordering swap; deferred.
  - **NIT-3** — `restart.sh` help wants a `====` closer (banner-parity); cosmetic, deferred.
  - **NIT-4** — `_launcher_state_values` raw render in one display path; cosmetic polish, deferred.
  - **NIT-5** — **refactor rule**: twin-helper equivalence = signature + guards + call-site reachability, NOT body similarity; one behavior-diff assertion per deleted helper.

---

## P2.3 Gate Rulings & Fences (2026-08-23, developer[v2] gate dispatch)

1. **U4 live-read per-request approval — FENCED.** "Assigned to P2.3 by promotion-ladder.md §5 U4 note, but omitted from phase3-plan.md (plan of record); fenced to the dedicated loopback-API-auth/F2 follow-up workstream — rationale: intertwined with F2 auth boundary; no live-resident Ari exists in P2.3 scope."
2. **Tidier P2.3 refactor batch — FENCED (user override 2026-08-23).** The tidier LEDGER set (actor-tool dedup, 330-line system_upgrade split, module split, type hardening, journal diagnosability) is fenced to a dedicated post-P2.3 refactor pass, overriding the tidier notes.md "P2.3 refactor batch" label. Rationale: refactoring code under drill before drilling invalidates drill evidence; the FIX-NOW subset already landed (b9bee5cd). Exception: an individual sub-item may be pulled into a P2.3 batch ONLY if a P2.3 feature directly requires it — minimal, justified in the batch report.
3. **MINOR-A/B — `[inferred, verbatim lost]`.** Minted by review-council-p2p2-fix (cycle 2, verifying 0949dd51). Verbatim text exists only in live-environment session data — unreachable by agents per the hard constraint (wanderer hunt 2026-08-23 confirmed; it correctly did not reach live). Best-evidence reconstruction, concordant across 3 independent non-live sources — (a) critical-note positional order, (b) decisions.md:245-257 documenting both fixes, (c) tidier notes.md:27 label set: MINOR-A ≈ M2 `source=None` seam test (fix landed, seam test missing); MINOR-B ≈ `job_create` source forcing fires only when caller_agent_id set → make unconditional. Disposition: IN — subsumed by the P2.3 B3.5 burn-down batch (both are its first two items). Optional: a human operator MAY recover verbatim text from the live ensemble UI (user-executed, allowed) to close the ledger reference definitively — nothing in P2.3 blocks on it.
4. **ADR-021 cycle-ledger interpretation — ACCEPTED (user-ruled 2026-08-23).** `ledger_check.py` semantics confirmed as the faithful §4.1 reading: a VIOLATION breaks the trailing consecutive-clean streak but never erases history; reset-to-zero is reserved for staleness (version change) only. Eligibility requires 3 NEW consecutive clean cycles after any violation, evidence intact. If ADR-021 later rules otherwise on reset-on-fix, the change is localized to `classify()` in `scripts/upgrade/ledger_check.py`.
5. **F-DR1-2 split-brain PG resolution — FENCED post-P2.3 (user-ruled 2026-08-23).** `POSTGRES_URL` is honored by the persistence.py checkpointer chain but ignored by `daemon/repositories/factory.py:189-193` (parts-only resolution) — both resolutions observed in one boot during DR-1. Latent, not blocking (DR-2/DR-3 passed on demo). Fenced to a post-P2.3 batch (DB-resolution core = highest blast radius); blueprint-KB correction (Dev-DB note wrongly cites factory.py in the POSTGRES_URL chain) recorded by the user at workstream close.
6. **B4 "refusal live end-to-end" — CLARIFIED (F-B6c-1, 2026-08-23).** The B4 claim was pack-level truth (t8a–t8d: real lib.sh refusal append → real classifier → alert) ≠ runtime truth: the in-daemon tool-refusal lane never journaled (upgrade_tools.py had zero journal_history_append calls — _refusal() was a pure formatter), so no runtime alert fired. B6.5 closes the lane: the append now rides _refusal() itself (best-effort, single write point). Shell lane remains file-write-only by design (structurally bypasses the in-process sink; watcher/relay territory).
7. **FL-23 frozen-install scripts provisioning — design (a) SPECIFIED, implementation FENCED pre-live (user-ruled 2026-08-23).** §8 now specifies stage.sh bundling of `scripts/upgrade/` into the release (self-contained; resolution: env override → release-local → repo-dev-only). Implementation is a PRE-LIVE checklist item with a stricter gate: bundling lands + one script-driven promote validates + 3 fresh ari-driven cycles on the bundled release before live eligibility (staleness rule: a mid-phase mechanism promote would reset the banked ledger — cycles #1–#3 ran on the disclosed env-var mechanism, bit-exact restored, for evidence consistency).

P2.3 final-batch deferrals (tidier 6M+14L batch, 2026-08-24 — single-line ledger):
- (a) Rank-tuple defensive copy at `_version_sort_key` call sites — deferred (tidier B; tuples are immutable, the copy would be ceremonial).
- (b) Untyped `counters` dict pass-through in the alert payload (`data.get("rollback_window_count")`) — accepted as-is (tidier C; journal-derived display data, no consumer types on it).
- (c) Lifespan-ordering watch item: the notification broadcaster is created + wired onto the manager BEFORE `_register_upgrade_alert_sink` runs (api.py boot order verified safe 2026-08-24); keep that order — a sink registered before its broadcaster exists would capture a dead reference.

---

## Decision Index (Phase 2)

| ADR | Topic | Recommendation (one line) | Flag |
|---|---|---|---|
| 016 | `system_restart` tool | Third tool, same category; health-gated SIGTERM path, never raw kill | user-ruled ACCEPTED 2026-08-22 |
| 017 | Env-target gate | demo/dev/sandbox FREE (user directive); live = enforced **3-factor** runtime gate (D-FA3.1: param + HUMAN-origin marker + nonce); derivation = fail-closed `ENSEMBLE_SELF_ENV` marker (D-FA2.3; PORT fallback rejected) | user-ruled ACCEPTED 2026-08-22 |
| 018 | Pipeline chassis | Env-parameterized `scripts/` stage/promote/rollback; make framing of ADR-009/015 superseded | user-ruled ACCEPTED 2026-08-22 |
| 019 | Tool exposure | ari-only this phase; jober deferred (delta from ADR-015) | user-ruled ACCEPTED 2026-08-22 |
| 020 | Phase ordering | Tools before drain/migration-guard; manifest `rollback_safe` interim gate (M3/M5 sanctions) | user-ruled ACCEPTED 2026-08-22 |
| 021 | N clean cycles | N = 3, objective cycle definition, staleness reset on release change | user-ruled ACCEPTED 2026-08-22 |
| 022 | dry_run default | `system_upgrade` defaults to dry_run=true | rec |
| 023 | Status tool | `upgrade_status` exists — early-return txn handle + journal-derived poller | rec |
| 024 | Sweep counts as rollback | Confirm ADR-012: launcher journal-sweep rollback feeds 3/24h counters + cooldown | user-ruled ACCEPTED 2026-08-22 |
| 025 | Alert channel | SSE + watchdog-watcher extension (journal/`.launcher-state` markers) | user-ruled ACCEPTED 2026-08-22 (daemon-down gap documented) |
| 026 | Promote retry policy | Manual re-trigger only this phase | rec |
| 027 | Version smoke | `/livez` payload `version` == manifest `binary_version` (runtime truth) | rec |
| 028 | Rollback-of-rollback | Manual, gated flip-forward; halt-for-human + user-chosen version | rec |
| 029 | Executor seam | Daemonized executor (start_new_session) for restart + promote; exit-74 deferred; not in BashProcessRegistry | minted 2026-08-23 (P2.2 Dispatch B) |
| 030 | Launcher in staged payload | launcher.sh travels with the release; swapped in the stopped window (D-FA4.1) | minted 2026-08-23 (P2.2 Dispatch B; implemented in P2.1) |
| 031 | Privileged categories | PRIVILEGED_TOOL_CATEGORIES={system_upgrade} excluded from the empty-allow universe — opt-in only (R-SR16) | minted 2026-08-23 (P2.2; implemented in Dispatch A) |
| 032 | USER_ORIGIN_SOURCES | Whitelist {api + telegram:/webhook:/whatsapp:/discord:/slack:} gates the per-turn user-origin window; everything else (internal_*, scheduler, agent:*) clears it — structural anti-forgery for the live gate | minted 2026-08-23 (P2.2 Dispatch B) |
| — (ruling) | Cooldown × sweep (D-FA4.2 adjudication) | Cooldown arms the next **ENTRY** only (promotes refused inside it); the ADR-012 sweep / an in-flight rollback **NEVER refuses on cap or cooldown** — refusing the recovery strands the env on an orphaned flip; rollback-cap 3/24h is entry-side enforcement | adjudicated (architecture-recommendation.md D-FA4.2) |
| 033 | Halt semantics on degraded current (deviation #3) | Halt paths boot-and-continue BY DESIGN; serve degraded current + journal `halt` events + notify (KeepAlive crash-loop → burst-abort → dark env strictly worse); dangerous direction guarded by rollback_safe + M4 quarantine gates; confirmed across all four rollback paths (promote auto-rollback, adopt_stale_txn, launcher sweep, manual rollback refusal) | standing ruling 2026-08-23 |
| 034 | Splice escape discipline (P2.2 journal module) | Python journal module MUST preserve ≥2-occurrence + hand-edit-only synthesis for field divergence; single-occurrence assert logged as P2.3 hardening — do NOT tighten to single-occurrence semantics (≥2 is the deliberate safety margin against false-positive torn writes) | standing ruling 2026-08-23 |

## Needs-User-Decision-at-Review Checklist

| # | Item | Where | Default if silent |
|---|---|---|---|
| 1 | **N (clean demo cycles)** — accept 3? | ADR-021 · `test-strategy.md` §4 · `promotion-ladder.md` S3 | 3 — **Resolved (2026-08-22 user ruling — see addendum)** |
| 2 | **Alert channel** — SSE-only, or extend watchdog-watcher for daemon-down abort classes? | ADR-025 · `promotion-ladder.md` §3 | SSE + watcher extension — **Resolved (2026-08-22 user ruling — see addendum)** |
| 3 | **ADR-016…020 deviating from APPROVED ADR-015/009** — approve each deviation (new tool, env-target gate, scripts-not-make, ari-only, early tool phase)? | ADR-016…020 | All five as recommended — **Resolved (2026-08-22 user ruling — see addendum)** |
| 4 | **ADR-024 explicit confirmation** — sweep-rollback counts toward 3/24h cap | ADR-024 | Counted (ADR-012 as written) — **Resolved (2026-08-22 user ruling — see addendum)** |
| 5 | **Live-stage runbooks (U1–U6)** — confirm the USER-GATED design (user-executed/approved, never agent-executed) matches intent | `promotion-ladder.md` §5 | As tabled |
