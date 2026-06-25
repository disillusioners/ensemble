# Phase 4: Agent Prompt Updates

## Objective
Update all other agents' system prompt files (leader, planner, jober, _mother, _prompt_system) to reference "developer" instead of "coder" as the agent_id. This ensures the leader correctly delegates to the renamed agent and all coordination documentation uses the new name.

## Coupling
- **Depends on**: Phase 1 (the `developer` agent must exist conceptually)
- **Coupling type**: loose
- **Shared files with other phases**: None (agent prompt files are independent)
- **Shared APIs/interfaces**: None (these are prompt files, not code)
- **Why this coupling**: The prompts reference agent names; they should match the new canonical name. Can run in parallel with Phase 2/3.

## Context
The leader agent's system prompt contains routing instructions like "spawn coder to explore" and "delegate to coder." After the rename, the leader must use "developer" as the agent_id when spawning. Other agents (planner, jober) also reference "coder" in their workflow instructions.

**Critical distinction**: The leader's routing was previously fixed from hardcoded "Delegate to Coder" to domain-based routing. The current references to "coder" are in natural language instructions that the LLM uses to decide which agent to spawn. If the leader still says "coder" but the agent_id is "developer", the LLM may fail to spawn correctly.

## Reference Analysis: 12 Files, ~40 References

### High Priority: Leader Agent (direct spawning impact)

#### agents/leader/rule.md
| Line | Current | New | Type |
|------|---------|-----|------|
| 14 | `Via a dedicated coder instance` | `Via a dedicated developer instance` | Routing instruction |
| 58 | `spawn coder to explore and report back` | `spawn developer to explore and report back` | Routing instruction |
| 83 | `delegate investigation to coder/tester` | `delegate investigation to developer/tester` | Routing instruction |
| 166 | `use coder to explore, then decide` | `use developer to explore, then decide` | Routing instruction |

#### agents/leader/workflow.md
| Line | Current | New | Type |
|------|---------|-----|------|
| 28 | `spawn coder to explore` | `spawn developer to explore` | Workflow step |
| 54 | `Other agents (coder, reviewer, tester)` | `Other agents (developer, reviewer, tester)` | Reference |
| 70 | `If coders start before branch exists` | `If developers start before branch exists` | Warning |
| 75 | `Spawn coder → send_message` | `Spawn developer → send_message` | Workflow step |
| 84 | `Spawn coder → send_message` | `Spawn developer → send_message` | Workflow step |
| 337 | `Spawn: coder-1, reviewer-1, tester-1` | `Spawn: developer-1, reviewer-1, tester-1` | Example |
| 338-340 | `coder-1 → reviewer-1 → tester-1` | `developer-1 → reviewer-1 → tester-1` | Example (3 lines) |
| 344-345 | `coder-2, reviewer-2, tester-2` | `developer-2, reviewer-2, tester-2` | Example (2 lines) |
| 389 | `coder has zero evidence` | `developer has zero evidence` | Explanation |
| 399 | `coder / tester / planner` | `developer / tester / planner` | Reference |
| 523 | `run coders simultaneously` | `run developers simultaneously` | Instruction |
| 524 | `start N+1 coder while N is in review` | `start N+1 developer while N is in review` | Instruction |
| 533-540 | `coder-1, coder-2, coder-3` slots | `developer-1, developer-2, developer-3` slots | Diagram (8 lines) |
| 541 | `start reviews as coders finish` | `start reviews as developers finish` | Instruction |
| 544-545 | `coder-1, coder-2` slots | `developer-1, developer-2` slots | Diagram (2 lines) |
| 549 | `Prioritize coders first` / `run coders in parallel` | `Prioritize developers first` / `run developers in parallel` | Instruction |
| 583 | `Spawn coder-1` | `Spawn developer-1` | Example |
| 584 | `Leader → coder-1: "Implement..."` | `Leader → developer-1: "Implement..."` | Example |

#### agents/leader/soul.md
| Line | Current | New |
|------|---------|-----|
| 86 | `\| **coder** \| Implements code...` | `\| **developer** \| Implements code...` |

#### agents/leader/memory.md
| Line | Current | New |
|------|---------|-----|
| 7 | `delegate investigation to coder/tester` | `delegate investigation to developer/tester` |

### Medium Priority: Planner Agent

#### agents/planner/rule.md
| Line | Current | New |
|------|---------|-----|
| 53 | `Each phase = 1 coder instance's work` | `Each phase = 1 developer instance's work` |
| 92 | `You're a planner, not a coder` | `You're a planner, not a developer` *(or rephrase: "not an implementer")* |

#### agents/planner/workflow.md
| Line | Current | New |
|------|---------|-----|
| 142 | `1 coder instance` | `1 developer instance` |
| 150 | `<30 min of coder work` | `<30 min of developer work` |
| 155 | `multiple coder instances` | `multiple developer instances` |

### Medium Priority: Jober Agent

#### agents/jober/rule.md
| Line | Current | New |
|------|---------|-----|
| 58 | `delegate to its team (coder, reviewer, tester)` | `delegate to its team (developer, reviewer, tester)` |

#### agents/jober/tools_note.md
| Line | Current | New |
|------|---------|-----|
| 13 | `agent_id="coder"` (example) | `agent_id="developer"` |
| 32 | `job_create(agent_id="coder", ...)` | `job_create(agent_id="developer", ...)` |
| 306 | `result = job_create(agent_id="coder", ...)` | `result = job_create(agent_id="developer", ...)` |
| 310 | `job_id = job_create(agent_id="coder", ...)` | `job_id = job_create(agent_id="developer", ...)` |
| 326 | `job_create(agent_id="coder", ...)` | `job_create(agent_id="developer", ...)` |

#### agents/jober/workflow.md
| Line | Current | New |
|------|---------|-----|
| 488 | `job_create(agent_id=coder, ...)` | `job_create(agent_id=developer, ...)` |

### Low Priority: System-Level Prompts

#### agents/_mother/tools_note.md
| Line | Current | New |
|------|---------|-----|
| 34 | `agent_name="coder"` | `agent_name="developer"` |
| 51 | `agent_read(agent_name="coder", ...)` | `agent_read(agent_name="developer", ...)` |

#### agents/_prompt_system/innate-skills/coordination/skill.md
| Line | Current | New |
|------|---------|-----|
| 9 | `reuse coder, reviewer, and tester instances` | `reuse developer, reviewer, and tester instances` |
| 19 | `run coders in parallel` | `run developers in parallel` |
| 20 | `pipeline: start next coder` | `pipeline: start next developer` |
| 24 | `Prioritize coders in parallel` | `Prioritize developers in parallel` |

#### agents/_prompt_system/project-experience.md
| Line | Current | New |
|------|---------|-----|
| 9 | `Feature plans (planner creates, coder reads)` | `Feature plans (planner creates, developer reads)` |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update leader prompts | Update rule.md (4 refs), workflow.md (~25 refs), soul.md (1 ref), memory.md (1 ref) | `agents/leader/*.md` |
| 2 | Update planner prompts | Update rule.md (2 refs), workflow.md (3 refs) | `agents/planner/*.md` |
| 3 | Update jober prompts | Update rule.md (1 ref), tools_note.md (5 refs), workflow.md (1 ref) | `agents/jober/*.md` |
| 4 | Update system prompts | Update _mother/tools_note.md (2 refs), _prompt_system/*.md (5 refs) | `agents/_mother/*.md`, `agents/_prompt_system/*.md` |

## Constraints
- **Leader's routing is domain-based, NOT hardcoded** — the leader decides which agent to spawn based on task type. The prompt changes ensure the leader uses the correct agent_id string "developer" when spawning.
- The word "coder" may appear in generic English usage (e.g., "you're not a coder" meaning "not an implementer"). Use judgment: if it refers to the agent_id, change it. If it's generic English, consider rephrasing for clarity.
- **DO NOT modify** the developer agent's own files (done in Phase 1)

## Deliverables
- [ ] Leader agent prompts use "developer" for all agent references
- [ ] Planner agent prompts use "developer" for agent references
- [ ] Jober agent prompts use `agent_id="developer"` in examples
- [ ] _mother and _prompt_system use "developer" in examples
- [ ] `grep -rn "coder" agents/ | grep -v "agents/developer/"` returns 0 matches
