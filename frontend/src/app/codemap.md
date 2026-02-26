# frontend/src/app/

## Responsibility
This is the core Angular 17+ frontend application providing a chat interface for an AI agent system. It manages user sessions, displays real-time streaming responses, and handles agent selection. The app communicates with a backend API via REST and Server-Sent Events (SSE) for real-time updates.

## Design Patterns
- **Standalone Components**: All components are Angular standalone components (no NgModules)
- **Dependency Injection**: Using Angular's `inject()` function for service injection
- **Reactive State with Signals**: Angular signals (`signal()`, `computed()`) for reactive state management
- **Service Layer Pattern**: Centralized API and SSE services for data access
- **Lazy Loading**: Routes lazy-load component modules for performance

## Architecture

### Component Hierarchy
```
App (Root Component)
├── Toolbar (Material)
│   ├── App Title + Logo
│   └── Health Status Indicator
└── RouterOutlet
    ├── HomeComponent (/)
    │   ├── AgentSelector
    │   ├── AgentSwitcher
    │   ├── AddAgentModal
    │   └── SessionList
    └── ChatComponent (/sessions/:sessionId)
        ├── ChatInterface
        │   ├── MessageList
        │   └── MessageBubble
        └── MessageInput
```

### Routing Structure
| Path | Component | Loading |
|------|-----------|---------|
| `/` | HomeComponent | Lazy |
| `/sessions/:sessionId` | ChatComponent | Lazy |
| `**` | Redirect to `/` | - |

### Service Layer
| Service | Responsibility |
|---------|----------------|
| `ApiService` | HTTP client for REST API calls (health, agents, sessions, messages) |
| `SseService` | Server-Sent Events for real-time streaming updates |

### Models (TypeScript Interfaces)
- **Session**: `SessionInfo`, `SessionListResponse`, `SessionStatus` ('idle' | 'running' | 'waiting' | 'error' | 'terminated')
- **Message**: `Message`, `MessageCreate`, `MessageResponse`, `ToolCall`
- **Agent**: `Agent`, `AgentListResponse`, `AgentCreate`
- **SSE**: `SSEEvent`, `EventType` ('connected' | 'message_queued' | 'status_changed' | 'content_chunk' | 'tool_call' | 'completed' | 'error' | 'keepalive')
- **Health**: `HealthResponse`

## Flow

### Initialization Flow
1. `App` component initializes in `ngOnInit()`
2. Calls `ApiService.health()` to verify backend connectivity
3. Health status displayed in toolbar

### Session Creation Flow
1. User selects agent in `AgentSelector`
2. User clicks "New Session" → `ApiService.createSession()` called
3. Navigate to `/sessions/{sessionId}`

### Chat/Streaming Flow
1. User sends message via `MessageInput`
2. `ApiService.sendMessage()` posts to REST API
3. `SseService.connect(sessionId)` opens SSE connection
4. Real-time events stream back:
   - `message_queued` → message queued for processing
   - `status_changed` → message status updates
   - `content_chunk` → streaming content chunks
   - `tool_call` → tool execution events
   - `completed` → final message with full content
   - `error` → error handling
5. UI updates reactively via signals

### State Management
- `SseService` maintains signals: `isStreaming`, `events`, `latestCompletedMessage`, `latestError`, `statusUpdates`
- Components inject services and read signals for reactive updates

## Integration

### REST API (`/api`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/agents` | GET | List available agents |
| `/agents` | POST | Create new agent |
| `/agents/:id` | DELETE | Delete agent |
| `/sessions` | POST | Create new session |
| `/sessions` | GET | List all sessions |
| `/sessions/:id` | GET | Get session details |
| `/sessions/:id` | DELETE | Terminate session |
| `/sessions/:id/messages` | GET | Get session messages |
| `/sessions/:id/messages` | POST | Send new message |
| `/sessions/:id/events` | SSE | Real-time event stream |

### Server-Sent Events
- Connection managed by `SseService`
- Automatic reconnection with exponential backoff (max 5 attempts)
- Uses `NgZone` for Angular change detection integration

## Key Files
- `app.ts`: Root standalone component, toolbar, health check initialization
- `app.config.ts`: Angular application config (providers for router, HTTP, animations)
- `app.routes.ts`: Lazy-loaded routing configuration
- `models/index.ts`: TypeScript interfaces for all data types
- `components/index.ts`: Re-exports of 6 shared components
- `services/index.ts`: Re-exports of API and SSE services
- `services/api.service.ts`: REST API HTTP client (providedIn: 'root')
- `services/sse.service.ts`: SSE event streaming with reconnection logic (providedIn: 'root')

## Dependencies
- **@angular/core**: Core Angular framework
- **@angular/material**: UI components (MatToolbar, MatIcon, MatButton)
- **@angular/common/http**: HttpClient for REST calls
- **rxjs**: Observable-based HTTP requests
