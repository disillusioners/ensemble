"""Is the DECLARED-front state still self-anchorable?

Scenario: operator declared an external base (legs b/c live) AND the
daemon is reachable with the auth-OFF CORS posture (wildcard+credentials,
which reflects any origin and safelists x-forwarded-host). Can a
cross-site fetch still pick its own anchor?
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


def build(declare):
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
    ac = on_auth_config()
    ac.live_view_external_base_url_fallback = declare
    app.state.auth_config = ac
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


for declare, label in ((None, "UNDECLARED"), ("https://www.ensem.dev", "DECLARED (live shape)")):
    c, store = build(declare)
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Host": "internal-svc:8079",
        "Origin": "https://evil.example",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "https",
        "Content-Type": "application/x-www-form-urlencoded"})
    print(f"{label:24s} auth-OFF CORS + self XFH -> {r.status_code} "
          f"| strikes: {store.get_row('planning', REL).failed_attempts}")
    # and: does an origin OUTSIDE the audience pass by naming itself in XFH?
    r2 = c.post(VIEW, data={"code": "correcthorse"}, headers={
        "Host": "internal-svc:8079",
        "Origin": "https://attacker.tld",
        "X-Forwarded-Host": "attacker.tld",
        "X-Forwarded-Proto": "https"})
    print(f"{'':24s}   (origin outside audience, self-named) -> {r2.status_code}")
