import {
  Directive,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
} from '@angular/core';
import { EditorState, Compartment, Extension } from '@codemirror/state';
import { EditorView, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from '@codemirror/view';
import { oneDark } from '@codemirror/theme-one-dark';

// Synchronous imports — bundled at build time (v1 approach).
// Total size of all lang-* packages ≈ 800KB minified, well within budget.
import { python } from '@codemirror/lang-python';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { sql } from '@codemirror/lang-sql';
import { yaml } from '@codemirror/lang-yaml';

/**
 * Thin Angular directive wrapping CodeMirror 6 EditorView in read-only mode.
 *
 * Why a directive, not ngx-codemirror:
 * - CM6 is framework-agnostic by design
 * - Direct EditorView access gives full control over extensions
 * - No third-party wrapper dependency to maintain
 */
@Directive({
  selector: '[appCodemirror]',
  standalone: true,
})
export class CodemirrorDirective implements OnChanges, OnDestroy {
  @Input() appCodemirror: string | undefined = '';
  @Input() content = '';
  @Input() language: string | null = null;
  @Input() editable: boolean = false;
  @Output() contentChange = new EventEmitter<string>();

  private view: EditorView | null = null;
  private readonly langCompartment = new Compartment();
  private readonly editableCompartment = new Compartment();

  constructor(private readonly el: ElementRef<HTMLElement>) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.view) {
      this.initView();
    }
    if (changes['content'] && this.view) {
      // Guard: skip the dispatch when the incoming content equals the
      // current document. This prevents cursor jumps when the binding
      // round-trips the user's own input back (editedContent → binding
      // → ngOnChanges), and also avoids clobbering restored edits on
      // tab switch-back where the signal was already set by the effect.
      if (this.content !== this.view.state.doc.toString()) {
        this.view.dispatch({
          changes: { from: 0, to: this.view.state.doc.length, insert: this.content }
        });
      }
    }
    if (changes['language'] && this.view) {
      this.view.dispatch({
        effects: this.langCompartment.reconfigure(this.getLangExtension())
      });
    }
    if (changes['editable'] && this.view) {
      this.view.dispatch({
        effects: this.editableCompartment.reconfigure([
          EditorView.editable.of(this.editable),
          EditorState.readOnly.of(!this.editable),
        ]),
      });
    }
  }

  private initView(): void {
    this.view = new EditorView({
      state: EditorState.create({
        doc: this.content,
        extensions: [
          this.editableCompartment.of([
            EditorView.editable.of(this.editable),
            EditorState.readOnly.of(!this.editable),
          ]),
          lineNumbers(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          oneDark,
          this.langCompartment.of(this.getLangExtension()),
          EditorView.lineWrapping,
          EditorView.updateListener.of((update) => {
            // Only surface USER-initiated edits via `contentChange`.
            // Programmatic dispatches (the `[content]` sync in
            // `ngOnChanges` below, and the language swap above) also
            // fire `docChanged` and would otherwise clobber the host
            // component's `editedContent` signal every time a file
            // reloads — defeating the savedBaseline gating that
            // preserves unsaved edits across same-file SSE reloads.
            //
            // `isUserEvent('input')` is true for any user-driven doc
            // change (typing, paste, drop, IME composition). It is
            // false for dispatches we issue programmatically because
            // those do not carry a userEvent annotation.
            if (update.docChanged && update.transactions.some((t) =>
              t.isUserEvent('input') || t.isUserEvent('undo') || t.isUserEvent('redo')
            )) {
              this.contentChange.emit(update.state.doc.toString());
            }
          }),
        ],
      }),
      parent: this.el.nativeElement,
    });
  }

  private getLangExtension(): Extension {
    // Synchronous language lookup — all packages imported at top of file.
    const langMap: Record<string, Extension> = {
      python: python(),
      typescript: javascript({ typescript: true }),
      javascript: javascript(),
      html: html(),
      css: css(),
      json: json(),
      markdown: markdown(),
      sql: sql(),
      yaml: yaml(),
    };
    if (this.language && langMap[this.language]) {
      return langMap[this.language];
    }
    return [];  // plain text — still gets line numbers + dark theme
  }

  ngOnDestroy(): void {
    this.view?.destroy();
  }
}
