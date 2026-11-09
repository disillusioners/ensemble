"""Is there ANY way to declare a proxy front with auth OFF?

If not, an auth-OFF deployment behind a proxy (dev/demo shape) can no
longer reconcile Host != Origin at all -> legit form POSTs 403.
"""
from daemon.config import LiveViewsConfig, LiveViewsRootConfig, AuthConfig
import inspect

# 1) the ONLY writers of live_view_external_base_url_fallback
import subprocess
out = subprocess.run(["grep","-rn","live_view_external_base_url_fallback",
                      "daemon/api.py","daemon/config.py"],
                     capture_output=True, text=True).stdout
for line in out.strip().splitlines():
    print("  ", line)

print()
# 2) does AuthConfig accept the field at CONSTRUCTION (i.e. could a test/
#    operator set it with auth OFF)?
import daemon.config as c
fields = c.AuthConfig.model_fields
print("field present:", "live_view_external_base_url_fallback" in fields)
print("strip guard (_strip_non_env_fields) removes it? ->",
      "_strip_non_env_fields" in dir(c.AuthConfig))
