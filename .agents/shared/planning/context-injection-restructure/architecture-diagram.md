# Context Injection Restructure — Architecture Diagram (v2)

## Before vs After Flow

```mermaid
flowchart TD
    subgraph BEFORE["BEFORE — Current State"]
        direction TB
        B_user(["User Request"])
        B_mutate["String Mutation<br/>format_project_context +<br/>shared_context_kv_block prepended<br/>to message body"]
        B_build["_build_graph_input<br/>[skill_msg, user_msg_with_context_prepended]"]
        B_astream["graph.astream"]
        B_node["agent_node"]
        B_full["full_messages =<br/>SystemMessage (PERSONA + CONTEXT baked in,<br/>frozen at spawn)<br/>+ messages"]
        B_llm[("LLM")]
        B_ckpt[("Checkpoint:<br/>context persisted in messages")]

        B_user --> B_mutate
        B_mutate --> B_build
        B_build --> B_astream
        B_astream --> B_node
        B_node --> B_full
        B_full --> B_llm
        B_node --> B_ckpt
    end

    subgraph AFTER["AFTER — Target State (v2: local injection)"]
        direction TB
        A_user(["User Request<br/>(raw, unmodified)"])
        A_build["_build_graph_input<br/>[user_msg ONLY]<br/>(sync, unchanged)"]
        A_astream["graph.astream"]
        A_node["agent_node"]
        A_assemble["ContextSlot.assemble()<br/>(async, inside agent_node)"]
        A_ctx1["1. SYSTEM CONTEXT: Related Project"]
        A_ctx2["2. SYSTEM CONTEXT: Shared Context"]
        A_ctx3["3. SYSTEM CONTEXT: Skills"]
        A_full["full_messages = LOCAL variable<br/>SystemMessage PERSONA ONLY<br/>+ context_msgs<br/>+ state messages<br/>+ RAM-queue injections<br/>+ report injections"]
        A_llm[("LLM")]
        A_compact["Compaction retry?<br/>Re-append context_msgs<br/>to compact_messages"]
        A_return["return {'messages': [response]}<br/>Context NOT in return"]
        A_ckpt[("Checkpoint:<br/>NO context — truly ephemeral")]

        A_user --> A_build
        A_build --> A_astream
        A_astream --> A_node
        A_node --> A_assemble
        A_assemble --> A_ctx1
        A_assemble --> A_ctx2
        A_assemble --> A_ctx3
        A_ctx1 --> A_full
        A_ctx2 --> A_full
        A_ctx3 --> A_full
        A_full --> A_llm
        A_llm --> A_compact
        A_compact -.->|no| A_return
        A_compact -.->|yes: rebuild + re-append| A_full
        A_return --> A_ckpt
    end

    classDef beforeFill fill:#fdecea,stroke:#c0392b,color:#922b21
    classDef afterFill fill:#eafaf1,stroke:#1e8449,color:#145a32
    classDef ctxMsg fill:#fef9e7,stroke:#b7950b,color:#7d6608
    classDef llmNode fill:#ebf5fb,stroke:#2471a3
    classDef ckptBefore fill:#fadbd8,stroke:#cb4335,color:#922b21
    classDef ckptAfter fill:#d4efdf,stroke:#239b56,color:#196f3d
    classDef sysMsg fill:#f5eef8,stroke:#7d3c98,color:#4a235a
    classDef decision fill:#fef9e7,stroke:#d4ac0d

    class B_user,B_mutate,B_build,B_astream,B_node beforeFill
    class A_user,A_build,A_astream,A_node,A_assemble,A_return afterFill
    class A_ctx1,A_ctx2,A_ctx3 ctxMsg
    class B_llm,A_llm llmNode
    class B_ckpt ckptBefore
    class A_ckpt ckptAfter
    class B_full,A_full sysMsg
    class A_compact decision
```

## Phase Dependency Graph (v2 — 6 phases)

```mermaid
flowchart LR
    P1[Phase 1<br/>ContextMessageBuilder<br/>Foundation]
    P2[Phase 2<br/>Appender Dormancy<br/>+ Defense Instruction]
    P3[Phase 3<br/>Inject into agent_node<br/>local full_messages]
    P4[Phase 4<br/>GET /messages API]
    P5[Phase 5<br/>Per-Turn Freshness]
    P6[Phase 6<br/>Testing & Rollout]

    P1 -->|loose| P2
    P2 -->|tight| P3
    P3 -->|loose| P4
    P3 -.->|loose, parallel| P5
    P4 --> P6
    P5 --> P6

    classDef foundation fill:#e8f8f5,stroke:#1abc9c
    classDef core fill:#fef9e7,stroke:#f39c12
    classDef parallel fill:#ebf5fb,stroke:#3498db
    classDef final fill:#f5eef8,stroke:#9b59b6

    class P1 foundation
    class P2,P3 core
    class P4,P5 parallel
    class P6 final
```

## What Changed from v1 (Reviewer Corrections)

```mermaid
flowchart TD
    subgraph v1["v1 — REJECTED"]
        V1_input["_build_graph_input<br/>injects context INTO graph state"]
        V1_filter["Phase 4: Filter at return<br/>MUST filter context out of state"]
        V1_compact["Phase 6: Compaction survival<br/>context must survive compaction"]
        V1_input --> V1_filter
        V1_filter --> V1_compact
    end

    subgraph v2["v2 — CORRECTED"]
        V2_node["agent_node assembles<br/>context LOCALLY"]
        V2_full["full_messages = LOCAL variable<br/>context NEVER in state"]
        V2_return["return ONLY [response]<br/>NO filter needed"]
        V2_compact["Re-append to compact_messages<br/>same C3 pattern"]
        V2_node --> V2_full
        V2_full --> V2_return
        V2_full -.-> V2_compact
    end

    R1["C1: add_messages is APPEND-ONLY<br/>Filter at return doesn't work"]
    R2["C2: agent_node is async<br/>No sync/async boundary issue"]
    R3["C3: Compaction solved by re-append<br/>No separate phase needed"]

    v1 --> R1
    R1 --> v2
    v1 --> R2
    R2 --> v2
    v1 --> R3
    R3 --> v2

    classDef v1style fill:#fdecea,stroke:#c0392b,color:#922b21
    classDef v2style fill:#eafaf1,stroke:#1e8449,color:#145a32
    classDef review fill:#fef9e7,stroke:#b7950b,color:#7d6608

    class V1_input,V1_filter,V1_compact v1style
    class V2_node,V2_full,V2_return,V2_compact v2style
    class R1,R2,R3 review
```

## Key Differences Summary

| Aspect | BEFORE | AFTER (v2) |
|--------|--------|------------|
| Context location | Baked into SystemMessage (frozen at spawn) | Local `full_messages` inside `agent_node` (ephemeral) |
| Freshness | Stale until instance respawn | Fresh every turn |
| Checkpoint | Context persisted in messages | Context NEVER in checkpoint (truly ephemeral) |
| Graph input | Mutated user message + skill msg | Clean user message only |
| Skill injection | Separate `[System Inject]` message | `[SYSTEM CONTEXT: Skills]` via ContextSlot |
| GET /messages | Context hidden in system prompt | Context rebuilt on-demand as synthetic messages |
| Compaction | Context in system prompt | Context re-appended to `compact_messages` (C3 pattern) |
| Filter needed? | N/A | NO — context never enters state |
| `_build_graph_input` | Changed in v1 | UNCHANGED in v2 |
