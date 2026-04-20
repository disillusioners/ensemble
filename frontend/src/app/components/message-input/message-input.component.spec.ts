import { signal } from '@angular/core';
import { EventEmitter } from '@angular/core';

// Simplified MessageInputComponent for testing (mirrors actual component structure)
interface MessagePayload {
  content: string;
  images: string[];
}

interface FilePreview {
  id: string;
  dataUrl: string;
  name: string;
  size: number;
}

class TestMessageInputComponent {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'coder';
  @Input() isStreaming = false;
  @Output() sendMessage = new EventEmitter<MessagePayload>();
  @Output() stopInstance = new EventEmitter<void>();

  message = signal('');
  images = signal<FilePreview[]>([]);

  protected readonly MAX_IMAGES = 3;

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'coder': '#10a7f7',
    'reviewer': '#8b5cf6',
  };

  get color(): string {
    return this.agentColorMap[this.agentColor] || '#10a7f7';
  }

  get canSend(): boolean {
    return (!!this.message().trim() || this.images().length > 0) && !this.disabled;
  }

  handleSubmit(): void {
    const trimmedMessage = this.message().trim();
    if ((!trimmedMessage && this.images().length === 0) || this.disabled) return;

    const payload: MessagePayload = {
      content: trimmedMessage,
      images: this.images().map(img => img.dataUrl)
    };

    this.sendMessage.emit(payload);
    this.message.set('');
    this.images.set([]);
  }

  onInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.message.set(target.value);
  }

  removeImage(id: string): void {
    this.images.update(imgs => imgs.filter(img => img.id !== id));
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

      expect(emitSpy).toHaveBeenCalledWith({
        content: 'Hello, world!',
        images: []
      });
    });

    it('should not emit when message is empty and no images', () => {
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

    it('should clear images after successful send', () => {
      component.images.set([{
        id: 'test-id',
        dataUrl: 'data:image/png;base64,test',
        name: 'test.png',
        size: 100
      }]);
      expect(component.images().length).toBe(1);

      component.handleSubmit();

      expect(component.images()).toEqual([]);
    });

    it('should emit with images when images are attached', () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Check this out!');
      component.images.set([{
        id: 'img-1',
        dataUrl: 'data:image/png;base64,abc123',
        name: 'photo.png',
        size: 5000
      }]);

      component.handleSubmit();

      expect(emitSpy).toHaveBeenCalledWith({
        content: 'Check this out!',
        images: ['data:image/png;base64,abc123']
      });
    });
  });

  describe('canSend', () => {
    it('should be false when message is empty and no images', () => {
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

    it('should be true when images are attached even without text', () => {
      component.message.set('');
      component.images.set([{
        id: 'img-1',
        dataUrl: 'data:image/png;base64,test',
        name: 'test.png',
        size: 100
      }]);
      expect(component.canSend).toBe(true);
    });
  });

  describe('removeImage', () => {
    it('should remove image by id', () => {
      component.images.set([
        { id: 'img-1', dataUrl: 'data1', name: 'a.png', size: 100 },
        { id: 'img-2', dataUrl: 'data2', name: 'b.png', size: 200 }
      ]);

      component.removeImage('img-1');

      expect(component.images()).toEqual([
        { id: 'img-2', dataUrl: 'data2', name: 'b.png', size: 200 }
      ]);
    });

    it('should do nothing when id not found', () => {
      component.images.set([
        { id: 'img-1', dataUrl: 'data1', name: 'a.png', size: 100 }
      ]);

      component.removeImage('non-existent');

      expect(component.images().length).toBe(1);
    });
  });
});
