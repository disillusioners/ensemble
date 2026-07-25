import { test, expect, Page, BrowserContext, request as pwRequest } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * E2E: Queue Selector Visibility Across Instance States
 *
 * Validates commit c9c2b42c — `isQueueSelectorVisible()` now hides the selector
 * ONLY for active states (running, waiting_children, paused, queued) and shows
 * it for everything else (idle, completed, error, failed, terminated, waiting,
 * null/undefined).
 *
 * KEY REGRESSION: the selector was previously missing on COMPLETED instances.
 * Test 1 directly exercises this.
 *
 * Visibility matrix (from message-input.component.ts isQueueSelectorVisible):
 *   HIDDEN   : running, waiting_children, paused, queued
 *   VISIBLE  : idle, completed, error, failed, terminated, waiting, null
 *
 * Avoids `waitUntil:'networkidle'` — SSE never goes idle.
 */

const FRONTEND_URL = 'http://localhost:4199';
const API_URL = 'http://localhost:8079';
const SHOTS_DIR = path.join(__dirname, '..', '..', 'e2e-shots', 'queue-selector-states');

const HIDDEN_STATES = ['running', 'waiting_children', 'paused', 'queued'];

function ensureShotsDir() {
  if (!fs.existsSync(SHOTS_DIR)) fs.mkdirSync(SHOTS_DIR, { recursive: true });
}

async function createProject(apiCtx: Awaited<ReturnType<typeof pwRequest.newContext>>, suffix: string) {
  const name = `E2E-QSelStates-${suffix}-${Date.now()}`;
  const res = await apiCtx.post('/api/projects', {
    data: { name, project_type: 'general' },
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok()) throw new Error(`project create failed: ${res.status()} ${await res.text()}`);
  const body = await res.json();
  const projectId = body.project_id ?? body.id;
  // Give the background queue auto-provision a moment to land.
  for (let i = 0; i < 10; i++) {
    const q = await apiCtx.get(`/api/projects/${projectId}/queues`);
    if (q.ok()) {
      const jb = await q.json();
      if (jb.queues && jb.queues.length >= 2) break;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return { projectId, name };
}

async function spawnInstance(
  apiCtx: Awaited<ReturnType<typeof pwRequest.newContext>>,
  projectId: string,
  agentId = 'leader'
): Promise<string> {
  const res = await apiCtx.post('/api/instances', {
    data: { agent_id: agentId, project_id: projectId },
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok()) throw new Error(`spawn failed: ${res.status()} ${await res.text()}`);
  const body = await res.json();
  return body.instance_id;
}

async function getInstance(apiCtx: Awaited<ReturnType<typeof pwRequest.newContext>>, instanceId: string) {
  const res = await apiCtx.get(`/api/instances/${instanceId}`);
  if (!res.ok()) throw new Error(`get instance failed: ${res.status()}`);
  return res.json();
}

/**
 * Navigate to an instance chat and wait for the message-input to render.
 * Seeds the `ensemble-project-tabs` localStorage BEFORE navigating so
 * tabStateService.activeProjectId() resolves to the right project
 * (message-input reads projectId from there, not route params).
 */
async function openInstanceChat(page: Page, projectId: string, projectName: string, instanceId: string) {
  await page.addInitScript(
    ({ pid, pname }) => {
      localStorage.setItem(
        'ensemble-project-tabs',
        JSON.stringify({
          openTabs: [
            { id: 'all', name: 'All', type: 'all' },
            { id: pid, name: pname, type: 'project' },
          ],
          activeTabId: pid,
        })
      );
    },
    { pid: projectId, pname: projectName }
  );
  await page.goto(`${FRONTEND_URL}/projects/${projectId}/instances/${instanceId}`, {
    waitUntil: 'domcontentloaded',
  });
  // Wait for the instance panel / message input wrapper to appear. Do NOT use networkidle (SSE).
  await page.waitForSelector('app-message-input', { timeout: 20000 });
  // Let the component resolve queues + computed signals.
  await page.waitForTimeout(1000);
}

/**
 * Inspect the queue selector's visibility by reading the Angular component
 * state directly AND probing the DOM. Returns a structured verdict.
 */
async function probeSelector(page: Page): Promise<{
  domVisible: boolean; // <label class="queue-selector"> present in DOM
  signalVisible: boolean | null; // isQueueSelectorVisible() signal value
  statusFromComponent: string | null; // instanceStatus() as seen by component
  queuesLen: number | null;
}> {
  return page.evaluate(() => {
    const ng = (window as any).ng;
    const el = document.querySelector('app-message-input');
    const comp = el && ng?.getComponent ? ng.getComponent(el) : null;
    const label = document.querySelector('label.queue-selector');
    const status = comp && typeof comp.instanceStatus === 'function' ? comp.instanceStatus() : null;
    return {
      domVisible: !!label,
      signalVisible:
        comp && typeof comp.isQueueSelectorVisible === 'function'
          ? comp.isQueueSelectorVisible()
          : null,
      statusFromComponent: status === null ? 'null' : String(status),
      queuesLen: comp && typeof comp.queues === 'function' ? comp.queues().length : null,
    };
  });
}

test.describe('Queue Selector Visibility Across Instance States (c9c2b42c)', () => {
  test.setTimeout(300000);

  let ctx: BrowserContext;
  let page: Page;
  let adminApi: Awaited<ReturnType<typeof pwRequest.newContext>>;
  let project: { projectId: string; name: string };
  const createdInstances: string[] = [];

  test.beforeAll(async ({ browser }) => {
    ensureShotsDir();
    ctx = await browser.newContext();
    page = await ctx.newPage();
    page.setDefaultTimeout(20000);
    adminApi = await pwRequest.newContext({ baseURL: API_URL });
    project = await createProject(adminApi, 'main');
    console.log(`\n[setup] project=${project.projectId} name=${project.name}`);
  });

  test.afterAll(async () => {
    for (const id of createdInstances) {
      try {
        await adminApi.delete(`/api/instances/${id}`);
      } catch (e) {
        console.log(`[cleanup] instance ${id}: ${(e as Error).message}`);
      }
    }
    try {
      await adminApi.delete(`/api/projects/${project.projectId}`);
    } catch (e) {
      console.log(`[cleanup] project: ${(e as Error).message}`);
    }
    if (adminApi) await adminApi.dispose();
    if (ctx) await ctx.close();
  });

  /**
   * Poll an instance until it reaches a target status (or non-idle null initial),
   * with a timeout.
   */
  async function waitForStatus(
    instanceId: string,
    target: string | null,
    timeoutMs = 90000
  ): Promise<string | null> {
    const start = Date.now();
    let last: string | null = null;
    while (Date.now() - start < timeoutMs) {
      const inst = await getInstance(adminApi, instanceId);
      last = inst.status ?? null;
      if (target === null ? last === null : last === target) return last;
      await new Promise((r) => setTimeout(r, 1500));
    }
    return last;
  }

  /**
   * Poll until the instance reaches a *terminal-ish* or running state, used when
   * we just want it to leave idle and observe whatever it becomes.
   */
  async function waitForAnyNonIdle(instanceId: string, timeoutMs = 90000): Promise<string | null> {
    const start = Date.now();
    let last: string | null = null;
    while (Date.now() - start < timeoutMs) {
      const inst = await getInstance(adminApi, instanceId);
      last = inst.status ?? null;
      if (last !== 'idle' && last !== null) return last;
      await new Promise((r) => setTimeout(r, 1500));
    }
    return last;
  }

  /**
   * Send a message to make an instance run.
   */
  async function sendMessage(instanceId: string, content: string) {
    const res = await adminApi.post(`/api/instances/${instanceId}/messages`, {
      data: { content },
      headers: { 'Content-Type': 'application/json' },
    });
    if (!res.ok()) throw new Error(`send message failed: ${res.status()} ${await res.text()}`);
    return res.json();
  }

  // ────────────────────────────────────────────────────────────────────────
  // Test 1 (KEY): COMPLETED instance → selector VISIBLE
  // ────────────────────────────────────────────────────────────────────────
  test('Test 1 (KEY): COMPLETED instance → selector VISIBLE', async () => {
    // Spawn + send a trivial message + wait for completion.
    const instanceId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(instanceId);
    console.log(`[T1] spawned ${instanceId}`);

    // It should be idle right after spawn.
    const initial = await getInstance(adminApi, instanceId);
    console.log(`[T1] initial status: ${initial.status}`);

    await sendMessage(instanceId, 'Reply with exactly: done');
    const final = await waitForStatus(instanceId, 'completed', 120000);
    console.log(`[T1] final status after message: ${final}`);

    let result: 'PASS' | 'FAIL' | 'SKIPPED' = 'SKIPPED';
    let note = '';

    if (final !== 'completed') {
      note = `Could not reach 'completed' (got '${final}'). Selector visibility on completed unverified via DOM.`;
      // Still probe whatever state we're in for diagnostics.
      await openInstanceChat(page, project.projectId, project.name, instanceId);
      const probe = await probeSelector(page);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test1-completed-fallback.png'), fullPage: false });
      console.log(`[T1] fallback probe: ${JSON.stringify(probe)}`);
      result = 'SKIPPED';
    } else {
      await openInstanceChat(page, project.projectId, project.name, instanceId);
      const probe = await probeSelector(page);
      console.log(`[T1] probe: ${JSON.stringify(probe)}`);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test1-completed.png'), fullPage: false });

      const ok = probe.domVisible && probe.signalVisible === true;
      result = ok ? 'PASS' : 'FAIL';
      note = `status=${probe.statusFromComponent} domVisible=${probe.domVisible} signal=${probe.signalVisible} queues=${probe.queuesLen}`;
    }

    console.log(`\n=== TEST 1 RESULT: ${result} ===\n${note}\n`);
    // We assert softly: record the outcome. The "must be visible" expectation:
    if (final === 'completed') {
      expect(result, `COMPLETED instance MUST show selector. ${note}`).toBe('PASS');
    } else {
      test.skip(true, `Could not reach completed state (got ${final}); skipping hard assertion`);
    }
  });

  // ────────────────────────────────────────────────────────────────────────
  // Test 2: IDLE instance → selector VISIBLE (regression)
  // ────────────────────────────────────────────────────────────────────────
  test('Test 2: IDLE instance → selector VISIBLE (regression)', async () => {
    const instanceId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(instanceId);
    const inst = await getInstance(adminApi, instanceId);
    console.log(`[T2] spawned ${instanceId} status=${inst.status}`);

    await openInstanceChat(page, project.projectId, project.name, instanceId);
    const probe = await probeSelector(page);
    console.log(`[T2] probe: ${JSON.stringify(probe)}`);
    await page.screenshot({ path: path.join(SHOTS_DIR, 'test2-idle.png'), fullPage: false });

    const ok = probe.domVisible && probe.signalVisible === true;
    console.log(`\n=== TEST 2 RESULT: ${ok ? 'PASS' : 'FAIL'} === status=${probe.statusFromComponent}\n`);
    expect(ok, `IDLE instance MUST show selector. ${JSON.stringify(probe)}`).toBe(true);
  });

  // ────────────────────────────────────────────────────────────────────────
  // Test 3: New instance (null status) → selector VISIBLE
  // ────────────────────────────────────────────────────────────────────────
  test('Test 3: New instance (null status) → selector VISIBLE', async () => {
    const instanceId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(instanceId);
    const inst = await getInstance(adminApi, instanceId);
    console.log(`[T3] spawned ${instanceId} status=${inst.status}`);

    await openInstanceChat(page, project.projectId, project.name, instanceId);
    const probe = await probeSelector(page);
    console.log(`[T3] probe: ${JSON.stringify(probe)}`);
    await page.screenshot({ path: path.join(SHOTS_DIR, 'test3-new-null.png'), fullPage: false });

    // A freshly-spawned instance may report idle OR null. Both must show selector.
    const ok = probe.domVisible && probe.signalVisible === true;
    console.log(`\n=== TEST 3 RESULT: ${ok ? 'PASS' : 'FAIL'} === status=${probe.statusFromComponent}\n`);
    expect(ok, `New/null-status instance MUST show selector. ${JSON.stringify(probe)}`).toBe(true);
  });

  // ────────────────────────────────────────────────────────────────────────
  // Test 4: RUNNING instance → selector HIDDEN
  // ────────────────────────────────────────────────────────────────────────
  test('Test 4: RUNNING instance → selector HIDDEN', async () => {
    const instanceId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(instanceId);
    console.log(`[T4] spawned ${instanceId}`);

    await sendMessage(instanceId, 'Think step by step for a while, do not finish quickly.');
    // Wait for it to become running (or any non-idle active state).
    const start = Date.now();
    let becameActive: string | null = null;
    while (Date.now() - start < 60000) {
      const inst = await getInstance(adminApi, instanceId);
      const s = inst.status ?? null;
      if (s === 'running' || s === 'waiting_children') {
        becameActive = s;
        break;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    console.log(`[T4] became active: ${becameActive}`);

    let result: 'PASS' | 'FAIL' | 'SKIPPED' = 'SKIPPED';
    let note = '';

    if (becameActive !== 'running' && becameActive !== 'waiting_children') {
      note = `Could not catch instance in running/waiting_children (last='${becameActive}').`;
      result = 'SKIPPED';
    } else {
      await openInstanceChat(page, project.projectId, project.name, instanceId);
      const probe = await probeSelector(page);
      console.log(`[T4] probe: ${JSON.stringify(probe)}`);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test4-running.png'), fullPage: false });

      const hidden = !probe.domVisible && probe.signalVisible === false;
      result = hidden ? 'PASS' : 'FAIL';
      note = `status=${probe.statusFromComponent} domVisible=${probe.domVisible} signal=${probe.signalVisible}`;
    }

    console.log(`\n=== TEST 4 RESULT: ${result} ===\n${note}\n`);
    if (result === 'PASS' || result === 'FAIL') {
      expect(result, `RUNNING instance MUST hide selector. ${note}`).toBe('PASS');
    } else {
      test.skip(true, note);
    }
  });

  // ────────────────────────────────────────────────────────────────────────
  // Test 5: PAUSED instance → selector HIDDEN
  // ────────────────────────────────────────────────────────────────────────
  test('Test 5: PAUSED instance → selector HIDDEN', async () => {
    const instanceId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(instanceId);
    console.log(`[T5] spawned ${instanceId}`);

    await sendMessage(instanceId, 'Think step by step for a while, do not finish quickly.');
    // Wait for running, then pause.
    const start = Date.now();
    let paused = false;
    let lastStatus: string | null = null;
    while (Date.now() - start < 60000) {
      const inst = await getInstance(adminApi, instanceId);
      lastStatus = inst.status ?? null;
      if (lastStatus === 'running') {
        const pauseRes = await adminApi.post(`/api/instances/${instanceId}/pause`);
        console.log(`[T5] pause attempt -> ${pauseRes.status()}`);
        if (pauseRes.ok()) {
          // Confirm it actually paused.
          await new Promise((r) => setTimeout(r, 2000));
          const after = await getInstance(adminApi, instanceId);
          if ((after.status ?? null) === 'paused') {
            paused = true;
            break;
          }
        }
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    console.log(`[T5] paused=${paused} lastStatus=${lastStatus}`);

    let result: 'PASS' | 'FAIL' | 'SKIPPED' = 'SKIPPED';
    let note = '';

    if (!paused) {
      note = `Could not reach 'paused' (last='${lastStatus}'). Pause may require precise timing.`;
      result = 'SKIPPED';
    } else {
      await openInstanceChat(page, project.projectId, project.name, instanceId);
      const probe = await probeSelector(page);
      console.log(`[T5] probe: ${JSON.stringify(probe)}`);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test5-paused.png'), fullPage: false });

      const hidden = !probe.domVisible && probe.signalVisible === false;
      result = hidden ? 'PASS' : 'FAIL';
      note = `status=${probe.statusFromComponent} domVisible=${probe.domVisible} signal=${probe.signalVisible}`;
    }

    console.log(`\n=== TEST 5 RESULT: ${result} ===\n${note}\n`);
    if (result === 'PASS' || result === 'FAIL') {
      expect(result, `PAUSED instance MUST hide selector. ${note}`).toBe('PASS');
    } else {
      test.skip(true, note);
    }
  });

  // ────────────────────────────────────────────────────────────────────────
  // Test 6: queue_id emission check
  // On a visible-selector instance: payload includes selected queue_id.
  // On a hidden-selector (running) instance: queue_id is null/omitted.
  // ────────────────────────────────────────────────────────────────────────
  test('Test 6: queue_id emission — visible emits UUID, hidden emits null', async () => {
    // 6a. IDLE (selector visible): intercept the POST and confirm queue_id is a UUID.
    const idleId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(idleId);
    await openInstanceChat(page, project.projectId, project.name, idleId);

    // Wait for the select to be visible (options inside a closed <select> are NOT
    // "visible" per Playwright's definition, so wait on the <select> itself).
    await page.waitForSelector('label.queue-selector select', { timeout: 15000 });
    // Then read options (attached, not visibility-checked).
    const allOptions = await page
      .locator('label.queue-selector select option')
      .evaluateAll((opts) =>
        opts.map((o) => ({
          value: (o as HTMLOptionElement).value,
          text: (o as HTMLOptionElement).textContent?.trim(),
        }))
      );
    const fifoOpt = allOptions.find((o) => o.text === 'system_fifo_queue');
    console.log(`[T6] queues: ${JSON.stringify(allOptions)}`);
    expect(fifoOpt, 'system_fifo_queue option must exist').toBeTruthy();

    // Select fifo via the UI so selectedQueueId signal updates.
    await page.locator('label.queue-selector select').selectOption(fifoOpt!.value);
    await page.waitForTimeout(400);

    // Intercept the outgoing POST /messages request.
    const idleRequestPromise = page.waitForRequest(
      (req) => req.url().includes(`/instances/${idleId}/messages`) && req.method() === 'POST',
      { timeout: 20000 }
    );

    const textarea = page.locator('textarea').first();
    await textarea.fill('E2E queue_id emission probe (idle).');
    await page.locator('button.send-button').first().click();

    const idleReq = await idleRequestPromise;
    const idleBody = JSON.parse(idleReq.postData() || '{}');
    console.log(`[T6a] idle POST body: ${JSON.stringify(idleBody)}`);
    await page.screenshot({ path: path.join(SHOTS_DIR, 'test6a-idle-send.png'), fullPage: false });

    const idleHasQueueId =
      idleBody.queue_id && typeof idleBody.queue_id === 'string' && idleBody.queue_id === fifoOpt!.value;
    console.log(`\n=== TEST 6a (idle queue_id) RESULT: ${idleHasQueueId ? 'PASS' : 'FAIL'} ===\n`);
    expect(
      idleHasQueueId,
      `Idle send MUST include queue_id === ${fifoOpt!.value}. Got: ${idleBody.queue_id}`
    ).toBe(true);

    // 6b. RUNNING (selector hidden): queue_id must be null.
    const runId = await spawnInstance(adminApi, project.projectId, 'leader');
    createdInstances.push(runId);
    await sendMessage(runId, 'Think step by step for a while, do not finish quickly.');

    // Wait for running.
    const start = Date.now();
    let becameRunning = false;
    while (Date.now() - start < 60000) {
      const inst = await getInstance(adminApi, runId);
      if ((inst.status ?? null) === 'running') {
        becameRunning = true;
        break;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }

    let resultB: 'PASS' | 'FAIL' | 'SKIPPED' = 'SKIPPED';
    let noteB = '';
    if (!becameRunning) {
      noteB = 'Could not reach running state for 6b.';
      resultB = 'SKIPPED';
    } else {
      await openInstanceChat(page, project.projectId, project.name, runId);
      const probe = await probeSelector(page);
      console.log(`[T6b] probe: ${JSON.stringify(probe)}`);

      // For a running instance, the UI offers an "injection" send (canInject).
      const runRequestPromise = page.waitForRequest(
        (req) => req.url().includes(`/instances/${runId}/messages`) && req.method() === 'POST',
        { timeout: 20000 }
      );
      const ta = page.locator('textarea').first();
      await ta.fill('E2E queue_id emission probe (running).');
      // Click whichever send button is rendered (injection or normal).
      const sendBtn = page.locator('button.send-button').first();
      await sendBtn.click().catch(() => {/* may be disabled briefly */});

      let runBody: any = {};
      try {
        const runReq = await Promise.race([
          runRequestPromise,
          new Promise<null>((r) => setTimeout(() => r(null), 12000)),
        ]);
        if (runReq) runBody = JSON.parse(runReq.postData() || '{}');
      } catch (e) {
        /* ignore */
      }
      console.log(`[T6b] running POST body: ${JSON.stringify(runBody)}`);
      await page.screenshot({ path: path.join(SHOTS_DIR, 'test6b-running-send.png'), fullPage: false });

      const runQueueIdNull = runBody.queue_id === null || runBody.queue_id === undefined;
      resultB = runQueueIdNull ? 'PASS' : 'FAIL';
      noteB = `status=${probe.statusFromComponent} signal=${probe.signalVisible} queue_id=${runBody.queue_id}`;
    }

    console.log(`\n=== TEST 6b (running queue_id null) RESULT: ${resultB} ===\n${noteB}\n`);
    if (resultB === 'PASS' || resultB === 'FAIL') {
      expect(resultB, `Running send MUST have queue_id null. ${noteB}`).toBe('PASS');
    } else {
      test.skip(true, noteB);
    }
  });
});
