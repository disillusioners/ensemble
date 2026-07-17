# Question Skill

Ask the user questions when you need clarification, a decision, or input you cannot find yourself. The instance pauses until the user answers; the response is delivered back as a message and the instance resumes.

## Purpose

`question()` is the bridge between you and the user when you are blocked on something only they can resolve — a missing requirement, a design choice with trade-offs, approval to proceed, or information that lives outside your tools.

After you call `question()`, your instance **pauses**. The user is notified and answers in the UI. Their answers are delivered as a HumanMessage and your instance **resumes** automatically.

## Tool: `question`

```python
question(questions: list) -> str
```

Each entry in `questions` is an object with:

| Field         | Type        | Required | Default | Description                                                                 |
|---------------|-------------|----------|---------|-----------------------------------------------------------------------------|
| `id`          | str         | no       | auto    | Unique identifier. Auto-generated if omitted; useful when you want to match an answer back to a specific question. |
| `text`        | str         | yes      | —       | The question shown to the user. Be concrete and decision-oriented.          |
| `options`     | list[str]   | no       | —       | Predefined choices. Rendered as selectable buttons in the UI.               |
| `allow_custom`| bool        | no       | `true`  | If `true`, the user may type a free-form answer in addition to any `options`. |
| `required`    | bool        | no       | `true`  | If `false`, the user can skip the question.                                 |

Return value is a short placeholder string; the real answers arrive later as a HumanMessage when the instance resumes.

## Pause / Resume Behavior

- Calling `question()` **pauses** your instance immediately after the tool returns. The graph commits the pause flag before routing back to the agent node.
- **Only one pending question pack per instance at a time.** Do not ask another `question()` while waiting for answers — wait for the HumanMessage containing the responses, then continue.
- When the user answers, a HumanMessage with their responses is injected through the normal resume path and your instance resumes from its checkpoint. Treat the resumed HumanMessage like any other user message and act on the answers directly.
- If the user skips an optional question (`required=false`) or a question is cancelled, you will receive an empty / cancel-shaped answer — handle it the same way.

## When to Use

- You need a **decision** between alternatives with non-obvious trade-offs.
- You need **clarification** on a requirement that is ambiguous in the request.
- You need **approval** before taking a risky, irreversible, or expensive action (deletions, deployments, external side effects).
- You need **information** that only the user has — credentials, preferences, environment specifics, business rules.

**Do not** use `question()` for things you can answer yourself by reading files, searching code, or asking another agent. Reserve it for human-in-the-loop gates.

## Examples

### Decision question with options

```json
{
  "questions": [
    {
      "id": "db-choice",
      "text": "Which database should the new service use?",
      "options": ["Postgres", "SQLite", "MySQL"],
      "allow_custom": false,
      "required": true
    }
  ]
}
```

### Clarification question with custom answer allowed

```json
{
  "questions": [
    {
      "id": "deploy-target",
      "text": "Which environment should I deploy to?",
      "options": ["staging", "production"],
      "allow_custom": true,
      "required": true
    },
    {
      "id": "notify-channel",
      "text": "Where should I post the deployment notification? (optional)",
      "allow_custom": true,
      "required": false
    }
  ]
}
```
