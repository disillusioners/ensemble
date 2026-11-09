"""FIX-10 probe at ac5f024fc — declared-state forwarded-header forgery class."""
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
DECL = "https://views.example.com"


def build(declared=DECL, allowed=("https://views.example.com",)):
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
    cfg.external_base_url = declared
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
    app.state.auth_service = _StubAuthService(set())
    if allowed is not None:
        app.state.auth_config = SimpleNamespace(
            live_view_external_base_url_fallback=declared,
            allowed_origins=list(allowed),
            external_redirect_base=None)
    _mint(store, cipher)
    return TestClient(app, follow_redirects=False), store, app


def post(app, headers, code="wrong0"):
    c, store, _ = None, None, None
    return None


print("=" * 80)
print("1. THE BYPASS — declared front + forged XFH + evil Origin (tester probe-5)")
print("=" * 80)
c, store, app = build()
r = c.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079",
    "X-Forwarded-Host": "evil.example",
    "Origin": "https://evil.example",
    "X-Forwarded-Proto": "https"})
row = store.get_row("planning", REL)
print(f"   -> {r.status_code} | content-type: {r.headers.get('content-type')}")
print(f"   strikes: {row.failed_attempts} | view_grant: {'view_grant=' in (r.headers.get('set-cookie') or '')}")
print(f"   body: {r.text[:90]!r}")

print()
print("   generalization sweep (declared front):")
sweep = [
    ("RFC7239 host= forged",        {"Forwarded": 'for=1.2.3.4;host="evil.example"', "Origin": "https://evil.example"}),
    ("RFC7239 host= forged (K2)",   {"Forwarded": 'for=1.2.3.4;host="evil.example"', "Origin": "https://views.example.com"}),
    ("attacker-PREPEND multi",      {"X-Forwarded-Host": "evil.example, views.example.com", "Origin": "https://evil.example"}),
    ("attacker-PREPEND (declared O)", {"X-Forwarded-Host": "evil.example, views.example.com", "Origin": "https://views.example.com"}),
    ("attacker-APPEND multi",       {"X-Forwarded-Host": "views.example.com, evil.example", "Origin": "https://evil.example"}),
    ("port-bearing forged",         {"X-Forwarded-Host": "evil.example:8443", "Origin": "https://evil.example:8443"}),
    ("UPPERCASE forged",            {"X-Forwarded-Host": "EVIL.EXAMPLE", "Origin": "https://evil.example"}),
    ("trailing-dot forged",         {"X-Forwarded-Host": "evil.example.", "Origin": "https://evil.example."}),
    ("UNPARSEABLE forged",          {"X-Forwarded-Host": "evil example:notaport", "Origin": "https://evil.example"}),
    ("UNPARSEABLE (no port)",       {"X-Forwarded-Host": ":::bogus:::", "Origin": "https://evil.example"}),
    ("K2 forged XFH + declared O",  {"X-Forwarded-Host": "evil.example", "Origin": "https://views.example.com"}),
    ("K2 forged Fwd + declared O",  {"Forwarded": 'host="evil.example"', "Origin": "https://views.example.com"}),
]
for label, h in sweep:
    hh = {"Host": "internal-svc:8079", "X-Forwarded-Proto": "https", **h}
    r = c.post(VIEW, data={"code": "wrong0"}, headers=hh)
    st = store.get_row("planning", REL).failed_attempts
    print(f"   {'REJECT' if r.status_code==403 else 'ALLOW!!':8s} {label:32s} -> {r.status_code} strikes={st}")
    if r.status_code != 403:
        store.get_row("planning", REL).failed_attempts = 0

print()
print("=" * 80)
print("2a. VETO PARITY — absent-Origin / null-Origin with a DISAGREEING forwarded value")
print("=" * 80)
c2, store2, _ = build()
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "evil.example",
    "X-Forwarded-Proto": "https"})
print(f"   absent-Origin + disagreeing XFH  -> {r.status_code} (must NOT be 403/poisoned)")
print(f"   strikes: {store2.get_row('planning', REL).failed_attempts}")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "evil.example",
    "Origin": "null", "X-Forwarded-Proto": "https"})
print(f"   null-Origin + disagreeing XFH    -> {r.status_code} (must NOT be 403/poisoned)")
r = c2.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "evil.example",
    "Origin": "null", "Sec-Fetch-Site": "cross-site", "X-Forwarded-Proto": "https"})
print(f"   null-Origin + cross-site attest  -> {r.status_code} (SFS must still win)")

print()
print("=" * 80)
print("2b. UNDECLARED state — forwarded values fully inert")
print("=" * 80)
c3, store3, app3 = build(declared=None, allowed=None)
r = c3.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "evil.example",
    "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
print(f"   undeclared, forged XFH + evil O  -> {r.status_code} (expect 403 - no anchor at all)")
sc = {"type":"http","headers":[(b"host",b"internal-svc:8079"),
                               (b"x-forwarded-host",b"evil.example"),
                               (b"origin",b"https://evil.example")],
      "app": app3, "client": ("203.0.113.7", 54321)}
print("   trust set (undeclared, forged XFH):",
      sorted({h for h,_ in nv._views_trusted_hosts(Request(sc))}), "(XFH must NOT appear)")

print()
print("=" * 80)
print("2c. LEGIT topology")
print("=" * 80)
c4, store4, _ = build()
r = c4.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079",
    "X-Forwarded-Host": "views.example.com, internal:8079",
    "Origin": "https://views.example.com", "X-Forwarded-Proto": "https"})
print(f"   K3 declared+agreeing XFH + matching O -> {r.status_code} (expect 200/303)")
r = c4.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079",
    "X-Forwarded-Host": "other.example",
    "Origin": "https://other.example", "X-Forwarded-Proto": "https"})
print(f"   multi-vhost UNDECLARED host via XFH    -> {r.status_code} (expect 403 = strict contract)")

print()
print("=" * 80)
print("3. PORT CORNER")
print("=" * 80)
c5, store5, _ = build(declared="https://views.example.com:8443", allowed=("https://views.example.com:8443",))
r = c5.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "views.example.com",
    "Origin": "https://views.example.com:9999", "X-Forwarded-Proto": "https"})
print(f"   declared :8443 + portless fwd + O :9999 -> {r.status_code} (expect 403, no port widening)")
r = c5.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "views.example.com",
    "Origin": "https://views.example.com:8443", "X-Forwarded-Proto": "https"})
print(f"   declared :8443 + portless fwd + O :8443 -> {r.status_code} (expect 200/303)")
r = c5.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "views.example.com:9999",
    "Origin": "https://views.example.com:9999", "X-Forwarded-Proto": "https"})
print(f"   declared :8443 + fwd :9999 + O :9999 -> {r.status_code} (port-bearing fwd vs declared)")
r = c5.post(VIEW, data={"code": "wrong0"}, headers={
    "Host": "internal-svc:8079", "X-Forwarded-Host": "views.example.com:9999",
    "Origin": "https://views.example.com:8443", "X-Forwarded-Proto": "https"})
print(f"   declared :8443 + fwd :9999 + O :8443 -> {r.status_code} (mismatched fwd port)")
