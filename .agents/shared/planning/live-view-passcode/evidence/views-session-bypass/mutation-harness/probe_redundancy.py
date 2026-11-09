"""FIX-10 redundancy probe — pre-implementation evidence.

Question the commission requires answered BEFORE choosing the
mechanism: after constraining every forwarded-derived anchor to the
operator-DECLARED anchor set, can a forwarded value EVER contribute
an anchor that legs (d) (audience) / (e) (declared bases) do not
already carry?

Probe: for every declared fixture shape in the lane, compute the
trust set with and without the forwarded headers and diff it.

Run: .venv/bin/python /tmp/views-review/probe_redundancy.py
"""
from types import SimpleNamespace

from starlette.requests import Request

from daemon.auth.cookie import parse_operator_audience
from daemon.config import LiveViewsConfig
from daemon.routers.live_views import (
    _authority_host_port,
    _declared_base_anchor,
    _views_trusted_hosts,
)
from daemon.services.live_views import LiveViewsService


def _service(base):
    cfg = LiveViewsConfig()
    if base is not None:
        cfg.external_base_url = base
    return LiveViewsService(
        config=cfg, project_workdir_by_shortname_resolver=lambda _s: None
    )


def _auth_config(allowed, fallback=None, redirect=None):
    ns = SimpleNamespace(
        allowed_origins=list(allowed),
        external_redirect_base=redirect,
        live_view_external_base_url_fallback=fallback,
    )
    return ns


def _request(headers, *, auth_config=None, service=None):
    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
        "app": SimpleNamespace(
            state=SimpleNamespace(
                auth_config=auth_config, live_views_service=service
            )
        ),
        "client": ("testclient", 50000),
    }
    return Request(scope)


def _declared_only(auth_config, service):
    """Legs (d)+(e) alone — the anchor set WITHOUT any forwarded
    contribution."""
    out = []
    if auth_config is not None:
        hosts, _ = parse_operator_audience(auth_config)
        for h in sorted(hosts):
            p = _authority_host_port(h)
            if p:
                out.append(p)
    declared = []
    if auth_config is not None:
        fb = getattr(
            auth_config, "live_view_external_base_url_fallback", None
        )
        if fb:
            declared.append(fb)
    if service is not None:
        sb = getattr(service, "declared_external_base_url", None)
        if sb:
            declared.append(sb)
    for d in declared:
        a = _declared_base_anchor(d)
        if a:
            out.append(a)
    return out


FIXTURES = [
    (
        "auth-fallback declared (proxy_front_client shape)",
        dict(
            auth_config=_auth_config(
                ["http://testserver"],
                fallback="https://elsewhere.example",
                redirect="http://testserver",
            ),
            service=None,
        ),
        {
            "Host": "internal-svc:8079",
            "X-Forwarded-Host": "views.example.com, internal:8079",
            "Forwarded": 'for=10.0.0.9;host="fwd.example.com"',
        },
    ),
    (
        "service-declared (auth-OFF shape)",
        dict(auth_config=None, service=_service("https://views.example.com")),
        {
            "Host": "internal-svc:8079",
            "X-Forwarded-Host": "views.example.com",
            "Forwarded": 'for=10.0.0.9;host="views.example.com"',
        },
    ),
    (
        "port-bearing service-declared base",
        dict(
            auth_config=None,
            service=_service("https://views.example.com:8443"),
        ),
        {
            "Host": "internal-svc:8079",
            "X-Forwarded-Host": "views.example.com",
            "Origin": "https://views.example.com:9999",
        },
    ),
    (
        "audience only",
        dict(
            auth_config=_auth_config(
                ["https://audience.example"],
                redirect="https://portal.example",
            ),
            service=None,
        ),
        {
            "Host": "internal-svc:8079",
            "X-Forwarded-Host": "audience.example",
            "Forwarded": 'for=10.0.0.9;host="portal.example"',
        },
    ),
]


for label, fixture, headers in FIXTURES:
    full = _views_trusted_hosts(_mock := _request(headers, **fixture))
    base = _declared_only(fixture["auth_config"], fixture["service"])
    forwarded_only = [
        t for t in full if t not in base
    ]
    print(f"\n=== {label}")
    print(f"  full trust set      : {full}")
    print(f"  legs (d)+(e) only   : {base}")
    print(f"  forwarded-ONLY addns: {forwarded_only}")
    # Outcome probe: does the forwarded contribution change an
    # ACCEPTANCE that (d)/(e) alone would refuse?
    extra = []
    for h, p in forwarded_only:
        for probe_port in (None, 443, 8443, 9999):
            extra.append((h, probe_port))
    print(f"  candidate extra accepts: {extra}")
