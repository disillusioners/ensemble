# Workflow

## 1. Receive Request

Accept natural language requests. The agent can:
- Use explicit intent/target: `inner_soul(request="...", intent="change", target="soul")`
- Use natural language: `inner_soul(request="My name is Cody")`
- Mix both: Natural language with hints

## 2. Classify Semantically

Match the request against classification patterns:

| Type | Patterns | Targets |
|------|----------|---------|
| identity | "my name is", "I am", "my purpose" | soul |
| personality | "be friendly", "be cozy", "I value" | soul, user |
| user_preference | "user likes", "user prefers" | user |
| user_identity | "user's name is", "user works as" | user |
| knowledge | "remember that", "important", "I know" | memory, memories |
| pattern | "I noticed", "it seems like" | memories |
| workflow | "always do", "before doing", "new rule" | workflow |
| event | "today", "just now", "we discussed" | memories |
| skill | "I learned to", "new skill" | memories |
| mistake | "mistake:", "don't do again" | memories |
| project_knowledge | "this project uses", "DB", "infrastructure", "k8s", "tech stack", "password", "credentials", "config file" | **REJECT** |

## 3. Determine Targets

Based on classification:
- If explicit `target` provided → use it
- Otherwise → use classified targets
- Multiple targets → update all atomically

## 4. Validate

Check against rules from growth.md:
- Size limits (memory.md: 500 words, soul.md: 2000 chars)
- Rate limits (soul.md: 1 per 10 tasks)
- Content limits (memories: 1000 chars each)

## 5. Execute Updates

For each target:
- **memories/** → Create timestamped file with classification metadata
- **memory.md** → Append if under limit, else redirect to memories/
- **soul.md** → Create proposal in history/ (requires approval)
- **user.md** → Append directly
- **workflow.md** → Append to "Learned" section

## 6. Report

Return clear feedback:
```
✓ Processed: "My name is Cody"
  Classification: identity (Core identity and self-definition)

  📝 soul: history/20260225_143000_soul_proposal.md
     → Awaiting user approval
```

## Edge Cases

### Project Knowledge Rejection (⚠️ CRITICAL)
If the request is about a specific project, **reject it**.

Example response:
```
✗ Rejected: "Remember this project uses PostgreSQL on k8s"
  Classification: project_knowledge

  This is project knowledge, not personal learning. It does not belong in your agent memory.md.
```

### Memory Full
If memory.md is at limit:
- Redirect to memories/
- Inform agent about the limit

### Soul Change Requested
- Create proposal file in history/
- Require manual approval
- Never auto-apply soul changes

### Multiple Classifications
"Be cozy and remember that tests are important"
- personality → soul.md, user.md
- knowledge → memories/
- Update all targets

### Unknown Pattern
Default to "event" → memories/ with generic classification
