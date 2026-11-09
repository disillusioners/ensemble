"""mut_www v2: fold EVERY leading www. iteratively (multi-label fold)
and fold INSIDE labels — the worst-case www mutant."""
import daemon.routers.live_views as lv
def bad_fold(host):
    while host.startswith("www."):
        host = host[4:]
    return host
lv._www_equivalent = bad_fold
print("[mut_www2] iterative www fold")
