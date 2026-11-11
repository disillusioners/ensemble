# Phase 4: Guardrails, test matrix, staged release

> **Binding order.** `architecture-recommendation.md` @ `df8da01a6` **wins on conflict** with this
> document. **Must-apply deltas (transcribed 2026-11-10):**
> - **⚠ NAMING VIOLATION FIXED (architect §7 defect 1):** the retired spellings
>   `PromptAssistConfig` and `ENSEMBLE_PROMPT_ASSIST_` are replaced by **`PromptAssentConfig`** and
>   **`ENSEMBLE_PROMPT_ASSENT_`** everywhere. `prompt-assent` is the one canonical spelling (H-11).
>   **The two retired spellings are quoted here on purpose** — they are the literal strings the
>   P4 acceptance criterion greps for, so they must stay greppable in this file. They are the
>   **only** intentional occurrences of a retired spelling in the planning set.
> - **D-2 — default OFF (fail-closed)**, with **ONE shared default constant** consumed by **both**
>   the Phase 2 defensive resolver and this phase's config class (the prior "resolver off / class
>   on" split is a defect under either default and is **reconciled** here).
> - **`PromptAssentConfig.hourly_mismatch_budget`** (default **10**) rides the same class (D-3b);
>   the **mechanism** is P2-owned, the **config key** lands here.
> - **SP-1 REWRITTEN to the rendered canonical bytes** (K-7) — not a content re-read.
> - **The runbook carries five specific disclosures:** the **"not the gate"** language
>   (O-H), the **D-7 persona gap**, the **factor-1-is-relay-self-assertion** caveat, the
>   **no-agent-filter blast radius** (O-I), and **refusals write no ledger row** (O-G).

## Objective

Close the lane out: the **kill-switch**, the **full test matrix** as a first-class artefact, the
**metrics/vocabulary bookkeeping**, the **runbook**, and a **STAGED RELEASE — and nothing
more**.

**The ceremony belongs to the Emperor.** This lane delivers a staged release and a runbook. It
does **not** promote, does **not** touch a live install, and does **not** perform the nonce flip.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **4.1** | **Add the kill-switch config class** — `daemon/config.py`: **`PromptAssentConfig(BaseSettings)`** with the **`ENSEMBLE_PROMPT_ASSENT_`** prefix (pattern `NotifyUserConfig`, `daemon/config.py:3166-3224`), field `enabled` (**`ENSEMBLE_PROMPT_ASSENT_ENABLED`**), wired at the `:6826`-style site. **Auto-covered by the tests' `ENSEMBLE_` prefix tracker** (`daemon/config.py:3171-3173`) — **no conftest edit**. **The class ALSO carries `hourly_mismatch_budget`** (default **10**, D-3b) — the **mechanism** is P2-owned (`phase2-plan.md` task 2.11); the **config key** lands here. **The default-OFF value is a SINGLE shared constant** consumed by **both** this class and Phase 2's defensive resolver — **not** two independently declared defaults | P2 | The class exists under the **correct** name (`PromptAssentConfig`); the prefix tracker picks it up automatically; the knob reads at **call time**; `hourly_mismatch_budget` defaults to **10**; a grep proves **no** `PromptAssistConfig` survives anywhere in the tree; **both** consumers read the **same** default constant |
| **4.2** | **Wire the switch into the tool's call-time resolver** — replace Phase 2's defensive resolver with the real one, reading the **same shared default constant**. **D-2 semantics:** (i) the switch is read **at call time** and the tool **stays registered** when off — refusal-at-call, never construction-absence (`daemon/tools/prompt_reload.py:25-28`, `:76-91`); (ii) the **gate order** is *ari → kill-switch → 3-factor gates → mutation*, so an unauthorised caller cannot probe the knob's state (`daemon/tools/notify_user.py:703-715` puts the agent gate first for exactly this reason); (iii) **`mint` is refused too** when off; (iv) **indeterminate/invalid values fail CLOSED**; (v) a disabled lane refuses cleanly with a typed code **naming the env var** — never a silent fall-back-to-FE confusion | 4.1, P2 | With the knob off: refusal `lane-disabled`, the tool is **still registered**; the **not-ari** envelope still wins over the switch (gate order); **`mint` is refused** with the knob off; an **invalid** env value fails **closed**; the refusal **names** `ENSEMBLE_PROMPT_ASSENT_ENABLED` |
| **4.3** | **Apply the architect's D-2 ruling — default OFF (fail-closed)** — the architect **OVERRULED** the planner's fail-open (default ON) recommendation. **The default is OFF**: `ENSEMBLE_PROMPT_ASSENT_ENABLED` unset or invalid ⇒ the lane is **off** ⇒ `lane-disabled`. Rationale of record: this is the first lane in the tree that can change a live agent's **own guardrails** through a chat message; the house's most recent Emperor-ratified posture for a novel authority surface (Decision Module P1, 2026-11-09: *"dormant by default, nothing decides anything"*) is exactly this shape; the FE lane fully covers approvals meanwhile. **ONE shared fail-closed OFF default constant**, consumed by both the Phase 2 resolver and this class — the prior "resolver off / class on" split is a **defect under either default** and is reconciled here. **Flipping to ON is one env var per environment — no code change** (architect §8 decision 1: Emperor-owned, non-blocking for the developer lane) | 4.1, architect ruling @ `df8da01a6` | The default is **OFF**; the **same** constant is read by the Phase 2 resolver and this class (a pin proves it); the runbook states the default explicitly; the CHANGELOG states the arming procedure |
| **4.4** | **Assemble the FULL test matrix as a first-class artefact** — the mandatory six cells (happy path with a real token echo + the five mandated refusals), the **six original named counter-mutations**, the **seven amendment-pass cells** (mint-without-window, group-DM mint, wrong-user-of-channel, budget exhaustion, pending-side mutation, effective-side mutation, column-source pin), the **counter-separate-transaction regression cell**, the additional refusal cells, the token-store cells, the registration cells, the freshness cells, and the FE-unchanged cells — collected into a single lane pack under `test/packs/` following the house convention, runnable as one command. **The pack must include the ON-path test that arms the env explicitly** — with the default OFF, the lane's default state is `lane-disabled` out of the box | P3 | The pack runs green in isolation **and** in the full-suite context; every cell is individually addressable and reports its own name; **the default state is OFF** ⇒ `lane-disabled` out of the box, and the ON-path test **arms the env explicitly** |
| **4.5** | **Net-new verification (H-6)** — capture the **TRUE pre-fix base** (the base commit `0efdf106f`, **same commands**) and diff the failed-lists. Produce the diff as an evidence artefact. A test already failing before the change does **not** count as a new red | 4.4 | The net-new failed-list is **empty**; the diff artefact is attached to the lane report |
| **4.6** | **Apply the structural pins (SP-1, SP-2)** — `risks.md` §Part 3: **(i) SP-1 — REWRITTEN to the rendered canonical bytes (K-7):** the token's render binding is the **canonical-render `sha256`** — produced by the **one shared helper** (`phase1-plan.md` task 1.10), computed at mint **from the exact bytes placed in the mint response**, and re-checked **inside the D-4 hook's transaction** from the row **as read in-txn** (**O-L** — the only render-hash site). **Copying `prompt_versions.content_hash` is FORBIDDEN** — the pin asserts the token table's hash column is **NOT** populated from that column (a mutation that feeds it reddens the mint test). **(ii) SP-2 — no UPDATE/DELETE ever touches `prompt_audit_events`** — a tree-level grep over the lane (and preferably the prompt subsystem) finds none, and the lane's own write path is an INSERT through `_append_audit_in_conn` (`daemon/repositories/prompt_version/repository.py:1502-1511`, impl `:3036+`). **Both pins must be non-vacuous** — a counter-mutation must redden each | 4.4, P1 | Both pins are in the pack; each has a demonstrated counter-mutation that reddens it. **An absence-only grep over an empty file is vacuous — prove the pin by mutation**; the SP-1 mutation **proves** the column source is the canonical render, not `prompt_versions.content_hash` |
| **4.7** | **Metrics / vocabulary bookkeeping + docs** — `daemon/checkpoint_metrics.py`: if the Phase 2 vocabulary bump (v8 → v9) was not already made in the same change as the artifact-label regeneration, fix that here (**the artifact and the constant move in the SAME change** — the F-1 trap class, `daemon/checkpoint_metrics.py:244-263`). `CHANGELOG.md`: an entry describing the lane, the doctrine, the operator-visible knob, and the **arming procedure** (one env var per environment). `docs/runbooks/prompt-assent.md` (NEW): how to arm/disarm, how the token TTL works, what each refusal means, what the audit payload contains, and the **D-2 default (OFF)**. **The runbook MUST carry these five mandated disclosures:** **(1) the "not the gate" language (O-H)** — `self-approval-attempt` is a **belt-and-braces assertion, NOT the load-bearing gate**; the load-bearing defences are the window-pop (`daemon/manager.py:6132-6133`), the ari-EXACTLY layer-3 gate, and the daemon-derived user equality (AR-1/K-8); **(2) the D-7 persona gap** — Phase 1 covers the classic `prompt_versions` surface ONLY; the memoir persona surface is **deferred** and writes **no** `prompt_audit_events`; **(3) the factor-1-is-relay-self-assertion caveat (O-H)** — factor 1 (`user_confirmed`) is the **relay agent's own assertion**, not a human act; it is the *weakest* of the three factors, and the human turn + token echo are what carry the authority; **(4) the no-agent-filter blast radius (O-I)** — the pending list is **cross-agent** (every CORE pending row, including other agents' drafts) because the relay lane must be able to approve any agent's row; the INFO line must stay content-free; **(5) refusals write no ledger row (O-G)** — a refusal writes an INFO line only; **no** `prompt_audit_events` row and **no** token-table row is written for a refused attempt (the token table records **mints**, not refusals) | 4.2, 4.3 | The vocabulary and the artifact label set are in lockstep; the CHANGELOG entry is accurate; the runbook answers "what do I do when the Emperor says yes and nothing happens"; **all five mandated disclosures are present** |
| **4.8** | **Stage the release — NO LIVE PROMOTE** — build and stage the release artefacts for the lane's version, exactly per the house release procedure, and **stop**. Do **not** promote; do **not** flip a nonce; do **not** touch the live install. The ceremony is the Emperor's | 4.5, 4.6, 4.7 | A **staged** release exists with its sidecars and manifest; **the live install is byte-identical to its pre-phase state**; the lane report states plainly that **no promote occurred** and that the ceremony remains outstanding |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/config.py` | `PromptAssentConfig` + field wiring + the shared default constant | 4.1 |
| `daemon/tools/prompt_assent.py` | the real call-time switch resolver + gate order | 4.2 |
| `test/packs/prompt_assent_matrix_test.sh` (+ `.py`) | NEW — the lane pack | 4.4 |
| `tests/unit/test_prompt_assent_pins.py` (or pack-embedded) | NEW — SP-1, SP-2 | 4.6 |
| `daemon/checkpoint_metrics.py` | vocabulary + artifact label set (if not already done in P2) | 4.7 |
| `CHANGELOG.md` | the lane entry | 4.7 |
| `docs/runbooks/prompt-assent.md` | NEW — the runbook | 4.7 |
| release artefacts | the **staged** release | 4.8 |

## Coupling

- **Loose with:** Phase 1 — the pack exercises the token store; the structural pins live on
  Phase 1's artefacts.
- **Loose with:** Phase 2 — the pack exercises the registration; the pin files' count assertions
  are Phase 2's, and Phase 4 only **runs** them.
- **Tight with:** Phase 3 — the mandatory matrix cells are Phase 3's tests, collected and run as
  one pack; the freshness cell depends on the D-6 ruling.
- **Independent of:** nothing else. Phase 4 is the **terminal** phase.

**Shared-context ownership.** Phase 4 owns the **C-4** (configuration), the **matrix half of C-5**
(tests), and the **C-6** (release surface) contexts. See `plan-overview.md` → Shared-context
grouping.

**The kill-switch ordering is already de-risked.** Phase 2 shipped a **fail-closed** resolver (a
missing config class ⇒ "off"), so Phase 1–3 can land and be tested without the lane ever being
armable. Phase 4 is the phase that lands the **config class** — and per the architect's D-2
ruling the default is **OFF**, so Phase 4 lands the class **and** the arming procedure (one env
var per environment), not a turn-on. This is deliberate: the lane must never be accidentally live
before its guardrails exist.

**The default is OFF — and the E2E is therefore gated on an arming step.** Because the default
is OFF, an unarmed E2E would be **vacuous** (every call would be refused `lane-disabled` and the
acceptance would "pass" for the wrong reason). The **E2E-0 arming step** in `plan-overview.md`
(§Acceptance A-2) exists for exactly this reason.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The lane is accidentally live before its guardrails exist** | High | Low | Phase 2's resolver is **fail-closed** by default (a missing class ⇒ off); the kill-switch class lands only in Phase 4 |
| **The switch is read at construction time**, so turning it off leaves the tool callable | High | Medium | Task 4.2's condition (i): read **at call time**; the tool **stays registered** when off (`daemon/tools/prompt_reload.py:25-28`); a test asserts the tool is present in the tool list with the knob off |
| **The not-ari gate is probed after the switch** (the knob's state leaks to unauthorised callers) | Medium | Medium | Task 4.2's condition (ii): gate order *ari → switch → 3-factor → mutation*; a test asserts the not-ari envelope wins and carries **no** refusal code |
| **The D-2 default is implemented the wrong way round** (an unreviewed fail-open on a persona-changing lane) | **High** | Medium | The architect **OVERRULED** to default-OFF; task 4.3 implements the ruling verbatim; **ONE shared constant** is read by both the Phase 2 resolver and the Phase 4 class (a pin proves they agree); the runbook and the CHANGELOG both state the default and the arming procedure. **A fail-open landing is a release-gate event, not a style choice** |
| **The E2E acceptance is run against an unarmed lane** and passes vacuously (every call refused `lane-disabled`) | **High** | Medium | The **E2E-0 arming step** (`plan-overview.md` §A-2) makes the arming explicit and first; the ON-path test **arms the env explicitly** (task 4.4); an unarmed E2E is treated as **not run**, not as passed |
| **The runbook omits one of the five mandated disclosures** (not-the-gate / D-7 persona gap / factor-1 self-assertion / no-agent-filter blast radius / refusals-write-no-ledger-row) | Medium | Medium | Task 4.7 enumerates all five explicitly and the acceptance requires **all five present**; a reviewer greps the runbook for each |
| **The pack is green but the matrix is incomplete** | High | Medium | Each cell reports its **own name**; the pack's cell list is checked against the mandatory six verbatim |
| **A structural pin is vacuous** (an absence-only grep over an empty or tiny file) | High | Medium | Task 4.6 requires a **demonstrated counter-mutation** for each pin — the house mutation rule: absence-only and raw-text pins are prose-satisfiable and prove nothing |
| **The net-new baseline is not a TRUE pre-fix base** (e.g. measured on a dirty tree) | Medium | Medium | H-6: the base is the **base commit**, with the **same commands**, on a clean tree; the diff artefact is produced and attached |
| **A live promote happens by accident** | **Critical** | Low | H-4 + task 4.8: the phase produces a **staged** release and **stops**. The lane report states that **no promote occurred**. The ceremony belongs to the Emperor |
| **The metrics vocabulary and the artifact label set drift apart** | Medium | Medium | Both move **in the same change** (`daemon/checkpoint_metrics.py:244-263`) — the F-1 trap class; verified in the same commit |
| **The runbook omits the "nothing happened" troubleshooting path** | Medium | Medium | The runbook must answer *"the Emperor said yes and nothing happened"* — the refusal-code table, the TTL, the restart caveat (risk R-4) and the window-staleness bound (D-6) |

## Test plan (house conventions)

**Env hygiene (H-7).** Scrub per-variable: `env -u POSTGRES_DB -u POSTGRES_USER -u
POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>`. **`env -u POSTGRES_*` is a silent
NO-OP** — bash does not glob env names, so an ambient `POSTGRES_DB=ensemble_prod` stays visible and
a "fenced" run can hit **prod**. Scrub the **full** family, or none: a partial scrub also breaks
collection.

**Runner (H-8).** The **worktree's own venv** — never the main checkout's `.venv` (its editable
`.pth` pins the main tree). `cd` into the worktree first. `testpaths=["tests"]`; four dirs
deselected (integration / postgres / slow / conformance); `timeout=30` + `thread`; `-p no:timeout`
where needed; pass explicit `tests/` lists; `pytest -q` buffers tracebacks to the session end ⇒
`tee LOG >/dev/null` + `tail -2 LOG`.

**The pack.** The lane pack must:
- run each matrix cell **independently addressable** and report its own name;
- be runnable **in isolation** and **in the full-suite context** (order independence — house rule:
  3× isolated order+load runs);
- include the **net-new** assertion census — no `await` without `AsyncMock`; no new flakiness.

**Net-new verification (H-6, task 4.5).** The base is the **base commit** with the **same
commands**; diff the failed-lists; attach the diff as evidence.

**Staged-release discipline.** A staged release is **not** isolated: the binary's path derivation
resolves the **LIVE** install root (`daemon/api.py:2545` +
`daemon/tools/upgrade_tools.py:658`), so a staged boot smoke binds the **live** promote journal.
Any smoke run from this lane must therefore pin
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** (never `env -u` — that strips the
ambient guard and the sweep **kills live processes**), and must not be run against the live
releases/state.json.

**Non-vacuity.** Every new assertion in this phase needs a **counter-mutation**. The two
structural pins (SP-1, SP-2) are the classic traps — prove them by mutation.

## Exit Criterion

**All of the following hold:**

1. The kill-switch is live: with the knob off the tool refuses `lane-disabled` and is **still
   registered**; the gate order holds; **`mint` is refused** too; the default is **OFF** and both
   the Phase 2 resolver and the Phase 4 class read the **same** shared constant; the runbook and
   the CHANGELOG state the default and the arming procedure.
2. The **full test matrix** is a first-class pack — green, individually addressable, order-
   independent, and covering the mandatory six, the **six named counter-mutations**, the **seven
   amendment-pass cells**, and the **counter-separate-transaction regression cell**.
3. The **net-new failed-list is empty** against the TRUE pre-fix base, with the diff artefact
   attached.
4. Both **structural pins** are in place **and demonstrated non-vacuous** by a counter-mutation;
   **SP-1** proves the token's render binding is the **canonical render**, not a copy of
   `prompt_versions.content_hash`.
5. The CHANGELOG and the runbook are accurate; the runbook answers the "nothing happened"
   troubleshooting path; and it carries **all five mandated disclosures** (not-the-gate, D-7
   persona gap, factor-1 self-assertion, no-agent-filter blast radius, refusals-write-no-ledger-row).
6. A **staged** release exists; the **live install is byte-identical** to its pre-phase state;
   **no promote occurred**; the ceremony remains outstanding and is the Emperor's to perform.

**Then the lane is complete — and only then.**
