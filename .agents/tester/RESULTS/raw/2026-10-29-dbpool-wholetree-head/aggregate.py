#!/usr/bin/env python3
"""Aggregate the HEAD-leg chunked corpus run into failed.head.txt + a totals summary.

ATTEMPT 2 (authoritative): /tmp/tdbp-wthead-120 — chunks run with
  timeout 1500 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
    -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT
    ENSEMBLE_SELF_ENV=dev HOME=$(mktemp -d) python -m pytest <ids> -q --tb=no -rfE --timeout=120
(method-matched to the BASE leg's /tmp/tdbp-wtbase/driver.sh).

Rules:
  * A chunk's results count ONLY if its log ends with a -q session summary
    (base-leg detection: tail -200 | grep -E "in [0-9]+(\\.[0-9]+)?s( ?\\(.+\\))?$").
  * A chunk WITHOUT a summary (killed/timeout/INTERNALERROR) is not evidence: its
    partial FAILED/ERROR lines are DISCARDED; split halves (rchunk_*) are used if
    they completed; otherwise the chunk is UNRESOLVED and its full id list is
    emitted verbatim for the byte-comm to classify.
  * failed.head.txt = deduplicated, sorted FAILED/ERROR short-summary lines.
"""
import hashlib
import json
import os
import re
import subprocess

OUT = "/tmp/tdbp-wthead-120"
CAT_RE = re.compile(r"([0-9]+) (failed|passed|errors?|skipped|xfailed|xpassed|warnings?)")


def summary_of(path):
    """Base-leg detection: last 'in N.NNs' line within the last 200 lines."""
    if not os.path.exists(path):
        return None
    try:
        tail = subprocess.run(["tail", "-200", path], capture_output=True, text=True).stdout
    except Exception:
        return None
    lines = [l for l in tail.splitlines() if re.search(r"in [0-9]+(\.[0-9]+)?s( ?\(.+\))?$", l)]
    return lines[-1] if lines else None


def counts_of(tally):
    counts = {}
    if not tally:
        return counts
    for num, cat in CAT_RE.findall(tally):
        cat = "error" if cat.startswith("error") else cat
        counts[cat] = counts.get(cat, 0) + int(num)
    return counts


def summary_lines(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = re.match(r"^(FAILED|ERROR) (\S+)", line.rstrip("\n"))
            if m:
                out.append(f"{m.group(1)} {m.group(2)}")
    return out


def main():
    chunk_files = sorted(f for f in os.listdir(OUT) if re.fullmatch(r"chunk_ids_\d{3}", f))
    counts = {}
    failed_lines = []
    unresolved = []
    per_chunk = []

    for cf in chunk_files:
        name = cf.replace("chunk_ids_", "chunk_")
        full = os.path.join(OUT, name + ".full.log")
        tally = summary_of(full)
        if tally:
            for k, v in counts_of(tally).items():
                counts[k] = counts.get(k, 0) + v
            failed_lines.extend(summary_lines(full))
            per_chunk.append({"chunk": name, "status": "OK", "tally": tally})
            continue
        # killed -> use completed split halves if present
        halves = sorted(f for f in os.listdir(OUT) if re.fullmatch(r"r" + re.escape(name) + r"_\d", f.replace(".full.log", "")))
        good = []
        for h in halves:
            ht = summary_of(os.path.join(OUT, h))
            if ht:
                good.append((h, ht))
        if good:
            for h, ht in good:
                for k, v in counts_of(ht).items():
                    counts[k] = counts.get(k, 0) + v
                failed_lines.extend(summary_lines(os.path.join(OUT, h)))
            per_chunk.append({"chunk": name, "status": "OK-SPLIT", "used": [g[0] for g in good]})
            continue
        # UNRESOLVED: emit the chunk's id list verbatim
        ids = []
        with open(os.path.join(OUT, cf)) as fh:
            ids = [l.strip() for l in fh if l.strip()]
        unresolved.append({"chunk": name, "ids": ids})
        per_chunk.append({"chunk": name, "status": "UNRESOLVED", "ids_count": len(ids)})

    uniq = sorted(set(failed_lines))
    failed_path = os.path.join(OUT, "failed.head.txt")
    with open(failed_path, "w") as fh:
        for line in uniq:
            fh.write(line + "\n")

    def sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for blk in iter(lambda: fh.read(65536), b""):
                h.update(blk)
        return h.hexdigest()

    accounted = sum(v for k, v in counts.items() if k != "warnings")
    result = {
        "attempt": "2 (--timeout=120, ENSEMBLE_SELF_ENV=dev — method-matched to BASE leg)",
        "chunks_total": len(chunk_files),
        "chunks_ok": sum(1 for p in per_chunk if p["status"] == "OK"),
        "chunks_ok_split": sum(1 for p in per_chunk if p["status"] == "OK-SPLIT"),
        "chunks_unresolved": len(unresolved),
        "unresolved_id_lists": unresolved,
        "totals": counts,
        "accounted_pfe_s_xf_xp": accounted,
        "expected_ids": 31084,
        "failed_error_unique": len(uniq),
        "failed_unique": len([l for l in uniq if l.startswith("FAILED ")]),
        "error_unique": len([l for l in uniq if l.startswith("ERROR ")]),
        "sha256_failed_head_txt": sha(failed_path),
        "sha256_collect_head_txt": sha(os.path.join(OUT, "collect.head.txt")),
        "per_chunk": per_chunk,
    }
    with open(os.path.join(OUT, "aggregate.head.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    printable = {k: v for k, v in result.items() if k != "per_chunk"}
    print(json.dumps(printable, indent=2))


if __name__ == "__main__":
    main()
