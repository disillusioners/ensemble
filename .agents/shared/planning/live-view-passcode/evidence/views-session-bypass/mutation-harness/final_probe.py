"""FINAL pass probe (a29cebbea) — READ-ONLY, /tmp.

Item 2: can ANY client header / request-derived value flip
`proxy_front_declared` to True? And: is the live shape unchanged?
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
from daemon.services.live_views import LiveViewsService, HostRecorder
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher, ViewPasscodeService, VerifyRateLimiter)
from tests.helpers.auth_fixture import on_auth_config
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _StubAuthService,
    _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(auth_on, live_views_base, record_host=None):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp / "project" / ".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True)
    (d / "landing.html").write_text("<h1>L</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None
    svc._cipher = cipher
    store = _InMemoryPasscodeStore()
    svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig()
    cfg.external_base_url = live_views_base
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"], allowed_extensions=["html"])
    rec = HostRecorder()
    if record_host:
        rec.record(record_host, "https")
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda s: str(tmp / "project"),
        bind_host="0.0.0.0", bind_port=9797, host_recorder=rec)
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000,
                                                      refill_per_minute=1000)
    if auth_on:
        app.state.auth_service = _StubAuthService(set())
        ac = on_auth_config()
        ac.live_view_external_base_url_fallback = None
        app.state.auth_config = ac
    else:
        app.state.auth_service = None          # TRUE auth-OFF
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store, app


def trusted(app, headers):
    sc = {"type": "http",
          "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
          "app": app}
    return sorted({h for h, _p in nv._views_trusted_hosts(Request(sc))})


print("=" * 78)
print("A. NO client header can flip the declaration (both sources unset)")
print("=" * 78)
c, store, app = build(auth_on=False, live_views_base=None,
                      record_host="hostcapture.example")
print("   (host_recorder pre-seeded with hostcapture.example => public_base()")
print("    would resolve to it; the DECLARATION must ignore that)")
svc = app.state.live_views_service
print("   public_base()              =", repr(svc.public_base()))
print("   declared_external_base_url =", repr(svc.declared_external_base_url))

aggressive = {
    "Host": "internal-svc:8079",
    "X-Forwarded-Host": "evil.example",
    "Forwarded": 'for=1.2.3.4;host="evil.example"',
    "Origin": "https://evil.example",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-For": "1.2.3.4",
    "X-Forwarded-Server": "evil.example",
    "X-Real-IP": "1.2.3.4",
    "X-Original-Host": "evil.example",
    "X-Forwarded-Port": "443",
    "Forwarded-Host": "evil.example",
    "Via": "1.1 evil.example",
}
print("   trust set under the full aggressive header set:",
      trusted(app, aggressive))
print("   => legs (b)/(c) inert:", "evil.example" not in trusted(app, aggressive))

r = c.post(VIEW, data={"code": "wrong0"}, headers=aggressive)
print("   auth-OFF undeclared, self-anchored POST ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts)
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://evil.example",
    "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
print("   auth-OFF undeclared, XFH only            ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts)
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://evil.example",
    "Forwarded": 'for=1.2.3.4;host="evil.example"', "X-Forwarded-Proto": "https"})
print("   auth-OFF undeclared, Forwarded only      ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts)

print()
print("=" * 78)
print("B. auth-ON undeclared (LIVE SHAPE: external_base_url unset)")
print("=" * 78)
c2, store2, app2 = build(auth_on=True, live_views_base=None)
print("   declared_external_base_url =", repr(app2.state.live_views_service.declared_external_base_url))
print("   auth fallback             =", repr(getattr(app2.state.auth_config,
                                                    "live_view_external_base_url_fallback", None)))
print("   => declared =", bool(app2.state.live_views_service.declared_external_base_url))
print("   trust set (aggressive headers):", trusted(app2, aggressive))
r = c2.post(VIEW, data={"code": "wrong0"}, headers=aggressive)
print("   auth-ON undeclared, self-anchored POST   ->", r.status_code,
      "| strikes:", store2.get_row("planning", REL).failed_attempts)

print()
print("=" * 78)
print("C. DECLARED via the SECOND source only (FIX-7 branch 2)")
print("=" * 78)
c3, store3, app3 = build(auth_on=False, live_views_base="https://views.example.com")
print("   declared_external_base_url =", repr(app3.state.live_views_service.declared_external_base_url),
      "| auth_config =", getattr(app3.state, "auth_config", None))
r = c3.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://views.example.com",
    "X-Forwarded-Host": "views.example.com", "X-Forwarded-Proto": "https"})
print("   auth-OFF declared, legit forwarded POST  ->", r.status_code, "(NEW-ISSUE-1 closed)")
r = c3.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://evil.example",
    "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
print("   auth-OFF declared, attacker self-anchor  ->", r.status_code,
      "| (declared front means XFH IS trusted; audience-independent)")
