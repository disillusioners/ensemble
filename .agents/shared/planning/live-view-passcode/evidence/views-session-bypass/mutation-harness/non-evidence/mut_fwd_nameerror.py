"""HYPOTHESIS TEST for the coder's 54F.

If the plugin restores the pre-FIX-10 _views_trusted_hosts but ALSO strips
the FIX-10-era helper names (_views_host_key / _www_strip) from the tip
module, then the tip's _host_matches -> _www_equivalent -> _views_host_key
chain raises NameError => a CASCADE of unrelated failures (the plugin is
broken, not the code). This is the most likely source of a 54F number.
"""
from pathlib import Path
import tempfile
import subprocess, sys, importlib.util

src = subprocess.run(["git", "show", "6398a29a3:daemon/routers/live_views.py"],
                     capture_output=True, text=True).stdout
open(str(Path(tempfile.gettempdir()) / "lv_base_blob.py"), "w").write(src)
spec = importlib.util.spec_from_file_location("lv_base_blob", str(Path(tempfile.gettempdir()) / "lv_base_blob.py"))
base = importlib.util.module_from_spec(spec)
sys.modules["lv_base_blob"] = base
spec.loader.exec_module(base)

import daemon.routers.live_views as cur
cur._views_trusted_hosts = base._views_trusted_hosts
# strip the FIX-10-era helper names the tip module's other functions need
for _n in ("_views_host_key", "_www_strip"):
    if hasattr(cur, _n):
        delattr(cur, _n)
print("[mut_fwd_nameerror] pre-FIX-10 fn + helpers stripped (NameError cascade)")
