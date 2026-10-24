# Plan Overview: DF-4 — AGENT-ORIGIN Provenance Gate Closure

Date: 2026-10-21
Author: planner[v2] via plan-creation worker
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-df4` @ `60d7daaf4` (branch `fix/df4-provenance-gate`, verified clean)
Release line: **v0.27.2** (stage only — promote is the user's 3-factor nonce ceremony)
Companion: `implementation-plan.md` (same directory) — phases, pin matrix, mutation/e2e specs, stage checklist.

---

## Objective

Close the DF-4 provenance defect on **both** sides: the sweep must stop minting a forged DIRECT user-origin window from an empty wake source, and the protected-target gate must stop refusing a GENUINE operator-direct turn — so that the delegated forgery is refused while the genuine operator path proceeds.

**Single testable sentence (N6 — aligned with impl-plan's gate shape):** after DF-4, a protected-target act is admitted exactly when the confirmed-target check passes **and** a live user-origin window exists (regardless of class), and no window can enter the system with a forged user-origin root minted by the sweep.

---

## Verified Ground Truth

All anchors below were re-read in the worktree at `60d7daaf4`. Line numbers are exact as verified unless flagged in §Anchor Corrections.

### Defect (A) — WRONG-SIDE ACCEPTANCE: the sweep forges a DIRECT user-origin window

| Anchor | Verified content |
|---|---|
| `daemon/services/upgrade_journal_sweep.py:1209` | `else (wake.source or "api")` — delivery source for the ari-fallback arm |
| `daemon/services/upgrade_journal_sweep.py:1734` | `else (head.source or "api")` — delivery source for the direct arm |
| `daemon/tools/upgrade_journal.py:1127` | `source: str = ""  # "" sentinel = no user-origin at arm time` |
| `daemon/tools/upgrade_journal.py:3354` | `_USER_ORIGIN_EXACT: frozenset[str] = frozenset({"api"})` |
| `daemon/tools/upgrade_journal.py:3376` / `:3391` | `def classify_user_origin(...)` → `if source in _USER_ORIGIN_EXACT:` |
| `daemon/manager.py:4850` | `def stamp_user_origin_window(self, instance_id, source, message_id)` — the dispatch funnel stamp |
| `daemon/manager.py:4905-4906` | `else:` → `self._user_origin_windows.pop(instance_id, None)` — the POP on a non-user-origin source |
| `daemon/manager.py:4914` | `def _inherit_provenance_window(...)` — called from the funnel at `manager.py:9623` |

**The chain:** `PendingWake.source == ""` (no user-origin at arm time) → the sweep substitutes the literal `"api"` → `classify_user_origin("api")` returns `(True, "exact:api")` → the funnel stamps a **DIRECT** user-origin window on the woken instance with **no genuine user ancestor** → on any delegated child (`source` prefix `internal_agent:`) `_inherit_provenance_window` copies that forged window as the **ROOT** of an INHERITED window (depth ≥ 1).

### Defect (B) — WRONG-SIDE REFUSAL: the gate refuses the genuine operator path

| Anchor | Verified content |
|---|---|
| `daemon/tools/instance_ops.py:362` | `"no-user-provenance"` — the sole legitimate provenance refusal |
| `daemon/tools/instance_ops.py:369` | `if verdict.pclass != "inherited":` |
| `daemon/tools/instance_ops.py:372` | `"direct-provenance-insufficient"` — the retired token |
| `daemon/routers/messages.py:691` | `source="api"` — the hard-coded **genuine-by-design** web-UI chat ingress |

A genuine human turn reaching the acting instance directly mints `pclass="direct"` → the gate REFUSES it. That is the wrong-side refusal: the operator path is broken by design.

### Root insight

`pclass` is a **read-time derivation** from store liveness (`daemon/tools/provenance.py:195` / `:229` / `:271`) — never stored, never re-verified, and therefore worthless as a trust signal. **Class-keying a gate is unsound by construction.** Post-Change-1, a live window's root was classified user-origin **at mint time** for BOTH classes, so the class carries zero additional trust information and survives only as observability.

### Gate scope — ONLY `instance_terminate` is gated (N5)

`_protected_gate` is defined at `daemon/tools/instance_ops.py:326` and has **exactly ONE call site** — `:562`, inside `async def instance_terminate` (line 497) within the `create_instance_ops_tools` factory (line 398). Grep-verified: no other tool, read or act, consults the gate. **No other protected-target tool is affected by Change 2**; the "genuine operator path" at issue is `instance_terminate` alone.

### House precedent (the fix recipe, already proven once)

| Anchor | Verified content |
|---|---|
| `daemon/constants.py:946` | `RESERVED_SOURCE_PREFIXES` — colon-terminated families; `"system:"` is the first entry (`:950`) |
| `daemon/constants.py:967-977` | The R18 precedent: the auto-dispatch fallback previously minted a bare `"api"`; the comment records the closure |
| `daemon/constants.py:977` | `"auto_dispatch_fallback"` — the reserved literal that replaced it |
| `daemon/tools/instance.py:973` | `else "auto_dispatch_fallback"` — the hardened fallback site |
| `daemon/constants.py:980` | `def is_reserved_source(source: str | None) -> bool` |
| `tests/unit/tools/test_agent_restart.py:139` | `test_t0a_prime_hardened_fallback_is_reserved` — the pin that keeps it reserved |
| `daemon/tools/upgrade_journal.py:1177` | `RESTART_WAKE_SOURCE = "system:post_restart_arm_notify"` — the restart-kind wake already escapes via the reserved lane; `classify` returns `(False, "reserved-internal")` and the funnel POPs the window |

**Therefore:** `sweep:1209` and `sweep:1734` are the **two remaining unhardened copies** of a defect that was already closed once, house-style. DF-4 applies the same recipe.

### Audit-row containment (must survive) — re-anchored (A6); F6-corrected line numbers

Containment truth at `60d7daaf4` (`daemon/tools/instance_ops.py`): refusal `:569` (kwargs `:577-580`) · budget refusal `:629` (kwargs `:637-640`) · cascade/orphan `:669-670` (**never** carried the fields) · `prov_fields` **assembly** `:688-694` · preview `:699`/`:707` · **COMMIT `:746`/`:755`** (`write_audit_and_act`) · service-refusal `:773`/`:780` · observability read `:724-727`. The **commit** write is `:746`/`:755` — **not** `:637` (a budget-refusal row) and **not** `:690` (the dict literal). Every site is **required to survive** the gate change: the audit ledger is the countability surface for the residual, and `provenance_class` becomes *more* meaningful post-DF-4 — it answers "which window shape authorised this act?" (minted-on-actor vs inherited-through-delegation) on one ledger.

### Bounded findings (architect-verified, A10)

- `MessageQueue.type` is **INVARIANT** under the swap: `"api"` and `system:*` both take the `else` branch → `MessageType.HUMAN` (`daemon/services/instance_messaging.py:2102-2103`). The swap does not change queue typing.
- `priority=2` at **both** sweep sites ⇒ `is_fresh_episode_user_message` stays **False** before and after the change (`instance_messaging.py:2420-2423`, which requires `priority == 1`). The wake turn never re-arms the attestation window.

### Mint-surface exhaustion (Q1)

`_user_origin_windows` has exactly **one** writer (`manager.py:4899`, inside `stamp_user_origin_window`); `_inherited_provenance_windows` exactly **one** (`manager.py:4994`). There are exactly **two** `or "api"` sites in `daemon/` (both sweep sites) and exactly two sweep `enqueue_message` calls. The mint-path enumeration is **closed, not sampled** — "P1 kills the sweep class" is a completeness claim, not a sampling claim. (AST census: **zero** `enqueue_message*` call sites omit `source=` — independently confirmed by this planner's AST walk over `daemon/` (43 calls, 0 omissions; the addendum's contrary denominator is corrected in its ERRATUM). But the `source: str = "api"` **defaults** remain a latent surface — routed, A12.)

### Council-verified facts (reviewer pass — reflected, not re-derived)

- **Migration delta 107 == 107 verified on disk** (vs v0.27.1). Stage-checklist row now cites this.
- **v0.27.2 confirmed as the next free version line** (strictly above every existing tag and `releases/` dir).
- **NO seal/tripwire SHA pin covers ANY DF-4-touched file → NO re-pin obligation** for this commission. Recorded in the stage checklist (row 0e) so the stage lane does not hunt for one.
- **Repo-wide census (council-verified, independently re-run by this planner):** exactly **TWO** `or "api"` sites in `daemon/` (both sweep sites) and **ZERO** `enqueue_message*` calls omitting `source=`. Strengthens the §Mint-surface exhaustion block above.
- **Pin (iii) location: `tests/postgres/test_df4_provenance_closure.py`** (N2/N3-corrected from F4's `tests/integration/`) — the pin needs **real PostgreSQL** and the house real-PG convention + the `postgres` marker live in `tests/postgres/`. Not a unit file, and **not** the `tests/job_queue/` routing-test harness (mock-based → the pin would be vacuous). The `postgres` marker is **deselected by default** (`addopts = -m 'not integration and not postgres and not slow'`), so the mandatory invocation is `pytest tests/postgres/ --override-ini="addopts=" -m postgres` — **a skipped pin = a failed gate**.

---

## Scope

### In Scope

1. **Change 1** — sweep fallback swap at `upgrade_journal_sweep.py:1209` and `:1734`: `(X.source or "api")` → `(X.source or <NEW_RESERVED_LITERAL>)`, the constant declared beside `RESTART_WAKE_SOURCE` in `daemon/tools/upgrade_journal.py`, value **must** begin with `"system:"` so the existing `RESERVED_SOURCE_PREFIXES` family catches it (no new prefix family).
2. **Change 2** — gate predicate at `daemon/tools/instance_ops.py:369-384`: retire the class-keyed predicate; key on `verdict.ok` only. Retire the token `"direct-provenance-insufficient"`; `"no-user-provenance"` (`:362`) remains the sole provenance refusal. Post-fix the gate is **predicate-identical** to the restart lane's gate (`daemon/tools/upgrade_tools.py:2720-2730`, which keys on `provenance.ok`).
3. **Pin amendments** — the four AC pins that encode the old (wrong) behaviour, re-pointed honestly (never silently flipped).
4. **Anti-re-inversion pin** — a NEW source-level pin asserting that NEITHER gate predicate keys on `pclass` (replaces the vacuous fixture-parity pin that never shipped).
5. **Reserved-literal pin** — NEW, mirroring `test_t0a_prime_hardened_fallback_is_reserved`: the new constant is reserved, `classify_user_origin` fails it closed, and neither sweep site retains the bare `or "api"` shape.
6. **e2e real-chain test** — the sweep-forge-dead scenario driven through the **REAL** enqueue/funnel/inherit order, not fabricated window dicts.
7. **Doc amendments** — bracketed corrections appended to the nest-ops docs that carry the retracted D-18 claim as live prose (A7 — the full file/line list is in `implementation-plan.md` P4); plus the `upgrade_tools.py:1275` "FIVE"→six comment fix. **(F7: the `registry.py:857` citation drift is DEFERRED to the §Routed Follow-ups cosmetic lane — not a P4 edit.)**
8. **Comment drift** — `daemon/tools/upgrade_tools.py:1275` (says FIVE, six names are bound).
9. **Agent-visible surface amendments (A3, P2 commit)** — the three `daemon/tools/instance_ops.py` surfaces that go **FALSE** post-P2: `:49-63` (the D-18 "SPEED BUMP, NOT A PROOF" paragraph, incl. the stale `f4cf9c4f` "DF-4 owns the root fix" citation), `:116-121` (`CATEGORY_DOC` — feeds the tool registry at `daemon/tools/_tool_registry.py:1511`/`:1519`), `:810-815` (`instance_terminate._full_doc_`).
10. **Docstring-only carve-out (A4, P4 commit)** — four false-claim sites: `daemon/tools/provenance.py:49-54` (the class-keyed-gate claim) and `:119` ("falls back to `'api'`"), `daemon/tools/upgrade_tools.py:1262` (comment), `daemon/repositories/instance_ops/models.py:78` (`(D-18)` → observability-only wording). **Docstring/comment text only — no code, no relocation.**
11. **Dispatch-flip disclosure + pin (A5, P3)** — post-P1 the wake takes the `is_system_origin` lane (`instance_messaging.py:3490`), `dispatch_source` resolves from `instance_metadata.original_source` (`:3500`) instead of the literal `"api"`, and external dispatch may become **real** (`daemon/sources/dispatcher.py:693-695` — today `"api"` has no `":"` so the dispatch is a no-op skip). Precedented by restart-kind; promote-kind now inherits it.

### Out of Scope

- **The REST-ingress class** (`daemon/routers/jobs_crud.py:597` — user-suppliable body `source` defaulting to `"api"`; `daemon/routers/messages.py:691`). Remains OPEN under filed ticket **critical note `257488dc`** (OAuth2 R3). DF-4 discloses this boundary; it does not claim total closure.
- **The defer-resend mint lane (A2 — leader decision: DISCLOSE-ONLY).** `daemon/services/job_queue_service.py:2608` hard-codes `source="api"` on re-enqueue → a fresh DIRECT window at resend time. Same class as the sweep forge; **not fixed in DF-4**. Routed as **DF-5** (§Routed Follow-ups).
- **The `source: str = "api"` default surfaces (A12)** and the **`instance_ops:` prefix registration (A13)** — routed, no code change in DF-4 (§Routed Follow-ups).
- **`scripts/upgrade/**` release-artifact provenance gate** — a *different* provenance surface (artifact lineage, not AGENT-ORIGIN). Untouched.
- **Any schema change.** No new migration; no `daemon/migrations/versions/` file.
- **Key rotation / `SYSTEM_ENCRYPTION_KEY`** — standing deferral, unrelated.
- **The upgrade lane's factor-2 read** (`upgrade_tools.py:3318-3366`), **the restart gate** (`upgrade_tools.py:2720-2730`), **`stamp_user_origin_window`'s bytes** (`manager.py:4850-4912`), and **`classify_user_origin`'s arm set** — all frozen. `"api"` STAYS armed: `messages.py:691` is the genuine web-UI chat path.
- **TypeScript / frontend** — daemon-only release. No frontend source, no `frontend/` build, no FE sidecar churn.
- **The `daemon/tools/provenance.py` relocation** and the 6-name alias block at `upgrade_tools.py:1289-1296` — already landed under D-14; DF-4 does not touch them. **Carve-out (A4/Q2):** *docstring-only* edits inside `provenance.py` ARE in scope (`:49-54`, `:119`) — the freeze is **relocation/code**, not a file ban.

---

## The Two Changes (summary; detail in `implementation-plan.md`)

### Change 1 — sweep reserved-literal swap (P1)

Replace `(X.source or "api")` at both sweep sites with `(X.source or <NEW_RESERVED_LITERAL>)`. The constant lives beside `RESTART_WAKE_SOURCE` (`daemon/tools/upgrade_journal.py:1177`) and its value MUST start with `"system:"` — `is_reserved_source` (`daemon/constants.py:980`) already matches that prefix family, so `classify_user_origin` fails the value closed and the funnel POPs rather than stamps.

**Semantics:** a fail-closed delivery lane for wakes armed with **no** user origin.

**MUST-PRESERVE:** a non-empty `head.source` still delivers **verbatim**. A genuine live arm captures a real source (factor-2 at `upgrade_tools.py:3318-3366` precedes the arm on live, guaranteeing non-empty) — that genuine path must not break.

**Beneficial side effects to record in the release notes:**
- the durable `instance_metadata.original_source="api"` write (`daemon/services/instance_messaging.py:3524-3528` — **N7-corrected span**) stops happening for these wakes;
- the env-flip 60-minute hazard (`NONCE_TTL_S = 60 * 60`, `upgrade_journal.py:140`) closes.

### Change 2 — gate predicate retirement (P2)

`daemon/tools/instance_ops.py:_protected_gate` keys on `verdict.ok` only (a live window in either store). The class-keyed branch at `:369-384` and the token `"direct-provenance-insufficient"` are retired. `"no-user-provenance"` (`:362`) remains the sole provenance refusal. The refusal text must stop teaching "route the request through the leader" as a workaround for a now-nonexistent condition.

**Rationale (Q1 reframing + root insight):** the **old** gate never blocked the delegated forge — `_inherit_provenance_window` copies the root **without inspecting the root's own provenance** (`manager.py:4960-4984`), so a forged DIRECT on a spawner propagated to an INHERITED window that `:369` **admitted** (the module docstring concedes it, `instance_ops.py:53-56`). The old gate's *entire* real guarantee was "no DIRECT window — genuine **or** forged — arms a protected act", and that guarantee **is** defect B. Post-Change-1, a live window's root was classified user-origin at mint time for BOTH classes, so the class adds **zero** trust signal → it becomes pure observability and stays in the audit rows (`:577-580`, `:637-640`, `:688-694`, `:746-755`). **Q1 verdict:** Change 2 **neutrally re-scopes** the gate — it strictly *strengthens* the delegated-forgery surface (the one the old gate actually admitted) in conjunction with P1, and strictly *weakens* exactly one narrow shape (DIRECT on the acting instance) from refused to admitted, disclosed below and bounded by the control stack.

**Symmetry, not divergence:** the post-fix gate is predicate-identical to the restart lane's gate (`upgrade_tools.py:2720-2730` keys on `provenance.ok`). Two lanes, one predicate.

---

## Coupling

| | P1 (sweep) | P2 (gate) | P3 (pins/e2e) | P4 (docs) |
|---|---|---|---|---|
| **P1** | — | **tight** (P3's closure proof needs both changes) | loose | independent |
| **P2** | **tight** | — | loose | independent |
| **P3** | loose | loose | — | independent |
| **P4** | independent | independent | independent | — |

**Tight coupling P1↔P2:** the closure proof is a conjunction. Change 1 alone leaves the genuine operator path refused (defect B open). Change 2 alone leaves the sweep able to mint a forged DIRECT window that now ADMITS (defect A open, and the direct-forged path would newly admit). **P1 MUST land before P2** so no intermediate commit admits a forged direct window.

---

## Risk Register

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | Change 2 alone lands first → the sweep-forged DIRECT window now ADMITS (net exposure ↑ vs. today) | **High** | Medium | Hard sequencing: P1 merges before P2. **Q4 confirmed by path-walk:** P2-alone is *strictly worse* than today. Pin (iv) anti-re-inversion + pin (iii) sweep-forge-dead are RED on the pre-P1 tree. |
| R1b | **P1 changes the promote-arm wake's MESSAGE IDENTITY.** The new `system:` literal is stamped `injected_message=True` (`daemon/services/instance_messaging.py:400-424` `_stamped_additional_kwargs`, applied at `:587`), whereas `"api"` produced a **bare** HumanMessage. Three downstream effects, previously undisclosed: **(i)** the wake is excluded from the attestation ladder (`daemon/services/attestation_scanner.py:315-444`) — it no longer advances the last-real-user-message anchor and renders as SECTION U (`daemon/services/attestation_resolver_activation.py:1297-1325`); **(ii)** it no longer resets the durable repair budget (`daemon/graph.py:2138-2150`); **(iii)** it is compacted as an injected note rather than a user message (`daemon/_content_hardening.py:141-160`/`:216-224`). | Medium | High | **Disclose in the release notes + pin in P3 (pin (vi) asserts the `injected_message` stamp shape).** Precedented: **the restart-kind wake already has exactly this shape in production** — the promote-kind wake now inherits it. The addendum's Q3 "trips exactly the two it should" understates the blast radius; corrected by the dated ERRATUM in `architecture-addendum.md`. |
| R1c | **Adjacent — NOT in scope (A9):** the re-stamp / TTL-reset defect lives at `daemon/services/upgrade_journal_sweep.py:1698-1709` (pre-existing; the re-stamp is `if w.source:`-gated and never fabricates). DF-4 does not touch it. The house critical note's `:1305-1317` anchor is **stale** (those lines are the grace-pass handler at `60d7daaf4`) — correct the note in a separate lane, not here. | Low (pre-existing) | Low | Out of scope; disclosed so a reviewer does not attribute it to DF-4. |
| R2 | The new literal's value misses the `"system:"` prefix → `is_reserved_source` misses → the forge survives | **High** | Low | Pin (v) asserts reservation + `classify` fail-closed; source-level assert on the constant's prefix. |
| R3 | A genuine live wake's non-empty source is clobbered by the reserved literal (genuine path breaks) | **High** | Medium | Pin: non-empty `head.source` delivers verbatim; the literal is only the **fallback** arm. |
| R4 | The `or "api"` shape survives at a third, un-swept site | High | Low | Pin (v) greps BOTH sweep sites for the bare shape; a repo-wide grep for `or "api"` is part of the P1 acceptance. |
| R5 | The AC-23b amendment is written as a **dishonest flip** (test edited to assert a property the code lacks) | **High** | Medium | The amendment must assert the **real-chain** forge-dead property; fabrication of window dicts is banned for the forge scenario. Reviewer gate on the diff. |
| R6 | The retired token leaks into a live refusal string / agent-visible doc surface | Medium | Medium | Repo-wide grep for `direct-provenance-insufficient` in the final tree; `instance_ops.py:45-46` module docstring amended in the same commit. |
| R7 | The audit-row containment is lost while retiring the class (the ledger stops counting) | High | Medium | The refusal path must still write `root_source=None` / `depth=None`; the commit path must still write `provenance_class`/`provenance_depth`/`root_source`/`root_message_id`. Pin (iii) asserts the refusal row's None values. |
| R8 | Env-fencing slip → a test run touches the live/prod DB | **High** | Low | Per-variable scrub only (`env -u POSTGRES_DB -u POSTGRES_USER …`); `env -u POSTGRES_*` is a silent NO-OP (house critical note). mktemp `FAKE_HOME` + escape guard; never resolve HOME-derived live paths. |
| R9 | The v0.27.2 stage is refused by the L1 tip-identity guard (HEAD ≠ `origin/latest` at stage time) | Medium | Medium | Re-fetch + re-verify tip identity immediately pre-stage; a re-stage from a non-tip tree is REFUSED by design (exit 78) — never use an override. |
| R10 | A reviewer reads the fix as "the gate got weaker" | Medium | Medium | The plan's §Honesty Clause travels with the PR description; Change 1 is the compensating control and it lands first. **Q5 adjudication (the honest answer):** the REST widening is a **disclosed residual, NOT a regression** — the retired refusal was (1) explicit D-18 design intent ("refused ON PURPOSE", `instance_ops.py:49-56`/`:374-382`), (2) **vacuous** against the actual exploit (the delegated shape of the same REST forge was ALREADY admitted pre-DF-4), and (3) simultaneously the cause of defect B. The release notes state plainly that the D-18 "direct refused on purpose" property is **retired BY DESIGN**, with the compensating control stack named (scopes / allow-list / named-target confirmation / D-16 budget / two-tier audit / kill switch). |

---

## Honesty Clause (must ship with the change and with the release notes)

DF-4 closes the **SWEEP-MINTED** class of the residual. It does **not** close the residual wholesale:

1. **The REST-ingress class remains OPEN.** `daemon/routers/jobs_crud.py:597` takes a user-suppliable body `source` (defaulting to `"api"`) and `daemon/routers/messages.py:691` hard-codes `source="api"`. Both are user-origination surfaces that are genuine-by-design for the chat lane but supply a user-origin claim without an authenticated user ancestor. Filed as **critical note `257488dc`** (OAuth2 R3). DF-4 must **disclose** this, not claim total closure.
2. **The sweep fallback is structurally unreachable on LIVE for promote-kind.** Factor-2 (`upgrade_tools.py:3318-3366`) precedes the arm, so on live the arm carries a genuine source. The **closed exposure** is therefore: (a) non-live arms, (b) the durable `original_source="api"` write, (c) the env-flip 60-minute hazard. Do not claim "the forge is dead on live".
3. **Post-Change-2, a window is admitted on liveness alone.** A window forged through a surface **other than** the sweep (i.e. REST ingress, per (1)) still reaches the gate as a live window and is admitted. The class carries no trust signal, so the gate cannot distinguish it. This is the *disclosed* consequence of Change 2 and is bounded by the D-16 budget + the audit ledger. **Mandatory release-note wording, quoted verbatim from this clause: "D-18's direct-refusal is retired BY DESIGN; a REST-forged direct window is now admitted — that is the disclosed residual, bounded by the named compensating controls"** (the named compensating controls = scopes / acting-instance allow-list / named-target confirmation / D-16 budget / two-tier audit / kill switch).
4. **The AC-23b amendment's closure claim is scoped to the sweep-minted class.** Its docstring must carry the boundary, and the nest-ops retraction trace (`nest-ops/decisions.md:666-684`) remains the authoritative history — DF-4 appends, it does not rewrite.
5. **The defer-resend lane is a THIRD same-class mint surface and remains OPEN (A2 — leader decision: DISCLOSE-ONLY).** `daemon/services/job_queue_service.py:2608` (`resend_deferred_foreground`) hard-codes `source="api"` on re-enqueue → a **fresh** DIRECT window minted **at resend time**, with no concurrent human turn — the same class as the sweep forge. Propagators carrying `JobItem.source` verbatim: `dead_letter_service.py:153`/`:290`, `job_queue_service.py:2816`, `job_feedback_observer.py:4862`. Post-P2 this admits. **DF-4 does NOT fix it** — the fix (carry `job.source` through the defer path) is a behaviour change for its own gated lane. Routed as **DF-5** (§Routed Follow-ups).
6. **The REST widening is a disclosed residual, NOT a regression (Q5).** The retired refusal was explicit D-18 design intent, was **vacuous** against the delegated exploit (the delegated shape of the same REST forge was ALREADY admitted pre-DF-4), and was simultaneously the cause of defect B. Compensating controls for the newly-admitted DIRECT shape: scopes / acting-instance allow-list / named-target confirmation / D-16 budget (protected 1/24h, fleet 3/24h — `instance_ops.py:147`/`:149`/`:602-623`) / two-tier audit / `ENSEMBLE_INSTANCE_OPS_ENABLED` kill switch.
7. **The `is_system_origin` dispatch flip (A5) and the promote-arm operator-injection delta (BLOCKING #1).** Post-P1 the wake takes the internal lane (`instance_messaging.py:3490`); `dispatch_source` resolves from `instance_metadata.original_source` (`:3500`) rather than the literal `"api"`, and external dispatch may become **real** (`daemon/sources/dispatcher.py:693-695` — today `"api"` has no `":"` so the dispatch is skipped as a no-op). **Additionally, the wake's MESSAGE IDENTITY flips:** the `system:` literal is stamped `injected_message=True` (`instance_messaging.py:400-424` `_stamped_additional_kwargs`, applied at `:587`) where `"api"` produced a bare HumanMessage. Three effects: **(i)** excluded from the attestation ladder (`attestation_scanner.py:315-444`) — no longer advances the last-real-user-message anchor, renders as SECTION U (`attestation_resolver_activation.py:1297-1325`); **(ii)** no longer resets the durable repair budget (`daemon/graph.py:2138-2150`); **(iii)** compacted as an injected note rather than a user message (`daemon/_content_hardening.py:141-160`/`:216-224`). **This is the same shape the restart-kind wake already has in production** — precedented and acceptable, but a real behaviour delta. Disclosed in the release notes; pinned in P3 (pin (vi) asserts the `injected_message` stamp shape).
8. **The `source: str = "api"` default-parameter surfaces (A12)** are a latent re-inversion class, one forgotten kwarg away. All **43** `enqueue_message*` call sites pass `source=` explicitly today (AST census, 0 omissions — denominator corrected 2026-10-22; see the addendum ERRATUM) — but the R4 grep shape (`or "api"`) does **not** catch a default. Routed, not fixed here.

---

## Routed Follow-ups (NOT DF-4 — separate commissions)

| # | Lane | Scope | Why deferred |
|---|------|-------|---------------|
| **DF-5** | **DF-5 — job-queue defer-resend source mint** | `daemon/services/job_queue_service.py:2608` — carry `job.source` through `resend_deferred_foreground` instead of hard-coding `"api"`; align the propagators (`dead_letter_service.py:153`/`:290`, `job_queue_service.py:2816`, `job_feedback_observer.py:4862`). | Leader decision: **DISCLOSE-ONLY** in DF-4. It is a **behaviour change** on a live re-enqueue path and needs its own gated lane. Disclosed in the §Honesty Clause item 5. |
| **A12** | **A12 default-flip lane** | Flip the `source: str = "api"` defaults to a reserved literal (per the `auto_dispatch_fallback` precedent). **Full enumeration (N4):** `daemon/manager.py:9442`/`:9505`, `daemon/services/instance_messaging.py:2523`/`:2699`, **`daemon/services/job_queue_service.py:1348`**, and **`daemon/repositories/job_queue/repository.py:286`**/**`:382`** (the last three are **latent — not live** today, but the same class). Add a source-level pin that no `enqueue_message*` call site omits `source=` (AST census: 43 calls, 0 omissions). | Scheduled future lane. The R4 grep shape (`or "api"`) does not catch a default-parameter surface. **No code change in DF-4.** |
| **A13** | **A13 — `instance_ops:` prefix registration** | Register the `instance_ops:{caller}` notify lane in `RESERVED_SOURCE_PREFIXES` — today it fails closed only as "unregistered", not as reserved. **True source-literal lines (grep-verified): `daemon/tools/instance_ops.py:968`** (`instance_reclaim`), **`:1114`** and **`:1119`** (`instance_nudge`) — all three are `source=f"instance_ops:{caller_instance_id or 'unknown'}"`. | Later lane, low. Registering a family is a global constant change and belongs to its own reviewed commit. **No code change in DF-4.** |
| **F7** | **Cosmetic citation-drift lane** | `daemon/routers/jobs_crud.py:555` and `daemon/constants.py:122` cite `daemon/sources/registry.py:857`; the live chat-source mint site is at `registry.py:~1056-1057`. | **Deferred out of P4 by leader decision (F7)** — cosmetic, in files outside the DF-4 surface. **No edit in DF-4.** |
| **257488dc** | REST-ingress class (OAuth2 R3) | `daemon/routers/jobs_crud.py:597` (user-suppliable body `source`) and `daemon/routers/messages.py:691` (hard-coded `"api"`). | Already filed as a critical note. DF-4 discloses it; it does not close it. |
| — | House-note anchor correction | The critical note's `:1305-1317` anchor for the re-stamp defect is **stale** (those lines are the grace-pass handler at `60d7daaf4`; the defect is at `sweep:1698-1709`). | A note-edit, not a code change; outside the DF-4 file surface. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | No sweep site retains the bare `or "api"` fallback | Source grep over `daemon/services/upgrade_journal_sweep.py` | 0 hits at both sites |
| 2 | The new constant is reserved and classifies fail-closed | New unit pin (v) | `is_reserved_source(lit)` is True; `classify_user_origin(lit)` → ok=False |
| 3 | A genuine operator-direct protected act is **admitted** | New unit pin (i) | Act proceeds; audit row `provenance_class="direct"`, `root_source=<genuine>`, **`provenance_depth IS NULL`** (F2: the write path coerces `verdict.depth or None` → 0 becomes NULL); the **verdict-level** `depth == 0` is asserted **separately** |
| 4 | A genuine delegated protected act is **admitted** | New unit pin (ii) | Act proceeds; audit row `provenance_class="inherited"`, `depth≥1` (truthy — survives the coercion), `root_source=<genuine>` |
| 5 | The sweep-forged chain is **refused** end-to-end | New e2e pin (iii) — REAL chain | `reason=no-user-provenance`; audit refusal row `root_source=None`, `depth=None`; no direct window minted on the woken instance |
| 6 | Neither gate predicate keys on `pclass` | New source pin (iv) | 0 occurrences of `pclass` in either gate predicate block; `pclass` appears only in observability contexts |
| 7 | Pins are mutation-meaningful | Revert Change 1's literal → pins (iii)+(v) RED; restore `pclass`-keying → pins **(i)+(iv)** RED (**F3: pin (ii) stays GREEN under this mutation**); break the inherit mint → pin **(ii)** RED | Every mutation observed RED, then GREEN on restore; **every pin (i)–(vi) has a RED leg** |
| 8 | The shipped alias/wiring pins stay GREEN (**N1** — cited individually, not as a range that would swallow the amended test) | `tests/unit/test_instance_ops_registration.py` — the **six GREEN tests individually**: `:230` `test_act_module_imports_the_canonical_helper`, `:240` `test_no_local_reimplementation`, `:251` `test_the_alias_block_still_resolves_from_upgrade_tools`, `:264` `test_t0a_slice_untouched_by_the_move`, `:301` `test_verdict_token_vocabulary`, `:308` `test_pclass_domain_is_direct_or_inherited` — **all GREEN, unmodified** | `:316-328` (`test_gate_refusal_token_mapping`) is **AMENDED in P2** (F1b) — it is **not** in the GREEN set |
| 9 | The audit ledger rows are written on both paths | Real-PostgreSQL run | Refusal row AND commit row both present with the four provenance fields |
| 10 | Staged artifact integrity + release line | v0.27.2 stage + independent verification | sha 3-way match; migration delta EMPTY vs v0.27.1 (107 == 107); `rollback_safe=true` source=explicit; **NO promote** |
| 11 | Every residual mint surface is either closed or **disclosed** | Repo-wide mint-path census (Q1: single-writer, 2 sweep sites, **43** call sites, 0 omissions — denominator corrected 2026-10-22; see the addendum ERRATUM) cross-checked against the §Honesty Clause | Every open class named with an anchor + a routed lane; **zero** undisclosed same-class mint lanes |
| 12 | The release notes state the honest post-DF-4 position | Reviewer reads the release notes (**N8** — the quoted wording is mandatory) | **Mandatory wording, quoted VERBATIM from Honesty ¶3: "D-18's direct-refusal is retired BY DESIGN; a REST-forged direct window is now admitted — that is the disclosed residual, bounded by the named compensating controls"** — plus: the promote-arm **operator-injection delta** (all three effects: attestation-ladder exclusion, no repair-budget reset, compaction-as-injected-note — with anchors), stated as matching the restart-kind shape already in production; REST-ingress + defer-resend named as OPEN |

---

## Open Questions

**None blocking — all 13 architecture amendments are applied, and the two previously-deferred items are now decided by the leader.** The remaining items are **recorded**, not open:

1. **A2 / A12 / A13 — DECIDED (leader).** A2 = DISCLOSE-ONLY (defer-resend stays open, routed as DF-5); A12 = scheduled future lane (default-flip); A13 = later low-priority lane (`instance_ops:` prefix). **No code change in DF-4** for any of the three.
2. **The `registry.py:857` citation drift** is optional/low-priority in P4. If the leader prefers a zero-doc-drift release, P4's drift sub-task can be deferred without affecting the closure.
3. **REST-ingress (`257488dc`)** needs its own commission — DF-4's release notes must carry the disclosure, and DF-4 must not be read as having closed it.
