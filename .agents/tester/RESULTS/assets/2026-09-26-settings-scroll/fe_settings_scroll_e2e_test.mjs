#!/usr/bin/env node
/**
 * E2E visual verification pack — Settings scroll-fix acceptance gate (Playwright)
 * Commission: fix/settings-scroll @ dd14d975 — Settings page must scroll in a real
 * browser and the R15 Snapshot toggle must be reachable + operable.
 *
 * Run: cd /home/nea/ensemble-src/frontend && timeout 280 node <this-file>
 * Playwright resolved from frontend/node_modules via absolute createRequire.
 *
 * Dual-layer timeout:
 *   Layer 1 (outer): `timeout 280 node ...` executed by the operator.
 *   Layer 2 (inner): 240s watchdog below -> close browser, RESULT: TIMEOUT, exit 124.
 *
 * Constraints honored: read-only against FE 4199 / daemon 8079 except the R15
 * toggle flip + revert (commission-mandated operability check; env restored).
 */

import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';

const requireFromFe = createRequire('/home/nea/ensemble-src/frontend/node_modules/');
const { chromium } = requireFromFe('playwright');

const BASE = 'http://127.0.0.1:4199';
const DIR = '/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll';
const WATCHDOG_MS = 240_000;

mkdirSync(DIR, { recursive: true });

const T0 = Date.now();
const t = () => `${((Date.now() - T0) / 1000).toFixed(1)}s`;

let browser = null;
const wd = setTimeout(async () => {
  console.log(`\n[${t()}] FATAL: internal watchdog fired (${WATCHDOG_MS / 1000}s) — aborting`);
  try { if (browser) await browser.close(); } catch { /* already closed */ }
  console.log('RESULT: TIMEOUT');
  process.exit(124);
}, WATCHDOG_MS);

// ── scenario bookkeeping ─────────────────────────────────────────────────────
const scenarioResults = [];
let cur = null;
function S(name) {
  cur = { name, pass: true, fails: [] };
  scenarioResults.push(cur);
  console.log(`\n━━━ SCENARIO ${name} ━━━`);
}
function chk(label, ok, detail) {
  if (cur && !ok) { cur.pass = false; cur.fails.push(`${label}: ${detail}`); }
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${label} | ${detail}`);
}

// ── network capture: R15 snapshot-create API ─────────────────────────────────
const snapApi = [];
function wireSnapApi(page) {
  page.on('response', async (r) => {
    if (!r.url().includes('/api/settings/snapshot-create')) return;
    let reqBody = null, resBody = null;
    try { reqBody = r.request().postData(); } catch { /* no body */ }
    try { resBody = await r.text(); } catch { /* body unreadable */ }
    const entry = { method: r.request().method(), status: r.status(), url: r.url(), reqBody, resBody, at: t() };
    snapApi.push(entry);
    console.log(`  [NET] ${entry.method} ${entry.url} -> ${entry.status} | req=${reqBody} | res=${(resBody || '').slice(0, 140)}`);
  });
  page.on('pageerror', (e) => console.log(`  [PAGEERROR] ${e.message?.slice(0, 200)}`));
}

// ── measurement helpers ──────────────────────────────────────────────────────
async function scrollMetrics(page) {
  return page.evaluate(() => {
    const se = document.scrollingElement;
    const c = document.querySelector('.settings-container');
    const m = (el) => el ? ({
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
      scrollTop: el.scrollTop, scrollable: el.scrollHeight > el.clientHeight,
      overflowY: getComputedStyle(el).overflowY,
    }) : null;
    return {
      window: { ...m(se), scrollY: window.scrollY, innerHeight: window.innerHeight },
      settingsContainer: m(c),
    };
  });
}

async function scrollToBottom(page) {
  await page.evaluate(() => {
    window.scrollTo(0, 999999);
    const c = document.querySelector('.settings-container');
    if (c) c.scrollTop = c.scrollHeight;
  });
  await page.waitForTimeout(300);
  return page.evaluate(() => {
    const se = document.scrollingElement;
    const c = document.querySelector('.settings-container');
    return {
      winScrollY: window.scrollY, winMax: se.scrollHeight - se.clientHeight,
      elScrollTop: c ? Math.round(c.scrollTop) : null,
      elMax: c ? c.scrollHeight - c.clientHeight : null,
    };
  });
}

async function sectionsList(page) {
  return page.evaluate(() => {
    const secs = Array.from(document.querySelectorAll('section.setting-section'));
    return secs.map((s, i) => {
      const h2 = s.querySelector('h2');
      const b = s.getBoundingClientRect();
      return { i, title: h2 ? h2.textContent.replace(/\s+/g, ' ').trim() : '(no h2)',
               top: Math.round(b.top), bottom: Math.round(b.bottom), height: Math.round(b.height) };
    });
  });
}

async function readToggle(page) {
  return page.evaluate(() => {
    const on = document.querySelector('input[type=radio][name="snapshot-create-preference"][value="on"]');
    const off = document.querySelector('input[type=radio][name="snapshot-create-preference"][value="off"]');
    const labels = Array.from(document.querySelectorAll('label.editor-option'));
    const snapChecked = document.querySelector('input[name="snapshot-create-preference"]:checked');
    const snapSelectedLabel = snapChecked
      ? (snapChecked.closest('label')?.querySelector('.editor-option-label')?.textContent || '').replace(/\s+/g, ' ').trim()
      : null;
    return {
      present: !!on && !!off,
      onChecked: on ? on.checked : null,
      offChecked: off ? off.checked : null,
      onDisabled: on ? on.disabled : null,
      selectedLabel: snapSelectedLabel,
      radioCount: document.querySelectorAll('input[type=radio][name="snapshot-create-preference"]').length,
      labelCount: labels.length,
    };
  });
}

/** The Agent Snapshots section (scoped locator for Apply button / dirty hint). */
function snapshotSection(page) {
  return page.locator('section.setting-section').filter({ has: page.locator('h2', { hasText: 'Agent Snapshots' }) });
}

/** Read the Apply button + dirty-hint state inside the Agent Snapshots section. */
async function readApplyState(page) {
  const sec = snapshotSection(page);
  const btn = sec.locator('.editor-actions button').first();
  const dirtyCount = await sec.locator('.dirty-hint').count();
  let btnText = null, disabled = null;
  try { btnText = (await btn.textContent())?.replace(/\s+/g, ' ').trim(); disabled = await btn.isDisabled(); } catch { /* absent */ }
  return { btnText, applyDisabled: disabled, dirtyHintVisible: dirtyCount > 0 };
}

/** Click Apply in the Agent Snapshots section (the persistence gate). */
async function clickApply(page) {
  await snapshotSection(page).locator('.editor-actions button').first().click({ timeout: 5000 });
}

async function clickRadio(page, value) {
  const radio = page.locator(`input[type=radio][name="snapshot-create-preference"][value="${value}"]`);
  try {
    await radio.check({ timeout: 4000 });
    return 'input.check() (radio directly)';
  } catch {
    const labels = page.locator('label.editor-option');
    const n = await labels.count();
    for (let i = 0; i < n; i++) {
      const l = labels.nth(i);
      const inp = l.locator(`input[value="${value}"]`);
      if (await inp.count() > 0) { await l.click({ timeout: 4000 }); return `label.editor-option[${i}].click() (label fallback)`; }
    }
    throw new Error(`could not click radio value=${value} via input or label`);
  }
}

async function waitForSnapApi(method, sinceIndex, timeoutMs = 6000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hit = snapApi.slice(sinceIndex).find((e) => e.method === method);
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 120));
  }
  return null;
}

const inVh = (box, vh, tol = 1) => !!box && box.y >= -tol && box.y < vh;
const boxStr = (b) => b ? `{y:${Math.round(b.y)}, bottom:${Math.round(b.y + b.height)}, h:${Math.round(b.height)}}` : 'null';

// ═════════════════════════════════════════════════════════════════════════════
async function main() {
  console.log(`=== E2E: settings scroll-fix acceptance gate ===`);
  console.log(`[${t()}] target=${BASE} assetDir=${DIR}`);
  let context;
  try {
    browser = await chromium.launch({ headless: true });
  } catch (e) {
    console.log(`[${t()}] default launch failed (${e.message.slice(0, 120)}) — retrying with --no-sandbox`);
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  }
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  wireSnapApi(page);

  // ── SCENARIO 1: open app root @1440x900, navigate to Settings ──────────────
  S('1. open-app-navigate-settings @1440x900');
  try {
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.app-header', { timeout: 15000 });
    const appTitle = (await page.locator('.app-title').textContent())?.trim();
    chk('app shell renders (.app-title)', !!appTitle, `text="${appTitle}"`);
    await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.settings-container', { timeout: 20000 });
    // Agent Snapshots section renders async after its API read — wait for radios
    await page.waitForSelector('input[type=radio][name="snapshot-create-preference"]', { timeout: 15000 });
    await page.waitForTimeout(600);
    const sectCount = await page.locator('section.setting-section').count();
    chk('settings route renders with sections', sectCount >= 5, `section.setting-section count=${sectCount}`);
    await page.screenshot({ path: `${DIR}/01-settings-top-1440.png` });
    chk('screenshot 01 captured', true, `${DIR}/01-settings-top-1440.png`);
  } catch (e) { chk('scenario 1 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── SCENARIO 2: scroll gate ─────────────────────────────────────────────────
  S('2. scroll-gate @1440x900');
  try {
    const m0 = await scrollMetrics(page);
    console.log(`  [measured] window: scrollHeight=${m0.window.scrollHeight} clientHeight=${m0.window.clientHeight} scrollable=${m0.window.scrollable} overflowY=${m0.window.overflowY}`);
    console.log(`  [measured] .settings-container: scrollHeight=${m0.settingsContainer?.scrollHeight} clientHeight=${m0.settingsContainer?.clientHeight} scrollTop=${m0.settingsContainer?.scrollTop} scrollable=${m0.settingsContainer?.scrollable} overflowY=${m0.settingsContainer?.overflowY}`);
    const containerScrolls = !!m0.settingsContainer?.scrollable;
    const windowScrolls = m0.window.scrollable;
    chk('page is scrollable by SOME mechanism', containerScrolls || windowScrolls,
      `container.scrollable=${containerScrolls} window.scrollable=${windowScrolls}`);
    chk('FIX CONFIRMED: .settings-container is the scroll container', containerScrolls,
      `overflowY=${m0.settingsContainer?.overflowY} scrollHeight=${m0.settingsContainer?.scrollHeight} vs clientHeight=${m0.settingsContainer?.clientHeight}`);

    const after = await scrollToBottom(page);
    console.log(`  [measured] after scrollToBottom: elScrollTop=${after.elScrollTop}/elMax=${after.elMax} winScrollY=${after.winScrollY}/winMax=${after.winMax}`);
    if (containerScrolls) {
      chk('container reached max scroll', after.elScrollTop !== null && Math.abs(after.elScrollTop - after.elMax) <= 2,
        `scrollTop=${after.elScrollTop} max=${after.elMax}`);
    }
    const vh = m0.window.innerHeight;
    const secs = await sectionsList(page);
    const last = secs[secs.length - 1];
    const snap = secs.find((s) => /agent snapshots/i.test(s.title));
    chk('last section bottom within viewport', last && last.bottom <= vh + 2 && last.bottom > 0,
      `last="${last?.title}" bottom=${last?.bottom} vh=${vh}`);
    chk('Agent Snapshots section in view at bottom', snap && snap.top < vh && snap.bottom > 0,
      `box={top:${snap?.top}, bottom:${snap?.bottom}} vh=${vh}`);
    await page.screenshot({ path: `${DIR}/02-settings-bottom-1440.png` });
    chk('screenshot 02 captured', true, `${DIR}/02-settings-bottom-1440.png`);
  } catch (e) { chk('scenario 2 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── SCENARIO 3: section matrix ──────────────────────────────────────────────
  S('3. section-reachability-matrix @1440x900');
  try {
    const vh = await page.evaluate(() => window.innerHeight);
    const locs = page.locator('section.setting-section');
    const n = await locs.count();
    const rows = [];
    for (let i = 0; i < n; i++) {
      const loc = locs.nth(i);
      const title = (await loc.locator('h2').first().textContent({ timeout: 5000 }).catch(() => null) || '(no h2)').replace(/\s+/g, ' ').trim();
      await loc.scrollIntoViewIfNeeded({ timeout: 5000 });
      await page.waitForTimeout(220);
      const box = await loc.boundingBox();
      const reachable = inVh(box, vh);
      rows.push({ title, inDom: true, reachable, box: boxStr(box), vh });
      console.log(`  [matrix] ${title.padEnd(28)} | inDOM=${'yes'} | scroll-reachable=${reachable ? 'YES' : 'NO'} | ${boxStr(box)} vh=${vh}`);
    }
    chk('>=5 sections rendered', n >= 5, `count=${n}`);
    const expected = ['Language Preference', 'Editor', 'Blueprint Peak Hours', 'Agent Snapshots', 'Snapshot Usage Metrics'];
    const titles = rows.map((r) => r.title);
    for (const exp of expected) {
      chk(`expected section "${exp}" present+reachable`, titles.some((x) => x.toLowerCase() === exp.toLowerCase()) &&
        rows.find((r) => r.title.toLowerCase() === exp.toLowerCase())?.reachable,
        `found=${titles.some((x) => x.toLowerCase() === exp.toLowerCase())} reachable=${rows.find((r) => r.title.toLowerCase() === exp.toLowerCase())?.reachable ?? 'n/a'}`);
    }
    chk('ALL sections scroll-reachable', rows.every((r) => r.reachable), `${rows.filter((r) => r.reachable).length}/${rows.length} reachable`);
  } catch (e) { chk('scenario 3 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── SCENARIO 4: R15 toggle reach → operate → persist-observe → revert ───────
  S('4. r15-toggle-reach-operate-persist');
  let originalOn = null;
  try {
    const radioOn = page.locator('input[type=radio][name="snapshot-create-preference"][value="on"]');
    await radioOn.scrollIntoViewIfNeeded({ timeout: 8000 });
    await page.waitForTimeout(250);
    const vh = await page.evaluate(() => window.innerHeight);
    const rbox = await radioOn.boundingBox();
    chk('R15 toggle ("on" radio) visible in viewport after scroll', inVh(rbox, vh), `${boxStr(rbox)} vh=${vh}`);

    const st0 = await readToggle(page);
    const apply0 = await readApplyState(page);
    const getEntry0 = snapApi.filter((e) => e.method === 'GET').pop();
    const getEnabled0 = getEntry0 ? (() => { try { return JSON.parse(getEntry0.resBody || '{}').enabled; } catch { return null; } })() : null;
    originalOn = st0.onChecked;
    console.log(`  [measured] initial: onChecked=${st0.onChecked} offChecked=${st0.offChecked} selectedLabel="${st0.selectedLabel}" radios=${st0.radioCount} GET.enabled=${getEnabled0} applyDisabled=${apply0.applyDisabled} dirtyHint=${apply0.dirtyHintVisible}`);
    chk('radio group rendered (2 radios)', st0.present && st0.radioCount === 2, `radios=${st0.radioCount}`);
    chk('initial toggle state consistent (exactly one checked)', (st0.onChecked === true) !== (st0.offChecked === true),
      `on=${st0.onChecked} off=${st0.offChecked}`);
    chk('initial state clean (Apply disabled, no dirty hint)', apply0.applyDisabled === true && apply0.dirtyHintVisible === false,
      `applyDisabled=${apply0.applyDisabled} dirtyHint=${apply0.dirtyHintVisible}`);
    await page.screenshot({ path: `${DIR}/03-toggle-before.png` });
    chk('screenshot 03 captured', true, `${DIR}/03-toggle-before.png`);

    // CLICK "on" radio → flips the WORKING selection (Apply-gated save by design)
    const netIdx = snapApi.length;
    const mech = await clickRadio(page, 'on');
    await page.waitForTimeout(350);
    const st1 = await readToggle(page);
    const apply1 = await readApplyState(page);
    console.log(`  [measured] click mechanism: ${mech}; after radio click: onChecked=${st1.onChecked} offChecked=${st1.offChecked} selectedLabel="${st1.selectedLabel}" applyDisabled=${apply1.applyDisabled} dirtyHint=${apply1.dirtyHintVisible}`);
    chk('radio click flipped WORKING selection (control state)', st1.onChecked === true && st1.offChecked === false,
      `on=${st1.onChecked} off=${st1.offChecked} selectedLabel="${st1.selectedLabel}"`);
    chk('dirty state surfaced (Apply enabled + "Unsaved changes")', apply1.applyDisabled === false && apply1.dirtyHintVisible === true,
      `applyDisabled=${apply1.applyDisabled} dirtyHint=${apply1.dirtyHintVisible} btn="${apply1.btnText}"`);
    chk('no PUT fired on bare radio click (Apply-gated by design)', !snapApi.slice(netIdx).some((e) => e.method === 'PUT'),
      `PUT count since click=${snapApi.slice(netIdx).filter((e) => e.method === 'PUT').length}`);

    // CLICK APPLY → persistence
    await clickApply(page);
    const put = await waitForSnapApi('PUT', netIdx);
    await page.waitForTimeout(500);
    const apply1b = await readApplyState(page);
    console.log(`  [measured] after Apply: applyDisabled=${apply1b.applyDisabled} dirtyHint=${apply1b.dirtyHintVisible}`);
    chk('PUT network call fired by Apply', !!put, put ? `${put.method} ${put.status} req=${put.reqBody} res=${(put.resBody || '').slice(0, 100)}` : 'no PUT captured');
    chk('PUT returned 2xx', !!put && put.status >= 200 && put.status < 300, `status=${put?.status}`);
    chk('PUT request body enabled=true', !!put && (put.reqBody || '').includes('"enabled":true'), `reqBody=${put?.reqBody}`);
    chk('dirty state cleared after Apply', apply1b.applyDisabled === true && apply1b.dirtyHintVisible === false,
      `applyDisabled=${apply1b.applyDisabled} dirtyHint=${apply1b.dirtyHintVisible}`);
    await page.screenshot({ path: `${DIR}/04-toggle-after.png` });
    chk('screenshot 04 captured', true, `${DIR}/04-toggle-after.png`);

    // RELOAD → persistence observation
    const getIdx = snapApi.length;
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('input[type=radio][name="snapshot-create-preference"]', { timeout: 15000 });
    const getAfter = (await waitForSnapApi('GET', getIdx, 8000)) || snapApi.filter((e) => e.method === 'GET').pop();
    await page.waitForTimeout(500);
    const st2 = await readToggle(page);
    const getEnabledAfter = getAfter ? (() => { try { return JSON.parse(getAfter.resBody || '{}').enabled; } catch { return null; } })() : null;
    console.log(`  [measured] after reload: onChecked=${st2.onChecked} offChecked=${st2.offChecked} GET.enabled=${getEnabledAfter}`);
    const persisted = st2.onChecked === true && getEnabledAfter === true;
    chk('persistence verdict = PERSISTED (else RESET)', persisted,
      persisted ? `onChecked=${st2.onChecked} matches pre-reload, GET.enabled=${getEnabledAfter}` :
      `RESET observed: onChecked=${st2.onChecked} GET.enabled=${getEnabledAfter} (pre-reload onChecked=${st1.onChecked})`);

    // revert to original state (radio + Apply)
    if (originalOn === false) {
      const idx2 = snapApi.length;
      const mech2 = await clickRadio(page, 'off');
      await page.waitForTimeout(300);
      await clickApply(page);
      const put2 = await waitForSnapApi('PUT', idx2);
      await page.waitForTimeout(500);
      const st3 = await readToggle(page);
      console.log(`  [measured] revert: mechanism=${mech2}+Apply onChecked=${st3.onChecked} offChecked=${st3.offChecked} PUT=${put2 ? `${put2.status} ${put2.reqBody}` : 'none'}`);
      chk('reverted to original state (disabled)', st3.onChecked === false && st3.offChecked === true,
        `on=${st3.onChecked} off=${st3.offChecked} PUT=${put2 ? put2.status : 'none'}`);
      chk('revert PUT returned 2xx with enabled=false', !!put2 && put2.status >= 200 && put2.status < 300 && (put2.reqBody || '').includes('"enabled":false'),
        `status=${put2?.status} reqBody=${put2?.reqBody}`);
    } else {
      chk('revert to original state', originalOn === true, `original was enabled; current onChecked=${(await readToggle(page)).onChecked} — no revert click needed`);
    }
    const stFinal = await readToggle(page);
    console.log(`  [measured] FINAL state left: onChecked=${stFinal.onChecked} (original=${originalOn})`);
  } catch (e) { chk('scenario 4 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── SCENARIO 5: fold-rescue @1280x720 ───────────────────────────────────────
  S('5. fold-rescue @1280x720');
  try {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.waitForTimeout(400);
    const m = await scrollMetrics(page);
    console.log(`  [measured] @720 container: scrollHeight=${m.settingsContainer?.scrollHeight} clientHeight=${m.settingsContainer?.clientHeight} scrollable=${m.settingsContainer?.scrollable}`);
    chk('container scrollable @1280x720', !!m.settingsContainer?.scrollable,
      `scrollHeight=${m.settingsContainer?.scrollHeight} clientHeight=${m.settingsContainer?.clientHeight}`);
    const after = await scrollToBottom(page);
    console.log(`  [measured] after scrollToBottom: elScrollTop=${after.elScrollTop}/elMax=${after.elMax}`);
    const vh = 720;
    const secs = await sectionsList(page);
    const snap = secs.find((s) => /agent snapshots/i.test(s.title));
    chk('Agent Snapshots section reachable @720', snap && snap.top < vh && snap.bottom > 0,
      `box={top:${snap?.top}, bottom:${snap?.bottom}} vh=${vh}`);
    const radioOn = page.locator('input[type=radio][name="snapshot-create-preference"][value="on"]');
    await radioOn.scrollIntoViewIfNeeded({ timeout: 6000 });
    await page.waitForTimeout(200);
    const rbox = await radioOn.boundingBox();
    chk('R15 toggle reachable @720', inVh(rbox, vh), `${boxStr(rbox)} vh=${vh}`);
    await page.screenshot({ path: `${DIR}/05-settings-bottom-720.png` });
    chk('screenshot 05 captured', true, `${DIR}/05-settings-bottom-720.png`);
  } catch (e) { chk('scenario 5 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── SCENARIO 6: regression sweep (reported, non-gating) ─────────────────────
  S('6. regression-sweep (reported, non-gating)');
  const hscroll = async () => page.evaluate(() => {
    const se = document.scrollingElement;
    return { scrollWidth: se.scrollWidth, clientWidth: se.clientWidth, ok: se.scrollWidth <= se.clientWidth };
  });
  try {
    // (a) home
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.app-header', { timeout: 15000 });
    let homeEl = null;
    try { homeEl = await page.waitForSelector('.home-container', { timeout: 8000 }); } catch { /* home may redirect */ }
    const title = (await page.locator('.app-title').textContent())?.trim();
    chk('6a home: app shell renders', title === 'Agents Ensemble', `.app-title="${title}" .home-container=${!!homeEl}`);
    const hm = await page.evaluate(() => {
      const se = document.scrollingElement;
      return { sh: se.scrollHeight, ch: se.clientHeight, scrollable: se.scrollHeight > se.clientHeight };
    });
    if (hm.scrollable) {
      await page.evaluate(() => window.scrollTo(0, 999999));
      await page.waitForTimeout(250);
      const y = await page.evaluate(() => window.scrollY);
      chk('6a home: scrollable + scrolls', y > 0, `scrollHeight=${hm.sh} clientHeight=${hm.ch} scrollY-after=${y}`);
    } else {
      chk('6a home: legitimately fits (no scroll needed)', true, `scrollHeight=${hm.sh} clientHeight=${hm.ch} → fits`);
    }
    const hh = await hscroll();
    chk('6a home: no horizontal scrollbar', hh.ok, `scrollWidth=${hh.scrollWidth} clientWidth=${hh.clientWidth}`);
    await page.screenshot({ path: `${DIR}/06-regression-home.png` });
    chk('screenshot 06 captured', true, `${DIR}/06-regression-home.png`);

    // (b) /sources + /migration
    for (const route of ['/sources', '/migration']) {
      const sel = route === '/sources' ? '.sources-container' : '.migration-container';
      try {
        await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 20000 });
        const el = await page.waitForSelector(sel, { timeout: 10000 });
        const rm = await page.evaluate(() => {
          const se = document.scrollingElement;
          return { sh: se.scrollHeight, ch: se.clientHeight, scrollable: se.scrollHeight > se.clientHeight };
        });
        const rh = await hscroll();
        chk(`6b ${route}: renders`, !!el, `${sel} visible; scrollHeight=${rm.sh} clientHeight=${rm.ch} scrollable=${rm.scrollable} hscroll-ok=${rh.ok}`);
      } catch (e) {
        chk(`6b ${route}: renders`, false, `ERROR: ${e.message.slice(0, 140)}`);
      }
    }

    // (c) chat overlay via instances list → first instance card
    await page.goto(`${BASE}/instances`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.instances, .empty-state', { timeout: 15000 });
    const cardCount = await page.locator('a.instance-item').count();
    if (cardCount === 0) {
      chk('6c chat overlay: OPENABLE CHECK SKIPPED — no instance cards exist', true,
        `a.instance-item count=0; chat overlay not mountable without an instance — not fabricating`);
    } else {
      await page.locator('a.instance-item').first().click({ timeout: 8000 });
      const chatEl = await page.waitForSelector('app-chat', { timeout: 15000 });
      await page.waitForTimeout(1200); // lazy VCR mount + first paint
      const chat = await page.evaluate(() => {
        const el = document.querySelector('app-chat');
        if (!el) return null;
        const cs = getComputedStyle(el);
        const b = el.getBoundingClientRect();
        return { display: cs.display, zIndex: cs.zIndex, w: Math.round(b.width), h: Math.round(b.height), visible: cs.display !== 'none' && b.width > 0 && b.height > 0 };
      });
      chk('6c chat overlay: visible', !!chat && chat.visible, `display=${chat?.display} box=${chat?.w}x${chat?.h}`);
      chk('6c chat overlay: z-index == 90', chat?.zIndex === '90', `computed z-index=${chat?.zIndex} (expect 90)`);
      const ch = await hscroll();
      chk('6c chat open: no horizontal scrollbar', ch.ok, `scrollWidth=${ch.scrollWidth} clientWidth=${ch.clientWidth}`);
      await page.screenshot({ path: `${DIR}/07-chat-overlay.png` });
      chk('screenshot 07 captured', true, `${DIR}/07-chat-overlay.png`);

      // workspace overlay via project tab bar (header only has a HIDE affordance by design)
      const tabWsBtn = page.locator('app-project-tab-bar button').filter({ hasText: /editor|workspace/i }).first();
      const tabWsAlt = page.locator('app-project-tab-bar button.workspace-btn, app-project-tab-bar [class*="workspace"]').first();
      const btn = (await tabWsBtn.count()) ? tabWsBtn : ((await tabWsAlt.count()) ? tabWsAlt : null);
      if (btn) {
        try {
          await btn.click({ timeout: 5000 });
          await page.waitForTimeout(900);
          const ws = await page.evaluate(() => {
            const el = document.querySelector('app-workspace');
            if (!el) return null;
            const cs = getComputedStyle(el);
            return { display: cs.display, zIndex: cs.zIndex };
          });
          chk('6c workspace overlay: opens + z-index == 100', !!ws && ws.display !== 'none' && ws.zIndex === '100',
            `display=${ws?.display} z-index=${ws?.zIndex} (expect flex/100)`);
          const wh = await hscroll();
          chk('6c workspace open: no horizontal scrollbar', wh.ok, `scrollWidth=${wh.scrollWidth} clientWidth=${wh.clientWidth}`);
        } catch (e) {
          chk('6c workspace overlay open attempt', false, `ERROR: ${e.message.slice(0, 140)}`);
        }
      } else {
        chk('6c workspace overlay: open affordance NOT found via header/tab-bar', true,
          'header exposes only .overlay-hide-btn (hide-only by design); no workspace-open button found in project tab bar — not fabricating');
      }
    }

    // plane overlay: only if actually mounted
    await page.goto(`${BASE}/plan`, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(800);
    const plane = await page.evaluate(() => {
      const el = document.querySelector('.plane-overlay');
      if (!el) return { mounted: false };
      const cs = getComputedStyle(el);
      return { mounted: true, display: cs.display, zIndex: cs.zIndex };
    });
    if (plane.mounted && plane.display !== 'none') {
      chk('6c plane overlay: z-index == 1000', plane.zIndex === '1000', `display=${plane.display} z-index=${plane.zIndex}`);
    } else {
      chk('6c plane overlay: not mounted, not verifiable', true,
        plane.mounted ? `.plane-overlay present but display=${plane.display}` : '.plane-overlay absent from DOM (planeEnabled() false)');
    }
  } catch (e) { chk('scenario 6 flow', false, `ERROR: ${e.message.slice(0, 200)}`); }

  // ── summary ─────────────────────────────────────────────────────────────────
  console.log('\n══════════════ SUMMARY ══════════════');
  for (const r of scenarioResults) {
    console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.fails.length ? '\n      └ ' + r.fails.join('\n      └ ') : ''}`);
  }
  const gating = scenarioResults.filter((r) => !r.name.startsWith('6.'));
  const gatePass = gating.every((r) => r.pass);
  console.log(`\nSnap-API traffic captured: ${snapApi.length} call(s)`);
  for (const e of snapApi) console.log(`  ${e.at} ${e.method} ${e.status} req=${e.reqBody} res=${(e.resBody || '').slice(0, 100)}`);
  console.log(`\n[${t()}] runtime=${((Date.now() - T0) / 1000).toFixed(1)}s`);
  console.log(`RESULT: ${gatePass ? 'PASS' : 'FAIL'}`);
  clearTimeout(wd);
  await browser.close().catch(() => {});
  process.exit(gatePass ? 0 : 1);
}

main().catch(async (e) => {
  console.log(`FATAL: ${e.message}`);
  console.log('RESULT: FAIL');
  clearTimeout(wd);
  try { if (browser) await browser.close(); } catch { /* noop */ }
  process.exit(1);
});
