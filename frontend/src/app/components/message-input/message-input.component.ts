import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface MessagePayload {
  content: string;
  images?: string[];  // optional, not required
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
export class MessageInputComponent {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') fileInputRef!: ElementRef<HTMLInputElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'coder';
  @Input() isStreaming = false;
  @Output() sendMessage = new EventEmitter<MessagePayload>();
  @Output() stopInstance = new EventEmitter<void>();

  message = signal('');
  images = signal<FilePreview[]>([]);
  isDragOver = signal(false);
  validationError = signal<string | null>(null);

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
    // Do NOT clear message/images here — parent calls clearInput() on API success
  }

  clearInput(): void {
    this.message.set('');
    this.images.set([]);
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

  onKeydownEnter(event: Event): void {
    const keyboardEvent = event as KeyboardEvent;
    if (keyboardEvent.shiftKey) return; // Allow newline
    event.preventDefault();
    this.handleSubmit();
  }

  onAttachClick(): void {
    this.fileInputRef.nativeElement.click();
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

  private showValidationError(message: string): void {
    this.validationError.set(message);
    setTimeout(() => this.validationError.set(null), 4000);
  }

  async processFiles(files: File[]): Promise<void> {
    for (const file of files) {
      // Check count limit
      if (this.images().length >= this.MAX_IMAGES) {
        this.showValidationError('You can only attach up to ' + this.MAX_IMAGES + ' images.');
        break;
      }

      // Check file type
      if (!this.ACCEPTED_TYPES.includes(file.type)) {
        this.showValidationError('Unsupported image type. Please use PNG, JPEG, GIF, WebP, BMP, or TIFF.');
        continue;
      }

      // Check file size
      if (file.size > this.MAX_IMAGE_SIZE) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        this.showValidationError('File "' + file.name + '" is ' + sizeMB + 'MB. Maximum is 10MB.');
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
        this.showValidationError('Failed to read file "' + file.name + '".');
      }
    }
  }

  removeImage(id: string): void {
    this.images.update(imgs => imgs.filter(img => img.id !== id));
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(false);
    
    const files = event.dataTransfer?.files;
    if (files) {
      this.processFiles(Array.from(files));
    }
  }
}
