# frontend/src/app/components/

## Responsibility
A component library for the Auto-Code agent chat application. Provides a complete UI for agent management, chat interactions, and session handling using Angular 17+ standalone components with signals-based state management.

## Design Patterns

### Angular Signals
All components use Angular signals for reactive state management:
- `signal<T>()` for single values (e.g., `isOpen`, `message`, `expandedSessions`)
- `computed()` for derived state (e.g., `sessionTree`, `activeColor`)
- `effect()` for side effects (e.g., auto-scroll trigger in ChatInterface)

### Standalone Components
All components are Angular standalone components (Angular 17+):
- No NgModule required
- Explicit imports in `@Component.imports` array
- Self-contained with template, styles, and logic

### Material Design
Components integrate Angular Material modules:
- `MatDialog` for AddAgentModal
- `MatMenu` for AgentSwitcher dropdown
- `MatCard`, `MatButton`, `MatIcon`, `MatList` for various UI elements

### Event-Driven Communication
- Parent components control child components via `@Input()` bindings
- Child components communicate via `@Output()` EventEmitters
- Example: AgentSelector emits `selectAgent` → parent handles → updates `selectedAgent` input

### Color Mapping Pattern
Shared color mapping for agent identification:
```typescript
const colorMap: Record<string, string> = {
  'accent-amber': '#f59e0b',
  'accent-cyan': '#10a7f7',
  'accent-violet': '#8b5cf6',
  'accent-emerald': '#10b981',
  'accent-rose': '#f43f5e',
  'accent-blue': '#3b82f6',
};
```

## Component Catalog

| Component | Purpose | Key Inputs/Outputs |
|-----------|---------|-------------------|
| **AddAgentModal** | Dialog for creating new AI agents with ID, name, description, icon, and color | Inputs: `data` (injected via MAT_DIALOG_DATA) <br> Outputs: Returns `AgentCreate` via MatDialogRef.close() |
| **AgentSelector** | Card-based agent selection UI with session handling | Inputs: `agents`, `selectedAgent`, `hasSessions`, `isLoading` <br> Outputs: `selectAgent`, `createSession`, `continueSession`, `addAgent`, `deleteAgent`, `startMother` |
| **AgentSwitcher** | Dropdown menu for quick agent switching | Inputs: `agents`, `selectedAgent` <br> Outputs: `agentChange` |
| **ChatInterface** | Message display area with auto-scroll, tool call visualization | Inputs: `messages`, `isLoading`, `agent`, `sessionId`, `showThinking`, `showToolCalls` <br> Outputs: None (pure presentation) |
| **MessageInput** | Text input for sending chat messages with auto-resize | Inputs: `disabled`, `agentColor` <br> Outputs: `sendMessage` (emits message string) |
| **SessionList** | Tree-view of chat sessions with status indicators | Inputs: `agents`, `sessions`, `currentSessionId`, `selectedAgent` <br> Outputs: `deleteSession`, `newSession`, `agentChange` |

## Data & Control Flow

### Agent Selection Flow
```
AgentSelectorComponent
    ├── Displays available agents as cards
    ├── Emits selectAgent(Agent) → Parent
    ├── Opens AddAgentModal via MatDialog
    │       └── Returns AgentCreate → emits addAgent(AgentCreate)
    └── Handles session creation/continuation
```

### Agent Switching Flow
```
SessionListComponent (contains AgentSwitcher)
    └── AgentSwitcherComponent
            ├── Displays dropdown with all agents
            ├── Emits agentChange(Agent) → Parent
            └── Closes on outside click (HostListener)
```

### Chat Interaction Flow
```
Parent Component
    ├── ChatInterfaceComponent (displays messages)
    │       └── Receives: messages, isLoading, agent, sessionId
    └── MessageInputComponent (sends messages)
            ├── Emits sendMessage(string) → Parent
            └── Parent sends to backend, updates messages
```

### Session Management Flow
```
SessionListComponent
    ├── Builds session tree from flat list (computed)
    ├── Displays sessions with expand/collapse
    ├── Emits deleteSession(id), newSession(), agentChange(agent)
    └── Contains AgentSwitcher for agent switching
```

## Integration

### Internal Dependencies
- `../../models`: `Agent`, `AgentCreate`, `Message`, `SessionInfo`, `ToolCall`

### External Dependencies (Angular Material)
- `@angular/material/dialog`: AddAgentModal
- `@angular/material/menu`: AgentSwitcher
- `@angular/material/card`: AgentSelector
- `@angular/material/button`: Various components
- `@angular/material/icon`: Various components
- `@angular/material/list`: SessionList

### Component Hierarchy
```
AgentSelectorComponent
    └── AddAgentModalComponent (via MatDialog)

SessionListComponent
    └── AgentSwitcherComponent
```
