import daemon.routers.live_views as lv
lv._www_equivalent = lambda host: host.replace("www.", "")
print("[mut_www] suffix-unsafe www fold")
