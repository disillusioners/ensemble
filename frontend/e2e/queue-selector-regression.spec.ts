import { test, expect, Page, BrowserContext, request as pwRequest } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Regression check for fd876cfb (HTML [selected] per-option) + 2567af9e (TS default-by-name).
 *
 * Both fixes together ensure the dropdown defaults to system_parallel_queue:
 *   - fd876cfb: moved [value] from <select> to [selected] per <option> so the
 *     Angular binding reflects selectedQueueId() regardless of async render timing.
 *   - 2567af9e: resolved the default queue by NAME (queue_name === 'system_parallel_queue')
 *     and stored the resolved UUID in selectedQueueId, so the localStorage slot
 *     never holds a queue NAME.
 *
 * Tests:
 *   1. Default selection resolves to system_parallel_queue (UUID match).
 *   2. User-chosen selection persists across reload (localStorage round-trip).
 *   3. Sending a message with selected queue transitions instance out of idle.
 */

const FRONTEND_URL = 'http://localhost:4199';
const API_URL = 'http://localhost:8079';
const PROJECT_ID = '9c022ae3-5bb8-43a4-9132-4c4a4d3ae971';
const PROJECT_NAME = 'E2E-QueueSelector-1784966240935';

const SHOTS_DIR = path.join(__dirname, '..', '..', 'e2e-shots', 'queue-selector');

function ensureShotsDir() {
  if (!fs.existsSync(SHOTS_DIR)) fs.mkdirSync(SHOTS_DIR, { recursive: true });
}

async function spawnInstance(apiCtx: Awaited<ReturnType<typeof pwRequest.newContext>>) {
  const spawn = await apiCtx.post('/api/instances', {
    data: { agent_id: 'leader', project_id: PROJECT_ID },
    headers: { 'Content-Type': 'application/json' },
  });
  expect(spawn.ok()).toBeTruthy();
  const newInstance = await spawn.json();
  return newInstance.instance_id;
}

async function gotoChatAndWaitForQueueSelector(page: Page, instanceId: string) {
  await page.goto(`${FRONTEND_URL}/projects/${PROJECT_ID}/instances/${instanceId}`, {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForSelector('label.queue-selector select', { timeout: 20000 });
  await page.waitForTimeout(800);
}

test.describe('Queue Selector Regression Check (fd876cfb)', () => {
  test.setTimeout(120000);

  let ctx: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    ensureShotsDir();
    ctx = await browser.newContext();
    page = await ctx.newPage();
    page.setDefaultTimeout(20000);
    await page.addInitScript(
      ({ projectId, projectName }) => {
        localStorage.setItem(
          'ensemble-project-tabs',
          JSON.stringify({
            openTabs: [
              { id: 'all', name: 'All', type: 'all' },
              { id: projectId, name: projectName, type: 'project' },
            ],
            activeTabId: projectId,
          })
        );
        // Clear queue selection on first nav only — use localStorage flag so it
        // survives page.reload() (window-scoped flags reset on reload).
        const flagKey = '__ensemble_queue_cleared__';
        if (localStorage.getItem(flagKey) !== '1') {
          localStorage.removeItem(`ensemble-queue-select-${projectId}`);
          localStorage.setItem(flagKey, '1');
        }
      },
      { projectId: PROJECT_ID, projectName: PROJECT_NAME }
    );
  });

  test.afterAll(async () => {
    if (ctx) await ctx.close();
  });

  /**
   * Test 1: Default selection should be system_parallel_queue.
   * Verifies the fix for the latent bug where selectedQueueId was seeded with
   * the queue NAME (compared against queue_id UUIDs) and the fallback picked
   * queues[0] (system_background_queue) instead of system_parallel_queue.
   */
  test('Test 1 (default selection): dropdown defaults to system_parallel_queue', async () => {
    const apiCtx = await pwRequest.newContext({ baseURL: API_URL });
    const newInstanceId = await spawnInstance(apiCtx);

    try {
      // Visit chat URL to clear+seed localStorage in a single nav.
      await gotoChatAndWaitForQueueSelector(page, newInstanceId);

      const state = await page.evaluate((projectId) => {
        const ng = (window as any).ng;
        const el = document.querySelector('app-message-input');
        const comp = el && ng?.getComponent ? ng.getComponent(el) : null;
        const sel = document.querySelector('label.queue-selector select') as HTMLSelectElement | null;
        return {
          selectedQueueId: comp && typeof comp.selectedQueueId === 'function' ? comp.selectedQueueId() : null,
          queuesLen: comp && typeof comp.queues === 'function' ? comp.queues().length : null,
          queuesList:
            comp && typeof comp.queues === 'function' ? comp.queues().map((q: any) => ({ name: q.queue_name, id: q.queue_id })) : null,
          selectValue: sel?.value ?? null,
          selectSelectedIndex: sel?.selectedIndex ?? null,
          storedLocal: localStorage.getItem(`ensemble-queue-select-${projectId}`),
        };
      }, PROJECT_ID);

      console.log('\n=== TEST 1 STATE ===');
      console.log(JSON.stringify(state, null, 2));

      await page.screenshot({
        path: path.join(SHOTS_DIR, 'test1-default-selection.png'),
        fullPage: false,
      });

      const parallel = state.queuesList?.find((q: any) => q.name === 'system_parallel_queue');
      const actualSelected = state.queuesList?.find((q: any) => q.id === state.selectValue);

      console.log(`[Test 1] Expected default: system_parallel_queue (${parallel?.id})`);
      console.log(`[Test 1] Actual default:   ${actualSelected?.name} (${actualSelected?.id})`);

      expect(
        state.selectValue,
        `Default selection MUST be system_parallel_queue (${parallel?.id}). ` +
          `Currently shows ${actualSelected?.name} (${actualSelected?.id}) — ` +
          `signal value=${state.selectedQueueId}, stored=${state.storedLocal}.`
      ).toBe(parallel?.id);
    } finally {
      try {
        await apiCtx.delete(`/api/instances/${newInstanceId}`);
      } catch (e) {
        console.log(`[Test 1] cleanup warning: ${(e as Error).message}`);
      }
      await apiCtx.dispose();
    }
  });

  /**
   * Test 2: Selection persists across reload — the critical regression test.
   * PASSES: User-chosen selections store real UUIDs in localStorage which match on reload.
   */
  test('Test 2: selection persists across reload (CRITICAL regression)', async () => {
    const apiCtx = await pwRequest.newContext({ baseURL: API_URL });
    const newInstanceId = await spawnInstance(apiCtx);

    try {
      // Defensive: navigate explicitly so localStorage is accessible.
      await gotoChatAndWaitForQueueSelector(page, newInstanceId);
      await page.evaluate((projectId) => localStorage.removeItem(`ensemble-queue-select-${projectId}`), PROJECT_ID);
      // Reload to apply cleared state.
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForSelector('label.queue-selector select', { timeout: 20000 });
      await page.waitForTimeout(800);

      const select = page.locator('label.queue-selector select');
      const allOptions = await select.locator('option').evaluateAll((opts) =>
        opts.map((o) => ({
          value: (o as HTMLOptionElement).value,
          text: (o as HTMLOptionElement).textContent?.trim(),
        }))
      );
      const fifoOption = allOptions.find((o) => o.text === 'system_fifo_queue');
      expect(fifoOption, 'system_fifo_queue option must exist').toBeTruthy();
      console.log(`[Test 2] system_fifo_queue queue_id: ${fifoOption!.value}`);

      await select.selectOption(fifoOption!.value);
      await page.waitForTimeout(400);

      const valueAfterChange = await select.inputValue();
      await page.screenshot({
        path: path.join(SHOTS_DIR, 'test2a-after-selecting-fifo.png'),
        fullPage: false,
      });
      console.log(`[Test 2a] after selectOption — value=${valueAfterChange}, expected=${fifoOption!.value}`);
      expect(valueAfterChange).toBe(fifoOption!.value);

      const stored = await page.evaluate((projectId) => localStorage.getItem(`ensemble-queue-select-${projectId}`), PROJECT_ID);
      console.log(`[Test 2] localStorage ensemble-queue-select: ${stored}`);
      expect(stored).toBe(fifoOption!.value);

      // RELOAD — the critical test.
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForSelector('label.queue-selector select', { timeout: 20000 });
      await page.waitForFunction(
        (expected) => {
          const sel = document.querySelector('label.queue-selector select') as HTMLSelectElement | null;
          return sel && sel.value === expected;
        },
        fifoOption!.value,
        { timeout: 10000 }
      );
      await page.waitForTimeout(300);

      const valueAfterReload = await page.locator('label.queue-selector select').inputValue();
      const textAfterReload = await page.locator('label.queue-selector select option:checked').textContent();
      await page.screenshot({
        path: path.join(SHOTS_DIR, 'test2b-after-reload.png'),
        fullPage: false,
      });
      console.log(`[Test 2b] after reload — value=${valueAfterReload}, text=${textAfterReload}`);

      expect(
        valueAfterReload,
        `After reload, dropdown MUST show system_fifo_queue (${fifoOption!.value}). Got: ${valueAfterReload}`
      ).toBe(fifoOption!.value);
      expect(textAfterReload).toContain('system_fifo_queue');
    } finally {
      try {
        await apiCtx.delete(`/api/instances/${newInstanceId}`);
      } catch (e) {
        console.log(`[Test 2] cleanup warning: ${(e as Error).message}`);
      }
      await apiCtx.dispose();
    }
  });

  /**
   * Test 3: Send a message with selected queue; instance transitions out of idle.
   * Spawns a fresh IDLE instance to avoid conflict with Test 2's instance state.
   */
  test('Test 3: sending a message with selected queue transitions instance out of idle', async () => {
    const apiCtx = await pwRequest.newContext({ baseURL: API_URL });
    const newInstanceId = await spawnInstance(apiCtx);
    console.log(`[Test 3] spawned fresh IDLE instance: ${newInstanceId}`);

    try {
      await gotoChatAndWaitForQueueSelector(page, newInstanceId);

      const select = page.locator('label.queue-selector select');
      const allOptions = await select.locator('option').evaluateAll((opts) =>
        opts.map((o) => ({
          value: (o as HTMLOptionElement).value,
          text: (o as HTMLOptionElement).textContent?.trim(),
        }))
      );
      const fifoOption = allOptions.find((o) => o.text === 'system_fifo_queue');
      expect(fifoOption).toBeTruthy();

      await select.selectOption(fifoOption!.value);
      await page.waitForTimeout(400);

      const currentValue = await select.inputValue();
      console.log(`[Test 3] queue selection: ${currentValue}`);

      const textarea = page.locator('textarea').first();
      await textarea.fill('E2E queue selector regression check — please acknowledge.');
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test3a-message-typed.png') });

      const sendButton = page.locator('button.send-button').first();
      await expect(sendButton).toBeEnabled({ timeout: 5000 });
      await sendButton.click();

      // Wait briefly for state change, then verify via API.
      await page.waitForTimeout(4000);
      const res = await apiCtx.get(`/api/instances/${newInstanceId}`);
      const instance = await res.json();
      console.log(`[Test 3] instance after send: status=${instance.status}, pending=${instance.pending_count}`);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test3b-after-send.png') });

      expect(
        ['running', 'queued', 'waiting_children', 'completed', 'terminated'],
        `Expected non-idle status, got ${instance.status}`
      ).toContain(instance.status);
      expect(instance.status).not.toBe('idle');
    } finally {
      try {
        await apiCtx.delete(`/api/instances/${newInstanceId}`);
      } catch (e) {
        console.log(`[Test 3] cleanup warning: ${(e as Error).message}`);
      }
      await apiCtx.dispose();
    }
  });
});
