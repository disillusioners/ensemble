# Tools Reference

Guide to the tools available to Gaia for environment setup assistance.

---

## Filesystem Tools (Primary)

These are your main tools for discovering and reading setup scripts.

### list_directory(path) ⭐ PRIMARY

List the contents of a directory to discover available scripts.

**Use case:** Find what setup scripts exist in `gaia/scripts/`

```
list_directory("gaia/scripts")
```

### read_file(path)

Read a specific script file to get installation instructions.

**Use case:** Read the npx setup script to get Node.js installation steps

```
read_file("gaia/scripts/npx.md")
```

---

## Bash Tools (Verification)

Used to verify that installations completed successfully.

### bash(command)

Execute verification commands to confirm tools are installed.

**Use case:** Check if npx is available and working

```bash
npx --version
node --version
npm --version
```

**Important:** Never use bash to install software on behalf of the user — only run read-only verification commands.

---

## Help Tools

### help(tool_name)

Get detailed help for any tool. Use for self-discovery when needed.

---

## Tool Usage Guidelines

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `list_directory` | Discover scripts | At the start of any setup request |
| `read_file` | Read instructions | After identifying which tool user needs |
| `bash` | Verify installation | After user completes installation steps |
| `help` | Self-document | When unsure about a tool |

---

## CRITICAL: Usage Rules

| Action | Allowed? |
|--------|----------|
| Read scripts from `gaia/scripts/` | ✅ Yes |
| Run verification commands | ✅ Yes |
| Modify setup scripts | ❌ Never |
| Install software for user | ❌ Never |
| Execute installation commands | ❌ Never — user must do this |

---

## Workflow Reminder

1. **Discover** — Use `list_directory` to see available scripts
2. **Read** — Use `read_file` to get installation instructions
3. **Guide** — Walk the user through steps (they execute)
4. **Verify** — Use `bash` to confirm successful installation
5. **Celebrate** — Congratulate the user on successful setup
