# Plan Overview: prompt-assent — the conversational approval lane (*The Emperor's Word / Assent*)

| Field | Value |
|---|---|
| Date | 2026-11-10 (UTC) |
| Author | plan-creation worker (plan lane) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` |
| Branch | `feature/prompt-assent` |
| Base commit | `0efdf106f3f66ccb400405f4137694592b72097d` (VERIFIED) |
| Status | **Draft — Ready for Architect Review** |
| Planning set | [`plan-overview.md`](plan-overview.md) · [`phase1-plan.md`](phase1-plan.md) · [`phase2-plan.md`](phase2-plan.md) · [`phase3-plan.md`](phase3-plan.md) · [`phase4-plan.md`](phase4-plan.md) · [`decisions.md`](decisions.md) · [`risks.md`](risks.md) · [`doctrine.md`](doctrine.md) · [`research/approval-path.md`](research/approval-path.md) · [`research/trust-anchors.md`](research/trust-anchors.md) · [`technical-analysis.md`](technical-analysis.md) |

---

## Objective

**An approval said out loud in chat is a first-class approval: the Emperor's word, relayed by Ari
with verifiable provenance, approves or rejects a PENDING prompt-version row exactly as the FE
session lane does — and the ledger can afterwards reconstruct who approved, from where, in which
message, on which item, at what time.**

A single sentence that marks this feature complete: *a PENDING `prompt_versions` row for
`agents/ari/tools_note.md` is approved end-to-end from a chat turn, with the approving user's
identity, source, message id, relay agent and timestamp recorded in `prompt_audit_events`, and
every refusal path (wrong token, expired token, non-pending row, agent-self-approval attempt,
non-user-origin turn) refuses with a typed code and no state change.*

---

## The Doctrine

> **FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval lane.**

Full text and its three binding consequences: [`doctrine.md`](doctrine.md).

The doctrine is the **north star**, and three clauses drive the entire shape of this plan:

1. **"FE-UI is a deeper view"** ⇒ the FE approval lane is **kept and unchanged**. The
   conversational lane is added **alongside** it, never instead of it.
2. **"the working path is conversation"** ⇒ the lane must be fully usable with the FE closed.
   That is why the approval path is an **in-process tool → service** path, not a new REST route.
3. **"with provenance"** ⇒ an approval is not a boolean flip. It is a decision whose evidence
   chain is reconstructable from the ledger: *token ↔ item ↔ message ↔ human ↔ relay agent ↔ time*.

---

## Scope

### In Scope

| # | Item | Why it is in |
|---|---|---|
| **S-1** | A new privileged **`ari`-EXACTLY** tool `prompt_assent` (category key **`prompt-assent`**), with `action ∈ {mint, approve, reject}` | BLESSED-1, BLESSED-5; DECIDED-3 |
| **S-2** | A **new PG table `prompt_assent_tokens`** — one row per minted token, storing `token_hash` (never the raw token), the item binding, TTL, status, attempts | D-1; a new table rides the `create_all` seam and therefore **avoids** the `_ensure_postgres_columns` one-transaction boot-abort surface |
| **S-3** | The new table's **own** `*.sql` migration in `daemon/migrations/versions/` | House rule: every new SQLModel table requires its own `.sql` — zero exceptions |
| **S-4** | A new auth-method lane value **`assent-relay`** + a dedicated actor constructor + an **attestation-bearing** repository entrypoint | D-8 — the relay actor needs a name that is neither `internal` (banned by F4) nor a forged `session` |
| **S-5** | The **three-factor echo gate**: `user_confirmed` + per-item token + genuine user turn via the EXISTING `classify_user_origin` | BLESSED-2 — the trust anchor is reused, never forked |
| **S-6** | **Audit payload extension** of `prompt_audit_events` — relay provenance keys, **append-only**, same transaction as the state change | BLESSED-3; DECIDED-1 |
| **S-7** | A **service-level acceptance path** — a single shared predicate `is_acceptable_approver(actor, attestation)` consulted by the classic prompt surface | BLESSED-4; DECIDED-4 |
| **S-8** | `repo.list_pending()` + `svc.list_pending()` + an **ari-facing read** surface | BLESSED-5; DECIDED-2 — a pending list does not exist today |
| **S-9** | The **kill-switch** `ENSEMBLE_PROMPT_ASSENT_ENABLED`, read at call time; the tool stays registered | BLESSED-6; D-2 |
| **S-10** | The **ari-EXACTLY registration** (category + `agents/ari/meta.json` allow entry + in-body `agent_id` gate) and the **complete pin set** | BLESSED-1; the SAME-PR rule (`daemon/tools/_tool_registry.py:366-417`) |
| **S-11** | The **full test matrix** (happy path + five mandated refusals + the token-store + registration proofs) | BLESSED-6; house testing conventions |
| **S-12** | A **STAGED RELEASE** (no live promote) | The ceremony belongs to the Emperor |

### Out of Scope

| # | Item | Why it is out |
|---|---|---|
| **O-1** | **The MEMOIR PERSONA surface** (`memoir_persona_versions`, `daemon/repositories/memoir/repository.py:220-262`, `daemon/routers/memoir.py:143-208`) | **EXPLICITLY DEFERRED pending an architect ruling (D-7).** ⚠ The commission's ROUTING requirement named *"the v2 persona-approval `HUMAN_LANE_REQUIRED` path"* — but that is the **memoir persona table**, a **DIFFERENT stack** from `prompt_versions`: a different table, a different repository, a different state vocabulary (`status` vs `state`), and a **different audit shape** — persona rows carry `approved_at` and write **no** `prompt_audit_events`. Phase 1 ships the **classic `prompt_versions` surface only**. Doing both in one change doubles the blast radius of a **trust-model** change for no functional gain. **If the architect rules that both ship, the persona lane needs its own ledger — this plan does not cover it** |
| **O-2** | **Any change to the FE routes** | Doctrine: FE-UI is a *deeper view* — it stays byte-identical (`daemon/routers/prompts.py:616-697`) |
| **O-3** | **A new public HTTP endpoint** for the relay or the pending list | The only consumer is Ari, in-process. An HTTP surface would add scope plumbing, a public attack surface, and AuthContext-forgery temptation (D-8 option (b), rejected) |
| **O-4** | **Changing `approve()` / `reject()` / `atomic_transition`'s existing semantics** | F4 and the FE contract must hold unchanged. The lane gets a **separate** entrypoint |
| **O-5** | **Forking `classify_user_origin`** or deriving chat types from source-id prefixes | BLESSED-2 — the trust anchor is single-sourced. The classifier's chat set is deliberately **not** `constants.CHAT_SOURCE_PREFIXES` (`daemon/tools/upgrade_journal.py:4089-4093`) — do not dedup the two |
| **O-6** | **A live promote / ceremony** | House rule: **STAGED RELEASE ONLY** from this lane. The ceremony belongs to the Emperor |
| **O-7** | **Executing** the E2E acceptance step | The artifact write path is **human-session-only** by design (`daemon/tools/prompt_artifacts.py` `check_write`). It is a **planned** acceptance step (see Acceptance E2E below) |
| **O-8** | **Chat sources beyond the D-5 ruling** | Source scope is an architect decision (D-5), not a planning assumption |
| **O-9** | **Docker** | House rule — native only, no Docker on this host |
| **O-10** | **Editing `agents/ari/*.md` prompt artifacts** | Prompt artifacts are DB-versioned, human-session-only (`daemon/tools/prompt_artifacts.py`). The T3.5 re-stamp is an *acceptance subject*, not a lane deliverable |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| **1** | **Token, lane and ledger primitives** | A durable per-item token store, the `assent-relay` actor lane, an attestation-bearing repository entrypoint, and the audit payload extension | 9 | tight with P2 (the tool consumes the token API) · tight with P3 (the service consumes the repo entrypoint) · independent of P4 | pending |
| **2** | **Tool surface and registration** | The `prompt_assent` tool, its `ari`-EXACTLY three-layer registration, the complete pin set, and the pending-list read surface | 9 | tight with P1 (token API) · tight with P3 (the tool calls the service) · loose with P4 (the kill-switch config lands in P4) | pending |
| **3** | **Acceptance path and provenance flow** | The service-level acceptance path, the shared approver predicate, the end-to-end provenance chain, and the FE-unchanged proof | 7 | tight with P1 (repo entrypoint) · tight with P2 (tool) · independent of P4 | pending |
| **4** | **Guardrails, test matrix, staged release** | The kill-switch, the full test matrix, the metrics-vocabulary bump, the STAGED RELEASE, and the runbook | 8 | loose with P1–P3 (the matrix exercises them) · independent of P2's registration except the count pins | pending |

### Shared-context grouping

The four phases are grouped by the **context they share** — the file clusters they touch. A
context is owned by exactly one phase, so no two phases edit the same files concurrently (the
house rule on concurrent commits in a shared worktree).

| Context (file cluster) | Owner phase | Files |
|---|---|---|
| **C-1 — token persistence** | **P1** | `daemon/repositories/prompt_assent/{models,repository}.py` (NEW) · `daemon/migrations/versions/<ts>_create_prompt_assent_tokens.sql` (NEW) · `daemon/api.py` (create_all seam) |
| **C-2 — prompt-version core** | **P1** (repo side) → **P3** (service side) | `daemon/repositories/prompt_version/repository.py` · `daemon/repositories/prompt_version/models.py` · `daemon/services/prompt_version_service.py` |
| **C-3 — tool surface** | **P2** | `daemon/tools/prompt_assent.py` (NEW) · `daemon/tools/instance.py` · `daemon/tools/_tool_registry.py` · `daemon/checkpoint_metrics.py` · `agents/ari/meta.json` |
| **C-4 — configuration** | **P4** | `daemon/config.py` (`PromptAssistConfig`) |
| **C-5 — tests** | **P4** (matrix) · **P2** (registration pins) | `tests/unit/tools/test_prompt_assent_registration.py` (NEW) · `tests/unit/test_prompt_assent_lane.py` (NEW) · the 15+ existing pin files |
| **C-6 — release surface** | **P4** | `CHANGELOG.md` · `docs/runbooks/prompt-assent.md` (NEW) · the staged release artefacts |

**Sequencing note.** C-2 is split **deliberately**: P1 owns the *repository* side of
`prompt_version` (the actor lane, the attestation-bearing entrypoint, the audit payload), and P3
owns the *service* side (the acceptance predicate and the provenance flow). The two halves meet
at the attestation type, which P1 defines and P3 consumes. If the architect prefers a single
owner for C-2, merge P1 and P3 into one phase — but do **not** let two agents edit
`daemon/repositories/prompt_version/repository.py` concurrently.

### Dependency map

```
                    ┌─────────────────────────────────────────────┐
                    │ P1  Token, lane and ledger primitives        │
                    │  • prompt_assent_tokens table + own .sql     │
                    │  • create_all seam (api.py)                  │
                    │  • AUTH_METHOD_ASSENT_RELAY + constructor    │
                    │  • attestation-bearing repo entrypoint       │
                    │  • audit payload extension                   │
                    └───────────────┬─────────────────────────────┘
                                    │ token API (mint/find/consume/attempts)
                                    │ attestation type
                                    ▼
                    ┌─────────────────────────────────────────────┐
                    │ P2  Tool surface and registration           │
                    │  • daemon/tools/prompt_assent.py (NEW)       │
                    │  • ari-EXACTLY 3-layer registration          │
                    │  • CATEGORY_MODULES / DYNAMIC / KNOWN /      │
                    │    PRIVILEGED_TOOL_CATEGORIES (#21)           │
                    │  • agents/ari/meta.json allow tail           │
                    │  • list_pending repo+service+read surface   │
                    └───────────────┬─────────────────────────────┘
                                    │ calls the service (in-process)
                                    ▼
                    ┌─────────────────────────────────────────────┐
                    │ P3  Acceptance path and provenance flow     │
                    │  • is_acceptable_approver(actor, attest)    │
                    │  • service-level second accepted path       │
                    │  • 3-factor gate + freshness bound (D-6)    │
                    │  • provenance chain end-to-end               │
                    │  • FE-unchanged proof                       │
                    └───────────────┬─────────────────────────────┘
                                    │ exercises everything
                                    ▼
                    ┌─────────────────────────────────────────────┐
                    │ P4  Guardrails, test matrix, staged release │
                    │  • PromptAssistConfig kill-switch           │
                    │  • FULL TEST MATRIX (happy + 5 refusals)    │
                    │  • metrics vocabulary bump (v8→v9)          │
                    │  • runbook + CHANGELOG                      │
                    │  • STAGED RELEASE ONLY — no live promote    │
                    └─────────────────────────────────────────────┘
```

**Hard dependencies (non-negotiable):**
- **P1 → P2**: the tool cannot exist without the token API and the attestation type.
- **P1 → P3**: the service cannot accept the relay path without the repo entrypoint.
- **P2 → P3**: the end-to-end provenance flow is only observable through the tool.
- **P2 + P3 → P4**: the test matrix exercises both.

**Soft dependency (mitigated, not blocking):**
- **P4's kill-switch class.** P2's tool must read the switch at call time, but the
  `PromptAssistConfig` class lands in P4. **Mitigation:** P2's tool resolves the switch through a
  **defensive resolver** that treats a missing config class as **off** — so P2 alone is safe to
  land (the tool refuses with `lane-disabled`), and P4's class is what turns the lane ON. This is
  fail-closed and removes the ordering hazard entirely.

---

## Acceptance Criteria

### A-1 — MANDATORY TEST MATRIX

> **[approve happy path with real token echo; REJECTS: wrong token, expired token, non-pending row, agent-self-approval attempt, non-user-origin turn]**

This matrix is **mandatory and verbatim**. Every cell must have a named test, and every REJECT
cell must assert: **a typed refusal code**, **no state change** on the pending row, and **no token
burn** (house rule: *"refusals never burn"*, `daemon/tools/upgrade_tools.py:3708-3777`).

| Cell | Expected refusal code | Required assertions |
|---|---|---|
| **approve happy path with real token echo** | *(success)* | The row transitions `pending_approval → approved`; exactly **one** new `prompt_audit_events` row is written; the payload carries approving user, source, message id, relay agent, timestamp, token fingerprint (never the raw token); the token row is `consumed` with `consumed_by_message_id` set |
| **REJECT: wrong token** | `token-mismatch` | Row state unchanged; token **not** burned; the attempt counter increments |
| **REJECT: expired token** | `token-expired` | Row state unchanged; token not burned |
| **REJECT: non-pending row** | `item-not-pending` (carrying the current state) | Row state unchanged; the terminal row is untouched |
| **REJECT: agent-self-approval attempt** | `self-approval-attempt` | Row state unchanged; **and** the assertion is documented as belt-and-braces — the load-bearing defence is that an agent-originated turn cannot arm factor 2 (`daemon/manager.py:6132-6133`) |
| **REJECT: non-user-origin turn** | `no-user-origin-turn` (naming the observed source + classifier detail) | Row state unchanged; token not burned; the refusal **names** the observed source — the W1 hardening (`daemon/tools/upgrade_tools.py:3393-3419`) |

**Additional matrix cells (beyond the mandatory six, from the refusal taxonomy):**
`user-confirmation-missing` · `lane-disabled` · `user-origin-window-expired` ·
`user-origin-window-stale` (D-6/J-2) · `assent-verification-unavailable` ·
`token-already-used` · `token-superseded` · `token-item-mismatch` · `token-locked` ·
`item-not-found` · `item-not-assentable` · `already-decided` · `audit-unavailable`.

**Matrix rules:**
- The happy path must use a **real token echo** — the token must be present in the **content of
  the exact message** identified by the factor-2 window, not merely in a tool argument.
- Every REJECT cell must prove **no audit event was written** for a decision (refusals write an
  INFO line, not a decision event) and **no state change** occurred.
- **Net-new tests = FAILED-list diff against a TRUE pre-fix base, run with the same commands.**
  A test that was already failing before the change does not count as a new red.
- Non-vacuity: each matrix cell must have a **counter-mutation** that reddens it
  (house mutation rule — absence-only and raw-text pins are prose-satisfiable).

### A-2 — E2E acceptance step (planned; NOT executed in this lane)

**Shaped like the owed T3.5 re-stamp.** The T3.5 re-stamp is a prompt-version re-approval for
`agents/ari/tools_note.md` — VERIFIED `grep -c memoir_about` = **0** today, matching
`.agents/tester/RESULTS/2026-11-10-memoir-v2-gate.md:178,278`: *"re-stamp verified NOT applied
(versioned-prompt-artifact lane, human session only)"*. It is **owed**, it is exactly this lane's
shape, and it is **human-session-only** — which is why it is a natural acceptance subject for a
lane whose entire purpose is to make human approval reachable from chat.

**The planned acceptance step (to be executed AFTER the staged release is promoted by the
Emperor's ceremony — NOT in this lane, NOT in the developer lane):**

| # | Step | Expected observation |
|---|---|---|
| **E2E-1** | A PENDING `prompt_versions` row exists for `agents/ari/tools_note.md` (the T3.5 re-stamp candidate), in state `pending_approval`, CORE class | The row is visible to `repo.list_pending()` |
| **E2E-2** | Ari calls `prompt_assent(action="mint", version_id=<that row>)` | The response carries the item identity, the **diff** (pending content vs effective content, rendered from `repo.get_effective` — `daemon/repositories/prompt_version/repository.py:1900-1945` — vs the pending row's `content`, the same pair the REST diff route uses, `daemon/routers/prompts.py:560-600`, `against=effective`), the **token** (grouped), and the expiry |
| **E2E-3** | Ari shows the Emperor the diff + token in chat; the Emperor replies with the token **verbatim** in that message's content | The reply's `source` classifies as user-origin; the window is stamped for Ari's instance |
| **E2E-4** | Ari calls `prompt_assent(action="approve", version_id=<that row>, token=<echoed>, user_confirmed=True)` **in that same turn** | The row transitions to `approved`; one `prompt_audit_events` row is written with the full relay provenance |
| **E2E-5** | Read the ledger back | The event's payload carries approving user (`discord:<user_id>`), source, approving **message id**, relay agent `ari`, timestamp, `token_fingerprint` — and **no raw token** anywhere |
| **E2E-6** | The effective prompt for `agents/ari/tools_note.md` now reflects the approved content | `repo.get_effective` returns the newly approved content |

**Explicitly NOT executed now.** The artifact write path is **human-session-only** by design
(`daemon/tools/prompt_artifacts.py` `check_write`, def at `:265`); prompt artifacts are
DB-versioned, not file-written. This lane delivers the **plan** for the acceptance step, not the
step itself.

### A-3 — Feature-level acceptance criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | A relayed approval transitions a CORE PENDING row to `approved` | E2E-2 … E2E-5 above, plus a service-level integration test | The row's state is `approved`; exactly one decision event exists |
| 2 | The FE lane is unchanged | Diff the FE routes and the FE service/repo methods against the base commit | **Zero** behavioural diff; the existing FE test suites pass byte-identically |
| 3 | The audit ledger reconstructs the full chain | Read the `prompt_audit_events` payload of a relayed decision | All of: approving user, source, message id, relay agent, timestamp present; **raw token absent** |
| 4 | No approval is possible without a genuine user turn | The `non-user-origin turn` matrix cell + a direct unit test that an agent-originated dispatch clears the window (`daemon/manager.py:6132-6133`) | Every attempt without a live user-origin window is refused; **zero** state changes |
| 5 | A token is single-use and item-bound | Replay matrix cells (`token-already-used`, `token-item-mismatch`, `token-superseded`) | Zero second transitions; every replay refused with its distinct code |
| 6 | The token never appears in durable state | Grep the token table + the audit ledger for the raw token after a mint→approve cycle | **Zero** hits; only `sha256(token)[:12]` fingerprints |
| 7 | The lane is `ari`-EXACTLY | The ari-ONLY census (the category appears in `agents/ari/meta.json` and **nowhere else** across every `agents/*/meta.json` — `tests/unit/test_notify_user_registration.py:23-25`) + the in-body gate | The category is in ari's allow list and in **no other** agent's; a non-ari caller receives the generic not-ari envelope |
| 8 | The kill-switch disables the lane without removing the surface | With `ENSEMBLE_PROMPT_ASSENT_ENABLED=0`, call the tool | Refusal `lane-disabled`; the tool is **still registered** (refusal-at-call, never construction-absence) |
| 9 | The pin set is complete and green | The three canonical greps (`daemon/tools/_tool_registry.py:412-417`) → bump every hit; run the affected unit tests | Zero failures; the count assertion reads **21** everywhere |
| 10 | The lane is released **staged only** | The staged release artefacts | No live promote; no ceremony performed by this lane |

---

## House Constraints (binding on every phase)

| # | Constraint | Where it bites |
|---|---|---|
| **H-1** | **Every new SQLModel table requires its own `*.sql` in `daemon/migrations/versions/` — zero exceptions.** The migration directory currently holds **127** `*.sql` files (observed at `0efdf106f`); the new token table makes it 128 | **P1** — the new `prompt_assent_tokens` table gets `YYYYMMDD_HHMMSS_create_prompt_assent_tokens.sql`, UP first / DOWN last, every statement idempotent (`IF NOT EXISTS` / `IF EXISTS`), index names **byte-identical** to the SQLModel `__table_args__` (parity test: `tests/migration/test_prompt_versions_index_parity.py`) |
| **H-2** | **The new table rides the `create_all` seam (`daemon/api.py:1142-1143`) and thereby AVOIDS the `_ensure_postgres_columns` one-transaction boot-abort surface (`daemon/manager.py:10036-10038`).** Both facts are stated deliberately: the new-table rule is what keeps this lane off the boot-abort path | **P1** — a missed import at the seam = **no table on PG** = every token INSERT fails (the "N1 REQUIRED seam" note at `daemon/api.py:1142-1143`). A brand-new table needs **no** `_ensure_postgres_columns` mirror. If anyone later adds a **column** to an existing table for this lane, they re-open the boot-abort surface — treat that as a release-gate event |
| **H-3** | **No Docker — native only** | Every phase |
| **H-4** | **STAGED RELEASE ONLY — no live promote from this lane. The ceremony belongs to the Emperor** | **P4** — deliver a staged release + runbook; stop there |
| **H-5** | **Kill-switch env knob** (`ENSEMBLE_PROMPT_ASSIST_ENABLED`); read at call time; the tool stays registered. **D-2 recommends fail-open (default ON) — flagged for the architect** | **P4** (config class) + **P2** (call-time read) |
| **H-6** | **Net-new tests = FAILED-list diff vs a TRUE pre-fix base, same commands.** A test already failing before the change does not count as a new red | **P4** |
| **H-7** | **Env scrub PER-VARIABLE.** `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) — scrub one by one: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>` | Every test phase |
| **H-8** | **pytest conventions per `.agents/tester/rules/ensure.md`**: `testpaths=["tests"]`; four dirs deselected (integration / postgres / slow / conformance); `timeout=30` + `thread`; `-p no:timeout`; pass explicit `tests/` lists; `pytest -q` buffers tracebacks to session end ⇒ `tee LOG >/dev/null` + `tail -2 LOG`; use the **worktree's own venv**, never the main checkout's `.venv`; always `cd` into the worktree first | **P4** (and every phase's test step) |
| **H-9** | **Net-new SQLModel tables + `grep_files` caveat:** `grep_files` silently skips files > 1.4 MB — use `bash grep -n` / `sed -n` for `daemon/tools/upgrade_journal.py`, `daemon/tools/upgrade_tools.py`, `daemon/routers/prompts.py`, `daemon/tools/prompt_artifacts.py` | Every phase |
| **H-10** | **Derive the pin set BY GREP, never from the census prose.** The SAME-PR block at `daemon/tools/_tool_registry.py:379-406` enumerates 14 pin files but is **STALE** — grep finds a **15th** (`tests/unit/tools/test_prompt_reload_pins.py:71`). **The prose must be corrected in this change** — it is the artifact that gets copied forward | **P2** |
| **H-11** | **One naming string: `prompt-assent`.** Never `prompt-assist` — that spelling is a carry-over error in `research/trust-anchors.md` (§4.5, §5, §7, title) and in a sibling lane that drifted | Every phase |

---

## Risks (summary)

Full register with mitigations and structural pins: [`risks.md`](risks.md).

| Theme | Headline risk | Mitigation |
|---|---|---|
| Authorization | **The lane can change Ari's own guardrails** — a self-modifying trust boundary | Token bound to the item and shown **with the diff**; the ledger records the full chain; the lane is privileged + ari-EXACTLY; **D-2** is the architect's dormancy lever |
| Freshness | **Window staleness via silent resume** (`daemon/manager.py:11503`) — an earlier turn's window can survive | **D-6 / J-2** — a ≤10-minute `stamped_at` bound on top of `expires_at` |
| Entropy | **Guessing loop** against a short token | **D-3 / J-1** — 8 chars / 40 bits (house value); if 6 chars is kept, the attempt counter + lockout-at-5 + a per-instance bound become mandatory |
| Confused deputy | **Ari presents a stale or truncated diff** | **J-3** — bind the token to a **content hash** of the pending row (anti confused-deputy) |
| Boot safety | A schema change aborts boot (one-transaction ensure list) | **D-1**'s new-table rule avoids the ensure list entirely |
| Audit integrity | Provenance drifts apart from the state change | Same-transaction write + **SP-2** (no UPDATE/DELETE on `prompt_audit_events`) + **SP-1** (content-hash binding) |
| Divergence | The classic and persona surfaces end up with different trust rules | **D-7**'s explicit deferral of the persona surface (O-1) |
| Process | The stale pin census prose misleads the next lane | **H-10** — derive by grep; fix the prose in this change |

---

## Open Questions for the Architect

> **This plan is Ready for Review, not Approved.** Nine decisions and three judgement calls need
> the architect's ruling before the developer lane lands them. Each carries the technical
> analysis's recommendation; the architect may overrule any of them. Full rationale in
> [`decisions.md`](decisions.md).

### The nine numbered decisions (D-1 … D-9)

| # | Decision | Options | **Recommendation** | Cost of overruling |
|---|---|---|---|---|
| **D-1** | Token store persistence | (a) new PG table · (b) columns on `prompt_versions` · (c) in-memory | **(a) new PG table `prompt_assent_tokens`** — no `_ensure_postgres_columns` entry ⇒ **no boot-abort surface**; survives restart; independent GC; correct if two daemons ever share a PG | (b) puts a mint write on the hottest prompt table and re-opens the one-transaction boot-abort surface (`daemon/manager.py:10036-10038`); (c) loses tokens across a routine daemon restart inside a 60-minute window |
| **D-2** | Kill-switch default | fail-open (default ON) · fail-closed (default OFF) | **fail-open (default ON)** — read at call time, tool stays registered, gate order = ari → switch → 3-factor → mutate. Mirrors the two closest precedents (`notify_user`, `prompt_reload`) | The architect's explicit lever to keep the lane dormant until first use. **This lane is the first in the tree that can change a live agent's own guardrails through a chat message** — if the Emperor wants that posture, default-OFF costs one env var per environment |
| **D-3** | Token length | 8 chars / 40 bits (house) · 6 chars / 30 bits (commission) | **8 chars** — costs nothing operationally; removes the brute-force argument | 6 chars is acceptable **only** with the per-item attempt counter + lockout-at-5 + a global per-relay-instance bound (§J-1) |
| **D-4** | Single-use enforcement point | consume-on-success (separate txn) · consume inside the transition txn | **transition-then-consume, preferably same-transaction** — the state-machine CAS already blocks double-apply; the ordering exists so a *refusal* never burns the Emperor's token | Consume-then-transition burns the token on a non-decision. Same-transaction requires extending a **core** method (`atomic_transition`) — an architect call |
| **D-5** | Which chat sources, and the per-source "approving user" key | discord-first · all registered chat types | **discord first**, with the user key taken from the adapter metadata `user_id` — **not** from `external_user_id` (a guild-channel `external_user_id` is a **CHANNEL**) | A broader source set requires explicit per-source extraction rules; an implicit extraction is a correctness bug waiting to happen |
| **D-6** | Extra window-freshness bound | none (promote parity) · bound `stamped_at` to ≤10 min | **bound it** — the silent-resume path (`daemon/manager.py:11503`) can leave an earlier turn's window alive; this lane changes live personas | Promote parity would accept a 60-minute-old window — looser than we want for a persona-changing lane |
| **D-7** | Surface scope | classic `prompt_versions` only · both | **classic `prompt_versions` only (Phase 1)**; the persona lane is a different table/repo/audit shape and doubles the blast radius of a trust-model change | "Both" requires a second ledger for the persona surface (persona rows write **no** `prompt_audit_events`) — **not covered by this plan** |
| **D-8** | Approver-identity vocabulary | new lane `assent-relay` · widen `session` · reuse `bearer` | **new lane `assent-relay`** + a dedicated constructor + an attestation-bearing repo entrypoint; `approve()`/`reject()` unchanged | Widening `session` forges the FE lane and makes the chat lane indistinguishable from it in the ledger — the opposite of the commission's goal |
| **D-9** | Audit event type | keep `APPROVED`/`REJECTED` + a payload discriminator · mint `ASSENT_APPROVED`/`ASSENT_REJECTED` | **keep the existing types** + `approval_path` discriminator | A new type **silently drops** relayed approvals from any existing `event_type == "APPROVED"` view — a forgetting bug in the audit trail, the worst kind of bug here |

### The three flagged judgement calls

The analysis made three judgement calls of its own and flagged them for overrule. Two are
embodied in the numbered decisions above; the third is **not** part of D-1…D-9 and needs its own
ruling.

| # | Judgement call | Analysis position | **What the architect is being asked** |
|---|---|---|---|
| **J-1** | **Token length: 8 chars (recommended) vs the commission's "~6-char"** | 8 base32 chars / 40 bits — the house `mint_nonce` value (`daemon/tools/upgrade_journal.py:205-208`). It costs nothing operationally and removes the brute-force argument | Accept 8, or keep 6 — in which case the **per-item attempt counter + lockout-at-5 + a per-relay-instance bound (≤10 mismatches/hour)** become **mandatory**. Note the token is **not** the security boundary: factors 2+3 are conjunctive and agent-originated messages **cannot** arm factor 2 (`daemon/manager.py:6132-6133`) |
| **J-2** | **Window freshness bound: ≤10 minutes (recommended) vs promote parity** | Require `window["stamped_at"]` to be within **10 minutes** of the tool call, in addition to `expires_at`; refusal `user-origin-window-stale` | Accept the extra fourth check, or accept promote parity (any unexpired window). It exists because the dispatch funnel **skips the stamp on a silent resume** (`daemon/manager.py:11503`), so a paused→resumed Ari can hold an earlier turn's window |
| **J-3** | **Content-hash binding of the token to the pending row (anti confused-deputy)** | Bind the token to a **content hash** of the pending row, so a mutated pending row invalidates the token. Cost: one hash column, computed at mint and re-checked at approve. This is an addition **beyond** the commission's three factors | Accept this **fourth** binding as in-scope for Phase 1, or defer it. Ari is a confused deputy by construction; this is what stops a prompt-injected Ari from getting a token approved against a **stale or truncated diff** |

### Also for the architect's attention (not a decision, but a disclosure)

- **The commission's ROUTING requirement named the wrong stack.** It referred to *"the v2
  persona-approval `HUMAN_LANE_REQUIRED` path"* — which is the **memoir persona table**
  (`daemon/repositories/memoir/repository.py:220-262`, `daemon/routers/memoir.py:143-208`), not
  `prompt_versions`. Phase 1 covers the classic surface only (**O-1**, D-7). The architect may
  want to re-read the commission's intent against this.
- **The persona surface would need its own ledger.** Persona rows write **no**
  `prompt_audit_events`; if it is ever in scope, "with provenance" means a new provenance
  mechanism, not the existing one.

---

## Research Insights

The findings that shaped this plan, with anchors. Full notes:
[`technical-analysis.md`](technical-analysis.md) ·
[`research/approval-path.md`](research/approval-path.md) ·
[`research/trust-anchors.md`](research/trust-anchors.md).

| # | Finding | Anchor | Consequence for the plan |
|---|---|---|---|
| 1 | "A human approved this" means **exactly one thing** today: `AuthContext.auth_method == "session"` — checked **four** times in defence in depth | service `daemon/services/prompt_version_service.py:677-717`; router `daemon/routers/prompts.py:639-640`, `:680-681`; repo `daemon/repositories/prompt_version/repository.py:1560-1565`, `:1594-1599` (F4); clamp `:1433-1444` | A relayed approval **cannot** reuse the existing gates — hence D-8's new lane + attestation entrypoint |
| 2 | The lane vocabulary is a **frozenset of exactly three** (`session`/`bearer`/`internal`) and `internal` is banned for approvals by name | `daemon/repositories/prompt_version/repository.py:503-511` | The hard part of this design is **naming** the relay approver, not the trust model |
| 3 | Three in-tree precedents make it tractable: the per-turn user-origin window, the message-content echo read, and the append-only audit row written **inside the state-transition transaction** | `daemon/manager.py:6126-6131`; `daemon/tools/upgrade_tools.py:3508-3531`; `daemon/repositories/prompt_version/repository.py:1493-1511` | No schema work for provenance; no new trust model — a re-scaled reuse |
| 4 | The audit row is written in the **same `engine.begin()`** as the state UPDATE | `daemon/repositories/prompt_version/repository.py:1457-1511` | Provenance lands in the same COMMIT as the state change → SP-2's pin is about *keeping* it that way |
| 5 | The audit payload is already a heterogeneous JSONB blob; the event-type vocabulary is **code-level only** | `daemon/repositories/prompt_version/models.py:532-536`, `:134-147`, `:120-133` | DECIDED-1 (payload-only) and D-9 (keep the types) — **zero migration** |
| 6 | A **brand-new table** needs no `_ensure_postgres_columns` entry — it rides the `create_all` seam, whose comment says so explicitly | `daemon/api.py:1142-1143` | D-1's key advantage, and H-2 |
| 7 | The ensure list is **one transaction for the whole statement list** with **no per-statement try/except** — any entry's failure **aborts boot** | `daemon/manager.py:10036-10038` | The whole reason D-1 chooses a table over columns; also why "just add a column later" is a release-gate event |
| 8 | **No global pending-enumeration exists** — `/versions` needs a full key and post-filters state; `pending-count` is a scalar | `daemon/routers/prompts.py:444-541`, `:500-507`, `:526-527`; `daemon/repositories/prompt_version/repository.py:1843-1866`, `:2999-3029` | BLESSED-5 / DECIDED-2 — the lane must add `list_pending` itself |
| 9 | The **ari-EXACTLY** posture is three layers, and the category alone **cannot** spell "ari only" | `daemon/tools/notify_user.py:19-30`; in-body constants `daemon/tools/prompt_reload.py:70`, `daemon/tools/memoir.py:183` | S-10 — all three layers, plus the ari-ONLY census |
| 10 | The **SAME-PR** rule: adding a category requires updating **all** pin sites **in the same commit**; the census must be **derived by grep** | `daemon/tools/_tool_registry.py:366-417` | H-10 + P2's pin task |
| 11 | The census prose is **STALE** — it lists 14 pin files; grep finds a 15th (`tests/unit/tools/test_prompt_reload_pins.py:71`) | `daemon/tools/_tool_registry.py:379-406` vs `tests/unit/tools/test_prompt_reload_pins.py:71` | H-10 — derive by grep, and **fix the prose in this change** |
| 12 | Discord's `external_user_id` is `dm:<user_id>` in a DM but `<guild_id>:<channel_id>` in a guild channel — i.e. a **channel**, not a user | `daemon/sources/adapters/discord/adapter.py:994-1021`; metadata at `:1035-1058` | BLESSED-3 / D-5 — the approving user comes from the adapter's `user_id` |
| 13 | The user-origin window is **in-memory**, and the dispatch funnel **skips the stamp on a silent resume** | `daemon/manager.py:2001`, `:11503` (`:11489-11505` is the single dispatch funnel) | D-6 / J-2 — the ≤10-minute freshness bound |
| 14 | The window is **cleared** on every non-user-origin dispatch | `daemon/manager.py:6132-6133` | The real reason factor 2 cannot be spoofed by an agent |
| 15 | `reject` has **no reason parameter**, the FE reject body is **deliberately empty**, and there is **no `reject_reason` column** | `daemon/repositories/prompt_version/repository.py:1577-1607`; `daemon/routers/prompts.py:318-322`; `daemon/repositories/prompt_version/models.py:331-436` | DECIDED-1 — the reason lives **only** in the audit payload, at the **top level** |
| 16 | `MEMORY`-class rows are born-effective and never enter `pending_approval`; the count predicate is CORE-only | `daemon/repositories/prompt_version/models.py:158-166`; `daemon/repositories/prompt_version/repository.py:3024-3027` | The lane only ever sees CORE rows; `item-not-assentable` covers the refusal shape |
| 17 | `atomic_transition` takes **no advisory lock** — its CAS is the serializer | `daemon/repositories/prompt_version/repository.py:871-875`, `:1459-1464` | No lock work is needed for the lane's transitions |
| 18 | The promote lane's **dry-run mint** and **`issued_to_instance` binding** and **per-destination hourly cap** do **not** scale down | `daemon/tools/upgrade_tools.py:3346-3356`; `research/trust-anchors.md` §2 | DECIDED-3 (`mint` is an explicit action) · DECIDED-1's item binding · R-11 in `risks.md` |
| 19 | `notify_user`'s ledger-write-failure rule is **fail-closed** | `daemon/tools/notify_user.py:95-97` | The `audit-unavailable` refusal — no state change when the ledger write fails |
| 20 | The "16-site" pin count is **not re-derivable**; the real set/count-pin file count is **15** | `research/trust-anchors.md` §4.5 vs the grep in `technical-analysis.md` §9 | H-10 — **derive, never copy a count** |
| 21 | The research notes were anchored in a **sibling worktree** (`/home/nea/ensemble-src-wt-v2915-bump`) because the commissioned worktree was deleted mid-lane; the notes' line numbers held | `research/trust-anchors.md` §0, §9 | The anchors used by this plan were **independently re-verified in the commissioned worktree** by the technical-analysis lane; the load-bearing ones were re-verified again by this plan lane (see §Verification below) |
| 22 | The owed **T3.5 re-stamp** for `agents/ari/tools_note.md` is un-applied (human-session-only) and is exactly this lane's shape | `grep -c memoir_about` = **0**; `.agents/tester/RESULTS/2026-11-10-memoir-v2-gate.md:178,278` | The E2E acceptance subject — A-2 |

### Verification performed by this plan lane

Every load-bearing anchor cited in this plan was re-verified **in the commissioned worktree**
(`0efdf106f`, branch `feature/prompt-assent`) with `bash grep -n` / `sed -n` — never
`grep_files` (which silently skips files > 1.4 MB, per H-9). Confirmed directly:

`_AUTH_METHODS` (3 values) at `daemon/repositories/prompt_version/repository.py:503-511` ·
the identity clamp at `:1433-1444` · F4 at `:1560-1565` · the memo/persona lane at
`daemon/repositories/memoir/repository.py:220-262` and `daemon/routers/memoir.py:143-208` ·
the silent-resume skip at `daemon/manager.py:11503` · the window clear at `:6132-6133` ·
the one-transaction ensure at `:10036-10038` · the `create_all` seam at `daemon/api.py:1142-1143` ·
Discord `external_user_id` at `daemon/sources/adapters/discord/adapter.py:994-1021` and the
`user_id` metadata at `:1035-1058` · the stale census at
`daemon/tools/_tool_registry.py:379-406` vs the 15th pin file
`tests/unit/tools/test_prompt_reload_pins.py:71` · the ari allow-list tail
(`agents/ari/meta.json` → `"prompt-reload"`) · `count_pending`'s CORE-only predicate at
`daemon/repositories/prompt_version/repository.py:2999-3029` ·
`daemon/repositories/prompt_version/models.py:134-147` · the `PROMPT_AUDIT_EVENT_TYPES` frozenset ·
the migration directory's current file count (**127**).

---

## Phase summaries

| Phase | File | Delivers | Exit criterion |
|---|---|---|---|
| **1** | [`phase1-plan.md`](phase1-plan.md) | The token table (+ its own `.sql`), the `assent-relay` actor lane, the attestation-bearing repo entrypoint, the audit payload extension | A token can be minted, found, consumed and swept in PG; a relay actor can be constructed **only** with a complete attestation; an audit event carries the full relay provenance — all without touching the FE path |
| **2** | [`phase2-plan.md`](phase2-plan.md) | The `prompt_assent` tool, the three-layer ari-EXACTLY registration, the complete (grep-derived) pin set, the pending-list read surface | `prompt_assent` is registered, privileged, ari-only, and the pin suite is green with the count at **21** |
| **3** | [`phase3-plan.md`](phase3-plan.md) | The service-level acceptance path, the shared approver predicate, the 3-factor gate with the freshness bound, the end-to-end provenance chain | The **mandatory test matrix** passes — the happy path with a real token echo, and all five mandated refusals; the FE path is provably unchanged |
| **4** | [`phase4-plan.md`](phase4-plan.md) | The kill-switch, the full test matrix, the metrics-vocabulary bump, the runbook, and the **STAGED RELEASE** | The lane is disabled by env, disabled-by-default in a fresh environment per D-2's ruling, documented, and released **staged only** |
