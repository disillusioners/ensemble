# Coding Server Architecture Vision (Final)

## Vision

In the AI era, keep the **Coder Agent** focused on reasoning and code
generation. Move engineering capabilities into a reusable **Coding
Server**.

The Coder should not become a giant collection of IDE features. Instead,
it delegates execution to a Coding Server.

------------------------------------------------------------------------

# Core Principles

-   Keep the Coder stateless.
-   The Coding Server owns the workspace and execution state.
-   Expose Coding Server capabilities through **MCP tools**.
-   Hide implementation details (LSP, Git Nexus, compilers, etc.) behind
    stable semantic APIs.

------------------------------------------------------------------------

# High-Level Architecture

``` text
Leader
   │
Planner
   │
Coder Agent (LLM)
   │
Tool Calling
   │
MCP
   │
Coding Server
```

To the LLM, every capability is simply a tool.

------------------------------------------------------------------------

# Coding Server Responsibilities

## Workspace

-   Git worktree
-   File system
-   Snapshots
-   Overlay for uncommitted edits

## Repository Intelligence

-   Semantic search
-   Find definition
-   Find references
-   Workspace symbols
-   Call hierarchy
-   Type hierarchy
-   Dependency graph
-   Impact analysis

## Language Services

-   LSP management
-   Diagnostics
-   Formatting
-   Auto imports

## Verification

-   Build
-   Lint
-   Static analysis
-   Security scan
-   Targeted tests

------------------------------------------------------------------------

# Internal Architecture

``` text
                 Coding Server
                        │
   ┌────────────────────┼────────────────────┐
   │                    │                    │
Workspace         Intelligence        Verification
   │                    │                    │
Git Worktree      Symbol Graph        Compiler
Overlay           Semantic Search     Tests
Filesystem        References          Linter
Snapshots         Definitions         Security Scan
```

------------------------------------------------------------------------

# MCP is the Public Interface

Expose semantic tools rather than raw LSP operations.

Examples:

## Workspace

-   workspace_create()
-   workspace_open()
-   workspace_snapshot()
-   workspace_commit()
-   workspace_discard()

## Code

-   code_read()
-   code_write()
-   code_apply_patch()

## Intelligence

-   find_definition()
-   find_references()
-   semantic_search()
-   impact_analysis()

## Language

-   diagnostics()
-   format()
-   auto_imports()

## Verification

-   build()
-   test()
-   lint()
-   security_scan()

The LLM only understands tool calling. MCP is therefore a natural
integration point.

------------------------------------------------------------------------

# Hide the Implementation

The Coding Server may internally use:

-   gopls
-   rust-analyzer
-   tsserver
-   GitHub Nexus
-   Tree-sitter
-   Custom graph engine
-   Build systems
-   Test runners

Agents never depend on these technologies directly.

------------------------------------------------------------------------

# Workspace Overlay

Maintain:

``` text
Persistent Repository Index
            +
Workspace Overlay
```

The overlay reflects in-progress edits, allowing semantic queries
without rebuilding the entire index after every change.

------------------------------------------------------------------------

# Kubernetes Strategy

Use a pool of warm Coding Servers.

``` text
Many Agents
      │
      ▼
Coding Server Pool
      │
Warm Workspace
Warm LSP
Warm Build Cache
Warm Repository Intelligence
```

This avoids every agent starting its own language server and index.

------------------------------------------------------------------------

# Future Evolution

Initially: - LSP provides repository intelligence.

Later: - GitHub Nexus may replace or augment semantic search. - A custom
graph engine can be introduced. - Additional language adapters can be
added.

Because everything is hidden behind MCP, agents remain unchanged.

------------------------------------------------------------------------

# Philosophy

Treat the Coding Server as the autonomous equivalent of a remote IDE
backend.

Agents decide **what** should change.

The Coding Server determines **how** to execute, understand, and verify
those changes efficiently.

This separation creates a scalable foundation for autonomous software
engineering.
