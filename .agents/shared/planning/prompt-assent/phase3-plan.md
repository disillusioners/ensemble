# Phase 3: Acceptance path and provenance flow

## Objective

Wire the **acceptance path** and prove the **provenance chain** end-to-end, with the FE path
**provably unchanged**:

1. a **service-level second accepted approval path** — the shared predicate
   `is_acceptable_approver(actor, attestation)`, implemented **once** in the service layer and
   consulted by the classic prompt surface, so the "who may approve, and on what evidence" rule
   exists in **one** place;
2. the **three-factor gate** in its final form, including the **window-freshness bound**
   (D-6 / J-2) — the fourth, cheap, fail-closed check on top of the commission's three factors;
3. the **provenance flow** — mint → echo → approve, with the full evidence chain reconstructable
   from the ledger (token ↔ item ↔ message ↔ human ↔ relay agent ↔ time);
4. the **mandatory test matrix** — the happy path with a **real token echo** and **all five**
   mandated refusals;
5. the **FE-unchanged proof** — the FE routes, service and repository methods are byte-identical
   to the base commit.

**The router does not change.** The relay path never goes through HTTP.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **3.1** | **Add the shared approver predicate** — `daemon/services/prompt_version_service.py`: `is_acceptable_approver(actor, attestation) -> bool`, implementing the rule **once**: the session lane (existing) **or** the assent-relay lane with a **complete** attestation. The router never calls it directly (the router has no attestation); the **repo** enforces the same rule at the entrypoint (Phase 1 task 1.6) — defence in depth, two layers, **one** rule | P1 | The predicate is the **only** place the rule lives; both the service and the repo consult the same semantics; the existing session path's behaviour is unchanged |
| **3.2** | **Add the service acceptance entrypoint** — `svc.approve_assent(version_id, attestation)` / `svc.reject_assent(version_id, attestation, reason)`: validate the attestation, then call the Phase 1 repo entrypoint. **Do NOT** call `repo.atomic_transition` directly — the F2 lesson (`daemon/routers/prompts.py:747-753` — the retract bypass that was the F2 finding) | P1, 3.1 | The service is the **only** entrypoint the tool uses; no path reaches `atomic_transition` except through the service |
| **3.3** | **Assemble the attestation** — the service (or a dedicated helper) builds the typed `AssentAttestation` from **daemon-owned** evidence only: the factor-2 window (`source`, `message_id`, `stamped_at`, `expires_at`), the factor-3 message row (the approving human identity from the adapter metadata `user_id`, `guild_id`, `channel_id`), the token row (the item binding + `token_id` + `token_fingerprint`), the relay instance id, and `attested_at`. **The tool arguments never contribute** | 3.2 | The attestation carries every field; an incomplete attestation is refused; a **grep proves no tool argument feeds the identity fields** (the R3 analogue, `daemon/repositories/prompt_version/repository.py:602-604`) |
| **3.4** | **Implement the user-origin + freshness gate** — read `manager._user_origin_windows[current_instance_id]` (the same read the promote gate performs, `daemon/tools/upgrade_tools.py:3388-3391`). Refusals: `no-user-origin-turn` (naming the observed source + classifier detail — the W1 hardening, `:3393-3419`), `user-origin-window-expired` (fail-closed on an unparseable/absent `expires_at`, `:3421-3432`), and **`user-origin-window-stale`** — the **D-6 / J-2** bound: `window["stamped_at"]` must be within **≤10 minutes** of the tool call, because the dispatch funnel **skips the stamp on a silent resume** (`daemon/manager.py:11503`) and a paused→resumed Ari can otherwise hold an earlier turn's window | 3.3 | Each refusal fires on its own trigger; an unparseable timestamp is **expired/stale**, never "valid"; the refusal names the observed source |
| **3.5** | **Implement the factor-3 content echo** — read the **exact** message row identified by the window's `message_id` (`manager._queue_repository.get(message_id).content`) and require the token in its content, matched with the house helpers `nonce_normalize` / `nonce_in_content` (`daemon/tools/upgrade_journal.py:218-246`) — **reused verbatim**, with a **new** regex (never `NONCE_RE`, which pins `CONFIRM-`+8). An unreadable row is `assent-verification-unavailable` (**fail closed**) — the promote analogue (`daemon/tools/upgrade_tools.py:3524-3530`) | 3.4 | A token present only in a tool argument (not in the message content) is **refused**; the refusal text says *"daemon restarted since the token was issued"* when the row is unreadable (risk R-4's wording requirement) |
| **3.6** | **Implement the token verification + consume ordering** — find the token by hash; verify status (`live`), TTL (`token-expired`), item binding (`token-item-mismatch`), and the **content hash** if J-3 is ruled in (`token-content-mismatch`); enforce the attempt counter + **lockout at 5** (`token-locked`); then **transition first, consume second** (D-4) — a refusal **never burns** the token (house rule: *"nonce NOT burned"*, `daemon/tools/upgrade_tools.py:3708-3777`) | 3.5, P1 | Each token refusal fires on its own trigger; the attempt counter increments on a mismatch; a lockout revokes the live token (⇒ a fresh mint, and therefore a **fresh human turn**, is required to clear it); on success the token row is `consumed` with `consumed_by_message_id` set |
| **3.7** | **Prove the FE path is unchanged** — diff the FE router (`daemon/routers/prompts.py:616-697`), the FE service methods (`daemon/services/prompt_version_service.py:435-504`) and the FE repo methods (`daemon/repositories/prompt_version/repository.py:1541-1607`) against the base commit `0efdf106f`; assert **zero** behavioural change; assert the existing FE test suites pass byte-identically; assert `approve()`/`reject()` still refuse `internal` actors (F4) | 3.1–3.6 | The diff is empty in behaviour; the FE suite is green and unchanged; the F4 refusal is intact; `bearer` is still refused for approvals by the clamp |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/services/prompt_version_service.py` | shared predicate + the two service entrypoints + `list_pending` (Phase 1 task 1.9) | 3.1, 3.2 |
| `daemon/repositories/prompt_version/repository.py` | **no change in this phase** (Phase 1 owns it) | — |
| `daemon/tools/prompt_assent.py` | the gate assembly (3.4–3.6 are implemented here or in a helper the tool calls) | 3.4, 3.5, 3.6 |
| `tests/unit/test_prompt_assent_lane.py` | NEW — the **mandatory test matrix** | 3.6, 3.7 |
| `tests/integration/test_prompt_assent_flow.py` | NEW — the end-to-end provenance flow | 3.3, 3.6 |

## The MANDATORY TEST MATRIX (the heart of this phase)

> **[approve happy path with real token echo; REJECTS: wrong token, expired token, non-pending row, agent-self-approval attempt, non-user-origin turn]**

**Every cell must assert three things:** a **typed refusal code**, **no state change** on the
pending row, and **no token burn**.

| Cell | Code | Extra assertions |
|---|---|---|
| **approve happy path with real token echo** | *(success)* | The row transitions `pending_approval → approved`; exactly **one** new `prompt_audit_events` row; the payload carries approving user, source, message id, relay agent, timestamp, token fingerprint (**never** the raw token); the token row is `consumed` with `consumed_by_message_id` set. **The token must be in the content of the exact message** identified by the factor-2 window — a token passed only as a tool argument must NOT be accepted |
| **REJECT: wrong token** | `token-mismatch` | Row state unchanged; token **not** burned; the attempt counter increments |
| **REJECT: expired token** | `token-expired` | Row state unchanged; token not burned |
| **REJECT: non-pending row** | `item-not-pending` (carrying the current state) | The terminal row is untouched; **no** audit decision event is written |
| **REJECT: agent-self-approval attempt** | `self-approval-attempt` | Row state unchanged. **Documented as belt-and-braces**: the load-bearing defence is that an agent-originated turn cannot arm factor 2 (`daemon/manager.py:6132-6133`) — the test must assert that property, not just the code |
| **REJECT: non-user-origin turn** | `no-user-origin-turn` (naming the observed source + classifier detail) | Row state unchanged; token not burned |

**Additional cells** (from the refusal taxonomy): `user-confirmation-missing` ·
`lane-disabled` · `user-origin-window-expired` · `user-origin-window-stale` ·
`assent-verification-unavailable` · `token-already-used` · `token-superseded` ·
`token-item-mismatch` · `token-locked` · `item-not-found` · `item-not-assentable` ·
`already-decided` · `audit-unavailable`.

**Mandatory extras for the happy path:**
- The provenance chain is **reconstructable** from the ledger: token ↔ item ↔ message ↔ human ↔
  relay agent ↔ time.
- The **raw token appears nowhere** in durable state — grep the token table and the audit ledger
  after a mint→approve cycle; only `sha256(token)[:12]` fingerprints.
- The FE path still works: the same row type can still be approved by the session lane.

## Coupling

- **Tight with:** Phase 1 — this phase **consumes** the repo entrypoint, the attestation type and
  the token API, and **owns the service half of C-2**.
- **Tight with:** Phase 2 — the tool's `approve`/`reject` actions call **this** phase's service
  entrypoint. The tool must never reach `atomic_transition` directly.
- **Independent of:** Phase 4's kill-switch class — the gate works with the Phase 2 fail-closed
  resolver; Phase 4 only adds the ability to turn the lane ON.

**Shared-context ownership.** Phase 3 owns the **service half of C-2**. Phase 1 owns the
repository half. **If the architect prefers a single owner, merge Phase 1 and Phase 3** rather than
run them concurrently against
`daemon/repositories/prompt_version/repository.py`.

**The seam.** The **attestation type** is the contract between Phase 1 (defines it) and Phase 3
(produces it). If it changes shape, both sides change together — never one alone.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The tool reaches `atomic_transition` directly**, bypassing the service | High | Medium | Task 3.2's acceptance forbids it; the F2 precedent is cited (`daemon/routers/prompts.py:747-753`); add a **grep pin** that the tool module contains no `atomic_transition` reference |
| **The rule is implemented twice** (service predicate + repo entrypoint) and drifts | High | Medium | One predicate in the service layer (task 3.1), consulted by both surfaces; the repo's check is the *enforcement* of the same semantics — a **counter-mutation** on either side must redden the same test |
| **The factor-3 check is satisfied by a tool argument** rather than by the message content | **Critical** | Medium | The happy-path test asserts the token must be **in the message content**; a token passed only as an argument must be refused. This is the single most important assertion in the matrix |
| **The freshness bound is not implemented** (D-6 not ruled, or forgotten) | High | Medium | `user-origin-window-stale` is a **named** refusal with its own test cell; if the architect rules D-6 the other way, the code and the test are removed **together** |
| **A refusal burns the token** (consume-before-transition) | Medium | Medium | D-4's ordering — transition first, consume second; the test asserts the token survives every refusal |
| **The window is stale from a silent resume** and authorizes a later turn | High | Medium | D-6 / J-2's ≤10-minute bound, plus the **direct unit test** that an agent-originated dispatch clears the window (`daemon/manager.py:6132-6133`) |
| **The approval is recorded without full provenance** (a missing payload key) | High | Medium | The happy-path test asserts **every** provenance key is present; the audit payload test (Phase 1) asserts the raw token is **absent** |
| **The FE path regresses** while the lane is added | High | Medium | Task 3.7's diff-against-base proof, plus the existing FE suites run byte-identically |
| **`self-approval-attempt` is mistaken for the load-bearing defence** | Medium | Medium | The test must assert the **construction property** (an agent-originated turn cannot arm factor 2) alongside the code — the code is an assertion, not a gate |
| **A daemon restart between mint and reply** looks like a user error | Medium | High | The refusal text says *"daemon restarted since the token was issued"* (risk R-4) — the promote lane's wording (`daemon/tools/upgrade_tools.py:3524-3530`) |

## Test plan (house conventions)

**Env hygiene (H-7).** Scrub per-variable — `env -u POSTGRES_DB -u POSTGRES_USER -u
POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>`. `env -u POSTGRES_*` is a **silent
NO-OP**: bash does not glob env names, so an ambient `POSTGRES_DB=ensemble_prod` stays visible and
a "fenced" run can hit **prod**.

**Runner (H-8).** The **worktree's own venv** (never the main checkout's `.venv` — its editable
`.pth` pins the main tree); `cd` into the worktree first; `testpaths=["tests"]`; the four
deselected dirs (integration / postgres / slow / conformance) — **note the integration dir is
deselected by default**, so the end-to-end test is invoked with an **explicit** `tests/` path;
`timeout=30` + `thread`; `-p no:timeout` where a test needs it; explicit `tests/` lists;
`pytest -q` buffers tracebacks to session end ⇒ `tee LOG >/dev/null` + `tail -2 LOG`.

**Net-new = FAILED-list diff (H-6).** Capture the TRUE pre-fix base with the **same commands**;
diff the failed-lists; a test already failing before the change is **not** a new red.

**Tests to write in this phase.**

| Test file | Covers |
|---|---|
| `tests/unit/test_prompt_assent_lane.py` (NEW) | the **mandatory test matrix** — the happy path with a real token echo + all five mandated refusals + the additional cells |
| `tests/integration/test_prompt_assent_flow.py` (NEW) | the end-to-end provenance flow: mint → show diff + token → human turn carrying the token → approve → read the ledger back → assert the full chain and the raw token's absence |
| `tests/unit/test_prompt_assent_fe_unchanged.py` (NEW) | the FE-unchanged proof: the FE route/service/repo behaviour is identical to the base; F4 and the `bearer` refusal are intact |
| `tests/unit/test_prompt_assent_freshness.py` (NEW) | `user-origin-window-stale` fires when `stamped_at` is older than the bound; an unparseable timestamp is stale/expired, never valid |

**Non-vacuity.** Each matrix cell needs a **counter-mutation** that reddens it. The two classic
prose-satisfiable traps here: (i) a test that only checks the row's state (a wrong token that
never touched the row would also pass) — assert the **refusal code** too; (ii) a test that greps
for the presence of a string — absence-only and raw-text pins are prose-satisfiable and prove
nothing.

**Pack.** Add a lane pack to `test/packs/` following the house convention, exercising the matrix
as a pack-level run.

## Exit Criterion

**All of the following hold:**

1. The **mandatory test matrix passes** — the happy path with a **real token echo** and **all
   five** mandated refusals, each asserting a typed code, **no state change**, and **no token
   burn**.
2. The **provenance chain is reconstructable** from the ledger: approving user, source, message
   id, relay agent, timestamp, token fingerprint — and the **raw token is absent**.
3. The **FE path is provably unchanged** — zero behavioural diff against the base commit, the FE
   suites green and byte-identical, F4 intact.
4. The service is the **only** route to `atomic_transition`; a grep proves the tool module
   contains no direct reference.
5. The **net-new failed-list is empty** against the TRUE pre-fix base.

Only then may Phase 4 begin.
