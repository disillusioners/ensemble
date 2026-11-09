"""mut_rawhost — restore UNCONDITIONAL Host trust (the pre-G2.7 behavior).
Coder claims 10 test failures. Verify."""
import daemon.routers.live_views as lv

_orig = lv._views_trusted_hosts

def rawhost(request):
    """Restore the ITERATION-3 shape: raw Host unconditionally anchors."""
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)                       # <-- unconditional (pre-G2.7)
    cfg = getattr(request.app.state, "auth_config", None)
    declared_bases = []
    if cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None):
        declared_bases.append(cfg.live_view_external_base_url_fallback)
    svc = getattr(request.app.state, "live_views_service", None)
    if svc is not None and getattr(svc, "declared_external_base_url", None):
        declared_bases.append(svc.declared_external_base_url)
    if declared_bases:
        for v in declared_bases:
            q = lv._declared_base_anchor(v)
            if q:
                out.append(q)
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

lv._views_trusted_hosts = rawhost
print("[mut_rawhost] unconditional raw-Host anchor restored (pre-G2.7)")
