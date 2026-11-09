"""mut_novouch: vouch ALWAYS true ( Host always anchored — pre-G2.7) via
a subtler route: add attacker names to the vouch set? No — mirror rawhost
but through the vouched_names path to test the CHECK itself."""
import daemon.routers.live_views as lv
_orig = lv._views_trusted_hosts
def always_vouch(request):
    out = _orig(request)
    h = request.headers
    p = lv._authority_host_port(h.get("host") or "")
    if p and p not in out:
        out.append(p)
    return out
lv._views_trusted_hosts = always_vouch
print("[mut_novouch] raw Host force-appended post-vouch")
