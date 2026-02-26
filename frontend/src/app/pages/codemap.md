# frontend/src/app/pages/

## Responsibility
Page-level Angular components that serve as route targets. These components handle top-level application views, orchestrate data flow between services and shared UI components, and manage user interactions for distinct application workflows.

## Pages
### home/
**Purpose**: Landing page and session management hub. Displays available AI agents and existing conversation sessions, allowing users to create new sessions or continue existing ones.

**Structure**:
- **Data Management**: Uses Angular signals (`agents`, `sessions`, `selectedAgent`) for reactive state
- **Polling**: Implements 10-second interval polling to refresh session list
- **Actions**:
  - Create new session with selected agent
  - Continue existing session
  - Add/delete agents
  - Start "Mother" agent session (special `./agents/_mother` path)
- **Persistence**: Saves selected agent preference to `localStorage` (`ensemble-next-session-agent`)

**Key Methods**:
- `loadInitialData()` - Fetches agents and sessions on mount
- `onCreateSession()` - Creates new session and navigates to chat
- `onContinueSession()` - Navigates to existing session
- `onAddAgent()` / `onDeleteAgent()` - Agent CRUD operations

### chat/
**Purpose**: Interactive chat interface for AI agent conversations. Displays message history, handles real-time streaming responses via Server-Sent Events (SSE), and provides message input capabilities.

**Structure**:
- **Data Management**: Uses Angular signals for reactive state (`messages`, `currentSession`, `isStreaming`)
- **Real-time Communication**: Connects to SSE service for streaming AI responses
- **Route Parameter Handling**: Subscribes to `sessionId` route parameter for dynamic session loading
- **Preferences**: Persists UI preferences (`showThinking`, `showToolCalls`) to localStorage
- **Effects**: Uses Angular `effect()` for:
  - Persisting UI preferences
  - Handling SSE completed messages
  - Processing SSE errors

**Key Methods**:
- `loadInitialData()` - Fetches agents and sessions
- `loadMessages()` - Retrieves message history for current session
- `handleSessionIdChange()` - Processes route parameter changes
- `onSendMessage()` - Sends user messages via API, displays immediately
- `onNewSession()` - Creates new session from chat view
- `onDeleteSession()` - Removes session and navigates away

**Imported Components**:
- `SessionListComponent` - Displays session sidebar
- `ChatInterfaceComponent` - Renders message conversation
- `MessageInputComponent` - User message input form

## Routing
**Route Configuration** (`app.routes.ts`):
| Path | Component | Type |
|------|-----------|------|
| `/` | `HomeComponent` | Lazy-loaded |
| `/sessions/:sessionId` | `ChatComponent` | Lazy-loaded |
| `**` | Redirect to `/` | Wildcard |

- Both pages use **lazy loading** via `loadComponent()`
- `ChatComponent` extracts `sessionId` from route using `ActivatedRoute`
- Invalid session IDs trigger navigation back to home

## Design Patterns

### 1. Signal-Based Reactive State
- All page components use Angular signals (`signal<T>`, `computed`) for state management
- Provides fine-grained reactivity without Zone.js overhead

### 2. Service Injection Pattern
- `ApiService` - HTTP calls for agents, sessions, messages
- `SseService` - Server-Sent Events for real-time streaming
- Both injected via `inject()` function (Angular 14+)

### 3. Effect Side Effects
- `ChatComponent` uses `effect()` for:
  - Persisting localStorage preferences
  - Reacting to SSE message updates
  - Handling SSE errors

### 4. Polling for Data Freshness
- Both pages implement 10-second polling intervals
- Properly cleaned up in `ngOnDestroy()`

### 5. Standalone Components
- Both components use Angular standalone architecture
- No NgModule required

### 6. LocalStorage Persistence
- Agent selection preference shared between pages
- UI preferences (thinking visibility, tool calls) persisted per session

## Key Files
- `home/`: Landing page with agent/session management
- `chat/`: Chat interface with SSE streaming
- `app.routes.ts`: Route definitions for page navigation
