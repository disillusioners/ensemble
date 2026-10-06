# ADR-PLUG-001 — Plugin Subsystem v1: Frozen Interface Contracts + Opendesign MCP-Lane Retirement

**Date:** 2026-10-06 · **Status:** PROPOSED — direction user-ratified (plugin_direction_status: RATIFIED, 2026-10-06); this ADR records the v1 freeze + retirement sequence for the record.
**Artifacts:** `architecture-recommendation.md` · `v1-interface-contracts.md`

## Context

The plugin direction is ratified: three tiers (native core / universal wrapper / plugins), paths B+C supported with A fenced-future, path registry extensible, opendesign = plugin #1, backend-mostly, no runtime plugin framework, resource-reuse over system adoption. The user opened the build gate (2026-10-06): "start small, improve in future. Need good overview architecture, design patterns, interfaces NOW, so later we extend rather than refactor" — and confirmed opendesign EXITS the MCP lane entirely.

## Decision

1. **The six v1 interface contracts are ADOPTED AS FROZEN** per `v1-interface-contracts.md`: plugin declaration schema · manifest v1 vocabulary (3-class provenance, tag-only per-class pins, per-class alarm owners, license carry, divergence register, positive `execution_mode`) · Port data-contract rules (JSON-serializable, schema-CI-guarded, typed error envelope, three-role seam) · path-type registry (`path_types.yaml`; D/E/F = add a row, no tier-1 edits, no tier reshaping) · sync-runner interface (dry-run default, sole-writer rules, fail-closed refusal set) · consumption interface (`plugin_ref` version visibility, named consumers).
2. **Convention versioning:** manifest `schema_version` SemVer — additive-only on `1.0.x`; minor bumps carry a dual-schema migration window; major bumps require user ratification. Extension happens through the frozen registry + additive fields, not through refactors of the frozen surface.
3. **Opendesign = plugin #1, C-dominant with a B element for generation.** Data layer (154 systems + 115 templates + 13 craft + 106 prompt-templates) vendors copy-freely; the prompt-code layer is snapshot-with-drift-alarm; Turn-3/compose-brief orchestration is own-outright; brand presets and the OD daemon are parity-boundary exclusions. Generation executes through an ensemble-side adapter with completeness gates INSIDE (finish_reason/usage visibility, parse5 EOF, lint), on the tier-1 LLM proxy. Provider mode (Python-native compose vs TS sidecar) is a pending user decision gated on the port-surface probe; the Port contract is identical either way.
4. **MCP retirement is a three-stage, fail-closed transition:** STAGE 1 BUILD (slices ①–⑤: tier-2 minimal, plugins/opendesign snapshot, native generate tool, designer lane rewired to plugin skill + native tool — `od_*` references rewritten, last-effort text-fallback semantics unchanged) → STAGE 2 PARITY VALIDATION (⑤b: native is the used path; explicit evidence artifact; any failure loops back and holds retirement) → STAGE 3 RETIRE THE SEAM (⑦: deregister the opendesign MCP server, drop its BYOK config, stop the OD daemon unit — it becomes non-load-bearing; its own retirement is a separate post-parity decision). The ensemble's MCP capability itself STAYS (context7 / plane / webfetch unaffected).
5. **dsh borrow items are consumed into v1** (per the borrow-NOW decision): (a) the exhaustive divergence register is part of the manifest spec for every snapshot-with-drift-alarm path, with the sync rule "re-apply or drop, update the log either way"; (b) the three-role seam rule (Definition ours / Provider = our wrapper / Consumer named) is the design-time gate for every adapter; (c) the anti-borrow rationale is recorded beside pillar (f): **no runtime plugin ABI, no in-process third-party code, no compatibility-exemption UX** — deepseek-harness demonstrates the full price of "everything is a plugin" (mechanism weight, onboarding tax, testing tax, compatibility churn, unsandboxed host code); that external evidence validates the native-core pillar, and we deliberately do not buy that composability.

## Consequences

- Plugins authoring against `"1.0.0"` keep working across additive changes; breaking the frozen surface is a deliberate, user-ratified event.
- Tier-1 stays structurally blind to plugin internals (sentinel-enforced); no runtime framework can accrete unnoticed (pluggy-tripwire).
- Every drift has an owner from day one; staleness is visible at promote; "4.5 months silent" becomes structurally impossible.
- The opendesign MCP dependency ends with a parity-gated, reversible-if-incomplete retirement rather than an abrupt cutover.
- Cost: the convention must be maintained (divergence registers, alarm ownership, sync discipline) — the accepted price of updatability (inv verdict: A's prompt drift is unfixable by version bump; B owns the loop where every observability gap lived).

## Alternatives rejected

- **Runtime plugin framework (pluggy-style):** mechanism weight with no customer in v1; the escape path (grow out of the Port registry, fenced to engine-only giants) is named and closed by default.
- **Path A (host the OD/dsh runtime):** undisciplined vendoring converges here; pillar (f) erosion; fenced behind explicit user-granted exceptions only.
- **Fix-the-MCP-path:** retains the unfixable frozen-snapshot prompt drift and the blind SSE relay's silent-partial failure mode (live 2/2 failure, 2026-10-06 smoke).
- **Indefinite strangler (permanent hybrid):** rejected by user steer — the bridge is a bounded 3-stage retirement with a fail-closed parity gate.
