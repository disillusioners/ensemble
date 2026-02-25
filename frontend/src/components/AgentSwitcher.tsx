import { FunctionalComponent } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import type { Agent } from '../types';

interface AgentSwitcherProps {
  agents: Agent[];
  selectedAgent: Agent | null;
  onAgentChange: (agent: Agent) => void;
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

export const AgentSwitcher: FunctionalComponent<AgentSwitcherProps> = ({
  agents,
  selectedAgent,
  onAgentChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const activeColor = selectedAgent ? getAgentColor(selectedAgent) : '#10a7f7';

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelectAgent = (agent: Agent) => {
    onAgentChange(agent);
    setIsOpen(false);
  };

  return (
    <div class="relative" ref={dropdownRef}>
      {/* Dropdown trigger button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        class={`
          flex items-center gap-2 px-3 py-2 rounded-lg
          bg-dark-800 border border-dark-700
          hover:border-dark-600
          transition-all duration-200
          focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-dark-900
        `}
        style={{
          '--tw-ring-color': `${activeColor}50`,
        } as any}
      >
        {/* Agent icon */}
        <div
          class="w-6 h-6 rounded-md flex items-center justify-center text-sm"
          style={{ backgroundColor: `${activeColor}20` }}
        >
          {selectedAgent?.icon || '🤖'}
        </div>
        
        {/* Agent name */}
        <span class="font-body text-sm font-medium text-dark-200">
          {selectedAgent?.name || 'Select Agent'}
        </span>
        
        {/* Chevron icon */}
        <svg
          class={`w-4 h-4 text-dark-500 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <div
          class="
            absolute top-full left-0 mt-1 w-56
            bg-dark-800 border border-dark-700 rounded-lg
            shadow-xl shadow-black/30 overflow-hidden
            z-50 animate-fade-in
          "
        >
          <div class="py-1">
            {agents.map((agent) => {
              const isSelected = selectedAgent?.id === agent.id;
              const color = getAgentColor(agent);

              return (
              <button
                key={agent.id}
                onClick={() => handleSelectAgent(agent)}
                class={`
                  w-full flex items-center gap-3 px-3 py-2.5
                  text-left font-body text-sm
                  transition-colors duration-150
                  ${isSelected 
                    ? 'bg-dark-700 text-dark-100' 
                    : 'text-dark-300 hover:bg-dark-700 hover:text-dark-100'
                  }
                `}
              >
                {/* Agent icon */}
                <div
                  class="w-7 h-7 rounded-md flex items-center justify-center"
                  style={{ backgroundColor: `${color}20` }}
                >
                  <span class="text-base">{agent.icon}</span>
                </div>
                
                {/* Agent info */}
                <div class="flex-1 min-w-0">
                  <div 
                    class="font-medium truncate"
                    style={{ color: isSelected ? color : undefined }}
                  >
                    {agent.name}
                  </div>
                  <div class="text-xs text-dark-500 truncate">
                    {agent.description}
                  </div>
                </div>

                {/* Selection indicator */}
                {isSelected && (
                  <svg
                    class="w-4 h-4 flex-shrink-0"
                    style={{ color }}
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      fill-rule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clip-rule="evenodd"
                    />
                  </svg>
                )}
              </button>
            );
            })}
          </div>

          {/* Footer hint */}
          <div class="px-3 py-2 bg-dark-850 border-t border-dark-700">
            <p class="text-xs text-dark-500">
              Switch to change agent for next session
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
