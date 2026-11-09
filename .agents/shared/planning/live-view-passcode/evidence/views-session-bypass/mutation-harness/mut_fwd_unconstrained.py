"""mut_fwd_unconstrained — restore the PRE-FIX-10 _views_trusted_hosts
(the forwarded-value ANCHORING legs (b)/(c), from blob 87220aa1c).

Coder claims ~54F. Verify independently.
"""
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
cur._views_trusted_hosts = base._views_trusted_hosts
print("[mut_fwd_unconstrained] pre-FIX-10 forwarded-ANCHORING function restored")
