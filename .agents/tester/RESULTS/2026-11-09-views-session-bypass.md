# VIEWS-SESSION-BYPASS — Functional Gates + (planned) Staged-Release Verification

- **Commission:** FUNCTIONAL GATES (G1–G8) + staged release v0.29.7 (NO promote) for the views-session-bypass lane
- **Worktree:** `/home/nea/ensemble-src-wt-views-session-bypass` · branch `feature/views-session-bypass` @ `87c3505785a7aef1fc6f2f9def52127af77f3b5a` (4 commits on base `f49006b92` = v0.29.6 tag peel) · worktree clean at lane start
- **Date:** 2026-11-09 (22:08–23:00 UTC) · **Test leader:** tester · **Executor workers:** intel `264a0e5f`, core-gates `b49aec56`, g1-browser `6bcc6a2c`, http-matrix `9b7dd82a`, g7-surface `bd2ded4a`, g7-lane `59f39708`, docs-commit (below)
- **Scope decision:** scoped verification, NOT full suite — blast radius = 4 files (1 router, 2 services [one purely additive +32/−0], 1 new test file), zero-diff proven on `daemon/config.py`, `daemon/middleware/`, `daemon/auth/`, `daemon/api.py` and every router except `live_views.py`; no core seam → Tier-1 gate. Ran: lane pack + 5-file registered surface + Core always-on pack + behavioral gates. Full suite not warranted.

## VERDICT: **PHASE 1 FAIL — G2.7 (origin-gate host-capture tautology). PHASE 2 NOT STARTED** (commission contract: "all must pass; STOP and report on any fail"). Slot v0.29.7 remains FREE (tag absent local+origin; no ceremony in flight; `state.json` current=v0.29.6, in_flight=null).

Everything else is green: the original browser symptom is dead (G1, proven twice at wire level with a differential base proof), session bypass semantics confirmed end-to-end incl. gate order, anonymous flow byte-identical, lockout unchanged, 74/74 lane tests + 196/196 unmodified surface + Core gates pass.

---

## Gate results

| Gate | Verdict | Decisive evidence |
|---|---|---|
| Intel / confinement | ✅ PASS | HEAD verified; exactly the 4-file diff set; zero-diff on config/middleware/auth/api.py + all routers≠live_views; 5 auth-lane suites byte-unmodified; collect 74+196 exact; ports free; no ceremony in flight; v0.29.7 slot free; pyproject 0.29.6 |
| Core gates (ensure.md) | ✅ PASS | `concurrency_atomic_unit_test` exit-1 raw = exactly QUARANTINE row-134 nodes (`test_instance_metadata_atomic.py` ×5, base-A/B-proven 2026-10-22, zero overlap with lane diff); skip-confirmation leg exit 0 (94P/74S). `dev.sh:155` graceful-shutdown grep PASS |
| **G1 closure (browser)** | ✅ **PASS** | Natural `Origin: null` (zero header injection, untrusted TLS, CDP wire capture) → **303** + `view_grant` (Path/HttpOnly/Secure/SameSite=Lax/Max-Age=86400 all on-wire) → PRG → 200 view HTML; ×2 independent runs; differential proof: base `f49006b92:_same_host_origin_check` executed verbatim → null REJECT(403), branch → ALLOW; controls: null+cross-site → 403 HTML, evil origin → 403 HTML, JSON only for explicit JSON clients; session rider: `ensemble_sid` alone → direct serve, no interstitial, no mint. Assets: `assets/2026-11-09-views-session-bypass/g1/` + `/tmp/board_{before,after}.png` |
| **G2 origin matrix** | ❌ **FAIL 8/9** | Cases 1–6, 8a, 8b PASS (null→303; www≡apex both directions; null+cross-site→403 HTML; no-Origin→303; evil→403 HTML; XFH/Forwarded inert without declared front). **Case 7 tautology FAILS** — see finding below |
| G3 session bypass | ✅ PASS | (a) public GET w/ session → 200 direct, no interstitial; (b) direct lane w/ session → 200 (without → 302 login redirect); (c) **gate order proven experimentally**: bucket burned to 429 → session POST still 303+grant, immediate anonymous correct-code POST → 429 ⇒ session admission PRECEDES the limiter (R1 confirmed); (d) GET mints nothing (0 Set-Cookie) |
| G4 stale/invalid session | ✅ PASS | Expired (−1h) and garbage `ensemble_sid` → interstitial HTML, never 403/forbidden, never JSON for browser Accept; POST falls to anonymous chain and grants |
| G5 anonymous flow | ✅ PASS | Raw Set-Cookie exact: `Path=/views/public/<view>; HttpOnly; Secure; SameSite=Lax; Max-Age=86400`; wrong code → HTML re-render (never JSON); plain-HTTP (no XFP) → secure-context page, 0 grants — unchanged |
| G6 never-JSON + R3 | ✅ PASS | 404/locked/429/403 all HTML for `Accept: text/html`, JSON for `application/json`; byte-identity sha-proven on JSON-native surfaces (404 `958f0b88…`, 403 `2815fee6…`); path-disclosure grep CLEAN across all bodies |
| G8 lockout safety | ✅ PASS | Lock at strike 10; locked short-circuits before compare (correct code does not unlock); bucket 429 + ≈13s refill recovery — identical to prior brute-lane estate |
| G7 regression | ✅ PASS | `session_bypass_lane_74`: **74/74** (4.63s). `liveview_surface_196` (registered, byte-unmodified files): **196/196 hermetic** — first leg 171P/6F/19E was 100% ambient `ENSEMBLE_AUTH_DISCOVERY_URL/ISSUER` poisoning (see LESSONS); scrubbing only that env family → 196/196 exit 0. Disjointness PROVEN |
| R5 view_write sanity | ✅ PASS | Exactly 3 `resolve_views_session` callsites (public GET :2087, verify POST :2364, board gate :2623); `view_write` has NO HTTP surface (agent tool, tool-layer authz) — rides the same seam by construction |

## THE FINDING — G2.7 origin-gate host-capture tautology (BLOCKER for this commission; security severity MAJOR; anomaly `2c719281`)

- **Probe:** `POST <viewA verify>` with `Host: evil.example` + `Origin: https://evil.example` + `X-Forwarded-Proto: https`, browser Accept, CORRECT passcode.
- **Expected:** graceful fail-closed reject (commission R2/G2.7: "tautology fail-closed… NOT satisfy via host-capture").
- **Observed:** **303 See Other + full `view_grant` issuance** (`set-cookie: view_grant=gAAAAAB…; Path=/views/public/g2g8matrix/matrix-a.md; HttpOnly; Secure; SameSite=Lax; Max-Age=86400`). Full capture: `assets/2026-11-09-views-session-bypass/g2g8/G2.txt` case 7.
- **Mechanism:** `_views_trusted_hosts` leg (a) adds the RAW client `Host` header as a trust anchor unconditionally → any self-consistent Host+Origin pair (DNS-rebinding shape) matches. The no-anchor tautology guard only fires when no leg yields an anchor (e.g. malformed Host port). Corollary: cases 2/3's www≡apex matches also ran against an attacker-chosen Host — same root cause.
- **Cross-references:** (1) the auth lane already hardened exactly this class — `daemon/auth/cookie.py:249` `host_is_trusted` gates its same-origin leg; the views lane did not carry it over. (2) The developer's POK contract `views-origin-gate-contract` states "server-anchor match (Host/audience/declared base, **never host-captured**)". (3) The lane's own test `test_portless_host_vs_explicit_origin_port_elides` PINS same-host Host+Origin as granting — the permissive behavior is test-pinned, i.e. a deliberate implementation choice that still violates the stated contract. (4) The review council approved "origin gate NOT bypassable within the stated threat model" — the council's stated model presumably excluded host-capture; the commission's spec includes it.
- **Suggested fix shape** (implementer owns): qualify leg (a) with `host_is_trusted`-style audience gating mirroring `cookie.py` so an arbitrary client Host can never self-satisfy the anchor match; update the pinning test; re-run this gate matrix (single-scenario re-run is cheap — the harness is in the assets).
- **Exploitability note:** attacker still needs the correct passcode for a grant; the practical exposure is the CSRF/origin-check being satisfiable by a rebound Host (defense-in-depth regression vs the auth lane), which is why this is MAJOR security-severity but BLOCKER for the release contract. Final severity + threat-model scoping belongs to the leader/security council.

## Informational deviations (not gate failures)

1. **R3 "otherwise" boundary:** byte-identical JSON proven on JSON-native surfaces (404/403). On HTML-native pages (locked 200, 429) `text/plain` receives HTML — the JSON flip is explicit-`application/json`-only (`_accept_wants_json_page`), preserving R4 browser shapes. Recorded as per-design; flag to the leader if the letter of R3 was intended to cover `text/plain`.
2. **429 carries no `Retry-After` header** — the HTML page carries the notice; refill recovery verified empirically (~12s/token). Same as prior lane — unchanged.

## Disclosures

1. **Concurrent foreign edits (NOT mine, untouched):** at 22:33–22:34Z a foreign actor left UNCOMMITTED edits on `daemon/routers/live_views.py` + `daemon/services/view_passcode_pages.py` mid-G1. The g1 worker proved them non-functional via normalized-AST identity (annotations/TYPE_CHECKING/docstrings stripped → AST identical; no origin-gate symbol diffs). All verdicts stand for `87c350578` + these overlays. The tester commit below is path-limited and does NOT include them. Giter finalize remains the leader's signal.
2. **Key-bearing transcript (PB-F1 family, known):** the intel worker's scrub-evidence grep echoed a live-shaped `BLUEPRINT_EMBEDDING_API_KEY` (sk-proj-…) into its transcript. No network contact occurred during collection. Folds into the standing user-gated deferred-rotation scope (urgency note already carried by the F7 addition).
3. **Ambient env traps (POK `auth-lane-scrub-ensemble-auth-env`):** operator shells carry live `ENSEMBLE_AUTH_ISSUER/DISCOVERY_URL/EXTERNAL_REDIRECT_BASE` — poisons auth-fixture packs (25 red nodes) and refuses fenced auth-ON boots (exit 78). All legs re-ran hermetic; boot recipes scrub per-variable.
4. **Advisory:** 2 lane tests use deprecated starlette per-request `cookies=` (`TestDirectLaneSessionComposition` ×2) — future-maintenance flag for the lane owner, passing today.
5. **Quarantine:** ZERO new rows. Core pack raw 5F = pre-existing row 134 (base-attributed, ledgered 2026-10-22). Dev-DB drill residue: `g1root1/g1root2` + `g2g8matrix` passcode rows (gen 1, no locks) — deliberate, dev-only.
6. **Quick fixes applied: NONE** (zero code modifications by any tester worker; worktree tree state = foreign overlays only).

## Ceremony notes (Phase 2 — NOT performed, by contract)

Pre-verified staging inputs (all still valid for the post-fix re-run): slot v0.29.7 free (no local/origin tag; `state.json` current=v0.29.6, previous=v0.29.4, in_flight=null, pending_op=null); base `f49006b92` peels exactly to tag `v0.29.6`; pyproject on branch = 0.29.6 (next-patch bump = 0.29.7); scripts-pin floor `2147c0298` ⊆ pin `84df6b55c` (ARM-CLEAN per state.json arm provenance); no ceremony in flight at any point this lane. Boot-smoke pin set + S1–S5 verification shape pre-assembled from `RESULTS/2026-11-08-v0293-staged-verification.md` (scratch-copy boot, SELF_ENV=sandbox, SWEEP=0 pinned, `env -i` allowlist + SYSTEM_ENCRYPTION_KEY, throwaway PG, banner/livez/readyz, state.json byte-fence, 4-way sha, migration delta expected 0, PROMPT-BASELINE 181).

## Artifacts index

- `assets/2026-11-09-views-session-bypass/g1/` — RUN-LOG.md, board_before.png / board_after.png (1280×800), g1-browser-evidence{,-run1}.json (CDP wire), logs/ (daemon+edge, curl controls, base-fn differential proof), scripts/
- `assets/2026-11-09-views-session-bypass/g2g8/` — G2…G8.txt, R5.txt, RUN-ENVIRONMENT.txt, boot/mint/session logs, all probe scripts (mirrored `/tmp/g2g8-evidence/`)
- Logs: `/tmp/g7_lane74.log`, `/tmp/g7_surface.log`, `/tmp/coregates.log`
- Anomaly: `report_anomaly` id `2c719281` (G2.7, major)

## Addendum (same session): branch movement + tip-state hardening

The branch moved past the commissioned SHA mid-lane (giter polish, all disclosed): `87c350578` → `91d1617e2` (style folds on the 2 daemon files — the exact overlays G1 proved AST-identical) → `9bb0651dd` (lane-test consolidation: 5 defs → 2 parametrized, 74→74 nodes) → `4a5a9582b` (tidier notes) → `24924ce08` (this report's tester docs commit).

- **G2.7 STILL PRESENT AT TIP — proven, not inferred:** `_views_trusted_hosts` (sha256 `eb7802c4c137bc1f…`) and `_same_host_origin_check` (sha256 `6b2d5fb69ec90f9f…`) byte-identical between `87c350578` and HEAD; the production diff vs the verified SHA is docstrings/comments/annotations/`TYPE_CHECKING` imports only (AST proof + per-hunk audit + `py_compile` OK; `daemon/services/live_views.py` untouched branch-wide).
- **Lane pack re-run at tip `24924ce08`: PASS 74/74 (5.04s)** — the consolidation is empirically node-preserving and green (`/tmp/g7_lane74_tip.log`). Notably this pack is NOT ambient-`ENSEMBLE_AUTH_*`-sensitive (green with the live door.ensem.dev vars present); the per-variable scrub stays default policy anyway.
- Surface files **zero-diff at tip** → the 196/196 remains a byte-faithful measurement of the shipping tree.
- **Test-suite posture at tip:** no test anywhere asserts rejection of a matching non-testserver Host/Origin pair; the two permissive pins survive (`test_portless_host_vs_explicit_origin_port_elides` :1647, `test_explicit_vs_explicit_match_grants` :1730) — the G2.7 fix must flip/update pins alongside the gate, and the review council's "stated threat model" should be re-scoped to explicitly include or exclude host-capture.
- 26 evidence files (`assets/…/g1/logs/` 25 + `g2g8/boot.log`) are gitignore-excluded (`logs/`, `*.log`) — disk-resident at the stable worktree path, NOT force-added (gitignore policy call left to the leader); fail-closed secret scans ran clean over staged AND excluded evidence.
