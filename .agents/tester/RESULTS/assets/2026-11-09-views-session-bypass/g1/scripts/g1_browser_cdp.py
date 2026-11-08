#!/usr/bin/env python3
"""G1 CLOSURE GATE — headless Chromium, REAL form navigation, ZERO header injection.

Subject under test: the ORIGINAL live symptom. An anonymous browser user opens a
passcode-gated live view over TLS whose cert is untrusted (self-signed). Chrome
serialises `Origin: null` on the top-level form POST. Before this branch that
token produced `403 {"error":"forbidden"}` rendered as a JSON page. After the
branch it must be ALLOWED (only `Sec-Fetch-Site: cross-site` is rejected), so
the same navigation must complete end-to-end and render the view.

HARD RULE (task binding): NO setExtraHTTPHeaders, NO route interception, NO
CDP request-header overrides. `ignore_https_errors=True` is required only to
reach the self-signed edge — and it is precisely the condition that makes
Chrome emit the natural `Origin: null`.

Instrumentation (CDP, read-only observation) — correlated by (requestId, hop
index). Redirect hops REUSE the requestId, and every hop emits exactly one of
each event, so per-requestId FIFO lists + index matching is exact:
  * Network.requestWillBeSent          -> hop order / method / url / postData
  * Network.requestWillBeSentExtraInfo -> WIRE-LEVEL request headers
  * Network.responseReceived           -> status
  * Network.responseReceivedExtraInfo  -> RAW response headers incl. Set-Cookie
"""
from __future__ import annotations

import collections
import json
import os
import sys

from playwright.sync_api import sync_playwright

FENCE = open("/tmp/g1fence.ready").read().strip()
assert FENCE.startswith("/tmp/g1lv."), FENCE

EV = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
BASE = "https://127.0.0.1:8443"
VIEW_PATH = "/views/public/g1root2/gate.md"
URL = BASE + VIEW_PATH
CODE = open(os.path.join(FENCE, "passcode")).read().strip()

# requestId -> ordered hop records (method/url/postData)
hopmap: dict[str, list[dict]] = collections.defaultdict(list)
hoporder: list[tuple[str, int]] = []
req_extra: dict[str, list[dict]] = collections.defaultdict(list)
resp_status: dict[str, list[int]] = collections.defaultdict(list)
resp_extra: dict[str, list[dict]] = collections.defaultdict(list)
extra_status: dict[str, list[int]] = collections.defaultdict(list)
resp_extra_status_seen: dict[str, list[int]] = collections.defaultdict(list)
all_raw_events: list[dict] = []


def norm(raw: dict) -> dict:
    return {k.lower(): v for k, v in raw.items()}


def main() -> int:
    with sync_playwright() as p:
        exe = os.environ.get(
            "G1_CHROME",
            "/home/nea/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
        )
        browser = p.chromium.launch(
            headless=True, executable_path=exe,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")

        def on_req(ev):
            rid = ev["requestId"]
            r = ev["request"]
            k = len(hopmap[rid])
            hopmap[rid].append({
                "rid": rid, "index": k,
                "method": r["method"], "url": r["url"],
                "post_data": r.get("postData"),
                "redirect_response": bool(ev.get("redirectResponse")),
            })
            hoporder.append((rid, k))

        cdp.on("Network.requestWillBeSent", on_req)
        cdp.on("Network.requestWillBeSentExtraInfo",
               lambda ev: req_extra[ev["requestId"]].append(norm(ev.get("headers", {}))))
        cdp.on("Network.responseReceived",
               lambda ev: resp_status[ev["requestId"]].append(ev["response"]["status"]))
        cdp.on("Network.responseReceivedExtraInfo",
               lambda ev: (resp_extra[ev["requestId"]].append(norm(ev.get("headers", {}))),
                           extra_status[ev["requestId"]].append(ev.get("statusCode")),
                           all_raw_events.append({
                               "rid": ev["requestId"],
                               "status": ev.get("statusCode"),
                               "location": norm(ev.get("headers", {})).get("location", "<absent>"),
                               "set_cookie": norm(ev.get("headers", {})).get("set-cookie", "<absent>"),
                           })))

        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        # ── 1. land on the gated URL (anonymous, no cookies) ───────────
        nav = page.goto(URL, wait_until="load", timeout=45000)
        page.wait_for_timeout(600)
        interstitial = {
            "status": nav.status if nav else None,
            "content_type": nav.headers.get("content-type") if nav else None,
            "title": page.title(),
            "h1": page.evaluate("() => (document.querySelector('h1')||{}).innerText || ''"),
            "form_action": page.evaluate(
                "() => { const f=document.querySelector('form'); return f?f.getAttribute('action'):null; }"),
            "form_method": page.evaluate(
                "() => { const f=document.querySelector('form'); return f?(f.getAttribute('method')||'get'):null; }"),
            "input_selector_hit": page.evaluate("() => !!document.querySelector('input#code')"),
            "script_count": page.evaluate("() => document.scripts.length"),
            "body_is_json": page.evaluate(
                "() => { const t=document.body.innerText.trim(); return t.startsWith('{') && t.includes('\"error\"'); }"),
            "cookies": [c["name"] for c in ctx.cookies()],
        }
        page.screenshot(path="/tmp/board_before.png", full_page=True)
        page.screenshot(path=os.path.join(EV, "board_before.png"), full_page=True)

        # ── 2. REAL form navigation: type + click submit (no fetch/XHR) ─
        page.fill("input#code", CODE)
        with page.expect_navigation(wait_until="load", timeout=45000) as nav_info:
            page.click('button[type="submit"]')
        final = nav_info.value
        page.wait_for_timeout(900)

        after = {
            "final_url": page.url,
            "final_status": final.status if final else None,
            "final_content_type": final.headers.get("content-type") if final else None,
            "title": page.title(),
            "h1": page.evaluate("() => (document.querySelector('h1')||{}).innerText || ''"),
            "body_text_head": page.evaluate("() => document.body.innerText.slice(0,300)"),
            "still_interstitial": page.evaluate(
                "() => document.body.innerText.includes('Enter passcode')"),
            "body_is_json": page.evaluate(
                "() => { const t=document.body.innerText.trim(); return t.startsWith('{') && t.includes('\"error\"'); }"),
            "body_is_exact_forbidden_json": page.evaluate(
                "() => document.body.innerText.trim() === '{\"error\":\"forbidden\"}'"),
            "content_marker_present": page.evaluate(
                "() => document.body.innerHTML.includes('G1-CONTENT-MARKER')"),
            "cookies": [c["name"] for c in ctx.cookies()],
        }
        page.screenshot(path="/tmp/board_after.png", full_page=True)
        page.screenshot(path=os.path.join(EV, "board_after.png"), full_page=True)

        cookies = [{
            **{k: c[k] for k in ("name", "domain", "path", "expires", "httpOnly", "secure", "sameSite")},
            "value_prefix": c["value"][:12],
        } for c in ctx.cookies()]

        cdp.detach()
        browser.close()

    chain = []
    for rid, k in hoporder:
        h = hopmap[rid][k]
        if "/views/" not in (h["url"] or ""):
            continue
        rh = req_extra[rid][k] if k < len(req_extra[rid]) else {}
        # Status: the ExtraInfo stream is exactly 1:1 with hops (it also fires
        # for the redirect response), so index it per requestId in order.
        st = extra_status[rid][k] if k < len(extra_status[rid]) else (
            resp_status[rid][k] if k < len(resp_status[rid]) else None)
        sh = resp_extra[rid][k] if k < len(resp_extra[rid]) else {}
        chain.append({
            "hop": k,
            "method": h["method"],
            "url": h["url"],
            "status": st,
            "redirect_hop": h["redirect_response"],
            "request_headers": {
                "origin": rh.get("origin", "<absent>"),
                "referer": rh.get("referer", "<absent>"),
                "sec-fetch-site": rh.get("sec-fetch-site", "<absent>"),
                "sec-fetch-mode": rh.get("sec-fetch-mode", "<absent>"),
                "sec-fetch-dest": rh.get("sec-fetch-dest", "<absent>"),
                "sec-fetch-user": rh.get("sec-fetch-user", "<absent>"),
                "content-type": rh.get("content-type", "<absent>"),
                "cookie": ("<present>" if "cookie" in rh else "<absent>"),
            },
            "post_data": h["post_data"],
            "response_headers": {
                "content-type": sh.get("content-type", "<absent>"),
                "location": sh.get("location", "<absent>"),
                "set-cookie": sh.get("set-cookie", "<absent>"),
                "cache-control": sh.get("cache-control", "<absent>"),
                "x-content-type-options": sh.get("x-content-type-options", "<absent>"),
                "referrer-policy": sh.get("referrer-policy", "<absent>"),
                "content-security-policy": sh.get("content-security-policy", "<absent>"),
            },
        })

    payload = {
        "meta": {
            "base": BASE,
            "view_path": VIEW_PATH,
            "browser": "playwright-python 1.64.1 + Chromium 148 (chromium-1223 bundle), headless",
            "header_injection": "NONE — no setExtraHTTPHeaders, no route interception, no CDP header overrides",
            "tls": "self-signed edge cert; ignore_https_errors=True (untrusted TLS => natural Origin: null)",
            "passcode": "correct code, single submission",
        },
        "interstitial": interstitial,
        "after_submit": after,
        "cookies_after": cookies,
        "wire_chain": chain,
        "all_raw_response_header_events": all_raw_events,
        "console": console,
    }
    with open(os.path.join(EV, "g1-browser-evidence.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
