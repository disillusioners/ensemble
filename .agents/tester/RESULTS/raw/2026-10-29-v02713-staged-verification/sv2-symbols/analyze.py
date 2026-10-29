#!/usr/bin/env python3
"""SV2 compiled-artifact analyzer. Read-only. Run with python3.14 -B."""
import sys, os, re, marshal, zlib, importlib.util, dis, io, json
sys.path.insert(0, '/tmp/sv2-symbols')
import car

PYZ = os.environ.get('SV2_PYZ', '/tmp/sv2-symbols/pyz.bin')

def load():
    return car.PYZ(PYZ)

def walk(code, qual='<module>'):
    """Yield (qual, code) for module + every nested code object."""
    yield qual, code
    for c in code.co_consts:
        if hasattr(c, 'co_code'):
            yield from walk(c, f"{qual}.{c.co_name}")

def all_string_consts(pyz, scope='daemon'):
    """Yield (module, qual, idx, is_docstring_pos, value) for every str const."""
    for name in sorted(pyz.toc):
        if scope == 'daemon' and not (name == 'daemon' or name.startswith('daemon.')):
            continue
        typ = pyz.toc[name][0]
        if typ == 3:  # namespace pkg
            continue
        try:
            code = pyz.code(name)
        except Exception as e:
            print(f"WARN unmarshal {name}: {e}", file=sys.stderr); continue
        if code is None: continue
        for qual, co in walk(code, name + '::'):
            for i, c in enumerate(co.co_consts):
                if isinstance(c, str):
                    yield name, qual, i, (i == 0), c

def main():
    cmd = sys.argv[1]
    pyz = load()
    if cmd == 'anchor':
        for mod in ('daemon',):
            code = pyz.code(mod)
            print(f"module {mod!r} co_filename={code.co_filename!r}")
            print(f"  __version__ const candidates:", [c for c in code.co_consts if isinstance(c, str) and '0.27' in c])
    elif cmd == 'filelist':
        # unique co_filename per daemon module — provenance of build
        seen = {}
        for name in sorted(pyz.toc):
            if not name.startswith('daemon'): continue
            if pyz.toc[name][0] == 3: continue
            code = pyz.code(name)
            if code is None: continue
            seen.setdefault(os.path.dirname(code.co_filename), []).append(name)
        for d, mods in seen.items():
            print(f"{d}  ({len(mods)} modules)")
    elif cmd == 'strsearch':
        pat = sys.argv[2]
        scope = sys.argv[3] if len(sys.argv) > 3 else 'daemon'
        rx = re.compile(pat)
        n = 0
        for mod, qual, idx, isdoc, s in all_string_consts(pyz, scope):
            if rx.search(s):
                n += 1
                tag = 'DOC' if isdoc else 'STR'
                print(f"[{tag}] {mod} | {qual} | const[{idx}] | {s!r}")
        print(f"--- total hits: {n} (scope={scope}, pattern={pat!r})")
    elif cmd == 'dismod':
        mod = sys.argv[2]
        filt = sys.argv[3] if len(sys.argv) > 3 else None
        code = pyz.code(mod)
        for qual, co in walk(code, mod + '::'):
            if filt and not re.search(filt, qual):
                continue
            print(f"===== {qual} (co_filename={co.co_filename}) =====")
            buf = io.StringIO()
            dis.dis(co, file=buf, depth=0)
            print(buf.getvalue())
    elif cmd == 'findcode':
        # find code objects (module+qual) whose name/varnames/names match
        pat = sys.argv[2]
        rx = re.compile(pat)
        for name in sorted(pyz.toc):
            if not (name == 'daemon' or name.startswith('daemon.')): continue
            if pyz.toc[name][0] == 3: continue
            code = pyz.code(name)
            if code is None: continue
            for qual, co in walk(code, name + '::'):
                hay = ' '.join([co.co_name] + list(co.co_varnames) + list(co.co_names))
                if rx.search(hay):
                    print(f"{qual}  varnames={co.co_varnames} names={co.co_names[:12]}{'...' if len(co.co_names)>12 else ''}")
    else:
        print("unknown cmd"); sys.exit(2)

if __name__ == '__main__':
    main()
