"""HYPOTHESIS 3: the coder's plugin restored the pre-FIX-10 _views_trusted_hosts
AND the pre-FIX-10 _www_equivalent (which lacks the trailing-dot
normalization). The base blob has NO _views_host_key, so restoring
_www_equivalent from it removes the NameError but changes normalization
semantics => more failures (a plausible route to a much larger count)."""
from pathlib import Path
import tempfile
import subprocess, sys, importlib.util
src = subprocess.run(["git", "show", "6398a29a3:daemon/routers/live_views.py"],
                     capture_output=True, text=True).stdout
open(str(Path(tempfile.gettempdir()) / "lv_base_blob.py"), "w").write(src)
spec = importlib.util.spec_from_file_location("lv_base_blob", str(Path(tempfile.gettempdir()) / "lv_base_blob.py"))
base = importlib.util.module_from_spec(spec); sys.modules["lv_base_blob"] = base
spec.loader.exec_module(base)
import daemon.routers.live_views as cur
cur._views_trusted_hosts = base._views_trusted_hosts
cur._www_equivalent = base._www_equivalent
print("[mut_fwd_revert2] pre-FIX-10 _views_trusted_hosts + _www_equivalent")
