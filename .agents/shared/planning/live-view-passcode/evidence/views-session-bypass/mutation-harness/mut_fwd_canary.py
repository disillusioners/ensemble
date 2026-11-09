"""mut_fwd_canary — the FIX-10 class-closure mutant WITH A CANARY.

Restores the pre-FIX-10 (6398a29a3-era) _views_trusted_hosts — the blob whose
sha equals 87220aa1c's (both fa75f5bf4a6fcb0172196561bf4bdc4f5afd126f).
Prints a canary on FIRST CALL so we can prove the plugin is genuinely loaded
and executing (not a stale-import / no-op plugin).
"""
from pathlib import Path
import tempfile
import subprocess, sys, importlib.util

BLOB = "6398a29a3:daemon/routers/live_views.py"
src = subprocess.run(["git", "show", BLOB], capture_output=True, text=True).stdout
path = str(Path(tempfile.gettempdir()) / "lv_base_blob.py")
open(path, "w").write(src)
spec = importlib.util.spec_from_file_location("lv_base_blob", path)
base = importlib.util.module_from_spec(spec)
sys.modules["lv_base_blob"] = base
spec.loader.exec_module(base)

import daemon.routers.live_views as cur
cur._views_trusted_hosts = base._views_trusted_hosts

_CANARY = {"n": 0}


def _canary_wrap(request):
    if _CANARY["n"] == 0:
        _CANARY["n"] += 1
        print("[CANARY] pre-FIX-10 _views_trusted_hosts IS INSTALLED AND CALLED "
              f"(blob={BLOB}, sha={subprocess.run(['git','rev-parse',BLOB],capture_output=True,text=True).stdout.strip()[:12]})")
    return base._views_trusted_hosts(request)


cur._views_trusted_hosts = _canary_wrap
print("[mut_fwd_canary] plugin loaded")
