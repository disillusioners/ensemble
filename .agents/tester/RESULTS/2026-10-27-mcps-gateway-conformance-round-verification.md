# MCPS Gateway v0.2 Conformance Round — Authoritative Test Gate

- **Date:** 2026-10-27
- **Branch / worktree:** `feature/mcps-gateway-conformance-round` @ `05b94c459` (base `51544da77`, 13 commits) in `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-conformance-round-v02`
- **SPEC authority (read-only):** `mcps_client-wt-session-termination` @ `7e748d2` (spec-changelog.md §11.0 Fork rulings A/B/C)
- **Tester instance:** tester (this lane); workers credited per pack
- **Production blast radius:** ONE file — `daemon/services/mcps_gateway/service.py` (+375/−42). No routers/repositories/FE/scripts changes; `scripts/conformance/` untouched.
- **Prior gates on record:** reviewer APPROVE-WITH-NOTES + delta APPROVE (AST no-drift, 0 assertions removed); tidier honesty batch landed.

## 1. Suite Runs

| # | Pack | Invocation (verbatim) | Result | Counts | Runtime |
|---|------|------------------------|--------|--------|---------|
| 1 | Gateway unit ×9 | `timeout 300 env -u MCPS_GATEWAY_ENABLED -u MCPS_GATEWAY_TOKENS uv run python -m pytest tests/unit/test_mcps_gateway_{audit_projection,config_and_tools,docs,protocol,service,session_expiry,termination,tool_filter,transport}.py -q` | ✅ **PASS** | 366 passed / 0 failed / 0 skipped | 12.7 s wall |
| 2 | Conformance kit | `CONFORMANCE_RUN_TIMEOUT=280 timeout 300 bash scripts/conformance/run.sh` | ✅ **PASS** | 56 passed / 0 failed / 0 skipped — census 56 collected (auth 4, errors 4, handshake 4, mcp_fidelity 4, peer_selftest 17, session_lifecycle 4, termination 14, transport 5) | 19.33 s |
| 3 | Integration lane | `timeout 300 env -u POSTGRES_URL -u PGPASSWORD -u DATABASE_URL POSTGRES_HOST=127.0.0.1 POSTGRES_DB=ensemble_dev_test_only ENSEMBLE_SELF_ENV=dev DAEMON_PORT=8079 uv run python -m pytest tests/integration/mcps_gateway --override-ini='addopts=' -m integration -q` | ✅ **PASS** | 38 passed / 0 failed / 0 skipped (28 warnings, pre-existing deprecations) | 70.37 s pytest / 73 s wall |
| 4 | P0 gate (authoritative) | via the `test_p0_gate.py` wrapper embedded in pack #3 (wrapper runs `p0_gate.py` main — the authoritative mechanism) | ✅ **PASS** | 44 `[PASS]` gate lines / 0 `[FAIL]`; attempt 1 | inside pack #3's 70 s |

**Unit ×9 notes:** count reconciles EXACTLY to the developer baseline (366 == 366 — no shrink, no growth). Worker `bce4d0dd`.

## 2. Mutation Matrix (per-rule RED→GREEN, both implementations of each rule)

Method: each rule is pinned in TWO independent implementations — the daemon (`daemon/services/mcps_gateway/service.py`) and the self-hosted kit's own transcription (`tests/conformance/conformance_peer.py`, zero `daemon.*` imports, so daemon mutations are provably inert on kit rows and vice versa). Each leg: mutate → RED → surgical `git checkout -- <file>` restore → GREEN. No `git reset --hard`/stash ever (foreign dirty files preserved throughout).

| Rule | Seam mutated | RED evidence | GREEN after restore | Verdict |
|------|--------------|--------------|---------------------|---------|
| **NC-1** declined end-ack = refusal (§6.9.7; Fork A) — sweep + explicit arms | A: `service.py:3047` `_ack_declines` body → `return False` (shared chokepoint of sweep `:2156` + explicit `:2780`) · B: `conformance_peer.py:185` same predicate in kit transcription | A: **3 failed**/144 passed — exactly the three pins: session_expiry `:528` (sweep arm, tombstone `machine_notified=True`), session_expiry `:766` (explicit arm, session survives), service `:787` (declined ≠ acknowledged); uniform signature "declined ack must NOT be recorded as machine-notified" · B: **1 failed**/55 passed — exactly `test_a_declined_end_ack_is_a_refusal_not_an_acknowledgement` (machine released local lock on decline, §6.9.3 finding), **zero collateral** | A: 147/147 · B: 56/56 | ✅ **MUTATION-PROVEN** (both seams) |
| NC-2 reason omitted, never `""`/ledger-only, on absent cause | A: `service.py:1108` guard reverted to `if reason in LEDGER_ONLY_REASONS:` (exact 1-hunk reverse of `5fe6462fb`; absent cause ⇒ `reason == ""` passes through: `:1075` maps None→`""`) · B: `conformance_peer.py:1532-1533` omission branch `if reason: ack["reason"]=…` reduced to unconditional `ack["reason"]=…` (pre-`96f20c4b1` shape) | A: **2 failed**/34 passed — `:679` absent-cause-omits (`:700` assert) + `:703` empty-never-empty (`:717` assert), both with the forbidden `reason: ''` on the wire; **`:720` ledger-verbatim pin stayed GREEN — spec-correct**: the ban is response-scoped (§6.9.3/§6.10.1), ledger exempt (§6.10.2), so flagging it would be a false finding · B: **1 failed**/55 passed — exactly `test_a_terminate_without_a_reason_code_omits_the_reason` (`FINDING …/terminate-reason-absent-omitted`), zero collateral | A: 36/36 · B: 56/56 | ✅ **MUTATION-PROVEN** (both seams) |
| NC-3 foreign-session terminate indistinguishable | A: `service.py:1173-1174` — deleted arm re-inserted at 12-space indent (`elif session is not None: wire_reason = REASON_UNKNOWN_SESSION`), exact reverse of `55e7cc4e1`; **binding verified by AST walk**: the `elif` must attach to `if tombstone is not None:` (`:1163`) — at 8-space it would bind to `if owned:` and be a silent no-op (false GREEN), the trap was avoided · B: `conformance_peer.py:1534-1549` — the shared `else` arm's reason expression (`:1547`) split so foreign-live answers `unknown_session` while never-existed keeps `already_ended` | A: **1 failed**/35 passed — exactly `test_a_foreign_live_session_answers_exactly_like_a_truly_unknown_one` (def `:617`, rule text `:624`), assertion `{'reason':'unknown_session'} != {'reason':'already_ended'}` at `:655` · B: **1 failed**/55 passed — exactly `test_a_terminate_for_another_machines_session_is_indistinguishable` (finding `terminate-foreign-indistinguishable`), zero collateral | A: 36/36 · B: 56/56 | ✅ **MUTATION-PROVEN** (both seams) |
| NC-4 params.reason = v0.2 AND client_terminate | A: `service.py:2051-2054` `_session_end_params` gate reverted to version-only (dropped the `and connection.client_terminate` conjunct — exact reverse of `280b00a57`) · B: `conformance_peer.py:1906-1914` `InKitCenter.agent_end` explicit-end site reduced to version-only (the kit transcribes the rule twice — sweep `:1775-1783` and explicit `:1907-1914`; the named row drives the explicit site) | A: **3 failed**/100 passed — exactly the three pins: termination `:919` + `:952` (`TestVersionAndCapabilityScopedSessionEndParams`, both bare-params asserts) and session_expiry `:355` (assert `:384`); signatures show the forbidden `reason` riding bare params · B: **1 failed**/55 passed — exactly `test_the_v02_connection_without_the_capability_keeps_the_bare_params` (`{'reason': 'superseded'}` on a no-capability 0.2 connection), zero collateral | A: 103/103 · B: 56/56 | ✅ **MUTATION-PROVEN** (both seams) |
| F2 decline-audit constants distinct from error-frame refusal | `service.py:2844` (`_end_declined`: `detail=AUDIT_DETAIL_SESSION_DECLINED` → `AUDIT_DETAIL_SESSION_REFUSED`) + `service.py:2214` (sweep's decline arm under `elif outcome == FORWARD_DECLINED:`: `AUDIT_DETAIL_EXPIRE_DECLINED` → `AUDIT_DETAIL_EXPIRE_REFUSED`) — the pre-`1eaea51e6` classification collapse, exactly 2 insertions/2 deletions (no kit seam: no kit row exists for the ledger-detail constants — daemon-only rule) | **2 failed**/145 passed — exactly the two pins, one per arm: `test_a_declined_sweep_ack_is_an_honest_refusal` fired at **:569** (ledger said "…refused the end", pin expects startswith "…declined the end") and `test_a_declined_ack_is_a_refusal_and_the_session_survives` fired at **:818** (same collapse on the explicit arm); zero collateral | 147/147 | ✅ **MUTATION-PROVEN** (daemon seam; the only seam the rule has) |

**NC-4 supplementary finding (evidence-backed coverage gap, adjudicated as KNOWN-GAP — not fixed mid-gate):** the kit's *sweep-path* transcription (`conformance_peer.py:1775-1783`, `"reaped_by_center"`) was separately probed with a version-only revert and the kit stayed 56/56 — **no kit row pins the capability half on the sweep path** (the kit's only negative no-capability pin is the explicit-end path). The daemon seam pins BOTH paths through the shared `_session_end_params` helper (one edit flipped 3 pins), so the RULE is fully pinned in production; the gap is confined to the kit's self-consistency net. **Adjudication:** no new kit row in this round (it would move the census off the 56 this gate certifies); filed for the conformance-lane owner as the follow-up row: *v0.2 machine with no `client_terminate` + TTL expiry → sweep's `session.end` params stay exactly `{"session_id": …}`*.

**Mutation-leg discipline:** every leg verified `git status --porcelain` = exactly the three foreign paths before mutating; restored surgically via `git checkout -- <file>` (never `reset --hard`/`checkout .`/`stash`); HEAD unchanged at `05b94c459` throughout; every RED leg reported its full failing-id set, every GREEN leg re-ran the identical invocation. One trap defeated on record: NC-3's re-inserted `elif` had to bind to `if tombstone is not None:` at 12-space indent — an 8-space indent would bind to `if owned:` and produce a **false GREEN**; the binding was AST-verified before the RED was trusted.

## 3. Kit Row Census (mission gates 2 + 3)

- **Run:** `CONFORMANCE_RUN_TIMEOUT=280 timeout 300 bash scripts/conformance/run.sh` → **56 passed / 0 failed / 0 skipped in 19.33 s** (self-consistency mode banner confirmed).
- **Census (`--collect-only`):** 56 collected = auth 4 + errors 4 + handshake 4 + mcp_fidelity 4 + peer_selftest 17 + session_lifecycle 4 + **termination 14** + transport 5.
- **Row accounting:** 52 baseline rows all green unchanged in intent (incl. the expiry-end-params v0.1 lane and the §9-patch-split `expiry-end-params-reason` lane) + 3 round rows + 1 F1 lane = 56.
- **The four round lanes, verbatim ids:** `test_a_declined_end_ack_is_a_refusal_not_an_acknowledgement` · `test_a_terminate_without_a_reason_code_omits_the_reason` · `test_a_terminate_for_another_machines_session_is_indistinguishable` · `test_the_v02_connection_without_the_capability_keeps_the_bare_params` — each present, green at HEAD, and **each independently RED under its own rule-break** (§2).
- **PACKS.md:85** updated: PENDING TESTER marker replaced with the 56/56 verdict; the prior 55/55 entry preserved.

## 4. P0 Gate (mission gate 6)

- **Authoritative run:** via the `test_p0_gate.py` wrapper inside the integration pack (the wrapper drives `p0_gate.py` main).
- **Verdict: PASS** — appended block header `## Attempt 1 — PASS (2026-10-27T17:56:27Z)` / `=== Phase-D1 P0 GATE — attempt 1 — PASS ===` with **44 `[PASS]` lines, 0 `[FAIL]`**. Notable gates: `engine-targets-fence-db` (→ `ensemble_dev_test_only`), `audit-row-args-hash-exact`, `session-end-acknowledged`, `no-listener-left — remaining=[] baseline=[]`, `client-process-stopped — exit=0`.
- **Evidence file delta (append by design, preserved):** `p0-gate-evidence.md` 2553 → 2646 lines (**+93 lines / +10901 bytes**); `git diff --numstat` = `93 0` (append-only, zero deletions).

## 5. A/B Classification (mission gate 7)

**Nothing to classify: every suite run was fully green** (unit 366/366; kit 56/56; integration 38/38; P0 gate 44/44 gate lines). Zero red nodes → branch diff ∩ QUARANTINE.md '## Active' = ∅ by construction. Known-open context, status this round:
- **ws pin `:215`** — fixed in prior round `31e25ea5f`; not re-observed (no ws-lane reds).
- **P3 flake** — known-open; not observed this run.
- **conftest langgraph-mock leak** — not in this lane (no such reds).
- QUARANTINE.md '## Active' contains **no mcps_gateway-lane families** — nothing pre-existing could have absorbed a red here.

## 6. Per-Rule Verdict Table

| Rule | Statement | Daemon pins | Kit row | Verdict |
|------|-----------|-------------|---------|---------|
| **NC-1** | A declined end-ack is a REFUSAL, never an acknowledgement; `machine_notified` never true because of one (sweep + explicit arms) | session_expiry `:528` (sweep/tombstone) + `:766` (explicit/survives) + service `:787` — all RED under chokepoint flip | `declined-end-is-a-refusal-not-an-acknowledgement` RED under peer flip | ✅ **CERTIFIED — MUTATION-PROVEN ×2** |
| **NC-2** | An absent cause OMITS `reason` — never `""`, never ledger-only tokens (response-scoped; ledger verbatim) | termination `:679` + `:703` RED with the forbidden `reason: ''` on the wire; `:720` ledger-verbatim pin correctly stayed green (ledger is exempt by spec) | `terminate-reason-absent-omitted` RED under peer flip | ✅ **CERTIFIED — MUTATION-PROVEN ×2** |
| **NC-3** | Foreign-session terminate indistinguishable from never-existed (leak ban) | termination `:617` (rule text `:624`) RED — `unknown_session` ≠ `already_ended` | `terminate-foreign-indistinguishable` RED under peer flip | ✅ **CERTIFIED — MUTATION-PROVEN ×2** |
| **NC-4** | `params.reason` rides VERSION **AND** capability (`client_terminate`), not version alone | termination `:919` + `:952` and session_expiry `:355` RED under version-only revert (all three) | `v02-no-capability-end-params-bare` RED under version-only peer revert | ✅ **CERTIFIED — MUTATION-PROVEN ×2** |
| **F2** | Decline-audit constants distinct from error-frame refusal (two arms, two constants) | session_expiry `:569` (sweep) + `:818` (explicit) RED under classification collapse | n/a — no kit row exists (ledger-detail rule is daemon-only) | ✅ **CERTIFIED — MUTATION-PROVEN (sole seam)** |

## 7. Residual Risks & Deferred Items

1. **Kit sweep-path capability gap (evidence-backed, non-blocking):** the kit's sweep-path transcription (`conformance_peer.py:1775-1783`) has NO negative no-capability row — a version-only revert there leaves the kit 56/56. The daemon pins both paths via the shared `_session_end_params` (one edit flipped 3 pins), so production coverage is complete; the gap is confined to the kit's self-consistency net. **Follow-up for the conformance-lane owner:** add the row (v0.2 machine without `client_terminate` + TTL expiry → sweep's `session.end` params stay exactly `{"session_id": …}`). Deliberately NOT added mid-gate — it would move the census off the 56 this gate certifies.
2. **MCPS E2E vs a real v0.2 client: DEFERRED (out of scope by instruction)** — gated on the client branch merge (`mcps_client` main has no terminate); the kit's self-hosted transcription plus the fake-remote integration lane cover the wire contract until then.
3. **P3 flake (known-open):** not observed this round; remains watch-listed.
4. **`machine_notified` semantic retarget (delivery → acknowledgement):** the error-frame refusal arm deliberately keeps its place in the tombstone tuple while `FORWARD_DECLINED` is excluded — pinned by NC-1's sweep test; no residual.

## 8. Scope Decision & ensure.md

**Scope decision:** production blast radius = ONE file (`daemon/services/mcps_gateway/service.py`, +375/−42) pinned by 7 modified/new test files + the kit. Full suite NOT warranted — a single-module conformance lane, not a cross-module refactor. Ran: the 4 packs in §1 + 5 mutation legs. Skipped: full non-integration suite, concurrency pack, e2e/FE, `tests/unit` beyond the gateway files, subdirectory gateway unit files (`repositories/`, `routers/` — outside the developer's 366-node baseline shape and untouched by the diff). Blast radius derived from `git diff 51544da77..05b94c459`.

**ensure.md (Core, blast-radius scoped):**
- ✅ Critical — "No regressions in changed packs": every pack in the change set PASS (§1: 366/366, 56/56, 38/38, P0 PASS). Mutation legs are scratch-only; all restored byte-identical.
- ⚪ Critical — `concurrency_atomic_unit_test`: **not in blast radius** (change set touches no lock/DB-call-shape seam; the new audit write uses the existing `_audit_session` helper). Not run — would burn a 5-min pack on an unrelated subsystem.
- ⚪ Critical — dev.sh `--timeout-graceful-shutdown` static check: dev.sh not in the change set; out of scope this round.
- Release Gate: not triggered (not a big/critical/architecture change).

## 9. Overall Status

| Gate | Verdict |
|------|---------|
| (1) Per-rule mutation-proven tests | ✅ 5/5 rules MUTATION-PROVEN (4 × both seams, F2 × its sole seam) |
| (2) Kit row update certified | ✅ 56/56 + PACKS.md:85 verdict written |
| (3) 52 baseline kit rows green | ✅ unchanged in intent |
| (4) Gateway unit files | ✅ 366/366 |
| (5) Integration lane | ✅ 38/38 |
| (6) P0 gate authoritative | ✅ PASS (44 `[PASS]`/0 `[FAIL]`; +93-line append preserved) |
| (7) A/B classification | ✅ nothing to classify — zero reds; known-open items not observed |

**TESTING COMPLETE — READY. Verdict: SHIP.**
