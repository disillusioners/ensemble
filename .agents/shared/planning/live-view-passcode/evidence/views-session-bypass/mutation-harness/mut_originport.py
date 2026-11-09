"""Mutation D: restore iteration-0 origin-parser malformed-port mapping."""
import daemon.routers.live_views as lv
from urllib.parse import urlsplit

def bad_origin(value):
    try:
        parts = urlsplit((value or "").strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None       # <-- iteration-0
    return host, port

lv._origin_url_host_port = bad_origin
print("[mut_originport] _origin_url_host_port malformed port -> portless")
