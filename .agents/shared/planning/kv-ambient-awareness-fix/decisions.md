# Decisions — kv-ambient-awareness-fix (Synthesis Adjudications)

Date: 2026-09-07
Author: synthesis worker (plan-creation, tie-break pen)
Status: Draft — dispatcher review
Inputs:
- `phase1-plan.md` (DEFECT 1 cadence; §Shared prerequisite stable-id scheme) — 614 lines, worktree `2750c815`
- `phase2-plan.md` (DEFECT 2 spawned-child mispartition) — 472 lines
- `phase3-plan.md` (DEFECT 3 system-default suppression removal) — 523 lines
- Worktree code anchors re-verified at `2750c815` (see each decision's citations)
- Explorer re-verification: injected-notes-absorb arc (`bb4e3e89` / `4e1e6698` / `c2142c69`) on `latest` — anchor re-pin mandatory at kickoff

Convention: D-numbered decisions in repo style (mirrors `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`). Each records **decision / rationale / alternatives rejected / reversibility**. Phase files use **defect numbering** (phase1=D1 cadence, phase2=D2 mispartition, phase3=D3 suppression); implementation order is a separate axis — see D9.

---

## D1 — Sequencing: stable-id prerequisite → D2 → D3 → D1-cadence

**Decision.** Ratify the dependency-derived implementation order all three phase plans converged on:

```
Step 0: phase1-plan.md §Shared prerequisite (stable-id helper; no behavior change)
Step 1: DEFECT 2 mispartition   (phase2-plan.md)  — parent_id threading
Step 2: DEFECT 3 suppression    (phase3-plan.md)  — system-default un-suppression + standalone host
Step 3: DEFECT 1 cadence        (phase1-plan.md body) — split block + per-turn refresh
```

**Rationale.** Three plans state the same order independently: phase2-plan.md:455 (`phase1 (stable-id scheme) → phase2 → phase3 → phase1-cadence`), phase3-plan.md:493-505 (same diagram, cadence labeled "phase4"), phase1-plan.md:575 (`D2 → D3 → D1`). The dependency edges are hard, not stylistic:

- **Prereq → D2/D3:** `_make_context_message` currently mints `uuid4` per call (context_messages.py:85-111, verified at 2750c815). Without the deterministic-id variant, re-emitted blocks are APPENDED by `add_messages` instead of superseded (phase1-plan.md:116-128) and id-less messages break `MessageTapSlot` + hit the moving-timestamp fallback (persistence.py:527-528; phase3-plan.md:210, phase3-plan.md:509).
- **D2 → D3:** D3's new host must bind the corrected tree-root key. On an un-fixed partition, D3 fetches KV under the child's own (empty) key, the empty-partition skip fires, and D3 silently regresses to current behavior (phase3-plan.md:510, Risk 5 at :460).
- **D3 → D1:** refreshing a block that the default-project branch suppresses has no observable effect for default-project trees — the refresh must run after the block exists (phase3-plan.md:515; phase2-plan.md:450 states the mirror: "refreshing a mispartitioned block would only make wrong content fresher").
- **Risk-adjusted order note:** D2 is the cheapest and safest change (one attribute-thread at instance_messaging.py:3599-3601/:3620/:3629, zero new reads), D3 is medium (new host + gate + config field), D1 is the largest (split refactor of `assemble_context_messages` + per-turn read). Ordering cheapest-first also front-loads the highest-confidence win while the branch is fresh.

**Alternatives rejected.**
- *D1-first* (as the file numbering implies): rejected — a per-turn refresh of a mispartitioned or suppressed block refreshes the wrong content (phase2-plan.md:450, phase3-plan.md:515). Highest-effort fix landing first also maximizes rebase exposure against the injected-notes-absorb arc.
- *D3 before D2*: rejected by phase3-plan.md Risk 5 (:460) — wrong-partition binding makes D3 untestable.
- *Parallel implementation*: rejected — all three fixes touch the same builder seam (context_messages.py:1319-1360) and the same injection seam (instance_messaging.py:3573-3657); parallel branches would serialize at merge time anyway with triple the conflict surface (see D2 branch strategy).

**Reversibility.** Order is a plan-level constraint, not a runtime toggle. Re-sequencing post-kickoff requires re-opening D1 here; the three runtime kill-switches (D5/D8) remain independently switchable regardless of landing order.

---

## D2 — Branch strategy: ONE branch, sequenced atomic commits

**Decision.** One feature branch — `feature/kv-ambient-awareness-fix` off `origin/latest` in a NEW worktree (NOT `2750c815`, which is read-only context; the main checkout is externally owned) — with four sequenced, individually revertable commits in D1-order:

```
C0  prereq: stable-id helper (_stable_id_for + id_= kwarg on _make_context_message) + registry reservations
C1  fix(D2): parent_id threading + ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT + its tests
C2  fix(D3): system-default un-suppression + standalone host + ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED + its tests
C3  fix(D1): split block + per-turn refresh + ENSEMBLE_AMBIENT_KV_FRESH + its tests
```

**Rationale.** All three fixes share one builder seam (`assemble_context_messages` KV path, context_messages.py:1319-1360 — verified) and one injection seam (instance_messaging.py:3573-3657 — verified, including the `_proj_row` fetch at :3599-3601, the hardcode at :3620, the call at :3629). Three parallel branches would each rebase the same two files and serialize at merge with triple the conflict surface. Per-defect revert does NOT need per-defect branches: the three independently-switchable kill-switches (D5/D8) give operator-level revert per defect via `=0` + restart, which is strictly faster than a git revert on a live daemon (no redeploy). The anchor-drift cost (3 newer commits `bb4e3e89/4e1e6698/c2142c69` on the implicated file set) is paid exactly once at kickoff on the single branch (phase2-plan.md:374-379, phase3-plan.md:401, phase1-plan.md:587-597 all mandate the same re-pin).

**Alternatives rejected.**
- *Three branches merged sequentially*: pays the rebase/re-pin cost three times; D3 blocked-on-D2 and D1-blocked-on-D3 make the merges strictly serial anyway; merge-order slips recreate the exact "phase1 spec not yet committed" halt condition phase2-plan.md:193 and phase3-plan.md:456 try to defend against.
- *One branch per defect with the id-helper in the first*: same serialization, plus the helper lands inside a defect commit instead of as a reviewable no-behavior-change prerequisite (C0 above keeps C1-C3 diffs purely behavioral).
- *One giant commit*: rejected — loses bisect granularity; each commit above is independently revertable by `git revert` in addition to the kill-switch path.

**Reversibility.** Per-commit `git revert` (code level) + per-defect kill-switch `=0` + restart (runtime level, D8). Kill-switch OFF pins are byte/value-identical tests in each phase (phase2-plan.md:338-346, phase3-plan.md:310-322, phase1-plan.md:414-423).

---

## D3 — Stable-id scheme: canonical helper + ONE id-format table (resolves cross-check B)

**Decision.** ONE canonical helper and ONE id-format table. The three phase drafts' variants (`id_=` param on `_make_context_message` (phase1-plan.md:130-145), `_make_stable_id` helper reference (phase2-plan.md:189-193), `stable_id=` kwarg + post-assignment (phase3-plan.md:129-156)) are **pre-adjudication drafts**; the canonical form is:

```python
# daemon/services/context_messages.py (C0, no behavior change)
def _make_context_message(kind, title, content, id_: str | None = None) -> HumanMessage:
    ...  # id_ if id_ is not None else str(uuid.uuid4())   (phase1-plan.md:133-145 verbatim)

def _stable_id_for(kind: str, *, instance_id: str | None = None,
                   context_key: str | None = None, agent_id: str | None = None) -> str:
    ...  # composes per the table below; raises on missing parts for the kind
```

Canonical id-format table (single source of truth; all callers use `_stable_id_for`):

| Block | Stable id format | Owner | Status |
|---|---|---|---|
| Project context block | `project:{instance_id}` | phase1 (D1-cadence C3) | NEW — phase1-plan.md:147-152 |
| KV ambient block (unified host, all projects) | `kv:{context_key}` where `context_key` = resolved tree-root key (leaf form: effectively `kv:{tree_root_id}`) | phase1 D1 + phase3 D3 via the unified builder (D4) | NEW — supersedes phase1's `kv:{context_key.split(':')[-1]}` extraction (phase1-plan.md:222) and phase3's per-(kind, tree_root, instance) tuple (phase3-plan.md:212): the id suffix IS the partition key the block was read from, so supersede granularity matches data granularity exactly |
| Auto-load skills | `auto_load:{instance_id}:{agent_id}` | existing | precedent, context_messages.py:683-693 |
| API synthetic contexts | `synthetic-context-{context_kind}-{instance_id}-{idx}` | existing | precedent, persistence.py:943; independent of this scheme (phase3-plan.md:43) |

**Rationale.** All three variants describe the same mechanism (deterministic id → `add_messages` supersede, per the message-id invariant in the Core Architecture blueprint); the conflicts are purely mechanical (`id_=` vs `stable_id=` vs post-hoc `msg.id =`). The additive `id_=` kwarg wins because it is the narrowest primitive that composes with every caller and keeps `None → uuid4` back-compat for all existing callers (phase1-plan.md:172-174). Routing every id through `_stable_id_for` (phase2's helper name, phase2-plan.md:189) gives one grep-able mint site, which is also the CI hook phase3-plan.md:456 asks for (`grep _make_stable_id` → adapted to `_stable_id_for`). The `kv:{context_key}` form wins over `kv:{instance_id}` because after D2 the partition key — not the child's own instance id — is what the block content derives from; two children of one root then share an id shape keyed to the data they actually read.

**Alternatives rejected.**
- *`stable_id=` kwarg on the new builder with post-hoc `msg.id =` (phase3 draft)*: works but forks the id-minting surface into two styles; phase3-plan.md:156 itself defers to whatever phase1 lands.
- *`kv:{instance_id}` (phase1 draft, `:222` split extraction)*: correct for roots, subtly wrong for children post-D2 — the block's content is tree-root data, so a child-owned id would let two sibling children hold two id-identical-but-content-identical entries anyway; keying on `context_key` is self-documenting.
- *New enum `CONTEXT_KIND_KV_METADATA` alongside the unified host (phase1-plan.md:613 open question 2)*: resolved by D4/D7 — the unified host uses phase3's `CONTEXT_KIND_SHARED_META_KV`; phase1's reuse-of-`CONTEXT_KIND_PROJECT` variant is superseded.

**Reversibility.** C0 is behavior-neutral (default `id_=None` path is byte-identical); the OFF pins of C1-C3 cover the revert path. Id formats are append-only: once shipped, ids are frozen contract for checkpoint supersede — changing a format after deploy would orphan the prior id's checkpoint entry (one stale block until compaction), so format changes are backward-incompatible by construction and gated behind a new decision.

---

## D4 — System-default emission-path unification + flag-composition semantics (resolves cross-check A)

**Decision.** ONE KV-block builder serves ALL projects. Phase1's `_build_kv_context_message` (phase1-plan.md:201-224) and phase3's `build_shared_meta_kv_message` (phase3-plan.md:129-154) merge into a single `build_shared_meta_kv_message(kv_metadata, *, stable_id)` whose output is the standalone `[SYSTEM CONTEXT: Shared Meta KV]` host (D7). Phase1's inline-`_format_kv_metadata_section`-inside-`[SYSTEM CONTEXT: Related Project]` variant is **superseded**: `build_project_context_message` drops its `kv_metadata` parameter entirely (phase1-plan.md:228-244 already specifies this removal for the non-default path). The system-default short-circuit inside phase1's builder ("returns None when system-default project; mirrors the existing if is_system_default: skip kv_fetch guard", phase1-plan.md:194) is **deleted** — it was written against the pre-D3 code and is exactly the stale-gate hazard cross-check A names.

Flag-composition semantics (the D1-refresh flag composes with the D3-host flag; both default ON per D8):

| `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` (D3 host) | `ENSEMBLE_AMBIENT_KV_FRESH` (D1 refresh) | Default-project tree | Non-default tree |
|---|---|---|---|
| ON | ON | KV block emitted turn 1 **and refreshed every non-retry turn** | KV block turn 1 + refreshed (identical path, one builder) |
| ON | OFF | KV block turn 1 only (once-per-instance, legacy cadence) | KV block turn 1 only |
| OFF | ON | NO KV block (suppressed — pre-D3 behavior restored) | KV block turn 1 + refreshed (D3 flag does not gate non-default trees) |
| OFF | OFF | NO KV block | KV block turn 1 only |

Implementation shape: the fetch gate at context_messages.py:1339-1345 becomes `if not is_system_default or kv_ambient_enabled:` (phase3-plan.md:160-172 verbatim); the standalone host append happens on BOTH the `is_system_default` branch (phase3-plan.md:174-195) and the `else` branch; the per-turn refresh in the `project_already_injected` short-circuit (phase1-plan.md:196-199) calls the SAME unified builder and honors the SAME composition (`kv_ambient_enabled` gates whether default-project trees refresh at all).

**Rationale.** Verified conflict: phase1-plan.md:194 hard-codes `None`-on-system-default into the per-turn builder while phase3-plan.md:168 un-suppresses that same path — written in parallel, they compose into the exact defect cross-check A predicts: an un-suppressed but never-refreshed default-project block (D1's bug reintroduced on the D3-enabled path, because the refresh builder bails before reading the flag). One builder + explicit composition eliminates the class. The composition table also resolves the ambiguous "what refreshes" question: refresh scope is a function of BOTH flags, evaluated at the unified call site, never at two divergent builders.

**Alternatives rejected.**
- *Keep two builders (inline section for non-default, standalone for default)*: rejected — two renderers for one dataset, two id formats to keep supersede-consistent, and the cross-check A hazard persists whenever either is edited.
- *D1 refresh gated ONLY by `ENSEMBLE_AMBIENT_KV_FRESH` (ignore the D3 flag on refresh)*: rejected — `OFF` on the D3 flag must restore pre-D3 default-project behavior entirely (phase3-plan.md:447 "byte-identical to pre-fix"); letting refresh re-add the block under `D3=OFF` breaks that pin and the revert contract.
- *Refresh applies to "whatever host D3 enabled" via runtime host-detection*: rejected — needless dynamism; the composition is static and testable per the table above.

**Reversibility.** Each flag column of the table is independently reachable via env; every cell is pinned by tests: OFF/OFF cell = the byte-identical OFF pins of all three phases (phase2-plan.md:338-346, phase3-plan.md:310-322, phase1-plan.md:414-423); ON/ON is the new combined regression suite.

---

## D5 — Kill-switch shape mix ratified; names distinct + registry discipline (resolves cross-check D)

**Decision.** Ratify the per-defect shape mix — do NOT normalize:

| Defect | Flag name | Shape | Rationale (phase-cited) |
|---|---|---|---|
| D2 mispartition | `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` | **B** (service-module cached resolver + `_reset_*_for_tests` + one-shot boot INFO) | Pure correctness pivot, no knob, no YAML surface; exemplar `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` / `ENSEMBLE_WC_WAKE_ENQUEUE` (phase2-plan.md:219-228, :228 implementation pattern) |
| D3 suppression | `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` | **A** (pydantic `ContextMessagesConfig` field + `_resolve_kv_ambient_system_default_enabled()` in `load_config`) | Daemon-config concern with an immediate same-class peer — `ENSEMBLE_PROACTIVE_COMPACTION` at config.py:805-863/:2147-2215 solves silent ambient suppression with Shape A; gets the empty-string-safe bool vocabulary for free (phase3-plan.md:224-230) |
| D1 cadence | `ENSEMBLE_AMBIENT_KV_FRESH` | **B** | Routing/behavioral pivot, no YAML surface, per-turn behavioral gate inside `assemble_context_messages` (phase1-plan.md:272-275) |

All three names verified **distinct and unclaimed**: `grep -rn` across `daemon/` for the three names returns zero matches at `2750c815`, and none appears in the `constants.py` registry block (constants.py:594-624 — the Wave-2 wc-wake registry — verified). Registration: each name enters `daemon/constants.py:594-624` as `RESERVED` at the commit that binds it (B.S.8 PARTIAL discipline; `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` precedent, phase3-plan.md:34, :222; phase2-plan.md:216 forbids reserved names appearing in `config.py` before binding). C0 of the branch (D2 here) may pre-reserve all three names in one registry-only commit to eliminate cross-commit name races.

**Rationale.** Shape A vs B is not a style fight — the plans' own citations draw the boundary: Shape A for daemon-config-surface gates with pydantic validator benefits (phase3-plan.md:230: "defect 3's gate IS a daemon-config concern and belongs in daemon/config.py"); Shape B for service-internal behavioral pivots with zero config surface (phase1-plan.md:275, phase2-plan.md:224-226). D3's gate sits next to `ENSEMBLE_PROACTIVE_COMPACTION` in the same file and same bug class — matching the neighbor minimizes reviewer surprise. D1/D2's gates are call-path pivots inside service modules; a pydantic field would add a config surface nobody tunes. Forcing one shape would put one of the three against its own nearest precedent.

**Alternatives rejected.**
- *Normalize all to Shape A*: puts D1/D2 gates in config.py against phase1-plan.md:275 ("Shape B is the documented precedent for this class") and phase2-plan.md:225 ("Shape A is reserved for features with bool-vocab inversion concerns or per-deploy tunability. This fix has neither").
- *Normalize all to Shape B*: loses D3's empty-string-safe validator and its peer-consistency argument (phase3-plan.md:228); boot-crash-on-bare-`KEY=` risk is exactly what Shape A's validator absorbs (phase3-plan.md:462, Risk 7).

**Reversibility.** All three are `=0` + restart (D8). Shape B resolvers expose `_reset_*_for_tests()`; Shape A rides the standard config reload path. Boot-log verification lines per flag are specified in each phase (phase1-plan.md:328-341, phase2-plan.md:288-294, phase3-plan.md:249-261).

---

## D6 — Refresh policy: per-turn, `is_retry` excluded — RATIFIED

**Decision.** The KV block refreshes on every non-retry turn; `is_retry=True` turns skip ALL of `assemble_context_messages` (instance_messaging.py:3574 gate, verified) and therefore skip refresh — by design, unchanged.

**Rationale.** Phase1-plan.md:256-263 is adopted as-is: (a) cost bound — `get_all_as_dict` is a single `SELECT WHERE context_key=?`; worst case ≈100 keys × (128 + 4096 B) ≈ 422 KB ≪ 1% of `DEFAULT_CONTEXT_LIMIT=700000` (compaction.py:1090); (b) precedent — `graph.py:665-684` already does a per-turn flag re-read, so one more `asyncio.to_thread` read per turn is the same class of cost; (c) compaction safety — stable id (D3) keeps the block a constant 1-entry numerator contribution (phase1-plan.md:163-170); (d) the seam table (phase1-plan.md:65-77) shows refresh is needed at exactly the inject/report/job-event seams that already run per-turn. The user intent — "ambient shared_meta_kv is reliable" (worktree-aware-prompts architecture-recommendation.md §6) — is only satisfiable per-turn.

**Alternatives rejected.** Change-detector hash (phase1-plan.md:248 Alternative A — race + state), every-N-turns (Alternative B — arbitrary knob), full-project-block rebuild (Alternative C — wasted work), uuid4 append (Alternative D — unbounded growth), RemoveMessage+rebuild (Alternative E — works but noisier than stable-id supersede).

**Reversibility.** `ENSEMBLE_AMBIENT_KV_FRESH=0` + restart → once-per-instance legacy cadence (phase1-plan.md:525-531). OFF semantics surprise is documented: OFF means the block is ABSENT on turns 2+, not stale-but-present (phase1-plan.md:546, Risk 8) — boot log and `.env.example` carry the wording.

---

## D7 — KV host choice: standalone `[SYSTEM CONTEXT: Shared Meta KV]` block — RATIFIED, extended to all projects

**Decision.** The unified KV block (D4) is phase3's standalone host: kind `CONTEXT_KIND_SHARED_META_KV = "shared_meta_kv"` (new enum value at context_messages.py:73-79 block — enum block verified at 2750c815), title `Shared Meta KV`, built by `build_shared_meta_kv_message`, empty-partition skip (`if not kv_metadata: return None`), `json.dumps` wrapped in try/except → `None` + WARNING on serialization failure (phase3-plan.md:199-206, :464 Risk 9). Extended by D4 to render for non-default projects too, replacing the inline `_format_kv_metadata_section` section inside `build_project_context_message`.

**Rationale.** Phase3-plan.md:106-121's four arguments all strengthen when the host serves all projects: clean stable-id semantics (own kind, own id — D3's `kv:{context_key}`), symmetry (the "same data, different host" inconsistency at :113 dissolves — there is now ONE host), empty-partition skip stays trivial, and the per-turn refresh target (phase3-plan.md:115) is exactly the supersede id D1-cadence needs. Scope-guide UX is preserved untouched (phase3-plan.md:40-42, pins at :353-363). `json.dumps(sort_keys=True, indent=2)` (phase3-plan.md:144) gives byte-stable content between turns when data is unchanged — stable id + stable bytes = zero-cost no-op re-emits from the reducer's perspective.

**Alternatives rejected.** Append-KV-to-scope-guide and KV-section-constant-in-guide-content (phase3-plan.md:117-120 — couples UX-guide lifecycle to ambient-data lifecycle); phase1's reuse-of-`CONTEXT_KIND_PROJECT` inline section (phase1-plan.md:219-226) — superseded by D4; the "new enum vs stable-id-sufficient" open question (phase1-plan.md:613 Q2) is answered: the enum wins because it is the durable key FE and consumers filter on (phase3-plan.md:459 Risk 4: "consumers filter by context_kind").

**Reversibility.** The host only exists under the D3 flag for default-project trees and under D1's flag-composition otherwise (D4 table). `=0` on the applicable flag removes the block entirely; no schema or checkpoint migration.

---

## D8 — Polarity defaults: all three flags default ON, `=0` disables — RATIFIED

**Decision.** All three kill-switches ship default ON with the shared disable vocabulary (`=0/false/no/off`; Shape A adds the pydantic validator's empty-string-safe parsing).

**Rationale.** All three fixes are in the "behavior-BUG fix" precedent class (phase3-plan.md:236-247 table): the un-fixed behavior IS the bug (stale block, wrong partition, suppressed block), so OFF = "preserve today's bug" is the wrong default for prod (phase2-plan.md:282). `ENSEMBLE_WC_WAKE_ENQUEUE`'s default-OFF + soak + operator flip (critical note: "Do not forget the flip after deploy") is explicitly the wrong precedent here — that flag changes a failure-mode owner; these three change which data is read/rendered, with bounded blast radius (phase2-plan.md:286). Post-deploy the only operator action is restart-to-activate; the boot-log lines (three grep-able markers, D5) make the live state visible, satisfying the verification surface phase1-plan.md:518, phase2-plan.md:398, phase3-plan.md:431 each specify.

**Alternatives rejected.** Default OFF with soak-then-flip for any of the three: reintroduces the "silent stale ambient" failure the initiative exists to fix, and adds a human-flip step that the WC-wake history shows is forgettable.

**Reversibility.** Each flag independently flips OFF via env + restart; composition matrix (D4) defines the four observable combined states; OFF pins are byte/value-identical tests per phase.

---

## D9 — Phase-numbering canonical mapping (resolves cross-check C)

**Decision.** Defect numbering (file names) is canonical for artifacts; implementation order is a separate axis with no "phase4" object — cadence is DEFECT 1 at implementation step 3 (after the C0 prerequisite). Canonical mapping table:

| Implementation step | Defect # | Artifact | File | Content |
|---|---|---|---|---|
| Step 0 (C0) | — (prerequisite) | phase1-plan.md §Shared prerequisite | phase1-plan.md:112-175 | `_stable_id_for` + `id_=` kwarg + registry reservations; no behavior change |
| Step 1 (C1) | DEFECT 2 | phase2-plan.md | phase2-plan.md (472 lines) | parent_id threading; `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` (Shape B) |
| Step 2 (C2) | DEFECT 3 | phase3-plan.md | phase3-plan.md (523 lines) | un-suppression + standalone host; `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` (Shape A) |
| Step 3 (C3) | DEFECT 1 | phase1-plan.md (body) | phase1-plan.md:178-264 | split block + per-turn refresh; `ENSEMBLE_AMBIENT_KV_FRESH` (Shape B) |

All prose in the overview and this file uses "DEFECT n" / "Cn" only; the string "phase4" is retired. The phase files' internal uses of "phase 4" (phase3-plan.md:503, :515) and "Phase 1 of 3" headers are pre-adjudication draft numbering — readers should translate via this table, not edit the phase files (they are inputs, frozen at adjudication).

**Rationale.** The collision is real: phase1-plan.md:2 self-describes as "Phase 1 of 3" while phase3-plan.md:503 calls cadence "phase4-plan.md" — two incompatible axes in one artifact set. Defect numbering is the stable key (the defects exist independent of any work order); implementation order already has unambiguous names (C0-C3 commits, D1 here).

**Alternatives rejected.** Renumber files to implementation order (rejected — rewrites three reviewed artifacts and breaks the dispatcher's cross-references); keep both axes ambiguous (rejected — the exact misread this table exists to kill).

**Reversibility.** Documentation-only; the table is authoritative and cheap to amend if implementation reality diverges.

---

## D10 — FE two-block surface: tracked risk with named owner and detection gate (resolves cross-check E)

**Decision.** The post-fix `GET /messages` surface carries, for a given instance, up to two `[SYSTEM CONTEXT]` blocks where today there is one: the existing project block (now WITHOUT its inline KV section after D4) plus the new standalone `Shared Meta KV` block (plus the scope guide for default-project trees, which today already coexists as a second block — phase3-plan.md:361-363). This is a **medium-impact / medium-likelihood** risk (phase1-plan.md:542, Risk 4) with a named owner: **frontend check before ship**. Detection: on the staging deploy, inspect a default-project and a non-default-project instance via `GET /messages` and the FE chat transcript for duplicate/duplicated-looking `[SYSTEM CONTEXT]` cards; verify the FE does not key dedup on title (phase1-plan.md:612, Open Question 1) — consumers should filter by `context_kind`, which is why D7 chose a distinct enum value.

**Rationale.** The API read path re-runs assembly live (persistence.py:914; phase1-plan.md:542), so FE sees the new block immediately on deploy, not just in checkpoints. The FE transcript merge rules are positionally inert on `created_at` (FE merge-order rule; `message-merge.util.ts` upsert-in-place) — the new block arrives as a distinct-id message, so merge integrity is safe; the open question is purely presentation (two cards instead of one). Prompt byte-identity fences are untouched (daemon-only diff — see the overview's Interaction section).

**Alternatives rejected.** Block ship on an FE change (rejected — FE renders, doesn't parse; risk is cosmetic-level with a clean rollback via kill-switch); collapse to one block by re-embedding KV into the project block (rejected — that is the pre-D4 design; re-introduces the cross-check A coupling).

**Reversibility.** Kill-switch per D4/D8 removes the new block from the surface in one restart if the FE check fails late.

---

## D11 — No user-forks surfaced

**Decision.** Zero preference-shaped forks remain after adjudication. Checked explicitly: polarity defaults (D8 — unanimous across plans), refresh cadence (D6 — per-turn ratified with cost proof), KV host kind/title (D7 — phase3's explicit `Shared Meta KV` title wins on debugging clarity), branch topology (D2 — one branch; per-defect revert already exists via kill-switches, so the git-topology choice carries no user-visible preference), id formats (D3 — mechanical, not preference).

**Rationale.** The initiative is a defect fix on a read path, not a preference surface; every plan-proposed option differed on correctness or coupling grounds, not taste. If the operator disagrees with any runtime-visible outcome, the three kill-switches are the preference mechanism (that is their job — D8).

**Reversibility.** N/A (no decision to reverse).
