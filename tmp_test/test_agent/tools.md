# Tools

## `inner_soul`

Remember, learn, or change yourself.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `intent` | string | Yes | `remember`, `learn`, or `change` |
| `content` | string | Yes | What to remember/learn/change |
| `target` | string | No | For change: `memory`, `workflow`, or `soul` |

**Example:**
```
inner_soul(intent="remember", content="User prefers TypeScript")
```
