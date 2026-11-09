"""FIX-7 trust-inversion probe.

The declaration is consumed as a BOOLEAN flag (router :1055 / :1083) — the
declared base's HOST is never added as a server-side anchor. Consequence in
the newly-enabled auth-OFF posture:

  (i)  a legit POST with NO forwarded header (proxy rewrites Host only, or
       the browser path where XFH is absent) has NO anchor -> 403
  (ii) an ATTACKER-named X-Forwarded-Host IS trusted -> 200

i.e. the operator's declaration licenses CLIENT headers instead of supplying
a server-side anchor.
"""
from pathlib import Path
import pathlib, tempfile
from types import SimpleNamespace
from fastapi import FastAPI
from starlette.requests import Request
from fastapi.testclient import TestClient
from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher, ViewPasscodeService, VerifyRateLimiter)
import daemon.routers.live_views as nv
from tests.unit.routers.test_live_views_session_bypass import (
    _InMemoryPasscodeStore, _InMemoryStorePort, _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(base):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp/"project"/".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True); (d/"landing.html").write_text("<h1>L</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None; svc._cipher = cipher
    store = _InMemoryPasscodeStore(); svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig(); cfg.external_base_url = base
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design","mockups"], allowed_extensions=["html"])
    app = FastAPI(); app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg, project_workdir_by_shortname_resolver=lambda s: str(tmp/"project"))
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000, refill_per_minute=1000)
    app.state.auth_service = None                   # auth OFF
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store, app


c, store, app = build("https://views.example.com")
print("DECLARED front = https://views.example.com   (auth OFF, path NOT auth-ON-bounded)")
print("trusted-host set for a bare proxied request:",
      sorted({h for h,_ in nv._views_trusted_hosts(Request({
          "type":"http","headers":[(b"host",b"internal-svc:8079")],"app":app}))}))
print("  => declared host present as an anchor?", "views.example.com" in {h for h,_ in nv._views_trusted_hosts(Request({
          "type":"http","headers":[(b"host",b"internal-svc:8079")],"app":app}))})

print()
print("(i)  legit origin, NO forwarded header (proxy rewrites Host only):")
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://views.example.com",
    "X-Forwarded-Proto": "https"})
print("     ->", r.status_code, "  <-- operator-declared origin REJECTED (no anchor)")

print("(ii) attacker origin + self-named XFH:")
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://evil.example",
    "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
print("     ->", r.status_code, "| strikes:", store.get_row("planning", REL).failed_attempts,
      "  <-- attacker-named anchor ACCEPTED")
