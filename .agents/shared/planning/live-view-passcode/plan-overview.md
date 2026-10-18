# live-view-passcode — Plan Overview

> **Fold status (2026-10-17):** the architect ADR (`architecture-recommendation.md`, DECIDED) landed AFTER this plan set; the reviewer-ordered delta-fold has been EXECUTED (D-A1–D-A4 in `decisions.md`; all M1–M4 + W1–W3 items applied). This overview is reconciled with the ADR of record.
>
> **ADR of record:** `architecture-recommendation.md` in THIS directory — written in
> parallel by the architect. This plan set is
> written against the leader rulings (see `decisions.md` D1–D6); where the architect's
> ADR later refines a ruling, the delta-fold lands in `decisions.md` (D7+) and the touched
> phase files, additively.

## 1. Objective

Deliver per-view passcode protection for daemon live-views so a customer WITHOUT ensemble
OAuth access can open a specific artifact URL: an agent calls `view_link`, receives a
public URL + 6-char passcode, the customer enters the passcode on a mobile-responsive
interstitial, and the view renders under a ~24h path-scoped session cookie. The lane is
the daemon's canonical gate (proxies stay pure passthrough) and must survive BOTH
deployment states: State A (external oauth2-proxy at door.ensem.dev still in front, narrow
path exemption) and State B (middle server dropped, in-house OAuth2 module enabled).
Anti-brute-force: durable 10-strike lock per view until regeneration, plus per-IP rate
limiting ahead of the strike counter. Minimal durable audit. NO new Python deps, NO
frontend/ build involvement, uniform-404/no-leak discipline preserved (one consciously
extended case, D10).

## 2. Scope

**In scope:**
- One additive migration (2 tables: `view_passcodes`, `view_passcode_audit`) following the
  secrets-tables grouped precedent (`20261017_000001_create_secrets_tables.sql`).
- `ViewPasscodeService`: mint (idempotent) / verify (constant-time) / lock (durable) /
  refresh (regenerate + unlock) / audit, Fernet at rest via `SecretCipher` pattern
  (`daemon/services/secret_service.py:228`), fail-closed on missing `SYSTEM_ENCRYPTION_KEY`.
- Public route family `/views/public/{root}/{path}` in the existing live-views router:
  interstitial (zero-JS HTML page), POST verify (the ONLY POST on the entire /views
  surface), grant cookie issuance + validation, content serving through the SAME
  resolution/render pipeline as the direct lane (incl. markdown + mermaid wrappers).
- `view_link` tool change: returns URL + passcode always; `refresh: bool = False` param;
  lazy mint for legacy views (requirement 7); reserved-root guard ("public" root name
  forbidden).
- Per-IP in-memory token-bucket rate limit on the verify endpoint.
- State A proxy manifest change SPEC (data-center-scripts repo — separate lane, exact YAML
  diff documented here, implemented in the deployment step of the implementation
  commission) + State B exemption via the ADR-D3 **code-seeded `tag_routes_by_path`
  prefix tag** (the `auth.anonymous_view_paths` knob ships EMPTY and stays empty —
  operator-knob path REJECTED by ADR).
- Deployment note doc (exactly which namespace must stay public through the migration).
- Test consolidation + governor security-review pack + merge/stage-RC closeout.

**Out of scope:**
- Any change to the existing direct `/views/{root}/{path}` family behavior (requirement 5:
  byte-identical; OAuth path untouched).
- Share-token views (`view_shares` reserved stub `20261011_030502_create_view_shares.sql`
  — different v2 mechanism; this plan does NOT populate that stub and does not touch it).
- argon2/bcrypt/passlib adoption (deps forbidden; D6 rules them out anyway).
- frontend/ build or SPA routes (interstitial is daemon-served HTML per the mermaid-wrapper
  precedent `daemon/services/live_views.py:1707`).
- Time-boxed auto-unlock of lockouts (spec default is persist-until-regenerate; a time-box
  would be a flagged deviation — NOT taken).
- Any code change in data-center-scripts FROM THIS PLANNING RUN (planning docs only; the
  manifest change itself is a P3 implementation task in that repo's own lane).

## 3. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| 1 | Storage + service layer | phase1-plan.md | Migration + repository models + `ViewPasscodeService` (mint/verify/lock/refresh/audit) + Fernet cipher reuse + passcode generation/comparison primitives, fully unit-tested | — |
| 2 | Public lane: routes + interstitial + cookie + rate limit + tool + guard | phase2-plan.md | `/views/public` route family (gate→interstitial→verify→grant→serve), grant cookie, per-IP bucket, `view_link` change, reserved-root guard, public-asset mirroring | P1 |
| 3 | Deployment two-state: runbook + State A spec + State B exemption | phase3-plan.md | Deployment note doc; exact State A HTTPRoute diff (data-center-scripts lane); State B code-seeded tag note (ADR D3 — no operator action); verification drills | P2 |
| 4 | Test consolidation + governor review pack | phase4-plan.md | Full-suite green incl. auth-ON interplay; security-review pack (threat model + controls + known gaps) for the mandatory governor pass | P2 (P3 docs for pack completeness) |
| 5 | Merge + stage RC | phase5-plan.md | Merge to latest, stage release candidate + evidence (tests, staged version, binary sha, ADR + deployment-note pointers) | P3, P4 |

## 4. Coupling Map

| \ | P1 | P2 | P3 | P4 | P5 |
|---|----|----|----|----|----|
| **P1** | — | service consumed by routes+tool (import direction P2→P1); migration must land before any route test | — | service tests feed pack | — |
| **P2** | strong (service seam) | — | PUBLIC_NAMESPACE constant + interstitial/verify paths feed runbook + proxy PathPrefix | route tests feed pack | — |
| **P3** | — | medium (namespace constant is the contract) | — | runbook excerpt goes in review pack | runbook is a stage-RC deliverable |
| **P4** | medium | strong | medium | — | green suite is the merge gate |
| **P5** | — | — | medium (deployment note must be merged) | strong | — |

Shared-file couplings (single-writer discipline per phase):
- `daemon/services/live_views.py` — P2 only (public URL builder + gate helpers). P1 does
  not touch it.
- `daemon/routers/live_views.py` — P2 only. P3/P4 read, never write.
- `daemon/config.py` — P2 only (`LiveViewsConfig` knobs at :2642 + reserved-root guard).
- `agents/*` prompt files — P2 lockstep grep + minimal note updates ONLY if a seal-pinned
  file references view_link's old return shape (seal re-pin discipline applies; see R6).
- data-center-scripts `infranstructure/k8s/ensemble/httproute.yaml` — P3 implementation
  lane only (different repo; spec written here, applied there).

## 5. Risks

> **Risk-ID convention (W2, fold 2026-10-17):** the ADR risk register (`architecture-recommendation.md` §Risk register, R1–R12, council-merged) is **CANONICAL** — where the plan's local R-ids below name the same risk, the ADR id governs. Rough alignment: plan-R1≈ADR-R1 · plan-R2≈ADR-R6 (limiter reset) · plan-R3≈ADR-R6/V4 (XFF) · plan-R4≈ADR-R5 (rotation) · plan-R5≈ADR-R11 (locked-page disclosure, signed off) · plan-R6≈ADR-R7 (seal-pinned prompts) · plan-R7≈ADR-R8 (mermaid asset) · plan-R9≈ADR-R2-family (exemption scope discipline) · plan-R10≈ADR-R10 (XSS) · plan-R8/plan-R11 are plan-local (migration portability; cross-repo sync — not in the ADR register).

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | New UNAUTHENTICATED surface becomes the weakest link (brute force, content probing) | high | 3 independent controls: per-IP token bucket (P2), durable 10-strike lock (P1/P2), uniform-404 no-leak preserved (D10 exception documented); mandatory governor security review is a merge gate (P4→P5) |
| R2 | In-memory per-IP bucket resets on daemon restart → burst window | medium | Durable strike counter is the backstop (10 lifetime wrong tries per view per generation); single-VM deployment makes in-memory acceptable (D5); restart caveat documented in runbook; revisit only if multi-replica ever happens |
| R3 | X-Forwarded-For trust: behind Envoy the bucket keys on a spoofable header from direct-LAN clients | medium | RESOLVED BY ADR D7 — key on the RIGHTMOST XFF entry (proxy-appended = real client; leftmost entries are client-supplied/spoofable) else socket peer; fail-open on limiter-internal error (ADR V6) with the durable strike lock as backstop; runbook pins Envoy XFF append/overwrite behavior (ADR V4) |
| R4 | `SYSTEM_ENCRYPTION_KEY` rotation orphans passcode ciphertexts AND outstanding grant cookies | medium | Fail-closed decrypt → typed "regeneration required" → route renders ask-for-fresh-link page (never 500, never plaintext error); rotation coupling added to the standing rotation runbook note (extends the secrets P1 coupling) |
| R5 | Locked-page reveals view existence (row known + locked) — a no-leak discipline extension | low | Conscious extension (D10): the lock is attacker-caused (10 strikes) and a legit code-holder needs the "locked, ask for fresh link" message; every pre-lock miss stays uniform 404; governor explicitly signs off |
| R6 | `view_link` return-shape change breaks agent prompts/docs that assume a bare URL string | medium | Lockstep grep across `agents/*/` for view_link/`/views/` expectations (P2 task T2.9); minimal doc notes; if any seal-pinned file changes, seal re-pin is an EXPLICIT adjudicated step before merge (chart-image blueprint lockstep rule) |
| R7 | Mermaid-viewer asset (`/views/assets/mermaid/...`) sits OUTSIDE the public namespace → 401 via oauth2-proxy in State A / default-deny in State B → granted customers see broken diagram pages | medium | Mirror vendored assets under `/views/public/assets/...` served by the public family (D7); wrapper pages emitted on the public lane reference the mirrored path |
| R8 | Migration/DDL not portable SQLite↔PG breaks fresh-SQLite test boots (F1 family) | medium | DDL mirrors the secrets two-table migration (proven portable); additive-only CREATE TABLE + indexes; F1 premark (`tests/fakes/f1_premark_helper.py:48`) continues to apply unchanged; no ALTER on existing tables |
| R9 | Public-lane POST surface creep (any accidental POST route = new write vector on a public family) | high | POST exists ONLY on the public verify path; method-surface test pins GET/HEAD on both families + POST only there (AC9); FastAPI 405 elsewhere |
| R10 | Interstitial XSS / content injection via path reflection | high | Zero-JS interstitial (D9), no path echoed into HTML without escaping (page renders root/rel only as HTML-escaped text or not at all), CSP `default-src 'none'` with no script-src at all; form is same-origin POST |

## 6. Success Criteria

- AC set (continuous across phases; per-phase subsets in phase files): AC1–AC19.
- Headline gates:
  - `view_link` always returns public URL + passcode; idempotent by default; `refresh=True`
    regenerates and clears lock. Legacy views lazily mint on next call (AC4–AC6).
  - Customer flow works with ZERO ensemble credentials: open URL → interstitial → correct
    6-char code (case-insensitive) → content renders; ~24h cookie; subsequent opens skip
    the interstitial (AC7–AC8).
  - 10 wrong entries → passcode lane locked for that view, DURABLE across daemon restart;
    only `view_link(refresh=True)` clears it (AC10–AC11).
  - Direct `/views/{root}/{path}` family responses byte-identical to pre-feature (AC3).
  - OAuth path untouched: PUBLIC exact-path tuple unchanged; State B exemption is the
    ADR-D3 code-seeded `tag_routes_by_path(app, path_prefix="/views/public")` registration
    — `auth.anonymous_view_paths` stays EMPTY (AC14–AC15).
  - Uniform-404 preserved for every pre-lock miss class on the public lane (AC12).
  - No new Python dependency; no frontend/ build artifact touched (AC16).
  - Full test suite green; governor security verdict recorded before merge (AC17–AC18).
  - Release candidate staged with evidence: test output, staged version, binary sha,
    deployment note, ADR reference (AC19).

## 7. Assumptions & Open Questions

**Assumptions (explicit):**
- A-1: Single-VM deployment (daemon directly reachable at 192.168.1.151:9797 behind the
  Envoy Gateway) holds for the feature's life; in-memory rate limiting is acceptable under it (D5).
- A-2: `SYSTEM_ENCRYPTION_KEY` is present in every deployment that enables live views
  (same precondition as agent-secrets P1; fail-closed otherwise — D1).
- A-3: The public URL host is the same host as `external_base_url` resolution today
  (`daemon/services/live_views.py:1139` resolver chain); no new hostname is introduced.
- A-4: Passcode rows are keyed `(root_name, rel_path)` — one passcode per view path, not
  per recipient (no per-customer codes in v1).
- A-5: The architect's `architecture-recommendation.md` lands BEFORE P1 implementation
  starts; if a refinement contradicts a plan-proposed decision (D7–D11), the delta-fold
  updates `decisions.md` first, then the phase files additively. **[SATISFIED 2026-10-17:
  the ADR landed (DECIDED) and the reviewer-ordered fold was executed — see the header
  note and `decisions.md` D-A1–D-A4.]**

**Open questions (RESOLVED by the ADR of record, 2026-10-17 fold — kept for traceability):**
- Q-1 → RESOLVED: ADR V3/D2 — locked page = HTTP 200 with notice; pre-lock boundary stays uniform 404; ADR architect sign-off granted on the disclosure (ADR R11).
- Q-2 → RESOLVED: ADR D6:77 — adopt the cheap same-host Origin/Referer check on the verify POST as defense-in-depth; no CSRF token.
- Q-3 → RESOLVED: ADR D7 — RIGHTMOST-XFF keying dominates in every topology; single-VM is the only supported topology (multi-VM shared limiter store explicitly out of scope).
- Q-4 → RESOLVED: ADR D9 — `ip_hmac = HMAC-SHA256(ip, domain-separated key derived from SYSTEM_ENCRYPTION_KEY)[:16 hex]` (64-bit keyed HMAC, never the raw Fernet key); rotation unlinks historical rows by design. (Supersedes the plan's original unsalted-`sha256[:16]` lean.)
- Q-5 → RESOLVED: ADR D4 — alphabet `23456789abcdefghjkmnpqrstuvwxyz` (31 symbols, drops `0/O/1/i/l`), 6 chars, `secrets.choice` CSPRNG, case-insensitive entry; 29.7-bit entropy acceptable ONLY under the bounded-online-attack model (state the bound; grow alphabet/length if unthrottled offline comparison ever enters the threat model).
- Q-6 → RESOLVED: ADR D10 — the 5-field `view_link` shape INCLUDES `authenticated_url` (the legacy direct URL), so the question is moot; always-passcode holds in all three mint cases.

## 8. Appendix — Requirement → Artifact Traceability (W3, fold 2026-10-17)

| Req # (binding user requirement) | Discharged by (decision → task → AC) |
|---|---|
| 1 — 6-char alnum passcode, case-insensitive, unambiguous alphabet, minted at view_link | D4/D5 (phase1) → T1.5 → AC1; ADR D4 |
| 2 — view_link ALWAYS returns passcode + public URL | D8 (phase2) → T2.8 → AC4; ADR D10 (5-field shape, always-passcode all three cases) |
| 3 — unauthenticated lane: interstitial → code → view; ~24h path-scoped cookie; never passcode-in-URL | D9/D12/D13 (phase2) → T2.3/T2.4/T2.5 → AC7/AC8; ADR D6/D11 |
| 4 — 10-strike durable lock until regen; regen cheap + agent-callable | D3 (phase1) → T1.3/T1.6 → AC10/AC11; D8 refresh param → T2.8; ADR D5 |
| 5 — OAuth/authenticated path byte-identical unchanged | phase2 T2.3 byte-parity → AC3; ADR T1 pin; unchanged attack-surface enumeration (ADR §Attack surface) |
| 6 — two-state deployment; canonical daemon gate; proxies pure passthrough; deployment note | D15–D18 (phase3) → T3.1–T3.4 → AC14/AC15; ADR D3/D8 + 📌 DEPLOYMENT NOTE |
| 7 — legacy views: lazy mint on next view_link; simple + documented | D2 (phase1) → T1.3; T2.8 lazy-mint → AC6; ADR D11 |
| 8 — minimal durable audit: grants + lockouts (view, timestamp, outcome; IP optional/hashed) | D5 (phase1) → T1.1/T1.3 → AC2 + audit assertions; ADR D9 (keyed-HMAC IP, closed action set) |
| 9 — security posture: governor review mandatory; per-IP rate limit; no user-controlled JS; not plaintext at rest (deviation) | D1/D6 deviation (phase1); D9/D14 (phase2) → T2.6/T2.7 → AC7/AC9; phase4 governor gate → AC18; ADR V1 (deviation) + D7 + D11 |
| 10 — process: branch → governor review → test → merge → stage RC → report w/ evidence | phase4 → phase5 (D22/D23) → AC17/AC18/AC19 |
