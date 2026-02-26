# agents/_inner_soul/

## Responsibility

The **Inner Soul** agent is the intelligence behind agent self-evolution and growth. It understands natural language requests and intelligently determines which agent files to update. It doesn't just route requests—it semantically classifies them and applies updates to the appropriate files (soul.md, user.md, memory.md, workflow.md, memories/).

**Core Purpose**: Enable agents to learn, grow, and evolve by managing their identity, preferences, knowledge, and workflows through semantic understanding of user requests.

## Design

### Agent Configuration

| Aspect | Configuration |
|--------|---------------|
| **Type** | Self-evolution / Reflection agent |
| **Immutable** | Cannot modify itself (enforced constraint) |
| **Input** | Natural language requests or structured intent/target |
| **Output** | File updates with classification metadata |

### File Understanding

The agent manages 5 file types representing different aspects of agent identity:

| File | Purpose | Update Frequency |
|------|---------|------------------|
| **soul.md** | Who the agent IS (identity, personality, core beliefs) | Rate-limited (1 per 10 tasks, min 24h) |
| **user.md** | Who the USER is (preferences, relationship style) | Unlimited |
| **memory.md** | What agent KNOWS (important knowledge, limited ~500 words) | Limited by size |
| **memories/** | What HAPPENED (events, patterns, lessons - timestamped files) | Unlimited (append-only) |
| **workflow.md** | HOW agent works (processes, rules, steps) | Rate-limited (1 per 5 tasks) |

## Capabilities

### Semantic Classification

The agent classifies requests into types to determine the right target file(s):

| Type | Description | Default Target(s) |
|------|-------------|-------------------|
| **identity** | Who the agent IS | soul.md |
| **personality** | How the agent behaves | soul.md + user.md |
| **user_preference** | What user likes/wants | user.md |
| **user_identity** | Who the user is | user.md |
| **knowledge** | Important facts to remember | memory.md + memories/ |
| **pattern** | Observed patterns | memories/ |
| **workflow** | Process changes | workflow.md |
| **event** | Events and observations | memories/ |
| **skill** | New capabilities | memories/ |
| **mistake** | Lessons learned | memories/ |

### Multi-File Intelligence

The agent recognizes when requests require multiple file updates:

- *"Be friendly"* → personality → soul.md (trait) + user.md (relationship style)
- *"My name is X and user likes Y"* → identity + user_preference → soul.md + user.md
- *"I value quality, always test first"* → identity + workflow → soul.md + workflow.md

### Self-Modification Constraints

| Constraint | Description |
|------------|-------------|
| **Immutable Self** | Cannot modify its own code/rules |
| **Approval Required** | soul.md changes require user approval (stored as proposals) |
| **Rate Limits** | soul.md: 1 per 10 tasks (min 24h), workflow.md: 1 per 5 tasks |
| **Size Limits** | memory.md: 500 words, soul.md: 2000 chars/20 statements |
| **Append-Only Memories** | Never delete, only add timestamped memory files |

## Integration Points

### Request Flow

1. **Receives requests** from agent runtime via explicit intent (`inner_soul(request="...", intent="change", target="soul")`) or natural language
2. **Classifies** the request semantically against pattern rules
3. **Determines targets** - which files need updating
4. **Validates** against growth.md rules (size, rate limits)
5. **Executes updates** - modifies appropriate files
6. **Reports** - returns clear feedback on what was done

### Integration with Agent Files

The agent operates on these files in the agent's directory:
- `soul.md` - Identity proposals stored in history/ for approval
- `user.md` - Direct append for user preferences
- `memory.md` - Condensed knowledge (redirects to memories/ when full)
- `memories/` - Timestamped event/pattern files
- `workflow.md` - Appends to "Learned" section

### External Enforcement

- Enforces **growth.md** rules (size limits, rate limits)
- Creates **proposal files** in history/ for soul.md changes requiring manual approval

## Key Files

- **soul.md**: Core identity definition - who the agent is, its understanding of file purposes, semantic classification rules, and how it thinks
- **rule.md**: Validation rules, rate limits, size limits, multi-file update logic, and "must not" constraints
- **workflow.md**: Step-by-step processing workflow - receive, classify, determine targets, validate, execute, report
- **codemap.md**: This file - architectural documentation
