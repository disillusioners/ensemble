"""Final assertions: per-image attribution, images[] exclusivity, reply presence."""
import json, os, re
import httpx

BASE = "http://127.0.0.1:8090"
HERE = os.path.dirname(os.path.abspath(__file__))
t = json.load(open(os.path.join(HERE, "burst_n4_transcript.json")))
ids, refs = t["instance_ids"], t["refs"]
c = httpx.Client(timeout=30)
fail = []
for k in (1, 2, 3, 4):
    iid, ref = ids[k - 1], refs[k - 1]
    msgs = c.get(f"{BASE}/api/instances/{iid}/messages").json()
    user_conv = [m for m in msgs if m.get("role") == "user" and (m.get("content") or "").startswith("[Image 1:")]
    assistant = [m for m in msgs if m.get("role") == "assistant" and (m.get("content") or "").strip()]
    u = user_conv[0] if user_conv else None
    checks = {
        "conversion_user_msg_present": u is not None,
        "own_code_in_conversion": bool(u) and f"GATE-N{k}" in u["content"],
        "no_foreign_code_in_conversion": bool(u) and not re.search(r"GATE-N[1-4]", re.sub(f"GATE-N{k}", "", u["content"])),
        "images_exactly_own_ref": bool(u) and u.get("images") == [ref],
        "no_foreign_ref_anywhere": not any(
            (r in json.dumps(msgs)) for j, r in enumerate(refs, 1) if j != k
        ),
        "assistant_reply_present": bool(assistant),
        "no_image_datauri_on_assistant": not any((m.get("images") for m in assistant)),
    }
    bad = [n for n, ok in checks.items() if not ok]
    print(f"ID_{k}: {'PASS' if not bad else 'FAIL ' + str(bad)}  {checks}")
    if bad:
        fail.append((k, bad))
print("AGGREGATE:", "PASS" if not fail else f"FAIL {fail}")
