// FE-UI lane marker (group-chat gc-fe-surfaces P4/G2 rooms gate): real-daemon leg.
/**
 * G2 — ORIGINAL-SYMPTOM FUNCTIONAL, daemon-fenced.
 *
 * The deferred `test.skip` adjacency row in `group-chat-rooms-gate.spec.ts`
 * ("header control is adjacent to the shipped room icon") is executed HERE,
 * against a REAL daemon + REAL Postgres rows, instead of being skipped.
 *
 * FIXTURE (seeded by the calling lane, rows prefixed `gcfe_e2e_`):
 *   alpha  — joins R1 (owner) and R2 (active member)
 *   beta   — joins nothing
 *   gamma  — owns R2 and R3; R3 has NO alpha membership (isolation probe)
 *
 * NOTHING is stubbed except:
 *   - the single forced-5xx leg (W1/W7 error-copy probe), and
 *   - ONE intercept that replays the REAL daemon's 404 body (byte-captured
 *     live in the same test) so the typed 404 copy can be asserted at UI
 *     level. The transport is injected; the body is the daemon's own.
 */
import { test, expect, type Page } from '@playwright/test';

const ALPHA = 'gcfe_e2e_alpha';
const BETA = 'gcfe_e2e_beta';
const R1 = 'gcfe_e2e_r1';
const R2 = 'gcfe_e2e_r2';
const R3 = 'gcfe_e2e_r3';

const PANE = '.cdk-overlay-pane.room-dialog-panel';
const TITLE = '.room-dialog-title .title-text';
const LIST_ERROR = '.rooms-pane .pane-state-error .pane-state-text';
const MSG_CONTENT = '.conversation-pane .message-content';

/** Any raw transport/Angular-internal string — the W1/W7 leak signature. */
const TRANSPORT_LEAK = /Http failure response|127\.0\.0\.1|localhost:8079|Internal Server Error|HttpErrorResponse/i;

function evidence(line: string): void {
  // One machine-greppable line per assertion group.
  console.log(`[EVIDENCE] ${line}`);
}

/** Deep-link that mounts the real chat overlay (the Revival-3 pattern). */
async function openChatOverlay(page: Page, instanceId: string): Promise<void> {
  await page.goto(`/projects/all/instances/${instanceId}`);
  await page.evaluate(() => document.querySelector('vite-error-overlay')?.remove());
  await expect(page.locator('.chat-header'), 'real chat header mounted').toBeVisible({ timeout: 20_000 });
}

/** The row-scoped locator for one instance on /instances.
 *
 * Anchored on the row's routerLink href — the visible label is CSS-truncated
 * (`gcfe_e2e_bet…`), so a text filter cannot match the full id/name.
 */
function instanceRow(page: Page, instanceId: string) {
  return page.locator(`a.instance-item[href$="/instances/${instanceId}"]`).first();
}

/** The big-popup pane geometry (82vw × 82vh ± 12px). */
async function measurePane(page: Page): Promise<{ w: number; h: number; ew: number; eh: number }> {
  const pane = page.locator(PANE).last();
  await expect(pane, 'the room dialog pane is open').toBeVisible();
  await page.waitForFunction(
    (sel) => {
      const el = document.querySelector(sel);
      return !!el && (el as HTMLElement).getBoundingClientRect().width > 0;
    },
    PANE,
  );
  const box = await pane.boundingBox();
  const viewport = page.viewportSize();
  expect(box, 'pane has a measurable box').not.toBeNull();
  expect(viewport, 'viewport known').not.toBeNull();
  const ew = viewport!.width * 0.82;
  const eh = viewport!.height * 0.82;
  expect(Math.abs(box!.width - ew), `pane width ${Math.round(box!.width)} ≈ 82vw`).toBeLessThanOrEqual(12);
  expect(Math.abs(box!.height - eh), `pane height ${Math.round(box!.height)} ≈ 82vh`).toBeLessThanOrEqual(12);
  return { w: Math.round(box!.width), h: Math.round(box!.height), ew: Math.round(ew), eh: Math.round(eh) };
}

/** Room names rendered in the rooms pane of the open dialog (waits for the list). */
async function listedRoomNames(page: Page): Promise<string[]> {
  await expect(page.locator('.rooms-pane .room-item-name').first(), 'the rooms list published').toBeVisible({
    timeout: 20_000,
  });
  return page.locator('.rooms-pane .room-item-name').allTextContents();
}

test.describe('gc-fe-surfaces G2 — real-daemon rooms gate', () => {
  // ── (a) header adjacency + global browser over the REAL chat page ──
  test('(a) header control is adjacent to the shipped room icon; global browser lists the nest', async ({ page }) => {
    await openChatOverlay(page, ALPHA);

    // The control lives in the SAME `.toggle-buttons` cluster and lands
    // AFTER the shipped room icon (the adjacency the deferred row wanted).
    const cluster = page.locator('.chat-header .toggle-buttons');
    await expect(cluster, 'the toggle-button cluster is rendered in the real header').toBeVisible();
    // The shipped room icon is gated on `renderedInstance()`, which is
    // populated by the instances poll — wait for it before reading the
    // cluster's button order (otherwise the read races the poll).
    await expect(cluster.locator('.room-btn'), 'shipped room icon resolved from the live instance list').toBeVisible({
      timeout: 20_000,
    });
    const classes = await cluster
      .locator('button')
      .evaluateAll((btns) => btns.map((b) => b.className));
    const roomIdx = classes.findIndex((c) => /\broom-btn\b/.test(c));
    const browserIdx = classes.findIndex((c) => /\brooms-browser-btn\b/.test(c));
    expect(roomIdx, 'the shipped room icon is present').toBeGreaterThanOrEqual(0);
    expect(browserIdx, 'the new chatroom-browser control is present').toBeGreaterThanOrEqual(0);
    expect(browserIdx, 'the browser control is ADJACENT, after the room icon').toBeGreaterThan(roomIdx);
    expect(browserIdx - roomIdx, 'no unrelated control sits between them').toBe(1);

    await page.locator('.rooms-browser-btn').click();
    await expect(page.locator(TITLE).last(), 'global mode title').toHaveText('All chatrooms in this nest');

    const names = await listedRoomNames(page);
    for (const r of [R1, R2, R3]) {
      expect(names, `${r} is listed in the nest-wide browser`).toContain(r);
    }
    evidence(`(a) cluster=[${classes.join('|')}] roomIdx=${roomIdx} browserIdx=${browserIdx} globalListed=${names.length} contains=${[R1, R2, R3].join(',')}`);

    // Open R1 in the detail pane — its three real messages render.
    await page.locator('.rooms-pane .room-item').filter({ hasText: R1 }).first().click();
    await expect(page.locator(MSG_CONTENT), 'R1 conversation renders 3 messages').toHaveCount(3, {
      timeout: 20_000,
    });
    const texts = await page.locator(MSG_CONTENT).allTextContents();
    expect(texts.join('|')).toContain('gcfe_e2e_r1 message one');
    expect(texts.join('|')).toContain('gcfe_e2e_r1 message three');
    evidence(`(a) R1 messages=${JSON.stringify(texts)}`);
  });

  // ── (b) instances-list row button → BIG popup, joined scope ────────
  test('(b) per-instance row button opens the 82vw×82vh popup with the JOINED rooms', async ({ page }) => {
    await page.goto('/instances');
    await page.evaluate(() => document.querySelector('vite-error-overlay')?.remove());

    const row = instanceRow(page, ALPHA);
    await expect(row, 'alpha row rendered').toBeVisible({ timeout: 20_000 });
    const btn = row.locator('.rooms-btn');
    await expect(btn, 'row button renders for an instance with joined rooms').toBeVisible();
    await expect(btn).toHaveAttribute('aria-label', `Chatrooms joined by ${ALPHA}`);
    evidence(`(b) row-btn aria-label="${await btn.getAttribute('aria-label')}"`);

    await row.hover();
    await btn.click();

    await expect(page.locator(TITLE).last(), 'joined mode title names the instance').toHaveText(
      `Chatrooms joined by ${ALPHA}`,
    );
    const geom = await measurePane(page);
    evidence(`(b) pane=${geom.w}x${geom.h} expected=${geom.ew}x${geom.eh}`);

    const names = await listedRoomNames(page);
    expect(names, 'R1 (owned) is listed').toContain(R1);
    expect(names, 'R2 (active member) is listed').toContain(R2);
    expect(names, 'R3 (owned by gamma, alpha not a member) is NOT listed').not.toContain(R3);
    expect(names.length, 'exactly the two joined rooms').toBe(2);
    evidence(`(b) joinedListed=${JSON.stringify(names)}`);

    await page.locator('.rooms-pane .room-item').filter({ hasText: R1 }).first().click();
    await expect(page.locator(MSG_CONTENT), 'R1 conversation renders in the popup').toHaveCount(3, {
      timeout: 20_000,
    });
    evidence(`(b) R1 messages rendered=3`);
  });

  // ── (c) conditional visibility: a room-less instance has NO button ──
  test('(c) row button is ABSENT for an instance that joined no chatroom', async ({ page }) => {
    await page.goto('/instances');
    await page.evaluate(() => document.querySelector('vite-error-overlay')?.remove());
    await expect(instanceRow(page, BETA), 'beta row rendered').toBeVisible({ timeout: 20_000 });

    // Precondition: the membership map LOADED (absence must be genuine,
    // not a fail-closed map error).
    await expect(page.locator('.room-map-error'), 'the membership map did not fail').toHaveCount(0);

    const betaBtn = instanceRow(page, BETA).locator('.rooms-btn');
    await expect(betaBtn, 'beta joined nothing → no row button').toHaveCount(0);
    // Control: alpha DOES have one, so the absence above is not vacuous.
    await expect(instanceRow(page, ALPHA).locator('.rooms-btn'), 'alpha still has its button').toHaveCount(1);
    evidence(`(c) betaButtons=0 alphaButtons=1 mapError=0`);
  });

  // ── (d) taxonomy live: 422 / 404 (+ typed 404 copy at UI level) ────
  test('(d) live taxonomy: 422 on malformed limit, 404 on unknown room, typed 404 copy', async ({ page }) => {
    const bad = await page.request.get('/api/rooms?limit=abc');
    expect(bad.status(), 'non-integer limit → 422 at the real daemon').toBe(422);
    evidence(`(d) /api/rooms?limit=abc → ${bad.status()}`);

    const missing = await page.request.get('/api/rooms/gcfe_e2e_nope/messages');
    expect(missing.status(), 'unknown room → 404').toBe(404);
    const missingBody = (await missing.json()) as { detail?: { error?: string } };
    expect(missingBody?.detail?.error, 'typed room_not_found envelope').toBe('room_not_found');
    evidence(`(d) unknown room → ${missing.status()} error=${missingBody?.detail?.error}`);

    // UI level: replay the daemon's OWN 404 body once for R1's messages so
    // the conversation pane's typed copy is exercised. The body above is
    // the live daemon body; only the transport is injected.
    let first = true;
    await page.route(`**/api/rooms/${R1}/messages**`, async (route) => {
      if (!first) return route.fallback();
      first = false;
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify(missingBody),
      });
    });

    await openChatOverlay(page, ALPHA);
    await page.locator('.rooms-browser-btn').click();
    await expect(page.locator(TITLE).last()).toHaveText('All chatrooms in this nest');
    await page.locator('.rooms-pane .room-item').filter({ hasText: R1 }).first().click();

    const convError = page.locator('.conversation-pane .pane-state-error .pane-state-text');
    await expect(convError, 'typed 404 copy in the conversation pane').toContainText(
      'Not found — this conversation is no longer available',
      { timeout: 20_000 },
    );
    const convText = (await convError.innerText()).trim();
    expect(convText).not.toMatch(TRANSPORT_LEAK);
    await expect(page.locator('.conversation-pane .pane-retry-btn'), 'retry control offered').toBeVisible();
    evidence(`(d) UI typed 404 copy="${convText}"`);
  });

  // ── (e) anchor-less vs anchored scope ─────────────────────────────
  test('(e) anchor-less lists the nest; anchored lists only the readable/joined set', async ({ page }) => {
    const global = await page.request.get('/api/rooms?limit=500');
    expect(global.status()).toBe(200);
    const gBody = (await global.json()) as { rooms: Array<{ name: string }>; truncated: boolean };
    const gNames = gBody.rooms.map((r) => r.name);
    for (const r of [R1, R2, R3]) expect(gNames, `${r} in the nest-wide read`).toContain(r);
    evidence(`(e) anchorless rows=${gNames.length} truncated=${gBody.truncated} gcfe=${gNames.filter((n) => n.startsWith('gcfe_e2e_')).join(',')}`);

    const anchored = await page.request.get(`/api/rooms?instance_id=${ALPHA}&limit=500`);
    expect(anchored.status()).toBe(200);
    const aBody = (await anchored.json()) as { rooms: Array<{ name: string }> };
    const aNames = aBody.rooms.map((r) => r.name).sort();
    expect(aNames, 'anchored read = exactly R1+R2').toEqual([R1, R2]);
    expect(aNames, 'anchored read excludes the unjoined room').not.toContain(R3);
    evidence(`(e) anchored(alpha)=${JSON.stringify(aNames)} triviallyExcludesR3=true`);

    // UI parity: global mode shows R3, joined mode does not.
    await openChatOverlay(page, ALPHA);
    await page.locator('.rooms-browser-btn').click();
    await expect(page.locator(TITLE).last()).toHaveText('All chatrooms in this nest');
    expect(await listedRoomNames(page), 'global UI contains R3').toContain(R3);
    await page.locator('.room-dialog-panel button:has-text("Close")').click();

    await page.goto('/instances');
    const row = instanceRow(page, ALPHA);
    await expect(row.locator('.rooms-btn')).toBeVisible({ timeout: 20_000 });
    await row.hover();
    await row.locator('.rooms-btn').click();
    await expect(page.locator(TITLE).last()).toHaveText(`Chatrooms joined by ${ALPHA}`);
    expect(await listedRoomNames(page), 'joined UI excludes R3').not.toContain(R3);
    evidence('(e) UI global∋R3, joined∌R3');
  });

  // ── (f) truncation disclosure ────────────────────────────────────
  test('(f) truncated flag at small limits and the truncation notice in the dialog', async ({ page }) => {
    const one = await page.request.get('/api/rooms?limit=1');
    expect(one.status()).toBe(200);
    const oneBody = (await one.json()) as { rooms: unknown[]; truncated: boolean };
    expect(oneBody.rooms.length, 'limit=1 returns exactly one row').toBe(1);
    expect(oneBody.truncated, 'limit=1 is truncated (674 rooms exist)').toBe(true);
    evidence(`(f) ?limit=1 rows=${oneBody.rooms.length} truncated=${oneBody.truncated}`);

    // The FE's own anchor-less read (no limit → server default 200) is
    // truncated in this dataset, so the dialog must disclose it.
    await openChatOverlay(page, ALPHA);
    await page.locator('.rooms-browser-btn').click();
    const notice = page.locator('.pane-truncated .pane-truncated-text');
    await expect(notice, 'truncation notice rendered').toBeVisible({ timeout: 20_000 });
    const text = (await notice.innerText()).trim();
    expect(text, 'notice names the prefix semantics').toMatch(/More rooms not shown — showing the first \d+ of the nest\./);
    evidence(`(f) notice="${text}"`);
  });

  // ── (g) W1/W7 — never the Angular transport string ───────────────
  test('(g) forced 5xx and body-less 4xx render fixed copy, never the transport string', async ({ page }) => {
    await openChatOverlay(page, ALPHA);

    // 500, empty body, exactly ONE request.
    let fired = true;
    await page.route('**/api/rooms**', async (route) => {
      if (!fired) return route.fallback();
      fired = false;
      await route.fulfill({ status: 500, contentType: 'application/json', body: '' });
    });
    await page.locator('.rooms-browser-btn').click();
    const err = page.locator(LIST_ERROR).last();
    await expect(err, 'typed 5xx copy').toHaveText('The server could not load the chatroom list — try again.');
    const errText = (await err.innerText()).trim();
    expect(errText).not.toMatch(TRANSPORT_LEAK);
    await expect(page.locator('.rooms-pane .room-item'), 'zero fabricated rows on 5xx').toHaveCount(0);
    evidence(`(g) 500 copy="${errText}" leak=false rows=0`);

    // Retry with a body-less 4xx (418) → the generic subject copy.
    let fired418 = true;
    await page.route('**/api/rooms**', async (route) => {
      if (!fired418) return route.fallback();
      fired418 = false;
      await route.fulfill({ status: 418, contentType: 'application/json', body: '' });
    });
    await page.locator('.pane-retry-btn').first().click();
    await expect(err, 'typed <500 copy').toHaveText('Failed to load chatrooms.');
    const t418 = (await err.innerText()).trim();
    expect(t418).not.toMatch(TRANSPORT_LEAK);
    evidence(`(g) 418 copy="${t418}" leak=false`);
  });

  // ── (h) polling freshness (the tick drives the open conversation) ──
  test('(h) a message inserted while the conversation is open appears within ~1 tick', async ({ page }) => {
    await openChatOverlay(page, ALPHA);
    await page.locator('.rooms-browser-btn').click();
    await expect(page.locator(TITLE).last()).toHaveText('All chatrooms in this nest');
    await page.locator('.rooms-pane .room-item').filter({ hasText: R1 }).first().click();
    await expect(page.locator(MSG_CONTENT).first(), 'conversation is open').toBeVisible({ timeout: 20_000 });

    // The caller inserts `gcfe_e2e_poll_probe` into R1 (seq 4) shortly
    // after this test starts; the 10 s tick must surface it.
    const probe = page.locator('.conversation-pane .message-content', { hasText: 'gcfe_e2e_poll_probe' });
    const started = Date.now();
    await expect(probe, 'polled new message appears').toBeVisible({ timeout: 35_000 });
    evidence(`(h) poll probe visible after ${Date.now() - started}ms (tick=10000ms)`);
  });

  // ── (i) tab-hide stops polling; restore resumes ───────────────────
  test('(i) tab-hide halts polling and restore resumes it', async ({ page }) => {
    await openChatOverlay(page, ALPHA);
    await page.locator('.rooms-browser-btn').click();
    await expect(page.locator(TITLE).last()).toHaveText('All chatrooms in this nest');
    await page.locator('.rooms-pane .room-item').filter({ hasText: R1 }).first().click();
    await expect(page.locator(MSG_CONTENT).first()).toBeVisible({ timeout: 20_000 });

    // The poll tick refreshes the OPEN CONVERSATION (messages lane), not
    // the room list — count that lane.
    let polls = 0;
    let maxConcurrent = 0;
    let inFlight = 0;
    page.on('request', (r) => {
      if (/\/api\/rooms\/[^/]+\/messages/.test(new URL(r.url()).pathname)) {
        polls += 1;
        inFlight += 1;
        maxConcurrent = Math.max(maxConcurrent, inFlight);
      }
    });
    page.on('requestfinished', (r) => {
      if (/\/api\/rooms\/[^/]+\/messages/.test(new URL(r.url()).pathname)) inFlight -= 1;
    });
    page.on('requestfailed', (r) => {
      if (/\/api\/rooms\/[^/]+\/messages/.test(new URL(r.url()).pathname)) inFlight -= 1;
    });

    // Baseline: a tick must be observed while visible.
    await page.waitForTimeout(12_000);
    const visiblePolls = polls;
    expect(visiblePolls, 'at least one tick while the tab is visible').toBeGreaterThanOrEqual(1);

    // Hide (DOM-level visibility shim — headless has no OS tab switch).
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const beforeHidden = polls;
    await page.waitForTimeout(13_000);
    const duringHidden = polls - beforeHidden;
    expect(duringHidden, 'ZERO polls while hidden (13s > one tick)').toBe(0);

    // Restore → the service refreshes immediately and ticks resume.
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect
      .poll(() => polls - beforeHidden, { timeout: 15_000, message: 'polling resumes after restore' })
      .toBeGreaterThan(duringHidden);

    evidence(
      `(i) visiblePolls=${visiblePolls} hiddenPolls=${duringHidden} maxConcurrent=${maxConcurrent} resumed=${
        polls - beforeHidden
      }`,
    );
    expect(maxConcurrent, 'no overlapping message fetches (supersede/in-flight guard)').toBeLessThanOrEqual(1);
  });
});
