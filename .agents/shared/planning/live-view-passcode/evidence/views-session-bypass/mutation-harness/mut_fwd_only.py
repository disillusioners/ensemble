"""Mutation H: remove ONLY leg (c) RFC7239 Forwarded (leg b kept)."""
import daemon.routers.live_views as lv


def no_forwarded(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    cfg = getattr(request.app.state, "auth_config", None)
    declared = bool(cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None))
    if declared:
        xfh = h.get("x-forwarded-host")          # leg (b) KEPT
        if xfh:
            fv = xfh.split(",")[0].strip()
            if fv:
                q = lv._authority_host_port(fv)
                if q:
                    out.append(q)
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


lv._views_trusted_hosts = no_forwarded
print("[mut_fwd_only] leg (c) RFC7239 Forwarded REMOVED (leg b kept)")
