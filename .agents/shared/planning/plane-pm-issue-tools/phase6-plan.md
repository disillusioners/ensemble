# Phase 6 — Functional gate → staged release → user ceremony

**Lane:** `plane-pm-issue-tools` · base `941cb6321` (re-derive at stage time) · **Tier: Release Gate (promote-blocking ⇒ Tier-2 whole-tree dual-side is MANDATORY)**
**Depends on:** Phases 2, 3, 4, 5 · **Blocks:** nothing — this is the lane's terminal phase. **The lane NEVER promotes.**

---

## 1. Objective

Carry the lane from "implemented + functionally verified" to "**staged** and **ceremony-ready**", with every artifact and every adjudication recorded — and then **stop**. The promote is the user's 3-factor nonce ceremony.

---

## 2. File-level scope

| File | Action | Detail |
|------|--------|--------|
| `.agents/shared/planning/plane-pm-issue-tools/` | **AMEND (append-only)** | the gate receipts, the stage manifest record, the rollback adjudication, the ceremony note |
| `.agents/tester/RESULTS/<date>-plane-pm-issue-tools-*.md` | **NEW** (tester-owned) | the functional-gate and staged-verification evidence (the tester's to write, not this lane's) |
| `.agents/tester/PACKS.md` | **AMEND** | the pack registrations for every new pack (invocation + scope + last-run + status columns) |
| `CHANGELOG.md` | **AMEND** | the release section, per the house form |
| `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` | **AMEND** | the version bump — **exactly 4 files, 1 hit each** (the house bump set) |
| `daemon/**`, `agents/**`, `scripts/**` | **NO CHANGE** in this phase** | the code is frozen at this point |
| `docs/runbooks/plane-workspace-scoping.md` | **AMEND (release-notes only)** | **> (G2 fold, approver iter-003 — non-blocking item 13: P23's "runbook" leg had no admissible Phase-6 scope, because `docs/**` was not in this phase's scope table.)** **P23** (the behavioural-rollback disclosure) requires the runbook to carry the rollback-window sentence. The runbook is **authored in Phase 4**; this phase may only **append the release-scoped sentences** to it: (i) the **behavioural-rollback window** ("if the plane lane goes quiet after a rollback, that is the rollback window, not a credential problem"), and (ii) the **staged version** the runbook was shipped with. **Scope boundary (binding):** this phase **does not re-author** the runbook and **does not** touch any other runbook section — the Phase-4 content is frozen; only the two release-scoped sentences are appended, in the same commit as the release notes. **Rationale for the placement:** the runbook is the operator-facing surface for the rollback window, and the window is a **release** property — but the file itself is a `docs/**` artifact, so it must be named in the scope table to be admissible at all. |

---

## 3. Gate chain (in order)

| # | Gate | Owner | Pass condition | Failure handling |
|---|------|-------|----------------|------------------|
| **G11** | **Code council** | reviewer | **0 critical**; advisories folded | fold → re-review (scoped delta re-review) |
| **G12** | **Tidier** | tidier | findings folded; **zero** behaviour change | fold → re-check |
| **G13** | **Functional gate** | tester | every pack green; **every mutation leg RED→GREEN by digest**; net-new reds = 0 against the declared blast radius | fix cycle → re-verify |
| **G14** | **Staged verification** | tester | artifact integrity + in-binary content + isolated boot smoke + **live fence held** | fix cycle → re-stage |
| **G15** | **Leader sweep** | leader | evidence merged to `origin/latest` | — |
| **G16** | **User 3-factor ceremony** | **the user** | the promote flips the symlink | **the lane does not perform this** |

### G13 — the functional gate (the mutation matrix is the gate's centre)

**Every MUTATION PIN in `plan-overview.md` §11 must be RED at the planted defect and GREEN at the fix, by digest.** **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 4: the pre-fold opener said "every pin … must be RED", which OVER-SCOPED the gate. Corrected: the **mutation pins** must each have a proven RED leg; the **gate-note obligations** (P-G1…P-G4) and the **behavioural assertions** (the A′ branch, the restore, the census) are verified by their own exit criteria, not by a planted-defect mutation.)** *(G1 folds B5 + A-g: the enumeration is now COMPLETE and split by runner class.)* The full set, per the **pin-ID master index** (`plan-overview.md` §11): the pre-fold rows **P1** (prefix-sweep ×4 cases), **P2** (resolver matrix, incl. the G1-B3 live+`e2e` leg **and the G2-NB5 live+third-slug leg**), **P3** (E2/E3 chokepoints, incl. the B3 leg **and the G2-NB5 live+third-slug leg** **and the G2-NB8 live+unset account-default legs in BOTH lanes**), **P4** (D-1 fail-open), **P5** (loud-skip WARNING), **P6** (registry-vs-row cross-check), **P7** (charter/baseline coupling), **P8** (E4 heal), **P9** (PM `deny:["mcp"]` bypass), **P10** (shell scrubs — now backed by the **named** static-content pack `tests/packs/plane_shell_scrub_content.sh`, **created by Phase 4** — the G2 fold B1), every **fold-added row** (P2-A, P2-B, P2-C, P2-D, P2-G, **P2-H**, P2-I, P2-K/FC11, P2-L, P3-C/P5c, P3-D/P5d, P3-A/P5e, P3-E/P6b, **FC7** (re-scoped, G1-B1; **pack named** G2-NB1), P5-C/P19, P5-A/P15a, P6-A/P23); **P11** (no invented verbs — **with its explicit three-name exclusion set** and its **named host file** `tests/unit/test_plane_prompt_verb_check.py`, G2-fold B2/iter-003) and **P12** (the `plane_sync_project` claim) from Phase 4 **+ P30 (the no-active-default check, G2-B3/iter-003 — named host file `tests/unit/test_plane_env_example_defaults.py`)**; **P13–P19** from Phase 5 (incl. P15a/P15b; P15 superseded) **+ the G2-fold Phase-5 pins P20 (the arming precondition), P21 (the STOP branch), P22 (SC1's numeric assertion), P28 (the FC9 derivation) and P29 (the A′ branch)**; and the Phase-6 staged pins **P24–P27**. **> (G2 fold, approver iter-002, 2026-11-02 — the G13 enumeration is reconciled to the corrected master index.)** Two corrections: (i) the four amendment IDs that the pre-fold index implied were pins — **P2-E, P2-F, P2-H, P2-J** — are **NOT mutation pins** and are therefore **not** in this enumeration, **except P2-H, which IS a mutation pin** (a RED leg exists and is meaningful) whose **implementation home is Phase 3** and whose validation point is **Phase-3 exit** — the G2 fold states that classification **once** and corrects the master index + this enumeration to match; the other three (P2-E = a citation correction, P2-F = a structural assertion, P2-J = a disclosure) are verification routes, not pins, and are listed in the amendment-status sub-table at `plan-overview.md` §11 (**P2-J**'s disclosure-presence check runs under **P23**'s runner class). (ii) The G2-fold additions **P30** (Phase 4) and the Phase-5 pins are now enumerated. **> (G2 fold, approver iter-003 — the gate-note pins are now named too.)** The **Phase-1 gate pins P-G1, P-G1a, P-G1b, P-G2, P-G3, P-G4** are **gate-note obligations**, not mutation pins: each is verified by the **Phase-1 exit criteria** (X1.1…X1.10), and each states its own RED leg where one is meaningful (P-G4's artifact-integrity leg).
**Runner classes (the mutation pins still require a proven RED leg; the gate-note pins are verified by their exit criteria):** *behavioral* pins run as pytest packs (mutation plants a code defect); *static/evidence* pins — P7 (seal tripwire), **P10 (the shell-scrub content pack)**, **P30 (the no-active-default check)**, P11 (static verb check), P23 (disclosure-presence), P26/P27 (census/binary assertions) — run as static packs whose RED leg is a **planted-absence** mutation (delete/weaken the asserted content).

**Positive controls are mandatory** for every mutation — an un-mutated sibling leg that stays GREEN — so a vacuous green is impossible.

**Tier selection (mechanical, `ensure.md:11-28`):** the diff touches `daemon/manager.py` ⇒ **Tier-2 whole-tree dual-side** is mandatory; the release is promote-blocking ⇒ the release-gate arm inherits the Release Gate's judgement trigger. Budgets: Tier-1 ≤ 30 min HARD (fail-loud; abort + report + ONE auto re-scope), Tier-2 ≤ 90 min advisory.

**Whole-tree shape.** Per the house proof form: mechanical derivation → declared set → `--collect-only` slicing proof (executed == declared, counts recorded) → identical commands both legs → red-at-base ≠ net-new → 3× flaky isolation on diff nodes only → FAILED-digest comparison per `.agents/tester/skills/failed-digest-equality-gate/`.

**Fences (every pack invocation).** `timeout 300` outer + `--timeout=120` inner · **per-variable** `env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL` (**the glob form is a silent NO-OP**) · `-p no:randomly` · worktree-local `.venv/bin/python` (worktrees carry no `.venv`) · PG packs need a scratch role+DB owned by a non-live role with `PG_TEST_ALLOWED_DBS=<name>` and `--override-ini="addopts=" -m postgres` (the default addopts deselect the marker ⇒ vacuous green) · serial only for PG · **never** a bare `pytest` · the pre-exclusion registry applies.

**Release Gate items** (`ensure.md:53-73`) apply because this is a promote-blocking release: full non-integration suite green via **packs** (parallel, each ≤ 5 min), the E2E parent→child happy path, pause-then-resume, terminate-then-revive, the 3-level cascade. Prereqs: the daemon running at `localhost:8079`; `unset SSL_CERT_FILE SSL_CERT_DIR` before each run; `PYTEST_TIMEOUT=280` + `--override-ini="timeout=280"` (the pyproject default 30 s kills E2E prematurely); queue cleanup before each test (`GET /api/jobs?status=pending`) — leftover pending jobs block defer-queue admission and cause false failures; run E2E one by one (each makes real LLM calls).

### G14 — staged verification (4 lanes)

| Lane | Content |
|------|---------|
| **S1 — artifact integrity** | 4-way sha (release dir == stage-worktree dist == manifest == provenance sidecar); the sidecars' `git_head` + `git_dirty=false`; the tag state; the payload is **source-less** (no `scripts/`, no `.env`, no provenance files inside the release dir); the manifest delta vs the live release is exactly the expected scalars; the prompt-artifact baseline **161/161 clean** at the staged tree |
| **S2 — in-binary content** | the new resolver symbols are **present in the staged binary** and **absent in the live one** (bytecode/raw-string level, not a source-level check); the `schema_version` bump is present; the loud-skip code path is present; the negative control (the live binary) reads GREEN on the same assertions |
| **S3 — isolated boot smoke** | scratch root + **scratch binary copy** (never in place) + `ENSEMBLE_DATA_DIR` fences + per-variable env scrubs; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly**; banner shows the staged version; `/livez` + `/readyz` 200 with all components true; **the four legs enumerated below**; SIGTERM-only teardown; **no orphans**; the scratch DB/role dropped; the live fence intact |

> **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 1: ALL FOUR LEGS MUST RUN WITH CREDENTIALS PRESENT.)** The pre-fold S3 text described only the negative leg ("the `plane` row is **not** created when the env is non-live with a `nea`/unset slug"). The approver's finding is exact: **if `PLANE_MCP_URL`/`PLANE_MCP_API_KEY` are absent, the negative legs pass via the SKIP path** (`manager.py:3073-3087` — the builtin is unavailable ⇒ no row) — i.e. **the refusal would be reached for the wrong reason** (missing credentials, not the workspace policy) and the leg would be **vacuous**. **Rule now in force: all four legs carry `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` PRESENT; only the slug and `ENSEMBLE_SELF_ENV` vary.**
>
> | Leg | `ENSEMBLE_SELF_ENV` | `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` | `PLANE_MCP_WORKSPACE_SLUG` | Expected outcome |
> |---|---|---|---|---|
> | **L1 — positive, non-live + `e2e`** | `dev` (or `sandbox`) | **PRESENT** (dummy — see the credential-provenance rule below) | `e2e` | the row **is** created; the slug header == `e2e`; **and the negative legs have a control** — L1 must produce a row with the *same* credentials, or L2/L3 prove nothing. **> (G2 fold, approver iter-003, 2026-11-02 — non-blocking item 10: the pre-fold third clause was STALE.)** The pre-fold text said *"the plane tools **are** resolved"* — that is **impossible under the mandated dummy key**: `is_available()` is **presence-only**, so the row registers, but the MCP handshake with a dummy bearer **fails** ⇒ `list_tools()` returns an **empty** list ⇒ **no `plane_*` name resolves**. The clause is therefore **deleted**, not weakened: **L1 asserts the ROW + the resolved slug header, and that the negative legs have a control** — it does **not** assert a resolved tool list. (The **tool-list** behaviour is covered by the **Phase-5** census on the **dev** env with a **real** key, not by the staged smokes.) |
> | **L2 — negative, non-live + `nea`** | `dev` (or `sandbox`) | **PRESENT** | `nea` | the row is **NOT** created; **no** `plane_*` tools; the WARNING carries the typed reason *"workspace 'nea' is production-only…"* — **reached via the policy refusal, NOT via a missing-credential skip** (this is the leg that distinguishes the two) |
> | **L3 — negative, non-live + unset** | `dev` (or `sandbox`) | **PRESENT** | **unset** | the row is **NOT** created; no tools; the WARNING carries *"PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'"* — again **not** a skip |
> | **L4 — positive, live + `nea`** | `live` | **PRESENT** | `nea` | the row **is** created (the byte-identical live case); the slug header == `nea`; the tools resolve — **the live continuity leg** |
>
> **The vacuity tripwire (mechanical):** each negative leg MUST assert that the **credential was present** in that leg's env (the key **name** census + the observed absence of a missing-credential skip reason). **If a negative leg's WARNING reason is a missing-credential reason, the leg is VACUOUS and FAILS** — it must be re-run with the credentials present. **The positive legs are enumerated above precisely so that L2/L3 have a control**: if L1 does not produce a row with the same credentials, then L2/L3 prove nothing.
>
> **The live leg's contact discipline:** L4 runs in the **scratch root** with a scratch DB and **never** addresses `ensemble_prod` — it proves the *resolution + row materialisation* path, not a prod write. The `nea` slug in L4 is the **workspace header**, not a data write; **no work item is created** in any leg.
>
> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 6: CREDENTIAL PROVENANCE for the staged smokes.)** `is_available()` is **presence-only** (`plane.py:296-298` — it checks that the URL and the key are **non-empty**, never that they are valid). **Therefore the staged smokes do NOT need a real bearer — and MUST NOT carry one.**
> - **Every leg uses a DUMMY, syntactically-valid, non-secret key** (e.g. `plane_api_staged_smoke_dummy_<leg>`) and the **real** Plane URL (the URL is not a secret; it is public in `.env.example:207`).
> - **No real bearer may enter a staged smoke's env, its logs, its artifacts, or the release payload.** The smoke asserts the **resolution + row-materialisation** behaviour, which is **key-independent** by construction.
> - **This is a security property, not a convenience:** a staged smoke's env and logs are the most likely place for a credential to leak into a release artifact (the AD2/AD1 class). A dummy makes that leak impossible **by construction** rather than by care.
> - The **staged-artifact integrity lane (S1)** additionally asserts that the release payload contains **no** `PLANE_*` key material.

> **> (G2 fold, approver iter-002, 2026-11-02 — non-blocking item 9: L4's SAFE FORM. The live-branch INTENT is kept; the live-resolving ROOT is removed.)**
>
> The approver's finding: L4 as folded booted with `ENSEMBLE_SELF_ENV=live` **on a scratch root** — but a **frozen binary derives its root from its own path** (`sys.executable.parent.parent.parent`), so unless the binary is **copied into the scratch root**, the "scratch" boot actually resolves the **LIVE install root** and can bind the live promote journal (the 2026-10-21 staged-smoke hazard). The two obvious fixes are both wrong: flipping L4 to `sandbox` **destroys the live-branch intent** (L4 exists to prove the live branch still materialises a row), and keeping a live-resolving root **re-introduces the hazard**.
>
> **The folded SAFE form of L4:**
>
> | Element | Requirement |
> |---|---|
> | **The binary** | the staged binary is **COPIED into the scratch root** (never booted in place) ⇒ the path-derived root **IS** the scratch root |
> | **The environment** | `ENSEMBLE_SELF_ENV=live` is pinned **explicitly in the scratch process's env**, so the **live *classification*** is exercised, while **every path the binary would derive points at the scratch** |
> | **The scrub** | the full per-variable env scrub (one by one — `env -u POSTGRES_*` is a **silent NO-OP**) + a `mktemp` FAKE_HOME with an escape guard (never a live-shaped install path) |
> | **The workspace** | slug `nea` (the live slug) — a **header value**, not a data write; **no work item is created** |
> | **The credentials** | the **dummy** key per the G2-fold NB6 rule above — L4 proves the *resolution*, not the credential |
> | **The DB** | the scratch PG DB on a throwaway role; `ensemble_prod` is **never** addressed |
> | **The fence** | the live `state.json` sha **PRE == MID == POST**; the live daemon's pid + uptime unchanged; the live `current` symlink unchanged |
> | **The port** | neither 9797 nor 7979 (both live) |
>
> **The claim L4 proves under this form:** *with the live classification explicitly asserted, and every filesystem/data path still inside the scratch*, the plane row materialises with slug `nea` — i.e. the **live-branch behaviour is unchanged**. **It does NOT claim** that a live install was booted (it was not). The distinction is recorded in the staged-verification artifact so a reviewer cannot read L4 as a live-boot test.
| **S4 — live fence + ceremony inputs** | `state.json` sha **PRE == MID == POST**; the live `current` symlink unchanged; the live daemon's PID/uptime continuous; the promote command + the outstanding preconditions (the live/demo credential re-mint; ADJ-4) recorded |

**Boot-smoke rule (absolute).** A frozen-binary boot smoke from a daemon-child shell **must pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly**. `env -u` on it strips the ambient guard, and the sweep then **kills LIVE processes** (it SIGTERM+SIGKILLs the live launcher + `ensemble-prod` pair and their children). A staged boot is **not** isolated by default: the binary's path derivation resolves the LIVE install root, so `ENSEMBLE_SELF_ENV=sandbox` selects the branch but does **not** override path derivation — the smoke can bind the live promote journal and prune a live ceremony's nonce. All five legs of the house mitigation are mandatory (worker shell is a daemon child; `systemd-run` unavailable; the frozen binary sweeps ON when the var is absent; `-u` strips the guard; the walk misclassifies live processes as orphans).

---

## 4. Staging (giter)

**Version re-derivation (W9).** The version slot is **re-derived at stage time** from the live census (next free strictly above all tags + `releases/` dirs) — **not** pre-committed from the plan. The plan's `v0.28.6` in `plan-overview.md` §12 is an **estimate**; the stage log records the actual derivation.

**Stage procedure.** `scripts/upgrade/stage.sh` with the `VERSION=v<slot>` describe output (the ADR-009 D3 guard: `VERSION=0.28.6` unprefixed is **refused pre-build** with no partial release dir; `VERSION=v0.28.6` stages cleanly). The staging worktree is **kept** (detached at the bumped commit) so both provenance sidecars stay recoverable for the ceremony — the v0.25.2 provenance-evidence gap is the precedent for keeping it.

**Rollback-safe adjudication (the STANDING RULE — explicit semantic reasoning, not a reflex).** This lane ships **zero** `.sql` files ⇒ the migration delta vs the live release is **EMPTY** ⇒ the delta-empty-legitimate case. The recorded reasoning must state: (a) no schema mutation ships (no new table, no `ALTER`, no `config.yaml` change — C5); (b) the old binary stays compatible (it does not reference any new symbol; the new module is additive and the changed call sites are behaviour-preserving on live because live's resolution is byte-identical — SC3/NR1); (c) rollback performs no down-migration (the runner is SQLite-only and the live engine is PG; PG schema is unchanged in both directions); (d) therefore `ENSEMBLE_ROLLBACK_SAFE=1` with `source=explicit`, and **auto-rollback may fire safely on a failed gate**.

> **Amendment P6-A (architect G0, 2026-11-02 — folded append-only): the BEHAVIOURAL rollback disclosure.** The **schema** adjudication above is unaffected — the delta is empty and the schema is untouched in both directions. But a rollback is **not behaviour-neutral**, and the release notes must say so plainly:
>
> **A rollback restores the OLD binary, which has no resolver and therefore re-heals the `plane` row to schema `"2"` with the RAW env slug.** On a **non-live** install, that re-opens pollution paths **P1 / P2 / P3** (non-prod REST with `nea`; MCP with the slug unset ⇒ account-default prod; non-prod MCP with `nea`) **until the next promote**. On **live** the behaviour is byte-identical (live is `nea`, and the raw env slug is `nea`), so the exposure is confined to the non-live environments.
>
> **Disposition: DISCLOSE-AND-ACCEPT.** The architect ruled this acceptable; the plan records it in the release notes, the ceremony note, and the runbook's operator-caveats section. It is **not** a blocker on staging and **not** a reason to withhold the rollback path — the alternative (refusing to ship the guard) leaves the pollution class open **permanently**, which is strictly worse than a bounded, disclosed window between a rollback and the next promote.
>
> **Operational mitigation that IS in scope:** the window is **observable** — Phase 3's loud-skip WARNING (§R-15) fires when the plane row is skipped, and Phase 3's **P3-D** makes `test-connection` report the state rather than lie. So an operator who rolls back sees the *silent* behaviour return — **the observability contract is the mitigation** for the rollback window's invisibility. The runbook must say: *"if the plane lane goes quiet after a rollback, that is the rollback window, not a credential problem."*

**The manifest delta is expected to be:** `binary_sha256`, `binary_version`, `version`, `staged_at`, `agents_tree_sha256` (the Phase-4 charter edits change the `agents/` payload tree) + the `agents` per-file map. `frontend` and `plugins` trees must be **byte-identical** to the live release (daemon-only release). **Zero** migration files added/changed/removed.

**Prompt-artifact + SEALED at stage time.** `scripts/protection/generate_prompt_artifact_baseline.py --check` must be **161/161 clean** at the staged tree; the `SEALED_SHA_BASELINES` row for `agents/project-manager/workflow.md` must match the staged `agents/` payload content sha (the seal tripwire is the house enforcement — a stale row reds the tripwire).

**Payload hygiene.** The release dir is **source-less**: no `scripts/`, no `.env`, no `*.build-provenance.json` inside it, no `.staging.*` / `.aside.*` residue. The FE sidecar is stripped per the house pattern.

---

## 5. Promote ceremony (the user's action — NOT the lane's)

**Command (env-first form):**

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v<slot> bash scripts/upgrade/promote.sh live --f2-verified-closed
```

**The lane's obligation ends at "ceremony-ready".** The 3-factor nonce gate (user-confirmed + genuine-user-turn + nonce match) is the user's. The lane records the command, the staged artifact's identifiers, and the outstanding preconditions.

**Outstanding preconditions to record at the ceremony:**

| # | Precondition | Owner | Effect if unmet |
|---|--------------|-------|-----------------|
| PC1 | **The live/demo Plane MCP credential re-mint** (R1 / ADJ-4 / O7) | user | live has a `plane` row and **failing sessions** (401). Path A on live yields no functional gain until it lands. The Phase-3 observability makes this **visible** instead of silent |
| PC2 | The **ADJ-1** live-policy decision is reflected in the shipped resolver (default = live-exempt) | architect | a live `e2e` slug would be permitted under the default |
| PC3 | The staged artifact is promoted **AS-IS** | leader/ceremony | a re-stage may be **REFUSED** by the L1 tip-identity guard (exit 78, non-tip-tree) when the tip has drifted docs-only — the house precedent is to promote the staged artifact as-is |

---

## 6. Tier gating

| Gate | Tier | Budget | Recipe (read the body first) |
|------|------|--------|------------------------------|
| Functional (G13) — scoped leg | Tier-1 | ≤ 30 min **HARD**, fail-loud | `.agents/tester/skills/blast-radius-leg/` |
| Functional (G13) — whole-tree | **Tier-2 (mandatory)** | ≤ 90 min advisory | `.agents/tester/skills/whole-tree-base-proven-diff/` or `regression-ab-partition-run/` |
| Diff-digest comparison | — | — | `.agents/tester/skills/failed-digest-equality-gate/` |
| Base-leg provisioning | — | — | `.agents/tester/skills/ab-base-leg-provisioning-artifacts/` |
| Release Gate | Release Gate (slow) | per `ensure.md:53-73` | packs, each ≤ 5 min, parallel |

**Mandatory recipe load before any leg** (ensure.md:24) — the recipe bodies live under `.agents/tester/skills/` and **must not** depend on probabilistic `skill_search` discovery. **Node-cap + measured-rate projection guard** before legs; a projected or actual breach ⇒ STOP + report + one re-scope.

---

## 7. Pin / test obligations

| # | Obligation |
|---|-----------|
| **P24** *(renumbered from P19 by G1 fold B5 — clears the collision with Phase 5's P19 = the FC9 baselines; see the master index in `plan-overview.md` §11)* | The staged artifact's `mcp_servers` behaviour in a real boot: **no row** for a non-live env with `nea`/unset; **a row** for a non-live env with `e2e`; **a row** for a live env with `nea` (the byte-identical case); **live + `e2e` ⇒ a row too (ADJ-1 live-exempt) AND the strict-variant reason string is never emitted (G1 fold B3)**. **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 1: the leg set is now EXPLICIT and the negative legs carry credentials.)** The four legs (L1 positive non-live+`e2e`; L2 negative non-live+`nea`; L3 negative non-live+unset; L4 positive live+`nea`) are enumerated in the S3 block above, **all four with `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` PRESENT** — only the slug and `ENSEMBLE_SELF_ENV` vary. **The vacuity tripwire:** a negative leg whose WARNING reason is a *missing-credential* reason ⇒ **VACUOUS ⇒ FAIL** (the refusal was reached via the skip path, not the policy path). **And the live+third-slug leg (G2 NB5):** live + a third slug ⇒ **a row** (the live-exempt rule permits any configured slug). |
| **P25** *(was P20)* | The live fence: `state.json` sha PRE == MID == POST; the live `current` symlink unchanged; the live daemon's PID + uptime continuous; `ensemble_prod` never addressed by the drill. |
| **P26** *(was P21)* | The rollback posture: the migration census is **EMPTY** (delta vs the live release, both source-level and embedded-binary level); the old binary is compatible with the post-release schema (trivially true — no schema change). |
| **P27** *(was P22)* | The staged binary's **in-binary** content: the new resolver + loud-skip symbols are present; the negative control (the live binary) reads GREEN on the same assertions. |
| **P23** *(P6-A)* | The **behavioural** rollback disclosure is present in the release notes, the ceremony note and the runbook: a rollback restores the old binary, which re-heals the row to schema `"2"` with the **raw env slug**, re-opening P1/P2/P3 on **non-live** until the next promote (live is unaffected). Disclose-and-accept; the observability contract is the in-scope mitigation. |

---

## 8. Exit criteria

| # | Criterion |
|---|-----------|
| **X6.1** | Code council: 0 critical; all advisories folded (or explicitly accepted with rationale). |
| **X6.2** | Tidier: findings folded; zero behaviour change. |
| **X6.3** | Functional gate: every pack green; **every** mutation leg RED→GREEN by digest, with positive controls; net-new reds = 0 against the declared blast radius; the whole-tree Tier-2 leg executed (or a leader-recorded waiver naming the compensating evidence). |
| **X6.4** | Every new pack is **registered in `.agents/tester/PACKS.md`** with the invocation, scope, last-run and status columns. |
| **X6.5** | Staged verification: S1–S4 all PASS; the staged artifact's sha matches 4 ways; the payload is source-less; the manifest delta is exactly the expected scalars; the `agents/` tree hash changed **only** by the Phase-4 charter edits; `frontend`/`plugins` byte-identical. |
| **X6.6** | The rollback adjudication is **recorded with explicit semantic reasoning** (`rollback_safe=true source=explicit`), and the migration delta is proven EMPTY (source-level **and** embedded-binary census). |
| **X6.7** | The prompt-artifact baseline is 161/161 clean at the staged tree; the SEALED row matches. |
| **X6.8** | The staging worktree is **kept** with both provenance sidecars intact. |
| **X6.9** | The ceremony note records the promote command, the preconditions PC1–PC3, and the outstanding user-owned items (the credential re-mint; ADJ-3's AD2 escalation if still unfixed). |
| **X6.10** | **No live promote was performed by this lane**; the live `current` symlink and `state.json` are byte-identical to their pre-stage values. |
| **X6.11** *(P6-A)* | The **behavioural** rollback disclosure (P23) is present in the release notes, the ceremony note and the runbook operator-caveats, and the ceremony note states the rollback window explicitly. The **schema** adjudication is unchanged (`rollback_safe=true source=explicit`, delta-empty-legitimate). |

---

## 9. Fold register — architect G0 amendments mapped to Phase 6

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 6 block) + §3 (the P6-A clause) + §6 (R20) · **Mode:** append-only.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P6-A** | the **behavioural-rollback disclosure clause** — a rollback restores the OLD binary, which re-heals the row to schema `"2"` with the **RAW env slug**; on a **non-live** install that re-opens **P1/P2/P3** until the next promote (live is unaffected); **disclose-and-accept**; the observability contract is the in-scope mitigation | §4 (the P6-A block) + §7 pin **P23** + §8 X6.11 + `plan-overview.md` §12 (step 6/10) + `decisions.md` §2 W-13 |

> **G1 reviewer-council fold addendum (2026-11-02).** **B5:** the staged pins were renumbered **P19–P22 → P24–P27** (P23 unchanged) — P19 was a **collision** (Phase 5's P19 = the FC9 baselines at `phase5-plan.md` §8); the G13 enumeration was completed to cover **every** §11 pin and split by runner class (**A-g**). **B3:** the boot pin (P24) gains the live+`e2e` ⇒ allowed leg + the strict-variant-string-never-emitted assertion. Central register: `decisions.md` §8. The architect's record and the wave-1 artifacts are untouched by design.

**Phase 6 carries 1 of the architect's §7 amendment IDs** (P6-A).

**The schema adjudication is NOT changed by P6-A.** `rollback_safe=true source=explicit` still holds on the delta-empty-legitimate grounds (no `.sql` ships, no schema mutation, the old binary stays schema-compatible, auto-rollback may fire safely on a failed gate). P6-A adds a **behavioural** clause that is disclosed alongside the schema clause — it does **not** downgrade `rollback_safe`.

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB1** | **FOLDED** — the S3 staged-boot legs are **enumerated (four: L1/L2/L3/L4)**, **all with `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` PRESENT** (only the slug + `ENSEMBLE_SELF_ENV` vary), plus the **vacuity tripwire** (a negative leg whose WARNING reason is a *missing-credential* reason ⇒ **VACUOUS ⇒ FAIL**) and the **positive control** (L1 must produce a row with the same credentials, or L2/L3 prove nothing). | §3 (the G2 fold NB1 block) + §7 (**P24**) |
| **NB8** | **FOLDED** — the G13 enumeration is reconciled to the **corrected** master index (`plan-overview.md` §11): the five **G2-fold pins** (P20/P21/P22/P28/P29) are added, and the four amendment IDs that the pre-fold index implied were pins (**P2-E/P2-F/P2-H/P2-J**) are correctly **excluded** (with their verification routes stated). | §3 (the G13 enumeration) |

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB6** | **FOLDED** — the staged smokes use a **DUMMY** key (`is_available()` is presence-only ⇒ a real bearer is unnecessary and must not be present): **no real bearer enters any staged smoke env/log/artifact/payload**; the S1 lane asserts the payload carries no `PLANE_*` key material. | §3 S3 (the credential-provenance block) |
| **NB9** | **FOLDED** — **L4's safe form**: the staged binary is **copied into the scratch root** (⇒ the path-derived root **IS** the scratch), `ENSEMBLE_SELF_ENV=live` is pinned **explicitly in the scratch process** (the live *classification* is exercised), the full per-variable scrub + FAKE_HOME escape guard apply, the credential is the **dummy**, and the artifact states explicitly that L4 proves the **live-branch behaviour**, **not** a live boot. | §3 S3 (the L4 safe-form block) |
| **NB2** | **FOLDED** — the G13 enumeration now classifies **P2-H** as a **mutation pin** (implementation home Phase 3, validation at Phase-3 exit), matching the single statement in `phase2-plan.md` §5. | §3 (the G13 enumeration) |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB4** | **FOLDED** — the G13 opener no longer over-scopes ("every pin must be RED" ⇒ "every **mutation pin** must be RED"; the gate-note pins P-G1…P-G4 and the behavioural assertions are verified by their own exit criteria), and the Phase-1 gate pins are now **named** in the enumeration. | §3 (the G13 enumeration) |
| **NB10** | **FOLDED** — the **L1** clause is corrected: under the mandated **dummy key** the handshake fails ⇒ `list_tools()` is empty ⇒ **no `plane_*` name resolves**; the stale "the plane tools are resolved" clause is **deleted** (not weakened) and L1 now asserts the **row + the resolved slug header + that the negative legs have a control**. | §3 (the S3 leg table) |
| **NB13** | **FOLDED** — P23's runbook leg is brought **into scope**: `docs/runbooks/plane-workspace-scoping.md` is now a Phase-6 **AMEND (release-notes only)** row — the two release-scoped sentences (the behavioural-rollback window + the shipped version) may be appended in the release commit; the Phase-4 content stays frozen. | §2 (the scope table) |
