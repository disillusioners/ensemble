#!/usr/bin/env python3
"""RELOAD SMOKE #40 — LEG (b): idle/enqueue leg.

Fresh instance ID_B (no turn). Upload PNG -> POST message with image_refs
(durable enqueue leg, expect 200/202) -> wait for wake turn -> GET /messages
must show the user message with ref surfaced in wire `images`.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8090"
SCRIPTS = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts")
OUT = SCRIPTS / "leg_b"
TRANSCRIPT = []


def emit(label, status, body, extra=None):
    rec = {"label": label, "http_status": status, "body": body}
    if extra:
        rec.update(extra)
    TRANSCRIPT.append(rec)
    print(f"\n=== {label} | HTTP {status} ===")
    s = body if isinstance(body, str) else json.dumps(body, indent=2)
    print(s[:1800])


def call(method, url, payload=None, timeout=60):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                body = json.loads(raw)
            except Exception:
                body = raw[:2000]
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            body = json.loads(raw)
        except Exception:
            body = raw[:2000]
        return e.code, body


def get_status(iid):
    st, body = call("GET", f"{DAEMON}/api/instances/{iid}")
    if st != 200:
        return None
    return body.get("status") or (body.get("instance") or {}).get("status")


t0 = time.time()

# 1. create fresh instance
st, body = call("POST", f"{DAEMON}/api/instances", {"agent_id": "experiencer"})
emit("1. create instance ID_B (experiencer)", st, body)
ID_B = body.get("instance_id")
print(f"\n>>> ID_B = {ID_B} status={body.get('status')}")
(OUT / "id_b.txt").parent.mkdir(parents=True, exist_ok=True)
(OUT / "id_b.txt").write_text(str(ID_B))

# 2. upload one PNG (batch-of-1)
b64 = base64.b64encode((SCRIPTS / "legB.png").read_bytes()).decode()
st, body = call("POST", f"{DAEMON}/api/tmp_images",
                {"images": [{"filename": "legB.png", "content_type": "image/png",
                             "data_base64": b64}]})
emit("2. upload legB.png (batch-of-1)", st, body)
ref = body["uploads"][0]["ref_url"]
print(f">>> ref = {ref}")

# 3. POST message with image_refs (idle instance -> durable enqueue leg)
st, body = call("POST", f"{DAEMON}/api/instances/{ID_B}/messages",
                {"content": "describe this when you wake", "image_refs": [ref]})
emit("3. POST image_refs message to IDLE instance (expect 200/202)", st, body,
     {"ref": ref})
enqueue_http = st

# 4. wait for wake + drain (<=120s)
deadline = time.time() + 120
transitions = []
final = None
while time.time() < deadline:
    sval = get_status(ID_B)
    if not transitions or transitions[-1][0] != sval:
        transitions.append((sval, round(time.time() - t0, 1)))
        print(f"  [{round(time.time()-t0,1)}s] status = {sval}")
    final = sval
    if sval and str(sval).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        if len(transitions) > 1 or time.time() - t0 > 20:
            break
    time.sleep(1.0)
print("transitions:", transitions)

# 5. GET /messages — union assertion
st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_B}/messages")
emit("5. GET /messages", st, {"count": len(msgs) if isinstance(msgs, list) else "?"})
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))

wire_imgs = None
user_hit = None
if isinstance(msgs, list):
    for m in msgs:
        if m.get("role") in ("user", "human") and "describe this when you wake" in (m.get("content") or ""):
            user_hit = m
            wire_imgs = m.get("images")
            print("\n--- wake user message on the wire ---")
            print(json.dumps({k: m.get(k) for k in
                              ("message_id", "role", "content", "images")}, indent=2)[:1500])
ok = isinstance(wire_imgs, list) and ref in [str(x) for x in wire_imgs]
print(f"\n### LEG-B UNION ASSERTION: ref in wire images -> "
      f"{'PASS' if ok else 'FAIL'} (images={wire_imgs})")

(OUT / "transcript.json").write_text(json.dumps(
    {"id_b": ID_B, "enqueue_http_status": enqueue_http, "final_status": final,
     "transitions": transitions, "union_ok": ok, "transcript": TRANSCRIPT}, indent=2))
print(f"\nID_B={ID_B} enqueue_http={enqueue_http} final={final} union_ok={ok}")
