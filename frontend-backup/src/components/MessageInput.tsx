import { FunctionalComponent } from 'preact';
import { useState, useRef } from 'preact/hooks';

interface MessageInputProps {
  onSendMessage: (content: string) => void;
  disabled?: boolean;
  agentColor?: string;
}

const agentColorMap: Record<string, string> = {
  'leader': '#f59e0b',
  'coder': '#10a7f7',
  'reviewer': '#8b5cf6',
};

export const MessageInput: FunctionalComponent<MessageInputProps> = ({
  onSendMessage,
  disabled,
  agentColor = 'accent-cyan',
}) => {
  const [message, setMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  
  const color = agentColorMap[agentColor] || '#10a7f7';

  const handleSubmit = (e?: Event) => {
    e?.preventDefault();
    if (!message.trim() || disabled) return;

    onSendMessage(message.trim());
    setMessage('');
    
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleInput = (e: Event) => {
    const target = e.target as HTMLTextAreaElement;
    setMessage(target.value);
    
    // Auto-resize textarea
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 150)}px`;
  };

  const canSend = message.trim() && !disabled;

  return (
    <form onSubmit={handleSubmit} class="p-4 border-t border-dark-700 bg-dark-900">
      <div class="max-w-3xl mx-auto">
        <div 
          class="relative flex items-end gap-2 p-2 rounded-2xl bg-dark-800 border border-dark-700 transition-all duration-200"
          style={{
            borderColor: canSend ? `${color}80` : undefined,
            boxShadow: canSend ? `0 0 0 2px ${color}20` : undefined,
          }}
        >
          {/* Input textarea */}
          <textarea
            ref={textareaRef}
            value={message}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Type your message..."
            disabled={disabled}
            rows={1}
            class="
              flex-1 bg-transparent text-dark-100 placeholder-dark-500 
              font-body text-[15px] resize-none outline-none
              max-h-[150px] py-2 px-3
              disabled:opacity-50
            "
          />
          
          {/* Send button */}
          <button
            type="submit"
            disabled={!canSend}
            class="p-3 rounded-xl transition-all duration-200"
            style={{
              backgroundColor: canSend ? color : '#343541',
              color: canSend ? 'white' : '#6e6e80',
              transform: canSend ? 'scale(1)' : undefined,
              cursor: canSend ? 'pointer' : 'not-allowed',
            }}
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
        
        <p class="text-center text-dark-600 text-xs mt-2 font-body">
          Press <kbd class="px-1.5 py-0.5 rounded bg-dark-800 text-dark-400">Enter</kbd> to send, 
          <kbd class="px-1.5 py-0.5 rounded bg-dark-800 text-dark-400 ml-1">Shift + Enter</kbd> for new line
        </p>
      </div>
    </form>
  );
};
