"""Per-instance verification: user content code-attribution, images[] refs, reply presence."""
import json, os
import httpx

BASE = "http://127.0.0.1:8090"
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "burst_n4_transcript.json")) as f:
    t = json.load(f)

ids = t["instance_ids"]; refs = t["refs"]
client = httpx.Client(timeout=30)
for k in (1, 2, 3, 4):
    iid = ids[k - 1]; ref = refs[k - 1]
    r = client.get(f"{BASE}/api/instances/{iid}/messages")
    msgs = r.json()
    if isinstance(msgs, dict):
        msgs = msgs.get("messages", msgs.get("items", []))
    print(f"===== ID_{k} {iid} ({len(msgs)} messages) =====")
    for m in msgs:
        role = m.get("role") or m.get("type")
        content = (m.get("content") or "")
        imgs = m.get("images")
        imgrefs = m.get("image_refs")
        mc = m.get("message_call_type") or m.get("invocation_type") or m.get("call_type")
        extra = {kk: m.get(kk) for kk in ("message_call_type", "invocation_type", "model", "model_name", "created_at") if m.get(kk) is not None}
        print(f"[{role}] call_type={mc} extra={extra}")
        print(f"  images={json.dumps(imgs)[:300]}")
        print(f"  image_refs={json.dumps(imgrefs)[:300]}")
        print(f"  content={content[:700]}")
        print()
