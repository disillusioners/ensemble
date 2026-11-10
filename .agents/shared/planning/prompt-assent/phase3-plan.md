# Phase 3: Acceptance path and provenance flow

> **Binding order.** `architecture-recommendation.md` @ `df8da01a6` **wins on conflict** with this
> document. **Must-apply deltas (transcribed 2026-11-10):**
> - **O-E — the three-factor gate has ONE implementation owner: Phase 2's
>   `daemon/tools/prompt_assent.py`.** This phase previously claimed it in tasks 3.4–3.6 **and**
>   Phase 2 claimed it in 2.4 — a double-claim. **This phase does NOT re-implement the gate.** It
>   owns: the **daemon-derived identity extraction**, the **attestation assembly**, the
>   **service-level predicates** the gate calls, and the gate's **acceptance tests**.
> - **D-5 — the approving-user key is the NESTED `message_metadata["discord"]["user_id"]`** —
>   **REQUIRED**, fail-closed `extraction-incomplete`. A top-level `metadata.get("user_id")`
>   returns `None` and must never be used.
> - **AR-1 legs 3 / K-8 — the approve branch requires BOTH a channel match AND a daemon-derived
>   `mint_user_id` equality** (both sides daemon-derived; neither is ever tool-supplied).
> - **D-4 — the counters commit in their OWN transaction, BEFORE the guarded transition** (refusal
>   path only); the render-hash re-check lives **inside** the P1 hook's transaction (**O-L** — the
>   **only** render-hash site).
> - **J-3 / K-7 — the render hash is over the NAMED CANONICAL RENDER from the one shared helper**
>   (P1 task 1.10); the **effective side is included**, so **baseline motion invalidates** the
>   outstanding token.
> - **Architect §4 — the matrix gains 5 cells + a counter-separate-transaction regression cell + 6
>   named counter-mutations + 6 NEW cells.**

## Objective

Wire the **acceptance path** and prove the **provenance chain** end-to-end, with the FE path
**provably unchanged**:

1. a **service-level second accepted approval path** — the shared predicate
   `is_acceptable_approver(actor, attestation)`, implemented **once** in the service layer and
   consulted by the classic prompt surface, so the "who may approve, and on what evidence" rule
   exists in **one** place;
2. the **daemon-derived identity extraction** and the **attestation assembly** — the nested
   `message_metadata["discord"]["user_id"]` key (fail-closed), the channel binding, the
   `mint_user_id` equality, and the **`render_sha256`** carried on the attestation;
3. the **service-level predicates** the tool gate calls — the channel-match and the
   `mint_user_id`-equality checks (AR-1), and the **counter-before-transition** ordering (D-4);
4. the **provenance flow** — mint → echo → approve, with the full evidence chain reconstructable
   from the ledger (token ↔ item ↔ message ↔ human ↔ relay agent ↔ time);
5. the **mandatory test matrix** — the happy path with a **real token echo**, the **five** mandated
   refusals, the architect §4 additions, and the **six named counter-mutations**;
6. the **FE-unchanged proof** — the FE routes, service and repository methods are byte-identical
   to the base commit.

**The router does not change.** The relay path never goes through HTTP.

**⚠ The three-factor gate is NOT implemented here (O-E).** Its single implementation owner is
Phase 2's `daemon/tools/prompt_assent.py` (`phase2-plan.md` task 2.4). This phase supplies the
**daemon-derived inputs** the gate consumes and **proves** the gate's behaviour by test.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **3.1** | **Add the shared approver predicate** — `daemon/services/prompt_version_service.py`: `is_acceptable_approver(actor, attestation) -> bool`, implementing the rule **once**: the session lane (existing) **or** the assent-relay lane with a **complete** attestation. The router never calls it directly (the router has no attestation); the **repo** enforces the same rule at the entrypoint (Phase 1 task 1.6) — defence in depth, two layers, **one** rule | P1 | The predicate is the **only** place the rule lives; both the service and the repo consult the same semantics; the existing session path's behaviour is unchanged |
| **3.2** | **Add the service acceptance entrypoint** — `svc.approve_assent(version_id, attestation)` / `svc.reject_assent(version_id, attestation, reason)`: validate the attestation, then call the Phase 1 repo entrypoint. **Do NOT** call `repo.atomic_transition` directly — the F2 lesson (`daemon/routers/prompts.py:747-753` — the retract bypass that was the F2 finding) | P1, 3.1 | The service is the **only** entrypoint the tool uses; no path reaches `atomic_transition` except through the service |
| **3.3** | **Build the daemon-derived identity extraction helper + assemble the attestation** — a single helper reads the **nested** adapter metadata from the message row the factor-2 window's `message_id` selects: **`message_metadata["discord"]["user_id"]` is REQUIRED and fail-closed** → `extraction-incomplete` when absent or unparseable (a top-level `metadata.get("user_id")` returns `None` and must **never** be used — the adapter builds `discord_meta` at `daemon/sources/adapters/discord/adapter.py:1046-1058` and wraps it at `:1113-1117`; the queue stores `metadata` JSONB). From the same nested dict: `guild_id`, `channel_id`, `channel_type`, `is_dm`, **`is_group_dm`** (P1's adapter discriminator), and `message_id` (platform-side). `source_id` = the window source's prefix before the first `":"`. **`external_user_id` is NEVER the approving-user key** — its guild form is a **channel** (`adapter.py:994-1021`); it remains the destination identity. `user_name`/display name are **cosmetic only, never authority**. `approver_subject = f"{source_id}:{user_id}"`. The service then assembles the typed `AssentAttestation` (architect §2.3 field set) from **daemon-owned** evidence only: the window (`source`, `message_id`, `stamped_at`, `expires_at`), the extracted human identity, the token row (item binding + `token_id` + `token_fingerprint` + **`render_sha256`**), `relay_source_message_id` (**platform-side** — the daemon queue id is ephemeral/wiped at boot, so without it the forensic chain breaks), the relay instance id, and `attested_at`. **The tool arguments never contribute** | 3.2, P1 (`is_group_dm` discriminator) | The attestation carries **every** field of the architect §2.3 set; an incomplete extraction is refused `extraction-incomplete`; **a grep proves no tool argument feeds the identity fields** (the R3 analogue, `daemon/repositories/prompt_version/repository.py:602-604`); `approver_subject` is `f"{source_id}:{user_id}"`; the platform-side message id is carried |
| **3.4** | **Own the AR-1 service-level predicates: channel match + `mint_user_id` equality** — the approve branch requires **BOTH**: (i) the approving turn's **daemon-derived** `(source_id, channel_id)` == the token row's mint context → else `token-channel-mismatch`; and (ii) the approving turn's **daemon-derived** `user_id` == the token row's `mint_user_id` → else `token-approver-mismatch`. Both sides are daemon-derived from the same window-message evidence chain — **neither is ever tool-supplied**, so this is **not** a forgery surface. Also the **private check** on the approving context (`is_dm AND NOT is_group_dm`, absent discriminator ⇒ not private ⇒ fail-closed) → `token-channel-mismatch`. **⚠ The window-freshness bound (D-6 / J-2) is now P2-owned** (the gate's implementation lives there) — this phase **specifies and pins** it: `stamped_at` within the configured bound (default 600 s) of the tool call, **in addition to** `expires_at`; absent/unparseable ⇒ stale, never valid; refusal `user-origin-window-stale` | 3.3 | Each predicate fires on its own trigger with its **own distinct code**; a channel match with a **different user** is `token-approver-mismatch` (not `token-channel-mismatch`); an absent/unparseable timestamp is **stale**, never "valid"; the freshness bound applies to **mint and approve** alike |
| **3.5** | **Carry the render binding on the attestation — over the NAMED CANONICAL RENDER** — the attestation carries **`render_sha256`** = `sha256(canonical_render)` where the canonical render is produced by the **one shared helper** (P1 task 1.10) from the **pending content**, the **effective content**, the item key, the labels, and the format tag `"v1"`. The **effective side IS included** — so **baseline motion invalidates**: if the effective side moves underneath (a newer version approved), the pending row's meaning changes and the outstanding token MUST invalidate (`token-content-mismatch`) rather than silently approve against a different baseline. The CAS alone cannot see effective-side motion. **The re-check lives ONLY in P1's D-4 hook, inside its transaction, from the row as read in-txn (O-L)** — this phase **does not** duplicate it (a second render-hash site would reintroduce the two-site drift the architect's ruling closed). **Copying `prompt_versions.content_hash` is FORBIDDEN** — the column-source pin proves the hash comes from the canonical render | 3.3, P1 (helper + hook) | The attestation's `render_sha256` equals the mint response's `render_sha256`; an **effective-side** mutation after mint invalidates the token (`token-content-mismatch`); a **pending-side** mutation after mint invalidates it too; a grep proves **no** second render-hash site exists outside the hook |
| **3.6** | **Implement the token verification + consume ordering — with the COUNTER-BEFORE-TRANSITION rule** — find the token by hash; verify status (`live`), TTL (`token-expired`), item binding (`token-item-mismatch`); enforce the **per-token attempts counter + lockout at 5** (`token-locked`) and the **hourly budget** (`mismatch-budget-exceeded` — the mechanism is **P2-owned**; this phase calls it); then **transition first, consume second** (D-4) — a refusal **never burns** the token (house rule: *"nonce NOT burned"*, `daemon/tools/upgrade_tools.py:3708-3777`). **⚠ D-4 BLOCKING RULE:** the **attempt-counter increment and the lockout revocation commit in their OWN transaction OUTSIDE the guarded transition** — before it opens, on the refusal path only. Inside it, every refusal's rollback resets the counter and the lockout **never fires** (D-3's defence-in-depth would be silently inert). The consume itself is **co-transactional** with the CAS + audit INSERT (P1's hook) | 3.4, 3.5, P1 | Each token refusal fires on its own trigger; the attempt counter increments on a mismatch; a lockout revokes the live token (⇒ a fresh mint, and therefore a **fresh human turn**, is required to clear it); on success the token row is `consumed` with `consumed_by_message_id` set; **the counter survives a rollback of the guarded transition** (the own-transaction property is directly asserted) |
| **3.7** | **Prove the FE path is unchanged** — diff the FE router (`daemon/routers/prompts.py:616-697`), the FE service methods (`daemon/services/prompt_version_service.py:435-504`) and the FE repo methods (`daemon/repositories/prompt_version/repository.py:1541-1607`) against the base commit `0efdf106f`; assert **zero** behavioural change; assert the existing FE test suites pass byte-identically; assert `approve()`/`reject()` still refuse `internal` actors (F4) | 3.1–3.6 | The diff is empty in behaviour; the FE suite is green and unchanged; the F4 refusal is intact; `bearer` is still refused for approvals by the clamp |
| **3.8** | **Land the six named counter-mutations + the column-source pin as first-class pins** — every matrix cell below must carry a **named counter-mutation** that **reddens** it (the house mutation rule — absence-only and raw-text pins are prose-satisfiable and prove nothing). The **single most important pin**: remove the message-exactness check ⇒ a token present **only in the tool arguments** must **REDDEN** the happy-path cell. Plus the **column-source pin** proving `render_sha256` is **not** a copied `prompt_versions.content_hash` (K-7) | 3.4, 3.5, 3.6 | Each named counter-mutation **demonstrably reddens** its cell (the mutation evidence is attached); the token-in-tool-args-only mutation **reddens** the happy path; the column-source mutation **reddens** the mint test |

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

### The six original cells — each with its **named counter-mutation** (architect §4)

| Cell | Code | Extra assertions | **Named counter-mutation (must REDDEN)** |
|---|---|---|---|
| **approve happy path with real token echo** | *(success)* | The row transitions `pending_approval → approved`; exactly **one** new `prompt_audit_events` row; the payload carries approving user, source, message id, relay agent, timestamp, token fingerprint (**never** the raw token); the token row is `consumed` with `consumed_by_message_id` set. **The token must be in the content of the exact message** identified by the factor-2 window — a token passed only as a tool argument must NOT be accepted | **⚠ THE SINGLE MOST IMPORTANT PIN: remove the message-exactness check** ⇒ a token present **only in the tool arguments** must **REDDEN** this cell |
| **REJECT: wrong token** | `token-mismatch` | Row state unchanged; token **not** burned; the attempt counter increments | **Move the counter into the guarded transaction** ⇒ the lockout test reddens |
| **REJECT: expired token** | `token-expired` | Row state unchanged; token not burned | **Break the TTL comparison** |
| **REJECT: non-pending row** | `item-not-pending` (carrying the current state) | The terminal row is untouched; **no** audit decision event is written | **Drop the state predicate from the CAS** |
| **REJECT: agent-self-approval attempt** | `self-approval-attempt` | Row state unchanged. **Documented as belt-and-braces — NOT the gate**: the load-bearing defences are that an agent-originated dispatch pops the window (`daemon/manager.py:6132-6133`), the ari-EXACTLY layer-3 gate, and — since pass 1 — the **daemon-derived user equality** (AR-1/K-8). The test must assert those construction properties, not just the code | **Make a non-user-origin dispatch leave the window alive** ⇒ reddens |
| **REJECT: non-user-origin turn** | `no-user-origin-turn` (naming the observed source + classifier detail) | Row state unchanged; token not burned | **Fork/re-derive the classifier** ⇒ the unforked-reuse pin reddens |

### The amendment-pass cells (architect §4 table 2) — **6 NEW cells + the column-source pin**

| Cell | Guards | Expected | **Named counter-mutation** |
|---|---|---|---|
| **Mint without a user-origin window** (K-2) | The mint gate — an agent-originated (or stale-window) turn cannot mint | `no-user-origin-turn` / `user-origin-window-stale`; **NO token row written**; a prior live token is **NOT** superseded | Remove the mint gate ⇒ a token row is written |
| **Mint in a group DM / non-private channel** (K-3) | The 1:1-DM discriminator + the fail-closed-absent rule | `mint-requires-private-channel`; **no token row** | Treat `is_dm=True` as private (ignore `is_group_dm`) ⇒ the cell reddens |
| **Approve by a different user of the same 1:1 channel** (K-8) | `mint_user_id` equality, **daemon-derived on both sides** | `token-approver-mismatch`; no state change, no burn | Drop the user-equality leg ⇒ the cell reddens |
| **Hourly mismatch budget exhaustion** (K-9) | The in-RAM per-instance budget | `mismatch-budget-exceeded` on the **11th** mismatch within the hour | Raise the default budget above 10 ⇒ the cell reddens |
| **Pending-content mutation after mint** (J-3) | The in-txn render-hash re-check | `token-content-mismatch`; **rollback** — no state change, no audit row | Move the re-check outside the transaction ⇒ the rollback assertion reddens |
| **Effective-side mutation after mint** (J-3/K-7) | The **effective side is included** in the canonical render | `token-content-mismatch`; the counter-mutation that proves the render is **NOT** a `prompt_versions.content_hash` copy | Drop the effective side from the canonical render ⇒ the cell reddens |
| **Token column populated from `prompt_versions.content_hash`** (K-7 pin) | SP-1 rewritten to the canonical render | The mint test **reddens** if the column is fed the wrong source | Feed the column from `prompt_versions.content_hash` ⇒ the mint test reddens |

### The counter-separate-transaction regression cell (D-4 blocking rule)

| Cell | Guards | Expected | **Named counter-mutation** |
|---|---|---|---|
| **Counters commit in their own transaction** | D-4's blocking rule — the attempt-counter increment and the lockout revocation commit **outside** the guarded transition, on the refusal path only | A refusal that rolls back the guarded transaction **still leaves the attempt counter incremented**; the lockout therefore **does** fire after 5 mismatches | Move `record_failed_attempt` inside the guarded transaction ⇒ the counter does **not** survive the rollback and this cell reddens |

**Additional cells** (from the refusal taxonomy): `user-confirmation-missing` ·
`lane-disabled` · `invalid-arguments` · `user-origin-window-expired` ·
`user-origin-window-stale` · `assent-verification-unavailable` · `token-already-used` ·
`token-superseded` · `token-item-mismatch` · `token-content-mismatch` ·
`token-channel-mismatch` · `token-approver-mismatch` · `mint-requires-private-channel` ·
`mint-live-token-exists` · `extraction-incomplete` · `token-locked` ·
`mismatch-budget-exceeded` · `item-not-found` · `item-not-assentable` ·
`already-decided` · `audit-unavailable`.

**Matrix rules (amended):**
- Every cell carries a **named counter-mutation** that **demonstrably reddens** it — the
  mutation evidence is attached to the lane report (task 3.8).
- **Non-vacuity is not optional here.** The house mutation rule applies: absence-only and
  raw-text pins are prose-satisfiable and prove nothing.
- The **gate is P2's** (O-E). This matrix **proves** the gate; it does not re-implement it. A
  grep pin asserts a single implementation site.

**Mandatory extras for the happy path:**
- The provenance chain is **reconstructable** from the ledger: token ↔ item ↔ message ↔ human ↔
  relay agent ↔ time.
- The audit payload carries **`render_sha256`** — the hash of the **exact canonical render bytes**
  the Emperor was shown — so the shown-bytes guarantee is auditable after the fact.
- The **raw token appears nowhere** in durable state — grep the token table and the audit ledger
  after a mint→approve cycle; only `sha256(token)[:12]` fingerprints.
- The FE path still works: the same row type can still be approved by the session lane.
- **The token-in-tool-args-only mutation REDDENS this cell** — the single most important pin in
  the whole set (task 3.8).

## Coupling

- **Tight with:** Phase 1 — this phase **consumes** the repo entrypoint, the attestation type and
  the token API, the **one shared canonical-render helper** (task 1.10) and the **D-4 hook**
  (task 1.11); it **owns the service half of C-2**.
- **Tight with:** Phase 2 — the tool's `approve`/`reject` actions call **this** phase's service
  entrypoint. The tool must never reach `atomic_transition` directly. **⚠ O-E — the gate
  itself is P2's**: this phase supplies the daemon-derived inputs and proves the behaviour by
  test; it does **not** re-implement the gate.
- **Independent of:** Phase 4's kill-switch class — the gate works with the Phase 2 fail-closed
  resolver; Phase 4 only adds the ability to turn the lane ON.

**Shared-context ownership.** Phase 3 owns the **service half of C-2**. Phase 1 owns the
repository half. **If the architect prefers a single owner, merge Phase 1 and Phase 3** rather than
run them concurrently against
`daemon/repositories/prompt_version/repository.py`.

**The seam.** The **attestation type** is the contract between Phase 1 (defines it) and Phase 3
(produces it). If it changes shape, both sides change together — never one alone.

**The second seam — the render hash (O-L).** `render_sha256` is produced by **P1's** helper
(1.10) and re-checked **only** inside **P1's** hook (1.11). Phase 3 **carries** the value on the
attestation and **tests** it; it does **not** compute or re-check it. A second render-hash site
anywhere would reintroduce the two-site drift the architect's ruling closed.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The tool reaches `atomic_transition` directly**, bypassing the service | High | Medium | Task 3.2's acceptance forbids it; the F2 precedent is cited (`daemon/routers/prompts.py:747-753`); add a **grep pin** that the tool module contains no `atomic_transition` reference |
| **The rule is implemented twice** (service predicate + repo entrypoint) and drifts | High | Medium | One predicate in the service layer (task 3.1), consulted by both surfaces; the repo's check is the *enforcement* of the same semantics — a **counter-mutation** on either side must redden the same test |
| **The factor-3 check is satisfied by a tool argument** rather than by the message content | **Critical** | Medium | The happy-path test asserts the token must be **in the message content**; a token passed only as an argument must be refused. **And the counter-mutation is named and demonstrated: removing the message-exactness check must REDDEN the cell** (task 3.8). This is the single most important assertion in the matrix |
| **The freshness bound is not implemented** (D-6 not ruled, or forgotten) | High | Medium | `user-origin-window-stale` is a **named** refusal with its own test cell; if the architect rules D-6 the other way, the code and the test are removed **together** |
| **A refusal burns the token** (consume-before-transition) | Medium | Medium | D-4's ordering — transition first, consume second; the test asserts the token survives every refusal |
| **The window is stale from a silent resume** and authorizes a later turn | High | Medium | D-6 / J-2's ≤10-minute bound, plus the **direct unit test** that an agent-originated dispatch clears the window (`daemon/manager.py:6132-6133`) |
| **The approval is recorded without full provenance** (a missing payload key) | High | Medium | The happy-path test asserts **every** provenance key is present; the audit payload test (Phase 1) asserts the raw token is **absent** |
| **The FE path regresses** while the lane is added | High | Medium | Task 3.7's diff-against-base proof, plus the existing FE suites run byte-identically |
| **`self-approval-attempt` is mistaken for the load-bearing defence** | Medium | Medium | The test must assert the **construction property** (an agent-originated turn cannot arm factor 2) alongside the code — the code is an assertion, not a gate |
| **A daemon restart between mint and reply** looks like a user error | Medium | High | The refusal text says *"daemon restarted since the token was issued"* (risk R-4) — the promote lane's wording (`daemon/tools/upgrade_tools.py:3524-3530`) |
| **The nested identity is read from the wrong place** — a top-level `metadata.get("user_id")` returns `None` for Discord, so the extraction silently yields no approver | **High** | Medium | The extraction is **REQUIRED** and **fail-closed** → `extraction-incomplete`; a top-level read is **never** used; the nested path `metadata["discord"]["user_id"]` is pinned by a test that also asserts a top-level read is absent |
| **The channel matches but the user does not** (K-8) — a second occupant of a bound channel satisfies all three factors plus a channel-only check | **High** | Medium | AR-1 leg 3 — the daemon-derived `mint_user_id` equality; refusal `token-approver-mismatch`; the "different user of the same channel" cell pins it |
| **The counters are committed inside the guarded transaction** (D-4) ⇒ every refusal's rollback resets the counter and the lockout **never fires** | **High** | Medium | D-4's blocking rule — counters commit in their **own** transaction; the **counter-separate-transaction regression cell** pins it; the named counter-mutation (move the counter inside) reddens it |
| **A second render-hash site creeps in** (O-L) ⇒ the pre-check and the in-txn re-check can diverge | High | Medium | The re-check lives **only** in P1's hook; a grep pin asserts no other render-hash site exists; the phase-3 coupling note states it explicitly |
| **The gate is implemented twice** (the prior P2-2.4 + P3-3.4–3.6 double-claim) ⇒ the two copies drift | High | Medium | **O-E** — the gate has **one** implementation owner (P2's tool module); a grep pin asserts a single implementation site; this phase supplies inputs and tests only |

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
| `tests/unit/test_prompt_assent_lane.py` (NEW) | the **mandatory test matrix** — the happy path with a real token echo + all five mandated refusals + the additional cells; **every cell carries its named counter-mutation** (task 3.8) |
| `tests/integration/test_prompt_assent_flow.py` (NEW) | the end-to-end provenance flow: mint → show diff + token → human turn carrying the token → approve → read the ledger back → assert the full chain, the `render_sha256`, and the raw token's absence |
| `tests/unit/test_prompt_assent_fe_unchanged.py` (NEW) | the FE-unchanged proof: the FE route/service/repo behaviour is identical to the base; F4 and the `bearer` refusal are intact |
| `tests/unit/test_prompt_assent_freshness.py` (NEW) | `user-origin-window-stale` fires when `stamped_at` is older than the bound; an unparseable timestamp is stale/expired, never valid; the bound applies to **mint and approve** alike |
| `tests/unit/test_prompt_assent_mint_gate.py` (NEW) | **mint-without-window** (no token row; a prior live token is **not** superseded) and **group-DM mint** (`mint-requires-private-channel`; no token row) |
| `tests/unit/test_prompt_assent_user_binding.py` (NEW) | **wrong-user-of-channel** → `token-approver-mismatch`; **channel mismatch** → `token-channel-mismatch`; both daemon-derived, neither tool-supplied |
| `tests/unit/test_prompt_assent_budget.py` (NEW) | **budget exhaustion** → `mismatch-budget-exceeded` on the 11th mismatch within the hour; the budget is per relay instance and is **not** persisted |
| `tests/unit/test_prompt_assent_render_mutation.py` (NEW) | **pending-side mutation** and **effective-side mutation** after mint both → `token-content-mismatch` with rollback; **baseline motion invalidates** |
| `tests/unit/test_prompt_assent_counter_transaction.py` (NEW) | the **counter-separate-transaction regression cell** — the attempt counter survives a rollback of the guarded transition; the lockout fires after 5 mismatches |
| `tests/unit/test_prompt_assent_counter_mutations.py` (NEW) | the **six named counter-mutations**, each demonstrated to **REDDEN** its cell (the mutation evidence is the artefact this file produces) |

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
2. **The amendment-pass cells pass** — mint-without-window, group-DM mint, wrong-user-of-channel,
   budget exhaustion, pending-side mutation, effective-side mutation, and the **column-source
   pin** — plus the **counter-separate-transaction regression cell**.
3. **All six named counter-mutations demonstrably REDDEN** their cells (task 3.8), with the
   mutation evidence attached — above all, **token-in-tool-args-only → the happy path reddens**.
4. The **provenance chain is reconstructable** from the ledger: approving user, source, message
   id, relay agent, timestamp, token fingerprint, **`render_sha256`** — and the **raw token is
   absent**.
5. The **FE path is provably unchanged** — zero behavioural diff against the base commit, the FE
   suites green and byte-identical, F4 intact.
6. The service is the **only** route to `atomic_transition`; a grep proves the tool module
   contains no direct reference.
7. **The gate has exactly one implementation site** (O-E — grep-proven).
8. The **net-new failed-list is empty** against the TRUE pre-fix base.

Only then may Phase 4 begin.
