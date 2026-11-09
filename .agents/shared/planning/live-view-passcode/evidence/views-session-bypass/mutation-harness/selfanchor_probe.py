"""Review probe (READ-ONLY, /tmp): can a CLIENT-supplied header become its
own trust anchor in daemon/routers/live_views.py::_views_trusted_hosts?

Builds the real router + the real daemon CORS posture (daemon/api.py:5404-5416)
and asks: does a cross-site JSON/fetch POST self-anchoring via
X-Forwarded-Host (or RFC7239 Forwarded: host=) pass the views origin gate?

Run from the worktree:
  cd /home/nea/ensemble-src-wt-views-session-bypass && \
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
      -u POSTGRES_PORT -u ENSEMBLE_AUTH_ALLOWED_ORIGINS \
      -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE -u ENSEMBLE_AUTH_ENABLED \
      .venv/bin/python /tmp/views-review/selfanchor_probe.py
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
    _InMemoryPasscodeStore, _InMemoryStorePort, _StubAuthService,
    _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(cors_origins):
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
    app.state.auth_service = _StubAuthService(set())      # no valid sessions
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


def main():
    print("== A. auth-OFF daemon posture (CORS wildcard + credentials) ==")
    c, store = build(["*"])
    pre = c.options(VIEW, headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-forwarded-host"})
    print("   preflight:", pre.status_code,
          "| allow-origin:", pre.headers.get("access-control-allow-origin"),
          "| allow-headers:", pre.headers.get("access-control-allow-headers"))
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "https",
        "Content-Type": "application/x-www-form-urlencoded"})
    print("   CSRF POST + self XFH      ->", r.status_code,
          "| strikes:", store.get_row("planning", REL).failed_attempts)
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Origin": "https://evil.example", "Forwarded": "for=1.2.3.4;host=evil.example",
        "X-Forwarded-Proto": "https"})
    print("   CSRF POST + self Forwarded->", r.status_code,
          "| strikes:", store.get_row("planning", REL).failed_attempts)
    r = c.post(VIEW, data={"code": "wrong0"}, headers={
        "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
    print("   CSRF POST (control, no XFH)->", r.status_code)

    print("== B. auth-ON daemon posture (CORS = allowed_origins) ==")
    c2, _ = build(["https://www.ensem.dev"])
    pre = c2.options(VIEW, headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-forwarded-host"})
    print("   preflight:", pre.status_code,
          "(blocked => the browser never sends the real POST)")

    print("== C. lockout burn (10 self-anchored CSRF POSTs) ==")
    c3, store3 = build(["*"])
    codes = []
    for _ in range(10):
        codes.append(c3.post(VIEW, data={"code": "wrong0"}, headers={
            "Origin": "https://attacker.example",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "https",
            "Content-Type": "application/x-www-form-urlencoded"}).status_code)
    row = store3.get_row("planning", REL)
    print("   statuses:", codes)
    print("   failed_attempts:", row.failed_attempts, "| locked:", row.locked)
    g = c3.get(VIEW, headers={"Accept": "text/html"})
    print("   subsequent anonymous GET ->", g.status_code,
          "| locked page:", "locked" in g.text.lower())


if __name__ == "__main__":
    main()
