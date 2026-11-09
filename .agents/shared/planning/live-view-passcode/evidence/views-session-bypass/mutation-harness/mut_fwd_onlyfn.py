"""mut_fwd_onlyfn — the MOST surgical form: restore ONLY the forwarded-anchor
appends (the pre-FIX-10 legs b/c) inside the CURRENT function, i.e. the
minimal 'unconstrained forwarded value anchors' mutant.

If the FIX-10 pins discriminate this, the class is closed.
"""
import daemon.routers.live_views as lv

_orig = lv._views_trusted_hosts


def unconstrained(request):
    """Current function + the forwarded values ANCHORING (pre-FIX-10)."""
    out = _orig(request)
    headers = request.headers
    # re-add the pre-FIX-10 legs (b)/(c) — client value becomes an anchor
    cfg = getattr(request.app.state, "auth_config", None)
    declared = []
    if cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None):
        declared.append(cfg.live_view_external_base_url_fallback)
    svc = getattr(request.app.state, "live_views_service", None)
    if svc is not None and getattr(svc, "declared_external_base_url", None):
        declared.append(svc.declared_external_base_url)
    if declared:
        xfh = headers.get("x-forwarded-host")
        if xfh:
            fv = xfh.split(",")[0].strip()
            if fv:
                q = lv._authority_host_port(fv)
                if q:
                    out.append(q)
        fwd = headers.get("forwarded")
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
    return out


lv._views_trusted_hosts = unconstrained
print("[mut_fwd_onlyfn] forwarded values ANCHOR again (minimal pre-FIX-10 shape)")
