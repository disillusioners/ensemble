import http.server, json, time, threading, re

# v2 FIX: sleep on EVERY non-selector chat call (per-request, not one-shot)
# => the worker turn duration is deterministically > capture_min_duration_seconds (60).
SLEEP_SECONDS = 80
LOCK = threading.Lock()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            req = json.loads(body) if body else {}
        except Exception:
            req = {}
        path = self.path.split("?")[0]
        if path.endswith("/chat/completions"):
            messages = req.get("messages", [])
            sys_content = ""
            user_content = ""
            for m in messages:
                if m.get("role") == "system":
                    sys_content += m.get("content", "") + "\n"
                elif m.get("role") == "user":
                    user_content += m.get("content", "") + "\n"
            if "skill selector" in sys_content.lower() or "skill selector" in user_content.lower():
                m = re.search(r"1\.\s*(.+?)\s*—", user_content)
                name = m.group(1).strip() if m else ""
                if not name:
                    m2 = re.search(r"(\S+)\s*—", user_content)
                    name = m2.group(1).strip() if m2 else ""
                payload = {"selected": [{"name": name, "score": 1.0}], "low_match": []}
                resp = {"choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}, "finish_reason": "stop"}]}
            else:
                with LOCK:
                    time.sleep(SLEEP_SECONDS)
                resp = {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path.endswith("/embeddings"):
            inp = req.get("input", "")
            n = 1 if isinstance(inp, str) else len(inp)
            vec = [1.0]*8
            data_obj = {"data": [{"embedding": vec, "index": i} for i in range(n)], "model": req.get("model",""), "usage": {"prompt_tokens":1,"total_tokens":1}}
            data = json.dumps(data_obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1])
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    httpd.serve_forever()
