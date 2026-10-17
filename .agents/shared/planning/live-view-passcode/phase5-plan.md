# Phase 5 — Merge + Stage Release Candidate + Evidence

## 1. Objective

Land the feature on the integration branch and produce the promote-ready release candidate
with the full evidence bundle the commission mandates: test evidence, staged version,
binary sha, the deployment note, and the ADR reference — staged ONLY (no live promote;
the 3-factor nonce gate is the user's ceremony, out of scope for this plan).

## 2. Decisions

| ID | Decision | Rationale (anchors) | Rejected alternatives |
|----|----------|---------------------|------------------------|
| D22 | Merge order: feature branch → latest ONLY after P4's governor verdict is recorded; the merge commit carries the planning-dir + ADR + pack references; the stage step follows the house release lane (bump commit + staged INSTALL_DIR + binary sha256, awaiting nonce ceremony) | Standing release posture (upgrade policy): staging without flip is the norm; the governor gate (AC18) is a merge precondition, not a post-hoc note | Merging before the verdict (violates requirement 10's process ordering); live promote (user-gated, explicitly out of scope) |
| D23 | Evidence bundle (single stage-RC report section, dispatcher-facing): (1) suite transcript + counts with named files; (2) staged version string + bump commit; (3) binary sha256; (4) deployment note path + the one-line namespace (`/views/public`); (5) ADR + governor pack + verdict pointers; (6) data-center-scripts manifest commit hash (R11 evidence); (7) project-history entry id + `experience()` KB pointer (M4 process owner, T5.4/T5.5) | Requirement 10's deliverable list, verbatim; the manifest hash closes the cross-repo evidence loop; the history + KB pointers close the process-owner loop | Evidence scattered across commits (unverifiable at ceremony time) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T5.1 | Pre-merge checklist: P4 verdict recorded; every AC1–AC19 pinned green; lockstep sweep (T2.9) closed incl. seal re-pin adjudication if triggered; `git diff --stat` proves the intended touch surface — **`daemon/api.py` shows EXACTLY the ONE expected registration line (`tag_routes_by_path(app, path_prefix="/views/public")` after `include_router`, per ADR D3 / phase2 T2.3 call-out) and `daemon/middleware/auth.py` shows ZERO changes** (also: no `frontend/`; `auth.anonymous_view_paths` default untouched/empty) | Checklist states each gate with evidence pointer; any red gate blocks the merge (report back to dispatcher instead) | merge checklist in the stage-RC report | P4 | R6 | AC17, AC18 |
| T5.2 | Merge branch `feature/live-view-passcode` → latest; resolve conflicts if the base moved (re-run the P4 subset that touches conflicted files); merge commit message cites: planning dir, `architecture-recommendation.md`, governor verdict, deployment-note path | Integration branch contains the feature; post-merge smoke: daemon boots (auth OFF default) + `/views/livez` + a public-lane round-trip on the dev instance | git lane (ensemble repo) | T5.1 | — | AC19 |
| T5.3 | Stage the release candidate per the house lane: version bump commit; build + stage binary to the live INSTALL_DIR (STAGING ONLY, no flip); record binary sha256 | Staged version + sha recorded; daemon NOT restarted onto the RC (no flip without the user's nonce ceremony) | release lane files (bump/version files, INSTALL_DIR staging) | T5.2 | — | AC19 |
| T5.4 | Compose the stage-RC report per D23 and deliver it to the dispatcher as the commission's closing evidence; it names the deployment note as the mandated two-state migration deliverable and quotes the namespace line verbatim. **PROCESS OWNER (M4): the implementer OWNS writing the project-history entry for this commission** (milestone/bugfix entry recording merge, staged version, governor verdict, deployment-note path) — the report cites the history entry id as evidence, not just intent | Report delivered with all seven evidence elements incl. the history-entry pointer | final report + project history | T5.2, T5.3 | R11 | AC19 |
| T5.5 | **PROCESS OWNER (M4): record the durable knowledge** — (a) `experience()` KB write capturing the public-namespace contract: "`/views/public` is the reserved, passcode-gated live-view namespace; it is the ONLY exempt-from-auth `/views` sub-namespace in BOTH deployment states (State A HTTPRoute rule ordered first; State B code-seeded `tag_routes_by_path(app, path_prefix='/views/public')` — `auth.anonymous_view_paths` stays EMPTY by design); never exempt `/views` or `/api` (release blocker, ADR R2); the canonical gate is daemon-side in both states"; (b) confirm the project-history entry from T5.4 is recorded and linked | KB entry queryable via `explore("live view public namespace")`; history entry linked from the stage-RC report | RAG KB + project history | T5.4 | — | AC19 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC19 | Release candidate STAGED (not promoted): bump commit + staged binary + sha256 recorded; evidence bundle complete (tests, version, sha, deployment note, ADR + verdict, manifest commit); user nonce ceremony pending |

## Exit criteria (phase gate)

- Merged to latest with green gates; RC staged; evidence report delivered. NO live promote.

## Risks (phase-local emphasis)

- Base drift between plan-approval and merge: re-run the conflicted-area test subset
  post-resolution; the byte-parity (AC3) and uniform-404 (AC12) tests are the drift
  tripwires for this feature specifically.
- R11: if the data-center-scripts manifest commit is missing at report time, the RC is
  staged but State A is NOT live-ready — the report must say so explicitly rather than
  implying two-state readiness.
