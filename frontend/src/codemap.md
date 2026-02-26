# frontend/src/

## Responsibility
Angular 17+ standalone application - serves as the web UI bootstrap and root for the Auto-Code application. Handles client-side routing, HTTP communication, and real-time streaming updates.

## Design
- **Architecture**: Angular standalone component pattern (no NgModules)
- **Bootstrapping**: Uses `bootstrapApplication()` for client-side rendering
- **Routing**: Lazy-loaded routes for code splitting and performance
- **State**: Signal-based reactivity in root component
- **UI Framework**: Angular Material components

## Flow
1. Browser loads `index.html` → loads `main.ts` bundle
2. `main.ts` calls `bootstrapApplication(App, appConfig)`
3. `appConfig` provides router, HTTP client, animations
4. Router loads home page (`/`) or chat session (`/sessions/:sessionId`)
5. `App` component fetches health status on init
6. `SseService` handles real-time streaming for chat updates

## Integration Points
- **Bootstraps**: Angular standalone app via `bootstrapApplication(App, appConfig)` in `main.ts`
- **Config Providers**: `app/app.config.ts` provides router, HTTP client, animations, and global error handlers
- **Routing**: `app/app.routes.ts` defines lazy-loaded routes for home (`/`) and chat sessions (`/sessions/:sessionId`)
- **API Communication**: Root `App` component injects `ApiService` for backend health checks
- **Real-time**: `SseService` injected at root for Server-Sent Events streaming
- **HTML Mount**: `index.html` contains `<app-root>` selector where Angular mounts the application

## Entry Points
- `main.ts`: Bootstrap configuration - uses Angular's standalone `bootstrapApplication()` to mount the `App` component with `appConfig`
- `index.html`: HTML entry point - contains `<app-root>` selector where Angular mounts the application

## Directory Structure
| Directory | Purpose |
|-----------|---------|
| app/ | Root Angular application module containing all components, services, and routing |
| app/components/ | Reusable UI components (chat-interface, session-list, message-input, agent-selector, agent-switcher, add-agent-modal) |
| app/pages/ | Page-level components (home, chat) |
| app/services/ | Application services (api.service.ts, sse.service.ts) |
| app/models/ | TypeScript interfaces and types |

## Key Files
- `main.ts`: App bootstrap entry point - imports and bootstraps the root `App` component
- `index.html`: HTML shell with `<app-root>` selector and Material Icons
- `app/app.ts`: Root standalone component - contains toolbar, navigation, health check on init
- `app/app.config.ts`: Application configuration with all required providers
- `app/app.routes.ts`: Route definitions with lazy-loaded page components
- `app/app.html`: Root component template with navigation toolbar
- `app/services/api.service.ts`: HTTP API client for backend communication
- `app/services/sse.service.ts`: Server-Sent Events service for real-time streaming
- `app/models/index.ts`: TypeScript type definitions
- `styles.scss`: Global application styles
