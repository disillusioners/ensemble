// Type stubs for the slice-⑤ OpenDesign plugin adapter sentinel.
// The real implementation lives in Python
// (daemon/plugin_subsystem/opendesign/{generate,compose_brief,save,lint}.py);
// this file exists to satisfy the manifest's ``plugin.entrypoint``
// contract + the ≤200-line tripwire alarm/refuse thresholds (CON §2).
// Plugin-tool invocation routes through the Python wrapper, not
// through the adapter entry — see
// daemon/plugin_subsystem/plugin_tool_factory.py.
export interface PluginInvocation {
  // The Port invocation shape — JSON-friendly by construction
  // (CON §3). Tools the Python Provider consumes.
  port_id: string;
  inputs: Record<string, unknown>;
}