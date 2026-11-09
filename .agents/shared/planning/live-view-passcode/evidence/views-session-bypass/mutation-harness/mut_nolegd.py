"""Mutation: remove ONLY leg (d) operator audience.

Does leg (d) still have its own standalone pin after the FIX-6
fixture change (elsewhere.example)?
"""
import daemon.routers.live_views as lv


def no_audience(request):
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    cfg = getattr(request.app.state, "auth_config", None)
    declared = bool(cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None))
    svc = getattr(request.app.state, "live_views_service", None)
    if not declared and svc is not None and getattr(svc, "declared_external_base_url", None):
        declared = True
    if declared:
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
    # leg (d) operator audience DELIBERATELY OMITTED
    return out


lv._views_trusted_hosts = no_audience
print("[mut_nolegd] leg (d) operator audience REMOVED")
