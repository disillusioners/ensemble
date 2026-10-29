#!/usr/bin/env python3
"""FM4: aggregate chunk summaries + extract FAILED/ERROR ids from all chunk logs.

Mirrors /tmp/tdbp-wtbase/aggregate.py + extract_failures.py (byte-compatible
id extraction: longest-prefix match against the chunk's id list).
Writes: failed.fmwt.raw.tsv, failed.fmwt.txt (bare sorted dedup ids),
aggregate.fmwt.summary.txt.
"""
import collections, glob, hashlib, os, re, subprocess

OUT = "/tmp/tdbp-fm4"
WT = "/home/nea/ensemble-src-wt-tdbp-fmwt"
KEYS = ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed", "warnings")

# ---- chunk status from driver.status ----
rows = []
with open(f"{OUT}/driver.status") as fh:
    for line in fh:
        m = re.match(r"^(OK|OK-SUB)\s+(\S+)\s+rc=(\d+)\s+(\d+)s\s+ids=(\d+)\s+\[(.+?)\]", line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3), int(m.group(4)), int(m.group(5)), m.group(6)))

totals = collections.Counter()
wall_total = 0
ids_total = 0
per_chunk = []
for kind_row, name, rc, wall, n, summary in rows:
    c = collections.Counter()
    for k in KEYS:
        for mm in re.finditer(rf"(\d+) {k}\b", summary):
            c[k] += int(mm.group(1))
    errs = c.pop("error", 0) + c.pop("errors", 0)
    c["errors"] = errs
    totals.update(c)
    wall_total += wall
    ids_total += n
    per_chunk.append((name, rc, wall, n, summary))

accounted = (totals["passed"] + totals["failed"] + totals["errors"] +
             totals["skipped"] + totals["xfailed"] + totals["xpassed"])

# ---- failure extraction from every chunk log ----
def extract(ids_file, log_file):
    ids = set()
    with open(ids_file, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.strip():
                ids.add(line)
    res = []
    with open(log_file, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = re.match(r"^(FAILED|ERROR) (.*)$", line)
            if not m:
                continue
            kind, rest = m.group(1), m.group(2)
            best = None
            for i in range(len(rest), 0, -1):
                cand = rest[:i]
                if cand in ids and (i == len(rest) or rest[i:i + 3] == " - "):
                    best = cand
                    break
            res.append((kind, rest.strip(), "" if best else "UNPARSED", best or rest.strip()))
    return res

raw = []
seen = {}
unparsed = []
for name, rc, wall, n, summary in per_chunk:
    log = f"{OUT}/{name}.full.log"
    if not os.path.exists(log):
        continue
    idsf = f"{OUT}/chunk_{name.split('_')[1]}.ids" if False else None
    # map chunk_NNN -> chunk_ids_NNN ; rchunk_NNN_X -> ids_chunk_NNN_X
    if name.startswith("chunk_"):
        idsf = f"{OUT}/chunk_ids_{name[6:]}"
    else:  # rchunk_NNN_X <- ids_chunk_NNN_X
        idsf = f"{OUT}/ids_chunk_{name[7:]}"
    if not os.path.exists(idsf):
        continue
    for kind, rest, note, ident in extract(idsf, log):
        raw.append((kind, ident, note))
        if note == "UNPARSED":
            unparsed.append((kind, rest))
        else:
            seen.setdefault(ident, kind)

failed_sorted = sorted(seen)
with open(f"{OUT}/failed.fmwt.raw.tsv", "w") as fh:
    for kind, ident, note in raw:
        fh.write(f"{kind}\t{ident}\t{note}\n")
with open(f"{OUT}/failed.fmwt.txt", "w") as fh:
    fh.write("\n".join(failed_sorted) + ("\n" if failed_sorted else ""))

kinds = collections.Counter(seen.values())

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(65536), b""):
            h.update(b)
    return h.hexdigest()

lines = []
lines.append("=== FM4 whole-tree leg — merged tree 6947cc2b8 — aggregate summary ===")
lines.append(f"worktree: {WT}")
lines.append("interpreter: /home/nea/ensemble-src/.venv/bin/python (pytest 9.0.2)")
lines.append("census: 31150 collected (of 32259; 1109 deselected) — house --ignore x2 at census")
lines.append(f"ids_executed: {ids_total}   accounted(p+f+e+s+xf+xp): {accounted}   delta: {ids_total - accounted}")
lines.append("")
lines.append("--- execution totals (sum of per-chunk -q summaries) ---")
for k in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "warnings"):
    lines.append(f"  {k:<10}: {totals[k]}")
lines.append("")
lines.append("--- failed/error set ---")
lines.append(f"  unique ids: {len(failed_sorted)}   kinds: {dict(kinds)}   UNPARSED: {len(unparsed)}")
lines.append(f"  sha256(failed.fmwt.txt): {sha(f'{OUT}/failed.fmwt.txt')}")
lines.append(f"  sha256(ids.fmwt.txt): {sha(f'{OUT}/ids.fmwt.txt')}")
lines.append(f"  sha256(collect.fmwt.txt): {sha(f'{OUT}/collect.fmwt.txt')}")
lines.append("")
lines.append(f"--- {len(per_chunk)} chunk attempt rows; sum of chunk walls: {wall_total}s = {wall_total/3600:.2f}h ---")
for name, rc, wall, n, summary in per_chunk:
    lines.append(f"  {name:<12} rc={rc:<4} wall={wall:>5}s ids={n:<4} {summary}")

with open(f"{OUT}/aggregate.fmwt.summary.txt", "w") as fh:
    fh.write("\n".join(lines) + "\n")

print(f"chunks={len(per_chunk)} ids={ids_total} accounted={accounted} delta={ids_total-accounted}")
print(f"totals: {dict(totals)}")
print(f"unique failed/error ids: {len(failed_sorted)}  kinds={dict(kinds)}  unparsed={len(unparsed)}")
if unparsed:
    print("UNPARSED (investigate):")
    for k, r in unparsed[:20]:
        print(f"  {k} {r}")
print(f"sha256(failed.fmwt.txt) = {sha(f'{OUT}/failed.fmwt.txt')}")
