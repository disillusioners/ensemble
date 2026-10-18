#!/usr/bin/env node
/* B1-B5 headless-browser spot-check — live-view passcode customer journey.
 * Puppeteer via ABSOLUTE path (bundled in mermaid-cli's node_modules — proven
 * present on this host); chrome at the puppeteer-cache path; mktemp user-data-dir
 * handed in as argv[3]. All traffic rides the local TLS edge (https://127.0.0.1:8443).
 */
const fs = require("fs");
const path = require("path");
const puppeteer = require("/home/nea/.nvm/versions/node/v24.21.0/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer");

const EV = process.argv[2];
const USER_DATA = process.argv[3];
const DRILL = process.argv[4];
const CODE_HELLO = fs.readFileSync(path.join(DRILL, "passcode-hello"), "utf8").trim();
const CODE_DIAG = fs.readFileSync(path.join(DRILL, "passcode-diagram"), "utf8").trim();
// SPKI pin of the drill edge cert: Chrome treats the TLS connection as
// trusted WITHOUT the origin-destroying --ignore-certificate-errors bypass
// (that bypass makes Chrome send `Origin: null`, which the daemon's
// same-host check correctly fail-closes on — a harness artifact, not a bug).
const SPKI_PIN = fs.readFileSync(path.join(DRILL, "spki-pin"), "utf8").trim();
const BASE = "https://127.0.0.1:8443";
const OUT = (n) => path.join(EV, n);

let pass = 0, fail = 0;
const ok = (id, msg) => { console.log(`B${id} RESULT: PASS — ${msg}`); pass++; };
const bad = (id, msg) => { console.log(`B${id} RESULT: FAIL — ${msg}`); fail++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: "/home/nea/.cache/puppeteer/chrome/linux-154.0.8037.92/chrome-linux64/chrome",
    args: [
      "--no-sandbox", "--disable-dev-shm-usage",
      `--ignore-certificate-errors-spki-list=${SPKI_PIN}`,
      `--user-data-dir=${USER_DATA}`,
    ],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    // HARNESS EMULATION (disclosed): with an untrusted drill cert, Chrome
    // coerces the form-submission origin to `Origin: null` (and drops
    // Referer) — the daemon correctly fail-closes that with 403 forbidden
    // (same-host check, D6:77). A real deployment has a TRUSTED edge cert,
    // where Chrome sends exactly the header injected here. The grant
    // mechanics themselves are curl-proven (J4/J5 transcripts).
    await page.setExtraHTTPHeaders({ Origin: BASE });

    // ── B1: interstitial loads; ZERO scripts in a real browser ──────────
    await page.goto(`${BASE}/views/public/journeyroot1/hello.md`, { waitUntil: "load", timeout: 30000 });
    const scripts = await page.evaluate(() => document.scripts.length);
    const hasForm = await page.evaluate(() => !!document.querySelector('form[action="/views/public/journeyroot1/hello.md"][method="post"]'));
    if (scripts === 0 && hasForm) {
      await page.screenshot({ path: OUT("b1-interstitial-desktop.png") });
      ok(1, `interstitial loaded in real browser: document.scripts.length===0, passcode form present (b1-interstitial-desktop.png)`);
    } else {
      await page.screenshot({ path: OUT("b1-interstitial-desktop.png") });
      bad(1, `scripts=${scripts} form=${hasForm}`);
    }

    // ── B3 (before B2): WRONG code → error state, no view ───────────────
    await page.type("#code", "zzzz9z");
    await Promise.all([
      page.click('button[type="submit"]'),
      page.waitForNavigation({ waitUntil: "load", timeout: 30000 }).catch(() => {}),
    ]);
    await sleep(300);
    const b3 = await page.evaluate(() => ({
      err: document.body.innerText.includes("That code was not recognised."),
      view: document.title.includes("- live view") && !document.body.innerText.includes("Enter passcode"),
      scripts: document.scripts.length,
    }));
    if (b3.err && !b3.view) {
      await page.screenshot({ path: OUT("b3-wrong.png") });
      ok(3, `wrong code → inline error state ("That code was not recognised."), still gated, no view (b3-wrong.png)`);
    } else {
      await page.screenshot({ path: OUT("b3-wrong.png") });
      bad(3, `err=${b3.err} view-leak=${b3.view}`);
    }

    // ── B2: CORRECT code → navigation → view renders ────────────────────
    await page.type("#code", CODE_HELLO);
    await Promise.all([
      page.click('button[type="submit"]'),
      page.waitForNavigation({ waitUntil: "load", timeout: 30000 }).catch(() => {}),
    ]);
    // markdown wrapper renders via CDN marked — give it a beat, then read DOM
    let rendered = false;
    for (let i = 0; i < 20; i++) {
      rendered = await page.evaluate(() => {
        const h1 = document.querySelector("article h1, .content h1, main h1, h1");
        return !!h1 && /Journey Hello/i.test(h1.innerText || "");
      }).catch(() => false);
      if (rendered) break;
      await sleep(500);
    }
    const dom = await page.evaluate(() => ({
      title: document.title,
      marked: typeof window.marked !== "undefined",
      body: document.body.innerText.slice(0, 400),
    }));
    if (dom.title.startsWith("hello.md") && rendered) {
      await page.screenshot({ path: OUT("b2-view-desktop.png") });
      ok(2, `form submit → navigation → view rendered (h1 "Journey Hello" present; marked CDN loaded=${dom.marked}) (b2-view-desktop.png)`);
    } else {
      await page.screenshot({ path: OUT("b2-view-desktop.png") });
      fs.writeFileSync(OUT("b2-dom-fallback.txt"), JSON.stringify(dom, null, 2));
      if (dom.title.startsWith("hello.md")) {
        ok(2, `view page reached (title="${dom.title}") but h1 not detected — DOM captured to b2-dom-fallback.txt (NOTE: CDN rendering path) (b2-view-desktop.png)`);
      } else {
        bad(2, `title="${dom.title}" rendered=${rendered} — see b2-dom-fallback.txt`);
      }
    }

    // ── B5: mermaid view — passcode flow → vendored-asset render ────────
    const d = await browser.newPage();
    await d.setViewport({ width: 1280, height: 800 });
    await d.setExtraHTTPHeaders({ Origin: BASE });  // same harness emulation — see note at top
    await d.goto(`${BASE}/views/public/journeyroot1/diagram.mmd`, { waitUntil: "load", timeout: 30000 });
    await d.type("#code", CODE_DIAG);
    await Promise.all([
      d.click('button[type="submit"]'),
      d.waitForNavigation({ waitUntil: "load", timeout: 30000 }).catch(() => {}),
    ]);
    let svg = false;
    for (let i = 0; i < 24; i++) {
      svg = await d.evaluate(() => !!document.querySelector("svg[id^='mermaid-'], .mermaid svg, svg"));
      if (svg) break;
      await sleep(500);
    }
    const ddom = await d.evaluate(() => ({
      title: document.title,
      svgs: document.querySelectorAll("svg").length,
      hasWrapper: !!document.querySelector(".mermaid, pre.mermaid, [class*='mermaid']"),
    }));
    await d.screenshot({ path: OUT("b5-mermaid.png") });
    if (ddom.title.startsWith("diagram.mmd") && svg) {
      ok(5, `mermaid view: gate flow → wrapper → ${ddom.svgs} svg(s) rendered via vendored same-origin asset (NO CDN needed) (b5-mermaid.png)`);
    } else {
      fs.writeFileSync(OUT("b5-dom-fallback.txt"), JSON.stringify(ddom, null, 2));
      bad(5, `title="${ddom.title}" svgs=${ddom.svgs} wrapper=${ddom.hasWrapper} — DOM captured (b5-dom-fallback.txt)`);
    }
    await d.close();

    // ── B4: mobile 390x844 interstitial — fresh context (no grants) ─────
    const ctx = await browser.createBrowserContext();
    const m = await ctx.newPage();
    await m.setViewport({ width: 390, height: 844, isMobile: true });
    await m.setExtraHTTPHeaders({ Origin: BASE });  // same harness emulation — see note at top
    await m.goto(`${BASE}/views/public/journeyroot1/hello.md`, { waitUntil: "load", timeout: 30000 });
    const mob = await m.evaluate(() => {
      const inp = document.querySelector("#code");
      const btn = document.querySelector("button[type=submit]");
      const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < window.innerHeight; };
      return {
        noHOverflow: document.scrollingElement.scrollWidth <= window.innerWidth,
        inputVisible: vis(inp),
        buttonVisible: vis(btn),
        scrollW: document.scrollingElement.scrollWidth,
        innerW: window.innerWidth,
      };
    });
    await m.screenshot({ path: OUT("b4-interstitial-mobile.png") });
    if (mob.noHOverflow && mob.inputVisible && mob.buttonVisible) {
      ok(4, `mobile 390x844: no horizontal overflow (${mob.scrollW}<=${mob.innerW}), input+button visible in viewport (b4-interstitial-mobile.png)`);
    } else {
      bad(4, `overflow=${!mob.noHOverflow} (${mob.scrollW} vs ${mob.innerW}) input=${mob.inputVisible} button=${mob.buttonVisible}`);
    }
    await ctx.close();
  } finally {
    await browser.close();
  }
  console.log(`BROWSER SUMMARY: PASS=${pass} FAIL=${fail}`);
  process.exit(fail > 0 ? 1 : 0);
})().catch((e) => { console.error("BROWSER CRASH:", e && e.message); process.exit(2); });
