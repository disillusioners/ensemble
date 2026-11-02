#!/usr/bin/env python3
"""Re-derive the adjudication verdict from the PERSISTED matrix.

The matrix (one row per side × id × run) is the evidence; the verdict is
a pure function of it.  Re-deriving from the matrix — rather than
re-running the legs — keeps the classification and the evidence in one
place: the verdict can never drift from the rows that produced it.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

OUT = Path(
    "/home/nea/ensemble-src-wt-central-metrics-v1/.agents/shared/planning/"
    "central-metrics-v1/evidence/p3-adjudication"
)
MATRIX = OUT / "adjudication-matrix.tsv"


def main() -> int:
    rows = MATRIX.read_text(encoding="utf-8").splitlines()
    header = rows[0].split("\t")
    index = {name: position for position, name in enumerate(header)}
    per_id: dict[tuple[str, str], dict[str, list[int]]] = {}
    for line in rows[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        key = (parts[index["side"]], parts[index["node_id"]])
        entry = per_id.setdefault(key, {"runs": [], "which": parts[index["set"]]})
        entry["runs"].append(int(parts[index["red"]]))

    lanes: dict[str, dict] = {}
    for (side, node), entry in per_id.items():
        lanes.setdefault(node, {"set": entry["which"], "base": [], "head": []})
        lanes[node]["base" if side == "BASE" else "head"].extend(entry["runs"])

    verdicts: dict[str, dict] = {}
    for node, data in lanes.items():
        base_reds = sum(data["base"])
        head_reds = sum(data["head"])
        if head_reds and not base_reds:
            classification = "LANE-CANDIDATE (red on HEAD only — a real regression)"
        elif base_reds and not head_reds:
            classification = (
                "RESOLVED-AT-HEAD (red on BASE only — the base-only side of the diff)"
            )
        elif base_reds and head_reds:
            classification = "PRE-EXISTING (red on BOTH sides)"
        else:
            classification = "ORDER/LOAD-SENSITIVE (green on both sides in isolation)"
        verdicts[node] = {
            "classification": classification,
            "set": data["set"],
            "base_reds": base_reds,
            "head_reds": head_reds,
            "base_runs": len(data["base"]),
            "head_runs": len(data["head"]),
        }

    summary = Counter(v["classification"] for v in verdicts.values())
    lane_candidates = sorted(
        node for node, v in verdicts.items()
        if v["classification"].startswith("LANE-CANDIDATE")
    )
    (OUT / "adjudication-verdict.json").write_text(
        json.dumps(
            {
                "rounds_per_side": 3,
                "ids": len(verdicts),
                "base_only_ids": sum(1 for v in verdicts.values() if v["set"] == "base-only"),
                "head_only_ids": sum(1 for v in verdicts.values() if v["set"] == "head-only"),
                "summary": dict(summary),
                "lane_candidates": lane_candidates,
                "lanes": verdicts,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    lines = ["set\tnode_id\tbase_reds\tbase_runs\thead_reds\thead_runs\tclassification"]
    for node in sorted(verdicts, key=lambda n: (verdicts[n]["set"], n)):
        v = verdicts[node]
        lines.append(
            f"{v['set']}\t{node}\t{v['base_reds']}\t{v['base_runs']}\t"
            f"{v['head_reds']}\t{v['head_runs']}\t{v['classification']}"
        )
    (OUT / "adjudication-verdict.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("=== the per-id adjudication summary (re-derived from the matrix) ===")
    for key, count in sorted(summary.items()):
        print(f"  {count:3d}  {key}")
    print("")
    print(f"LANE CANDIDATES (red on HEAD only, green on BASE): {len(lane_candidates)}")
    for node in lane_candidates:
        print(f"  {node}")
    print("")
    resolved = [
        n for n, v in verdicts.items() if v["classification"].startswith("RESOLVED-AT-HEAD")
    ]
    print(f"RESOLVED-AT-HEAD (the base-only side of the diff): {len(resolved)}")
    for node in resolved:
        print(f"  {node}  (BASE {verdicts[node]['base_reds']}/{verdicts[node]['base_runs']} red)")
    print("")
    print(f"the verdict TSV:  {OUT / 'adjudication-verdict.tsv'}")
    print(f"the verdict JSON: {OUT / 'adjudication-verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
