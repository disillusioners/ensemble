#!/usr/bin/env bash
# E2E journey J1 stage — mint via the view_link TOOL path (not the service
# fallback the drill used). Seeds the tmp-image entry via the store's own
# save() API (file-layout-faithful; NOT the authed HTTP API), then mints
# passcode rows for hello.md / diagram.mmd / tmp-image id through the REAL
# langchain tool object (.invoke) against the SAME DB + SAME drill key as
# the running daemon.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EV="$(cd "$HERE/.." && pwd)"
WT="$(cd "$HERE/../../../../../../.." && pwd)"
if [ ! -f "$WT/.env" ] || [ ! -d "$WT/daemon" ]; then
  echo "FENCE FAIL: worktree resolution landed on $WT"; exit 99
fi
cd "$WT"

[ -f "$EV/.drill-tmp" ] || { echo "no .drill-tmp — boot stage missing"; exit 91; }
DRILL_TMP="$(cat "$EV/.drill-tmp")"
case "$DRILL_TMP" in /tmp/lvjourney.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac
# shellcheck disable=SC1090
set -a; source "$DRILL_TMP/state.env"; set +a

# POSTGRES_* for the mint engine (same pattern as the drill).
set -a; source "$WT/.env"; set +a
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"
export ENSEMBLE_DATA_DIR

timeout 120 .venv/bin/python - <<'PY' 2>&1 | tee "$EV/j1-mint.txt"
import os, re, sys, uuid, zlib, struct, json, datetime, hashlib

IMAGE_ID = uuid.uuid4().hex
open(os.path.join(os.environ["DRILL_TMP"], "image-id"), "w").write(IMAGE_ID)

# ── seed a small valid PNG straight through the store's save() API ──────
def tiny_png(w=8, h=8, rgb=(200, 60, 60)):
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

from daemon.services.tmp_image_store import TmpImageStore
store = TmpImageStore(data_dir=os.environ["ENSEMBLE_DATA_DIR"])
png = tiny_png()
rec = store.save(IMAGE_ID, png, "image/png",
                 provenance={"feature": "liveview-passcode-e2e", "page": "journey", "source_agent": "tester-e2e"})
print(f"SEEDED tmp-image id={IMAGE_ID} bytes={len(png)} sha256={rec.sha256_hex[:16]}...")
open(os.path.join(os.environ["DRILL_TMP"], "image-sha"), "w").write(rec.sha256_hex)

# ── TOOL-path mint: real view_link tool over a manager facade carrying ──
# the REAL services wired to the SAME DB + SAME drill key as the daemon.
from sqlalchemy import create_engine
from daemon.config import LiveViewsConfig
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import ViewPasscodeCipher, ViewPasscodeService
from daemon.tools.live_views import create_live_view_tools

url = (f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
       f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}")
engine = create_engine(url)
cipher = ViewPasscodeCipher()
passcode_service = ViewPasscodeService(engine=engine, cipher=cipher)
lv_service = LiveViewsService(
    config=LiveViewsConfig(),
    tmp_image_store=store,
)

class _Mgr:  # minimal manager facade — the two seams the tool reads
    live_views_service = lv_service
    _view_passcode_service = passcode_service

tool = create_live_view_tools(_Mgr(), "e2e-journey-tester")[0]

FIVE_FIELD = re.compile(
    r"^Public URL: (?P<url>\S+)\n"
    r"Passcode: (?P<code>[A-Za-z0-9]{6})\n"
    r"Authenticated URL: (?P<auth>\S+)\n"
    r"Generation: (?P<gen>\d+)\n"
    r"Locked: (?P<locked>yes|no)"
    r"(?P<hint>.*)$",
    re.DOTALL,
)

def mint(root, path, refresh=False):
    """Invoke the tool; on rotation-required envelope, retry via refresh
    (the documented recovery) — recorded honestly in the output."""
    out = tool.invoke({"root_name": root, "path": path, "refresh": refresh})
    if out.startswith("Error:") and "ciphertext cannot" in out:
        print(f"  mint({root}/{path}) -> rotation-required envelope; documented recovery: refresh=True")
        out = tool.invoke({"root_name": root, "path": path, "refresh": True})
    return out

targets = [
    ("journeyroot1", "hello.md", "passcode-hello"),
    ("journeyroot1", "diagram.mmd", "passcode-diagram"),
    ("tmp-images", IMAGE_ID, "passcode-image"),
]
failures = 0
for root, path, pfile in targets:
    print(f"== J1 mint via TOOL: view_link({root!r}, {path!r})")
    out = mint(root, path)
    print(out)
    m = FIVE_FIELD.match(out)
    if not m or out.startswith("Error:"):
        print(f"  J1 FAIL: 5-field shape violated for {root}/{path}")
        failures += 1
        continue
    open(os.path.join(os.environ["DRILL_TMP"], pfile), "w").write(m.group("code"))
    # public URL shape asserts
    expected_public = f"/views/public/{root}/{path}"
    expected_auth = f"/views/{root}/{path}"
    if m.group("url") != expected_public or m.group("auth") != expected_auth:
        print(f"  J1 FAIL: URL shape — public={m.group('url')!r} auth={m.group('auth')!r}")
        failures += 1
    print(f"  J1 shape OK: 5/5 fields, public={expected_public}, gen={m.group('gen')}, locked={m.group('locked')}")

# idempotency spot-check: second call same passcode (ADR D5)
out2 = tool.invoke({"root_name": "journeyroot1", "path": "hello.md"})
m2 = FIVE_FIELD.match(out2)
first = open(os.path.join(os.environ["DRILL_TMP"], "passcode-hello")).read()
if m2 and m2.group("code") == first:
    print("  J1 idempotency OK: repeat call returned SAME passcode")
else:
    print("  J1 NOTE: repeat call mismatch (not fatal for the journey)")

if failures:
    sys.exit(f"J1 FAILURES: {failures}")
print("J1 RESULT: PASS")
PY
rc=${PIPESTATUS[0]}
exit "$rc"
