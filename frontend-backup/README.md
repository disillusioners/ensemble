# Ensemble Frontend

A Preact-based chatbot UI for the multi-agent orchestration system.

## Quick Start

### Development
```bash
cd frontend
npm install
npm run dev
```
The frontend will start on http://localhost:3000 and proxy API requests to the backend at http://localhost:8080.

### Production Build
```bash
cd frontend
npm run build
```

The built files will be in `frontend/dist/`. The FastAPI backend will serve these at `/ui` when running.

## Project Structure

```
frontend/
├── src/
│   ├── components/
│   │   ├── AgentSelector.tsx    # Agent selection UI
│   │   ├── ChatInterface.tsx   # Chat messages display
│   │   ├── MessageInput.tsx    # Message input form
│   │   └── SessionList.tsx     # Session sidebar
│   ├── hooks/
│   │   └── useSSE.ts           # SSE streaming hook
│   ├── utils/
│   │   └── api.ts              # API client
│   ├── types/
│   │   └── index.ts            # TypeScript types
│   ├── App.tsx                 # Main app component
│   ├── main.tsx                # Entry point
│   └── index.css               # Tailwind styles
├── index.html
├── vite.config.ts
├── tailwind.config.js
└── package.json
```

## Available Agents

- **Leader** - Coordinates tasks and manages workflow delegation
- **Coder** - Specializes in code generation and debugging
- **Reviewer** - Provides code review and quality assurance

## Features

- Agent selection before session creation
- Session management (create, switch, delete)
- Real-time message streaming via SSE
- Dark theme with custom design system
- Responsive layout

## Tech Stack

- Preact
- TypeScript
- Vite
- Tailwind CSS
