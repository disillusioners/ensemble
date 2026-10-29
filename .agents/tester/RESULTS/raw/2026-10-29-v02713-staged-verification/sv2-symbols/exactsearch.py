import sys, re
sys.path.insert(0, '/tmp/sv2-symbols')
import analyze
pyz = analyze.load()
target = sys.argv[1]; scope = sys.argv[2] if len(sys.argv) > 2 else 'all'
hits = 0; mods = 0
for name in sorted(pyz.toc):
    if scope == 'daemon' and not name.startswith('daemon'): continue
    if pyz.toc[name][0] == 3: continue
    code = pyz.code(name)
    if code is None: continue
    mods += 1
    for qual, co in analyze.walk(code, name + '::'):
        for i, c in enumerate(co.co_consts):
            if isinstance(c, str) and c == target:
                hits += 1
                print(f"EXACT-HIT {name} | {qual} | const[{i}] (doc-pos={i==0}) | {c!r}")
print(f"--- scanned {mods} modules, exact-match hits for {target!r}: {hits}")
