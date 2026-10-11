# Architecture Recommendation: OAuth2 Plan Validation & Enrichment

Date: 2026-10-10
Author: architect (controller) — synthesis of 3 dispatched validation workers
Mode: Standard Design (area-based validation fan-out; council criteria not met — validation of an additive, default-OFF, reversible layer)
Deliverable type: design validation of the PLAN-ONLY artifact set (`plan-overview.md` + `decisions.md` D1-D16 + `technical-analysis.md` [TA] + `requirements.md` [REQ] + phase1-6)

**Verdict up front: the plan's architecture is SOUND to hand to implementation —
with 3 MUST-FIX defects, 1 MUST-ADD missing seam, and a short list of
hardening conditions.** No load-bearing decision (flow choice, LIFO placement,
PG store, machine lane, health invariant) is reversed. Every condition below is
an amendment to the plan text, not a redesign.

---

## 0. Validation Method + Provenance

| Worker | Skill | Instance | Coverage | Adjudication |
|---|---|---|---|---|
| A `pipeline` | structural-design | ad473046 | Focus 1 (pipeline order, SSE/.md/Mount), Focus 5 (seam audit), OD-6 wiring | **DEGRADED — skill bank miss** (`structural-design` absent from this project's bank; worker confirmed via bank search). Substance independently verified by architect against pinned Starlette 0.52.1 source — accepted. |
| B `seclane` | security-design | 63004176 | Focus 3 (machine lane, scopes, bootstrap), OD-1/2/3/4/6/12/14 | Accepted — key claims spot-verified (`ens_env_read` override path, CORS block, forge-lane sites). |
| C `resilience` | resilience-design | 84e8c344 | Focus 2 (session store), Focus 4 (failure-mode architecture), OD-9 | Accepted — key claims spot-verified (exit-75 preflight, readiness conjunction, migration runner, launcher location). |

All code citations below were re-anchored by the workers and spot-verified by
the architect. Line-drift register for the implementer (plan citations → live):

| Plan says | Reality (2026-10-10) |
|---|---|
| `jobs_crud.py:275-278` forge lane | `daemon/routers/jobs_crud.py:595` (`source=body.source` — client-supplied) |
| `messages.py:391` forge lane | `daemon/routers/messages.py:690` (`source="api"` hard-coded) |
| `scripts/upgrade/launcher.sh:52-54` | `launcher.sh:52` (repo ROOT) |
| "27 routers" (REQ §5.4) | 28 (`tmp_images_router` at `api.py:3142` post-dates plan) |

---

## 1. Focus-Area Verdicts

### 1.1 Middleware pipeline order — **ENDORSE-WITH-CONDITION** (2 must-fix defects inside)

The planner's core design is correct against the real stack:
auth-outermost LIFO (register 4th → execute 1st), after-HostCapture,
conditional registration when OFF, zero-I/O public fast-path. Verified
anchors: `daemon/api.py:3012-3032` (3 middlewares today: CORS
wildcard+credentials → SelectiveAccessLog → HostCapture), health probes as
post-middleware app routes (`api.py:3172-3238`), `host_capture.py`
scope-type guard pattern.

Endorsed sub-rulings: 401-before-CORS-mutation on non-preflighted
credentialed calls ✓; recorder last-write-wins cold-start handled by override
+ 503 ✓; `/views/*.md` wrapper path has NO ordering conflict (views router at
`api.py:3259` registered before SPA catch-all, which carves out `views*` at
`:3292-3294`) ✓; SSE cookie-on-initial-GET with 401-not-event-error is the
right contract (EventSource cannot send headers) — implementation must keep
`Cache-Control: no-cache` + `X-Accel-Buffering: no` on the 5 SSE routes ✓;
websocket passthrough canonical ✓; default-OFF = not-registered (introspectable
via `app.user_middleware`) ✓.

**🔴 MUST-FIX D1 — the plan's Mount-bypass claim is factually wrong.**
TA §Middleware Placement rule 6 (`:716-722`) claims "Starlette `Mount`
applications run OUTSIDE the parent middleware stack" so `/api/mcp/kb` stays
unauthenticated. False for pinned Starlette 0.52.1 / FastAPI 0.133.1:
`build_middleware_stack` wraps `self.router` (`starlette/applications.py:75-93`)
and `app.mount()` registers Mounts inside that router (`routing.py:373+`) —
`add_middleware(AuthMiddleware, ...)` IS applied to `/api/mcp/kb/*`. Un-amended,
auth-ON 401s every cross-process KB-MCP consumer (local agents) on day 1.
**Fix:** add `/api/mcp/kb` and `/api/mcp/kb/sse` to `AUTH_DEFAULT_PUBLIC_PATHS`
in v1 (preserves today's loopback posture verbatim; the middleware never
touches them), document the loopback trust rationale in the runbook, and
reserve a KB-MCP-auth seam for a future commission. Amend TA rule 6 and the
[REQ] §5.5 mounts row accordingly.

**🔴 MUST-FIX D2 — TA's redirect pseudocode contradicts D6/OD-6.**
TA `:749-754` constructs `BaseURLResolver(external_base_url=None)`, which
silently SKIPS the resolver's tier-1 operator-override — but D6/OD-6 mandate
the chain auth-override → live-view `external_base_url` → Host-capture → bind.
As written, an operator setting only `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`
gets a chain that never consults it.
**Fix (DI-clean, no live_views import in auth_service):** at lifespan, copy
`config.live_views.external_base_url` (a plain string) into
`AuthConfig.live_view_external_base_url_fallback: str | None`; AuthService
passes that string as the resolver's tier-1 argument. Same one-shot-copy
pattern as `bind_host`/`bind_port` today. Amend TA `:749-754` and ratify the
field name in D6.

### 1.2 Session store — **ENDORSE**

PG-backed `auth_sessions` + `auth_states` + `auth_bearer_tokens` (3 per-table
`.sql` migrations, D14 convention) survives adversarial pressure-testing under
the real restart reality. Decisive evidence:

- **Single-process uvicorn** (`daemon/__main__.py:345-352`, no `workers=`) —
  there is NO multi-worker cache-coherence argument for PG. The justification
  is restart-survival alone: launcher SIGKILL at 70 s (`launcher.sh:52`,
  `CHILD_STOP_WAIT_S`) makes every promote a full process death; in-memory
  stores log out every user and orphan every in-flight login, routinely.
- **States must be durable, not RAM-with-fallback:** the OAuth redirect
  round-trip spans user-time minutes against a 300 s TTL, and D2's
  exactly-once replay arbitration (`pending → consumed` single-CAS) needs a
  durable arbitration point — an in-memory CAS evaporates on restart,
  collapsing replay protection to zero exactly when restarts are most likely.
- **3-table decomposition is right:** disjoint lifecycles (8 h mutated rows /
  300 s insert-once / long-lived soft-revocable), disjoint sweep cadences (5
  min vs 60 s), disjoint sparse columns. A combined table mixes sweep loops
  and puts unauthenticated login writes on the authenticated-session table.
- **Signed-stateless (JWT cookie) rejected on Risk + Maintainability:** it
  saves ~1 sub-ms indexed PK lookup and costs server-side revocation, logout
  semantics, TTL rigidity after a breach, and the plan's own
  `user_agent`/`ip_subnet` hijack forensics (rows must exist). Four promises
  broken to save 1 ms.
- **Sliding renewal default-OFF endorsed:** immutable `expires_at` keeps audit
  keys stable; the UX cost is near-zero because D12's 401→302 re-login is
  silent when the IdP session lives.

Five-axis note (the one real trade-off here):

| Axis | PG row-lookup | Signed-stateless JWT |
|---|---|---|
| Complexity | Med (repo + sweeper — net-new but patterned) | Low (no tables) |
| Scalability | Parity at ~10² RPS (both fine; NFR-16 headroom) | Parity |
| Maintainability | **High** (revocation, forensics, 4 D-decisions honored) | Low (no revocation path) |
| Risk | **Low** | High (stolen cookie unrevokable to exp) |
| Cost | ~1 ms/request (inside p99 < 5 ms budget) | ~0 |

**Condition on NFR-8 wording:** the SQLite migration runner auto-applies every
non-`MANUAL: TRUE` `.sql` at boot (`daemon/migrations/runner.py:107-113`), so
auth tables WILL be created on SQLite installs — "PG-only" is a **feature
gate, not a schema fact**. Reword NFR-8's acceptance as "boot refuses
(`enabled=true` ∧ SQLite backend)" and decide consciously between inert
tables on SQLite vs `MANUAL: TRUE` markers. As written, a literal reading of
"auth tables rejected" fails its own acceptance.

### 1.3 Machine-client lane — **ENDORSE-WITH-CONDITION**

Static bearer (hash-only `sha256`, printed once, `Authorization: Bearer`) over
client-credentials is right for this deployment shape: remote VMs run their
own ensemble copies — there are no external service consumers to issue OAuth
clients to; per-script static bearers with least-privilege scopes match the
operator cron-loop reality at zero protocol cost. Loopback-exemption rejection
is correct and now evidence-backed: the forge lanes are real
(`jobs_crud.py:595` client-supplied `source`; `messages.py:690` hard-coded
`source="api"`), and a blanket exemption would preserve the known risk while
granting full create access to any process that can reach 127.0.0.1.
`secrets.compare_digest` precedent exists (`webhooks.py:69`). 401-vs-403
discrimination (401 no/invalid token, 403 `SCOPE_INSUFFICIENT` with valid
token) is correct.

**🟡 Condition C1 — undecorated-route default `*` is a least-privilege
hazard.** Default allow-any-authenticated means every future router mounted
without `@require_scope` silently ships weakest policy. Keep the default for
v1 (default-deny would force decorating 28 router families in the first
commission — wrong blast radius), but add a **regression-cage test that fails
when `include_router()` mounts a router without an explicit scope
declaration** (per-family manifest: decorated or explicitly-wildcarded).

**🟡 Condition C2 — bootstrap token exfiltration surface.** Verified:
`daemon/tools/ens_env_tools.py` — the `ens_env_read(keys=[...])` override path
reads ANY env var verbatim from `os.environ` with no deny-list (the narrow
`_DEFAULT_KEYS` only guards the default path). Any agent holding the tool can
read `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN`. Requirements: (a) deny-list
`ENSEMBLE_AUTH_*` on the override path (mirroring the documented
POSTGRES-exclusion posture); (b) one-shot bootstrap semantics — consumed at
first mint, subsequent reuse rejected; (c) runbook step: remove the env var
after first admin mint. Note the parallel surface: an agent with `bash` + DB
access can invoke the mint CLI directly — the deny-list does not close that;
it is the same trust class as every other DB-adjacent agent capability, but
the runbook should say so plainly.

**🔴 Follow-up to FILE (out of this plan's scope, do not silently drop):**
auth-ON does NOT close the source-forgery lanes — a stolen `jobs:write`
bearer still forges `source=api`/client-supplied source. The durable closure
is deriving `source` from authenticated `AuthContext.subject` when auth is ON
(ignore/merge `body.source`). File as a separate ticket; cross-link from this
plan's risk register so the plan's own citation of the forge-lane risk does
not read as "solved by auth."

### 1.4 Failure-mode architecture — **ENDORSE-WITH-CONDITION** (the structural home is sharpened)

The cascade table (IdP-down: login 503, existing sessions validate PG-only;
store-down: protected 503 + `Retry-After: 30`; every scenario: probes public)
is coherent and verified against the real readiness machinery:
`ReadinessComposite.ready` is a pure conjunction of four booleans +
`not forced_degraded` (`readiness.py:224-233`); advisory fields ride `detail`
and never touch the verdict contract (`inflight_turns` precedent at
`:222`); `/readyz` is an O(1) cached read (`api.py:3190-3194`) — the
middleware adds no DB dependency to probes. Offline-tolerant session
validation is correct (validation = PG row check; IdP touched only at
login/callback/JWKS-kid-miss).

**Precision the ACs must carry:** during a PG outage `/readyz` is NOT 200 —
the pre-existing `database` component flips it to 503. The invariant that
holds in every scenario is **publicness (never 401, no auth dependency)**,
not "GREEN = 200". SC-4/AC-S3 wording must not assert 200-during-PG-outage.

**🟡 Condition C3 — where publicness structurally lives (strongest
sharpening of this validation):** the plan's hand-maintained
construction-time frozenset is a SECOND list that can drift from route
registration, and the drift hazard is already scheduled: the reserved
`draining` Phase-4 drain-controller field (`readiness.py:238-239`,
`api.py:3195-3197`) means a `/drainz`-style probe route is planned — a later
commission adding it without editing the frozenset gets 401 at the promote
gate. Fail-closed in both designs (unlisted = protected), so this is a
resilience regression (burned promote ceremony), not a security hole.
**Required amendment:** derive the frozenset from route metadata at middleware
construction — routes carry a `public-probe` tag/attribute (prefix rules
allowed for `/api/auth/*` and the SPA PUBLIC-ASSETS family); construction
walks `app.routes`, builds the same O(1) frozenset, and **logs the derived
set at startup** (reviewable in promote transcripts). Request-time behavior
byte-identical to D11. Single source of truth AND zero-I/O. AC-22's curl
canary stays as runtime backstop and should assert the full known probe
inventory. Adopt into the plan text (phase3 + requirements FR-22 amendment).

**🟡 Condition C4 — NFR-7's exit-78 is unreachable as written.** A lifespan
`RuntimeError` exits via uvicorn's generic path (exit 1/traceback, after port
bind), but NFR-7 requires "code 78 + ERROR + missing-field list". The exact
precedent sits one screen away: `_boot_db_preflight` runs before
`uvicorn.run` and exits 75 (`__main__.py:319-324`). Move static misconfig
validation (`enabled ∧ missing issuer/client_id/client_secret`) to a preflight
beside it, exit 78. Keep the discovery-fetch WARN at lifespan (network I/O
does not belong in config-time validators). Promote interaction confirmed
acceptable: boot-fail → version-verify gate fail → auto-rollback is the
pipeline working as designed, and only bites operators who opted in.

**Sweeper conditions:** follow the readiness-refresher task pattern (create in
lifespan, cancel AND await on shutdown — `api.py:1544-1555`, not the
manager's fire-and-forget variant); per-batch connection acquisition (never
hold one connection across the sweep — 2026-10-10 checkpoint-pool incident
sensitivity) + per-batch statement timeout (~5-10 s). Dual-instance same-DB
(dev checkout on another port): no guard exists today; SKIP LOCKED +
idempotent deletes make it safe — document in the runbook, don't build a
guard.

### 1.5 Extensibility seams — **SUFFICIENT FOR 15 of 18; ONE SEAM MISSING (must add), one optional**

The 18-seam audit against the three named future features
(share tokens / password-gated views / anonymous roots):

- **Share tokens (S-12/S-13/S-2/S-1): INSUFFICIENT as reserved.** A v2
  share-token URL (`GET /views/x/share/{token}`, no cookie) hits
  `AuthMiddleware` FIRST; with `Accept: text/html` the browser gets 302'd to
  login and S-3's router-level grant hook never fires. The §10.2 promise
  ("no refactor of v1 middleware, only ADD branches") breaks here.
- **Password-gated views (S-13): same collision** — form-render requires the
  request to reach a router-level handler, but the 401-contract always 302s
  browser-Accept requests.
- **Anonymous roots (S-3/S-4): router-level hooks are sufficient** — a root
  granted anonymous can be exempted via public-path/metadata once S-19 exists;
  grants table S-4 unaffected by middleware.

**🔴 MUST-ADD S-19 — anonymous-public `/views` subpath set, consulted by
`AuthMiddleware` BEFORE session validation:** operator-configured
`auth.anonymous_view_paths: tuple[str, ...]` (v1: empty default, `pass`
body + regression-cage entry), consult via `startswith` on
`scope["path"]`; v2 auto-populates from `view_shares` rows. This is the seam
that lets S-12/S-13 bolt on WITHOUT middleware refactor. Optional polish
seam S-20 (form-render signal — middleware returns the local form route
instead of 302) can be deferred; first v2 iteration can render from the
router after S-19 permits the path. Add S-19 to [REQ] §10 +
`tests/test_reserved_seams.py`; amend §10.2's promise to name S-19 as the
reserved branch point.

---

## 2. OD Rulings (independent; endorse / override with reason)

| OD | Topic | Planner default | Architect ruling | Basis |
|---|---|---|---|---|
| OD-1 | Docs trio stance | (b) public + kill switch, default true | **ENDORSE** | OpenAPI inventory leak acceptable for an operator-LAN product; `ENSEMBLE_AUTH_DOCS_ENABLED=false` gives sensitive deployments the 404. |
| OD-2 | Bootstrap mint surface | (a) CLI + env bootstrap token | **ENDORSE-WITH-CONDITION** | Secret never crosses HTTP; but C2 applies (ens_env_read deny-list + one-shot consumption + runbook removal). |
| OD-3 | Scope granularity | (c) 7 fine-grained, conservative defaults | **ENDORSE-WITH-CONDITION** | Taxonomy maps cleanly onto the 28-router inventory; undecorated-default-`*` needs the router-mount scope-declaration cage test (C1). |
| OD-4 | CORS policy | (b) boot-refusal guard only; hardening = OOS-5 | **ENDORSE** | Guard verified correctly scoped (`api.py:3015-3021`): kills the wildcard+credentials+cookies combination without touching off-state posture; no new gap created; pre-existing risk owned by OOS-5. |
| OD-5 | Cookie Secure / LAN | (b) Secure ON, explicit opt-in | **ENDORSE** | Fail-closed default with documented escape; matches project LAN reality. |
| OD-6 | Base-URL chain | (c) auth override > live-view var > capture > bind; None ⇒ 503 | **ENDORSE-WITH-AMENDMENT** | Chain shape correct; host-poisoning impact is login-DoS not session-fixation (code_verifier never leaves server; state CAS; conformant IdPs reject unregistered redirect_uris). MUST-FIX D2 wires the live-view tier for real. |
| OD-7 | /vscode WS stance | (b) loopback posture; seam pre-wired | **ENDORSE** | `scope["type"]=="websocket"` early-return verified canonical. |
| OD-8 | Multi-IdP | (b) single issuer + `idp_alias` seam | **ENDORSE** | V1 shape correct. |
| OD-9 | /readyz degraded signal | (b) body sub-field, verdict stays 200 | **ENDORSE** | Structurally proven safe: pure-conjunction `ready`; advisory-in-`detail` precedent (`inflight_turns`). Pin a verdict-freeze test; add the field to the no-composite fail-closed branch for body-shape consistency. |
| OD-10 | Logout shape | (c) POST + DELETE alias | **ENDORSE** | Both lanes served, one code path. |
| OD-11 | Cookie name | `ensemble_sid` | **ENDORSE** | Namespace convention. |
| OD-12 | SPA first-load UX | (a) middleware 302, zero SPA change | **ENDORSE-WITH-CONDITION** | Correct for NFR-18; `return_to` sanitizer MUST normalize `\` → `/` BEFORE the same-origin prefix check (legacy browsers treat `\\`/`//` interchangeably in Location). AC-25 must keep the backslash cases. |
| OD-13 | SPA asset publicness | (a) implicit PUBLIC-ASSETS mirroring carve-outs | **ENDORSE** | Verified no conflict with views/.md path; carve-out prefix set matches SPA router's own. |
| OD-14 | First-admin scopes | (c) CLI `--scopes`, default `auth:admin` only | **ENDORSE** | Least-privilege default with explicit escalation. |
| OD-15 | CSRF timing | (a) double-submit now | **ENDORSE** | ~10 LoC; the pre-existing wildcard-CORS reality is precisely why belt-and-suspenders is justified now, not later. |

---

## 3. Missing-Seam Findings

| Seam | Status | Placement + contract |
|---|---|---|
| **S-19 anonymous-public `/views` subpath set** | 🔴 **MISSING — add to [REQ] §10 + cage test** | `AuthMiddleware.__call__`: `auth.anonymous_view_paths` startswith-consult before session validation; v1 ships empty/pass; v2 auto-derives from `view_shares`. Without it, share-tokens/password-gates force v1 middleware refactor — breaking §10.2. |
| S-20 form-render signal | 🟢 optional, deferrable | Same site as S-19 or `request.state.auth_required_response = "form"`; polish for S-13, not load-bearing. |
| KB-MCP auth seam | 🟢 add alongside D1 fix | `/api/mcp/kb` public in v1 + documented reserved seam for future MCP auth (the plan currently asserts a falsehood instead of reserving a seam). |
| Route-metadata publicness (C3) | 🟢 structural amendment | Not a numbered seam — it changes how S-* public paths are derived; log-derived-set at startup. |

---

## 4. Residual Risk Register (merged, deduped, severity-ordered)

- 🔴 **R1 Mount claim false → KB-MCP 401s on auth-ON** (D1) — plan must amend; promote-blocker for the plan text itself.
- 🔴 **R2 redirect chain wiring bug** (D2) — operator sets live-view var only, chain silently skips it; plan must amend pseudocode + D6 field.
- 🔴 **R3 source-forgery lanes persist post-auth** (`jobs_crud.py:595`, `messages.py:690`) — out of scope, MUST be filed as follow-up ticket + cross-linked; not solved by this plan.
- 🟡 R4 NFR-7 exit-78 unreachable at lifespan site (C4 — preflight beside exit-75 precedent).
- 🟡 R5 hand-listed frozenset drift vs scheduled `/drainz` (C3 — derive from route metadata).
- 🟡 R6 undecorated-route default-`*` least-privilege hazard (C1 — cage test).
- 🟡 R7 `ens_env_read` bootstrap-token exfiltration path (C2 — deny-list + one-shot).
- 🟡 R8 NFR-8 wording fails its own acceptance on SQLite (feature gate not schema fact — reword).
- 🟡 R9 share-token v2 blocked without S-19 (add seam now).
- 🟡 R10 `return_to` backslash normalization before prefix check (OD-12 condition).
- 🟢 R11 off-state wildcard+credentials persists (pre-existing, OOS-5 owns it).
- 🟢 R12 sweeper pool-hold under PG degradation (per-batch acquisition + timeout).
- 🟢 R13 dual-sweeper same-DB dev checkout (safe by SKIP LOCKED; document).
- 🟢 R14 HostRecorder cold-start first-login (503 + override + pre-warm — already handled).
- 🟢 R15 `/readyz` body-shape in no-composite branch once auth field exists (one-line + test).

---

## 5. INTEGRITY Verdict

**SOUND TO HAND TO IMPLEMENTATION — CONDITIONAL PASS.**

The plan's architecture survives adversarial validation against the real
stack: flow choice (Auth Code + PKCE + PG sessions), LIFO auth-outermost
placement with conditional registration, 3-table PG store, static-bearer
machine lane with no loopback exemption, zero-I/O public fast-path — all
endorsed on verified evidence. The conditions are plan-text amendments, not
redesigns:

**Must land in the plan text BEFORE the implementation commission starts:**
1. D1 — Mount fix: `/api/mcp/kb`(+`/sse`) into `AUTH_DEFAULT_PUBLIC_PATHS`; TA rule 6 amended; seam reserved.
2. D2 — redirect chain wiring: `live_view_external_base_url_fallback` lifespan copy; TA `:749-754` amended; D6 ratified.
3. S-19 — anonymous-public `/views` subpath seam added to [REQ] §10 + cage test; §10.2 amended.
4. C4 — static misconfig validation moved to boot preflight, exit 78 (NFR-7 verbatim).
5. C3 — publicness derived from route metadata (tagged routes → frozenset → startup log); FR-22/phase3 amended.
6. C1+C2 — router-mount scope-declaration cage test; `ens_env_read` deny-list `ENSEMBLE_AUTH_*` + one-shot bootstrap.
7. R8 — NFR-8 reworded to boot-refusal semantics.
8. R3 — source-forgery follow-up ticket filed + cross-linked (explicitly NOT closed by auth).

**Confidence: High.** The flipping assumption would be a Starlette upgrade
that changes Mount/middleware composition semantics before implementation
lands — pin the framework versions in the plan and add a one-line test
asserting Mounts traverse the auth middleware (it doubles as the D1
regression guard).

---

## 6. Gaps

- Worker A ran **without its dispatched skill** (`structural-design` absent
  from this project's skill bank — explicit bank-miss, not silent). Its
  analysis was independently verified by the architect on the critical
  framework-semantics claim (Starlette source inspection) and re-anchored
  citations. Recommend the skill bank gain a `structural-design` skill or the
  architect's dispatch table remap middleware/pattern questions to an
  existing skill (`system-decomposition` is the nearest neighbor).
- Worker B omitted the skill-confirmation first line (content verified
  skill-grade by spot-check); protocol note only.
