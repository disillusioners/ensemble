"""Residual reachability: auth ON + allowed_origins containing '*' (accepted
by AuthConfig validation — no star guard) + external base declared.

That combination yields CORS allow_origins=['*'] + credentials AND
proxy_front_declared=True -> the self-anchor is browser-reachable.
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
from tests.helpers.auth_fixture import on_auth_config
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _StubAuthService,
    _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(allowed_origins, declare):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp/"project"/".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True); (d/"landing.html").write_text("<h1>L</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None; svc._cipher = cipher
    store = _InMemoryPasscodeStore(); svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig()
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
    app.state.auth_service = _StubAuthService(set())
    ac = on_auth_config(allowed_origins=allowed_origins)
    ac.live_view_external_base_url_fallback = declare
    app.state.auth_config = ac
    # api.py:5407-5416 posture for auth ON: CORS = allowed_origins verbatim
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins),
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


for origins, declare, label in (
    (["*"], "https://www.ensem.dev", "auth ON, origins=['*'], front DECLARED"),
    (["*"], None,                     "auth ON, origins=['*'], front UNDECLARED"),
    (["https://www.ensem.dev"], "https://www.ensem.dev", "auth ON, origins=[www], front DECLARED"),
):
    c, store = build(origins, declare)
    pre = c.options(VIEW, headers={"Origin": "https://evil.example",
                                   "Access-Control-Request-Method": "POST",
                                   "Access-Control-Request-Headers": "content-type,x-forwarded-host"})
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Host": "internal-svc:8079", "Origin": "https://evil.example",
        "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https",
        "Content-Type": "application/x-www-form-urlencoded"})
    print(f"{label}\n   preflight={pre.status_code} "
          f"allow-origin={pre.headers.get('access-control-allow-origin')!r} "
          f"| self-anchor POST -> {r.status_code} "
          f"| strikes={store.get_row('planning', REL).failed_attempts}")
