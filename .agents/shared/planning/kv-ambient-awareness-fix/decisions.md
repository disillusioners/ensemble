# Decisions — kv-ambient-awareness-fix (Synthesis Adjudications)

Date: 2026-09-07 (revision pass 2026-09-08: D12-D15 added; D1/D2/D5/D6/D8/D9 updated for the C1 re-adjudication)
Author: synthesis worker (plan-creation, tie-break pen); revision: plan worker per dispatcher adjudication
Status: Draft — dispatcher review (revised per review REJECT → revision route; one pass, no new forks)
Inputs:
- `phase1-plan.md` (DEFECT 1 cadence; §Shared prerequisite stable-id scheme) — 614 lines, worktree `2750c815`
- `phase2-plan.md` (DEFECT 2 spawned-child mispartition) — 472 lines
- `phase3-plan.md` (DEFECT 3 system-default suppression removal) — 523 lines
- Worktree code anchors re-verified at `2750c815` (see each decision's citations)
- Explorer re-verification: injected-notes-absorb arc (`bb4e3e89` / `4e1e6698` / `c2142c69`) on `latest` — anchor re-pin mandatory at kickoff (**superseded by D13: the drift set is now 7 commits, `2750c815..9eebf3ff`**)

Convention: D-numbered decisions in repo style (mirrors `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`). Each records **decision / rationale / alternatives rejected / reversibility**. Phase files use **defect numbering** (phase1=D1 cadence, phase2=D2 mispartition, phase3=D3 suppression); implementation order is a separate axis — see D9.

---

## D1 — Sequencing: stable-id prerequisite → D2 → D3 → D1-cadence

**Decision.** Ratify the dependency-derived implementation order all three phase plans converged on:

```
Step 0: phase1-plan.md §Shared prerequisite (stable-id helper; no behavior change)
Step 1: DEFECT 2 mispartition   (phase2-plan.md)  — C1′ VERIFY-AND-PIN (fix already landed at base via 80bb61dd; see D12)
Step 2: DEFECT 3 suppression    (phase3-plan.md)  — system-default un-suppression + standalone host
Step 3: DEFECT 1 cadence        (phase1-plan.md body) — split block + per-turn refresh
```

**Rationale.** Three plans state the same order independently: phase2-plan.md:455 (`phase1 (stable-id scheme) → phase2 → phase3 → phase1-cadence`), phase3-plan.md:493-505 (same diagram, cadence labeled "phase4"), phase1-plan.md:575 (`D2 → D3 → D1`). The dependency edges are hard, not stylistic:

- **Prereq → D2/D3:** `_make_context_message` currently mints `uuid4` per call (context_messages.py:85-111, verified at 2750c815). Without the deterministic-id variant, re-emitted blocks are APPENDED by `add_messages` instead of superseded (phase1-plan.md:116-128) and id-less messages break `MessageTapSlot` + hit the moving-timestamp fallback (persistence.py:527-528; phase3-plan.md:210, phase3-plan.md:509).
- **D2 → D3:** D3's new host must bind the corrected tree-root key. On an un-fixed partition, D3 fetches KV under the child's own (empty) key, the empty-partition skip fires, and D3 silently regresses to current behavior (phase3-plan.md:510, Risk 5 at :460).
- **D3 → D1:** refreshing a block that the default-project branch suppresses has no observable effect for default-project trees — the refresh must run after the block exists (phase3-plan.md:515; phase2-plan.md:450 states the mirror: "refreshing a mispartitioned block would only make wrong content fresher").
- **Risk-adjusted order note (updated by D12):** D2 is now the CHEAPEST step of all — the fix is landed at base (80bb61dd), so C1′ is a tests-only verify-and-pin commit (D12). D3 is medium (new host + gate + config field), D1 is the largest (split refactor of `assemble_context_messages` + per-turn read). Ordering cheapest-first also front-loads the highest-confidence win while the branch is fresh.

**Alternatives rejected.**
- *D1-first* (as the file numbering implies): rejected — a per-turn refresh of a mispartitioned or suppressed block refreshes the wrong content (phase2-plan.md:450, phase3-plan.md:515). Highest-effort fix landing first also maximizes rebase exposure against the injected-notes-absorb arc.
- *D3 before D2*: rejected by phase3-plan.md Risk 5 (:460) — wrong-partition binding makes D3 untestable.
- *Parallel implementation*: rejected — all three fixes touch the same builder seam (context_messages.py:1319-1360) and the same injection seam (instance_messaging.py:3573-3657); parallel branches would serialize at merge time anyway with triple the conflict surface (see D2 branch strategy).

**Reversibility.** Order is a plan-level constraint, not a runtime toggle. Re-sequencing post-kickoff requires re-opening D1 here; the two runtime kill-switches (D5/D8, post-D12) remain independently switchable regardless of landing order.

---

## D2 — Branch strategy: ONE branch, sequenced atomic commits

**Decision.** One feature branch — `feature/kv-ambient-awareness-fix` off `origin/latest` in a NEW worktree (NOT `2750c815`, which is read-only context; the main checkout is externally owned) — with four sequenced, individually revertable commits in D1-order:

```
C0  prereq: stable-id helper (_stable_id_for + id_= kwarg on _make_context_message) + registry reservations
    (TWO names post-D12 — ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT is RETIRED, see D12)
C1′ verify-and-pin (DEFECT 2): tests-only commit — coverage audit + gap-closing pins against the LANDED fix
    (80bb61dd); no daemon code, no flag (see D12)
C2  fix(D3): system-default un-suppression + standalone host + ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED + its tests
C3  fix(D1): split block + per-turn refresh + ENSEMBLE_AMBIENT_KV_FRESH + its tests
```

**Rationale.** All three fixes share one builder seam (`assemble_context_messages` KV path, context_messages.py:1319-1360) and one injection seam (instance_messaging.py — re-anchored by D13: gate `:3637`, landed parent_id threading `:3671-3686`, call `:3695`). Three parallel branches would each rebase the same two files and serialize at merge with triple the conflict surface. Per-defect revert does NOT need per-defect branches: the two independently-switchable kill-switches (D5/D8) give operator-level revert per defect via `=0` + restart, which is strictly faster than a git revert on a live daemon (no redeploy); DEFECT 2 needs no revert path at all — its fix is landed, verified, and pinned (D12). The anchor-drift cost is paid exactly once at kickoff on the single branch: the drift set is now SEVEN commits, `2750c815..9eebf3ff` (D13 — supersedes the stale 3-commit injected-notes list `bb4e3e89/4e1e6698/c2142c69` in R2/R10 and the phase plans' re-anchor steps).

**Alternatives rejected.**
- *Three branches merged sequentially*: pays the rebase/re-pin cost three times; D3 blocked-on-D2 and D1-blocked-on-D3 make the merges strictly serial anyway; merge-order slips recreate the exact "phase1 spec not yet committed" halt condition phase2-plan.md:193 and phase3-plan.md:456 try to defend against.
- *One branch per defect with the id-helper in the first*: same serialization, plus the helper lands inside a defect commit instead of as a reviewable no-behavior-change prerequisite (C0 above keeps C1-C3 diffs purely behavioral).
- *One giant commit*: rejected — loses bisect granularity; each commit above is independently revertable by `git revert` in addition to the kill-switch path.

**Reversibility.** Per-commit `git revert` (code level) + per-defect kill-switch `=0` + restart (runtime level, D8). Kill-switch OFF pins are byte/value-identical tests in each phase (phase3-plan.md:310-322, phase1-plan.md:414-423; the DEFECT 2 flag is retired per D12 — no OFF pin).

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

**Erratum (2026-09-08 revision, S19).** The frozen phase1 draft carried a superseded id derivation in its `_build_kv_context_message` sketch — `kv:{context_key.split(':')[-1]}` (phase1-plan.md:222, "extract instance_id from context_key"). That suffix-extraction is a **WRONG-ID HAZARD**: the canonical D3 table (above) defines the KV block id as `kv:{context_key}` where the FULL `context_key` IS the resolved tree-root key — splitting it re-keys the id to the last colon segment and breaks supersede granularity. Stricken in this revision; phase1-plan.md now uses `kv:{context_key}`. This is an erratum against a pre-adjudication draft artifact, not a change to the canonical scheme.

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

**Cell-level test pins (W7, 2026-09-08 revision).** Every cell of the 2×2 carries a named test; the two anti-diagonal cells are NEW cross-flag independence tests:

| Cell (C2 host × C3 refresh) | Named test | Asserts |
|---|---|---|
| ON × ON | `test_kv_ambient_on_default_project_renders_block_when_partition_has_rows` (phase3 1a) + phase1 turn-2+ refresh suite | Block emitted turn 1 AND refreshed every non-retry turn |
| ON × OFF | `test_composition_c2_on_c3_off` (**NEW, W7**) | Block emitted on turn 1, NEVER refreshed on turns 2+ (cadence-only reversion — block still present) |
| OFF × ON | `test_composition_c2_off_c3_on` (**NEW, W7**) | NO KV block on any turn — the refresh flag alone must NOT re-add a suppressed block (cross-flag independence) |
| OFF × OFF | `test_kv_ambient_disabled_keeps_old_no_fetch_behavior` (phase3 1b) + `test_kv_block_absent_when_flag_off` (phase1, turn-2+ scope) + `test_kv_block_present_on_turn1_when_flag_off` (phase1, W3 turn-1-surface pin — turn-1 block present, never refreshed) | Byte/value-identical legacy state |

The two new tests are cross-flag independence pins: flipping either flag alone must produce exactly its row/column of this table, not a blend.

**Rationale.** Verified conflict: phase1-plan.md:194 hard-codes `None`-on-system-default into the per-turn builder while phase3-plan.md:168 un-suppresses that same path — written in parallel, they compose into the exact defect cross-check A predicts: an un-suppressed but never-refreshed default-project block (D1's bug reintroduced on the D3-enabled path, because the refresh builder bails before reading the flag). One builder + explicit composition eliminates the class. The composition table also resolves the ambiguous "what refreshes" question: refresh scope is a function of BOTH flags, evaluated at the unified call site, never at two divergent builders.

**Alternatives rejected.**
- *Keep two builders (inline section for non-default, standalone for default)*: rejected — two renderers for one dataset, two id formats to keep supersede-consistent, and the cross-check A hazard persists whenever either is edited.
- *D1 refresh gated ONLY by `ENSEMBLE_AMBIENT_KV_FRESH` (ignore the D3 flag on refresh)*: rejected — `OFF` on the D3 flag must restore pre-D3 default-project behavior entirely (phase3-plan.md:447 "byte-identical to pre-fix"); letting refresh re-add the block under `D3=OFF` breaks that pin and the revert contract.
- *Refresh applies to "whatever host D3 enabled" via runtime host-detection*: rejected — needless dynamism; the composition is static and testable per the table above.

**Reversibility.** Each flag column of the table is independently reachable via env; every cell is pinned by tests (table above, W7): OFF/OFF cell = the byte-identical OFF pins of both flag phases (phase3-plan.md:310-322, phase1-plan.md:414-423); ON/ON is the new combined regression suite.

---

## D5 — Kill-switch shape mix ratified; names distinct + registry discipline (resolves cross-check D)

**Decision.** Ratify the per-defect shape mix — do NOT normalize. **(Updated by D12: the D2 row's flag is RETIRED — the defect is fixed at base; two flags remain.)**

| Defect | Flag name | Shape | Rationale (phase-cited) |
|---|---|---|---|
| ~~D2 mispartition~~ | ~~`ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT`~~ | **RETIRED (D12)** — fix landed at base (80bb61dd); flag-wrap adjudicated negative-value; name REMOVED from C0's registry pre-reservation (avoid a reserved-unused entry per B.S.8) | Historical shape was **B**; rationale retired with the flag |
| D3 suppression | `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` | **A** (pydantic `ContextMessagesConfig` field + `_resolve_kv_ambient_system_default_enabled()` in `load_config`) | Daemon-config concern with an immediate same-class peer — `ENSEMBLE_PROACTIVE_COMPACTION` at config.py:805-863/:2147-2215 solves silent ambient suppression with Shape A; gets the empty-string-safe bool vocabulary for free (phase3-plan.md:224-230) |
| D1 cadence | `ENSEMBLE_AMBIENT_KV_FRESH` | **B** | Routing/behavioral pivot, no YAML surface, per-turn behavioral gate inside `assemble_context_messages` (phase1-plan.md:272-275) |

All remaining names verified **distinct and unclaimed**: `grep -rn` across `daemon/` returns zero matches at `2750c815`, and none appears in the `constants.py` registry block (constants.py:594-624 — the Wave-2 wc-wake registry — verified). Registration: each name enters `daemon/constants.py:594-624` as `RESERVED` at the commit that binds it (B.S.8 PARTIAL discipline; `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` precedent, phase3-plan.md:34, :222; phase2-plan.md:216 forbids reserved names appearing in `config.py` before binding). C0 of the branch (D2 here) pre-reserves the TWO surviving names in one registry-only commit to eliminate cross-commit name races; the retired `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` is deliberately NOT pre-reserved — a reserved-unused entry contradicts B.S.8's own rationale (D12).

**Rationale.** Shape A vs B is not a style fight — the plans' own citations draw the boundary: Shape A for daemon-config-surface gates with pydantic validator benefits (phase3-plan.md:230: "defect 3's gate IS a daemon-config concern and belongs in daemon/config.py"); Shape B for service-internal behavioral pivots with zero config surface (phase1-plan.md:275, phase2-plan.md:224-226). D3's gate sits next to `ENSEMBLE_PROACTIVE_COMPACTION` in the same file and same bug class — matching the neighbor minimizes reviewer surprise. D1/D2's gates are call-path pivots inside service modules; a pydantic field would add a config surface nobody tunes. Forcing one shape would put one of the three against its own nearest precedent.

**Alternatives rejected.**
- *Normalize all to Shape A*: puts D1/D2 gates in config.py against phase1-plan.md:275 ("Shape B is the documented precedent for this class") and phase2-plan.md:225 ("Shape A is reserved for features with bool-vocab inversion concerns or per-deploy tunability. This fix has neither").
- *Normalize all to Shape B*: loses D3's empty-string-safe validator and its peer-consistency argument (phase3-plan.md:228); boot-crash-on-bare-`KEY=` risk is exactly what Shape A's validator absorbs (phase3-plan.md:462, Risk 7).

**Reversibility.** Both surviving flags are `=0` + restart (D8). Shape B resolvers expose `_reset_*_for_tests()`; Shape A rides the standard config reload path. Boot-log verification lines per flag are specified in each phase (phase1-plan.md:328-341, phase3-plan.md:249-261); both must emit AT BOOT (see S13 note in plan-overview Rollout — a lazy first-call emit makes quiet-daemon boot-log grep false-fail).

---

## D6 — Refresh policy: per-turn, `is_retry` excluded — RATIFIED

**Decision.** The KV block refreshes on every non-retry turn; `is_retry=True` turns skip ALL of `assemble_context_messages` (instance_messaging.py gate `if not is_retry:` — re-anchored to `:3637` by D13) and therefore skip refresh — by design, unchanged, RATIFIED again per the W4 erratum (D15).

**Rationale.** Phase1-plan.md:256-263 is adopted as-is: (a) cost bound — `get_all_as_dict` is a single `SELECT WHERE context_key=?`; worst case ≈100 keys × (128 + 4096 B) ≈ 422 KB ≪ 1% of `DEFAULT_CONTEXT_LIMIT=700000` (compaction.py:1090); (b) precedent — `graph.py:665-684` already does a per-turn flag re-read, so one more `asyncio.to_thread` read per turn is the same class of cost; (c) compaction safety — stable id (D3) keeps the block a constant 1-entry numerator contribution (phase1-plan.md:163-170); (d) the seam table (phase1-plan.md:65-77) shows refresh is needed at exactly the inject/report/job-event seams that already run per-turn. The user intent — "ambient shared_meta_kv is reliable" (worktree-aware-prompts architecture-recommendation.md §6) — is only satisfiable per-turn.

**Alternatives rejected.** Change-detector hash (phase1-plan.md:248 Alternative A — race + state), every-N-turns (Alternative B — arbitrary knob), full-project-block rebuild (Alternative C — wasted work), uuid4 append (Alternative D — unbounded growth), RemoveMessage+rebuild (Alternative E — works but noisier than stable-id supersede).

**Reversibility.** `ENSEMBLE_AMBIENT_KV_FRESH=0` + restart → legacy CADENCE, restated per W3 (2026-09-08 revision): the KV block still EXISTS — it is emitted on turn 1 exactly as with the flag ON, and is simply NEVER REFRESHED on turns 2+. OFF is a cadence-only reversion: turn-1 surface unchanged, turns 2+ frozen at the turn-1 snapshot (the turn-1 block is superseded in place by nothing; it just stays). OFF does NOT mean "block absent on turns 2+" in the sense of removal — the turn-1 block persists in the checkpoint; it means "no new emission on turns 2+". This wording (cadence-only reversion; turn-1 block present; never refreshed) is canonical and is restated identically at phase1-plan.md:24, :280, :362, :414-423, and here — the five statements now agree. A turn-1-surface OFF pin (`test_kv_block_present_on_turn1_when_flag_off`) guards the "block still EXISTS on turn 1" half.

---

## D7 — KV host choice: standalone `[SYSTEM CONTEXT: Shared Meta KV]` block — RATIFIED, extended to all projects

**Decision.** The unified KV block (D4) is phase3's standalone host: kind `CONTEXT_KIND_SHARED_META_KV = "shared_meta_kv"` (new enum value at context_messages.py:73-79 block — enum block verified at 2750c815), title `Shared Meta KV`, built by `build_shared_meta_kv_message`, empty-partition skip (`if not kv_metadata: return None`), `json.dumps` wrapped in try/except → `None` + WARNING on serialization failure (phase3-plan.md:199-206, :464 Risk 9). Extended by D4 to render for non-default projects too, replacing the inline `_format_kv_metadata_section` section inside `build_project_context_message`.

**Rationale.** Phase3-plan.md:106-121's four arguments all strengthen when the host serves all projects: clean stable-id semantics (own kind, own id — D3's `kv:{context_key}`), symmetry (the "same data, different host" inconsistency at :113 dissolves — there is now ONE host), empty-partition skip stays trivial, and the per-turn refresh target (phase3-plan.md:115) is exactly the supersede id D1-cadence needs. Scope-guide UX is preserved untouched (phase3-plan.md:40-42, pins at :353-363). `json.dumps(sort_keys=True, indent=2)` (phase3-plan.md:144) gives byte-stable content between turns when data is unchanged — stable id + stable bytes = zero-cost no-op re-emits from the reducer's perspective.

**Alternatives rejected.** Append-KV-to-scope-guide and KV-section-constant-in-guide-content (phase3-plan.md:117-120 — couples UX-guide lifecycle to ambient-data lifecycle); phase1's reuse-of-`CONTEXT_KIND_PROJECT` inline section (phase1-plan.md:219-226) — superseded by D4; the "new enum vs stable-id-sufficient" open question (phase1-plan.md:613 Q2) is answered: the enum wins because it is the durable key FE and consumers filter on (phase3-plan.md:459 Risk 4: "consumers filter by context_kind").

**Reversibility.** The host only exists under the D3 flag for default-project trees and under D1's flag-composition otherwise (D4 table). `=0` on the applicable flag removes the block entirely; no schema or checkpoint migration.

---

## D8 — Polarity defaults: both surviving flags default ON, `=0` disables — RATIFIED (updated by D12)

**Decision.** Both kill-switches ship default ON with the shared disable vocabulary (`=0/false/no/off`; Shape A adds the pydantic validator's empty-string-safe parsing). The third original flag (DEFECT 2) is retired — its fix is landed at base and needs no polarity (D12).

**Rationale.** Both fixes are in the "behavior-BUG fix" precedent class (phase3-plan.md:236-247 table): the un-fixed behavior IS the bug (stale block, suppressed block), so OFF = "preserve today's bug" is the wrong default for prod (phase2-plan.md:282). `ENSEMBLE_WC_WAKE_ENQUEUE`'s default-OFF + soak + operator flip (critical note: "Do not forget the flip after deploy") is explicitly the wrong precedent here — that flag changes a failure-mode owner; these change which data is read/rendered, with bounded blast radius (phase2-plan.md:286). Post-deploy the only operator action is restart-to-activate; the boot-log lines (two grep-able markers, D5) make the live state visible, satisfying the verification surface phase1-plan.md:518 and phase3-plan.md:431 each specify. Deploy-time blast-radius staging is an OPERATOR choice, not a plan default — see the Pause-First runbook addendum (plan-overview Rollout).

**Alternatives rejected.** Default OFF with soak-then-flip for either flag: reintroduces the "silent stale ambient" failure the initiative exists to fix, and adds a human-flip step that the WC-wake history shows is forgettable. (A STAGED default-OFF-first deploy remains available as an operator option in the runbook — recorded there, not as a plan default.)

**Reversibility.** Each flag independently flips OFF via env + restart; composition matrix (D4) defines the four observable combined states; OFF pins are byte/value-identical tests per phase.

---

## D9 — Phase-numbering canonical mapping (resolves cross-check C)

**Decision.** Defect numbering (file names) is canonical for artifacts; implementation order is a separate axis with no "phase4" object — cadence is DEFECT 1 at implementation step 3 (after the C0 prerequisite). Canonical mapping table:

| Implementation step | Defect # | Artifact | File | Content |
|---|---|---|---|---|
| Step 0 (C0) | — (prerequisite) | phase1-plan.md §Shared prerequisite | phase1-plan.md:112-175 | `_stable_id_for` + `id_=` kwarg + registry reservations (TWO names post-D12); no behavior change |
| Step 1 (C1′) | DEFECT 2 | phase2-plan.md | phase2-plan.md | VERIFY-AND-PIN: tests-only against the fix LANDED at base (80bb61dd); `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` RETIRED (Shape B row struck in D5) — see D12 |
| Step 2 (C2) | DEFECT 3 | phase3-plan.md | phase3-plan.md (523 lines) | un-suppression + standalone host; `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` (Shape A) |
| Step 3 (C3) | DEFECT 1 | phase1-plan.md (body) | phase1-plan.md:178-264 | split block + per-turn refresh; `ENSEMBLE_AMBIENT_KV_FRESH` (Shape B) |

All prose in the overview and this file uses "DEFECT n" / "Cn" only; the string "phase4" is retired. **(Updated by the 2026-09-08 revision:** the phase files were revised in place per the dispatcher's revision dispatch; phase3's sequencing diagram now shows the C0 → C1′ → C2 → C3 order with no "phase4" node. Remaining "Phase 1 of 3" style headers in the phase files are pre-adjudication draft numbering — translate via this table.**)**

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

**Rationale.** The initiative is a defect fix on a read path, not a preference surface; every plan-proposed option differed on correctness or coupling grounds, not taste. If the operator disagrees with any runtime-visible outcome, the two kill-switches are the preference mechanism (that is their job — D8).

**Reversibility.** N/A (no decision to reverse).

---

## D12 — C1 RE-ADJUDICATION: DEFECT 2 is FIXED AT BASE; C1 re-scoped to VERIFY-AND-PIN (dispatcher decision; revision pass 2026-09-08)

**Dispatcher decision recorded verbatim** (adjudication delivered with the revision dispatch; worker verified the anchors independently — see D13):

> **Defect 2 is ALREADY FIXED at base**: commit 80bb61dd (merged via 36a46b01 "Merge feature/explorer-shared-context-injection") deleted the hardcoded `_persistent_parent_id=None` and threads `_proj_row.parent_id` at instance_messaging.py:3672-3686, shipping tests/services/test_instance_messaging_parent_resolution.py (324 lines).
>
> - **Behavioral equivalence: CONFIRMED** — 80bb61dd implements phase2's Option A exactly (reads parent_id from the SAME single instance-row fetch via `getattr(_proj_row,"parent_id",None) or None`; zero extra round-trips; the misleading comment block deleted; three-surface audit in its trailer matches phase2's own audit — messaging FIXED, graph.py ContextSlot already correct, persistence.py:899/:921 already correct).
> - **Exception-ladder parity: CONFIRMED** — except path sets BOTH `_persistent_project_id` and `_persistent_parent_id` to None → `_resolve_tree_root_id` (context_messages.py:949-961) short-circuits to own id = the pre-fix fallback, rated "at least as correct as today" in phase2's exception table.
> - **Flag-wrap: REJECTED as negative-value** — the fix is landed, in base, pure-correctness, exception-safe; a post-hoc Shape B flag adds resolver+boot-log+restart-dependency+churn on the exact seam C2/C3 touch, with nil revert demand. The kill-switch convention protects NEW behavior shipping to live prod; it does not mandate retro-wrapping landed correctness fixes.
> - **DECISION: option (c)** — C1 re-scoped to VERIFY-AND-PIN: (i) the equivalence verification above is recorded (this entry); (ii) coverage audit of tests/services/test_instance_messaging_parent_resolution.py against phase2's original test list — close any GAPS with new pins (candidate gaps to check: child-first-turn sentinel KV visible from tree-root partition through the REAL service with file-backed SQLite; exception-ladder path; messaging-path vs tool-path partition consistency). Expected outcome: tests-only, no daemon code; (iii) the flag OFF-pin premise is DROPPED (no flag → no OFF pin; `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` is RETIRED — remove it from C0's constants.py registry pre-reservation and from every flag table; avoid a reserved-unused entry per B.S.8).

**Worker verification evidence (grep, worktree @ 9926bca0):**
- `git log -1 80bb61dd` = "fix(context): resolve first-turn tree-root from instance parent_id in messaging path"; merged via 36a46b01. The landed threading block sits at **instance_messaging.py:3671-3686** (hardcode init `:3671`; `_proj_row` fetch `:3673-3675`; `_persistent_parent_id = getattr(_proj_row, "parent_id", None) or None` `:3677-3678`; except-branch sets BOTH `_persistent_project_id` and `_persistent_parent_id` to None `:3684-3686`); the `assemble_context_messages(parent_id=...)` call site is `:3695`. The misleading pre-fix comment block (`:3609-3619` in the draft) is deleted and replaced by a doc-true comment (`:3664-3670`).
- `tests/services/test_instance_messaging_parent_resolution.py` = **322 lines, 3 tests**: `TestMessagingParentResolution::test_child_instance_passes_true_parent_id_to_orchestrator` (:132), `::test_root_instance_passes_none_to_orchestrator` (:181), `TestMessagingParentResolutionBugExercising::test_fix_reverted_child_mispartitions_to_own_id` (:240, includes the fix-shape `_resolve_tree_root_id` cross-check :311-322).

**Coverage audit (item ii — evidence for the gap list).** The landed file pins the parent_id THREAD-THROUGH at the kwargs level: `assemble_context_messages` itself is patched to capture kwargs, and the instance row is a `SimpleNamespace` mock. Covered: child→true parent_id; root→None; bug-exercising mispartition shape. CONFIRMED GAPS vs phase2's original test list: (a) **no test asserts block CONTENT/partition** — no child-first-turn sentinel KV visible from the tree-root partition through the REAL service with file-backed SQLite (the mocks never execute the real assembly, so "child reads parent's partition" is inferred, not observed); (b) **exception-ladder path untested** (`_proj_row` fetch fails → both None; `get_tree_root_id` raises → resolver returns `parent_id` at context_messages.py:954-959; `get_tree_root_id` returns None → `parent_id` at :961); (c) **messaging-path vs tool-path partition consistency untested** (shared_meta_kv_tools.py:109-122 reads the same tree-root key — no pin asserts the two paths agree for one spawned child). All three gaps close with TESTS-ONLY pins in C1′ (phase2-plan.md §Test strategy).

**Rewiring.** Sequencing stays C0 → C1′ (verify-and-pin, no flag) → C2 → C3; **C1′ remains the verify GATE for C2** (the correct-partition contract is pinned before C2 builds on it). D4's flag-composition table is now cleanly 2×2 (C2-host × C3-refresh). Kill-switch count: **3 → 2**. phase2-plan.md keeps its root-cause documentation as accurate history and is re-framed "Defect 2 — FIXED AT BASE by 80bb61dd; this phase verifies and pins", with the landed diff as the design-of-record.

**Reversibility.** Nothing to reverse at runtime (no flag exists); the adjudication itself is re-openable only by the dispatcher. If the landed fix ever needs an incident kill-switch, that is a NEW decision with a NEW name — not a retro-fit of the retired name.

---

## D13 — Anchor + drift refresh record (grep-verified @ 9926bca0; replaces the 3-commit list)

**Drift set.** The implicated-file drift `2750c815..9eebf3ff` is **7 commits** (verified by `git log -- <3 implicated files>`): `d348ad4e` (tidier doc-truth/comment fixes), `80bb61dd` (the DEFECT 2 fix), and the LCA arc `d6e30d9d` + `7a899517` + `e321bdb3` + `f965345a` + `53baef57` (judge punch-list, inline LLM report judge, enqueue-lane stamping, nudge embeds, conditional attestation). This REPLACES the stale 3-commit injected-notes list (`bb4e3e89/4e1e6698/c2142c69`) wherever it appears (overview R2/R10; phase re-anchor steps).

**Verified current anchors (worker grep, 2026-09-08):**

| Anchor | Draft cite (@ 2750c815) | Current (@ 9926bca0) |
|---|---|---|
| `project_injected` flag capture | instance_messaging.py:2865-2890 | :2907-2949 |
| `project_injected` stamp site | instance_messaging.py:2988-2992 | :3050-3056 (`:3054`) |
| `if not is_retry:` gate | instance_messaging.py:3574 | **:3637** |
| Injection seam | instance_messaging.py:3573-3657 | :3637-~3760 |
| `_proj_row` fetch | instance_messaging.py:3599-3601 | :3673-3675 |
| Misleading comment / hardcode / call | :3609-3619 / :3620 / :3629 | DELETED / threading block **:3671-3686** / call **:3695** |
| graph.py discard (do-NOT-un-discard) | graph.py:3866-3879 / :3873-3879 | **:4059-4065** (comment :4059-4064, `_ = _persistent_msgs` :4065) |
| graph.py ContextSlot parent_id | graph.py:674-684 (:681) | UNCHANGED (:681) |
| `_resolve_tree_root_id` ladder | context_messages.py:949-961 | UNCHANGED (:949-950 short-circuit; :952-961 ladder) |
| persistence.py parent_id pass-through | :899/:921 | **:905 / :927** |
| persistence.py synthetic id / enumerate | :943 | **:949** (enumerate region :937-949) |

**Migration record.** `instances.parent_id` exists since migration `daemon/migrations/versions/20260402_000001_rename_session_to_instance.sql` (2026-04-02; column at :46, index `ix_instances_parent_id` at :225) — it predates base by months. The landed fix (80bb61dd) reads a long-established, indexed column: **no migration risk, no schema change** in this initiative.

**Addendum (2026-09-08, C0 implementer — re-grep file-list adjudication for phase1-plan.md:519-527).** The phase1 re-anchor step still carried `daemon/compaction.py` + `daemon/config.py` in its "key files to re-grep" list — implicated only by the SUPERSEDED 3-commit injected-notes list. Adjudicated by direct git evidence at C0 kickoff (worktree `agents-ensemble-wt-kvfix` @ `aaa93a1d`, base `9eebf3ff`):

```
$ git log --oneline 2750c815..9eebf3ff -- daemon/services/context_messages.py daemon/services/instance_messaging.py daemon/graph.py
d348ad4e chore(context): tidier follow-ups — doc truth + comment fixes + test hygiene
80bb61dd fix(context): resolve first-turn tree-root from instance parent_id in messaging path
d6e30d9d fix(lca): judge pre-merge punch list (request_timeout, fixtures, log fields, pins)
7a899517 feat(lca): inline LLM report judge on deny path (quick model, conservative fallback)
e321bdb3 fix(lca): stamp internal enqueue-lane messages - close report masquerade hole (review critical)
f965345a feat(lca): nudge embeds completion-flow mermaid diagram
53baef57 feat(lca): conditional attestation (delegation-gated) + system-context nudge header

$ git log --oneline 2750c815..9eebf3ff -- daemon/compaction.py daemon/config.py
c2142c69 fix(compaction): validate ENSEMBLE_INJECTED_NOTES_ABSORB at boot
4e1e6698 feat(compaction): ENSEMBLE_INJECTED_NOTES_ABSORB absorb kill-switch
bb4e3e89 fix(compaction): absorb answered injected notes into compacted span
```

The two file sets are **disjoint**: the injected-notes arc touched exactly `compaction.py`/`config.py` and never the seam files; the 7-commit drift set touched exactly the three seam files and never `compaction.py`/`config.py`. Per D13's supersession, `compaction.py`/`config.py` are DROPPED from phase1's re-grep step; the step now names the three seam files explicitly. No other phase file's re-grep list required this correction (phase2/phase3 name the seam files only).

**Reversibility.** Documentation-only; re-grep at C0 kickoff remains mandatory (R2) — this table is the starting point, not a substitute.

---

## D14 — Legacy uuid4-id block bloat: DECIDED (ii) document bounded cost + pin test (W8)

**Decision.** Option **(ii)**: document the bounded cost and pin it — `test_legacy_instance_no_double_project_block` asserts **at most ONE project block + at most ONE kv block per kind** per instance's context surface, even for legacy checkpoints whose injected blocks carry uuid4 ids (stable-id supersede cannot replace those). The one-shot RemoveMessage sweep (option i) is REJECTED.

**Rationale (evidence-based).** A sweep of legacy blocks cannot key on ids (they are random uuid4s) — it must match by `context_kind`/title across checkpointed history, which is exactly the fragile kind-based mutation path the auto-load sweep needed a dedicated, id-keyed mechanism to avoid (context_messages.py:1273-1279; instance_messaging.py:3725). Risk asymmetry: a wrong-kind match can delete a legitimate block (e.g. skills), and the sweep code is permanent even though its benefit is one-time. The duplication it would heal is bounded: per legacy instance at most one stale project block + one stale KV block (a few KB), counted once in the 700k window (DEFAULT_CONTEXT_LIMIT, compaction.py:1090) and absorbed by the first compaction that reaches those messages. Permanent-but-bounded duplication + a cheap structural pin beats a one-time heal with a permanent new mutation path.

**Reversibility.** The pin is additive; if post-deploy evidence shows the legacy duplication matters (compaction pressure), a sweep can be proposed as a follow-up with id-kind telemetry — not blocked by this decision.

---

## D15 — ERRATUM (W4): skip-on-retry is the RATIFIED refresh behavior

**Decision.** phase1-plan.md:23's sentence "Gate the `is_retry` short-circuit ... to ALWAYS emit the KV block on retries" is a frozen-draft CONTRADICTION and is corrected in this revision: **`is_retry=True` turns never refresh** (they skip all of `assemble_context_messages` via the gate at instance_messaging.py:3637). The ratified behavior is D6 as written; the draft line was the erratum. Retry turns replay the checkpointed (possibly stale) KV block; the next non-retry turn refreshes it.

**Rationale.** The same draft contradicts itself four lines later in its own seam table (:73 "is_retry resume ... No (by design)") and its test inventory (`test_kv_block_absent_on_is_retry`). D6's cost/correctness analysis never proposed refreshing on retries; the `:3637` gate makes "emit on retries" unreachable without dismantling the retry-skip contract (WARN-5 removal, instance_messaging.py:1272/:3933).

**Reversibility.** Documentation erratum; no runtime surface.
