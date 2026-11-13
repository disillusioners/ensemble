# Architecture Adjudication: guideline-read-tool — Q1–Q4 verdicts + sanity rulings

Date: 2026-11-13T19:05Z
Adjudicator: architect (Emperor's process, diagnosis-of-seam mandate)
Base: worktree `/home/nea/ensemble-src-wt-guideline-read-tool` @ `eb85d99a7` (branch `feature/guideline-read-tool`, clean)
Evidence: two independent read-only verification legs dispatched against this tree —
(A) release-pipeline mechanics (stage/manifest/floor/integrity/consumers), (B) runtime-resolution + privileged-gate mechanics.
Every anchor below was re-derived by the verification legs from THIS tree; the planner's research file
(`research-release-seam.md`) was treated as a claim set, not as truth. Where this document and any plan
file disagree, THIS document wins for the seams it rules on.

Verdict statuses: **BINDING** (developer must follow) / **correction** (plan text is factually wrong and must be fixed) /
🟢 optional (recommended, non-blocking).

---

## Q1 — THE SEAM: frozen-binary runtime resolution of the guidelines dir — BINDING

**Resolution order (final):**

```
1. explicit `base_dir` argument (factory param, default None) — tests & ops injection; when given, it is
   used verbatim, no further derivation
2. elif sys.frozen:  Path(sys.executable).parent / "guidelines"      → releases/<v>/guidelines/
3. else (dev):       Path(__file__).resolve().parents[2] / "docs" / "guidelines"   → <repo>/docs/guidelines/
4. absent dir at CALL time → typed refusal naming the resolved path + reason ("release predates the
   guidelines payload"); NEVER an exception, NEVER an empty-list lie, NEVER a boot/registration failure
```

**(a) frozen/live = the frozen fork, NOT the registry anchor.** `sys.frozen → Path(sys.executable).parent`
is the house fork at ALL SEVEN runtime-lane sites (`loader.py:445-448`, `manager.py:4815-4818`,
`registry.py:1580-1583`, `graph.py:11044-11047`, `api.py:168-171`, `routers/agents.py:19-22`,
`agent_mother.py:140-143`); frozen `sys.executable` IS `releases/<v>/ensemble-prod`, so
`Path(sys.executable).parent` = the release root (`upgrade_tools.py:455-457`: install dir is
`binary.parent.parent.parent`). The registry anchor (`prompt_refresh.py:660` `resolved_dir = Path(agent_meta.path)`,
`:745` `release_root = resolved_dir.parent.parent`) is real but REJECTED here: it yields the IDENTICAL
directory in frozen mode (the registry itself resolves agents via the same fork, `registry.py:1580-1584`) —
it buys nothing in live — while costing either a reach into the PRIVATE `_agents_dir` (`registry.py:622/:630`,
no public accessor; only tests bind it, `test_prompt_refresh_primitive.py:272-277`) or a per-agent
`get_resolved(agent_id).path.parent` dependency inside a tool factory. New coupling to registry internals,
zero frozen-mode gain: refused. The anti-pattern stays refused: `plugin_registry.py:64`
(`Path(__file__).resolve().parents[2] / "plugins"`, unresolvable frozen — `manager.py:3860` boot-scans it,
absent dir = SILENT EMPTY registry `scan_plugins_root:456-461`, boot continues at `:3862-3872`).

**(b) dev = serve repo `docs/guidelines/` via the dev branch of the same fork.** Ruled under Q3 below.
Binding shape constraint: the repo path is computed ONLY inside the non-frozen `else` branch — never an
unconditional fallback, never a try/except that a frozen binary could reach. Note for reviewers:
`parents[2]` arithmetic in the dev branch is NOT the plugin anti-pattern — the defect there is
UNCONDITIONAL `__file__` resolution; the frozen-guarded dev branch is the sanctioned house shape
(agent_mother.py:141-143 uses exactly this at the same directory depth as the new module).

**(c) tests = explicit `base_dir` constructor arg only.** First-tier house pattern is explicit-argument
injection (`test_release_manifest.py:38-53`: `release_root = tmp_path/...`, real sha256 fixtures, ZERO
env/monkeypatch in the file); second tier is registry-seam monkeypatch (not needed — the param exists).
NO env var: the runtime payload-base lane has ZERO env knobs today (`loader.py`, `registry.py`,
`agent_mother.py`: 0 hits; `manager.py`: only generic env snapshot + a refresh-interval knob, `:5769-5770`/`:6757`;
`api.py:723-725` `ENSEMBLE_DATA_DIR` is the DB data-dir knob, not payload-base) — the feature must not
mint the first one.

**(d) absent dir = typed refusal, boot-neutral.** The factory ALWAYS constructs both tools (notify-user
precedent, `instance.py:2912-2913`: "The factory itself ALWAYS constructs (the tool refuses cleanly when a
handle is missing)"); dir-existence is checked at CALL time inside the tool body (house shape:
`access_memory.py:206`, `inner_soul.py:672`, `agent_mother.py:164-169`), NEVER at factory/registration time.
`guidelines_list` on an absent dir returns the SAME typed refusal as `guideline_read` (an empty list would
assert "no guidelines exist" when the truth is "payload not present in this release" — dishonest and
undiagnosable). A live release predating the payload therefore boots and registers cleanly; the tools
refuse with the typed token. This also covers future-binary-on-old-release-dir shapes (manual/partial installs).

**Emperor's invariant check:** LIVE runtime never reads repo paths — enforced BY THE FORK: the repo path
is computable only in the non-frozen branch; a frozen binary cannot reach it. The invariant needs no
additional guardrail.

---

## Q2 — FLOOR GUARD: both constants → merge sha, in the bump commit — BINDING (planner's sketch CONFIRMED, two sharpenings)

**Ruling:** advance BOTH `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`daemon/tools/upgrade_tools.py:4145`) and
`PIPELINE_SCRIPTS_REV` (`scripts/upgrade/lib.sh:95`) to the **MERGE commit M** (`git merge --no-ff
feature/guideline-read-tool` into `latest`), inside the **release-bump commit B**, post-merge, pre-tag,
pre-stage. One commit, both files — D6-8 "the two travel together" is enforced by the W-2 gate itself
(`lib.sh:737-739` refuses `stamp != floor`).

**Why the merge sha and not the bump sha or tip-at-stage:** the cadence rule pins "the merge commit that
introduces the ceremony-relevant scripts/upgrade semantics" (`stage.sh:633-641` verbatim;
`lib.sh:90-94`; remedy text `stage.sh:695`: "re-pin … to a merge commit that contains the WHOLE shipped
family"). The semantics (modified `stage.sh`) arrive via M. Pinning B would ALSO pass mechanically
(a commit is an ancestor of itself) but would misrecord provenance — the stamp must name where the
scripts semantics landed, and B is a version-bump commit. Current values verified identical:
`087f782afbf1957dd0bf6acea5198314093d2a89` at both sites — old floor disclosed at ceremony.

**Gate-by-gate verification of the sequence (merge M → bump B advances both constants → tag v0.30.3 at B → stage from clean worktree at B):**
- Floor gate: value read from the BUILT tree's own `upgrade_tools.py` (`stage.sh:644-645`; `REPO_ROOT` =
  staging tree root, `stage.sh:106`) → B's new constant is what is validated; ancestry runs
  `git -C <built-tree> merge-base --is-ancestor <floor> HEAD` (`lib.sh:772` via `stage.sh:646`) → M is an
  ancestor of B ✓. Refusal shape: `_warn` + `rm -rf "$STAGE_TMP"` + exit 78 (`stage.sh:646-649`).
- Stamp W-2: REV from the built tree's `lib.sh` (`stage.sh:665-666`); must be 40-hex (`lib.sh:729`),
  resolve in tree (`:733`), EQUAL floor (`:737`) → both = M ✓ (`_pipeline_scripts_stamp_verdict` spans
  `lib.sh:727-742`; the plan's `:707-738` was the doc-comment-inclusive span).
- Coverage: existence-only `cat-file -e "<stamp>:<rel>"` (`executor_family.sh:188`); `stage.sh` and
  `lib.sh` are already family members (`executor_family.sh:76`, `:72`) → content edits do NOT trip it;
  adding guidelines to `executor_family_paths` WOULD trip it (`stage.sh:688-698`) — the plan correctly does not.
- Exact-tag gate `stage.sh:189-193` fires before tip-identity (`:195-206`) before build (`:208+`) before
  floor/stamp/coverage (`:627-699`); ordering is not load-bearing (same tree state evaluated throughout).

**Sharpening 1 (correction — the push is load-bearing, not audit trail).** NO gate compares floor/REV to
`origin/latest` — but the L1 tip-identity gate DOES bind the staging tree HEAD to the integration tip:
`_verify_tip_identity` (`lib.sh:4015-4045`, tip via `_tip_sha` `lib.sh:3525` = `origin/latest` with local
`latest` fallback) refuses `non-tip-tree` unless HEAD == tip (`:4032-4042`). Therefore the recipe's step-1
"Push `latest`" is mechanically REQUIRED before stage (or the journaled `--allow-stale-stage` override,
`stage.sh:149-156`/`lib.sh:4026-4031`). Release-recipe step 1 must drop the "(audit trail)" framing.

**Sharpening 2 (bump-commit file set — disclose).** The bump commit carries 4 version files + CHANGELOG +
the 2 constant files. This deviates from the 4-file bump invariant and is SANCTIONED by the cadence text
itself (`stage.sh:633-641`: "the release bump … writes the constant") — disclose the 6-file+CHANGELOG bump
in the ceremony record.

**Scripts-pin worktree: does NOT apply to our stage run.** `stage.sh` never consults
`ENSEMBLE_UPGRADE_SCRIPTS_DIR`; the arm-time guard (`upgrade_tools.py:4210-4390`) binds ONLY an explicitly
pinned scripts dir and only at arm/promote time — comment `:3928-3930`: "The refusal applies EXCLUSIVELY
to an explicitly pinned ENSEMBLE_UPGRADE_SCRIPTS_DIR"; refusal is downgraded to warn when the pin env is
unset (`:3943-3945`). Recipe step 7's NEXT-ceremony consequence is CONFIRMED: after the floor advances to
M, the operator's pinned worktree must move to ≥ M or the next arm refuses `stale-scripts` (`:4360-4367`);
escape `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` is warn-only (`:4259-4266`); `floor-unset` refuses (`:4343-4351`);
a non-git pinned dir with a stamp ARMS (`:4380-4385`).

**rollback_safe (adjacent, confirmed):** `ENSEMBLE_ROLLBACK_SAFE=1` explicit is correct and legitimate —
tag-to-tag delta is DDL-empty; unset+contract-phase would refuse exit 78 (`stage.sh:382-387`); the
full-history grep always fires `contains_contract_phase=true` (`:358-363`); legitimacy rule verbatim at
`:379-381`.

---

## Q3 — DEV FALLBACK SHAPE: repo-path fallback inside the fork — BINDING

**Ruling:** dev serves `<repo>/docs/guidelines/` via the dev branch (`Path(__file__).resolve().parents[2] /
"docs" / "guidelines"` from `daemon/tools/guidelines.py`; same-depth precedent `agent_mother.py:143`).
NOT override-only, NO env var.

Rationale: (i) it is the uniform runtime-lane house shape — all 7 fork sites use repo-path dev fallbacks,
zero env knobs exist (Q1c census); (ii) the tool must be usable/testable in dev without staging — plan
success criterion 1 literally calls the tool in dev against the 5 files; (iii) the fixed-design invariant
is LIVE-scoped ("no repo-path reads at runtime in live") and is enforced by the fork shape itself; (iv)
simplest testable form — two lines in the `else` branch, exercised both via natural dev runs and via
`base_dir` injection. Binding constraint (restated): the repo path is reachable ONLY from the non-frozen
branch; no unconditional fallback; no except-path that falls back to repo paths under frozen.

---

## Q4 — KILL-SWITCH: NO — BINDING (recorded per the Emperor's pre-ruling)

No kill-switch. Rationale: (i) pure read surface — no writes, no ledger, no external effects; DR-3-spirit
switches protect surfaces with side effects (the notify-user knob protects a SEND surface; there is
nothing here to kill); (ii) the surface is already doubly gated — privileged default-deny strip
(`instance.py:7633`, filter `:7665-7668`, live-constant by design `:7646-7653`) plus the in-body frozenset
authority gate (Q1/sanity iv); per-agent revocation already exists as a config-level off (remove the
category key from the agent's `tools.allow`); (iii) the payload is the natural off-switch — a release
without the dir yields the typed refusal (Q1d), and rollback to a pre-v0.30.3 release removes the tool
entirely with the binary; (iv) an env kill-switch would mint the FIRST env knob in the runtime
payload-base lane (Q1c census) — a new config surface for zero protective value; (v) boot-safe by
construction (no factory-time dir check). Record this ruling + rationale in the ceremony notes.

---

## Sanity rulings (binding review, no re-planning)

**(i) Phases 1+2 atomic-commit SAME-PR — SOUND, not overkill.** The SAME-PR rule is the house rule, not a
planner invention: the registry pin census exists because partial landings break the suite in confusing
ways — count pins across ~12 assert sites + the memoir derivation string flip 21→22 with the entry, and
decorator-only registration is SILENTLY invisible (the extend-order pins are the only guard). A split
landing opens a window where registry=22 but pins=21 (suite red) or grants precede the category (unknown-category
allow key). One commit is the minimum correct shape; the phase split remains work-organization only.

**(ii) Stage-local re-hash leg — SUFFICIENT and the correct choice; do NOT touch lib.sh.** `integrity_verify`
loops `for tree in agents frontend` only (`lib.sh:4102` verbatim); plugins (`stage.sh:747-753`) and the
executor family (`:760-767`) ALREADY close their gap with stage-local re-hash legs — the local leg is the
established pattern for trees outside the loop, and without it the guidelines tree is integrity-verified
NOWHERE (no consumer re-hashes tree aggregates; `release_manifest.py` verifies only requested paths against
`agents_manifest`, `:113-127`). Extending the lib.sh loop would work mechanically but expands the diff into
a second family member's content for zero additional guarantee, and unilaterally closes a tracked open
escalation (`stage.sh:734-746` comment) that this feature should leave open. The corrupt-a-file negative
leg (test-matrix D5) is the proof the leg works — keep it mandatory.

**(iii) Manifest key naming — KEY NAMES APPROVED; ordinal prose and two audit rows CORRECTED.**
`guidelines_tree_sha256` joins the `*_tree_sha256` family (`stage.sh:714-717`) and `guidelines_manifest`
joins `*_manifest` (`:718-721`) — consistent with the flat-checksums convention. All consumers are
key-addressed or whole-dict: promote.sh (`:496`, `:924`), rollback.sh (`:174-177`), status.sh (`:88`,
`:123-124`), `upgrade_tools.py` `_scan_releases` whole-JSON `.get` (`:798-837`), `release_manifest.py`
agents_manifest-only with missing-key tolerance (`:22-23`). Tests: presence-only pin loop of 14 keys
(`tests/test_release_journal.sh:782-787`) — additive keys safe, no exclusivity anywhere. Insertion points
confirmed: tree sha after `:717` (mid-object, self-comma'd); map per plan after `:720` (between
`plugins_manifest` and `scripts_manifest` — mechanically fine; every non-tail heredoc line carries its own
trailing comma). See corrections C1–C3 below.

**(iv) Auth via frozenset, NO `_auth.py` entry — CONSISTENT with how privileged categories gate.**
Two-layer model verified: VISIBILITY = privileged default-deny strip (an agent reaches the category ONLY
via an explicit `tools.allow` entry; `instance.py:7639-7645` docstring, `:7665-7668` filter) + AUTHORITY =
in-body frozenset gate, first logic in the tool body, typed refusal before any IO (metrics ladder
`metrics_read.py:1404-1417`: unknown-agent → unauthorized → only-then provider/IO; fail-closed helper
`:403-410`; refusal shape `:412-419`; comment `:204-206`: "The privileged category gates VISIBILITY; this
set gates AUTHORITY. Both are required."). `_auth.py` serves a DIFFERENT mechanism — `TOOL_REQUIRED_AGENTS`
(`:36-51`, 5 entries, all agent-backed) expands `team_members` when a category is declared (consumed only
at `registry.py:117`); the existing non-agent-backed privileged categories (notify-user, metrics-read)
have NO entry — a guidelines entry would be inert-to-wrong. D3 stands.

---

## Corrections the developer MUST apply (truth-in-docs; seam rulings unaffected)

- **C1 — ordinal.** "7th top-level tree sha" is wrong under both counting conventions: with
  `scripts_tree_sha256` already present (`stage.sh:717`), `guidelines_tree_sha256` is the **5th tree sha
  and 8th sha256-suffixed key**. Fix at: plan-overview.md:18, success criterion 7, release-recipe.md step 6,
  phase3-plan.md T5. Reword to "a guidelines tree sha alongside agents/frontend/plugins/scripts" (safest —
  no ordinal to go stale).
- **C2 — consumer-table row status.sh.** "zero manifest refs (grep-verified)" is FALSE: status.sh reads
  `rollback_safe` (`:88`) and `manifest_path` + `binary_version` (`:123-124`), all key-addressed. Verdict
  (tolerated) unchanged; fix the row (phase3-plan §Consumers; research §B).
- **C3 — tests row.** Replace "ONE tangential hit … no test pins the manifest key set" with: presence-pin
  loop `tests/test_release_journal.sh:782-787` (14 keys, presence-only, additive-safe) + hits at
  `tests/packs/preflight_watchdog_test.sh:141-145`, `tests/test_stage_plugins_tree.sh:247-269/:357`,
  `tests/test_executor_family_payload.sh:318`; `tests/test_bind_retry.py:149` tangential. No exact-set
  rejection anywhere.
- **C4 — release-recipe step 1.** Drop "(audit trail)" from the push: the push of `latest` is REQUIRED by
  the L1 tip-identity gate (`stage.sh:195-206`, `lib.sh:4015-4045`) before stage, unless the journaled
  `--allow-stale-stage` override is used.
- **C5 — release-recipe step 2 (disclosure duty).** Note the bump commit's sanctioned file set: 4 version
  files + CHANGELOG + the 2 floor-constant files (sanctioned by `stage.sh:633-641` cadence; disclose at ceremony).
- **C6 — anchor fixes in plan prose.** metrics gate ladder = `metrics_read.py:1404-1417` (not `:222-227`,
  which is the token-constants region; tokens at `:224-226`); `*_if_available` wrappers live in
  `daemon/tools/instance.py:2684-2964` (notify at `:2897`), not module-adjacent; prompt-assent extend block
  = `instance.py:7105-7110` (not `:7102-7107`); `load_release_manifest(release_root)` takes the ROOT and
  joins `manifest.json` internally (`release_manifest.py:96/:103` — research §C.2's `:747` phrasing
  implied a path argument); `_pipeline_scripts_stamp_verdict` = `lib.sh:727-742`.

## Optional, non-binding (🟢 — take or route, never block on these)

- 🟢 Add `guidelines_tree_sha256` + `guidelines_manifest` to the presence-pin loop at
  `tests/test_release_journal.sh:782-787` (rides the Phase 3 change set if taken).
- 🟢 While editing the adjacent block, fix the stale IN-FILE anchor `stage.sh:735` ("lib.sh:2424" → the
  real loop at `lib.sh:4102`) — comment-only.
- 🟢 Registry-anchor future note: if any future feature needs the registry anchor, promote a PUBLIC
  accessor for `_agents_dir` first (`registry.py:622/:630` is private; tests currently bind the private attr).

## Flags for the councils (none blocking)

1. **Standing open escalation, unchanged:** `integrity_verify` (`lib.sh:4102`) covers only agents+frontend;
   plugins/scripts (and now guidelines) are verified ONLY stage-locally — promote/rollback-time integrity
   for those trees is absent. Pre-existing (`stage.sh:734-746` comment); this feature deliberately does
   not expand or close it. A future lane may want the loop extended or a promote-time re-hash.
2. **In-file comment anchors drift** (stage.sh:735 citing lib.sh:2424): symptom class worth a sweep
   whenever scripts/upgrade is next touched.
3. **No public accessor for the registry's resolved agents dir** — private-attr reach-ins are spreading
   (tests bind `_agents_dir`); promote an accessor before the next consumer.

## Evidence appendix (decisive anchors, verified @ eb85d99a7)

| Seam | Anchor | Decisive fact |
|---|---|---|
| Frozen fork ×7 | loader.py:445-448; manager.py:4815-4818; registry.py:1580-1583; graph.py:11044-11047; api.py:168-171; routers/agents.py:19-22; agent_mother.py:140-143 | frozen = `Path(sys.executable).parent` = release root; dev = `__file__`→repo |
| Frozen topology | upgrade_tools.py:455-457 | install = `binary.parent.parent.parent` |
| Anti-pattern | plugin_registry.py:64; manager.py:3860-3872; scan_plugins_root:456-461 | unconditional `__file__`; absent dir = silent EMPTY, boot continues |
| Registry anchor | prompt_refresh.py:660/:745; registry.py:622/:630 | yields same dir frozen; accessor private |
| Always-construct | instance.py:2912-2913 | factory constructs; typed refuse at call |
| Privileged strip | _tool_registry.py:446 (21 members, prompt-assent :822); instance.py:7633/:7646-7653/:7665-7668 | default-deny; live constant; no strip edit needed |
| Authority gate | metrics_read.py:204-209/:403-410/:1404-1417 | frozenset + fail-closed + gate-first ladder |
| _auth.py scope | _auth.py:36-51; registry.py:117 | agent-backed spawn expansion only |
| Floor gate | stage.sh:627-651/:644-649; lib.sh:746-777 (:772 ancestry, :773 text) | floor from BUILT tree; ancestor-of-HEAD; exit 78 |
| Stamp W-2 | stage.sh:653-672; lib.sh:727-742 (:729 40-hex, :733 resolves, :737 ==floor) | D6-8 travel-together enforced |
| Coverage | stage.sh:674-699; executor_family.sh:64-81/:188 | existence-only; stage.sh:76 + lib.sh:72 members |
| Cadence duty | stage.sh:633-641; lib.sh:90-95 | bump writes the constant; both = 087f782a… today |
| Tip identity | stage.sh:189-193 (tag), :195-206; lib.sh:4015-4045/:3525 | staging HEAD must equal origin/latest → push REQUIRED |
| Arm guard | upgrade_tools.py:4210-4390 (:4343-4351, :4352-4354, :4360-4367, :4259-4266, :3928-3945) | pins OPERATOR's scripts dir at next arm; not our stage |
| integrity_verify | lib.sh:4051-4129 (:4102 `for tree in agents frontend`) | only agents+frontend; plugins :747-753 / family :760-767 local legs |
| Manifest keys | stage.sh:701-723 (trees :714-717; maps :718-721; :721 tail, comma-less) | 4 tree shas + 3 scalar shas today; guidelines = 5th/8th |
| Consumers | promote.sh:496/:924; rollback.sh:174-177; status.sh:88/:123-124; upgrade_tools.py:798-837; release_manifest.py:22-23/:53/:96/:103/:113-127; test_release_journal.sh:782-787 | key-addressed / whole-dict / presence-only → additive keys safe |
| rollback_safe | stage.sh:356-394 (:382-387 refuse; :379-381 legitimacy) | =1 explicit + DDL-empty delta = correct adjudication |
| Injection | test_release_manifest.py:38-53; test_prompt_refresh_primitive.py:265-298 | explicit arg first; monkeypatch second; no env |
