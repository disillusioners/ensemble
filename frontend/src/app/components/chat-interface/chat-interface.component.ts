import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, AfterViewChecked, signal, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MarkdownModule } from 'ngx-markdown';
import { Message, Agent, ToolCall } from '../../models';

@Component({
  selector: 'app-chat-interface',
  standalone: true,
  imports: [CommonModule, MarkdownModule],
  templateUrl: './chat-interface.html',
  styleUrls: ['./chat-interface.scss']
})
export class ChatInterfaceComponent implements AfterViewChecked, OnChanges {
  @ViewChild('messagesEnd') messagesEndRef!: ElementRef<HTMLDivElement>;
  @ViewChild('messagesContainer') messagesContainerRef!: ElementRef<HTMLDivElement>;
  
  @Input() messages: Message[] = [];
  @Input() pendingMessage: Message | null = null;
  @Input() isLoading = false;
  @Input() agent: Agent | null | undefined = null;
  @Input() sessionId: string | null = null;
  @Input() showThinking = true;
  @Input() showToolCalls = true;

  private shouldScroll = signal(false);
  isNearBottom = signal(true);
  private userHasScrolled = signal(false);

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'coder': '#10a7f7',
    'reviewer': '#8b5cf6',
  };

  ngOnChanges(changes: SimpleChanges): void {
    const messagesChanged = changes['messages'] && changes['messages'].currentValue?.length !== changes['messages'].previousValue?.length;
    const pendingMessageChanged = changes['pendingMessage'];
    const isLoadingChanged = changes['isLoading'];

    if ((messagesChanged || pendingMessageChanged || isLoadingChanged) && !this.userHasScrolled()) {
      this.shouldScroll.set(true);
    }
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll()) {
      this.scrollToBottom();
      this.shouldScroll.set(false);
    }
  }

  onScroll(event: Event): void {
    const container = event.target as HTMLDivElement;
    const scrollThreshold = 100; // pixels from bottom to consider "near bottom"
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    
    const nearBottom = distanceFromBottom <= scrollThreshold;
    this.isNearBottom.set(nearBottom);
    
    // If user scrolls to bottom manually, reset the flag
    if (nearBottom) {
      this.userHasScrolled.set(false);
    }
  }

  scrollToBottom(): void {
    if (this.messagesEndRef) {
      this.messagesEndRef.nativeElement.scrollIntoView({ behavior: 'smooth' });
      this.isNearBottom.set(true);
      this.userHasScrolled.set(false);
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

  /**
   * Check if a message has any visible content to display.
   * Used to determine if the entire message row should be rendered.
   */
  hasVisibleContent(message: Message): boolean {
    // User messages are always shown
    if (message.role === 'user') return true;
    
    // For assistant messages, check if there's anything to display
    const hasContent = this.hasMeaningfulContent(message);
    const hasThinking = this.showThinking && !!this.getThinkingContent(message);
    const hasToolCalls = this.showToolCalls && !!message.tool_calls && message.tool_calls.length > 0;
    
    return hasContent || hasThinking || hasToolCalls;
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
