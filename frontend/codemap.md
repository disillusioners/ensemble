# frontend/

## Responsibility
Angular 21 single-page application providing the user interface for the auto-code system. It serves as the frontend client that communicates with the Spring Boot backend via REST APIs and WebSocket connections.

## Tech Stack
- **Framework**: Angular 21.1.0
- **Language**: TypeScript 5.9.2
- **UI Library**: Angular Material 21.1.5
- **Build Tool**: Angular CLI 21.1.5 with @angular/build
- **Styling**: SCSS
- **Key Dependencies**:
  - `@angular/core`: ^21.1.0 (core framework)
  - `@angular/router`: ^21.1.0 (routing)
  - `@angular/forms`: ^21.1.0 (form handling)
  - `@angular/cdk`: ~21.1.5 (component dev kit)
  - `@angular/material`: ~21.1.5 (Material Design components)
  - `rxjs`: ~7.8.0 (reactive programming)

## Build Configuration
| Script | Command | Purpose |
|--------|---------|---------|
| `start` | `ng serve` | Start dev server (default: http://localhost:4200) |
| `build` | `ng build` | Build production bundle |
| `watch` | `ng build --watch --configuration development` | Watch mode for development |
| `test` | `ng test` | Run unit tests |

### Production Build Settings
- **Output Hashing**: All (for cache busting)
- **Initial Bundle Budget**: 500kB warning, 1MB error
- **Component Style Budget**: 4kB warning, 8KB error

### Development Build Settings
- Optimization disabled
- Source maps enabled
- Licenses not extracted

## Directory Structure
| Directory | Purpose |
|-----------|---------|
| `src/` | Main application source code |
| `src/main.ts` | Application bootstrap entry point |
| `src/index.html` | Main HTML template |
| `src/styles.scss` | Global styles |
| `public/` | Static assets (favicon, etc.) |

## Integration Points
- **Backend API**: Proxied via `proxy.conf.json` to `http://localhost:8080`
- **WebSocket**: Proxied via `proxy.conf.json` to `ws://localhost:8080`
- **API Prefix**: `/api` requests are rewritten to backend (path prefix removed)
- **Dev Server Proxy**: Configured in `angular.json` using `proxy.conf.json`

## Key Files
- `angular.json`: Angular CLI workspace configuration with build/serve settings
- `tsconfig.json`: TypeScript configuration with strict mode enabled
- `tsconfig.app.json`: Application-specific TypeScript config (extends base)
- `proxy.conf.json`: Development proxy configuration for backend communication
- `package.json`: NPM dependencies and scripts
- `src/main.ts`: Application bootstrap entry point
- `src/styles.scss`: Global SCSS styles
- `src/index.html`: Main HTML template

## TypeScript Configuration
- **Strict Mode**: Enabled (strict: true)
- **Target**: ES2022
- **Module**: preserve (Angular bundler format)
- **Angular Compiler Options**:
  - Strict injection parameters
  - Strict input access modifiers
  - Strict templates
