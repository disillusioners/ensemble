# Common Tools

Use `tool_help("tool_name")` for full docs. Common tools:

## File Operations

```
read_file(path, workdir)                     # Read file with line numbers (path relative to workdir)
write_file(content, path, workdir)           # Write or append to file
edit_file(path, old_string, new_string, workdir, replace_all=False)  # Replace text in file
list_directory(path, workdir, show_hidden=False)  # List dir contents
glob_files(pattern, workdir, path=".")       # Find files by pattern
grep_files(pattern, workdir, path=".", include="", case_sensitive=False, whole_word=False)  # Search files
```

**Note**: `path` is always relative to `workdir`. Always specify workdir explicitly (typically the project directory).

## Shell

```
bash(command, timeout=120, workdir=None)  # Execute shell command
time(format_type="iso")                   # Get current time
```

## Session Management

```
spawn_session(agent_dir, session_id=None)  # Spawn new agent
send_message(session_id, message)          # Send to session queue
terminate_session(session_id)              # Kill session
list_sessions()                            # List all sessions
get_session_info(session_id)               # Get session details
```

## Project Management

```
project_create(name, project_type="general", main_directory=None, tags=[], metadata={})
project_get(project_id=None, name=None, shortname=None)  # Get by ID, name, or shortname
project_list(status=None, tags=[], limit=50)
project_search(query, limit=20)
project_update(project_id, name=None, description=None, tags=None)
project_set_status(project_id, status)     # active|paused|completed|archived
project_add_tag(project_id, tag)
project_set_metadata(project_id, key, value)
project_link(project_id, entity_type, entity_id)
project_delete(project_id)
```

## Self-Modification

```
inner_soul(intent, content)               # intent: remember|learn|change
```

## Help

```
tool_help()                                # List all tools
tool_help("tool_name")                     # Detailed docs for tool
tool_help(category="project")              # List by category
```

---

**Status values:** `active`, `paused`, `completed`, `archived`
**Project types:** `software`, `documentation`, `research`, `task`, `general`, or custom
