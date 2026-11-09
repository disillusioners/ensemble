import daemon.routers.live_views as lv
def bad(o, t):
    o_host, o_port = o
    t_host, t_port = t
    a, b = lv._www_equivalent(o_host), lv._www_equivalent(t_host)
    if not (a == b or a.startswith(b)): return False
    if o_port is None or t_port is None: return True
    return o_port == t_port
lv._host_matches = bad
print("[mut_startswith] prefix-contains match")
