#!/usr/bin/env python3
"""EXP-3: does a tool-loop (multi-superstep) turn tap the 202 injection mid-turn?"""
import base64, json, time, urllib.error, urllib.request
from pathlib import Path
DAEMON = "http://127.0.0.1:8090"
SCRIPTS = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts")
OUT = SCRIPTS / "exp3"
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
st, b = call("POST", f"{DAEMON}/api/instances", {"agent_id": "experiencer"})
ID_E = b.get("instance_id")
print(f"1. create ID_E: HTTP {st} -> {ID_E}")
(OUT / "id_e.txt").parent.mkdir(parents=True, exist_ok=True)
(OUT / "id_e.txt").write_text(str(ID_E))

st, b = call("POST", f"{DAEMON}/api/instances/{ID_E}/messages",
             {"content": "First use your knowledge tool to look up 'ensemble clipboard', then write a 200-word summary of what you found."})
print(f"2. multi-superstep prompt POST: HTTP {st}")

run_at = None
deadline = time.time() + 30
while time.time() < deadline:
    if str(status_of(ID_E)).upper() == "RUNNING":
        run_at = time.time(); break
    time.sleep(0.1)
print(f"   RUNNING at +{round((run_at or time.time())-t0,2)}s; waiting 6s so superstep-1 (tool call) begins...")
time.sleep(6.0)

b64 = base64.b64encode((SCRIPTS / "legE.png").read_bytes()).decode()
st, b = call("POST", f"{DAEMON}/api/tmp_images",
             {"images": [{"filename": "legE.png", "content_type": "image/png", "data_base64": b64}]})
ref = b["uploads"][0]["ref_url"]
print(f"3. upload: HTTP {st} ref={ref}")

st, b = call("POST", f"{DAEMON}/api/instances/{ID_E}/messages",
             {"content": "also consider this image SMOKE-LGE-991", "image_refs": [ref]})
inject_http = st
print(f"4. image_refs injection: HTTP {inject_http}")
print(json.dumps(b, indent=2)[:700])
TR.append({"step": "injection", "http": st, "body": b, "ref": ref})

deadline = time.time() + 180
final = None
while time.time() < deadline:
    final = status_of(ID_E)
    if str(final).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        if time.time() - t0 > 30:
            break
    time.sleep(1.0)
print(f"\n5. drained: final={final} at +{round(time.time()-t0,1)}s")

st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_E}/messages")
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))
delivered = False; in_images = False
for m in msgs:
    c = m.get("content") or ""
    if "SMOKE-LGE-991" in c and m.get("role") in ("user", "human"):
        delivered = True
        print(f"\n--- injected message delivered (role=user) ---")
        print("content[:160]:", c[:160].replace("\n"," "))
        print("wire images:", m.get("images"))
        if m.get("images") and any("tmp_images" in str(x) for x in m.get("images")): in_images = True
print(f"\n### EXP-3: injection delivered into turn -> {delivered} | ref in images[] -> {in_images} | total msgs={len(msgs)}")

st, inj = call("GET", f"{DAEMON}/api/instances/{ID_E}/injection")
print(f"/injection: {json.dumps(inj)}")
(OUT / "transcript.json").write_text(json.dumps(
    {"id_e": ID_E, "injection_http": inject_http, "final_status": final, "delivered": delivered,
     "in_images": in_images, "injection_after": inj, "transcript": TR}, indent=2))
print(f"\nID_E={ID_E} injection_http={inject_http} delivered={delivered} in_images={in_images} final={final}")
