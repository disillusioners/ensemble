# plugins-convention — Frozen Convention Assets (Plugin Subsystem v1)

This directory holds the **frozen convention assets** for the ensemble plugin
subsystem (tier 2): the manifest JSON-Schema, the execution-mode enum, the
path-type registry data, the vendored SPDX validator list, and the CI runner
wrapper. It sits at repo root — deliberately **NOT** inside `plugins/`
(a plugin must never be able to mutate the schema or the runner; `plugins/`
stays pure tier-3 content).

- **Source of truth:** `.agents/shared/planning/plugin-subsystem/v1-interface-contracts.md` ("CON"; FROZEN surface) ·
  `architecture-recommendation.md` · ADR-PLUG-001.
- **Runtime consumers:** `daemon/plugin_subsystem/` (tier-2 vocabulary zone —
  the ONLY place inside the daemon where manifest vocabulary may appear; see
  the scoped sentinels, CON §7).
- **Rule of the convention:** everything marked FROZEN is the v1 contract
  surface — changing it is a versioned, breaking event. Everything marked
  EVOLVABLE is internal and free.

## Assets

| File | Role | Status |
|---|---|---|
| `manifest.schema.json` | Manifest v1 vocabulary as JSON-Schema; `additionalProperties: false` at every level, unknown fields refused by construction | FROZEN (§8 table) |
| `execution_mode.enum.json` | The 3-value enum: `resource-only` \| `lifted-symbol` \| `hosted-runtime` | FROZEN (new value = minor + migration window) |
| `path_types.yaml` | Path-type registry rows B / C / A — DATA, not code; D/E/F later = add a row | FROZEN schema + fence semantics; row ADDITIONS non-breaking |
| `spdx_ids.json` | Vendored ids-only SPDX allowlist for exact-match license validation | Field carry FROZEN; **validator list EVOLVABLE** |
| `ci_runner.py` | Thin stdlib-only CLI wrapping `daemon.plugin_subsystem.schema_ci` | Runner BEHAVIOR FROZEN; new check functions EVOLVABLE via name registration |

## Manifest vocabulary (per-class semantics)

- `copy_freely` — clean-pulled upstream data; never authored locally.
  `alarm_owner` REQUIRED (empty ⇒ refused manifest).
- `snapshot_with_drift_alarm` — hot upstream content; alarm + reconcile only.
  `alarm_owner` REQUIRED. Non-empty `paths` ⇒ `divergence_register` REQUIRED
  and non-empty (empty register on non-empty path ⇒ manifest REFUSED).
- `own_outright` — ensemble-authored; never synced; invisible to sync.
  Carries **no** `alarm_owner` and no `divergence_register` — authored
  locally means there is nothing upstream to alarm on or diverge from
  (interpretation note: CON §2's `own_outright` block declares only `paths`;
  the schema refuses the other fields by construction).

`execution_mode` is a POSITIVE declaration: absent ⇒ refuse (silence is not
permission).

## Divergence register

Required from day one on non-empty `snapshot_with_drift_alarm` paths.
Entries are numbered and exhaustive:

```yaml
divergence_register:
  - id: 1                          # integer, numbered
    files: ["prompts/x.ts"]        # ≥1 file, tree-root-relative
    delta: "<one-line>"
    rationale: "<one-line>"
    upstream_ref: "<PR-or-commit>" # the one optional field (CON §2)
    pinning_test: "<test-name-or-path>"
```

**Sync rule for divergences (verbatim convention):**
"re-apply or drop, update the log either way." Where a divergence could
plausibly be upstreamed, shape it as an upstreamable PR against a fork so
reconciliation can degrade to merge — and retire the entry when it lands.

## Parity boundary

`parity_boundary` is a **required manifest section** (not a separate file).
It records what the plugin deliberately does NOT carry:

- `intentionally_not_vendored` — upstream paths consciously left out.
- `not_executed` — paths present in the tree that are inert bytes under the
  declared execution mode (e.g. C-path `index.ts`).

Subsections are lists that may be empty; **any row present must carry
exactly `{path, reason}`**. Row fields are EVOLVABLE (§8); the section
itself is FROZEN.

## Tag-only pins

`upstream.tag_pin_per_class` pins are TAG-ONLY. The v1 validator enforces the
**OFFLINE-PROVABLE** refusals inline (`non_tag_pin`):

- empty values;
- range-expression markers from `_RANGE_PIN_CHARS` (`^`, `~`, `>=`, `<=`,
  `>`, `<`, `*`, `x`, `X`) — range expressions are provably never tags;
- the reserved-git-literal blocklist `{HEAD, main, master, develop, latest}`
  — each is provably never a tag;
- bare hex SHAs (7–40 hex chars).

**FULL** discrimination (e.g. a tag genuinely named `main`, arbitrary branch
names, reflog inspection) lands at slice ③ where the upstream git is
actually consulted as part of vendoring-time sync. Until then, the
conservative blocklist refuses even a real upstream tag that collides with
one of the reserved literals — the safety margin is intentional, and the
docstring in `manifest_reader.py` carries the same caveat.

`own_outright` carries no pin (it is never synced).

## License carry

`plugin.license` is an SPDX id validated by **exact match** against
`spdx_ids.json`. The field carry is FROZEN; the validator list is
EVOLVABLE — it grows without breaking authored manifests. SPDX expressions
(`(A OR B)`, `A+`) are out of scope for validator v1.

## Fence semantics

Path A (host-shim adapter) is the ENGINE-ONLY FENCED exception:

- a `fence: true` row MUST carry `fence_evidence_required: true` (registry
  refusal otherwise);
- `fence: false` onto a v1-fenced path letter ⇒ registry refusal **+ alarm
  to the path-type registrar role**;
- `integration_path: "A"` requires a complete `fence_grant` block
  (`rationale`, `granted_by`, `granted_at`) in the manifest (`fence_missing`).

**Fence-routing contract (refusal-code split, matches `manifest_reader`):**

- `fence_grant` ABSENT on an A-path plugin ⇒ `missing_required_manifest_fields`
  (the registry row lists `fence_grant` as a required manifest field, so its
  absence fires the row-required-fields refusal first);
- `fence_grant` PRESENT but INCOMPLETE (any of `rationale` / `granted_by` /
  `granted_at` missing or empty-string) ⇒ `fence_missing`.

The two refusals map to distinct points in the validator: the row-required
absence fires from `_check_semantics`'s `missing_required_manifest_fields`
path (CON §4 cross-check); the incompleteness check fires from the A-path
fence-grant block of `_check_semantics`. Callers MUST handle both.

## Versioning rule (CON §8, verbatim)

`schema_version: "1.0.0"`, SemVer. `1.0.x` = additive only (no new required
fields, no removals, no enum deletions, no renames). `1.x.0` = new optional
fields / enum values / sections / path types — permitted with a MIGRATION
WINDOW: the runner carries both schemas for one minor cycle. `x.0.0` =
vocabulary change, enum tightening, field removal, fence removal — requires
a migration plan AND user ratification. The first additive change is
`1.0.1`, never v2. Plugin #1 authors against the literal `"1.0.0"`.

**Validator implementation of the rule (slice ①):** the v1 validator accepts
the literal `1.0.0` and the additive `1.0.x` family; anything ≥ `1.1.0` or a
different major is refused (`schema_version_unsupported`) until the runner
carries the newer schema.
