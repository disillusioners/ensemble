# Question Skill

`ask_questions()` is your direct line to the user. Ask whenever you need a decision, clarification, approval, or information only they have. In collaborative (SemiAuto) mode, asking early prevents wasted work — don't guess when a quick question keeps you aligned.

After you call `ask_questions()`, your instance **pauses**. The user answers in the UI, and their answers arrive as a HumanMessage that resumes your instance.

## Tool: `ask_questions`

```python
ask_questions(questions: list) -> str
```

Each entry in `questions` is an object:

| Field         | Type      | Required | Default | Description                                                                 |
|---------------|-----------|----------|---------|-----------------------------------------------------------------------------|
| `id`          | str       | no       | auto    | Unique identifier; useful for matching answers back to questions.           |
| `text`        | str       | yes      | —       | The question shown to the user. Be concrete and decision-oriented.          |
| `options`     | list[str] | no       | —       | Predefined choices, rendered as selectable buttons.                         |
| `allow_custom`| bool      | no       | `true`  | If `true`, the user may type a free-form answer in addition to `options`.   |
| `required`    | bool      | no       | `true`  | If `false`, the user can skip the question.                                 |

## When to Use

- **Decision** between alternatives with non-obvious trade-offs.
- **Clarification** on an ambiguous requirement.
- **Approval** before a risky, irreversible, or expensive action (deletions, deployments, external side effects).
- **Information** that only the user has — credentials, preferences, environment specifics, business rules.

In SemiAuto mode, prefer asking over assuming. One early question saves multiple wasted steps.

**Don't** use `ask_questions()` for things you can resolve yourself — reading files, searching code, or consulting another agent. Reserve it for genuine human-in-the-loop gates.

## Pause / Resume

- Calling `ask_questions()` **pauses** your instance immediately after the tool returns.
- **Only one pending pack per instance.** Wait for answers before asking again.
- Answers arrive as a HumanMessage via the normal resume path — treat them like any user message.

## Example

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