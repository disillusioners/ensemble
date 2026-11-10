# Decision Module P1 (SHADOW-ONLY) — Functional Gate Report

- **Date**: 2026-11-09
- **Worktree**: `/home/nea/ensemble-src-wt-decision-module-p1`
- **Branch / HEAD verified**: `feature/decision-module-p1` @ `6c2985a1cd7d09f211187726421237bd2ca07575` (= `6c2985a1c`)
- **Base**: `818411107` (proven ancestor via `git merge-base --is-ancestor`)
- **Gate tier**: Tier 2 (the diff touches `daemon/manager.py`, a core seam on ensure.md's mechanical path-prefix list) → whole-tree dual-side base-proven leg mandatory, in addition to the leader's enumerated scoped leg
- **Test leader**: Tester · 10 dispatched workers + 1 follow-up RCA worker · **0 commits, 0 pushes, 0 production edits** by the gate
- **Verdict**: **NO-GO** — 4 blocking items (1 HIGH production, 2 MAJOR lane-caused reds, 1 test-integrity tautology). All six behavioural invariants are proven; the blockers are small and mechanical.

---

## 1. VERDICT

**NO-GO** on the tree as it stands.

| # | Blocking item | Sev | Where |
|---|---|---|---|
| **B-1** | Exception **message + args** logged on the consult path — breaches the ratified "exception TYPE only" invariant; 10 sites in one family | 🔴 HIGH | `daemon/services/decision/client.py:263-267` (+9 siblings) |
| **B-2** | New **hard** `Config.decision` dependency in `InstanceManager.__init__` breaks `MagicMock(spec=Config)` fixtures — 2 measured reds + **21 derived latent reds** | 🟠 MAJOR | `daemon/manager.py:2880` |
| **B-3** | The lane's `daemon/manager.py` delta lands inside a pre-existing guard's forbidden span → deterministic whole-tree red | 🟠 MAJOR | `tests/unit/repositories/test_source_gc_repository.py:713-722` vs `daemon/manager.py:2872-2891` |
| **B-4** | The daemon-diff pin's assertion is **tautological** (`LANE_OWNED ⊆ ALLOWED` by derivation ⇒ `lane_offenders` always ∅) | 🟠 important | `tests/unit/metrics/test_metrics_family_adjudication.py:276-292` |

All six load-bearing invariants of the lane were independently proven by execution:

| # | Invariant | Verdict | Anchor |
|---|---|---|---|
| 1 | Nothing decides anything | **PROVEN** | apply has exactly one code site (`registry.py:531`), reachable only via `allow_apply` (`overlay.py:553-557`) → `is_deciding_permitted` (`registry.py:388-400`) = stage DECIDING **and** `seam_id in self._ratified`; `_ratified` written at one site inside `transition_stage`; the only production consult entry is `consult_detached` → `force_shadow=True`. The instrumented apply **never fired** on the shadow path and **did** fire under a ratified+inline control (both directions proven) |
| 2 | Byte-identical to today | **PROVEN** | S1 unconfigured → `['a','c']`, **0** socket calls · S2 configured+seam OFF → `['a','c']`, **0** calls · S3 configured+ON+shadow (transport answers `['b']`) → selection still `['a','c']`, **1** call, 1 A/B row `verdict=disagree`, answer **not applied**. Socket counter 0→0→1 proves the zeroes are live |
| 3 | Content-free shadow row | **PROVEN for the row / BREACHED for a log line** | row, metric labels (all in the §14 vocabulary) and board payloads clean under the full hostile set; **but** `client.py:263-267` logs the exception message+args ⇒ B-1 |
| 4 | Fail-closed everywhere | **PROVEN** | unreadable ⇒ OFF + `switch_unreadable` row, 0 calls (real store class over a failing engine); readable-ON ⇒ ON; kill-write mismatch ⇒ seam poisoned, cleared only by explicit `invalidate()`/`reverify()`; global-master poison is **consulted** and suppresses a different seam |
| 5 | The ceiling binds | **PROVEN** | 200-no-usage ⇒ **0.0005** billed · 200-unknown-usage-keys ⇒ **0.0005** · timeout ⇒ **0.0005** · connect-phase ⇒ **0.0** · concurrent shadows ⇒ 2 rows + one ledger total `+0.001`; trip writes per-seam keys **and** the master, then notifies (removing either reddens the pin) |
| 6 | ~0 critical-path latency | **PROVEN** | detached median **5.7 ms** with a 2 s transport sleep, **4.7 ms** with an 8 s sleep (invariant to sleep length); inline positive control **2004 ms / 8010 ms**; 30/30 consults reached the socket, 30/30 rows landed |

**Why NO-GO rather than GO-WITH-DISCLOSED-LIMITATIONS:** B-1 breaches a **ratified** invariant of the design of record (`decisions.md` §5 item 16(7)); B-2 and B-3 are **lane-caused reds in the whole-tree gate** (3 measured, deterministic, not disputed by flake evidence) — a lane whose own regression leg is red cannot be green-gated. None of the four is a design failure; all are small and mechanically fixable.

**Re-gate cost after the fixes is small**: B-1 sweep + B-2 fixture/guard resolution + B-4 pin rewrite, then re-run G7 (≈5 min), the decision pack (202 nodes, ≈7 s) and a **base-proven re-run of the 3 net-new ids** (plus the 21 derived) — not a full 35k-node re-sweep unless the fixtures change broadly.

---

## 2. DEFECT REGISTER

| ID | Sev | Location | Defect | Evidence | Disposition |
|---|---|---|---|---|---|
| **B-1** | 🔴 HIGH | `daemon/services/decision/client.py:263-267` | `logger.debug("decision client internal fault (non-fatal): %s: %s", type(exc).__name__, exc)` logs the exception **message and args**, not type only. Under a transport fault whose message/args echo request content, the secret + full prompt text land **verbatim in a daemon log line** on the consult path | G7 H8 quoted verbatim; contradicts `decisions.md` §5 item 16(7) | **BLOCKER** — log `type(exc).__name__` only |
| **B-1a** | 🟠 HIGH (same family) | `overlay.py:501-507`, `:874-879`, `:979-984`, `:1046-1051`, `:1148-1152`; `registry.py:443-449`, `:465-473`, `:524-531`; `shadow.py:175-179` | 9 sibling sites log `exc` beside the type | G7 family enumeration; corroborated by G1/G2 (the detached wrapper is TYPE-only at `overlay.py:807-812`; the deeper fences are not) | **BLOCKER** — same one-line fix each |
| **B-2** | 🟠 MAJOR | `daemon/manager.py:2880` (`house_decision_config=self.config.decision,`) | A new **hard** attribute dependency in `InstanceManager.__init__` that every `MagicMock(spec=Config)` fixture must satisfy. `decision` is a pydantic-v2 `model_fields` entry, so `MagicMock(spec=Config)` does **not** expose it (`'decision' in dir(Config)` → False; `in Config.model_fields` → True) — exactly the hazard the fixtures' own comments document for `blueprint`/`slash_commands`/`service_tool` | Whole-tree: 2 hard reds (N2/N3) + **21 latent** reds masked behind the pre-existing `service_tool` gap. Fixture sites: `tests/unit/test_webfetch_builtin.py:656`, `tests/unit/test_builtin_mcp_servers.py:368`, `tests/unit/test_context7_builtin.py:366`, and the shared helper `tests/conftest.py:~500-501` (`config = MagicMock(spec=Config)`, **no `decision` line anywhere in `tests/conftest.py`**) | **BLOCKER** — test-side (3 fixtures + 1 shared helper) **or** production-side (`getattr(self.config, "decision", None)` with a documented fail-soft). Leader's call |
| **B-3** | 🟠 MAJOR | `tests/unit/repositories/test_source_gc_repository.py:713-722` vs `daemon/manager.py:2872-2891` | The pre-existing guard `TestP11BootNoBehaviorChange::test_post_merge_span_exactly_sanctioned_hunk` asserts the lane-span diff over `NOT_TOUCHED_PATHS` is **empty**, and `daemon/manager.py` is on that list (`:657-664`). The lane's +44 `manager.py` delta therefore lands inside the guard's span by construction (the guard's span starts at the last first-parent merge = `818411107` = the lane's own base) | HEAD=FFF / BASE=PPP, 3/3 each side, deterministic; no `QUARANTINE.md` row covers the id or the file; the two sibling guards in the same class pass | **BLOCKER** — guard-vs-lane contract collision, not a `manager.py` code bug: widen the sanctioned-hunk list / drop `manager.py` from `NOT_TOUCHED_PATHS` for this span, or land the wiring through a sanctioned path |
| **B-4** | 🟠 important | `tests/unit/metrics/test_metrics_family_adjudication.py:276-292` | The daemon-diff pin's assertion is **tautological**: `ALLOWED_DAEMON_DIFF` is derived as `ALLOWED += tuple(sorted(LANE_OWNED_DAEMON))`, so `LANE_OWNED ⊆ ALLOWED` ⇒ `lane_offenders = offenders ∩ LANE_OWNED` is always ∅ ⇒ `assert lane_offenders == []` **can never fail**. Measured escape class: a path in **neither** list ⇒ GREEN | Pin-hole probe (3 synthetic offender sets); the RED branch is reachable only under simulated derivation drift | Enumerate the 78 pre-existing offenders **by name** and assert `set(offenders) - PRE_EXISTING_OFFENDERS_BY_NAME == set()`; refresh becomes a deliberate reviewable diff. No live escape among the lane's 16 owned files (16/16 in the diff, 0 orphans) |
| **D-3** | 🟠 important | `decisions.md:125` (item 16 point 6); mirrored at `overlay.py:743`, `snapshot_search_service.py:701` | The disclosure states a kill written "between dispatch and execution may not be honoured". Measured: a kill written in the dispatch→first-step window **IS** honoured (suppressed pre-socket, 0 billed). The non-honour window starts **after** the consult's inner gate read (`overlay.py:488`) | G4 leg (e), both write paths | Reword: *"a kill written after the consult's gate read — i.e. once the consult is in flight — may not be honoured for that one consult; bounded to one."* |
| **D-4** | 🟠 important | `daemon/services/decision/cost_store.py:70` (lock), `:85-88`/`:185-211` (`current_totals`) | The ledger RMW lock is **per-object** while the ledger row is **per-key**; safe today (one accumulator per process: `factory.py:128` → `manager.py:2878` → `api.py:707`, no uvicorn `workers=`) but breaks with a second overlay/instance or `workers>1` ⇒ the ceiling can undercount. Separately, `current_totals()` takes **no lock** ⇒ an unsynchronised reader can observe a pre-write value (moot today: the ceiling consumes `record()`'s return) | Ledger RCA + barrier-injection mechanism proof | Disclose for P2/P4; consider a key-scoped (module-level) lock |
| **D-5** | 🟠 important | `tests/unit/decision/test_council_blockers.py:405-428` + `tests/unit/decision/conftest.py:16` | The ledger pin's discriminating power depends on the db-offload pool having ≥2 workers; with `max_workers=1` the mutant is **0/10 red** ⇒ the pin is silently vacuous under a pool change. It relies on a conftest constant that **does not name that intent** | Ledger RCA mutant-signature matrix | Set the pool **inside the test** + assert ≥2 distinct offload threads reached the RMW; add a barrier-forced overlap leg (timing-free) |
| **D-6** | 🟡 low | `daemon/services/decision/shadow.py:77-89`, `types.py:524-533` | `sanitize_token("")` returns `sha256:e3b0c442…` (hash of empty), so every success/suppression row renders `failure_class=sha256:e3b0c44298fc1c14` instead of an empty field — "no failure class" is indistinguishable from a token for a grep. The same predicate admits C0 controls / RTL marks in a validated id | G4 finding 2; G7 minor | Observability only |
| **D-7** | 🟡 low | `daemon/services/decision/overlay.py:658, 688, 697, 704` vs `port.py:63` | `OverlayConsultResult.cost_usd` reports the **client-reported** cost (measured `0.0` where the ledger billed `0.0005`) while the port documents it as "the billed cost of the attempt (0 when no call was made)" | G5 addendum | Contract mismatch; harmless in P1, misleading for P4 |
| **D-8** | 🟡 low (coverage gap) | `daemon/services/decision/overlay.py:859` | No **shipped store-level** pin for the 200-missing-usage never-zero; transitively protected by the timeout pin, but the direct durable-store assertion existed only in a /tmp probe | G5 addendum | Promote the probe into the pack |
| **D-9** | 🟡 low | `daemon/services/decision/client.py:747-751` + `overlay.py:859` | A usage block with tokens but no price and no operator rate accrues **$0.00 USD**; the token ceiling is then the only binding protection | G5 addendum, measured `delta_usd=0.0, delta_tokens=500` | Not a §11 violation (usage present; degradation visible) — disclose |
| **D-10** | 🟡 low | `daemon/services/decision/overlay.py:861` | The floor's comment says "carried NO usage at all" while the predicate is `tokens <= 0` (a present-but-unrecognised block also bills) | RECON addendum | Doc-vs-code; safe direction (over-bills) |
| **D-11** | 🟡 minor | — | **No registered pack** for `tests/unit/decision/` in `.agents/tester/PACKS.md`; no wrapper in `test/packs/` | RECON + G6a | Register a pack (tester-side, post-merge) |
| **D-12** | 🟡 minor | `.agents/tester/PACKS.md:968` | Adjudication pack annotated **26** nodes vs census **27**; its own arithmetic (24+2+1) says 27 | G6a | Fold-e violation |
| **D-13** | 🟡 minor | `daemon/services/decision/kill.py` (`boot_seed`) | `boot_seed` has **no production call site** at this HEAD — dormant API. Restart survival rests entirely on `reverify` | G8 leg 3 grep | Disclose or wire |
| **D-14** | 🟡 minor | `decisions.md` §15 | Claim denominators do not reconcile: `117 raw` (current raw from `LANE_BASE 644c2bfaa` = **1447**); `metrics/` **527 collected** vs claimed 538 (532P/5F/1E); the "10 new producer files" is **12**; the lane is **27 commits**, not 28 | G6a + RECON | Claim hygiene (the other four §15 numbers — 105/78/0/27 — reconcile exactly) |
| **D-15** | 🟡 minor | lane labels | "D3" is **overloaded** (design-doc D3 = shadow-log/transition gate; council-blocker D3 = timeout billing floor) — a bare grep matches 4+ unrelated nodes | RECON | Disambiguate labels |
| **D-16** | 🟡 minor | lane artifacts | The "11 mutation kills" claim is **written nowhere** in the repo (27 commit bodies, 7 planning files, test provenance, RESULTS/EVIDENCE searched). 5 explicit claims exist in `decisions.md`; the other 6 are 10 `Discriminating:` docstrings in `test_council_blockers.py` + the §14 band pin. **G5 independently constructed and killed all 11 + 1 bonus**, so the substance is verified — only the provenance is missing | RECON addendum + G5 | Claim hygiene |
| **D-17** | 🟡 minor (gate infra) | `.agents/tester/skills/whole-tree-base-proven-diff/` §3 fence | The recipe's LLM fence does **not** scrub the `ENSEMBLE_AUTH_*` family. From a daemon-child shell, **28** `tests/api/test_editor_settings.py` nodes red on `[AuthAudit] event=request_denied_302`; scrubbed → 37 passed. Any whole-tree leg launched from a daemon-child shell without this scrub reports ~28 false reds | G6b D4 | **Recipe patch (one line, symmetric both legs):** add `-u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` to the §3 `env -u` block. Not applied by this gate (verification-only) |
| **D-18** | 🟡 minor (gate infra) | `sweep_digest_verdict.sh` | `pytest -q` emits `FAILED <nodeid> - <message>` and parametrized ids legitimately contain `" - "`, so prefix-stripping alone truncates ids. The 12 residual `NON-NODE` lines are cap-log records (`ERROR␣␣␣␣<logger>:<file>`) | G6b §6 | Resolve raw lines by **longest-prefix match against the collected node-id set**; report residual NON-NODE lines separately |
| **D-19** | 🟡 minor | `QUARANTINE.md:65` | The `InstanceManager blueprint-fixture family` row is **stale**: its recorded signature (`Mock object has no attribute 'blueprint'`) no longer fires (fixtures repaired), and the same two tests now red at BASE on `service_tool` and at HEAD on `decision` | G6b §8 | Quarantine ledger hygiene (not edited by this gate) |

---

## 3. PER-GATE RESULTS

### G1 — DOCTRINE (byte-identical) · PASS
6/6 lane pins green + an **independent** probe on the real `build_decision_overlay` path (no `configured=`, no `make_config`): S1/S2/S3 as in §1. `raw_yaml_decision() = None` (shipped config.yaml has no active `decision:` block). §13 coverage limitation verified: with no chat model configured `search()` bypasses `_llm_select` entirely ⇒ 0 calls, 0 rows — covered-by-limitation, not a hole.
*Disclosure (info):* S1/S2 emit **exactly one suppression row** (`verdict='no_overlay'`, `reason='switch_off'`) rather than literally zero — the detached consult fires whenever an overlay is attached and reads the gate inside the task (the ratified round-3 semantic). The substantive invariant (no A/B row, 0 calls, byte-identical selection) holds in every state.

### G2 — SHADOW INVARIANT · PASS
By construction (one apply site; `allow_apply` needs `_ratified`; `_ratified` written only inside `transition_stage`; seams 2/3/4 have no builders and no registrations — one production registration at `factory.py:108`); by instrumentation (apply instrumented to raise → **0** invocations on the S3 path); by positive control (ratified transition + inline consult → instrument **fired**, `apply_calls = 1`). D3 refusal quoted verbatim; refusal lives at **write-time** (`registry.py:374-380`) **and** read-time (`overlay.py:553-557`). Named residual (design-authorised, `seam-registry.md:116`): a hand-set `stage='deciding'` makes the seam consult, bill and write deciding-mode rows, but the answer stays **structurally unappliable** without a ratified transition.

### G3 — TEN BEHAVIOURAL OBLIGATIONS · PASS (strongest evidence in the gate)
44/44 obligation pins green across 12 groups; **19/19 named-revert probes RED for the right reason**, 2 control legs correctly GREEN. T-9's positive WITHIN_SET pin is what catches a superset-only regression **while both negatives stay green** (the "negative-only reads green for the wrong reason" mode demonstrated empirically); T-5 separates both 400 classes **and** the plain transport 404; T-6's trip order (keys → master → notify) is asserted; AC-9 (seed/reverify) and AC-10 (write-verify/no-restart flip) separately mutation-proven; AC-14 = `fallbacks ≡ failures` scoped to `mode=deciding`.
*Test-corpus notes:* T-7's discrimination closes only as a **pin pair** (keep them together); the T-10 parametrize reads `DENIED_SEAM_ROLES` at import, so emptying the constant deletes cases (rc=4) rather than reddening them.

### G4 — DETACHED DISPATCH · PASS (D-3, D-6)
(a) 5.7 ms @2 s sleep vs 4.7 ms @8 s vs 2004/8010 ms inline control; 30/30 rows landed in both ON runs. (b) production lands the row under 8 adversarial `gc.collect()` sweeps — **but the bare-`create_task` control did NOT discriminate on stock CPython 3.14**, so the strong-ref set's *necessity* is not proven by that control (the hazard is real per a separate demonstrator: weakref dead + "Task was destroyed but it is pending!"). (c) timeout row lands (`failure_class=timeout`, `latency_ms=501`). (d) timeout bills exactly the floor; `ConnectError` bills 0 (12/12). (e) in-flight kill not honoured, bounded to one; **dispatch→first-step kill IS honoured** ⇒ D-3. Concurrent dispatch: 2 rows + one ledger total.
*Unreproduced warning:* one early uninstrumented run billed the `ConnectError` control `+0.0005`; not reproduced in 12 subsequent runs; not root-caused (adjacent to the ledger episode, now explained as contamination).

### G5 — MUTATION VERIFICATION · PASS (D-5, D-7, D-8, D-9)
**11/11 claimed kills re-executed red→green**, plus M7 (`config.py` passthrough) and M12/M13 (200-missing-usage / unknown-usage-keys). Reproduced signatures verbatim: `assert False is True` (factory revert), `got 0.0` (D3 floor), `UNIQUE constraint failed: app_settings.key` (lock removal), `assert 499 <= 411` (band), `DID NOT RAISE` ×2 (D2 stores), `'switch_off' == 'switch_unreadable'`, `True is False` (poison removal). Every restore sha-matched; tree porcelain-clean apart from the foreign jsonl.
**Ledger-lock leg:** removal ⇒ UNIQUE-constraint signature at n=20 cold; pre-seeded UPDATE path ⇒ silent undercount to 0.01–0.06 (one trial 0.06 with **zero** exceptions); locked control 0.20, 10/10.
**Accumulator-direct leg:** never-reached consult bills `0.0`; dropping `and reached_socket` reddens the direct store assertion at `0.0005`.

### G6a + G9 — SCOPED DUAL-SIDE REGRESSION + HOUSE PINS · PASS (B-4, D-11, D-12, D-14)
Two-class declaration: **D1** `tests/unit/decision/` — **202 collected / 202 passed / 0 failed / 0 error** at HEAD (the "202" denominator is real), absent-at-base by construction. **D2** dual-side — **673 collected at both legs with per-file byte-parity** (metrics 527 · snapshot_v3 57 · snapshot_search 42 · settings 36). Slicing proof: HEAD 875, BASE 673, executed == declared.
Digests: HEAD `(BARE-ID, 4f3da0a6064d432da0f3a7df19f7b3fe997add98522f4da8ed343854d58ef196)` · BASE `(BARE-ID, cf014a0b0802cd1dade8adfe27209074ac7c7bca1dfc1a985f4dfa5cc428c2f0)` · `comm -13` **∅**. HEAD 1 red id, BASE 7 red ids, 0 ERROR ids in the hermetic legs.
Claim corrections: of the six "pre-existing" reds only **one** reproduces (F21Journal); the 3 auth-matrix + 2 scope-cage reds are **ambient-`ENSEMBLE_AUTH_*` artifacts** (green ×3 hermetically); the `TestTheRuntimeSourceLocator` 5-red block is an **xdist load-flake at base** (8/8 green solo ×3). True pre-existing state = **1 red**, better than claimed, but the claim was not evidence-backed.
**G9:** `test_snapshot_v3.py` **57/57**; `metrics` substring in `snapshot_search_service.py` = **0** (pin :1645 + independent grep); registered adjudication pack **27/27**; **no** pack for `tests/unit/decision/` (D-11).
**Daemon-diff pin (B-4):** GREEN ×3 at HEAD, RED ×3 at BASE (direction real) — but the predicate change makes the assertion **tautological**; `decisions.md` §15's "asserts only the lane-caused list is empty" overstates the pin's strength.

### G7 — CONTENT-FREE UNDER ADVERSITY · **FAIL (B-1, B-1a, D-6)**
Hostile set H1–H8 (prose id + fake secret · unicode/RTL/NUL/emoji · 10,000-char id · dunder ids · raising `.id` · non-str `.id` / raising `__str__` · no `.id` at all · transport exception carrying secret + prompt). `search()` returned the heuristic selection in **every** case (the overlay never fails the caller); the shadow row was clean in every case; every metric label value was inside the §14 vocabulary; the board payload was clean; H4 proved by AST that every `getattr(` on the consult path uses a **literal** attribute name, and a live class object as `.id` contributes nothing.
**Leak (B-1):** `client.py:263-267` — exception message and args reach a DEBUG record verbatim. The **detached** containment is TYPE-only (`overlay.py:807-812`); the deeper fences are not (B-1a).
*Discrimination:* patching `ShadowWriter._render` to emit prompt text made the detector catch all 4 needles ⇒ the green is discriminating, not vacuous.
*Pre-existing, out of lane (advisory):* `snapshot_search_service.py:368-372, 418-421, 429-430` log `{e}`/`{e!s}` verbatim and `:430` returns hostile exception text in the caller-visible `error` envelope (declared contract) — introduced 2026-09-25 (`35de22b73`), predates the lane base.

### G8 — KILL CHANNEL END-TO-END · PASS (D-13)
All six legs over the **real** services (real `SettingsService`, `AppSettingKillStore`, `SettingsServiceKillStore`, `KillChannelResolver`, `DecisionOverlay`, `SeamRegistry`; scratch SQLite): flip-without-restart **both directions** (calls frozen at 1→2→3) with the load-bearing contrast that **without** the invalidation callback the same write leaves a stale cached ON and the consult still reaches the socket; restart survival (`verified_off`/`verified_on`, 0 vs 1 calls; store tier authoritative over env); boot seed never overwrites (mutation-discriminating) but **dormant** (D-13); A1 narrowing **0** invalidations for an unrelated write, **exactly 1** per `decision_*` write/clear, with the widened-filter control proving the filter is what discriminates; global-master poison consulted (a poisoned global key suppresses a *different* seam, survives a bare cache drop, cleared only by explicit `invalidate()`/`reverify()`); fail-closed polarity both directions with the real store class over a failing engine.
*Not exercised:* the manager's real `initialize()` and `set_settings_service` entry points (daemon boot prohibited) — wiring read-verified at `manager.py:2878, 4396-4402, 6152-6179`.

---

## 4. REGRESSION CLASSIFICATION (with denominators)

**Scoped dual-side (G6a) — D1 + D2**
HEAD **875** nodes (202 D1 + 673 D2) · BASE **673** nodes · net-new **∅** · HEAD reds 1 (pre-existing F21Journal) · BASE reds 7 (1 pre-existing + 1 daemon-diff + 5 xdist load-flakes) · 0 ERROR ids in the hermetic legs.

**Whole-tree Tier-2 dual-side (G6b) — the tier that mattered**
| | HEAD leg | BASE leg |
|---|---|---|
| collected | **35,364** (deselected 1,199; ignored `tests/packs` + `tests/e2e/test_context_injection_hybrid.py`) | **35,162** (deselected 1,199; same ignores) |
| executed (run set) | **35,352** (12 perf nodes pre-excluded per the registry) | 491 HEAD-red ids, identical id→shard mapping, 0 absent at base |
| outcome | **34,520 P / 464 F / 27 E / 335 S / 6 xfail** — closure **exact** (`34,520+464+27+335+6 = 35,352`; no node lost, no death-recovery bisection needed) | **84 P / 385 F / 22 E** (of 491) — closure exact |
| rate | 746 nodes/min aggregate (HEAD leg 47.4 min) | 382 s |
| digests | sweep `(BARE-ID, a6bdc251cf2a07e8cff84dca499280416148e43c8a7acc57ade1964f1c6839b8)` · `(GREP-FULL, 300643ebbab4e4179a12261c331f04f9c389ab1d5d3a6dcc7b28218686331f65)` | sweep `(BARE-ID, a240fbd894c2626ec06d0a6f1f6225f1e392d37b0e754f81a2a39c53ab6813af)` · `(GREP-FULL, b90d19c31e345cd558ba63902e31d485cd07b838d071a23abbf7d8b9d83b6989)` |

Sweep delta `comm -23` = **84**; `comm -13` (base-only/"fixed") = **0** — nothing silently healed; `comm -12` = 407. Per-id adjudication (3× per side, no xdist, then 1-process-per-node ×3 for residuals): **62 reproduced on both sides** (pre-existing) · **18 green 3/3 both sides** (xdist/event-loop artefact: `daemon/services/db_offload.py:188 RuntimeError: got Future attached to a different loop`) · **1 green at HEAD / flaked at BASE** · **3 red at HEAD only = RESIDUAL NET-NEW** · 1 red at BASE only.

**Net-new = 3** (HEAD=FFF/EEE, BASE=PPP, deterministic both directions, not disputed by flake evidence):
1. `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_post_merge_span_exactly_sanctioned_hunk` ⇒ **B-3**
2. `tests/unit/test_webfetch_builtin.py::TestWebFetchBootstrapIntegration::test_bootstrap_creates_webfetch_server` ⇒ **B-2**
3. `tests/unit/test_webfetch_builtin.py::TestWebFetchBootstrapIntegration::test_schema_drift_removes_stale_flag` ⇒ **B-2**

**Pre-existing baseline (denominator for the release stage): 469 unique pre-existing reds across 122 files** (`4d81183d22e5da282d87d312f5369d91b5b57f3ac8cfd5372ec1e63a244e34c4`); 161 are named by a `QUARANTINE.md` row referencing the file, **308 are not** (the ledger indexes families by prose, not by path — a cross-reference caveat, stated rather than papered over). Largest families (all red at BASE under the identical command): `tests/test_injection_api.py` 29 · `tests/unit/routers/test_slash_commands_router.py` 25 · `tests/test_spawn_team_members.py` 21 · `tests/test_progressive_dispatch.py` 18 · `tests/unit/test_builtin_mcp_servers.py` 17 · `tests/test_instance_search_api.py` 17 · `tests/test_memory_integration.py` 16 · `tests/test_governor_integration.py` 16 · `tests/unit/test_configure_builtin_idempotency.py` 15 · `tests/unit/test_governor_recursion_guard.py` 10.

**Budget:** 98 min actual vs the ≤90 min Tier-2 advisory — **loud breach**; the sweep itself was complete (all 12 shards finished with summaries) and the overrun was post-sweep adjudication (84 candidates × 3 per side + solo bisection).

**Note on scope:** the leader's enumerated G6 scope (decision/, metrics/, snapshot suites, settings resolution, adjudication pin) produced **net-new ∅** — and still missed two lane-caused reds, because the lane's `manager.py` delta reaches test files nobody thought to include. The mechanical Tier-2 escalation is what caught them.

---

## 5. WHAT I COULD NOT FULLY VERIFY

1. **The 21 latent reds behind B-2.** `tests/unit/test_builtin_mcp_servers.py` ×17 and `tests/unit/test_context7_builtin.py` ×4 error at `daemon/manager.py:1365` (`self.config.services.service_tool.max_concurrent`) **before** reaching `:2880`. The "21" is a **derivation from trace order**, not an execution — fixing `service_tool` alone would convert 21 base-reds into 21 `decision` reds. So the true lane-caused red count at whole-tree scale is **3 measured + 21 derived = up to 24**, and it stays unmeasured until the pre-existing `service_tool` fixture gap is closed.
2. **The strong-reference set's necessity** (G4b). The specified bare-`create_task` control did **not** discriminate on stock CPython 3.14 (it also landed the row). Proven: the production path lands the row under adversarial GC, and the GC hazard is real on this platform. **Not** proven: that removing the set would lose a row here.
3. **The manager's real boot path.** `initialize()`'s `await self._decision_kill_resolver.reverify([...])` and `set_settings_service`'s callback registration were verified **by reading** (`manager.py:4396-4402`, `:6152-6179`) and by exercising the identical call shapes — not by booting a daemon (prohibited).
4. **Live proxy / live PostgreSQL / live daemon.** Zero live contact by instruction. The mock audit found the transport doubles **faithful** on every recorded live-contract row, with two divergences: `answers[]`-out-of-order is never mocked (order-independence unpinned — low), and the live `usage` **key names** are unpinned in the design of record (info).
5. **The one-off `ConnectError` billing** (G4 warning) — not reproduced in 12 runs, not root-caused.
6. **Live PG row-lock semantics** for the ledger RMW — all probes SQLite; PG is strictly more forgiving for an unsynchronised RMW and the shipped lock makes the question moot today.
7. **Which process held the mutation at each instant** during the ledger episode — contamination is proven by traceback fingerprint + mtime proximity, not per-process observation.
8. **The 19 xdist artefact ids' per-node attribution** — proven non-reproducible without xdist (3/3 green at HEAD), but the specific cause (‑n 4 × 3 concurrent shards × 7 sibling workers) was not isolated per node.

---

## 6. PROCESS DEFECT — MY OWN DISPATCH (disclosed)

The two ledger reds that reached G8 were **caused by my dispatch**, not by the lane: I sent G5 to mutate `daemon/services/decision/cost_store.py` **in the shared worktree** while eight sibling workers were running tests in that same worktree. The RCA proved it from the traceback (`# MUTANT M4: process-level lock REMOVED` — a line absent from HEAD) and the mtime timeline (mutant 19:48:56 → restore 19:52:40). **Lesson: file-mutating verification legs must run in an isolated worktree, or by in-memory subclass/monkeypatch only** — never in a worktree other lanes are testing. Every other leg's red/green evidence in this report was re-verified on a clean tree and is unaffected.

---

## 7. RECOMMENDED DISPOSITION

1. **B-1 + B-1a** (10 sites): log `type(exc).__name__` only. Re-run G7 + the decision pack.
2. **B-2**: either add `decision` to the spec'd fixtures (3 sites + `tests/conftest.py`) **or** make the production read fail-soft (`getattr(self.config, "decision", None)` + a documented default). Leader's call on which side owns the fix.
3. **B-3**: resolve the guard-vs-lane collision — widen the sanctioned-hunk list / drop `manager.py` from `NOT_TOUCHED_PATHS` for this span / route the wiring through a sanctioned path. **Not** a `manager.py` code bug.
4. **B-4**: enumerate the 78 pre-existing offenders by name; assert `set(offenders) - PRE_EXISTING == set()`.
5. **D-3** reword in `decisions.md:125` + `overlay.py:743` + `snapshot_search_service.py:701`.
6. **D-5** harden the ledger pin (pool inside the test + barrier leg); **D-4** disclose the per-object lock for P2/P4.
7. **D-17 / D-18** are recipe patches for `.agents/tester/skills/whole-tree-base-proven-diff/` — not applied by this gate (verification-only); the patch text is in the defect register.
8. Optional hygiene: D-6…D-16, D-19. Register a pack for `tests/unit/decision/` (D-11) — tester-side, post-merge.

**Verdict: NO-GO on the current tree.** All six behavioural invariants are proven; the blockers are small, mechanical and precisely located; the re-gate after the fixes is cheap.

---

*House artifacts:* this file is untracked in the lane worktree for the giter's evidence-rider (**it is the tester's own gate report** — G6b observed it appear mid-run and correctly declined to touch it). Foreign telemetry `…/designer-agent/install-audit.jsonl` was never reverted, committed or deleted; it grew from other lanes' pytest runs during the session (349 → 376 lines). No commits, merges, pushes or staging were performed by this gate.
