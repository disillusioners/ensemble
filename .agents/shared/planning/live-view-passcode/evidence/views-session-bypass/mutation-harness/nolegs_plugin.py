"""Mutation-proof plugin: STRIP legs (b)/(c) at runtime.

If the XFH/Forwarded pins are DISCRIMINATING, the tests that claim to
pin those legs must FAIL under this mutation. If they still pass, the
pin is satisfied by another leg (i.e. vacuous).
"""
import daemon.routers.live_views as lv

_orig = lv._views_trusted_hosts


def _no_forwarded_legs(request):
    """Only legs (a) Host and (d) operator audience."""
    from daemon.auth.cookie import parse_operator_audience
    out = []
    p = lv._authority_host_port(request.headers.get("host") or "")
    if p:
        out.append(p)
    cfg = getattr(request.app.state, "auth_config", None)
    if cfg is not None:
        try:
            hosts, _ = parse_operator_audience(cfg)
        except Exception:
            hosts = frozenset()
        for h in sorted(hosts):
            q = lv._authority_host_port(h)
            if q:
                out.append(q)
    return out


lv._views_trusted_hosts = _no_forwarded_legs
# _same_host_origin_check resolves the name at call time from module globals
print("[nolegs_plugin] legs (b)/(c) REMOVED for this run")
