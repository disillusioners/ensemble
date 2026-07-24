import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Transaction } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { CodemirrorDirective } from './codemirror.directive';

@Component({
  standalone: true,
  imports: [CodemirrorDirective],
  template: `
    <div
      [appCodemirror]=""
      [content]="content"
      [language]="language"
      (contentChange)="onContentChange($event)"
    ></div>
  `,
})
class HostComponent {
  content = 'print("hello")';
  language: string | null | undefined = 'python';
  emitCount = 0;
  lastEmittedContent: string | null = null;

  onContentChange(value: string): void {
    this.lastEmittedContent = value;
    this.emitCount++;
  }
}

describe('CodemirrorDirective', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  function editor(): HTMLElement | null {
    return fixture.nativeElement.querySelector('.cm-editor') as HTMLElement | null;
  }

  function contentElement(): HTMLElement | null {
    return fixture.nativeElement.querySelector('.cm-content') as HTMLElement | null;
  }

  it('should create a CodeMirror editor on initialization', () => {
    expect(editor()).not.toBeNull();
  });

  it('should render the input content inside .cm-content', () => {
    expect(contentElement()?.textContent).toContain('print("hello")');
  });

  it('should render in read-only mode', () => {
    expect(contentElement()?.getAttribute('contenteditable')).toBe('false');
  });

  it('should re-render when content changes', () => {
    host.content = 'const answer = 42;';
    fixture.detectChanges();

    expect(contentElement()?.textContent).toContain('const answer = 42;');
    expect(contentElement()?.textContent).not.toContain('print("hello")');
  });

  it('should swap the language extension from Python to JavaScript', () => {
    // jsdom doesn't run CodeMirror's HighlightStyle measurement pipeline the
    // way a real browser does, so we can't reliably assert on generated
    // `.tok-*` classes here. Instead, verify the swap is structurally sound:
    // editor survives, content updates, and read-only mode is preserved.
    expect(editor()).not.toBeNull();
    expect(contentElement()?.textContent).toContain('print("hello")');

    host.content = 'const answer = 42;';
    host.language = 'javascript';
    fixture.detectChanges();

    expect(editor()).not.toBeNull();
    expect(contentElement()?.textContent).toContain('const answer = 42;');
    expect(contentElement()?.textContent).not.toContain('print("hello")');
    expect(contentElement()?.getAttribute('contenteditable')).toBe('false');
  });

  it('should remain read-only after a language change', () => {
    host.language = 'javascript';
    fixture.detectChanges();

    expect(contentElement()?.getAttribute('contenteditable')).toBe('false');
  });

  it('should destroy and remove the EditorView with the host fixture', () => {
    // W13: prefer spying on EditorView.prototype.destroy over DOM
    // presence checks. The DOM tree can detach for unrelated reasons
    // (zone teardown ordering, parent cleanup race) and yield a
    // false negative. The destroy call is the actual contract.
    const destroySpy = jest.spyOn(EditorView.prototype, 'destroy');

    fixture.destroy();

    expect(destroySpy).toHaveBeenCalled();
    destroySpy.mockRestore();
  });

  it('should handle an undefined language with plain-text rendering', () => {
    host.content = 'plain text only';
    host.language = undefined;
    fixture.detectChanges();

    expect(contentElement()?.textContent).toContain('plain text only');
    // No assertions on highlighting tokens — see the comment in the
    // language-swap test for why jsdom can't reliably render them.
    expect(contentElement()?.getAttribute('contenteditable')).toBe('false');
  });

  // ── contentChange filtering: F1/F2 same-file clobber guard ─────
  //
  // The directive's `contentChange` output only fires for user-initiated
  // edits (`t.isUserEvent('input')`). Programmatic dispatches — the
  // `[content]` sync in `ngOnChanges`, the language swap, and any
  // external `view.dispatch(...)` — must NOT emit. This is the primary
  // mechanism preventing F1/F2 clobbering of unsaved edits when the
  // same file is reloaded via SSE.

  function editorView(): EditorView | null {
    const el = fixture.nativeElement.querySelector('.cm-editor') as HTMLElement | null;
    return el ? EditorView.findFromDOM(el) : null;
  }

  it('should NOT emit contentChange for a programmatic doc change', () => {
    const view = editorView();
    expect(view).not.toBeNull();

    host.emitCount = 0;
    host.lastEmittedContent = null;

    // Dispatch WITHOUT an `userEvent` annotation — simulates the
    // `[content]` sync inside `ngOnChanges`. The doc does change
    // (`docChanged === true`), but no transaction carries the
    // `input` user-event annotation, so the output must not fire.
    view!.dispatch({
      changes: { from: 0, to: view!.state.doc.length, insert: 'programmatic' },
    });

    expect(host.emitCount).toBe(0);
    expect(host.lastEmittedContent).toBeNull();
  });

  it('should emit contentChange for a user-input doc change', () => {
    const view = editorView();
    expect(view).not.toBeNull();

    host.emitCount = 0;
    host.lastEmittedContent = null;

    // Dispatch WITH the `Transaction.userEvent.of('input')` annotation
    // — this is what CodeMirror 6 attaches to typing/paste/drop/IME
    // composition transactions from real user inputs. The directive's
    // `isUserEvent('input')` filter must let this through.
    view!.dispatch({
      changes: { from: 0, to: view!.state.doc.length, insert: 'user typed' },
      annotations: [Transaction.userEvent.of('input')],
    });

    expect(host.emitCount).toBe(1);
    expect(host.lastEmittedContent).toBe('user typed');
  });
});
