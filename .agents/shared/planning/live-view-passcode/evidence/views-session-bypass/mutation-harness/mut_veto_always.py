"""mut_veto_always — the veto fires on EVERY declared request (over-broad).
Magnitude probe: how many tests does an OVER-BROKEN veto break?"""
import daemon.routers.live_views as lv
_orig = lv._views_trusted_hosts
def always_veto(request):
    out = _orig(request)
    return []
lv._views_trusted_hosts = always_veto
print("[mut_veto_always] trust set forced empty")
