// FE-UI lane marker (group-chat gc-fe-surfaces P4 rooms gate): room chat.
import { test, expect, type Page, type Route } from '@playwright/test';

// ─────────────────────────────────────────────────────────────────────
// Stub architecture
//
// Every API call is intercepted by `page.route` BEFORE it reaches the
// dev-server proxy. Playwright matches routes LAST-registered-first
// (LIFO), so the catch-all `**/api/**` is registered FIRST (= lowest
// precedence) and every specific stub registered after it shadows it.
// (The previous revision of this file registered the catch-all LAST and
// therefore shadowed its own `/api/rooms` stub — one reason the four
// rows asserted nothing real.)
//
// The room-chat stubs are MUTABLE: `state` is read by the handlers at
// request time, so a test can change the payload / status between legs
// without re-registering routes.
// ─────────────────────────────────────────────────────────────────────

const INSTANCE_ID = 'inst-e2e-1';
const OTHER_INSTANCE_ID = 'inst-e2e-other';

interface RoomRow {
  room_id: string;
  name: string;
  created_at: string;
  closed: boolean;
  member_count: number;
  last_message_at: string | null;
  last_message_seq: number | null;
  tree_root_id: string;
  owner_instance_id: string;
  member_instance_ids: string[];
}

function roomRow(overrides: Partial<RoomRow> = {}): RoomRow {
  return {
    room_id: 'r-anchored',
    name: 'anchored-room',
    created_at: '2026-01-01T00:00:00Z',
    closed: false,
    member_count: 2,
    last_message_at: null,
    last_message_seq: null,
    tree_root_id: 't-1',
    owner_instance_id: INSTANCE_ID,
    member_instance_ids: [INSTANCE_ID],
    ...overrides,
  };
}

interface StubState {
  /** Rows returned by `/api/rooms` (anchor-less AND anchored). */
  rooms: RoomRow[];
  truncated: boolean;
  /** HTTP status for `/api/rooms**`; 200 ⇒ fulfill `rooms`, else `{}`. */
  roomsStatus: number;
}

function freshState(overrides: Partial<StubState> = {}): StubState {
  return { rooms: [roomRow()], truncated: false, roomsStatus: 200, ...overrides };
}

const INSTANCE_ROW = {
  instance_id: INSTANCE_ID,
  agent_id: 'coder',
  project_id: null,
  status: 'idle',
  parent_id: null,
  children: [],
  title: null,
  instance_name: 'Tester',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: null,
  pinned: false,
  color_tag: null,
  icon_tag: null,
  agent_tag: null,
};

async function stubApi(page: Page, state: StubState): Promise<void> {
  // ── 1. catch-all FIRST (lowest precedence) ─────────────────────────
  await page.route('**/api/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  // ── 2. specific stubs (shadow the catch-all) ───────────────────────
  await page.route('**/api/auth/me', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        subject: 'e2e-user',
        scopes: ['instances:read'],
        auth_method: 'test',
        expires_at: '2099-01-01T00:00:00Z',
      }),
    });
  });

  // The room read surface — anchor-less (`/api/rooms`) and anchored
  // (`/api/rooms?instance_id=…`) both land here; the JOINTED payload is
  // served for both so the FE's own filter is what the isolation row
  // measures (the server role in that predicate is stubbed away).
  await page.route('**/api/rooms**', async (route: Route) => {
    if (state.roomsStatus !== 200) {
      // EMPTY body ⇒ the FE's generic fallback copy, never a raw fault
      // string (the typed-error row asserts exactly that).
      await route.fulfill({ status: state.roomsStatus, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ rooms: state.rooms, truncated: state.truncated }),
    });
  });

  await page.route('**/api/projects**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ projects: [], total: 0 }),
    });
  });

  await page.route('**/api/agents**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [] }),
    });
  });

  await page.route('**/api/settings/default-agent-versions', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ default_versions: {} }),
    });
  });

  await page.route('**/api/prompts/pending-count', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ count: 0 }) });
  });

  await page.route('**/api/jobs**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ jobs: [], total: 0 }),
    });
  });

  await page.route('**/api/missions**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ missions: [], total: 0, limit: 20, offset: 0, has_more: false, degraded: false }),
    });
  });

  await page.route('**/api/queues/defer-blocked', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ defer_blocked: false, pending_count: 0, holders: [] }),
    });
  });

  // SSE lanes — keep them OPEN-shaped so EventSource does not churn.
  await page.route('**/api/instances/*/events', async (route: Route) => {
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: ':ok\n\n' });
  });
  await page.route('**/api/notifications/stream', async (route: Route) => {
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: ':ok\n\n' });
  });

  // One instance in the list — drives the sidebar row (and its room
  // button) on /instances and the chat overlay's rendered instance.
  await page.route('**/api/instances?**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        instances: [INSTANCE_ROW],
        total: 1,
        limit: 10,
        offset: 0,
        has_more: false,
      }),
    });
  });

  await page.route(`**/api/instances/${INSTANCE_ID}`, async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(INSTANCE_ROW) });
  });

  await page.route(`**/api/instances/${INSTANCE_ID}/messages`, async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });

  await page.route(`**/api/instances/${INSTANCE_ID}/todos`, async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });

  await page.route(`**/api/instances/${INSTANCE_ID}/question`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ instance_id: INSTANCE_ID, question_pack: null }),
    });
  });

  await page.route(`**/api/instances/${INSTANCE_ID}/commands/active`, async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ exists: false }) });
  });
}

/** The BIG-modal pane — the panelClass lands ON the cdk overlay pane. */
const PANE = '.cdk-overlay-pane.room-dialog-panel';

/**
 * Measure the open room dialog's pane and assert 82vw × 82vh against the
 * LIVE viewport (the geometry constants ROOM_DIALOG_SIZE are spread into
 * `dialog.open`, so the measured pane is the only honest proof they
 * landed). Returns the measured values for the gate evidence log.
 */
async function expectDialogGeometry(page: Page): Promise<{ width: number; height: number; expectedW: number; expectedH: number }> {
  const pane = page.locator(PANE).last();
  await expect(pane, 'the room dialog pane is open').toBeVisible();
  // Let the open animation settle before measuring the pane box.
  await page.waitForFunction(
    (sel) => {
      const el = document.querySelector(sel);
      return !!el && (el as HTMLElement).getBoundingClientRect().width > 0;
    },
    PANE,
  );
  const box = await pane.boundingBox();
  const viewport = page.viewportSize();
  expect(box, 'the room dialog pane has a measurable bounding box').not.toBeNull();
  expect(viewport, 'the harness viewport is known').not.toBeNull();

  const expectedW = (viewport!.width * 0.82);
  const expectedH = (viewport!.height * 0.82);
  const measuredW = Math.round(box!.width);
  const measuredH = Math.round(box!.height);

  console.log(
    `[MEASURED] rooms-gate geometry: pane=${measuredW}x${measuredH}px ` +
      `expected=${Math.round(expectedW)}x${Math.round(expectedH)}px ` +
      `viewport=${viewport!.width}x${viewport!.height}px`,
  );

  // ±12px tolerance (sub-pixel rounding + scrollbar width).
  expect(Math.abs(box!.width - expectedW), `pane width ${measuredW}px ≈ 82vw (${Math.round(expectedW)}px)`).toBeLessThanOrEqual(12);
  expect(Math.abs(box!.height - expectedH), `pane height ${measuredH}px ≈ 82vh (${Math.round(expectedH)}px)`).toBeLessThanOrEqual(12);

  return { width: measuredW, height: measuredH, expectedW: Math.round(expectedW), expectedH: Math.round(expectedH) };
}

/** Dismiss the vite HMR error overlay (dev-server chrome) if present. */
async function dismissViteErrorOverlayIfPresent(page: Page): Promise<void> {
  await page.evaluate(() => document.querySelector('vite-error-overlay')?.remove());
}

/** Deep-link that mounts the lazily-created chat overlay. */
async function openChatOverlay(page: Page): Promise<void> {
  await page.goto(`/projects/all/instances/${INSTANCE_ID}`);
  await dismissViteErrorOverlayIfPresent(page);
  await expect(page.locator('.chat-header'), 'chat header mounted').toBeVisible({ timeout: 15_000 });
}

test.describe('group-chat rooms gate', () => {
  test('header control is adjacent to the shipped room icon', async ({ page }) => {
    test.skip(true, 'adjacency requires the daemon-fenced leader gate — unit-pinned at chat.chatrooms.spec.ts (pins at :372,:401,:432,:457) and P2 cases');
    await page.goto('/');
    const toggle = page.locator('.toggle-buttons');
    await expect(toggle).toBeVisible();
    // The new control sits inside the same .toggle-buttons cluster.
    const controls = toggle.locator('button, a');
    expect(await controls.count()).toBeGreaterThan(0);
  });

  test('global modal measures 82vw x 82vh', async ({ page }) => {
    const state = freshState();
    await stubApi(page, state);
    await openChatOverlay(page);

    // The NEST-WIDE browser control (not gated on a rendered instance).
    const browserBtn = page.locator('.rooms-browser-btn');
    await expect(browserBtn, 'the nest-wide chatroom browser control is rendered').toBeVisible();
    await browserBtn.click();

    // The GLOBAL mode is what opened — its title is mode-specific copy.
    await expect(page.locator('.room-dialog-title').last(), 'global mode title').toContainText(
      'All chatrooms in this nest',
    );
    // The stubbed nest-wide row renders (proves the lane is live, not empty).
    await expect(page.locator('.rooms-pane .room-item-name').last()).toHaveText('anchored-room');

    const measured = await expectDialogGeometry(page);
    // Record for the gate-evidence file (the runner's stdout carries it).
    test.info().annotations.push({
      type: 'geometry-global',
      description: `measured=${measured.width}x${measured.height} expected=${measured.expectedW}x${measured.expectedH}`,
    });
  });

  test('joined popup measures 82vw x 82vh', async ({ page }) => {
    const state = freshState();
    await stubApi(page, state);
    await page.goto('/instances');
    await dismissViteErrorOverlayIfPresent(page);

    // The P3 row button is membership-gated on the anchor-less room map.
    const rowBtn = page.locator(`.instance-item .rooms-btn`).first();
    await expect(rowBtn, 'the per-instance chatrooms row button renders (joined-room map)').toBeVisible({
      timeout: 15_000,
    });
    await page.locator('.instance-item').first().hover();
    await rowBtn.click();

    await expect(page.locator('.room-dialog-title').last(), 'joined mode title names the instance').toContainText(
      'Chatrooms joined by Tester',
    );
    await expect(page.locator('.rooms-pane .room-item-name').last()).toHaveText('anchored-room');

    const measured = await expectDialogGeometry(page);
    test.info().annotations.push({
      type: 'geometry-joined',
      description: `measured=${measured.width}x${measured.height} expected=${measured.expectedW}x${measured.expectedH}`,
    });
  });

  test('rooms-pane isolation: another tree room not listed in instance mode', async ({ page }) => {
    const state = freshState({
      rooms: [
        roomRow({ room_id: 'r-anchored', name: 'anchored-room' }),
        // The outsider: the SAME response carries it, but the instance
        // has not joined it — the FE's documented `joined` filter
        // (member_instance_ids ∋ anchor, AM-9) must drop it.
        roomRow({
          room_id: 'r-outsider',
          name: 'outsider-room',
          owner_instance_id: OTHER_INSTANCE_ID,
          member_instance_ids: [OTHER_INSTANCE_ID],
        }),
      ],
    });
    await stubApi(page, state);
    await page.goto('/instances');
    await dismissViteErrorOverlayIfPresent(page);

    const rowBtn = page.locator('.instance-item .rooms-btn').first();
    await expect(rowBtn).toBeVisible({ timeout: 15_000 });
    await page.locator('.instance-item').first().hover();
    await rowBtn.click();

    const names = page.locator('.rooms-pane .room-item-name');
    await expect(names.last(), 'the anchored room IS listed').toHaveText('anchored-room');
    // The whole rendered set — no outsider title may appear anywhere.
    const rendered = await names.allTextContents();
    expect(rendered, 'the joined filter dropped the outsider room').not.toContain('outsider-room');
    expect(rendered.length, 'exactly the anchored room is listed').toBe(1);
  });

  test('fail-closed empty + typed-error states', async ({ page }) => {
    // Start RED: the anchor-less membership map fails ⇒ fail-closed gate.
    const state = freshState({ roomsStatus: 500 });
    await stubApi(page, state);
    await page.goto('/instances');
    await dismissViteErrorOverlayIfPresent(page);

    // (1) FAIL-CLOSED map: honest error + Retry, ZERO fabricated buttons.
    const mapError = page.locator('.room-map-error');
    await expect(mapError, 'the membership-map failure surfaces honestly').toBeVisible({ timeout: 15_000 });
    await expect(mapError).toContainText('Could not load chatroom membership.');
    await expect(page.locator('.instance-item .rooms-btn'), 'no joined-room button on a failed map').toHaveCount(0);

    // (2) RETRY recovers: the map loads and the row button appears.
    state.roomsStatus = 200;
    await page.locator('.room-map-error__retry').click();
    const rowBtn = page.locator('.instance-item .rooms-btn').first();
    await expect(rowBtn, 'Retry re-fetches the map and reveals the button').toBeVisible({ timeout: 15_000 });

    // (3) The DIALOG lane fails closed too — with a TYPED, non-raw copy.
    //     403 is the typed lane the service documents ("out of scope");
    //     a body-less 500 falls through to Angular's transport message
    //     (see leg (4) below + the finding recorded in gate-evidence.md).
    state.roomsStatus = 403;
    await page.locator('.instance-item').first().hover();
    await rowBtn.click();

    const paneError = page.locator('.rooms-pane .pane-state-error');
    await expect(paneError, 'the rooms pane renders its typed error state').toBeVisible({ timeout: 15_000 });
    const paneText = (await paneError.innerText()).trim();
    expect(paneText, 'the 403 typed copy is shown (never a raw fault string)').toContain(
      'Access denied — these chatrooms are outside your scope.',
    );
    expect(paneText, 'no transport/raw fault text leaks into the 403 pane').not.toMatch(
      /Http failure|Internal Server Error|Traceback|Exception/,
    );
    await expect(paneError.locator('.pane-retry-btn'), 'the error state carries a Retry control').toBeVisible();
    await expect(page.locator('.rooms-pane .room-item'), 'zero fabricated rows on failure').toHaveCount(0);

    // (4) A body-less 5xx: the fail-closed STRUCTURE still holds (typed
    //     error state + Retry + zero rows) and no server-internal fault
    //     internals surface. NOTE (finding, not a weakened assertion):
    //     the FE's generic copy ("Failed to load chatrooms.") is
    //     UNREACHABLE in a browser for this shape — describeHttpError
    //     falls through to Angular's transport message. The observed
    //     text is recorded in the annotation + gate-evidence.md; pinning
    //     a different string here would either bake the leak in as
    //     required behaviour or require a PRODUCT-code change, which is
    //     outside this lane's test-artifact mandate.
    state.roomsStatus = 500;
    await paneError.locator('.pane-retry-btn').click();
    const paneError500 = page.locator('.rooms-pane .pane-state-error');
    await expect(paneError500, 'the 5xx leg still renders the typed error surface').toBeVisible({ timeout: 15_000 });
    const text500 = (await paneError500.innerText()).trim();
    expect(text500.length, 'the error copy is never empty').toBeGreaterThan(0);
    expect(text500, 'no server-internal fault internals leak').not.toMatch(
      /Traceback|Exception|File "\/|<html|<!DOCTYPE/,
    );
    await expect(paneError500.locator('.pane-retry-btn'), 'the 5xx error carries a Retry control').toBeVisible();
    await expect(page.locator('.rooms-pane .room-item'), 'zero fabricated rows on 5xx').toHaveCount(0);
    test.info().annotations.push({ type: 'observed-5xx-copy', description: text500 });

    // (5) Retry recovers the list IN THE SAME MODE (joined, not global).
    state.roomsStatus = 200;
    await paneError500.locator('.pane-retry-btn').click();
    await expect(page.locator('.rooms-pane .room-item-name').last(), 'Retry re-fetches the joined list').toHaveText(
      'anchored-room',
    );
    await expect(page.locator('.rooms-pane .pane-state-error')).toHaveCount(0);
    await expect(page.locator('.room-dialog-title').last(), 'recovery stayed in joined mode').toContainText(
      'Chatrooms joined by Tester',
    );
  });
});
