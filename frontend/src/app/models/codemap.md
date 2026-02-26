# frontend/src/app/models/

## Responsibility
This directory contains TypeScript interfaces and type definitions that represent the data models for the Auto-Code frontend application. These models align with backend API responses and define the data shapes used throughout the application for sessions, messages, agents, and server-sent events (SSE).

## Models

### Session Models

#### SessionStatus
```typescript
export type SessionStatus = 'idle' | 'running' | 'waiting' | 'error' | 'terminated';
```
- Purpose: Represents the lifecycle state of a session (idle, active, paused, failed, or ended)

#### SessionInfo
```typescript
export interface SessionInfo {
  session_id: string;
  agent_dir: string;
  status: SessionStatus;
  parent_id: string | null;
  children: string[];
  created_at: string;
  updated_at: string | null;
}
```
- Purpose: Represents a coding session with its metadata, parent-child relationships, and timestamps

#### SessionListResponse
```typescript
export interface SessionListResponse {
  sessions: SessionInfo[];
}
```
- Purpose: Wrapper response for listing multiple sessions

---

### Message Models

#### Message
```typescript
export interface Message {
  type: string;
  message_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  tool_calls?: ToolCall[];
  error?: string;
  created_at: string;
}
```
- Purpose: Represents a chat message in the conversation, including user queries, AI responses, and system messages. Supports reasoning/thinking content and tool execution.

#### ToolCall
```typescript
export interface ToolCall {
  id: string;
  name: string;
  arguments: string | Record<string, unknown>;
  output?: string;
}
```
- Purpose: Represents a tool/function call made by the AI during agent execution

#### MessageCreate
```typescript
export interface MessageCreate {
  content: string;
}
```
- Purpose: Payload for creating a new message (sending user input)

#### MessageResponse
```typescript
export interface MessageResponse {
  message_id: string;
  role: string;
  content: string | null;
  thinking?: string | null;
  tool_calls: unknown[] | null;
  created_at: string;
}
```
- Purpose: Backend response after message creation/retrieval

---

### Agent Models

#### Agent
```typescript
export interface Agent {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  version?: string;
  agent_dir: string;
}
```
- Purpose: Represents a configurable AI agent with visual properties (icon, color) and metadata

#### AgentListResponse
```typescript
export interface AgentListResponse {
  agents: Agent[];
}
```
- Purpose: Wrapper response for listing available agents

#### AgentCreate
```typescript
export interface AgentCreate {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
}
```
- Purpose: Payload for creating a new agent configuration

---

### Health/System Models

#### HealthResponse
```typescript
export interface HealthResponse {
  status: string;
  uptime_seconds: number;
  version: string;
}
```
- Purpose: Backend health check response containing server status, uptime, and version

---

### SSE Event Models

#### EventType
```typescript
export type EventType = 
  | 'connected' 
  | 'message_queued' 
  | 'status_changed' 
  | 'content_chunk' 
  | 'tool_call' 
  | 'completed' 
  | 'error' 
  | 'keepalive';
```
- Purpose: Enumeration of all possible server-sent event types for real-time updates

#### SSEEvent
```typescript
export interface SSEEvent {
  event_id: number;
  type: EventType;
  session_id: string;
  message_id: string | null;
  data: Record<string, unknown>;
}
```
- Purpose: Represents a server-sent event from the backend for real-time session updates

---

## Design Patterns

### TypeScript Interface Pattern
- Uses TypeScript interfaces for defining structured data shapes
- Aligns with backend Pydantic/dataclass models
- Supports backward compatibility with optional fields (`?`)

### Union Types for Enums
- Uses `type` with union strings for status/enum-like values (e.g., `SessionStatus`, `EventType`)
- More flexible than TypeScript enums for API compatibility

### Response Wrapper Pattern
- Uses response wrapper interfaces (e.g., `SessionListResponse`, `AgentListResponse`) for API responses

### Optional Fields
- Extensive use of optional properties (`?`) for backward compatibility with evolving API

---

## Integration Points

### Services
| Service | Imported Models |
|---------|-----------------|
| `api.service.ts` | SessionInfo, SessionListResponse, Message, MessageCreate, MessageResponse, Agent, AgentListResponse, AgentCreate, HealthResponse |
| `sse.service.ts` | Message, SSEEvent, EventType |

### Components
| Component | Imported Models |
|-----------|-----------------|
| `home.component.ts` | Agent, AgentCreate, SessionInfo |
| `chat.component.ts` | Agent, SessionInfo, Message |
| `chat-interface.component.ts` | Message, Agent, ToolCall |
| `add-agent-modal.component.ts` | AgentCreate |
| `agent-switcher.component.ts` | Agent |
| `agent-selector.component.ts` | Agent, AgentCreate |
| `session-list.component.ts` | Agent, SessionInfo |
| `app.ts` | HealthResponse |

### Backend Alignment
- These frontend models map to backend Pydantic models in `backend/app/schemas/`
- Session models align with backend `Session`, `SessionCreate` schemas
- Message models align with backend `Message`, `MessageCreate` schemas
- Agent models align with backend `Agent`, `AgentCreate` schemas
- SSE events mirror backend event emission types
