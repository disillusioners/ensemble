"""Mutation I: OVER-TIGHTEN — reject every Origin:null (the pre-fix ban).

The live-fix preservation pin MUST fail here, otherwise it does not
actually protect the Safari/Chromium shape.
"""
import daemon.routers.live_views as lv

_orig = lv._same_host_origin_check


def _reject_null(request):
    h = request.headers
    judged = h.get("origin") if h.get("origin") else h.get("referer")
    if judged is not None and judged.strip().lower() == "null":
        return False          # <-- nail: no null ever allowed
    return _orig(request)


lv._same_host_origin_check = _reject_null
print("[mut_reject_all_null] all Origin:null rejected")
