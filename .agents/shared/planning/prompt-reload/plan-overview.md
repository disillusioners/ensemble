# prompt-reload — Plan Overview

Commission: privileged, **Ari-only** tool `prompt_reload` — Ari refreshes its own system prompt mid-session from the active release, without restart or session loss. User-approved 2026-11-06. Plan set authored by the plan-creation worker against the survey (F1–F8) and empirical probe (P1–P7) findings, within leader rulings R1–R9.

**Binding basis:** all F/P findings below are BINDING FACTS — verified by the survey and live empirical probe phases (dev daemon :8079, throwaway PG, stub LLM). This plan designs ON TOP of them; it does not re-litigate them. Anchors re-verified line-exact in this worktree (`feature/prompt-reload` @ `7426e0d8a`, `daemon.__version__ 0.29.0`); where the survey tree's line numbers drifted, the worktree-exact anchor is authoritative and the survey value is noted.

---

## 1. Objective

Deliver a typed, gated manager primitive (`refresh_instance_prompt`) + an Ari-only `prompt_reload` tool that: resolves the calling instance's agent dir via the spawn-path registry, validates it (self-env-aware + manifest verification), recomposes the prompt through the SAME loader the spawn/restore path uses (single source of truth), diffs old vs new at the loader level, evicts the cached graph with the probe-validated **bare pop** seam so the next dispatch cold-restores onto the new persona, and returns an abbreviated diff + provenance report. Docs disclose next-turn semantics and provider prompt-cache invalidation (accepted).

## 2. Scope

- In scope: manager-level refresh primitive; `prompt_reload` tool (args: `force?: bool` only); new privileged category `prompt-reload` (ari-exact, three-layer gate); category/pin ceremony incl. `tool-categories.json` regeneration via the house generator; fingerprint idempotence + diff/provenance reporting; typed refusal ladder; tests/pins (the tester-gate contract); docs + disclosures.
- In scope (explicitly NOT wired): the primitive is designed so the compaction path MAY call it later (R8) — compaction integration itself is out of scope.
- Out of scope: see Non-goals.

## 3. Non-goals (verbatim from the commission)

- Fixing F7 defects
- Compaction wiring (primitive callable — that's all, R8)
- Cross-agent reload
- Any change to `load_and_cache_prompt` semantics beyond what the tool needs to call it
- Touching `current` symlink logic
- UI changes
- Staged-release/promotion mechanics (house flow owns that; the lane stages later, the user promotes)

## 4. Binding findings — survey (F1–F8)

| ID | Fact (binding) | Anchors (worktree @ 7426e0d8a) |
|----|----------------|-------------------------------|
| F1 | Assembly is HYBRID: the persona is a Python `str` captured in a closure cell of the compiled graph; NEVER persisted to the checkpoint. Dynamic context blocks ARE checkpointed HumanMessages — NOT this tool's target. | Closure capture: `create_agent_node(llm_with_tools, system_prompt, …)` daemon/graph.py:8209 (param :8210); prepend inside the node: graph.py:8344 (`full_messages = [SystemMessage(content=system_prompt)] + list(messages)`); node returns only `{'messages': …}` (graph.py:8442 return shape) — persona discarded. Checkpoint carries zero SystemMessages (P2). Persistence fabricates a synthetic display twin: persistence.py:676-690 docstring, rebuild :910-:1009. |
| F2 | Cold restore already does the whole job: `_restore_instance` → `load_and_cache_prompt` → `_apply_post_cache_appends` → `build_instance_graph` → cache write. Nothing evicts the cached graph on a prompt change — that absence is the entire gap. | instance_lifecycle.py:4604 (`async def _restore_instance`); :4795/:4805 (`load_and_cache_prompt`); :4819 (`_apply_post_cache_appends`, def :1229); :2245/:2266 (`build_instance_graph`); :5019 cache write (`self._manager.instances[instance_id] = (graph, meta.agent_dir)`). |
| F3 | Sole per-instance holder: `manager.instances[instance_id] = (graph, resolved_agent_dir)`. | daemon/manager.py:749 (`self.instances: dict[str, tuple[CompiledStateGraph, str]] = {}`); spawn-path write instance_lifecycle.py:2474. |
| F4 | Source precedence per artifact: file baseline + per-artifact DB override (CORE: selected → latest_approved → file; MEMORY: selected → latest-any-state → file). DB side de facto inert today (0 approved CORE rows; auth disabled → prompt-versions API 503) but the tool MUST go through the same builder so a future approved DB row is honored. `load_and_cache_prompt` has a fingerprint cache (file mtimes + DB fingerprint) that self-heals — exact freshness semantics = Open Question 6. | daemon/loader.py:1339 (`def load_and_cache_prompt`); CORE resolution loader.py:436-441; DB fingerprint `_compute_db_versions_fingerprint` loader.py:183-208 (missing repo → `""` convention); fingerprint fold into mtimes loader.py:1452-1464 (synthetic key `__prompt_versions_fingerprint__` :1455, mtime comparison :1464); tools_note mtime in cache keys loader.py:1398-1399. Call shape (restore): instance_lifecycle.py:4808-4814 — `load_and_cache_prompt(resolved_agent_id, agent_path, prompt_cache, mcp_tool_names, version_tag=resolved_tag, is_custom_agent=…) -> (system_prompt, token_count)`. |
| F5 | Agent-dir resolution: registry anchors frozen daemons on `Path(sys.executable).parent / "agents"` (the RUNNING release — verified live: 44/44 agent dirs under releases/v0.29.0, 0 under /current/, 0 under ensemble-src); dev anchors on the repo. Spawn/restore use `metadata.path` from the registry. ⚠️ TENSION: the brief said "resolve via `current` symlink", but spawn parity = registry; the two can DIVERGE if the symlink flips without a restart. Recommendation: resolve via registry + REPORT divergence in provenance; validation must be self-env-aware. | Registry frozen anchor daemon/registry.py:1534-1536; loader frozen base daemon/loader.py:446; manager comment + anchor daemon/manager.py:4682-4693; spawn resolution `resolved_agent_dir = str(metadata.path)` instance_lifecycle.py:1922. Reusable install-root callables daemon/tools/upgrade_tools.py: `_install_root_candidates` :395, `_canonical_install_root` :423, `_self_env_marker` :563, `_resolve_install_dir` :665, `_journal_read` :700, `_current_symlink` :880. |
| F6 | Release `manifest.json` carries per-file sha256 for all agent files but NOTHING in daemon/ or scripts/ reads it at runtime. "Refuse if the active release tree fails manifest verification" = NEW (small) verification logic; scope (ari's files only vs whole tree) = Open Question 2. | Manifest is written by scripts/upgrade/stage.sh (layout header stage.sh:9-16 — "manifest.json (ADR-004 M5 fields + D-FA4.4 checksums)"; agents copy :397; per-file map :403). No runtime reader today (grep: only writer-side + metric references). |
| F7 | KNOWN DEFECTS the tool must be honest about (do NOT fix in this lane — routed): (i) `tools_note` DB overrides are DEAD — the override hook writes key `tools_note` while the loader composes the file content under key `tools`; (ii) `user.md`/`growth.md` never enter any prompt (Ari's 4 approved `user` MEMORY rows are write-only). Consequence: the tool's diff/report must NOT claim these artifacts' DB overrides take effect. | (i) `_HOOK_ARTIFACT_KEYS` contains BOTH `tools_note` and `tools` loader.py:98-118; hook gate loader.py:405; CORE override write `out[artifact] = resolved` loader.py:436-441; file→`prompts["tools"]` loader.py:943-950 ("Load tools_note.md with fallback to tools.md"); consumer reads `prompts["tools"]` only loader.py:1022-1024. Executed proof exists (survey). (ii) loader.py:100-118 comment block ("``user.md`` / ``growth.md`` are not in the loader's prompt-key map — they NEVER appear in the loader's ``prompts`` dict"). |
| F8 | Display divergence: the FE-visible synthetic system message can lag/diverge from the closure persona after a file edit (observed 1-of-3 runs). The tool must not promise display parity. | persistence.py:910-1009 (synthetic rebuild + skip guard :1005-1009). |

## 5. Binding findings — empirical probe (P1–P7)

| ID | Fact (binding) | Consequence |
|----|----------------|-------------|
| P1 | Two compiled graphs over one thread_id + one checkpointer hand over cleanly for COMPLETED turns (state coherent `next=[]`, no duplication; 10-turn round-robin stable). Mid-turn handover safe when node sets match (our case: same-code rebuild); divergent node sets silently drop the pending node (code-drift hazard — NOT our case: we only evict, never swap mid-turn). | Evict-only design is safe; state the hazard as a disclosure. |
| P2 | Persona byte-identical across turns; checkpoint carries ZERO SystemMessages. F1 confirmed at rest and in flight. | Loader-cache persona == closure persona in the normal case → exact diff possible (R3 tier b). |
| P3 | **BARE POP IS THE SEAM — VALIDATED:** `manager.instances.pop(instance_id, None)` — in-flight turn survives (holds its own graph ref; REST probes 200; no re-cache at turn-end), next dispatch cold-restores (~1 s) and the new persona carries; history intact; no error. **Effect lands on the NEXT TURN.** | The mechanism (R1). Next-turn semantics is a disclosure + breadcrumb (R7/D10). |
| P4 | `_release_cached_instance` is a PROVEN ZOMBIE-MAKER (its pause path returns `paused:True` while the turn completes into the paused instance's checkpoint) — categorically forbidden. | Hard exclusion; a pin forbids its use in the primitive. (manager.py:7166-7187 — note it also `_cleanup_instance_state` + cancels requests with `SESSION_TERMINATED`: exactly the harm.) |
| P5 | Tuple-swap works but is REJECTED as the primary seam: `build_instance_graph` needs 23 arguments reaching into ~9 manager internals (incl. a `raw_saver` unwrap trap). Out-of-band rebuild would duplicate wiring and drift. | The tool NEVER builds a graph (R1). |
| P6 | Revive/`send_message` alone is a no-op (negative control proven). | Not a fallback path. |
| P7 | Ranking: ① bare pop → ② DB-versioned edit + pop → ③ tuple swap → ✗ release → ✗ revive-alone → ✗ closure-cell mutation. | Design = ①. |

**Additional planning-phase fact (worktree-verified, load-bearing for the diff design):** `_apply_post_cache_appends` (instance_lifecycle.py:1229) appends runtime/project content (context-key, timezone, language, platform context, auto-loaded skills, allowed models, injection-defense instruction) AFTER the cached load, explicitly "keeps project-scoped and runtime content, including language and skill changes, out of the prompt cache so those changes do not invalidate it" (docstring :1244-1247). **Consequence:** the live closure persona = loader-cached persona + appends. The changed/unchanged decision AND the R3 persona diff must operate on the **loader-cached persona only** (the artifact-derived portion). Diffing the appended persona would produce false deltas from runtime state that the reload does not change and that the next cold restore re-appends fresh anyway. → D5.

## 6. Leader rulings (R1–R9 — binding; plan WITHIN these)

| ID | Ruling | Where honored |
|----|--------|---------------|
| R1 | Mechanism = bare pop. Tool: validate → (re)build prompt via the standard loader path → compare → if changed (or force) `manager.instances.pop(own_instance_id, None)` → return diff+provenance. No graph building; no `_release_cached_instance`; no closure mutation; no tuple swap. | Phase 1 (T1.7), Phase 2 (T2.1); P4/P5 exclusions pinned in Phase 4. |
| R2 | Single source of truth: composition ONLY via `load_and_cache_prompt` (or its exact helpers). No own .md-reading/assembly. Per-FILE hashing for the provenance report = reporting, allowed. | D4 + D5; Phase 1 T1.5, Phase 3 T3.3. |
| R3 | Diffing old vs new: closure introspection FORBIDDEN. Evaluate loader-cache-based diff (exact when cache == closure — the normal case, P2) + honest fallback when the old string cannot be proven. Consider per-file sha256 structural alternative. | D5 (three-tier strategy); Phase 3. |
| R4 | Self-only scope: reloads THE CALLING instance's own agent prompt (factory-captured agent_id/instance_id; never caller-supplied; no cross-instance parameter). | D9; Phase 2 T2.1/T2.2. |
| R5 | New privileged category (e.g. `prompt-reload`), allow-list ari EXACTLY, three-layer gate incl. in-body factory-captured agent_id (notify-user precedent). `tool-categories.json` regenerated via the house generator, never hand-edited. Pin ceremony per house rules; privileged pin census updated. | D8; Phase 2 T2.2–T2.4. |
| R6 | Guardrails: idempotent no-op on unchanged persona (fingerprint-equal → clear message, NO pop); optional `force=true` re-emits; typed refusal ladder (dir validation self-env-aware, manifest verification, loader/DB errors fail-closed). Never write conversation history; never touch other instances. | D6 + Phase 1 T1.1; Phase 4 leg 3. |
| R7 | Breadcrumb: evaluate tool-return-as-breadcrumb (leader leaning: tool-return only, NO persistent injection) vs injected persistent note. | D10 (tool-return only); architect confirmation = Open Question 3. |
| R8 | Expose the validated reload as a manager-level primitive callable by BOTH the tool and (future, optionally) compaction. Compaction integration OUT OF SCOPE. | D1; Phase 1 T1.8. |
| R9 | Docs must note provider prompt-cache invalidation as an accepted consequence. | D7/T5.1 (Phase 5). |

## 7. Phases (with per-phase gates)

| # | Name | File | Objective | Gate (exit criterion) | Depends on |
|---|------|------|-----------|----------------------|------------|
| 1 | Manager primitive + resolver/validation | phase1-plan.md | `refresh_instance_prompt` primitive: registry resolution, self-env-aware validation, manifest verification, loader composition, fingerprint changed/unchanged decision, bare pop, typed refusals, thin manager delegate. | Primitive unit-testable against a stubbed `manager.instances` dict; validation matrix green (live/dev shapes); every refusal code typed; no-op never pops. | — |
| 2 | Tool surface + category/pin ceremony | phase2-plan.md | `prompt_reload` tool (args `force?: bool` only), three-layer gate, category registration + `tool-categories.json` regeneration + VOCABULARY_VERSION bump, privileged pin census, kill-switch. | Tool registered for ari only; non-ari agents cannot see/call it; artifact regenerated via generator and artifact pack green; pins green; prompt baseline UNCHANGED (181 rows). | P1 |
| 3 | Diff/provenance reporting | phase3-plan.md | Tiered diff (loader-cache exact / honest fallback), per-file sha256 provenance, abbreviated diff format, F7 honesty marks, F8 non-promise, breadcrumb text. | Report format frozen; honesty constraints pinned by tests; fallback path proven non-claiming. | P1 (composition), P2 (result envelope) |
| 4 | Tests/pins (tester-gate contract) | phase4-plan.md | Full enumeration of functional legs L1–L7 + mutation legs M1–M4 + pin/census legs — the tester gate consumes this verbatim. | All legs green on the dev-daemon harness; every mutation leg RED at planted mutation / GREEN at HEAD (digest-proven); zero live contact. | P1–P3 |
| 5 | Docs + disclosures | phase5-plan.md | Runbook, prompt-cache-invalidation note (R9), disclosure ledger, CHANGELOG, closeout. | Docs complete; every disclosure from §10 present verbatim; lane ready for review council → tester gates → staged release (house flow). | P4 |

## 8. Coupling map

| | P1 primitive | P2 tool/ceremony | P3 reporting | P4 tests | P5 docs |
|---|---|---|---|---|---|
| P1 | — | consumes primitive (strong, direction P2←P1) | consumes composition + fingerprint (strong, P3←P1) | exercises (strong) | documents behavior (weak) |
| P2 | | — | defines result envelope (medium) | exercises gate/ceremony (strong) | documents refusal codes (weak) |
| P3 | | | — | pins format + honesty (strong) | report sample in runbook (weak) |
| P4 | | | | — | disclosure checklist source (medium) |

Shared files: `daemon/manager.py` (thin delegate + instances dict — P1), `daemon/services/prompt_refresh.py` (NEW — P1/P3), `daemon/tools/prompt_reload.py` (NEW — P2), `daemon/tools/_tool_registry.py` + `daemon/checkpoint_metrics.py:244` + `.agents/shared/metrics/tool-categories.json` (P2 ceremony), `agents/ari/meta.json` (P2 allow entry — NOT a prompt artifact), `tests/unit/…` (P4), `docs/runbooks/prompt-reload.md` (NEW — P5).

## 9. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|-----------|
| RA1 | Registry-resolved dir diverges from `current` symlink mid-session (a promote flips the symlink without restart) | medium | Provenance reports `current_matches_running` + both versions every call (D2); validation re-runs per call (no staleness). Architect owns the policy call (OQ4). |
| RA2 | Bare-pop concurrency edge: pop racing a dispatch that is mid-cold-restore | low | P3-validated for the validated shapes; benign double-restore worst case (no corruption); allow-with-disclosure (OQ1); no extra guard in v1 unless architect rules otherwise. |
| RA3 | Loader fingerprint subtleties (mtime granularity, DB `""` convention, tools_note mtime key) cause a missed or false change detection | medium | OQ6 surfaced with anchors; Phase 4 pins the OBSERVED semantics (not aspirational); force=true is the operator escape hatch. |
| RA4 | Loader-cache persona ≠ closure persona in an edge → wrong "exact diff" claim | low | Tiered diff: exact claim ONLY when cache provenance is sound; otherwise honest fallback `persona_diff=unavailable` (D5 tier c). |
| RA5 | Whole-tree manifest verification cost/blast-radius if adopted | medium | v1 default = calling agent's files only; whole-tree behind a default-OFF strict knob (D3); architect decides (OQ2). |
| RA6 | Reports mislead about tools_note/user/growth DB overrides (F7) | medium | Honesty marks in the provenance payload + pin tests asserting the marks (T3.4, T4.x). |
| RA7 | Live contact during gates (env poison family) | high | Phase 4 discipline: dev daemon :8079, throwaway PG, per-variable env scrub (NEVER `env -u POSTGRES_*` — known silent no-op), `ENSEMBLE_SELF_ENV` sandbox pin, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned on any frozen-binary smoke; zero-live-contact fence leg (L4). |
| RA8 | `tool-categories.json` hand-edited (F-1 trap class) | medium | Generator-only rule; artifact pack + mutation leg M4 proves hand-edit → RED. |

## 10. Known disclosures (MUST appear verbatim in this set)

- **F7(i):** tools_note DB overrides are DEAD (loader.py:436-441 write key vs loader.py:943-950/:1022-1024 read key) — routed, NOT fixed here; tool reports must not claim they take effect.
- **F7(ii):** user.md/growth.md never enter any prompt (loader.py:100-118) — same.
- **F8:** FE synthetic system message can lag/diverge (persistence.py:910-1009) — no display-parity promise.
- **Next-turn semantics:** effect lands on the NEXT dispatch (P3); the tool-return breadcrumb is the only in-turn signal.
- **Provider prompt-cache invalidation on persona swap:** accepted consequence (R9).
- **Mid-turn eviction semantics (P1/P3):** in-flight turn survives on its own graph ref; no re-cache at turn-end.
- **P1 divergent-node-set hazard:** only relevant under code drift; this lane evicts, never swaps mid-turn.

## 11. Open Questions for the ARCHITECT (exactly these; caller-requested)

1. **Seam hardening for bare pop** — concurrency/edge semantics: pop during own in-flight turn is probe-validated (P3); is any additional guard warranted (e.g. refusing pop while the calling instance has an in-flight task), or is allow-with-disclosure the ruling? Plan default: allow-with-disclosure (RA2), no guard.
2. **Manifest-verification scope** — ari's files only (plan default: cost O(agent files), blast-radius small) vs whole release tree (stricter; catches foreign corruption; cost O(tree)). Plan ships the default-OFF strict knob so either ruling is config-flippable (D3).
3. **Breadcrumb ruling** — tool-return-only (R7 leader leaning; plan default D10: no persistent injection, no history write) vs injected persistent note (context cost, pollutes history). Confirm or overrule.
4. **Current-vs-running release tension** — registry resolution (spawn parity; plan default D2) vs `current` symlink resolution. Plan resolves via registry and REPORTS `current_matches_running` + both versions in provenance. Confirm registry-first, and rule whether a detected divergence should (a) proceed (plan default), (b) warn-only, or (c) refuse.
5. **Refusal vocabulary** — typed enum shape. Plan drafts a 10-code set in the notify-user REFUSED envelope house shape (`Error: prompt_reload REFUSED — reason=<code>: <message>`, notify_user.py:314-321) — see D6; architect finalizes codes/naming (align with the notify-user 12-code D9 pattern).
6. **Loader fingerprint freshness semantics** — F4 cache self-heal: fingerprint = per-file mtimes (incl. tools_note.md, loader.py:1398-1399) + synthetic `__prompt_versions_fingerprint__` from the DB rows (loader.py:1455, `_compute_db_versions_fingerprint` :183-208 with the missing-repo → `""` convention :207-208); on any change the cache-hit step busts and recomposes (:1464 comparison). Subtleties to rule on: mtime granularity vs sub-second edits; whether a DB-row CONTENT change with unchanged fingerprint shape is possible (TOCTOU window closed per loader.py:318-320 comments — verify at implementation); behavior when the prompt-versions repo ERRORS mid-call (plan: typed fail-closed refusal, not the `""` convention — `""` is for an absent repo, not a failing one).
7. **R3 honest-diff fallback shape** — when the old persona cannot be proven (cache entry absent/divergent): plan default = composition+provenance-only with `persona_diff=unavailable` (D5 tier c), with per-file sha256 before/after always present. Alternative considered: file-level structural diff claims. Architect picks the default claim policy.

## 12. Success criteria (measurable)

- L1 sentinel E2E (dev daemon :8079 harness): tool returns diff+provenance; the CURRENT turn completes on the old persona; the NEXT dispatch carries the sentinel; message count unchanged (no duplication).
- No-op: fingerprint-equal → clear "no changes" message AND the `manager.instances` entry still present (asserted); `force=true` pops even when unchanged.
- Every typed refusal code proven by an independent test (L3 matrix).
- Live-fence proof: self-env=live requires resolved dir under `<install>/releases/`; dev accepts the repo path; zero live contact across the whole gate.
- Mutation legs M1–M4 each RED at the planted mutation, GREEN at HEAD (digest-proven).
- `tool-categories.json` regenerated by the house generator only; VOCABULARY_VERSION artifact-match green; privileged pin census updated.
- Prompt-artifact baseline unchanged: `agents/.prompt-artifacts.sha256` stays 181 rows, `--check` clean (this lane adds NO agent prompt files — see D7).
- Zero live contact during all gates.
