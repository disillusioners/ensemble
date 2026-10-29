#!/usr/bin/env python3
"""Reader cross-validation for the v0.27.13 staged verification (SV2).

Two INDEPENDENT implementations read the SAME artifact:
  NATIVE  : PyInstaller 6.19.0 (certified venv, via PYTHONPATH) CArchiveReader
            -> open_embedded_archive('PYZ.pyz') -> ZlibArchiveReader
  CUSTOM  : the lane's own pure-python reader (/tmp/sv2-symbols/car.py)

Compares: TOC key sets, per-key TOC tuples, and marshal.dumps(code) for the
9 key daemon modules. Read-only; writes nothing but this report.
"""
import sys, marshal, hashlib, importlib.util
sys.path.insert(0, '/tmp/sv2-symbols')
import car

BIN = '/home/nea/agents-ensemble/releases/v0.27.13/ensemble-prod'
PYZ = '/tmp/sv2-symbols/pyz.bin'

KEY9 = [
    'daemon.services.readiness',
    'daemon.manager',
    'daemon.constants',
    'daemon.repositories.factory',
    'daemon.persistence',
    'daemon.repositories.mission_record.repository',
    'daemon.services.mission_record_service',
    'daemon.tools.ens_db_tools',
    'daemon.services.skill_evolution_service',
]

print("interpreter:", sys.version.split()[0], "marshal MAGIC:", importlib.util.MAGIC_NUMBER.hex())
try:
    import PyInstaller
    print("PyInstaller:", PyInstaller.__version__, "(native reader)")
except Exception as e:                                    # pragma: no cover
    print("PyInstaller import FAILED:", type(e).__name__, e); raise

from PyInstaller.archive.readers import CArchiveReader

print("\n=== NATIVE reader: CArchiveReader(binary).open_embedded_archive('PYZ.pyz') ===")
car_native = CArchiveReader(BIN)
print("archive type:", car_native.__class__.__name__, "entries:", len(car_native.toc))
nat = car_native.open_embedded_archive('PYZ.pyz')
print("embedded archive type:", nat.__class__.__name__)
nat_toc = nat.toc
print("native PYZ module count:", len(nat_toc))

print("\n=== CUSTOM reader: car.PYZ(pyz.bin) ===")
cus = car.PYZ(PYZ)
print("custom PYZ pymagic:", cus.pymagic.hex(), "== running MAGIC:",
      "MATCH" if cus.pymagic == importlib.util.MAGIC_NUMBER else "*** MISMATCH ***")
print("custom PYZ module count:", len(cus.toc))

nk, ck = set(nat_toc), set(cus.toc)
print("\n=== TOC KEY SET ===")
print("native keys:", len(nk), "custom keys:", len(ck))
print("key sets identical:", nk == ck)
only_nat = sorted(nk - ck)
only_cus = sorted(ck - nk)
print("native-only:", len(only_nat), only_nat[:5])
print("custom-only:", len(only_cus), only_cus[:5])

print("\n=== TOC TUPLES (per shared key) ===")
diff = []
for k in sorted(nk & ck):
    if tuple(nat_toc[k]) != tuple(cus.toc[k]):
        diff.append((k, tuple(nat_toc[k]), tuple(cus.toc[k])))
print("shared keys:", len(nk & ck), "| differing TOC tuples:", len(diff))
for d in diff[:10]:
    print("   DIFF", d)

print("\n=== CODE OBJECT EQUALITY (marshal.dumps) — 9 key daemon modules ===")
allsame = True
for k in KEY9:
    in_n, in_c = k in nat_toc, k in cus.toc
    if not (in_n and in_c):
        print(f"  {k}: PRESENT native={in_n} custom={in_c}  (SKIP)")
        allsame = False
        continue
    cn = nat.extract(k)
    cc = cus.code(k)
    bn, bc = marshal.dumps(cn), marshal.dumps(cc)
    same = bn == bc
    allsame = allsame and same
    print(f"  {k}: native_len={len(bn)} custom_len={len(bc)} identical={same} "
          f"sha_native={hashlib.sha256(bn).hexdigest()[:16]} "
          f"sha_custom={hashlib.sha256(bc).hexdigest()[:16]}")
print("all 9 key modules byte-identical across readers:", allsame)

print("\n=== VERDICT ===")
ok = (nk == ck) and (len(diff) == 0) and allsame
print("READER CROSS-VALIDATION:", "PASS" if ok else "FAIL",
      f"(keyset_equal={nk == ck}, toc_tuple_diffs={len(diff)}, code_identical={allsame})")
sys.exit(0 if ok else 1)
