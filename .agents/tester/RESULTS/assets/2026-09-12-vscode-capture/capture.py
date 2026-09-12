#!/usr/bin/env python3
"""Capture v8 — capture CSP context, virtual-host URL pattern, and any
subsequent image fetch attempt.

Findings so far:
- Explorer double-click triggers the media-preview extension
- Extension's CSS/JS fetch from https://vscode-remote+localhost-8079.vscode-resource.vscode-cdn.net/...
- These fail with "csp" because webview CSP only allows 'self' scripts
- The actual image URL may use a different shape (path-only, virtual-host, or query-param)
"""
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qsl

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EVIDENCE = Path("/tmp/vscode-capture-2026-09-12")
EVIDENCE.mkdir(parents=True, exist_ok=True)
NET_JSON = EVIDENCE / "network.json"
SCREENSHOT = EVIDENCE / "failing-request.png"

FOLDER = "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"
BASE = f"http://localhost:8079/vscode/?folder={FOLDER}"
IMG_PATH = os.environ["IMG_PATH"]
IMG_BASENAME = Path(IMG_PATH).name
PATH_PARTS = Path(IMG_PATH).relative_to(FOLDER).parts

def _on_alarm(s, f):
    print("=== WATCHDOG ===", flush=True)
    try:
        NET_JSON.write_text(json.dumps({"events": _events, "emergency": True}, indent=2))
    except Exception: pass
    sys.exit(124)
signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(600)

_events = []
_phase_start = [time.time()]
_phase_name = ["INIT"]

def _now(): return round(time.time() - _phase_start[0], 3)
def _phase(n):
    _phase_start[0] = time.time()
    _phase_name[0] = n
    _events.append({"t": 0.0, "kind": "PHASE", "phase": n})
    print(f"[{time.strftime('%H:%M:%S')}] PHASE -> {n}", flush=True)
def _record(k, d):
    _events.append({"t": _now(), "kind": k, "phase": _phase_name[0], **d})

PROBE = """
() => {
  const q = (sel) => Array.from(document.querySelectorAll(sel));
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  const treeitems = q('[role="treeitem"]');
  // Active editor tabs
  const tabs = q('.tab').map(t => ({
    label: (t.getAttribute('aria-label') || t.textContent || '').trim(),
    active: t.classList.contains('active'),
  }));
  // Editor group contents
  const editorContainers = q('.editor-container').map(e => ({
    id: e.id,
    className: (e.className || '').slice(0, 80),
  }));
  return {
    url: location.href,
    hasWorkbench: !!document.querySelector('.monaco-workbench'),
    quickInputVisible: visible(document.querySelector('.quick-input-widget')),
    treeitemCount: treeitems.length,
    tabs,
    editorContainers,
    webviews: q('iframe.webview, iframe[role="presentation"]').length,
    imgs: q('img').map(i => ({src: i.src, nw: i.naturalWidth, complete: i.complete})).filter(o => o.nw > 0 || o.src.includes('.png') || o.src.includes('.jpg')),
    bodyTextSnippet: (document.body.innerText || '').slice(0, 1500),
  };
}
"""

def find_treeitem(page, name):
    items = page.locator('[role="treeitem"]').filter(has_text=name)
    cnt = items.count()
    for i in range(cnt):
        item = items.nth(i)
        if item.is_visible():
            return item
    return None

def expand_path(page, parts):
    """Expand each folder in parts (except last = file)."""
    print(f"[{_now():.1f}s] expanding path: {parts[:-1]}", flush=True)
    for name in parts[:-1]:
        item = find_treeitem(page, name)
        if not item:
            print(f"[{_now():.1f}s] expand: '{name}' not visible", flush=True)
            return False
        expanded = item.get_attribute("aria-expanded")
        if expanded == "true":
            print(f"[{_now():.1f}s] '{name}' already expanded", flush=True)
            continue
        # Click to expand
        try:
            chevron = item.locator(".codicon-chevron-right").first
            if chevron.is_visible():
                chevron.click()
            else:
                item.click()
        except Exception:
            item.click()
        print(f"[{_now():.1f}s] '{name}' clicked", flush=True)
        time.sleep(2)
    return True

def main():
    print(f"Image: {IMG_BASENAME}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.new_page()

        # Capture all requests with full headers
        page.on("request", lambda req: _record("request", {
            "url": req.url, "method": req.method,
            "resourceType": req.resource_type,
            "headers": dict(req.headers or {}),
        }))
        # Capture all responses with full headers + CSP
        page.on("response", lambda resp: _record("response", {
            "url": resp.url, "status": resp.status,
            "headers": dict(resp.headers or {}),
            "bodySnippet": _safe_body(resp),
        }))
        page.on("requestfailed", lambda req: _record("requestfailed", {
            "url": req.url, "method": req.method,
            "resourceType": req.resource_type,
            "failureText": req.failure,
        }))
        # Capture console errors
        page.on("console", lambda msg: _record("console", {
            "type": msg.type, "text": msg.text[:500],
        }) if msg.type in ("error", "warning") else None)
        # Capture page errors
        page.on("pageerror", lambda err: _record("pageerror", {"text": str(err)[:500]}))

        _phase("NAVIGATE")
        page.goto(BASE, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_selector(".monaco-workbench", timeout=60_000)

        _phase("SETTLE")
        for i in range(90):
            time.sleep(1)
            try:
                bt = page.evaluate("() => document.body.innerText || ''")
                if ".inspiration-projects" in bt:
                    print(f"[{_now():.1f}s] Explorer settled (i={i})", flush=True)
                    break
            except Exception:
                pass
        time.sleep(5)

        # Expand path
        _phase("EXPAND_PATH")
        ok = expand_path(page, PATH_PARTS)
        _record("expand-result", {"ok": ok})

        time.sleep(2)

        # Initial state
        try:
            info = page.evaluate(PROBE)
            _record("probe-pre-click", {"tabs": info.get("tabs"), "treeitemCount": info.get("treeitemCount")})
        except Exception:
            pass

        # First click — open the file
        _phase("FIRST_CLICK")
        file_item = find_treeitem(page, IMG_BASENAME)
        if file_item:
            try:
                # Try single click first (some workbenches need this to select)
                file_item.click()
                time.sleep(1)
                _record("single-click", {"ok": True})
            except Exception as e:
                _record("single-click-err", {"err": str(e)[:200]})

        time.sleep(3)

        # Probe — see if anything opened
        try:
            info = page.evaluate(PROBE)
            _record("probe-after-first-click", {
                "tabs": info.get("tabs"),
                "webviews": info.get("webviews"),
                "imgs": info.get("imgs"),
                "bodyTextSnippet": info.get("bodyTextSnippet"),
            })
            print(f"[{_now():.1f}s] tabs: {[t.get('label')[:30] for t in info.get('tabs', [])]}", flush=True)
            print(f"[{_now():.1f}s] webviews: {info.get('webviews')}", flush=True)
            print(f"[{_now():.1f}s] imgs (with .png/.jpg): {info.get('imgs')[:3]}", flush=True)
            # Check if body has error text
            bt = info.get("bodyTextSnippet", "").lower()
            if "error occurred while loading" in bt:
                print(f"[{_now():.1f}s] *** ERROR DIALOG PRESENT ***", flush=True)
        except Exception as e:
            _record("probe-after-first-click-err", {"err": str(e)[:200]})

        # Try double-click — the canonical open action
        _phase("DOUBLE_CLICK")
        if file_item:
            try:
                # Re-locate because DOM may have refreshed
                file_item2 = find_treeitem(page, IMG_BASENAME)
                if file_item2:
                    file_item2.dblclick()
                    _record("dblclick", {"ok": True})
            except Exception as e:
                _record("dblclick-err", {"err": str(e)[:200]})
        time.sleep(3)

        # Probe
        try:
            info = page.evaluate(PROBE)
            _record("probe-after-dblclick", {
                "tabs": info.get("tabs"),
                "webviews": info.get("webviews"),
                "imgs": info.get("imgs"),
                "bodyTextSnippet": info.get("bodyTextSnippet"),
            })
            print(f"[{_now():.1f}s] (after dblclick) tabs: {[t.get('label')[:30] for t in info.get('tabs', [])]}", flush=True)
            print(f"[{_now():.1f}s] (after dblclick) webviews: {info.get('webviews')}", flush=True)
        except Exception:
            pass

        # Observe
        _phase("OBSERVE")
        outcome = _observe(page, "FINAL", 45)
        _record("outcome", {"result": outcome})

        # Final probe
        try:
            info = page.evaluate(PROBE)
            _record("final-dom", {
                "tabs": info.get("tabs"),
                "webviews": info.get("webviews"),
                "imgs": info.get("imgs"),
                "bodyTextSnippet": info.get("bodyTextSnippet"),
            })
        except Exception:
            pass

        try:
            page.screenshot(path=str(SCREENSHOT), full_page=True)
        except Exception as e:
            print(f"final screenshot err: {e}", flush=True)

        _record("dump-summary", {"totalEvents": len(_events)})
        NET_JSON.write_text(json.dumps({"events": _events}, indent=2))
        print(f"[{_now():.1f}s] network.json saved ({len(_events)} events)", flush=True)

        # Triage — print FAILING requests + image-related + media-preview
        print("\n=== ALL REQUEST-FAILED + IMAGE/PREVIEW/MEDIA EVENTS ===", flush=True)
        for ev in _events:
            url = ev.get("url", "")
            if ev["kind"] == "requestfailed" or any(k in url.lower() for k in (
                IMG_BASENAME.lower(), "deepcode", "media-preview", "imagePreview",
                "vscode-remote-resource", "vscode-resource",
            )):
                kind = ev["kind"]
                st = ev.get("status", ev.get("failureText", ""))
                print(f"[{ev.get('phase','')[:15]:15}/{kind:15}] [{st}] {url[:350]}", flush=True)

        # Also print the CSP from the webview response
        print("\n=== WEBVIEW CSP HEADERS ===", flush=True)
        for ev in _events:
            if ev["kind"] == "response" and "webview/browser/pre/index.html" in ev.get("url", ""):
                csp = ev.get("headers", {}).get("content-security-policy", "")
                print(f"URL: {ev.get('url','')[:200]}", flush=True)
                print(f"CSP: {csp}", flush=True)
                print(f"Body snippet (first 500 chars): {ev.get('bodySnippet','')[:500]}", flush=True)
                print("---", flush=True)

        browser.close()

def _observe(page, label, max_seconds):
    for i in range(max_seconds):
        time.sleep(1)
        try:
            bt = page.locator("body").inner_text(timeout=500).lower()
            if "error occurred while loading the image" in bt:
                print(f"[{_now():.1f}s] OUTCOME-{label}: error-dialog (i={i})", flush=True)
                _record("outcome-detect", {"label": label, "result": "error-dialog"})
                return "error-dialog"
        except Exception:
            pass
        try:
            imgs = page.evaluate("""() => Array.from(document.querySelectorAll('img')).map(i => ({
              src: i.src, nw: i.naturalWidth, complete: i.complete
            })).filter(o => o.nw > 100 && o.complete && !o.src.includes('templates.png'))""")
            if imgs:
                print(f"[{_now():.1f}s] OUTCOME-{label}: image-rendered (i={i}) imgs={imgs[:2]}", flush=True)
                _record("outcome-detect", {"label": label, "result": "image-rendered", "imgs": imgs[:3]})
                return "image-rendered"
        except Exception:
            pass
    print(f"[{_now():.1f}s] OUTCOME-{label}: timeout", flush=True)
    return "timeout"

def _safe_body(resp):
    try:
        body = resp.body()
        if not body: return ""
        snip = body[:500]
        try: return snip.decode("utf-8", errors="replace")
        except: return repr(snip)
    except Exception as e:
        return f"<err: {e}>"

if __name__ == "__main__":
    try:
        main()
        print("RESULT: CAPTURED", flush=True)
    except Exception as e:
        _record("fatal", {"err": str(e), "tb": traceback.format_exc()})
        NET_JSON.write_text(json.dumps({"events": _events, "fatal": str(e)}, indent=2))
        print(f"RESULT: FAIL ({e})", flush=True)
        raise
