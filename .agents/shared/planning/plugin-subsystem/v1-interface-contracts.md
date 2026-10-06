# Plugin Subsystem v1 — Interface Contracts (FROZEN surface definition)

**Date:** 2026-10-06 · **Companion to:** `architecture-recommendation.md` · **Decision record:** ADR-PLUG-001 in `architecture-decision-record.md`
**Rule of the doc:** everything marked FROZEN here is the v1 contract surface — changing it is a versioned, breaking event. Everything marked EVOLVABLE is internal and free. The versioning rule (§8) governs the transitions.

**Naming adjudication:** the manifest file is `MANIFEST.yaml` (JSON-Schema-validated YAML; matches the `skill-set.yaml` precedent). `PLUGIN.toml` was considered and set aside — the schema-CI guard and Port tooling are JSON-Schema-based; YAML keeps one validation stack. Class directory names use underscores: `copy_freely/`, `snapshot_with_drift_alarm/`, `own_outright/` (r1's hyphenated names `copy-freely` / `port-with-alarm` / `own-outright` map 1:1; `port-with-alarm` is renamed `snapshot_with_drift_alarm` per the dsh-reference refinement).

**Placement (recommended, pending user confirmation — behavior frozen regardless):** frozen convention assets (`manifest.schema.json`, `port.schema.json`, `execution_mode.enum.json`, `path_types.yaml`, `ci_runner.py`) live in `plugins-convention/` at repo root — NOT inside `plugins/` (a plugin must never be able to mutate the schema or the runner; `plugins/` stays pure tier-3 content). Tier-2 runtime code lives in the `daemon/plugin_subsystem/` package.

---

## 1. Plugin Declaration Schema (the directory-level contract)

**Purpose.** A plugin is a self-describing tree rooted at `plugins/<name>/` with a manifest discoverable by path; nothing else.

```
plugins/<name>/
  MANIFEST.yaml                # REQUIRED; validates against manifest.schema.json
  copy_freely/                 # optional subtree; clean-pulled, never authored locally
  snapshot_with_drift_alarm/   # optional subtree; alarm + reconcile only
  own_outright/                # optional subtree; ensemble-authored, never synced
  adapter/                     # required iff execution_mode = lifted-symbol | hosted-runtime
  skills/                      # required iff plugin-skill consumption; one YAML per skill
```

Manifest MUST contain (minimum set): `schema_version: "1.0.0"`, `plugin.name` (matches dir), `plugin.license` (SPDX), `plugin.integration_path` (`"B"` | `"C"`; `"A"` only with fence grant), `plugin.execution_mode` (positive declaration), and ≥1 provenance-class section. Parity boundary is a manifest section (§2) — no separate file.

**Invariants + guards.** Manifest path is part of the contract (rename = breaking). Manifest size hard-cap 64 KB. No symlinks inside `copy_freely/`. `own_outright/` is writable by tools but invisible to sync.

**Refusal rules.** Missing/invalid manifest ⇒ refuse to register · missing parity section ⇒ refuse · `integration_path: "A"` without `fence_grant` ⇒ refuse · `adapter/` missing for lifted-symbol/hosted-runtime ⇒ refuse at VENDORING (never runtime).

**Changing later breaks:** every plugin. Directory-layout or manifest-name change = major bump.

---

## 2. Manifest v1 Vocabulary

```yaml
schema_version: "1.0.0"             # SemVer; see §8
plugin:
  name: "<kebab-name>"               # matches dir name
  license: "<SPDX-id>"               # REQUIRED, validated
  upstream:
    repo: "<url>"
    tag_pin_per_class:                # TAG-ONLY pins, per class (commits/SHAs/branches refused)
      copy_freely: "v0.16.1"
      snapshot_with_drift_alarm: "v0.16.1"   # absent ⇒ inherit copy_freely pin
  integration_path: "B" | "C"         # "A" only with fence_grant
  execution_mode: "resource-only" | "lifted-symbol" | "hosted-runtime"   # POSITIVE; absent ⇒ REFUSE
  # B-path fields (required iff execution_mode=lifted-symbol):
  lifted_symbol: "<name>"
  entrypoint: "adapter/entry.ts"      # ≤200-line tripwire (CI: alarm 220, refuse 300)
  ipc_version: 1
  # A-path fields (FENCED; required iff execution_mode=hosted-runtime):
  hosted_runtime_deps:
    runtime_pin: "<exact-node-version>"        # ranges refused (strict peer-dep)
    profile_id: "<sdk-minimal|...>"
    ipc_schema_pin: "<semver>"
    capability_allowlist: [<port-id>, ...]     # static, never runtime-open
  fence_grant:                          # required iff integration_path=A
    rationale: "<short>"
    granted_by: "<user-confirmation-or-job-id>"
    granted_at: "<iso8601>"

copy_freely:
  paths: ["data/", "presets/"]          # globs relative to tree root
  alarm_owner: "<role-or-team>"         # REQUIRED per class (empty ⇒ refused manifest)
  escalation: "block-promote-after-days"  # default N=14 — EVOLVABLE placeholder

snapshot_with_drift_alarm:
  paths: ["prompts/", "compose/"]
  alarm_owner: "<role-or-team>"
  escalation: "block-promote-after-days"
  divergence_register:                   # REQUIRED for this class; numbered, exhaustive
    - id: 1
      files: ["prompts/x.ts"]
      delta: "<one-line>"
      rationale: "<one-line>"
      upstream_ref: "<PR-or-commit>"    # optional
      pinning_test: "<test-name-or-path>"

own_outright:
  paths: ["compose_brief/"]             # authored locally, never synced

parity_boundary:                        # required section
  intentionally_not_vendored:
    - path: "ui/"
      reason: "<one-line>"
  not_executed:                          # C-path NOT-EXECUTED declarations live here
    - path: "index.ts"
      reason: "inert bytes under resource-only"
```

**Sync rule for divergences (verbatim convention):** "re-apply or drop, update the log either way." Where a divergence could plausibly be upstreamed, shape it as an upstreamable PR against a fork so reconciliation can degrade to merge — and retire the entry when it lands.

**Invariants + guards.** `execution_mode` positive or refused (silence is not permission) · per-class `alarm_owner` required · SPDX valid or refused · tag-only pins (tombstone guard) · `divergence_register` required-from-day-one on non-empty `snapshot_with_drift_alarm` paths (empty register on non-empty path = refused).

---

## 3. Port Data-Contract Rules

**Purpose.** Keep every Port a pure DATA contract so each wrapper is adapter-not-rewrite. One Port, N Adapters; the Port is derived from consumer workflow use-cases (never from a tool registry count).

```yaml
port_id: "od.generate"            # stable, dotted
version: 1                         # Port-level version
definition:                        # OWNED BY US (three-role seam — Definition role)
  inputs_schema: { ...json-schema... }    # ALL fields JSON-serializable
  outputs_schema: { ...json-schema... }
  errors:
    envelope: {ok: false, code, message, details?}   # typed envelope, MANDATORY at every Port
  capability_tags: ["generation", "design"]          # catalog only, not routing
provider:                          # Provider = OUR wrapper — the plugin is a resource, never a role
  adapter_id: "od.generate.v1"
  path: "B" | "C" | "A"
consumer:                          # NAMED consumers only
  - "designer.generate_draft"
  - "job.generate_mockup"
```

**Guards.** `ci_runner.py` asserts zero non-JSON-serializable types in every Port schema (negative tests for raw object/callable/datetime-without-format/binary-without-contentEncoding) — a match fails the Port, the adapter is not built. Out-of-schema output fields are dropped at the boundary. **Three-role seam gate:** no adapter is approved until Definition + Provider + Consumer are ALL named; Definition and Provider are ours; the plugin is never a role. A provider-without-consumer PR is rejected at the gate regardless of CI status.

**Refusal rules.** Non-serializable type ⇒ CI refusal · Provider referencing plugin source directly (bypassing our wrapper) ⇒ seam-gate refusal · anonymous consumer (`["*"]`) ⇒ seam-gate refusal · unknown `port_id` ⇒ registry refusal.

**Mode note (opendesign):** the generation Provider may be Mode P (Python-native compose) or Mode T (TS sidecar) — Provider internals are EVOLVABLE behind the frozen Port; the choice is a Decisions-Pending item, not a contract fork.

---

## 4. Path-Type Registry Interface (THE extension point — frozen in v1)

```yaml
# plugins-convention/path_types.yaml
path_types:
  B:
    display_name: "Port-wrapping"
    fence: false
    allowed_execution_modes: ["lifted-symbol"]
    required_manifest_fields: ["lifted_symbol", "entrypoint", "ipc_version"]
    vendoring_check: "importable_ctx_free"      # registered check NAME (declarative)
    smoke_fixture: "fixtures/path_b_smoke.yaml"
    sync_default: "snapshot_with_drift_alarm"
    classification: "vendoring-time"            # NEVER runtime
  C:
    display_name: "Resource-izing"
    fence: false
    allowed_execution_modes: ["resource-only"]
    required_manifest_fields: []
    vendoring_check: "resource_only_purity"
    smoke_fixture: "fixtures/path_c_smoke.yaml"
    sync_default: "copy_freely"
    classification: "vendoring-time"
  A:
    display_name: "Host-shim adapter"
    fence: true                                  # ENGINE-ONLY EXCEPTION
    fence_evidence_required: true
    allowed_execution_modes: ["hosted-runtime"]
    required_manifest_fields: ["hosted_runtime_deps", "fence_grant"]
    vendoring_check: "hosted_runtime_validated"
    smoke_fixture: "fixtures/path_a_smoke.yaml"
    sync_default: "snapshot_with_drift_alarm"
    classification: "vendoring-time"
```

A path-type declaration MUST provide: (a) `vendoring_check` name; (b) `smoke_fixture`; (c) `allowed_execution_modes`; (d) `required_manifest_fields`; (e) `fence: bool` (fenced rows require evidence + manifest `fence_grant`); (f) `sync_default`. The registry is DATA, not code — no executable back-edge, no runtime loading.

**Adding D/E/F later:** author a row + register the check-function name and fixture. No tier-1 code changes; the three tiers do not reshape. CI cross-checks every manifest's `integration_path` against registry keys and every `execution_mode` against the row's allowlist.

**Refusal rules.** Unregistered path ⇒ refuse · fence row without `fence_evidence_required` ⇒ registry CI refusal · fence-STRIPPING (editing `fence: false` onto A) ⇒ registry CI refusal + alarm to the path-type registrar role.

---

## 5. Sync-Runner Interface

```yaml
sync(plugin: "<name>",
     target_class: "copy_freely" | "snapshot_with_drift_alarm",
     dry_run: true)                  # default TRUE — shadow-before-apply is the cheap path
```

Inputs resolved fresh every call (manifest re-read; no cache). Sync-runner is the SOLE writer of `copy_freely/` and `snapshot_with_drift_alarm/`; `own_outright/` is never written (hard rule, no override). Copy-freely pulls stage into temp + atomic rename (mid-pull crash = whole-or-nothing).

```yaml
sync_result:
  plugin: "<name>"
  target_class: "<class>"
  upstream_tag: "<tag>"
  action: "clean_pulled" | "alarmed" | "refused" | "no_change"
  diff_summary: {files_added: int, files_modified: int, files_removed: int}
  alarm: {divergence_register_entry: {id, files, delta, rationale, pinning_test, status}}   # iff alarmed
  refusal: {code: "absent_execution_mode" | "own_outright_mutation" | "license_invalid"
                | "fence_missing" | "non_tag_pin" | "tag_missing_upstream" | ...,
            message: "<one-line>"}                                                            # iff refused
  staleness_age_days: int      # (now − upstream_tag commit date) — promote-gate + readout input
```

**Refusal rules (fail-closed set):** absent `execution_mode` ⇒ refuse · sync into `own_outright/` ⇒ refuse · invalid SPDX ⇒ refuse · A-path without fence grant ⇒ refuse · pin not a git tag ⇒ refuse · upstream tag missing / force-pushed ⇒ refuse + alert · misclassification detected at vendoring ⇒ refuse (fails the VENDORING, never the runtime).

**Drift-event emission:** payload `{plugin, class, divergence_id, files, delta, rationale, pinning_test, observed_at, observed_tag}` into the tier-1 trigger engine (single subject; no new bus); routed to the manifest-declared `alarm_owner`; unowned for `escalation` days ⇒ block promote.

**Staleness surfaces (v1):** `sync_result` field + admin status tool + `promote_staleness_check` predicate wired to `stage.sh`/`promote.sh` (argv-only override, journaled — same discipline as the freshness guards). An HTTP health endpoint is an EVOLVABLE later addition; the report SHAPE above is frozen.

---

## 6. Consumption Interface

Two and only two shapes (skills-not-new-agents; dedicated agent only if need demands — the standing escape hatch).

**Plugin-skill** — source lives in the plugin tree, registered into the existing skill system at vendoring/load:

```yaml
# plugins/<name>/skills/<skill_id>.yaml
skill_id: "opendesign.list_systems"
schema_version: "1.0.0"
plugin_ref:                          # VERSION-VISIBILITY surface — what the agent sees
  name: "opendesign"
  manifest_schema_version: "1.0.0"
  upstream_tag: {copy_freely: "v0.16.1", snapshot_with_drift_alarm: "v0.16.1"}
  license: "Apache-2.0"
content:
  description: "<...>"
  body_markdown: |
    # skill text; may reference vendored paths (e.g. plugins/opendesign/copy_freely/systems/)
  vendored_references:
    - alias: "systems"
      path: "copy_freely/systems/"   # must resolve at skill-load time; traversal outside the tree refused
consumption:
  by: ["worker/designer"]             # NAMED consumers; empty/anonymous refused
```

**Adapter-tool** — invoked via the agent-tool lane, backed by a Port (§3); registration carries the same `plugin_ref` so the version is visible at call time and alarms correlate to consumers.

**Invariants.** `plugin_ref.upstream_tag` MUST be present at load (the worker sees "data=vX, prompt=vY" — read-your-writes, never eventual) · `vendored_references` resolve or the skill refuses to load · skills/tools never import plugin source directly (C-path reads content via the registry; B/A invoke via the Port) — direct import = seam-gate refusal.

**Designer-lane note (first concrete consumer):** at slice ⑤ the designer prompt's `od_*` MCP tool references are rewritten to plugin skill + native tool; last-effort text-fallback semantics unchanged. The rewiring is a consumer migration, NOT a contract change.

---

## 7. Tier-Boundary Sentinels (CI-guarded invariants)

- Vocabulary confinement: `execution_mode` / `lifted_symbol` / `ipc_version` / `hosted_runtime_deps` may appear ONLY under `daemon/plugin_subsystem/**` and `plugins-convention/**` — the sentinel is a scoped grep, not a repo-wide one (tier-2 code legitimately lives inside the daemon repo; the invariant is that TIER-1 modules stay structurally blind to manifest vocabulary).
- No-import invariant: no module outside `daemon/plugin_subsystem/` imports from `plugins/` or references plugin symbols.
- No-runtime-loading: no `importlib.import_module` / entry-point scanning in any registry (pluggy-tripwire).
- Post-⑦: `open-design-mcp` references in `daemon/` = zero.

## 8. Frozen vs Evolvable (per element) + Convention Versioning Rule

| Element | v1 status | Bump rule |
|---|---|---|
| Directory layout + `MANIFEST.yaml` name | FROZEN | major |
| Manifest field names/types | FROZEN | major |
| `execution_mode` enum | FROZEN (new value = minor + migration window) | minor |
| 3-class vocabulary | FROZEN (new class = major; 4th-class promotion of resource-only stays a field in v1) | major |
| Divergence-register entry shape | FROZEN (new optional field = additive) | minor/additive |
| `alarm_owner` field-set | FROZEN; escalation default N EVOLVABLE | — |
| License carry (SPDX field) | FROZEN; validator version EVOLVABLE | — |
| Parity-boundary section | FROZEN; row fields EVOLVABLE | — |
| `fence_grant` block | FROZEN — removal requires new user ratification | major |
| Port JSON-Schema USAGE + typed envelope + three-role gate | FROZEN; per-Port schemas gated by Port `version` | Port-level |
| `path_types.yaml` schema + fence semantics | FROZEN; row ADDITIONS non-breaking | — |
| `sync_result` core + refusal-code enum | FROZEN; new optional fields additive; new required = minor | minor/additive |
| `dry_run=true` default | FROZEN | major |
| Drift-event payload core | FROZEN; new fields additive | additive |
| `plugin_ref` shape | FROZEN; new fields additive | additive |
| `consumption.by` + `vendored_references` semantics | FROZEN; alias set EVOLVABLE (alias REMOVAL breaking for referencing skills) | — |
| Schema-CI runner BEHAVIOR | FROZEN; new check functions EVOLVABLE via name registration | — |
| Generation Provider internals (Mode P/T) | EVOLVABLE behind the frozen Port | Port-level |
| Escalation N, cadence policy, health-endpoint surface, per-Port schema content | EVOLVABLE | — |

**Versioning rule (one paragraph).** `schema_version: "1.0.0"`, SemVer. `1.0.x` = additive only (no new required fields, no removals, no enum deletions, no renames). `1.x.0` = new optional fields / enum values / sections / path types — permitted with a MIGRATION WINDOW: the runner carries both schemas for one minor cycle. `x.0.0` = vocabulary change, enum tightening, field removal, fence removal — requires a migration plan AND user ratification. The first additive change is `1.0.1`, never v2. Plugin #1 authors against the literal `"1.0.0"`.
