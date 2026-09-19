#!/usr/bin/env python3
"""EXP-1: plain-text injection control. Is stranding image_refs-specific or lane-general?"""
import json, sys, time, urllib.error, urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8090"
OUT = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts/exp1")
TR = []
def emit(label, status, body):
    TR.append({"label": label, "http_status": status, "body": body})
    print(f"\n=== {label} | HTTP {status} ===")
    print((body if isinstance(body, str) else json.dumps(body, indent=2))[:1500])

def call(method, url, payload=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw[:1] in '{["' else raw[:2000])
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw[:1] in '{["' else raw[:2000])

def status_of(iid):
    st, b = call("GET", f"{DAEMON}/api/instances/{iid}")
    return b.get("status") if st == 200 else None

t0 = time.time()
st, b = call("POST", f"{DAEMON}/api/instances", {"agent_id": "experiencer"})
emit("1. create ID_D", st, b)
ID_D = b.get("instance_id")
print(f"\n>>> ID_D = {ID_D}")
(OUT / "id_d.txt").parent.mkdir(parents=True, exist_ok=True)
(OUT / "id_d.txt").write_text(str(ID_D))

st, b = call("POST", f"{DAEMON}/api/instances/{ID_D}/messages",
             {"content": "Write a detailed 600-word story about a lighthouse. Take your time and be thorough."})
emit("2. long-story POST", st, b)

# fast poll to RUNNING
deadline = time.time() + 30
run_at = None
while time.time() < deadline:
    if str(status_of(ID_D)).upper() == "RUNNING":
        run_at = time.time()
        break
    time.sleep(0.1)
print(f"\nRUNNING observed at +{round((run_at or time.time())-t0, 2)}s")

# plain-text injection IMMEDIATELY (no image fields)
st, b = call("POST", f"{DAEMON}/api/instances/{ID_D}/messages",
             {"content": "QUICKNOTE-7731 plain text injection"})
inject_latency = round(time.time() - (run_at or t0), 2)
emit(f"3. PLAIN-TEXT injection (expect 202; latency after RUNNING: {inject_latency}s)", st, b)

# drain
deadline = time.time() + 180
final = None
while time.time() < deadline:
    final = status_of(ID_D)
    if str(final).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        if time.time() - t0 > 25:
            break
    time.sleep(1.0)
print(f"\n4. drained: final={final} at +{round(time.time()-t0,1)}s")

st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_D}/messages")
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))
delivered = False
if isinstance(msgs, list):
    for m in msgs:
        if "QUICKNOTE-7731" in (m.get("content") or ""):
            delivered = True
            print(f"\n--- QUICKNOTE-7731 found in checkpoint, role={m.get('role')}, images={m.get('images')} ---")
            print((m.get("content") or "")[:400])
n_msgs = len(msgs) if isinstance(msgs, list) else "?"
print(f"\n### EXP-1: QUICKNOTE-7731 in checkpoint -> {'DELIVERED' if delivered else 'STRANDED'} (total msgs={n_msgs})")

st, inj = call("GET", f"{DAEMON}/api/instances/{ID_D}/injection")
emit("5. GET /injection", st, inj)

(OUT / "transcript.json").write_text(json.dumps(
    {"id_d": ID_D, "injection_http": TR[2]["http_status"], "inject_latency_after_running": inject_latency,
     "delivered": delivered, "final_status": final, "transcript": TR}, indent=2))
print(f"\nID_D={ID_D} injection_http={TR[2]['http_status']} delivered={delivered} final={final}")
