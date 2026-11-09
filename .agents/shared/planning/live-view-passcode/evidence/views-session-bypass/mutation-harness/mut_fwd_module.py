"""mut_fwd_module — replace the ENTIRE daemon.routers.live_views MODULE with
the pre-FIX-10 blob (sys.modules swap before collection).

This is the harshest form: the whole router (build_router, helpers) is the
base. Explains a much larger failure count.
"""
import subprocess, sys, importlib.util

src = subprocess.run(["git", "show", "87220aa1c:daemon/routers/live_views.py"],
                     capture_output=True, text=True).stdout
path = "/tmp/lv_base5.py"
open(path, "w").write(src)
spec = importlib.util.spec_from_file_location("daemon.routers.live_views", path)
base = importlib.util.module_from_spec(spec)
sys.modules["daemon.routers.live_views"] = base
spec.loader.exec_module(base)
print("[mut_fwd_module] WHOLE daemon.routers.live_views replaced by pre-FIX-10 blob")
