"""Reverse mutation: RESTORE the iteration-0 UNGATED legs (b)/(c).

The negative FIX-1 pins (echoed XFH/Forwarded must 403 when the proxy
front is undeclared) must FAIL under this mutation, otherwise they are
not pinning the gate.
"""
import daemon.routers.live_views as lv


def _ungated(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    xfh = h.get("x-forwarded-host")
    if xfh:
        fv = xfh.split(",")[0].strip()
        if fv:
            q = lv._authority_host_port(fv)
            if q:
                out.append(q)
    fwd = h.get("forwarded")
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


lv._views_trusted_hosts = _ungated
print("[ungate_plugin] iteration-0 UNGATED trust restored")
