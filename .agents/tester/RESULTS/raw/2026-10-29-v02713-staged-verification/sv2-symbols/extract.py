import sys, os, importlib.util
sys.path.insert(0, '/tmp/sv2-symbols')
import car
bin_path, out_pyz = sys.argv[1], sys.argv[2]
arc = car.CArchive(bin_path)
e = arc.entries['PYZ.pyz']
data = arc.read('PYZ.pyz')
open(out_pyz, 'wb').write(data)
print("wrote", out_pyz, len(data), "bytes")
p = car.PYZ(out_pyz)
print("PYZ pymagic:", p.pymagic.hex(), "== reader MAGIC:", importlib.util.MAGIC_NUMBER.hex(),
      "MATCH" if p.pymagic == importlib.util.MAGIC_NUMBER else "*** MISMATCH ***")
print("PYZ module count:", len(p.toc))
types = {}
for k, v in p.toc.items():
    types[v[0]] = types.get(v[0], 0) + 1
print("PYZ entry type census:", dict(sorted(types.items())))
daemon = sorted(k for k in p.toc if k == 'daemon' or k.startswith('daemon.'))
print("daemon.* entries in PYZ:", len(daemon))
print("=== CArchive non-PYZ python-bearing entries ===")
for n, ent in sorted(arc.entries.items()):
    if ent['type'] in ('m', 'M', 's', 'b', 'n'):
        print(f"  type={ent['type']} dlen={ent['dlen']:>9} {n}")
