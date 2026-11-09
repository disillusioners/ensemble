"""Mutation E: endswith-based host matching (the T1 'suffix-safe' claim)."""
import daemon.routers.live_views as lv
_orig = lv._host_matches
def bad(o, t):
    o_host, o_port = o
    t_host, t_port = t
    # plausible-looking but suffix-unsafe
    if not (o_host == t_host or o_host.endswith("." + t_host) or t_host.endswith("." + o_host)):
        return False
    if o_port is None or t_port is None:
        return True
    return o_port == t_port
lv._host_matches = bad
print("[mut_suffix] _host_matches -> suffix-match (subdomain-tolerant)")
