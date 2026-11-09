"""Mutation B: drop the Sec-Fetch-Site discriminator (iteration-0 null-allow)."""
import daemon.routers.live_views as lv
_orig = lv._views_trusted_hosts
src_ok = True
# Re-implement the top-level check with the null branch un-conditioned.
_orig_check = lv._same_host_origin_check
def _check(request):
    h = request.headers
    judged = h.get("origin") if h.get("origin") else h.get("referer")
    if judged is None:
        return True
    if not judged.strip():
        return True
    if judged.strip().lower() == "null":
        return True          # <-- cross-site discriminator REMOVED
    return _orig_check(request)
lv._same_host_origin_check = _check
print("[mut_secfetch] null-branch discriminator REMOVED")
