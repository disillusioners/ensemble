#!/usr/bin/env python3
"""G1 CLOSURE GATE — headless Chromium, REAL form navigation, NO origin injection.

Proves the ORIGINAL browser symptom is DEAD on feature/views-session-bypass:
anonymous browser user under untrusted TLS (self-signed) submits the CORRECT
passcode via a real top-level form navigation. Chrome serialises `Origin: null`
naturally; the branch's navigation-correct gate must now ALLOW it (it only
rejects when Sec-Fetch-Site attests cross-site).

HARD RULE (task binding): no setExtraHTTPHeaders, no route interception, no
header overrides of any kind — the natural Origin: null IS the subject.

Outputs (JSON) on stdout, artifacts into the evidence dir.
"""
from __future__ import annotations

import json
import os
import sys

from playwright.sync_api import sync_playwright

FENCE = open("/tmp/g1fence.ready").read().strip()
assert FENCE.startswith("/tmp/g1lv."), FENCE

EV = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
BASE = "https://127.0.0.1:8443"
VIEW_PATH = "/views/public/g1root1/gate.md"
URL = BASE + VIEW_PATH
CODE = open(os.path.join(FENCE, "passcode")).read().strip()

chain: list[dict] = []
console: list[str] = []
records: dict = {}


def on_request(req):
    if "/views/" not in req.url:
        return
    h = req.headers
    chain.append({
        "kind": "request",
        "method": req.method,
        "url": req.url,
        "origin_header": h.get("origin", "<absent>"),
        "referer_header": h.get("referer", "<absent>"),
        "sec_fetch_site": h.get("sec-fetch-site", "<absent>"),
        "sec_fetch_mode": h.get("sec-fetch-mode", "<absent>"),
        "sec_fetch_dest": h.get("sec-fetch-dest", "<absent>"),
        "content_type": h.get("content-type", "<absent>"),
        "cookie_header": h.get("cookie", "<absent>"),
        "post_data": req.post_data,
    })


def on_response(resp):
    if "/views/" not in resp.url:
        return
    try:
        h = resp.headers
        entry = {
            "kind": "response",
            "status": resp.status,
            "url": resp.url,
            "content_type": h.get("content-type", "<absent>"),
            "location": h.get("location", "<absent>"),
            "set_cookie": h.get("set-cookie", "<absent>"),
            "cache_control": h.get("cache-control", "<absent>"),
            "content_security_policy": h.get("content-security-policy", "<absent>"),
        }
        try:
            body = resp.body()[:4000]
            entry["body_head"] = body.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            entry["body_head"] = f"<unavailable: {type(exc).__name__}>"
        chain.append(entry)
    except Exception as exc:  # noqa: BLE001
        chain.append({"kind": "response-error", "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    with sync_playwright() as p:
        # Existing on-disk Chromium (playwright's bundled headless-shell for
        # this lib version is absent; the full browser binary works headless).
        exe = os.environ.get(
            "G1_CHROME",
            "/home/nea/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
        )
        browser = p.chromium.launch(
            headless=True,
            executable_path=exe,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--headless=new"],
        )
        ctx = browser.new_context(
            ignore_https_errors=True,          # untrusted self-signed cert => natural Origin: null
            viewport={"width": 1280, "height": 800},
            user_agent=None,
        )
        page = ctx.new_page()
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        # ── step 3: open the gated URL ─────────────────────────────────────
        nav = page.goto(URL, wait_until="load", timeout=45000)
        page.wait_for_timeout(400)
        records["interstitial"] = {
            "status": nav.status if nav else None,
            "content_type": (nav.headers.get("content-type") if nav else None),
            "title": page.title(),
            "h1": page.evaluate("() => (document.querySelector('h1')||{}).innerText || ''"),
            "form_action": page.evaluate(
                "() => { const f=document.querySelector('form'); return f ? f.getAttribute('action') : null; }"
            ),
            "form_method": page.evaluate(
                "() => { const f=document.querySelector('form'); return f ? (f.getAttribute('method')||'get') : null; }"
            ),
            "input_name": page.evaluate(
                "() => { const i=document.querySelector('input#code'); return i ? i.getAttribute('name') : null; }"
            ),
            "script_count": page.evaluate("() => document.scripts.length"),
            "json_looking_body": page.evaluate(
                "() => { const t=document.body.innerText.trim(); return t.startsWith('{') && t.includes('\"error\"'); }"
            ),
            "cookie_names": sorted(c["name"] for c in ctx.cookies()),
        }
        page.screenshot(path="/tmp/board_before.png", full_page=True)
        page.screenshot(path=os.path.join(EV, "board_before.png"), full_page=True)

        # ── step 6: REAL form navigation (type + click submit) ──────────────
        page.fill("input#code", CODE)
        with page.expect_navigation(wait_until="load", timeout=45000) as nav_info:
            page.click('button[type="submit"]')
        redirect_resp = nav_info.value
        page.wait_for_timeout(700)
        records["after_submit"] = {
            "final_url": page.url,
            "final_status": redirect_resp.status if redirect_resp else None,
            "final_content_type": (
                redirect_resp.headers.get("content-type") if redirect_resp else None
            ),
            "title": page.title(),
            "h1": page.evaluate("() => (document.querySelector('h1')||{}).innerText || ''"),
            "body_text_head": page.evaluate("() => document.body.innerText.slice(0, 600)"),
            "awaiting_interstitial": page.evaluate(
                "() => document.body.innerText.includes('Enter passcode')"
            ),
            "json_looking_body": page.evaluate(
                "() => { const t=document.body.innerText.trim(); return t.startsWith('{') && t.includes('\"error\"'); }"
            ),
            "content_marker_present": page.evaluate(
                "() => document.body.innerHTML.includes('G1-CONTENT-MARKER')"
            ),
            "cookie_names": sorted(c["name"] for c in ctx.cookies()),
        }
        page.screenshot(path="/tmp/board_after.png", full_page=True)
        page.screenshot(path=os.path.join(EV, "board_after.png"), full_page=True)

        records["cookies_after"] = [
            {k: c[k] for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")}
            for c in ctx.cookies()
        ]
        records["cookies_after"] = [
            {**c, "value": (c["value"][:10] + "…") if len(c["value"]) > 10 else c["value"]}
            for c in records["cookies_after"]
        ]

        browser.close()

    records["request_response_chain"] = chain
    records["console"] = console
    print(json.dumps(records, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
