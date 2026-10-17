# live-view-passcode — Decisions Ledger

Seeded with the leader rulings at plan-creation time (2026-10-17). Statuses:
`decided (leader)` = binding ruling this plan executes within · `plan-proposed` = lean
decision made by THIS plan, open to architect-ADR refinement · `deviation (reasoned)` =
conscious, documented departure from a requirement's suggested mechanism.
The architect's `architecture-recommendation.md` (same directory) is the ADR of record;
refinements land here ADDITIVELY as D7+ entries with rationale + source (delta-fold rules:
no renumbering, supersession recorded by reference).

---

## Leader rulings (seeded verbatim-in-spirit, one-line rationale each)

### D1 — Storage: Fernet-encrypted at rest + constant-time compare — `decided (leader)`
Passcodes are stored Fernet-encrypted (SecretCipher pattern,
`daemon/services/secret_service.py:228-361`, fail-closed on missing
`SYSTEM_ENCRYPTION_KEY`); verification compares via `hmac.compare_digest` after
casefold normalization.
Rationale: plaintext-at-rest is forbidden (requirement 9); the cipher + fail-closed
construction already exist in-repo (zero new deps).

### D2 — Public namespace: `/views/public/<root>/<path>` + reserved-root guard — `decided (leader)`
Passcode-gated mints live under the `/views/public` prefix (PathPrefix-exemptable in both
deployment states, groups under views semantics); no configured root may claim the name
"public".
Rationale: one stable prefix is the contract shared by daemon code, State A proxy YAML,
State B env config, and the runbook.

### D3 — Grant cookie: stateless Fernet token — `decided (leader)`
Session grant = stateless Fernet token embedding view identity + passcode generation +
expiry (~24h), path-scoped, HttpOnly/Secure/SameSite=Lax; passcode regeneration bumps
generation so old grants die; NO server-side session table.
Rationale: revocation-via-generation needs no session store; the signing key already
exists (rotation coupling documented, R4).

### D4 — Lockout: durable counter + persist-until-regenerate — `decided (leader)`
10 wrong attempts on a view → passcode path LOCKED for that view; `failed_attempts` +
`locked` are durable columns on the passcode row (PG-backed, additive migration); lock
persists until regeneration; NO time-boxed auto-unlock (spec default, not a deviation).
`view_link` default is idempotent (existing passcode returned); explicit `refresh` param
regenerates + clears the lock.
Rationale: requirement 4 demands durability; auto-unlock would be an unforced deviation.

### D5 — Rate limit: per-IP in-memory token bucket ahead of the strike counter — `decided (leader)`
The public verify endpoint is rate-limited per-IP (in-memory token bucket) BEFORE the
10-strike counter is consulted; acceptable on the single-VM deployment (caveat: bucket
resets on restart — the durable lock is the authoritative control).
Rationale: requirement 9 orders per-IP limiting ahead of the lock; in-memory avoids PG
write amplification per request.

### D6 — DEVIATION (reasoned): plaintext passcode at mint vs "hashed" storage suggestion — `deviation (reasoned)`, `decided (leader)`
Requirement 9 suggested hashed (argon2/bcrypt) at-rest storage; the executed posture is
Fernet-ENCRYPTED at rest with PLAINTEXT returned by `view_link` at every mint.
Rationale: requirement 2 ("view_link result ALWAYS includes the passcode") forces
plaintext re-display on every call — a hash cannot be re-displayed, so hash-only storage
is INFEASIBLE against the binding requirements; reversible encryption is the weakest
at-rest primitive compatible with re-display, and it reuses the existing key + cipher
(D1). This entry exists so the governor review (P4) adjudicates the deviation EXPLICITLY.
**CONFIRMED by ADR Deviation V1** (2026-10-17): same ratified class as agent-secrets
plaintext-at-mint; ADR D4 additionally rejects the dual-store (hash+cipher) variant —
"two protections, same key, zero gain".

---

## Plan-proposed decisions (open to architect-ADR refinement; refine additively)

### D7 — Mermaid vendored asset mirrored under the public prefix — `plan-proposed` → CONFIRMED by ADR D11:130 (as a GATE-FREE alias under asset-header lockdown)
Public-lane wrapper pages reference `/views/public/assets/mermaid/...`; the vendored asset
is served by the public family (gate-free static alias; same vendoring pin).
Rationale: the canonical `/views/assets/...` path sits outside the exempted namespace and
would 401 via oauth2-proxy (State A) / default-deny (State B) — broken diagram pages for
granted customers (R7 / ADR-R8).

### D8 — `view_link` return shape: labeled lines + `refresh` param — `plan-proposed`
`view_link(root_name, path, refresh: bool = False)` returns stable labeled lines
(`URL:`, `Passcode:`, and `Status: locked — use refresh=True` when locked); category
`view-views` unchanged.
Rationale: requirement 2 + house plain-string tool-result style; the locked line lets the
agent self-serve the unlock path in one call.

### D9 — Zero-JavaScript interstitial — `plan-proposed`
The passcode page is daemon-served self-contained HTML (mermaid-wrapper precedent,
`daemon/services/live_views.py:1707`), static CSS only, CSP `default-src 'none'` with NO
script-src at all, mobile-responsive, form POST same-path.
Rationale: requirement 9's "no user-controlled JS" is best satisfied by NO JS at all;
strongest XSS posture; zero frontend/ build involvement (hard constraint).

### D10 — Locked-page existence disclosure = conscious no-leak extension — `plan-proposed` (governor confirm)
A locked view renders a dedicated "locked — ask for a fresh link" page (lean HTTP 200,
Q-1 pending). Every PRE-lock miss class stays uniform 404
(`_uniform_404`, `daemon/routers/live_views.py:451`).
Rationale: the lock is attacker-caused; legit code-holders need the actionable message;
disclosure is consciously accepted and explicitly adjudicated at the governor review.

### D11 — Public namespace prefix is a FROZEN constant, not config — `plan-proposed`
`PUBLIC_NAMESPACE = "/views/public"` as a module constant; reserved-root guard at config
validation + route resolution.
Rationale: the prefix is a cross-repo deployment contract (code + proxy YAML + env +
runbook must agree byte-for-byte); config-izing invites drift across the two-state
migration.

### D12–D18, D22–D23 — Implementation mechanics — `plan-proposed` (detailed in phase files)
Grant-cookie codec + flags (D12, phase2), verify-endpoint shape + ordering (D13, phase2),
per-IP bucket wiring + config knobs (D14, phase2), two-state exemption scope (D15–D17,
phase3), deployment-note placement (D18, phase3), test consolidation + review-pack shape
(D19–D21, phase4), merge/stage ordering + evidence bundle (D22–D23, phase5). Each carries
its own rationale + rejected alternatives in its phase file; the architect may refine any
of them via the ADR without renumbering this ledger.

---

## Reserved for architect refinements (delta-fold landing zone)

> **FOLDED 2026-10-17.** The ADR (`architecture-recommendation.md`, DECIDED, 18:21) landed after this plan set (18:08–18:13); reviewer verdict REJECT-WITH-BLOCKERS required the delta-fold below. All ADR anchors were verified in the worktree before folding. Doc sites updated in the same pass; nothing outside the named M/W/ALSO scope was touched.

### D-A1 — State-B exemption mechanism — SUPERSEDED: phase3-D17 → **ADR D3**
The operator-knob path (`auth.anonymous_view_paths=["/views/public"]`) is REJECTED. The mechanism is the **code-seeded PUBLIC prefix tag**: `tag_routes_by_path(app, path_prefix="/views/public")` called at the create_app registration site AFTER `include_router` (the existing PUBLIC exact-tag loop at `daemon/api.py:3526-3560` runs BEFORE `include_router` at `:4099` — a SECOND, prefix-mode call is required; machinery `daemon/auth/route_meta.py:66-100` DOES support prefix tagging, correcting this plan's earlier "exact-template lane" mischaracterization). The knob ships EMPTY and stays empty. Doc sites updated: phase3 (objective, D17, D18, T3.1c, T3.3iii, AC15, exit gate, R11-note), plan-overview (scope, P3 row, AC14–15 headline), phase4 (D19), phase5 (T5.1 gate).

### D-A2 — Audit schema + IP hashing — SUPERSEDED: phase1-D5/Q-4 → **ADR D9**
`ip_hash = sha256(ip)[:16]` replaced by `ip_hmac = HMAC-SHA256(ip, domain-separated key derived from SYSTEM_ENCRYPTION_KEY)[:16 hex]` (64-bit keyed HMAC — never the raw Fernet key; rotation unlinks historical rows by design). Audit schema adopted: closed action set `{mint, regenerate, grant, deny, lockout}` (TEXT column, not enum), `actor` NULLable for public actions, `wrapped_passcode` BLOB/BYTEA ciphertext, `last_attempt_at`, partial index `WHERE locked`, per-request cookie failures log-only (write-amplification). Q-4 CLOSED. Doc sites: phase1 (T1.1, D5, T1.2, T1.3, T1.6), plan-overview (Q-4), decisions Q-table.

### D-A3 — Phase2/3 ADR drift items — **folded per ADR D6/D7/D10/D11/§2:186** (M3)
(a) Rate-limit keying: leftmost/first-XFF → **rightmost-XFF else socket peer** (ADR D7:83-86) + fail-open (V6); (b) grant-cookie claims `{view_id, …}` → **`{root_name, rel_path, generation, exp}`** (ADR D6:75); (c) verify handler order → **bucket → unminted-404 → locked short-circuit → compare → strike/grant** (ADR §Attack surface :186); (d) interstitial CSP → **`default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` + nosniff + no-store + no-referrer** (ADR D11:129 — supersedes `style-src 'unsafe-inline'`); (e) `view_link` shape → **ADR D10 5-field `{public_url, passcode, authenticated_url, generation, locked}`** + docstring debt (tool "Read-only" + family "no POST" docstrings amended, ADR D10:123); (f) mermaid asset alias is **gate-free** under asset-header lockdown (ADR D11:130); (g) public sub-routes declared BEFORE the parametrized handlers — route order load-bearing (ADR D1:26); (h) locked page HTTP 200 DECIDED (ADR V3, sign-off ADR D2/R11); (i) State-A rule timeout 5m (ADR D8; 30m acceptable tunable). Doc sites: phase2 (D7–D14, T2.3–T2.8, T2.10), phase3 (D16, T3.1b), plan-overview (Q-1/Q-3/Q-6 resolutions, R3).

### D-A4 — Council sharpenings fold (enumerated from ADR council-merged sections; raw manifest not reachable from planner tree)
Provenance: enumerated from the ADR's council-merged material (risk register :166+, governor-ruling adjudication :195+, sharpenings embedded in D1–D11); the raw council manifest ran under the architect's tree and is not reachable from the planner partition — this enumeration is ADR-derived, never a silent skip. **4 of 11 were already reflected** in the pre-ADR docs; **7 folded in this pass** (fold sites in parentheses):

| # | Sharpening | Status |
|---|---|---|
| S1 | Idempotent `view_link` does NOT clear an active lock (ADR D5:63) | pre-reflected (phase1 T1.3 refresh-only-unlock) — made explicit in this fold |
| S2 | No remaining-attempt disclosure pre-lock — never aid multi-IP grinding (ADR D2:40) | pre-reflected (phase2 T2.4 strike-count-never-disclosed) |
| S3 | Uniform-404 for unminted paths; no phantom rows; no interactive verify oracle on garbage (ADR D2:36-39) | pre-reflected (phase2 T2.3/AC12 row-gating) |
| S4 | Same-host Origin/Referer check on verify POST; no CSRF token (ADR D6:77) | pre-reflected (phase2 D13/Q-2 lean — now ADR-decided) |
| S5 | Valid grant cookies persist through lockout — lockout gates entry lane, not established grants (ADR D5:68) | FOLDED → phase2 D12/T2.5, phase4 T4 map |
| S6 | Rightmost-XFF limiter keying, dominates every topology (ADR D7:83-86) | FOLDED → phase2 D14/T2.6, phase3 R3, overview R3 |
| S7 | Keyed-HMAC domain-separated IP hashing (ADR D9:116) | FOLDED → M2/D-A2 |
| S8 | Over-broad-tag exclusion test pin — legacy template can't be swept into the prefix tag; matcher is evolvable shared code (ADR D1:32/T3) | FOLDED → phase2 T2.3, phase4 T4.1 (T3) |
| S9 | Rate-limiter fail-open on internal error; strike lock is the backstop (ADR D7:88/V6) | FOLDED → phase2 D14/T2.6 |
| S10 | Logging hygiene: never log passcodes/grant tokens/ciphertexts; wrong-passcode lines carry counter snapshot, not the submitted value (ADR D11:131) | FOLDED → phase2 T2.4 |
| S11 | Plain-HTTP edge: `Secure`-cookie failure renders an explicit "secure context required" message, never a redirect loop (ADR D6:79) | FOLDED → phase2 D12/T2.5 |

Additional ADR items adopted in the same pass (not council sharpenings per se): W1 rotation-fail pin (phase4 T4.1), ADR T1–T8 mapped by id into the phase4 matrix (🟢), route-declaration-order discipline (D-A3g), no-reflection + `autocomplete=off` form contract (ADR D11:129).

## Open questions (RESOLVED by the ADR of record, 2026-10-17)

| ID | Question | Resolution (ADR ref) |
|----|----------|----------------------|
| Q-1 | Locked-page HTTP status | **CLOSED** — 200 with notice (ADR V3; sign-off D2/R11) |
| Q-2 | CSRF posture on verify POST | **CLOSED** — same-host Origin/Referer check, no token (ADR D6:77) |
| Q-3 | XFF trust depth for the per-IP bucket | **CLOSED** — rightmost-XFF else socket peer (ADR D7) |
| Q-4 | Audit IP hashing scheme | **CLOSED** — keyed-HMAC `ip_hmac`, 64-bit, domain-separated key (ADR D9) — supersedes the plan's unsalted-sha256 lean |
| Q-5 | Final passcode alphabet | **CLOSED** — 31-symbol `23456789abcdefghjkmnpqrstuvwxyz`, 6 chars, CSPRNG (ADR D4; entropy bound stated under bounded-online-attack model) |
| Q-6 | Should view_link also return the legacy direct URL | **CLOSED** — yes, as `authenticated_url` in the ADR-D10 5-field shape |

---

## Reserved for leader re-ruling

- If any future ADR refinement contradicts a LEADER ruling (D1–D5), it does NOT fold silently — it is recorded here as a flagged conflict for the leader to re-rule. (None at fold time: ADR D4/D5/D6/D7/D9/D10 refine within the rulings; D6-at-rest deviation confirmed as ADR Deviation V1.)

| Q-6 | Should view_link also return the legacy direct URL | No — one URL per mint |
