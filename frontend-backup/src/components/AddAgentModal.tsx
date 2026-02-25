import { FunctionalComponent } from 'preact';
import { useState } from 'preact/hooks';
import type { Agent } from '../types';
import type { AgentCreate } from '../utils/api';

interface AddAgentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAdd: (agent: AgentCreate) => Promise<Agent | null>;
}

const ICON_OPTIONS = ['🤖', '🚀', '💡', '⚡', '🔧', '📝', '🎯', '🔍', '📊', '🎨', '🛠️', '🌟'];
const COLOR_OPTIONS = [
  { value: 'accent-amber', label: 'Amber', hex: '#f59e0b' },
  { value: 'accent-cyan', label: 'Cyan', hex: '#10a7f7' },
  { value: 'accent-violet', label: 'Violet', hex: '#8b5cf6' },
  { value: 'accent-emerald', label: 'Emerald', hex: '#10b981' },
  { value: 'accent-rose', label: 'Rose', hex: '#f43f5e' },
  { value: 'accent-blue', label: 'Blue', hex: '#3b82f6' },
];

export const AddAgentModal: FunctionalComponent<AddAgentModalProps> = ({
  isOpen,
  onClose,
  onAdd,
}) => {
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [icon, setIcon] = useState('🤖');
  const [color, setColor] = useState('accent-cyan');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resetForm = () => {
    setId('');
    setName('');
    setDescription('');
    setIcon('🤖');
    setColor('accent-cyan');
    setError(null);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleSubmit = async (e: Event) => {
    e.preventDefault();
    
    if (!id.trim() || !name.trim()) {
      setError('ID and Name are required');
      return;
    }

    // Validate ID format
    if (!/^[a-z0-9_-]+$/.test(id)) {
      setError('ID must be lowercase letters, numbers, hyphens, or underscores');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const result = await onAdd({
        id: id.trim().toLowerCase(),
        name: name.trim(),
        description: description.trim(),
        icon,
        color,
      });
      
      if (result) {
        handleClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create agent');
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div class="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div 
        class="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleClose}
      />
      
      {/* Modal */}
      <div class="relative bg-dark-800 border border-dark-700 rounded-2xl w-full max-w-md mx-4 shadow-2xl animate-fade-in">
        {/* Header */}
        <div class="flex items-center justify-between p-4 border-b border-dark-700">
          <h2 class="font-display text-xl font-semibold text-dark-50">Add New Agent</h2>
          <button
            onClick={handleClose}
            class="p-2 rounded-lg text-dark-400 hover:text-dark-200 hover:bg-dark-700 transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} class="p-4 space-y-4">
          {/* Error */}
          {error && (
            <div class="px-3 py-2 bg-accent-rose/20 border border-accent-rose/30 rounded-lg text-accent-rose text-sm">
              {error}
            </div>
          )}

          {/* ID */}
          <div>
            <label class="block text-sm font-medium text-dark-300 mb-1">
              ID <span class="text-accent-rose">*</span>
            </label>
            <input
              type="text"
              value={id}
              onInput={(e) => setId((e.target as HTMLInputElement).value)}
              placeholder="my-agent"
              class="w-full px-3 py-2 bg-dark-900 border border-dark-700 rounded-lg text-dark-100 placeholder-dark-500 focus:outline-none focus:border-accent-cyan transition-colors"
            />
            <p class="text-xs text-dark-500 mt-1">Lowercase letters, numbers, hyphens, underscores</p>
          </div>

          {/* Name */}
          <div>
            <label class="block text-sm font-medium text-dark-300 mb-1">
              Name <span class="text-accent-rose">*</span>
            </label>
            <input
              type="text"
              value={name}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
              placeholder="My Agent"
              class="w-full px-3 py-2 bg-dark-900 border border-dark-700 rounded-lg text-dark-100 placeholder-dark-500 focus:outline-none focus:border-accent-cyan transition-colors"
            />
          </div>

          {/* Description */}
          <div>
            <label class="block text-sm font-medium text-dark-300 mb-1">Description</label>
            <textarea
              value={description}
              onInput={(e) => setDescription((e.target as HTMLTextAreaElement).value)}
              placeholder="What does this agent do?"
              rows={2}
              class="w-full px-3 py-2 bg-dark-900 border border-dark-700 rounded-lg text-dark-100 placeholder-dark-500 focus:outline-none focus:border-accent-cyan transition-colors resize-none"
            />
          </div>

          {/* Icon */}
          <div>
            <label class="block text-sm font-medium text-dark-300 mb-1">Icon</label>
            <div class="flex flex-wrap gap-2">
              {ICON_OPTIONS.map((iconOption) => (
                <button
                  key={iconOption}
                  type="button"
                  onClick={() => setIcon(iconOption)}
                  class={`w-10 h-10 rounded-lg text-xl flex items-center justify-center transition-all ${
                    icon === iconOption
                      ? 'bg-accent-cyan/20 border-2 border-accent-cyan'
                      : 'bg-dark-900 border border-dark-700 hover:border-dark-600'
                  }`}
                >
                  {iconOption}
                </button>
              ))}
            </div>
          </div>

          {/* Color */}
          <div>
            <label class="block text-sm font-medium text-dark-300 mb-1">Color</label>
            <div class="flex flex-wrap gap-2">
              {COLOR_OPTIONS.map((colorOption) => (
                <button
                  key={colorOption.value}
                  type="button"
                  onClick={() => setColor(colorOption.value)}
                  class={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                    color === colorOption.value
                      ? 'ring-2 ring-offset-2 ring-offset-dark-800'
                      : ''
                  }`}
                  style={{
                    backgroundColor: `${colorOption.hex}20`,
                    color: colorOption.hex,
                    ringColor: color === colorOption.value ? colorOption.hex : undefined,
                  }}
                >
                  {colorOption.label}
                </button>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div class="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={handleClose}
              class="px-4 py-2 rounded-lg font-medium text-dark-300 bg-dark-700 hover:bg-dark-600 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading || !id.trim() || !name.trim()}
              class="px-4 py-2 rounded-lg font-medium text-white bg-accent-cyan hover:bg-accent-cyan/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {isLoading && (
                <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              )}
              Create Agent
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
