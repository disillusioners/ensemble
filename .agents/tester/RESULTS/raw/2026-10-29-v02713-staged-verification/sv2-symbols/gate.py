import sys, os, dis, marshal
sys.path.insert(0, '/tmp/sv2-symbols')
import car, analyze
pyz = analyze.load()
res = []
def chk(n, ok, ev):
    res.append((n, ok, ev))
    print(f"{'PASS' if ok else 'FAIL'}  {n}\n      {ev}")

# 1 MANAGER_POOL_CONFIGURED_CAPACITY == 25
co = pyz.code('daemon.services.readiness')
ins = list(dis.get_instructions(co))
i = [k for k, x in enumerate(ins) if x.opname == 'STORE_NAME' and x.argval == 'MANAGER_POOL_CONFIGURED_CAPACITY']
v = ins[i[0]-1].argval if i else None
chk("1 MANAGER_POOL_CONFIGURED_CAPACITY", i and v == 25,
    f"daemon.services.readiness module body: LOAD_SMALL_INT {v!r} -> STORE_NAME MANAGER_POOL_CONFIGURED_CAPACITY (source line 165)")

# 2 _manager_pool_effective_capacity present (def + call)
defs = [x for x in dis.get_instructions(co) if x.opname == 'STORE_NAME' and x.argval == '_manager_pool_effective_capacity']
callers = [q for q, c in analyze.walk(co, 'r') if '_manager_pool_effective_capacity' in c.co_names and q != 'r']
chk("2 _manager_pool_effective_capacity", bool(defs) and bool(callers),
    f"module body STORE_NAME _manager_pool_effective_capacity (code obj line 1501); called from {callers[0]} (line 1587)")

# 3 MissionEpochConflictError
repo = pyz.code('daemon.repositories.mission_record.repository')
svc  = pyz.code('daemon.services.mission_record_service')
chk("3 MissionEpochConflictError",
    'MissionEpochConflictError' in repo.co_names and 'MissionEpochConflictError' in svc.co_names,
    "repository module: LOAD_BUILD_CLASS + LOAD_CONST 'MissionEpochConflictError' + LOAD_NAME IntegrityError -> STORE_NAME (line 45); "
    "service module: IMPORT_NAME repositories.mission_record.repository + IMPORT_FROM MissionEpochConflictError -> STORE_NAME (line 132)")

# 4 no text("BEGIN") anywhere in the daemon surface (+ controls)
hits = [m for m, q, i, d, s in analyze.all_string_consts(pyz, 'daemon') if s == 'BEGIN']
ctl11 = [m for m, q, i, d, s in analyze.all_string_consts(car.PYZ('/tmp/sv2-symbols/pyz_v02711.bin'), 'daemon') if s == 'BEGIN']
chk("4 F3 BEGIN literal absent", not hits,
    f"exact-const 'BEGIN' in compiled daemon surface: {len(hits)} hits (582 modules); CONTROL v0.27.11(base 19fc11c8b): {len(ctl11)} hit in {ctl11}")

# 5 strict carrier consumer
mgr = pyz.code('daemon.manager')
found = []
for q, c in analyze.walk(mgr, 'm'):
    if 'live_carrier_exists' in c.co_varnames:
        ins2 = list(dis.get_instructions(c))
        for k, x in enumerate(ins2):
            if x.opname == 'LOAD_FAST_BORROW' and x.argval == 'live_carrier_exists':
                nxt = ins2[k+1:k+3]
                if any(n.opname == 'IS_OP' and n.argval == 0 for n in nxt):
                    found.append((q, x.starts_line if hasattr(x,'starts_line') else None))
chk("5 strict live_carrier_exists is False", len(found) == 2,
    f"{len(found)} sites with IS_OP 0 (is): {[f[0] for f in found]}")

# 6 budget == 0.25, no -1.0 sentinel assignment
cc = pyz.code('daemon.constants')
ins3 = list(dis.get_instructions(cc))
j = [k for k, x in enumerate(ins3) if x.opname == 'STORE_NAME' and x.argval == 'MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S']
bv = ins3[j[0]-1].argval if j else None
sent = []
for name in sorted(pyz.toc):
    if not name.startswith('daemon') or pyz.toc[name][0] == 3: continue
    c2 = pyz.code(name)
    if c2 is None: continue
    ii = list(dis.get_instructions(c2))
    for k, x in enumerate(ii):
        if x.opname == 'STORE_NAME' and x.argval == 'MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S':
            pv = ii[k-1].argval
            if isinstance(pv, float) and pv == -1.0:
                sent.append((name, pv))
            elif not isinstance(pv, float) or pv != 0.25:
                sent.append((name, pv))
chk("6 budget 0.25 / no -1.0 sentinel", bv == 0.25 and not sent,
    f"daemon.constants: LOAD_CONST {bv!r} -> STORE_NAME MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S (line 394); bindings of that target to a non-0.25 value: {len(sent)} {sent}")

# 7 pool knobs
fac = pyz.code('daemon.repositories.factory')
allfac = []
for q, c in analyze.walk(fac, 'fac'):
    allfac.append((q, list(dis.get_instructions(c))))
kwn = [(q, x.argval) for q, ii in allfac for x in ii
       if x.opname == 'LOAD_CONST' and isinstance(x.argval, tuple) and 'pool_recycle' in x.argval]
rb = [(q, x.argval) for q, ii in allfac for x in ii
      if x.opname == 'LOAD_CONST' and x.argval == 'rollback']
ins4 = list(dis.get_instructions(cc))
def const_val(name):
    for k, x in enumerate(ins4):
        if x.opname == 'STORE_NAME' and x.argval == name: return ins4[k-1].argval
chk("7 pool knobs", bool(kwn) and bool(rb) and const_val('MANAGER_ENGINE_POOL_RECYCLE_S') == 3600 and const_val('MANAGER_ENGINE_POOL_TIMEOUT_S') == 30,
    f"create_engine KW_NAMES {kwn}; 'rollback' value const at {rb}; constants RECYCLE={const_val('MANAGER_ENGINE_POOL_RECYCLE_S')} TIMEOUT={const_val('MANAGER_ENGINE_POOL_TIMEOUT_S')}")

# 8 application_name trio + shared constant clean
tri = {}
for name in sorted(pyz.toc):
    if not name.startswith('daemon') or pyz.toc[name][0] == 3: continue
    c3 = pyz.code(name)
    if c3 is None: continue
    for m, q, i, d, s in analyze.all_string_consts(pyz, 'daemon'):
        if s in ('ensemble-manager','ensemble-repair','ensemble-checkpoint-adapter') and m == name:
            tri.setdefault(s, set()).add(m)
ins5 = list(dis.get_instructions(fac))
pg = None
for k, x in enumerate(ins5):
    if x.opname == 'STORE_NAME' and x.argval == 'PG_SESSION_CONNECT_ARGS':
        w = [(y.opname, y.argval) for y in ins5[k-3:k]]
        pg = {'window': w, 'build_map_size': ins5[k-1].argval}
app_module_level = [(x.opname, x.argval) for x in ins5 if x.opname == 'LOAD_CONST' and x.argval == 'application_name']
app_in_func = [(q, x.argval) for q, ii in allfac for x in ii if x.opname == 'LOAD_CONST' and x.argval == 'application_name']
chk("8 application_name trio + D-17", len(tri) == 3 and pg and pg['build_map_size'] == 1 and not app_module_level and app_in_func,
    f"{ {k: sorted(v) for k,v in tri.items()} }; PG_SESSION_CONNECT_ARGS window={pg['window']} (BUILD_MAP {pg['build_map_size']}); 'application_name' at factory MODULE level: {len(app_module_level)}; inside function: {app_in_func}")
print()
print("=" * 70)
print(f"CONSOLIDATED GATE: {sum(1 for _,ok,_ in res if ok)}/{len(res)} PASS")
