import { signal } from '@angular/core';

// ---- Shared mocks (same style as instance-delete-dialog + chat specs) ----

/**
 * Mock InstancesViewStateService — mirrors the subset of the real
 * service the document-level handlers read: `detailVisible`. The W6
 * gate bails on the handlers when the detail overlay is hidden, so
 * tests flip this signal to drive both gate branches.
 */
class MockInstancesViewStateService {
  readonly detailVisible = signal(false);
}

// ===========================================================================
// TestableTodoListComponent
//
// Mirrors the document-level handler slice of the real
// `TodoListComponent` so we can drive it in isolation without spinning
// up Angular's TestBed (templateUrl/CDK overlay plumbing stays out of
// the picture). The handler logic is identical to the production
// source; if the real component changes, this mirror must be updated
// too.
// ===========================================================================
class TestableTodoListComponent {
  // Popup state mirrored from the production component. The handlers
  // below close these via closeAllPopups(), which is what the tests
  // observe (a gated handler must leave every popup signal untouched).
  readonly commentPopupNodeId = signal<string | null>(null);
  readonly subtaskPopupNodeId = signal<string | null>(null);
  readonly editingNodeId = signal<string | null>(null);
  readonly editingComment = signal<string>('');
  readonly newSubtaskText = signal<string>('');
  readonly expandedSubtaskNodeId = signal<string | null>(null);
  readonly subtaskError = signal<string | null>(null);

  /** Mirrors the production `inject(InstancesViewStateService)`. */
  readonly viewState = new MockInstancesViewStateService();

  /** Mirrors the production closeAllPopups (full popup teardown). */
  closeAllPopups(): void {
    this.commentPopupNodeId.set(null);
    this.subtaskPopupNodeId.set(null);
    this.editingNodeId.set(null);
    this.editingComment.set('');
    this.newSubtaskText.set('');
    this.expandedSubtaskNodeId.set(null);
    this.subtaskError.set(null);
  }

  /** Mirrors the production @HostListener('document:click') handler. */
  onDocumentClick(_event: MouseEvent): void {
    // W6: the component stays alive (and its document-level listeners
    // stay registered) while the detail overlay is hidden, so bail
    // before touching popup state.
    if (!this.viewState.detailVisible()) return;
    if (this.commentPopupNodeId() || this.subtaskPopupNodeId()) {
      this.closeAllPopups();
    }
  }

  /** Mirrors the production @HostListener('document:keydown.escape') handler. */
  onEscape(event: Event): void {
    // W6: same gate as onDocumentClick.
    if (!this.viewState.detailVisible()) return;
    if (this.commentPopupNodeId() || this.subtaskPopupNodeId()) {
      event.preventDefault();
      this.closeAllPopups();
    }
  }
}

/** A minimal stand-in for a DOM event recording preventDefault calls. */
function createEvent(): Event & { defaultPrevented: boolean } {
  return {
    defaultPrevented: false,
    preventDefault(): void {
      (this as { defaultPrevented: boolean }).defaultPrevented = true;
    },
  } as Event & { defaultPrevented: boolean };
}

/** Opens a popup scenario on the component (comment popup + editor state). */
function openPopupState(component: TestableTodoListComponent): void {
  component.commentPopupNodeId.set('node-1');
  component.subtaskPopupNodeId.set('node-2');
  component.editingNodeId.set('node-1');
  component.editingComment.set('some draft');
  component.newSubtaskText.set('pending sub-task');
  component.expandedSubtaskNodeId.set('node-3');
  component.subtaskError.set('stale error');
}

// ===========================================================================
// Tests
// ===========================================================================
describe('TodoListComponent — W6 detail-visibility gate on document handlers', () => {
  let component: TestableTodoListComponent;

  beforeEach(() => {
    component = new TestableTodoListComponent();
  });

  describe('onDocumentClick', () => {
    it('is a no-op while detailVisible=false: popup state stays unchanged', () => {
      openPopupState(component);
      component.viewState.detailVisible.set(false);

      component.onDocumentClick({} as MouseEvent);

      // Popup state must be untouched — the invisible overlay's popup
      // survives an outside click on the underlying list page.
      expect(component.commentPopupNodeId()).toBe('node-1');
      expect(component.subtaskPopupNodeId()).toBe('node-2');
      expect(component.editingNodeId()).toBe('node-1');
      expect(component.editingComment()).toBe('some draft');
      expect(component.newSubtaskText()).toBe('pending sub-task');
      expect(component.expandedSubtaskNodeId()).toBe('node-3');
      expect(component.subtaskError()).toBe('stale error');
    });

    it('closes popups normally when detailVisible=true', () => {
      openPopupState(component);
      component.viewState.detailVisible.set(true);

      component.onDocumentClick({} as MouseEvent);

      expect(component.commentPopupNodeId()).toBeNull();
      expect(component.subtaskPopupNodeId()).toBeNull();
      expect(component.editingNodeId()).toBeNull();
      expect(component.editingComment()).toBe('');
      expect(component.newSubtaskText()).toBe('');
      expect(component.expandedSubtaskNodeId()).toBeNull();
      expect(component.subtaskError()).toBeNull();
    });
  });

  describe('onEscape', () => {
    it('is a no-op while detailVisible=false: popup state stays unchanged and the event is NOT consumed', () => {
      openPopupState(component);
      component.viewState.detailVisible.set(false);
      const event = createEvent();

      component.onEscape(event);

      // Popup state untouched AND the underlying page still receives
      // the Escape (no preventDefault steal from an invisible overlay).
      expect(component.commentPopupNodeId()).toBe('node-1');
      expect(component.subtaskPopupNodeId()).toBe('node-2');
      expect(component.editingNodeId()).toBe('node-1');
      expect(component.editingComment()).toBe('some draft');
      expect(component.newSubtaskText()).toBe('pending sub-task');
      expect(component.expandedSubtaskNodeId()).toBe('node-3');
      expect(component.subtaskError()).toBe('stale error');
      expect(event.defaultPrevented).toBe(false);
    });

    it('closes popups and consumes the event when detailVisible=true', () => {
      openPopupState(component);
      component.viewState.detailVisible.set(true);
      const event = createEvent();

      component.onEscape(event);

      expect(component.commentPopupNodeId()).toBeNull();
      expect(component.subtaskPopupNodeId()).toBeNull();
      expect(component.editingNodeId()).toBeNull();
      expect(event.defaultPrevented).toBe(true);
    });

    it('leaves state alone when visible but no popup is open', () => {
      component.viewState.detailVisible.set(true);
      const event = createEvent();

      component.onEscape(event);

      expect(component.commentPopupNodeId()).toBeNull();
      // No popup open → handler must not consume the Escape key.
      expect(event.defaultPrevented).toBe(false);
    });
  });
});
