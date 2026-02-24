import { FunctionalComponent } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import { AgentSelector } from './components/AgentSelector';
import { SessionList } from './components/SessionList';
import { ChatInterface } from './components/ChatInterface';
import { MessageInput } from './components/MessageInput';
import { useSSE } from './hooks/useSSE';
import { api } from './utils/api';
import type { SessionInfo, Message, Agent } from './types';
import { AVAILABLE_AGENTS } from './types';

type AppView = 'select-agent' | 'chat';

export const App: FunctionalComponent = () => {
  const [view, setView] = useState<AppView>('select-agent');
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionInfo | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [health, setHealth] = useState<{ status: string; uptime_seconds: number; version: string } | null>(null);

  // SSE for real-time updates
  const { isStreaming, latestMessage } = useSSE(currentSession?.session_id || null);

  // Handle incoming SSE messages
  useEffect(() => {
    if (latestMessage && latestMessage.role === 'assistant') {
      setMessages(prev => {
        // Check if this is an update to an existing message or a new one
        const existingIndex = prev.findIndex(m => m.message_id === latestMessage.message_id);
        if (existingIndex >= 0) {
          // Update existing message
          const updated = [...prev];
          updated[existingIndex] = latestMessage;
          return updated;
        } else {
          // Add new message
          return [...prev, latestMessage];
        }
      });
      setIsSending(false);
    }
  }, [latestMessage]);

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

  const handleSelectAgent = useCallback((agent: Agent) => {
    setSelectedAgent(agent);
  }, []);

  const handleCreateSession = useCallback(async () => {
    if (!selectedAgent) return;

    setIsLoading(true);
    try {
      // Convert agent name to path format expected by backend
      const agentPath = `./agents/${selectedAgent.id}`;
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
  }, [selectedAgent]);

  const handleSelectSession = useCallback((session: SessionInfo) => {
    setCurrentSession(session);
    
    // Find agent based on session's agent_dir
    const agentId = session.agent_dir.split('/').pop();
    const agent = AVAILABLE_AGENTS.find(a => a.id === agentId);
    if (agent) {
      setSelectedAgent(agent);
    }
    
    setView('chat');
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

  const handleNewSession = useCallback(() => {
    setCurrentSession(null);
    setMessages([]);
    setView('select-agent');
  }, []);

  const handleSendMessage = useCallback(async (content: string) => {
    if (!currentSession) return;

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
      
      // Add assistant response
      const assistantMessage: Message = {
        type: 'message',
        message_id: response.message_id,
        role: 'assistant',
        content: response.content || '',
        created_at: response.created_at,
      };
      setMessages(prev => [...prev, assistantMessage]);
    } catch (err) {
      console.error('Failed to send message:', err);
      // Remove the temporary user message on error
      setMessages(prev => prev.filter(m => m.message_id !== userMessage.message_id));
    } finally {
      setIsSending(false);
    }
  }, [currentSession]);

  // Get the current agent for the chat view
  const currentAgent = selectedAgent || (currentSession 
    ? AVAILABLE_AGENTS.find(a => currentSession.agent_dir.includes(a.id)) 
    : null);

  return (
    <div class="h-screen flex flex-col bg-dark-950 font-body text-dark-100 overflow-hidden">
      {/* Header */}
      <header class="h-14 flex items-center justify-between px-6 border-b border-dark-700 bg-dark-900 flex-shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet flex items-center justify-center">
            <span class="text-white font-bold text-sm">AC</span>
          </div>
          <h1 class="font-display text-lg font-semibold text-dark-50">Auto-Code</h1>
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
              selectedAgent={selectedAgent}
              onSelect={handleSelectAgent}
              onCreateSession={handleCreateSession}
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
            <ChatInterface
              messages={messages}
              isLoading={isSending}
              agent={currentAgent}
              sessionId={currentSession?.session_id || null}
            />
            
            {currentSession && (
              <MessageInput
                onSendMessage={handleSendMessage}
                disabled={isSending}
                agentColor={currentAgent?.id || 'coder'}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
};
