# Phase 4 — Test Consolidation + Governor Security Review Pack

## 1. Objective

Prove the feature as a WHOLE before the mandatory security-weighted governor review
(requirement 9: this opens an unauthenticated surface — governor review is a merge GATE,
not a formality): consolidate the full test surface (unit + router + auth-interplay +
migration), run the suite, and assemble the review pack (threat model, controls map,
consciously-accepted gaps, drill evidence) so the governor can issue a verdict with zero
archaeology.

## 2. Decisions

| ID | Decision | Rationale (anchors) | Rejected alternatives |
|----|----------|---------------------|------------------------|
| D19 | Auth-ON interplay tests EXTEND the existing harness at `tests/integration/test_auth_middleware_dispatch.py:197-223` (`TestS19AnonymousViewPaths` — ADR T2 names it) with the `/views/public` code-seeded prefix-tag case per ADR T2: `/views/public/*` passes `AuthMiddleware` with the tag seeded in code; the non-public direct lane stays default-deny; `/views/livez` + `/views/assets/*` unaffected; **`anonymous_view_paths` is asserted EMPTY in every test** (the knob is not the mechanism — ADR D3) — rather than a new integration file | The harness exists and covers the tagged-resolution branch point; the ADR T2 pin binds the State-B exemption semantics to exactly this cage; extending it keeps the semantics tested where they live | New standalone integration file (duplicates middleware bootstrapping; drifts from the canonical cage); asserting the knob populated (would validate the REJECTED operator-knob path) |
| D20 | The review pack is a SINGLE doc in the planning dir (`governor-review-pack.md`), structured as: surface inventory → threat model → controls map (each threat → ≥1 control → test pinning it) → consciously-accepted gaps (D10 locked-page disclosure, in-memory bucket restart, XFF trust, rotation coupling) → verdict-request checklist | Governors review against evidence + explicit gap acknowledgment; a single greppable artifact beats scattered commit messages; mirrors how the sketcher-lane and agent-secrets packs were consumed | Verdict over chat only (unauditable); per-phase mini-packs (fragmented) |
| D21 | Suite-run discipline: any test invoking the runner on a fresh DB uses the F1 premark helper (`tests/fakes/f1_premark_helper.py:48`); any NEW shell script (if the pack needs one) is fenced via `mktemp` FAKE_HOME + escape guard — NEVER resolving HOME-derived install paths (project critical note: two live-install deletions preceded that rule) | Pre-existing F1 defect breaks every fresh-SQLite boot regardless of this feature; the fencing convention is a standing project rule with incident provenance | Unfenced scripts (forbidden by critical note); skipping F1 premark (fresh-DB tests would false-red) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T4.1 | Consolidation audit: enumerate every test added in P1–P2 against the AC table (AC1–AC17) AND map the ADR's binding pins T1–T8 (ADR §"Binding test pins" `:157-164`) into the matrix by T-id: T1 route order/guard → phase2 T2.3 tests (AC3/AC12/AC16); T2 auth-ON exemption → D19 harness extension (AC15); T3 over-broad-tag exclusion → phase2 T2.3 pin; T4 lockout (10th flips locked + `lockout` audit atomically; short-circuit; no auto-unlock; refresh revokes grants; concurrent-race serialization R9) → phase1 T1.6 + phase2 (AC10/AC11); T5 rate-limit interplay → phase2 T2.6 pins (AC9); T6 cookie (exact-path scope; decrypt-fail/generation-mismatch → interstitial; Secure plain-HTTP message) → phase2 T2.5 (AC8); T7 verify shape (unminted → uniform 404 NO row; locked page no content/attempts) → phase2 T2.3/T2.4/T2.7 (AC12); T8 view_link (always-passcode ×3 cases; docstrings amended; no passcode/token in logs) → phase2 T2.8 (AC4). Close gaps — specifically: byte-parity test for the direct lane (AC3), uniform-404 equivalence public-lane vs direct-lane across ALL miss classes (AC12), cookie TTL/generation/tamper matrix (AC8), strike-boundary + durability + refresh-revocation (AC10/AC11), method-surface matrix (AC9), reserved-root boot refusal (AC16), migration↔model parity (AC2), **W1 rotation-fail pin: flip `SYSTEM_ENCRYPTION_KEY` mid-test → passcode-row decrypt AND grant-cookie decrypt both collapse to the interstitial ("regeneration required" path), never 500** | A coverage matrix (AC + ADR T-id → test id → file:line) appended to the pack; every AC and every ADR pin T1–T8 has ≥1 named test; W1 pin present | `tests/unit/services/test_view_passcode_service.py`, `tests/unit/routers/test_live_views_router.py`, `tests/integration/test_auth_middleware_dispatch.py`, new `tests/unit/services/test_view_passcode_migration.py` (if not covered in P1) | P1, P2 | R8 | AC1–AC12, AC16 |
| T4.2 | Full-suite run + triage: whole test suite green (or pre-existing failures explicitly base-proven per house convention — run the same subset on the base commit to attribute); record counts per file ("N passed, M deselected" claims NAME which files ran — deselected @integration cases must not hide a red seal) | Suite transcript archived; every non-green test attributed pre-existing or fixed | test evidence (transcripts in planning dir `evidence/` or cited paths) | T4.1 | R8 | AC17 |
| T4.3 | Write `governor-review-pack.md` per D20: (a) surface inventory — exact route table added (`GET/HEAD/POST /views/public/{root}/{path}`, gate-free `GET /views/public/assets/mermaid/mermaid.min.js` alias), stateless grant cookie spec (ADR D6 claims/scoping), and what remains OAuth-guarded (ADR §Attack surface enumeration); (b) threat model — code brute force (→ bucket + durable lock + 29.7-bit bound, ADR D4), content probing (→ uniform 404 + row-gating + minted-vs-404 residual oracle ADR R3), lockout-DoS (ADR R4: bucket blunts, refresh recovers, grants survive), session theft (→ HttpOnly/Secure/SameSite + exact-path scope + 24h), XSS (→ zero-JS + ADR CSP set + escaping, ADR R10), CSRF (→ SameSite=Lax + Origin/Referer check, ADR D6:77), rotation blowup (→ fail-closed + re-encrypt walk + `ip_hmac` unlinkability, ADR R5), limiter fail-open (→ ADR V6/R12), enumeration via locked page (→ ADR R11, signed off); (c) controls map with test pins (ADR T1–T8 ids); (d) accepted-gaps section quoting ADR deviations V1–V6 + the ADR risk register verbatim as the CANONICAL register (W2); (e) resolved governor questions cited from the ADR §"Governor-ruling adjudication" (`:195-197` — all five rulings RATIFIED; R11 merge-gate sign-off granted) | Pack complete enough that a governor verdict needs NO code reading (anchors cited for every claim); ADR register cited as canonical | `.agents/shared/planning/live-view-passcode/governor-review-pack.md` | T4.1, T4.2, P3 | R1, R2, R3, R5 | AC18 |
| T4.4 | Governor review execution: dispatch the security-weighted review (governor lane) with the pack; capture verdict + conditions; convert any REQUIRED condition into a pre-merge task (fold back into phase files additively if it changes scope); record verdict in the pack footer | Verdict recorded with reviewer id + date; conditions either satisfied pre-merge or explicitly carried into the stage-RC report as disclosed caveats | `governor-review-pack.md` (footer), review evidence | T4.3 | — | AC18 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC17 | Full suite green with per-file attribution (which files ran, what was deselected); lockstep sweep (T2.9) clean incl. seal adjudication if triggered |
| AC18 | Governor security review COMPLETED with a recorded verdict (approve / approve-with-conditions / reject) BEFORE the merge in P5; conditions tracked to closure or disclosed |

## Exit criteria (phase gate)

- Coverage matrix complete (every AC1–AC17 pinned to ≥1 test); suite transcript archived.
- Governor verdict recorded; zero UNSPOKEN accepted gaps — every gap is a named decision.

## Risks (phase-local emphasis)

- Governor rejects a lean decision (e.g., D10's locked-page disclosure or the 200 status):
  treat as a scope delta — fold the required change additively into the owning phase file
  + `decisions.md` BEFORE P5; do not merge with an open rejection.
- Suite runtime growth (cookie TTL tests must not sleep wall-clock — inject TTL/expiry via
  service parameters, never `time.sleep`).
