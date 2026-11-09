"""Mutation F: trust EVERY Forwarded element's host= (breaks 'first element only')."""
import daemon.routers.live_views as lv

def all_forwarded(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    cfg = getattr(request.app.state, "auth_config", None)
    declared = bool(cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None))
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
            for element in fwd.split(","):          # <-- ALL elements
                for piece in element.split(";"):
                    piece = piece.strip()
                    if piece.lower().startswith("host="):
                        rv = piece[len("host="):].strip()
                        if len(rv) >= 2 and rv.startswith('"') and rv.endswith('"'):
                            rv = rv[1:-1]
                        q = lv._authority_host_port(rv)
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

lv._views_trusted_hosts = all_forwarded
print("[mut_fwdtail] ALL Forwarded elements trusted")
