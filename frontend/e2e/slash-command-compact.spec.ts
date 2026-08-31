import { test, expect, Page } from '@playwright/test';
import { createTestInstance } from './fixtures/test-helpers';
import { trackInstance, cleanupAll } from './fixtures/cleanup';

/**
 * E2E — /compact slash-command UX (phase2-plan.md §Test Strategy, SC1–SC15
 * as applicable + O17 keepalive-on-idle).
 *
 * MOCK STRATEGY (plan-sanctioned): Playwright ``page.route`` intercepts
 *   (a) POST /api/instances/{id}/messages  → §7 command ack / 400 / message
 *       bodies (branching on content for mixed scenarios),
 *   (b) GET  /api/instances/{id}/events    → aborted (SSE dead) so the
 *       transitions ride the REST fallback — EventSource streams cannot be
 *       push-driven from route.fulfill,
 *   (c) GET  /api/instances/{id}/commands/active → scripted bodies stepping
 *       in_progress → fallback_applied / success / {exists:false}.
 *
 * SSE-driven transitions (heartbeats, phase_seq guard) are covered
 * deterministically by the Jest logic-mirror suite
 * (command-state.service.spec.ts).
 *
 * Conventions per send-pause-button.spec.ts: serial, createTestInstance /
 * cleanup fixtures, domcontentloaded + waitForSelector — NEVER
 * waitUntil:'networkidle' (NotificationService holds an SSE open forever).
 */

const FRONTEND_URL = 'http://localhost:4199';
const COMPOSER = 'textarea.input-textarea';
const CARD = '[data-testid="active-command-card"]';

test.describe.configure({ mode: 'serial' });

// ─────────────────────────────────────────────────────────────────────────
// §7 wire-body builders (mirror the pinned schema verbatim)
// ─────────────────────────────────────────────────────────────────────────

function uuid(): string {
  return crypto.randomUUID();
}

function isoNow(): string {
  return new Date().toISOString();
}

interface AckOverrides {
  status?: string;
  command?: string;
  command_id?: string | null;
  state?: 'accepted' | 'rejected';
  reason?: string | null;
  detail?: string | null;
  ttl_seconds?: number;
}

function commandAck(overrides: AckOverrides = {}): Record<string, unknown> {
  return {
    status: 'command',
    command: 'compact',
    command_id: uuid(),
    state: 'accepted',
    reason: null,
    detail: null,
    timestamp: isoNow(),
    ttl_seconds: 600,
    ...overrides,
  };
}

interface ProgressOverrides {
  instance_id?: string;
  command_id?: string;
  phase?: string;
  phase_seq?: number;
  elapsed_ms?: number;
  eta_ms?: number;
  detail?: Record<string, unknown> | null;
}

function progressEvent(instanceId: string, overrides: ProgressOverrides = {}): Record<string, unknown> {
  return {
    instance_id: instanceId,
    command_id: 'e2e-cmd-1',
    phase: 'in_progress',
    phase_seq: 1,
    timestamp: isoNow(),
    elapsed_ms: 8000,
    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// Route-mock harness
// ─────────────────────────────────────────────────────────────────────────

interface CommandMocks {
  /** POST /messages handler — return a body (fulfill 200) or a special
   *  descriptor; content-based branching lives in the test. */
  respondPost: (postBody: { content?: string }) => {
    status: number;
    body: Record<string, unknown>;
  };
  /** GET /commands/active script — receives the 1-based POST-command call
   *  count (see ``gateUntilPost``). */
  activeScript?: (postCommandCallCount: number) => Record<string, unknown>;
  /** When true, the active-command GET answers ``{exists:false}`` until
   *  the command POST fires. This mirrors real timing: a load-time
   *  reconcile that resolves AFTER the POST belongs to a post-command
   *  world and must report the command, not the pre-command void. Without
   *  this gate a fast test can have the first GET land post-POST and
   *  legitimately clear the freshly seeded card (server-wins semantics). */
  gateUntilPost?: boolean;
  /** Abort the SSE stream (SSE-dead recovery scenarios). */
  killSse?: boolean;
  /** Hold the FIRST post-command GET /commands/active response for N ms.
   *  Used by tests that must observe the ack-seeded ``waiting`` phase:
   *  a load-time reconcile that resolves right after the POST legitimately
   *  applies server truth (in_progress) within ms, skipping the transient
   *  waiting render (server-wins). Holding the first response keeps the
   *  waiting card observable, then releases it. */
  delayFirstActiveMs?: number;
}

async function installCommandMocks(page: Page, instanceId: string, mocks: CommandMocks): Promise<{ postCount: () => number; activeCount: () => number }> {
  let postCount = 0;
  let activeCount = 0;
  let postCommandActiveCount = 0;
  let commandPosted = false;

  await page.route(`**/api/instances/${instanceId}/messages`, async (route) => {
    const req = route.request();
    if (req.method() !== 'POST') return route.fallback();
    postCount++;
    let parsed: { content?: string } = {};
    try {
      parsed = JSON.parse(req.postData() ?? '{}');
    } catch (e) {
      console.log('[SC-MOCK] POST body JSON.parse failed:', String(e), JSON.stringify(req.postData()));
    }
    console.log('[SC-MOCK] POST #', postCount, 'content=', JSON.stringify(parsed.content));
    const respond = mocks.respondPost(parsed);
    console.log('[SC-MOCK] →', respond.status, JSON.stringify(respond.body).slice(0, 120));
    commandPosted = true;
    await route.fulfill({
      status: respond.status,
      contentType: 'application/json',
      body: JSON.stringify(respond.body),
    });
  });

  if (mocks.activeScript) {
    await page.route(`**/api/instances/${instanceId}/commands/active`, async (route) => {
      activeCount++;
      if (mocks.gateUntilPost && !commandPosted) {
        // Pre-command world — nothing on the server registry yet.
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ exists: false }),
        });
        return;
      }
      postCommandActiveCount++;
      console.log('[SC-MOCK] ACTIVE call #', postCommandActiveCount);
      const body = JSON.stringify(mocks.activeScript!(postCommandActiveCount));
      if (postCommandActiveCount === 1 && mocks.delayFirstActiveMs) {
        await new Promise((r) => setTimeout(r, mocks.delayFirstActiveMs));
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body,
      });
    });
  }

  if (mocks.killSse) {
    await page.route(`**/api/instances/${instanceId}/events`, (route) => route.abort());
  }

  return {
    postCount: () => postCount,
    activeCount: () => activeCount,
  };
}

async function clearRoutes(page: Page): Promise<void> {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
}

async function gotoInstance(page: Page, instanceId: string): Promise<void> {
  await page.goto(`${FRONTEND_URL}/instances/${instanceId}`, { waitUntil: 'domcontentloaded' });
  // The composer is the app's "booted on this instance" signal.
  await page.waitForSelector(COMPOSER, { state: 'visible' });
}

async function sendCommand(page: Page, text: string): Promise<void> {
  await page.fill(COMPOSER, text);
  await page.press(COMPOSER, 'Enter');
}

// ─────────────────────────────────────────────────────────────────────────
// Suite
// ─────────────────────────────────────────────────────────────────────────

test.describe('/compact slash-command UX (Phase 2)', () => {
  let page: Page;
  let instanceId: string;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    page.setDefaultTimeout(20000);

    // Surface the app's own chat/SSE diagnostics (house convention from
    // send-pause-button.spec.ts) plus mock-side POST visibility.
    page.on('console', (msg) => {
      const text = msg.text();
      if (text.includes('[SC-MOCK]') || text.includes('[CmdState') || text.includes('[Chat]') || text.includes('[SSE]') || msg.type() === 'error') {
        console.log(`[BROWSER ${msg.type()}] ${text}`);
      }
    });
    page.on('pageerror', (err) => {
      console.log('[BROWSER PAGE ERROR]', err.message);
    });

    const instance = await createTestInstance('leader');
    instanceId = instance.instance_id;
    trackInstance(instanceId);
    console.log(`[slash-commands] Created instance ${instanceId}`);
  });

  test.afterAll(async () => {
    await clearRoutes(page);
    await cleanupAll();
    await page?.close();
  });

  // ── SC3: unknown command rejected client-side, ZERO POST ──────────────
  test('SC3: /foo shows inline error <500ms with zero network POST', async () => {
    await clearRoutes(page);
    const counters = await installCommandMocks(page, instanceId, {
      killSse: false,
      respondPost: () => ({ status: 200, body: commandAck() }),
    });
    await gotoInstance(page, instanceId);

    const t0 = Date.now();
    await sendCommand(page, '/foo');
    await page.waitForSelector('.validation-error', { state: 'visible' });
    const elapsed = Date.now() - t0;
    expect(elapsed).toBeLessThan(500);
    await expect(page.locator('.validation-error')).toContainText('Unknown command: /foo');
    expect(counters.postCount()).toBe(0); // ZERO network call (advisory path)
    // Input keeps the text so the user can fix it.
    await expect(page.locator(COMPOSER)).toHaveValue('/foo');
  });

  // ── AC1 (Task 10): autocomplete palette — / opens, ArrowDown+Enter
  //    completes AND submits (plan e2e acceptance: "/co"+Enter completes
  //    to /compact and submits).
  test('AC1: autocomplete — "/" opens palette; ArrowDown+Enter completes to /compact and submits', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    const PALETTE = '[data-testid="slash-command-palette"]';
    let capturedBody: { content?: string } | null = null;
    const counters = await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      respondPost: (parsed) => {
        capturedBody = parsed;
        return { status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) };
      },
      // Post-command polls answer {exists:false}; the card stays in the
      // ack-seeded waiting phase for the assertion window (~5s poll gap).
      activeScript: () => ({ exists: false }),
    });
    await gotoInstance(page, instanceId);

    // Bare "/" opens the palette listing /compact (name + description).
    await page.fill(COMPOSER, '/');
    await page.waitForSelector(PALETTE, { state: 'visible' });
    const paletteOption = page.locator(`${PALETTE} [role="option"]`, { hasText: '/compact' });
    await expect(paletteOption).toHaveCount(1);
    await expect(paletteOption).toContainText("Compact this instance's message history");
    // ARIA combobox wiring on the input.
    await expect(page.locator(COMPOSER)).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator(COMPOSER)).toHaveAttribute('aria-activedescendant', /slash-command-option-\d+/);

    // ArrowDown keeps a valid highlight; Enter accepts the highlighted
    // command AND submits — identical to typing '/compact' + Enter.
    await page.press(COMPOSER, 'ArrowDown');
    await page.press(COMPOSER, 'Enter');

    // Card appears from the ack; the POST body proves the palette
    // completion delivered the full '/compact' command (trimmed send).
    await page.waitForSelector(`${CARD}[data-command-phase="waiting"]`);
    expect(counters.postCount()).toBe(1);
    expect(capturedBody?.content).toBe('/compact');
    // Palette closed after acceptance; input cleared by the ack path.
    await expect(page.locator(PALETTE)).toHaveCount(0);
  });

  // ── SC1: happy path — ack seeds waiting, REST fallback drives phases ──
  test('SC1: /compact full path — waiting from ack → in_progress → success (+tokens)', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    const counters = await installCommandMocks(page, instanceId, {
      killSse: true, // SSE dead — the sanctioned REST-fallback e2e path
      gateUntilPost: true,
      delayFirstActiveMs: 3000,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: (call) => {
        // Post-command calls: 1st = first poll (5s), then subsequent polls.
        if (call === 1) return { exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 12000, eta_ms: 40000 }) };
        return {
          exists: true,
          command: progressEvent(instanceId, {
            phase: 'success', phase_seq: 2, elapsed_ms: 45000,
            detail: { compacted_type: 'summary', tokens_before: 120000, tokens_after: 45000 },
          }),
        };
      },
    });
    await gotoInstance(page, instanceId);

    await sendCommand(page, '/compact');

    // Card appears in waiting FROM THE ACK — before any SSE/poll event.
    await page.waitForSelector(`${CARD}[data-command-phase="waiting"]`);
    // Zero provisional timeline rows for the command (SC6).
    await expect(page.locator(CARD)).toContainText('/compact');
    const bubbleWithCommand = page.locator('.message-bubble', { hasText: '/compact' });
    await expect(bubbleWithCommand).toHaveCount(0);

    // Poll (5s) drives in_progress with server elapsed + advisory ETA.
    await page.waitForSelector(`${CARD}[data-command-phase="in_progress"]`, { timeout: 20000 });
    await expect(page.locator(CARD)).toContainText('0:12'); // server elapsed_ms
    await expect(page.locator(CARD)).toContainText('~40s remaining');
    // Queued-messages note visible while working.
    await expect(page.locator(CARD)).toContainText('Messages sent now will run after compaction finishes.');

    // Terminal success with verbatim copy + tokens.
    await page.waitForSelector(`${CARD}[data-command-phase="success"]`, { timeout: 30000 });
    await expect(page.locator(CARD)).toContainText('Context compacted');
    await expect(page.locator(CARD)).toContainText('120,000 → 45,000 tokens');
    expect(counters.postCount()).toBe(1);
  });

  // ── SC2a: timeout→fallback, partial_summary branch ────────────────────
  test('SC2a: fallback_applied + partial_summary shows partway copy + tokens', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: (call) => {
        if (call === 1) return { exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 30000 }) };
        return {
          exists: true,
          command: progressEvent(instanceId, {
            phase: 'fallback_applied', phase_seq: 3, elapsed_ms: 90000,
            detail: { compacted_type: 'partial_summary', failure_kind: 'timeout', reason: 'budget_exhausted', tokens_before: 100000, tokens_after: 40000 },
          }),
        };
      },
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="fallback_applied"]`, { timeout: 60000 });
    await expect(page.locator(CARD)).toContainText(
      'Compaction timed out partway — kept the summarized sections, trimmed the un-summarized older section',
    );
    await expect(page.locator(CARD)).toContainText('budget_exhausted');
    await expect(page.locator(CARD)).toContainText('100,000 → 40,000 tokens');
    // No error banner, no crash.
    await expect(page.locator('.validation-error')).toHaveCount(0);
  });

  // ── SC2b: timeout→fallback, truncation branch (honest copy) ───────────
  test('SC2b: fallback_applied + truncation shows the no-summary copy', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: () => ({
        exists: true,
        command: progressEvent(instanceId, {
          phase: 'fallback_applied', phase_seq: 3, elapsed_ms: 88000,
          detail: { compacted_type: 'truncation', failure_kind: 'timeout', tokens_before: 90000, tokens_after: 25000 },
        }),
      }),
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="fallback_applied"]`, { timeout: 60000 });
    await expect(page.locator(CARD)).toContainText(
      'Compaction timed out — history was trimmed without a summary',
    );
  });

  // ── SC13: noop renders as instant SUCCESS, not failure ────────────────
  test('SC13: success + noop shows "Nothing to compact" with reason, no failure styling', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: () => ({
        exists: true,
        command: progressEvent(instanceId, {
          phase: 'success', phase_seq: 1, elapsed_ms: 1200,
          detail: { compacted_type: 'noop', noop_reason: 'recently_compacted' },
        }),
      }),
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="success"]`, { timeout: 60000 });
    await expect(page.locator(CARD)).toContainText('Nothing to compact');
    await expect(page.locator(CARD)).toContainText('Already compacted recently');
    // NOT a failure: success tint, never the failed class (SC13).
    await expect(page.locator(CARD)).not.toHaveClass(/failed/);
    await expect(page.locator(CARD)).toHaveClass(/success-terminal/);
  });

  // ── SC4 + SC5: non-blocking input + duplicate-command guard ───────────
  test('SC4/SC5: normal message posts while card active; duplicate /compact blocked with zero second POST', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    let postSeq = 0;
    const counters = await installCommandMocks(page, instanceId, {
      killSse: true,
      respondPost: (content) => {
        if (content.content === '/compact') {
          return { status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) };
        }
        // Normal message → legacy 202 injected body (deterministic echo id).
        postSeq++;
        return {
          status: 202,
          body: {
            status: 'injected',
            instance_id: instanceId,
            content: content.content ?? '',
            timestamp: isoNow(),
            created_at: isoNow(),
            pending_count: 1,
            message_id: `echo-${postSeq}`,
          },
        };
      },
      activeScript: () => ({ exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 20000 }) }),
      gateUntilPost: true,
      delayFirstActiveMs: 3000,
    });
    await gotoInstance(page, instanceId);

    // First /compact → accepted, card waiting.
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="waiting"]`);

    // SC4: input NOT disabled — a normal message still POSTs and renders
    // its optimistic bubble while the card is active.
    await expect(page.locator(COMPOSER)).toBeEnabled();
    await sendCommand(page, 'a normal message during compaction');
    await page.waitForSelector('.message-bubble', { timeout: 10000 });
    await expect(page.locator(COMPOSER)).toBeEnabled();

    // SC5: duplicate /compact → inline "already in progress", ZERO second
    // command POST (the normal message above was POST #2, so we assert the
    // POST body, not just the count).
    await sendCommand(page, '/compact');
    await page.waitForSelector('.validation-error');
    await expect(page.locator('.validation-error')).toContainText('already in progress');
    const commandPosts = counters.postCount();
    await expect(page.locator(COMPOSER)).toHaveValue('/compact');
    await sendCommand(page, '/compact'); // one more attempt
    await page.waitForSelector('.validation-error');
    expect(counters.postCount()).toBe(commandPosts); // still zero second command POST
  });

  // ── SC6: no message-pipeline pollution (covered in SC1; explicit here) ─
  test('SC6: accepted command creates zero provisional timeline rows', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      delayFirstActiveMs: 3000,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: () => ({ exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 15000 }) }),
    });
    await gotoInstance(page, instanceId);
    const bubblesBefore = await page.locator('.message-bubble').count();
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="waiting"]`);
    await page.waitForTimeout(1000);
    const bubblesAfter = await page.locator('.message-bubble').count();
    expect(bubblesAfter).toBe(bubblesBefore); // +0 rows
  });

  // ── SC7: reload mid-command — card restored from GET (no stuck spinner) ─
  test('SC7: reload mid-command restores in_progress card from GET reconcile', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      respondPost: () => ({ status: 200, body: commandAck() }),
      activeScript: () => ({
        exists: true,
        command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 4, elapsed_ms: 37000 }),
      }),
    });
    await gotoInstance(page, instanceId);
    // No POST needed: boot-time reconcile alone restores the card.
    await page.waitForSelector(`${CARD}[data-command-phase="in_progress"]`, { timeout: 15000 });
    await expect(page.locator(CARD)).toContainText('0:37'); // server elapsed survived reload
  });

  // ── SC9: accessibility parity ──────────────────────────────────────────
  test('SC9: card has role="status" and aria-live="polite"', async () => {
    test.setTimeout(120_000);
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      delayFirstActiveMs: 3000,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: () => ({ exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 10000 }) }),
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector(CARD);
    await expect(page.locator(CARD)).toHaveAttribute('role', 'status');
    await expect(page.locator(CARD)).toHaveAttribute('aria-live', 'polite');
  });

  // ── SC14: rejections render correctly ─────────────────────────────────
  test('SC14a: rejected terminal_instance renders ack detail VERBATIM, input kept', async () => {
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: false,
      respondPost: () => ({
        status: 200,
        body: commandAck({
          state: 'rejected',
          reason: 'terminal_instance',
          detail: 'Send a message to start a new turn, then /compact.',
        }),
      }),
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector('.validation-error');
    // VERBATIM — the exact §9-12 guidance string.
    await expect(page.locator('.validation-error')).toHaveText(
      'Send a message to start a new turn, then /compact.',
    );
    // No card (machine never started); input retained for retry.
    await expect(page.locator(CARD)).toHaveCount(0);
    await expect(page.locator(COMPOSER)).toHaveValue('/compact');
  });

  test('SC14b: rejected busy renders human copy with the reason', async () => {
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: false,
      respondPost: () => ({
        status: 200,
        body: commandAck({ state: 'rejected', reason: 'busy', detail: 'A command is already in flight.' }),
      }),
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector('.validation-error');
    await expect(page.locator('.validation-error')).toContainText('busy');
    await expect(page.locator(CARD)).toHaveCount(0);
  });

  test('SC14c: 400 UNKNOWN_COMMAND → inline error carrying available commands', async () => {
    await clearRoutes(page);
    await installCommandMocks(page, instanceId, {
      killSse: false,
      respondPost: () => ({
        status: 400,
        body: {
          detail: {
            code: 'UNKNOWN_COMMAND',
            message: 'Unknown command: /compact',
            details: { available: ['compact', 'clear'] },
          },
        },
      }),
    });
    await gotoInstance(page, instanceId);
    // NOTE: the command must be KNOWN to the FE registry, otherwise the
    // client-side advisory guard blocks pre-POST with zero network (SC3)
    // and this BE-authoritative path can never fire. Sending a known
    // command and mocking the BE as 400 exercises exactly the R7 drift
    // scenario the typed-error mapping exists for.
    await sendCommand(page, '/compact');
    await page.waitForSelector('.validation-error');
    await expect(page.locator('.validation-error')).toContainText('Unknown command.');
    await expect(page.locator('.validation-error')).toContainText('Available: /compact, /clear');
    await expect(page.locator(CARD)).toHaveCount(0);
  });

  // ── C1 e2e (plan-mandated): // escape delivers a literal message, NOT a command ──
  // Regression for review C1: the FE previously re-parsed ``//x`` into
  // ``/x`` and POSTed that, which the BE re-classified as a command —
  // ``//compact is useful`` triggered REAL compaction (no card yet ⇒ user
  // confusion) and ``//etc/hosts`` 400'd. The fix posts the RAW ``//…``
  // text and lets the BE strip the escape on its end. This e2e covers the
  // end-to-end happy-path through the message branch (NOT the command
  // branch): a bubble with the literal text, no card, no advisory error,
  // and the POST body is verified to be the RAW ``//…`` text (no FE-side
  // pre-strip, which would have re-triggered the original bug).
  test('C1: //compact is useful → literal message bubble, no command ack, no card (POST body raw)', async () => {
    await clearRoutes(page);
    let capturedBody: { content?: string } | null = null;
    const counters = await installCommandMocks(page, instanceId, {
      killSse: false,
      respondPost: (content) => {
        capturedBody = content;
        // BE contract: ``//`` escape is stripped server-side and stored
        // verbatim as a plain message — return the stripped literal.
        const stripped = (content.content ?? '').replace(/^\/\//, '/');
        return {
          status: 202,
          body: {
            status: 'injected',
            instance_id: instanceId,
            content: stripped,
            timestamp: isoNow(),
            created_at: isoNow(),
            pending_count: 1,
            message_id: `echo-escape-${Date.now()}`,
          },
        };
      },
    });
    await gotoInstance(page, instanceId);

    await sendCommand(page, '//compact is useful');

    // A user-bubble must appear (server echoed the stripped literal).
    const bubble = page.locator('.message-bubble', { hasText: '/compact is useful' });
    await expect(bubble).toHaveCount(1, { timeout: 10000 });

    // NO command-ack branch consumed: no card, no validation-error.
    await expect(page.locator(CARD)).toHaveCount(0);
    await expect(page.locator('.validation-error')).toHaveCount(0);

    // POST body verified RAW — the FE bug would have shipped ``/compact…``
    // (one slash pre-stripped) and re-triggered the original bug. With
    // the fix the FE ships ``//…`` and lets the BE strip it.
    expect(capturedBody).not.toBeNull();
    expect(capturedBody?.content).toBe('//compact is useful');
    expect(counters.postCount()).toBe(1);
  });

  // ── SC15: restart + polling semantics ──────────────────────────────────
  test('SC15: {exists:false} clears card silently; poll cadence ~5s while SSE dead; poll stops', async () => {
    test.setTimeout(180_000);
    await clearRoutes(page);
    let existsFalseReturned = false;
    const counters = await installCommandMocks(page, instanceId, {
      killSse: true,
      gateUntilPost: true,
      delayFirstActiveMs: 3000,
      respondPost: () => ({ status: 200, body: commandAck({ command_id: 'e2e-cmd-1' }) }),
      activeScript: (call) => {
        // Post-command calls only. Flip to {exists:false} (daemon restart
        // lost the registry) after the first two polls.
        if (call <= 2) {
          return { exists: true, command: progressEvent(instanceId, { phase: 'in_progress', phase_seq: 1, elapsed_ms: 9000 }) };
        }
        existsFalseReturned = true;
        return { exists: false };
      },
    });
    await gotoInstance(page, instanceId);
    await sendCommand(page, '/compact');
    await page.waitForSelector(`${CARD}[data-command-phase="waiting"]`);

    // Poll drives in_progress (SSE dead).
    await page.waitForSelector(`${CARD}[data-command-phase="in_progress"]`, { timeout: 20000 });

    // Cadence check while polling: sample the counter, wait ~12s, expect
    // 2-3 more GETs (loose bounds — flake-proof ~5s cadence evidence).
    const before = counters.activeCount();
    await page.waitForTimeout(12000);
    const during = counters.activeCount() - before;
    expect(during).toBeGreaterThanOrEqual(1);
    expect(during).toBeLessThanOrEqual(4);

    // Restart: flip the script to {exists:false}; next poll clears the card.
    existsFalseReturned = true;
    await page.waitForSelector(CARD, { state: 'detached', timeout: 20000 });
    // SILENT: no error toast, no inline error appeared.
    await expect(page.locator('.validation-error')).toHaveCount(0);

    // Poll stopped: counter frozen after the clear.
    const stopped = counters.activeCount();
    await page.waitForTimeout(12000);
    expect(counters.activeCount() - stopped).toBeLessThanOrEqual(1); // no ~5s cadence anymore
  });

  // ── O17: SSE transport keepalive-on-idle (V-1 automated proxy) ─────────
  test('O17: SSE stream stays OPEN across a >30s idle window (keepalive proxy check)', async ({ browser }) => {
    test.setTimeout(120_000);
    // Dedicated page + instance: real backend, real SSE, NO route mocks.
    const instance = await createTestInstance('leader');
    trackInstance(instance.instance_id);
    const keepalivePage = await browser.newPage();
    await keepalivePage.addInitScript(() => {
      const Orig = window.EventSource;
      interface PatchedWindow extends Window {
        EventSource: typeof EventSource;
        __sseSources?: EventSource[];
      }
      const w = window as PatchedWindow;
      class LoggingEventSource extends Orig {
        constructor(url: string | URL, protocols?: string | string[]) {
          super(url, protocols);
          w.__sseSources = w.__sseSources ?? [];
          w.__sseSources.push(this);
        }
      }
      w.EventSource = LoggingEventSource as unknown as typeof EventSource;
    });

    try {
      await keepalivePage.goto(`${FRONTEND_URL}/instances/${instance.instance_id}`, {
        waitUntil: 'domcontentloaded',
      });
      await keepalivePage.waitForSelector(COMPOSER, { state: 'visible' });
      await keepalivePage.waitForTimeout(2000); // let the stream open

      // Sample the INSTANCE /events EventSource across ~35s — longer than
      // the backend's 30s SSE_PING_INTERVAL. A missing keepalive on a
      // proxy with an idle timeout would manifest as readyState cycling
      // 0 (CONNECTING) or the source being replaced (reconnect). Other
      // /events consumers exist on this page (workspace project tree,
      // notifications stream) — filter to the instance stream only.
      const samples = await keepalivePage.evaluate(async () => {
        const states: number[] = [];
        for (let i = 0; i < 35; i++) {
          const src = (window.__sseSources ?? []).find(
            (s) => s.url.includes('/instances/') && s.url.endsWith('/events'),
          );
          states.push(src ? src.readyState : -1);
          await new Promise((r) => setTimeout(r, 1000));
        }
        return states;
      });
      // readyState: 0 CONNECTING, 1 OPEN, 2 CLOSED. Every sample must be
      // OPEN and there must be exactly ONE instance stream (no reconnect
      // cycle during the window).
      const sourceCount = await keepalivePage.evaluate(
        () =>
          (window.__sseSources ?? []).filter(
            (s) => s.url.includes('/instances/') && s.url.endsWith('/events'),
          ).length,
      );
      expect(sourceCount).toBe(1);
      expect(samples.length).toBe(35);
      for (const state of samples) {
        expect(state).toBe(1);
      }
    } finally {
      await keepalivePage.close();
    }
  });
});
