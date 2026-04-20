import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, signal, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface MessagePayload {
  content: string;
  images: string[];
}

interface FilePreview {
  id: string;
  dataUrl: string;
  name: string;
  size: number;
}

@Component({
  selector: 'app-message-input',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './message-input.html',
  styleUrls: ['./message-input.scss']
})
export class MessageInputComponent implements OnDestroy {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'coder';
  @Input() isStreaming = false;
  @Output() sendMessage = new EventEmitter<MessagePayload>();
  @Output() stopInstance = new EventEmitter<void>();

  message = signal('');
  images = signal<FilePreview[]>([]);

  protected readonly MAX_IMAGES = 3;
  protected readonly MAX_IMAGE_SIZE = 10 * 1024 * 1024;
  private readonly ACCEPTED_TYPES = [
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'image/bmp',
    'image/tiff'
  ];

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
    
    // Reset textarea height
    if (this.textareaRef) {
      this.textareaRef.nativeElement.style.height = 'auto';
    }
  }

  onInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.message.set(target.value);
    
    // Auto-resize textarea
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 150)}px`;
  }

  onAttachClick(): void {
    const input = document.querySelector('.image-input') as HTMLInputElement;
    if (input) {
      input.click();
    }
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files;
    if (files) {
      this.processFiles(Array.from(files));
    }
    input.value = '';
  }

  private convertToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async processFiles(files: File[]): Promise<void> {
    for (const file of files) {
      // Check count limit
      if (this.images().length >= this.MAX_IMAGES) {
        alert(`Maximum ${this.MAX_IMAGES} images allowed`);
        break;
      }

      // Check file type
      if (!this.ACCEPTED_TYPES.includes(file.type)) {
        alert(`Invalid file type: ${file.type}. Please use PNG, JPEG, GIF, WebP, BMP, or TIFF.`);
        continue;
      }

      // Check file size
      if (file.size > this.MAX_IMAGE_SIZE) {
        alert(`File too large: ${file.name}. Maximum size is 10MB.`);
        continue;
      }

      try {
        const dataUrl = await this.convertToBase64(file);
        const filePreview: FilePreview = {
          id: crypto.randomUUID(),
          dataUrl,
          name: file.name,
          size: file.size
        };
        this.images.update(imgs => [...imgs, filePreview]);
      } catch (error) {
        alert(`Failed to read file: ${file.name}`);
      }
    }
  }

  removeImage(id: string): void {
    this.images.update(imgs => imgs.filter(img => img.id !== id));
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    const wrapper = document.querySelector('.input-wrapper');
    if (wrapper) {
      wrapper.classList.add('drag-over');
    }
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    const wrapper = document.querySelector('.input-wrapper');
    if (wrapper) {
      wrapper.classList.remove('drag-over');
    }
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    const wrapper = document.querySelector('.input-wrapper');
    if (wrapper) {
      wrapper.classList.remove('drag-over');
    }
    
    const files = event.dataTransfer?.files;
    if (files) {
      this.processFiles(Array.from(files));
    }
  }

  ngOnDestroy(): void {
    // No cleanup needed for data URIs
  }
}
