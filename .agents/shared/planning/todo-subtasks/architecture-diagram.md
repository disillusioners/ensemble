# Architecture Diagram: Todo Node Sub-Tasks

```mermaid
flowchart TD
    subgraph AgentLayer["Agent Tools Layer"]
        AT1["todo_add_subtask\n(node_id, text)"]
        AT2["todo_update_subtask\n(node_id, subtask_id, status, auto_complete)"]
        AT3["todo_remove_subtask\n(node_id, subtask_id)"]
    end

    subgraph APILayer["API Endpoints Layer"]
        AE1["POST /todos/{node_id}/subtasks"]
        AE2["PATCH /todos/{node_id}/subtasks/{subtask_id}"]
        AE3["DELETE /todos/{node_id}/subtasks/{subtask_id}"]
    end

    subgraph ServiceLayer["TodoGraphManager Service"]
        SM1["add_subtask()"]
        SM2["update_subtask()"]
        SM3["remove_subtask()"]
        DN["TodoNode\n{id, text, status, comment,\nnext_ids, index, subtasks}"]
        DS["SubTask\n{id: s-prefixed, text, status: pending|done}"]
        SP["Status Propagation\nauto_complete=True + all done\n→ parent node = done"]
    end

    subgraph SSELayer["SSE Emission"]
        SSE["stream_todo_update()\n7-key payload:\nid, index, text, status,\ncomment, next_ids, subtasks"]
    end

    subgraph FrontendLayer["Frontend (Angular)"]
        FL1["Linear Mode:\nExpandable checklist\nunder each node"]
        FL2["Graph Mode:\nCount badge on node\n+ popup checklist"]
        FT["TodoNode TS interface\n+ SubTask interface"]
    end

    AT1 --> SM1
    AT2 --> SM2
    AT3 --> SM3
    AE1 --> SM1
    AE2 --> SM2
    AE3 --> SM3

    SM1 --> DN
    SM2 --> DN
    SM3 --> DN
    DN --> DS
    SM2 --> SP
    SP --> DN

    SM1 --> SSE
    SM2 --> SSE
    SM3 --> SSE

    SSE --> FT
    FT --> FL1
    FT --> FL2
```

## Data Flow Summary

1. **Agent or API** calls a sub-task operation (add/update/remove)
2. **TodoGraphManager** mutates the `TodoNode.subtasks` list (thread-safe via `threading.Lock`)
3. **Status propagation** (optional): if `auto_complete=True` and all sub-tasks are `done`, parent node status → `done`
4. **SSE emission**: `stream_todo_update()` pushes the full 7-key node list to all connected clients
5. **Frontend** receives SSE update, replaces `todos` signal, re-renders sub-task checklist (linear: inline expandable; graph: popup)
