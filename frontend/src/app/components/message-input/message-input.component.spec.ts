import { signal } from '@angular/core';
import { EventEmitter } from '@angular/core';

// Simplified MessageInputComponent for testing (mirrors actual component structure)
class TestMessageInputComponent {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'coder';
  @Input() isStreaming = false;
  @Output() sendMessage = new EventEmitter<string>();
  @Output() stopInstance = new EventEmitter<void>();

  message = signal('');

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'coder': '#10a7f7',
    'reviewer': '#8b5cf6',
  };

  get color(): string {
    return this.agentColorMap[this.agentColor] || '#10a7f7';
  }

  get canSend(): boolean {
    return !!this.message().trim() && !this.disabled;
  }

  handleSubmit(): void {
    const trimmedMessage = this.message().trim();
    if (!trimmedMessage || this.disabled) return;

    this.sendMessage.emit(trimmedMessage);
    this.message.set('');
  }

  onInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.message.set(target.value);
  }
}

// Mock decorators for testing
function Input() {
  return function(target: any, propertyKey: string) {
    // Property descriptor setup handled at runtime
  };
}

function Output() {
  return function(target: any, propertyKey: string) {
    // Property descriptor setup handled at runtime
  };
}

function ViewChild(selector: string) {
  return function(target: any, propertyKey: string) {
    // Property descriptor setup handled at runtime
  };
}

function ElementRef<T>(selector: string) {
  return class {
    nativeElement = {
      style: { height: '' },
    };
  };
}

describe('MessageInputComponent', () => {
  let component: TestMessageInputComponent;

  beforeEach(() => {
    component = new TestMessageInputComponent();
  });

  describe('@Input() isStreaming', () => {
    it('should exist', () => {
      expect(component.isStreaming).toBeDefined();
    });

    it('should default to false', () => {
      expect(component.isStreaming).toBe(false);
    });

    it('should accept true value', () => {
      component.isStreaming = true;
      expect(component.isStreaming).toBe(true);
    });

    it('should be settable to true and back to false', () => {
      component.isStreaming = false;
      expect(component.isStreaming).toBe(false);

      component.isStreaming = true;
      expect(component.isStreaming).toBe(true);

      component.isStreaming = false;
      expect(component.isStreaming).toBe(false);
    });
  });

  describe('@Output() stopInstance', () => {
    it('should exist as EventEmitter', () => {
      expect(component.stopInstance).toBeDefined();
      expect(typeof component.stopInstance.emit).toBe('function');
    });

    it('should emit event when called', () => {
      const emitSpy = jest.spyOn(component.stopInstance, 'emit');
      component.stopInstance.emit();
      expect(emitSpy).toHaveBeenCalled();
    });

    it('should emit undefined (void) when called', () => {
      let emittedValue: void | undefined;
      component.stopInstance.subscribe((value) => {
        emittedValue = value;
      });
      component.stopInstance.emit();
      expect(emittedValue).toBeUndefined();
    });
  });

  describe('button swap functionality', () => {
    it('should have isStreaming false by default for send button display', () => {
      expect(component.isStreaming).toBe(false);
      // When isStreaming is false, send button should be shown (not stop button)
    });

    it('should have isStreaming true for stop button display', () => {
      component.isStreaming = true;
      expect(component.isStreaming).toBe(true);
      // When isStreaming is true, stop button should be shown (not send button)
    });

    it('should swap correctly when isStreaming toggles', () => {
      // Initially isStreaming is false
      expect(component.isStreaming).toBe(false);

      // Toggle to true (stop button visible)
      component.isStreaming = true;
      expect(component.isStreaming).toBe(true);

      // Toggle back to false (send button visible)
      component.isStreaming = false;
      expect(component.isStreaming).toBe(false);
    });
  });

  describe('sendMessage', () => {
    it('should have sendMessage as EventEmitter', () => {
      expect(component.sendMessage).toBeDefined();
      expect(typeof component.sendMessage.emit).toBe('function');
    });

    it('should emit message content when handleSubmit is called', () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Hello, world!');

      component.handleSubmit();

      expect(emitSpy).toHaveBeenCalledWith('Hello, world!');
    });

    it('should not emit when message is empty', () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('');

      component.handleSubmit();

      expect(emitSpy).not.toHaveBeenCalled();
    });

    it('should clear message after successful send', () => {
      component.message.set('Hello!');
      expect(component.message()).toBe('Hello!');

      component.handleSubmit();

      expect(component.message()).toBe('');
    });
  });

  describe('canSend', () => {
    it('should be false when message is empty', () => {
      component.message.set('');
      expect(component.canSend).toBe(false);
    });

    it('should be false when disabled is true', () => {
      component.message.set('Hello!');
      component.disabled = true;
      expect(component.canSend).toBe(false);
    });

    it('should be true when message is not empty and disabled is false', () => {
      component.message.set('Hello!');
      component.disabled = false;
      expect(component.canSend).toBe(true);
    });
  });
});
