import sys, os, dis
sys.path.insert(0, '/tmp/sv2-symbols')
import analyze
pyz = analyze.load()
print("== every float const == -1.0 anywhere in compiled daemon.* (with location) ==")
hits = 0
for name in sorted(pyz.toc):
    if not name.startswith('daemon'): continue
    if pyz.toc[name][0] == 3: continue
    code = pyz.code(name)
    if code is None: continue
    for qual, co in analyze.walk(code, name + '::'):
        for i, c in enumerate(co.co_consts):
            if isinstance(c, float) and c == -1.0:
                hits += 1
                print(f"  float -1.0 const[{i}] in {qual}")
print(f"total float(-1.0) consts in daemon surface: {hits}")
print()
print("== module-level STORE_NAME bindings whose value const is float -1.0 ==")
def scan_module_level(modname, code):
    found = []
    instrs = list(dis.get_instructions(code))
    for j, ins in enumerate(instrs):
        if ins.opname == 'STORE_NAME':
            # look back for the value-producing LOAD_CONST
            k = j - 1
            while k >= 0 and instrs[k].opname in ('EXTENDED_ARG',):
                k -= 1
            if k >= 0 and instrs[k].opname in ('LOAD_CONST', 'LOAD_SMALL_INT'):
                val = instrs[k].argval
                if isinstance(val, float) and val == -1.0:
                    found.append(ins.argval)
    return found
total = 0
for name in sorted(pyz.toc):
    if not name.startswith('daemon'): continue
    if pyz.toc[name][0] == 3: continue
    code = pyz.code(name)
    if code is None: continue
    for tgt in scan_module_level(name, code):
        total += 1
        print(f"  {name}: STORE_NAME {tgt} <- LOAD_CONST -1.0")
print(f"total module-level -1.0 assignments in daemon surface: {total}")
