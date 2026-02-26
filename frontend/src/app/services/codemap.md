# frontend/src/app/services/

## Responsibility
Service layer handling HTTP API communication and Server-Sent Events (SSE) for real-time updates in the Angular application. Provides a clean abstraction over backend communication with reactive patterns.

## Design Patterns
- **Observable Pattern**: ApiService returns RxJS Observables for all HTTP requests, enabling async data handling with operators like `.subscribe()`, `.pipe()`, etc.
- **Singleton Services**: Both services use `providedIn: 'root'` for application-wide singleton instances via Angular's Dependency Injection
- **Reactive Signals**: SseService uses Angular Signals (`signal()`, `computed()`, `effect()`) for reactive state management
- **Event-Driven Architecture**: SseService implements pub/sub pattern via EventSource for real-time message streaming

## Data & Control Flow

### HTTP API Flow (ApiService)
```
Component/Service → ApiService Method → HttpClient → /api endpoints → Observable<Response>
```
- All API methods return typed Observables
- HTTP methods: GET (health, list, get), POST (create), DELETE (remove)
- Base URL: `/api`

### SSE Event Flow (SseService)
```
Backend SSE → EventSource → Event Listeners → NgZone.run() → Signal Updates → Components
```
- **Connection**: `connect(sessionId)` → creates EventSource to `/api/sessions/{id}/events`
- **Event Types Handled**:
  - `connected`: Connection established
  - `message_queued`: New message queued for processing
  - `status_changed`: Message status updated (queued → processing → completed/failed)
  - `content_chunk`: Streaming content chunks (for AI responses)
  - `tool_call`: Tool execution events
  - `completed`: Message processing finished
  - `error`: Error events
  - `keepalive`: Connection keepalive

- **State Management via Signals**:
  - `isStreaming`: Boolean for connection status
  - `events`: Array of all SSE events
  - `latestCompletedMessage`: Most recent completed message
  - `latestError`: Last error encountered
  - `statusUpdates`: Map of message_id → status

- **Reconnection Logic**: Exponential backoff (max 5 attempts, max delay 30s)

## Integration Points

### Backend API Endpoints Consumed
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/health` | Health check |
| GET | `/api/agents` | List all agents |
| POST | `/api/agents` | Create new agent |
| DELETE | `/api/agents/{agentId}` | Delete agent |
| GET | `/api/sessions` | List all sessions |
| POST | `/api/sessions` | Create new session |
| GET | `/api/sessions/{sessionId}` | Get session details |
| DELETE | `/api/sessions/{sessionId}` | Delete/terminate session |
| POST | `/api/sessions/{sessionId}/messages` | Send message to session |
| GET | `/api/sessions/{sessionId}/messages` | Get session messages |
| GET | `/api/sessions/{sessionId}/events` | SSE event stream |

## Key Files
- `api.service.ts`: HTTP client service wrapping Angular HttpClient with typed methods for all REST API endpoints (health, agents, sessions, messages)
- `sse.service.ts`: Real-time event streaming service using EventSource with Angular signals for reactive state, handles connection management with auto-reconnection
- `index.ts`: Barrel export file re-exporting both services for clean imports
