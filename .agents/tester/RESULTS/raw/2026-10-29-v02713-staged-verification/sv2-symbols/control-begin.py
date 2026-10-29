#!/usr/bin/env python3
"""BEGIN-const CONTROL ARTIFACTS for the v0.27.13 staged verification (SV2).

One-way positive controls: the lane's F3 fix removes the literal 'BEGIN' from
the compiled daemon surface. The two OLDER releases must still contain it,
otherwise the '0 hits' observation on the staged artifact is vacuous.

  STAGED   v0.27.13  -> expect 0 exact-const 'BEGIN' in daemon.*
  CONTROL  v0.27.12  -> expect >=1 (fix not yet in that release)
  CONTROL  v0.27.11  -> expect >=1 (the lane base, 19fc11c8b)

Read-only. Same scan routine as the lane's exactsearch.py (analyze.all_string_consts).
"""
import sys
sys.path.insert(0, '/tmp/sv2-symbols')
import analyze

TARGETS = [
    ('STAGED  v0.27.13 (b1183a637)', '/tmp/sv2-symbols/pyz.bin'),
    ('CONTROL v0.27.12 (79bf44936)', '/tmp/sv2-symbols/pyz_v02712.bin'),
    ('CONTROL v0.27.11 (19fc11c8b)', '/tmp/sv2-symbols/pyz_v02711.bin'),
]

for label, path in TARGETS:
    analyze.PYZ = path
    pyz = analyze.load()
    mods = sum(1 for n in pyz.toc
               if n.startswith('daemon') and pyz.toc[n][0] != 3)
    hits = [(m, q, i, d, s) for m, q, i, d, s in analyze.all_string_consts(pyz, 'daemon')
            if s == 'BEGIN']
    print(f"=== {label} ===")
    print(f"    source pyz : {path}")
    print(f"    daemon.* modules scanned : {mods}")
    print(f"    exact-const 'BEGIN' hits : {len(hits)}")
    for m, q, i, d, s in hits:
        print(f"      EXACT-HIT {m} | {q} | const[{i}] (doc-pos={i == 0}) | {s!r}")
    print()

print("CONTROL VERDICT: staged=0 hits AND both older releases >=1 hit => "
      "the 'BEGIN'-absent observation is NON-VACUOUS.")
