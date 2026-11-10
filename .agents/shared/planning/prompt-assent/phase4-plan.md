# Phase 4: Guardrails, test matrix, staged release

## Objective

Close the lane out: the **kill-switch**, the **full test matrix** as a first-class artefact, the
**metrics/vocabulary bookkeeping**, the **runbook**, and a **STAGED RELEASE — and nothing
more**.

**The ceremony belongs to the Emperor.** This lane delivers a staged release and a runbook. It
does **not** promote, does **not** touch a live install, and does **not** perform the nonce flip.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **4.1** | **Add the kill-switch config class** — `daemon/config.py`: `PromptAssistConfig(BaseSettings)` with the `ENSEMBLE_PROMPT_ASSIST_` prefix (pattern `NotifyUserConfig`, `daemon/config.py:3166-3224`), field `enabled` (`ENSEMBLE_PROMPT_ASSIST_ENABLED`), wired at the `:6826`-style site. **Auto-covered by the tests' `ENSEMBLE_` prefix tracker** (`daemon/config.py:3171-3173`) — **no conftest edit** | P2 | The class exists; the prefix tracker picks it up automatically; the knob reads at **call time** |
| **4.2** | **Wire the switch into the tool's call-time resolver** — replace Phase 2's defensive "missing class ⇒ off" resolver with the real one. **Two house conditions (D-2):** (i) the switch is read **at call time** and the tool **stays registered** when off — refusal-at-call, never construction-absence (`daemon/tools/prompt_reload.py:25-28`, `:76-91`); (ii) the **gate order** is *ari → kill-switch → 3-factor gates → mutation*, so an unauthorised caller cannot probe the knob's state (`daemon/tools/notify_user.py:703-715` puts the agent gate first for exactly this reason) | 4.1, P2 | With the knob off: refusal `lane-disabled`, the tool is **still registered**; the **not-ari** envelope still wins over the switch (gate order) |
| **4.3** | **Set the default per the architect's D-2 ruling** — **fail-open (default ON)** is the technical analysis's recommendation (mirroring `notify_user` and `prompt_reload`); **fail-closed (default OFF)** is the alternative the architect may choose to keep the lane dormant until first use. **This is the architect's call — implement whichever is ruled, and record which in the runbook** | 4.1, architect ruling | The default matches the ruling; the runbook states it explicitly |
| **4.4** | **Assemble the FULL test matrix as a first-class artefact** — the mandatory six cells (happy path with a real token echo + the five mandated refusals) plus the additional refusal cells, the token-store cells, the registration cells, the freshness cells, and the FE-unchanged cells — collected into a single lane pack under `test/packs/` following the house convention, runnable as one command | P3 | The pack runs green in isolation **and** in the full-suite context; every cell is individually addressable and reports its own name |
| **4.5** | **Net-new verification (H-6)** — capture the **TRUE pre-fix base** (the base commit `0efdf106f`, **same commands**) and diff the failed-lists. Produce the diff as an evidence artefact. A test already failing before the change does **not** count as a new red | 4.4 | The net-new failed-list is **empty**; the diff artefact is attached to the lane report |
| **4.6** | **Apply the structural pins (SP-1, SP-2)** — `risks.md` §Part 3: (i) the token is bound to a **content hash** of the pending row (if J-3 is ruled in) — the pin asserts the column exists, the mint path **computes** it, and the approve path **re-checks** it; (ii) **no UPDATE/DELETE ever touches `prompt_audit_events`** — a tree-level grep over the lane (and preferably the prompt subsystem) finds none, and the lane's own write path is an INSERT through `_append_audit_in_conn` (`daemon/repositories/prompt_version/repository.py:1502-1511`, impl `:3036+`). **Both pins must be non-vacuous** — a counter-mutation must redden each | 4.4, P1 | Both pins are in the pack; each has a demonstrated counter-mutation that reddens it. **An absence-only grep over an empty file is vacuous — prove the pin by mutation** |
| **4.7** | **Metrics / vocabulary bookkeeping + docs** — `daemon/checkpoint_metrics.py`: if the Phase 2 vocabulary bump (v8 → v9) was not already made in the same change as the artifact-label regeneration, fix that here (**the artifact and the constant move in the SAME change** — the F-1 trap class, `daemon/checkpoint_metrics.py:244-263`). `CHANGELOG.md`: an entry describing the lane, the doctrine, and the operator-visible knob. `docs/runbooks/prompt-assent.md` (NEW): how to arm/disarm, how the token TTL works, what each refusal means, what the audit payload contains, and the **D-2 default** | 4.2, 4.3 | The vocabulary and the artifact label set are in lockstep; the CHANGELOG entry is accurate; the runbook answers "what do I do when the Emperor says yes and nothing happens" |
| **4.8** | **Stage the release — NO LIVE PROMOTE** — build and stage the release artefacts for the lane's version, exactly per the house release procedure, and **stop**. Do **not** promote; do **not** flip a nonce; do **not** touch the live install. The ceremony is the Emperor's | 4.5, 4.6, 4.7 | A **staged** release exists with its sidecars and manifest; **the live install is byte-identical to its pre-phase state**; the lane report states plainly that **no promote occurred** and that the ceremony remains outstanding |

### File-level task map

| File | Change | Task |
|---|---|---|
| `daemon/config.py` | `PromptAssistConfig` + field wiring | 4.1 |
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
armable. Phase 4 is the phase that turns it ON. This is deliberate: the lane must never be
accidentally live before its guardrails exist.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The lane is accidentally live before its guardrails exist** | High | Low | Phase 2's resolver is **fail-closed** by default (a missing class ⇒ off); the kill-switch class lands only in Phase 4 |
| **The switch is read at construction time**, so turning it off leaves the tool callable | High | Medium | Task 4.2's condition (i): read **at call time**; the tool **stays registered** when off (`daemon/tools/prompt_reload.py:25-28`); a test asserts the tool is present in the tool list with the knob off |
| **The not-ari gate is probed after the switch** (the knob's state leaks to unauthorised callers) | Medium | Medium | Task 4.2's condition (ii): gate order *ari → switch → 3-factor → mutation*; a test asserts the not-ari envelope wins and carries **no** refusal code |
| **The D-2 default is implemented the wrong way round** (an unreviewed fail-open on a persona-changing lane) | High | Medium | Task 4.3 makes the ruling an **explicit input**; the runbook states the default; the lane report states it. If the architect has not ruled, the lane stays **fail-closed** (Phase 2's behaviour) |
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
   registered**; the gate order holds; the default matches the **architect's D-2 ruling** (and the
   runbook states it).
2. The **full test matrix** is a first-class pack — green, individually addressable, order-
   independent, and covering the mandatory six verbatim.
3. The **net-new failed-list is empty** against the TRUE pre-fix base, with the diff artefact
   attached.
4. Both **structural pins** are in place **and demonstrated non-vacuous** by a counter-mutation.
5. The CHANGELOG and the runbook are accurate, and the runbook answers the "nothing happened"
   troubleshooting path.
6. A **staged** release exists; the **live install is byte-identical** to its pre-phase state;
   **no promote occurred**; the ceremony remains outstanding and is the Emperor's to perform.

**Then the lane is complete — and only then.**
