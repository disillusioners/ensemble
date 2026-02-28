import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, AfterViewChecked, signal, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Message, Agent, ToolCall } from '../../models';

@Component({
  selector: 'app-chat-interface',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './chat-interface.html',
  styleUrls: ['./chat-interface.scss']
})
export class ChatInterfaceComponent implements AfterViewChecked {
  @ViewChild('messagesEnd') messagesEndRef!: ElementRef<HTMLDivElement>;
  
  @Input() messages: Message[] = [];
  @Input() isLoading = false;
  @Input() agent: Agent | null | undefined = null;
  @Input() sessionId: string | null = null;
  @Input() showThinking = true;
  @Input() showToolCalls = true;

  private shouldScroll = signal(false);

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'coder': '#10a7f7',
    'reviewer': '#8b5cf6',
  };

  constructor() {
    effect(() => {
      if (this.messages.length > 0 || this.isLoading) {
        this.shouldScroll.set(true);
      }
    });
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll()) {
      this.scrollToBottom();
      this.shouldScroll.set(false);
    }
  }

  private scrollToBottom(): void {
    if (this.messagesEndRef) {
      this.messagesEndRef.nativeElement.scrollIntoView({ behavior: 'smooth' });
    }
  }

  get agentColor(): string {
    return this.agent ? this.agentColorMap[this.agent.id] || '#10a7f7' : '#10a7f7';
  }

  formatToolArgs(args: string | Record<string, unknown>): string {
    if (typeof args === 'string') return args;
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return '[Unable to display]';
    }
  }

  formatToolOutput(output: string | unknown): string {
    if (typeof output === 'string') return output;
    try {
      return JSON.stringify(output, null, 2);
    } catch {
      return '[Unable to display]';
    }
  }

  getFormattedToolCalls(toolCalls: ToolCall[] | undefined) {
    if (!toolCalls) return [];
    return toolCalls.map(tc => ({
      ...tc,
      formattedArgs: this.formatToolArgs(tc.arguments),
      formattedOutput: tc.output ? this.formatToolOutput(tc.output) : null
    }));
  }

  trackByMessageId(index: number, message: Message): string {
    return message.message_id || index.toString();
  }

  formatTime(dateString: string): string {
    return new Date(dateString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  hasMeaningfulContent(message: Message): boolean {
    const content = message.content;
    // Check if content exists and has non-whitespace characters
    return content != null && content.trim().length > 0;
  }

  getThinkingContent(message: Message): string | null {
    // Prioritize thinking (from metadata) over thinking_extracted (from tags)
    if (message.thinking && message.thinking.trim()) {
      return message.thinking;
    }
    if (message.thinking_extracted && message.thinking_extracted.trim()) {
      return message.thinking_extracted;
    }
    return null;
  }
}
