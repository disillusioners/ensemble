# Coder Tools

You have access to these tools for implementing code changes.

---

## `bash`

Execute shell commands.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `command` | string | Yes | - | The bash command to execute |
| `timeout` | int | No | 120 | Timeout in seconds |
| `workdir` | string | No | None | Working directory |

**Example:**
```
bash(command="npm test", workdir="/path/to/project")
```

---

## `read_file`

Read file contents with line numbers.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `path` | string | Yes | - | File path to read |
| `offset` | int | No | 1 | Start line (1-indexed) |
| `limit` | int | No | 2000 | Max lines to read |

**Example:**
```
read_file(path="src/main.py", offset=10, limit=50)
```

---

## `list_directory`

List directory contents.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `path` | string | No | "." | Directory path |
| `show_hidden` | bool | No | False | Show hidden files |

**Returns:** Directory listing with type indicators (`/` for dirs, `@` for symlinks, `*` for executables)

---

## `glob_files`

Find files matching a pattern.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pattern` | string | Yes | - | Glob pattern (e.g., `**/*.py`) |
| `path` | string | No | "." | Base directory |

**Returns:** Matching file paths, sorted by modification time (newest first)

**Example:**
```
glob_files(pattern="**/*.test.ts")
```
