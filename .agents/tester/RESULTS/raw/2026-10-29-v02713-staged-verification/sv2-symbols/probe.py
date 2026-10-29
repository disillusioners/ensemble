import sys, os
sys.path.insert(0, '/tmp/sv2-symbols')
import car, importlib.util
print("reader python:", sys.version.split()[0], "MAGIC:", importlib.util.MAGIC_NUMBER.hex())
bin_path = sys.argv[1]
arc = car.CArchive(bin_path)
print(f"archive: start=0x{arc.start:x} len={arc.archive_len} toc_off={arc.toc_off} toc_len={arc.toc_len} pyvers={arc.pyvers} pylib={arc.pylib}")
types = {}
for n, e in arc.entries.items():
    types[e['type']] = types.get(e['type'], 0) + 1
print("CArchive TOC type census:", dict(sorted(types.items())))
pyz_names = [n for n, e in arc.entries.items() if e['type'] == 'z']
print("PYZ entries:", pyz_names)
for n in pyz_names:
    e = arc.entries[n]
    print(f"  PYZ '{n}': dlen={e['dlen']} ulen={e['ulen']} flag={e['flag']}")
