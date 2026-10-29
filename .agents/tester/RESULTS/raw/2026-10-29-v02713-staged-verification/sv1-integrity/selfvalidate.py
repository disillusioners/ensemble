import json, hashlib, os
def sha256_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda: f.read(1<<20), b''): h.update(c)
    return h.hexdigest()

# Method self-validation: recompute tree hashes for a PRIOR release (v0.27.12, live)
# whose tree hashes are published in ITS OWN manifest. If our implementation reproduces
# those, the implementation is sound and the v0.27.13 PASS is trustworthy.
REL="/home/nea/agents-ensemble/releases/v0.27.12"
m=json.load(open(os.path.join(REL,"manifest.json")))
for tree in ("agents","frontend","plugins"):
    amap=m[f"{tree}_manifest"]; root=os.path.join(REL,tree)
    lines=[]; bad=0
    for rel,want in amap.items():
        p=os.path.join(root,rel)
        got=sha256_file(p) if os.path.isfile(p) else None
        if got!=want: bad+=1
        lines.append(f"{want}  {rel}\n")
    treehash=hashlib.sha256("".join(lines).encode()).hexdigest()
    print(f"{tree}: entries={len(amap)} per_file_mismatch={bad} "
          f"tree_recomputed={treehash} tree_published={m[f'{tree}_tree_sha256']} "
          f"match={treehash==m[f'{tree}_tree_sha256']}")
