import { FunctionalComponent } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import { AgentSelector } from './components/AgentSelector';
import { AgentSwitcher } from './components/AgentSwitcher';
import { SessionList } from './components/SessionList';
import { ChatInterface } from './components/ChatInterface';
import { MessageInput } from './components/MessageInput';
import { useSSE } from './hooks/useSSE';
import { api } from './utils/api';
import type { SessionInfo, Message, Agent } from './types';
import { AVAILABLE_AGENTS } from './types';

type AppView = 'select-agent' | 'chat';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-session-agent';

export const App: FunctionalComponent = () => {
  const [view, setView] = useState<AppView>('select-agent');
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionInfo | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [nextSessionAgent, setNextSessionAgent] = useState<Agent | null>(() => {
    // Initialize from localStorage
    const saved = localStorage.getItem(NEXT_AGENT_STORAGE_KEY);
    return saved ? AVAILABLE_AGENTS.find(a => a.id === saved) || null : null;
  });
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [health, setHealth] = useState<{ status: string; uptime_seconds: number; version: string } | null>(null);
  const [showThinking, setShowThinking] = useState(() => 
    localStorage.getItem('ensemble-show-thinking') === 'true'
  );
  const [showToolCalls, setShowToolCalls] = useState(() => 
    localStorage.getItem('ensemble-show-toolcalls') === 'true'
  );

  // Persist toggle states
  useEffect(() => {
    localStorage.setItem('ensemble-show-thinking', String(showThinking));
  }, [showThinking]);
  
  useEffect(() => {
    localStorage.setItem('ensemble-show-toolcalls', String(showToolCalls));
  }, [showToolCalls]);

  // SSE for real-time updates
  const { isStreaming, latestCompletedMessage, latestError } = useSSE(currentSession?.session_id || null);

  // Handle incoming SSE completed messages
  useEffect(() => {
    if (latestCompletedMessage && latestCompletedMessage.role === 'assistant') {
      setMessages(prev => {
        // Check if this is an update to an existing message or a new one
        const existingIndex = prev.findIndex(m => m.message_id === latestCompletedMessage.message_id);
        if (existingIndex >= 0) {
          // Update existing message
          const updated = [...prev];
          updated[existingIndex] = latestCompletedMessage;
          return updated;
        } else {
          // Add new message
          return [...prev, latestCompletedMessage];
        }
      });
      setIsSending(false);
    }
  }, [latestCompletedMessage]);

  // Handle SSE errors
  useEffect(() => {
    if (latestError) {
      console.error('Message processing error:', latestError);
      // Could show error toast/notification here
      setIsSending(false);
    }
  }, [latestError]);

  // Load health status and sessions on mount
  useEffect(() => {
    const init = async () => {
      try {
        const healthData = await api.health();
        setHealth(healthData);
        
        const sessionsData = await api.listSessions();
        setSessions(sessionsData.sessions);
      } catch (err) {
        console.error('Failed to initialize:', err);
      }
    };
    
    init();
    
    // Poll for sessions every 10 seconds
    const pollInterval = setInterval(async () => {
      try {
        const sessionsData = await api.listSessions();
        setSessions(sessionsData.sessions);
      } catch (err) {
        console.error('Failed to poll sessions:', err);
      }
    }, 10000);
    
    return () => clearInterval(pollInterval);
  }, []);

  // Load messages when session changes
  useEffect(() => {
    if (!currentSession) {
      setMessages([]);
      return;
    }

    const loadMessages = async () => {
      try {
        const msgs = await api.getMessages(currentSession.session_id);
        setMessages(msgs);
      } catch (err) {
        console.error('Failed to load messages:', err);
      }
    };

    loadMessages();
  }, [currentSession]);

  // Agent for next session (user can change via combobox)
  const handleSetNextSessionAgent = useCallback((agent: Agent) => {
    setNextSessionAgent(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }, []);

  const handleCreateSession = useCallback(async (agent: Agent) => {
    setIsLoading(true);
    try {
      // Convert agent name to path format expected by backend
      const agentPath = `./agents/${agent.id}`;
      const session = await api.createSession(agentPath);
      
      setCurrentSession(session);
      setSessions(prev => [session, ...prev]);
      setView('chat');
    } catch (err) {
      console.error('Failed to create session:', err);
      alert(`Failed to create session: ${err}`);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Create session from AgentSelector
  const handleCreateSessionFromSelector = useCallback(async () => {
    if (!nextSessionAgent) return;
    await handleCreateSession(nextSessionAgent);
  }, [nextSessionAgent, handleCreateSession]);

  const handleSelectSession = useCallback((session: SessionInfo) => {
    setCurrentSession(session);
    setView('chat');
    // Note: we don't change nextSessionAgent here - it's independent of current session
  }, []);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    try {
      await api.deleteSession(sessionId);
      setSessions(prev => prev.filter(s => s.session_id !== sessionId));
      
      if (currentSession?.session_id === sessionId) {
        setCurrentSession(null);
        setView('select-agent');
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, [currentSession]);

  const handleNewSession = useCallback(async () => {
    if (!nextSessionAgent) {
      // No agent selected, go to selector
      setView('select-agent');
      return;
    }
    
    setCurrentSession(null);
    setMessages([]);
    await handleCreateSession(nextSessionAgent);
  }, [nextSessionAgent, handleCreateSession]);

  const handleSendMessage = useCallback(async (content: string) => {
    if (!currentSession) return;

    // Clear any previous error
    setSendError(null);

    // Add user message to UI immediately
    const userMessage: Message = {
      type: 'message',
      message_id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMessage]);

    setIsSending(true);
    try {
      const response = await api.sendMessage(currentSession.session_id, content);
      
      // Update user message with real message_id from response
      setMessages(prev => prev.map(m => 
        m.message_id === userMessage.message_id 
          ? { ...m, message_id: response.message_id }
          : m
      ));
      
      // The assistant response will come via SSE (completed event)
      // isSending will be set to false when the completed event is received
    } catch (err) {
      console.error('Failed to send message:', err);
      // Show error feedback to user
      setSendError(err instanceof Error ? err.message : 'Failed to send message');
      // Keep the user message visible but mark it as failed
      setMessages(prev => prev.map(m => 
        m.message_id === userMessage.message_id 
          ? { ...m, error: 'Failed to send' }
          : m
      ));
      setIsSending(false);
    }
  }, [currentSession]);

  // Get the current session's agent (read-only, derived from session)
  const sessionAgent = currentSession 
    ? AVAILABLE_AGENTS.find(a => currentSession.agent_dir.includes(a.id)) || null
    : null;

  return (
    <div class="h-screen flex flex-col bg-dark-950 font-body text-dark-100 overflow-hidden">
      {/* Header */}
      <header class="h-14 flex items-center justify-between px-6 border-b border-dark-700 bg-dark-900 flex-shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet flex items-center justify-center">
            <span class="text-white font-bold text-sm">AC</span>
          </div>
          <h1 class="font-display text-lg font-semibold text-dark-50">Ensemble</h1>
        </div>
        
        <div class="flex items-center gap-4">
          {health && (
            <div class="flex items-center gap-2 text-sm">
              <span class={`w-2 h-2 rounded-full ${health.status === 'healthy' ? 'bg-accent-emerald' : 'bg-accent-rose'}`} />
              <span class="text-dark-500">v{health.version}</span>
            </div>
          )}
          {isStreaming && (
            <div class="flex items-center gap-1.5 text-xs text-accent-cyan">
              <span class="w-1.5 h-1.5 rounded-full bg-accent-cyan animate-pulse" />
              Live
            </div>
          )}
        </div>
      </header>

      {/* Main content */}
      {view === 'select-agent' && !currentSession ? (
        <div class="flex-1 flex items-center justify-center p-8 overflow-y-auto">
          <div class="max-w-4xl w-full">
            <AgentSelector
              selectedAgent={nextSessionAgent}
              onSelect={handleSetNextSessionAgent}
              onCreateSession={handleCreateSessionFromSelector}
              isLoading={isLoading}
            />
          </div>
        </div>
      ) : (
        <div class="flex-1 flex overflow-hidden">
          {/* Session sidebar */}
          <div class="w-72 flex-shrink-0">
            <SessionList
              sessions={sessions}
              currentSessionId={currentSession?.session_id || null}
              onSelectSession={handleSelectSession}
              onDeleteSession={handleDeleteSession}
              onNewSession={handleNewSession}
            />
          </div>
          
          {/* Chat area */}
          <div class="flex-1 flex flex-col">
            {/* Chat header with agent info */}
            <div class="h-14 flex items-center justify-between px-4 border-b border-dark-700 bg-dark-900 flex-shrink-0">
              <div class="flex items-center gap-3">
                <AgentSwitcher
                  selectedAgent={nextSessionAgent}
                  onAgentChange={handleSetNextSessionAgent}
                />
              </div>
              <div class="flex items-center gap-3">
                {/* Toggle buttons */}
                <div class="flex items-center gap-1.5">
                  <button
                    onClick={() => setShowThinking(!showThinking)}
                    class={`px-2 py-1 text-xs rounded-md transition-colors ${
                      showThinking 
                        ? 'bg-accent-amber/20 text-accent-amber border border-accent-amber/30' 
                        : 'bg-dark-800 text-dark-400 border border-dark-700 hover:text-dark-200'
                    }`}
                    title="Toggle thinking visibility"
                  >
                    💭 Think
                  </button>
                  <button
                    onClick={() => setShowToolCalls(!showToolCalls)}
                    class={`px-2 py-1 text-xs rounded-md transition-colors ${
                      showToolCalls 
                        ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30' 
                        : 'bg-dark-800 text-dark-400 border border-dark-700 hover:text-dark-200'
                    }`}
                    title="Toggle tool calls visibility"
                  >
                    🔧 Tools
                  </button>
                </div>
                {currentSession && (
                  <span class="text-xs text-dark-500 font-body">
                    Session: {currentSession.session_id.slice(0, 8)}...
                  </span>
                )}
              </div>
            </div>
            
            {/* Error banner */}
            {sendError && (
              <div class="mx-4 mt-2 px-4 py-2 bg-accent-rose/20 border border-accent-rose/30 rounded-md flex items-center justify-between">
                <span class="text-accent-rose text-sm">{sendError}</span>
                <button 
                  onClick={() => setSendError(null)}
                  class="text-accent-rose hover:text-white"
                >
                  ✕
                </button>
              </div>
            )}
            
            <ChatInterface
              messages={messages}
              isLoading={isSending}
              agent={sessionAgent}
              sessionId={currentSession?.session_id || null}
              showThinking={showThinking}
              showToolCalls={showToolCalls}
            />
            
            {currentSession && (
              <MessageInput
                onSendMessage={handleSendMessage}
                disabled={isSending}
                agentColor={sessionAgent?.id || 'coder'}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
};
