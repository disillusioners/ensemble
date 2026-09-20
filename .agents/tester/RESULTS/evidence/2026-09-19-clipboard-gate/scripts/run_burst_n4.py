"""B9 FINAL MERGE GATE — CONCURRENCY N=4 clipboard-image burst.

4 fresh experiencer instances, 4 distinct PNGs (GATE-N1..N4), one
coordinated asyncio burst through ONE daemon (127.0.0.1:8090).
Proves no semaphore starvation: all convert, all turns complete.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8090"
HERE = os.path.dirname(os.path.abspath(__file__))
POLL_BOUND_S = 240.0
POLL_INTERVAL_S = 3.0
TERMINAL = {"completed", "error", "failed", "terminated"}
POST_TIMEOUT = 300.0  # each POST blocks on sync-in-POST VISION conversion


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def rel(t0: datetime) -> float:
    return round((now_utc() - t0).total_seconds(), 2)


async def main() -> int:
    out = {"phase": "setup", "created": [], "uploads": []}
    async with httpx.AsyncClient(timeout=POST_TIMEOUT) as c:
        # ── 1. create 4 fresh instances ─────────────────────────────
        ids: list[str] = []
        for _ in range(4):
            r = await c.post(f"{BASE}/api/instances", json={"agent_id": "experiencer"})
            body = r.json()
            if r.status_code not in (200, 201):
                print(json.dumps({"fatal": "instance create failed", "code": r.status_code, "body": body}))
                return 1
            ids.append(body["instance_id"])
        out["created"] = ids
        print("INSTANCE_IDS", json.dumps(ids), flush=True)

        # ── 2. pre-upload 4 images SEQUENTIALLY (not the target) ────
        refs: list[str] = []
        for i in range(1, 5):
            with open(os.path.join(HERE, f"gate_n{i}.png"), "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            r = await c.post(
                f"{BASE}/api/tmp_images",
                json={"images": [{"filename": f"gate_n{i}.png", "content_type": "image/png", "data_base64": b64}]},
            )
            if r.status_code != 200:
                print(json.dumps({"fatal": "upload failed", "k": i, "code": r.status_code, "body": r.text[:400]}))
                return 1
            refs.append(r.json()["uploads"][0]["ref_url"])
        out["uploads"] = refs
        print("IMAGE_REFS", json.dumps(refs), flush=True)

        # ── 3. mark T0 and fire the 4-send SIMULTANEOUS burst ───────
        t0 = now_utc()
        t0_plus7 = t0 + timedelta(hours=7)
        print("T0_UTC", t0.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), flush=True)
        print("T0_LOCAL+07", t0_plus7.strftime("%Y-%m-%dT%H:%M:%S+07:00"), flush=True)

        async def send(k: int) -> dict:
            iid, ref = ids[k - 1], refs[k - 1]
            t_s = now_utc()
            try:
                r = await c.post(
                    f"{BASE}/api/instances/{iid}/messages",
                    json={"content": "describe this image", "image_refs": [ref]},
                )
                t_e = now_utc()
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": r.text[:500]}
                return {
                    "k": k, "instance_id": iid, "ref": ref, "http_status": r.status_code,
                    "post_seconds": round((t_e - t_s).total_seconds(), 2), "body": body,
                }
            except Exception as exc:  # noqa: BLE001
                t_e = now_utc()
                return {
                    "k": k, "instance_id": iid, "ref": ref, "http_status": None,
                    "post_seconds": round((t_e - t_s).total_seconds(), 2),
                    "exception": f"{type(exc).__name__}: {exc}",
                }

        burst_t = now_utc()
        responses = await asyncio.gather(*(send(k) for k in range(1, 5)))
        for resp in responses:
            print("BURST_RESPONSE", json.dumps(resp), flush=True)
        print("BURST_WALL_S", round(rel(t0), 2), flush=True)

        # ── 4. poll all 4 until terminal, bound 240s total ──────────
        done: dict[int, dict] = {}
        pending = set(range(1, 5))
        deadline = time.monotonic() + POLL_BOUND_S
        while pending and time.monotonic() < deadline:
            async def probe(k: int) -> tuple[int, str]:
                r = await c.get(f"{BASE}/api/instances/{ids[k - 1]}")
                return k, r.json().get("status", "unknown")

            for k, st in await asyncio.gather(*(probe(k) for k in sorted(pending))):
                if st.lower() in TERMINAL:
                    done[k] = {"status": st, "completed_at_rel_s": rel(t0)}
                    pending.discard(k)
                    print("TERMINAL", json.dumps({"k": k, **done[k]}), flush=True)
            if pending:
                await asyncio.sleep(POLL_INTERVAL_S)
        for k in sorted(pending):
            print("TERMINAL", json.dumps({"k": k, "status": "POLL_BOUND_EXCEEDED", "completed_at_rel_s": rel(t0)}), flush=True)
            done[k] = {"status": "POLL_BOUND_EXCEEDED", "completed_at_rel_s": rel(t0)}

        summary = {
            "t0_utc": t0.isoformat(),
            "instance_ids": ids,
            "refs": refs,
            "burst": list(responses),
            "final": {str(k): done[k] for k in sorted(done)},
        }
        with open(os.path.join(HERE, "burst_n4_transcript.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print("TRANSCRIPT_SAVED", os.path.join(HERE, "burst_n4_transcript.json"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
