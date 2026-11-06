# prompt-reload — Architecture Recommendation (Architect Rulings)

- **Date:** 2026-11-06
- **Worktree:** `/home/nea/ensemble-src-wt-prompt-reload` @ `364b8254e` (`feature/prompt-reload`, daemon 0.29.0)
- **Basis:** plan set (`plan-overview.md` §4–§12, `decisions.md` D1–D21, `phase1..5-plan.md`); two read-only analysis passes (structural + resilience), all anchors verified line-exact in THIS worktree; live manifest verified against `/home/nea/agents-ensemble/releases/v0.29.0/manifest.json`.
- **Status:** Rulings on the 11 open questions + amendment register. R1–R9 NOT reopened; the mechanism (bare pop, loader-only composition, self-only scope, ari-exact gate) is CONFIRMED as planned. Where this document says "amend", the plan's default stands unless the named file is edited per §3.

---

## 0. Method

Two parallel read-only analyses: (A) mechanism/seam cluster (Q1/Q2/Q4/Q9/Q10) against `manager.instances` mutation-site census, manifest.json real shape, registry/upgrade-tools anchors, service-module precedents; (B) contract/failure cluster (Q3/Q5/Q6/Q7/Q8/Q11) against loader fingerprint internals, notify-user envelope/kill-switch/lock precedents, compaction semantics, refusal-vocabulary census. Overlaps adjudicated by the architect; conflicts resolved in §2 (notably Q5: final vocabulary is a one-for-one swap, not either worker's count).

---

## 1. Rulings at a glance

| # | Question | Verdict | vs plan default |
|---|----------|---------|-----------------|
| Q1 | Seam hardening for bare pop | **Allow-with-disclosure, NO guard**; disclosure in BOTH tool return (`manager_instances before→after`) and one INFO log line | Confirms RA2, adds the two disclosure surfaces + no-guard pin |
| Q2 | Manifest scope / reader / absence | **Loader-read-set scope** (not `agents/ari/**`); reader in NEW `daemon/services/release_manifest.py`; dev → skip; live AND sandbox → verify; torn JSON → new refusal code | **Amends D3/T1.4** (scope defect); confirms the rest with placement + sandbox rulings |
| Q3 | Breadcrumb | **Tool-return only** + fixed marker line `[PROMPT-RELOADED outcome=<o> next_turn=true]` | Confirms D10; adds the marker |
| Q4 | current-vs-running divergence | **Proceed + structured report; NEVER refuse**; `current_matches_running` is **three-valued** `true\|false\|unknown` | Confirms D2 proceed; refines the field to tri-state + no-refusal pin |
| Q5 | Refusal vocabulary | **Final = 10 REFUSED codes** — drop `not_ari` (→ notify-exact `ERROR:` envelope), add `manifest_unreadable` | **Amends D6** (one-for-one swap; the draft's count was right by accident) |
| Q6 | Fingerprint freshness | **Hash-based changed/unchanged decision MANDATORY** + `PromptCache.invalidate` stale-cache bust; tool probes the prompt-versions repo itself; DB TOCTOU closed, file TOCTOU bounded one-call self-heal | **Amends D5/T1.6** (decision basis); confirms fail-closed DB posture |
| Q7 | Honest-diff fallback | **Tier-c `persona_diff=unavailable` IS the v1 contract**; file-level manifest diff REJECTED as an old-persona proxy; `no_change` claims file-level freshness, not closure currency | Confirms D5 tier-c; adds honesty wording + rejects the structural alternative |
| Q8 | Kill-switch | **`ENSEMBLE_PROMPT_RELOAD_ENABLED`, default ON, PER-CALL in-body typed refusal**; never construction-time absence | Confirms D11; fixes the gate placement (per-call, not construction) |
| Q9 | Primitive placement | **NEW `daemon/services/prompt_refresh.py` + 5-line async Manager delegate** (function-local import via `daemon.manager`; ≤10-line manager diff) | Confirms D1; pins the skeleton + the test-patch-seam constraint |
| Q10 | Concurrency | **NO lock**; claim-guard instead (mtimes skew → tier-c downgrade); escalation path documented, not built | Refines: plan was silent; ruling = no lock + M5 mutation leg |
| Q11 | Observability | **One frozen-format INFO line per tool call** (success AND refusal); no journal; no ledger; metrics family ROUTED | Adds the spec (plan was silent) |

---

## 2. The eleven rulings

### Q1 — Bare-pop seam: allow-with-disclosure, no guard

**Verdict.** No guard flag, no in-flight-restore check. Disclosure lands in BOTH the tool return (`manager_instances: <n_before> → <n_after>`) and exactly one `logger.info` line on the pop path (one on the no-pop/forced paths; nothing above DEBUG when the entry was already absent — the normal next-dispatch state).

**Rationale.** The dict (`daemon/manager.py:749`) has NO lock anywhere (only `MaintenanceRunLock` at :4413) across a 12-site mutation census (spawn write `instance_lifecycle.py:2474`, restore write :5019, terminate `del` :2810, watchover put-back :4558/:4573, `clear_all_instances` :5162, `_release_cached_instance` `del` manager.py:7182, LRU touch :7202-7204, cap enforcement :7270, TTL sweep :7818, maintenance callback :4432, `snapshot_executor.py:520` pop, `routers/projects.py:972`). Every writer is a bare dict statement on the single event loop; `_restore_instance`'s critical span 4805→5019 contains ZERO awaits and `spawn_instance` is sync (:1802) — nothing interleaves the critical regions. `pop(iid, None)` is idempotent by construction; `snapshot_executor.py:509/520` is the house-blessed pin-then-pop precedent. A guard flag would introduce a NEW failure mode (stuck flag after a refusal/early-return) to prevent a non-defect; "refuse while a restore is in flight" is unimplementable cheaply — the only in-flight signal is negative ("not in `instances`"), which is also the never-dispatched state, so the guard would refuse the very case the tool exists for. Worst-case interleave = benign double-restore (RA2), and a pop racing a restore registers a graph built from the NEW files anyway — content stays correct.

**Residual (disclosed, no action).** `_enforce_instance_cache_cap`/TTL-sweep evictions route through `_release_cached_instance` (the P4 zombie-maker), but both filter to non-active ids (`_terminal_cached_instance_ids` :7206-7232) — a live Ari turn is not evictable; this tool never calls the sweeper.

**Amendment:** AM-7 (§3).

### Q2 — Manifest verification: loader-read-set scope; new `release_manifest.py`; sandbox = live-shaped; torn JSON refused

**Verdict.** (a) The default scope concept ("calling agent's own files") is correct BUT must be **the loader's read set for the calling agent**, not a directory glob: ari's own artifact files AND the shared `_prompt_system/{project-experience,knowledge,knowledge_no_force_explore}.md` (selected via `no_force_explore`), AND every innate-skill file declared in `meta.json` (`loader.py:880-886`), AND `memories/*.md` when present (:1423-1425), AND `meta.json` itself. The path set MUST be derived by importing/calling the loader's own resolvers — a hand-written glob is a test failure. (b) The reader lives in a NEW `daemon/services/release_manifest.py` (~120 lines, stdlib-only: `load_release_manifest(release_root) -> ReleaseManifest | None` + `verify_paths(release_root, manifest, relpaths) -> ManifestVerdict`; reads only the `agents_manifest` map; NO tree walk). (c) Absent manifest: dev → skip with `manifest=absent(dev)` provenance; **live AND sandbox → refuse** (`manifest_missing_live`); torn/invalid JSON → NEW code `manifest_unreadable` (≠ missing). "On disk, not in manifest" is NEVER a mismatch for requested-path checks (only the strict whole-tree knob, default OFF, and even there exempt `memories/` + `history/`).

**Rationale.** Verified against the real `releases/v0.29.0/manifest.json` (738 KB): `agents_manifest` is a flat `{relpath: sha256}` map, 379 entries, keys relative to `agents/`; ari contributes exactly 6 entries; 9/9 spot re-hashes match; writer is `stage.sh` heredoc key `:600` (map built `:444`/`:476-486`); NOTHING in `daemon/` reads it today (grep zero — `_scan_releases` :797 and `_target_release_state` :1965 read only identity fields). The plan's glob scope is a real under-verification hole: the shared `_prompt_system` and innate-skill files are exactly the files whose tampering changes Ari's persona, and a glob over `agents/ari/**` would report `manifest=ok` over a tampered shared file. Reader placement: `upgrade_tools.py` is 4,168 lines of upgrade-lane scope whose private-import consumers all dodge cycles by importing lazily inside functions (`promote_txn_hold.py:152`, `metrics_query/wiring/sources.py:185`) — a module-level generic verifier there cements a bad dependency direction; `prompt_refresh.py` is misfiled for the obvious second consumer (the arm-time scripts/provenance-guard family); `prompt_artifacts.py` is the write-guard with a DIFFERENT trust root (`.prompt-artifacts.sha256` baseline) and would invite confusion. A small services-layer module is the nest-ops `provenance.py` precedent applied correctly. Sandbox must verify: `_self_env_marker()` (:563) resolves frozen binaries to sandbox, and a frozen-binary sandbox IS running out of a staged release — silently skipping there voids the point of every staged boot smoke. `ari/user.md` sits in the manifest but is NOT in the loader read set (F7ii) — the derived set excludes it automatically; the verifier's path list must not imply it is prompt-affecting (D15 keeps the report honest).

**Amendment:** AM-1 (scope — the pass's highest-priority plan defect), AM-2 (reader home + T1.3b), AM-3 (absence/sandbox/torn-JSON policy).

### Q3 — Breadcrumb: tool-return only + fixed marker line

**Verdict.** Tool-return only (D10 confirmed — no persistent injection, no history write, no SystemMessage). Every SUCCESS outcome ends with exactly one fixed marker line: `[PROMPT-RELOADED outcome=no_change|reloaded|forced_reload next_turn=true]`. Refusal paths carry ZERO `[PROMPT-RELOADED` occurrences — the refusal grep target is the `Error: prompt_reload REFUSED — reason=<code>:` envelope.

**Rationale.** Compaction semantics live in `daemon/compaction.py` (NOT graph.py): AI+ToolMessage pairs form atomic MessageGroups (:1489-1580) and groups inside the compacted span are LLM-summarized into the lossy `[CONTEXT COMPACTION — …]` envelope (:963-978); the only verbatim-surviving class is HOISTED injected messages (:418-446), and hoisting is a conversation-history write R6 forbids. So injection loses on both horns: a plain injected note is compactable (defeats its purpose), and the surviving hoisted class violates the never-write-history guardrail at permanent context cost for a fact that is stale the moment it is consumed (the persona is live next turn). Compaction erasing the tool return later is ACCEPTABLE — after compaction the reload is no longer news; provenance lives in the runbook + daemon log (Q11), not in history. The marker line gives the model and the pins a stable, greppable in-turn anchor whose outcome token must equal the structured `PromptRefreshResult.outcome` (drift = pin failure).

**Amendment:** AM-4.

### Q4 — current-vs-running divergence: proceed + structured tri-state report, never refuse

**Verdict.** Registry resolution (spawn parity) stands; divergence is REPORTED only. `current_matches_running` is **three-valued** — `true | false | unknown` — where `unknown` is a first-class value emitted whenever `_current_symlink` returns `(None, "")` (no install dir / not a symlink / dev) or a non-empty note (dangling link). `running_release` is derived only when `sys.frozen` AND `Path(sys.executable).parent.name` matches `^v\d+\.\d+\.\d+$` (else `null`); `current_target` is `null` not `""` when unresolved; `daemon_version` is a SEPARATE field (it is the build version and can disagree with both). No refusal is reachable from divergence; the tool return carries one fixed disclosure line for `false` and a DIFFERENT fixed line for `unknown` (so the tester pins both and neither silently collapses).

**Rationale.** The resolved dir is right BY CONSTRUCTION: spawn/restore feed the same registry `metadata.path` into the loader (`instance_lifecycle.py:1922`, :4795; frozen anchor `registry.py:1534-1539`; loader frozen base `loader.py:446`). Refusing would deny the exact operation that fixes the situation, in the three scenarios where divergence actually occurs — a non-restart symlink flip, a manual flip, operator surgery — all of which have the operator already in the loop. And the common case is benign: a promote RESTARTS the daemon (that is what the promote transaction exists for), so a live divergence cannot be produced by a normal promote. The `(None, "")` conflation inside `_current_symlink` (:880-896, :899) is the one real trap: reporting it as `false` would claim divergence where there is merely no install dir (dev) — hence `unknown`. `daemon.__version__` measures the BUILD, `exe` parent names the RUNNING release, `current` names the INTENDED one — three distinct quantities that must never be collapsed into one boolean.

**Amendment:** AM-5.

### Q5 — Refusal vocabulary: 10 REFUSED codes via a one-for-one swap

**Verdict.** Final REFUSED vocabulary (envelope `Error: prompt_reload REFUSED — reason=<code>: <message>`, `notify_user.py:314-316` exact shape):

| # | Code | Definition (one line, freeze in runbook + constants) |
|---|------|----------------------|
| 1 | `prompt_reload_disabled` | kill-switch OFF (`ENSEMBLE_PROMPT_RELOAD_ENABLED`) |
| 2 | `prompt_reload_agent_dir_unresolved` | registry miss / `metadata.path` absent for the caller's agent |
| 3 | `prompt_reload_dir_invalid` | dir resolved but fails self-env containment (live/sandbox → under `<install>/releases/…`; dev → repo `agents/`), incl. unresolvable self-env marker |
| 4 | `prompt_reload_manifest_missing_live` | live/sandbox-shaped release with NO manifest.json |
| 5 | `prompt_reload_manifest_unreadable` | manifest present but torn/invalid JSON (NEW — ≠ missing) |
| 6 | `prompt_reload_manifest_mismatch` | per-file sha256 mismatch against the loader-read-set scope |
| 7 | `prompt_reload_agent_meta_invalid` | `meta.json` unparseable/structurally invalid — MUST stay distinct: the loader PAPERS OVER corrupt meta (`loader.py:1376-1383` → `meta=None` + WARN → silently skill-less persona); this check turns that into a refusal |
| 8 | `prompt_reload_compose_failed` | `load_and_cache_prompt` raised (incl. fingerprint-computation failures) |
| 9 | `prompt_reload_db_unavailable` | the tool-side prompt-versions repo probe RAISED (message must name the prompt-versions repo explicitly — see Q6b; NOT "the database") |
| 10 | `prompt_reload_internal_error` | unexpected exception; FAILED envelope shape (`:319-321`) |

**The swap.** DROP `prompt_reload_not_ari` from the REFUSED vocabulary: the in-body layer-3 non-ari check returns the notify-exact generic envelope `ERROR: prompt_reload is available to the ari agent only (caller: <id>).` — precedent `notify_user.py:703-711`, which deliberately does NOT use a typed REFUSED code for the authorization-error class. A code that never appears in a REFUSED envelope is not honestly pinnable; the guardrail itself is untouched (three-layer gate intact, M3 mutation leg still applies — it pins the `ERROR:` shape). ADD `prompt_reload_manifest_unreadable` (Q2): torn JSON is a different failure with a different operator remedy than absence, and the draft set had no slot for it.

**Completeness rulings.** No `not_running_agent`/instance-not-in-cache code — a bare `pop(id, None)` on an uncached instance is a correct no-op and composition is identical to the next restore; report a `was_cached=false` provenance flag, never refuse. No fingerprint-specific code — fingerprint computation failures fold into `compose_failed`; the DB leg owns `db_unavailable`. Self-env marker unresolvable → `dir_invalid` with a message naming the marker failure (no 11th code). So: is 10 honest? Yes — but not the drafted ten; the draft's count was right by accident (one member could never appear, one real failure mode was missing). Underscore+prefix naming is safe: no cross-tool regex parses reason tokens anymore (instance_ops returns its token STRUCTURALLY, `instance_ops.py:212-221`); grep confirms zero `prompt_reload` collisions in `daemon/`.

**Amendment:** AM-6 (phase1 D6, phase2 D8, phase4 L3(e) pin retarget).

### Q6 — Fingerprint freshness: hash-based decision mandatory; tool probes the repo; TOCTOU status stated

**Verdict.** (a) The changed/unchanged decision is **tool-side per-file sha256 over the loader's persona-bearing read set** (the same frozen set AM-1 derives; module-frozen constant asserted equal to the loader's keys by pin) — NOT the loader's mtime+fingerprint inputs. When hashes differ while the loader fingerprint is unchanged, the loader cache is provably stale: the primitive calls `PromptCache.invalidate(agent_id, mcp_tool_names, version_tag)` (`loader.py:1326-1337`, public seam) BEFORE composing, and reports outcome `reloaded` with provenance flags `hash_mtime_divergence=true` + `cache_busted=true`. (b) The primitive runs its OWN prompt-versions repo probe (`repo.get_effective_rows(agent_id, version_tag)` in try/except) BEFORE composition: probe raises → `prompt_reload_db_unavailable` (fail-closed, NO composition — composing file-only when the running persona carried DB overrides would silently downgrade it); repo absent/None → proceed with `db_fingerprint=absent`; rows present → fingerprint computed and reported. (c) TOCTOU: DB side CLOSED (fingerprint and content share one SELECT — A8 comment `loader.py:318-321`, rows threaded :1448 → :1481-1485); file side NOT closed but CONVERGENT (mtimes snapshotted before the content re-read; `cache.set` stores the PRE-read snapshot :1507 vs re-read :1471 — an edit inside the window leaves the cache holding new bytes + old mtimes, and the NEXT call compares unequal and reloads). Pins observe convergence (two consecutive calls), not absence.

**Rationale.** mtime equality is not byte-freshness on two independent grounds: float `st_mtime` sub-tick races AND mtime-preserving restores (`cp -p`/`rsync -a`/backup rollback — a realistic operator scenario, not a theoretical race). Critically, `force=true` does NOT cure a stale loader cache: the loader's own cache-hit (`:1466`) serves the stale persona on the NEXT RESTORE too — pop is useless while the cache lies, so busting the cache is a correctness requirement, not polish. The decision-side hashing is FREE: the plan already computes per-file sha256 for provenance (R2-allowed reporting, T1.5/D14). And the loader cannot be the source of DB-failure truth: `_fetch_effective_rows_safe` (:237-259) FAILS OPEN on ANY exception (WARN → None → `""`, the same fingerprint as an absent repo, `return ""` :210) — so fail-closed-on-DB-failure (R6) is only achievable with a tool-side probe. Residual disclosed: a repo failure between probe and compose still fail-opens inside the loader (sub-second window; closing it needs loader changes = out of scope per non-goals).

**Amendment:** AM-8 (hash decision + bust), AM-9 (repo probe + pin legs L-a…L-d), AM-14 (mcp_tool_names resolution parity — see below).

### Q7 — Honest-diff fallback: tier-c is the v1 contract; file-level manifest diff rejected

**Verdict.** D5 tier-c IS the v1 contract: when the old persona cannot be proven (cache miss/evicted/provenance unsound), return `persona_diff=unavailable` + full new composition + provenance, and make NO structural claim. The file-level structural alternative is **rejected as an old-persona proxy**: a manifest-referenced "old" proves drift-since-release of the staged file tree, NOT the persona this instance's closure was built from (the old closure may post-date the manifest via a prior reload, or include DB overrides/dynamic blocks no file hash reconstructs — `compose_system_prompt` inputs are many-to-one over file AND non-file sources, `loader.py:955-962`). It MAY return later as an explicitly labeled `release_drift` provenance section (fast-follow, optional, never phrased as a persona delta).

**Rationale.** Only the loader-cache tier-b can claim persona-level equality, and only while the shared cache is provably the one this closure consumed (P2 normal case). That exposes a wording edge the plan missed: `no_change` means "files unchanged since last cache fill", NOT "your closure persona is current" — with two instances of one agent_id sharing `prompt_cache`, instance A's reload refreshes the shared cache and instance B's call then sees fingerprint-unchanged → `no_change` → NO pop → B's closure stays stale (B needs `force=true`). ari-EXACTLY makes this theoretical today (one ari instance at a time in practice), but the claim wording must not over-reach, and the tier-b provenance guard must include the shared-cache consumer check (folded into AM-10's mtimes-skew guard — same observable).

**Amendment:** AM-10 (tier-c confirmation + wording disclosure + tier-b guard).

### Q8 — Kill-switch: per-call in-body typed refusal; never construction-time absence

**Verdict.** `ENSEMBLE_PROMPT_RELOAD_ENABLED`, default ON, **read PER CALL in the tool body** (resolver in the lean `instance_ops` direct-env style — off-spellings {0,false,no,off,disabled}, invalid → loud warn + default; one module constant holds the exact knob string, `metrics_read.py:139` pattern). A killed tool returns typed `prompt_reload_disabled` promptly; it is NEVER removed from ari's registered surface at construction time.

**Rationale.** Verified notify mechanics: the operative check is IN THE TOOL BODY (`notify_user.py:715`), the bounds resolver re-reads `os.environ` per call ("Read at CALL time so tests and operators can flip knobs without a restart", :384-402), default True (`daemon/config.py` NotifyUserConfig). Refusal-at-call strictly dominates construction-absence: (1) ari's tool surface stays stable — the T2.4/AC2.5 pins ("ari sees prompt_reload") hold under ANY switch state, whereas a construction gate would silently shrink the surface and force kill-switch-aware pins; (2) the refusal TEACHES (ari can report "the tool exists and is disabled" vs. an indistinguishable absence); (3) env flip takes effect next call, no restart; (4) the switch refusal must not depend on any DB/ledger health (notify N9 ordering). For ONE boolean the Pydantic-model machinery is overweight — `instance_ops.py:245-252` is the right resolver weight.

**Amendment:** AM-11.

### Q9 — Primitive placement: service module + 5-line async delegate (confirmed)

**Verdict.** Logic in NEW `daemon/services/prompt_refresh.py`; `manager.py` gains exactly this delegate (≤10-line diff):

```python
async def refresh_instance_prompt(self, instance_id: str, *, force: bool = False) -> "PromptRefreshResult":
    """Reload the calling instance's system prompt on its NEXT turn (R8)."""
    from .services.prompt_refresh import refresh_instance_prompt
    return await refresh_instance_prompt(self, instance_id, force=force)
```

Properties (all pinned): `async` (composition is sync-but-loop-bound; the pop must be on the loop; keeps the R8 compaction-caller shape compatible); NO lock, NO logging, NO try/except in the delegate (any lock lives in the service so tool + future compaction caller share it; delegate logging would double-log); `instance_id` positional and REQUIRED (never defaulted — a default would let a future caller omit it and reach the wrong instance; R4's no-caller-supplied-target applies to the TOOL surface, the delegate receives the already-validated self id); return type via lazy import / `TYPE_CHECKING` only (module-level import risks the manager↔lifecycle cycle). HARD CONSTRAINT: the service imports `load_and_cache_prompt` through `daemon.manager` (function-local), NEVER `daemon.loader` — preserving the house test patch seam (`tests/test_persistence.py:561`, :679; callers `instance_lifecycle.py:2097`/`:4795`, `persistence.py:1413`).

**Rationale.** manager.py is 16,364 lines at HEAD (not the plan's ~13.7k — corrected); it already delegates heavily (`get_instance` :15713 and `spawn_instance` :10778 are one-line delegates) and the service-module precedent is exact and recent (`boot_epoch.py` 154 L, `install_audit.py` 191 L, `report_delivery_ledger.py` 148 L, `promote_txn_hold.py` 575 L — all take a manager handle; `promote_txn_hold.py:145-182` is the lazy-import-tools-privates pattern to copy for `_self_env_marker`/`_resolve_install_dir`/`_current_symlink`). Overruling D1 would add ~400 lines to the monolith for zero benefit.

**Amendment:** AM-12 (skeleton + patch-seam constraint + line-count correction).

### Q10 — Concurrency: no lock; claim-guard instead

**Verdict.** NO per-agent (or any) lock in v1. The one genuine subtlety is a truthfulness risk in the REPORT, fixed by a claim guard: capture the cached tuple + its stored mtimes ONCE before composing; if the cache key's stored mtimes differ at decision time, downgrade to tier-c (`persona_diff=unavailable`) instead of claiming an exact diff. Document (do NOT build) the escalation path: a per-CACHE-KEY per-loop lock table in `prompt_refresh.py`, keyed by `(agent_id, version_tag, tuple(sorted(mcp)))` — never by agent id, never held across a turn. Runbook records the precondition: the primitive must be AWAITED on the daemon's main loop (the awaitless-atomicity argument collapses off-loop).

**Rationale.** `PromptCache` is a plain sync dict (`loader.py:1263`) with no suspension point in any method; `load_and_cache_prompt`'s read-modify-write span (:1462 stored-mtimes read → :1499 cache.set) contains NO await; `_restore_instance`'s 4805→5019 span is awaitless; `spawn_instance` is sync. A lock would serialize a region that cannot interleave — while adding lock-table lifecycle. It would also be wrong-scoped: the shared state is keyed by `(agent_id, mcp, tag)`, not agent; and a compaction caller holding an agent-scoped lock across its own turn would turn a 1 ms primitive into a turn-long stall. The notify-user per-destination lock exists precisely because ITS read-decide-write spans an `await` on the audit repo (`notify_user.py:277-283` docstring) — our path spans no await, so the precedent does not fire. Enumerated hazards: two concurrent reloads → both pop (second no-op), one restore, correct persona; reload racing spawn/restore of the same agent (different instance) → shared cache key, later coherent snapshot wins, content-derived key makes the overwrite benign; reload racing a DB-write-driven `invalidate` (`inner_soul.py` :1387/:1509) → both orderings end at a correct next restore.

**Amendment:** AM-13 (incl. new mutation leg M5: delete the mtimes-stability check → tier-c leg must RED).

### Q11 — Observability: one INFO line per call; metrics ROUTED

**Verdict.** Exactly ONE `logger.info` line per tool call, on EVERY terminal path (success and refusal), frozen field order for a grep pin:

`prompt_reload: instance=<id> agent=<agent_id> outcome=<no_change|reloaded|forced_reload|refused> code=<code|-> release=<running_release|?> daemon_version=<__version__> fingerprint_changed=<bool> hash_divergence=<bool> cache_busted=<bool> current_matches_running=<true|false|unknown> persona_diff=<exact|unavailable|-> was_cached=<bool> manager_instances=<before>-><after>`

NO upgrade-journal write (the journal is promote-scoped machinery). NO notify-audit-style ledger (a new table + migration is ceremony for a diagnostic-only need — ROUTED as follow-up). NO metrics family in this lane: there is no generic tool-usage family in the F-registry (`checkpoint_metrics.py` register_* :1120-1224; VOCABULARY_VERSION 6 at :244), and neither notify nor memoir added one in their lanes — `prompt_reload_total{outcome}` ROUTED to a future metrics lane; this lane's ceremony budget is already the tool-categories regen + VOCABULARY 6→7 (D12). The log line NEVER contains persona or diff content — counts and flags only.

**Rationale.** Notify's success observability is its audit ledger, and it logs nothing at INFO on success — but a ledger is disproportionate here (notify needed rate-budget enforcement; this tool needs operator diagnosability). The one-line-per-call shape also discharges Q1's disclosure duty without per-branch noise (the pop/no-pop distinction rides the `manager_instances` field; the already-absent-entry case surfaces as `was_cached=false`, never as an extra line).

**Amendment:** AM-15.

---

## 3. Amendment register (implementer must fold; numbered, file-targeted)

| AM | Sev | Files | Change |
|----|-----|-------|--------|
| **AM-1** | 🟡 (highest-priority plan defect) | `phase1-plan.md` D3/T1.4/AC1.4 | Manifest default scope = **the loader's read set for the calling agent** (own artifacts + `meta.json` + shared `_prompt_system/{project-experience,knowledge,knowledge_no_force_explore}.md` per `no_force_explore` + all declared innate-skill files + `memories/*.md`), derived by importing the loader's own resolvers (`_resolve_innate_skill_paths` loader.py:880-886; module constants :449-451; memories :1423-1425). A hand-written path glob = test failure. |
| **AM-2** | 🟡 | `phase1-plan.md` (NEW T1.3b), `decisions.md` D3, overview §8 coupling map | Manifest reader in NEW `daemon/services/release_manifest.py` (~120 L, stdlib-only, no tree walk, no `daemon.tools` import, reads only `agents_manifest`): `load_release_manifest(release_root) -> ReleaseManifest \| None` (None on absent; typed raise on torn/invalid) + `verify_paths(...) -> ManifestVerdict`. Rejections recorded: `upgrade_tools` (4,168 L, cycle pressure — consumers import it only lazily in-function), `prompt_refresh.py` (misfiled for consumer #2 = arm-time provenance guard), `prompt_artifacts.py` (different trust root). |
| **AM-3** | 🟡 | `phase1-plan.md` D3/T1.4/D6 | Absence/sandbox policy: sandbox treated as LIVE-SHAPED (verify; refuse on mismatch); dev-absent → `manifest=absent(dev)` skip; live-absent → `manifest_missing_live`; torn/invalid JSON → NEW `manifest_unreadable`. "On disk, not in manifest" never a mismatch for requested-path checks (strict whole-tree mode only, exempting `memories/`+`history/`). Reader tolerates missing top-level keys (demo v0.16.3 manifest lacks `plugins_manifest`). |
| **AM-4** | 🟢 | `phase3-plan.md` D10/T3.6, `phase4-plan.md` L-sets, `phase5-plan.md` T5.1 | Breadcrumb marker frozen: exactly one `[PROMPT-RELOADED outcome=<o> next_turn=true]` per SUCCESS outcome; ZERO `[PROMPT-RELOADED` on refusals; marker token == structured `outcome` (pin ×3 outcomes + refusal-absence legs). Runbook notes post-compaction erasure is acceptable (persona already live). |
| **AM-5** | 🟡 | `phase1-plan.md` D2/T1.2/AC1.2, `phase3-plan.md` D14, `decisions.md` D2 | `current_matches_running` → three-valued `true\|false\|unknown` (`unknown` on `(None,"")` or non-empty `current_note`); `running_release` only when `sys.frozen` AND exe-parent matches `^v\d+\.\d+\.\d+$`; `current_target` null-not-empty; `daemon_version` separate field; two DISTINCT fixed disclosure lines (false vs unknown); NO refusal reachable from divergence; AC1.2 matrix extended to three fixtures (matched/diverged/no-symlink→unknown, no raise). Mark D2 DECIDED. |
| **AM-6** | 🟢 | `phase1-plan.md` D6, `phase2-plan.md` D8, `phase4-plan.md` L3(e), `decisions.md` D6 | Final vocabulary = the 10-code table in §2-Q5 (drop `not_ari` → notify-exact `ERROR:` envelope per `notify_user.py:703-711`; add `manifest_unreadable`); per-code one-line definitions; `db_unavailable` message names the prompt-versions repo; uncached instance = `was_cached` flag (no code). Tester pins the 10-set + the ERROR-shape leg. |
| **AM-7** | 🟢 | `phase1-plan.md` T1.7/AC1.7, `phase3-plan.md` T3.5/D14, `decisions.md` (NEW D22) | No seam guard (decision D22: unlocked dict across 12 sites; pop idempotent; snapshot_executor precedent; a guard adds a stuck-flag failure mode to a non-defect). Envelope gains `manager_instances before→after`; AC greps the ABSENCE of a guard flag / in-flight-restore check; double-pop proven a no-op by test. |
| **AM-8** | 🟡 | `phase1-plan.md` D5/T1.6, `phase3-plan.md` D13/T3.1, `decisions.md` D5 | Changed/unchanged decision = tool-side per-file sha256 over the frozen loader-read-set (pin asserts set == loader keys); hashes-differ + fingerprint-unchanged → `PromptCache.invalidate(agent_id, mcp_tool_names, version_tag)` (loader.py:1326) BEFORE composing; flags `hash_mtime_divergence` + `cache_busted`. Rationale recorded: force=true cannot cure a stale loader cache (:1466 serves stale on next restore). |
| **AM-9** | 🟡 | `phase1-plan.md` D6/T1.5, `phase4-plan.md` L-set | Tool-side prompt-versions repo probe BEFORE composition (loader `_fetch_effective_rows_safe` :237-259 fail-OPENS — cannot be the source of fail-closed truth); probe raises → `prompt_reload_db_unavailable` (no compose); absent → proceed + `db_fingerprint=absent`; rows → fingerprint computed+reported. Pin legs: L-a absent→proceed; L-b raising→refusal-no-compose; L-c DB-row identity change → fingerprint bump → loader recomposes; L-d hash-bust leg (AM-8). |
| **AM-10** | 🟡 | `phase3-plan.md` D14/D17 wording + T3.1 guard, `plan-overview.md` §10 | Tier-c confirmed as v1 contract; file-level manifest diff REJECTED as old-persona proxy (optional labeled `release_drift` fast-follow); `no_change` wording = "files unchanged since last cache fill", NOT closure currency — multi-instance-same-agent must `force=true` (§10 disclosure); tier-b soundness predicate = stored-mtimes-unchanged check (AM-13's guard). |
| **AM-11** | 🟢 | `phase1-plan.md` D11, `phase2-plan.md` T2.1/AC2.5 | Kill-switch per-call in-body (instance_ops-style resolver, off-spellings {0,false,no,off,disabled}, default ON, knob string in ONE module constant); pin: tool REGISTERED + typed `prompt_reload_disabled` under switch OFF; registration pins switch-state-independent. |
| **AM-12** | 🟡 | `phase1-plan.md` T1.8/AC1.8, `decisions.md` D1 | Delegate skeleton pinned (§2-Q9): async, keyword-only `force`, no lock/log/try, function-local import, `TYPE_CHECKING` type import; manager.py diff ≤10 lines; service imports `load_and_cache_prompt` via `daemon.manager` (patch seam `tests/test_persistence.py:561`); D1 line count corrected to 16,364. |
| **AM-13** | 🟡 | `decisions.md` (NEW D23), `phase1-plan.md` T1.6, `phase3-plan.md` T3.1, `phase4-plan.md` (NEW M5) | No concurrency lock (D23, rationale §2-Q10); claim-guard: capture cached tuple + stored mtimes once; skew at decision time → tier-c downgrade; M5 mutation leg (delete the check → tier-c leg RED); escalation path (per-cache-key lock table) documented NOT built; runbook precondition: awaited on the main loop. |
| **AM-14** | 🟡 | `phase1-plan.md` D4/T1.5 | `mcp_tool_names` resolution parity: compose with the SAME set the restore path resolves (`_get_mcp_tool_names`, `instance_lifecycle.py:1487-1524`; restore call shape :4808-4814); provenance `mcp_tool_names_source=cache\|metadata\|empty`; if resolved set differs from the cached key's set (or empty + metadata-missing) → tier-c downgrade (spurious-diff vector: empty resolution would otherwise diff against a differently-keyed cache entry). |
| **AM-15** | 🟢 | `phase2-plan.md` T2.1, `phase3-plan.md` (result envelope), routed-followups | One frozen-format INFO line per call (§2-Q11 exact field order — grep-pinned); no journal; no ledger; `prompt_reload_total{outcome}` metrics family ROUTED (recorded follow-up); log carries counts/flags only, never persona/diff content. |

**Scope discipline (rejected expansions):** no compaction wiring (R8 primitive-only), no cross-agent reload, no F7 defect fixes (D15 honesty marks only), no `current`-symlink logic changes, no FE changes, no loader-semantics changes, no metrics family, no journal, no audit ledger, no lock, no guard flag.

---

## 4. Anchor corrections (use these, not the plan's cites)

| Plan cite | Correction |
|---|---|
| `manager.py ~13.7k lines` (D1) | **16,364 lines** at HEAD |
| `loader.py` fingerprint compare ":1464" | **:1466** (fold spans :1452-1466) |
| `loader.py` `""` return ":207-208" | **:210** |
| `loader.py` tools_note mtimes ":1398-1399" | path declarations :1398-1399; **mtime assignments :1402/:1404** |
| `notify_user.py` D9 codes ":176-196" | **:182-197** (frozenset :201-216, import validation :218-235) |
| `notify_user.py` kill-switch ":106-115/:332" | docstring/env-prefix only; **operative**: per-call check :715, per-call env read :384-402, default `daemon/config.py:3086-3107` |
| `notify_user.py` layer-3 non-ari | returns the **`ERROR:` envelope** :703-711 (not a REFUSED code) — basis of the Q5 swap |
| `stage.sh:403` "per-file map" | :403 is a comment in the plugins block; map writer = heredoc key **:600** (walk :444, join :476-486) |
| compaction "graph.py" (dispatch assumption) | compaction lives in **`daemon/compaction.py`** (groups :1489-1580, envelope :963-978, hoisting :418-446); graph.py carries the proactive hook :7562+ |
| `_release_cached_instance` "manager.py:7166-7187" | def :7166, `del` :7182, range effectively **:7166-7191** |

---

## 5. Disclosures to append to plan-overview §10

- **File-side TOCTOU (loader cache):** bounded one-call staleness that self-heals on the next call (pre-read mtime snapshot stored at :1507 vs content re-read :1471); DB-side TOCTOU closed by the A8 single-SELECT (:318-321).
- **Probe-to-compose residual:** a prompt-versions repo failure between the tool-side probe (AM-9) and composition still fail-opens inside the loader (sub-second window; closing it = loader change, out of scope).
- **`no_change` wording:** file-level freshness, NOT closure currency (shared-cache multi-instance edge; `force=true` is the escape) — per AM-10.
- **Marker erasure:** the `[PROMPT-RELOADED …]` marker survives only in-turn; compaction may erase it — acceptable, the persona is live — per AM-4.
- **Unverified at this pass (routed to tester gate L1):** runtime proof that no path rewrites a live instance's cached tuple first element (bears on tier-b's cache==closure claim); the `maintenance.py` DB-delete TOCTOU guard around `on_instance_deleted` (read, not exercised — Q1 residual only).

---

## 6. Confidence & assumptions

**High** on Q1/Q3/Q5/Q8/Q9/Q10/Q11 (awaitless-atomicity, envelope, placement, per-call switch, log shape all anchor-verified). **High** on Q6 given AM-8 makes the decision independent of mtime semantics (the ruling does not depend on st_mtime precision — it removes the dependency). **Medium-high** on Q2 (manifest shape verified against the live v0.29.0 artifact + all on-disk releases back to v0.28.13 carry `agents_manifest`; assumption: future stage.sh keeps the map — stage.sh:600 is the writer and the L1/staged-verification family re-hashes it every release). **Medium-high** on Q4/Q7 (tri-state + no-refusal and tier-c are contract choices grounded in verified mechanics; assumption that would flip Q4: if an operator flow emerges that flips `current` WITHOUT restart as a routine (not exceptional) operation, revisit warn-vs-refuse then — with data, not now).

**Assumption that would flip AM-8:** if `PromptCache.invalidate`'s signature/semantics change before implementation (it is stable at loader.py:1326-1337 today), the bust step re-derives — the hash-based decision itself is unaffected.
