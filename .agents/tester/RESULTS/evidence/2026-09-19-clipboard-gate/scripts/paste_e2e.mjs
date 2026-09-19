// LIVE E2E — clipboard image paste full path (Scenario B4 of FINAL MERGE GATE)
// Walks the FE through the original-promise flow:
//   paste image -> composer chip -> upload POST /api/tmp_images -> message POST
//   carrying image_refs (NOT data-URI) -> waits for agent turn to complete.
// Captures every network request/response, DOM evidence, screenshots.
//
// Run with:  timeout 300 node data-gate-main/scripts/paste_e2e.mjs

import { chromium } from '/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/frontend/node_modules/playwright/index.mjs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

const ROOT = '/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img';
const SCRIPT_DIR = path.join(ROOT, 'data-gate-main', 'scripts');
const FE_BASE = 'http://127.0.0.1:4199';
const DAEMON_BASE = 'http://127.0.0.1:8090';
const MAIN_ID = process.env.MAIN_ID || '2bf16de7-71a9-4097-b9e4-2ad7fcd17f4c';
const PROJECT_CTX = 'all';  // matches the route /projects/all/instances/:id
const IMAGE_PATH = process.env.IMAGE_PATH || path.join(SCRIPT_DIR, 'gate42.png');
const AGENT_WAIT_MS = 120_000;

// Trace + screenshot dump location
const EVIDENCE_DIR = path.join(ROOT, 'data-gate-main', 'scripts', 'evidence');
if (!existsSync(EVIDENCE_DIR)) await mkdir(EVIDENCE_DIR, { recursive: true });

// Shared report artifact
const REPORT = {
  started_at: new Date().toISOString(),
  main_id: MAIN_ID,
  paste_target_selectors: [],
  steps: [],
  network: {
    upload_request: null,
    upload_response: null,
    message_request: null,
    message_response: null,
  },
  errors: [],
};

function logStep(name, status, payload = {}) {
  const step = { name, status, ts: new Date().toISOString(), ...payload };
  REPORT.steps.push(step);
  const emoji = status === 'PASS' ? '✅' : status === 'FAIL' ? '❌' : '⏳';
  console.log(`\n${emoji}  ${name}  ${status}`);
  for (const [k, v] of Object.entries(payload)) {
    if (v === undefined || v === null) {
      console.log(`    ${k}: <null>`);
      continue;
    }
    const s = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
    if (typeof s !== 'string') { console.log(`    ${k}: <unstringifiable>`); continue; }
    console.log(`    ${k}: ${s.split('\n').slice(0, 20).join('\n    ')}`);
  }
}

// === STEP 0: confirm test image exists ===
{
  if (!existsSync(IMAGE_PATH)) {
    console.error(`❌ Test image missing: ${IMAGE_PATH}`);
    process.exit(2);
  }
  const stat = await import('node:fs').then(m => m.promises.stat(IMAGE_PATH));
  logStep('STEP 0 — test PNG present', 'PASS', { image: IMAGE_PATH, size: stat.size });
}

// === STEP 1: Launch browser, open chat ===
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1400, height: 900 },
  acceptDownloads: true,
});
const page = await context.newPage();

// Capture all request/response pairs
const requests = [];
page.on('request', req => {
  const u = req.url();
  if (u.includes('/api/') || u.includes('/tmp_images') || u.includes('/messages')) {
    requests.push({
      kind: 'req',
      ts: Date.now(),
      method: req.method(),
      url: u,
      headers: req.headers(),
      post_data: req.postData(),
    });
  }
});
page.on('response', async resp => {
  const u = resp.url();
  if (u.includes('/api/') || u.includes('/tmp_images') || u.includes('/messages')) {
    let body = null;
    try {
      const ct = (resp.headers()['content-type'] || '').toLowerCase();
      if (ct.includes('json') || ct.includes('text')) {
        const text = await resp.text();
        body = text.length > 4000 ? text.slice(0, 4000) + '...[truncated]' : text;
      } else {
        body = `<binary ${resp.headers()['content-type'] || ''} ${resp.headers()['content-length'] || '?'}B>`;
      }
    } catch (e) {
      body = `<body read failed: ${e.message}>`;
    }
    requests.push({
      kind: 'resp',
      ts: Date.now(),
      status: resp.status(),
      url: u,
      headers: resp.headers(),
      body,
    });
  }
});

page.on('console', msg => {
  const t = msg.type();
  if (t === 'error' || t === 'warning') {
    console.log(`  [browser-${t}]`, msg.text().slice(0, 300));
  }
});
page.on('pageerror', err => console.log('  [page-error]', err.message.slice(0, 300)));

try {
  // 1.a — go to the conversation URL directly (deep-link)
  // The route is /projects/:projectId/instances/:instanceId
  const targetUrl = `${FE_BASE}/projects/${PROJECT_CTX}/instances/${MAIN_ID}`;
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  // Wait for the composer textarea (or fallback nav)
  await page.waitForSelector('.input-textarea', { timeout: 30_000 });
  await page.waitForTimeout(1500); // give the FE a moment to wire SSE/injection

  // 1.b — confirm we landed on chat
  const composerVisible = await page.locator('.input-textarea').isVisible();
  logStep('STEP 1 — navigated to chat', composerVisible ? 'PASS' : 'FAIL', {
    url: page.url(),
    composerVisible,
  });

  // Take a screenshot of the empty chat
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '01-empty-chat.png'), fullPage: false });

  // === STEP 2: simulate paste — clipboard DataTransfer with PNG File ===
  const pngBytes = await readFile(IMAGE_PATH);
  const base64 = pngBytes.toString('base64');

  // Find the textarea — there should be exactly one
  const textareaCount = await page.locator('.input-textarea').count();
  if (textareaCount < 1) {
    logStep('STEP 2 — locate textarea', 'FAIL', { textareaCount });
    throw new Error('textarea missing');
  }

  // Build the File + DataTransfer, dispatch paste event directly on the textarea.
  // This bypasses browser clipboard permission; we synthesise the ClipboardEvent.
  const dispatchResult = await page.evaluate(async ({ b64, fname, mime }) => {
    function base64ToUint8Array(b64) {
      const bin = atob(b64);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    }
    const bytes = base64ToUint8Array(b64);
    const file = new File([bytes], fname, { type: mime });
    const dt = new DataTransfer();
    dt.items.add(file);
    const ta = document.querySelector('.input-textarea');
    if (!ta) return { ok: false, reason: 'no textarea in DOM' };
    const ev = new ClipboardEvent('paste', {
      bubbles: true,
      cancelable: true,
      clipboardData: dt,
    });
    ta.dispatchEvent(ev);
    return { ok: true, fileName: fname, fileSize: file.size, fileType: file.type };
  }, { b64: base64, fname: 'gate42.png', mime: 'image/png' });
  logStep('STEP 2 — dispatched paste event', dispatchResult.ok ? 'PASS' : 'FAIL', dispatchResult);

  // === STEP 3: assert chip appears ===
  let chipInfo = { found: false };
  try {
    await page.waitForSelector('[data-testid="image-upload-chip"]', { timeout: 5000 });
    chipInfo = await page.evaluate(() => {
      const chip = document.querySelector('[data-testid="image-upload-chip"]');
      const filename = chip?.querySelector('.preview-filename')?.textContent?.trim() || '';
      const status = chip?.querySelector('.image-upload-status')?.getAttribute('data-status') || '';
      const dt = chip?.querySelector('.preview-thumbnail')?.getAttribute('src') || '';
      return { found: true, filename, status, hasDataUrl: dt.startsWith('data:') };
    });
    logStep('STEP 3 — chip appeared in composer', chipInfo.found ? 'PASS' : 'FAIL', chipInfo);
  } catch {
    logStep('STEP 3 — chip appeared in composer', 'FAIL', chipInfo);
  }

  // Sanity-check that upload has NOT yet started (upload happens at handleSubmit time,
  // not at paste time — per processFiles() in message-input.component.ts:700).
  // So we expect status "idle" here, NOT "uploaded". STEP 3b is informational.
  {
    const preSend = await page.evaluate(() => document.querySelector('[data-testid="image-upload-chip"] .image-upload-status')?.getAttribute('data-status'));
    logStep('STEP 3b — chip pre-send status (informational)', 'INFO', {
      note: 'upload runs at handleSubmit time, not paste time — expected status="idle"',
      current_status: preSend,
    });
  }

  await page.screenshot({ path: path.join(EVIDENCE_DIR, '02-chip-uploaded.png'), fullPage: false });

  // === STEP 4: type caption + click send ===
  const caption = 'what is in this image?';
  await page.locator('.input-textarea').click();
  await page.keyboard.type(caption);
  logStep('STEP 4a — typed caption', 'PASS', { caption });

  // Capture network at moment-of-send: attach a request listener NOW so we
  // can identify the send-time requests specifically.
  const sendMarker = Date.now();
  const pre = requests.length;

  await page.locator('[data-testid="send-button"]').click();
  logStep('STEP 4b — clicked send', 'PASS', { send_marker_ts: sendMarker });

  // === STEP 5: wait for the upload + message POSTs and capture them ===
  // The upload + message POSTs happen at handleSubmit time. Poll up to 30s
  // for both, then drain.
  let attempts = 0;
  let localUploadResp = null, localMessageReq = null, localMessageResp = null;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(1000);
    attempts++;
    localUploadResp = requests.find(r => r.kind === 'resp' && /\/api\/tmp_images(\?|$|\/)/.test(r.url) && r.status === 200);
    localMessageReq = requests.find(r => r.kind === 'req' && r.method === 'POST' && /\/api\/instances\/.*\/messages(\?|$|\/)/.test(r.url));
    localMessageResp = requests.find(r => r.kind === 'resp' && /\/api\/instances\/.*\/messages(\?|$|\/)/.test(r.url));
    if (localUploadResp && localMessageReq && localMessageResp) break;
  }
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '03-after-send.png'), fullPage: false });

  // Pull the upload + message POST events out of the requests buffer
  const uploadReq = requests.find(r => r.kind === 'req' && r.method === 'POST' && /\/api\/tmp_images(\?|$|\/)/.test(r.url));
  const uploadResp = localUploadResp || requests.find(r => r.kind === 'resp' && /\/api\/tmp_images(\?|$|\/)/.test(r.url) && r.status === 200);
  const messageReq = localMessageReq || requests.find(r => r.kind === 'req' && r.method === 'POST' && /\/api\/instances\/.*\/messages(\?|$|\/)/.test(r.url));
  const messageResp = localMessageResp || requests.find(r => r.kind === 'resp' && /\/api\/instances\/.*\/messages(\?|$|\/)/.test(r.url));

  REPORT.network.upload_request = uploadReq ? { method: uploadReq.method, url: uploadReq.url, body: uploadReq.post_data } : null;
  REPORT.network.upload_response = uploadResp ? { status: uploadResp.status, body: uploadResp.body } : null;
  REPORT.network.message_request = messageReq ? { method: messageReq.method, url: messageReq.url, body: messageReq.post_data } : null;
  REPORT.network.message_response = messageResp ? { status: messageResp.status, body: messageResp.body } : null;

  // Validate body of upload request
  let uploadBody = null, uploadBodyParsed = null;
  if (uploadReq?.post_data) {
    try { uploadBodyParsed = JSON.parse(uploadReq.post_data); uploadBody = 'OK'; }
    catch { uploadBody = 'NOT JSON'; }
  }
  logStep('STEP 5a — upload POST captured', uploadReq ? 'PASS' : 'FAIL', {
    url: uploadReq?.url,
    body_shape: uploadBody,
    images_array_len: uploadBodyParsed?.images?.length,
    first_image_fields: uploadBodyParsed?.images?.[0] ? Object.keys(uploadBodyParsed.images[0]) : null,
  });
  logStep('STEP 5b — upload response captured', uploadResp ? 'PASS' : 'FAIL', {
    status: uploadResp?.status,
    body: uploadResp?.body,
  });

  // Validate body of message POST
  let msgBodyParsed = null, msgValidate = null;
  if (messageReq?.post_data) {
    try {
      msgBodyParsed = JSON.parse(messageReq.post_data);
      msgValidate = {
        content: msgBodyParsed.content,
        has_images_field: 'images' in msgBodyParsed,
        has_image_refs_field: 'image_refs' in msgBodyParsed,
        image_refs: msgBodyParsed.image_refs,
        has_data_uri: JSON.stringify(msgBodyParsed).includes('data:image/'),
      };
    } catch (e) {
      msgValidate = { parse_error: e.message };
    }
  }
  logStep('STEP 5c — message POST captured', messageReq ? 'PASS' : 'FAIL', {
    url: messageReq?.url,
    body_shape: msgValidate,
  });

  // === STEP 6: wait for agent turn to complete (status RUNNING -> idle/done) ===
  // The /messages POST may return 202 + injection. We poll the instance status.
  let agentStatusReport = { polled: 0, last_status: null, last_messages_count: 0 };
  const pollStart = Date.now();
  let saw_agent_message = false;
  let last_messages_dump = null;
  while (Date.now() - pollStart < AGENT_WAIT_MS) {
    await page.waitForTimeout(2000);
    agentStatusReport.polled++;
    try {
      const resp = await page.evaluate(async ({ instanceId, base }) => {
        const r = await fetch(`${base}/api/instances/${instanceId}`);
        const j = await r.json();
        const r2 = await fetch(`${base}/api/instances/${instanceId}/messages?limit=20`);
        const msgs = await r2.json();
        return { status: j.status, messages_count: (msgs.messages || msgs || []).length, last_role: ((msgs.messages || msgs || []).slice(-1)[0] || {}).role };
      }, { instanceId: MAIN_ID, base: DAEMON_BASE });
      agentStatusReport.last_status = resp.status;
      agentStatusReport.last_messages_count = resp.messages_count;
      agentStatusReport.last_role = resp.last_role;
      if (resp.last_role === 'assistant' && agentStatusReport.last_messages_count >= 2) {
        saw_agent_message = true;
        // snapshot last 4 messages for inspection
        last_messages_dump = await page.evaluate(async ({ instanceId, base }) => {
          const r2 = await fetch(`${base}/api/instances/${instanceId}/messages?limit=10`);
          const j = await r2.json();
          return (j.messages || j || []).slice(-4);
        }, { instanceId: MAIN_ID, base: DAEMON_BASE });
        break;
      }
    } catch (e) {
      agentStatusReport.error = e.message;
    }
  }
  logStep('STEP 6 — agent turn completed', saw_agent_message ? 'PASS' : 'FAIL', {
    polled: agentStatusReport.polled,
    elapsed_s: ((Date.now() - pollStart) / 1000).toFixed(1),
    final_status: agentStatusReport.last_status,
    final_messages_count: agentStatusReport.last_messages_count,
    last_role: agentStatusReport.last_role,
  });
  REPORT.last_messages_dump = last_messages_dump;

  // === STEP 7: thumbnail rendering on user message bubble ===
  await page.waitForTimeout(1000);
  let thumbInfo = null;
  try {
    thumbInfo = await page.evaluate(async () => {
      const imgs = Array.from(document.querySelectorAll('[data-testid="message-image"]'));
      // Filter to ones with /api/tmp_images src
      const refImgs = imgs.filter(i => (i.getAttribute('src') || '').startsWith('/api/tmp_images/'));
      const dataImgs = imgs.filter(i => (i.getAttribute('src') || '').startsWith('data:image/'));
      // Wait for naturalWidth to settle on the first ref image
      const waitLoad = (img, ms = 8000) => new Promise((res) => {
        if (img.complete && img.naturalWidth > 0) return res({ ok: true, w: img.naturalWidth, h: img.naturalHeight });
        const t = setTimeout(() => res({ ok: false, reason: 'timeout', w: img.naturalWidth, h: img.naturalHeight }), ms);
        img.addEventListener('load', () => { clearTimeout(t); res({ ok: true, w: img.naturalWidth, h: img.naturalHeight }); });
        img.addEventListener('error', () => { clearTimeout(t); res({ ok: false, reason: 'error', w: img.naturalWidth, h: img.naturalHeight }); });
      });
      const first = refImgs[0];
      const probe = first ? await waitLoad(first) : null;
      return {
        ref_image_count: refImgs.length,
        data_uri_image_count: dataImgs.length,
        first_src: first?.getAttribute('src') || null,
        probe,
      };
    });
    logStep('STEP 7 — user-message thumbnail rendered', thumbInfo.ref_image_count >= 1 && thumbInfo.probe?.ok ? 'PASS' : 'FAIL', thumbInfo);
  } catch (e) {
    logStep('STEP 7 — user-message thumbnail rendered', 'FAIL', { error: e.message });
  }
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '04-thumbnail-rendered.png'), fullPage: false });

  // === STEP 8: click thumbnail → popup viewer opens ===
  let popupOpened = false;
  try {
    await page.locator('[data-testid="message-image"]').first().click();
    await page.waitForSelector('[data-testid="image-viewer-dialog"]', { timeout: 5000 });
    const dialogInfo = await page.evaluate(() => {
      const d = document.querySelector('[data-testid="image-viewer-dialog"]');
      const img = d?.querySelector('[data-testid="image-viewer-img"]');
      return {
        dialog_visible: !!d,
        dialog_role: d?.getAttribute('role'),
        img_src: img?.getAttribute('src'),
      };
    });
    popupOpened = dialogInfo.dialog_visible;
    logStep('STEP 8 — popup viewer opens', popupOpened ? 'PASS' : 'FAIL', dialogInfo);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '05-popup-open.png'), fullPage: false });
  } catch (e) {
    logStep('STEP 8 — popup viewer opens', 'FAIL', { error: e.message });
  }

  // === STEP 9: Escape closes the dialog ===
  let popupClosed = false;
  try {
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('[data-testid="image-viewer-dialog"]'), { timeout: 5000 });
    popupClosed = true;
    logStep('STEP 9 — Escape closes popup', popupClosed ? 'PASS' : 'FAIL');
  } catch (e) {
    logStep('STEP 9 — Escape closes popup', 'FAIL', { error: e.message, still_visible: await page.locator('[data-testid="image-viewer-dialog"]').isVisible().catch(() => false) });
  }
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '06-popup-closed.png'), fullPage: false });

  // === STEP 10: save full network transcript ===
  REPORT.network_full = requests;
  REPORT.finished_at = new Date().toISOString();
  await writeFile(path.join(EVIDENCE_DIR, 'paste-e2e-report.json'), JSON.stringify(REPORT, null, 2));
  console.log(`\nWrote evidence report: ${path.join(EVIDENCE_DIR, 'paste-e2e-report.json')}`);

  await context.close();
  await browser.close();

  // Exit 0 if all steps PASS (INFO is informational, not a failure)
  const allPassed = REPORT.steps.every(s => s.status === 'PASS' || s.status === 'INFO');
  const failCount = REPORT.steps.filter(s => s.status === 'FAIL').length;
  console.log(`\n${failCount === 0 ? '✅ ALL STEPS PASS (or INFO)' : `❌ ${failCount} STEPS FAILED`}`);
  process.exit(allPassed ? 0 : 1);
} catch (e) {
  console.error('FATAL:', e.stack || e.message);
  REPORT.errors.push({ fatal: e.message, stack: e.stack });
  await writeFile(path.join(EVIDENCE_DIR, 'paste-e2e-report.json'), JSON.stringify(REPORT, null, 2));
  await context.close().catch(() => {});
  await browser.close().catch(() => {});
  process.exit(2);
}
