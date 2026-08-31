import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { CommandRegistryService } from '../../services/command-registry.service';
import type { MessagePayload } from './message-input.component';
import { MessageInputComponent } from './message-input.component';

/**
 * Component-level specs for the slash-command autocomplete palette
 * (phase2-plan.md Task 10): template rendering, ARIA combobox wiring and
 * the keyboard/mouse handlers. The palette *logic* (trigger rule,
 * filtering, wrap-around) is logic-mirror tested without TestBed in
 * ``slash-command-palette.util.spec.ts`` — house style.
 *
 * TestBed is used here because these assertions need the RENDERED
 * template (role/aria attributes, focus, event bindings).
 */
describe('MessageInputComponent — slash-command autocomplete palette (Task 10)', () => {
  let fixture: ComponentFixture<MessageInputComponent>;
  let component: MessageInputComponent;
  let registry: CommandRegistryService;
  let textarea: HTMLTextAreaElement;
  let sent: MessagePayload[];
  let resumes: string[];

  const palette = (): HTMLElement | null =>
    fixture.nativeElement.querySelector('[data-testid="slash-command-palette"]');
  const options = (): NodeListOf<HTMLElement> =>
    fixture.nativeElement.querySelectorAll('[role="option"]');
  const liveRegion = (): HTMLElement | null => fixture.nativeElement.querySelector('.sr-only');
  const highlighted = (): HTMLElement | null =>
    fixture.nativeElement.querySelector('.slash-command-option.highlighted');

  function type(text: string): void {
    textarea.value = text;
    textarea.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  function press(key: string, init: KeyboardEventInit = {}): void {
    textarea.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }),
    );
    fixture.detectChanges();
  }

  beforeEach(async () => {
    sent = [];
    resumes = [];
    await TestBed.configureTestingModule({
      imports: [MessageInputComponent],
      providers: [
        // The constructor effect only calls getQueues for a non-null
        // projectId (never set here); the stub keeps inject() resolvable.
        { provide: ApiService, useValue: { getQueues: () => of({ queues: [] }) } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MessageInputComponent);
    component = fixture.componentInstance;
    registry = TestBed.inject(CommandRegistryService);
    component.sendMessage.subscribe(p => sent.push(p));
    component.resumeInstance.subscribe(t => resumes.push(t));
    fixture.detectChanges();
    textarea = fixture.nativeElement.querySelector('textarea.input-textarea');
  });

  afterEach(() => {
    fixture.destroy();
  });

  describe('trigger rules', () => {
    it("bare '/' opens the palette and lists the seeded /compact command", () => {
      type('/');
      expect(palette()).not.toBeNull();
      expect(options().length).toBe(1);
      expect(options()[0].textContent).toContain('/compact');
      expect(options()[0].textContent).toContain('Compact this instance\'s message history');
    });

    it("'//compact' (escape form) NEVER opens the palette", () => {
      type('//compact');
      expect(palette()).toBeNull();
      expect(textarea.getAttribute('aria-expanded')).toBe('false');
    });

    it('whitespace closes the palette (no longer a bare command)', () => {
      type('/compact');
      expect(palette()).not.toBeNull();
      type('/compact ');
      expect(palette()).toBeNull();
      expect(textarea.getAttribute('aria-expanded')).toBe('false');
    });

    it('non-slash text never opens the palette', () => {
      type('hello world');
      expect(palette()).toBeNull();
    });

    it('palette closes once the input is cleared', () => {
      type('/');
      expect(palette()).not.toBeNull();
      type('');
      expect(palette()).toBeNull();
    });
  });

  describe('filter-as-you-type', () => {
    beforeEach(() => {
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
    });

    it("bare '/' shows ALL commands", () => {
      type('/');
      expect(options().length).toBe(2);
    });

    it("'/c' narrows to both; '/cl' narrows to clear", () => {
      type('/c');
      expect(options().length).toBe(2);
      type('/cl');
      expect(options().length).toBe(1);
      expect(options()[0].textContent).toContain('/clear');
    });

    it("'/CO' matches case-insensitively", () => {
      type('/CO');
      expect(options().length).toBe(1);
      expect(options()[0].textContent).toContain('/compact');
    });

    it("zero matches keeps the palette open with the 'No matching command' hint", () => {
      type('/zz');
      expect(palette()).not.toBeNull();
      expect(options().length).toBe(0);
      expect(palette()?.textContent).toContain('No matching command');
      expect(liveRegion()?.textContent).toContain('No matching command');
    });
  });

  describe('a11y wiring (ARIA combobox pattern)', () => {
    it('exposes aria-expanded, aria-controls, listbox + option roles and ids', () => {
      type('/');
      expect(textarea.getAttribute('role')).toBe('combobox');
      expect(textarea.getAttribute('aria-expanded')).toBe('true');
      expect(textarea.getAttribute('aria-controls')).toBe('slash-command-listbox');
      expect(textarea.getAttribute('aria-haspopup')).toBe('listbox');

      const listbox = fixture.nativeElement.querySelector('[role="listbox"]');
      expect(listbox).not.toBeNull();
      expect(listbox.getAttribute('id')).toBe('slash-command-listbox');

      const option = options()[0];
      expect(option.getAttribute('role')).toBe('option');
      expect(option.getAttribute('id')).toBe('slash-command-option-0');
      expect(option.getAttribute('aria-selected')).toBe('true');
    });

    it('aria-activedescendant tracks the highlighted option', () => {
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
      type('/');
      expect(textarea.getAttribute('aria-activedescendant')).toBe('slash-command-option-0');
      press('ArrowDown');
      expect(textarea.getAttribute('aria-activedescendant')).toBe('slash-command-option-1');
      expect(highlighted()?.getAttribute('id')).toBe('slash-command-option-1');
    });

    it('announces open/match-count changes in the polite live region', () => {
      expect(liveRegion()?.textContent).toBe('');
      type('/');
      expect(liveRegion()?.textContent).toContain('1 command available');
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
      type('/');
      expect(liveRegion()?.textContent).toContain('2 commands available');
      type('hello');
      expect(liveRegion()?.textContent).toBe('');
    });
  });

  describe('keyboard navigation', () => {
    beforeEach(() => {
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
    });

    it('ArrowDown moves the highlight and wraps last → first', () => {
      type('/');
      press('ArrowDown');
      expect(highlighted()?.textContent).toContain('/clear');
      press('ArrowDown');
      expect(highlighted()?.textContent).toContain('/compact'); // wrapped
    });

    it('ArrowUp moves backward and wraps first → last', () => {
      type('/');
      press('ArrowUp');
      expect(highlighted()?.textContent).toContain('/clear'); // wrapped
      press('ArrowUp');
      expect(highlighted()?.textContent).toContain('/compact');
    });

    it('Escape dismisses; the next input event re-arms the palette', () => {
      type('/');
      press('Escape');
      expect(palette()).toBeNull();
      // No new input yet → still dismissed.
      expect(textarea.getAttribute('aria-expanded')).toBe('false');
      type('/c');
      expect(palette()).not.toBeNull();
    });

    it('Tab accepts: inserts the command + trailing space WITHOUT sending', () => {
      type('/c');
      press('Tab');
      expect(textarea.value).toBe('/compact ');
      expect(sent).toHaveLength(0);
      expect(palette()).toBeNull(); // trailing space ends the bare-command trigger
      expect(document.activeElement).toBe(textarea);
    });

    it('Shift+Tab never accepts (keeps normal focus traversal)', () => {
      type('/c');
      press('Tab', { shiftKey: true });
      expect(textarea.value).toBe('/c');
      expect(palette()).not.toBeNull();
    });
  });

  describe('Enter — accept-then-send vs non-regression', () => {
    it('palette-Enter accepts the highlighted command AND sends it', () => {
      type('/co');
      press('Enter');
      // Complete-then-send: inserted text was '/compact ' and the normal
      // send path dispatched the trimmed command (Task 5 flow).
      expect(sent).toHaveLength(1);
      expect(sent[0].content).toBe('/compact');
      expect(palette()).toBeNull();
    });

    it('palette-Enter with a fully-typed command still sends the command', () => {
      type('/compact');
      press('Enter');
      expect(sent).toHaveLength(1);
      expect(sent[0].content).toBe('/compact');
    });

    it('palette-Enter with the input ALREADY the complete command sends it VERBATIM (no rewrite)', () => {
      // Byte-identical non-regression with the pre-palette flow: a
      // rejected duplicate must keep '/compact' in the input (e2e SC5 /
      // SC14 assert the exact value), not the inserted '/compact ' form.
      type('/compact');
      press('Enter');
      expect(textarea.value).toBe('/compact');
      expect(sent).toHaveLength(1);
      expect(sent[0].content).toBe('/compact');
    });

    it('Enter with zero palette matches falls through to the normal send (unknown command)', () => {
      type('/foo');
      expect(palette()).not.toBeNull(); // hint shown, but no options
      press('Enter');
      expect(sent).toHaveLength(1);
      expect(sent[0].content).toBe('/foo'); // chat component validates it (Task 5)
    });

    it('Enter with the palette closed sends normally', () => {
      type('hello world');
      press('Enter');
      expect(sent).toHaveLength(1);
      expect(sent[0].content).toBe('hello world');
    });

    it('Shift+Enter still makes a newline (never sends, never accepts)', () => {
      type('hello');
      press('Enter', { shiftKey: true });
      expect(sent).toHaveLength(0);
      expect(textarea.value).toBe('hello');
    });

    it('paused instance: palette-Enter routes through handleResume (dispatch parity)', () => {
      fixture.componentRef.setInput('instanceStatus', 'paused');
      fixture.detectChanges();
      type('/compact');
      press('Enter');
      expect(resumes).toEqual(['/compact']);
      expect(sent).toHaveLength(0);
    });

    it('paused instance with the palette closed: Enter still resumes with the text (unchanged)', () => {
      fixture.componentRef.setInput('instanceStatus', 'paused');
      fixture.detectChanges();
      type('continue with this');
      press('Enter');
      expect(resumes).toEqual(['continue with this']);
      expect(sent).toHaveLength(0);
    });
  });

  describe('mouse interaction — never steals focus', () => {
    it('click accepts (insert only, no send) and focus stays in the textarea', () => {
      type('/');
      const option = options()[0];
      // mousedown preventDefault keeps the textarea focused (no blur).
      option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
      option.click();
      fixture.detectChanges();
      expect(textarea.value).toBe('/compact ');
      expect(sent).toHaveLength(0);
      expect(document.activeElement).toBe(textarea);
      expect(palette()).toBeNull();
    });

    it('hover moves the highlight', () => {
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
      type('/');
      options()[1].dispatchEvent(new MouseEvent('mouseenter'));
      fixture.detectChanges();
      expect(textarea.getAttribute('aria-activedescendant')).toBe('slash-command-option-1');
      expect(highlighted()?.textContent).toContain('/clear');
    });
  });
});
