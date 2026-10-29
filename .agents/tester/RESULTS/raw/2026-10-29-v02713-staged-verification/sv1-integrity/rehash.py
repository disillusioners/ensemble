import json, hashlib, os, sys

REL = "/home/nea/agents-ensemble/releases/v0.27.13"
m = json.load(open(os.path.join(REL, "manifest.json")))

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

total_checked = 0
mismatches = []
missing = []
tree_results = {}

for tree in ("agents", "frontend", "plugins"):
    amap = m[f"{tree}_manifest"]
    root = os.path.join(REL, tree)
    for rel, want in amap.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            missing.append(f"{tree}/{rel}")
            continue
        got = sha256_file(p)
        total_checked += 1
        if got != want:
            mismatches.append(f"{tree}/{rel}: manifest={want} disk={got}")
    # reconstruct tree hash: lines "sha  path" in map insertion order == _tree_manifest order
    blob = "".join(f"{sha}  {rel}\n" for rel, sha in amap.items()).encode()
    tree_results[tree] = hashlib.sha256(blob).hexdigest()

print(f"entries_checked={total_checked}")
print(f"mismatches={len(mismatches)}")
for x in mismatches[:20]: print("  MISMATCH", x)
print(f"missing={len(missing)}")
for x in missing[:20]: print("  MISSING", x)
for t in tree_results:
    want = m[f"{t}_tree_sha256"]
    got = tree_results[t]
    print(f"tree {t}: manifest={want} recomputed={got} match={want==got}")

# extra: scalar fields
for f, p in (("launcher_sha256", "launcher.sh"), ("config_sha256", "config.yaml"), ("binary_sha256", "ensemble-prod")):
    want = m[f]
    got = sha256_file(os.path.join(REL, p))
    print(f"scalar {p}: manifest={want} disk={got} match={want==got}")

# stray files on disk not in manifest (payload census)
for tree in ("agents", "frontend", "plugins"):
    amap = m[f"{tree}_manifest"]
    on_disk = set()
    root = os.path.join(REL, tree)
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            on_disk.add(os.path.relpath(os.path.join(dp, fn), root))
    extra = sorted(on_disk - set(amap))
    absent = sorted(set(amap) - on_disk)
    print(f"census {tree}: disk={len(on_disk)} manifest={len(amap)} extra_on_disk={len(extra)} in_manifest_not_on_disk={len(absent)}")
    for x in extra[:10]: print("  EXTRA", tree, x)
