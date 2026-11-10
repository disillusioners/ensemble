# Decision Module P1 (SHADOW-ONLY) — RE-GATE REPORT

- **Date**: 2026-11-09
- **Worktree**: `/home/nea/ensemble-src-wt-decision-module-p1`
- **Branch / HEAD verified**: `feature/decision-module-p1` @ `72a233df37d4cc917cb835283330fbed92b3debf` (= `72a233df3`)
- **Base**: `818411107` (proven ancestor). Lane: **32 commits / 48 files / +14,696 −123** (commit-range form).
- **Previous gate**: NO-GO on four blockers at `6c2985a1c` — see `.agents/tester/RESULTS/2026-11-09-decision-module-p1-functional-gate.md` (untracked; mine; untouched).
- **Fix round**: `c81dd0dea` (round-4 fixes) + `a840357ef` / `6ced954a7` / `e12c25b16` / `72a233df3` (doc passes + census pin rewrite).
- **Test leader**: Tester · 11 dispatched workers + 1 follow-up RCA worker + 3 follow-up rounds · **0 commits, 0 pushes, 0 production edits** by the gate.
- **Re-gate scope**: MECHANICAL, WHOLE-TREE, BASE-COMPARED (the leader's ruling — no leader-chosen subset).

---

## 1. VERDICT

# **NO-GO** at `72a233df3`

**B-1 (content-free / TYPE-only) is NOT closed.** One exception-message interpolation survives at `daemon/services/decision/overlay.py:981`; it leaks verbatim to a daemon log line **and into the notify payload**. The lane's two new round-4 guard pins — the static AST scan and the behavioural sweep — are **GREEN on a full-leak counter-mutation** (round-4 file 8 passed, whole decision pack **210 passed**). So the fix round's central claim ("TYPE-only everywhere, with a static guard that proves it") is **false as stated**, and the shipped guard cannot be used as evidence.

B-2, B-3 and B-4 **are** closed, each proven by execution and by planting.

**No behavioural invariant regressed.** All six load-bearing invariants are PROVEN green at this SHA.

| # | Invariant | Verdict | Anchor (measured at `72a233df3`) |
|---|---|---|---|
| 1 | Nothing decides anything | **PROVEN** | Exactly one apply call site (`registry.py:534`), behind two independent locks (ratification **and** the structural `force_shadow=True` split). `transition_stage` has **zero** production callers. Round-4 added **zero** apply callers. Instrumented apply: **0 hits** in S3; **fired** under the ratified+inline control. **R3 measured**: even with a *fully ratified* deciding registry driven through the real detached production path, the apply instrument was reached **0 times** and the answer was logged (`verdict=disagree`), never applied |
| 2 | Byte-identical to today | **PROVEN** | Full-envelope byte-identity (`canon(json.dumps(res, sort_keys=True))` vs baseline) in **all three** states — not just the id list. S1 unconfigured → `['a','c']`, **0** calls; S2 configured+seam OFF → `['a','c']`, **0** calls; S3 configured+ON+shadow (overlay answers `['b']`) → `['a','c']`, **1** call, exactly 1 A/B row, answer **not applied**. Socket counter positive-controlled (0→0→1) |
| 3 | Content-free shadow row | **PROVEN for the row / BREACHED for one log+notify path** | H1–H8 leak **nothing** through the consult path (rows, metric labels, board, notify all clean; 0 out-of-vocabulary labels). **But** `overlay.py:981` interpolates the exception message → log (`:1010/:1018`) + notify payload (`:990`) + TripRecord (`:1020`) ⇒ **D-1 below** |
| 4 | Fail-closed everywhere | **PROVEN** | G8 39/39 checks, 6/6 legs over the real services: flip-without-restart (both directions, with the no-callback contrast proving the callback is the carrier), restart survival (`verified_off`/`verified_on`, store tier beats a shouting env), boot seed never overwrites, A1 narrowing 0/1, global-master poison consulted (poisons a *different* seam, survives a bare cache drop, cleared only by explicit `invalidate()`/`reverify()`), fail-closed polarity both directions with the real store class over a failing engine |
| 5 | The ceiling binds | **PROVEN for the floor paths / CONDITIONALLY BREACHED for USD** | Timeout ⇒ **+0.000500** (the operator floor); connect-phase ⇒ **+0.000000**; 200-no-usage ⇒ **+0.000500**; 200-unknown-usage-keys ⇒ **+0.000500**. **But** a 200-with-tokens-and-no-price payload accrues **$0.00** ⇒ **R-B below** |
| 6 | ~0 critical-path latency | **PROVEN** | OFF median 0.0158 ms → ON@2 s **0.0262 ms** → ON@8 s **0.0573 ms**: a **6,000,000 µs** sleep change moves the caller path by **31 µs** (0.0005 %). Inline positive control **2004.69 ms → 8012.27 ms** (tracks within 0.13 %), so the harness *can* detect sleep-tracking and the detached path shows none |

**Why NO-GO rather than GO-WITH-DISCLOSED-LIMITATIONS:** the blocker is not a stale number or a doc string — it is a **ratified invariant breached with a sentinel**, and the guard pins that are supposed to prevent exactly that breach are **green on it**. A record of truth that is green on a full-leak mutant cannot certify the fix. The fix is small (one line, two pins) and the re-gate is cheap.

**What the leader gets if the lane is staged anyway (a disclosure, not a verdict):** the module is **shadow-only and OFF by default** — `config.yaml` ships with no active `decision:` block, the per-seam key defaults to 0, and the D1 predicate requires an explicit `DECISION_*` signal, so no consult, no socket call, no billing and no row can occur without an operator action. The D-1 leak is therefore **latent** in that posture: it requires the module armed **and** the ceiling tripped **and** a kill-store write failure. The disclosure set is D-1, R-B, R-D, D-2/D-3 (artefact), D-4 (pack), §15.

---

## 2. BLOCKING ITEMS (the fix list)

| # | Sev | Location | Defect | Proof | Fix shape |
|---|---|---|---|---|---|
| **D-1** | 🔴 HIGH | `daemon/services/decision/overlay.py:981` (consumed `:990` notify payload, `:1010/:1018` log, `:1020` TripRecord) | The **only** remaining exception-MESSAGE interpolation: `error = f"{type(exc).__name__}: {exc}"`. A cost-trip whose kill-store raises `RuntimeError("db write failed: SENTINEL… in the dsn; prompt=…")` puts the sentinel **and** the prompt fragment verbatim into a `daemon.services.decision.overlay` WARNING **and** into the notify payload | Two independent workers; sentinel `SENTINEL-B1-91a6ceda41f4811f` captured in the log record and in `payload["error"]` | `error = type(exc).__name__` (type only) **and** drop `"error"` from the notify payload |
| **D-2** | 🔴 HIGH | `tests/unit/decision/test_council_round4.py:92` (AST pin) | The static guard is blind to `str(exc)` — **which its own docstring claims to cover** — to any exception variable named outside `{exc,err,error,e,exception}`, to a message routed through an intermediate `str` variable, and to the BoolOp flow. **The pin is GREEN on the live D-1 site** | The pin's own scan logic extracted and exec'd on synthetic modules: all three bypasses GREEN; the real site GREEN | Flag `str()`/`repr()` calls, non-allowlist except-names, intermediate variables and BoolOp flows; then prove the pin REDs on the real `overlay.py:981` |
| **D-3** | 🔴 HIGH | `tests/unit/decision/test_council_round4.py:149` (behavioural pin) | The behavioural half never enters the **cost-trip** path. Counter-mutation to a full leak ⇒ round-4 file **8 passed**, decision pack **210 passed** | Green-green counter-mutation in an isolated worktree | Add a behavioural pin that drives `_trip` with a secret-bearing kill-store exception and asserts no leak; counter-mutation-prove it (the pack must go RED under a full leak) |
| **D-4** | 🟠 MAJOR (artefact) | `daemon/services/decision/MUTATION_MATRIX.md` | **0/15 digests re-derivable.** The recipe names the operands but never publishes them; ~**1.80 M** reconstruction hashes across 3 sweeps failed. The digests are one-way commitments a reviewer can neither confirm nor refute, defeating the file's stated purpose | Independent re-derivation attempt | Publish the operands (or drop the digest column and cite file:line + the exact mutation text) |
| **D-5** | 🟠 MAJOR (artefact) | `MUTATION_MATRIX.md:42`, `:45`, `:66-67`, whole file | **M6** (two clamp sites) and **M12** (named target `config.py:179`; the pin builds its own config — the floor must be removed at the consumer `overlay.py:881`) do not kill their pins as written; **M9**'s target drifted to `logging_safe.py`; the **seven round-4 claims have no matrix leg** (the matrix was committed in the same commit as the fixes) | 17/17 legs re-executed; **killed as literally described 13/15**, with a corrected mutation **15/15** | Re-point M6/M9, re-target M12, add the round-4 claims as legs |
| **D-6** | 🟠 MAJOR (doc) | `decisions.md` §15 (11 figures) + §5 L107/L166 (3 figures) | §15 is **arithmetically stale at HEAD**: its stated base `c81dd0dea` is 2 commits back; offenders 79→**78**, pre-existing "78 of 79"→**78 of 78**, new 1→**0**, lane-owned 17→**18**, allowlist 28→**29**, label space 817→**818**, commits 30→**32**, D2 headline 673→**675** (components 540+57+42+36), metrics 527→**540** (527 is not the metrics count at *any* commit), adjudication 27→**29**. It also reports a **live finding that is not live** ("the census pin would RED" — it is GREEN, 29 passed). My pass-12 producer-file correction was **deleted** by pass 13 | Independent re-measurement of all 29 figures | A paste-ready replacement block + a command index has been handed to the implementer (one command reproduces every census figure) |
| **D-7** | 🟠 MAJOR (house) | `test/packs/decision_module_p1_unit_test.sh:19` (claim) and `:58-61` (implementation); `.agents/tester/PACKS.md:4` | The header claims the scrub covers "the live `OPENAI_*` credentials" — **false**: the `env -u` list contains no `OPENAI_*` (nor `DECISION_*`, `ENSEMBLE_AUTH_*`, `ENSEMBLE_SELF_ENV`, `PG*`). Proven with an in-child `sitecustomize.py` probe: the pytest child received the **live** `OPENAI_API_KEY` (sha256 `e33c34f5af42…` — the project's recorded live-key fingerprint) and `OPENAI_BASE_URL=https://llm.ensem.dev/v1` | Direct capture inside a real pack run | Correct the comment, extend the `-u` list, add the house **fence self-test** (the reference pack `hrr_mq_boot_recovery_unit_test.sh:117-130` has one that must *refuse* a deliberate `ensemble_prod` pointer) |
| **D-8** | 🟠 MAJOR (house) | `test/packs/decision_module_p1_unit_test.sh` (whole file) | **The scrub is LATENT**: legs A (pack as-is) ≡ B (+ambient `OPENAI_*`) ≡ C (+ambient `DECISION_*`) ≡ D (**scrub bypassed entirely**, live `POSTGRES_DB=ensemble_prod` + live `OPENAI_*` + `ENSEMBLE_SELF_ENV=live` all visible) ≡ E (`env -i`) — **210/210 identical, node for node**. A PASS from this pack is currently **not** evidence of hermeticity | Differential measurement (5 postures) | Hermeticity is independently proven (socket guard logged **zero** outbound sockets and **zero** DNS; SQLite only; `httpx.MockTransport` throughout), but the scrub must be made load-bearing or the claim withdrawn. The hardening was measured **behaviour-neutral** (210 passed with the extended list) |
| **D-9** | 🟠 important (correctness) | `daemon/services/decision/overlay.py:875` (the `tokens <= 0` conjunct) | **The USD ceiling is bypassable**: a 200-with-`total_tokens`-and-no-price payload under the **default empty** `pricing_usd_per_million_tokens` ⇒ USD delta **0.0**; **40 attempts against a USD 0.01 ceiling ⇒ 0 trips**. Only the **token** ceiling binds. The D3 floor's "timeout-only traffic cannot bypass the ceiling" protection is defeated by a *token-bearing* response | Execution-proven | Make the USD floor independent of the token dimension (bill the floor when USD is unknown), **or** make an unconfigured rate table a documented non-startable state. Must be resolved **before the seam is ever armed** |
| **D-10** | 🟠 important (test integrity) | `tests/unit/decision/test_council_round3.py:598-605` | The `(e)` "floor discriminator" leg is **vacuous**: it asserts a `metrics.cost_total` delta of 0.0 around a **client-only** `consult()`, but `DecisionClient` never writes `metrics.cost_total` (the only writer is `overlay.py:904`) ⇒ it passes for **every** failure class, including a floor-billing one | The real discriminator is the `reached_socket is False` assertion on the line above | Assert the store-level delta (as the /tmp probe did), not the client-level metric |

**Disclosed (not blocking) items:** R-A (my prior "ConnectError bills 0 (12/12)" was breaker-contaminated — only 5 of 50 attempts reached the socket) · R-C (the boot-seed write path stores `value_type="str"` for int keys ⇒ the settings service skips the row and logs an ERROR per read; dormant) · D-2 LOW (raw unsanitized `seam_id` in logs at `overlay.py:777` / `registry.py:322`) · **the derived-set tautology class audited across all 20 other `tests/unit/metrics/` files: 5 TRUE tautologies found (`test_metrics_family_w1.py:200-202`, `:281`; `test_metrics_family_w4.py:181`, `:183-185`, `:186-188`) — each `assert f(X) == f(X)`, each counter-mutation-PROVEN incapable of failing (`LAG_STATS += "p99"`, `"p50"→"p50b"`, `"tool-failure"→"tool-failure-v2"` all left the pins GREEN); ALL LATENT in the metrics-query lane — the decision lane's own metrics pins (`test_metrics_discipline.py:53-65`, `:105-119`, `:134-153`, `test_s1_wiring_behaviour.py:183`) use **test literals against production** and can fail, so the lane carries **zero** instances of the class; the fix template is the existing `test_metrics_vocabulary_amendment.py` pattern** · the source-gc sanction's three MEDIUM residuals (below) · the census pin's five LOW/INFO residuals (below) · `kill.py:405` direct-store-write foot-gun · the disclosure wording at `decisions.md:125` (one scheduler tick already closes the honour window) · `factory.py:160-175`'s docstring vs the malformed-section behaviour (overlay installed but inert) · the lane's own "invalid decision config" test exercises only the `except` branch · `types.py::_safe_token` still hashes an empty outcome in the summary builder · `pytest-randomly` is not installed, so `-p no:randomly` is inert today.

---

## 3. THE FOUR BLOCKERS — CLOSED OR NOT

| Blocker | Verdict | Evidence |
|---|---|---|
| **B-1** content-free / TYPE-only | **NOT CLOSED** | D-1/D-2/D-3 above. The 22 `log_exception` sites are clean under a forced secret-bearing exception at every site; H1–H8 leak nothing through the consult path; the empty-token rule now renders genuinely empty fields; **one** interpolation survives and the guards are green on it |
| **B-2** defensive install + the 21 latent reds | **CLOSED** (load-bearing half) | 7/7 shapes: manager alive, nothing raises, **no `SeamRegistry` constructed at all** (spy `call_count == 0`) for absent/unreadable configs. The **21-latent-reds question is answered by measurement**: the latent set was **23**, **zero** lane-caused, all 23 green at HEAD (proven by the pre-fix leg `6ced954a7` where all 23 error). The production side owns the fix (`tests/conftest.py` unchanged) |
| **B-3** scoped source-gc sanction | **CLOSED for the leader's question** | A **non-decision** `manager.py` hunk **still REDs**; the marker binds **per hunk**; other not-touched paths still RED; removing the sanctioned guard still REDs. **Not a blanket amnesty** — but three MEDIUM residuals, and a proven **hardening design** (fixed base + per-line pin + a file-presence pin) has been handed over with per-planting proof |
| **B-4** census pin | **CLOSED** | Two **independent** literals (the `+ tuple(sorted(LANE_OWNED_DAEMON))` derivation is gone); three directions each proven by a planting that REDs naming the offender; the **on-disk** sweep catches an **UNTRACKED** file that the diff census structurally cannot see (`git diff | grep -c` → 0, while the sweep REDs); the previously-vacuous NEITHER-list case is now caught by **two** independent assertions; the both-directions consistency pin bites on 6 plantings. Counts: owned 18 · allowlist 29 (= owned ∪ prior) · on-disk 14 ⊆ owned · pre-existing 78 |

---

## 4. PER-GATE RESULTS

### G1 + G2 — doctrine + shadow invariant · PASS
6/6 named pins + 8/8 round-4 pins + `test_council_round3.py::test_c1_a_detached_consult_never_applies_even_when_ratified_deciding` + `test_registry.py` (24 passed) + the whole decision suite (210) + the registered pack (210, RESULT: PASS). The probe drove the manager's **actual** entry (`manager.py:2891` → `build_decision_overlay_defensively` → `factory.py:153` → `:72`), with **positive controls on both instruments** (socket counter: a real dispatch → 1; row tee: a production `ShadowWriter` write → 1). §13 coverage limitation confirmed (`search()` with no chat model ⇒ 0 calls, 0 rows). D3 refusal quoted verbatim at write-time (`registry.py:373-380`) **and** read-time (`:389-399`); residuals R1/R2/R3 all measured — a hand-set `stage='deciding'` buys the operator **nothing**.

### G3 — ten behavioural obligations · PASS (carried from `6c2985a1c`; re-confirmed at this SHA by the 210/210 pack + 8/8 round-4 pins)
19/19 named-revert probes RED for the right reason; the T-9 positive pin is what catches a superset-only regression while both negatives stay green.

### G4 — detached dispatch · PASS (D-11, D-12)
Legs (a)–(e) as in §1. Timeout rows land 5/5 (`latency_ms=1002`); the caller returns in ≤0.265 ms. Both disclosure windows measured with both write paths, and **bounded to exactly one consult** (in-flight spends 0.001000; consult #2 suppressed, 0.000000).

### G5 — mutation matrix · **FAIL** (D-4, D-5)
17/17 legs executed in an isolated worktree; 17/17 restores sha-matched; 17/17 final GREEN; **13/15 killed as literally described**, **15/15 with a corrected mutation**. Eleven counter-mutation probes of the fix-round's own claims: **9 DISCRIMINATING** (AST site scan, defensive install, census directions 2 and 3, billed-cost port contract, real-concurrency ledger probe, empty-token rule, source-gc sanction) · **2 VACUOUS** (the behavioural half of the TYPE-only rule — GREEN-GREEN; and the AST pin's `str(exc)` claim — D-2).

### G6 — whole-tree dual-side (Tier 2, mechanical) · see §5

### G7 — content-free under adversity · **FAIL (D-1, D-2, D-3)**
H1–H8 clean through the consult path; the leak is in the cost-trip path.

### G8 — kill channel + open item (B) · PASS
39/39 checks, 6/6 legs, **clean tree** (start and end `git status` identical; foreign jsonl byte-identical). Ledger pin **20/20 GREEN** on the clean tree. **Open item (B) ROOT-CAUSED**: three mechanisms, each execution-proven, all producing exactly +0.0005 — (i) **my own shared-worktree mutant live during the measurement** (the G5 leg planted exactly this mutant; the surviving G4 artefacts overlap the documented mutation window), (ii) the **pre-`(e)`-fix client** (at `6c2985a1c^` a ConnectError fell into the broad `except httpx.HTTPError` ⇒ `reached_socket=True` ⇒ floor), (iii) **measurement-window contamination** (an overlapping consult's `record()` landing inside the before/after window; `current_totals()` takes no lock). **Decisive exclusion at HEAD**: 50/50 isolated zeros; 5/5 socket-reaching zeros on a shared stack; **no exception class reaches the floor with `reached_socket=False`** — only `ConnectError` and `ConnectTimeout` bill 0; a **real mid-flight disconnect** bills the floor (correct and conservative).

### G9 — house pins · PASS (D-7, D-8)
`snapshot_v3` 57/57 · `metrics` substring in `snapshot_search_service.py` = **0** · adjudication pin 29/29 (green) · **the new registered pack**: mechanics PASS (dual-layer timeout, serial `-p no:xdist`, `--override-ini="addopts="`, `RESULT:` mapping, exits 0/1/124, `-k` passthrough), determinism PASS (two runs byte-identical modulo the timing line), census 210 = claimed 210, registration format PASS (marker-delimited, newest-first, house table shape) — **but the scrub contract FAILS (D-7) and the scrub is latent (D-8)**.

---

## 5. WHOLE-TREE TIER-2 DUAL-SIDE (mechanical) — **PASS: adjudicated net-new = 0 / 94**

**HEAD** `72a233df3` verified at start **and** end · base `818411107` detached worktree with import-proof (`daemon → /tmp/dm2-wt-base/…`, venv byte-matched 3.14.7). Fence identical both legs (recipe §3 **plus** the `ENSEMBLE_AUTH_*` family, the `DECISION_*` family, `ENSEMBLE_SELF_ENV`/`ENSEMBLE_UPGRADE_*`; per-variable `-u` only; `OPENAI_BASE_URL=http://127.0.0.1:9/v1`; child-census proof: `POSTGRES_DB/AUTH_ENABLED/DECISION_ENABLED` all unset).

| | HEAD leg | BASE leg |
|---|---|---|
| collected | **35,375** of 36,574 (deselected 1,199) | **35,162** of 36,361 (deselected 1,199) |
| run set | **35,363** (12 perf nodes pre-excluded per the registry; compensating pin `regression_performance`) | the **492** HEAD-red ids, identical shape + id→shard mapping, 0 absent at base |
| outcome | **34,560 P / 470 F / 22 E / 305 S / 6 xfail** — closure **exact** (`34,560+470+22+305+6 = 35,363`; every shard closed on its own count) | **94 P / 376 F / 22 E** = 492 ✓ — base reds **398** unique |
| shards | 13 file-boundary shards (1 serial 77-node + 12 × ~2,941 xdist `-n 4`), per-shard `timeout 1750`, max wall 271 s; **879 nodes/min** aggregate; 0 dead shards, death-recovery unneeded | 12 groups, 524 s |
| digests | `(BARE-ID, 44091bc4cabc5bd6f2665c9adff3b68c73d610a0b70e32f16f0f9a0c375e5c83)` · `(GREP-FULL, 68d37436ce510eb36779ba902ff82e3701ff0eb569a6f6a787428e184aef11c2)` | `(BARE-ID, 5301d4fbbe38f5cf9d912183490c3b42013b84f86b59cbbe56ad15c7fc641037)` · `(GREP-FULL, 45d32ea1fb8f878496628a4e8bb5e7dcacc7ddd78aca46f2a6d4ff769af922c6)` |

Two contamination self-checks passed: BARE-ID count == pytest's own summary on both legs (HEAD 492 = 470+22 ✓, BASE 398 = 376+22 ✓); residual NON-NODE lines (53 HEAD / 58 BASE, all caplog `ERROR␣␣␣␣` records) reported separately and never polluting the BARE-ID form.

**Sweep delta → adjudication** (denominator **94** resolved candidates): tool GREP-FULL 98 / BARE-ID 97 (the 3–4 extra are reason-string variants of the same ids plus one caplog line). Adjudicated 3× per side serial, then 1-process-per-node ×3 for residuals:

| bucket | n |
|---|---|
| PRE_EXISTING (red 3/3 both sides) | **69** |
| ARTEFACT (green 3/3 both sides) | **23** |
| FLAKY-BOTH-SIDES (mixed on both legs) | **2** |
| **NET-NEW** | **0** |

The one candidate that looked net-new (`tests/test_chart_tools_reuse_integration.py::…::test_third_call_revives_revived_charter_unchanged` — HEAD F,F,F vs BASE P,P,P in the batch) resolved **green 3/3 solo on both sides** with the prior round's event-loop signature (`got Future attached to a different loop`) — an artefact, not a regression. The 2 flaky-both-sides ids are excluded by the protocol's own terms (mixed on both legs) and are named in the evidence pack.

**Last round's 3 residual net-new ids are all now GREEN at HEAD and at BASE** — the B-2 pair (`test_webfetch_builtin` ×2) and the B-3 guard (`test_source_gc_repository::…sanctioned_hunk`). The mechanical whole-tree leg confirms what the scoped legs found.

**Pre-existing baseline: 467 ids / 119 files** (was 469/122 last round — a like-for-like construction, same fence, same command shape; −2 ids / −3 files; the per-id diff is impossible because the prior 469-id list was never persisted, only its sha `4d81183d…`). Largest families (all red at BASE): `slash_commands_router` 25 · `defer_blocked_api` 24 · `test_injection_api` 22 · `spawn_team_members` 21 · `progressive_dispatch` 18 · `builtin_mcp_servers` 17 · `governor_integration` 16 · `memory_integration` 16. QUARANTINE cross-reference for the 94 candidates: 19 distinct files, 10 referenced by a row, 9 not — **after adjudication every id resolved to ARTEFACT or PRE_EXISTING; none is lane-caused**.

**Budget breach (loud): ≈102 min vs the ≤90-min Tier-2 advisory (+12 min).** Fully attributed to **two defects in the worker's own driver scripts** (H-1: a `read` variable named `PATH` clobbered `$PATH`, aborting 11 shards on the first drive pass; H-2: an argv bug passed the whole `bash -c` script as ONE argv element to `timeout` ⇒ **rc=127 for all 6 adjudication batches — and the first adjudication run printed a clean-looking `ARTEFACT 94 / NET-NEW 0` verdict from a run in which NOTHING executed**). Both were caught, fixed and re-run; **no shard was lost and no datum was lost**. H-2 is a **near-miss vacuous verdict** of exactly the class the house canon warns about — the recommended guard (rc ∈ {127, 4} or "no tests ran" ⇒ **VACUOUS, never a verdict**) should be added to the adjudication harness, and **never name a shell read-variable `PATH`**.

**Could not verify (this leg):** a full base census (the base leg ran only the 492 HEAD-red ids, per the recipe — same construction as last round, so the baselines are comparable but neither is a complete base census); `tests/integration/**` + `tests/postgres/**` (deselected by the repo's own `addopts` on both legs); the per-id diff vs the prior 469-id baseline (not persisted).

---

## 6. OPEN ITEM (A) — the strong-ref set: THE HONEST FRAME

**The strong-ref set is CHEAP INSURANCE AGAINST A DOCUMENTED HAZARD — neither a proven necessity nor a defect.** The unproven hazard does not read as proven; the unproven insurance does not read as a defect.

- **The natural trigger does NOT reproduce**, and the reason is the useful finding: on CPython 3.14 a suspended task is held by **three incidental strong edges** that the production set merely *replaces*, never removes — the awaited future's done-callback, `asyncio.timeouts.Timeout` (holds `_task` plus a live timer in `loop._scheduled`), and CPython 3.14's `fut._asyncio_awaited_by` frozenset. Measured: 200 bare tasks with refs dropped and `gc.collect()` interleaved every tick ⇒ **200/200 rows landed**, stock **and** uvloop 0.22.1.
- **The mechanism IS proven deterministically** once those incidental edges are severed (exact recipe published): the pending task is **collected mid-flight** — weakref dead, *"Task was destroyed but it is pending!"* — and **the consult never completes: rows = 0, ledger = 0.000000**. Identical under uvloop. That is precisely the failure mode the set defends against.
- **The production path is sound under identical adversarial conditions**: **200/200 rows × 3 rounds** (600/600) with `gc.collect()` every tick, stock **and** uvloop; 0 mid-flight collections; 0 sentinel deaths pre-completion.
- **Caveat carried:** the recipe removes the incidental edges, so it proves the weak-only-retention mechanism, not a natural production occurrence — the bare-`create_task` mutation therefore still does **not** discriminate on this runtime, exactly as the implementer recorded.

---

## 7. TWO CORRECTIONS TO MY OWN PRIOR REPORT (round 1, `6c2985a1c`)

1. **The adjudication buckets summed to 85 against a stated 84.** The "1 red at BASE only" id is not one of the 84 `comm -23` candidates; the correct split is 62 + 18 + 1 + 3 = **84**, with the base-only id outside the candidate set.
2. **"35,364 collected (deselected 1,199)" does not yield 35,352** — the 12-node perf pre-exclusion must be folded in.
3. **"ConnectError bills 0 (12/12)" was breaker-contaminated** — on a shared overlay only 5 of 50 attempts reached the socket; the rest were `breaker_open` and billed 0 trivially (R-A).

---

## 8. WHAT I COULD NOT FULLY VERIFY

1. **The manager's real boot path** — `initialize()`'s `reverify(["snapshot.l3.selection"])` and `set_settings_service`'s callback registration were verified **by reading** and by exercising the identical call shapes, not by booting a daemon (prohibited).
2. **Live proxy / live PostgreSQL / live daemon** — zero live contact by instruction. The transport doubles were audited against the recorded live contract and found **faithful** on every recorded row (404-with-envelope vs plain 404, 400-misroute vs shape-400, verbatim 502, 401/403/405, omissions, `noul`/`legend`/map-probabilities fail-open); two divergences stand — `answers[]`-out-of-order is never mocked, and the live `usage` **key names** are unpinned in the design of record.
3. **The 21/23 masked nodes in the repo's own CI** — the pre-existing `daemon/manager.py:1365` `service_tool` mask keeps them red, so the B-2 win is **not observable through the repo's suite** without a fixture or mask change; my proof used an in-memory mask-removal probe.
4. **The USD-ceiling bypass under a real provider** — the payload shape was reproduced locally; no live provider was contacted.
5. **uvloop as the daemon's actual loop** — uvloop was exercised by running probes under `uvloop.run`, not by booting the daemon.
6. **The whole-tree leg's per-node attribution of the 19 xdist artefacts** and the exact interleaving that produced them — proven non-reproducible without xdist, but the per-node cause was not isolated.
7. **`QUARANTINE.md` completeness** — 308 of the 469 pre-existing reds are not named by file path; the ledger indexes families by prose.
8. **The 15 digest values themselves** — unverifiable in either direction (D-4).

---

## 9. RECOMMENDED DISPOSITION

1. **D-1** — one line: `error = type(exc).__name__`, and drop `"error"` from the notify payload. **D-2** — widen the AST pin (flag `str()`/`repr()`, foreign except-names, intermediate variables, BoolOp flows) and prove it REDs on the real site. **D-3** — add a behavioural pin that drives `_trip` with a secret-bearing exception, counter-mutation-proven (the pack must go RED under a full leak).
2. **D-4/D-5** — publish the matrix operands (or drop the digests), re-point M6/M9, re-target M12, add the seven round-4 claims as legs.
3. **D-6** — apply the paste-ready §15 block (the command index reproduces every figure) and restore the lost producer-file correction.
4. **D-7/D-8** — correct the pack's header claim, extend the `-u` list, add the fence self-test, fix the PACKS.md last-run sha (`6c2985a1c` predates the pack's existence).
5. **D-9** — decide the USD-ceiling semantics (floor independent of tokens, **or** an unconfigured rate table is a non-startable state). Must be closed **before the seam is armed**, not before shadow staging.
6. **D-10** — assert the store-level delta in the `(e)` leg.
7. The B-3 hardening design (fixed base + per-line pin + file-presence pin) and the three named decisions (base model, lane-growth model, presence-pin scope) are handed over; the source-gc guard's MEDIUM residuals are a **next-lane** quality item, not a P1 blocker.
8. **Re-gate scope after the fixes (cheap)**: G7 + the round-4 pins + the matrix + §15 + the pack + a **base-proven re-run of the affected net-new ids** — **not** a full 35k-node re-sweep unless the fixtures move broadly.

---

*House artifacts:* this file is untracked in the lane worktree for the giter's evidence-rider (it is the tester's own re-gate report; a sibling worker observed it appear mid-run and correctly declined to touch it). The foreign telemetry `…/designer-agent/install-audit.jsonl` was never reverted, committed or deleted; it grew from 376 → 389 lines during the round, attributable by evidence to the **concurrent whole-tree leg running pytest from the repo root** (the known house rule: the audit path resolves from `Path.cwd()`), with control probes proving individual workers' runs write nothing. No commits, merges, pushes or staging were performed by this gate.
