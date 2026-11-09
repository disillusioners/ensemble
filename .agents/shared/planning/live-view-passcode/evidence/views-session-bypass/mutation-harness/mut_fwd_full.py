"""mut_fwd_full — restore the ENTIRE pre-FIX-10 module namespace
(every function from the base blob), not just _views_trusted_hosts.
This is the harsher variant; it should explain a larger failure count."""
import subprocess, sys, importlib.util

src = subprocess.run(["git", "show", "87220aa1c:daemon/routers/live_views.py"],
                     capture_output=True, text=True).stdout
path = "/tmp/lv_base5.py"
open(path, "w").write(src)
spec = importlib.util.spec_from_file_location("lv_base5", path)
base = importlib.util.module_from_spec(spec)
sys.modules["lv_base5"] = base
spec.loader.exec_module(base)

import daemon.routers.live_views as cur
for name in dir(base):
    if name.startswith("__"):
        continue
    val = getattr(base, name)
    if callable(val) and hasattr(cur, name):
        setattr(cur, name, val)
print("[mut_fwd_full] ENTIRE pre-FIX-10 module namespace restored")
