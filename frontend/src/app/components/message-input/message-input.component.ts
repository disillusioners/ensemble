import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-message-input',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './message-input.html',
  styleUrls: ['./message-input.scss']
})
export class MessageInputComponent {
  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  
  @Input() disabled = false;
  @Input() agentColor = 'coder';
  @Output() sendMessage = new EventEmitter<string>();

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

  handleSubmit(event?: Event): void {
    event?.preventDefault();
    
    const trimmedMessage = this.message().trim();
    if (!trimmedMessage || this.disabled) return;

    this.sendMessage.emit(trimmedMessage);
    this.message.set('');
    
    // Reset textarea height
    if (this.textareaRef) {
      this.textareaRef.nativeElement.style.height = 'auto';
    }
  }

  onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.handleSubmit();
    }
  }

  onInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.message.set(target.value);
    
    // Auto-resize textarea
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 150)}px`;
  }
}
