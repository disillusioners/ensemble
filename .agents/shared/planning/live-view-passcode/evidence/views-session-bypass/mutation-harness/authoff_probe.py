"""auth-OFF deployment behind a proxy front: can a legit forwarded form POST pass?"""
from pathlib import Path
import pathlib, tempfile
from fastapi import FastAPI
from fastapi.testclient import TestClient
from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher, ViewPasscodeService, VerifyRateLimiter)
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _StubAuthService,
    _TEST_FERNET_KEY, _mint)

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
    cfg = LiveViewsConfig()
    cfg.external_base_url = external_base          # the live_views knob
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"], allowed_extensions=["html"])
    app = FastAPI(); app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg, project_workdir_by_shortname_resolver=lambda s: str(tmp/"project"))
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.view_rate = None
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000, refill_per_minute=1000)
    app.state.auth_service = _StubAuthService(set())
    # AUTH OFF -> api.py sets NO app.state.auth_config at all
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


for base in (None, "https://look.example.com"):
    c, store = build(base)
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Host": "internal-svc:8079",
        "Origin": "https://look.example.com",
        "X-Forwarded-Host": "look.example.com",
        "X-Forwarded-Proto": "https",
        "Content-Type": "application/x-www-form-urlencoded"})
    print(f"auth OFF, live_views.external_base_url={'set' if base else 'unset':5s} "
          f"-> legit forwarded POST {r.status_code} "
          f"| strikes={store.get_row('planning', REL).failed_attempts}")
