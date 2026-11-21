#!/usr/bin/env python
"""M2-A2: READ-ONLY PyInstaller archive audit of the shipped frozen binary.
Route per POK pyinst-01/02: CArchiveReader(binary).toc (names, not content),
PYZ -> ZlibArchiveReader (module-name census), pyc-magic == local MAGIC_NUMBER.
NEVER executes the binary. Read-only file access.
"""
import importlib.util
import json
import re
import sys

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

BIN = sys.argv[1] if len(sys.argv) > 1 else "/home/nea/agents-ensemble/current/ensemble-prod"

out = {"binary": BIN}
r = CArchiveReader(BIN)
toc = r.toc  # dict-like: name -> entry tuple/dict
names = list(toc.keys())
out["carchive_entry_count"] = len(names)

# 1) python runtime lib names (version + free-threading 't' abiflag marker)
libpython = sorted(n for n in names if re.match(r"^(lib)?python3", n) or "python3" in n.lower())
out["python_lib_entries"] = {n: toc[n] for n in libpython}
out["pyz_present"] = [n for n in names if n.endswith(".pyz")]

# 2) embedded PYZ module census
pyz_reader = None
for n in out["pyz_present"]:
    pyz_reader = r.open_embedded_archive(n)
    break
if pyz_reader is not None:
    import struct
    ptoc = list(pyz_reader.toc.keys())
    out["pyz_module_count"] = len(ptoc)
    # pymagic sits in the PYZ header right after the 4-byte PYZ magic
    with open(BIN, "rb") as fp:
        fp.seek(pyz_reader._start_offset + len(pyz_reader._PYZ_MAGIC_PATTERN))
        pymagic = fp.read(4)
    out["pyz_pymagic_hex"] = pymagic.hex()
    out["local_pyc_magic_hex"] = importlib.util.MAGIC_NUMBER.hex()
    out["pyz_magic_matches_local_interpreter"] = pymagic == importlib.util.MAGIC_NUMBER
    for probe in ("multiprocessing", "concurrent", "tiktoken", "orjson",
                  "pydantic", "daemon", "run_app", "uvicorn", "httpx", "encodings"):
        hits = sorted(m for m in ptoc if m == probe or m.startswith(probe + "."))
        out[f"pyz_probe::{probe}"] = {"count": len(hits), "sample": hits[:12]}
    mp_top = sorted(m for m in ptoc if m.startswith("multiprocessing"))
    out["multiprocessing_modules_full"] = mp_top

# 3) CArchive-level probes (frozen exe bootloader stuff, base_library.zip)
out["carchive_probe_base_library_zip"] = [n for n in names if "base_library" in n]
out["carchive_probe_spechint"] = sorted(n for n in names if n in ("run_app", "run_app.py", "ensemble-prod", "ensemble"))

print(json.dumps(out, indent=2, default=str))
