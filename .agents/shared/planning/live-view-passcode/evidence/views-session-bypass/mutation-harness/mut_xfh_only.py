"""Mutation G: remove ONLY leg (b) X-Forwarded-Host; keep leg (c) Forwarded.

If any test genuinely pins leg (b), it must fail here. If everything
passes, leg (b) has no discriminating test (leg (d) carries the
declared-front host instead).
"""
import daemon.routers.live_views as lv


def no_xfh(request):
    from daemon.auth.cookie import parse_operator_audience
    out = []
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p:
        out.append(p)
    cfg = getattr(request.app.state, "auth_config", None)
    declared = bool(cfg is not None and getattr(cfg, "live_view_external_base_url_fallback", None))
    if declared:
        fwd = h.get("forwarded")          # leg (c) KEPT
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
        # leg (b) X-Forwarded-Host DELIBERATELY OMITTED
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
print("[mut_xfh_only] leg (b) X-Forwarded-Host REMOVED (leg c kept)")
