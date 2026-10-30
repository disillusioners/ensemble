#!/usr/bin/env python3
"""journal_fixture_incident.py — deterministic INCIDENT-SHAPED journal fixture.

Builds the journal shape the 2026-10-28 promote preflight choked on:

    **455,783 bytes · 2,949 history entries · 2,775 of them `mcp_server_state`**
    (89 % of the file, written by daemon/mcp/init_guard.py between ceremonies)

The fixture is GENERATED, never copied from the live install: reading the
live `releases/state.json*` would put a live artifact on the test path, and
committing 455 KB of live journal into the repo is a hygiene violation
(phase0 P0-D5).  Generation is deterministic and the sha256 is asserted, so a
drifted generator is a LOUD failure rather than a silent measurement drift.

Usage:
    python3 tests/helpers/journal_fixture_incident.py --out /tmp/incident.json
    python3 tests/helpers/journal_fixture_incident.py --print-sha

The generator is also importable (`build()`), which is how the packs derive
the fixture at runtime without shelling out to python twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

TARGET_BYTES = 455783
TARGET_ENTRIES = 2949
TARGET_MCP_ENTRIES = 2775

# Filled in from the first deterministic run; asserted on every later run.
# (Recorded in the wave-1 evidence index: tests/fixtures/journal/INCIDENT.sha256)
EXPECTED_SHA256 = "06ebaac3eb9b881b9ff550c6adca591941158eaf87e5d6bc5a90ec98a2398c6c"

BASE_DETAILS = (
    "context7 startup failed: spawn npm exec @upstash/context7-mcp EAI_AGAIN getaddrinfo ENOTFOUND registry.npmjs.org",
    "context7 startup failed: handshake timeout after 30000ms",
    "context7 quarantined for boot after 3 consecutive failures",
)

OTHER_EVENTS = (
    "nonce_consumed",
    "sweep",
    "executor_still_running",
    "stage_freshness_override",
)


def _is_mcp(i: int) -> bool:
    """Bresenham spread: exactly TARGET_MCP_ENTRIES of TARGET_ENTRIES marks.

    A plain modulo would not hit the measured 2,775 / 2,949 ratio exactly; the
    ratio IS the fixture's identity (89 % of the bytes are mcp_server_state),
    so it is pinned rather than approximated.
    """
    return ((i + 1) * TARGET_MCP_ENTRIES) // TARGET_ENTRIES > (
        i * TARGET_MCP_ENTRIES
    ) // TARGET_ENTRIES


def _entry(i: int) -> dict[str, object]:
    if _is_mcp(i):
        event = "mcp_server_state"
        detail = BASE_DETAILS[i % len(BASE_DETAILS)].format(n=i)
    else:
        event = OTHER_EVENTS[i % len(OTHER_EVENTS)]
        detail = "operator/daemon event #%d in the between-ceremony window" % i
    # Fixed-width timestamps keep the byte count a pure function of i.
    day = 27 + (i // 1440)
    hour = (i // 60) % 24
    minute = i % 60
    second = (i * 7) % 60
    return {
        "ts": "2026-10-%02dT%02d:%02d:%02dZ" % (day, hour, minute, second),
        "event": event,
        "detail": detail,
    }


def _skeleton() -> tuple[dict[str, object], list[dict[str, object]]]:
    """The journal with EMPTY details -- the byte budget's fixed part."""
    history = []
    for i in range(TARGET_ENTRIES):
        if _is_mcp(i):
            event = "mcp_server_state"
        else:
            event = OTHER_EVENTS[i % len(OTHER_EVENTS)]
        # Fixed-width timestamps keep the byte count a pure function of i.
        history.append({
            "ts": "2026-10-%02dT%02d:%02d:%02dZ"
                  % (27 + (i // 1440), (i // 60) % 24, i % 60, (i * 7) % 60),
            "event": event,
            "detail": "",
        })
    doc = {
        "current": "v0.27.11",
        "previous": "v0.27.8",
        "in_flight": None,
        "rollback_window_count": {"24h": 1, "window_start": "2026-10-26T00:00:00Z"},
        "cooldown_until": None,
        "quarantined": [],
        "history": history,
    }
    return doc, history


def _serialise(doc: dict[str, object]) -> bytes:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build() -> bytes:
    """Return the incident-shaped journal bytes (exactly TARGET_BYTES long).

    The detail budget is DISTRIBUTED, not padded at the tail: the skeleton's
    size is measured, the remainder is spread over the 2,949 details
    (longest-first by one char for the first `rem` entries), and each detail
    is the realistic prefix truncated/padded to its share.  That makes the
    total exact by construction and stable across interpreters.
    """
    doc, history = _skeleton()
    skeleton_len = len(_serialise(doc))
    available = TARGET_BYTES - skeleton_len
    if available < 0:
        raise SystemExit(
            "generator overshoots: skeleton alone is %d > target %d"
            % (skeleton_len, TARGET_BYTES)
        )
    per, rem = divmod(available, TARGET_ENTRIES)
    for i, entry in enumerate(history):
        want = per + (1 if i < rem else 0)
        seed = BASE_DETAILS[i % len(BASE_DETAILS)]
        entry["detail"] = (seed + "." * want)[:want]
    raw = _serialise(doc)
    if len(raw) != TARGET_BYTES:  # pragma: no cover - defensive
        raise SystemExit("generator produced %d bytes, want %d" % (len(raw), TARGET_BYTES))
    return raw


def build_stats() -> dict[str, int | str]:
    raw = build()
    doc = json.loads(raw)
    hist = doc["history"]
    mcp = sum(1 for e in hist if e.get("event") == "mcp_server_state")
    return {
        "bytes": len(raw),
        "entries": len(hist),
        "mcp_entries": mcp,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--print-sha", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="assert the generated sha256 equals EXPECTED_SHA256")
    args = ap.parse_args(argv)
    raw = build()
    stats = build_stats()
    if args.print_sha:
        sys.stdout.write(stats["sha256"] + "\n")
        return 0
    if args.stats:
        sys.stdout.write(json.dumps(stats, sort_keys=True) + "\n")
        return 0
    if args.verify_only:
        if stats["entries"] != TARGET_ENTRIES or stats["mcp_entries"] != TARGET_MCP_ENTRIES:
            sys.stderr.write("incident fixture shape drift: %r\n" % (stats,))
            return 1
        if EXPECTED_SHA256 != "PLACEHOLDER" and stats["sha256"] != EXPECTED_SHA256:
            sys.stderr.write(
                "incident fixture sha256 drift: got %s want %s\n"
                % (stats["sha256"], EXPECTED_SHA256)
            )
            return 1
        sys.stdout.write("incident fixture OK: %s\n" % json.dumps(stats, sort_keys=True))
        return 0
    if not args.out:
        ap.error("--out is required (or use --print-sha / --stats / --verify-only)")
    with open(args.out, "wb") as fh:
        fh.write(raw)
    sys.stdout.write("%s\n" % stats["sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
