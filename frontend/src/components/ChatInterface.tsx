import { FunctionalComponent } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import type { Message, Agent } from '../types';

interface ChatInterfaceProps {
  messages: Message[];
  isLoading: boolean;
  agent: Agent | null | undefined;
  sessionId: string | null;
  showThinking: boolean;
  showToolCalls: boolean;
}

const agentColorMap: Record<string, string> = {
  'leader': '#f59e0b',
  'coder': '#10a7f7',
  'reviewer': '#8b5cf6',
};

function MessageBubble({ 
  message, 
  agent,
  showThinking,
  showToolCalls 
}: { 
  message: Message; 
  agent?: Agent | null;
  showThinking: boolean;
  showToolCalls: boolean;
}) {
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';
  const color = agent ? agentColorMap[agent.id] : '#10a7f7';
  
  // Safely format tool call arguments
  const formatArgs = (args: unknown): string => {
    if (typeof args === 'string') return args;
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return '[Unable to display]';
    }
  };
  
  // Parse tool calls with safe formatting
  const toolCalls = message.tool_calls?.map(tc => ({
    ...tc,
    formattedArgs: formatArgs(tc.arguments)
  }));
  
  return (
    <div class={`flex gap-4 animate-slide-up ${isUser ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div 
        class="w-10 h-10 rounded-2xl flex items-center justify-center flex-shrink-0"
        style={{ backgroundColor: isUser ? '#343541' : `${color}20` }}
      >
        {isUser ? (
          <svg class="w-5 h-5 text-dark-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
        ) : (
          <span class="text-xl">{agent?.icon || '🤖'}</span>
        )}
      </div>
      
      {/* Message content */}
      <div class={`flex flex-col max-w-[70%] ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Thinking block - only for assistant messages */}
        {isAssistant && message.thinking && showThinking && (
          <div class="mb-2 w-full">
            <div class="px-4 py-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-sm">
              <div class="flex items-center gap-1.5 mb-1.5 text-amber-400 text-xs font-medium">
                <span>💭</span>
                <span>Thinking</span>
              </div>
              <pre class="text-dark-300 text-xs whitespace-pre-wrap font-mono overflow-x-auto">
                {message.thinking}
              </pre>
            </div>
          </div>
        )}
        
        {/* Tool calls - only for assistant messages */}
        {isAssistant && toolCalls && toolCalls.length > 0 && showToolCalls && (
          <div class="mb-2 w-full space-y-1.5">
            {toolCalls.map((tc, idx) => (
              <div key={tc.id || idx} class="px-4 py-2 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-sm">
                <div class="flex items-center gap-1.5 mb-1.5 text-cyan-400 text-xs font-medium">
                  <span>🔧</span>
                  <span>{tc.name}</span>
                </div>
                <pre class="text-dark-300 text-xs whitespace-pre-wrap font-mono overflow-x-auto">
                  {tc.formattedArgs}
                </pre>
              </div>
            ))}
          </div>
        )}
        
        {/* Main content */}
        <div 
          class="px-5 py-3 rounded-2xl font-body text-[15px] leading-relaxed whitespace-pre-wrap"
          style={{ 
            backgroundColor: isUser ? '#343541' : `${color}15`,
            color: '#ececf1',
            border: isAssistant ? `1px solid ${color}30` : undefined,
            borderTopLeftRadius: isAssistant ? '4px' : undefined,
            borderTopRightRadius: isUser ? '4px' : undefined,
          }}
        >
          {message.content || (isAssistant ? '...' : '')}
        </div>
        
        {/* Timestamp */}
        <span class="text-dark-600 text-xs mt-1.5 px-1">
          {new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </div>
  );
}

function TypingIndicator({ agent }: { agent?: Agent | null }) {
  const color = agent ? agentColorMap[agent.id] : '#10a7f7';
  
  return (
    <div class="flex gap-4 animate-fade-in">
      <div 
        class="w-10 h-10 rounded-2xl flex items-center justify-center flex-shrink-0"
        style={{ backgroundColor: `${color}20` }}
      >
        <span class="text-xl">{agent?.icon || '🤖'}</span>
      </div>
      <div 
        class="px-5 py-3 rounded-2xl"
        style={{ backgroundColor: `${color}15`, border: `1px solid ${color}30` }}
      >
        <div class="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              class="w-2 h-2 rounded-full bg-dark-500 animate-pulse"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export const ChatInterface: FunctionalComponent<ChatInterfaceProps> = ({
  messages,
  isLoading,
  agent,
  sessionId,
  showThinking,
  showToolCalls,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const agentColor = agent ? agentColorMap[agent.id] : '#10a7f7';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  if (!sessionId) {
    return (
      <div class="flex-1 flex items-center justify-center bg-dark-950">
        <div class="text-center max-w-md px-6">
          <div 
            class="w-20 h-20 mx-auto mb-6 rounded-3xl flex items-center justify-center"
            style={{ backgroundColor: `${agentColor}20` }}
          >
            <span class="text-4xl">{agent?.icon || '🤖'}</span>
          </div>
          <h2 class="font-display text-2xl font-semibold text-dark-100 mb-2">
            {agent?.name || 'Select an Agent'}
          </h2>
          <p class="text-dark-500 font-body">
            {agent?.description || 'Choose an agent to start a conversation'}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div class="flex-1 overflow-y-auto bg-dark-950 p-6">
      <div class="max-w-3xl mx-auto space-y-6">
        {/* Welcome message */}
        {messages.length === 0 && (
          <div class="text-center py-12 animate-fade-in">
            <div 
              class="inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-4"
              style={{ backgroundColor: `${agentColor}20` }}
            >
              <span class="text-3xl">{agent?.icon || '🤖'}</span>
            </div>
            <h3 class="font-display text-xl font-semibold text-dark-100 mb-2">
              {agent?.name} is ready
            </h3>
            <p class="text-dark-500 text-sm">
              Send a message to start the conversation
            </p>
          </div>
        )}
        
        {/* Messages */}
        {messages.map((message, index) => (
          <MessageBubble 
            key={message.message_id || index} 
            message={message}
            agent={agent}
            showThinking={showThinking}
            showToolCalls={showToolCalls}
          />
        ))}
        
        {/* Typing indicator */}
        {isLoading && <TypingIndicator agent={agent} />}
        
        {/* Auto-scroll anchor */}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
};
