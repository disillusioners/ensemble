# Implementation Plan: DF-4 — AGENT-ORIGIN Provenance Gate Closure

Date: 2026-10-21
Companion: `plan-overview.md` (same directory) — objective, verified ground truth, scope, risks, honesty clause.
Worktree: `/home/nea/ensemble-src-wt-df4` @ `60d7daaf4` on branch `fix/df4-provenance-gate`.
Release line: **v0.27.2**, stage only.

---

## Execution Contract

- **One developer instance** executes P1 → P2 → P3 → P4, **one commit per phase**.
- **The implementing lane commits.** This planning lane writes only the plan files.
- **All reads/writes use absolute paths inside `/home/nea/ensemble-src-wt-df4`.** NEVER touch `/home/nea/ensemble-src` (shared checkout, another lane's WIP, stale nest-ops copies).
- **Daemon-only.** No TypeScript, no frontend source, no FE build.
- **Env-fenced.** See §Env-Fencing Spec — mandatory for every test that boots anything.
- **Do not** move `daemon/tools/provenance.py` — the freeze is **relocation/code**, NOT a file ban; **docstring-only** edits inside it are **authorised** (A4: `:49-54`, `:119`) — nor the 6-name alias block at `daemon/tools/upgrade_tools.py:1289-1296`.
- **Do not** change: `classify_user_origin`'s arm set (`"api"` STAYS armed); `system_upgrade`'s factor-2 read (`upgrade_tools.py:3318-3366`); `system_restart`'s gate (`upgrade_tools.py:2720-2730`); `stamp_user_origin_window`'s bytes (`manager.py:4850-4912`).
- **DF-4 file surface (F7 — widened by leader decision):** the claimed surface is `daemon/tools/provenance.py`, `daemon/tools/instance_ops.py`, `daemon/services/instance_ops_service.py`, `daemon/services/upgrade_journal_sweep.py`, `daemon/manager.py`, `daemon/tools/upgrade_journal.py`, `daemon/tools/upgrade_tools.py` — **plus `daemon/repositories/instance_ops/models.py`** (its `provenance_class` docstring at `:78` is the provenance field's own docstring; **docstring-only** edit). `scripts/upgrade/**` remains OUT of scope (the release-artifact provenance gate is a different provenance surface).

**Breaking any unpinned constraint above is a DEFECT, not a style choice.** Each is listed in §Pin Matrix with the test that pins it.

**Freeze restatement (Q2, adopted verbatim):** *the freeze protects the five enumerated semantic surfaces; the DF-4 change set touches zero bytes of all five; the sweep file must change because the defect lives there.* The freeze is **per-surface, not per-file**.

---

## Phase Table

| Phase | Name | Objective | Files | Coupling | Status |
|---|---|---|---|---|---|
| P1 | Sweep reserved-literal swap | The sweep can no longer forge a user-origin root | `daemon/tools/upgrade_journal.py`, `daemon/services/upgrade_journal_sweep.py`, 1 new test file, **A1 routing test** (build-breaking) | tight with P2 | pending |
| P2 | Gate predicate retirement | The protected-target gate keys on liveness, not class | `daemon/tools/instance_ops.py` (gate + 3 agent-visible surfaces + module docstring) + **3 test amendments** (AC-23a, token-map, AC-16) | tight with P1 (lands second) | pending |
| P3 | Pin amendments + real-chain e2e | The closure proof, in both directions, mutation-proven | 2 test amendments (AC-23b, journal docstring) + 2 new test files + pin (vi) | loose with P1/P2; consumes both | pending |
| P4 | Doc amendments + comment drift | The retracted D-18 prose is corrected, not rewritten | nest-ops docs (list is authoritative) + 4 false-claim doc sites + 2 comment sites | independent | pending |

*F10: the per-phase file lists are authoritative; the counts above are indicative only.*

**Hard ordering: P1 merges before P2.** Change 2 alone would newly ADMIT the sweep-forged DIRECT window. P3 may be developed alongside P1/P2 but may not be merged before both.

---

## P1 — Sweep Reserved-Literal Swap

### Objective
The sweep's delivery source for a wake armed with **no** user origin becomes a reserved internal literal instead of the user-origin literal `"api"`. `classify_user_origin` then fails it closed and the dispatch funnel POPs the window rather than stamping one.

### Files

| File | Change |
|---|---|
| `daemon/tools/upgrade_journal.py` | **NEW constant** beside `RESTART_WAKE_SOURCE` (`:1177`). **Q3-confirmed name/value:** `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"`, **module-level**. |
| `daemon/services/upgrade_journal_sweep.py:1209` | `else (wake.source or "api")` → `else (wake.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)` |
| `daemon/services/upgrade_journal_sweep.py:1734` | `else (head.source or "api")` → `else (head.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)` |
| `daemon/services/upgrade_journal_sweep.py:1203-1205` | **F8 — twin comment.** The ari-fallback arm's comment ("same reserved-lane rule as the direct deliver path — restart-kind wakes NEVER ride a user source, even on the ari fall-back") carries the **same silence** as the `:1734` twin. Amend it too: the non-restart fallback now also uses the reserved lane. |
| `daemon/services/upgrade_journal_sweep.py:1726-1730` | Update the stale comment: it claims restart-kind wakes "never ride a user source" — that remains TRUE for the restart arm, but the comment does not mention the non-restart fallback arm's forged `"api"`. Amend to state that the non-restart fallback now also uses the reserved lane. |
| `tests/unit/tools/test_df4_sweep_reserved_fallback.py` | **NEW** — pin (v) (see §Pin Matrix). |
| `tests/job_queue/test_post_restart_arm_notify_routing.py:203-221` | **A1 — BUILD-BREAKING, MUST land in the P1 commit.** `test_empty_source_uses_api_sentinel` asserts `kwargs["source"] == "api"` → **fails post-P1**. Re-point to assert the reserved literal (not `"api"`), `is_reserved_source(lit) is True`, and `classify_user_origin(lit)` is not user-origin. Reword docstrings `:13-18` (module) and `:129-134` (class). **This is a DIFFERENT file from the `tests/unit/tools/` journal test amended in P3** — both are required, in their respective phases. |

### Commit-content boundary (A11) — what the P1 commit contains

**P1 commit = the swap + the constant + pin (v) + the A1 routing-test amendment. NOTHING ELSE.**
Pin **(iii) must NOT land in the P1 commit** — it needs P2's ok-keyed gate to exist, and the **P1-only intermediate must stay suite-green**. Pin (iii) lands in **P3**. The A1 amendment, by contrast, asserts **P1's own** behaviour, so the suite is red without it — it is mandatory in P1.

### Commit-content boundary (F1) — what the P2 commit contains

**House rule (F1): a phase commit carries the amendments that pin its own behaviour.** A phase commit that lands RED is a defect.

**P2 commit = the gate deletion + the A3 agent-visible surfaces + the AC-23a amendment + the token-map amendment + the AC-16 amendment.**
- **AC-23a** (`tests/unit/test_instance_ops_behaviour.py:289-299`) — **MOVED from P3 into P2.** It asserts `reason=direct-provenance-insufficient` (`:298`) and `fleet.terminated == []` (`:299`); P2 deletes the branch at `instance_ops.py:369-384`, so both fail.
- **Token map** (`tests/unit/test_instance_ops_registration.py:316-328`) — **MOVED from P3 into P2.** It references the retired token.
- **AC-16** (`tests/unit/test_instance_ops_behaviour.py:605`) — **NEW, in P2.** It asserts `"INHERITED" in term`; the A3 rewrite of `instance_ops.py:810-815` is a P2 change and breaks it.
- **AC-23b** (`behaviour.py:301`) — **STAYS in P3.** The council verified it is **green at the P2 commit** (a genuine inherited window admits before and after P2).

### Q3 correction note (restate in the P1 commit message)

`RESTART_WAKE_SOURCE` does **NOT** feed `_settle_restart_audit` (no reference; audit rows are handled independently at `daemon/tools/upgrade_journal.py:454+`, `daemon/tools/upgrade_tools.py:2353`/`:2776`/`:2865`). Its only non-sweep consumer is the hard-coded `"wake_source"` field render at `daemon/tools/upgrade_tools.py:2924`. The two literals are **mutually exclusive arms of the same ternary** — no collision either way.

### Import note
The sweep module must import the new constant from `daemon.tools.upgrade_journal` — the same import surface it already uses for `RESTART_WAKE_SOURCE`. Verify the existing import statement before editing; do not introduce a second import path.

### Acceptance criteria

1. `grep -n 'or "api"' daemon/services/upgrade_journal_sweep.py` → **0 hits** (both sites gone).
2. The new constant's value starts with `"system:"` — asserted by source inspection in the new test.
3. `is_reserved_source(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)` is **True**.
4. `classify_user_origin(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)` → ok **False** (fail closed).
5. **A non-empty source still delivers VERBATIM.** The fallback is the `else` of a truthiness test — a non-empty `head.source` must reach `enqueue_message(source=head.source)` unchanged. Pin this with a non-empty-source case.
6. The constant is declared at MODULE level in `daemon/tools/upgrade_journal.py` (importable), not nested in a function.
7. The repo-wide grep for the `or "api"` shape over `daemon/` returns **no** sweep-site instance (R4).
8. `tests/unit/tools/test_agent_restart.py:122` and `:139` remain GREEN (the R18 precedent is untouched).
9. `tests/unit/tools/test_upgrade_journal.py:2289-2315` remains GREEN (the arm set is untouched).
10. **A1 acceptance:** `tests/job_queue/test_post_restart_arm_notify_routing.py:203-221` is re-pointed and GREEN; it asserts the reserved literal, `is_reserved_source(lit) is True`, and classify-not-user-origin. Its `manager.stamp_user_origin_window.assert_not_called()` assertion (`:221`) is retained. The **whole `tests/job_queue/` pack is GREEN** — the P1 commit is suite-green on its own.
11. The constant's name and value are **exactly** `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE` / `"system:sweep_no_user_origin_wake"` (Q3) — no variant spellings anywhere in the tree.

### Notes
The reserved literal's classification path: `is_reserved_source` (`daemon/constants.py:980`) matches the `"system:"` prefix family (`constants.py:946`; the `"system:"` entry at `:950`) → `classify_user_origin` returns a reserved-internal verdict → the funnel's `else` branch POPs the window (`manager.py:4905-4906`). **Do not add a new reserved-prefix family** — the existing `"system:"` family is the house mechanism.

---

## P2 — Gate Predicate Retirement

### Objective
`instance_ops._protected_gate` admits any live user-origin window (direct or inherited) and refuses only when no live window exists. The class-keyed branch and the retired token are removed; the audit-row containment survives untouched.

### Files

| File | Change |
|---|---|
| `daemon/tools/instance_ops.py:369-384` | **Delete** the `if verdict.pclass != "inherited":` branch and its `_refusal(..., "direct-provenance-insufficient", ...)` call. The `if not verdict.ok:` refusal at `:362` (`"no-user-provenance"`) remains the sole provenance refusal. |
| `daemon/tools/instance_ops.py:45-46` | Module docstring token chain: remove `direct-provenance-insufficient` from the enumerated refusal vocabulary. |
| `daemon/tools/instance_ops.py:49-63` | **A3 — agent-visible surface that goes FALSE post-P2.** The D-18 "THE PROVENANCE GATE IS A SPEED BUMP, NOT A PROOF" paragraph asserts the class-keyed gate "CLOSES the direct-window-on-the-acting-instance forgery class" — that claim is now false (and the Q1 reframing shows the delegated forge was never closed by it). Also correct the stale critical-note citation: it says `f4cf9c4f` / "DF-4 owns the root fix" — DF-4 **is** this fix; the note is superseded by this plan. |
| `daemon/tools/instance_ops.py:116-121` | **A3 — agent-visible surface that goes FALSE post-P2.** `CATEGORY_DOC` states a protected target requires a window "that was INHERITED through a delegation chain" and that "A window minted directly on THIS instance is refused on purpose". Both become false. This text **feeds the tool registry** (`daemon/tools/_tool_registry.py:1511`/`:1519`) — it is agent-visible, not a private comment. |
| `daemon/tools/instance_ops.py:810-815` | **A3 — agent-visible surface that goes FALSE post-P2.** `instance_terminate._full_doc_` item 5 requires the window "to be INHERITED (`pclass == "inherited"`)" and states "A DIRECT window on this session is refused ON PURPOSE — route through the leader". Rewrite to the liveness-only rule. |
| `daemon/tools/instance_ops.py:688-694` + `:569`/`:629`/`:669-670`/`:699`/`:707`/`:746`/`:755`/`:773`/`:780` | **NO CHANGE** — the audit-row write sites (see §Audit Containment) must continue to carry `provenance_class` / `provenance_depth` / `root_source` / `root_message_id` on BOTH the admit and refuse paths. The **commit** write is `:746`/`:755` (`write_audit_and_act`) — **not** `:637` (a budget-refusal row) and **not** `:690` (the `prov_fields` dict literal). |
| `tests/unit/test_instance_ops_behaviour.py:289-299` | **F1(a) — AMEND in the P2 commit (moved from P3).** `test_direct_window_on_acting_instance_refused` (AC-23a) asserts `reason=direct-provenance-insufficient` (`:298`) and `fleet.terminated == []` (`:299`). P2 deletes the branch at `instance_ops.py:369-384`, so a genuine direct window now **ADMITS** and **both assertions fail** — leaving this in P3 would land the **P2 commit RED**. Re-point honestly to the admitting behaviour (see §Pin Matrix). |
| `tests/unit/test_instance_ops_registration.py:316-328` | **F1(b) — AMEND in the P2 commit (moved from P3).** `test_gate_refusal_token_mapping` references the retired token → RED the moment P2 lands. Re-point to "exactly one provenance refusal token". |
| `tests/unit/test_instance_ops_behaviour.py:605` | **F1(c) — NEW AC-16 amendment, in the P2 commit.** `test_terminate_doc_states_the_current_semantics` asserts `"INHERITED" in term` (`# D-18 current semantics`). The A3 rewrite of `instance_ops.py:810-815` is a **P2** change and removes the class-keyed wording → this assertion goes RED. Re-point to the liveness-only semantics the new `_full_doc_` states. **This test is listed nowhere in the plan today** — it is the third P2-carried amendment. |

### Audit containment (A6 re-anchor; F6-corrected line numbers)

The sites that carry the four provenance fields at `60d7daaf4` (`daemon/tools/instance_ops.py`): refusal `:569` (kwargs `:577-580`) · budget refusal `:629` (kwargs `:637-640`) · cascade/orphan `:669-670` (**never** carried them) · `prov_fields` **assembly** `:688-694` · preview `:699`/`:707` · **COMMIT `:746`/`:755`** (`write_audit_and_act`) · service-refusal `:773`/`:780` · observability read `:724-727`. All are **required to survive**. No column is orphaned: `pclass` survives as a verdict field (`daemon/tools/provenance.py:161`/`:195`/`:229`/`:271`); the schema is untouched (`daemon/repositories/instance_ops/models.py:154-169`; migration `20261021_120000`).

### Release-notes statement (A3/Q5 — mandatory wording)

The release notes state plainly: **the D-18 "direct refused on purpose" property is RETIRED BY DESIGN.** The compensating controls for the newly-admitted DIRECT shape are: scopes · acting-instance allow-list · named-target confirmation (`confirmed_target_id`) · D-16 budget (protected 1/24h, fleet 3/24h) · two-tier audit ledger · `ENSEMBLE_INSTANCE_OPS_ENABLED` kill switch. This is a **disclosed residual, not a regression** (Q5) — see `plan-overview.md` §Honesty Clause items 6 and 7.

### Post-fix gate shape (target)
The gate reduces to, in order: (1) the confirmed-target check (`:345-357`, unchanged); (2) `if not verdict.ok:` → `_refusal(label, "no-user-provenance", ...)` (`:362`, unchanged text apart from any wording cleanup); (3) `return None` (admit).

The post-fix predicate is **predicate-identical** to the restart lane's gate at `daemon/tools/upgrade_tools.py:2720-2730`, which keys on `provenance.ok`. Two lanes, one predicate.

### Rationale that must travel with the change (Q1 reframing + root insight)

The **old** gate never blocked the delegated forge: `_inherit_provenance_window` copies the root **without inspecting the root's own provenance** (`manager.py:4960-4984`), so a forged DIRECT on a spawner propagated to an INHERITED window that `:369` **admitted** (the module docstring concedes it — `instance_ops.py:53-56`). The old gate's *entire* real guarantee was "no DIRECT window, genuine **or** forged, arms a protected act" — and that guarantee **is** defect B. Post-P1, a live window's root was classified user-origin **at mint time** for BOTH classes, so the class carries **zero** trust signal. It survives as **observability only** (the audit rows) — and becomes *more* meaningful: it answers "which window shape authorised this act?" on one ledger.

**Q1 verdict:** Change 2 **neutrally re-scopes** the gate — strictly strengthening the delegated-forgery surface (the one the old gate actually admitted) in conjunction with P1, and strictly weakening exactly one narrow shape (DIRECT on the acting instance) from refused to admitted, disclosed and bounded. **Change 2 ALONE is strictly weakening — P1-before-P2 is load-bearing (Q4).**

### Acceptance criteria

1. `"direct-provenance-insufficient"` appears **nowhere** in the final tree (repo-wide grep over `daemon/` and `tests/`).
2. `"no-user-provenance"` remains, and is now the **only** provenance refusal token.
3. The gate no longer reads `verdict.pclass` — asserted by the new anti-re-inversion pin (§Pin Matrix, pin (iv)).
4. A genuine DIRECT window on the acting instance **admits** (pin (i)).
5. A genuine INHERITED window **admits** (pin (ii)).
6. `ok=False` still refuses with `reason=no-user-provenance` — `tests/unit/test_instance_ops_behaviour.py:281` stays GREEN unmodified.
7. The audit refusal row still records `root_source=None` / `depth=None` on the refuse path (asserted in pin (iii)).
8. The audit **commit** row still records `provenance_class` / `provenance_depth` / `root_source` / `root_message_id` (commit write = `instance_ops.py:746`/`:755`; budget-refusal row `:629`/`:637-640`; preview `:699`/`:707`; service-refusal `:773`/`:780`; assembly `:688-694`; the cascade/orphan row `:669-670` never carried them and must not start to).
9. `tests/unit/test_instance_ops_registration.py`: **`:230-249` + `:301` + `:308` GREEN unmodified; `:316` re-pointed in this commit.**
10. `daemon/tools/upgrade_tools.py:2720-2730` is **byte-unchanged**.
11. **A3 acceptance:** the three agent-visible surfaces (`:49-63`, `:116-121`, `:810-815`) no longer assert the class-keyed rule, and the tool-registry text fed from `daemon/tools/_tool_registry.py:1511`/`:1519` reflects the liveness-only rule. The stale `f4cf9c4f` "DF-4 owns the root fix" citation is corrected.
12. **F1 acceptance:** the **P2 commit is GREEN on its own** — the three amendments it carries (AC-23a `behaviour.py:289-299`, token-map `registration.py:316-328`, AC-16 `behaviour.py:605`) are all landed in this commit, so no P2-carried assertion is left RED.

---

## P3 — Pin Amendments + Real-Chain e2e

### Objective
Turn the closure proof into executable, mutation-meaningful pins — in BOTH directions (refuse the forgery, admit the genuine paths) — and amend the four AC pins that encode the old behaviour honestly.

### Files

| File | Change |
|---|---|
| `tests/unit/test_instance_ops_behaviour.py:301` | **AMEND** `test_inherited_window_proceeds_ac23b` (AC-23b) — the closure proof. See §AC-23b Amendment Spec. **STAYS in P3:** the council verified it is **green at the P2 commit** (a genuine inherited window admits before and after P2). |
| `tests/unit/tools/test_post_restart_arm_notify_journal.py:839-845` | **AMEND** the docstring: "_capture_user_source returns '' sentinel; Phase 2 falls back to 'api'" → the fallback is now the reserved literal, not `"api"`. The assertions at `:845` / `:856` (`== ""`) stay as they are. |
| `tests/unit/tools/test_df4_sweep_reserved_fallback.py` | **NEW** — pins (v) + the P1 acceptance set (created in P1, extended in P3). |
| `tests/postgres/test_df4_provenance_closure.py` | **NEW** — pin (iii), the REAL-chain sweep-forge-dead e2e. Module-level `pytestmark = pytest.mark.postgres`; real `InstanceManager` over the `pg_engine` fixture (see §Marker policy + §Real-manager construction path). |

### New pin set (encode each explicitly)

| Pin | Name | What it asserts |
|---|---|---|
| **(i)** | `test_genuine_direct_window_admits_protected_act` | A DIRECT window minted by a **genuine** user-origin source on the **acting** instance → the protected act **proceeds**. Audit row: `provenance_class="direct"`, `root_source=<genuine>`, and **`provenance_depth IS NULL`** — **F2: the write path coerces `verdict.depth or None`, so `0` becomes NULL; asserting `depth == 0` on the row is unachievable.** The verdict-level depth is asserted **separately**: `verdict.depth == 0`. |
| **(ii)** | `test_genuine_inherited_window_admits_protected_act` | A REAL delegation chain from a genuine user-origin parent (`internal_agent:` prefix) → the child's inherited window → the act **proceeds**. Audit row: `provenance_class="inherited"`, `depth>=1` (**truthy — survives the `or None` coercion unchanged**), `root_source=<genuine>`. |
| **(iii)** | `test_sweep_forged_wake_is_refused_real_chain` | e2e through the **REAL** chain — a `PendingWake` armed with `source=""` → the **REAL** sweep enqueue → the **REAL** dispatch funnel → the **REAL** inherit path. Asserts: the delivery source is the new reserved literal; `classify` fails closed; **NO** direct window is minted on the woken instance; **NO** inherited window can be minted from it on a delegated child; the gate **REFUSES** with `reason=no-user-provenance`; the audit refusal row records `root_source=None` / `depth=None` (countability preserved). **NO fabricated window dicts** for this scenario. |
| **(iv)** | `test_no_gate_predicate_keys_on_pclass` | Source-level: NEITHER gate predicate — `daemon/tools/instance_ops.py::_protected_gate` NOR the restart-lane gate block (`daemon/tools/upgrade_tools.py:2720-2730`) — keys on `pclass`. The class may appear only in observability contexts (audit rows, observability reads). **This replaces the vacuous fixture-parity pin** that the original nest-ops plan specified and that was judged vacuous and never shipped — do NOT resurrect it as a function-to-itself comparison. |
| **(v)** | `test_sweep_reserved_fallback_is_reserved` | **F11 — assert the shape the sweep actually uses.** The constant is reserved (`is_reserved_source` True), `classify_user_origin` fails it closed, and **`or "api"` is ABSENT from BOTH sweep sites**. **Do NOT literal-transcribe the house precedent's `'else "api"' not in ...` form (`test_agent_restart.py:162`)** — that shape never occurs at the sweep sites (they use `or "api"`), so a transcribed form would be **vacuous**. The `or "api"` form is what P1-AC-#1 and SC#1 already use. |
| **(vi)** | `test_sweep_wake_takes_the_is_system_origin_lane` **(A5 + BLOCKING #1, NEW)** | Post-P1 the wake takes the `is_system_origin` lane (`daemon/services/instance_messaging.py:3490`) — `dispatch_source` resolves from `instance_metadata.original_source` (`:3500`) instead of the literal `"api"`, and the durable `original_source="api"` write (`:3524-3528`) no longer happens for these wakes. External dispatch may become **real** (`daemon/sources/dispatcher.py:693-695` — today `"api"` has no `":"` so the dispatch is skipped as a no-op). **BLOCKING #1 — the pin must ALSO assert the `injected_message` stamp shape:** the delivered wake's HumanMessage carries `additional_kwargs == {"injected_message": True, "source": <the reserved literal>}` (produced by `_stamped_additional_kwargs`, `instance_messaging.py:400-424`, applied at `:587`) — **not** a bare HumanMessage as `"api"` produced. Assert the **stamp shape**, which pins the disclosed behaviour rather than merely asserting it. The pin does **not** assert that an external dispatch occurred (that requires a live adapter — see the addendum's unverified residuals). |

### AC-23b Amendment Spec (the heart of the closure proof)

The commission requirement is two-sided: the gate must **REFUSE** the delegated forgery **AND** **ADMIT** the genuine operator path. A fix that only tightens the refusal leaves the genuine path broken — and the current defect is a **wrong-side ACCEPTANCE**, not only a wrong-side refusal.

The AC-23b test today (`tests/unit/test_instance_ops_behaviour.py:301`) fabricates a forged direct window on the spawner (`source="api"`) plus an inherited window on the acting instance, and asserts the act **ADMITS** — documented as "PINNED AS TRUTH" with a docstring forbidding a dishonest flip to a refusal assertion.

**The honest amendment:**

1. **Do NOT flip it to assert a refusal.** A fabricated `root_source="api"` inherited window is, post-P2, a *live* window — the gate admits it by design. Asserting a refusal there would certify a property the code does not have.
2. **Split the scenario.** The amended test asserts:
   - the **genuine** delegated chain (a genuine user-origin parent → the child inherits → the act admits, audit `class="inherited"`, `depth>=1`, `root_source=<genuine>`) — i.e. pin (ii); **and**
   - the **forged** chain is now **unreachable through the real sweep path** — i.e. pin (iii), the real-chain e2e, which asserts the refusal with `no-user-provenance` and `root_source=None`/`depth=None`.
3. **Carry the disclosure in the docstring.** The amended test must state, in prose, that a window forged through a **non-sweep** surface (REST ingress — `daemon/routers/jobs_crud.py:597`, `daemon/routers/messages.py:691`) remains reachable and admitted, and is tracked by critical note `257488dc`. The docstring must NOT claim total closure.

### Mutation-Proof Spec

Both mutations must be executed by the tester and observed, with results recorded in the tester evidence file:

| Mutation | Expectation |
|---|---|
| Revert P1's literal at `sweep:1209` (and `:1734`) from the reserved literal back to `"api"` | Pin **(iii)** turns **RED** (the forge re-mints a direct window; the gate admits). Pin **(v)** also **RED** (F11: the `or "api"` shape is back at both sites). |
| Restore the `verdict.pclass != "inherited"` predicate in `instance_ops.py:369` | Pins **(i)** and **(iv)** turn **RED** (the genuine direct path is refused again; the class-keying shape is back). **F3: pin (ii) stays GREEN under this mutation** — a genuine INHERITED window is admitted by design, so this mutation does **not** touch it. Do not claim otherwise. |
| **Dedicated pin-(ii) mutation leg (F3)** — break the inherit mint: e.g. make `_inherit_provenance_window` (`manager.py:4914-5000`) skip the copy, or force `ok=False` on the inherited verdict | Pin **(ii)** turns **RED** (the genuine delegated path no longer admits). This is the leg that proves pin (ii) is **non-vacuous** — without it, pin (ii) would have no mutation proving it can fail. |

Each mutation must be restored and the tree re-verified GREEN. A pin that does not turn RED under its mutation is **vacuous** — it must be re-authored, not waived. **Every pin (i)–(vi) must have a mutation leg that turns it RED**; the legs above cover (i), (ii), (iii), (iv), and (v) — add one for (vi) (e.g. force the `is_system_origin` lane selection back to the literal-`"api"` branch at `instance_messaging.py:3514`).

### e2e Real-Chain Spec (pin iii)

The forge scenario must be driven through the **real** order — spawn → enqueue → funnel stamp → inherit — because the defect lives in the **order**, not in a dict shape.

1. Arm a `PendingWake` with `source=""` (the verified sentinel at `daemon/tools/upgrade_journal.py:1127`).
2. Run the **real** sweep delivery path so the fallback arm is exercised (do not hand-build the `source=` argument).
3. Assert the delivered message's source equals the new reserved literal (and is NOT `"api"`).
4. Assert `classify_user_origin` on the delivered source → not user-origin.
5. **A8:** assert `stamp_user_origin_window` was **NOT CALLED** for the `""`-source wake (mirror `tests/job_queue/test_post_restart_arm_notify_routing.py:221`) — **not merely** that no window exists — and additionally assert no direct window exists on the woken instance after the funnel (`manager.py:4905-4906` POP path).
6. Delegate from that instance to a child (`internal_agent:` source) and assert **no** inherited window is minted on the child.
7. Invoke a protected-target act on the child → assert `reason=no-user-provenance`.
8. Assert the audit refusal row: `root_source=None`, `depth=None`.

**Negative controls (also required):**
- a genuine user-origin source on the same path → the window IS minted and the act admits (proves the test is not passing vacuously);
- the pre-fix tree (or an equivalent mutant) → the forge admits — this is the mutation leg above.

### MINIMAL-REAL SEAM spec (F4) — the vacuity guard for pin (iii). F6-corrected anchors: `_inherit_provenance_window` read confirmed at `manager.py:4914-5000`; POP at `:4905-4906`.

**Both house precedents for this area are FAKE harnesses** — a `MagicMock` manager (`tests/job_queue/test_post_restart_arm_notify_routing.py:111-122`) and a `FakeManager` + hand-written window dicts (`tests/unit/test_instance_ops_behaviour.py:125-131`). With a mock, e2e steps 6–7 (inherit + gate) **pass vacuously** — the exact failure mode this plan bans. **Pin (iii) is the most important pin in the set; it must not inherit that weakness.**

**Required seam (zero mocks along the whole path):**

| Step | Real component | Anchor |
|---|---|---|
| 1 | real `UpgradeJournalSweepService.sweep_wake_records` over a `tmp_path` journal, with a **real** `PendingWake` armed at `source=""` | `daemon/services/upgrade_journal_sweep.py` |
| 2 | capture the **delivered** source from the real enqueue call | sweep `:1200-1211` / `:1723-1736` |
| 3 | real `InstanceManager.stamp_user_origin_window` — assert the **POP** branch runs (no window minted) | `daemon/manager.py:4850`, POP at `:4905-4906` |
| 4 | real `_inherit_provenance_window` — assert no inherited window is minted on the delegated child | `daemon/manager.py:4914-5000` |
| 5 | real `_protected_gate` | `daemon/tools/instance_ops.py:369-384` |
| 6 | assertions read the **REAL** `_user_origin_windows` / `_inherited_provenance_windows` dicts | `daemon/manager.py` |

**Rules:**
- **ANY mock anywhere in this path INVALIDATES pin (iii).** A `MagicMock` manager, a `FakeManager`, or a hand-authored window dict is disqualifying — the same shapes the plan bans for the forge scenario.
- The window dicts are read **from the real manager**; they are never fabricated as fixtures.
- **File location: `tests/postgres/test_df4_provenance_closure.py`** — **corrected from F4's `tests/integration/`**: the pin requires **real PostgreSQL** (Env-Fencing §4) and the house real-PG convention is `tests/postgres/` with the `postgres` marker (see §Marker policy below). `tests/integration/` is the `integration` marker class — a different lane.
- The stub `manager` in the house precedent files may remain for their own narrow assertions, but **pin (iii) does not use them.**

### Marker policy + the mandatory tester command (N2) — a skipped pin is a failed gate

The house marker is **`pytest.mark.postgres`** (registered in `pyproject.toml:90` — *"PostgreSQL-only tests that require a live PostgreSQL server (run with `pytest -m postgres`)"*). **The default `addopts` is `-m 'not integration and not postgres and not slow'` (`pyproject.toml:94`) — so a `postgres`-marked pin is DESELECTED from the default suite.** That is precisely the risk: the most important pin could silently never run.

- **Marker:** module-level `pytestmark = pytest.mark.postgres` in `tests/postgres/test_df4_provenance_closure.py` (the house convention — e.g. `tests/postgres/test_attestation_live_descendants_pg_lca.py:80`).
- **Mandatory tester command** (verbatim from the house pattern, `tests/postgres/conftest.py:41-44` and `tests/postgres/test_attestation_live_descendants_pg_lca.py:31-41`) — **`--override-ini="addopts="` is REQUIRED**, it clears the default `-m 'not postgres'` deselection:
  ```
  pytest tests/postgres/ --override-ini="addopts=" -m postgres
  ```
  Plus the fence (Env-Fencing §4) — the scrub+set+assert rule applies to this invocation, and the PG env vars (`PG_TEST_HOST`/`PG_TEST_PORT`/`PG_TEST_DB`/`PG_TEST_USER`/`PG_TEST_PASSWORD`) are set explicitly, with `POSTGRES_*` unset.
- **Serial only:** `tests/postgres/conftest.py` auto-skips every postgres test when `PYTEST_XDIST_WORKER` is set (xdist guard) — run the pin **serially** (`-p no:xdist` or without `-n`).
- **THE GATE RULE: a skipped pin = a failed gate.** The tester must record, for pin (iii), the **collected-and-passed** count from the `pytest -m postgres` invocation — a `skipped` or `deselected` result for the pin is a **FAILED gate**, not a pass. The tester must show the pin's node id in the `pytest -m postgres` output, not merely in the default-suite output.

### Real-manager construction path (N3) — the one spot an implementer could produce the vacuous pin

**Ground it in the house real-PG pattern:** `tests/postgres/test_attestation_live_descendants_pg_lca.py` (module-level `pytestmark = pytest.mark.postgres` at `:80`; real repositories over the `pg_engine` fixture; documented invocation at `:31-41`).

**Required construction for pin (iii):**
1. **Real `InstanceManager`** — `daemon.manager.InstanceManager`, constructed with a real config, **not** a stub and **not** a `MagicMock`. Its `_user_origin_windows` / `_inherited_provenance_windows` must be the **real dicts** the production code writes to.
2. **Real repositories over the `pg_engine` fixture** from `tests/postgres/conftest.py` (`pg_engine` `:160`, `pg_session_factory` `:199`, `pg_repository_factory` `:220`, `pg_url` `:286`) — the manager's instance / message-queue / job repositories are PG-backed.
3. **Fenced env per Env-Fencing §4** — set `POSTGRES_*` **and** the `PG_TEST_*` vars explicitly, then **assert the resolved database is not `ensemble_prod` / `ensemble_dev_prod`** (inside the test, not only in the shell).
4. **tmp checkpoint dir** — the manager's LangGraph/checkpoint path must point at a `tmp_path`-scoped directory (never a live install path; never a HOME-derived path).
5. **The sweep's journal is a `tmp_path` fixture** — real `UpgradeJournalSweepService` over a tmp journal, with a real `PendingWake` armed at `source=""`.

**If no real-`InstanceManager` integration pattern can be reused**, the fallback is the **direct construction** above (real `InstanceManager(config)` + PG-backed repositories + fenced env + tmp checkpoint dir), **not** a hand-rolled fake manager. **A fake manager disqualifies the pin** — that is the vacuity this pin exists to prevent.

### Env-Fencing Spec (mandatory for every test that boots anything)

House critical notes — both are RED conventions, both have caused real incidents:

1. **Per-variable env scrub ONLY.** `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names). Use:
   ```
   env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>
   ```
   An ambient `POSTGRES_DB=ensemble_prod` staying visible is how a "fenced" run hits prod.
2. **NEVER resolve live-shaped HOME-derived paths.** Use `mktemp -d` for a `FAKE_HOME` and add an escape guard that asserts the resolved path is under the temp dir before any delete. (Incident 2026-10-12: two live-install deletions.)
3. The worktree `.env` is already fenced: `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`. Verify it before the run; do not rely on ambient values.
4. Use a **real PostgreSQL** instance for the `instance_ops_audit` two-tier ledger rows (the audit-row assertions are meaningless against a substitute). **F9 — the real-PG leg must SET, not only scrub:** the per-variable scrub *removes* `POSTGRES_*`, so the real-PG leg must **set them explicitly** (`POSTGRES_DB=ensemble_dev` + the matching `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_HOST`/`POSTGRES_PORT`) **and assert the resolved database is NOT `ensemble_prod` and NOT `ensemble_dev_prod`** before the run starts. Assert it **inside the test** (not only in the shell) so a mis-configured run fails loudly rather than writing to prod.
5. No test may touch the live daemon on `:9797` or the live DB. If a boot smoke is needed, isolate it in its own cgroup (a transient `systemd-run` unit), and tear down SIGTERM-only.

### Acceptance criteria (P3)

0. **A11:** pin **(iii)** lands **here**, in P3 — it must **not** be in the P1 commit (it needs P2's gate; the P1-only intermediate stays suite-green). The A1 routing-test amendment, by contrast, was mandatory in P1.
1. Pins (i)–(vi) all exist and PASS on the merged tree.
2. Both mutations observed RED, then GREEN on restore.
3. The amended AC-23a test — **landed in P2 (F1a)** — asserts **ADMIT** for the genuine direct path on the merged tree, and says so in prose.
4. The amended AC-23b test does **not** assert a refusal on a fabricated window, and its docstring carries the REST-ingress disclosure.
5. `tests/unit/test_instance_ops_registration.py:316-328` — **amended in P2 (F1b)** — no longer references the retired token on the merged tree.
6. `tests/unit/tools/test_post_restart_arm_notify_journal.py:839-845` docstring re-worded; assertions unchanged.
7. All GREEN pins from §Pin Matrix remain GREEN, unmodified.
8. Real-PostgreSQL audit rows verified for both the admit and refuse paths (commit row `:746`/`:755`, refusal row `:569`/`:577-580`).
9. No fabricated window dicts anywhere in the forge scenario.
10. **A5 + BLOCKING #1 acceptance:** the `is_system_origin` lane selection, the **absence of the durable `original_source="api"` write**, **and the `injected_message` stamp shape** at the delivered wake (`:587`) are asserted (pin (vi)). The release notes name the promote-arm **operator-injection delta** with all three effects and their anchors (attestation-ladder exclusion `attestation_scanner.py:315-444`; no repair-budget reset `daemon/graph.py:2138-2150`; compaction-as-injected-note `daemon/_content_hardening.py:141-160`/`:216-224`), stating it matches the restart-kind shape already in production.
11. **N2 acceptance:** the tester evidence shows the pin's node id **collected and passed** in the `pytest tests/postgres/ --override-ini="addopts=" -m postgres` invocation. A **skipped** or **deselected** pin is a **failed gate**.
12. **N3 acceptance:** the pin constructs a **real** `InstanceManager` with PG-backed repositories over the `pg_engine` fixture, a fenced env (asserted not-prod), and a tmp checkpoint dir — no `MagicMock`, no `FakeManager`, no hand-written window dicts.

---

## P4 — Doc Amendments + Comment Drift

### Objective
Correct the retracted D-18 prose in place with a bracketed correction — **do not rewrite history** — and fix the two comment drifts.

### Files

| File | Change |
|---|---|
| `.agents/shared/planning/nest-ops/plan-overview.md` §7.2 (the D-18 bullet at ~`:951-953`, the `(c) pclass == "inherited"` clause inside the "requires ALL THREE" list) | Append a short bracketed correction pointing at `.agents/shared/planning/df4-provenance-closure/plan-overview.md` and noting that the class-keyed clause was **retracted** and superseded by DF-4's liveness-only gate. **Read the WORKTREE copy** (committed at `60d7daaf4`) — NEVER `/home/nea/ensemble-src`. |
| `.agents/shared/planning/nest-ops/architecture-recommendation.md` (~`:167-190`, the "(c) R1 — does the promote-wake forge materially weaken this gate" section and its "The cheap guard: require `pclass == "inherited"`" recommendation) | Same bracketed correction. The recommendation text is historical analysis — it is **not** deleted, it is annotated. |
| `.agents/shared/planning/nest-ops/decisions.md:666-684` | **CITE, do not duplicate.** This is the authoritative 5-step retraction trace. The DF-4 docs reference it; they do not restate it. |
| `daemon/tools/upgrade_tools.py:1275` | Comment fix only: `# FIVE names moved` → the bound count is **six** (`_restart_provenance`, `_lookup_provenance_field`, `_capture_user_source`, `_capture_provenance_class`, `_ProvenanceVerdict`, `_EMPTY_PROVENANCE` — the block at `:1289-1296`). The comment at `:1275` names only five. **Fix the comment only** — do NOT touch the alias block itself. |
| `daemon/tools/provenance.py:49-54` | **A4 — docstring-only (carve-out authorised by Q2).** The "Known bounds" docstring asserts "Requiring `pclass == "inherited"` on a protected target CLOSES the direct-window-on-the-acting-instance forgery class" — false post-P2, and the Q1 reframing shows the delegated forge was never closed by it. Also drop the stale `f4cf9c4f` "(DF-4) for the root-fix lane" pointer. **Docstring text only — no code, no relocation, no signature change.** |
| `daemon/tools/provenance.py:119` | **A4 — docstring-only.** `capture_user_source` says "Returns `""` on absence (non-user-origin arm; **Phase 2 falls back to `"api"`** for routing)" — now false. Reword to the reserved literal. |
| `daemon/tools/upgrade_tools.py:1262` | **A4 — comment only.** The arm-time-capture comment says "non-user-origin arms leave the wake's source as "" (sentinel, **falls back to "api" in Phase 2**)". Reword. |
| `daemon/repositories/instance_ops/models.py:78` | **A4 — docstring only.** `provenance_class: "direct" \| "inherited" \| None (D-18).` → reword to "**OBSERVABILITY ONLY — not a gate input (D-18 superseded by DF-4)**". The field, column, and schema are unchanged. **F7 — the DF-4 file-surface claim is WIDENED to include this file** (it is the provenance field's own docstring); recorded in the Execution Contract. |
| `.agents/shared/planning/nest-ops/implementation-plan.md:643`/`:644`/`:660` | **A7 — bracketed correction.** `:643` states the token mapping "exact name + **direct** window ⇒ `direct-provenance-insufficient` (D-18)"; `:644` and `:660` carry the AC-10b vocabulary mapping (`ok=False` → `no-user-provenance`; `pclass="direct"` on a protected target → `direct-provenance-insufficient`) and the "neither lane can silently change semantics" claim. Append a bracketed correction pointing at DF-4. |
| `.agents/shared/planning/nest-ops/risk-register.md:21-25`/`:63-67` | **A7 — bracketed correction.** `:21` names the sweep `:1734`/`:1209` `or "api"` fallback as a known risk; `:63-67` re-scopes it. Annotate: the sweep class is closed by P1; the defer-resend class (DF-5) and the REST-ingress class (257488dc) remain OPEN. |
| `.agents/shared/planning/nest-ops/decisions.md:657` + `:137` | **A7 — bracketed correction.** `:657` is the `direct-provenance-insufficient` — "genuine or forged" line; `:137` is the D-18 index entry ("D-18 (`pclass == "inherited"` required for protected…)"). The authoritative 5-step retraction trace is `:666-684` — cite it, do not duplicate it. |
| `.agents/shared/planning/nest-ops/plan-overview.md:450` | **A7 — bracketed correction.** The refusal-token enumeration line (`confirmed-target-mismatch` · `no-user-provenance` · `direct-provenance-insufficient` · …). |
| `.agents/shared/planning/nest-ops/plan-overview.md:645-649`/`:679` | **A7 — bracketed correction.** The protected-target rule text, and the line noting the sweep `or "api"` fallback is "a separate lane, **NOT fixed here**". |
| `.agents/shared/planning/nest-ops/plan-overview.md:687-692`/`:694-699`/`:701-705` | **A7 — bracketed correction.** The D-18 "requires ALL THREE" rule block and its surrounding guarantees. |
| `.agents/shared/planning/nest-ops/plan-overview.md:1084-1085`/`:1097` | **A7 — bracketed correction.** The success-criteria / acceptance rows that encode the class-keyed rule. |
| `.agents/shared/planning/nest-ops/architecture-recommendation.md:272` | **A7 — bracketed correction.** The Q5 row whose recommendation reads "**require `pclass=="inherited"` for protected-target acts** — this closes the R1 promote-wake forge completely" and "Record the one refused legitimate case (direct HTTP operator message to the maintenancer)". Annotate with the Q1 reframing (the delegated forge was never closed by this rule) and DF-4's supersession. |
| `.agents/shared/planning/post-restart-arm-notify/phase2-plan.md:265` | **A7 — cite-only note** (historical). |
| `.agents/shared/planning/agent-restart/architecture-recommendation.md:44` | **A7 — cite-only note** (stale producer census). |
| `daemon/routers/jobs_crud.py:555` + `daemon/constants.py:122` | **F7 — DEFERRED OUT of P4.** These cite `daemon/sources/registry.py:857` while the live chat-source mint site is at `registry.py:~1056-1057`. Cosmetic citation drift, in files outside the DF-4 surface — moved to §Routed Follow-ups (plan-overview) as a **cosmetic citation-drift lane**. **No edit in DF-4.** |

**Discipline (A7):** every entry above is a **bracketed correction or a cite-only note**. **No history rewrite** — no original line is deleted, and the retraction trace at `nest-ops/decisions.md:666-684` stays the authoritative history. DF-4 appends.

### Acceptance criteria

1. Both nest-ops corrections are **appended bracketed notes**, not rewrites — the original prose remains readable and its history is intact.
2. No line of the original D-18 prose was deleted.
3. The correction names the superseding doc (`df4-provenance-closure/plan-overview.md`) and the new predicate (liveness-only).
4. `upgrade_tools.py:1275` now says six; the alias block at `:1289-1296` is byte-unchanged.
5. **A4 acceptance:** `daemon/tools/provenance.py:49-54` and `:119`, `daemon/tools/upgrade_tools.py:1262`, and `daemon/repositories/instance_ops/models.py:78` no longer assert the false claims. **Docstring/comment text only** — `provenance.py` is NOT relocated, its code and signatures are unchanged, and the model field, column, and migration `20261021_120000` are untouched.
6. `tests/unit/test_instance_ops_registration.py:251-286` (alias identity + T0a slice) remains GREEN — the comment edits are prose-only, but the pins must confirm.
7. The DF-4 doc pair carries the §Honesty Clause verbatim (REST-ingress open; defer-resend open; sweep-unreachable-on-live disclosure).
8. **A7 acceptance:** every listed nest-ops file carries a bracketed correction or cite-only note; **no original line was deleted**; `nest-ops/decisions.md:666-684` remains the authoritative retraction trace.

---

## Pin Matrix

### GREEN — must remain GREEN, unmodified

| Test | Anchor | What it pins |
|---|---|---|
| `test_act_module_imports_the_canonical_helper` | `tests/unit/test_instance_ops_registration.py:230` | The act module imports the canonical helper from the neutral home |
| `test_no_local_reimplementation` | `:240` | No second reader in the act module |
| `test_the_alias_block_still_resolves_from_upgrade_tools` | `:251` | All **six** alias identities resolve from `upgrade_tools` |
| `test_t0a_slice_untouched_by_the_move` | `:264` | The T0a slice keeps `windows.get(current_instance_id)` and none of the six moved names |
| `test_verdict_token_vocabulary` | `:301` | The verdict dataclass vocabulary |
| `test_pclass_domain_is_direct_or_inherited` | `:308` | `pclass` remains a vocabulary value in `provenance.py` (observability survives) |
| `test_no_user_provenance` | `tests/unit/test_instance_ops_behaviour.py:281` | `ok=False` → `reason=no-user-provenance` (unchanged path) |
| `test_t0a_upgrade_factor2_never_calls_restart_helper` | `tests/unit/tools/test_agent_restart.py:122` | The factor-2 read is untouched |
| `test_t0a_prime_hardened_fallback_is_reserved` | `tests/unit/tools/test_agent_restart.py:139` | The R18 precedent literal stays reserved |
| `test_capture_user_source_*` | `tests/unit/tools/test_post_restart_arm_notify_journal.py:828/:839/:852` | The `_capture_user_source` sentinel + the alias import sites (`:833/:844/:852`) |
| classify arm-set pins | `tests/unit/tools/test_upgrade_journal.py:2289-2315`; `tests/unit/tools/test_upgrade_tools.py:3147-3151`/`:3219-3223` | `"api"` STAYS armed (the genuine web-UI chat path) |

### AMENDED — re-pointed honestly (never a silent flip) — commit phase per row (F1)

| Test | Anchor | **Commit** | Old assertion | New assertion |
|---|---|---|---|---|
| `test_direct_window_on_acting_instance_refused` (AC-23a) | `tests/unit/test_instance_ops_behaviour.py:289-299` | **P2** (F1a) | `reason=direct-provenance-insufficient` (`:298`), nothing terminated (`:299`) | The **genuine** direct window **ADMITS** — the act proceeds; the audit row shows `class="direct"`, `root_source=<genuine>`, **`provenance_depth IS NULL`** (F2 coercion), and the **verdict-level** `depth == 0` asserted separately. The test name and docstring must be re-worded to say so (it is no longer a refusal test). |
| `test_gate_refusal_token_mapping` | `tests/unit/test_instance_ops_registration.py:316-328` | **P2** (F1b) | `"no-user-provenance"` then `"direct-provenance-insufficient"`, ordered | Exactly **one** provenance refusal token remains (`"no-user-provenance"`); the retired token is absent from the source. |
| `test_terminate_doc_states_the_current_semantics` (AC-16) | `tests/unit/test_instance_ops_behaviour.py:605` | **P2** (F1c — NEW row) | `"INHERITED" in term  # D-18 current semantics` | The `_full_doc_`'s liveness-only semantics (`ok`-keyed, no class requirement) — the A3 rewrite of `instance_ops.py:810-815` **is a P2 change** and removes the class-keyed wording, so this must be amended in P2. |
| `test_inherited_window_proceeds_ac23b` (AC-23b) | `:301` | **P3** (stays) | Fabricated forged-direct + inherited → act ADMITS, "PINNED AS TRUTH" | Split per §AC-23b Amendment Spec: genuine delegated chain ADMITS (pin ii); the forged chain is **unreachable via the real sweep** (pin iii). **Do not** flip it to a refusal assertion on a fabricated window. Docstring carries the REST-ingress disclosure. |
| `test_capture_user_source_returns_empty_sentinel_for_absent` (docstring) | `tests/unit/tools/test_post_restart_arm_notify_journal.py:839-845` | **P3** | "Phase 2 falls back to 'api'" | The fallback is the **reserved literal**, not `"api"`. The `== ""` assertions (`:845`/`:856`) are unchanged. |
| `test_empty_source_uses_api_sentinel` **(A1 — BUILD-BREAKING)** | `tests/job_queue/test_post_restart_arm_notify_routing.py:203-221` | **P1** | `kwargs["source"] == "api"` | The **reserved literal** (not `"api"`); `is_reserved_source(lit) is True`; `classify_user_origin(lit)` is not user-origin. The `stamp_user_origin_window.assert_not_called()` assertion (`:221`) is retained. Docstrings `:13-18`/`:129-134` re-worded. |
| module docstring token chain | `daemon/tools/instance_ops.py:45-46` | **P2** (source change) | enumerates `direct-provenance-insufficient` | token removed from the vocabulary |

### NEW — created by DF-4

| Pin | Anchor (proposed) | Purpose |
|---|---|---|
| (i) genuine direct admits | `tests/unit/test_instance_ops_behaviour.py` | Genuine operator path is not broken |
| (ii) genuine inherited admits | `tests/unit/test_instance_ops_behaviour.py` | Delegation path preserved |
| (iii) sweep forge dead (real chain) | `tests/postgres/test_df4_provenance_closure.py` (`pytestmark = pytest.mark.postgres`; **deselected by default** — run via `pytest tests/postgres/ --override-ini="addopts=" -m postgres`) | The wrong-side acceptance is closed; **a skipped pin = a failed gate** |
| (iv) anti-re-inversion | `tests/unit/test_instance_ops_registration.py` (source-pin style, next to `:230-286`) | Neither gate predicate keys on `pclass` — **replaces the vacuous fixture-parity pin** |
| (v) reserved literal | `tests/unit/tools/test_df4_sweep_reserved_fallback.py` | The new constant is reserved; classify fails it closed; no `or "api"` shape survives |
| (vi) `is_system_origin` lane (A5) | `tests/unit/tools/test_df4_sweep_reserved_fallback.py` or a sibling | The wake takes the `is_system_origin` lane; the durable `original_source="api"` write no longer happens for these wakes |

---

## v0.27.2 Stage Checklist (stage ONLY — NO promote) — incl. F5 pre-stage rows

| # | Item | Value / command | Owner |
|---|---|---|---|
| 0a | **Pre-stage 1 (F5)** — merge `fix/df4-provenance-gate` into `latest` | `--no-ff` merge onto `origin/latest` | **giter / stage lane** |
| 0b | **Pre-stage 2** — 4-file version bump to 0.27.2 | **MUST include `tests/test_api.py:227`** (asserts `data["version"] == "0.27.1"`) — omit it and the suite goes RED. The house 4-file set: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`. | **giter / stage lane** |
| 0c | **Pre-stage 3** — local annotated tag `v0.27.2` at HEAD | **NOT pushed** | **giter / stage lane** |
| 0d | **Pre-stage 4** — clean worktree at tip | `git status --porcelain` empty at the staged commit | **giter / stage lane** |
| 0e | **Pre-stage 5** — seal / tripwire re-pin | **NONE REQUIRED.** Council-verified: **no seal/tripwire SHA pin covers ANY DF-4-touched file** → this commission carries **no re-pin obligation**. The stage lane should **not** hunt for one. | — |
| 1 | Version line | **v0.27.2** — the next free line strictly above every existing tag **and** every `releases/` dir (**council-verified**). `v0.28.x` is a MAJOR line and **must not** be claimed by this point-fix commission. | — |
| 2 | Known schema gen (unchanged) | `20261021_120000_create_instance_ops_audit.sql` | — |
| 3 | Migration delta vs v0.27.1 | **EMPTY** (107 == 107 files, 107 distinct prefixes) — **council-verified on disk**. No schema change ships. | — |
| 4 | Rollback-safe adjudication | `rollback_safe=true`, `source=explicit`, reason `delta-empty-legitimate`. The old binary remains compatible — the schema is untouched. | — |
| 5 | Stage command form | `VERSION=v0.27.2 bash scripts/upgrade/stage.sh` — the **v-prefixed** describe output is required (ADR-009 D3 guard, `stage.sh:146-150`); a bare `0.27.2` is **refused** (exit 78). Stage **only after** 0a–0d. | — |
| 6 | L1 tip-identity | Must be re-verified immediately pre-stage: `HEAD == refs/remotes/origin/latest`. A non-tip tree is **REFUSED** (exit 78) — that is correct behaviour; re-fetch and re-stage from the tip. **No override.** | — |
| 7 | L2 provenance | Both sidecars written (`git_head`, `git_dirty=false`); the stage re-hashes the artifacts rather than trusting the sidecar sha. | — |
| 8 | Staging path | `/home/nea/agents-ensemble/releases/v0.27.2`; keep the staging worktree so both provenance sidecars survive to the ceremony. | — |
| 9 | Payload expectation | **Daemon-only** — the `agents/`, `plugins/`, and `frontend/` payload trees are byte-identical to v0.27.1. Only the binary + version fields change. The FE sidecar is unchanged (`fe_index_sha256` unchanged since v0.25.0). | — |
| 10 | Promote | **NOT performed.** The user's 3-factor nonce ceremony owns the promote. Do not touch the `current` symlink. | — |
| 11 | Fences | The live daemon (`:9797`), the live DB, and the live symlink are untouched by staging. No boot smoke against the live instance. | — |
| 12 | Independent verification | A tester lane verifies the staged artifact independently (sha 3-way, banner version, `livez`/`readyz` on an isolated cgroup boot, migration count, alias/wiring pins GREEN, whole-tree net-new reds = 0) before the ceremony. | tester lane |

---

## Handoff Notes for the Implementing Lane

1. **P1 before P2** — non-negotiable (R1; Q4 confirms P2-alone is strictly weakening).
2. **Commit-content boundary (A11):** the **P1 commit** = swap + constant + pin (v) + the A1 routing-test amendment. **Pin (iii) does NOT land in P1** — it lands in P3. The P1-only intermediate must be suite-green.
3. **Do not** touch the alias block (`upgrade_tools.py:1289-1296`), the factor-2 read, the restart gate, `stamp_user_origin_window`, or `classify_user_origin`'s arm set. `daemon/tools/provenance.py` is **not** relocated and its **code** is frozen — but **docstring-only** edits there are authorised (A4: `:49-54`, `:119`).
4. **Do not** resurrect the vacuous fixture-parity drift pin. Pin (iv) replaces it.
5. **Do not** rewrite the nest-ops history — append bracketed corrections; cite `nest-ops/decisions.md:666-684`, do not duplicate it.
6. **Every boot** needs the per-variable env scrub and a `mktemp` `FAKE_HOME` with an escape guard.
7. **The release notes carry the honesty clause** — REST-ingress stays open (`257488dc`); the **defer-resend lane** (`job_queue_service.py:2608`) stays open (DF-5); the sweep fallback is structurally unreachable on live promote-kind arms. State plainly that the D-18 "direct refused on purpose" property is **retired BY DESIGN** with its compensating controls, and disclose the `is_system_origin` dispatch flip (A5).
8. **Stage v0.27.2; never promote.**

---

## Honesty Clause (verbatim from `plan-overview.md` — the doc pair carries it in full)

P4 acceptance requires the DF-4 doc pair to carry the §Honesty Clause verbatim
(REST-ingress open; defer-resend open; sweep-unreachable-on-live disclosure). The
block below is copied UNCHANGED from `plan-overview.md` §Honesty Clause — it is the
ship-facing truth and must travel with the change and the release notes.

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
