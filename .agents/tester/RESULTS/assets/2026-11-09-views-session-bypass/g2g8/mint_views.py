#!/usr/bin/env python
"""g2g8 view mint — in-process view_link TOOL path against dev DB + drill key
(same pattern as the 2026-10-18 journey mint_tool.sh). Mints matrix-a.md and
matrix-b.md in root g2g8matrix; handles ViewPasscodeRotationRequired via the
documented refresh=True recovery."""
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

tool = create_live_view_tools(_Mgr(), "e2e-g2g8-tester")[0]
root = os.environ["ROOT"]
targets = [(root, os.environ["VIEW_A"], "passcode-a"),
           (root, os.environ["VIEW_B"], "passcode-b")]

failures = 0
for r, path, pfile in targets:
    print(f"== mint view_link({r!r}, {path!r})")
    out = tool.invoke({"root_name": r, "path": path})
    if out.startswith("Error:") and "ciphertext cannot" in out:
        print("  rotation-required envelope; documented recovery: refresh=True")
        out = tool.invoke({"root_name": r, "path": path, "refresh": True})
    print(out)
    m = FIVE_FIELD.match(out)
    if not m or out.startswith("Error:"):
        print(f"  MINT FAIL for {r}/{path}")
        failures += 1
        continue
    open(os.path.join(os.environ["FENCE"], pfile), "w").write(m.group("code"))
    expected_public = f"/views/public/{r}/{path}"
    expected_auth = f"/views/{r}/{path}"
    ok = m.group("url") == expected_public and m.group("auth") == expected_auth
    print(f"  shape {'OK' if ok else 'FAIL'}: public={m.group('url')} auth={m.group('auth')} gen={m.group('gen')} locked={m.group('locked')}")
    if not ok:
        failures += 1

sys.exit(1 if failures else 0)
