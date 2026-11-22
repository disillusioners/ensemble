import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { ReportComponent } from './report.component';

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
 * component's `imports: [MarkdownModule]` resolves. Safe here: this
 * fixture never selects a run, so the `<markdown>` node stays OUT of
 * the render tree and the stub cannot trigger NG0304 ("markdown is
 * not a known element") during CD.
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
