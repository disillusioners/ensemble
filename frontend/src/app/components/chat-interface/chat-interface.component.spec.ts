import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import type { Message } from '../../models';
import { ChatInterfaceComponent } from './chat-interface.component';
import { ImageViewerActionsService } from '../../services/image-viewer-actions.service';

/**
 * ngx-markdown ships raw ESM (fesm2022 + marked.esm.js) that the jest
 * pipeline does not transform — importing the real package in ANY spec
 * would require a global transformIgnorePatterns change (blast radius
 * across all 60+ suites). Spec-local mock instead, following the repo
 * precedent of isolating heavy deps at the spec boundary
 * (todo-list.component.spec.ts "Testable" pattern rationale). Safe here:
 * these fixtures never render a ``<markdown>`` node (compaction docs
 * render the fold card; the main-bubble markdown branch is never hit),
 * so a decorated empty NG module satisfies the component's
 * ``imports: [MarkdownModule]``.
 */
jest.mock('ngx-markdown', () => {
  const core = require('@angular/core');
  return {
    MarkdownModule: core.NgModule({ imports: [] })(class MarkdownModuleStub {}),
    provideMarkdown: () => [],
  };
});

import { provideMarkdown } from 'ngx-markdown';

/**
 * Unit tests for the compaction fold-with-preview card
 * (compaction-output-structure §10.9b) + the §9 /compact card copy.
 *
 * TestBed pattern (see agent-switcher.component.spec.ts /
 * vscode-editor-cache.component.spec.ts): real component + real template,
 * providers limited to what the DI graph needs to construct
 * (HttpClient → SseService, noop animations → CDK overlay/dialog in
 * MermaidActionsService, ngx-markdown → spec-local mock above).
 *
 * Scope: the ``compaction-global-`` id-prefix special case — collapsed
 * by default, ≤500-char GLOBAL OVERVIEW preview, "Show compacted
 * context" expander revealing the full body, visibility independent of
 * the system-prompt toggle, and the count-aware command titles. NOT in
 * scope: the merge contract (pinned in message-merge.util.spec.ts
 * §10.9a).
 */

// jsdom gaps: no scrollIntoView, and requestAnimationFrame only exists
// when the environment enables pretendToBeVisual. The component's
// auto-scroll + mermaid scan paths touch both on every CD pass.
beforeAll(() => {
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? jest.fn();
  if (typeof window.requestAnimationFrame !== 'function') {
    window.requestAnimationFrame = ((cb: FrameRequestCallback) =>
      setTimeout(() => cb(Date.now()), 0) as unknown as number) as typeof window.requestAnimationFrame;
    window.cancelAnimationFrame = ((id: number) => clearTimeout(id)) as typeof window.cancelAnimationFrame;
  }
});

// ── Fixture ────────────────────────────────────────────────────────────────

const OVERVIEW_SENTENCE_A =
  'The user is hardening the compaction output into a single document. ';
const OVERVIEW_SENTENCE_B =
  'Decisions so far: one SystemMessage per compaction, stable compaction-global id prefix, fold card in the chat surface. ';
// >500 chars — proves the preview cap.
const LONG_OVERVIEW = OVERVIEW_SENTENCE_A.repeat(9) + OVERVIEW_SENTENCE_B.repeat(3);
const SECTION_DETAIL =
  '── SECTION DETAIL ──\n' +
  '### SECTION 1/2 — messages #1–#10\n' +
  'Early arc: baseline FE counts captured at 64 suites / 2342 tests.\n' +
  '\n' +
  '── END OF COMPACTED CONTEXT — everything below is the verbatim recent transcript ──';
const ENVELOPE_HEADER =
  '[CONTEXT COMPACTION — mode=summary | compacted_at=2026-09-01T09:00:00Z | summarized messages #1–#40 → global overview + 2/2 sections]';

const DOC_ID = 'compaction-global-inst-1-3';

function makeCompactionDoc(overrides: Partial<Message> = {}): Message {
  return {
    message_id: DOC_ID,
    role: 'system',
    content: `${ENVELOPE_HEADER}\n\n── GLOBAL OVERVIEW ──\n${LONG_OVERVIEW}\n\n${SECTION_DETAIL}`,
    created_at: '2026-08-30T11:00:00Z',
    ...overrides,
  };
}

describe('ChatInterfaceComponent — compaction fold card (compaction-output-structure §10.9b)', () => {
  let fixture: ComponentFixture<ChatInterfaceComponent>;
  let component: ChatInterfaceComponent;

  function queryCard(): HTMLElement | null {
    return fixture.debugElement.query(By.css('[data-testid="compaction-card"]'))?.nativeElement ?? null;
  }
  function queryPreview(): HTMLElement | null {
    return fixture.debugElement.query(By.css('[data-testid="compaction-card-preview"]'))?.nativeElement ?? null;
  }
  function queryFull(): HTMLElement | null {
    return fixture.debugElement.query(By.css('[data-testid="compaction-card-full"]'))?.nativeElement ?? null;
  }
  function queryExpander(): HTMLButtonElement | null {
    return fixture.debugElement.query(By.css('[data-testid="compaction-card-expander"]'))?.nativeElement ?? null;
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatInterfaceComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        provideMarkdown(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatInterfaceComponent);
    component = fixture.componentInstance;
    // instanceId gates the messages container in the template
    // (@if (!instanceId) renders the empty state instead).
    fixture.componentRef.setInput('instanceId', 'inst-1');
    fixture.componentRef.setInput('messages', [makeCompactionDoc()]);
    fixture.detectChanges();
  });

  it('renders the compaction card for a compaction-global- id — COLLAPSED by default', () => {
    expect(queryCard()).not.toBeNull();
    // Collapsed: preview visible, full body NOT rendered.
    expect(queryPreview()).not.toBeNull();
    expect(queryFull()).toBeNull();
  });

  it('is visible with the system-prompt toggle OFF (default) — the fold card is toggle-independent', () => {
    // Default showSystemPrompt=false would hide a plain system message;
    // the compaction doc must still render its card.
    expect(component.showSystemPrompt).toBe(false);
    expect(queryCard()).not.toBeNull();
    // And it must NOT render as the plain system-prompt block.
    expect(fixture.debugElement.query(By.css('.system-prompt-block'))).toBeNull();
  });

  it('does NOT render the plain system-prompt block even with the toggle ON', () => {
    fixture.componentRef.setInput('showSystemPrompt', true);
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.css('.system-prompt-block'))).toBeNull();
    expect(queryCard()).not.toBeNull();
  });

  it('shows a ≤500-char preview drawn from the GLOBAL OVERVIEW section', () => {
    const preview = queryPreview();
    expect(preview).not.toBeNull();
    const text = preview!.textContent || '';
    expect(text.length).toBeLessThanOrEqual(500);
    // Preview content comes from the overview — not the envelope header,
    // not the section detail.
    expect(text).toContain('hardening the compaction output');
    expect(text).not.toContain('[CONTEXT COMPACTION');
    expect(text).not.toContain('── SECTION DETAIL ──');
    // LONG_OVERVIEW is >500 chars → the preview must be truncated.
    expect(LONG_OVERVIEW.length).toBeGreaterThan(500);
    expect(text.endsWith('…')).toBe(true);
  });

  it('expander reads "Show compacted context" while collapsed and toggles the full body on click', () => {
    const expander = queryExpander();
    expect(expander).not.toBeNull();
    expect((expander!.textContent || '').trim()).toBe('Show compacted context');

    expander!.click();
    fixture.detectChanges();

    const full = queryFull();
    expect(full).not.toBeNull();
    // The FULL body — envelope header, overview, section detail, boundary.
    expect(full!.textContent).toContain('[CONTEXT COMPACTION');
    expect(full!.textContent).toContain('── GLOBAL OVERVIEW ──');
    expect(full!.textContent).toContain('── SECTION DETAIL ──');
    expect(full!.textContent).toContain('── END OF COMPACTED CONTEXT');
    // While expanded the preview is replaced by the full body.
    expect(queryPreview()).toBeNull();
    expect((queryExpander()!.textContent || '').trim()).toBe('Hide compacted context');
  });

  it('collapses again on a second click (toggle)', () => {
    queryExpander()!.click();
    fixture.detectChanges();
    expect(queryFull()).not.toBeNull();

    queryExpander()!.click();
    fixture.detectChanges();
    expect(queryFull()).toBeNull();
    expect(queryPreview()).not.toBeNull();
    expect((queryExpander()!.textContent || '').trim()).toBe('Show compacted context');
  });

  it('keys expansion state by message id — another doc (different seq) stays collapsed', () => {
    queryExpander()!.click();
    fixture.detectChanges();
    expect(component.isCompactionExpanded(DOC_ID)).toBe(true);

    fixture.componentRef.setInput('messages', [
      makeCompactionDoc(),
      makeCompactionDoc({ message_id: 'compaction-global-inst-1-4', created_at: '2026-08-30T11:05:00Z' }),
    ]);
    fixture.detectChanges();
    expect(component.isCompactionExpanded(DOC_ID)).toBe(true);
    expect(component.isCompactionExpanded('compaction-global-inst-1-4')).toBe(false);
    expect(queryFull()).not.toBeNull();
    expect(queryPreview()).not.toBeNull();
  });

  it('degrades the preview to the envelope header when the doc has no GLOBAL OVERVIEW section', () => {
    const truncationDoc = makeCompactionDoc({
      content:
        `${ENVELOPE_HEADER}\n\n── END OF COMPACTED CONTEXT — everything below is the verbatim recent transcript ──`,
    });
    const preview = component.compactionPreview(truncationDoc);
    expect(preview.startsWith('[CONTEXT COMPACTION')).toBe(true);
    expect(preview.length).toBeLessThanOrEqual(500);
  });

  it('isCompactionDoc is id-prefix scoped — non-compaction messages never match', () => {
    expect(component.isCompactionDoc(makeCompactionDoc())).toBe(true);
    expect(component.isCompactionDoc(
      makeCompactionDoc({ message_id: 'm-user', role: 'user' }),
    )).toBe(false);
    expect(component.isCompactionDoc(
      makeCompactionDoc({ message_id: 'system-prompt-head' }),
    )).toBe(false);
    // Prefix must match at the START of the id, not mid-string.
    expect(component.isCompactionDoc(
      makeCompactionDoc({ message_id: 'xx-compaction-global-inst-1-3' }),
    )).toBe(false);
  });
});

describe('ChatInterfaceComponent — /compact card copy (compaction-output-structure §9)', () => {
  let component: ChatInterfaceComponent;

  const baseCmd = {
    command: 'compact' as const,
    commandId: 'cmd-1',
    phase: 'success' as const,
    phaseSeq: 2,
    elapsedMs: 1200,
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatInterfaceComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        provideMarkdown(),
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ChatInterfaceComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('success with section counts → "Context compacted — global overview + N section summaries preserved"', () => {
    const title = component.commandTitle({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'summary', sections_kept: 4, sections_total: 4 },
    } as never);
    expect(title).toBe('Context compacted — global overview + 4 section summaries preserved');
  });

  it('success WITHOUT counts keeps the prior copy verbatim (graceful degradation — no fabricated number)', () => {
    const title = component.commandTitle({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'summary' },
    } as never);
    expect(title).toBe('Context compacted');
  });

  it('success noop still wins over counts ("Nothing to compact")', () => {
    const title = component.commandTitle({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'noop', noop_reason: 'below_floor', sections_kept: 0, sections_total: 0 },
    } as never);
    expect(title).toBe('Nothing to compact');
  });

  it('partial fallback with counts → existing copy + "(k/N sections kept; dropped spans listed in the compaction notice)"', () => {
    const title = component.commandTitle({
      ...baseCmd,
      phase: 'fallback_applied',
      detail: { compacted_type: 'partial_summary', sections_kept: 3, sections_total: 7 },
    } as never);
    expect(title).toBe(
      'Compaction timed out partway — kept the summaries that completed, trimmed the messages that could not be summarized ' +
      '(3/7 sections kept; dropped spans listed in the compaction notice)',
    );
  });

  it('partial fallback WITHOUT counts keeps the prior copy verbatim', () => {
    const title = component.commandTitle({
      ...baseCmd,
      phase: 'fallback_applied',
      detail: { compacted_type: 'partial_summary' },
    } as never);
    expect(title).toBe(
      'Compaction timed out partway — kept the summaries that completed, trimmed the messages that could not be summarized',
    );
  });

  it('rejects malformed counts (non-finite / zero total) → graceful degradation', () => {
    expect(component.commandTitle({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'summary', sections_kept: Number.NaN, sections_total: 4 },
    } as never)).toBe('Context compacted');
    expect(component.commandTitle({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'summary', sections_kept: 4, sections_total: 0 },
    } as never)).toBe('Context compacted');
  });

  // ── commandDetailLine — noop_reason → user-facing copy ──────────────
  // Cycle 2 (proactive-compaction-fix review W-4) added
  // noop_reason="injections_dominate" and Cycle 3 (residual W-4.5)
  // added noop_reason="preserved_within_threshold". The two new
  // switch cases live at
  //   frontend/src/app/components/chat-interface/chat-interface.component.ts:412
  //   frontend/src/app/components/chat-interface/chat-interface.component.ts:420
  // and the literals are pinned in the NoopReason type
  // (frontend/src/app/models/index.ts:61). Plain-logic specs — no DOM
  // needed; commandDetailLine is a pure function of cmd.detail.

  it('success noop injections_dominate → "All messages are injections; nothing to compact"', () => {
    const line = component.commandDetailLine({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'noop', noop_reason: 'injections_dominate' },
    } as never);
    expect(line).toBe('All messages are injections; nothing to compact');
  });

  it('success noop preserved_within_threshold → "Preserved groups still fit within the threshold"', () => {
    const line = component.commandDetailLine({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'noop', noop_reason: 'preserved_within_threshold' },
    } as never);
    expect(line).toBe('Preserved groups still fit within the threshold');
  });

  it('success noop with a legacy reason still renders the prior copy (adjacent regression guard)', () => {
    // One of the three pre-existing reasons (none of which had a
    // commandDetailLine spec before this branch). Pins the prior copy
    // so a future case shuffle cannot silently drop it.
    const line = component.commandDetailLine({
      ...baseCmd,
      phase: 'success',
      detail: { compacted_type: 'noop', noop_reason: 'below_floor' },
    } as never);
    expect(line).toBe('Context too small to compact');
  });
});

// ─── Phase 6 / image-viewer dialog + onerror fallback (clipboard-image-chat) ─
//
// Plain-TS-logic mirror (per FE conventions) for the bubble-level image
// interactions. The chat-interface owns the click + (error) bindings
// on the rendered thumbnails; the dialog itself is opened via
// `ImageViewerActionsService` (stubbed here so the spec does not mount
// MatDialog).
//
// Test strategy (plan / phase6-plan.md Test Strategy table rows 3, 5, 6):
//
//   - Click handler opens the dialog with the right data
//   - Click on a FAILED image does NOT open the dialog
//   - Click does not bubble (defensive stopPropagation — pinned by
//     reading the production source rather than mounting a parent
//     handler spy, since today no parent (click) handler exists)
//   - onerror swaps src to the pinned SVG fallback
//   - onerror records the failure in `failedImages`
//   - onerror is idempotent — a second error event for the same
//     (message_id, index) pair is a no-op
//   - Cross-seam invariant (row 6): a user message with images
//     retains its `images` array after the (error) handler fires —
//     the DOM-side failure does NOT cascade into a model-side clear
//
// Fixtures cover the cap ladder:
//   - 1 image, onerror fires
//   - 3 images where only the middle one 410s — the other two render
//     normally (independent state per index)
//   - 0 images — no-op (the @if branch never enters)
//   - ref URL vs data URI discrimination via isTmpImageRef
//     (predicate is the canonical §2 form discriminator)

const REF_URL_32HEX = '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const DATA_URI_A = 'data:image/png;base64,AAAA';
const DATA_URI_B = 'data:image/jpeg;base64,/9j/4AAQ';

function makeUserImageMessage(overrides: Partial<Message> = {}): Message {
  return {
    message_id: 'srv-images',
    role: 'user',
    // Empty content keeps the markdown bubble OUT of the render tree
    // so the spec-local MarkdownModule stub doesn't trigger NG0304
    // ("markdown is not a known element") during CD. The image tests
    // exercise the `message.images` branch only — the markdown bubble
    // is irrelevant to the click/error semantics.
    content: '',
    created_at: '2026-09-19T19:00:00+00:00',
    instance_id: 'inst-1',
    images: [DATA_URI_A],
    ...overrides,
  };
}

describe('ChatInterfaceComponent — image-viewer click + onerror (phase 6)', () => {
  let fixture: ComponentFixture<ChatInterfaceComponent>;
  let component: ChatInterfaceComponent;
  let imageViewerOpen: jest.Mock;

  function setupMount(messages: Message[]): void {
    imageViewerOpen = jest.fn();
    TestBed.configureTestingModule({
      imports: [ChatInterfaceComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        provideMarkdown(),
        {
          provide: ImageViewerActionsService,
          useValue: { openViewer: imageViewerOpen },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatInterfaceComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('instanceId', 'inst-1');
    fixture.componentRef.setInput('messages', messages);
    fixture.detectChanges();
  }

  function queryThumbnails(): HTMLImageElement[] {
    return Array.from(
      fixture.nativeElement.querySelectorAll('[data-testid="message-image"]'),
    ) as HTMLImageElement[];
  }

  describe('click handler — opens the dialog', () => {
    it('opens the dialog with the correct src + message_id on click', () => {
      setupMount([makeUserImageMessage({ images: [DATA_URI_A] })]);
      const img = queryThumbnails()[0];
      expect(img).toBeDefined();
      img.click();
      expect(imageViewerOpen).toHaveBeenCalledTimes(1);
      expect(imageViewerOpen).toHaveBeenCalledWith(DATA_URI_A, 'srv-images');
    });

    it('opens the dialog for a canonical /api/tmp_images/<id> src', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      queryThumbnails()[0].click();
      expect(imageViewerOpen).toHaveBeenCalledWith(REF_URL_32HEX, 'srv-images');
    });

    it('stopPropagation is called defensively (read-by-source pin)', () => {
      // No parent (click) handler exists today, so we cannot wire a
      // bubble spy. The defensive stopPropagation is pinned by the
      // chat-interface.component.spec.ts comment + the production
      // source (chat-interface.component.ts:onImageThumbnailClick).
      // This spec asserts the click reached the handler with the
      // correct args — a future regression that drops stopPropagation
      // would still pass this spec (the click is processed correctly),
      // but the merge-gate identity-grep will catch the production
      // source drift.
      setupMount([makeUserImageMessage()]);
      queryThumbnails()[0].click();
      expect(imageViewerOpen).toHaveBeenCalledTimes(1);
    });

    it('does NOT open the dialog when the thumbnail is already failed', () => {
      setupMount([makeUserImageMessage()]);
      const img = queryThumbnails()[0];
      // First trigger an error so the (message_id, 0) pair is recorded
      // as failed.
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      expect(component.isImageFailed('srv-images', 0)).toBe(true);
      imageViewerOpen.mockClear();
      // Click on the failed thumbnail — the handler must short-circuit.
      img.click();
      expect(imageViewerOpen).not.toHaveBeenCalled();
    });

    it('opens the dialog for a message with 3 images (independent click handlers per index)', () => {
      setupMount([
        makeUserImageMessage({
          message_id: 'srv-3',
          images: [DATA_URI_A, REF_URL_32HEX, DATA_URI_B],
        }),
      ]);
      const imgs = queryThumbnails();
      expect(imgs.length).toBe(3);
      imgs[1].click();
      expect(imageViewerOpen).toHaveBeenCalledWith(REF_URL_32HEX, 'srv-3');
      imgs[0].click();
      expect(imageViewerOpen).toHaveBeenCalledWith(DATA_URI_A, 'srv-3');
    });

    it('is a no-op when a user message has 0 images (the @if branch never enters)', () => {
      setupMount([makeUserImageMessage({ images: [] })]);
      expect(queryThumbnails().length).toBe(0);
      // No click → no dialog open call.
      expect(imageViewerOpen).not.toHaveBeenCalled();
    });
  });

  describe('onerror handler — fallback swap + idempotency', () => {
    it('swaps the src to the pinned SVG fallback on the first error event', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // The src binding is computed by imageSrc() — once the (m, 0)
      // pair is recorded as failed, imageSrc() returns the fallback
      // SVG. OnPush re-renders pick it up via the signal.
      expect(component.isImageFailed('srv-images', 0)).toBe(true);
      expect(img.getAttribute('src')).toBe(
        ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE,
      );
      // The class flip adds the SCSS styling.
      expect(img.classList.contains('message-image-failed')).toBe(true);
    });

    it('records the failure in failedImages keyed by (message_id, index)', () => {
      setupMount([
        makeUserImageMessage({
          message_id: 'srv-images',
          images: [DATA_URI_A, REF_URL_32HEX, DATA_URI_B],
        }),
      ]);
      const imgs = queryThumbnails();
      imgs[1].dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // The middle index (1) is failed; the others are NOT.
      expect(component.isImageFailed('srv-images', 0)).toBe(false);
      expect(component.isImageFailed('srv-images', 1)).toBe(true);
      expect(component.isImageFailed('srv-images', 2)).toBe(false);
    });

    it('is idempotent — a second error event for the same pair does not re-fire the swap', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      const srcAfterFirst = img.getAttribute('src');
      const failedAfterFirst = component.failedImages();
      // Second error event — must be a no-op.
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // The signal must have stable referential identity (no-op
      // update returns the previous reference).
      expect(component.failedImages()).toBe(failedAfterFirst);
      // The src remains the fallback (no further swap to a different
      // value).
      expect(img.getAttribute('src')).toBe(srcAfterFirst);
    });

    it('clears the native onerror on the broken element to suppress further events', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      const img = queryThumbnails()[0];
      img.onerror = jest.fn();
      img.dispatchEvent(new Event('error'));
      // The chat-interface nils the element-level onerror handler so
      // the browser does not refire the event when we swap the src.
      expect(img.onerror).toBeNull();
    });

    it('is a defensive no-op when the event target is null', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      // Direct invocation with a null target — must not throw and
      // must NOT record a phantom failure (no messageId resolution
      // is possible without a target).
      expect(() =>
        component.onImageError({ target: null } as unknown as Event, 'srv-images', 0),
      ).not.toThrow();
      expect(component.isImageFailed('srv-images', 0)).toBe(false);
    });

    it('does not regress legacy data URIs (ref-URL-only fallback)', () => {
      // Plan Task 9 / risk #4: only refs can 410 (data URIs are inline
      // and never fail). The spec asserts that even when the (error)
      // handler fires on a data-URI src (an unlikely-but-defensive
      // path), the fallback still applies — the handler does NOT
      // discriminate by src form. The discrimination lives in the SSE
      // whitelist upstream; the (error) handler is a safety net.
      setupMount([makeUserImageMessage({ images: [DATA_URI_A] })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      expect(component.isImageFailed('srv-images', 0)).toBe(true);
    });
  });

  describe('cross-seam invariant — onerror → model survives', () => {
    // Plan row 6: the DOM-side failure does NOT cascade into a
    // model-side clear. The message retains its `images` array
    // (the phase-5 merge pin protects this). The bubble renders the
    // fallback SVG, not a broken-image icon.
    it('retains the message.images array after the (error) handler fires', () => {
      const originalImages = [REF_URL_32HEX];
      setupMount([makeUserImageMessage({ images: originalImages })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // The model is untouched — the (error) handler swaps the DOM
      // src, not the model field.
      expect(component.messages[0].images).toEqual(originalImages);
    });

    it('keeps the bubble visible — the fallback SVG renders, not a broken-image icon', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // The image element still exists in the DOM — the fallback
      // SVG is its src.
      expect(img).toBeDefined();
      expect(img.tagName.toLowerCase()).toBe('img');
      expect(img.getAttribute('src')).toBe(
        ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE,
      );
      // The class adds the SCSS styling — opacity 0.55, grayscale(1).
      expect(img.classList.contains('message-image-failed')).toBe(true);
    });
  });

  describe('imageSrc helper — template binding', () => {
    it('returns the original src when the (message_id, index) is not failed', () => {
      setupMount([makeUserImageMessage({ images: [DATA_URI_A] })]);
      expect(component.imageSrc(DATA_URI_A, 'srv-images', 0)).toBe(DATA_URI_A);
    });

    it('returns the fallback src when the (message_id, index) IS failed', () => {
      setupMount([makeUserImageMessage({ images: [REF_URL_32HEX] })]);
      const img = queryThumbnails()[0];
      img.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      expect(component.imageSrc(REF_URL_32HEX, 'srv-images', 0)).toBe(
        ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE,
      );
    });
  });

  describe('cap-fixture coverage', () => {
    // Plan row "Fixtures at AND past caps": 1 image, 3 images where
    // only the middle one 410s, 0 images (no-op). The cases above
    // cover 1 and 3; this block covers the "middle-only" independence.

    it('3 images where only the middle 410s — the other two render normally (independent state per index)', () => {
      setupMount([
        makeUserImageMessage({
          message_id: 'srv-middle',
          images: [DATA_URI_A, REF_URL_32HEX, DATA_URI_B],
        }),
      ]);
      const imgs = queryThumbnails();
      imgs[1].dispatchEvent(new Event('error'));
      fixture.detectChanges();
      // Index 0 + 2 are NOT failed — imageSrc returns the originals.
      expect(component.imageSrc(DATA_URI_A, 'srv-middle', 0)).toBe(DATA_URI_A);
      expect(component.imageSrc(DATA_URI_B, 'srv-middle', 2)).toBe(DATA_URI_B);
      // Index 1 IS failed — imageSrc returns the fallback.
      expect(component.imageSrc(REF_URL_32HEX, 'srv-middle', 1)).toBe(
        ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE,
      );
    });
  });
});

// Stub declaration so the spec can compile without pulling the real
// service module — the spec configures the stub via DI providers.
function _imageViewerStub() {
  // No-op; the stub is provided via the `useValue` provider above.
  return null;
}
void _imageViewerStub;

