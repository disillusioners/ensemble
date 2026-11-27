# Architecture Recommendation v4 — tool-usage digest defect fix (D1 delivery · D2 durability · D3 semantic)

Status: **v4 supersedes v3** (v3 was patched by the leader's precise fix list — three blocking fixes and four advisory fixes — and carries the three open questions it raised, now DECIDED in §9). Nothing implemented — this is a specification for the implementing lane.
Date: 2026-11-26. Author: Architect.
Superseded v1 preserved byte-identical for audit at `.agents/shared/planning/tool-usage-metrics-p1/architecture-recommendation-digest-fix-v1-superseded.md` (sha256 `509b1d10b65086929ee75d4838c63308849518ab030394fd64c97641a770e6eb`). **v2 was superseded IN PLACE** (overwritten at the leader's instruction — no v2 file is preserved): v2 was sha256 `50196fd8f8a40b3fc8c539238eb693f2b5d524e4dccceb7f5c8f2182fd8a1d1e`, 939 lines; its bytes are recorded here for audit.

**Read tree for every repo citation:** `origin/latest` = `d7c15d45f65037844471c3d2ff2a06d4ed9afeec`, read as `git -C /home/nea/ensemble-src-wt-tool-usage-digest show d7c15d45f…:<path>`. The lane worktree HEAD **is** `d7c15d45f` and its tracked tree is clean, so worktree reads and `git show` reads are byte-identical. **The detached HEAD `bf4bfde6e` is NEVER used**: a vocabulary read at that stale checkout manufactures a phantom population (it has 64 `CATEGORY_MODULES` keys — `room` is missing — and reclassifies 8 real allow entries as "unresolvable"; see F-CENSUS). Every count in this document was re-derived by the architect at `d7c15d45f` and is independently reproducible from the commands in Appendix A.

**Plan of record — UNTOUCHED:** `.agents/shared/planning/tool-usage-metrics-p1/architecture-recommendation.md`, sha256 `deaf040a3535431e03bb552d4549edb93e3e42803d64a9401a329186567f97cb`, git blob `d0173599fb063e24b29854af1a038ce0fe5cc2c9` (verified BEFORE and AFTER this revision; §1 PROVENANCE records the rulings that are honored and the two that are narrowed).

**What changed from v1, in one paragraph.** v1 shipped a spec whose central artifact — a category→tool map claimed to enumerate all 65 categories — cannot be built from any static source (only 63 of 65 category keys carry source-level tool definitions; the remaining two are empty runtime stubs), and whose expansion applied the allow list but **not** the deny list, so the digest would have reported grants the daemon never makes (`explorer` allows the category `rag` and denies `rag_query`: the runtime denies it, v1's digest said "yes"). v1 also treated the MCP/plane namespaces as statically expandable (they are not — they render `unknown`), never mentioned the census pack that hard-asserts a node count over three of this lane's test files, named 1 of 5 digest-root pins while deleting the helper those pins call, specified a root-resolution test that cannot pass as written, and left the "no silent drop" rule declared but unpopulated. v3 fixes each of those in place, and it carries the two NEW decisions the ruling set did not cover (innate-skill grants; taxonomy enumerability) that the v1 evidence forced into the open.
**What changed from v2 to v3, in one paragraph.** v3 is a PATCH of v2, not a redesign: the leader's precise fix list landed in full. **Two blocking fixes.** (B1) §3.6.1's `effective_agent_tools` now gates the default-universe branch on an **EMPTY ALLOW ALONE**, mirroring `resolve_tool_filter` — the runtime builds the default universe at `daemon/tools/instance.py:587-591` under the condition at `:581` (EMPTY ALLOW, regardless of deny), applies deny afterwards at `:601-609`, and names that exact case at `:8117-8119` ("the empty-allow + non-empty-deny universe construction"). v2's `not allow and not deny` gate returned an EMPTY grant set for an agent with `allow: []` and a non-empty deny — the false-NO class §3.6.3 promises never to produce (latent today: `watcher` is the only empty-allow agent and its deny is empty, so the 55-agent corpus cannot catch it). Test 44 additionally takes the runtime's `allow` argument from the REAL `expand_allow_for_innate_skills` (`daemon/tools/instance.py:204`, called by the runtime at `:8180`) instead of the digest's own expansion — the innate half is no longer compared against itself — and three synthetic agents pin the empty-allow branches the corpus cannot supply. (B2) F6 asserted a CI freshness test that DOES NOT EXIST at `d7c15d45f` (`git grep` finds only the generator's own usage docstring); v3 declares it NEW in this lane (§3.9.8, node 57) and folds its +1 node into the census arithmetic. **Twelve non-blocking fixes** are applied in place (N1–N12): the env-override bypass in the D4 pins (N1), test 54's per-case expectations (N2), the `allowlists()` growth statement and its pin (N3), the shell test's runner and the unconditional 10-file slice (N4), the `tool-weights.json` digests (N5), the `daemon/mcp/tool_adapter.py` path (N6), the `rag` member list (N7), the freeze-sha definition (N8), the `PACK_SELFTEST` bypass clause (N9), the citation slips (N10), the mutation wiring (N11), and the `.agents/shared/metrics/tool-categories.json` / F11 cross-reference (N12). **One consequential coherence edit** was required by the leader's own N2 and by test 39, and is flagged for override in §3.6.3: v2's `grant_verdict` never read its `granted` parameter or the deny list, so the PRIMARY probe (`explorer` + `rag_query`) would have rendered "yes" and an unresolved DENY entry would have rendered "unknown"; v3 adds the deny gate that makes both achievable exactly as specified. **The three open questions are DECIDED** (§9): the FE copy correction SHIPS IN-LANE (OQ1), the stage-time drift against the committed dev artifact WARNS — and ONLY because F6 lands with it (OQ2) — and v2's MCP strictness recommendation is APPROVED (OQ3).

**What changed from v3 to v4, in one paragraph.** v4 is a PATCH of v3, not a redesign. The approver confirmed the architecture is sound, that D1/D2/D4 are genuinely fixed, and that **no over-claim path was found**; the leader's fix list is therefore a THREE-CLAUSE DELTA, and every item below is applied in place. **Three blocking fixes.** (APPROVER-B1) §3.8/§3.9.8/§4.3's F6 drift gate is RE-BUILT as a **SEPARATION, not a pin**: `--verify` byte-compares the SEMANTIC PAYLOAD only (seven payload keys), while provenance is compared SEPARATELY (content hashes ALWAYS; `last_commit` only where the corresponding content hash moved; `provenance.tree_commit` INFORMATIONAL and excluded) — v3's whole-file byte compare was RED from the lane's first commit onward (a committed artifact can never equal the regeneration of the tree that carries it) and would have broken every unrelated lane; v3's "inherits the F11 discipline verbatim" analogy is CORRECTED (`tool-categories.json` carries NO commit stamp at all, so its whole-file compare IS complete — this artifact is stamped, so it CANNOT be). (APPROVER-B2) `grant_verdict` gains an EXPLICIT `inherits_all` branch BEFORE the owner branch (an inherit-all agent — `watcher`, the only empty-allow agent — was rendered "no" for its ENTIRE column, the exact mirror of the D3 defect, and no test pinned it), the reader's category-key universe becomes `categories ∪ runtime_only_categories` (so `category_granted(agent, "plane")` is true for an agent that allows `"plane"`, and the `plane_` sweep renders "unknown" where §4.6 requires it), and the MCP-shape predicate adopts the RUNTIME's exact form `name.startswith("mcp_") and "_" in name[4:]` (v3's "≥2 underscores after the prefix" is STRICTER than the runtime, so a runtime-swept name would have been decided "no" — a false NO). (APPROVER-B3) the per-agent `allow_listed` field STAYS A BOOLEAN (derived as `verdict == "yes"`), a NEW `allow_listed_detail` field carries the verdict + reason, and the renderer's reasonful marker variants are constrained so the pinned substring `(allow-listed)` survives — the two ad-hoc `allowlists` monkeypatch legs of the determinism suite (`:497`, `:530`) keep passing **UNCHANGED**. **Four advisory fixes.** (A1) a TENTH top-level key `known_tool_names` (KNOWN ∪ DYNAMIC = **278** names) joins the artifact, so the reader can separate "no" from "unknown" for the 7 DYNAMIC-only names (`od.generate`, `doc_write`, …) and §4.6's 162 / §8.2's 201 stay derivable; (A2) the MCP-shape predicate correction above; (A3) the pack's `:7` HEADER literal joins the census re-stamp surface (v3 named only the `:325` echo literal); (A4) the stage-time generator call reuses `stage.sh`'s existing `_stage_cc_py` resolution (`:537-541`) instead of hardcoding `python3`. §9.1 records the approver's "no over-claim path found" as the safety argument for the allow-list dimension.

---

## 1. DECISION SUMMARY

### 1.1 Ruling-by-ruling recommendation

| Ruling | Recommendation | One-sentence why |
|---|---|---|
| **R-PAYLOAD** | **RECOMMENDED — a CLEAN, DEDICATED payload dir `releases/<v>/tool-usage-artifacts/` containing ONLY `allowlists.json` + `tool-weights.json`.** | The read-side artifacts and the write-side digest output then share **no** parent directory, so the D4 promote-blocking collision is eliminated *by construction* rather than by an exclusion list that one added file undoes. |
| **R-ROOT** | **RECOMMENDED — reuse `resolve_live_install_root()` (`daemon/repositories/source/maintenance_seed.py:274` → `_frozen_install_root` :241 → `_install_dir_owning_releases` :219, a 4-ancestor bounded walk for the dir owning `releases/`), behind a verification gate; `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` keeps precedence.** | That resolver is the house's promote-stable root (it survives a promote AND the keep-3 eviction — the exact defect class its own docstring names at `:244-248`); a new walk, a `../..` guess, or a `sys.executable.parent` anchor re-opens that defect. |
| **R-EXPANSION** | **RECOMMENDED — category→tool expansion lives in the READER (`allowlists()` plus a new `allowlist_source()`), never in `rollup()`; the map's keys are REGISTRY KEYS (`list_tools_by_category()` semantics), never display names; an explicit test pin forbids the display-name function.** | `rollup()`'s source is AST-pinned (determinism test `:599-607`) to contain no `os.environ` / `getenv` / `agents/` while containing `tool_weights()` and `allowlists()`; keeping the expansion in the reader leaves the rollup a pure function of the artifact. |
| **R-OVERCLAIM** | **RECOMMENDED — the artifact carries `allow` AND `deny` (plus innate-derived grants); the reader applies deny AFTER allow with deny winning; the result is proven EQUIVALENT to `resolve_tool_filter(allow, deny, tool_categories, all_tool_names)` (`daemon/tools/instance.py:455`) against a fixed fixture; the two ownership guards are modelled; G4 and G5 are DISCLOSED, not approximated.** | The runtime grant rule has two halves (expand allow, then subtract deny with deny winning) — a category-only expansion reports grants the daemon never makes, which is the false-YES half of D3. |
| **R-SCHEMA** | **RECOMMENDED — the artifact's schema version moves to 2, and an old/unversioned artifact is REJECTED LOUDLY (banner + `unknown` cells + `logger.error`), never degraded silently.** | A missing `deny` list is indistinguishable from an empty one at read time, so without a version gate the over-claim returns by a different route. |
| **R-MCP** | **RECOMMENDED — MCP-owned and plane-runtime-owned rows render `unknown`, never `yes`/`no`.** | MCP names are `mcp_{slugified_server}_{tool}` (`daemon/mcp/tool_adapter.py:201-215`), the server list is DB/config state at call time, and `daemon/tools/mcp_tools.py` is a 15-line stub whose docstring says tools are "discovered dynamically at runtime" — and 26 of 55 agents allow `"mcp"`, so a category-expanding digest would mark every MCP row "yes", including the withheld `mcp_rpc_*`. |
| **R-PROBE** | **RECOMMENDED — the primary discriminating probe is `explorer` + `rag_query` (verdict AND attribution asserted); `devops` + `edit_file` is the secondary verdict probe; the board's stored `bash`/`devops` acceptance probe is recorded here as a documented FALSE GREEN and is not used.** | "bash" is BOTH a category key and a tool name, so the board's probe passes under an exact-match implementation and cannot discriminate; the primary probe is the one pair where both wrong implementations produce a different observable. |
| **R-WEIGHTS** | **RECOMMENDED — relabel now (honest heading + a permanent weight disclosure), keep `tool-weights.json` byte-identical (`{}\n`), route the real per-tool weight table to the fc9 measurement point as a SEPARATE item, and correct the FE copy in the same change.** | The committed weights artifact is literally `{}` and all 387 live digest rows carry the 230-token fallback, so the heading "sorted by schema weight × calls" claims a join that does not happen, and the FE claims authority on two of three axes that are false. |
| **R-FRESHNESS** | **RECOMMENDED — REGENERATE at stage time from the tree being staged, extend the generator to emit `deny` + `innate` + the taxonomy + a multi-input provenance stamp, and compare (warn) against the committed dev-mode artifact.** | The committed artifact is already stale versus live (3 of 55 agents differ; 792 vs 794 pairs), and a `generated_from_commit` derived from the agents tree alone is a LIE about provenance when the artifact also depends on the registry taxonomy. |
| **R-CENSUS** | **RECOMMENDED — re-stamp ALL THREE census surfaces in the SAME commit as the test additions: the pack's `UNIT_EXPECTED` + `EXPECTED_NODES`, the `.agents/tester/PACKS.md` row (per-file counts, total, freeze sha), AND the row's prose clause that pins the C5 semantic being corrected.** | The pack's census is a strict equality (`-ne`) that `PACK_SELFTEST=1` deliberately does not bypass; leaving the row stale reproduces exactly the `4ae666c4a` plan-body-ledger drift. |

### 1.2 Two NEW decisions this revision makes (the v1 evidence forced them open)

| # | Decision | Recommendation | One-sentence why |
|---|---|---|---|
| **D-NEW-1** | **Innate-skill grants** | **RECOMMENDED — the artifact carries each agent's `innate_skills`, and the reader applies `INNATE_SKILL_TOOL_CATEGORIES` (`daemon/tools/instance.py:195-201`) to the allow list before expansion.** | 36 of 55 agents receive a category grant ONLY through innate skills (e.g. `worker` → `todo`; `developer` → `chart`, `dynamic-skill`, `todo`), so an artifact that carries only `tools.allow` + `tools.deny` produces a false **NO** for every one of those tools — the exact mirror of the over-claim defect. |
| **D-NEW-2** | **Taxonomy enumerability** | **RECOMMENDED — the taxonomy is SOURCE-DERIVED through the house AST walker (extended, not duplicated, for per-category attribution), and the artifact declares `runtime_only_categories` explicitly; no complete static taxonomy is claimed.** | 63 of 65 category keys carry source-level `@tool` definitions (5 at module level, 58 only inside factory bodies); `mcp` and `plane` are empty stubs whose membership is call-time state; and the *presence* of a key in `list_tools_by_category()` is instance-dependent (G4). |

### 1.3 PROVENANCE — what this revision does to the plan of record

The plan of record is `architecture-recommendation.md` (**D-6 at `:119`**: "pin an **explicit canonical root** (config/env constant) — digest path `<root>/.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md`, same-day re-run **overwrites**") and **U8 at `phase3-plan.md:61`** ("The canonical root is the env constant **`ENSEMBLE_TOOL_USAGE_DIGEST_ROOT`** … when the env var is unset, the root resolves … to `<live install tree>/.agents/shared/planning/tool-usage-metrics-p1/digests` (the live install tree, NOT a worktree and NOT the repo checkout)"), plus **§3.12 item 12 at `phase3-plan.md:80`** (U7 — the committed snapshot artifact) and **T2 at `phase3-plan.md:97`** (the weight artifact + the allow-list snapshot artifact).

**I HONOR D-6 at `:119` and U8 at `:61` as written**, and I NARROW exactly two things, both declared here:

1. **NARROW-1 (root resolution).** U8 says "the daemon's canonical project-data root" without naming a resolver. v3 pins it to the house resolver `resolve_live_install_root()` behind a **verification gate**: a root that cannot be verified (`(root/"releases").is_dir()` for a frozen install, `(root/"daemon").is_dir()` for a source tree) **fails closed** with `DigestRootUnresolved` instead of being guessed. U8 is silent on the unverifiable case; v1 guessed (`/tmp`); a guess writes digests into the wrong tree.
2. **NARROW-2 (source-mode destination).** U8's parenthetical "(the live install tree, NOT a worktree and NOT the repo checkout)" is true in production and is HONORED there: in a frozen install the resolved root is the live install dir. In a **source** run the house resolver deliberately returns the *checkout* (`maintenance_seed.py:274-300`: "a dev run never writes into the production install"), so v3 writes a source-mode digest to `<checkout>/.agents/shared/planning/tool-usage-metrics-p1/digests/` — a path the repo **already gitignores** (`.gitignore:96`), so there is no git pollution (the concern D-6 raised for the live tree, which has no `.git`). A source run therefore never writes into the production install tree; `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` overrides both modes.

**I also AMEND** `phase3-plan.md:80` (§3.12 item 12) and `phase3-plan.md:97` (T2) in exactly two ways: the artifact's **shape** moves to schema 2 (§3.3) and its **generation site** moves to stage time (R-FRESHNESS). Both amendments are spelled out in §4.

### 1.4 BLOCKER DISPOSITION TABLE (v1's 9 council blockers → the section of v4 that resolves each)

#### 1.4.0 v4's OWN disposition — the APPROVER's three blockers and the leader's four advisories

The approver returned **REJECT** on v3 with three blockers (**APPROVER-B1 / B2 / B3**); the leader added four advisories (**A1–A4**). **v4 closes all seven.** No approver blocker is deferred, re-scoped, or answered with "out of scope"; the disposition is item-by-item below.

| Id | Raised against | What v3 got wrong | **v4's closure** | Section |
|---|---|---|---|---|
| **APPROVER-B1** | the F6 drift gate | `--verify` regenerated in memory and BYTE-COMPARED the WHOLE file, `provenance.tree_commit` included — a stamp that moves on EVERY commit (including commits that touch neither `agents/` nor `daemon/tools/`), so the gate was RED from the lane's first commit onward and would have broken unrelated lanes. The "inherits the F11 discipline verbatim" analogy was INVALID: `tool-categories.json` carries NO commit stamp, so a whole-file compare IS complete for it — and is structurally impossible for a stamped artifact | **The SEPARATION, not the pin.** `--verify` byte-compares the SEMANTIC PAYLOAD only (seven keys); provenance is compared SEPARATELY (content hashes ALWAYS; `last_commit` only where its content hash moved; `tree_commit` INFORMATIONAL, excluded). The F11 analogy is CORRECTED with the precise difference and why the difference is safe. A new assertion pins that a deliberately mutated `agents/` payload makes `--verify` exit **NON-ZERO** (no vacuous gate) | §3.3.1, §3.8, §3.9.8, §4.3 (F2/F6), §4.4 |
| **APPROVER-B2** | `grant_verdict` | two false-NO paths. **(a) THE INHERIT-ALL AGENT** — `watcher` is the only empty-allow agent (`agents/watcher/meta.json:21-23`); the empty-allow branch attributes its grants to NO category, so `granted_categories` is EMPTY and the owner branch returned "no" for the WHOLE watcher column (the exact mirror of the D3 defect) — and no test pinned the verdict (test 47 pinned the grant SET). **(b) THE `plane_` SWEEP** — `category_granted(agent_id, "plane")` consulted only `categories`, but `"plane"` is a `runtime_only_categories` member, so an unowned `plane_*` name rendered "no" where §4.6 requires "unknown" | **An EXPLICIT `inherits_all` branch BEFORE the owner branch** (statically-owned non-privileged → "yes"; privileged-category → "no"; MCP-shaped / prefix-shaped unowned → "unknown"), and the reader's **category-key universe becomes `categories ∪ runtime_only_categories`** everywhere it is consulted (including `category_granted`). **Three new verdict assertions** — each asserts the VERDICT, not merely the grant set | §3.6.2a (new), §3.6.3, §3.9.3 (tests 58–60) |
| **APPROVER-B3** | the `allow_listed` field type / marker format | `allow_listed` was to BECOME the verdict (a string) while the renderer emitted reasonful markers such as `(allow-listed via filesystem)` — but `tests/unit/services/test_tool_usage_digest_determinism.py:541` asserts `tool["agents"][0]["allow_listed"] is True` and `:544` asserts `"(allow-listed)" in _agent_lines(md, "tie_a")[0]`. Those two claims CANNOT both hold: a string breaks `:541`, and `(allow-listed via filesystem)` removes the closing paren and breaks `:544`. Separately, the fixture instruction "neutralizes `allowlist_source`" would have meant `state != "ok"` → every verdict "unknown" → "without changing any verdict" is FALSE | **`allow_listed` STAYS A BOOLEAN** (`verdict == "yes"`); a NEW `allow_listed_detail` field carries `{verdict, reason}`; the renderer's reasonful variants are constrained to KEEP the pinned `(allow-listed)` substring; the REASON is a separate, degradable input from the VERDICT, so the ad-hoc `allowlists` monkeypatch legs keep passing **UNCHANGED**; the fixture instruction is corrected to a DETERMINATE ("ok"-state) envelope | §3.6.3, §3.6.6, §3.9.2 |
| **A1** | advisory | the artifact fixed itself at "exactly nine" keys while §3.6.3 consulted a `static_tool_universe` the reader could only derive from `categories` — pushing the 7 DYNAMIC-only names (`od.generate`, `doc_write`, …) to "unknown" and contradicting §4.6's 162 / §8.2's 201 | **A TENTH key `known_tool_names`** (KNOWN ∪ DYNAMIC = **278** names, derived at `d7c15d45f`); the normative shape moves to "exactly ten"; §4.6's 162 and §8.2's 201 get an EXPLICIT derivation a developer can compute | §3.3, §3.3.1, §3.6.3, §3.8, §4.6, §8.2, Appendix A |
| **A2** | advisory | §3.6.3 said "≥2 underscores after the prefix"; the runtime's predicate is `name.startswith("mcp_") and "_" in name[4:]` — **≥1** underscore after the prefix. A name shaped `mcp_x_y` IS swept by the runtime but would not be MCP-shaped to the reader ⇒ a possible false NO | **The runtime's exact predicate is adopted verbatim** (`instance.py:520-523`; the runtime's COMMENT at `:519` counts the prefix's own underscore — the CODE at `:522` is the authority) | §3.6.2a, §3.6.3 |
| **A3** | advisory | the census re-stamp surface named only the pack's `:325` echo literal; the pack's `:7` HEADER literal `"(8 files, one pack script + one PACKS.md block)"` also has to become "10 files" | **The `:7` header literal joins the re-stamp surface** (both literals, in the same commit) | §3.1, §3.9.7 |
| **A4** | advisory | §2.1 said "one `python3` generation call"; `scripts/upgrade/stage.sh:537-541` already resolves `_stage_cc_py` (venv python, else `python3`) | **The generation call reuses `_stage_cc_py`** — no hardcoded `python3` | §2.1, §3.1, §5.2 |

**The council's FINAL blocking labels (BL-1 … BL-8) mapped onto this revision.** The reviewer closes the spec item by item against these labels; the brief's C1-C9 labels are kept in the table below for traceability.

| Council final label | Brief label | Resolution in v3 | Section |
|---|---|---|---|
| **BL-1** census-pack collision | C3 / C8 | The full census re-stamp obligation is carried: all three surfaces, in the same commit as the test additions, counts re-derived by the pack's own `--collect-only` inside the lane worktree's own `.venv`; the "8 neither" phantom is explained (the stale detached HEAD lacks the `room` key) and councilor 2's withdrawal of that claim is recorded. | §3.9.7, §4.2, §4.5 |
| **BL-2** MCP unresolvable | C2 / C9 | `mcp` / `mcp_rpc` / `plane` are handled explicitly: unowned dynamic names render `unknown` (never `yes`/`no`); the two statically-decidable cases (literal grants, statically owned `mcp_rpc_*`) are stated as an explicit reading of R-MCP and surfaced as Open Question 3. | §3.6.3, §3.6.2, §4.6, §8.3 |
| **BL-3** ownership guards | R-OVERCLAIM | Both guards are modelled, not approximated: `mcp_rpc_*` withheld from an `mcp`-only agent, `plane_sync_*` excluded from the `plane_` sweep — with the runtime sites and the ownership-union helper cited. | §3.6.2, §3.9.3 (tests 45-46) |
| **BL-4** factory categories | C1 / F-GRANT | The exact enumerability classes are stated (5 module-level, 58 factory-only, 2 runtime stubs; key presence is instance-dependent); no complete static taxonomy is claimed; the house scanner is cited correctly and reused rather than duplicated. | §3.5, §1.2 (D-NEW-2), §8.2 |
| **BL-5** only 1 of 5 pins | C4 | All five pins are named with their assertions and rewritten one by one; `_code_root()` is **kept** (callers enumerated: 2 production + 2 test sites) and re-pointed, not deleted. | §3.9.1, §3.4.1 |
| **BL-6** T-B unrunnable + no real-artifact test | C5 | T-B gets a passing shape (a REAL tmp install layout so the house resolver actually resolves), plus T-B2 (the bounded-walk leg) and T-B3 (fail-closed); the semantics suite reads the REAL artifact with no monkeypatch of the artifact, the taxonomy, or the readers. | §3.9.1, §3.9.3 |
| **BL-7** `unresolved` never populated | C6 | The term is DEFINED ("unclassified" = not a category key, not a static tool name, not prefix-matched), POPULATED with the measured numbers (ALLOW **0**, DENY **1** — `maintenancer: git_commit`), and given a REAL TRIGGER: a fixture-driven test proving an unresolvable entry is preserved as a literal AND surfaced (the live corpus cannot supply an ALLOW-side trigger, because the measured ALLOW population is zero). | §3.6.6, §3.9.4 (test 54), §4.1 |
| **BL-8** wrong primary probe | R-PROBE | `explorer` + `rag_query` is the PRIMARY probe, asserted on verdict **and** attribution (the verdict alone cannot discriminate an exact-match mutant); `devops`/`edit_file` is the secondary verdict probe; the board's stored `bash`/`devops` probe is recorded as a documented FALSE GREEN and is not used. | §3.9.3 (tests 39-43), §7 |


| Blocker | Raised by | One-line resolution | Resolved in |
|---|---|---|---|
| **C1** — the taxonomy CANNOT enumerate factory-built categories (v1 claimed 65 statically-enumerable categories; only 52 appear in `list_tools_by_category()` at runtime, the rest are factory-built); v1:412 also misstates the house scanner | agentic B1 | v3 states the exact enumerability classes measured at `d7c15d45f` (63 source-enumerable keys, 2 empty runtime stubs, instance-dependent key presence), names the house scanner precisely (`_scan_category_module_sources` `_tool_registry.py:1063`, `discover_source_only_tool_names` :1143, `discover_all_tool_names` :1177), **withdraws** v1's "270 category-associated tools" figure as unreproducible, and claims **no** complete static taxonomy | **§3.5**, **§1.2 (D-NEW-2)**, **§8.2** |
| **C2** — `mcp` / `mcp_rpc` / `plane` expand EMPTY in a static map, which yields a FALSE NO (the mirror of the false YES); v1:239 asserted the opposite | agentic B2 | v3 renders those three classes `unknown` (never `yes`/`no`), explains why (stub modules, DB/config server list, prefix override), and keeps the ownership guards that DO decide `mcp_rpc_*` and `plane_sync_*` statically | **§3.6.4**, **§3.6.5**, **§8.3** |
| **C3** — the census pack `test/packs/tool_usage_pins_unit_test.sh` was unacknowledged | agentic B3 | v3 carries the full census-re-stamp obligation (all three surfaces, same commit, pack's own `--collect-only` method, lane worktree's own venv) | **§3.9**, **§4.5** |
| **C4** — only 1 of 5 `digest_root` pins was named, and v1:45 DELETES `_code_root()` | agentic B4 | v3 names all 5 pins, specifies each rewrite, enumerates `_code_root()`'s callers (2 production + 2 test sites) and decides **KEEP** (re-pointed, docstring corrected) rather than delete | **§3.9.1**, **§3.4.1** |
| **C5** — the T-B test cannot pass as specified, and T-TESTS(a) has no real-artifact test | agentic B5 | v3 gives T-B a passing shape (a REAL tmp install layout, so the house resolver actually resolves) and makes the semantics suite exercise the **real** artifact path with no monkeypatch | **§3.9.1 (T-B/T-B2/T-B3)**, **§3.9.3** |
| **C6** — the no-silent-drop rule was declared but never populated; "unclassified" was undefined | agentic B6 | v3 DEFINES "unclassified" and populates it with the measured population (allow: **0**; deny: **1** — `maintenancer: git_commit`) | **§3.6.6**, **§3.3 (artifact `unresolved` block)**, **§4.4** |
| **C7** — the F2 census was wrong | coding B1 | RESOLVED by R-CENSUS: v3 carries the corrected census (65 / 278 / 0-neither on the allow side) and re-derives it at `d7c15d45f` in Appendix A | **§4.1**, **§4.2**, **Appendix A** |
| **C8** — the C1 census-collision was unaddressed and v1:428's residual claim was false | coding B2 | v3 explains the collision (the "8 neither" population was the single string `room` read at the stale detached HEAD, where `CATEGORY_MODULES` has 64 keys) and CORRECTS the false residual | **§4.2**, **§8.4** |
| **C9** — F4 (MCP) unaddressed | coding B3 | same resolution as C2 | **§3.6.4**, **§3.6.5**, **§8.3** |

**NOTES folded in (scope-neutral):** F5 pin misattribution — resolved by naming every pin with its file and assertion (§3.9); F6 precedent loose — resolved by stating exactly which precedent is followed and which is deliberately NOT (the `4ae666c4a` drift) (§4.5); D4 test pin missing — resolved by two explicit pinning assertions (§5.4); C2 partial — resolved by the equivalence proof over all 55 agents (§3.9.3); schema-2 shape unpinned — resolved by the schema-gate test set (§3.9.4); no-silent-drop untested — resolved by the unresolved-population test (§3.9.4).

---

## 2. TRADE-OFF MATRIX

### 2.1 Artifact delivery

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Payload dir (RECOMMENDED)** — `releases/<v>/tool-usage-artifacts/{allowlists.json,tool-weights.json}` | **Med** — 6 insertion points in `stage.sh` mirroring the `guidelines/` precedent (`:487` copy, `:619-620` walk+hash, `:690-699` per-file map, `:894`/`:899` manifest keys, `:942-948` stage-local re-hash leg) | **High** — two small JSON files (~16 KB + taxonomy); trivial next to the 4930-file plugins tree | **High** — self-contained per release; visible in `manifest.json`; provenance inside the file | **Low** — additive manifest keys; no existing key is renamed or removed; no exact-key-set assertion exists in the test corpus | **Low** — one generator call through `stage.sh`'s EXISTING `_stage_cc_py` resolution (`:537-541`: the lane worktree's venv python, else `python3`) + one tree walk at stage time — **never a hardcoded `python3`** (A4) | **RECOMMENDED** |
| **Embed in the frozen binary** (`ensemble.spec` datas) | **Med** — PyInstaller spec work + runtime introspection | **Low** — every `meta.json` allow/deny edit forces a full binary rebuild | **Low** — datas are opaque to shell-level release integrity checks | **High** — build(T1) vs stage(T2) skew; per-release mutable data tracking the registry | **Med–High** — build cycle + test latency on every taxonomy change | **REJECTED** |
| **Install into the install tree at stage time** | **High** — custom stage/promote/rollback hooks | **Low** — splits release state across versioned and unversioned trees | **Med** — two homes for the same artifact | **Critical** — a staged-not-promoted release overwrites the live artifact; rollback does not restore the previous one | **Low** code, severe operational debt | **REJECTED** |
| *(reference)* `docs/guidelines` → `releases/<v>/guidelines` | — | — | — | precedent verified in production; stage-local verification only | — | **TEMPLATE** |

### 2.2 Payload-dir fork

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Clean dedicated dir (RECOMMENDED)** — `releases/<v>/tool-usage-artifacts/` | **Low–Med** — one reader path constant + the frozen/source fork | **High** — completely isolated from repo planning artifacts and scratch files | **High** — the directory name states its contents | **≈Zero D4 risk** — no shared parent with the write side, so no writable dir can land inside a hashed tree | **Low** | **RECOMMENDED** |
| **Mirror the repo path** — `releases/<v>/.agents/shared/planning/tool-usage-metrics-p1/` | **Low** — no reader fork | **Low** — bakes the repo hierarchy into release binaries | **Low** — re-confuses read artifacts with the mutable `digests/` output | **High** — recreates the shared parent; excluding `digests/` is a fragile exclusion that one added file undoes | **Low** | **REJECTED** |

### 2.3 Expansion source

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Artifact carries the map (RECOMMENDED)** — a source-derived, registry-keyed category→tools map emitted by the generator, read in the reader | **Med** — the generator must derive a per-category attribution the house walker does not currently return | **High** — the reader does in-memory dict lookups; no registry import, no DB, no MCP state | **High** — the taxonomy is frozen with the release and is inspectable with `jq` | **Low** — preserves the determinism AST pin; the map is a superset-of-source attribution that is DISCLOSED as such | **Low** | **RECOMMENDED** |
| **Generator expands to flat per-agent tool lists** | **Low** — generator emits only names | **Med** — loses category attribution | **Low** — the digest can no longer say *why* a tool is granted | **Med** — the zero/near-zero section loses the "granted via category" story the commission asks for | **Low** | **REJECTED** |
| **Runtime registry scan** (`list_tools_by_category()` at digest time) | **High** — the reader imports daemon modules | **Low** — fails in the frozen binary (bytecode-only tree) | **Low** — couples the digest to per-instance registry state | **Critical** — violates `test_no_live_env_or_registry_read_inside_the_rollup` (`:599-607`) and re-introduces the live-read the plan forbids (`phase3-plan.md:80`) | **Med** | **REJECTED** |

### 2.4 Deny modelling

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Artifact carries `deny` (RECOMMENDED)** — per agent, alongside `allow` and `innate` | **Med** — the generator emits a second dimension; the reader subtracts after expanding | **High** — one dict lookup per agent | **High** — the artifact becomes the single, auditable statement of "what this agent is granted" | **Low** — deterministic; the equivalence proof is a straight comparison against `resolve_tool_filter` | **Low** | **RECOMMENDED** |
| **Read the deny list live** (from `agents/*/meta.json` at digest time) | **Low** — no artifact change | **Low** — a live tree scan at digest time | **Low** — a second source of truth for the same fact | **Critical** — breaks the frozen determinism invariant (`phase3-plan.md:80` item 12(c) — the verbatim quote; restated by T2 at `:97` in lower case: "the digest NEVER scans the live `agents/` tree at run time") and the `agents/` AST pin | **Low** | **REJECTED** |
| **Skip deny entirely (v1's position)** | **Low** | **High** | **Low** | **Critical** — 59 deny entries resolve to individual tool names (93 deny pairs / 34 distinct) and 33 to categories; skipping them yields false YES claims | **Low** | **REJECTED** |

---

## 3. THE RECOMMENDED SHAPE

### 3.1 Files and symbols to touch

| File | Action | Symbols / changes |
|---|---|---|
| `daemon/services/tool_usage_digest.py` | **MODIFY** | `DIGEST_ROOT_ENV` (unchanged); **NEW** `ARTIFACT_DIR_ENV = "ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR"` (test seam, documented test-only); **NEW** `DigestRootUnresolved(RuntimeError)`; **NEW** `_install_root()`; `_code_root()` **KEPT** (docstring/comment corrected — see §3.4.1); `digest_root()` re-pointed; `_artifact_dir()` re-pointed; `_load_json_artifact()` → versioned loader; **NEW** `allowlist_source() -> AllowSource`; `allowlists()` (contract preserved, semantics changed to EFFECTIVE grants); **NEW** `effective_agent_tools()` / `grant_verdict()`; `rollup()` (per-agent verdicts + reasons; AST pins still satisfied); `render_markdown()` (honest heading + disclosure + attribution/denial markers); `digest_path()` / `write_digest()` (unchanged shape); `__all__` extended. |
| `daemon/tools/_tool_registry.py` | **MODIFY (additive)** | **NEW** `scan_category_module_sources_by_category() -> dict[str, dict[str, str \| None]]` — the per-category attribution sibling of the existing walker; `_scan_category_module_sources()` (`:1063`) is re-implemented as a **delegate** to it (same return shape, same `any_source_read` semantics → `discover_source_only_tool_names()` :1143 and `discover_all_tool_names()` :1177 are behaviourally unchanged, so the registration pack's zero-drift pin still holds). **NO second AST walker is introduced.** |
| `scripts/gen_tool_usage_allowlists.py` | **MODIFY** | `build_payload()` → schema-2 payload (agents{allow,deny,innate}, categories, runtime_only_categories, privileged_categories, dynamic_prefixes, innate_skill_categories, **known_tool_names**, unresolved, provenance); reads `tools.deny` (today it reads ONLY `tools["allow"]` — zero occurrences of "deny" in the current source) and `innate_skills`; derives the taxonomy via the extended house walker; **NEW** `--verify` (regenerate-and-compare — SEMANTIC PAYLOAD byte equality, with the provenance compared SEPARATELY, §3.8); **NEW** `--tree-commit`; deterministic output preserved (`json.dumps(..., indent=2, sort_keys=True)`, no timestamps). |
| `scripts/upgrade/stage.sh` | **MODIFY** | generate the payload into `$STAGE_TMP/tool-usage-artifacts/` (copy `tool-weights.json` verbatim, generate `allowlists.json` from `$REPO_ROOT` **through `"$_stage_cc_py"`** — the interpreter `stage.sh` already resolves at `:537-541` (venv python, else `python3`); **never a hardcoded `python3`**, A4); `_stage_tree_walk tool-usage-artifacts` + `_tree_hash_of_lines`; per-file map (same shape as the `guidelines/` block); two new `manifest.json` keys; a stage-local re-hash leg mirroring `:942-948`. |
| `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` | **REGENERATE** | schema 2 (dev/source-mode artifact). |
| `.agents/shared/planning/tool-usage-metrics-p1/tool-weights.json` | **UNCHANGED** | byte-identical `{}\n` (3 bytes; sha256 `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356`, sha1 `5f36b2ea290645ee34d943220a14b54ee5ea5be5`, git blob `0967ef424bce6791893e9a57bb952f80fd536e93`); the weights artifact's shape is deliberately OUT OF SCOPE (reviewer ruling 9(a): the empty `{}` is the sanctioned state). |
| `tests/unit/services/test_tool_usage_digest_root.py` | **REWRITE** | all 5 pins rewritten + 4 added (9 nodes). |
| `tests/unit/services/test_tool_usage_digest_determinism.py` | **MODIFY** | +1 AST pin node (38); `hermetic_artifacts` also neutralizes `allowlist_source` — with a **DETERMINATE "ok"-state envelope**, never a degraded one (§3.9.2, APPROVER-B3); the two ad-hoc `allowlists` monkeypatch legs (`:497`, `:530`) keep working **UNCHANGED** — `allow_listed` stays a BOOLEAN and the reason is a separate, degradable input (§3.6.6). |
| `tests/unit/tools/test_tool_usage_digest_registration.py` | **MODIFY** | **+2 nodes (22 → 23)**: the D4 pin node (56, §3.9.5) + the F6 generator-output freshness test (node 57, §3.9.8 — the F6 test is homed here so its +1 folds into the §3.9.7 census arithmetic without adding an 11th slice file). |
| `tests/unit/services/test_tool_usage_digest_allow_semantics.py` | **NEW** | the equivalence + mutation + probe suite (**12 nodes**; §3.9.3 — the original 9 plus the 3 verdict assertions that close APPROVER-B2). |
| `tests/unit/services/test_tool_usage_digest_artifact_schema.py` | **NEW** | the schema gate + loudness + no-silent-drop + MCP-unknown suite (8 nodes; §3.9.4). |
| `tests/test_tool_usage_artifacts_payload.sh` | **NEW** | hermetic stage-level payload test (copy, manifest hashes, corruption rejection, D4 pin (ii)). **Its runner is named in §3.9.6 — a `.sh` file has no pytest home, so without the wrapper pack + its PACKS.md row the test never reaches the gate.** |
| `test/packs/tool_usage_artifacts_payload_unit_test.sh` | **NEW** | the **RUNNER** for the shell test above — a thin wrapper that invokes `timeout 480s bash tests/test_tool_usage_artifacts_payload.sh < /dev/null 2>&1` with a dual-layer timeout, the house pattern for the sibling stage-payload shells (`test/packs/executor_family_payload_unit_test.sh` wrapping `tests/test_executor_family_payload.sh`, registered at `.agents/tester/PACKS.md:2220`; `test/packs/guidelines_read_tool_unit_test.sh` leg 2 wrapping `tests/test_guidelines_payload.sh`, registered at `.agents/tester/PACKS.md:749`). Registered as its own PACKS.md row (§3.9.6). A `.sh` file cannot join the pytest census slice, so it adds **0** nodes. |
| `test/packs/tool_usage_pins_unit_test.sh` | **MODIFY** | census re-stamp (§3.9.5, §3.9.7) + header prose + **BOTH** `"8 files"` literals: the `:325` echo literal AND the `:7` HEADER literal `"(8 files, one pack script + one PACKS.md block)"` → "10 files" (A3). |
| `.agents/tester/PACKS.md` | **MODIFY** | row `:156` counts/total/freeze sha + the prose clause at `:150` (and the "eight-file"/"200 nodes" prose at `:148`) **+ the NEW row that registers the shell test's runner pack (§3.9.6)**. |
| `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts` | **MODIFY** | doc comment `:62-68` + template caveat `:213-219`. |
| `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts` | **MODIFY** | +2 pins on the honest claim (see §6.2). |

### 3.2 Exact artifact paths, roots, and manifest keys

| Item | Path (relative to its root) | Root |
|---|---|---|
| Dev/source-mode artifacts (read side) | `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` · `.agents/shared/planning/tool-usage-metrics-p1/tool-weights.json` | the checkout (source mode) |
| Release payload (read side, production) | `tool-usage-artifacts/allowlists.json` · `tool-usage-artifacts/tool-weights.json` | `releases/<v>/` (the release dir = the running binary's dir) |
| Digest output (write side) | `.agents/shared/planning/tool-usage-metrics-p1/digests/YYYY-MM-DD.md` | the resolved install root (live install dir in production; the checkout in source mode — gitignored at `.gitignore:96`) |
| Manifest keys (new, additive) | `"tool_usage_artifacts_tree_sha256": "$TOOL_USAGE_ARTIFACTS_TREE"` (beside `:894`) · `"tool_usage_artifacts_manifest": {$tool_usage_artifacts_map}` (beside `:899`) | `releases/<v>/manifest.json` |

The payload dir contains **only** the two JSON files — no `digests/`, no scratch, no mutable output. The digest output dir is under a **different root** (the install root's `.agents/`), so no writable path is a descendant of any shipped tree (§5.3).

### 3.3 The schema-2 artifact (exact shape)

```json
{
  "schema_version": 2,
  "agents": {
    "<agent-id>": {
      "allow":  ["<entry>", "..."],
      "deny":   ["<entry>", "..."],
      "innate": ["<skill>", "..."]
    }
  },
  "categories": {
    "<registry-key>": ["<tool-name>", "..."]
  },
  "runtime_only_categories": ["mcp", "plane"],
  "privileged_categories": ["board", "board-link", "ens-db", "ens-env", "guidelines", "instance_ops", "instance_read", "mcp_rpc", "memoir", "metrics-read", "nest-report-read", "notify-user", "prompt-assent", "prompt-reload", "room", "secrets", "self-alarm", "settings", "system-log", "system_upgrade", "view-views", "view-write", "wedge-capture"],
  "dynamic_prefixes": ["plane_"],
  "innate_skill_categories": {"chart": ["chart"], "todo": ["todo"], "dynamic-skill": ["dynamic-skill"], "skill-evolution": ["skill-evolution"], "question": ["question"]},
  "unresolved": {
    "allow": [],
    "deny":  [{"agent": "maintenancer", "entry": "git_commit"}]
  },
  "provenance": {
    "generator": "scripts/gen_tool_usage_allowlists.py",
    "generator_version": 2,
    "tree_commit": "<git rev-parse HEAD of the tree the generator ran in>",
    "inputs": {
      "agents":   {"last_commit": "<git log -1 --format=%H -- agents/>", "content_sha256": "<sha256 over the sorted 'path\\0sha256' list of every agents/*/meta.json>"},
      "taxonomy": {"last_commit": "<git log -1 --format=%H -- daemon/tools/>", "content_sha256": "<sha256 of the canonical JSON of {categories, runtime_only_categories, privileged_categories, dynamic_prefixes, innate_skill_categories}>"}
    }
  }
}
```

Rules that the shape encodes:

- **`schema_version` is mandatory and is the gate.** A payload without `schema_version`, or with a value ≠ 2, is **REJECTED LOUDLY** (R-SCHEMA). The committed v1 shape (`{"agents": {"<id>": ["a","b"]}, "generated_from_commit": "…"}`) is schema 1 and is rejected.
- **The `agents` map's values are OBJECTS, not lists.** That is a second, structural reason an old artifact cannot be misread as a new one: an old reader iterating a dict would produce the literal strings `"allow"`, `"deny"`, `"innate"` as tool names. The new reader never sees an old artifact in production (the payload ships with the binary), but the shape change makes a silent misread structurally impossible in the dev/source mode too.
- **`generated_from_commit` is RETIRED.** It named the last commit touching `agents/` only (measured value at `d7c15d45f`: `a064fedf49a604f36cdcc3012161fe6cce9f7367`, 2026-11-20) while the artifact also depends on the registry taxonomy — a lie about provenance. `provenance.inputs` names **every** input with both a last-commit and a **content** hash; `provenance.tree_commit` names the tree.
- **`known_tool_names` is the FULL KNOWN-NAME VOCABULARY** (`KNOWN_TOOL_NAMES ∪ DYNAMIC_TOOL_NAMES` = **278** names at `d7c15d45f`), NOT a category projection. It is what lets the reader separate a **"no"** from an **"unknown"** for a name that belongs to no static category — the 7 DYNAMIC-only names (`comment_edit`, `commit_docs_validated`, `doc_write`, `od.compose_brief`, `od.generate`, `od.lint`, `od.save`). Without it (A1) those names, and every `plane_*` / MCP-shaped name, collapse into "unknown" and the reader loses the line between "the daemon does not grant this" and "the digest cannot tell" — which is exactly the line §4.6's 162 / §8.2's 201 are drawn on. It is derived by the generator from the two house vocabulary constants, never hand-listed (§3.8).
- **`provenance.tree_commit` is INFORMATIONAL.** It names the tree the generator ran in and is **EXCLUDED from every equality comparison** — it moves on EVERY commit, including commits that touch neither `agents/` nor `daemon/tools/` (APPROVER-B1, §3.8).
- **`unresolved` is the no-silent-drop ledger** (§3.6.6).

### 3.3.1 THE EXACT PINNED SHAPE (normative — a developer can write the artifact from this alone)

The sketch in §3.3 is illustrative; **this subsection is normative**.

**Top-level keys (exactly ten, no others):** `schema_version`, `agents`, `categories`, `runtime_only_categories`, `privileged_categories`, `dynamic_prefixes`, `innate_skill_categories`, `known_tool_names`, `unresolved`, `provenance`.

**The seven SEMANTIC PAYLOAD keys** — the ones `--verify` byte-compares (APPROVER-B1): `agents`, `categories`, `runtime_only_categories`, `privileged_categories`, `dynamic_prefixes`, `innate_skill_categories`, `known_tool_names`. The other three are compared under their OWN rules, never as one blob: `schema_version` must **EQUAL 2** (a scalar gate); `unresolved` must **EQUAL the regenerated ledger as a SET** (entry order is irrelevant); `provenance` is compared by the **PROVENANCE rules** of §3.8 (content hashes ALWAYS; `last_commit` ONLY where the corresponding content hash moved; `tree_commit` INFORMATIONAL and excluded).

| Key | Type | Value |
|---|---|---|
| `schema_version` | integer | **2** (the literal integer `2`, not `"2"`) |
| `agents` | object | map `agent-id` → agent object (see below) |
| `categories` | object | map `registry-key` → array of tool names (sorted) |
| `runtime_only_categories` | array of strings | sorted; `["mcp", "plane"]` today. **Derivation rule — a category key with EMPTY attribution:** a `CATEGORY_MODULES` key for which the per-category walk returns no `@tool` member (an empty runtime stub) is listed here. It is DERIVED by the generator, never hand-listed, and it is the complement of the keys that carry attribution (measured at `d7c15d45f`: 65 keys, 63 with attribution, 2 empty → `mcp`, `plane`). |
| `privileged_categories` | array of strings | sorted; the 23 `PRIVILEGED_TOOL_CATEGORIES` members |
| `dynamic_prefixes` | array of strings | sorted; `["plane_"]` today |
| `innate_skill_categories` | object | innate-skill name → array of category keys (sorted) |
| `known_tool_names` | array of strings | sorted; the FULL KNOWN-NAME VOCABULARY = `KNOWN_TOOL_NAMES ∪ DYNAMIC_TOOL_NAMES` (**278** names at `d7c15d45f`: 271 ∪ 121, overlap 114). **Derivation rule — a name the house vocabulary constants declare:** the generator reads both constants from `daemon/tools/_tool_registry.py` (`KNOWN_TOOL_NAMES` `:1719`, `DYNAMIC_TOOL_NAMES` `:26`) and emits their union, sorted. It is NEVER derived from `categories` (that projection misses the 7 DYNAMIC-only names — A1) and NEVER hand-listed. It is the reader's ONLY basis for the "name not in the vocabulary ⇒ not statically decidable ⇒ `unknown`" rule (§3.6.3). |
| `unresolved` | object | `{"allow": [...], "deny": [...]}` — the no-silent-drop ledger (§3.6.6) |
| `provenance` | object | §4.4 |

**The per-agent object — this is where `deny` sits: INSIDE the per-agent object, as a SIBLING of `allow` (not a second top-level map, and NOT appended to the allow array):**

```json
"explorer": {
  "allow":  ["blueprint", "context", "filesystem", "help", "mcp", "nest-report", "pok", "rag", "shared_meta_kv", "time"],
  "deny":   ["experience", "explore", "rag_insert_text", "rag_insert_texts", "rag_query"],
  "innate": []
}
```

Field rules, stated exactly:

| Field | Type | Rule |
|---|---|---|
| `allow` | array of strings | the agent's `tools.allow` **verbatim**, de-duplicated and sorted; entries are NOT expanded here (expansion is the reader's job — R-EXPANSION) |
| `deny` | array of strings | the agent's `tools.deny` **verbatim**, de-duplicated and sorted; **always present**, even when empty (`"deny": []`) — an ABSENT `deny` key is a **rejected** artifact, never an empty deny (§3.7, test 50) |
| `innate` | array of strings | the agent's `innate_skills` verbatim, de-duplicated and sorted; **always present** (`"innate": []`); the reader applies `innate_skill_categories` to derive the implicit category grants (D-NEW-1) |

**Worked example — one agent, both dimensions, from the live corpus at `d7c15d45f` (`agents/explorer/meta.json`):**

```json
{
  "schema_version": 2,
  "agents": {
    "explorer": {
      "allow":  ["blueprint", "context", "filesystem", "help", "mcp", "nest-report", "pok", "rag", "shared_meta_kv", "time"],
      "deny":   ["experience", "explore", "rag_insert_text", "rag_insert_texts", "rag_query"],
      "innate": []
    }
  },
  "categories": {
    "bash": ["bash"],
    "filesystem": ["edit_file", "glob_files", "grep_files", "list_directory", "read_file", "write_file"],
    "rag": ["rag_create_entity", "rag_create_relation", "rag_delete_docs", "rag_delete_entity", "rag_delete_relation", "rag_get_entity", "rag_get_graph", "rag_insert_text", "rag_insert_texts", "rag_list_docs", "rag_merge_entities", "rag_query", "rag_query_data", "rag_search_labels", "rag_track_status", "rag_update_entity"]
  },
  "runtime_only_categories": ["mcp", "plane"],
  "privileged_categories": ["board", "board-link", "ens-db", "ens-env", "guidelines", "instance_ops", "instance_read", "mcp_rpc", "memoir", "metrics-read", "nest-report-read", "notify-user", "prompt-assent", "prompt-reload", "room", "secrets", "self-alarm", "settings", "system-log", "system_upgrade", "view-views", "view-write", "wedge-capture"],
  "dynamic_prefixes": ["plane_"],
  "innate_skill_categories": {"chart": ["chart"], "dynamic-skill": ["dynamic-skill"], "question": ["question"], "skill-evolution": ["skill-evolution"], "todo": ["todo"]},
  "known_tool_names": ["<tool-name>", "..."],
  "unresolved": {
    "allow": [],
    "deny":  [{"agent": "maintenancer", "entry": "git_commit"}]
  },
  "provenance": {
    "generator": "scripts/gen_tool_usage_allowlists.py",
    "generator_version": 2,
    "tree_commit": "<sha>",
    "inputs": {
      "agents":   {"last_commit": "<sha>", "content_sha256": "<sha256>"},
      "taxonomy": {"last_commit": "<sha>", "content_sha256": "<sha256>"}
    }
  }
}
```

(The `categories` entries above are abbreviated to the members this document needs; the generator emits the full per-key lists. The `rag` entry is the COMPLETE 16-member list measured at `d7c15d45f` (AST over `daemon/tools/rag_tools.py`), because three of its members — `rag_insert_text`, `rag_insert_texts`, `rag_query` — are the DENY probes this document relies on; the names `rag_delete_entries`, `rag_search` and `rag_stats` that v3 listed exist NOWHERE in `daemon/tools/` and are removed.)

**Serialization rules (determinism):** the whole file is `json.dumps(payload, indent=2, sort_keys=True)` plus a trailing newline — key order is alphabetical at every level, every array is sorted, and there is **no timestamp** anywhere, so the same tree yields byte-identical bytes. The file is UTF-8.

### 3.4 The resolver (pseudocode)

```python
DIGEST_ROOT_ENV = "ENSEMBLE_TOOL_USAGE_DIGEST_ROOT"        # unchanged (D-6 / U8)
ARTIFACT_DIR_ENV = "ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR"      # NEW — test seam only, unset in production
DIGEST_RELATIVE_DIR = Path(".agents") / "shared" / "planning" / "tool-usage-metrics-p1" / "digests"
ARTIFACT_DIRNAME = "tool-usage-artifacts"                  # the payload dir name (R-PAYLOAD)

class DigestRootUnresolved(RuntimeError):
    """Raised when the canonical digest destination cannot be VERIFIED."""

def _verified_install_root(candidate: Path | None) -> Path | None:
    if candidate is None:
        return None
    try:
        if (candidate / "releases").is_dir() or (candidate / "daemon").is_dir():
            return candidate
    except OSError:
        return None
    return None

def _install_root() -> Path:
    from daemon.repositories.source.maintenance_seed import resolve_live_install_root
    root = _verified_install_root(resolve_live_install_root())
    if root is None:
        raise DigestRootUnresolved(
            "Digest destination root unresolved: the live install root could not be verified "
            f"(expected a directory owning 'releases/' or 'daemon/'). Set {DIGEST_ROOT_ENV} explicitly."
        )
    return root

def digest_root() -> Path:
    """Resolve the canonical digest root (D-6 / U8). Env override wins."""
    env = os.environ.get(DIGEST_ROOT_ENV)
    if env and env.strip():
        return Path(env.strip())
    return _install_root() / DIGEST_RELATIVE_DIR

def _artifact_dir() -> Path:
    """Resolve the READ-ONLY artifact dir (R-PAYLOAD)."""
    env = os.environ.get(ARTIFACT_DIR_ENV)
    if env and env.strip():
        return Path(env.strip())
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / ARTIFACT_DIRNAME     # releases/<v>/tool-usage-artifacts
    return _code_root() / ".agents" / "shared" / "planning" / "tool-usage-metrics-p1"
```

#### 3.4.0 Why the digest imports the house resolver directly (and writes no walk)

v3 imports `resolve_live_install_root()` directly — `from daemon.repositories.source.maintenance_seed import resolve_live_install_root` — and adds **no** new root walk, no `../..` guess and no `sys.executable.parent` anchor. The grounds are:

1. **The resolver is public and already exercised.** No leading underscore; exported in the module's `__all__` (`maintenance_seed.py:1351`); consumed internally at `:321`; covered by two test modules (`tests/unit/test_maintenance_results_root_equivalence.py:28` and `:96`; `tests/unit/test_maintenance_seed_guard.py:56`, `:258`, `:269`, `:291`, `:317`).
2. **Service → repository-source imports are established in this codebase** — `daemon/services/execution_receipt.py:28`, `daemon/services/scheduling_service.py:963`, `daemon/services/sources_gc_service.py:56`.
3. **Extraction buys nothing here.** Moving the resolver into a "neutral" module would ripple through `maintenance_seed`'s own consumers and both test modules for zero behavioural gain, and would split the promote-stability rationale across two homes.

(For the record: a grep-fence pin does exist at `tests/unit/test_maintenance_results_root_equivalence.py:51`, but it pins **`maintenance_results_dir`** — not the resolver — and it is **not** part of this justification. The import direction stands on the three grounds above.)

#### 3.4.1 `_code_root()` — KEEP, re-pointed, and its callers enumerated (C4)

v1:45 proposed to **delete** `_code_root()`. v3 **keeps** it. The full caller census at `d7c15d45f` (`git grep -n '_code_root'`):

| Caller | Line | After v3 |
|---|---|---|
| `digest_root()` | `tool_usage_digest.py:63` | **no longer calls it** — re-pointed to `_install_root()` |
| `_artifact_dir()` | `tool_usage_digest.py:67` | **keeps calling it** — it is the correct anchor for the READ side (the code tree that ships the artifact) |
| `tests/unit/services/test_tool_usage_digest_root.py:24` | test | rewritten (pin 1 / pin 2, §3.9.1) |
| `tests/unit/services/test_tool_usage_digest_root.py:37` | test | rewritten (pin 2) |

No other module, script, test, or document references `_code_root` — so keeping it costs nothing and removing it would require migrating the two test call sites for no gain. Its current comment is **wrong** and must be corrected: it says the frozen branch uses "sys.executable's parent (the install dir)" — that path is the **RELEASE dir** (`releases/<v>/`), which is exactly the anchor D-6/U8 forbid for the WRITE side (a promote moves it; keep-3 eviction destroys it). The corrected contract is: *"`_code_root()` = the root of the executing CODE tree — the RELEASE dir when frozen, the checkout root in source mode. It is valid ONLY as the anchor for read-only data shipped with the code; it is NEVER a write destination."*

### 3.5 The taxonomy: exactly what is statically enumerable (C1)

**v1's claim is withdrawn.** v1 asserted a complete 65-category static taxonomy and reported "270 category-associated tools" resolved by "scanning all 65 modules in `CATEGORY_MODULES`". The house scanner does not produce that number, and v1 misstated it. The measured truth at `d7c15d45f` (Appendix A, reproducible):

| Class | Count | Members | How it is derived |
|---|---|---|---|
| **S — source-enumerable, module-level `@tool`** | **5** | `attestation`, `bash`, `filesystem`, `language`, `time` | a `@tool`-decorated function at module scope in the key's module file(s) — import-time constructible |
| **S — source-enumerable, factory-only `@tool`** | **58** | every other key except the two below | a `@tool`-decorated function at ANY nesting depth (inside a factory) — the house walker descends into nested scopes for exactly this reason (`_tool_registry.py:1063`, the quote at `:1073-1074`: "The AST walker descends into every nested scope so factory-internal `@tool` decorations are caught (they never register at import time)") |
| **R — runtime-only (no `@tool` anywhere)** | **2** | **`mcp`** (`daemon/tools/mcp_tools.py`, 15 lines: "MCP tools are discovered dynamically at runtime from configured MCP servers… individual tools are not defined in this module") and **`plane`** (`daemon/tools/plane_tools.py`, 17 lines: "Plane tools are created dynamically at runtime by `create_lazy_mcp_tools` with the `plane_` prefix override") | membership is call-time state — the MCP server list (DB/config) and the `plane_` prefix sweep |

**The council's "52 of 65 statically enumerable" is a RUNTIME-population observation, not a static property.** `list_tools_by_category()` (`:1048`) is built from `_tool_metadata`, which is populated per instance by `scan_tools_for_full_docs(tools)` (`:1228`, the docstring's "This should be called after tools are created" at `:1231`): which category KEYS appear depends on the instance's own tool set (its agent's allow list, MCP registration, feature gates). So "52 keys present" is a fact about one measuring process, not about the source. v3 therefore:

- **does not claim a complete static taxonomy** (C1);
- classifies keys by SOURCE enumerability (the table above) and **declares the runtime-only keys explicitly** in the artifact (`runtime_only_categories`);
- treats the *presence* of a key in the runtime map as instance-dependent and **discloses** it (G4, §8.1);
- **reuses the house walker** rather than adding a second one: the registry gains `scan_category_module_sources_by_category()` (per-category attribution, with `@register_tool_category("X")` overrides honoured) and `_scan_category_module_sources()` delegates to it.

**Attribution rule (measured).** 238 `@tool` functions carry an explicit `@register_tool_category("X")`; **14 of them attribute to a category other than their defining module's key** (`instance` → `council` for `spawn_councilor`, `clear_councilor_errors`, `convene_council`, `convene_council_with_skill`; `council` → `instance` for `spawn_instance`, `send_message`, `subtree_messages`, `subtree_status`, `terminate_instance`, `pause_instance`, `resume_instance`, `list_instances`, `get_instance_info`; `job` → `mission` for `watch_mission`), and **0** name a category that is not a `CATEGORY_MODULES` key. So the generator attributes by: explicit decorator arg (resolved as a literal, or against the module's constants) → else the defining module's key. The 14 cross-attributions are exactly the ones a naive "defining module = category" rule would mis-assign; the attribution test pins them.

**Why `list_tools_by_category()` (registry keys) and not `get_tool_categories()` (display names).** `get_tool_categories()` (`:2140`) returns DISPLAY names — the module's `CATEGORY_NAME` ("File Operations", "Nest Report", "Shell", "MCP") — so an allow entry `filesystem` would never match a key "File Operations", and the digest would report a false NO for every categorized tool. The artifact's category keys are the **registry keys** (the `CATEGORY_MODULES` key space — the same key space the validator uses at `daemon/registry.py:1413`, `known_categories = set(CATEGORY_MODULES.keys())`). The four names that are BOTH a category key and a tool name (`bash`, `shared_meta_kv`, `system_upgrade`, `time`) are pinned explicitly: the runtime's `if item in tool_categories: … else: add(item)` makes the **category win**, so the same-named tool is granted only if the category's own tool list contains it.

### 3.6 Expansion, deny, ownership guards, and verdicts (pseudocode)

#### 3.6.1 The effective allow list (mirrors `resolve_tool_filter`, `daemon/tools/instance.py:455-611`)

```python
def effective_allow(agent: AgentSpec, src: AllowSource) -> list[str] | None:
    """Mirrors expand_allow_for_innate_skills (instance.py:204-249)."""
    if not agent.innate or agent.allow is None:
        return agent.allow
    extra = []
    for skill in agent.innate:
        for cat in src.innate_skill_categories.get(skill, []):
            if cat in src.privileged_categories:      # fail-closed second door (instance.py:242)
                continue
            if cat not in agent.allow and cat not in extra:
                extra.append(cat)
    return [*agent.allow, *extra] if extra else agent.allow

def effective_agent_tools(agent, src) -> tuple[set[str], dict[str, str], set[str]]:
    """Returns (granted tool names, {tool: reason}, granted CATEGORY KEYS).

    Mirrors resolve_tool_filter. The THIRD element is the set of category keys
    the agent was granted — INCLUDING the runtime-only ones ("mcp", "plane"),
    whose members are call-time state and therefore contribute NO static tool
    names but MUST still make `category_granted(agent, "plane")` TRUE
    (APPROVER-B2b). It is accumulated EXPLICITLY, never DERIVED from the reason
    strings: a runtime-only category has no members, so no member reason would
    ever carry its name — deriving it from reasons is exactly how v3 lost the
    `plane` category grant (and with it the "unknown" verdict for the `plane_`
    sweep). `src.category_keys` is `categories ∪ runtime_only_categories`
    (§3.6.2a) — the reader NEVER consults `categories` alone.
    """
    allow = effective_allow(agent, src)          # never None from a schema-2 artifact
    deny = agent.deny or []
    granted_categories: set[str] = set()
    if not allow:
        # instance.py:581-591 — an EMPTY allow means "everything potentially allowed"
        # REGARDLESS of deny (the docstring at :8117-8119 names exactly this
        # "empty-allow + non-empty-deny universe construction"). Deny is applied below.
        granted = {t for cat, tools in src.categories.items()
                   if cat not in src.privileged_categories for t in tools}
        reasons = {t: "inherit-all (empty allow; non-privileged only)" for t in granted}
        granted_categories = {cat for cat in src.category_keys
                               if cat not in src.privileged_categories}
    else:
        granted, reasons = set(), {}
        for entry in allow:
            if entry in src.category_keys:
                # A CATEGORY KEY — including a runtime-only one ("mcp", "plane"):
                # the category IS granted, but a runtime-only category has no
                # static members (its members are call-time state), so it
                # contributes no tool NAMES here while still counting as a
                # granted category (§3.6.2a).
                granted_categories.add(entry)
                for t in src.categories.get(entry, ()):
                    granted.add(t)
                    reasons.setdefault(t, f"category:{entry}")
            else:
                granted.add(entry)
                reasons.setdefault(entry, "literal")
    denied = set()
    for entry in deny:
        if entry in src.category_keys:
            denied |= set(src.categories.get(entry, ()))
            # A deny entry naming a RUNTIME-ONLY category has no static members:
            # the names it would remove are call-time state and render
            # "unknown", so nothing is silently denied here.
        else:
            denied.add(entry)
    granted -= denied                           # DENY WINS (instance.py:609)
    for t in denied:
        reasons.pop(t, None)
    return granted, reasons, granted_categories
```

The runtime's own comment at `instance.py:596-599` is the **G4 disclosure** the artifact must carry: *an allow entry naming a category that is ABSENT from the runtime map falls through to the literal branch and grants the category NAME as if it were a tool name — i.e. it silently grants nothing.* v3 models this: the artifact's `categories` map is source-derived and may contain keys the runtime map lacks for a given instance; the digest therefore **discloses** G4 rather than pretending the two maps are identical (§8.1).

#### 3.6.2 The two ownership guards (modelled, not approximated)

| Guard | Runtime site | Digest model |
|---|---|---|
| **`mcp_rpc_*` is withheld from an agent that allows only `"mcp"`** | `instance.py:497-525`: the `mcp` sweep takes every `mcp_<server>_<tool>` name that is **not owned by another registered category** (`owned_elsewhere = _owned_elsewhere(tool_categories, "mcp")`, `:518`); the rationale at `:500-511` — "The `mcp_rpc` category is PRIVILEGED (opt-in only), so the sweep must not be a back door around that gate" | A name that the artifact's map attributes to another category (e.g. `mcp_rpc_command` → `mcp_rpc`) is **owned elsewhere**: it is never swept into `mcp`, and for an agent whose allow list does not name `mcp_rpc` the verdict is **"no"** with reason `owned by category mcp_rpc (privileged)` |
| **`plane_sync_*` is excluded from the `plane` sweep** | `instance.py:561-579` (W-8): the `plane_` prefix sweep excludes names owned by another registered category — `plane_sync_project` is registered under `plane_sync` (`daemon/tools/plane_sync.py`) | same rule: a `plane_`-shaped name attributed to `plane_sync` is owned elsewhere and is never swept into `plane` |

`PRIVILEGED_TOOL_CATEGORIES` has **23** members at `d7c15d45f` (the artifact carries the list verbatim; it is read from the registry constant, never hard-coded in the digest).

#### 3.6.2a The reader's CATEGORY-KEY UNIVERSE, and the two shapes that are decided by rule (APPROVER-B2)

**The category-key universe is `categories ∪ runtime_only_categories`.** EVERY place the reader asks "is this name a category?" consults THAT union — never `categories` alone:

| Reader decision | v3 (WRONG) | v4 |
|---|---|---|
| Is an allow entry a CATEGORY (expand it) or a LITERAL (grant the name)? | `entry in src.categories` — so `"mcp"` and `"plane"` fell through to the LITERAL branch and the reader granted the bare category NAME as if it were a tool | `entry in (categories ∪ runtime_only_categories)` — a runtime-only key is a CATEGORY whose members are call-time state, so the name itself is never granted and its members render `"unknown"` |
| Is a deny entry a CATEGORY (subtract its members) or a LITERAL? | `entry in src.categories` | the same union |
| `category_granted(agent_id, key)` | consulted `categories` only ⇒ `category_granted(agent_id, "plane")` was FALSE for an agent that allows `"plane"` ⇒ the `plane_` sweep fell through to a confident `"no"` | consults the union ⇒ TRUE for an agent that allows `"plane"` ⇒ the `plane_` sweep renders `"unknown"` |

**The runtime-only keys are CATEGORIES, not tool names.** `"mcp"` and `"plane"` are `CATEGORY_MODULES` keys with EMPTY static attribution (§3.5); the runtime populates them at call time (the `mcp_` sweep at `instance.py:497-525`, the `plane_` prefix sweep at `:561-579`). A reader that treats the allow entry `"plane"` as a literal grant of a tool named `"plane"` reports a grant the runtime never makes AND renders every `plane_*` member `"no"` for the agents that receive it through the sweep. v4 fixes both.

**The MCP-shape predicate is the runtime's EXACT predicate (A2).** The runtime sweeps `name.startswith("mcp_") and "_" in name[4:]` (`daemon/tools/instance.py:520-523`) — **at least ONE underscore after the prefix**. v3 wrote "≥2 underscores after the prefix", which is STRICTER: a name shaped `mcp_x_y` (one underscore after the prefix) IS swept by the runtime but would not be MCP-shaped to the reader, so it would have fallen through to a confident `"no"` — a false NO, the cardinal defect. The reader MUST use the runtime's predicate verbatim. (The runtime's own COMMENT at `:519` says "contain at least 2 underscores", which counts the prefix's own underscore; the CODE at `:522` is the authority.)

**The inherit-all branch is EXPLICIT (APPROVER-B2a).** `inherits_all(agent_id)` is TRUE when the agent's EFFECTIVE allow (after innate-skill expansion, §3.6.1) is EMPTY — `watcher` is the only such agent in the corpus (`agents/watcher/meta.json:21-23`, `"tools": {"allow": []}`). The runtime then builds the default universe — every NON-PRIVILEGED category's tools (`instance.py:581-591`) — and subtracts deny (`:601-609`). The reader's rules for such an agent are therefore exactly:

- a statically-owned **NON-PRIVILEGED** tool → **`"yes"`**;
- a **PRIVILEGED-category** tool → **`"no"`** (privileged categories NEVER join the default universe, `instance.py:589-592`);
- an **MCP-shaped** or **prefix-shaped** unowned name → **`"unknown"`** (the swept sets are call-time state);
- any other unowned name → **`"unknown"`** (the instance's runtime category map may still contain it — G4, §8.1).

**What the inherit-all branch does NOT change.** The disclosed under-report of §3.6.4 / §8.3 stands: an inherit-all agent receives MCP tools at filter level, and the digest still renders those rows `"unknown"` — the static map cannot distinguish "an MCP tool this agent would receive" from "a stale name". v4 narrows the false-NO, it does not widen any claim.

#### 3.6.3 Verdict function (three-valued, never a false NO)

```python
def grant_verdict(tool: str, agent_id: str, granted: set[str], src: AllowSource) -> tuple[str, str]:
    """-> (verdict, reason); verdict in {"yes", "no", "unknown"}. NEVER a false NO.

    `granted` is the EFFECTIVE grant set for THIS agent — the flat map the
    reader publishes as `allowlists()[agent_id]` (allow expansion MINUS deny,
    deny winning, instance.py:601-609). It is the VERDICT AUTHORITY for "yes":
    the runtime grants exactly those names, so membership IS a grant.

    The REASON is a SEPARATE, DEGRADABLE input: it is read from the detailed
    source (`grant_reason`) and may be unavailable — the ad-hoc `allowlists`
    monkeypatch legs of the determinism suite replace the flat map with a
    synthetic one, so no attribution exists. Losing the reason NEVER changes
    the verdict (§3.6.6, APPROVER-B3).
    """
    # 0. The deny gate precedes EVERY grant branch: deny wins
    #    (instance.py:601-609). A tool the agent's deny list removes is "no"
    #    EVEN WHEN its category is allowed or its name is literally allowed.
    hit = src.deny_entry_for(agent_id, tool)   # the deny ENTRY naming `tool` (literal or category), else None
    if hit is not None and tool not in granted:
        owner = src.owned_category.get(tool)
        via = f"category {owner} allowed, " if owner is not None and owner in src.granted_categories.get(agent_id, ()) else ""
        return "no", f"denied: {via}deny wins (deny entry `{hit}`)"
    # 1. The flat effective-grant map is the "yes" authority. A name in the
    #    agent's effective grant set IS granted by the runtime.
    if tool in granted:
        return "yes", src.grant_reason(agent_id, tool)   # "literal" | "category:<k>" | "inherit-all ..." | "no reason available"
    # 2. A degraded artifact is not decidable.
    if src.state != "ok":
        return "unknown", f"artifact {src.state} (allow-list dimension disabled)"
    # 3. Agent coverage. A POPULATED source that omits the agent is making a
    #    claim about which agents it covers -> the omitted agent is undecidable
    #    (a custom/DB-seeded agent). An EMPTY agents map makes NO per-agent
    #    claim at all (test-only envelopes) -> the decision falls through to
    #    the vocabulary rules below, so the verdict stays DETERMINATE.
    if src.agents and agent_id not in src.agents:
        return "unknown", "agent not present in the artifact (custom/DB-seeded agent)"
    # 4. INHERIT-ALL (APPROVER-B2a). `watcher` is the only empty-allow agent
    #    (agents/watcher/meta.json:21-23); the runtime grants it the default
    #    universe - every NON-PRIVILEGED category's tools (instance.py:581-591).
    #    Without this branch the owner branch below returned "no" for the WHOLE
    #    watcher column (the exact mirror of the D3 defect).
    if src.inherits_all(agent_id):
        owner = src.owned_category.get(tool)
        if owner is not None:
            if owner in src.privileged_categories:
                return "no", f"privileged category {owner} - never part of the default universe"
            return "yes", "inherit-all (empty allow; non-privileged default universe)"
        if src.is_mcp_shaped(tool):
            return "unknown", "MCP: membership is call-time state (DB/config server list; the category stub has no static members)"
        if src.is_dynamic_prefix_shaped(tool):
            return "unknown", "plane_ prefix sweep: the swept set is call-time state"
        return "unknown", "name not attributed to any static category - the instance's runtime category map may still contain it (G4)"
    # 5. A LITERAL entry is an exact grant of that one name (the runtime adds
    #    the name itself: instance.py:599). Decidable even for a dynamic name.
    if tool in src.literal_grants.get(agent_id, ()):
        return "yes", "literal"
    # 6. Statically owned name: the category map decides, EXACTLY.
    owner = src.owned_category.get(tool)
    if owner is not None:
        if owner in src.granted_categories.get(agent_id, ()):
            return "yes", f"category:{owner}"
        return "no", f"owned by category {owner} - not swept into the allowed category (ownership guard)"
    # 7. Unowned MCP-shaped name: the server list is call-time state (R-MCP).
    #    The predicate is the RUNTIME'S EXACT predicate (A2):
    #    `name.startswith("mcp_") and "_" in name[4:]` (instance.py:520-523) -
    #    AT LEAST ONE underscore after the prefix. v3's ">=2 underscores after
    #    the prefix" was STRICTER than the runtime, so a runtime-swept name
    #    (e.g. `mcp_x_y`) fell through to a confident "no" - a false NO.
    if src.is_mcp_shaped(tool):
        return "unknown", "MCP: membership is call-time state (DB/config server list; the category stub has no static members)"
    # 8. Unowned plane_-prefixed name: the swept set is call-time state.
    if src.is_dynamic_prefix_shaped(tool):
        if src.category_granted(agent_id, "plane") or src.inherits_all(agent_id):
            return "unknown", "plane_ prefix sweep: the swept set is call-time state"
        return "no", "not granted (allow expansion minus deny; deny wins)"
    # 9. Not in the known-name vocabulary (a factory-built name the vocabulary
    #    misses, a custom-prefix server tool, or a removed tool): NOT decidable.
    if tool not in src.known_tool_names:
        return "unknown", "name not in the known vocabulary - not statically decidable"
    # 10. Known name, not granted.
    return "no", "not granted (allow expansion minus deny; deny wins)"
```

`deny_entry_for(agent_id, tool)` is the deny-side twin of the category expansion at §3.6.1: it returns the agent's deny ENTRY that names `tool` — the entry itself when the entry is NOT a category key (a literal deny), else the category key whose member list contains `tool` — and `None` when the agent does not deny the tool. It is a pure lookup over the artifact's per-agent `deny` list plus the category map; it never consults the runtime.

**Why the gate exists (v3 coherence edit — flagged for override).** v2's `grant_verdict` accepted a `granted` parameter and never read it, and never consulted the deny list at all. Two consequences, both of which contradict this document's own test rows: the PRIMARY probe (`explorer` + `rag_query` — the category `rag` is allowed and `rag_query` is denied) would have rendered `"yes"` / `category:rag` instead of `"no"`, and an unresolved DENY entry (a name in no vocabulary constant, e.g. `maintenancer: git_commit`) would have fallen through to `"unknown"` instead of `"no"`. The gate is the minimal fix that makes test 39 and test 54(b) achievable exactly as specified; it changes no verdict that v2 got right (a name nobody denies keeps its previous branch).

**v4's amendment to that gate (APPROVER-B2).** The v3 gate read the deny list but derived EVERY positive verdict from `literal_grants` / `granted_categories` / `owned_category` — and `granted_categories` is EMPTY for an inherit-all agent (its grants are attributed to no category), so the owner branch returned `"no"` for the whole `watcher` column. v4 therefore (a) makes the **FLAT EFFECTIVE-GRANT MAP the "yes" authority** — membership in `allowlists()[agent_id]` IS a runtime grant, which is the whole point of the equivalence proof (test 44) — and (b) adds the **EXPLICIT `inherits_all` branch ahead of the owner branch**, so the inherit-all verdict is decided BY RULE and not by the accident of an empty attribution map. **The reason is a SEPARATE input** (`grant_reason`), so degrading it (APPROVER-B3) never changes a verdict.

**`grant_reason(agent_id, tool)` — the degradable attribution.** It returns `"literal"` (the name is an allow entry of its own), `"category:<key>"` (the name is a member of a granted category), `"inherit-all (empty allow; non-privileged default universe)"`, or the exact string **`"no reason available"`** when the detailed source cannot attribute the grant — which is the case for the ad-hoc `allowlists` monkeypatch legs, whose flat map is synthetic. It NEVER changes the verdict.

The artifact carries `owned_category` (name → the static category that owns it), `known_tool_names` (the 278-name vocabulary), `literal_grants`, `deny_entry_for`, `grant_reason` and `granted_categories` per agent, so the three name classes are decided in ONE place, in this order:

- **statically owned** — the name is attributed to a category in the artifact's map. This includes `mcp_rpc_command` → `mcp_rpc`, which is exactly why the `mcp_rpc_*` withholding is **decidable** rather than unknown (§3.6.2).
- **runtime-shaped, unowned** — MCP-shaped (`mcp_<server>_<tool>`, the RUNTIME'S EXACT predicate: `name.startswith("mcp_") and "_" in name[4:]` — **at least ONE underscore after the prefix**, `instance.py:520-523`; A2) or matching a `DYNAMIC_TOOL_PREFIXES` value (`plane_`), and owned by no static category → membership is call-time state → **unknown** (R-MCP).
- **unresolved** — in no KNOWN-NAME constant at all (a factory-built name the vocabulary misses, a custom-prefix server tool, or a removed tool) → **unknown**, never "no" (measured live examples: `sed`, `task_schedule_create` — §4.6). The membership test is `tool not in src.known_tool_names`, i.e. the tenth key (A1), NOT a projection of `categories`.

The ordering matters and is deliberate: a **literal** grant is exact and renders "yes" even for a dynamic name (no agent literally grants an `mcp_*` name today, so this is latent); a **statically owned** name is decided by the category map; only an UNOWNED dynamic name is "unknown". This is the reading under which R-MCP's categorical rule and R-OVERCLAIM's ownership-guard requirement are simultaneously satisfiable — see Open Question 3.

**R-MCP compliance:** runtime-class rows render `unknown` — never `yes`, never `no`. 26 of 55 agents allow `"mcp"`, so a category-expanding digest would have marked every MCP row "yes", including the withheld `mcp_rpc_*`.

#### 3.6.4 What "unknown" costs (the honest trade, disclosed)

One consequence is worth stating plainly because it is a *known* under-report: an **inherit-all** agent (`watcher` — the only agent with an empty allow list) is in fact granted every non-privileged tool at filter level, MCP included. v3 still renders MCP rows `unknown` for every agent, because the static map cannot distinguish "an MCP tool this agent would receive" from "a stale name" without the runtime's server list. The under-report is disclosed in the digest's notes footer (§3.7), not hidden.

#### 3.6.5 The no-silent-drop rule, defined and populated (C6)

**Definition.** An allow/deny entry is **"unclassified"** iff it is: (a) not a key of `CATEGORY_MODULES` (65 at `d7c15d45f`); (b) not a member of `KNOWN_TOOL_NAMES ∪ DYNAMIC_TOOL_NAMES` (271 ∪ 121 → 278 names); and (c) not prefixed by any `DYNAMIC_TOOL_PREFIXES` value (`{"plane_"}`).

**Measured population (both bases, at `d7c15d45f`):**

| Dimension | Pairs | Distinct | category | tool | prefix | **unclassified** |
|---|---|---|---|---|---|---|
| ALLOW — committed artifact | 792 | 145 | 694 | 70 | 28 | **0** |
| ALLOW — live `agents/*/meta.json` | 794 | 147 | 692 | 74 | 28 | **0** |
| DENY — live `agents/*/meta.json` | 93 | 34 | 33 | 59 | 0 | **1** |

**The one unclassified entry** is `git_commit` in `maintenancer`'s deny list (`agents/maintenancer/meta.json:31` → `"deny": ["git_commit", "edit_file", "write_file"]`). It is in no vocabulary constant at `d7c15d45f` (`git grep` finds it only in that meta.json and in ROLLOUT.md) and the runtime validator would warn on it (`daemon/registry.py:1443-1447`). It looks like a dead deny (renamed or removed tool).

**Rule.** Every unclassified entry is (1) written into the artifact's `unresolved` block, (2) rendered in the digest's notes footer (`unresolved allow entries: 0 · unresolved deny entries: 1 — maintenancer: git_commit (in no vocabulary constant; the registry validator warns on it)`), and (3) asserted by a test against the measured population. Nothing is dropped silently. Because the fix ADDS a deny dimension, this is the live population that the rule protects.

#### 3.6.6 Rollup and render changes (AST-pin safe)

- `rollup()` keeps calling `tool_weights()` and `allowlists()` (the pinned literals), adds the per-agent verdict + reason, and gains **no** `os.environ` / `getenv` / `agents/` substring (the pinned forbiddens). The verdict lookup is a pure function of the already-loaded artifact.
- `allowlists()` keeps its contract `dict[str, list[str]]` — but the values are now the **EFFECTIVE grants** (expanded categories + innate grants, minus deny), not the raw entries. The existing monkeypatch legs in the determinism suite (`monkeypatch.setattr(tud, "allowlists", lambda: {"ari": ["tie_a"]})`) keep working unchanged. **Growth (measured by a read-only AST re-derivation at `d7c15d45f`):** the zero/near-zero section is driven by `al.items()` (`tool_usage_digest.py:214-234`), so its input population grows from the RAW entries (147 distinct names / 794 agent×entry pairs) to the EFFECTIVE grants (306 distinct names / 3 009 agent×tool pairs — ≈2.1× the names, ≈3.8× the pairs; the non-privileged universe is 201 names). 62 of the 147 raw entries are CATEGORY KEYS (e.g. `filesystem`) that today render in that section as if they were tool names — the effective-grants change removes that false-positive class. **Pin (assertions added to the existing determinism node `test_zero_near_zero_lists_allow_listed_tools_at_or_below_the_threshold` (`:497`) — NODE COUNT UNCHANGED):** (i) every name in the section is in some agent's EFFECTIVE grant set and NO category key appears as a tool name; (ii) each entry's `allowed_agents` equals exactly the agents whose effective grant set contains it; (iii) the section's size equals the number of distinct effective names at or below `NEAR_ZERO_CALLS_7D`, computed from the same artifact — never a hand-copied constant (the 306 / 3 009 figures are the `d7c15d45f` reference; the AUTHORITY is the generator's own output).
- `allowlist_source()` is the typed envelope (state, schema_version, agents, categories, runtime-only, privileged, dynamic prefixes, innate map, **known_tool_names**, unresolved, provenance) plus the reader-derived `category_keys` (`categories ∪ runtime_only_categories`), `owned_category`, `known_tool_names` and `granted_categories` (per agent — the EXPLICIT set of granted category keys, §3.6.1). The rollup reads it for reasons; the `hermetic_artifacts` fixture additionally neutralizes it so the math-only legs stay hermetic.
- `render_markdown()` keeps the 6-column table (`| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |` — pinned by the suite's `_section_tools` / `_table_row` helpers) and keeps the `(allow-listed)` marker substring (pinned). It ADDS the attribution and the denial marker **in the forms fixed by APPROVER-B3** (the exact table is below): `- ari: 24h=1 7d=3 (allow-listed) — category:filesystem` / `- explorer: 24h=0 7d=38 (not allow-listed — denied: category rag allowed, deny wins (deny entry `rag_query`))`. **The reason rides AFTER the pinned marker, never inside it** — see the marker contract below.
- The table cell becomes a three-valued summary over the calling agents: `yes` / `no` / `unknown` when uniform, otherwise `mixed(yes=N no=M unknown=K)`. The rollup's JSON keeps the legacy boolean `allow_listed_any` for compatibility.
- **The per-agent `allow_listed` field STAYS A BOOLEAN (APPROVER-B3).** It is derived as `verdict == "yes"` — `True` exactly for a granted agent, `False` for every other case ("no" AND "unknown"). `tests/unit/services/test_tool_usage_digest_determinism.py:541` asserts `tool["agents"][0]["allow_listed"] is True`; **that assertion is NOT edited and passes unchanged.** The verdict STRING does **not** live in `allow_listed`.
- **A NEW, separately-named field carries the verdict + reason.** Its exact name and shape:

  ```json
  "allow_listed_detail": {"verdict": "yes" | "no" | "unknown", "reason": "<string>"}
  ```

  It is emitted on EVERY per-agent entry, alongside `allow_listed`. `reason` is the attribution string from §3.6.3 (`"literal"`, `"category:filesystem"`, `"inherit-all (empty allow; non-privileged default universe)"`, `"denied: category rag allowed, deny wins (deny entry `rag_query`)"`, `"MCP: membership is call-time state …"`). When the reason cannot be resolved — the ad-hoc `allowlists` monkeypatch legs replace the flat map with a synthetic one, so no attribution exists — the reason is the exact string **`"no reason available"`** and **the VERDICT IS UNCHANGED** (the verdict authority is the flat grant map, §3.6.3 step 1; the reason is a separate, degradable input).
- **The renderer marker contract (APPROVER-B3) — the pinned substring survives every form:**

  | Case | Marker emitted | Contains the pinned `(allow-listed)`? |
  |---|---|---|
  | verdict `yes`, reason available | ` (allow-listed) — <reason>` | **YES** |
  | verdict `yes`, reason `"no reason available"` | ` (allow-listed)` (the bare, existing form) | **YES** |
  | verdict `no`, reason available | ` (not allow-listed — <reason>)` | n/a |
  | verdict `no`, no reason | ` (not allow-listed)` (the bare form) | n/a |
  | verdict `unknown`, reason available | ` (allow-list unknown — <reason>)` | n/a |
  | verdict `unknown`, no reason | ` (allow-list unknown)` | n/a |

  **The reasonful variant MUST NOT swallow the closing paren.** The leader's illustrative form `"(allow-listed via filesystem)"` would REMOVE the substring `(allow-listed)` and **break `test_tool_usage_digest_determinism.py:544`** — that assertion is **NOT edited** and passes unchanged under the forms above. The binding constraint is the pinned substring; the reason rides AFTER it, separated by ` — `. (The bare `(not allow-listed)` is a NEW marker where v3 rendered no marker at all; no pinned assertion depends on its absence — `grep 'allow-listed|allow_listed'` across `tests/` and `test/` at `d7c15d45f` returns the determinism suite's two hits and nothing else — and the FE does not parse the per-agent lines (no `allow-listed` / `allow_listed` reference anywhere under `frontend/src` or `frontend/e2e`).)
- **The two ad-hoc `allowlists` monkeypatch legs (`:497`, `:530`) keep passing UNCHANGED.** In the `:530` leg the flat map is `{"ari": ["tie_a"]}` and the seeded agent is `ari`; the verdict is `"yes"` (the flat map IS the grant authority), `allow_listed` is `True`, and the reason is `"no reason available"` → the renderer emits the BARE ` (allow-listed)` → **`:541` and `:544` both hold**. The deny gate cannot fire in either leg: `ari`'s deny list is `["edit_file", "write_file", "watch_job", "watch_jobs"]` (`agents/ari/meta.json:54`) and names none of the fixture tools (`tie_a`, `tool_usage_digest`, `barely`, `well_used`). In the `:497` leg the zero/near-zero section is built from the flat map exactly as today and `allowed_agents` is unchanged.

### 3.7 Loudness mechanism

| Trigger | Behaviour |
|---|---|
| Artifact absent / unreadable / malformed JSON | state `absent` / `unreadable` / `malformed` → **banner** in the markdown (`> ⚠️ ALLOW-LIST DIMENSION DISABLED — …`), every allow-list cell renders `unknown`, the zero/near-zero section is disabled, `logger.warning` |
| `schema_version` missing or ≠ 2 (including the v1 schema) | state `rejected` → **banner naming the found/required version** (`ARTIFACT SCHEMA REJECTED (found: none, required: 2)`), every cell `unknown`, zero/near-zero disabled, `logger.error` |
| `agents` present but a value lacks the `deny` key | **REJECTED** (not treated as an empty deny) — this is the exact hole the version gate closes |
| A name in the runtime class (MCP / plane) | cell `unknown` with the reason string; no banner (this is expected, not a failure) |
| Unclassified entries present | notes footer lists them (count + entries); never dropped |
| Digest root unverifiable | `DigestRootUnresolved` is raised by `digest_root()`; `write_digest()` surfaces the typed error string; **no file is written** and **no guessed path is used** |
| Payload/tree mismatch (frozen mode) | if `releases/<v>/manifest.json` is readable and its `source_commit` ≠ the artifact's `provenance.tree_commit` → banner `ARTIFACT/TREE MISMATCH` and the allow-list dimension is marked degraded (not silently trusted) |

The digest never raises on *content* (the daily digest must still be produced) — but it is never silent.

**The precedent this follows (verified at `d7c15d45f`):** the house's loud-degradation pattern is "catch the failure, log it loudly at the point of failure, and still return a typed, non-raising result". The logger calls are at `daemon/tools/guidelines.py:394` (`logger.exception("guidelines_list failed")`) and `:445` (`logger.exception("guideline_read failed")`), each paired with a typed refusal returned to the caller. (`daemon/tools/guidelines.py:181` is `_payload_refusal` — the typed *payload-unavailable* envelope — and is **not** the loudness precedent; this spec does not cite it as such.)


### 3.8 Generator change (the deterministic producer)

`scripts/gen_tool_usage_allowlists.py` becomes a schema-2 producer:

1. Reads every `agents/*/meta.json`; takes `tools.allow`, `tools.deny`, and `innate_skills` (the current generator reads ONLY `tools["allow"]` — it has **zero** occurrences of "deny"), preserving its legacy handling of a `tools` list form.
2. Derives the taxonomy through the extended house walker (`scan_category_module_sources_by_category`), honouring `@register_tool_category` overrides; writes `categories` (registry keys), `runtime_only_categories`, `privileged_categories`, `dynamic_prefixes`.
3. Extracts `INNATE_SKILL_TOOL_CATEGORIES` from `daemon/tools/instance.py:195-201` by AST literal extraction; a non-literal value makes the generator **fail closed** rather than emit a guessed map.
4. Computes the `unresolved` ledger for allow and deny against the same vocabulary.
5. Emits the provenance block: `tree_commit` + per-input `last_commit` and `content_sha256`.
6. Stays deterministic: no timestamps, `json.dumps(..., indent=2, sort_keys=True)`, agents and tool lists sorted.

**Two generator modes:**
- `--out <path>` (default behaviour) — write the artifact.
- `--verify` — regenerate in memory and compare against an existing artifact; exit non-zero on drift. Used by the CI freshness test and by the stage-time drift report.

**The `--verify` contract (APPROVER-B1) — a SEPARATION, not a pin.** `--verify` NEVER compares the file as one blob. It compares the artifact in TWO independent parts.

**Part 1 — the SEMANTIC PAYLOAD (byte equality).** The seven payload keys are compared as canonical JSON, byte for byte:

```python
PAYLOAD_KEYS = ("agents", "categories", "runtime_only_categories",
                 "privileged_categories", "dynamic_prefixes",
                 "innate_skill_categories", "known_tool_names")
```

Any drift in the SEMANTICS fails the gate: a changed `meta.json`, a changed taxonomy, a changed privileged set, a changed innate map, or a changed known-name vocabulary. The remaining two non-provenance keys have their own rules — `schema_version` must **EQUAL 2** (a scalar gate, not a byte compare) and `unresolved` must **EQUAL the regenerated ledger as a SET** (entry order is irrelevant).

**Part 2 — the PROVENANCE (separate rules, never a blob compare).**

| Provenance field | Rule |
|---|---|
| `provenance.inputs.agents.content_sha256` | **ALWAYS** compared. A change ⇒ the artifact's agents input changed ⇒ the gate is RED. |
| `provenance.inputs.taxonomy.content_sha256` | **ALWAYS** compared. A change ⇒ the artifact's taxonomy input changed ⇒ the gate is RED. |
| `provenance.inputs.*.last_commit` | Compared **ONLY WHERE the corresponding content hash MOVED**. A rewritten history (a rebase) moves `last_commit` while the content hash stands ⇒ the gate stays GREEN, because the CONTENT did not drift. |
| `provenance.tree_commit` | **INFORMATIONAL. EXCLUDED from every equality comparison.** It names the tree the generator ran in and moves on EVERY commit — including commits that touch neither `agents/` nor `daemon/tools/`. |
| `provenance.generator`, `provenance.generator_version` | Compared (a version bump is a semantic change to the producer). |

**Why a whole-file byte compare is IMPOSSIBLE (the defect this replaces).** The committed artifact's recorded stamp can NEVER equal the HEAD of the commit that carries it — committing the artifact CHANGES the commit. A whole-file byte compare against a regenerated artifact is therefore RED from the lane's first commit onward, and (because `tree_commit` moves on every commit) it would break EVERY unrelated lane's CI. The separation removes the moving stamp from the equality surface entirely: the gate asks "did the SEMANTICS or the CONTENT of an input change?", never "is the stamp still the same commit?".

**The vacuity guard (normative).** `--verify` MUST be able to go RED on a SEMANTIC drift alone. The F6 test therefore mutates a committed `agents/` payload (a deliberately altered agent allow entry) in a tmp copy and asserts `--verify` exits **non-zero** against it (APPROVER-B1, §3.9.8). A gate that can only ever be GREEN is not a gate.

The generator loads `daemon/tools/_tool_registry.py` by **file path** (importlib, module spec) rather than importing the `daemon.tools` package — the package `__init__` imports the whole tool set (`daemon/tools/__init__.py`), which is unnecessary for a static walk and would drag daemon side effects (including the KMS log-redaction install in `daemon/__init__.py`) into a build-time script.

### 3.9 The complete test list

Every test below names its file and its assertion. The census impact of each addition is stated (§3.9.5).

#### 3.9.1 `tests/unit/services/test_tool_usage_digest_root.py` — REWRITE (5 → 9 nodes)

| # | Test | Assertion |
|---|---|---|
| 1 | `test_frozen_code_root_anchors_at_the_release_dir` | frozen → `_code_root()` == the **release** dir (the binary's parent). Pins the READ anchor and corrects v1's mislabelling of it as "the install dir". |
| 2 | `test_non_frozen_code_root_is_the_checkout_root` | `_code_root()` == `Path(tud.__file__).resolve().parents[2]`. |
| 3 | **T-B** `test_digest_root_resolves_to_the_install_dir_owning_releases` | With a **real** layout `<tmp>/install/releases/v0.0.0/ensemble-prod` (created by the test) and `sys.frozen=True`, `sys.executable` pointed at it: `digest_root()` == `<tmp>/install/.agents/shared/planning/tool-usage-metrics-p1/digests` and `"releases"` is not in the root's parts. **Why v1's shape could not pass:** v1 pointed `sys.executable` at a non-existent `/tmp/fake-install/bin/ensemble`, so the house resolver returned its *unverified* convention candidate and the verification gate raised `DigestRootUnresolved` — the assertion "root resolves to /tmp/fake-install/…" was unreachable. |
| 4 | **T-B2** `test_digest_root_finds_the_install_dir_through_the_bounded_walk` | Nested layout `<tmp>/install/releases/v9/nested/ensemble-prod` → the same install dir (exercises `_install_dir_owning_releases`' 4-ancestor bound, `maintenance_seed.py:219-238`). |
| 5 | **T-B3** `test_digest_root_raises_when_no_install_dir_is_verifiable` | Layout beyond the 4-ancestor bound → `pytest.raises(DigestRootUnresolved)`; **no** directory is created and **no** file is written. |
| 6 | `test_env_override_precedence_over_frozen` | The env override wins; the resolver is **not consulted** (it is monkeypatched to raise). |
| 7 | `test_env_override_precedence_over_non_frozen` | Same in source mode. |
| 8 | `test_env_override_empty_string_falls_through` | A whitespace-only override falls through to the default resolution. |
| 9 | `test_artifact_dir_reads_the_release_payload_when_frozen` | frozen → `_artifact_dir()` == `<release>/tool-usage-artifacts` (R-PAYLOAD) and **not** the repo planning path. |

#### 3.9.2 `tests/unit/services/test_tool_usage_digest_determinism.py` — +1 node (37 → 38)

| # | Test | Assertion |
|---|---|---|
| 38 | `test_the_digest_never_consumes_the_display_name_category_function` | Source pin over `daemon/services/tool_usage_digest.py`: no `get_tool_categories`, no `CATEGORY_NAME`; plus a data pin: every key of the artifact's `categories` map is a member of `CATEGORY_MODULES` (registry keys), never a display name ("File Operations", "Nest Report", "Shell", "MCP"). **This is the R-EXPANSION test pin the ruling requires.** |

Fixture change (no node count change): `hermetic_artifacts` also neutralizes `allowlist_source` — **with a DETERMINATE "ok"-state envelope, NEVER a degraded one.** The neutralized envelope is a minimal, VALID schema-2 source: `state="ok"`, `schema_version=2`, `agents={}`, `categories={}`, `runtime_only_categories=[]`, `privileged_categories=[]`, `dynamic_prefixes=[]`, `innate_skill_categories={}`, `known_tool_names=<the full 278-name vocabulary>`, `unresolved={"allow": [], "deny": []}`, `provenance={}`.

**Why "ok"-state and not a degraded state (APPROVER-B3).** v3's instruction — "neutralizes `allowlist_source`" — was ambiguous, and the DANGEROUS reading (a state other than `"ok"`) would make EVERY verdict `"unknown"`: that is not "without changing any verdict", it IS a change, and it would have made the hermetic legs vacuous. With an "ok"-state envelope the reader's decision path is exercised normally: a tool the neutralized envelope cannot attribute falls through to the vocabulary rule — a name in the 278-name vocabulary is DECIDED (`"no"`), a name outside it is `"unknown"` (genuinely undecidable) — and the agent-coverage rule does not fire, because an EMPTY `agents` map makes NO per-agent claim (§3.6.3 step 3). The verdict is therefore **DETERMINATE**, and the boolean `allow_listed` is unchanged (False, because the hermetic flat map is empty).

**The two ad-hoc `allowlists` monkeypatch legs (`:497`, `:530`) keep passing UNCHANGED.** The flat `allowlists()` contract is preserved; the flat map is the VERDICT authority (§3.6.3 step 1) and the attribution is a SEPARATE, degradable input (`grant_reason`), so losing the reason changes no verdict: the reason degrades to the exact string `"no reason available"` and the renderer emits the BARE ` (allow-listed)` marker (§3.6.6) — which is what keeps `test_tool_usage_digest_determinism.py:544` passing unchanged.

#### 3.9.3 `tests/unit/services/test_tool_usage_digest_allow_semantics.py` — NEW (12 nodes: the original 9 + the 3 APPROVER-B2 verdict assertions)

| # | Test | Assertion |
|---|---|---|
| 39 | `test_the_primary_probe_explorer_rag_query_is_denied_and_names_the_deny_wins_path` | Against the **real** artifact (no monkeypatch of the artifact, the taxonomy, or the readers): `explorer` + `rag_query` → verdict **"no"** AND the reason names BOTH the category grant and the deny override. `explorer` allows the category `rag` and denies `rag_insert_text`, `rag_insert_texts`, `rag_query`, `experience`, `explore` (`agents/explorer/meta.json`). **Verdict + attribution are asserted together** because the verdict alone cannot discriminate: an exact-match implementation also yields "no" (by the wrong route), so the attribution assertion is what makes the probe discriminating. |
| 40 | `test_the_secondary_probe_devops_edit_file_is_granted_via_the_filesystem_category` | `devops` + `edit_file` → verdict **"yes"**, reason `category:filesystem` (`edit_file` is a tool in the category `filesystem`, allowed by `devops` and `explorer`, and named by neither — the namespace defect). |
| 41 | `test_the_board_probe_bash_devops_is_vacuous` | Documents, as an executable assertion, that "bash" is BOTH a category key and a tool name: the exact-match mutant returns the SAME verdict as the correct implementation for `devops` + `bash`, i.e. the board's stored acceptance probe **cannot** discriminate and is not used as a probe. |
| 42 | `test_the_mutant_category_only_expansion_fails_the_primary_probe` | A mutant implementation (allow expansion WITHOUT deny subtraction) returns "yes" for `explorer` + `rag_query` → differs from the real verdict ⇒ the real verdict is discriminating. The mutant is installed per the **MUTATION-WIRING RULE** below. |
| 43 | `test_the_mutant_exact_match_expansion_fails_the_probes` | A mutant implementation (literal matching, no category expansion) returns "no" for `devops` + `edit_file` and no attribution for `explorer` + `rag_query` → differs from the real verdicts. The mutant is installed per the **MUTATION-WIRING RULE** below. |
| 44 | `test_effective_grants_are_equivalent_to_resolve_tool_filter_for_every_agent` | The equivalence proof: for all 55 agents and a fixed fixture universe (the artifact's category map + a fixed `all_tool_names` set including `mcp_*`, `mcp_rpc_command`, `plane_sync_project`, `plane_list_work_items`), the digest's effective grant set equals `resolve_tool_filter(allow=effective_allow, deny=deny, tool_categories=fixture_map, all_tool_names=fixture_names)` (`daemon/tools/instance.py:455`), restricted to the statically-decidable names (runtime-class names are excluded from the comparison by construction and are asserted `unknown` separately). **The runtime's `allow` argument is produced by the REAL `expand_allow_for_innate_skills(agent.allow, agent.innate)` (`daemon/tools/instance.py:204`, the runtime's own call site is `:8180`) — NOT by the digest's own `effective_allow` — and the test asserts the two are EQUAL for all 55 agents, so the innate half is NOT compared against itself (v2's oracle was circular on that half).** The `None` (inherit-all) return is handled explicitly: `resolve_tool_filter` returns `None` only when allow AND deny are both empty (`instance.py:483-488`), and the expected set is then every non-privileged name of the fixture universe. **Three synthetic agents are added, because the corpus cannot supply them** (the only empty-allow agent is `watcher`, whose deny is empty, so the live corpus NEVER exercises the empty-allow + non-empty-deny branch): (a) `allow=[] deny=[]` → the runtime returns `None` → the digest's set is the full non-privileged universe (201 names on the live fixture); (b) `allow=[] deny=['bash']` → the runtime takes the EMPTY-ALLOW branch at `instance.py:581` REGARDLESS of the deny (the runtime names that exact case at `:8117-8119`), builds the default universe at `:587-591`, then subtracts deny at `:601-609` → the digest's set is that universe MINUS the denied names, asserted NON-EMPTY (v2's algorithm returned the EMPTY set here — the false-NO class §3.6.3 promises never to produce); (c) `allow=[] innate=['todo'] deny=[]` → the runtime's innate expansion runs FIRST (`:8180` → `:204-249`), turning the empty allow into `['todo']`, so the runtime expands ONLY the `todo` category (`:592-599`) and does NOT take the default-universe branch → the digest must equal the `todo` category's tools and must NOT be inherit-all. This third case pins the ORDER (innate expansion BEFORE the emptiness test) in both implementations. |
| 45 | `test_the_ownership_guards_withhold_mcp_rpc_from_an_mcp_only_agent` | An agent whose allow list contains `"mcp"` but not `"mcp_rpc"` does NOT receive `mcp_rpc_command` → verdict "no", reason names the owning category `mcp_rpc` (privileged). |
| 46 | `test_the_plane_ownership_guard_excludes_plane_sync_from_the_plane_sweep` | `plane_sync_project` is owned by `plane_sync` and is never swept into the `plane` category. |
| 47 | `test_the_innate_skill_expansion_is_modelled` | An agent whose `innate_skills` grant a category not present in its `allow` list receives that category's tools (e.g. `worker` → `todo`), and an agent with an empty allow list inherits every non-privileged category (and NOT the privileged ones). |
| 58 | `test_an_inherit_all_agent_is_granted_the_statically_owned_non_privileged_tools` | **APPROVER-B2a — the VERDICT, not the grant set.** The corpus's only inherit-all agent is `watcher` (`agents/watcher/meta.json:21-23`, `"tools": {"allow": []}`). The test asserts that for `watcher` and a statically-owned **NON-PRIVILEGED** tool (e.g. `edit_file`, owned by the category `filesystem`), `grant_verdict` returns the VERDICT **`"yes"`**, with the reason naming the inherit-all default universe. (v3's algorithm returned `"no"` here: the inherit-all grants are attributed to NO category, so `granted_categories` was EMPTY and the owner branch said "no" — the exact mirror of the D3 defect, and NO test pinned it: test 47 pinned the grant SET, not the verdict.) |
| 59 | `test_an_inherit_all_agent_is_not_granted_the_privileged_category_tools` | **APPROVER-B2a.** For the SAME inherit-all agent and a tool owned by a **PRIVILEGED** category (e.g. `memoir_recall`, owned by the privileged `memoir` category), the VERDICT is **`"no"`** — privileged categories NEVER join the default universe (`daemon/tools/instance.py:589-592`). The assertion is on the VERDICT, not on the grant set. |
| 60 | `test_an_agent_allowing_plane_renders_an_unowned_plane_name_unknown` | **APPROVER-B2b.** An agent whose allow list names the category **`"plane"`** (a `runtime_only_categories` member, NOT a `categories` key) and a `plane_*` name it does NOT literally allow ⇒ the VERDICT is **`"unknown"`**, not `"no"` — the `plane_` sweep's member set is call-time state (§4.6). This is the assertion v3 could not make: `category_granted(agent, "plane")` consulted only `categories`, so it was FALSE and the sweep fell through to a confident `"no"`. The reader's category-key universe is `categories ∪ runtime_only_categories` (§3.6.2a), and the test pins the VERDICT. |

**MUTATION-WIRING RULE (normative — applies to tests 42 and 43).** A mutant **REPLACES THE READER'S GRANT FUNCTION AT THE SAME ENTRY POINT** the verdict path consults — `effective_agent_tools` (the function the reader calls to compute an agent's effective grants, §3.6.1), installed by `monkeypatch.setattr(tud, "effective_agent_tools", <mutant>)` from a module-level fixture — and NEVER a private re-implementation inside the test (a mutant that lives only inside the test proves nothing about the reader). **The mutant MUST keep the 3-tuple signature** `(granted, reasons, granted_categories)` of §3.6.1 — a mutant that returns 2 values would fail on the signature, not on the semantics, and would prove nothing. Each mutant leg then **RE-RUNS THE ASSERTION BODY of the real probe(s) through the shared assertion helper** — test 39 for the category-only mutant, tests 39 **and** 40 for the exact-match mutant — and asserts that body FAILS. **The pass condition of tests 42 and 43 is that tests 39 and 40 THEMSELVES go red under the mutant**: a mutant under which the real probes still pass is a vacuous mutant and must fail this lane's gate.

#### 3.9.4 `tests/unit/services/test_tool_usage_digest_artifact_schema.py` — NEW (8 nodes)

| # | Test | Assertion |
|---|---|---|
| 48 | `test_an_unversioned_artifact_is_rejected_loudly` | A schema-1 artifact (the current committed shape) → state `rejected`, a banner naming the required version, every cell `unknown`, zero/near-zero disabled, and an `ERROR` log. |
| 49 | `test_a_schema_2_artifact_is_accepted` | The committed artifact loads with `schema_version == 2` and state `ok`. |
| 50 | `test_a_missing_deny_list_is_not_read_as_an_empty_deny_list` | A schema-2 artifact whose agent object lacks `deny` → **rejected**, not silently treated as an empty deny (the precise hole the version gate closes). |
| 51 | `test_mcp_rows_render_unknown_never_yes_or_no` | An `mcp_<server>_<tool>` row for an agent that allows `"mcp"` → verdict "unknown" (never "yes", never "no"). |
| 52 | `test_plane_runtime_rows_render_unknown_and_literal_grants_render_yes` | A `plane_*` name that is not literally allowed → "unknown"; the same name literally allowed (as `project-manager`'s 28 `plane_*` entries are) → "yes" (a literal grant is exact). |
| 53 | `test_the_unclassified_population_is_reported_not_dropped` | The artifact's `unresolved` block equals the measured population: allow **0**, deny **1** (`maintenancer: git_commit`), and the digest's notes footer names it. |
| 54 | `test_an_unresolvable_allow_entry_is_preserved_as_a_literal_and_surfaced` | **FIXTURE-driven (BL-7's real trigger).** A synthetic schema-2 artifact is written to a tmp dir and loaded through the real reader (no monkeypatch of the readers). It contains an agent whose `allow` carries an entry that is in NO vocabulary constant (e.g. `totally_unknown_tool`) and whose `deny` carries a second such entry (e.g. `git_commit`). The test asserts **three** things: (a) the entry is **preserved as a literal** — the reader treats it as a literal grant/deny of that one name and never drops or rewrites it; (b) it is **surfaced** — it appears in the artifact's `unresolved` ledger and in the digest's notes footer, and the cell for that tool renders the **per-case expectation**, never a confident "no": an unresolved **ALLOW** entry is a **literal grant** for the agent that carries it → verdict **"yes"** with reason `literal` for THAT agent, and **"unknown"** (never a confident "no") for every OTHER agent; an unresolved **DENY** entry → verdict **"no"** for the **denying** agent (a literal deny is exact, and deny wins — §3.6.3's deny gate), and `"unknown"` for the agents that do not deny it; (c) the ledger is **complete** — the fixture's unresolvable set equals the `unresolved` block exactly (no entry lost, no entry invented). The live corpus CANNOT drive this test: the measured ALLOW-side unclassified population is **0** (only DENY has one), so a corpus-driven test would be vacuous on the ALLOW side. |
| 55 | `test_a_missing_artifact_degrades_to_unknown_not_no` | The artifact is absent → every cell "unknown" (never a false "no") + the degradation banner. |

#### 3.9.5 `tests/unit/tools/test_tool_usage_digest_registration.py` — +2 nodes (21 → 23: node 56 the D4 pin + node 57 the F6 freshness test, specified in §3.9.8)

| # | Test | Assertion |
|---|---|---|
| 56 | `test_the_written_digest_lands_outside_every_hash_verified_tree` | With a **real tmp install layout** (`<tmp>/install/releases/v0.0.0/ensemble-prod`) and the **DEFAULT frozen resolution** — `sys.frozen=True` with `sys.executable` pointed at that binary, and **NO `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` override** (the env override wins BEFORE any resolution, `tool_usage_digest.py:60-62`, so an override-path test proves NOTHING about the default) — the written path's ancestors contain **no** hash-verified tree root (`agents`, `frontend`, `plugins`, `guidelines`, `scripts`, `daemon_slice`), and the digest output dir is **not** a descendant of the release payload dir. The digest dir is **DERIVED** by the test from that default resolution (asserted equal to `<tmp>/install/.agents/shared/planning/tool-usage-metrics-p1/digests` — the install root that owns `releases/`, one level ABOVE the release dir), never injected. This is D4 pinning assertion **(i)** at the unit level; **assertion (i) is already covered in substance by test 3** (`test_digest_root_resolves_to_the_install_dir_owning_releases`, §3.9.1), which pins the same default resolution — test 56 adds the ancestor-scan leg on top of it, so the two must not drift apart. |

#### 3.9.6 `tests/test_tool_usage_artifacts_payload.sh` — NEW (shell; stage-level) — **RUNNER: `test/packs/tool_usage_artifacts_payload_unit_test.sh` (NEW), registered as its own row in `.agents/tester/PACKS.md`**

**Runner (normative — the test is NOT orphaned).** A `.sh` file has no pytest home, so this test is run by a dedicated thin wrapper pack, `test/packs/tool_usage_artifacts_payload_unit_test.sh`, which invokes `timeout 480s bash tests/test_tool_usage_artifacts_payload.sh < /dev/null 2>&1` with a dual-layer timeout — the house pattern for the sibling stage-payload shells (`test/packs/executor_family_payload_unit_test.sh` wrapping `tests/test_executor_family_payload.sh`, registered at `.agents/tester/PACKS.md:2220`; `test/packs/guidelines_read_tool_unit_test.sh` leg 2 wrapping `tests/test_guidelines_payload.sh`, registered at `.agents/tester/PACKS.md:749`). The wrapper pack AND its `.agents/tester/PACKS.md` row are both in this lane's change set (§3.1): without the row the test never reaches the gate. The wrapper adds **0** nodes to the census — the pack's slice collects `.py` files with `--collect-only`, so a shell test is not, and cannot be, one of the slice's 10 `UNIT_FILES`.

Asserts: the staged payload contains `tool-usage-artifacts/{allowlists.json,tool-weights.json}` and **nothing else**; `manifest.json` carries `tool_usage_artifacts_tree_sha256` and `tool_usage_artifacts_manifest`, and the per-file map matches a fresh hash; the stage-local re-hash leg **rejects** a corrupted copy (tamper the staged file → stage exits non-zero); the staged tree passes `_no_env_in_release`; and **D4 pinning assertion (ii)**: dropping a file into the digest output dir changes **no** tree hash. **The digest output dir is DERIVED from the DEFAULT frozen resolution against the tmp install** — the staged code is invoked with `sys.frozen=True` and `sys.executable` pointed at `<tmp>/install/releases/<v>/ensemble-prod`, with `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` UNSET (the env override wins BEFORE any resolution, `tool_usage_digest.py:60-62`, so it must not be used to inject the dir) — and the test asserts the derived dir equals `<tmp>/install/.agents/shared/planning/tool-usage-metrics-p1/digests` (the install root that OWNS `releases/`, one level ABOVE the release dir) before dropping the file there.

#### 3.9.7 Census re-stamp plan (C3 / R-CENSUS)

The pack `test/packs/tool_usage_pins_unit_test.sh` hard-asserts a node census over three of this lane's test targets, and the census is a **strict equality** (`-ne` at `:285`, `:308`, `:319`) that `PACK_SELFTEST=1` deliberately does **not** bypass — **and `PACK_SELFTEST=1` ALONE does not bypass it either**: the pack drops the pinned census only when `PACK_SELFTEST=1` is combined with `PACK_UNIT_FILES` or `PACK_EXPECTED_NODES` (`:191-207`); with `PACK_SELFTEST=1` and neither override set, `SELFTEST` stays 0 and the strict `-ne` equality at `:285`/`:308`/`:319` still fires. Because this lane adds nodes to those files, the re-stamp is mandatory and must land **in the same commit** as the test additions.

| Surface | What changes |
|---|---|
| (1) the pack's `UNIT_EXPECTED` array **and** `EXPECTED_NODES` (`:139`, `:152-161`) | all 8 per-file entries re-derived by the pack's **own** `--collect-only` method (`:254-291`), **plus the two new suites as the 9th and 10th files — the slice is 10 files UNCONDITIONALLY** (not "if they join": the census is a strict equality over a DECLARED slice, so a conditional slice is a non-census) |
| (2) the `.agents/tester/PACKS.md` row for `tool_usage_pins_unit_test` (`:156`) | its per-file counts, its total, and its freeze sha (currently "2026-11-25 @ `40301a5e3`"). **The freeze sha is the BASE the census was MEASURED AT** — the commit whose tree the pack's `--collect-only` ran against — and NOT the sha of the commit that carries the re-stamp: a same-commit re-stamp CANNOT contain its own sha (the sha exists only once the commit does). The re-stamp therefore records the base sha of the tree the counts were collected from (today's row: "2026-11-25 @ `40301a5e3`"); the lane's own commit is identified by the merge/PR record, never by a self-referential sha. |
| (3) **the row's PROSE clause** (`:150`) | it currently pins the semantic being CORRECTED: "the frozen-aware `digest_root()` resolution (council C5) — `sys.frozen` anchors at `sys.executable`'s parent, the non-frozen default resolves via `__file__` parents[2] …". It must be rewritten to the new contract. The pack's own header comment (`:55-56`) carries the same stale clause and is re-stamped with it. A count re-stamp that leaves this prose in place **asserts the bug being fixed**. |
| (4) **the pack's `:7` HEADER literal** (A3) | the header comment reads `Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — the` / `       PACKS.md + pack-scripts coverage registration wave, FAMILY A2` / `       "TOOL-USAGE" (8 files, one pack script + one PACKS.md block).` — the `"(8 files, one pack script + one PACKS.md block)"` literal must become **"(10 files, one pack script + one PACKS.md block)"**. v3 named ONLY the `:325` echo literal (`CENSUS: OK (8 files, per-file sum=…)`); a re-stamp that fixes the echo and leaves the header at "8 files" leaves the pack self-inconsistent. **BOTH literals move, in the SAME commit.** |

**Predicted counts (MUST be re-derived by the pack's own `--collect-only`; these are the architect's arithmetic, not measurements):** `root` 5 → 9; `determinism` 37 → 38; `registration` 21 → 23 (node 56 the D4 pin + node 57 the F6 freshness test, §3.9.8); the two new suites add **12 + 8** nodes (the allow-semantics suite is its original 9 **plus the 3 APPROVER-B2 verdict assertions** of §3.9.3 — the artifact-schema suite stays at 8); the slice is **10 files UNCONDITIONALLY** (the 8 existing + the two new suites); the pack total moves from 200 to **227** (200 + 4 + 1 + 2 + 12 + 8). The three pre-existing census files alone would sum to 207, but the census covers the whole declared slice, so 227 is the number to stamp. **BOTH** `"8 files"` literals are re-stamped to "10 files" in the same commit — the `:325` echo literal AND the `:7` header literal (A3, surface (4)). The implementer re-derives and stamps the measured numbers.

**Count discipline (do not get this wrong):** the count MUST come from the pack's own collection method, never from grepping test defs — `test_tool_usage_digest_determinism.py` has **23** `def test_` but collects **37** nodes because a parametrized fixture (`:143-144`, `params=["sqlite","postgres"]`) multiplies 14 of them (14×2 + 9 = 37); `test_tool_usage_digest_registration.py` has 21 test functions of which only 17 match `^\s*def test_` (four are `async def`). The census MUST run inside the **lane worktree's own uv-synced `.venv`** — the pack resolves `$PROJECT_DIR/.venv/bin/python` from its own repo root (`:166-171`), so the main checkout's venv would collect the wrong branch's nodes. Exactly one pack covers these three files; `test/packs/tool_usage_family_unit_test.sh` is genuinely disjoint (4 other files) and carries no census.

#### 3.9.8 The generator-output freshness test — NEW in this lane (F6): `tests/unit/tools/test_tool_usage_digest_registration.py` — +1 node (22 → 23)

| # | Test | Assertion |
|---|---|---|
| 57 | `test_committed_bundle_equals_generator_output` | Runs `scripts/gen_tool_usage_allowlists.py --verify` against the committed `allowlists.json` **inside the lane worktree's own `.venv`** and asserts **exit 0**. The comparison is the **SEPARATED** one of §3.8: the seven SEMANTIC PAYLOAD keys are byte-equal, `schema_version` equals 2, the `unresolved` ledger equals the regenerated ledger as a SET, the provenance CONTENT hashes are equal, and `provenance.tree_commit` is **NOT** compared (it is INFORMATIONAL). It **NEVER rewrites** the committed artifact (the F11 discipline below): it compares and fails, it does not author. It **skips with a reason** where git history is unavailable (a shallow CI clone has no `git log -1` for the provenance inputs). The F6 contract is stated in full in §4.3; this node is homed in the census-covered registration suite so its **+1 folds into the §3.9.7 census arithmetic** without adding an 11th slice file. **THE SAME NODE also carries APPROVER-B1's VACUITY GUARD:** it copies the committed artifact to a tmp path, **deliberately mutates one agent's allow entry** inside the `agents` payload, runs `--verify` against the mutated copy and asserts **exit ≠ 0**. A `--verify` that compares nothing would exit 0 forever — the gate must be able to go RED on a semantic drift alone. The mutation is on the PAYLOAD (never on the provenance), which is exactly the surface the separated comparison keeps under equality. **This second assertion does NOT change the census arithmetic** — it lives inside node 57's test FUNCTION (pytest counts one node per test function), so the registration file stays at 23 nodes. |

**House precedent this FOLLOWS — and the PRECISE difference between the two disciplines (APPROVER-B1).** The repository already owns this shape: the committed vocabulary artifact **`.agents/shared/metrics/tool-categories.json`** plus **`tests/unit/tools/test_tool_category_artifact.py`**. That suite's contract (its own docstring, `tests/unit/tools/test_tool_category_artifact.py:1-30`) is: the generator writes to a **scratch** path; the **COMMITTED artifact is never rewritten by a test run** — the ambient regeneration env var alone cannot authorise a rewrite (F11: it used to make the drift check compare the file to itself); and the drift check re-runs the generator and FAILS when the on-disk artifact diverges, so a new `register_tool_category` site or a renamed category breaks CI instead of silently drifting.

**The two disciplines DIFFER, and the difference is load-bearing.** Verified at `d7c15d45f`: **`.agents/shared/metrics/tool-categories.json` carries NO commit stamp at all** — its whole content is `{"categories": [ … ]}` (a category LABEL set). A whole-file byte compare against a regenerated file is therefore a COMPLETE equality check for THAT artifact: there is no moving stamp inside it to exclude, and its entire drift surface IS its content. **THIS artifact is different: it is STAMPED** (`provenance.tree_commit` + per-input `last_commit` + content hashes), so a whole-file byte compare is structurally IMPOSSIBLE to satisfy — the committed artifact can never equal the regeneration of the tree that carries it, and the stamp moves on every unrelated commit. **v3's claim that the F6 test "inherits that discipline verbatim" was therefore INVALID**: taken verbatim, the discipline would make the gate RED from the lane's first commit onward (and would break unrelated lanes).

**What v4 INHERITS, and what it REPLACES.** INHERITED (identical to the precedent): `--verify` compares and NEVER writes; regeneration is out-of-band (a human copies the scratch file over the committed one, exactly as the precedent documents); a drift FAILS the gate rather than silently drifting. REPLACED (the necessary difference): the comparison is **SEPARATED** — the SEMANTIC PAYLOAD is byte-compared, the provenance CONTENT hashes are ALWAYS compared, `last_commit` is compared only where its content hash moved, and `tree_commit` is INFORMATIONAL and excluded (§3.8). **The separation is SAFE precisely because it is strictly STRONGER than the precedent's whole-file compare on the surface that matters:** every semantic byte and every input's CONTENT hash stays under equality; only the commit IDENTITY — which a rebase can change without changing content — is excluded. A renamed category, a new `register_tool_category` site, an edited `meta.json`, or a changed privileged set still breaks CI, exactly as the precedent does, while an unrelated commit no longer does.


---

## 4. FRESHNESS ANALYSIS

### 4.1 Measured staleness (committed artifact vs live tree, at `d7c15d45f`)

The committed artifact `.agents/shared/planning/tool-usage-metrics-p1/allowlists.json` (15 975 bytes, sha256 `36f9918c39d4f7edd6cb64efede8ea20ef3059fea29aa2c8996250e090575c31`, provenance stamp `a064fedf49a604f36cdcc3012161fe6cce9f7367` = "last commit touching `agents/`", dated 2026-11-20) was compared against the live `agents/*/meta.json` at `origin/latest`:

| | agents | allow pairs | distinct | category | tool | prefix | unclassified |
|---|---|---|---|---|---|---|---|
| **Basis A** — committed artifact | 55 | 792 | 145 | 694 | 70 | 28 | **0** |
| **Basis B** — live `agents/*/meta.json` | 55 | 794 | 147 | 692 | 74 | 28 | **0** |
| **DENY dimension** (live only — v1 had none) | — | 93 | 34 | 33 | 59 | 0 | **1** |

**Drift: 3 of 55 agents differ.**

| Agent | Artifact-only (phantom grant) | Live-only (missing grant) |
|---|---|---|
| `experiencer` | `pok` | — |
| `kb-writer` | `pok` | — |
| `maintenancer` | — | `send_message`, `spawn_instance`, `subtree_messages`, `subtree_status` |

The agent set is 55 files = 50 base agents + 5 tagged variants (`approver[v2]`, `developer[v2]`, `planner[v2]`, `reviewer[v2]`, `tidier[v2]`); only `watcher` has an empty allow list, and **no** agent has a missing/null `tools` block (so the "no tools config ⇒ everything allowed" path of `_apply_tool_filter` (`instance.py:8153-8156`) never applies to the corpus).

### 4.2 The census collision and the phantom population (C7 / C8)

The council's "8 unresolvable allow entries" was a **phantom**, manufactured by reading the vocabulary at the **stale detached HEAD `bf4bfde6e`**, where `CATEGORY_MODULES` has **64** keys — the missing one is **`room`** (present at `origin/latest` at `_tool_registry.py:1536`). The same 792 pairs therefore classify as 686 / 70 / 28 / **8** at the stale checkout and 694 / 70 / 28 / **0** at `origin/latest`. All 8 "neither" entries were the single string `room`, carried by `ari`, `leader`, `sonic-leader`, `video-capturer`, `video-editor`, `video-producer`, `video-verifier`, `worker`.

**Rule this spec enforces: any vocabulary used to classify is read at `origin/latest` (or at the release tag being staged) — never at a detached checkout, and never at the main checkout when the lane worktree is the subject.** A grep hit may be stale and an absence may be new; the vocabulary constants are read from the exact tree that is being staged.

**Correction of v1's false residual.** v1's §8 residual 2 (v1:424) claimed: *"Approximately 6 of the 182 tools in the production ledger (such as `sed`, `task_schedule_create`, and dynamic MCP server endpoints) cannot be resolved via static taxonomy and will render as `unknown` rather than `no`."* That is wrong in three ways: (a) the "6" was never measured — the live digest's 2026-11-26 file carries **387 rendered rows over 182 distinct tool names**, and the runtime-class population is **5** MCP-shaped names, not 6; (b) the two named examples are **not** dynamic server tools in the sense claimed — `task_schedule_create` is in **no** vocabulary constant (the scheduling factory defines `task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update` — `_tool_registry.py:2043-2046` — and not `task_schedule_create`), and `sed` is likewise absent from every constant, i.e. both are *unresolved names*, a different class from MCP-shaped names; (c) it conflated the `plane_*` names (13 in the live ledger) with MCP endpoints, although the `plane_*` names are **literally** granted to `project-manager` and are therefore decidable. v3 replaces the residual with the measured table in §4.6.

### 4.3 The freshness contract

| # | Clause |
|---|---|
| **F1** | The shipped artifact is **DERIVED, never copied**: stage time regenerates it from the tree being staged (`$REPO_ROOT`), so the artifact's inputs are exactly the shipped `agents/` and the shipped `daemon/tools/`. A stale committed file can never reach a release. |
| **F2** | The generator is a **pure function** of its two inputs (the agents tree, the registry taxonomy): same tree → byte-identical output (no timestamps, `sort_keys=True`). `--verify` regenerates in memory and compares the **SEMANTIC PAYLOAD** byte-for-byte, with the **PROVENANCE compared SEPARATELY** under the rules of §3.8 (content hashes ALWAYS; `last_commit` only where its content hash moved; `tree_commit` INFORMATIONAL and excluded). **It is NOT a whole-file byte compare** — the artifact carries a commit stamp that can never equal the HEAD of the commit that carries it (APPROVER-B1). |
| **F3** | The stage-time regeneration is compared against the committed dev-mode artifact; a mismatch is **reported** (`_warn` + a journaled event), **not** a stage refusal: the committed file is the *dev-mode read source* and its enforcement is the CI freshness test, whereas the shipped payload is correct by construction. Refusing would re-arm the "one unrelated lane's `meta.json` edit blocks the next release" failure class. **LEADER RULING (OQ2, §9): WARN — but ONLY because F6 lands with it.** The dependency is explicit: without F6 (§3.9.8) the committed dev artifact would have **NO drift gate**, and REFUSE (exit 78) would be the only enforcement left — F6 is a PRECONDITION of this ruling, not a nicety. |
| **F4** | The provenance stamp names **every** input (`agents` and `taxonomy`), each with a last-commit **and** a content hash, plus the `tree_commit` of the tree the generator ran in (§4.4). |
| **F5** | The reader can detect a payload/tree mismatch in frozen mode: if `releases/<v>/manifest.json` is readable and its `source_commit` ≠ the artifact's `provenance.tree_commit`, the digest renders the mismatch banner and marks the allow-list dimension degraded (never silently trusted). |
| **F6** | The CI freshness test is **NEW in this lane** (§3.9.8): `test_committed_bundle_equals_generator_output` runs `scripts/gen_tool_usage_allowlists.py --verify` against the committed `allowlists.json` in the worktree's own `.venv` and asserts **exit 0** under the **SEPARATED** comparison (§3.8): the seven semantic payload keys byte-equal, `schema_version == 2`, the `unresolved` ledger equal as a set, the provenance CONTENT hashes equal, `provenance.tree_commit` **NOT** compared. The same node also pins the **VACUITY GUARD**: a deliberately mutated `agents/` payload makes `--verify` exit **non-zero**. It never rewrites the committed file (the F11 discipline, `tests/unit/tools/test_tool_category_artifact.py:15-17`). No generator-output test exists at `d7c15d45f`. It skips with a reason where git history is unavailable (shallow CI). |

### 4.4 The provenance stamp (and why `generated_from_commit` is retired)

The v1 artifact carries exactly two top-level keys: `agents` and `generated_from_commit`. The stamp is derived from the **agents tree alone** (`git log -1 --format=%H -- agents/`), but the artifact's *content* also depends on the registry taxonomy — so the stamp was a **lie about provenance**. v3 replaces it with the multi-input block specified in §3.3:

- `provenance.tree_commit` — the tree the generator ran in (the stage's `$SOURCE_COMMIT` in production). **It is INFORMATIONAL and is EXCLUDED from every equality comparison** (§3.8, APPROVER-B1): it names the tree for a human reader, but it is NOT a drift signal, because it moves on EVERY commit — including commits that touch neither `agents/` nor `daemon/tools/`;
- `provenance.inputs.agents.last_commit` + `content_sha256` — the last commit touching `agents/` **and** a content hash over the sorted `path\0sha256` list of every `agents/*/meta.json` (content-addressed: it survives a rebase that preserves content and catches an edit that preserves the commit);
- `provenance.inputs.taxonomy.last_commit` + `content_sha256` — the same treatment for `daemon/tools/` (the category modules, the registry constants) — the hash covers the canonical JSON of `{categories, runtime_only_categories, privileged_categories, dynamic_prefixes, innate_skill_categories}`;
- `provenance.generator` + `generator_version` — which generator produced it (the generator's own bytes are additionally covered by the taxonomy content hash, because the generator's output IS the taxonomy projection).

### 4.5 The generator change (the deny extension and the taxonomy projection)

The current `scripts/gen_tool_usage_allowlists.py` reads **only** `tools["allow"]` — the source has **zero** occurrences of `"deny"` — and emits `{"agents": {id: [entries]}, "generated_from_commit": …}`. The extension (full detail in §3.8):

1. read `tools.deny` and `innate_skills` alongside `tools.allow`;
2. project the taxonomy (categories / runtime-only / privileged / dynamic prefixes / innate map) through the extended house walker;
3. compute the `unresolved` ledger for both dimensions;
4. emit the multi-input provenance block;
5. add `--verify` (byte-compare) and `--tree-commit`.

**The census re-stamp obligation (C3 / R-CENSUS) is specified in §3.9.7** — all three surfaces, in the same commit as the test additions, using the pack's own `--collect-only` method inside the lane worktree's own `.venv`. The precedent deviation is explicit: family-scoped history shows 7 commits touching the 8 census-covered files with **0** same-commit re-stamps, and repo-wide exactly 7 commits ever changed a node-count constant (all touching zero `tests/unit/` files); the closest analogue `4ae666c4a` re-stamped the PACK and left the PACKS.md row stale — that is the plan-body-ledger-agreement class, and **this lane corrects it rather than copying it**.

### 4.6 The live ledger's name classes (measured on the 2026-11-26 live digest)

The live digest file (`/home/nea/agents-ensemble/.agents/shared/planning/tool-usage-metrics-p1/digests/2026-11-26.md`, 56 272 bytes) carries **387 rendered rows over 182 distinct tool names**, all at weight 230. Classified against the vocabulary at `origin/latest`:

| Class | Distinct names | Examples | Verdict policy |
|---|---|---|---|
| **statically owned** | **162** | `bash`, `edit_file`, `task_schedule`, `rag_query`, `tool_usage_digest` | decidable — yes / no by the expansion+deny algorithm |
| **MCP-shaped, unowned** | **4** | `mcp__context7__resolve-library-id`, `mcp_zai_web_reader_webReader`, `mcp_zai_zread_read_file`, `mcp_zai_zread_search_doc` | **unknown** (call-time server list) |
| **MCP-shaped, statically owned** | **1** | `mcp_rpc_list_machine` (owned by the privileged `mcp_rpc` category) | decidable — **no** for the 26 agents that allow only `"mcp"`; **yes** for the 2 that allow `"mcp_rpc"` |
| **dynamic-prefix shaped, unowned** | **13** | `plane_list_projects`, `plane_create_work_item`, `plane_count_work_items`, … | decidable via the **literal** grants held by `project-manager`; unknown for an agent that would receive them only through the `plane_` sweep |
| **unresolved** (in no vocabulary constant) | **2** | `sed`, `task_schedule_create` | **unknown** (a factory-built name the static universe misses, a custom-prefix server tool, or a removed tool) |

**The 162, DERIVED (A1 — a developer can compute it).** "Statically owned" = *the name is a member of the artifact's `categories` map* (the source-derived category→tools attribution, §3.5). The derivation, at `d7c15d45f`:

1. The artifact's `categories` map is the union of the **63** source-attributed category keys (65 `CATEGORY_MODULES` keys − the 2 empty runtime stubs `mcp`, `plane`) and carries **271** distinct tool names — which is EXACTLY `KNOWN_TOOL_NAMES` (271) at `d7c15d45f`: every source-discoverable tool is in `KNOWN`, and every `KNOWN` name is in a category.
2. `known_tool_names` = `KNOWN_TOOL_NAMES ∪ DYNAMIC_TOOL_NAMES` = 271 ∪ 121 (overlap 114) = **278** — i.e. the category map's 271 **PLUS** the 7 DYNAMIC-only names (`comment_edit`, `commit_docs_validated`, `doc_write`, `od.compose_brief`, `od.generate`, `od.lint`, `od.save`).
3. The live ledger's **182** distinct names (the `#### <tool>` headings of `digests/2026-11-26.md`) split as: **162** statically owned (this set INCLUDES `mcp_rpc_list_machine`, the MCP-shaped-but-owned name); **4** MCP-shaped and unowned (`mcp__context7__resolve-library-id`, `mcp_zai_web_reader_webReader`, `mcp_zai_zread_read_file`, `mcp_zai_zread_search_doc`); **13** `plane_`-prefixed; **2** in no vocabulary constant (`sed`, `task_schedule_create`); and **1** — the bare category key `mcp`, which the live ledger renders as a tool name ONLY because the CURRENT digest matches raw allow entries by exact string (the §3.6.6 false-positive class the effective-grants change removes). 162 + 4 + 13 + 2 + 1 = **182**.
4. **The 162 are therefore also exactly the ledger's names that lie inside `known_tool_names`** (162 ∩ 278 = 162): the 20 ledger names OUTSIDE `known_tool_names` are precisely the 4 MCP-shaped unowned + the 13 `plane_*` + the 2 unresolved + the bare `mcp` key.

**Without `known_tool_names` (A1) the reader could not draw this line.** The 7 DYNAMIC-only names are in NO category, so a reader whose vocabulary is a projection of `categories` would classify them as "not in the vocabulary ⇒ unknown" — and would have to sweep the 13 `plane_*` and the 4 MCP-shaped names into the same bucket, losing the line between "the daemon does not grant this" ("no") and "the digest cannot tell" ("unknown") that this table is built on. `known_tool_names` is the tenth key that restores it.

This is the honest population behind the digest's `unknown` cells — it is **not** "about 6" (v1) and it is **not** 0.

---

## 5. MANIFEST BLAST RADIUS

### 5.1 What changes in the manifest shape

Exactly **two additive keys** in the `manifest.json` heredoc (`scripts/upgrade/stage.sh:875-903`):

```json
  "tool_usage_artifacts_tree_sha256": "$TOOL_USAGE_ARTIFACTS_TREE",
  "tool_usage_artifacts_manifest": {$tool_usage_artifacts_map},
```

placed beside `"guidelines_tree_sha256"` (`:894`) and `"guidelines_manifest"` (`:899`). **No existing key is deleted, renamed, reordered or re-valued.** The per-file map has the same flat `{path: sha256}` shape as the `agents` / `frontend` / `plugins` / `guidelines` maps, with tree-relative keys (`allowlists.json`, `tool-weights.json`), so it composes with `lib.sh`'s `_manifest_map_lines` verifier (`:3683`) exactly like the other trees.

**No exact-key-set assertion exists anywhere in the test corpus** (the release-journal test at `tests/test_release_journal.sh:827-833` asserts key *presence* via `grep -q "\"$f\""`, and the guidelines / executor-family / plugins-payload tests assert their own keys' presence), so adding keys breaks nothing.

### 5.2 The verification code that changes with it (file:line)

| Site | Current state | Change |
|---|---|---|
| `scripts/upgrade/stage.sh:487` | `cp -R "$REPO_ROOT/docs/guidelines" "$STAGE_TMP/guidelines"` | **NEW** sibling: generate `$STAGE_TMP/tool-usage-artifacts/allowlists.json` (generator invocation) and copy `$REPO_ROOT/.agents/shared/planning/tool-usage-metrics-p1/tool-weights.json` verbatim |
| `scripts/upgrade/stage.sh:537-541` | `_stage_cc_py` resolution — already resolved by this script (`.venv/bin/python` if executable, else `python3`) for the config-compat contract | **REUSED, not duplicated (A4):** the generator invocation runs `"$_stage_cc_py" scripts/gen_tool_usage_allowlists.py --out …`. **NEVER a hardcoded `python3`** — the venv interpreter is the one the rest of the stage machinery already uses, and a hardcoded `python3` would run the generator under a DIFFERENT interpreter than the rest of the stage (and could fail outright on a host whose `python3` lacks the generator's dependencies) |
| `scripts/upgrade/stage.sh:571-575` | `_stage_tree_walk agents|frontend|plugins` + `_tree_hash_of_lines` (`:576-578`) | **NEW** `_stage_tree_walk tool-usage-artifacts "$STAGE_TMP/tool-usage-artifacts"` + `_tree_hash_of_lines` — the walker is `lib.sh:3631` and is budget-bounded (`STAGE_TREE_BUDGET_S`, `lib.sh:261`); a 2-file tree is trivially inside the budget |
| `scripts/upgrade/stage.sh:619-620` | the guidelines walk + hash (the pattern being mirrored) | the new walk is added beside it |
| `scripts/upgrade/stage.sh:690-699` | the guidelines per-file map (`_stage_join_map`) | the new per-file map is added beside it |
| `scripts/upgrade/stage.sh:875-903` | the manifest heredoc | **NEW** the two keys (§5.1) |
| `scripts/upgrade/stage.sh:942-948` | the stage-local guidelines re-hash leg (re-walks the staged tree and refuses on mismatch, BEFORE the rename-aside swap) | **NEW** the same leg for `tool-usage-artifacts/` |
| `scripts/upgrade/lib.sh:3672-3675` | `_tree_hash_of_lines` (aggregate of already-computed lines) | **unchanged** — reused |
| `scripts/upgrade/lib.sh:3683` | `_manifest_map_lines` (per-file pinpoint diff) | **unchanged** — the new map is composable with it |
| `scripts/upgrade/lib.sh:4642`, `:4693` | `integrity_verify` loops `for tree in agents frontend` | **unchanged — and that is the deliberate decision, see §5.3** |

### 5.3 The consequence of stage-local-only verification

`lib.sh:4693` verifies **only** `agents` and `frontend` at promote time; `plugins/`, `guidelines/`, `scripts/` and the daemon slice are verified **stage-locally** only (their re-hash legs exist precisely because "lib.sh's integrity_verify loop does NOT cover this tree — same open escalation as plugins/"). v3 follows that precedent for `tool-usage-artifacts/`.

**Consequence, stated plainly:** a post-stage tamper of `releases/<v>/tool-usage-artifacts/` is NOT detected at promote time. The blast radius of that gap is bounded: the artifact feeds a **reporting** surface (the daily digest's allow-list column), not an authorization path — a tampered artifact can mis-state which agents are granted a tool, but it cannot grant one. The alternative (adding the tree to `lib.sh:4693`) would require a backward-compatible guard, because older releases carry no `tool_usage_artifacts_tree_sha256` key and the verifier's `want_hash` would be empty → `integrity MISMATCH` → every rollback/verify against an older release would fail (the N-1 machinery risk). **Decision: stage-local only, mirroring `guidelines/`, and disclosed.**

Also note the promote-time verifier's per-file diff semantics (`lib.sh:4712-4716`): an **extra** file on disk in a verified tree is flagged as "on disk, not in manifest" → `rc=1`. That is precisely why the digest's write side must never live inside a hash-verified tree (§5.4).

### 5.4 The two D4 pinning assertions (no writable output inside any hashed tree)

**The invariant.** No writable output may sit inside any hash-verified tree root. The hash-verified roots are: `releases/<v>/agents`, `releases/<v>/frontend`, `releases/<v>/plugins`, `releases/<v>/guidelines`, `releases/<v>/scripts`, the daemon slice, and (stage-locally) `releases/<v>/tool-usage-artifacts`. The digest's write side is `<install-root>/.agents/shared/planning/tool-usage-metrics-p1/digests/` — under the **install root**, in a different subtree from the release payload, and **outside** every one of those roots.

**(i) The digest output dir is not a descendant of any hashed tree root.** Pinned twice:
- **unit level** — `tests/unit/tools/test_tool_usage_digest_registration.py` (node 56, §3.9.5): with a **real tmp install layout** and the **DEFAULT frozen resolution** — **no `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` override** (the env override wins BEFORE any resolution, `tool_usage_digest.py:60-62`, so an override-path test proves nothing about the default) — the written path's ancestors contain none of `agents` / `frontend` / `plugins` / `guidelines` / `scripts` / `daemon_slice`, and the digest dir (**DERIVED** from that default resolution, not injected) is not a descendant of the release payload dir;
- **stage level** — `tests/test_tool_usage_artifacts_payload.sh` asserts the same property for the staged tree's roots.

**(ii) A file dropped into the digest dir does NOT change any tree hash.** Pinned at stage level: compute the tree hashes of the staged trees, drop a file into the digest output directory, recompute, and assert every hash is unchanged (the digest dir is not inside any walked root, so no walk can see it). This is the assertion that would have caught the historical defect class — "the shipped READ-ONLY artifacts and the WRITABLE digest output share ONE parent directory, so fixing defect 1 by adding a hash-verified manifest tree can ARM a promote-pipeline failure on the next daily fire" — and R-PAYLOAD eliminates the shared parent **by construction** rather than by an exclusion list.

---

## 6. THE WEIGHT-DIMENSION RULING + THE FE COPY

### 6.1 Honest weight rendering (W3, relabel now — no table-shape change)

Facts (measured): the committed `tool-weights.json` is literally `{}\n` (3 bytes; sha256 `ca3d163b…`, sha1 `5f36b2ea…`, git blob `0967ef42…` — full digests in §3.1), and the live digest's **387** rendered rows all carry weight **230** — the `FALLBACK_WEIGHT` constant (`daemon/services/tool_usage_digest.py:33`). The heading at `tool_usage_digest.py:261` nonetheless claims **"### Tools (sorted by schema weight × calls, descending)"**, which asserts a join that does not happen (the score is `230 × calls_7d`, so the sort is frequency-only).

**The exact honest rendering (mandatory):**

1. **Heading** — computed from the rollup data, never hard-coded:
   - when every rendered row's weight equals `FALLBACK_WEIGHT`:
     `### Tools (sorted by calls 7d, descending — no per-tool schema weights are available; every tool uses the 230-token average fallback, so the weight does not discriminate)`
   - otherwise:
     `### Tools (sorted by schema weight × calls 7d, descending)`
2. **A permanent disclosure blockquote directly beneath the heading**, rendered on every run:
   ```markdown
   > **Weight disclosure:** the per-tool schema-weight artifact (`tool-weights.json`) is currently empty.
   > All tools in this section use the 230-token average fallback (deterministic; `phase3-plan.md` §3.3).
   > While the fallback is in effect the Weight column is constant (230) and the ranking is frequency-only.
   ```
   (When a real weight table exists, the blockquote reports how many rows carry a measured weight and how many use the fallback.)
3. **The table shape is unchanged** — `| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |` stays 6 columns, the Weight cell stays the numeric weight (the suite's `_table_row` / `_section_tools` helpers parse it), and the Score stays `weight × calls_7d` (pinned by `test_score_is_weight_times_calls_7d_not_calls_24h`).
4. **`tool-weights.json` is NOT changed in this lane** — it stays `{}\n`. The reviewer ruling recorded in the determinism suite's own docstring ("9(a) — the committed `tool-weights.json` artifact is the sanctioned empty `{}` … a content pin would rot") is honoured; the artifact's *shape* is out of scope here.
5. **Building the real per-tool weight table is a SEPARATE item** and depends on the fc9 measurement point (`phase3-plan.md` §3.3 / §10 A1): a research deliverable, not a code change in this lane.

### 6.2 The FE copy — ruling, build cost, and the spec-pin question (resolved)

**The false claim.** `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts:213-219` tells the user: *"the Phase-3 daily digest (schema-token weight × frequency, zero/near-zero, allow-list intersection) is the authoritative ranking surface."* On two of three axes that is false today — there are no per-tool schema weights (every weight is the 230 fallback) and the allow-list intersection it names is currently computed by exact string match against raw entries (so it is wrong for categorized tools). The doc comment at `:62-68` repeats the claim.

**The corrected copy (rendered in the `data-test="tool-usage-ranking-caveat"` paragraph, and mirrored in the doc comment):**

> the calls (7d) sort is a frequency-only PROXY for layer-2 candidacy — the Phase-3 daily digest (per-tool weight × frequency where per-tool weights are published, zero/near-zero, and the per-agent allow-list intersection) is the authoritative day-figure and allow-list surface; while no per-tool weights are published it ranks by frequency only — the same proxy as this table. calls 24h is a rolling surface — the digest's D−1 complete-UTC-day window is the precise day figure.

**Spec-pin question — RESOLVED.** `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts:450-454` asserts only three substrings of that paragraph: `PROXY`, `daily digest`, `rolling`. **All three survive the correction**, so the existing spec passes unchanged. `frontend/e2e/tool-usage-metrics-gate.spec.ts` (215 lines) contains **zero** references to the caveat, the digest copy, the ranking or the weight — it is unaffected. v3 ADDS two pins to `metrics.tool-usage.spec.ts` (a positive pin on `ranks by frequency only` and a negative pin on the withdrawn `is the authoritative ranking surface` phrasing) so the honest claim cannot silently regress. Spec files are not part of the shipped bundle, so these two pins cost nothing at build time.

**Build cost.** Modifying the Angular component requires a rebuilt `frontend/dist/frontend/browser`: `scripts/upgrade/_build_frontend.sh` (wraps `npm run build`) must be run before staging, because `stage.sh:348` refuses to stage without `frontend/dist/frontend/browser` and `stage.sh:468` copies that pre-built tree into the payload. The FE provenance sidecar (`frontend/dist/frontend/browser/index.html.build-provenance.json`) is verified by `_verify_artifact_provenance` (`lib.sh:4394`), which **refuses** (78) a missing or malformed sidecar and a tree/artifact mismatch — so a TS edit without a rebuild is refused at stage time, not shipped stale. **Net cost of the rider: one `npm run build` + one jest run of the metrics spec — no new stage-time step**, because every release stage already requires a fresh FE build.

**LEADER RULING (OQ1, §9): SHIP IT IN-LANE.** The FE copy correction ships as an in-lane rider commit (same change series as the backend fix), because the FE copy over-claims on two of three axes and a user following the UI's own pointer must not be sent to a surface whose stated ranking axes do not hold — leaving a false claim in front of users while the backend is being made honest is worse than a slightly wider diff. The digest's own disclosure blockquote (§6.1) is **not** a substitute: it does not repair the false UI claim. The FE files are in this lane's change set (§3.1); the cost is one `npm run build` that every stage already requires.

---

## 7. REJECTED ALTERNATIVES

Each row names the alternative and the **specific failure mode it leaves behind**.

| Rejected alternative | Specific failure mode left behind |
|---|---|
| **Category-only expansion (v1's model)** — expand the allow list, ignore the deny list | The digest reports grants the daemon never makes. Concretely: `explorer` allows the category `rag` and denies `rag_query`, `rag_insert_text`, `rag_insert_texts` (`agents/explorer/meta.json`), so the runtime denies them (`daemon/tools/instance.py:609`, deny wins) while v1's digest says **yes**. The deny dimension holds 93 pairs over 34 distinct entries (59 tool names, 33 categories) — every one of them is mis-reported. |
| **Exact-match only (no category expansion)** | The mirror failure: a false **NO** for every categorized tool. `edit_file` is a tool in the category `filesystem`, allowed by `devops` and `explorer` and named by neither — an exact-match reader says "not allow-listed" while the daemon grants it. This is the failure the commission calls D3. |
| **Mirroring the repo path in the payload** (`releases/<v>/.agents/shared/planning/…`) | Re-creates the shared parent between the read-only artifacts and the writable `digests/` output, so a future hash-verified tree can be armed by a daily write — the "FOURTH defect" class. Excluding `digests/` from the copy is a fragile exclusion that one added file undoes. |
| **Embedding the artifact in the frozen binary** | Every taxonomy or `meta.json` change forces a full PyInstaller rebuild; the data becomes invisible to shell-level release integrity checks, and the build(T1)-vs-stage(T2) skew class returns. |
| **Installing the artifact into the install tree at stage time** | Version skew: a staged-not-promoted release overwrites the live artifact, and a rollback does not restore the previous one. That is also D2's prohibition for the read side. |
| **Reading the deny list live** (from `agents/*/meta.json` at digest time) | Breaks the frozen determinism invariant (`phase3-plan.md:80` item 12(c) — the verbatim quote; restated by T2 at `:97` in lower case: "the digest NEVER scans the live `agents/` tree at run time") and the `agents/` AST pin (`test_no_live_env_or_registry_read_inside_the_rollup`, `:599-607`); the digest would change under a live edit, byte for byte, with no artifact change. |
| **Leaving the PACKS.md row stale** (the `4ae666c4a` precedent) | The pack's census and the row's prose would disagree about the same fact — the plan-body-ledger-agreement class — and the row would keep asserting the C5 semantic being corrected ("`sys.frozen` anchors at `sys.executable`'s parent") while the code no longer does that. |
| **Expanding categories inside `rollup()`** | Violates the determinism AST pin: `rollup`'s source must contain no `os.environ` / `getenv` / `agents/`. Putting the expansion in the reader keeps the rollup a pure function of the artifact. |
| **Using the board's stored `bash`/`devops` probe as the acceptance probe** | Complete vacuity: `bash` is BOTH a category key and a tool name, so an exact-match implementation returns the same verdict as the correct one. The probe would pass against a digest that is still broken. |
| **Sweeping the `mcp` category into the artifact's map** | The false-YES class at scale: 26 of 55 agents allow `"mcp"`, so every MCP row — including the privileged `mcp_rpc_*` the sweep must withhold — would be marked "yes". MCP membership is call-time state (`mcp_tools.py` is a 15-line stub; the server list is DB/config), so it is not statically knowable. |
| **Adding `tool-usage-artifacts` to the promote-time integrity loop** (`lib.sh:4693`) | Breaks verification of older releases: `want_hash` would be empty for any release that predates the key → `integrity MISMATCH` → the rollback/verify path fails against N-1 releases. Stage-local verification (the `guidelines/` precedent) keeps the N-1 machinery intact. |
| **Writing a second AST walker for the taxonomy** (v1's `scripts/tool_usage_taxonomy.py`) | A third source of truth alongside `_tool_registry.py` and the house's own walker, free to drift silently. v3 extends the house walker instead (§3.5). |
| **Extracting `resolve_live_install_root()` into a "neutral" module** | Buys nothing and costs churn: the resolver is already public and exported (`maintenance_seed.py:274`, `__all__` at `:1351`), already exercised by two test modules, and service→repository-source imports are established in this codebase (`daemon/services/execution_receipt.py:28`, `daemon/services/scheduling_service.py:963`, `daemon/services/sources_gc_service.py:56`). Moving it would ripple through `maintenance_seed`'s own consumers for zero behavioural gain — so v3 **imports it directly** (`from daemon.repositories.source.maintenance_seed import resolve_live_install_root`) and adds no new walk. |
| **Shipping the committed artifact at stage time instead of regenerating** | A stale committed file reaches a release (it is already stale today: 3 of 55 agents differ). Regeneration at stage time makes the shipped artifact correct by construction. |
| **Treating an unresolvable name as "no"** | A false NO for a tool the daemon does grant (e.g. `task_schedule_create`, a factory-built name the static universe misses; `sed`, a custom-prefix server tool). Unresolvable names render **unknown**. |

---

## 8. RESIDUALS

### 8.1 G4 — an absent category key silently grants nothing (disclosed, not approximated)

The runtime's own comment (`daemon/tools/instance.py:596-599`) is the G4 statement: an allow entry naming a category that is **absent** from the runtime map falls through to the literal branch, so the category NAME is added to the allowed set as if it were a tool name — which grants nothing. Because the artifact's category map is source-derived and a given instance's runtime map is instance-dependent (§3.5), the two maps are not guaranteed identical, and G4 can therefore bite in the artifact's direction. **v3 discloses it in the digest's notes footer** ("category keys present in the runtime map but absent from the artifact, and vice versa, are listed") rather than pretending the maps are the same.

### 8.2 Factory-built categories are not statically enumerable (the C1 limit)

63 of the 65 category keys carry source-level `@tool` definitions; 2 (`mcp`, `plane`) are empty stubs; and the *set of keys present in a running instance's* `list_tools_by_category()` is instance-dependent. The artifact's map is therefore a **source-derived approximation** of any given instance's map — an over-approximation of names (it can contain names an instance never built) and never an under-approximation of the source. Any verdict that depends on a name's *category membership* inherits that approximation; the 14 cross-attributed tools (`instance`↔`council`, `job`→`mission`) are handled by honouring the explicit `@register_tool_category` decorator, and a tool built through `StructuredTool.from_function` (2 sites) or with a runtime-composed name is not statically discoverable at all → **unknown**.

**The 201 — the non-privileged universe — DERIVED (A1).** The default universe an inherit-all agent receives is the union of the NON-PRIVILEGED categories' tool lists (`daemon/tools/instance.py:587-591`, which skips every `PRIVILEGED_TOOL_CATEGORIES` member). At `d7c15d45f`, derived from the same source-derived attribution the generator emits:

1. the **63** attributed category keys carry **271** distinct tool names in total (== `KNOWN_TOOL_NAMES`, 271);
2. the **23** privileged categories own **70** of those names;
3. the non-privileged categories own the remaining **201** — `271 − 70 = 201`.

So the "full non-privileged universe (201 names)" of test 44 and the "non-privileged universe is 201 names" of §3.6.6 are **COMPUTED, not hand-copied**: the implementer re-derives `|union(non-privileged categories)|` from the generator's own `categories` map and asserts it equals the fixture's expected set. The **7 DYNAMIC-only names are NOT in the 201** — they belong to no static category, so they are part of no default universe; `known_tool_names` (278) says they are KNOWN names, which is what lets the reader decide them `"no"` (rather than `"unknown"`) for an agent whose allow list does not reach them.

### 8.3 MCP and the dynamic-prefix sweep render `unknown` (and one deliberate under-report)

Unowned MCP-shaped names and unowned `plane_`-prefixed names are call-time state → **unknown**. One consequence is disclosed rather than hidden: an **inherit-all** agent (`watcher`, the only agent with an empty allow list) is in fact granted every non-privileged tool at filter level, MCP included — the digest still renders those rows `unknown`, because the static map cannot distinguish "an MCP tool this agent would receive" from "a stale name". The under-report is one agent wide and is listed in the notes footer.

A second, related disclosure: a ledger row proves a call happened, which in the normal path implies the grant. The digest **deliberately does not** infer the allow-list verdict from the call — that would make the allow-list column a tautology ("yes, because it was used").

### 8.4 G5 — in-body role gates are disclosed, not modelled

The filter-level verdict cannot see in-body authorization: `pok_curate` is withheld from every agent except leader/project-manager by an in-body role gate (the category is deliberately NON-privileged so the grants flow through the per-agent `tools.allow` entries — `_tool_registry.py:1290-1298`), and `mcp_rpc_*` carries its own `_is_mcp_rpc_authorized(agent_id)` check inside each tool body (`daemon/tools/mcp_rpc_tools.py:85`). The digest reports the **filter-level** verdict and names these gates in the notes footer. It does not approximate them.

### 8.5 One walker, one source of truth

The taxonomy projection rides the house walker (`_scan_category_module_sources` → the new per-category sibling), so `_tool_registry.py` remains the single source of truth. Drift is caught by the existing zero-drift pin in the registration pack (`test_the_static_universe_and_the_source_have_zero_drift`) and by the new attribution pin (§3.9.2). No second taxonomy module is created.

### 8.6 The payload is verified at stage time only

`lib.sh:4693` verifies `agents` + `frontend` at promote time; `tool-usage-artifacts/` is verified stage-locally only (the `guidelines/` precedent). A post-stage tamper of the payload would not be detected at promote. The blast radius is a reporting surface, not an authorization path (§5.3).

### 8.7 The provenance stamp mixes commit identity with content identity

`provenance.inputs.*.last_commit` changes when the history is rewritten (a rebase); `content_sha256` does not. Both are recorded so a reviewer can tell "the same content under a new commit" from "different content". The stamp describes the tree the artifact was generated from, not the release's git history.

### 8.8 Names the static universe misses stay `unknown`

`task_schedule_create` and `sed` appear in the live ledger and in **no** vocabulary constant at `d7c15d45f`. Regenerating `KNOWN_TOOL_NAMES` from the source (`discover_source_only_tool_names`, `:1143`) is a house duty outside this lane; until it happens, those rows render `unknown`.

### 8.9 The FE rider costs one build

§6.2: one `npm run build` plus one jest run of the metrics spec — no new stage-time step, because every stage already requires a fresh FE build (`stage.sh:348` refuses without it) and the provenance sidecar check (`lib.sh:4394`) refuses a stale build.

### 8.10 v1's residual list — three corrections

| v1 residual | Verdict in v3 |
|---|---|
| v1 residual 2 (v1:424) — *"Approximately 6 of the 182 tools in the production ledger (such as `sed`, `task_schedule_create`, and dynamic MCP server endpoints) cannot be resolved via static taxonomy"* | **FALSE as stated.** The live digest carries 387 rows over 182 distinct names; the runtime-shaped population is 5 MCP-shaped names (4 unowned + 1 owned by `mcp_rpc`), the prefix-shaped population is 13 `plane_*` names, and the unresolved population is exactly 2 (`sed`, `task_schedule_create`). The examples named are not MCP endpoints, and the "6" was never measured. Corrected in §4.2 and §4.6. |
| v1 residual 3 — *"Taxonomy as a Third Source of Truth"* | **MOOT.** v3 reuses the house walker instead of adding `scripts/tool_usage_taxonomy.py` (§3.5); there is no third source of truth. |
| v1 residual 6 (v1:428) — *"Pre-Existing Unregistered Test Pack Status: At base commit `f83fb5d81`, the digest determinism suite was not registered in any pack. The fix introduces a dedicated test pack `tool_usage_digest_unit_test.sh` and registers it in `PACKS.md`."* | **FALSE.** The suite **IS** registered at tip: `test/packs/tool_usage_pins_unit_test.sh` covers `test_tool_usage_digest_determinism.py` (37 nodes), `test_tool_usage_digest_root.py` (5) and `tests/unit/tools/test_tool_usage_digest_registration.py` (21) — the pack landed on `origin/latest` on 2026-11-26 and the row (`.agents/tester/PACKS.md:156`) carries the census. v1's statement was true only of the older base `f83fb5d81`. **Consequence for v3: no new PYTEST pack is created (the shell payload test's runner is a thin shell wrapper pack, §3.9.6).** The two new test suites JOIN the existing pack's slice — **the slice becomes 10 files, UNCONDITIONALLY** (the 8 existing + the two new suites; v2's "9 or 10" was conditional and contradicted the §3.9.7 count) — and the census is re-stamped in place (§3.9.7). The stage-level SHELL test is a different artifact class and gets its OWN thin runner pack (§3.9.6); **no new PYTEST pack is created**. `test/packs/tool_usage_family_unit_test.sh` is genuinely disjoint (4 other files) and carries no census. |

---

## 9. LEADER RULINGS — the three open questions v2 raised are DECIDED (2026-11-26)

The three questions v2 raised for the leader are **DECIDED**. This section is the single home for the three rulings; every other section cites them by id (OQ1 / OQ2 / OQ3) rather than restating them.

| # | Question v2 raised | **RULING** | Why it is right — and what it binds | Where it lands in this document |
|---|---|---|---|---|
| **OQ1** | Ship the FE copy correction as an in-lane rider, or defer it to a follow-up? | **APPROVED — SHIP IT IN-LANE.** | The FE copy over-claims on two of three axes, and a user following the UI's own pointer must not be sent to a surface whose stated ranking axes do not hold. The cost is one `npm run build` that every stage already requires (§6.2), and the three pinned substrings (`PROXY`, `daily digest`, `rolling`) survive the correction. | §6.2 (the rider is IN-LANE, same change series); §3.1 (the FE files are in the change set) |
| **OQ2** | Stage-time drift against the committed dev artifact: WARN (journaled) or REFUSE (exit 78)? | **WARN — but ONLY because F6 lands with it.** | The shipped payload is regenerated from the tree being staged, so it is correct by construction; refusing because the *committed dev-mode* file is stale would re-arm the failure class where one unrelated lane's `meta.json` edit blocks the next release. **The dependency is explicit: without F6 — the CI freshness test, NEW in this lane (§3.9.8) — the committed artifact would have NO drift gate, and REFUSE would be the only enforcement left. F6 is a PRECONDITION of this ruling, not a nicety.** | §4.3 F3 (the dependency is stated inside the clause) + §4.3 F6 and §3.9.8 (the test that makes WARN safe) |
| **OQ3** | R-MCP's categorical rule vs. the decidable MCP cases — how strict should the reader be? | **AGREE with v2's recommendation — APPROVED.** | UNOWNED MCP-shaped names render `unknown` for every agent (membership is call-time state: the server list is DB/config state and the category stub has no static members); the two statically-decidable cases stay exact — a LITERAL grant renders `yes` (no agent literally grants an `mcp_*` name today, so this is latent), and a name owned by a static category renders `yes`/`no`, which is what makes the `mcp_rpc_*` withholding that R-OVERCLAIM demands decidable at all. The stricter alternative (every MCP-shaped name `unknown`, including `mcp_rpc_*`) would leave the ownership guard unobservable and silently lose a real distinction. | §3.6.3 (the three name classes and their order), §3.6.4 and §8.3 (the disclosed under-report), §3.9.4 tests 51-52 |


**Decisions I made inside my own mandate (flagged for override, not asked):**
- **D-NEW-1** — carry `innate_skills` in the artifact and apply `INNATE_SKILL_TOOL_CATEGORIES` (36 of 55 agents are affected; omitting it produces false NOs).
- **D-NEW-2** — source-derived taxonomy via the extended house walker, with `runtime_only_categories` declared (no complete static taxonomy is claimed).
- **NARROW-2** — the source-mode digest root is the checkout's `digests/` dir (gitignored at `.gitignore:96`), not the live install tree, so a dev run never writes into production.
- **`ENSEMBLE_TOOL_USAGE_ARTIFACT_DIR`** — a read-side env seam for tests, documented test-only and unset in production.
- **The two new test suites join the existing pack's slice — 10 files UNCONDITIONALLY** (the 8 existing + the two new suites); no new PYTEST pack is created (v1's residual 6 is false). The stage-level SHELL test is a different artifact class and gets its OWN thin runner pack + PACKS.md row (§3.9.6).
- **The F6 freshness test is homed in the census-covered registration suite** (`tests/unit/tools/test_tool_usage_digest_registration.py`, node 57, §3.9.8), so its +1 folds into the §3.9.7 census arithmetic without adding an 11th slice file.

### 9.1 The APPROVER's safety argument for the allow-list dimension (recorded 2026-11-26)

The approver's verdict on the architecture was: **"No over-claim path found"** — the residual risks are **UNDER-claims**, not over-claims. v4 records that as the safety argument for the allow-list dimension and states exactly what it rests on:

1. **Every `"yes"` branch corresponds to a runtime grant.** A `"yes"` is emitted from exactly three places: (a) the agent's **EFFECTIVE grant set** — the flat map = allow expansion MINUS deny, deny winning, proven equivalent to `resolve_tool_filter` for all 55 agents by test 44; (b) an **inherit-all** agent's NON-PRIVILEGED default universe (the runtime's own construction, `instance.py:581-591`, now an EXPLICIT branch, §3.6.2a); or (c) a **literal allow entry** (the runtime adds the name itself, `instance.py:599`). There is no fourth source of `"yes"`, and none of the three can fire for a name the runtime does not grant.
2. **MCP-shaped and prefix-shaped names render `"unknown"`, never `"yes"`/`"no"`** — with ONE documented exception that is itself a runtime grant: a name **LITERALLY** allowed by an agent (the `project-manager`'s 28 `plane_*` entries) is an exact grant and renders `"yes"`. The 26 agents that allow only the category `"mcp"` render `"unknown"`, and the privileged `mcp_rpc_*` names render `"no"` for them (the ownership guard).
3. **The residual risks are UNDER-claims** — precisely the two the approver named: the **inherit-all column** (fixed in v4 by the explicit `inherits_all` branch) and the **missing name universe** (fixed in v4 by the tenth key `known_tool_names`). Neither can over-claim; both can only under-report, and v4 closes both.
4. **The one residual over-claim risk is G4 and is DISCLOSED, not approximated** (§8.1): the artifact's category map is SOURCE-derived, so it can contain a name a given instance never built. The digest reports the FILTER-level verdict and names the gap in the notes footer.

v4's changes do not weaken any of the four points: they CLOSE the two under-claims the approver named and leave the disclosed G4 exactly as it was. The two other fixes (APPROVER-B1's separation, APPROVER-B3's field/marker split) touch the DRIFT GATE and the OUTPUT SHAPE respectively — neither creates a new `"yes"` branch, and APPROVER-B3's split makes the verdict STRING more visible, not more permissive.

---

## Appendix A — Reproducing the census (read at `origin/latest`, never at a detached HEAD)

```bash
S=d7c15d45f65037844471c3d2ff2a06d4ed9afeec
WT=/home/nea/ensemble-src-wt-tool-usage-digest
git -C "$WT" show "$S:daemon/tools/_tool_registry.py"        # CATEGORY_MODULES :1287, KNOWN_TOOL_NAMES :1719, DYNAMIC_TOOL_NAMES :26, DYNAMIC_TOOL_PREFIXES :372, PRIVILEGED_TOOL_CATEGORIES :486
git -C "$WT" show "$S:daemon/registry.py" | sed -n '1413p'   # known_categories = set(CATEGORY_MODULES.keys())
git -C "$WT" show "$S:agents/maintenancer/meta.json" | sed -n '31p'   # "deny": ["git_commit", "edit_file", "write_file"]
git -C "$WT" ls-tree -r --name-only "$S" agents | grep -c 'meta.json$'   # 55
```

Measured results (the architect's run, reproducible with the classifier in Appendix B):

| Quantity | Value |
|---|---|
| `CATEGORY_MODULES` keys | **65** |
| `KNOWN_TOOL_NAMES` | **271** |
| `DYNAMIC_TOOL_NAMES` | **121** |
| `DYNAMIC_TOOL_PREFIXES` | **`{"plane_"}`** |
| static tool universe (union) | **278** (`DYNAMIC − KNOWN` = 7: `comment_edit`, `commit_docs_validated`, `doc_write`, `od.compose_brief`, `od.generate`, `od.lint`, `od.save`) |
| `known_tool_names` (the tenth key, A1) | **278** — the SAME set as the union above: `KNOWN_TOOL_NAMES` (271) ∪ `DYNAMIC_TOOL_NAMES` (121), overlap 114. It is the reader's vocabulary; the union above is the same set seen from the constants' side |
| the category map's name count | **271** — the union of the **63** attributed category keys' tool lists, which is EXACTLY `KNOWN_TOOL_NAMES` (271) at `d7c15d45f` |
| the non-privileged universe (201, §8.2) | **201** = 271 − the **70** names owned by the **23** privileged categories |
| the ledger's name classes (§4.6) | **182** distinct = **162** statically owned (INCLUDING the owned `mcp_rpc_list_machine`) + 4 MCP-shaped unowned + 13 `plane_`-prefixed + 2 unresolved (`sed`, `task_schedule_create`) + 1 bare category key (`mcp`) |
| keys that are BOTH a category key and a tool name | **4** (`bash`, `shared_meta_kv`, `system_upgrade`, `time`) |
| full resolvable vocabulary | **339** = 65 + 278 − 4 |
| `PRIVILEGED_TOOL_CATEGORIES` | **23** |
| Basis A (committed artifact) | 55 agents · 792 pairs · 145 distinct · 694 category / 70 tool / 28 prefix / **0 unclassified** |
| Basis B (live `agents/*/meta.json`) | 55 agents · 794 pairs · 147 distinct · 692 category / 74 tool / 28 prefix / **0 unclassified** |
| DENY dimension (live only) | 93 pairs · 34 distinct · 33 category / 59 tool / 0 prefix / **1 unclassified** (`maintenancer: git_commit`) |
| stale detached HEAD `bf4bfde6e` | `CATEGORY_MODULES` has **64** keys (`room` missing) → the phantom "8 neither" (all eight were the single string `room`) |

## Appendix B — Evidence log (every load-bearing claim, with its site)

| Claim | Site |
|---|---|
| grant rule: expand allow, then deny wins | `daemon/tools/instance.py:455` (`resolve_tool_filter`), `:596-599` (G4), `:609` (`allowed_tools -= denied_tools`) |
| privileged categories never default-granted | `instance.py:589-592`; `_strip_privileged_category_tools` `:8089-8124`; `PRIVILEGED_TOOL_CATEGORIES` `_tool_registry.py:486` (23 members) |
| innate-skill expansion | `instance.py:195-201` (map), `:204-249` (`expand_allow_for_innate_skills`), `:242` (privileged fail-closed), `:8180` (the runtime's own call site) |
| `mcp` ownership guard + rationale | `instance.py:497-525` (sweep), `:518` (`owned_elsewhere`), `:500-511` (rationale), `_owned_elsewhere` `:435-454` |
| `plane_` ownership guard (W-8) | `instance.py:561-579` |
| MCP name shape / prefix override | `daemon/mcp/tool_adapter.py:201-215` |
| MCP is a runtime stub | `daemon/tools/mcp_tools.py` (15 lines); `daemon/tools/plane_tools.py` (17 lines) |
| registry keys vs display names | `list_tools_by_category` `_tool_registry.py:1048`; `get_tool_categories` `:2140` |
| house AST scanner | `_scan_category_module_sources` `:1063`; `discover_source_only_tool_names` `:1143`; `discover_all_tool_names` `:1177`; `scan_tools_for_full_docs` `:1228` |
| validator vocabulary | `daemon/registry.py:1413` (`known_categories`), `:1432-1436` (allow), `:1443-1447` (deny) |
| digest readers and the render | `daemon/services/tool_usage_digest.py:43` (`_code_root`), `:54` (`digest_root`), `:66` (`_artifact_dir`), `:70` (loader), `:80`/`:91` (readers), `:132` (`rollup`), `:199`/`:206` (allow-list fields), `:246` (`render_markdown`), `:261` (the weight heading), `:293-305` (write path) |
| determinism AST pin | `tests/unit/services/test_tool_usage_digest_determinism.py:586` (source), `:599-607` (the pin) |
| the 5 digest-root pins | `tests/unit/services/test_tool_usage_digest_root.py:18-28`, `:31-38`, `:41-47`, `:50-55`, `:58-65` |
| the house resolver | `daemon/repositories/source/maintenance_seed.py:219` (walk), `:241` (frozen root), `:244-248` (the defect class it names), `:274` (`resolve_live_install_root`), `:1351` (`__all__`) |
| resolver consumers / test coverage | `tests/unit/test_maintenance_results_root_equivalence.py:28`, `:96`; `tests/unit/test_maintenance_seed_guard.py:56`, `:258`, `:269`, `:291`, `:317` |
| service→repository-source import precedent | `daemon/services/execution_receipt.py:28`, `daemon/services/scheduling_service.py:963`, `daemon/services/sources_gc_service.py:56` |
| the loud-degradation precedent (logger calls) | `daemon/tools/guidelines.py:394` (`logger.exception("guidelines_list failed")`), `:445` (`logger.exception("guideline_read failed")`) — note `:181` is `_payload_refusal` (the typed payload-unavailable envelope), **not** the logger precedent |
| stage/promote machinery | `scripts/upgrade/stage.sh:348` (FE absent ⇒ refuse), `:354` (the build instruction), `:468` (FE copy), `:487` (guidelines copy), `:559` (`_no_env_in_release`), `:571-575` (walks), `:576-578` (hashes), `:619-620` (guidelines walk+hash), `:690-699` (per-file map), `:875-903` (manifest heredoc), `:894`/`:899` (guidelines keys), `:942-948` (stage-local re-hash leg) |
| lib helpers | `scripts/upgrade/lib.sh:3631` (`_stage_tree_walk`), `:3672-3675` (`_tree_hash_of_lines`), `:3683` (`_manifest_map_lines`), `:4394` (`_verify_artifact_provenance`), `:4642`/`:4693` (`integrity_verify`, agents+frontend only), `:4712-4716` (the "on disk, not in manifest" diff) |
| the census pack | `test/packs/tool_usage_pins_unit_test.sh:14-18` (header counts), `:55-56` (the stale C5 prose), `:139` (`EXPECTED_NODES=200`), `:142-161` (`UNIT_FILES`/`UNIT_EXPECTED`), `:166-171` (`.venv` resolution), `:191-209` (the `PACK_SELFTEST` override rules), `:254-291` (`--collect-only` slicing proof, strict `-ne`), `:308`/`:319` (per-file and sum equality), `:325` (the `"8 files"` echo literal) |
| the PACKS.md row | `.agents/tester/PACKS.md:148` (block prose), `:150` (the C5 semantic clause — the one that must be rewritten), `:156` (the row: per-file counts, total 200, freeze `40301a5e3`) |
| the generator | `scripts/gen_tool_usage_allowlists.py` (118 lines): `build_payload()` reads only `tools["allow"]`; `_derive_commit()` derives the stamp from `agents/` alone; `--agents-dir` default `agents/`, `--out` required, `--commit` optional; output `json.dumps(..., indent=2, sort_keys=True) + "\n"` |
| the FE copy and its pins | `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts:62-68`, `:213-219`; `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts:450-454`; `frontend/e2e/tool-usage-metrics-gate.spec.ts` (215 lines, zero references to the caveat) |
| the plan of record | `architecture-recommendation.md:119` (D-6); `phase3-plan.md:59-63` (weights, determinism invariant, U8), `:78-82` (§3.12 item 12 — U7; the verbatim "the digest NEVER scans the live `agents/` tree at run time" is item 12(c) at `:80`), `:97` (T2 — restates the same rule in lower case) |
| the MCP-shape predicate (A2) | `daemon/tools/instance.py:520-523` — the CODE: `name.startswith("mcp_") and "_" in name[4:]` — **at least ONE underscore after the prefix**. The COMMENT at `:519` says "contain at least 2 underscores", which counts the prefix's own underscore; the CODE at `:522` is the authority. Measured against the live ledger, both readings select the same 5 names — the predicate still has to be the CODE's, because a name shaped `mcp_x_y` exists outside the ledger |
| the inherit-all agent (APPROVER-B2a) | `agents/watcher/meta.json:21-23` — `"tools": {"allow": []}` — the ONLY empty-allow agent in the 55-agent corpus (verified at `d7c15d45f`). The runtime's default universe: `daemon/tools/instance.py:581-591`; the privileged categories are skipped at `:589-592` |
| the two pinned determinism assertions (APPROVER-B3) | `tests/unit/services/test_tool_usage_digest_determinism.py:541` (`assert tool["agents"][0]["allow_listed"] is True`) and `:544` (`assert "(allow-listed)" in _agent_lines(md, "tie_a")[0]`) — **BOTH PASS UNCHANGED under v4**; neither is edited |
| the ad-hoc monkeypatch legs (APPROVER-B3) | `tests/unit/services/test_tool_usage_digest_determinism.py:497` (zero/near-zero leg, `allowlists` → `{"ari": ["tool_usage_digest", "barely", "well_used"]}`) and `:530` (marker leg, `allowlists` → `{"ari": ["tie_a"]}`). `ari`'s deny list (`agents/ari/meta.json:54`) is `["edit_file", "write_file", "watch_job", "watch_jobs"]` — it names NONE of those fixture tools, so the deny gate cannot fire in either leg |
| the hermetic fixture (APPROVER-B3) | `tests/unit/services/test_tool_usage_digest_determinism.py:235-247` — `hermetic_artifacts` patches `tool_weights` → `{}` and `allowlists` → `{}`; v4 ADDS the `allowlist_source` neutralization, which must be an **"ok"-state** envelope (§3.9.2) |
| the stage-time interpreter (A4) | `scripts/upgrade/stage.sh:537-541` — `_stage_cc_py=""`; `$REPO_ROOT/.venv/bin/python` if executable, else `python3` |
| the pack's `:7` header literal (A3) | `test/packs/tool_usage_pins_unit_test.sh:7` — `       "TOOL-USAGE" (8 files, one pack script + one PACKS.md block).` (the `:325` echo literal is `CENSUS: OK (8 files, per-file sum=…)`) |
| the UNSTAMPED precedent (APPROVER-B1) | `.agents/shared/metrics/tool-categories.json` — the whole content is `{"categories": [ … ]}`; **NO commit stamp**, which is exactly why its drift check CAN be a whole-file byte compare and this artifact's CANNOT |
| the generator's current shape | `scripts/gen_tool_usage_allowlists.py` (118 lines): `build_payload()` reads only `tools["allow"]`; `_derive_commit()` derives the stamp from `agents/` alone; `--agents-dir` default `agents/`, `--out` required, `--commit` optional; output `json.dumps(..., indent=2, sort_keys=True) + "\n"` |

## Appendix C — UNVERIFIED items (with reasons)

| Item | Why it is unverified | How the implementer closes it |
|---|---|---|
| The council's "52 of 65 statically enumerable" | The council's measuring method was not disclosed; it is a runtime-population observation, and the static classification at `d7c15d45f` is 5 module-level + 58 factory-only + 2 stubs. v3 does not depend on the number — it classifies by source enumerability (§3.5) and discloses key presence as instance-dependent. | Re-run the source classification (Appendix A) and record the classes in the artifact's own `categories` block. |
| The exact node counts after the test additions | v3's counts are arithmetic predictions, not measurements. | The pack's own `--collect-only` (inside the lane worktree's `.venv`) is authoritative; stamp the measured numbers (§3.9.7). |
| Whether the FE provenance sidecar check catches a dist built at a different commit **and** a matching tree | `lib.sh:4394` refuses a missing/malformed sidecar; the commit comparison's exact strictness was not read line by line. | The implementer rebuilds the FE before staging and confirms the stage accepts the rebuilt dist. |
| Whether the new payload dir would be picked up by the release's `_no_env_in_release` walk or any other whole-tree guard | The walk is invoked over the staged tree (`:559`), so the new dir is inside it by construction; no separate rule was read. | The payload shell test asserts the staged tree passes the walk. |
| The exact behaviour of `resolve_live_install_root()` when the checkout itself lives under a `releases/<ver>/` directory | The lifting branch (`maintenance_seed.py:279-300`) was read in part, not exercised. | The implementer runs the source-mode root test inside a fake `releases/<ver>/` layout if the lane wants that leg pinned. |

---
