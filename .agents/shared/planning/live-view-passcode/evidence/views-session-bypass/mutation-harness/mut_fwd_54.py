"""HYPOTHESIS 2: the coder's 54F likely came from restoring the pre-FIX-10
FUNCTION BODY *in place* (source-level edit of the tip module) rather than
monkeypatching — i.e. the tip's _views_trusted_hosts with legs (b)/(c)
re-inserted AND the veto removed AND the declared-anchor contribution kept.

Also: if the plugin restores the pre-FIX-10 function but the tip's TEST file
imports `_views_trusted_hosts` by name at import time (from daemon.routers
.live_views import _views_trusted_hosts) then a module-level sys.modules
swap does NOT rebind the already-imported name in the test module — the
plugin may be a NO-OP for the test's binding. THAT is the most likely
explanation for a mismatch in counts.

We test all three variants' failure counts here for the record.
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

# VARIANT A: rebind the module attribute (what I did) -> test-module's
# `from ... import _views_trusted_hosts` binding is ALREADY bound at import
# time, so the ATTRIBUTE swap is invisible to the test module's local name.
cur._views_trusted_hosts = base._views_trusted_hosts
print("[mut_fwd_54] VARIANT A: module attribute swapped (test-module local binding unaffected)")
