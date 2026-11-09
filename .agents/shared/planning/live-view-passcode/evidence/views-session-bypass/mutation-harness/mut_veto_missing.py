"""mut_veto_missing — the veto is DELETED (disagreeing forwarded value accepted).
The exact inverse of FIX-10's mechanism."""
import daemon.routers.live_views as lv
_orig = lv._views_trusted_hosts
def no_veto(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    cfg = getattr(request.app.state, "auth_config", None)
    declared = []
    if cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None):
        declared.append(cfg.live_view_external_base_url_fallback)
    svc = getattr(request.app.state, "live_views_service", None)
    if svc is not None and getattr(svc, "declared_external_base_url", None):
        declared.append(svc.declared_external_base_url)
    if declared:
        for v in declared:
            q = lv._declared_base_anchor(v)
            if q: out.append(q)
        xfh = h.get("x-forwarded-host")
        if xfh:
            fv = xfh.split(",")[0].strip()
            if fv:
                q = lv._authority_host_port(fv)
                if q: out.append(q)          # <-- forwarded value ANCHORS (veto gone)
    p = lv._authority_host_port(h.get("host") or "")
    if p: out.append(p)
    if cfg is not None:
        try:
            hosts, _ = parse_operator_audience(cfg)
        except Exception:
            hosts = frozenset()
        for a in sorted(hosts):
            q = lv._authority_host_port(a)
            if q: out.append(q)
    return out
lv._views_trusted_hosts = no_veto
print("[mut_veto_missing] veto deleted; forwarded value anchors again")
