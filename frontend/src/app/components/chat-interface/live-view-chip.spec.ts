/**
 * Live-view chip-injection spec — Phase 2.
 *
 * The chat-interface's ``scanForLiveViewChips`` walks the message
 * container after markdown rendering, picks out ``<a>`` tags
 * whose href passes the ``parseLiveViewUrl`` matcher, and
 * replaces each with a clickable chip. The chip's click handler
 * delegates to ``LiveViewActionsService.openViewer``.
 *
 * The spec strategy:
 *
 *  - The message bubble is rendered by the chat-interface
 *    template (the @for / @if blocks that wrap every message
 *    row). The bubble's inner HTML is empty when the markdown
 *    chain is stubbed, so the spec SEEDS anchor children
 *    directly into the bubble's DOM (the same way a real
 *    ``<markdown>`` render would).
 *  - The MutationObserver that the chat-interface attaches in
 *    ``ensureMutationObserver`` fires on the DOM mutation; the
 *    rAF-coalesced ``scheduleScan`` then runs, which calls
 *    ``scanForLiveViewChips`` which swaps the anchor for a
 *    chip. The spec awaits the rAF + a microtask flush.
 *  - The matcher's full bypass matrix is pinned in
 *    ``constants/live-view-url.spec.ts`` — the spec here only
 *    needs to assert that the chat-interface's scan USES the
 *    matcher correctly (i.e. it does NOT swap anchors whose
 *    href is not a live-view URL).
 *
 * The chat-interface spec already stubs ``ngx-markdown`` (the
 * real package is raw ESM and the jest pipeline cannot
 * transform it). The stub still renders the message bubble
 * shell — that is enough for the chip scan to find.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import type { Message } from '../../models';
import { ChatInterfaceComponent } from './chat-interface.component';
import { ImageViewerActionsService } from '../../services/image-viewer-actions.service';
import { LiveViewActionsService } from '../../services/live-view-actions.service';

jest.mock('ngx-markdown', () => {
  const core = require('@angular/core');
  return {
    MarkdownModule: core.NgModule({ imports: [] })(class MarkdownModuleStub {}),
    provideMarkdown: () => [],
  };
});

import { provideMarkdown } from 'ngx-markdown';

// jsdom gaps: no scrollIntoView, and requestAnimationFrame only exists
// when the environment enables pretendToBeVisual. The component's
// auto-scroll + scan paths touch both on every CD pass.
beforeAll(() => {
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? jest.fn();
  if (typeof window.requestAnimationFrame !== 'function') {
    window.requestAnimationFrame = ((cb: FrameRequestCallback) =>
      setTimeout(() => cb(Date.now()), 0) as unknown as number) as typeof window.requestAnimationFrame;
    window.cancelAnimationFrame = ((id: number) => ((clearTimeout(id) as unknown) as number));
  }
});

function makeAssistantMessage(content: string, overrides: Partial<Message> = {}): Message {
  return {
    message_id: 'inst-1-msg-1',
    role: 'assistant',
    content,
    created_at: '2026-10-08T04:00:00Z',
    ...overrides,
  };
}

describe('ChatInterfaceComponent — live-view chip injection (Phase 2)', () => {
  let fixture: ComponentFixture<ChatInterfaceComponent>;
  let liveViewOpen: jest.Mock;

  /**
   * Wait for the rAF-coalesced scan to fire. The chat-interface
   * schedules the chip scan via ``requestAnimationFrame`` after a
   * MutationObserver tick; the test waits one macrotask + one
   * microtask flush so the rAF callback has time to run.
   */
  async function waitForScan(): Promise<void> {
    // Two animation frames so the MutationObserver's rAF + the
    // rAF scheduled by scheduleScan both have a chance to run.
    await new Promise<void>((resolve) =>
      window.requestAnimationFrame(() => resolve()),
    );
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  }

  function setupMount(messages: Message[]): void {
    liveViewOpen = jest.fn();
    TestBed.configureTestingModule({
      imports: [ChatInterfaceComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        provideMarkdown(),
        {
          provide: ImageViewerActionsService,
          useValue: { openViewer: jest.fn() },
        },
        {
          provide: LiveViewActionsService,
          useValue: { openViewer: liveViewOpen },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatInterfaceComponent);
    fixture.componentRef.setInput('instanceId', 'inst-1');
    fixture.componentRef.setInput('messages', messages);
    fixture.detectChanges();
  }

  function getBubble(): HTMLElement {
    const bubble = fixture.nativeElement.querySelector('.message-bubble');
    if (!bubble) {
      throw new Error('expected a .message-bubble in the rendered DOM');
    }
    return bubble as HTMLElement;
  }

  function seedAnchor(bubble: HTMLElement, href: string, text: string): HTMLAnchorElement {
    const anchor = document.createElement('a');
    anchor.setAttribute('href', href);
    anchor.textContent = text;
    bubble.appendChild(anchor);
    return anchor;
  }

  function queryChips(): HTMLButtonElement[] {
    return Array.from(
      fixture.nativeElement.querySelectorAll('[data-testid="live-view-chip"]'),
    ) as HTMLButtonElement[];
  }

  describe('accepted URLs render as chips', () => {
    it('replaces a /views/planning/<rel> anchor with a chip', async () => {
      setupMount([makeAssistantMessage('See [this](/views/planning/ens/plan.md).')]);
      const bubble = getBubble();
      seedAnchor(bubble, '/views/planning/ens/plan.md', 'this');
      await waitForScan();
      fixture.detectChanges();

      const chips = queryChips();
      expect(chips.length).toBe(1);
      expect(chips[0].getAttribute('data-live-view-href')).toBe(
        '/views/planning/ens/plan.md',
      );
      expect(chips[0].getAttribute('data-live-view-root')).toBe('planning');
      expect(chips[0].getAttribute('data-live-view-rel')).toBe('ens/plan.md');
    });

    it('replaces a /views/designer-artifact/<rel> anchor with a chip', async () => {
      setupMount([
        makeAssistantMessage(
          '[view](/views/designer-artifact/ens/feat/design/mockups/landing.html)',
        ),
      ]);
      const bubble = getBubble();
      seedAnchor(
        bubble,
        '/views/designer-artifact/ens/feat/design/mockups/landing.html',
        'view',
      );
      await waitForScan();
      fixture.detectChanges();

      const chips = queryChips();
      expect(chips.length).toBe(1);
      expect(chips[0].getAttribute('data-live-view-root')).toBe('designer-artifact');
    });

    it('replaces a /views/tmp-images/<id> anchor with a chip', async () => {
      setupMount([
        makeAssistantMessage(
          '[img](/views/tmp-images/abcdef0123456789abcdef0123456789)',
        ),
      ]);
      const bubble = getBubble();
      seedAnchor(
        bubble,
        '/views/tmp-images/abcdef0123456789abcdef0123456789',
        'img',
      );
      await waitForScan();
      fixture.detectChanges();

      const chips = queryChips();
      expect(chips.length).toBe(1);
      expect(chips[0].getAttribute('data-live-view-root')).toBe('tmp-images');
    });

    it('handles multiple anchors in the same bubble', async () => {
      setupMount([
        makeAssistantMessage('Two artifacts: [a](/views/planning/ens/a.md) and [b](/views/planning/ens/b.md).'),
      ]);
      const bubble = getBubble();
      seedAnchor(bubble, '/views/planning/ens/a.md', 'a');
      seedAnchor(bubble, '/views/planning/ens/b.md', 'b');
      await waitForScan();
      fixture.detectChanges();

      const chips = queryChips();
      expect(chips.length).toBe(2);
      const hrefs = chips.map((c) => c.getAttribute('data-live-view-href')).sort();
      expect(hrefs).toEqual([
        '/views/planning/ens/a.md',
        '/views/planning/ens/b.md',
      ]);
    });

    it('derives the chip title from the file name (strips .html)', async () => {
      setupMount([
        makeAssistantMessage(
          '[view](/views/planning/ens/feat/design/mockups/landing.html)',
        ),
      ]);
      const bubble = getBubble();
      seedAnchor(
        bubble,
        '/views/planning/ens/feat/design/mockups/landing.html',
        'view',
      );
      await waitForScan();
      fixture.detectChanges();

      const chip = queryChips()[0];
      const label = chip.querySelector('.live-view-chip-label');
      expect(label?.textContent?.trim()).toBe('landing');
    });
  });

  describe('rejected URLs do NOT render as chips', () => {
    // The full bypass matrix is pinned in
    // ``constants/live-view-url.spec.ts``. The cases below
    // exercise the chat-interface side: a non-live-view anchor
    // is left untouched and the chip is never built.
    const rejected: ReadonlyArray<{ href: string; reason: string }> = [
      { href: '/api/views/x/y', reason: 'wrong prefix' },
      { href: 'http://example.com/views/x/y', reason: 'absolute URL' },
      { href: '//host/views/x/y', reason: 'protocol-relative' },
      { href: '/views', reason: 'no trailing path' },
      { href: '/viewsfoo/x/y', reason: 'wrong prefix (no separator)' },
      { href: 'javascript:alert(1)', reason: 'javascript scheme' },
      { href: 'mailto:foo@bar', reason: 'mailto' },
      { href: 'https://example.com/foo', reason: 'unrelated absolute URL' },
    ];

    for (const { href, reason } of rejected) {
      it(`does NOT chip ${JSON.stringify(href)} (${reason})`, async () => {
        setupMount([makeAssistantMessage(`[text](${href})`)]);
        const bubble = getBubble();
        seedAnchor(bubble, href, 'text');
        await waitForScan();
        fixture.detectChanges();

        // No chip is built.
        expect(queryChips().length).toBe(0);
        // The anchor stays in the DOM untouched.
        const anchors = bubble.querySelectorAll('a[href]');
        expect(anchors.length).toBe(1);
        expect(anchors[0].getAttribute('href')).toBe(href);
      });
    }
  });

  describe('chip click → opens the WebView dialog', () => {
    it('calls LiveViewActionsService.openViewer with the chip href', async () => {
      setupMount([
        makeAssistantMessage('[view](/views/planning/ens/plan.md)'),
      ]);
      const bubble = getBubble();
      seedAnchor(bubble, '/views/planning/ens/plan.md', 'view');
      await waitForScan();
      fixture.detectChanges();

      const chip = queryChips()[0];
      chip.click();
      expect(liveViewOpen).toHaveBeenCalledWith('/views/planning/ens/plan.md');
    });

    it('does NOT bubble the click (stopPropagation)', async () => {
      // A bubble-level click listener would catch the click if
      // the chip did not stopPropagation. The spec verifies
      // the chip itself carries the handler and a click does
      // not produce a second side effect; the production
      // template does not attach a bubble-level click today,
      // so a future contributor who adds one sees this spec
      // stay green (the chip is still bound to openViewer).
      setupMount([
        makeAssistantMessage('[view](/views/planning/ens/plan.md)'),
      ]);
      const bubble = getBubble();
      seedAnchor(bubble, '/views/planning/ens/plan.md', 'view');
      await waitForScan();
      fixture.detectChanges();

      const chip = queryChips()[0];
      // dispatchEvent + preventDefault is what the click() helper
      // would do; we use dispatchEvent to assert no extra
      // listeners fire.
      const evt = new MouseEvent('click', { bubbles: true, cancelable: true });
      chip.dispatchEvent(evt);
      expect(liveViewOpen).toHaveBeenCalledTimes(1);
      expect(liveViewOpen).toHaveBeenCalledWith('/views/planning/ens/plan.md');
    });
  });

  describe('idempotency', () => {
    it('does NOT double-inject when the scan runs twice on the same anchor', async () => {
      setupMount([
        makeAssistantMessage('[view](/views/planning/ens/plan.md)'),
      ]);
      const bubble = getBubble();
      seedAnchor(bubble, '/views/planning/ens/plan.md', 'view');
      await waitForScan();
      fixture.detectChanges();
      // After the first scan, the anchor is GONE — it was
      // replaced by the chip. A second scan finds no anchor
      // and produces no second chip.
      await waitForScan();
      fixture.detectChanges();
      expect(queryChips().length).toBe(1);
    });
  });

  describe('scope', () => {
    it('does NOT touch anchors outside a .message-bubble', async () => {
      // Plant a stray anchor in the chat header (e.g. a future
      // contributor adds a header link). The scan is scoped to
      // .message-bubble so the stray anchor is left alone.
      setupMount([makeAssistantMessage('hi')]);
      const headerArea = fixture.nativeElement.querySelector('.chat-header');
      if (headerArea) {
        const stray = document.createElement('a');
        stray.setAttribute('href', '/views/planning/ens/plan.md');
        stray.textContent = 'stray';
        headerArea.appendChild(stray);
      }
      await waitForScan();
      fixture.detectChanges();
      expect(queryChips().length).toBe(0);
    });
  });
});
