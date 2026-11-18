# Group-Chat P3 — Independent Tester Gate (FULL/REGRESSION + behavior)

- **Lane**: `feature/group-chat-p3` @ **`3274fe96e`** (11 commits, 16 files, +3578/−129) · base **`09a683ddc`** (P2 merge; verified == `origin/latest`)
- **Worktree**: `/home/nea/ensemble-src-wt-group-chat-p3` (fenced `.env` mode 600, `POSTGRES_DB=ensemble_dev` @127.0.0.1:5432, `DAEMON_PORT=8079`, Python 3.14.7 / pytest 9.0.2)
- **Gate tier**: **TIER-2 mechanically** — `daemon/manager.py` touched (the ONLY core seam in the diff; other five seam prefixes verified untouched). Re-scoped to the leader's floor `tests/unit` dual-side per the P1/P2 precedent.
- **VERDICT: PASS (as of the F-1 re-gate at HEAD `f4fe2df16`, §8)** — the gate originally returned **FAIL at `3274fe96e`** on one genuine defect (§3.1 F-1: `boot_deliver` wrong-target over-delivery — unpinned, minimal-repro'd, contradicted the code's own substrate contract). The developer lane landed fix `ebcd6a115` (+ docs `f4fe2df16`); the focused re-gate (§8) verified it 5/5 with a mutation-grade pin. FULL REGRESSION was **NET-NEW EMPTY** throughout; every other gate section green. Basis below.
- **Workers** (11 dispatches: 9 primaries + 2 focused revives, 0 re-dispatches, 0 worker failures): gc-p3-infra `cd8bfe84` · gc-p3-docs `dba26e4c` (+revive) · gc-p3-conc `9d48a8dd` · gc-p3-lanemap `ec42c299` (+revive) · gc-p3-f1boot `c209aee9` · gc-p3-addr `2fd569db` · gc-p3-regbase `84eeb0b2` · gc-p3-reghead `c0e092dd` · gc-p3-core `3c4dc9de` · gc-p3-digest `5dbf9a7d` (+revive).
- HEAD `3274fe96e` unchanged throughout; `git status --porcelain` EMPTY before/after every leg (the P2-era ` M .agents/tidier/notes.md` is gone from this worktree). No commits, no checkouts, no production/test edits — validate-only mandate honored. Evidence root: `/tmp/gc_p3_gate/` (infra/, docs/, conc/, lanemap/, f1boot/, addr/, base_leg/, head_leg/, core/, digest/).

## Scope Decision

Full-tier request → mechanical Tier-2 fired (`manager.py` in diff). Floor = dual-side `tests/unit` (BASE via `git archive` extraction + worktree venv with `daemon.__file__` asserted inside the extraction; HEAD in-worktree) + serial `tests/unit/repositories/` both sides + fence-family closure both sides. Regression budget: BASE leg 1474 s + HEAD leg ~1428 s ≈ **48 min total — under the 90-min advisory, no breach, no timeout** (no rc=124, no xdist INTERNALERROR anywhere). Behavior validation: three service-level packs against real PG private schemas (connect_args search_path fence, dropped CASCADE after). No web automation (backend only, per brief).

## 1. Changeset & collections reconciliation — PASS (exact)

- Diff: **11 commits / 16 files / +3578−129**. daemon ×6 (`manager.py`, `migrations/versions/20261115_120000_add_room_proposal_dedup_columns.sql` A, `repositories/room/{membership,models,repository,service}.py`, `services/room_delivery.py` A); tests ×6 (`helpers/migration_execution.py` A, `repositories/test_room_migration_runner_execution.py` A, `repositories/{test_room_repository,test_room_membership,test_room_models_and_migration}.py` M, `services/test_room_delivery.py` A); docs/planning ×3; PACKS.md ×1. **Core seams: `manager.py` only → Tier-2.**
- Collections (whole `tests/` tree, both sides, `--ignore tests/packs/g7*` + `--continue-on-collection-errors` per POK; the one residual collection error `tests/e2e/test_context_injection_hybrid.py` is symmetric both sides, pre-existing, 0 nodes): HEAD **38,513** vs BASE **38,462** → **head_only 51 / base_only 0**. Per-file delta reconciles EXACTLY to the lane's test set: `test_room_delivery.py` +24 (A), `test_room_migration_runner_execution.py` +13 (A), `test_room_repository.py` +11 (M), `test_room_membership.py` +3 (M), `test_room_models_and_migration.py` ±0 (M). Set-equality proven per file, not just counts.
- Base-debt lane-diff re-proof: `git diff 09a683ddc..HEAD -- tests/manager/test_skill_service_init.py tests/test_instance_title.py` = **EMPTY**.

## 2. Full regression — PASS, NET-NEW EMPTY

### 2.1 Dual-side execution (identical fence recipe, t=1750/shard, `-n 8`, junitxml canonical, `-p no:randomly`)

| Leg | Files | Nodes | rc | Counts | Wall |
|---|---|---|---|---|---|
| BASE shard A | 515 | 12,257 | 1 | 109F/23E · 12,113P · 12S | 456 s |
| BASE shard B | 514 | 13,810 | 1 | 71F · 13,660P · 79S | 749 s |
| BASE repositories (serial) | 22 | 375 | 1 | 4F · 371P | 231 s |
| BASE fence-family (unset-POSTGRES_DB) | 4 | 165 | **0** | **165P** | 38 s |
| HEAD shard A | 527 | 12,096 | 1 | 144F/21E · 11,920P · 11S | 420 s |
| HEAD shard B | 528 | 13,998 | 1 | 60F · 13,863P · 75S | 716 s |
| HEAD repositories (serial) | 23 | 402 | **0** | **402P** | 256 s |
| HEAD fence-family (unset-POSTGRES_DB) | 4 | 165 | **0** | **165P** | 36 s |

Accounting closure exact on every leg (collected == passed+failed+skipped+errors, xfolds noted). No rc=124, no xdist worker deaths. HEAD repositories leg fully green (the lane's new/migrated repo tests: 402/402).

### 2.2 Digest equality (form: **BARE-ID from junitxml** `<failure>`/`<error>` testcases, `LC_ALL=C sort -u`; `-q` suppresses the FAILED/ERROR summary lines, so both legs used the identical junitxml extraction — method parity re-verified byte-identical by an independent re-extraction)

| Set | Count | sha256 (prefix) |
|---|---|---|
| base_failed | **207** | `3f7b9db7342aa2dd…` |
| head_failed | **225** | `231f1627f34a8341…` |
| both | **175** | — |
| head_only | **50** | — |
| base_only | **32** | — |

Arithmetic exact: 175+50=225; 175+32=207. Cross-checks: junitxml failures+errors == digest count on both sides (207==207, 225==225), zero duplicates across legs.

### 2.3 Manifest symmetry & the +26/+3 anomaly (closed, lane-innocent)

BASE manifests were built from a `test_*.py` glob (1029 shard files); HEAD from all-`.py`-minus-(repos+fence) (1055). The **+26 head-only manifest files = 1 lane file (`test_room_delivery.py`) + 25 non-test files** (`__init__.py`/`conftest.py`/helpers — present in BOTH trees, 0 scan-collected nodes). The **+3 node delta** beyond the lane's +51 = `tests/unit/decision/helpers.py` explicit-arg pseudo-tests (3 test-named functions force-collected only when passed as explicit args; ran at HEAD only; all 3 green). **Collect-universe delta = +51 exactly, matching the lane set; zero file-set drift; no genuine manifest gap on either side.**

### 2.4 Floor coverage — 100% dual-side

BASE: scan-collected 26,607 == executed 26,607 (**0 unexecuted**). HEAD: scan-collected 26,658, executed 26,661 (the +3 pseudo-tests; **0 unexecuted scan nodes**). The 4 fence-family files (165 nodes) covered by their dedicated legs on both sides.

### 2.5 head_only ladder (50 ids → 49 composition flakes + 1 quarantined pin; **0 net-new**)

All 7 head_only files have EMPTY lane diffs and none is a lane test file (git pre-check). 3× isolated serial re-runs at HEAD + BASE-side probes:

- **42 composition/order flakes** (`test_slash_commands_router.py` 25, `test_work_router.py` 17): failure signatures are event-loop/db_offload pollution (`RuntimeError: Task … BaseHTTPMiddleware…call_next`), reproducing with **byte-identical fail-sets at BASE** under matching serial compositions — the two legs' different xdist compositions selected different red subsets; the branch changed nothing.
- **7 corroborated xdist flakes** (graph_retry ×2, llm_failover_v2_adversarial ×3, ladder_p2_routed_gap3 ×1, tool_config_validation_boot ×1): P/P/P isolated at HEAD, full-file serial green.
- **1 deterministic standing stale pin** (`test_nest_report_rest_route.py::TestAM1TheRouteSetPin::test_the_metrics_router_source_is_untouched_by_this_lane`): F/F/F at HEAD, base pass **vacuous** (gitless extraction; test shells `git diff` at :409-410), lane reach EMPTY — **exactly the QUARANTINE.md Active row ledgered at the P2 gate** (cited; no new row).
- **Zero deterministic head-only ids with lane reach → NET-NEW EMPTY.** The lane's own +51 nodes have zero reds in either digest.

### 2.6 base_only (32) — favorable direction, mechanisms proven

25 gitless-extraction artifacts (git/tree-shelling families: aegis_security_family, plugin_registry, sync_runner ×2, source_gc P11 ×4, filesystem walk/workdir ×5, memoir ×3, prompt_assent ×7, self_alarm, transcript_marker_gate — deterministic F at BASE serially, green at HEAD) + 7 base-xdist composition artifacts (graphless_running ×2, hide_kb ×3, plane_sync ×1, frozen_tool_name ×1 — P at BASE serially). None in lane-modified files; all green at HEAD.

### 2.7 Base-debt parity re-proof (ad-hoc, outside the floor)

Both files sit **outside the tests/unit floor** (`tests/manager/`, `tests/` root) — never collected by the floor legs; the shard digests correctly never see them. Ad-hoc both-sides serial runs (4 invocations, fenced, `timeout 300`, junitxml): **fail-sets IDENTICAL head-vs-base — 5/5 nodes, same ids, same signatures** (`MigrationError` ×3 in `test_skill_service_init.py` + `DetachedInstanceError` ×2 in `test_instance_title.py`; object addresses normalized, otherwise byte-identical) — **exact P2 parity, no divergence**. Lane diff on both files EMPTY (infra leg). Evidence: `digest/debt_parity_sigs.json`.

### 2.8 Pre-existing-red ledger (HEAD 225)

- **107 attributed** to QUARANTINE Active rows + **60** to adjudicated consolidated rows in the Resolved-history section (same attribution basis as P2 §2.7).
- **58 unledgered** (`/tmp/gc_p3_gate/digest/head_unledgered.txt`) = **51 both-side** (same stable families as P2: governor_recursion_guard 10, bash_spawn_reaper 4, child_report_next_turn 3, anomaly_report 5, live_views 2, dbpool 2, + 26 singles) + **7 head-only** (all ladder-adjudicated flakes; the P2 pin is now Active-ledgered). **Ledger documentation debt, same class as P1/P2, no new debt class — smaller than P2's 68.**

## 3. Behavior validation of the delivery surface — 1 DEFECT, rest green (service level, real PG)

### 3.1 Lane map per state + DEFECT-A + dedup + Y14 — 6/7 PASS, **FINDING F-1 (DEFECT)**

PASS: idle/queued/waiting/waiting_children → enqueue under the terminal-refusing CAS (4/4 states; forced mid-route terminal flip → CAS refuses, rowcount 0, fresh re-read → terminal branch mints proposal); paused → park+audit (2 audit rows, no inject/enqueue, no revive, cursor NOT advanced — durable for re-derivation); terminal → ONE-HOP parent proposal (exactly 1 durable row; routing consults exactly `room.owner_instance_id`; grandparent untouched; parent NOT auto-revived; terminal parent → durable pending); dedup triple (same (source_message_id,trigger,member) → 1 row; differing member/trigger → separate rows); Y14 cursor-at-admission (admitted → cursor 0→seq; forced admission failure → `deferred_cursor`, cursor untouched); strictly-greater boundary on BOTH cursors (`seq > max(last_read,last_delivered)` — boundary lands on the SKIP side).

**F-1 🔴 CRITICAL DEFECT — `boot_deliver` wrong-target over-delivery.** The substrate (`derive_undelivered_tagged`, `repository.py:1035-1134`) deliberately over-includes per its docstring ("TARGET resolution … is lane-assignment territory (P3's tag resolution …); the substrate deliberately over-includes … **P3 filters**"), and the repository test prose pins "the router filters targeting later" (`test_room_repository.py:604-609`). `route_post` (the live path) DOES resolve targets (`room_delivery.py:128-179`). **`boot_deliver` (`room_delivery.py:851-927`) never calls the resolver** — its own comment at `:868-869` claims "derive_undelivered_tagged already filtered tagged non-empty", a filter the substrate never provided. Minimal 2-member repro (script `/tmp/gc_p3_gate/lanemap/boot_repro_2member.py`): one post tagged ONLY to `worker-x` → boot derives the `(owner, post)` pair too → **owner receives the post** (enqueue, source `internal_agent:{owner}` — notified about its own post as if from itself), **owner CAS-flipped `idle→running`**, **owner's `last_delivered_seq` advanced 0→1 over a post not tagged to them**. Richer fixture: 92 derived pairs, 4 true tag-targets, 88 spurious; 75 seam calls (73 spurious); untagged alive owner flipped to running after 11 spurious enqueues. Production reach: any room with tagged traffic + stale-cursor members across any daemon restart. **No committed pin covers boot targeting** — all five `boot_deliver` tests pre-advance the owner's cursor specifically to dodge the over-included pair (comments at `test_room_delivery.py:536-541`, `:674`, `:789`). Violates the user-pinned contract (tagged = mid-turn escalation FOR THE TARGET; untagged waits for nudges) at boot, for non-targets. Fix belongs to the developer lane: `boot_deliver` must apply the same tag-target resolution as `route_post`, plus a pin seeding a non-target member WITHOUT the cursor workaround.

Inject-stamp adjudication (worker's CHECK-1 sub-fail): the INJECT lane stamps the A-P3-1 composite `room:{room_id}:{sender}:{seq}:author={author}` (`room_delivery.py:316-319`; pinned at `test_room_delivery.py:374-375` — pin matches code exactly); the ENQUEUE family (incl. the DEFECT-A graphless-running downgrade) stamps `internal_agent:{author}` (`:442/:502/:585/:766`; unpinned). The gate brief's parenthetical ("source= internal_agent:<author> on EVERY injection") conflates the two families — author attribution IS carried on every path. **Downgraded to a notes item (F-3), not a defect.** Injection-unavailable variant: 2 bounded attempts → `deferred_cursor`, no enqueue downgrade, cursor untouched (correct).

### 3.2 F1 gating + boot pair — PASS 5/5

F1 (the review's critical): forced `task_proposal` append failure at service level → outcome `deferred_cursor` (gate `room_delivery.py:266-270`; boot parity `:896-900`), member cursor byte-equal `(0,0)`, 0 proposal rows; force removed → re-derivation pass 2 delivers exactly once (`pending 1 → delivered 1`, one proposal row, cursor `(0,1)`), pass 3 `pending 0` — **no swallow-then-advance, no loss, no duplicate**. Boot direction 1: active AND closed rooms re-derived (pinned pair `boot.py:5-8`); owner included; `last_delivered ≥ seq` members excluded, cursors untouched; delivered cursors advance to seq. Boot direction 2: P1 regen = pure SELECT (proposal table 0→0); boot never parent-ROUTES — both parent-routing sites are `at_boot`-gated (`:273-276`, `:393-399`) before `_route_proposal_to_parent`; terminal member at boot → mint-only (durable preservation, surfaced at the parent's next legitimate lane), 0 manager-effect calls. Two-hop race, both flip windows: (a) running→completed between derive scan and boot status read → boot terminal branch, mint-only, 0 manager calls; (b) idle→completed between lane re-read and CAS → **CAS refuses (rowcount 0, never resurrects a terminal row)** → fresh re-read → terminal branch → mint + admission gate + advance, returns before any parent-routing call.

### 3.3 Addressing matrix — PASS 8/8

Terminal addressee → c′ parent notice, trigger EXACTLY `"addressed_terminal"` (`room_delivery.py:80`, used `:546`); parent injected with 400-char-capped notice; terminal member: 0 calls, no auto-revive; addressee's delivered cursor advances at admission (intended F1 shape). Alive addressee → digest-only (0 recorder calls, 0 cursor writes; digest = posts above cursor via `repository.py:806-828`). RUNNING addressee, untagged → never injected by addressing alone (digest-only even with a live graph task). Tag dominates co-presence: X tagged + Y≠X addressed → X injected, Y digest-only (`tag_dominates` `:184-193` applies only when the addressee itself is tagged). Tagged+addressed same member → single fire (tagged lane wins; no second `addressed_terminal` proposal). Skip class: unresolvable name / LEFT member / cross-room name → post row persists, no crash, routing skips with recorded `warnings: ["addressed unresolved: '<name>'"]` (`:156`; resolve reads only the room's own active roster `:124-136`).

### 3.4 Committed-test surfaces — green

Registered pack `gc_p3_room_concurrency`: **21/21 PASS** (23 s; count matches the registration row exactly — the 20→21 growth was the expected P3 emission/collision + lock-registry pins). Lane bracket (the lane's 5 committed test files = the O-1..O-6 + A-P2-11 carry-over surface): **140/140 PASS ×3 runs stable** (40+13+13+50+24; 112/91/90 s). Registered `concurrency_atomic_unit_test`: 5F/94P/74S (177 s) — the 5 failures are a **node-for-node QUARANTINE Active-row match** (`test_instance_metadata_atomic.py` DetachedInstanceError ×5, base-proven `f49006b92`) → quarantine-adjudicated PASS, zero real signal. Carry-over mapping verified: O-1 runner-execution pin (13 nodes, incl. pre-marked `20260714_000001` via `tests/fakes/f1_premark_helper.py`), O-2 after_seq/limit window (4 tests), O-3 cross-room deny + tree-scoped-ancestor ALLOW (+ negative leg), O-5 apply_migration-based helper (`tests/helpers/migration_execution.py`, no `_executable_sql`), O-6 corrupt-JSON pin (landed at **WARNING** level — documented A-P3-9 deviation from the original `logger.debug` wording), A-P2-11 room_join same-tx emission (`test_join_emits_content_free_room_join_event` + registry-names variant, same-tx per A-P3-13).

## 4. ensure.md Core (scoped) — PASS

- **R1 Critical `concurrency_atomic_unit_test`**: quarantine-adjudicated PASS (§3.4). **R2 Critical**: `--timeout-graceful-shutdown 10` present at `dev.sh:156/:159`. **R3 Important**: await sweep 3/3 + 1/1 + **6/6** (`get_queue_stats` grew 5/6 with the NEW P3 site `room_delivery.py:466`, awaited); strict negative sweep 0 unawaited. No ensure.md contradictions → no Improvement Notices; ensure.md untouched.

## 5. Known-residual documentation — 2 GAPS (B2/B3), rest documented

DOCUMENTED: **A-P3-18 TOCTOU** (planning `decisions.md:355` acceptance record + test pin `test_room_delivery.py:323`); **10k dedup-probe cap cliff** (`room_delivery.py:66-70`, probe-is-optimization + UNIQUE backstop); **D1(i)** RK-13 acceptance note (planning `architecture-recommendation.md:61` + `service.py:106`); **D1(ii)** `never expected` docstring AMENDED (`repository.py:180`, old claim quoted inside the amendment); **D1(iii)** live-PG fingerprint = NAMED-CONSTRAINT form, documented (`decisions.md:263` A-P3-10, classifiers accept either); **D2** L6 skip recorded (A-P3-12 `decisions.md:294` — note: the entry itself flags the original referent as unrecoverable and asks reviewer confirmation at the gate; reviewer APPROVED 3 cycles — carrying the note, not re-litigating).

**MISSING (F-2):** **B2** — no note anywhere pairs the boot None-guard skips (`room_delivery.py:865-874` post/member/room → `continue`) with a reviewer defer; closest is A-P3-20 skip-semantics. **B3** — no status-machine-bypass known/deferred record exists (the word "bypass" appears nowhere); the bypass-shaped items are recorded as **CLOSED-FIX remediation records** (A-P3-17/22/24, all mutation-pinned) rather than deferred residuals. Diligence: all 4 tidier fold commits (`c9a2f9b4e..3274fe96e`) are **subject-only** (empty bodies); the fold range touched **zero `.agents/` files**; `.agents/tidier/notes.md` has no P3 entry; planning dir = 5 files, no fold record. The tidier's deferred list exists only in its report to the leader, not in-repo. **Leader adjudication: either land the two defer notes (small docs commit) or correct the lane record to "fixed, not deferred" for B3's substance.**

## 6. Findings

- **F-1 🔴 CRITICAL — boot over-delivery** (§3.1): `boot_deliver` never applies tag-target resolution; tagged posts reach non-target members at every boot, spuriously CAS-flipping idle members to `running` and advancing their delivered cursors over non-targeted posts. Minimal repro + pin archaeology attached. **Gate-blocking for the behavior section.**
- **F-2 🟠 — B2/B3 defer-note documentation gaps** (§5): claimed-documented residuals absent in-repo; fold commits carry no bodies.
- **F-3 🟢 — inject-stamp brief/code conflation**: A-P3-1 composite stamp on inject (pinned, code-matching), `internal_agent:` on the enqueue family (incl. DEFECT-A downgrade). Author attribution present on every path.
- **F-4 🟢 — digest method adaptation**: `-q` suppresses FAILED/ERROR summaries; both legs used identical junitxml BARE-ID extraction (form-tagged, independently re-verified byte-identical).
- **F-5 🟢 — digest is room-wide**: an alive addressee's `unread_count` includes every row above its cursor (others' posts, minted proposals) — relevant to P4 nudge thresholds.
- **F-6 🟢 — `route_post` has zero daemon callers today** (P5 room tools pending) — expected at P3; behavior packs exercised it at service level per the future tool contract.
- **F-7 🟢 — O-6 at WARNING level**: documented A-P3-9 deviation from the carry-over's `logger.debug` wording.
- **F-8 🟢 — base-debt files sit outside the tests/unit floor**; parity proven ad-hoc (§2.7).
- **F-9 🟢 — hygiene**: plaintext PG password found in 7 mode-644 scratch scripts under `/tmp/gc_p3_gate/` (leg fence/run scripts) — **scrubbed to `<REDACTED>`, zero-remaining re-verified twice** (`digest/scrub_password.sh`; password never echoed, pulled from `.env` at runtime). Disclosure: the chmod sweep also touched ~890 inert extraction-tree files (predicate `contains POSTGRES_PASSWORD`; single-user host, no content modified, reversible) — perm restore is cosmetic and left to the /tmp lifecycle.

## 7. Disposition

RESULTS + a PACKS.md P3 gate block are written into the worktree **UNCOMMITTED** (P1/P2 precedent); HEAD remains `3274fe96e`; commit/landing is the leader/merge lane's call. No production or test code was modified by this gate. Snapshot minted for the P4/P5 gates.

**Gate verdict (at `3274fe96e`): FAIL — behavior §3.1 F-1 (boot over-delivery) is a delivery-contract violation with a minimal repro and no pin coverage; FULL REGRESSION NET-NEW EMPTY; lane bracket 140/140 ×3; gc_p3_room_concurrency 21/21; F1/boot 5/5; addressing 8/8; ensure.md Core satisfied; documentation gaps F-2 for the leader to adjudicate.**

## 8. F-1 RE-GATE (2026-11-16, HEAD `f4fe2df16` = fix `ebcd6a115` + docs A-P3-25/26) — **FIXED; OVERALL VERDICT FLIPS TO PASS**

Fix shape (`git show ebcd6a115`, 2 files +149/−5): new helper `_resolve_tagged_target_identities(room_id=, post=)` (`room_delivery.py:1001-1028`) resolves `tagged_members` against the **active roster** (name match or 32-hex UUID→instance_id); called in `boot_deliver` BEFORE any lane assignment/CAS/cursor (cache-keyed per `(room_id, seq)`); non-target pairs `continue` as neither delivered nor deferred (new count `filtered_not_targets`); the false ":868 substrate-already-filtered" comment corrected. Everything below at HEAD `f4fe2df16` (verified; porcelain = only this gate's own uncommitted artifacts).

| Check | Verdict | Evidence |
|---|---|---|
| 1. Minimal repro re-run | **PASS** | owner: 0 seam calls, status `idle` (NO CAS flip), `last_delivered_seq` stays 0; target `worker-x` delivered normally; boot counts `delivered=1, filtered_not_targets=1`; substrate still over-includes by design (2 pairs derived) — filter is downstream, correct layering. Richer fixture: spurious seam calls **73 → 0**, spurious examples `[]`, untagged alive owner no longer flipped; route_post checks 2-7 unchanged PASS (check 1 = the known stale script expectation, §6 F-3 — byte-identical to baseline, not a regression) |
| 2. New pin | **PASS** | `test_boot_delivers_only_resolved_tagged_targets_owner_receives_nothing` (`test_room_delivery.py:660`) GREEN solo (1.56 s); seeding quotes a deliberate NO-cursor-workaround and ASSERTS the over-included owner pair IS derived (so the filter is genuinely exercised — the pin covers exactly the path every prior boot test dodged) |
| 3. Mutation spot-check | **PASS** | ONE-line neutralization (resolver returns all roster identities → pre-fix shape): pin **RED** (`assert counts["delivered"] == 1 — assert 2 == 1`); immediate `git checkout --` revert; porcelain + residue sha256 byte-identical before/after; HEAD unmoved; pin GREEN again after restore. Guardrail adaptation disclosed: whole-tree porcelain could not be empty (this gate's own uncommitted PACKS/RESULTS entries, static, outside the target path) → narrowed to scoped-target-clean + full before/after snapshots + trap-guaranteed revert — safety intent preserved |
| 4. Addressed-at-boot adjudication | **CONFIRMED (20/20)** | 4-room script: addressed-only→alive = route-time digest_only, boot 0 mints/0 seams/cursor untouched; addressed-only→terminal = route-time `addressed_terminal` mint IS the durable record, boot 0 new mints/0 seams; co-presence (tagged X + addressed M) = M gets nothing at boot — the filter bites exactly here; counterfactual room (tagged→terminal, never routed) proves the boot mint path is LIVE (1 mint, cursor advance, parent never routed) ⇒ the no-mint results are non-vacuous. Matches `architecture-recommendation.md` §10 pinned pair #2 ("Boot regen does NOT route, wake, or revive… surfaced only when the parent next runs through a legitimate lane") and §4.4 (proposal row = the durable record; boot regen does not re-route it — D4 pair) |
| 5. A-P3-25/26 (F-2 closure) | **SATISFIED** | A-P3-25 (`decisions.md:369`): purged-mid-lane None-deref DEFERRED (accepted-risk, paired with the A-P3-18 TOCTOU defer, containment + revisit trigger recorded). A-P3-26 (`:371`): lane-(b) CAS bypasses the instance state machine — DEFERRED with the two open residuals named (enum↔machine drift pin absent; state-machine-level audit for room-initiated flips). Both code-verified: `cas_instance_status_to_running` (`repository.py:968-998`) is a raw UPDATE with no `job_state_machine` reference; 13 per-row warn+skip handlers hold the containment claim |
| Pack re-runs | **PASS 4/4** | delivery file **25/25** (24 + the new pin); boot-family substrate `test_room_boot_f1_pin.py` **8/8** (8 = stable historical count, verified across `3274fe96e`/`bdd546942`/`ebcd6a115^`/`ebcd6a115`/HEAD — the "~10" estimate was wrong, no drift); `TestP11BootNoBehaviorChange` **6/6**; PACKS row `gc_p3_room_concurrency` **21/21** (registered invocation verbatim, 28 s) |

**Re-gate deltas (non-blocking, recorded):** (a) the regate copy of `lanemap_behavior.py` documents the inject-stamp as the `room:` composite (script-side stale expectation corrected in the copy; the ORIGINAL stays as the pre-fix baseline artifact); (b) the fix's comment "addressed-only pairs skip too" describes a substrate-impossible pair shape (addressed-only rows never derive) — the effective case is co-presence; comment-precision only; (c) `Event`'s table is `event` (singular) — noted for hand-written PG probes.

**Re-gate evidence:** `/tmp/gc_p3_gate/regate/` (repro + richer fixture + baseline JSON, pin outputs, mutation sequence incl. porcelain/sha snapshots, check4 script + results, fence runners) + `/tmp/gc_p3_gate/regate_packs/` (4 pack runs + fences + junitxml). Workers: gc-p3-regate-behavior `01bc6e6e` · gc-p3-regate-packs `7269f6b2` (2 dispatches, 0 failures).

**OVERALL GATE VERDICT: PASS.** F-1 fixed and mutation-pinned at HEAD `f4fe2df16`; F-2 closed by A-P3-25/26; FULL REGRESSION NET-NEW EMPTY (§2, at `3274fe96e` — the fix commit touches only `room_delivery.py` + its test file, both re-run green here); all behavior sections green; ensure.md Core satisfied. **The P3 gate flips to overall PASS; the phase proceeds to the approver.**
