#!/usr/bin/env python3
"""RELOAD SMOKE #40 — LEG (c): legacy data-URI bonus (Discord-consumer regression).

Fresh instance ID_C. POST message {"content", "images": ["data:image/png;base64,..."]}
(LEGACY field). Expect 200, turn completes, MAIN instance IS vision-routed
(log shows Invoking LLM (VISION) for ID_C), reply references image content.
"""
import base64, json, sys, time, urllib.error, urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8090"
SCRIPTS = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts")
OUT = SCRIPTS / "leg_c"
TRANSCRIPT = []

def emit(label, status, body, extra=None):
    TRANSCRIPT.append({"label": label, "http_status": status, "body": body, **(extra or {})})
    print(f"\n=== {label} | HTTP {status} ===")
    s = body if isinstance(body, str) else json.dumps(body, indent=2)
    print(s[:1800])

def call(method, url, payload=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try: body = json.loads(raw)
            except Exception: body = raw[:2000]
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: body = json.loads(raw)
        except Exception: body = raw[:2000]
        return e.code, body

def get_status(iid):
    st, body = call("GET", f"{DAEMON}/api/instances/{iid}")
    return body.get("status") if st == 200 else None

t0 = time.time()
st, body = call("POST", f"{DAEMON}/api/instances", {"agent_id": "experiencer"})
emit("1. create instance ID_C (experiencer)", st, body)
ID_C = body.get("instance_id")
print(f"\n>>> ID_C = {ID_C}")
(OUT / "id_c.txt").parent.mkdir(parents=True, exist_ok=True)
(OUT / "id_c.txt").write_text(str(ID_C))

data_uri = "data:image/png;base64," + base64.b64encode((SCRIPTS / "legC.png").read_bytes()).decode()
print(f">>> data_uri len = {len(data_uri)} (starts {data_uri[:40]}...)")
(OUT / "data_uri_prefix.txt").write_text(data_uri[:80])

st, body = call("POST", f"{DAEMON}/api/instances/{ID_C}/messages",
                {"content": "what do you see?", "images": [data_uri]})
emit("2. POST LEGACY data-URI message (expect 200)", st, body)
send_http = st

deadline = time.time() + 180
transitions = []
final = None
while time.time() < deadline:
    sval = get_status(ID_C)
    if not transitions or transitions[-1][0] != sval:
        transitions.append((sval, round(time.time() - t0, 1)))
        print(f"  [{round(time.time()-t0,1)}s] status = {sval}")
    final = sval
    if sval and str(sval).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        if len(transitions) > 1 or time.time() - t0 > 20:
            break
    time.sleep(1.0)
print("transitions:", transitions)

st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_C}/messages")
emit("4. GET /messages", st, {"count": len(msgs) if isinstance(msgs, list) else "?"})
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))

if isinstance(msgs, list):
    for m in msgs:
        if m.get("role") in ("user", "human"):
            imgs = m.get("images")
            c = m.get("content") or ""
            print(f"\nuser msg: content[:120]={c[:120]!r}")
            print(f"  wire images: {str(imgs)[:300]}")
        if m.get("role") in ("assistant", "ai"):
            a = m.get("content") or ""
            if a.strip():
                print(f"\nassistant reply len={len(a)}")
                print("  mentions LGC-444:", "LGC-444" in a, "| magenta/pink:", ("magenta" in a.lower() or "pink" in a.lower()))
                print("--- last 700 chars ---")
                print(a[-700:])
                (OUT / "assistant_reply.txt").write_text(a)

(OUT / "transcript.json").write_text(json.dumps(
    {"id_c": ID_C, "send_http_status": send_http, "final_status": final,
     "transitions": transitions, "transcript": TRANSCRIPT}, indent=2))
print(f"\nID_C={ID_C} send_http={send_http} final={final}")
