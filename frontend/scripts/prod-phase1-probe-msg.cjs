/**
 * PROD Phase 1 supplemental probe — message-row structure inspection.
 * Strictly read-only: no clicks, no inputs, no storage writes.
 */
const { chromium } = require('playwright');

const PROD_URL =
  'http://localhost:9797/projects/83da04de-a410-4fb5-9e92-251a99d28a52/' +
  'instances/cba392f7-49c8-403c-852d-f7c260ae4606';
const SETTLE_MS = 4000;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(PROD_URL, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(SETTLE_MS);

  const probe = await page.evaluate(() => {
    const chat = document.querySelector('app-chat');
    if (!chat) return { error: 'no app-chat' };

    // List all .message-row with role class + first 80 chars of text content.
    const rows = Array.from(chat.querySelectorAll('.message-row')).map((row, i) => {
      const cls = row.className;
      const role = /user-message/.test(cls)
        ? 'user'
        : /assistant-message/.test(cls)
          ? 'assistant'
          : /system-message/.test(cls)
            ? 'system'
            : 'unknown';
      const content = row.querySelector('.message-content');
      const t = (content?.textContent || '').trim();
      const header = row.querySelector('.message-header, .role, .author');
      const headerText = header ? (header.textContent || '').trim().slice(0, 60) : null;
      return {
        idx: i,
        role,
        cls: cls.slice(0, 80),
        headerText,
        contentSnippet: t.slice(0, 80),
        contentLength: t.length,
      };
    });

    // Unique first-80-chars patterns (sanity check).
    const snippetCounts = {};
    for (const r of rows) {
      const key = r.contentSnippet;
      snippetCounts[key] = (snippetCounts[key] || 0) + 1;
    }

    // Visible chat header (title / instance id).
    const chatHeaderTitle = chat.querySelector('.chat-header .instance-id, .chat-header .title, .chat-header h2, .chat-header h1');
    const chatHeaderAgent = chat.querySelector('.chat-header .agent-name');
    const headerInstanceId = chatHeaderTitle ? (chatHeaderTitle.textContent || '').trim() : null;
    const headerAgentName = chatHeaderAgent ? (chatHeaderAgent.textContent || '').trim() : null;

    // Loading spinner inside chat?
    const spinner = chat.querySelector('mat-spinner, mat-progress-spinner, .loading-overlay, .spinner');
    const spinnerVisible = spinner ? getComputedStyle(spinner).display !== 'none' : false;

    // Empty state?
    const empty = chat.querySelector('.empty-state');
    const emptyVisible = empty ? getComputedStyle(empty).display !== 'none' : false;

    // Detect chat-input area
    const inputArea = chat.querySelector('textarea, .chat-input, app-message-input');
    const inputVisible = inputArea ? getComputedStyle(inputArea).display !== 'none' : false;

    return {
      rowsCount: rows.length,
      rowsSample: rows.slice(0, 5),
      rowsLast: rows.slice(-3),
      snippetCounts,
      headerInstanceId,
      headerAgentName,
      spinnerVisible,
      emptyVisible,
      inputVisible,
    };
  });
  console.log(JSON.stringify(probe, null, 2));

  await browser.close();
})().catch((e) => { console.error('FATAL', e?.stack || e); process.exit(2); });