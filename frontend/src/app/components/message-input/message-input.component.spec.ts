import { signal } from '@angular/core';
import { EventEmitter } from '@angular/core';

type InstanceStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error' | 'terminated' | 'queued' | 'waiting_children' | 'failed';

// Simplified MessageInputComponent for testing (mirrors actual component structure)
interface MessagePayload {
  content: string;
  images: string[];
  image_refs?: string[];
}

interface FilePreview {
  id: string;
  dataUrl: string;
  name: string;
  size: number;
  refUrl?: string;
  imageId?: string;
  uploadStatus?: 'idle' | 'uploading' | 'uploaded' | 'failed';
  uploadError?: string;
}

class TestMessageInputComponent {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'developer';
  @Input() instanceStatus: InstanceStatus | null = null;
  @Output() sendMessage = new EventEmitter<MessagePayload>();
  @Output() pauseInstance = new EventEmitter<void>();

  message = signal('');
  images = signal<FilePreview[]>([]);

  protected readonly MAX_IMAGES = 3;

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'developer': '#10a7f7',
    'coder': '#10a7f7',  // backward compat for cached responses
    'reviewer': '#8b5cf6',
  };

  get color(): string {
    return this.agentColorMap[this.agentColor] || '#10a7f7';
  }

  get canSend(): boolean {
    return (!!this.message().trim() || this.images().length > 0) && !this.disabled;
  }

  // Computed signal: returns true when instance is actively running
  readonly isInstanceRunning = (): boolean => {
    return this.instanceStatus === 'running' || this.instanceStatus === 'waiting_children' || this.instanceStatus === 'queued';
  };

  /**
   * Phase 4 mirror — async handleSubmit. Mirrors the production
   * upload-first flow:
   * - no images (pending = 0): sync emit, payload.images = undefined,
   *   payload.image_refs = undefined (text-only legacy path);
   * - one image with refUrl: sync emit, payload.image_refs = [url];
   * - one image WITHOUT refUrl: delegates to a stub upload mock
   *   that resolves with a synthetic ref URL (the spec injects the
   *   stub via ``mockUpload``).
   *
   * The spec-only stub lets the existing TestMessageInputComponent
   * stay free of HttpClient while still exercising the new
   * upload-first shape end-to-end. The full ImageUploadService
   * pipeline is covered by image-upload.spec.ts.
   */
  mockUpload: (file: File) => Promise<{ ref_url: string; image_id: string }> = async () => {
    throw new Error('mockUpload not injected');
  };

  async handleSubmit(): Promise<void> {
    const trimmedMessage = this.message().trim();
    if ((!trimmedMessage && this.images().length === 0) || this.disabled) return;

    const pending = this.images().filter(p => !p.refUrl && p.uploadStatus !== 'uploaded');
    if (pending.length === 0) {
      const payload: MessagePayload = {
        content: trimmedMessage,
      };
      this.sendMessage.emit(payload);
      this.message.set('');
      this.images.set([]);
      return;
    }

    // Upload each pending chip — mirror of MessageInputComponent.uploadPreview.
    const settled = await Promise.allSettled(
      pending.map(async preview => {
        // The cache lives in the production module; the spec uses the
        // FilePreview.name to look up a synthetic File via a parallel
        // map. Production uses the WeakMap; the spec mirrors the
        // behavioral contract by skipping the cache lookup entirely
        // (tests inject the File via ``pending`` directly).
        const result = await this.mockUpload(new File([], preview.name));
        preview.refUrl = result.ref_url;
        preview.imageId = result.image_id;
        preview.uploadStatus = 'uploaded';
        return preview;
      }),
    );

    const anyFailed = settled.some(r => r.status === 'rejected');
    if (anyFailed) {
      // Production marks failed chips. Mirror:
      for (let i = 0; i < settled.length; i++) {
        if (settled[i].status === 'rejected') {
          pending[i].uploadStatus = 'failed';
        }
      }
      // No emit on block-send default.
      return;
    }

    const refs = this.images().map(img => img.refUrl).filter((u): u is string => !!u);
    const payload: MessagePayload = {
      content: trimmedMessage,
      image_refs: refs,
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

  describe('@Input() instanceStatus', () => {
    it('should exist', () => {
      expect(component.instanceStatus).toBeDefined();
    });

    it('should default to null', () => {
      expect(component.instanceStatus).toBe(null);
    });

    it('should accept running value', () => {
      component.instanceStatus = 'running';
      expect(component.instanceStatus).toBe('running');
    });

    it('should accept waiting_children value', () => {
      component.instanceStatus = 'waiting_children';
      expect(component.instanceStatus).toBe('waiting_children');
    });
  });

  describe('@Output() pauseInstance', () => {
    it('should exist as EventEmitter', () => {
      expect(component.pauseInstance).toBeDefined();
      expect(typeof component.pauseInstance.emit).toBe('function');
    });

    it('should emit event when called', () => {
      const emitSpy = jest.spyOn(component.pauseInstance, 'emit');
      component.pauseInstance.emit();
      expect(emitSpy).toHaveBeenCalled();
    });

    it('should emit undefined (void) when called', () => {
      let emittedValue: void | undefined;
      component.pauseInstance.subscribe((value) => {
        emittedValue = value;
      });
      component.pauseInstance.emit();
      expect(emittedValue).toBeUndefined();
    });
  });

  describe('isInstanceRunning', () => {
    it('should return true for running status', () => {
      component.instanceStatus = 'running';
      expect(component.isInstanceRunning()).toBe(true);
    });

    it('should return true for waiting_children status', () => {
      component.instanceStatus = 'waiting_children';
      expect(component.isInstanceRunning()).toBe(true);
    });

    it('should return false for idle status', () => {
      component.instanceStatus = 'idle';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return false for error status', () => {
      component.instanceStatus = 'error';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return false for terminated status', () => {
      component.instanceStatus = 'terminated';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return false for completed status', () => {
      component.instanceStatus = 'completed';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return false for paused status', () => {
      component.instanceStatus = 'paused';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return true for queued status', () => {
      component.instanceStatus = 'queued';
      expect(component.isInstanceRunning()).toBe(true);
    });

    it('should return false for failed status', () => {
      component.instanceStatus = 'failed';
      expect(component.isInstanceRunning()).toBe(false);
    });

    it('should return false for null status', () => {
      component.instanceStatus = null;
      expect(component.isInstanceRunning()).toBe(false);
    });
  });

  describe('sendMessage', () => {
    it('should have sendMessage as EventEmitter', () => {
      expect(component.sendMessage).toBeDefined();
      expect(typeof component.sendMessage.emit).toBe('function');
    });

    it('should emit message content when handleSubmit is called', async () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Hello, world!');
      component.mockUpload = async () => ({ ref_url: '', image_id: '' });

      await component.handleSubmit();

      // No images attached — text-only sync emit, no image_refs.
      expect(emitSpy).toHaveBeenCalledWith({
        content: 'Hello, world!',
      });
    });

    it('should not emit when message is empty and no images', async () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('');

      await component.handleSubmit();

      expect(emitSpy).not.toHaveBeenCalled();
    });

    it('should clear images after successful send', async () => {
      component.images.set([{
        id: 'test-id',
        dataUrl: 'data:image/png;base64,test',
        name: 'test.png',
        size: 100,
        refUrl: '/api/tmp_images/abc123',
        imageId: 'abc123',
        uploadStatus: 'uploaded',
      }]);
      component.mockUpload = async () => ({ ref_url: '', image_id: '' });
      expect(component.images().length).toBe(1);

      await component.handleSubmit();

      expect(component.images()).toEqual([]);
    });

    it('should emit with image_refs (NOT images) when uploads succeed', async () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Check this out!');
      // Chip is "idle" — no refUrl yet — so handleSubmit goes through
      // the upload-first path. The mock resolves with the canonical
      // /api/tmp_images/<id> URL.
      component.images.set([{
        id: 'img-1',
        dataUrl: 'data:image/png;base64,abc123',
        name: 'photo.png',
        size: 5000,
        uploadStatus: 'idle',
      }]);
      component.mockUpload = async (file: File) => ({
        ref_url: `/api/tmp_images/${file.name === 'photo.png' ? 'abc123' : 'unknown'}`,
        image_id: 'abc123',
      });

      await component.handleSubmit();

      // Phase 4 contract: ref-sends carry `image_refs` (NEVER `images`).
      expect(emitSpy).toHaveBeenCalledWith({
        content: 'Check this out!',
        image_refs: ['/api/tmp_images/abc123'],
      });
      // Cross-seam invariant: emitted payload has NO `images` field.
      const emitted = emitSpy.mock.calls[0][0] as MessagePayload;
      expect(emitted.images).toBeUndefined();
      // Identity-grep mirror-parity: emitted ref URLs match the ref-form shape.
      expect(emitted.image_refs!.every((u: string) => u.startsWith('/api/tmp_images/'))).toBe(true);
    });

    it('should NOT emit when upload fails (block-send default)', async () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Try to send!');
      component.images.set([{
        id: 'img-1',
        dataUrl: 'data:image/png;base64,abc123',
        name: 'photo.png',
        size: 5000,
        uploadStatus: 'idle',
      }]);
      component.mockUpload = async () => {
        throw new Error('5xx');
      };

      await component.handleSubmit();

      // Block-send: NO emit on failure.
      expect(emitSpy).not.toHaveBeenCalled();
      // Chip transitioned to failed.
      expect(component.images()[0].uploadStatus).toBe('failed');
    });

    it('should NOT emit on mixed results (block-send default)', async () => {
      const emitSpy = jest.spyOn(component.sendMessage, 'emit');
      component.message.set('Mixed!');
      component.images.set([
        { id: 'img-1', dataUrl: 'd1', name: 'a.png', size: 1, uploadStatus: 'idle' },
        { id: 'img-2', dataUrl: 'd2', name: 'b.png', size: 1, uploadStatus: 'idle' },
        { id: 'img-3', dataUrl: 'd3', name: 'c.png', size: 1, uploadStatus: 'idle' },
      ]);
      // 2 succeed, 1 fails — block-send still holds.
      component.mockUpload = async (file: File) => {
        if (file.name === 'b.png') throw new Error('flaky');
        return { ref_url: `/api/tmp_images/${file.name}`, image_id: file.name };
      };

      await component.handleSubmit();

      expect(emitSpy).not.toHaveBeenCalled();
      // The two successful chips landed; the failing chip is failed.
      const statuses = component.images().map(i => i.uploadStatus);
      expect(statuses).toContain('uploaded');
      expect(statuses).toContain('failed');
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
