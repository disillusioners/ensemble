"""G2.7 item 3 — the four-cell degradation contract + strike accounting."""
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
    _InMemoryPasscodeStore, _InMemoryStorePort, _TEST_FERNET_KEY, _mint)

VIEW = "/views/public/planning/ens/feat/design/mockups/landing.html"
REL = "ens/feat/design/mockups/landing.html"


def build(external_base=None):
    tmp = pathlib.Path(tempfile.mkdtemp())
    d = tmp/"project"/".agents/shared/planning/feat/design/mockups"
    d.mkdir(parents=True); (d/"landing.html").write_text("<h1>Landing</h1>")
    cipher = ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)
    svc = ViewPasscodeService.__new__(ViewPasscodeService)
    svc._engine = None; svc._cipher = cipher
    store = _InMemoryPasscodeStore(); svc._store = _InMemoryStorePort(store, cipher)
    cfg = LiveViewsConfig(); cfg.external_base_url = external_base
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
    app.state.auth_service = None
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store


print("(i) audience-vouched same-origin grants (live shape) — declared=None, audience set")
c, store = build()
# simulate the live app's auth_config by mounting it post-hoc
from tests.helpers.auth_fixture import on_auth_config
ac = on_auth_config()
ac.live_view_external_base_url_fallback = None
c.app.state.auth_config = ac
plaintext_row = store.get_row("planning", REL)
print("   audience hosts:", end=" ")
from daemon.auth.cookie import parse_operator_audience
print(sorted(parse_operator_audience(ac)[0]))
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "testserver", "Origin": "http://testserver", "X-Forwarded-Proto": "https"})
print("   same-origin (Host=testserver, Origin=http://testserver) ->", r.status_code,
      "| strikes:", store.get_row("planning", REL).failed_attempts)
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "evil.example", "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
print("   self-match (Host=evil, Origin=evil, NOT vouched)        ->", r.status_code)

print()
print("(ii) declared-base grants auth-OFF (P1/P2)")
c2, store2 = build(external_base="https://views.example.com")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "Origin": "https://views.example.com",
    "X-Forwarded-Proto": "https"})
print("   declared-base origin, Host=internal ->", r.status_code)
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "views.example.com", "Origin": "https://views.example.com",
    "X-Forwarded-Proto": "https"})
print("   declared-base self-Host (vouched)   ->", r.status_code)

print()
print("(iii) loopback carve")
c3, store3 = build()
# TestClient peer defaults to ("testclient", 50000) — non-loopback.
# Force a loopback peer via a custom ASGI transport shim:
import starlette.testclient as stc
from fastapi.testclient import TestClient as TC
class LoopbackTC(TC):
    def request(self, method, url, **kw):
        # TestClient builds scope with client=("testclient",50000); override via portal is complex.
        # Instead: call the gate directly with peer loopback.
        return super().request(method, url, **kw)
from types import SimpleNamespace
from starlette.requests import Request
import daemon.routers.live_views as nv
def gate(headers, app, peer):
    sc = {"type":"http",
          "headers":[(k.lower().encode(), v.encode()) for k,v in headers.items()],
          "app": app, "client": peer}
    return nv._same_host_origin_check(Request(sc))
app3 = c3.app
print("   peer=127.0.0.1 + real same-origin loopback (Host=localhost Origin=http://localhost) ->",
      "ALLOW" if gate({"Host":"localhost","Origin":"http://localhost","X-Forwarded-Proto":"https"}, app3, ("127.0.0.1",5000)) else "REJECT")
print("   peer=127.0.0.1 + lookalike (localhost.evil.example)                                 ->",
      "ALLOW" if gate({"Host":"localhost.evil.example","Origin":"https://localhost.evil.example"}, app3, ("127.0.0.1",5000)) else "REJECT")
print("   peer=203.0.113.7 + exact loopback name (Host=localhost)                             ->",
      "ALLOW" if gate({"Host":"localhost","Origin":"https://localhost"}, app3, ("203.0.113.7",5000)) else "REJECT")

print()
print("(iv) unconfigured non-loopback degradation")
c4, store4 = build()
print("   real-Origin same-origin (Host=testserver Origin=http://testserver) ->", end=" ")
r = c4.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "testserver", "Origin": "http://testserver", "X-Forwarded-Proto": "https"})
print(r.status_code, "| strikes:", store4.get_row("planning", REL).failed_attempts)
r = c4.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "testserver", "Origin": "null", "X-Forwarded-Proto": "https"})
print("   null-Origin flow                                                   ->", r.status_code,
      "| strikes:", store4.get_row("planning", REL).failed_attempts)
r = c4.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "testserver", "X-Forwarded-Proto": "https"})
print("   absent-Origin flow                                                 ->", r.status_code,
      "| strikes:", store4.get_row("planning", REL).failed_attempts)
