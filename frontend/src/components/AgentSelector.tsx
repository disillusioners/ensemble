import { FunctionalComponent } from 'preact';
import { useState } from 'preact/hooks';
import type { Agent } from '../types';
import type { AgentCreate } from '../utils/api';
import { AddAgentModal } from './AddAgentModal';

interface AgentSelectorProps {
  agents: Agent[];
  selectedAgent: Agent | null;
  onSelect: (agent: Agent) => void;
  onCreateSession: () => void;
  onContinueSession: (sessionId: string) => void;
  onAddAgent: (agent: AgentCreate) => Promise<Agent | null>;
  onDeleteAgent: (agentId: string) => Promise<void>;
  hasSessions: boolean;
  isLoading?: boolean;
}

// Color mapping for known accent colors
const colorMap: Record<string, string> = {
  'accent-amber': '#f59e0b',
  'accent-cyan': '#10a7f7',
  'accent-violet': '#8b5cf6',
  'accent-emerald': '#10b981',
  'accent-rose': '#f43f5e',
  'accent-blue': '#3b82f6',
};

const getAgentColor = (agent: Agent): string => {
  return colorMap[agent.color] || agent.color || '#10a7f7';
};

export const AgentSelector: FunctionalComponent<AgentSelectorProps> = ({
  agents,
  selectedAgent,
  onSelect,
  onCreateSession,
  onContinueSession,
  onAddAgent,
  onDeleteAgent,
  hasSessions,
  isLoading,
}) => {
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const activeColor = selectedAgent ? getAgentColor(selectedAgent) : '#10a7f7';

  return (
    <div class="animate-fade-in">
      <div class="text-center mb-8">
        <h1 class="font-display text-4xl font-bold text-dark-50 mb-2 tracking-tight">
          Select an Agent
        </h1>
        <p class="text-dark-400 font-body">
          Choose an agent to start a new conversation
        </p>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {agents.map((agent) => {
          const isSelected = selectedAgent?.id === agent.id;
          const color = getAgentColor(agent);
          
          return (
            <button
              key={agent.id}
              onClick={() => onSelect(agent)}
              class={`
                group relative p-6 rounded-2xl border-2 transition-all duration-300
                text-left font-body
                ${isSelected ? 'bg-dark-800' : 'bg-dark-800/50 border-dark-700 hover:border-dark-600 hover:bg-dark-800'}
              `}
              style={{
                borderColor: isSelected ? color : undefined,
                backgroundColor: isSelected ? `${color}15` : undefined,
              }}
            >
              <div class="flex items-center gap-3 mb-3">
                <span class="text-3xl">{agent.icon}</span>
                <span 
                  class="font-display text-xl font-semibold"
                  style={{ color }}
                >
                  {agent.name}
                </span>
              </div>
              <p class="text-dark-400 text-sm leading-relaxed">
                {agent.description}
              </p>
              
              {/* Selection indicator */}
              {isSelected && (
                <div 
                  class="absolute top-3 right-3 w-3 h-3 rounded-full animate-pulse-subtle" 
                  style={{ backgroundColor: color }}
                />
              )}

              {/* Delete button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`Delete agent "${agent.name}"? This will move it to trash.`)) {
                    onDeleteAgent(agent.id);
                  }
                }}
                class="absolute bottom-3 right-3 p-1.5 rounded-lg bg-dark-900/80 text-dark-500 
                       opacity-0 group-hover:opacity-100 transition-opacity
                       hover:bg-accent-rose/20 hover:text-accent-rose"
                title="Delete agent"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </button>
          );
        })}
        
        {/* Add new agent card */}
        <button
          onClick={() => setIsAddModalOpen(true)}
          class="p-6 rounded-2xl border-2 border-dashed border-dark-600 bg-dark-800/30
                 hover:border-dark-500 hover:bg-dark-800/50 transition-all duration-300
                 flex flex-col items-center justify-center gap-2 text-dark-400
                 hover:text-dark-300"
        >
          <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          <span class="font-display font-medium">Add New Agent</span>
        </button>
      </div>

      <div class="flex justify-center gap-4">
        {hasSessions && (
          <button
            onClick={() => onContinueSession('latest')}
            class="px-6 py-4 rounded-xl font-display font-semibold text-lg
                   bg-dark-700 text-dark-200 border border-dark-600
                   hover:bg-dark-600 hover:border-dark-500 transition-all duration-300"
          >
            Continue Session
          </button>
        )}
        <button
          onClick={onCreateSession}
          disabled={!selectedAgent || isLoading}
          class={`
            px-8 py-4 rounded-xl font-display font-semibold text-lg
            transition-all duration-300 transform
            ${selectedAgent ? 'hover:scale-105 hover:shadow-lg' : 'bg-dark-700 text-dark-400 cursor-not-allowed'}
          `}
          style={{
            backgroundColor: selectedAgent ? activeColor : undefined,
            boxShadow: selectedAgent ? `0 10px 40px -10px ${activeColor}50` : undefined,
          }}
        >
          {isLoading ? (
            <span class="flex items-center gap-2">
              <svg class="animate-spin h-5 w-5" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Creating Session...
            </span>
          ) : (
            'Start New Chat'
          )}
        </button>
      </div>

      {/* Add Agent Modal */}
      <AddAgentModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onAdd={onAddAgent}
      />
    </div>
  );
};
