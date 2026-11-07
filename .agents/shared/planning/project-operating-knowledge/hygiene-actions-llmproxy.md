# POK Phase-3 — critical-notes hygiene actions: llmproxy (PREPARE ONLY — D5)

**Status:** PREPARED, **NOT EXECUTED**. The `project_cn_*` mutation verbs are **leader-owned**; this table is the leader's execution brief. Execution precondition: POK Phase-1.5 **live** (the live `llmproxy` project row carrying the 8 seeded slugs verified by `pok_query`) — retiring/superseding notes BEFORE their POK successors are live would punch holes in the operational surface.
**Fact base:** the 8 dispatch-cited note ids (from the 49 pulled llmproxy notes). Design anchors: plan §5-2.6 ("Critical-notes hygiene | D5"), implementation-readiness G-3 (enumerates the three deploy-day corrections: "wrong flag name, config.json non-surface, PORT gotcha" — grep-confirmed ZERO design content on which note supplies them; located below).
**Principle (design-faithful):** POK carries the operational facts; notes that are pure duplicates of seeded slugs are MIGRATE/RETIRE candidates; notes with substance beyond the 8 slugs are KEEP or SUPERSEDE candidates.

## Actions (one row per note id)

| note id | substance (dispatch-cited) | action | absorbing slug | rationale (one line) |
|---|---|---|---|---|
| `b82409f4` | CI builds on master push only + paths filter + workflow_dispatch | **MIGRATE** | `pipeline-01-merge-master-triggers-image-ci` | Entire substance is the seeded body verbatim-fact (master-push trigger, paths filter, manual dispatch) — nothing left behind; retire the note once POK is live. |
| `68a80f90-detail` | no build-sha endpoint; /healthz not publicly routed (404 at edge); /fe/api/* OAuth-gated; digest+push-timestamp = deploy identity; backup=Helm release, primary=plain kubectl apply | **KEEP** | — | Four of five facts are absorbed (POK slugs 1/5/6/7) but the **/fe/api/* OAuth-gated** fact has NO absorbing slug in the §11 payload — the note stays until a slug (or a SUPERSEDE trim) is ruled. |
| `6dcc0ed9` | backup-first rollout: daoduc helm rev → gate → ensem gen; **rollback anchors** | **KEEP** | — | The ORDER is absorbed (pipeline-03 + deploy-01) but the **rollback-anchors detail** is explicitly beyond the 8 slugs (named in the commission as the canonical KEEP example); candidate to SUPERSEDE into an order-only stub post-live, leader's call. |
| `f7511668` | sticky last-success **shipped-not-deployed**; layer-extract digest proof | **KEEP** | — | pipeline-02 absorbs only the verify-discipline kernel ("only then is it shipped"); the sticky-last-success mechanism and the layer-extract proof are unseeded substance. Also correction-(1)'s alternative candidate (below) — do not retire while that question is open. |
| `14b4bbdc` | live verify discipline; primary 19 vs backup 16 models config drift note | **SUPERSEDE** (replacement text below) | `model-catalogs-19-vs-22-not-drift` | Counts + not-drift triage are absorbed by slug 8, but this note is the OBSERVED counts record backing the seeded body — replace (not retire) with a text that points at the canonical slug and keeps the observed numbers. |
| `fc8f6ae0` | go test rewrites `~/.config/llm-supervisor-proxy/config.json`; PORT=9797 leaks from daemon env | **KEEP** | — | Both facts are dev-workflow traps deliberately OUTSIDE the §11 deploy payload (no absorbing slug exists); they are the SOURCES of corrections (2) and (3) — they must stay visible until the corrections are folded somewhere durable. |
| `08da5e78` | GOMEMLIMIT grammar trap: "1GiB", not "1Gi" | **KEEP** | — | Env-tuning deploy-day trap outside the 8 operational facts; the SOURCE of correction (1) — stays until the correction is folded. (Seen variant: `4331b24f` — same trap.) |
| `1e5a371f` | image version = CI run_number | **MIGRATE** | `pipeline-01-merge-master-triggers-image-ci` | Fully absorbed ("version = CI run number" in the seeded body); retire once POK is live. |

**Counts:** MIGRATE ×2 (`b82409f4`, `1e5a371f`) · SUPERSEDE ×1 (`14b4bbdc`) · KEEP ×5 (`68a80f90-detail`, `6dcc0ed9`, `f7511668`, `fc8f6ae0`, `08da5e78`) · RETIRE ×0.

### SUPERSEDE replacement text for `14b4bbdc` (leader-owned execution)

> Observed model catalogs: primary 19, backup 16 — a count gap is environment config, not deploy drift. Canonical triage now lives in POK slug `model-catalogs-19-vs-22-not-drift` (llmproxy).

## The THREE deploy-day corrections (G-3: "wrong flag name, config.json non-surface, PORT gotcha")

Design line (implementation-readiness G-3): *"grep-confirmed ZERO content on stale/migratable llmproxy notes or deploy-day corrections (wrong flag name, config.json non-surface, PORT gotcha)"* — the design names the CLASS, no instance. Located per correction:

| # | G-3 phrase | verdict | source (note id + the line that sources it) |
|---|---|---|---|
| 1 | "wrong flag name" | **SOURCED (best-fit), with a naming-caveat FLAGGED** | `08da5e78`: "GOMEMLIMIT grammar trap '1GiB' not '1Gi'". This is the only crisp wrong-vs-right token pair among the notes that breaks a deploy day. Caveat: the trap is the flag's **value grammar** (the `GiB` suffix), not literally its NAME — if the Emperor's phrase meant a mis-named flag (e.g. the sticky_last_success family, `f7511668`), that alternative remains open. |
| 2 | "config.json non-surface" | **SOURCED** | `fc8f6ae0`: "go test rewrites ~/.config/llm-supervisor-proxy/config.json" — config.json is NOT a config surface (the store is config.db; tests rewrite the file), exactly the non-surface correction. |
| 3 | "PORT gotcha" | **SOURCED** | `fc8f6ae0`: "PORT=9797 leaks from daemon env" — the daemon environment's PORT leaks into the child process, the PORT gotcha. |

**Residual Emperor-question (correction 1 only):** if "wrong flag name" was meant as a mis-named flag rather than the GOMEMLIMIT value-grammar trap, the sticky_last_success flag family (`f7511668`) is the only other candidate in the fact base — the two are mutually exclusive readings and the design line alone cannot adjudicate.

## Standing FLAG surfaced by the seed run (not a correction — a discrepancy)

The design slug `model-catalogs-19-vs-22-not-drift` (§11/§4.5 verbatim, frozen) says **22**; the observed note `14b4bbdc` says **19 vs 16**. The seeded BODY carries the observed numbers (provenance=observed; no invented facts); the slug stays design-verbatim. Slugs freeze AFTER dogfood (§4.5) — this discrepancy is precisely what dogfood exists to surface: the design owner should either correct the slug to `19-vs-16` or produce the observation that says 22.
