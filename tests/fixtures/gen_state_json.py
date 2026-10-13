#!/usr/bin/env python3
"""Generate a 100KB / 500-entry state.json fixture for the pre-change baseline.

Mirrors `journal_init`'s payload shape (current/previous/in_flight/
rollback_window_count/cooldown_until/quarantined/history) and pads the history
list to the requested entry count while keeping total bytes ≈ 100 KB.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def gen(path: Path, entries: int, target_bytes: int) -> None:
    base = {
        "current": "v9.9.9",
        "previous": "v9.9.8",
        "in_flight": None,
        "rollback_window_count": {"24h": 0, "window_start": None},
        "cooldown_until": None,
        "quarantined": [],
        "history": [],
    }
    # Pad history entries until total bytes ≥ target_bytes.
    detail_pad = "x"
    history: list[dict[str, str]] = []
    while len(history) < entries:
        history.append(
            {
                "ts": "2026-10-12T00:00:00Z",
                "event": "heartbeat",
                "detail": detail_pad * 200,
            }
        )
    # Approximate size: shrink/extend padding to hit target.
    payload = json.dumps({**base, "history": history}, separators=(",", ":"))
    if len(payload) < target_bytes:
        deficit = target_bytes - len(payload)
        # Stuff the last entry's detail with the remaining bytes.
        history[-1]["detail"] = detail_pad * (200 + deficit // len(detail_pad) + 10)
        payload = json.dumps({**base, "history": history}, separators=(",", ":"))
    if len(payload) > target_bytes * 1.2:
        # Shrink padding to fit.
        target_pad = max(1, (target_bytes - 1000) // entries)
        history = [
            {"ts": "2026-10-12T00:00:00Z", "event": "hb", "detail": "x" * target_pad}
            for _ in range(entries)
        ]
        payload = json.dumps({**base, "history": history}, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")
    print(f"wrote {path} entries={len(history)} bytes={len(payload)}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--entries", type=int, default=500)
    p.add_argument("--approx-bytes", type=int, default=100_000)
    args = p.parse_args()
    gen(Path(args.path), args.entries, args.approx_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
