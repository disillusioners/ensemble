# Lesson: views-session-bypass gate lane (2026-11-09) — G2.7 host-capture tautology + two harness traps

## 1. The G2.7 defect pattern: an origin gate that trusts the client Host header unconditionally

`_views_trusted_hosts` leg (a) treats the raw client `Host` header as a trust anchor, so any
self-consistent `Host`+`Origin` pair (DNS-rebinding shape) satisfies the same-origin check and
**issues a view_grant** (observed: 303 + full-attribute Set-Cookie for `Host: evil.example` /
`Origin: https://evil.example`). The no-anchor tautology guard only fires when NO leg yields an
anchor — it does not protect the host-captured leg itself.

How to not repeat: **when porting an origin/comparator from one lane to another, port the TRUST
GATING too.** `daemon/auth/cookie.py:249 host_is_trusted` gates the auth lane's same-origin leg;
the views lane copied the shape without the gate. A test that PINS the permissive behavior
(`test_portless_host_vs_explicit_origin_port_elides`) is a smell: pinned ≠ correct — check the
stated contract (POK `views-origin-gate-contract`: "never host-captured") before pinning.

Detection probe (10s, reusable): `curl -X POST <verify> -H "Host: evil.example" -H "Origin:
https://evil.example" -H "X-Forwarded-Proto: https" -d "code=<correct>"` → any 2xx/3xx + Set-Cookie
is the bug. Harness: `assets/2026-11-09-views-session-bypass/g2g8/G2.txt` case 7.

## 2. Ambient `ENSEMBLE_AUTH_*` poisoning (packs AND boots)

Operator/daemon-child shells carry LIVE `ENSEMBLE_AUTH_ISSUER`, `ENSEMBLE_AUTH_DISCOVERY_URL`
(door.ensem.dev), `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`. Pydantic BaseSettings auto-binds them:
- in pytest: `tests/helpers/auth_fixture.py` builds `AuthConfig(issuer="https://idp.test")` →
  ambient discovery_url origin mismatches → ValidationError → **25 red nodes** in the auth-lane
  surface that are 100% environmental (196/196 green once scrubbed).
- in fenced auth-ON boots: boot refuses **exit 78** on issuer/discovery mismatch;
  `EXTERNAL_REDIRECT_BASE` additionally FAKES a declared proxy front, silently changing
  origin-gate semantics (would have false-passed G2.8).

Rule: scrub ALL THREE per-variable on every auth-lane pack run and every fenced auth-ON boot
(POK `auth-lane-scrub-ensemble-auth-env`). `env -u ENSEMBLE_AUTH_*` is a silent NO-OP like
`env -u POSTGRES_*`.

## 3. Concurrent-edit adjudication pattern (reusable)

A foreign actor left uncommitted edits on 2 lane files mid-gate. Do not touch/revert/commit them.
Adjudicate impact via **normalized-AST identity** (strip annotations + TYPE_CHECKING + docstrings;
compare) plus targeted grep on the symbols under test. AST-identical ⇒ verdicts stand for HEAD +
overlays. All tester commits go path-limited (`git add -- <explicit paths>`), never `-A`.

## 4. Minor

- Playwright JS-level `request.headers` is NOT wire evidence (drops sec-fetch-*, hides Set-Cookie
  on 3xx). Use a CDP session (`requestWillBeSent*`/`responseReceived*`) for closure-grade browser
  proofs. Working recipe: `assets/2026-11-09-views-session-bypass/g1/scripts/g1_browser_cdp.py`
  (/tmp/pwenv playwright 1.64.1 + executable_path to chromium-1223).
