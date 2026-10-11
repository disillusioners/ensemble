# Phase 6 — Runbook + CHANGELOG + Rollout

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMEND v1.4 / module-lanes — runbook module-lane content (user ruling; decisions.md D19/D20).** T6.1 gains a **§Module-lane key management** subsection: module key management is INDEPENDENT of OAuth2; **OAuth2 does NOT govern module endpoints** (webhook ingest, MCP-KB mounts — they authenticate with their own per-module keys; module-lane passes the middleware untouched in BOTH auth states, FR-42); operator note on the **current fully-open MCP-KB state** (kb_server.py zero auth — pre-existing posture) + the module-lane key mechanism improvement note (independent of OAuth2); `webhook_secret` guidance re-framed as **existing best practice for the pre-existing open-ingest posture (webhooks.py:67), NOT auth-enablement config — the v1.2/A6 "set webhook_secret BEFORE enabling auth" Enable-step framing is SUPERSEDED** (webhook senders are auth-INDEPENDENT; row 17 verdict YES UNCHANGED). Webhook-secret ADMINISTRATION runs through the existing `/api/sources` CRUD (OAuth2-governed; masking decision per §12 item 4(c), AC-42); MCP-KB keys via the NEW `/api/mcp/kb/keys` family (FR-41). T6.5 sweep adds the module-lane checks.

> **AMENDED v1.1 runbook additions (per architect §1.1 D1 + §1.3 C2 +
> resilience note):**
> - **KB-MCP loopback trust rationale (D1):** `docs/runbooks/oauth2-auth.md`
>   §KB-MCP-auth posture — explains why `/api/mcp/kb` and `/api/mcp/kb/sse`
>   are PUBLIC in v1 (Starlette 0.52.1 Mounts traverse parent middleware,
>   so PROTECTED would 401 every cross-process KB-MCP consumer on day 1);
>   documents the loopback trust model and notes the KB-MCP-auth seam is
>   reserved for a future commission.
> - **Bootstrap env-var removal step (C2):** the runbook §Token mint
>   procedure (a) instructs the operator to remove
>   `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` from the daemon environment after
>   the first admin mint, and (b) plainly states that an agent holding
>   bash + DB access can still invoke the mint CLI directly — the
>   `ens_env_read` deny-list (FR-16a) does not close that lane; same
>   trust class as every other DB-adjacent agent capability.
> - **Dual-sweeper same-DB note (resilience):** the runbook §Sweeper
>   notes that two ensemble instances on different ports pointing at
>   the same DB (the dev-checkout scenario) is safe — `SKIP LOCKED` +
>   idempotent deletes make concurrent sweeps non-interfering. No
>   guard is built; operators do not need to coordinate sweeps.

## 1. Objective

Ship the operator-facing documentation and the rollout story:
`docs/runbooks/oauth2-auth.md` (7 sections per FR-35, live-views v2.0
pattern), the CHANGELOG entry, the default-OFF rollout verification note,
and the CN cross-links ([TA] §Citations section).

Entry prerequisites: P5 (canary evidence + suite output feed the runbook).

## 2. Decisions

| ID | Decision | Rationale / source | Rejected alternatives |
|----|----------|--------------------|------------------------|
| D6.1 | Runbook follows the live-views v-pattern (`docs/runbooks/live-views.md`: single-edge-rule → scope → operator notes → troubleshooting → security notes → cross-references → deep-dive annexes) with the 7 FR-35-mandated sections mapped onto it | FR-35; NFR-17; [TA] §Technical Debt paydown #1-2 | ad-hoc README |
| D6.2 | Rollout posture: **merge with auth OFF**; enabling is a pure operator config+restart act; no live promote of an auth-ON daemon in the same step | [REQ] §6.5 operator UX; A8 (restart on config change); default-OFF contract UR-1 | shipping enabled-by-default (violates FR-1) |
| D6.3 | OD defaults are recorded in the runbook "Decisions & defaults" annex so operators can see which knobs are OPEN-vs-DECIDED without reading planning artifacts. **AMENDED v1.1:** all OD items are now DECIDED (per the architect's CONDITIONAL PASS — see `decisions.md` and `plan-overview.md §7`); the annex documents the closure plus the runbook-side notes (KB-MCP loopback trust, bootstrap env-var removal, dual-sweeper) | Ledger traceability (plan-overview §7) | burying defaults in code |

## 3. Tasks

| ID | Task | Files touched | What changes (plan level) | Satisfies |
|----|------|---------------|---------------------------|-----------|
| T6.1 | Author `docs/runbooks/oauth2-auth.md` — sections: (a) Enable procedure (IdP registration walkthrough, env vars, redirect_uri override + pre-warm note, first-token mint via CLI+BOOTSTRAP_TOKEN, **AMENDED v1.1 / C2 (c): bootstrap env-var removal step after first admin mint + (d) trust-class disclosure that bash+DB agents can still reach the mint CLI**, **AMEND v1.2 / A6 (external webhook senders Enable-step):** explicit "per-source `webhook_secret` MUST be set BEFORE enabling auth" instruction — covers the new §7 row 17 (Telegram/Slack/Discord/WhatsApp sources without `webhook_secret` configured at `webhooks.py:63-74` become the dangerous day-1 footgun; the runbook Enable step walks the operator through setting `webhook_secret` per source), verification commands = T5.6 probe script), (b) Default-OFF verification (AC-S1 curl walkthrough + suite evidence), (c) Token mint procedure (CLI examples + output sample + storage/rotation guidance from P4 T4.6 — consumer enablement guide; **AMEND v1.2 / Minor #12:** the v1.1 "P4 T4.5" cross-reference was a stale wrong-task pointer; T4.5 is the webhook pre-shared-secret lane in middleware dispatch, not the consumer enablement guide (**AMEND v1.4 / module-lanes: this Minor #12 description is itself now stale — webhook ingest is MODULE-LANE and is NOT in middleware dispatch; T4.5's v1.3 framing is superseded by phase3 T3.10 + decisions.md D19; phase4-plan.md is untouched in this pass (out of the v1.4 dispatch scope) and carries the residual stale text — flagged for the implementer**; **P6 DISCHARGE (2026-10-11, same PR): the phase4 residual is now annotated — the §5 size-rationale prose ("webhook dual-credential adds a dispatch branch") carries an AMEND v1.4 supersession note citing phase3 T3.10 + decisions.md D19, completing approver note 1**), (d) Rotation & maintenance (client-secret rotation semantics per [REQ] §6.5, JWKS cache invalidation, session revocation), (e) Upgrade/rollout plan reference (auth OFF merge; enable is post-merge operator act), (f) CORS notes (FR-27 guard + OOS-5 ticket link), (g) Troubleshooting (failure-mode table from [TA] §Failure Modes rendered operator-side: LOGIN_NOT_CONFIGURED, IDP_UNREACHABLE, STATE_REPLAYED, SCOPE_INSUFFICIENT, 401-vs-302 discrimination, misconfigured consumer fail-loudly), (h) Cross-references. **AMENDED v1.1 added runbook subsections:** **§KB-MCP-auth posture (D1)** — explains why `/api/mcp/kb` and `/api/mcp/kb/sse` are PUBLIC in v1, loopback trust model, KB-MCP-auth reserved seam. **§Sweeper dual-instance note (resilience)** — same-DB dev checkout safe via SKIP LOCKED + idempotent deletes; no guard built. **AMEND v1.4 / module-lanes added runbook subsections + supersessions:** **§Module-lane key management (D19/D20)** — module key management independent of OAuth2; OAuth2 does NOT govern module endpoints (webhook ingest + MCP-KB mounts authenticate with their own per-module keys; middleware pass-through in BOTH auth states, FR-42); current fully-open MCP-KB state disclosed + the module key mechanism improvement note (independent of OAuth2; risk ML-4); MCP-KB keys administered via the NEW `/api/mcp/kb/keys` family (T3.12), webhook secrets via the EXISTING `/api/sources` CRUD (OAuth2-governed; masking decision per §12 item 4(c) / AC-42 / risk ML-3). **SUPERSEDED v1.2/A6 text:** the §(a) Enable-procedure instruction "per-source `webhook_secret` MUST be set BEFORE enabling auth" (external webhook senders footgun) is RETRACTED — webhook ingest is MODULE-LANE, never OAuth2-gated, so no auth-ON lockout exists; the runbook instead carries `webhook_secret` as existing security best practice for the pre-existing open-ingest posture (webhooks.py:67), with the §7 row 17 verdict flipped YES UNCHANGED (auth-INDEPENDENT). **§KB-MCP-auth posture (D1) re-labeled:** that v1.1 subsection's "PUBLIC in v1" framing is superseded by the module-lane classification (the loopback trust model prose remains valid history; the operative section is §Module-lane key management). | `docs/runbooks/oauth2-auth.md` (new) | Operator surface | FR-35; AC-35; NFR-17; **FR-39..42 (AMEND v1.4 / module-lanes)** |
| T6.2 | CHANGELOG entry: feature summary, config surface, default-OFF guarantee, operator enable steps pointer, consumer compat statement (0 BLOCKED / 0 code change), **AMENDED v1.1: line-drift-correction note (the plan's pre-existing line-drift — `jobs_crud.py:275-278`→`:595` etc. — was applied in this planning set; no code change in this PR)**, **AMENDED v1.1: R3 source-forgery follow-up ticket cross-link** ("auth does NOT close the forge-source lane — see project critical note `OAuth2 planning follow-ups (R3): source-forgery lanes NOT closed by auth plan` and the filed follow-up ticket"), **AMEND v1.4 / module-lanes: module-lane statement in the compat sentence — webhook ingest + MCP-KB mounts are module endpoints beyond OAuth2 (own keys; unchanged when auth OFF; lane-isolated per D20); ZERO OAuth2 config delta** ([REQ] §6.6) | `CHANGELOG.md` | Release note | FR-35; NFR-17 |
| T6.3 | Rollout verification note: reproduce the §8 default-OFF strategy evidence (suite green + snapshot diff + consumer walk) as a checklist the implementer attaches to the merge PR | `.agents/shared/planning/oauth2-auth/rollout-verification.md` (new) | Proof-of-no-disruption artifact | UR-1; SC-1 |
| T6.4 | CN cross-links: `[decision]` AUTH DESIGN v1 note; link CORS KNOWN-RISK ↔ `auth.allowed_origins`; `[risk]` bearer operator-mint-only note; **AMENDED v1.1: `[risk]` R3 source-forgery follow-up ticket cross-link** (file-as-follow-up — auth does NOT close the forge lane) | project critical-notes via leader (content drafted in this phase) | Knowledge base continuity | [TA] §Citations |
| T6.5 | Final consistency sweep of the planning set: phase files ↔ overview tables ↔ decisions.md cross-refs; ID sequences coherent (FR/NFR/AC anchors); file-touch lists match what shipped. **AMENDED v1.1:** verify every OD closure in `decisions.md` matches the runbook; verify the line-drift register applied; verify S-19 + KB-MCP-auth seam entries in `tests/test_reserved_seams.py`; verify the router-mount scope-declaration cage manifest matches the actual `daemon/api.py` `include_router(` calls. **AMEND v1.3 / F7 — canary-unmodified assertion:** verify `frontend/scripts/boot-e2e-maintenance-daemon.sh` and `boot-e2e-snapshots-daemon.sh` remain UNMODIFIED (zero `git diff`) — TA Debt #7 is **SUPERSEDED** by FR-22/FR-23 + G5.3 (the canary route is PUBLIC per the route-metadata derivation, D18/D1; the canaries run unchanged against an auth-ON daemon). No implementer may "fix" the canary scripts with bearer-token support — that supersede ruling is binding. **AMEND v1.4 / module-lanes — module-lane sweep additions:** verify the three-class derivation is reflected consistently across the set ([REQ] §5.0/§5.6/§7.7 ↔ decisions.md D19/D20 ↔ phase3 T3.10-T3.12 ↔ phase5 T5.11 ↔ this runbook section); verify the §7 row 17 verdict flip and row 18 addition propagated (§7.2 counts 10/6/2, AC-32 "18 consumers"); verify S-20's superseded semantics are recorded ([REQ] §10 row annotation + decisions.md D19; NFR-11 count 20/20 unchanged); verify the v1.2/A6 Enable-step supersession landed in the runbook outline. | `.agents/shared/planning/oauth2-auth/*.md` (planning set only) | Hand-off hygiene | dispatch contract |

## 4. Tests to Write

| File | Asserts |
|------|---------|
| `tests/unit/test_runbook_sections.py` (or CI lint step) | Heading-presence check: the 7 FR-35 sections exist in `docs/runbooks/oauth2-auth.md` (mechanizable per AC-35) |

## 5. Exit Criteria (gated)

- G6.1: Runbook complete (7/7 sections, AC-35) and every operator command
  in it verified by copy-paste execution during this phase.
- G6.2: CHANGELOG entry merged; rollout checklist attached to the merge
  PR description.
- G6.3: CN notes recorded via the leader; planning set internally
  consistent (T6.5 sweep clean).

**Size: S** — documentation + verification notes; no production code.

## 6. Phase-Specific Risks

| Risk | Mitigation |
|------|------------|
| Runbook drifts from shipped behavior | Every command executed (G6.1); runbook examples generated from the P5 canary transcripts where possible |
| OD defaults land silently | D6.3 annex makes OPEN items visible to operators post-merge |
| Merge carried with auth accidentally enabled | Rollout checklist G6.2 includes "config default verification" step (AC-2 re-run on the merge commit) |
