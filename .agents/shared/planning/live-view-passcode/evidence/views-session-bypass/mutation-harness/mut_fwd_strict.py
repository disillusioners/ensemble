"""mut_fwd_strict — restore the pre-FIX-10 module namespace AND remove the
FIX-10-only names (_views_host_key / _views_forwarded_values / _www_strip),
simulating a module-level revert. Tests importing those names would ERROR
(collection-level), which is how a much larger failure count arises.
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
print("[mut_fwd_strict] module reverted; FIX-10-only names absent")
