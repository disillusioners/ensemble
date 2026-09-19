#!/usr/bin/env python3
"""EXP-2: is stranded ID_A content recoverable on a later turn (D2 wake-turn drain)?"""
import json, time, urllib.error, urllib.request
from pathlib import Path
DAEMON = "http://127.0.0.1:8090"
OUT = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts/exp2")
ID_A = "7b011705-4ca0-4f3e-a84a-33d2c6a07e21"
TR = []
def call(method, url, payload=None, timeout=120):
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
st, b = call("POST", f"{DAEMON}/api/instances/{ID_A}/messages",
             {"content": "what did I send you earlier?"})
print(f"=== EXP-2 revive POST | HTTP {st} ===")
print(json.dumps(b, indent=2)[:900])
TR.append({"step": "revive_post", "http": st, "body": b})

deadline = time.time() + 180
final = None
while time.time() < deadline:
    final = status_of(ID_A)
    if str(final).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        if time.time() - t0 > 25:
            break
    time.sleep(1.0)
print(f"\ndrained: final={final} at +{round(time.time()-t0,1)}s")

st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_A}/messages")
(OUT / "messages.json").parent.mkdir(parents=True, exist_ok=True)
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))
print(f"\n=== GET /messages: {len(msgs)} messages ===")
lga_delivered = False; lga_in_images = False; reply_refs = False
for m in msgs:
    c = m.get("content") or ""; imgs = m.get("images")
    if "LGA-111" in c or "LGA-222" in c:
        lga_delivered = True
        if m.get("role") in ("user", "human"):
            print(f"\n--- stranded content DELIVERED as role={m.get('role')} ---")
            print("content[:200]:", c[:200].replace("\n", " "))
            print("wire images:", imgs)
            if imgs and any("tmp_images" in str(x) for x in imgs): lga_in_images = True
    if m.get("role") in ("assistant", "ai") and ("LGA-111" in c or "LGA-222" in c or "lighthouse story" in c.lower()):
        if "LGA" in c: reply_refs = True
print(f"\n### EXP-2: stranded text delivered -> {lga_delivered} | refs surfaced in images[] -> {lga_in_images} | reply mentions LGA -> {reply_refs}")

st, inj = call("GET", f"{DAEMON}/api/instances/{ID_A}/injection")
print(f"\n=== /injection after wake turn ===\n{json.dumps(inj, indent=2)}")
(OUT / "transcript.json").write_text(json.dumps(
    {"id_a": ID_A, "revive_http": st, "final_status": final, "lga_delivered": lga_delivered,
     "lga_in_images": lga_in_images, "reply_refs": reply_refs, "injection_after": inj, "transcript": TR}, indent=2))
