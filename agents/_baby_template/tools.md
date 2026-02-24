# Tools

Available tools for this agent.

---

## `inner_soul`

Remember, learn, or change yourself. Just say what you want — I handle the rest.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `intent` | string | Yes | What you want: `remember`, `learn`, `change` |
| `content` | string | Yes | What to remember/learn/change |

**What happens based on intent:**

| Intent | Action |
|--------|--------|
| `remember` | Stores as timestamped file in `memories/` |
| `learn` | Stores in `memories/` + checks if pattern should evolve workflow |
| `change` | Proposes change to `workflow.md` or `soul.md` (may need approval) |

**Examples:**
```
inner_soul(intent="remember", content="User prefers TypeScript over JavaScript")
inner_soul(intent="learn", content="Iterative testing catches bugs earlier")
inner_soul(intent="change", content="Add 'review own code' step before reporting done")
```

---

{{TOOLS}}

---

*Tools are assigned during agent initialization based on purpose and domain.*
