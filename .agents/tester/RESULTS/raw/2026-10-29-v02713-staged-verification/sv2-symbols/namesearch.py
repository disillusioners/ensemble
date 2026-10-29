import sys, re
sys.path.insert(0, '/tmp/sv2-symbols')
import car, analyze
pyz = analyze.load()
pat = re.compile(sys.argv[1]); scope = sys.argv[2] if len(sys.argv) > 2 else 'daemon'
mode = sys.argv[3] if len(sys.argv) > 3 else 'names'  # names|varnames|all
for name in sorted(pyz.toc):
    if scope == 'daemon' and not name.startswith('daemon'): continue
    if pyz.toc[name][0] == 3: continue
    code = pyz.code(name)
    if code is None: continue
    for qual, co in analyze.walk(code, name + '::'):
        pools = []
        if mode in ('names', 'all'): pools.append(('names', co.co_names))
        if mode in ('varnames', 'all'): pools.append(('varnames', co.co_varnames))
        for what, seq in pools:
            for item in seq:
                if pat.search(item):
                    print(f"{what[:-1]}:{item!r}  in  {qual}")
