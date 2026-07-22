# Architecture Diagrams: Workspace Viewer

## System Architecture

```mermaid
flowchart TB
    subgraph FE["🌐 Angular Frontend (port 4199)"]
        direction TB
        WPC["WorkspacePageComponent<br/>split layout"]:::feComp
        FTC["FileTreeComponent<br/>lazy expansion"]:::feComp
        CVC["CodeViewerComponent<br/>CodeMirror 6 read-only"]:::feComp
        DVC["DiffViewerComponent<br/>CodeMirror 6 merge-view"]:::feComp
        CMD["CodemirrorDirective"]:::feComp
        WS["WorkspaceService<br/>HTTP + SSE"]:::feSvc

        WPC --> FTC
        WPC --> CVC
        WPC --> DVC
        WPC --> CMD
    end

    subgraph TRANS["📡 HTTP / SSE Transport"]
        direction LR
        REST["REST Requests<br/>GET tree / file / diff"]:::transport
        SSE["SSE Stream<br/>file_changed events"]:::transport
    end

    subgraph BOTTOM[" "]
        direction LR

        subgraph BE["⚙️ FastAPI Backend (port 8079)"]
            direction TB
            WR["WorkspaceRouter<br/>/api/workspace/*"]:::beComp
            WG["WorkspaceGuard<br/>extracted from filesystem.py"]:::beComp
            GDS["GitDiffService<br/>subprocess git"]:::beComp
            FCM["FileChangeMonitor<br/>watchdog / polling"]:::beComp
            FS["filesystem.py<br/>existing — delegates to WorkspaceGuard"]:::beLegacy
        end

        subgraph EXT["🔌 External Services"]
            direction TB
            PR[("Project Repository<br/>SQLModel")]:::extData
            GB["Git Binary<br/>subprocess"]:::extSvc
            FSY["Filesystem<br/>ignore patterns"]:::extSvc
        end
    end

    WS -.->|API| FTC
    WS -.->|API| CVC
    WS -.->|API| DVC

    WS -->|HTTP GET| REST
    WS <-->|subscribes| SSE

    REST -->|routes| WR
    SSE -->|events| FCM

    FS -.->|delegates| WG

    WR --> WG
    WG -->|reads| PR
    GDS -->|invokes| GB
    FCM -->|watches| FSY

    classDef feComp fill:#e3f2fd,stroke:#1976d2,stroke-width:2px,color:#000
    classDef feSvc fill:#90caf9,stroke:#0d47a1,stroke-width:3px,color:#000
    classDef transport fill:#c8e6c9,stroke:#388e3c,stroke-width:2px,color:#000
    classDef beComp fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#000
    classDef beLegacy fill:#ffe0b2,stroke:#bf360c,stroke-width:2px,stroke-dasharray:5 5,color:#000
    classDef extData fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#000
    classDef extSvc fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#000
```

### Color Legend
- 🔵 **Blue** = Angular frontend components (darker blue = central WorkspaceService hub)
- 🟢 **Green** = HTTP/SSE transport layer
- 🟠 **Orange** = FastAPI backend modules (dashed border = existing/legacy code)
- 🟣 **Purple** = SQLModel repository (cylinder)
- ⚪ **Gray** = External service dependencies

---

## Request Lifecycle (Sequence)

```mermaid
sequenceDiagram
    actor Browser
    participant Angular as Angular (WorkspaceService)
    participant FastAPI as FastAPI (WorkspaceRouter)
    participant Guard as WorkspaceGuard
    participant GitDiff as GitDiffService
    participant Monitor as FileChangeMonitor
    participant FS as Filesystem/Git

    rect rgb(220, 240, 255)
    note over Browser, FS: Initial Load
    Browser->>Angular: Open /projects/:projectId/workspace
    Angular->>Angular: WorkspacePageComponent loads
    Angular->>FastAPI: GET /api/workspace/{projectId}/tree
    FastAPI->>FastAPI: ProjectRepository.get_by_id(projectId)
    FastAPI->>Guard: resolve(workdir)
    Guard-->>FastAPI: workdir resolved
    FastAPI->>FS: Build file tree (depth-limited, ignore patterns)
    FS-->>FastAPI: tree data
    FastAPI-->>Angular: FileTreeResponse
    Angular-->>Browser: Render FileTreeComponent
    end

    rect rgb(230, 255, 230)
    note over Browser, FS: File Selection
    Browser->>Angular: Click file
    Angular->>FastAPI: GET /api/workspace/{projectId}/file?path=...
    FastAPI->>Guard: resolve(path)
    Guard-->>FastAPI: valid (within workdir)
    FastAPI->>FS: Read file content
    FS-->>FastAPI: content bytes
    FastAPI->>FastAPI: Check size limit + detect language
    FastAPI-->>Angular: FileContentResponse
    Angular-->>Browser: Render CodeViewerComponent (Codemirror)
    end

    rect rgb(255, 245, 220)
    note over Browser, FS: Diff View
    Browser->>Angular: Switch to Diff tab
    Angular->>FastAPI: GET /api/workspace/{projectId}/diff?path=...
    FastAPI->>Guard: resolve(path)
    Guard-->>FastAPI: valid (within workdir)
    FastAPI->>GitDiff: Run git diff HEAD
    GitDiff->>FS: git diff HEAD
    FS-->>GitDiff: diff output
    GitDiff-->>FastAPI: GitDiffResponse
    FastAPI-->>Angular: GitDiffResponse
    Angular-->>Browser: Render DiffViewerComponent (MergeView)
    end

    rect rgb(255, 230, 230)
    note over Browser, FS: Real-time Update
    FS->>Monitor: File change detected
    Monitor->>Angular: SSE file_changed event
    Angular->>Angular: Auto-refresh file content
    Angular-->>Browser: Updated content rendered
    end
```

### Phase Grouping
- 🔵 **Initial Load** — Route activation → file tree fetch → tree render
- 🟢 **File Selection** — Click file → content fetch → CodeMirror render
- 🟠 **Diff View** — Switch tab → git diff fetch → MergeView render
- 🔴 **Real-time Update** — FileChangeMonitor → SSE → auto-refresh
