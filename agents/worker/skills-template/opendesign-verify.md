---
version: 1.0.0
category: execution
auto_load: false
---

capability_check("opendesign")

# opendesign-verify — consume the OpenDesign capability and report its tool surface

You are the CONSUMER half of the designer-agent bootstrap loop (the
installer half is `install-opendesign`). Your `requires:` declares the
capability you consume — `{mcp: [opendesign], tools: [bash]}` — so
this skill is ONLY loadable into a turn whose pre-flight can see the
`opendesign` MCP server. The pre-flight below is mandatory-first: it
decides between the two, and only two, outcomes of this skill.

## Outcome A — capability miss (pre-flight returned `missing` or `unconfigured`)

Do NOT attempt the verification steps. Emit ONE `Result:`-prefixed
escalation envelope on the child-report lane and stop:

```
Result: {"kind": "capability_missing", "capability": "opendesign",
         "installer_skill": "install-opendesign",
         "detection_evidence": "<the pre-flight detection_evidence string>",
         "blocker_scope": "this_task",
         "resume_hint": "step_after_install", "policy_denied_reason": null,
         "ts": "<ISO-8601 now>"}
```

Notes on the envelope:

- `kind` is `capability_missing` for a `missing` pre-flight; for an
  `unconfigured` pre-flight (row present, binding missing) emit
  `kind: "installed_but_unconfigured"` instead — same envelope shape,
  and the installer's KMS-bind step resolves it.
- `installer_skill` names the skill the designer must dispatch:
  `install-opendesign` (the only ratified installer for this
  capability; see the capabilities registry).
- NEVER fabricate a passing verification. NEVER call `bash` to install
  the server yourself — generic installs bypass the marker discipline.
- After emitting the envelope, END THE TURN. The resume arrives via
  `job_continue` (Outcome B).

## Outcome B — resume via `job_continue` (the `[resume]` convention)

The resume message carries a `[resume]`-tagged JSON block (canonical
spec: `install-opendesign` skill body §"[resume] convention"; parser:
`daemon.services.capability_resolver.parse_resume_message`):

```
[resume] {"capability_id": "opendesign", "status": "...",
          "tools_now_available": ["mcp_opendesign_*"],
          "resume_from": "step_after_install"}
```

Consumer contract (mirrors the installer's — both halves pin the same
convention):

1. Parse the `[resume]` block. A malformed block (unparseable JSON,
   missing any of the four fields) is an ESCALATION, not a crash:
   emit the Outcome-A envelope with
   `detection_evidence: "malformed [resume] block: <reason>"` and stop.
2. Re-run `capability_check("opendesign")` — never trust the carried
   `status`; the fresh check is the single source of truth. If it is
   still not `present`, emit the Outcome-A envelope with the fresh
   evidence and stop.
3. Continue at `resume_from` (`step_after_install` → the verification
   below). Honoring `resume_from` is REQUIRED.

## Verification (only reached with `capability_check` → `present`)

Report the live `mcp_opendesign` tool surface:

1. List the `od_*` tools now available to this instance (they appear
   as `mcp_opendesign_<tool>` — `od_list_designs`, `od_get_design`,
   and the rest of the 10-tool surface).
2. Emit ONE plain `Result:` summary line on the child-report lane
   naming the tools you can see, e.g.:

```
Result: opendesign capability present — mcp_opendesign tool surface:
mcp_opendesign_od_list_designs, ... (N tools). Verification complete.
```

Then STOP. This skill verifies and reports; it does not design. Design
work re-enters through the designer agent with the capability present.

Day-1 host note (P2-WP4 §6, accepted): the OD daemon itself may be
absent on this host — the MCP server still starts and lists its tools,
which is the surface this skill verifies. Actual design operations
failing at the OD-daemon seam is an ops-lane gap, NOT a verification
failure; note it in the report body if you observe it.
