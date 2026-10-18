#!/usr/bin/env python3
"""Fenced local TLS edge for the live-view-passcode browser lane.

Listens on https://127.0.0.1:<EDGE_PORT> and forwards to
http://127.0.0.1:<PORT>. This is the in-drill stand-in for the real
deployment edge (door.ensem.dev): it injects ``X-Forwarded-Proto: https``
(the exact signal the daemon's secure-context contract reads) and pins
the upstream ``Host`` to the browser-facing authority so the router's
same-host Origin check passes — byte-for-byte the proxy mechanics the
State-A/A-B deployment describes (proxies stay pure passthrough).

GET/POST form traffic only; response is buffered and re-emitted with
all headers preserved (Set-Cookie duplicates included).
"""
import http.client
import os
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UP_HOST = "127.0.0.1"
UP_PORT = int(os.environ.get("UP_PORT", "8081"))
EDGE_AUTHORITY = f"127.0.0.1:{os.environ.get('EDGE_PORT', '8443')}"
HOP = {"connection", "keep-alive", "transfer-encoding", "proxy-connection",
       "te", "trailer", "upgrade"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet-ish: keep a trace in the log
        sys.stderr.write("[edge] %s\n" % (fmt % args))

    def _relay(self, include_body: bool) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if (length and include_body) else None
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=30)
        try:
            if os.environ.get("EDGE_DEBUG"):
                sys.stderr.write("[edge-req] %s %s\n" % (self.command, self.path))
                for k, v in self.headers.items():
                    sys.stderr.write("[edge-hdr] %s: %s\n" % (k, v))
            conn.putrequest(self.command, self.path,
                            skip_host=True, skip_accept_encoding=True)
            for k, v in self.headers.items():
                lk = k.lower()
                if lk in HOP or lk == "host" or lk == "x-forwarded-proto":
                    continue
                if lk == "content-length" and body is not None:
                    continue  # re-emitted below with the drained length
                conn.putheader(k, v)
            conn.putheader("Host", EDGE_AUTHORITY)
            conn.putheader("X-Forwarded-Proto", "https")
            conn.putheader("Connection", "close")
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            if body is not None:
                conn.send(body)
            resp = conn.getresponse()
            rbody = resp.read() if self.command != "HEAD" else b""
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in HOP or lk == "content-length":
                    continue
                self.send_header(k, v)  # repeats fine for Set-Cookie etc.
            self.send_header("Content-Length", str(len(rbody)))
            self.end_headers()
            if rbody and include_body:
                self.wfile.write(rbody)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.close_connection = True

    def do_GET(self):
        self._relay(include_body=True)

    def do_POST(self):
        self._relay(include_body=True)

    def do_HEAD(self):
        self._relay(include_body=False)


def main() -> None:
    cert = sys.argv[1]
    key = sys.argv[2]
    httpd = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("EDGE_PORT", "8443"))), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write(f"[edge] https://{EDGE_AUTHORITY} -> http://{UP_HOST}:{UP_PORT} ready\n")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
