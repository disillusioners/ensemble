#!/usr/bin/env python3
"""RELOAD SMOKE #40 — LEG (a): RUNNING-target mid-turn 202 injection.

Steps:
  1. POST /api/instances {agent_id: experiencer}        -> ID_A
  2. POST long-story message; poll status until RUNNING (~200ms)
  3. While RUNNING: upload 2 PNGs (batch-of-1 each) -> refs
     POST message {content, image_refs:[r1, r2]}        -> EXPECT 202
  4. Drain (<=180s)
  5. GET /messages -> injection message must carry BOTH refs in wire `images`
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DAEMON = "http://127.0.0.1:8090"
SCRIPTS = Path("/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts")
OUT = SCRIPTS / "leg_a"

TRANSCRIPT = []


def emit(label, status, body, extra=None):
    rec = {"label": label, "http_status": status, "body": body}
    if extra:
        rec.update(extra)
    TRANSCRIPT.append(rec)
    print(f"\n=== {label} | HTTP {status} ===")
    s = body if isinstance(body, str) else json.dumps(body, indent=2)
    print(s[:2500])
    if extra:
        print("EXTRA:", json.dumps(extra, indent=2)[:1200])


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
        return st, body, None
    status_val = body.get("status") if isinstance(body, dict) else None
    if status_val is None and isinstance(body, dict) and isinstance(body.get("instance"), dict):
        status_val = body["instance"].get("status")
    return st, body, status_val


t0 = time.time()

# ---- STEP 1: create instance -------------------------------------------------
EXISTING_ID = os.environ.get("EXISTING_ID")
if EXISTING_ID:
    ID_A = EXISTING_ID
    print(f"\n=== 1. reuse existing instance (EXISTING_ID) = {ID_A} ===")
else:
    st, body = call("POST", f"{DAEMON}/api/instances", {"agent_id": "experiencer"})
    emit("1. create instance (experiencer)", st, body)
    if st not in (200, 201):
        print("FATAL: instance creation failed"); sys.exit(2)
    ID_A = body.get("instance_id") or body.get("id") or (body.get("instance") or {}).get("id")
print(f"\n>>> ID_A = {ID_A}")
(OUT / "id_a.txt").parent.mkdir(parents=True, exist_ok=True)
(OUT / "id_a.txt").write_text(str(ID_A))

# ---- STEP 2: long turn + poll to RUNNING -------------------------------------
story = ("Write a detailed 600-word story about a lighthouse. "
         "Take your time and be thorough.")
st, body = call("POST", f"{DAEMON}/api/instances/{ID_A}/messages", {"content": story})
emit("2. POST long-story message", st, body)

seen = []
deadline = time.time() + 60
running_at = None
while time.time() < deadline:
    _, _, sval = get_status(ID_A)
    if not seen or seen[-1][0] != sval:
        seen.append((sval, round(time.time() - t0, 2)))
        print(f"  [{round(time.time()-t0,2)}s] status = {sval}")
    if sval and str(sval).upper() == "RUNNING":
        running_at = time.time()
        break
    time.sleep(0.2)
print("status transitions:", seen)
if running_at is None:
    print("WARNING: never observed RUNNING; proceeding anyway")

# ---- STEP 3: upload 2 PNGs + inject while RUNNING -----------------------------
refs = []
for name in ("legA1.png", "legA2.png"):
    b64 = base64.b64encode((SCRIPTS / name).read_bytes()).decode()
    st, body = call("POST", f"{DAEMON}/api/tmp_images",
                    {"images": [{"filename": name, "content_type": "image/png",
                                 "data_base64": b64}]})
    emit(f"3a. upload {name} (batch-of-1)", st, body)
    if st != 200:
        print("FATAL: upload failed"); sys.exit(2)
    refs.append(body["uploads"][0]["ref_url"])
print(f"\n>>> refs = {refs}")
(OUT / "refs.json").write_text(json.dumps(refs))

inject_at = time.time()
was_running = running_at is not None
st, body = call("POST", f"{DAEMON}/api/instances/{ID_A}/messages",
                {"content": "also consider this image", "image_refs": refs})
emit("3b. POST injection message (image_refs) — EXPECT 202", st, body,
     {"injected_while_running": was_running,
      "seconds_after_running_observed": round(inject_at - (running_at or inject_at), 2)})
injection_http = st

# ---- STEP 4: drain ------------------------------------------------------------
deadline = time.time() + 180
final = None
while time.time() < deadline:
    _, _, sval = get_status(ID_A)
    final = sval
    if sval and str(sval).upper() in ("COMPLETED", "ERROR", "FAILED", "TERMINATED"):
        break
    time.sleep(1.0)
print(f"\n=== 4. drained after {round(time.time()-t0,1)}s, final status = {final} ===")

# ---- STEP 5: GET /messages — union assertion -----------------------------------
st, msgs = call("GET", f"{DAEMON}/api/instances/{ID_A}/messages")
emit("5. GET /messages", st, {"count": len(msgs) if isinstance(msgs, list) else "?"})
(OUT / "messages.json").write_text(json.dumps(msgs, indent=2))

if isinstance(msgs, list):
    hits = [m for m in msgs
            if isinstance(m, dict) and (m.get("image_refs")
                                        or (isinstance(m.get("images"), list)
                                            and any("tmp_images" in str(x) for x in m.get("images"))))]
    for m in hits:
        print("\n--- injection/refs message on the wire ---")
        print(json.dumps({k: m.get(k) for k in
                          ("id", "role", "type", "content", "images", "image_refs",
                           "additional_kwargs")}, indent=2)[:2200])
    wire_imgs = None
    if hits:
        cand = [m for m in hits if m.get("content") == "also consider this image"]
        wire_imgs = (cand or hits)[-1].get("images")
    got = set(map(str, wire_imgs or []))
    want = set(refs)
    ok = want.issubset(got)
    print(f"\n### LEG-A UNION ASSERTION: both refs in wire images -> "
          f"{'PASS' if ok else 'FAIL'} (images={wire_imgs})")

(OUT / "transcript.json").write_text(json.dumps(
    {"id_a": ID_A, "injection_http_status": injection_http,
     "injected_while_running": was_running, "final_status": final,
     "transcript": TRANSCRIPT}, indent=2))
print(f"\nID_A={ID_A} injection_http={injection_http} final={final}")
