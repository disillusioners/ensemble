# Architecture Recommendation — tool-usage digest defect fix (D1 delivery · D2 durability · D3 semantic)

Status: SETTLED recommendation for leader ratification or override. Nothing implemented.
Date: 2026-11-26T06:45Z. Author: Architect (instance a722ee0f-83a3-43ae-a42c-7e6509b862fa).
Read tree for every repo citation: `/home/nea/ensemble-src-wt-tool-usage-digest` @ `f83fb5d8113afc5b4a5a625e8b18030e9ac0c790` (branch `fix/tool-usage-digest-artifact-delivery`). `origin/latest` = `d7c15d45f650` (16 commits ahead of base: dump-lane probes, SELF_ENV fence hardening, pack review fixes); `git diff --stat f83fb5d81 origin/latest` over every path cited here is EMPTY (verified directly), so every citation holds identically at base and at origin/latest. LIVE install `/home/nea/agents-ensemble/` (current -> `releases/v0.31.59`) was READ-ONLY.
Plan of record — UNTOUCHED: `.agents/shared/planning/tool-usage-metrics-p1/architecture-recommendation.md`, sha256 `deaf040a3535431e03bb552d4549edb93e3e42803d64a9401a329186567f97cb`, git blob `d0173599fb063e24b29854af1a038ce0fe5cc2c9`.
Process disclosure: this run operated in `DEGRADED — skill bank miss` mode (the architect execution skills exist as template files `agents/architect/skills-template/*.md` but are not registered in the dynamic-skill database). All worker dispatches were executed without `load_skill` using manually embedded analytical briefs. Every load-bearing claim was verified directly by the architect.

---

## 1. DECISION SUMMARY

**PROVENANCE.** The plan of record is `architecture-recommendation.md` (Phase-3 ruling 6, **D-6 at :119**: "pin an **explicit canonical root** (config/env constant) — digest path `<root>/.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md`, same-day re-run **overwrites**") and **U8 at `phase3-plan.md:61`** ("The canonical root is the env constant **`ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` … when the env var is unset, the root resolves … to `<live install tree>/.agents/shared/planning/tool-usage-metrics-p1/digests` (the live install tree, NOT a worktree and NOT the repo checkout)"). I **HONOR both as written** and **NARROW + EXTEND** U8 in two declared ways:
- (i) **NARROW** — "the daemon's canonical project-data root" is pinned to ONE verified resolver: `resolve_live_install_root()` accepted ONLY when it passes local verification (`(root / "releases").is_dir()` for frozen, or `(root / "daemon").is_dir()` for source); the unverified house resolver otherwise degrades to a 3-up heuristic guess (`/tmp`, measured).
- (ii) **EXTEND** — an unverifiable root FAILS CLOSED (`DigestRootUnresolved` raise at write time, U8 is silent on that case), and a source checkout in development writes to its own checkout tree (U8 excludes the checkout for production but specifies nothing for dev).

I also **AMEND** `phase3-plan.md:80` (§3.12 item 12 — the artifact's location and shape) and `phase3-plan.md:97` (T2) — exact wording detailed in §3.11.

**THE R1 / R6 CONFLICT.** R1 dictates shipping a clean, immutable payload directory `releases/<v>/tool-usage-artifacts/` containing `allowlists.json` and `tool-weights.json`, where `tool-weights.json` is intentionally an unpopulated `{}` fallback artifact (uniform 230 weight per R5). Meanwhile, R6 addresses the frontend copy at `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts:213-219`, which explicitly claims to users that the daily digest ranks by "schema-token weight × frequency, zero/near-zero, allow-list intersection". This creates a direct two-fold conflict:
1. **Semantic Contradiction:** Shipping R1 with W3/R5 means the digest's weights are uniformly the fallback (230), rendering ranking strictly frequency-only. If R6 is not updated, the frontend asserts a capability that R1/R5 deliberately leave unpopulated.
2. **Release Coupling & Build Cost:** R1 is a clean backend/stage-only change. R6 modifies frontend TypeScript source, which mandates an `npm run build` execution via `scripts/upgrade/_build_frontend.sh` because `stage.sh:468` copies a pre-built `frontend/dist/frontend/browser` and refuses unbuilt staging at `:348`. The spec explicitly adjudicates this trade-off: deliver R6 as an in-lane rider commit if the lane carries the FE build, or split it to an immediate follow-up while letting the backend digest's in-file disclosure banner (§6) carry the interim honesty.

| Item | Decision | Recommendation & One-Sentence Rationale |
|---|---|---|
| **R1** | Payload Shape | **Horn B (`releases/<v>/tool-usage-artifacts/`)**: Eliminates the D4 shared-parent trap by construction by keeping immutable read artifacts in a clean, dedicated payload-root directory disjoint from any mutable output path, exactly matching the guidelines precedent (`docs/guidelines` → `releases/<v>/guidelines`). |
| **R2** | Install-Root Resolver | **Direct import of `resolve_live_install_root()` behind a local verification gate**: Reuses the tested house resolver from `daemon.repositories.source.maintenance_seed` without moving it, avoiding churn on existing grep-fence tests (`test_maintenance_results_root_equivalence.py:51`), while failing closed if the resolved path cannot be verified. |
| **R3** | Category Expansion | **Expand categories to tool names inside the reader (`allowlists()`), never in `rollup()`**: Satisfies the frozen determinism AST pin (`test_no_live_env_or_registry_read_inside_the_rollup`) by reading a pre-computed taxonomy map bundled in the schema-2 artifact, preserving literal tool names, handling `tools.deny`, and resolving the D3 namespace mismatch. |
| **R4** | Freshness Contract | **Regenerate-and-verify at stage time (`--verify`)**: Enforces that the staged `allowlists.json` matches the exact shipped `agents/` and `daemon/tools/` code at stage time, refusing the build on drift while continuing to ship reviewed, committed bytes. |
| **R5** | Weight Dimension | **W3 — Relabel heading and add disclosure banner; route population as a research follow-up**: Keeps the existing 6-column schema and `{}` file to preserve all determinism test contracts without inventing an ungrounded weight table. |
| **R6** | FE Copy Ruling | **In-lane rider text correction (keeps pinned substrings `PROXY`, `daily digest`, `rolling`)**: Aligns the UI caveat with W3's frequency ranking; if the lane cannot absorb the required `npm run build` stage leg, defer as an immediate follow-up. |
| **D5** | Loudness Mechanism | **Visible degradation banner + `unknown` cell status (never raise on content)**: Makes missing or corrupt artifacts loud in the logs (`logger.warning`/`ERROR`) and surfaces an explicit banner in the markdown digest, ensuring empty allow-lists are never confused with missing files while preserving daily digest generation. |

---

## 2. TRADE-OFF MATRIX

### Table 2.1 — Artifact Delivery Options

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Payload Delivery (Horn B — clean payload root)** | **Med**: 6 insertion points in `stage.sh` mirroring the guidelines precedent; 1 reader function. | **High**: Two small JSON files (~16 KB); trivial tree walk compared to plugins (4930 files). | **High**: Self-contained in payload; visible in `manifest.json`; clear provenance in file. | **Low**: Additive manifest keys; fail-closed stage checks; stage-local integrity matching guidelines. | **Low**: One fast `python3` verification call at stage time. | **RECOMMENDED** |
| **Embed in Binary (`ensemble.spec` datas / constant)** | **Med**: PyInstaller spec modifications; complex runtime inspection. | **Low**: Every `meta.json` allow/deny edit forces a full binary compile and re-stage. | **Low**: Binary datas are opaque to shell scripts and cannot be inspected at ceremony time. | **High**: T1 (build) vs T2 (stage) payload skew; uncommitted stage tree dirtiness is unverified. | **Med–High**: Significantly increases build cycles and test latency. | **REJECTED** |
| **Install-Tree Copy at Stage Time** | **High**: Requires custom install hooks in stage/promote and rollback restore logic. | **Low**: Breaks payload self-containment; introduces out-of-band state. | **Med**: Splits release state across versioned and unversioned install trees. | **Critical**: Staged-not-promoted releases overwrite live files; rollback leaves orphaned state. | **Low**: Minimal code, but severe operational debt. | **REJECTED** |
| **Guidelines Precedent (Baseline Reference)** | `docs/guidelines` copied to `releases/<v>/guidelines` (`stage.sh:487`); read via `Path(sys.executable).parent / "guidelines"` (`guidelines.py:173`). | Shipped as-is (static docs); no runtime generation. | Stage-local re-hash leg (`stage.sh:942-948`); manifest keys at `:894/:899`. | Precedent verified in production; serves as the template for Horn B. | Baseline reference. | **TEMPLATE** |

### Table 2.2 — Payload Directory Fork: Horn A vs. Horn B

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Horn B: Clean Payload Root (`releases/<v>/tool-usage-artifacts/`)** | **Low–Med**: Small frozen-aware fork in `_artifact_dir()` (`sys.executable.parent / "tool-usage-artifacts"` vs repo path). | **High**: Completely isolated from repo planning artifacts and gitignored scratch files. | **High**: Self-documenting; directory name explicitly conveys contents. | **Zero D4 Risk**: The shared parent path `.agents/shared/planning/...` does not exist in the payload, preventing accidental writes. | **Low**: 1 path constant. | **RECOMMENDED** |
| **Horn A: Mirror Repo Path (`releases/<v>/.agents/shared/planning/...`)** | **Low**: Reader path requires no frozen fork (`_code_root() / ".agents/..."` works in both). | **Low**: Bakes repo planning directory hierarchy into release binaries. | **Low**: Confuses read artifacts with mutable output paths (`digests/`). | **High**: Risk of copying developers' local `digests/` into payload; re-arms D4 promote-blocking trap. | **Low**: Zero reader changes. | **REJECTED** |

### Table 2.3 — Category Expansion Source Options

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Artifact Carries Taxonomy Map (Schema 2)** | **Med**: Generator runs AST extraction across `CATEGORY_MODULES` and bundles mapping into `allowlists.json`. | **High**: Reader performs pure in-memory dict lookups; zero disk or registry imports at runtime. | **High**: Exact taxonomy frozen with release; inspectable via `jq` in the payload JSON. | **Low**: Preserves determinism AST pin; completely isolated from live DB or MCP state. | **Low**: Negligible JSON overhead (~4 KB). | **RECOMMENDED** |
| **Generator Expands Directly (Flattened Tool Lists)** | **Low**: Generator emits only tool names per agent; reader remains an exact string equality check. | **Med**: Loses category attribution (cannot distinguish whether tool was granted via category or literal). | **Low**: Digest cannot render `(allow-listed via filesystem)` markers; opaque attribution. | **Low**: Deterministic, but degrades reporting fidelity required by commission. | **Low**: Minimal code. | **REJECTED** |
| **Runtime Registry Scan (`list_tools_by_category()`)** | **High**: Reader imports live registry modules or calls `list_tools_by_category()`. | **Low**: Fails under frozen binary where category module source files are not readable. | **Low**: Circular dependency risks between services and tool definitions. | **Critical**: Violates determinism AST pin (`test_no_live_env_or_registry_read_inside_the_rollup`); instance-dependent. | **Med**: Slower rollup execution. | **REJECTED** |

---

## 3. THE RECOMMENDED SHAPE

### 3.1 Files and Symbols to Touch

| File Path | Action | Symbols / Components Touched |
|---|---|---|
| `scripts/tool_usage_taxonomy.py` | **NEW** | `scan_taxonomy(repo_root: Path) -> dict`: AST scanner extracting `CATEGORY_MODULES`, `KNOWN_TOOL_NAMES`, `DYNAMIC_TOOL_NAMES`, `DYNAMIC_TOOL_PREFIXES`, `PRIVILEGED_TOOL_CATEGORIES`, and per-module `@register_tool_category` overrides. |
| `scripts/gen_tool_usage_allowlists.py` | **MODIFY** | `build_payload()`, `verify_payload()`, `main()`: Add `--repo-root`, `--verify` mode, schema-version 2 emission, `tools.deny` extraction, and dynamic prefix metadata. |
| `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` | **REGENERATE** | Update committed bundle to schema_version 2 with categories, deny lists, and comprehensive provenance. |
| `scripts/upgrade/stage.sh` | **MODIFY** | Lines `466` (verification/copy), `620` (tree walk), `700` (map join), `895/901` (manifest heredoc), `948` (stage-local re-hash). |
| `daemon/services/tool_usage_digest.py` | **MODIFY** | Delete `_code_root()`; add `_verified_install_root()`, `_install_root()`, `DigestRootUnresolved`; update `_artifact_dir()`; add `ArtifactLoad`, `allowlist_source()`, `_expand_effective_tools()`; update `render_markdown()`. |
| `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts` | **MODIFY** | Lines `62-67` (doc comment), `213-219` (template hint): Refine ranking caveat copy. |
| `tests/unit/services/test_tool_usage_digest_root.py` | **MODIFY** | Lines `18-28`: Rewrite `test_frozen_branch_anchors_at_executable_parent` to assert install ancestor. |
| `tests/unit/services/test_tool_usage_digest_allow_semantics.py` | **NEW** | Primary discriminating probe (`edit_file` / `devops`), literal preservation, deny rules, degradation tests. |
| `tests/test_tool_usage_artifacts_payload.sh` | **NEW** | Hermetic stage test verifying payload copies, manifest hashes, and corruption rejection. |
| `test/packs/tool_usage_digest_unit_test.sh` | **NEW** | Dedicated test pack running the digest unit suite under the mandatory scrub harness. |
| `.agents/tester/PACKS.md` | **MODIFY** | Register `tool_usage_digest_unit_test` and `test_tool_usage_artifacts_payload.sh`. |

### 3.2 Exact Artifact Paths Relative to Roots

- **Development / Repository Source:**
  - Bundle: `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json`
  - Weights: `.agents/shared/planning/tool-usage-metrics-p1/tool-weights.json`
- **Release Payload (Frozen):**
  - Bundle: `releases/<v>/tool-usage-artifacts/allowlists.json`
  - Weights: `releases/<v>/tool-usage-artifacts/tool-weights.json`
- **Digest Output (Runtime Durable):**
  - `<INSTALL_DIR>/.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md`
- **Exact Manifest Keys (`stage.sh:895, 901`):**
  - Tree Hash: `"tool_usage_artifacts_tree_sha256": "$TOOL_USAGE_ARTIFACTS_TREE"`
  - Per-file Map: `"tool_usage_artifacts_manifest": {$tool_usage_artifacts_map}`

### 3.3 Resolver Algorithm (Pseudocode)

```python
class DigestRootUnresolved(RuntimeError):
    """Raised when the canonical live install root cannot be verified."""

DIGEST_RELATIVE_DIR = Path(".agents") / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"
DIGEST_ROOT_ENV = "ENSEMBLE_TOOL_USAGE_DIGEST_ROOT"
ARTIFACT_DIR_ENV = "ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR"

def _verified_install_root(candidate: Path | None) -> Path | None:
    if candidate is None:
        return None
    try:
        # Frozen install root owns releases/; source checkout owns daemon/
        if (candidate / "releases").is_dir() or (candidate / "daemon").is_dir():
            return candidate
    except OSError:
        return None
    return None

def _install_root() -> Path | None:
    # Direct import from maintenance_seed (public, tested symbol)
    from daemon.repositories.source.maintenance_seed import resolve_live_install_root
    raw_root = resolve_live_install_root()
    return _verified_install_root(raw_root)

def digest_root() -> Path:
    """Resolve the canonical digest destination root (D-6 / U8)."""
    env = os.environ.get(DIGEST_ROOT_ENV)
    if env and env.strip():
        return Path(env.strip())
    root = _install_root()
    if root is None:
        raise DigestRootUnresolved(
            "Could not determine a verified install root (missing releases/ or daemon/). "
            f"Set {DIGEST_ROOT_ENV} explicitly."
        )
    return root / DIGEST_RELATIVE_DIR

def _artifact_dir() -> Path:
    """Resolve the read-only artifact directory (Horn B)."""
    env = os.environ.get(ARTIFACT_DIR_ENV)
    if env and env.strip():
        return Path(env.strip())
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "tool-usage-artifacts"
    # In dev/test: REPO_ROOT / .agents/shared/planning/tool-usage-metrics-p1
    return Path(__file__).resolve().parents[2] / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1"
```

### 3.4 Category Expansion Algorithm (Pseudocode)

```python
@dataclass(frozen=True)
class AllowSource:
    agents: dict[str, list[str]]
    deny: dict[str, list[str]]
    categories: dict[str, list[str]]
    privileged_categories: set[str]
    dynamic_prefixes: dict[str, dict]
    unresolved: dict[str, list[str]]
    state: str  # "ok", "degraded", "disabled"

def _expand_entry(entry: str, source: AllowSource, known_ledger_tools: set[str]) -> tuple[set[str], str]:
    # 1. Category expansion
    if entry in source.categories:
        tools = set(source.categories[entry])
        if entry in source.categories.get(entry, []):
            tools.add(entry)
        return tools, f"category:{entry}"

    # 2. Dynamic namespace prefix expansion (e.g. mcp, plane)
    if entry in source.dynamic_prefixes:
        cfg = source.dynamic_prefixes[entry]
        pfx = cfg["prefix"]
        matched = {t for t in known_ledger_tools if t.startswith(pfx)}
        return matched, f"prefix:{entry}"

    # 3. Literal tool name (or unresolvable entry)
    return {entry}, "literal"

def effective_agent_tools(agent_id: str, source: AllowSource, known_ledger_tools: set[str]) -> tuple[set[str], dict[str, str]] | None:
    if agent_id not in source.agents:
        return None  # Agent unknown -> renders "unknown"

    raw_allow = source.agents.get(agent_id, [])
    raw_deny = source.deny.get(agent_id, [])

    # Special case: empty allow AND empty deny inherits all non-privileged categories
    if not raw_allow and not raw_deny:
        allowed = set()
        reasons = {}
        for cat, tools in source.categories.items():
            if cat not in source.privileged_categories:
                allowed.update(tools)
                for t in tools:
                    reasons[t] = f"category:{cat}"
        return allowed, reasons

    allowed = set()
    reasons = {}
    for entry in raw_allow:
        tools, reason = _expand_entry(entry, source, known_ledger_tools)
        allowed.update(tools)
        for t in tools:
            reasons.setdefault(t, reason)

    # Deny wins
    for entry in raw_deny:
        denied_tools, _ = _expand_entry(entry, source, known_ledger_tools)
        allowed.difference_update(denied_tools)
        for t in denied_tools:
            reasons.pop(t, None)

    return allowed, reasons

def allowlists() -> dict[str, list[str]]:
    """Backward-compatible map consumed by rollup() — EFFECTIVE expanded tools."""
    source = allowlist_source()
    if source is None or source.state == "disabled":
        return {}
    ledger_tools = _collect_ledger_tool_names()
    result = {}
    for agent_id in source.agents:
        eff = effective_agent_tools(agent_id, source, ledger_tools)
        if eff is not None:
            result[agent_id] = sorted(eff[0])
    return result
```

### 3.5 Loudness and Degradation Mechanism

1. **Status-Carrying Loader:**
   `_load_json_artifact(filename)` returns an `ArtifactLoad` envelope with state `ok`, `empty`, `absent`, `unreadable`, or `malformed`. If `allowlists.json` cannot be loaded, it logs a prominent warning via `logger.warning("tool_usage_digest: LOUD FAILURE loading %s: %s", path, exc)`.
2. **Digest Markdown Banner:**
   When `allowlists.json` is missing or unreadable, the rendered markdown injects an explicit warning banner immediately after the header block:
   ```markdown
   > ⚠️ **ALLOW-LIST DIMENSION DISABLED** — Artifact `allowlists.json` was unreadable or absent at `<path>`.
   > All `Allow-listed` table cells are marked as `unknown`. The Zero / near-zero section is disabled.
   ```
3. **Truthful Cell Rendering (`yes` / `no` / `unknown`):**
   - `yes`: Tool is present in the effective allow-list of the agent.
   - `no`: Tool is confirmed absent from the effective allow-list of a known agent.
   - `unknown`: Rendered whenever `allowlists.json` is disabled/missing, OR when the agent is unknown (e.g. DB-seeded or custom agent), OR when the tool is unclassified. It **NEVER** outputs a false `no`.
4. **Refusal Envelope on Write Failure:**
   If `digest_root()` cannot resolve a verified install root, `write_digest()` catches `DigestRootUnresolved` and bubbles a clear error string:
   `"ERROR: Digest destination root unresolved — install directory could not be verified."`

### 3.6 Test Specifications

- **T-A (Primary Probe — Discriminating):**
  `test_edit_file_is_allow_listed_for_devops_via_filesystem_category`:
  Provides fixture with `devops` allowing `filesystem` (which contains `edit_file`). Verifies `edit_file` renders `yes` and per-agent breakdown shows `(allow-listed via filesystem)`.
  *Mutation Leg:* Remove category expansion (revert to exact string equality). `edit_file` probe turns **RED**, while `bash/devops` probe stays **GREEN**, proving the test is non-vacuous.
- **T-A2 (Secondary Probe):**
  `test_bash_devops_allowlisted`:
  Asserts `bash` on `devops` renders `yes`. Marked explicitly as secondary because `bash` is both a tool name and category name.
- **T-7.4 (Literal Preservation):**
  `test_literal_tool_names_survive_expansion`:
  Asserts `kb-writer` with literal grants `rag_insert_text` and `tool_help` renders `yes`, while ungranted tools in the same category render `no`.
- **T-7.6 (Deny Overrides Allow):**
  `test_deny_takes_precedence_over_category_allow`:
  Asserts that if an agent allows `filesystem` but denies `edit_file`, `edit_file` evaluates to `no`.
- **T-7.8 (Empty Allow Inherits Non-Privileged):**
  `test_empty_allow_inherits_non_privileged`:
  Asserts agents with empty allow lists inherit all tools in non-privileged categories.
- **T-B (Install Root Verification):**
  `test_frozen_release_layout_digest_root_is_install_ancestor`:
  Rewrites `test_tool_usage_digest_root.py:18-28`. Sets `sys.frozen=True`, `sys.executable="/tmp/fake-install/releases/v0.31.59/ensemble-prod"`, asserts root resolves to `/tmp/fake-install/.../digests` and `"releases" not in root.parts`.
- **T-B3 (Fail-Closed Root):**
  `test_unverified_root_raises_error`:
  Asserts that a frozen executable located without a `releases/` ancestor raises `DigestRootUnresolved`.
- **T-C (Hermetic Payload Test):**
  `tests/test_tool_usage_artifacts_payload.sh`:
  Clones `test_guidelines_payload.sh`. Uses `mktemp`, `FAKE_HOME` guard, runs `stage.sh`, asserts `tool-usage-artifacts/{allowlists.json,tool-weights.json}` exist in payload, verifies manifest hashes, and tests negative corruption rejection.
- **T-F (Freshness CI Test):**
  `test_committed_bundle_equals_generator_output`:
  Regenerates payload in-memory from `agents/*/meta.json` and asserts content hashes match committed `allowlists.json`.
- **T-E (Weight Disclosure):**
  Asserts dynamic heading and weight fallback disclosure render accurately under uniform weights.
- **T-PIN (AST Seam Integrity):**
  `test_expansion_helpers_never_import_live_registry`:
  AST-scans `daemon/services/tool_usage_digest.py` to ensure no imports of `daemon.tools._tool_registry` or `daemon.registry` are introduced.

---

## 4. FRESHNESS ANALYSIS

### 4.1 Measured Staleness Census

The committed artifact `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` (15,975 bytes, sha256 `36f9918c39d4f7edd6cb64efede8ea20ef3059fea29aa2c8996250e090575c31`, stamped with commit `a064fedf49a604f36cdcc3012161fe6cce9f7367` on 2026-11-20) was compared against the current repository state across all 55 agent definitions:
- **Committed Artifact:** 55 agents, 792 total `(agent, entry)` pairs, 145 distinct entries.
- **Current Repository (`agents/*/meta.json`):** 55 agents, 794 total `(agent, entry)` pairs.
- **Measured Drift (3 of 55 agents differ):**
  1. `experiencer`: Artifact contains `["pok"]`; current `meta.json` contains `[]` (phantom grant in artifact).
  2. `kb-writer`: Artifact contains `["pok"]`; current `meta.json` contains `[]` (phantom grant in artifact).
  3. `maintenancer`: Artifact contains `[]`; current `meta.json` contains `["send_message", "spawn_instance", "subtree_messages", "subtree_status"]` (4 missing grants in artifact).
- **Drift Commits (touching `agents/*/meta.json` after `a064fedf4`):**
  - `313dc0808` (2026-11-25)
  - `fd7dc6480` (2026-11-24)
  - `ede7d7d4c` (2026-11-22)
  - `e44ba6632` (2026-11-22)
  - `181eec3e3` (2026-11-20)

### 4.2 Comprehensive 55-Agent Entry Taxonomy Census

Every entry across the 792 pairs in the committed artifact was classified against the runtime's validator predicate (`daemon/registry.py:1432-1436`):
1. **Known Categories (`CATEGORY_MODULES` keys, 65 total):** 694 pairs (62 distinct category keys).
2. **Statically Known Tool Names (`KNOWN_TOOL_NAMES` ∪ `DYNAMIC_TOOL_NAMES`, 278 total):** 70 pairs (55 distinct tool names).
3. **Dynamic Namespace Prefixes (`DYNAMIC_TOOL_PREFIXES = {"plane_"}`):** 28 pairs (28 distinct `plane_*` tool names, all carried by `project-manager`).
4. **Neither Category Nor Known Tool Name:** **ZERO (0)** pairs. Every single allow entry in the 55-agent corpus resolves validly under the runtime's classification rules.

*Overlap Note:* Exactly 4 entries belong to both categories and tool names: `bash`, `shared_meta_kv`, `system_upgrade`, and `time`.

### 4.3 Provenance Stamp Specification

The schema-2 bundle replaces the single-input `generated_from_commit` with a tamper-evident multi-input provenance block:
```json
"provenance": {
  "generated_from_commit": "<git rev-parse HEAD at generation time>",
  "agents_commit": "<git log -1 --format=%H -- agents/>",
  "agents_payload_sha256": "<sha256 of canonical JSON of agents + deny>",
  "taxonomy_commit": "<git log -1 --format=%H -- daemon/tools/>",
  "taxonomy_payload_sha256": "<sha256 of canonical JSON of categories + privileged_categories + dynamic_prefixes>",
  "tool_name_universe_sha256": "<sha256 of canonical JSON of sorted tool universe>",
  "generator": "scripts/gen_tool_usage_allowlists.py",
  "generator_version": 2
}
```

---

## 5. MANIFEST BLAST RADIUS

### 5.1 Manifest Changes

In `scripts/upgrade/stage.sh:875-903`, exactly two new lines are added to the manifest heredoc:
```json
  "tool_usage_artifacts_tree_sha256": "$TOOL_USAGE_ARTIFACTS_TREE",
  "tool_usage_artifacts_manifest": {$tool_usage_artifacts_map},
```
No existing keys are deleted, renamed, or modified.

### 5.2 Verification Impact Analysis by File and Line

- **`scripts/upgrade/stage.sh`:**
  - `:466`: Add verification call and copy to `$STAGE_TMP/tool-usage-artifacts`.
  - `:620`: Add `TOOL_USAGE_ARTIFACTS_LINES="$(_stage_tree_walk tool-usage-artifacts "$STAGE_TMP/tool-usage-artifacts")"` and tree hash calculation.
  - `:700`: Add per-file mapping join via `_stage_join_map`.
  - `:895, 901`: Add heredoc keys.
  - `:948`: Add stage-local re-hash leg.
- **`scripts/upgrade/lib.sh:4693`:**
  - `integrity_verify` loop: `for tree in agents frontend; do`.
  - **Decision:** **DO NOT TOUCH `lib.sh:4693`**. Leaving `lib.sh` untouched maintains symmetry with the guidelines precedent (`guidelines/` is verified only at stage time, never in `lib.sh:4693`).
  - **Consequence:** `tool-usage-artifacts/` is verified fail-closed at stage time, but is not re-checked at promote time. If an attacker with local root access tampers with `releases/<v>/tool-usage-artifacts/` post-stage, promote will not halt on it. This risk is acceptable and identical to the guidelines precedent.
- **Existing Test Corpus Review:**
  - `tests/test_release_journal.sh:827-833`: Loops over a required key list and asserts presence via `grep -q "\"$f\"" "$MANIFEST"`. Adding keys does NOT break this test (**UNAFFECTED**).
  - `tests/test_guidelines_payload.sh:294-298`: Checks presence of guidelines keys (**UNAFFECTED**).
  - `tests/test_executor_family_payload.sh:383-393`: Asserts scripts and daemon-slice keys (**UNAFFECTED**).
  - `tests/test_stage_plugins_tree.sh:288-291`: Asserts plugins keys (**UNAFFECTED**).
  - **Exact-Key-Set Assertions:** **NONE** in the entire test suite.
  - **Hardcoded File Count Assertions:** **NONE** in executable tests.

---

## 6. THE WEIGHT-DIMENSION RULING + THE FE COPY

### 6.1 Honest Weight Rendering (W3)

Because `tool-weights.json` is committed as `{}` (3 bytes), all 387 tool rows in production evaluate to `FALLBACK_WEIGHT = 230`. To ensure honesty without breaking existing determinism test parsers (`_table_row()` in `test_tool_usage_digest_determinism.py:208-216` pins the 6-column layout):
1. **Dynamic Section Heading:**
   - If all tools in the section evaluate to `FALLBACK_WEIGHT`:
     `### Tools (sorted by calls 7d, descending — no per-tool schema weights are available; every tool uses the 230-token average fallback, so the weight does not discriminate)`
   - If real weights exist:
     `### Tools (sorted by schema weight × calls, descending)`
2. **Permanent Disclosure Blockquote:**
   Directly beneath the table heading, always render:
   ```markdown
   > **Weight disclosure:** The per-tool schema-weight artifact (`tool-weights.json`) is currently empty.
   > All tools in this section use the 230-token average fallback (deterministic; phase3-plan.md §3.3).
   > While the fallback is in effect, the Weight column is constant (230) and ranking is frequency-only.
   ```
3. **Table Structure Intact:**
   The columns remain `| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |`.

### 6.2 Frontend Copy Ruling & Build Cost

- **Location:** `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts:213-219`
- **Current False Wording:**
  `"...the Phase-3 daily digest (schema-token weight × frequency, zero/near-zero, allow-list intersection) is the authoritative ranking surface..."`
- **Corrected Truthful Wording:**
  ```html
  <p class="tab-hint" data-test="tool-usage-ranking-caveat">
    the calls (7d) sort is a frequency-only PROXY for layer-2 candidacy — the
    Phase-3 daily digest (per-tool schema-token weight × frequency where measured, zero/near-zero,
    allow-list intersection) is the authoritative ranking surface; while no per-tool weights are
    available it ranks by frequency only, and each digest states its own weight coverage.
    calls 24h is a rolling surface — the digest's D−1 complete-UTC-day window
    is the precise day figure.
  </p>
  ```
- **Test Impact:** `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts:448-457` asserts only `PROXY`, `daily digest`, and `rolling`. All three substrings are preserved. The spec passes without changes.
- **Build Cost Assessment:**
  Modifying the Angular component requires running `npm run build` via `scripts/upgrade/_build_frontend.sh` prior to staging, because `stage.sh:348` verifies the existence of `frontend/dist/frontend/browser/index.html` and `:468` copies it. This is already a mandatory precondition for every release stage.
  *Recommendation:* Deliver as a clean in-lane rider commit. If the lane owner needs to minimize scope, defer the frontend copy update to a standalone follow-up, as the backend markdown disclosure carries sufficient honesty in the interim.

---

## 7. REJECTED ALTERNATIVES

| Alternative | Specific Failure Mode Left Behind |
|---|---|
| **Horn A (Mirroring `.agents/shared/planning/...` in Payload)** | Re-arms the D4 promote-blocking trap. Staging copies developers' local uncommitted `digests/` markdown files into the immutable release directory. Subsequent daily runs attempt to write into the release tree, causing hash verification mismatches and blocking future promotions. |
| **Embedding in Frozen Binary (`ensemble.spec` datas / module)** | Creates unresolvable T1 (build) vs T2 (stage) skew. Any modification to `meta.json` allow/deny lists requires a full PyInstaller binary compilation. Embedded data is compressed and invisible to shell-level release integrity checks. |
| **Install-Tree Copy at Stage Time** | Fatal version skew during deployment. Staged release vB overwrites live release vA's copy in the install tree before vB is promoted. Rolling back from vB to vA does not restore vA's artifact, permanently corrupting the allow-list basis. |
| **Deriving at Digest Time from Live `agents/` Directory** | Directly violates the core Phase-3 determinism invariant (`phase3-plan.md:80`, "the digest NEVER scans the live `agents/` tree at run time"). Breaks if custom DB-seeded agents exist or if directory resolution drifts. |
| **Generator-Time Flattening via `list_tools_by_category()`** | Produces a near-empty mapping because tool registrations occur lazily inside instance factory closures (`_tool_metadata` is unpopulated at import time). Destroys category attribution reporting. |
| **Runtime Registry Scan Inside Digest** | Fails in frozen binary environments where category module sources are unreadable. Introduces non-deterministic behavior because `_tool_metadata` is mutated dynamically by per-instance MCP and plugin tools. |
| **Expanding Categories Inside `rollup()`** | Violates the hard determinism AST pin in `test_tool_usage_digest_determinism.py:599-607`, which greps `inspect.getsource(tud.rollup)` and asserts that `agents/`, `os.environ`, and `getenv` are strictly absent. |
| **Ignoring `tools.deny` During Expansion** | Emits false positive allow-list claims for 37 `(agent, tool)` pairs across 9 tools. Specifically, 16 agents that explicitly deny `edit_file` (e.g. `architect`, `ari`, `approver`, `aegis`) would be falsely reported as granting it. |
| **Auto-Consulting `INSTALL_DIR` Environment Variable** | Unsafe ambient inheritance. A developer or sandbox process launched from a shell inheriting `INSTALL_DIR=/home/nea/agents-ensemble` would silently write test digest files into the production install tree. |
| **Using `bash/devops` as the Sole Acceptance Probe** | Complete test vacuity. Because `bash` is both a category name and a literal tool name, an exact-string comparison passes `bash/devops` even if category expansion is completely broken, masking the D3 defect. |

---

## 8. RESIDUALS

1. **Stage-Time-Only Integrity Verification:** `tool-usage-artifacts/` is verified fail-closed at stage time via the stage-local re-hash leg, but is not checked during promote by `lib.sh:4693`. Accepted for symmetry with the `guidelines/` precedent and to avoid modifying `lib.sh` while concurrent lanes are in flight.
2. **Dynamic Server Tools Marked `unknown`:** Approximately 6 of the 182 tools in the production ledger (such as `sed`, `task_schedule_create`, and dynamic MCP server endpoints) cannot be resolved via static taxonomy and will render as `unknown` rather than `no`.
3. **Taxonomy as a Third Source of Truth:** The taxonomy mapping in `scripts/tool_usage_taxonomy.py` must be maintained alongside `_tool_registry.py`. Drift is prevented by CI unit test `T-F` and stage-time `--verify`.
4. **`ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR` Production Reachability:** The environment variable used to redirect artifact reading for unit tests is technically accessible in production, but is documented strictly as a test seam and is unset in production.
5. **Committed File Retains Generation Commit:** The `generated_from_commit` field in `allowlists.json` records the commit at which generation was executed rather than the release commit. The cryptographic payload SHA256 fields provide the true integrity proof.
6. **Pre-Existing Unregistered Test Pack Status:** At base commit `f83fb5d81`, the digest determinism suite was not registered in any pack. The fix introduces a dedicated test pack `tool_usage_digest_unit_test.sh` and registers it in `PACKS.md`.
7. **Leftover Files in v0.31.59 Release Directory:** The historical digest files generated at `releases/v0.31.59/.agents/.../digests/` will remain until cleaned up by the standard keep-3 release retention sweep. Identical copies already exist in the install root.
8. **Analysis Clock Dependency (`wi-aac95252`):** The 7-day post-promote tool usage analysis depends on reliable digest generation. Because the current digest emits false allow-list data, the analysis window must be measured from the deployment of this fix.

---

## 9. OPEN QUESTIONS FOR THE LEADER

1. **Ratify Horn B + Stage-Time `--verify` over Runtime Derivation (Option d)?**
   *Architect Recommendation:* **Ratify Horn B.** It strictly preserves the frozen determinism invariant (`phase3-plan.md:80`), packages an immutable, manifest-verified artifact in the release payload, and introduces zero promote-time risk. Option (d) requires amending a frozen core invariant and reading unverified files at runtime.
2. **Confirm Inclusion of `tools.deny` Processing in this Lane?**
   *Architect Recommendation:* **Confirm IN scope.** Supporting `deny` requires fewer than 10 lines of set subtraction in `effective_agent_tools()`. Omitting it would cause the digest to report false allow-list grants for 37 `(agent, tool)` pairs (including falsely reporting that `architect` and `approver` allow `edit_file`).
3. **Approve Frontend Copy Correction as an In-Lane Rider?**
   *Architect Recommendation:* **Approve as an in-lane rider.** The copy fix ensures user-facing honesty regarding frequency ranking versus measured weights. It requires no test changes. If the lane owner wishes to avoid running `npm run build` during staging, the frontend change can be detached into an immediate follow-up commit without blocking backend delivery.

### Appendix A — Alternative (d): Runtime Derivation Details (If Open Question 1 is Overridden)

If the leader rejects modifying `stage.sh` payload packaging, Alternative (d) derives allow/deny lists at digest runtime directly from `releases/<v>/agents/*/meta.json` using `resolve_agents_dir()`, while loading category taxonomy from a generated Python module constant `daemon/services/_tool_taxonomy_data.py`.
- *Required Plan Amendment:* Strike `phase3-plan.md:80` rule (c) and replace with: "The digest reads agent configurations from the immutable release `agents/` payload at runtime."
- *Drawbacks:* Breaks encapsulation, couples digest logic to filesystem directory layout conventions, and cannot report on dynamic DB-seeded agents. Horn B is strongly superior.

### Appendix B — Stage Execution Constraints for Implementation Lane

1. **No Live Promotion:** The implementing lane must stage only (`stage sandbox` or `stage live`); the live promotion ceremony requires Emperor authorization.
2. **Dynamic Slot Derivation:** The release version slot must be resolved live at stage time (origin/latest has progressed past `v0.31.60`).
3. **Clean Environment Fencing:** Use strict per-variable scrubbing (`-u POSTGRES_DB -u POSTGRES_USER ...`); do not use `env -u POSTGRES_*` as bash does not glob environment variable names.
4. **Shell Continuation Discipline:** Never place `#` comments inside backslash-continued shell lines in `stage.sh` or test packs.

### Appendix C — Evidence Provenance and Verification Log

- **Primary AST Taxonomy Scan:** Executed via `.venv/bin/python` scanning all 65 modules in `CATEGORY_MODULES`, resolving 270 category-associated tools and 278 static tool names.
- **55-Agent Allow-List Census:** Mechanically verified against `allowlists.json` (792 pairs) and live `agents/*/meta.json` (794 pairs). Confirmed zero unclassified entries.
- **Stage Script and Manifest Analysis:** Verified lines `466`, `487`, `619-620`, `690-700`, `890-901`, and `942-948` of `scripts/upgrade/stage.sh` and `lib.sh:4693`.
- **Test Suite Analysis:** Inspected `test_tool_usage_digest_determinism.py` (617 lines), `test_tool_usage_digest_root.py`, `test_tool_usage_digest_registration.py`, and `PACKS.md:156, 207`.
