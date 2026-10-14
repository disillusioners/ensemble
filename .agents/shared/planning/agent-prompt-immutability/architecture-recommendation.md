# Architecture Recommendation: agent-prompt-immutability — Plan Enrichment Addendum

Date: 2026-10-13 (overnight run)
Architect instance: this agent (controller). Analysts: 6 skill-equipped workers (one skill each), 2 waves of 3.
Worktree: `/home/ensemble-worktrees/agent-prompt-immutability` (branch `feature/agent-prompt-immutability`, base `latest @ e9cdc25c6`)
Status: **COMPLETE** — 6/6 focus areas fanned out, analyzed, and aggregated. All worker reports verified first-line skill confirmation (no skill-bank misses).
Reading order for the leader: this file → `decisions.md` §6 TL;DR → the UPGRADE register below (§3) triaged per item.

---

## 1. Executive Verdict

**The plan's skeleton is architecturally sound and is ENDORSED: overrides-only seeding, single-choke-point resolution with `selected > latest_approved > template` precedence, `(agent_id, version_tag)` keying (F9), human-only approval via session lane, the carve-out dispositions (memory/memories/growth/meta out), Phase A→B→C structure, and the four folded-in bug fixes.**

The addendum's value is **surgical, not structural**. Six workers identified **7 critical defects** (§3.1) in the plan-as-written — mostly correctness mechanisms that are named but under-specified, or plan-doc contradictions that would surface as mid-build rework — and ~15 significant upgrades (§3.2), plus a test-wall strategy refinement (§6). None of these change the architecture's shape; all of them are cheap NOW and expensive AFTER Phase A code lands (the R14 contract-freeze argument).

**Recommendation: proceed with the plan + fold in the §3 register before Phase A implementation begins.**

---

## 2. Per-Focus-Area Verdicts

| Focus | Verdict | Headline |
|---|---|---|
| 1. Schema & data model | **ENDORSED w/ 4 upgrades** | Shape correct; monotonic-version MAX+1 race unsolved by cited precedent; one-pointer needs partial unique index; identity columns must split; `event_type` column required |
| 2. Resolution semantics | **ENDORSED w/ 3 upgrades** | Hook placement verified correct (cache-hit-safe by sole-writer invariant); R10's invalidation-callback mechanism is INSUFFICIENT as specified → fingerprint-bust-on-get; hook signature must take the sync repo (T6 signature bug); "drift closed by construction" overstated |
| 3. Approval workflow | **ENDORSED w/ 4 upgrades** | Authority model sound; `rejected→draft` re-open is a forensic hazard; phaseB omits `create-draft` endpoint (breaks D-SEED-EDGE operator migration); auth-OFF = 503 (phaseB right, decisions.md wrong); pending-flood needs content-hash dedup |
| 4. inner_soul rewrite | **ENDORSED w/ 3 upgrades** | user.md contradiction ADJUDICATED: phaseB D5 wins (DB-joins) — reconcile decisions.md; **draft-composition rule is missing from the plan** (without it, approval = content regression); response grammar needs `📋 Pending approval:` not `✓`+row-id |
| 5. Guards & enforcement | **ENDORSED w/ 3 upgrades** | Guard site as-written is defective on both literal readings (fires on reads, or misses edit_file) → shared helper at 2 call sites; drift-detection leg has no owner/task; config = module frozenset not DB table |
| 6. Migration/compat & phasing | **ENDORSED w/ 3 upgrades** | Seeding sound; rollback clean ×3; **hook failure posture unspecified** (the plan's only new hot-path DB dependency — must be fail-open+WARN); Phase A→B contract needs 8 amendments (A1–A8) before code lands; test-wall ordered list + missed router-manifest cage |
| 7. Trade-off matrix | **Plan + amendments (Approach B) recommended** | See §5 |

---

## 3. UPGRADE-RECOMMENDED Register (triage-able without user input)

### 3.1 🔴 CRITICAL — correctness defects; fold in before/during Phase A implementation

| # | Item | Evidence | Fix | Phase |
|---|---|---|---|---|
| C1 | **Approval-side cache invalidation**: decisions.md R10 specifies `prompt_cache.invalidate(agent_id, version_tag)` on transitions — but `invalidate` pops exactly ONE key (`loader.py:809-819`, `{agent}::` shape) and every MCP-variant key `{agent}[{tag}]::{mcp_csv}` survives stale. Files never change post-guard → **approved content never takes effect for cached agents with MCP tools until restart**. phaseB C2's acceptance would fail. | W2 (verified control flow :846-:930) | Replace the callback mechanism with **fingerprint-bust-on-get**: extend the mtime-equality check (`:915`) with a synthetic `db_versions_fingerprint` (sha256 of sorted `(artifact, version_id)` pairs) computed in `current_mtimes` each call. Cache hits then self-heal on ANY DB change (approve/reject/select/clear). Selection-change is covered automatically. Legacy invalidates become belt-and-braces for file writers only. Re-word R10. | A (T7) |
| C2 | **TOCTOU on fingerprint×content**: if the hook fetches content and fingerprint as separate queries, an approval landing between them stores stale content labeled with the NEW fingerprint → stale-forever entry. | W2 | ONE repo method `get_effective_rows(agent_id, version_tag)` returning fingerprint inputs AND override content from a single SELECT. Cite in T6 acceptance; add concurrency test. | A (T6) |
| C3 | **Guard placement defect**: "inside `_resolve_within_workdir` at :889" — reading (A) fires on `read_file` (:665) and `list_directory` (:591), breaking agent self-inspection; reading (B) covers only `write_file` — `edit_file` resolves at `:1243`. T9(a)'s "single shared site covers both tools" is false as written. | W5 (verified 5 resolver call sites) | Shared guard helper (see C4 module) invoked at TWO call sites: `write_file` post-`:889` pre-open, `edit_file` post-`:1243` pre-write. Add `test_read_file_still_allowed_for_agents_dir`. Amend T9(a)/F8 wording. | A (T9) |
| C4 | **Draft-composition rule absent**: inner_soul soul/workflow/user writes are SECTION EDITS (append `Learned:` bullet, etc.); `create_and_submit(content)` with `content=request` would replace the whole artifact with a bullet on approval. Plan never specifies the composition source. | W4 | Spec in T7: `draft.content = f(get_effective(agent_id, tag, artifact), request)` — compose on the EFFECTIVE chain (selected > latest_approved > file), then apply the target-specific mutation (soul replace/append, `Learned:` append, user placeholder-strip+append). **Chain on prior pending**: consecutive un-approved calls compose on `max(version) WHERE state IN ('draft','pending_approval')` (preserves today's "two calls = two appends"). Add pending-supersession: a new draft archives the prior pending row (bounds pending to 1 active per key). | B (T7) |
| C5 | **Monotonic-version race**: cited precedent (`skill/repository.py:423-469`) bumps a counter on an EXISTING row — it does not solve `SELECT MAX(version)+1` for new rows. Two concurrent `create_draft` calls can compute the same max. | W1 (precedent read) | DB-level `UNIQUE(agent_id, version_tag, artifact, version)` + INSERT-with-retry-on-IntegrityError (recompute max, retry ≤3). Alternative: per-key counter table. Recommend UNIQUE+retry (one less table). | A (T2/T3) |
| C6 | **Sync/async seam bug in T6**: phaseA T6's hook signature takes the async service facade, but `load_and_cache_prompt` is SYNC and all 4 call sites (`instance_lifecycle.py:2098/:4561`, `persistence.py:1064/:1141`) are sync. Implementing as written = event-loop deadlock or forced refactor mid-build. | W2+W6 (verified all call sites sync; facade is `asyncio.to_thread` per `skill_store_service.py`) | Hook takes the **sync repo** (or a sync service counterpart), not the async facade. Pin in the frozen contract (amendment A4). | A (T6) |
| C7 | **Hook failure posture unspecified — the plan's only new hot-path DB dependency**: today `daemon/loader.py` has zero DB imports. The hook creates a spawn-path failure mode; fail-closed = total system outage on DB blips. | W6 (Q7a) | **FAIL-OPEN with loud WARN**: catch all hook exceptions → log `prompt-version resolution unavailable, serving template` + failure counter → proceed with file content (pre-Phase-A contract). No retries (rely on pool checkout timeout). Named test `test_hook_db_unreachable_falls_back_to_template`. Pin in T6 acceptance. | A (T6) |

### 3.2 🟡 SIGNIFICANT — fold in; mostly contract-freeze (A1–A8) and doc reconciliations

| # | Item | Fix | Phase |
|---|---|---|---|
| S1 | **`rejected→draft` re-open hazard** (phaseB D1): same row re-submitted under same `version_code` with different content breaks forensic identity ("v3 rejected, v3 approved, different content"). | Make `rejected` TERMINAL; resubmit = new row (`version = max+1`), rejected row stays in lineage. Drop `(rejected, draft)` from the Phase-A `VALID_TRANSITIONS` skeleton NOW (amendment A1). | A+B |
| S2 | **One-pointer enforcement**: plan relies on transactional clear-and-set only. A **partial unique index** `WHERE is_selected = 1` makes the invariant structurally impossible — dual-dialect precedents exist (`report_injection/models.py:227-235`, `service_tool/models.py:152-158`). | Add partial unique index alongside the transactional toggle (defense-in-depth; kills the `set_selection` race). | A |
| S3 | **Identity columns too narrow**: single `submitted_by TEXT` ≠ D-IDENTITY-STAMPING's `(instance_id, agent_id, version_tag)` + `(subject, auth_method)`. | Split columns on `prompt_versions` (`submitter_instance_id/agent_id/version_tag`, `approver_subject/auth_method`); `prompt_audit_events` gains `event_type` (frozenset-validated: `CREATED, SUBMITTED, APPROVED, REJECTED, RETRACTED, ARCHIVED, SELECTED, CLEARED, AGENT_CREATE_TEMPLATE_COPIED`). UPPERCASE values (ReportInjectionState convention). | A |
| S4 | **API surface**: canonical = **12 endpoints** under `/api/prompts/`: phaseB's 10 + `POST /versions` (create-draft — REQUIRED by D-SEED-EDGE operator migration) + `GET /effective/{agent_id}/{version_tag}/{artifact}`. **5 scopes** (add `prompts:write` for create-draft + selections); all must join `SCOPE_TAXONOMY` (`scopes.py:47`) — outside it, a scope never reaches the browser lane (`SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY`, `:74`). Explicit `409 ALREADY_APPROVED` on double-approve. URL-segment regex allowlist (`^[A-Za-z0-9_\-]+$`) on agent_id/version_tag/artifact. | B (T4/T5) |
| S5 | **user.md contradiction ADJUDICATED**: decisions.md D-ARTIFACT-DISPOSITION carves OUT (file); phaseB D5 carves IN (DB-joins, closes the `_mother` bypass). **D5 wins** — bypass closure is the regime's point; "no user-visible benefit" conflates prompt-visibility with enforcement value (the operator reads user.md via `agent_mother.agent_read`). | Amend decisions.md D-ARTIFACT-DISPOSITION (one paragraph + forward ref to phaseB D5) BEFORE code lands; note the real behavior change (target=user becomes pending-approval). | docs+B |
| S6 | **Response grammar**: `✓ soul: <row-id>` (phaseB R7) is misleading — the change is NOT effective until approval. | Success line for DB-versioned targets: `📋 Pending approval: <target> → <row-id> (<N> chars) (effective on next spawn after approval)`. Keep `✓` for memory/memories (file-immediate). Update `_format_response` + inner_soul docstring atomically. | B (T7) |
| S7 | **Pending-flood**: D4 auto-submits on every inner_soul call — unbounded pending rows per key from a looping agent. | Content-hash dedup at `create_and_submit` (latest pending with same `content_hash` → no-op, return existing row id) + pending-supersession (C4). Bounded by design. | B (T1) |
| S8 | **Auth-OFF drift**: decisions.md D-AUTH-OFF says 403; phaseB D3 says 503 all-endpoints. **phaseB wins** (`routers/auth.py:96` `503 LOGIN_NOT_CONFIGURED` precedent; auth-OFF is misconfiguration, not authorization failure). Reads also 503 — the audit log exposes human principals' subject strings. | Flip decisions.md D-AUTH-OFF wording to 503 `AUTH_NOT_CONFIGURED`. | docs+B |
| S9 | **Drift-detection leg unowned**: D-BASH-ESCAPE's accepted-risk story leans on promote-time drift detection that exists only as narrative — no task, no owner. Risk register cites a nonexistent control. | Minimal spec: committed baseline `agents/.prompt-artifacts.sha256` (generated over the versioned set); `stage.sh` read-only re-hash + report + journal (NON-blocking, honoring the allow-stale plugin policy); boot-time WARN on mismatch. | A/D |
| S10 | **Guard config surface + failure posture**: single-source module `daemon/tools/prompt_artifacts.py` (`VERSIONED_PROMPT_ARTIFACTS` frozenset, `check_write()` helper, refusal builder, agents-root candidates = registry base + `BASE_DIR/agents`, both `resolve(strict=False)`d, depth-2 filename-anchored match INCLUDING `_`-dirs to prevent template poisoning). NOT a DB config table — a runtime flip without matching code semantics ships inconsistency. Guard's own exception → **fail-closed refusal with `GUARD_ERROR` token** (never reach `open()` unverified). | A (T9) |
| S11 | **Phase-A inner_soul unguarded window**: soul/workflow direct writes (`inner_soul.py:1237/:1327`) are not guarded by any Phase A task; the rewrite is Phase B. If A+B co-land on one branch (this commission's shape), the window is intra-branch only; if a Phase-A-only merge is possible, it is a RUNTIME window. | Explicit decision (see Decisions Pending D-1). Recommended: land the ~5-line 2-site shim calling the C3/C10 helper in Phase A regardless — cheap belt-and-braces. | A |
| S12 | **F9 leak pins**: (i) NULL/`""` tag normalization — `_make_key` omits bracket for falsy tags but SQL `WHERE version_tag = ''` ≠ NULL rows → base overrides invisible; (ii) `developer[v2]` names submitted without registry parsing write rows under the wrong identity while cache-key strings collide (masking the bug in cache-only tests). | Repo normalizes tag (`""` convention, documented); DB-write sites parse via `registry._parse_agent_dir_name` (`registry.py:31-43`); DB-path tests parametrized base + `[v2]`; add "base selection does NOT bust v2's cached entry" to T12. | A+B |
| S13 | **Router-manifest cage missed**: `tests/unit/test_router_scope_manifest.py` + router census tests fail loudly on any new mount absent from the pinned manifest — phaseB T6 adding `prompts_router` WILL trip them; no phase task mentions it. | Add manifest/census updates to T6 acceptance. | B |
| S14 | **Phase C gates**: Gate 1's own verification text is self-contradictory (expects 200-shape from an auth-OFF curl that D3 turns into 503); UI is untestable without an OIDC session. | Add **Gate 1b** (auth-configured dev env + auth-ON curl smoke) and **Gate 2b** (frontend toolchain via local `./node_modules/.bin/tsc` — never bare `npx tsc`, per the 2026-10-03 critical note). | C |
| S15 | **`create_and_submit` missing from frozen contract** (phaseB T7/T8 call it; Phase A's contract list omits it) — plus its composition rule (C4). | Add to the Phase-A contract list verbatim (amendment A5). | A docs |
| S16 | **Retract authority + anti-forgery**: plans silent on who retracts; `actor_from_request` could construct `auth_method="internal"` (forgery path). | Retract = submitting agent (own row) OR any human session. `actor_from_request` raises on internal (only `actor_from_instance()` may mint it). Body-asserted actor fields ignored + deny-test. | B |
| S17 | **modifiable_files residual set drift** (phaseA T9(b) vs phaseB T8 vs decisions D-AGENT-MOTHER vs OQ3 disagree). | Enumerate explicitly: post-rewrite `modifiable_files = [memory.md]`; `rule.md` + `tools.md` (fallback) JOIN the versioned set (close the `_mother` bypass); OQ3 default = join. | A+B |
| S18 | **RAG rejection-hint + `soul_requires_approval`**: hint must add the new-flow note for persona-intent content rejected by the project pre-check; the dead growth knob (`inner_soul.py:1378`) is now enforceable. | T7: update `ProjectContentRejectionHint`; **WIRE** the knob (~20 LOC: parse in `_load_growth_rules`; when false, same-call create+approve+select escape hatch for trusted agents) — preserves the autonomy-signal; delete only if morning review objects. | B |
| S19 | **Restore-drift wording**: "closed by construction" is true ONLY for overridden artifacts; no-DB-rows agents keep today's template-drift semantics (required by the no-regression guarantee). | Caveat in D-RESTORE-DRIFT + release notes (decisions R2 extension): restored instance sees (a) byte-identical behavior when no DB rows, (b) NEW approved content if approved mid-session (human-gated behavior change), (c) no on-disk drift for overridden artifacts. | docs |
| S20 | **Rollback fresh-stamp note**: re-landing Phase A after a revert with a reused version stamp breaks the checksummed ledger. | One line in the rollback note: fresh stamp on re-land. | docs |

### 3.3 🟢 OPTIONAL / follow-ups

- `GET /effective` endpoint (folded into S4 — cheap, included).
- Audit retention: ~10 rows/approve-cycle, human-gated rarity → unbounded growth acceptable 6–12 months; OQ follow-up only (retention sweep or cap-on-read). Do NOT scope into Phase D now.
- Migration-count doc fix: corpus is **92** `.sql` files (not 79 as one research doc says) — cosmetic, cite correctly in T1.
- Interim (pre-guard) chmod guidance: apply to main checkout AND live `INSTALL_DIR/current/agents/`; advisory-only status acknowledged.
- Watcher caches (D-WATCHER-CACHES): confirmed deferrable — bounded staleness (one daemon lifetime), release-restart clears; Phase D.
- **A1–A8 frozen contract table (fold-in G-1)**: written at fold-in as the dedicated doc **`phase-contract.md`** (placement option 2 — this §3.3 remains the optional/follow-ups register).
- history/ orphan disposition: ACCEPT orphan-on-disk (files remain forensic-readable; DB events are the queryable trail); document; optional future back-fill.

---

## 4. ACCEPT-AS-IS Register (explicit endorsements)

1. **F1 overrides-only seeding** — no hidden boot dependency (verified: nothing at boot reads `prompt_versions`; hook NO-OP on zero rows); failure direction safe (un-migrated runtime edits keep today's behavior; loss is lineage-only, recoverable via the D-SEED-EDGE manual workflow).
2. **F6 precedence + invisibility of pending/rejected** — the load-bearing property; composes into no-deadlock (F6 + retract + curl-approve).
3. **F9 `(agent_id, version_tag)` keying** — composes 1:1 with D15 cache keys; base-selection-while-v2-runs semantics confirmed correct (v2 key hits, content unchanged).
4. **Hook placement between `loader.py:920` and `:926`** — verified cache-hit-safe by the sole-writer invariant (`cache.set :930` is the only mutation path, downstream of the hook); the decorator-at-manager-re-export alternative is strictly worse (test-patching divergence).
5. **Cache key option A** (fingerprint in the mtime snapshot, NOT in the key) — option B orphans an entry per approval (slow leak on long-lived daemons); file-mtime busting preserved under A.
6. **D-RESTORE-DRIFT core decision** (DB-resolved at restore, not frozen-at-spawn) — frozen-at-spawn needs a new persistence seam and betrays approval intent; spawn/restore/reconstruction reading one resolution = one mental model.
7. **State/transition enforcement shape** — repo-level guarded single-statement UPDATE + rowcount check (`job_queue/repository.py:1692-1772` reference); no CHECK-constraint precedent exists and none is needed.
8. **Migration layout** — single grouped 2-table file (skill-tables 6-table precedent); `api.py:713/:726` lifespan `create_all` precedents verified for the PG-existing-DB path; fresh-SQLite-boot safety is the named pitfall (20260714 lesson).
9. **Write-matrix dispositions** — memory.md/memories carve-outs, `_prompt_system`/meta/skill-set exclusions, agent_create/delete exceptions (structurally invisible to the tool-layer guard — "preserved" needs only documentation).
10. **Bash boundary (D-BASH-ESCAPE)** — accepted-risk ratified; 26/43 agents hold bash; no code-level guard is possible; the fix is S9 (own the drift-detection leg).
11. **The 6 invalidate fixes + `_get_system_prompt_tokens` threading in Phase A** — 4 of 6 sites stay live post-guard (memory/user/memories + agent_mother remainder); the token bug is ACTIVE today for `[v2]` instances (0-token accounting → wrong compaction decisions).
12. **Rollback stories** — all three phases clean (additive-only state; no checkpoint change; in-memory cache).
13. **Authority model (F4/D2)** — belt-and-suspenders (scope AND `auth_method=='session'`) sound; agents cannot originate HTTP session calls.
14. **Phase D scoping** — nothing must-ship with this commission except S9's cheap drift check (recommended-not-deferred-silently).

---

## 5. Trade-Off Matrix (Focus 7)

**Approaches compared:**
- **A — Plan-as-written** (decisions.md + phaseA/B/C verbatim, drifts unresolved)
- **B — Plan + Architect Amendments** (this addendum's §3 folded in) — **RECOMMENDED**
- **C — Rival skeleton**: "spawn-pinned snapshots + event-driven invalidation + config-table flips + boot seeding" — the strongest materially-different architecture the fork-space offers (frozen-at-spawn restore per D-RESTORE-DRIFT option B; R10's literal invalidation callbacks; DB config-table artifact flips; full/hashed seed). Each constituent was evaluated by the workers and loses.

| Axis | A: plan-as-written | B: plan+amendments ⭐ | C: rival skeleton |
|---|---|---|---|
| **Complexity** | M — hidden complexity surfaces as mid-build rework (C1–C7) | **M−** — surgical amendments, contract frozen before code | M+ — snapshot persistence seam + event fan-out + seed machinery |
| **Scalability** | M− — stale-cache classes under MCP variants; unbounded pending flood | **H** — self-healing fingerprint; bounded pending; DB-enforced invariants | M — event storms on mass approve; orphaned cache keys; per-release re-seed |
| **Maintainability** | L+ — contradictory docs, unowned controls, narrative-only risk legs | **H** — single-source artifact module; frozen contract; owned drift task | L — two sources of truth (snapshot vs live resolution) |
| **Risk** | H — 7 identified critical defects (race, TOCTOU, guard-site, seam, posture) | **M−** — each closed with mechanism + named test | H+ — stale-forever + silent-invisibility failure classes |
| **Cost** | lowest now / highest rework | **+~1–2 days now / minimal rework** | highest — new persistence + events + UI churn |

**Conclusion: B, decisively.** Dominant axes: **Risk** and **Maintainability**. The plan's skeleton survives every challenge the six workers mounted; the amendments are mechanism-specifications and reconciliations, not redesigns. **Confidence: High.** The assumption that would flip the recommendation: if Phase A were to ship to PRODUCTION without Phase B (separate release), C7/S11 escalate from test-window to runtime-window concerns and the inner_soul shim + fail-open posture become release-blocking — confirm the single-branch co-landing (see Decisions Pending).

---

## 6. Test-Wall Strategy (25k+)

**Ordered first-run list** (break-first = deterministic contract pin, cheapest signal):

1. `tests/test_prompt_cache.py` (11 tests) — pins exact `_make_key` format + invalidate round-trip → T7 changes both. **Breaks first, deterministically.**
2. `tests/test_loader.py` (1432L) — cache-miss/mtime/compose; T6 edits the miss path (`loader.py:~920-933`).
3. `tests/unit/test_filesystem_absolute_path.py` — `test_absolute_path_skips_workdir` (:80-83) asserts absolute paths always resolve clean; the guard now refuses agents/-shaped absolutes. Direct contradiction — reclassify.
4. inner_soul packs (`tests/integration/test_inner_soul.py:216-224`, `test_inner_soul_standalone.py:362`) — assert workflow.md WAS updated; Phase B removes that file effect. Highest-volume reclassification block.
5. Boot smoke + manager init (`tests/integration/test_boot_report_recovery.py` init-body cage — T5 adds 2 attrs beside `manager.py:1171-1173`) + skill-seed boot-scan.
6. Migration: fresh-SQLite boot smoke + NEW `tests/migration/test_prompt_versions_index_parity.py` (precedents exist ×2).
7. Invalidate-signature callers (`test_manager.py`, `test_persistence.py`, `test_progressive_dispatch.py`, `test_llm_load_balance_integration.py`) — most patch `daemon.manager.load_and_cache_prompt` whole-function → unaffected; only direct invalidate-shape assertions move.
8. `tests/test_agents_api.py` (14 create/delete) — expected GREEN (proves preserved exceptions).
9. **`tests/unit/test_router_scope_manifest.py` + router census (S13)** — deterministic red at T6 unless the manifest is updated. MISSED BY THE PLAN.
10. Spot-check only: compaction/memory tests (carve-out stands), snapshot prompt tests, watcher tests (none found).

**Classification rubric** (every reclassification cites its fork id in a comment; no assertion silently weakened):
(i) intent survives, mechanism changed → update mechanism detail; (ii) bug pinned as contract → fix to NEW contract + annotate fork/bug id; (iii) file-effect assertions removed by design → rewrite to DB-row effect (row exists AND file unchanged); (iv) golden snapshots → regenerate only if pre-Phase-A loader behavior changed (it must not).

**New-test inventory estimate**: Phase A ≈ 25–30; Phase B ≈ 80–100; Phase C ≤10 (build/typecheck if no FE harness exists — unverified); plus the C1/C2/C7 named tests (`approve-mid-session pre-cached MCP agent`, fingerprint×content concurrency, `hook_db_unreachable_falls_back_to_template`).

**Fence discipline** (binding, per 2026-10-12 critical note): all guard/hook tests use mktemp FAKE_HOME + tmp_path agent dirs — never live-shaped paths.

---

## 7. Consolidated Risk Posture (post-amendment)

- 🔴 residual: NONE critical once §3.1 lands. The honest residuals: bash escape hatch (ratified accepted-risk, now with an OWNED drift leg), TOCTOU resolve→open (out of threat model; OS fence is the true fix), Phase-A inner_soul window (D-1 below).
- 🟡 info-disclosure posture: `prompts:read` bearer grants read every prompt in the system (soul files embed operational knowledge) — grant narrowly; document in the scope docstring. Auth-OFF = 503 everywhere (S8).
- 🟢 storage (15K-row ceiling, audit growth 6-12mo), watcher staleness (bounded), migration re-land stamp (S20).

---

## 8. Decisions Pending (leader triage — no user input required tonight)

- **D-1 (only decision with implementation consequence):** Phase-A inner_soul unguarded window — (a) land the ~5-line 2-site shim in Phase A (recommended: cheap, closes the window even if merge order shifts), or (b) confirm A+B co-land on this single branch and re-scope the D-BASH-ESCAPE morning-hook text to Phase B. Default if untrigged: (a).
- **D-2 (doc fixes — bundle into one commit):** decisions.md amendments: D-AUTH-OFF 403→503 (S8); D-ARTIFACT-DISPOSITION user.md → "IN per phaseB D5" (S5); D-RESTORE-DRIFT caveat (S19); R10 re-word to fingerprint mechanism (C1); modifiable_files enumeration (S17); migration-count 92 (§3.3).
- **D-3 (morning-review items unchanged):** memory.md disposition + D-SEED-EDGE carryover remain the two user-flavored forks; both endorsed as-defaulted.

**Leader triage outcome (fold-in bundle, 2026-10-13 — recorded by the doc owner):** **D-1 ACCEPTED, option (a)** — the 2-site inner_soul shim lands as phaseA T11 (soul/workflow write sites route through the shared `check_write()` guard). **D-2 ACCEPTED and EXTENDED** — the doc-fix bundle is the explicit FIRST Phase A commit and additionally carries the §2.9 endpoint-family reconciliation (canonical 12-endpoint family per S4) + the six doc fixes + the full G/O register (G-1 frozen contract doc `phase-contract.md`; G-4 C1–C7 acceptance folds; O-1…O-16; S18 knob-wiring STRUCK per scope adjudication).

## 9. Open Questions

1. Does the frontend have any unit-test harness at all? (Phase C inventory assumed ≤10; if none, Gate 2b = build/typecheck only.)
2. `test_persistence.py:644` simulates `load_and_cache_prompt` raising ("corrupt agent") — verify it composes with the C7 fail-open hook semantics.
3. Exact 25k+ wall timing baseline (collection exceeds sandbox timeouts; count from commission context).
4. Audit-retention follow-up commission timing (post-6-months check is sufficient).

---

## 10. Instances & Attribution

| Worker | Skill | Instance |
|---|---|---|
| schema & data model | data-flow-design | c4ca05dd-3b56-42a4-bb93-5d0aa059b499 |
| resolution semantics | structural-design | e1d88532-826b-4e1d-a6ae-a4dfd4c26bcf |
| approval workflow | security-design | e9ad151a-251b-49d8-b503-74ae8da00a97 |
| inner_soul rewrite | structural-design | 33898a9c-6899-41fa-b409-ea9728f5e393 |
| guards & enforcement | security-design | 9de78dfc-6fda-4d39-8977-e995ccb61bee |
| phasing & test wall | resilience-design | ee5ad242-ba8d-4342-8870-3a4ef590f86e |

All claims in this addendum trace to worker reports citing worktree file:line evidence, spot-verified during aggregation. One inter-worker conflict resolved by specificity rule: W1's cache-poisoning concern (option-A risk) is subsumed by W2's deeper control-flow analysis + the C2 single-query requirement.

**End of architecture-recommendation.md.**
