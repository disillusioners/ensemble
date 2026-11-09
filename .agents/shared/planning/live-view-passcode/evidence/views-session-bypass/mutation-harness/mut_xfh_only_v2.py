"""FIX-7-AWARE mutation: remove ONLY leg (b) X-Forwarded-Host.

The v1 plugin predates FIX-7 and did not implement the service declaration
source, so auth-OFF tests failed for the wrong reason. This version mirrors
the CURRENT declaration composition exactly, then drops leg (b) only.
"""
import daemon.routers.live_views as lv


def _declared(request):
    cfg = getattr(request.app.state, "auth_config", None)
    if cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None):
        return True
    svc = getattr(request.app.state, "live_views_service", None)
    if svc is not None and getattr(svc, "declared_external_base_url", None):
        return True
    return False


def no_xfh(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    if _declared(request):
        fwd = h.get("forwarded")             # leg (c) KEPT
        if fwd:
            first = fwd.split(",")[0]
            for piece in first.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("host="):
                    rv = piece[len("host="):].strip()
                    if len(rv) >= 2 and rv.startswith('"') and rv.endswith('"'):
                        rv = rv[1:-1]
                    q = lv._authority_host_port(rv)
                    if q:
                        out.append(q)
                    break
        # leg (b) DELIBERATELY OMITTED
    cfg = getattr(request.app.state, "auth_config", None)
    if cfg is not None:
        try:
            hosts, _ = parse_operator_audience(cfg)
        except Exception:
            hosts = frozenset()
        for a in sorted(hosts):
            q = lv._authority_host_port(a)
            if q:
                out.append(q)
    return out


lv._views_trusted_hosts = no_xfh
print("[mut_xfh_only_v2] leg (b) removed; FIX-7 declaration composition mirrored")
