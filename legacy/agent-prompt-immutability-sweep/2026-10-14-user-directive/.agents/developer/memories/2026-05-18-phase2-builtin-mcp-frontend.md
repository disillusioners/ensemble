# Phase 2 Frontend UI — Built-in MCP Servers

## Key Learnings:

1. **Backend/Frontend field name mismatches are critical**: Backend used `options` for select choices but frontend initially used `choices`. Always cross-reference backend models with frontend TypeScript interfaces.

2. **`display_name` vs `name`**: Backend `BuiltinServerTemplate` has both. Frontend must include `display_name` and use it in UI (dialog titles, dropdown labels).

3. **Don't include fields backend doesn't send**: Frontend had `default_config` but backend doesn't return it.

4. **Boolean toggle labels should reflect current state**: Use `getFieldValue(field.key)` not `field.default`.

5. **Never call `ngOnInit()` directly**: Create proper `resetForm()` method.

6. **Parallel execution worked**: Tasks 1-3 (types, service, schema form) were independent. Tasks 4-5 (list, dialog) depended on batch 1.

## Architecture:
- ConfigSchemaFormComponent: standalone, `input()` signals, `output()` emitters
- Dialog: computed signals for mode detection (edit/builtin-configure/template)
- Server list: computed signals for separation (builtInServers, userServers, unconfiguredTemplates)
- Service signals are `readonly` for direct component access
- Error handling via MatSnackBar in components

## Commit: 5a4bb23
