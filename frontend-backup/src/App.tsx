import { FunctionalComponent } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import { Routes, Route, useNavigate, useLocation, Link } from 'react-router-dom';
import { AgentSelector } from './components/AgentSelector';
import { AgentSwitcher } from './components/AgentSwitcher';
import { SessionList } from './components/SessionList';
import { ChatInterface } from './components/ChatInterface';
import { MessageInput } from './components/MessageInput';
import { useSSE } from './hooks/useSSE';
import { api } from './utils/api';
import type { AgentCreate } from './utils/api';
import type { SessionInfo, Message, Agent } from './types';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-session-agent';

// Home page component - Agent selection
interface HomeProps {
  agents: Agent[];
  sessions: SessionInfo[];
  nextSessionAgent: Agent | null;
  onSetNextSessionAgent: (agent: Agent) => void;
  onCreateSession: (agent: Agent) => Promise<void>;
  onContinueSession: (sessionId: string) => void;
  onAddAgent: (agent: AgentCreate) => Promise<Agent | null>;
  onDeleteAgent: (agentId: string) => Promise<void>;
  onStartMother: () => void;
  isLoading: boolean;
}

const Home: FunctionalComponent<HomeProps> = ({
  agents,
  sessions,
  nextSessionAgent,
  onSetNextSessionAgent,
  onCreateSession,
  onContinueSession,
  onAddAgent,
  onDeleteAgent,
  onStartMother,
  isLoading,
}) => {
  return (
    <div class="flex-1 flex items-center justify-center p-8 overflow-y-auto">
      <div class="max-w-4xl w-full">
        <AgentSelector
          agents={agents}
          selectedAgent={nextSessionAgent}
          onSelect={onSetNextSessionAgent}
          onCreateSession={() => nextSessionAgent && onCreateSession(nextSessionAgent)}
          onContinueSession={onContinueSession}
          onAddAgent={onAddAgent}
          onDeleteAgent={onDeleteAgent}
          onStartMother={onStartMother}
          hasSessions={sessions.length > 0}
          isLoading={isLoading}
        />
      </div>
    </div>
  );
};

// Chat page component
interface ChatProps {
  agents: Agent[];
  sessions: SessionInfo[];
  currentSession: SessionInfo | null;
  messages: Message[];
  nextSessionAgent: Agent | null;
  onDeleteSession: (sessionId: string) => void;
  onNewSession: () => void;
  onSetNextSessionAgent: (agent: Agent) => void;
  onSendMessage: (content: string) => Promise<void>;
  isSending: boolean;
  sendError: string | null;
  onClearError: () => void;
  showThinking: boolean;
  showToolCalls: boolean;
  onToggleThinking: () => void;
  onToggleToolCalls: () => void;
}

const Chat: FunctionalComponent<ChatProps> = ({
  agents,
  sessions,
  currentSession,
  messages,
  nextSessionAgent,
  onDeleteSession,
  onNewSession,
  onSetNextSessionAgent,
  onSendMessage,
  isSending,
  sendError,
  onClearError,
  showThinking,
  showToolCalls,
  onToggleThinking,
  onToggleToolCalls,
}) => {
  const navigate = useNavigate();
  
  // Get current session's agent
  const sessionAgent = currentSession 
    ? agents.find(a => currentSession.agent_dir.includes(a.id)) || null
    : null;

  const handleBackToHome = () => {
    navigate('/');
  };

  return (
    <div class="flex-1 flex overflow-hidden">
        {/* Session sidebar */}
        <div class="w-72 flex-shrink-0">
          <SessionList
            agents={agents}
            sessions={sessions}
            currentSessionId={currentSession?.session_id || null}
            onDeleteSession={onDeleteSession}
            onNewSession={onNewSession}
          />
        </div>
      
      {/* Chat area */}
      <div class="flex-1 flex flex-col">
        {/* Chat header with agent info */}
        <div class="h-14 flex items-center justify-between px-4 border-b border-dark-700 bg-dark-900 flex-shrink-0">
          <div class="flex items-center gap-3">
            {/* Back to home button */}
            <button
              onClick={handleBackToHome}
              class="p-2 rounded-lg text-dark-400 hover:text-dark-200 hover:bg-dark-800 transition-colors"
              title="Back to home"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
            </button>
            
            <AgentSwitcher
              agents={agents}
              selectedAgent={nextSessionAgent}
              onAgentChange={onSetNextSessionAgent}
            />
          </div>
          
          {/* Breadcrumb / Session info */}
          <div class="flex items-center gap-3">
            <Link 
              to="/"
              class="text-xs text-dark-500 hover:text-dark-300 transition-colors"
            >
              ← Home
            </Link>
            {/* Toggle buttons */}
            <div class="flex items-center gap-1.5">
              <button
                onClick={onToggleThinking}
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
                onClick={onToggleToolCalls}
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
              <span class="text-xs text-dark-500 font-mono">
                {currentSession.session_id.slice(0, 8)}...
              </span>
            )}
          </div>
        </div>
        
        {/* Error banner */}
        {sendError && (
          <div class="mx-4 mt-2 px-4 py-2 bg-accent-rose/20 border border-accent-rose/30 rounded-md flex items-center justify-between">
            <span class="text-accent-rose text-sm">{sendError}</span>
            <button 
              onClick={onClearError}
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
            onSendMessage={onSendMessage}
            disabled={isSending}
            agentColor={sessionAgent?.id || 'coder'}
          />
        )}
      </div>
    </div>
  );
};

export const App: FunctionalComponent = () => {
  const navigate = useNavigate();
  const location = useLocation();
  
  // Extract sessionId from URL pathname (more reliable than useParams with Preact)
  const sessionId = (() => {
    const match = location.pathname.match(/\/sessions\/(.+)$/);
    return match ? match[1] : null;
  })();
  
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionInfo | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [nextSessionAgent, setNextSessionAgent] = useState<Agent | null>(null);
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

  // Load health status, agents, and sessions on mount
  useEffect(() => {
    const init = async () => {
      try {
        const healthData = await api.health();
        setHealth(healthData);
        
        const agentsData = await api.listAgents();
        setAgents(agentsData.agents);
        
        // Initialize nextSessionAgent from localStorage after agents are loaded
        const saved = localStorage.getItem(NEXT_AGENT_STORAGE_KEY);
        if (saved && agentsData.agents.length > 0) {
          const savedAgent = agentsData.agents.find(a => a.id === saved);
          if (savedAgent) {
            setNextSessionAgent(savedAgent);
          }
        }
        
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

  // Handle session ID from URL - navigate to specific session
  useEffect(() => {
    if (sessionId && sessions.length > 0) {
      const session = sessions.find(s => s.session_id === sessionId);
      if (session) {
        setCurrentSession(session);
      } else {
        // Session not found - navigate to home
        console.warn('Session not found:', sessionId);
        navigate('/');
      }
    }
  }, [sessionId, sessions, navigate]);

  // Load messages when sessionId changes (direct trigger, not via currentSession)
  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }

    const loadMessages = async () => {
      try {
        console.log('Loading messages for session:', sessionId);
        const msgs = await api.getMessages(sessionId);
        console.log('Loaded messages:', msgs.length);
        setMessages(msgs);
      } catch (err) {
        console.error('Failed to load messages:', err);
      }
    };

    loadMessages();
  }, [sessionId]);

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
      // Navigate to the new session
      navigate(`/sessions/${session.session_id}`);
    } catch (err) {
      console.error('Failed to create session:', err);
      alert(`Failed to create session: ${err}`);
    } finally {
      setIsLoading(false);
    }
  }, [navigate]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    try {
      await api.deleteSession(sessionId);
      setSessions(prev => prev.filter(s => s.session_id !== sessionId));
      
      if (currentSession?.session_id === sessionId) {
        setCurrentSession(null);
        navigate('/');
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, [currentSession, navigate]);

  const handleNewSession = useCallback(async () => {
    if (!nextSessionAgent) {
      // No agent selected, go to home/selector
      navigate('/');
      return;
    }
    
    setCurrentSession(null);
    setMessages([]);
    await handleCreateSession(nextSessionAgent);
  }, [nextSessionAgent, handleCreateSession, navigate]);

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

  const handleClearError = useCallback(() => {
    setSendError(null);
  }, []);

  const handleToggleThinking = useCallback(() => {
    setShowThinking(prev => !prev);
  }, []);

  const handleToggleToolCalls = useCallback(() => {
    setShowToolCalls(prev => !prev);
  }, []);

  const handleAddAgent = useCallback(async (agentCreate: AgentCreate): Promise<Agent | null> => {
    try {
      const newAgent = await api.createAgent(agentCreate);
      // Add to agents list
      setAgents(prev => [...prev, newAgent]);
      return newAgent;
    } catch (err) {
      console.error('Failed to create agent:', err);
      throw err;
    }
  }, []);

  const handleDeleteAgent = useCallback(async (agentId: string) => {
    try {
      await api.deleteAgent(agentId);
      // Remove from agents list
      setAgents(prev => prev.filter(a => a.id !== agentId));
      // Clear selection if deleted agent was selected
      if (nextSessionAgent?.id === agentId) {
        setNextSessionAgent(null);
        localStorage.removeItem(NEXT_AGENT_STORAGE_KEY);
      }
    } catch (err) {
      console.error('Failed to delete agent:', err);
      alert(`Failed to delete agent: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }, [nextSessionAgent]);

  const handleStartMother = useCallback(async () => {
    setIsLoading(true);
    try {
      // Create a session with the _mother agent
      const agentPath = './agents/_mother';
      const session = await api.createSession(agentPath);
      
      setCurrentSession(session);
      setSessions(prev => [session, ...prev]);
      navigate(`/sessions/${session.session_id}`);
    } catch (err) {
      console.error('Failed to start Mother session:', err);
      alert(`Failed to start Mother session: ${err}`);
    } finally {
      setIsLoading(false);
    }
  }, [navigate]);

  return (
    <div class="h-screen flex flex-col bg-dark-950 font-body text-dark-100 overflow-hidden">
      {/* Header */}
      <header class="h-14 flex items-center justify-between px-6 border-b border-dark-700 bg-dark-900 flex-shrink-0">
        <div class="flex items-center gap-3">
          <Link to="/" class="flex items-center gap-3 hover:opacity-80 transition-opacity">
            <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-violet flex items-center justify-center">
              <span class="text-white font-bold text-sm">AC</span>
            </div>
            <h1 class="font-display text-lg font-semibold text-dark-50">Agents Ensemble</h1>
          </Link>
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

      {/* Main content with routes */}
      <Routes>
        <Route 
          path="/" 
          element={
            <Home
              agents={agents}
              sessions={sessions}
              nextSessionAgent={nextSessionAgent}
              onSetNextSessionAgent={handleSetNextSessionAgent}
              onCreateSession={handleCreateSession}
              onContinueSession={(sessionId) => {
                if (sessionId === 'latest' && sessions.length > 0) {
                  navigate(`/sessions/${sessions[0].session_id}`);
                } else if (sessionId !== 'latest') {
                  navigate(`/sessions/${sessionId}`);
                }
              }}
              onAddAgent={handleAddAgent}
              onDeleteAgent={handleDeleteAgent}
              onStartMother={handleStartMother}
              isLoading={isLoading}
            />
          } 
        />
        <Route 
          path="/sessions/:sessionId" 
          element={
            <Chat
              agents={agents}
              sessions={sessions}
              currentSession={currentSession}
              messages={messages}
              nextSessionAgent={nextSessionAgent}
              onDeleteSession={handleDeleteSession}
              onNewSession={handleNewSession}
              onSetNextSessionAgent={handleSetNextSessionAgent}
              onSendMessage={handleSendMessage}
              isSending={isSending}
              sendError={sendError}
              onClearError={handleClearError}
              showThinking={showThinking}
              showToolCalls={showToolCalls}
              onToggleThinking={handleToggleThinking}
              onToggleToolCalls={handleToggleToolCalls}
            />
          } 
        />
      </Routes>
    </div>
  );
};
