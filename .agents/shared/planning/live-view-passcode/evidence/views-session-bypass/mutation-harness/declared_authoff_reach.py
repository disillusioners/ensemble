"""FIX-7 branch 2 reachability: auth-OFF + declared front + wildcard CORS.

Before FIX-7, auth-OFF could never declare, so the wildcard-CORS posture
was safe as a side effect. FIX-7 gives auth-OFF a declaration path — does
that reintroduce browser-reachable self-anchoring in that posture?
"""
from pathlib import Path
import pathlib, tempfile
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher, ViewPasscodeService, VerifyRateLimiter)
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(external_base):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp/"project"/".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True); (d/"landing.html").write_text("<h1>L</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None; svc._cipher = cipher
    store = _InMemoryPasscodeStore(); svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig(); cfg.external_base_url = external_base
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"], allowed_extensions=["html"])
    app = FastAPI(); app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg, project_workdir_by_shortname_resolver=lambda s: str(tmp/"project"))
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000, refill_per_minute=1000)
    app.state.auth_service = None                       # auth OFF
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])   # api.py auth-OFF posture
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


for base in (None, "https://views.example.com"):
    c, store = build(base)
    pre = c.options(VIEW, headers={"Origin": "https://evil.example",
                                   "Access-Control-Request-Method": "POST",
                                   "Access-Control-Request-Headers": "content-type,x-forwarded-host"})
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Host": "internal-svc:8079", "Origin": "https://evil.example",
        "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https",
        "Content-Type": "application/x-www-form-urlencoded"})
    print(f"auth OFF, external_base_url={'SET  ' if base else 'unset'}"
          f" | preflight={pre.status_code} allow-origin={pre.headers.get('access-control-allow-origin')!r}"
          f" | self-anchor POST -> {r.status_code} strikes={store.get_row('planning', REL).failed_attempts}")
