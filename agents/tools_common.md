# Common Tools

> ⚠️ **CRITICAL: Always specify `workdir` explicitly** for ALL file/shell operations.
> This is enforced by the system. Omitting it causes errors and requires retry.

> 📌 **Note on Skills**: Skills CLI (e.g., `opencode_skill`) have their own tools set and restrictions. The `workdir` restriction and other tools restrictions from this file do NOT apply to skills. Each skill CLI has its own tool set defined within its skill definition. Do not impose these tools restrictions when using skills.

Use `tool_help("tool_name")` for full docs. Common tools:

## File Operations

```raw
read_file(path, workdir)                     # Read file with line numbers
write_file(content, path, workdir)           # Write or append to file
edit_file(path, old_string, new_string, workdir, replace_all=False)  # Replace text
list_directory(path, workdir, show_hidden=False)  # List dir contents
glob_files(pattern, workdir, path=".")       # Find files by pattern
grep_files(pattern, workdir, path=".", include="", case_sensitive=False, whole_word=False)  # Search
```
**Rules**:

- Important: `workdir` parameters are MUST for all file operations. Always specify them to avoid errors.
- `path` is always relative to `workdir`. Never use absolute paths.

Example read_file:
```json
{
  "path": ".agents/shared/planning/<feature>/plan-overview.md",
  "workdir": "/path_to/current/working/project/directory"
}
```

## Shell

```raw
bash(command, timeout=120, workdir)          # Execute shell command (workdir required)
time(format_type="iso")                      # Get current time
```

**Rules**: Always set `workdir` to the project directory. Never omit it.

## Instance Management

```raw
spawn_instance(agent_dir, instance_id=None, instance_name=None)  # Spawn new agent
send_message(instance_id, message)           # Send to instance queue
terminate_instance(instance_id)              # Kill instance
list_instances()                             # List all instances
get_instance_info(instance_id)               # Get instance details
```

**instance_name**: Optional short name for the instance to identify it in reports. Use concise, descriptive names. Examples: `create-feature-a`, `fix-bug-b`, `refactor-auth`.

## Project Management

```raw
project_create(name, project_type="general", main_directory=None, tags=[], metadata={})
project_get(project_id=None, name=None, shortname=None)  # Get by ID, name, or shortname
project_list(status=None, tags=[], limit=50, project_type=None)
project_search(query, limit=20)
project_update(project_id, name=None, description=None, tags=None)
project_set_status(project_id, status)     # active|paused|completed|archived
project_add_tag(project_id, tag)
project_remove_tag(project_id, tag)
project_set_tags(project_id, tags)
project_add_directory(project_id, directory, as_main=False)
project_remove_directory(project_id, directory)
project_get_by_instance(instance_id)
project_get_by_directory(directory)
project_set_metadata(project_id, key, value)
project_delete_metadata(project_id, key)
project_set_shortnames(project_id, shortnames)
project_add_shortname(project_id, shortname)
project_remove_shortname(project_id, shortname)
project_link(project_id, entity_type, entity_id)
project_unlink(project_id, entity_type, entity_id)
project_delete(project_id)
```

## Self-Modification

```raw
inner_soul(intent, content)               # intent: remember|learn|change
access_memory(filename)                     # Read a memory file from your memories/ directory.
```

## Help

```raw
tool_help()                                # List all tools
tool_help("tool_name")                     # Detailed docs for tool
tool_help(category="project")              # List by category
```

---

**Status values:** `active`, `paused`, `completed`, `archived`
**Project types:** `software`, `documentation`, `research`, `task`, `general`, or custom
