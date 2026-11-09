"""G2.7 probe at 6398a29a3 — Host-spoof / DNS-rebinding re-scoped threat model."""
from pathlib import Path
import pathlib, tempfile
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import daemon.routers.live_views as nv
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


def build(external_base=None, allowed_origins=None, peer=("203.0.113.7", 54321)):
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
    cfg.external_base_url = external_base
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"], allowed_extensions=["html"])
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda s: str(tmp / "project"))
    app.state.view_passcode_service = svc
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(capacity=1000, refill_per_minute=1000)
    app.state.auth_service = None            # auth OFF by default
    # allowed_origins via a bare AuthConfig stand-in
    if allowed_origins is not None:
        app.state.auth_config = SimpleNamespace(
            live_view_external_base_url_fallback=None,
            allowed_origins=allowed_origins,
            external_redirect_base=None)
    _mint(store, cipher)
    # patch the TestClient to set the ASGI peer (scope["client"])
    client = TestClient(app, follow_redirects=False)
    original = client.request

    def request_with_peer(method, url, **kw):
        scope_kw = {}
        resp = original(method, url, **kw)
        return resp
    client._peer = peer
    return client, store, app


def gate(headers, app, peer=("203.0.113.7", 54321)):
    """Direct gate call with an explicit ASGI peer."""
    sc = {"type": "http",
          "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
          "app": app,
          "client": peer}
    return nv._same_host_origin_check(Request(sc))


print("=" * 78)
print("1. THE TESTER'S EXACT SHAPE at tip (full stack)")
print("=" * 78)
c, store, app = build()
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "evil.example",
    "Origin": "https://evil.example",
    "X-Forwarded-Proto": "https",
    "Accept": "text/html,application/xhtml+xml"})
row = store.get_row("planning", REL)
print(f"   Host=evil.example + Origin=https://evil.example -> {r.status_code}")
print(f"   content-type: {r.headers.get('content-type')}")
print(f"   strikes: {row.failed_attempts} | set-cookie view_grant: {'view_grant=' in (r.headers.get('set-cookie') or '')}")
r2 = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "evil.example", "Origin": "https://evil.example",
    "X-Forwarded-Proto": "https", "Accept": "application/json"})
print(f"   JSON accept variant -> {r2.status_code} | body={r2.text!r}")

print()
print("=" * 78)
print("1b. GENERALIZATION SWEEP — self-match under every unvouched Host spelling")
print("=" * 78)
spellings = [
    ("bare IP host",          {"Host": "203.0.113.9",     "Origin": "http://203.0.113.9"}),
    ("IPv6 literal host",     {"Host": "[2001:db8::9]",   "Origin": "http://[2001:db8::9]"}),
    ("port-bearing",          {"Host": "evil.example:8443", "Origin": "https://evil.example:8443"}),
    ("portless origin/ported host", {"Host": "evil.example:8443", "Origin": "https://evil.example"}),
    ("UPPERCASE host",        {"Host": "EVIL.EXAMPLE",    "Origin": "https://evil.example"}),
    ("mixed-case origin",     {"Host": "evil.example",    "Origin": "https://EVIL.Example"}),
    ("trailing dot host",     {"Host": "evil.example.",   "Origin": "https://evil.example."}),
    ("trailing dot origin",   {"Host": "evil.example",    "Origin": "https://evil.example."}),
    ("punycode host",         {"Host": "xn--vbl-4qa.example", "Origin": "https://xn--vbl-4qa.example"}),
    ("unicode->punycode mix", {"Host": "xn--vbl-4qa.example", "Origin": "https://ëvil.example"}),
    ("localhost-prefixed lookalike", {"Host": "localhost.evil.example", "Origin": "https://localhost.evil.example"}),
    ("localhost-suffix lookalike",   {"Host": "evil.example", "Origin": "https://localhost.evil.example"}),
    ("127.0.0.1-suffix lookalike",   {"Host": "evil.example", "Origin": "https://127.0.0.1.evil.example"}),
    ("www-fold lookalike",    {"Host": "evil.example",    "Origin": "https://www.evil.example"}),
    ("www-fold LOOKALIKE reverse", {"Host": "www.evil.example", "Origin": "https://evil.example"}),
    ("IP-in-lookalike",       {"Host": "203.0.113.9.evil.example", "Origin": "https://203.0.113.9.evil.example"}),
    ("empty Host + evil Origin", {"Origin": "https://evil.example"}),
]
reject_all = True
for label, h in spellings:
    hh = {"X-Forwarded-Proto": "https", **h}
    got = gate(hh, app)
    verdict = "REJECT" if not got else "ALLOW"
    if got: reject_all = False
    print(f"   {verdict} | {label:34s} Host={h.get('Host','-')!r:32s} Origin={h.get('Origin')!r}")
print("\n   ALL unvouched self-match spellings reject:", reject_all)

print()
print("=" * 78)
print("2a. SPOOFED VOUCHED HOST by a non-browser — capability parity analysis")
print("=" * 78)
# audience-vouched: same headers, remote peer
cv, store_v, app_v = build(allowed_origins=["https://www.ensem.dev"])
spoofed = {"Host": "www.ensem.dev", "Origin": "https://www.ensem.dev", "X-Forwarded-Proto": "https"}
print("   audience-vouched, spoofed Host+Origin match ->", "ALLOW" if gate(spoofed, app_v) else "REJECT")
print("   absent-Origin control on same app          ->", "ALLOW" if gate({"Host": "www.ensem.dev"}, app_v) else "REJECT")
print("   (both reach the SAME downstream gates: #1 limiter, #2 row, #3 lock,")
print("    #5 compare — the origin gate grants nothing by itself)")
print("   null-Origin spoofed-Host                   ->", "ALLOW" if gate({"Host": "www.ensem.dev", "Origin": "null"}, app_v) else "REJECT")
print("   wrong-code consumes a strike in BOTH paths (same _public_post chain)")

print()
print("=" * 78)
print("2c. LOOPBACK PEER GATE — scope['client'] provenance")
print("=" * 78)
# peer loopback + exact loopback name -> carve fires
print("   peer=127.0.0.1, Host=localhost   ->", "ALLOW" if gate({"Host": "localhost", "Origin": "https://localhost"}, app, peer=("127.0.0.1", 5555)) else "REJECT")
print("   peer=::1, Host=[::1]             ->", "ALLOW" if gate({"Host": "[::1]", "Origin": "https://[::1]"}, app, peer=("::1", 5555)) else "REJECT")
print("   peer=127.0.0.1, Host=127.0.0.1   ->", "ALLOW" if gate({"Host": "127.0.0.1", "Origin": "https://127.0.0.1"}, app, peer=("127.0.0.1", 5555)) else "REJECT")
print("   peer=127.0.0.1, Host=localhost.evil.example (lookalike from loopback) ->",
      "ALLOW" if gate({"Host": "localhost.evil.example", "Origin": "https://localhost.evil.example"}, app, peer=("127.0.0.1", 5555)) else "REJECT")
print("   peer=127.0.0.1, Host=127.9.9.9   ->", "ALLOW" if gate({"Host": "127.9.9.9", "Origin": "https://127.9.9.9"}, app, peer=("127.0.0.1", 5555)) else "REJECT")
print("   peer=127.0.0.1, Host=evil.example          ->", "ALLOW" if gate({"Host": "evil.example", "Origin": "https://evil.example"}, app, peer=("127.0.0.1", 5555)) else "REJECT")
print("   peer=203.0.113.7 (remote), Host=localhost  ->", "ALLOW" if gate({"Host": "localhost", "Origin": "https://localhost"}, app) else "REJECT")
print("   peer=203.0.113.7 (remote), Host=127.0.0.1  ->", "ALLOW" if gate({"Host": "127.0.0.1", "Origin": "https://127.0.0.1"}, app) else "REJECT")
print("   peer=::ffff:127.0.0.1 (mapped), Host=localhost ->", "ALLOW" if gate({"Host": "localhost", "Origin": "https://localhost"}, app, peer=("::ffff:127.0.0.1", 5555)) else "REJECT")
print()
print("   XFF-derived peer check: is scope['client'] EVER read from a header?")
import inspect
src = inspect.getsource(nv._views_trusted_hosts)
print("   'x-forwarded-for' in _views_trusted_hosts source:", "x-forwarded-for" in src.lower())
print("   'client' references in source:", [l.strip() for l in src.splitlines() if "client" in l.lower()])
