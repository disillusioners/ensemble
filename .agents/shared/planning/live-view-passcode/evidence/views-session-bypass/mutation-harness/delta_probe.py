"""Delta probe for fix-iteration 1 (8a024b5b7) — READ-ONLY, /tmp.

Re-tests the two MAJOR findings against the NEW code, on the REAL router
with the REAL daemon CORS posture (daemon/api.py:5404-5416).

Run from the worktree:
  cd /home/nea/ensemble-src-wt-views-session-bypass && \
  env -u POSTGRES_* (per-variable) -u ENSEMBLE_AUTH_* .venv/bin/python \
      /tmp/views-review/delta_probe.py
"""
from pathlib import Path
import pathlib, tempfile
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.requests import Request

import daemon.routers.live_views as nv
from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher, ViewPasscodeService, VerifyRateLimiter)
from tests.helpers.auth_fixture import on_auth_config
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _StubAuthService,
    _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(cors_origins, declare_front=None):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp / "project" / ".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True)
    (d / "landing.html").write_text("<h1>Landing</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None
    svc._cipher = cipher
    store = _InMemoryPasscodeStore()
    svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig()
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html", "mmd", "mermaid", "md"])
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda s: str(tmp / "project"))
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000,
                                                     refill_per_minute=1000)
    app.state.auth_service = _StubAuthService(set())
    if declare_front is not None:
        ac = on_auth_config()          # the lifespan-writer shape
        ac.live_view_external_base_url_fallback = declare_front or None
        app.state.auth_config = ac
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


def gate(headers, auth_config=None):
    sc = {"type": "http",
          "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
          "app": SimpleNamespace(state=SimpleNamespace(auth_config=auth_config))}
    return nv._same_host_origin_check(Request(sc))


print("=" * 74)
print("MAJOR-1 — self-anchoring, auth-OFF CORS-reflection exploit (the ORIGINAL finding)")
print("=" * 74)
c, store = build(["*"])                      # auth OFF: wildcard + credentials
pre = c.options(VIEW, headers={"Origin": "https://evil.example",
                              "Access-Control-Request-Method": "POST",
                              "Access-Control-Request-Headers": "content-type,x-forwarded-host"})
print("preflight:", pre.status_code, "| allow-origin:",
      pre.headers.get("access-control-allow-origin"),
      "| allow-headers:", pre.headers.get("access-control-allow-headers"))
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
    "X-Forwarded-Proto": "https", "Content-Type": "application/x-www-form-urlencoded"})
print("  CSRF + self XFH        ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts,
      "  (iteration-0 was 200 / strikes=1)")
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Origin": "https://evil.example", "Forwarded": "for=1.2.3.4;host=evil.example",
    "X-Forwarded-Proto": "https"})
print("  CSRF + self Forwarded  ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts,
      "  (iteration-0 was 200 / strikes=2)")
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
print("  CSRF control (no XFH)  ->", r.status_code, "(unchanged: 403)")

print()
print("MAJOR-1(b) — declared proxy front: legit forwarded topology preserved")
c2, _ = build(["*"], declare_front="https://views.example.com")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "views.example.com, internal:8079",
    "Origin": "https://views.example.com", "X-Forwarded-Proto": "https"})
print("  declared front, Host=internal, XFH=views ->", r.status_code, "(expect 200 wrong-code page)")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Forwarded": 'for=10.0.0.9;host="views.example.com"',
    "Origin": "https://views.example.com", "X-Forwarded-Proto": "https"})
print("  declared front, Forwarded host=           ->", r.status_code, "(expect 200)")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "evil.example",
    "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
print("  declared front, XFH=evil (NOT in audience) ->", r.status_code,
      "(expect 200 — XFH is trusted once declared; by design)")

print()
print("MAJOR-1(d) — Host leg (a) + audience leg (d) intact in BOTH states")
def hosts(headers, ac=None):
    sc = {"type": "http",
          "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
          "app": SimpleNamespace(state=SimpleNamespace(auth_config=ac))}
    return sorted({h for h, _p in nv._views_trusted_hosts(Request(sc))})
ac_unset = on_auth_config()
ac_set = on_auth_config()
ac_set.live_view_external_base_url_fallback = "https://views.example.com"
H = {"Host": "internal-svc:8079", "X-Forwarded-Host": "xfh.example",
     "Forwarded": 'for=1.1.1.1;host="fwd.example"'}
print("  undeclared:", hosts(H, ac_unset))
print("  declared  :", hosts(H, ac_set))
print("  no auth_config:", hosts(H, None))

print()
print("=" * 74)
print("MAJOR-2 — Sec-Fetch-Site discriminator on Origin: null")
print("=" * 74)
cases = [
    ("null + absent SFS  (LIVE FIX PRESERVATION PIN)", {"Host": "www.ensem.dev", "Origin": "null"}, True),
    ("null + Sec-Fetch-Site: none",       {"Origin": "null", "Sec-Fetch-Site": "none"}, True),
    ("null + same-origin",                {"Origin": "null", "Sec-Fetch-Site": "same-origin"}, True),
    ("null + same-site",                  {"Origin": "null", "Sec-Fetch-Site": "same-site"}, True),
    ("null + CROSS-SITE  (sandboxed iframe)", {"Origin": "null", "Sec-Fetch-Site": "cross-site"}, False),
    ("null + CROSS-SITE uppercase",       {"Origin": "null", "Sec-Fetch-Site": "CROSS-SITE"}, False),
    ("null + ' Cross-Site ' (padded/mixed)", {"Origin": "null", "Sec-Fetch-Site": " Cross-Site "}, False),
    ("NULL (origin case) + cross-site",   {"Origin": "NULL", "Sec-Fetch-Site": "cross-site"}, False),
    ("null + sfs='cross-siteX' (no exact match)", {"Origin": "null", "Sec-Fetch-Site": "cross-siteX"}, True),
]
for label, h, expect in cases:
    got = gate(h)
    print(("OK  " if got == expect else "FAIL"), f"{label:48s} -> {'ALLOW' if got else 'REJECT'}")

print()
print("  Full-stack: null + cross-site on the real router")
c3, store3 = build(["*"])
r = c3.post(VIEW, data={"code": "wrong0"}, headers={
    "Origin": "null", "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "navigate", "X-Forwarded-Proto": "https",
    "Accept": "text/html,application/xhtml+xml"})
print("   ->", r.status_code, "| content-type:", r.headers.get("content-type"),
      "| strikes:", store3.get_row("planning", REL).failed_attempts)
codes = [c3.post(VIEW, data={"code": "wrong0"}, headers={
    "Origin": "null", "Sec-Fetch-Site": "cross-site",
    "X-Forwarded-Proto": "https"}).status_code for _ in range(10)]
print("   10x sandboxed-iframe burn ->", codes,
      "| locked:", store3.get_row("planning", REL).locked)

print()
print("=" * 74)
print("FIX-3 — port semantics, both parsers")
print("=" * 74)
for label, a, b in [
    ("Host 'example.com:notaport' vs Origin :9999", "example.com:notaport", "https://example.com:9999"),
    ("Host 'example.com:99999'   vs Origin portless", "example.com:99999", "https://example.com"),
    ("Host portless vs Origin ':notaport'", "example.com", "https://example.com:notaport"),
    ("Host portless vs Origin ':99999'", "example.com", "https://example.com:99999"),
    ("legit elision: Host portless vs Origin :8443", "example.com", "https://example.com:8443"),
    ("legit elision: Host :443 vs Origin portless", "example.com:443", "https://example.com"),
    ("legit explicit match :8443/:8443", "example.com:8443", "https://example.com:8443"),
    ("legit explicit mismatch :8443/:9999", "example.com:8443", "https://example.com:9999"),
    ("legit real-world: Host www.ensem.dev vs Origin apex :443", "www.ensem.dev", "https://ensem.dev:443"),
]:
    print(f"  {'ALLOW ' if gate({'Host': a, 'Origin': b}) else 'REJECT'} | {label}")

print()
print("=" * 74)
print("REGRESSION GUARDS — the things that must NOT have changed")
print("=" * 74)
print("  Origin:null absent-SFS full-stack ->",
      build(["*"])[0].post(VIEW, data={"code": "wrong0"},
                           headers={"Origin": "null", "X-Forwarded-Proto": "https"}).status_code,
      "(expect 200 wrong-code page = gate passed)")
print("  www/apex reconciliation ->",
      build(["*"])[0].post(VIEW, data={"code": "wrong0"},
                           headers={"Host": "www.example.com", "Origin": "https://example.com",
                                    "X-Forwarded-Proto": "https"}).status_code, "(expect 200)")
