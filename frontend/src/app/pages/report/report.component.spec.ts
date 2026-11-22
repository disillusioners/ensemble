import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ReportComponent, extractMarkdownFromWrapper } from './report.component';
import { ReportService } from './report.service';
import type { MaintenanceReportRun } from '../../models';

/**
 * ngx-markdown ships raw ESM (fesm2022 + marked.esm.js) that the jest
 * pipeline does not transform — importing the real package in ANY spec
 * would require a global transformIgnorePatterns change (blast radius
 * across all 60+ suites). Spec-local mock instead, following the repo
 * precedent of isolating heavy deps at the spec boundary
 * (`chat-interface.component.spec.ts` carries the same block).
 *
 * The component's real template keeps the original import; the spec
 * replaces it with an inert, decorated empty NG module so the
 * component's `imports: [MarkdownModule]` resolves. Safe here: the
 * stub exports NO `markdown` component, so the `<markdown>` node must
 * stay OUT of the render tree — the fixture never runs CD after a
 * selection lands on the HIT path (the stub would raise NG0304,
 * "markdown is not a known element"). The HIT path is therefore
 * asserted on the BOUND SIGNAL (`reportMarkdown()` — the exact value
 * the `[data]` binding receives), and the MISS path is asserted on
 * the signal AND on the rendered error paragraph (its branch never
 * renders the `<markdown>` node).
 */
jest.mock('ngx-markdown', () => {
  const core = require('@angular/core');
  return {
    MarkdownModule: core.NgModule({ imports: [] })(class MarkdownModuleStub {}),
    provideMarkdown: () => [],
  };
});

describe('ReportComponent', () => {
  let fixture: ComponentFixture<ReportComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ReportComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    fixture = TestBed.createComponent(ReportComponent);
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });
});

/**
 * The daemon's markdown WRAPPER page carries the raw markdown in the
 * stable `<article id="rendered"><pre>` element (the daemon's own
 * bootstrap reads that element's `textContent` —
 * `daemon/services/live_views.py:1850`).
 */
describe('extractMarkdownFromWrapper — the wrapper extraction seam', () => {
  const WRAPPER = (carrier: string): string =>
    `<article id="rendered"><pre>${carrier}</pre></article>`;

  it('treats a whitespace-only carrier as a MISS (fail-closed) — returns null', () => {
    expect(extractMarkdownFromWrapper(WRAPPER('   \n\t  '))).toBeNull();
  });

  it('treats an empty carrier as a MISS — returns null', () => {
    expect(extractMarkdownFromWrapper(WRAPPER(''))).toBeNull();
  });

  it('returns null when the wrapper carries no markdown element', () => {
    expect(extractMarkdownFromWrapper('<html><body>no carrier</body></html>')).toBeNull();
  });

  it('returns the carrier text UNCHANGED when it carries content (the HIT path)', () => {
    expect(extractMarkdownFromWrapper(WRAPPER('# Weekly report\n\nAll systems nominal.'))).toBe(
      '# Weekly report\n\nAll systems nominal.',
    );
  });

  it('decodes HTML entities in the carrier text (the daemon html-escape contract)', () => {
    expect(extractMarkdownFromWrapper(WRAPPER('a &amp; b &lt;tag&gt;'))).toBe('a & b <tag>');
  });
});

/**
 * G3 — the fail-closed MISS path, driven through the component's real
 * service against a mock transport (the house style — the precedent is
 * `frontend/src/app/pages/mcps-gateway/mcps-gateway.service.spec.ts`):
 * the production service + the production extraction run against the
 * real HTTP layer, so the URL, the verb and the response shape are
 * all exercised against the actual production code.
 */
describe('ReportComponent — the wrapper extraction drives the render posture', () => {
  let fixture: ComponentFixture<ReportComponent>;
  let httpMock: HttpTestingController;

  const RUN: MaintenanceReportRun = {
    run_id: 'run-1',
    trigger_type: 'scheduled',
    started_at: '2026-11-22T00:00:00.000Z',
    ended_at: '2026-11-22T00:05:00.000Z',
    overall_status: 'ok',
    report_view_url: '/views/maintenance-reports/run-1/report.md',
    receipt_view_url: '/views/maintenance-reports/run-1/receipt.md',
  };

  const WRAPPER = (carrier: string): string =>
    `<article id="rendered"><pre>${carrier}</pre></article>`;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ReportComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    fixture = TestBed.createComponent(ReportComponent);
    httpMock = TestBed.inject(HttpTestingController);
    // The component's constructor issues the run-list GET — flush it
    // so `verify()` below sees no outstanding request.
    httpMock.expectOne(ReportService.REPORTS_URL).flush([]);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('a whitespace-only wrapper drives the MISS path — fail-closed error posture, no markdown', () => {
    fixture.componentInstance.select(RUN);
    httpMock.expectOne(RUN.report_view_url).flush(WRAPPER('   \n\t  '));

    expect(fixture.componentInstance.reportMarkdown()).toBe('');
    expect(fixture.componentInstance.reportError()).toBe('Failed to load the report.');

    // The MISS posture renders the fail-closed error paragraph. CD is
    // safe on this branch: the error branch wins, so the `<markdown>`
    // node never enters the render tree.
    fixture.detectChanges();
    const errorText = fixture.nativeElement.querySelector('.report-error')?.textContent;
    expect(errorText).toContain('Failed to load the report.');
  });

  it('a non-empty wrapper drives the HIT path — the markdown is bound, no error', () => {
    fixture.componentInstance.select(RUN);
    httpMock.expectOne(RUN.report_view_url).flush(WRAPPER('# Weekly report\n\nAll systems nominal.'));

    expect(fixture.componentInstance.reportMarkdown()).toBe('# Weekly report\n\nAll systems nominal.');
    expect(fixture.componentInstance.reportError()).toBeNull();
    // NO detectChanges() here: on the HIT path the `<markdown>` node
    // enters the render tree and the spec-local ngx-markdown stub
    // exports no `markdown` component (NG0304 at CD). The bound value
    // is asserted on the signal — the exact value `[data]` receives.
  });
});
