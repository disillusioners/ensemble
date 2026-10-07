// OpenDesign plugin adapter — slice ⑤ entry point.
//
// Path-type B + Mode-P (Python-native compose). This file is the
// ≤200-line tripwire sentinel declared by the manifest's
// ``plugin.entrypoint`` field (CON §2: alarm at 220 lines, refuse
// at 300; CON §1 the tripwire operates on real adapter files).
//
// Mode-P note. The lifted-symbol idiom in CON §2 is the abstract
// noun for "our adapter invokes a capability"; the concrete
// dispatch for opendesign uses the Python-native compose Provider
// at daemon/plugin_subsystem/opendesign/{generate, compose_brief,
// save, lint}.py — invoked via the Port → tool binding in
// daemon/plugin_subsystem/plugin_tool_factory.py. This entry.ts
// exists as the structural sentinel + the documentation of the
// Mode-P decision (REC §4.2); it is NOT a runtime sidecar (the
// adapter invokes no subprocess; the LLM is the tier-1 proxy the
// daemon already uses).
//
// When the Mode-T fallback lands at the next B-path plugin, this
// file becomes the JSON-stdin → JSON-stdout wrapper for the
// subprocess; until then it stays a documentation sentinel.
//
// To keep this file well under the tripwire (CON §2 alarm 220 /
// refuse 300), the body is intentionally minimal: imports, the
// sentinel function with the docstring that documents the Mode-P
// decision, and the exported entrypoint. Plugin #2+ that needs a
// real TS wrapper extends this file (or replaces it) with the
// per-capability dispatch logic; the tripwire catches the growth
// at the 220/300 line boundary.

import type { PluginInvocation } from "./types";

// Mode-P sentinel: the Python Provider is the real implementation;
// this file documents the contract + provides the manifest's
// declared entrypoint for the ≤200-line tripwire.
//
// Returns a JSON-friendly summary of the provider's actual location
// (Python module path) so external readers (audit, triage) can
// locate the real adapter code without inspecting the daemon repo.
export function modePDocumentation(): {
  mode: "P" | "T";
  rationale: string;
  provider_module: string;
  upstream_decision_ref: string;
} {
  return {
    mode: "P",
    rationale:
      "Mode-P is the recommended default per REC §4.2. The composer " +
      "chain is string concatenation of pre-rendered template " +
      "strings with conditional inclusion + section ordering — " +
      "re-expressible faithfully in Python at one-time cost. Mode-T " +
      "(TS sidecar / lifted symbol) is the fallback iff the composer " +
      "chain proves too entangled to re-express in Python; that gate " +
      "was not tripped at slice ⑤ (probes artifact §A1.2).",
    provider_module:
      "daemon.plugin_subsystem.opendesign.{generate,compose_brief,save,lint}",
    upstream_decision_ref:
      ".agents/shared/planning/plugin-subsystem/2026-10-06-slice-5-probes.md §A1.5",
  };
}

// Exported entrypoint (declared by plugin.entrypoint in MANIFEST.yaml).
// The Python Provider is the real implementation; this function is the
// documentation sentinel + the structural surface the manifest
// points at. Plugin-tool invocation routes through the Python wrapper,
// not through this function — see plugin_tool_factory.build_tools_for_port.
export default function entry(_invocation: PluginInvocation): {
  ok: true;
  mode: "P";
  message: string;
} {
  const info = modePDocumentation();
  return {
    ok: true,
    mode: info.mode,
    message:
      "OpenDesign plugin adapter: Mode-P (Python-native compose). " +
      "Real implementation lives at " +
      info.provider_module +
      ". See " +
      info.upstream_decision_ref +
      " for the probe artifact.",
  };
}