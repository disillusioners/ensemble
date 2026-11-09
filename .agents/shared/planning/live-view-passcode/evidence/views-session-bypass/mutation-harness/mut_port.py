import daemon.routers.live_views as lv
from urllib.parse import urlsplit
def bad_authority(value):
    value = (value or "").strip()
    if not value: return None
    if "://" not in value: value = f"//{value}"
    try: parts = urlsplit(value)
    except ValueError: return None
    host = (parts.hostname or "").lower()
    if not host: return None
    try: port = parts.port
    except ValueError: port = None          # <-- iteration-0 mapping
    return host, port
lv._authority_host_port = bad_authority
print("[mut_port] malformed port -> portless")
