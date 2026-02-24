import { FunctionalComponent } from 'preact';
import type { SessionInfo, Agent } from '../types';
import { AVAILABLE_AGENTS } from '../types';

interface SessionListProps {
  sessions: SessionInfo[];
  currentSessionId: string | null;
  onSelectSession: (session: SessionInfo) => void;
  onDeleteSession: (sessionId: string) => void;
  onNewSession: () => void;
}

function getAgentInfo(agentDir: string): Agent | undefined {
  const agentId = agentDir.split('/').pop() || agentDir;
  return AVAILABLE_AGENTS.find(a => a.id === agentId);
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, { bg: string; text: string }> = {
    idle: { bg: '#4d4d5c', text: '#c5c5d2' },
    running: { bg: '#10b98120', text: '#10b981' },
    waiting: { bg: '#f59e0b20', text: '#f59e0b' },
    error: { bg: '#f43f5e20', text: '#f43f5e' },
    terminated: { bg: '#343541', text: '#6e6e80' },
  };
  
  const style = colors[status] || colors.idle;
  
  return (
    <span 
      class="px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ backgroundColor: style.bg, color: style.text }}
    >
      {status}
    </span>
  );
}

export const SessionList: FunctionalComponent<SessionListProps> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onDeleteSession,
  onNewSession,
}) => {
  return (
    <div class="flex flex-col h-full bg-dark-900 border-r border-dark-700">
      {/* Header */}
      <div class="p-4 border-b border-dark-700">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-display text-lg font-semibold text-dark-50">Sessions</h2>
          <button
            onClick={onNewSession}
            class="p-2 rounded-lg bg-dark-700 text-dark-300 hover:bg-dark-600 transition-colors"
            title="New Session"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </div>
        <p class="text-dark-500 text-sm font-body">
          {sessions.length} active session{sessions.length !== 1 ? 's' : ''}
        </p>
      </div>

      {/* Session List */}
      <div class="flex-1 overflow-y-auto">
        {sessions.length === 0 ? (
          <div class="p-4 text-center">
            <p class="text-dark-500 font-body text-sm">No active sessions</p>
            <button
              onClick={onNewSession}
              class="mt-3 text-accent-cyan text-sm font-medium hover:underline"
            >
              Start a new chat
            </button>
          </div>
        ) : (
          <div class="p-2 space-y-1">
            {sessions.map((session) => {
              const agent = getAgentInfo(session.agent_dir);
              const isActive = session.session_id === currentSessionId;
              
              return (
                <button
                  key={session.session_id}
                  onClick={() => onSelectSession(session)}
                  class={`
                    w-full p-3 rounded-xl text-left transition-all duration-200
                    group relative
                    ${isActive ? 'bg-dark-800' : 'hover:bg-dark-800/50'}
                  `}
                  style={{
                    border: isActive ? '1px solid #4d4d5c' : '1px solid transparent',
                  }}
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="flex items-center gap-2 min-w-0">
                      <span class="text-lg flex-shrink-0">{agent?.icon || '🤖'}</span>
                      <div class="min-w-0">
                        <p class={`font-medium truncate ${isActive ? 'text-dark-50' : 'text-dark-300'}`}>
                          {agent?.name || 'Agent'}
                        </p>
                        <p class="text-dark-500 text-xs font-mono truncate">
                          {session.session_id.slice(0, 12)}...
                        </p>
                      </div>
                    </div>
                    
                    <div class="flex flex-col items-end gap-1 flex-shrink-0">
                      <StatusBadge status={session.status} />
                      <span class="text-dark-600 text-xs">
                        {formatDate(session.created_at)}
                      </span>
                    </div>
                  </div>
                  
                  {/* Delete button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm('Delete this session?')) {
                        onDeleteSession(session.session_id);
                      }
                    }}
                    class="absolute top-2 right-2 p-1.5 rounded-lg bg-dark-900/80 text-dark-500 
                           opacity-0 group-hover:opacity-100 transition-opacity
                           hover:bg-accent-rose/20 hover:text-accent-rose"
                    title="Delete session"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
