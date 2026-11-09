"""rk_mint.py — RE-KILL 2 drill-row mint (in-process, dev DB, drill key).

Mints TWO rows:
  1. the view target   (root=g27fix, rel=rekill2.md) — via the
     view_link TOOL path (the documented mint lane).
  2. the BOARD row     (root=board, rel=index.html) — via the
     ViewPasscodeService.mint service call (the board root is
     RESERVED — 'view_link' refuses it, so the service call is the
     only cheap mint path; the commission's "cheaply mintable"
     condition).

Both passcodes are written into the fence as plain drill values
(dev-DB rows only — never a production secret).
"""
import os
import re
import sys

from sqlalchemy import create_engine

from daemon.config import LiveViewsConfig
from daemon.services.live_views import LiveViewsService
from daemon.services.tmp_image_store import TmpImageStore
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher,
    ViewPasscodeService,
)
from daemon.tools.live_views import create_live_view_tools

FIVE_FIELD = re.compile(
    r"^Public URL: (?P<url>\S+)\n"
    r"Passcode: (?P<code>[A-Za-z0-9]{6})\n"
    r"Authenticated URL: (?P<auth>\S+)\n"
    r"Generation: (?P<gen>\d+)\n"
    r"Locked: (?P<locked>yes|no)"
    r"(?P<hint>.*)$",
    re.DOTALL,
)

url = (
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
    f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
    f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)
engine = create_engine(url)
store = TmpImageStore(data_dir=os.environ["ENSEMBLE_DATA_DIR"])
passcode_service = ViewPasscodeService(
    engine=engine, cipher=ViewPasscodeCipher()
)
lv_service = LiveViewsService(config=LiveViewsConfig(), tmp_image_store=store)


class _Mgr:
    live_views_service = lv_service
    _view_passcode_service = passcode_service


root = os.environ["ROOT"]
path = os.environ["VIEW_R"]

print(f"== mint view_link({root!r}, {path!r})")
tool = create_live_view_tools(_Mgr(), "e2e-rekill2-tester")[0]
out = tool.invoke({"root_name": root, "path": path})
if out.startswith("Error:") and "ciphertext cannot" in out:
    print("  rotation-required envelope; documented recovery: refresh=True")
    out = tool.invoke({"root_name": root, "path": path, "refresh": True})
print(out)
m = FIVE_FIELD.match(out)
if not m or out.startswith("Error:"):
    print("  VIEW MINT FAIL")
    sys.exit(1)
expected_public = f"/views/public/{root}/{path}"
expected_auth = f"/views/{root}/{path}"
ok = m.group("url") == expected_public and m.group("auth") == expected_auth
print(
    f"  shape {'OK' if ok else 'FAIL'}: public={m.group('url')} "
    f"auth={m.group('auth')} gen={m.group('gen')} locked={m.group('locked')}"
)
open(os.path.join(os.environ["FENCE"], "passcode-view"), "w").write(
    m.group("code")
)

print("== mint board row (service path — 'board' is a RESERVED root)")
board = passcode_service.mint("board", "index.html", "e2e-rekill2-tester")
print(
    f"  board gen={board.generation} created={board.created} "
    f"locked={board.locked_status}"
)
open(os.path.join(os.environ["FENCE"], "passcode-board"), "w").write(
    board.passcode
)
print("== MINT OK")
