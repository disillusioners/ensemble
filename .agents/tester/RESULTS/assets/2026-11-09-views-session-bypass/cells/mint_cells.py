#!/usr/bin/env python
"""Degradation-cells view mint — in-process view_link TOOL path against
dev DB + the ONE drill fence key (same pattern as the g2g8 mint_views.py
/ 2026-10-18 journey mint_tool.sh). Mints ONE view (ROOT/VIEW_A from env);
handles ViewPasscodeRotationRequired via the documented refresh=True
recovery. Writes the 6-char passcode to $FENCE/passcode-a."""
import os, re, sys

FIVE_FIELD = re.compile(
    r"^Public URL: (?P<url>\S+)\n"
    r"Passcode: (?P<code>[A-Za-z0-9]{6})\n"
    r"Authenticated URL: (?P<auth>\S+)\n"
    r"Generation: (?P<gen>\d+)\n"
    r"Locked: (?P<locked>yes|no)"
    r"(?P<hint>.*)$",
    re.DOTALL,
)

from sqlalchemy import create_engine
from daemon.config import LiveViewsConfig
from daemon.services.live_views import LiveViewsService
from daemon.services.tmp_image_store import TmpImageStore
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
from daemon.tools.live_views import create_live_view_tools

url = (f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
       f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}")
engine = create_engine(url)
store = TmpImageStore(data_dir=os.environ["ENSEMBLE_DATA_DIR"])
passcode_service = ViewPasscodeService(engine=engine, cipher=ViewPasscodeCipher())
lv_service = LiveViewsService(config=LiveViewsConfig(), tmp_image_store=store)

class _Mgr:
    live_views_service = lv_service
    _view_passcode_service = passcode_service

tool = create_live_view_tools(_Mgr(), "e2e-cells-tester")[0]
root, path = os.environ["ROOT"], os.environ["VIEW_A"]

print(f"== mint view_link({root!r}, {path!r})")
out = tool.invoke({"root_name": root, "path": path})
if out.startswith("Error:") and "ciphertext cannot" in out:
    print("  rotation-required envelope; documented recovery: refresh=True")
    out = tool.invoke({"root_name": root, "path": path, "refresh": True})
print(out)
m = FIVE_FIELD.match(out)
if not m or out.startswith("Error:"):
    print("  MINT FAIL")
    sys.exit(1)
open(os.path.join(os.environ["FENCE"], "passcode-a"), "w").write(m.group("code"))
expected_public = f"/views/public/{root}/{path}"
expected_auth = f"/views/{root}/{path}"
ok = m.group("url") == expected_public or m.group("url").endswith(expected_public)
print(f"  shape OK: public={m.group('url')} auth={m.group('auth')} gen={m.group('gen')} locked={m.group('locked')}")
