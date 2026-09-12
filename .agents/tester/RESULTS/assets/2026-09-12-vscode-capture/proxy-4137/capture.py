#!/usr/bin/env python3
"""Browser capture for 4.137.0-via-proxy decisive experiment.
Same harness as v8 from previous run; output paths updated.
"""
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EVIDENCE = Path("/tmp/vscode-proxy-4137/capture-output")
EVIDENCE.mkdir(parents=True, exist_ok=True)
NET_JSON = EVIDENCE / "network.json"
SCREENSHOT = EVIDENCE / "rendered-or-blocked.png"
CONSOLE_LOG = EVIDENCE / "console.log"

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
_console = []
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
  // Inspect ALL webview iframes for in-iframe naturalWidth (the dispatcher's signal)
  const iframes = q('iframe');
  const iframeInfo = iframes.map((f, i) => {
    let doc = null;
    try { doc = f.contentDocument || f.contentWindow?.document; } catch (e) {}
    if (!doc) return { idx: i, src: f.src, accessible: false};
    const imgs = Array.from(doc.querySelectorAll('img')).map(im => ({
      src: im.src, nw: im.naturalWidth, nh: im.naturalHeight,
      cw: im.clientWidth, ch: im.clientHeight,
      complete: im.complete, alt: im.alt || '',
    }));
    return { idx: i, src: f.src, accessible: true, imgCount: imgs.length, imgs };
  });
  const tabs = q('.tab').map(t => ({
    label: (t.getAttribute('aria-label') || t.textContent || '').trim(),
    active: t.classList.contains('active'),
  }));
  return {
    url: location.href,
    hasWorkbench: !!document.querySelector('.monaco-workbench'),
    quickInputVisible: visible(document.querySelector('.quick-input-widget')),
    treeitemCount: treeitems.length,
    tabs,
    iframeCount: iframes.length,
    iframeInfo,
    imgs: q('img').map(i => ({src: i.src, nw: i.naturalWidth, complete: i.complete})).filter(o => o.nw > 0),
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

        page.on("request", lambda req: _record("request", {
            "url": req.url, "method": req.method,
            "resourceType": req.resource_type,
            "headers": dict(req.headers or {}),
        }))
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
        page.on("console", lambda msg: (_record("console", {"type": msg.type, "text": msg.text[:500]}), _console.append(f"[{msg.type}] {msg.text}")) if msg.type in ("error", "warning") else None)

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

        _phase("EXPAND_PATH")
        ok = expand_path(page, PATH_PARTS)
        _record("expand-result", {"ok": ok})
        time.sleep(2)

        # Single click
        _phase("FIRST_CLICK")
        file_item = find_treeitem(page, IMG_BASENAME)
        if file_item:
            try:
                file_item.click()
                _record("single-click", {"ok": True})
            except Exception as e:
                _record("single-click-err", {"err": str(e)[:200]})
        time.sleep(5)  # generous: 4.137.0 may load faster

        # Observe
        _phase("OBSERVE")
        for i in range(60):  # 60s render patience
            time.sleep(1)
            try:
                # Check both dialog AND in-iframe images
                bt = page.locator("body").inner_text(timeout=500).lower()
                if "error occurred while loading the image" in bt:
                    _record("outcome-detect", {"result": "error-dialog", "i": i})
                    return _finish(page, "error-dialog")
            except Exception:
                pass
            try:
                info = page.evaluate(PROBE)
                # In-iframe naturalWidth>0 (the dispatcher's signal)
                for fi in info.get("iframeInfo", []):
                    for im in fi.get("imgs", []):
                        if im.get("nw", 0) > 0 and im.get("complete"):
                            _record("outcome-detect", {
                                "result": "image-rendered-in-iframe",
                                "i": i, "iframeSrc": fi.get("src"),
                                "img": im,
                            })
                            return _finish(page, "image-rendered", info)
                # Also top-level
                for im in info.get("imgs", []):
                    if im.get("nw", 0) > 0 and "templates.png" not in im.get("src", ""):
                        _record("outcome-detect", {
                            "result": "image-rendered-top",
                            "i": i, "img": im,
                        })
                        return _finish(page, "image-rendered", info)
            except Exception as e:
                _record("probe-err", {"err": str(e)[:200]})
        _record("outcome-detect", {"result": "timeout"})
        return _finish(page, "timeout", None)

def _finish(page, outcome, info=None):
    _phase("FINAL")
    _record("outcome", {"result": outcome})
    if info is None:
        try:
            info = page.evaluate(PROBE)
        except Exception:
            pass
    _record("final-dom", {
        "tabs": info.get("tabs") if info else None,
        "iframeCount": info.get("iframeCount") if info else None,
        "iframeInfo": info.get("iframeInfo") if info else None,
        "imgs": info.get("imgs") if info else None,
        "bodyTextSnippet": info.get("bodyTextSnippet") if info else None,
    })
    try:
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        print(f"[{_now():.1f}s] screenshot saved", flush=True)
    except Exception as e:
        print(f"screenshot err: {e}", flush=True)

    _record("dump-summary", {"totalEvents": len(_events)})
    NET_JSON.write_text(json.dumps({"events": _events}, indent=2))
    CONSOLE_LOG.write_text("\n".join(_console))
    print(f"[{_now():.1f}s] network.json + console.log saved ({len(_events)} events, {len(_console)} console)", flush=True)

    # Triage
    print("\n=== ALL FAILING/MEDIA-PREVIEW/VSCODE-REMOTE-RESOURCE EVENTS ===", flush=True)
    for ev in _events:
        url = ev.get("url", "")
        if ev["kind"] == "requestfailed" or any(k in url.lower() for k in (
            IMG_BASENAME.lower(), "deepcode", "media-preview", "imagepreview",
            "vscode-remote-resource", "vscode-resource",
        )):
            kind = ev["kind"]
            st = ev.get("status", ev.get("failureText", ""))
            print(f"[{ev.get('phase','')[:15]:15}/{kind:15}] [{st}] {url[:300]}", flush=True)

    # Webview CSP context
    print("\n=== WEBVIEW HTML + CSP ===", flush=True)
    seen = set()
    for ev in _events:
        if ev["kind"] == "response" and "webview/browser/pre/index.html" in ev.get("url", ""):
            csp = ev.get("headers", {}).get("content-security-policy", "")
            if ev["url"] not in seen:
                seen.add(ev["url"])
                ext = ev["url"].split("?")[1][:60] if "?" in ev["url"] else ""
                print(f"\nURL: .../webview/browser/pre/index.html?{ext}...", flush=True)
                print(f"CSP HEADER: {csp}", flush=True)
                body = ev.get("bodySnippet", "")
                if "Content-Security-Policy" in body:
                    import re
                    m = re.search(r'Content-Security-Policy"\s*content="([^"]+)"', body)
                    if m:
                        print(f"META CSP:    {m.group(1)}", flush=True)

    # Console dump
    print("\n=== CONSOLE (errors/warnings) ===", flush=True)
    for line in _console[-30:]:
        print(line[:300], flush=True)

    # Print verdict
    print(f"\n=== VERDICT: {outcome} ===", flush=True)
    return outcome

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
    except Exception as e:
        _record("fatal", {"err": str(e), "tb": traceback.format_exc()})
        NET_JSON.write_text(json.dumps({"events": _events, "fatal": str(e)}, indent=2))
        print(f"RESULT: FAIL ({e})", flush=True)
        raise
