# PLAN REVIEW — ENSEMBLE-31 "sonic team v1" overnight build (council deep review)

| | |
|---|---|
| **Verdict** | **APPROVE-WITH-NOTES** — unanimous (both councilors, independently) |
| **Review date** | 2026-11-12T20:46:44Z |
| **Mode** | 🔴 Deep-Review (governor council via `convene_council_with_skill`) |
| **Governor** | `104fef17-7193-48f1-a3d7-fb79dc7793b0` (instance name `review-council`) |
| **Councilors** | 2 COMPLETED — models `agentic` + `coding`; skill `plan-review` injected 1:1 per councilor |
| **Inputs** | Worktree plan set `.agents/shared/planning/sonic-team/` (11 files, worktree copies only; canonical checkout `/home/nea/ensemble-src` never read) |
| **Discipline** | Read-only throughout; councilors wrote no files; no 402 events; no read-only violations |

**Controller gate-order note (aggregation, reviewer-added):** MF-1 + MF-2 MUST land **before Phase 3 (dry-run) executes** — as written, the single permitted dry-run dies at its first agent turn and the overnight stages nothing. MF-3 + MF-4 MUST land **before Phase 4 (stage)**. Phases 0–2 may proceed immediately. The 15 advisories are non-blocking (cheap inline clarifications or morning-report items). The council's 10 overnight adjudications below are decided — carry them into the morning report. The plan-text edits themselves belong to the giter/leader lane, not the reviewer.

---

# COUNCIL SYNTHESIS — DEEP PLAN REVIEW — ENSEMBLE-31 "sonic team v1" overnight build

**Branch:** `feature/sonic-team-v1` @ `ac9849bf7` (= `origin/latest`) · plan set under `.agents/shared/planning/sonic-team/` · read-only review, worktree copies only

## VERDICT: **APPROVE-WITH-NOTES** — unanimous (both councilors, independently)

The plan set is unusually strong: mechanically-checkable ACs with a counter-mutation per leg, a complete and disclosed deviations ledger (no silent deviations found by either councilor), correct ceremony arithmetic (both re-derived every load-bearing constant against the tree — **zero mismatches** across their combined ~50+ checks), a genuinely rigorous dry-run fence, and a hard stage/no-promote stop. It is approvable **only with 4 MUST-FIX plan-text/env edits** — none is a design change. MF-1/MF-2 must land **before Phase 3 executes** (as written, the single permitted dry-run dies at its first agent turn and the overnight stages nothing); MF-3/MF-4 must land **before Phase 4**. Phases 0–2 may proceed immediately.

---

## MUST-FIX (prioritized)

**MF-1 🔴 — Phase-3 dry-run boots with NO LLM credential; the dry-run cannot prove the sonic loop.** *Found independently by both councilors.*
- Evidence: `dryrun-boot-procedure.md:150-168` — the `env -i` block forwards only `POSTGRES_*/PORT/HOME`, **no `OPENAI_*`**; the worktree fenced `.env` carries only `ENSEMBLE_SELF_ENV` + `POSTGRES_*` (both councilors verified the names); `daemon/config.py` reads `OPENAI_*` from process env — **no `load_dotenv` anywhere in the boot path**. The procedure was validated boot-path only (`dryrun-boot-procedure.md:5-6`), so `/readyz` green proves nothing about the LLM lane.
- Consequence: D4–D7 (`phase3-dryrun-spec.md:58-63`) — the real coder→reviewer→tester turns that are the entire point of the dry-run — die at the first LLM call → F4/F5-class failure → the single permitted run is consumed (`phase3:14`, no retry) → **C8 ⇒ stage NOTHING → overnight objective dead.** This is the one gate that stops the sequence dead overnight.
- Fix: forward `OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL_BACKUP` from the invoking shell into the `env -i` block (agentic verified all four are present in the invoking shell); state that shared-proxy provider contact ≠ live daemon/DB/install contact (C1-safe); add a **D3.5 lane probe** (verify the key is present in the scratch pid's environ, or one trivial dispatch) explicitly classed **F1-environment/pre-flight** so a missing key can never burn the single run as an F5.

**MF-2 🔴 — Secret-hygiene pins absent from the dry-run fence (F7 / wi-2454ede3 incident class).** *Agentic MUST-FIX; coding folds the same substance into its MF-1 fix.*
- Evidence: `dryrun-boot-procedure.md:141,149` — `set -a; . ./.env` loads the dev DB password into the agent shell and creates `/tmp/ensemble-sonic-dryrun/{data,logs}` with only `chmod 700 home`; `phase3-dryrun-spec.md:40-50` (§3 pin list) has **no** umask rule, no ambient-env-capture ban, no evidence-redaction rule.
- Stakes: MF-1 deliberately introduces the live proxy key into that scratch env — without hygiene pins this recreates exactly the F7 exposure (live key in world-readable scratch files; the standing deferred-rotation concern).
- Fix (same edit window as MF-1): add pins **(j)** `umask 077` on `/tmp/ensemble-sonic-dryrun/**` + the evidence bundle; **(k)** never dump/capture ambient env into logs or evidence — if env evidence is needed, capture a redacted `name + sha256-prefix` block, never raw values; **(l)** scrub the evidence bundle before commit/attach. Cite the F7 rule.

**MF-3 🟠 — D16 "no live install dirs" contradicts Phase 4's sanctioned stage-live write-path.**
- Evidence: `decisions.md:26` (D16) vs `phase4-stage-runbook.md:19` (C7 "this runbook stages live"), `:62-68`, `:82` — `stage.sh live` necessarily writes `$HOME/agents-ensemble/releases/$VERSION` and idempotently sets the install `.env` marker. A lane reading D16 literally refuses Phase 4; one reading the runbook violates D16.
- Fix: reconcile in one line — "live contact" = live **runtime state only** (daemon port, live DB, `current` symlink flip, `state.json` promote fields, promote/nonce, restart); the `releases/` staging write-path is the Emperor-sanctioned STAGE act. Record per D15.

**MF-4 🟠 — Bump/tag/push actor unnamed while D16/D17 route ALL commits through giter tonight.**
- Evidence: `phase4-stage-runbook.md:40-50` (§2.3 bump "makes a targeted commit") and `:52-58` (§2.4 tag + push) never name the actor; `decisions.md:26-27` binds every lane's commits to the giter lane with pre-approval.
- Fix: name the giter lane for §2.2–2.4 with a pre-approval envelope (bump + annotated tag + push on `latest`), or record an explicit D15 leader exception; and repeat the "NEVER the canonical checkout `/home/nea/ensemble-src`" fence **inline at §2.3** — the canonical checkout is a detached HEAD carrying a 6-entry staged index plus 15 files of unstaged WIP, and the bump step is exactly where a tired operator reaches for it.

---

## ADVISORY (non-blocking; cheap spec clarifications during implementation, or morning-report items)

1. **Pack glob coverage:** Phase-2 A7 enumerates **9** globs while the authoritative frozen block has **14** (`phase2-test-pack-spec.md:81` vs `phase1-implementation-spec.md:197-213`). Derive the pack's glob constants exactly from the 14-line `[sonic-blast-radius-v1]` block so the registered gate cannot go green while `daemon/routers/**` or `scripts/**` coverage silently drops.
2. **Frozen-string legs:** A10/A11 grep only the `'ESCALATION: '` prefix and note header (`phase1:432-433`); no pack leg covers the full `[sonic-escalation-v1]` line or §5.2 note body. Add diff-based checks (content review covers it tonight; harden for CI).
3. **`daemon/constants.py` exclusion asymmetry:** `phase1:499` excludes it while keeping `daemon/routers/**`; constants.py holds pool size 5 and loop-breaker/recursion defaults. Documented; acceptable for v1 under C4 — revisit in v2.
4. **Base-red bookkeeping artifact unnamed:** overview §8 / A23 require the tester gate to "explicitly adjudicate" the 4 canary-rooted reds, but no destination artifact is named. Pin it (QUARANTINE.md row vs gate verdict file) + base evidence pointer.
5. **D4 wording:** "applied via the DB-versioned lane" (`decisions.md:14`; `phase1:15,374`) could be read as a live-DB prompt-artifact write (C1 conflict). Reword: the Ari edit is a **repository file edit committed via giter**; no live DB / prompt-artifact state-machine write tonight.
6. **Phase-1 exit circularity:** `phase1:466` gates Phase-1 closure on A29, which is the Phase-2 pack (`phase2:13`). Resolve: Phase-1 exit = A1–A28 green; A29 is the Phase-2 gate that unblocks Phase 3.
7. **Rule (i) restatement:** `ENSEMBLE_DB_AMBIENT_ALLOW=1` is correctly pinned in the procedure (`dryrun-boot-procedure.md:56-60`) but absent from the phase3 §3 pin list (`phase3-dryrun-spec.md:40-50`) — restate it so AC-P3-3 can assert it from the spec.
8. **phase3:76 token grep casing:** greps lowercase tokens vs Title-Case headers ("## Files touched") — specify `-i` or exact header strings.
9. **C7 "operator confirms"** (`phase4:19`) is a human-ask inside an overnight runbook — pre-resolve now per D15/A1d (the Emperor's STAGE directive satisfies it) and record the adjudication.
10. **Version derivation:** `phase4:31-36` uses local `git tag --list` — this host has documented local/origin tag divergence (POK release-02). Derive from a remote-aware listing (`git ls-remote --tags origin` cross-checked) and record the source.
11. **×7 budget honesty:** a tiny dry-run feature never approaches 2100 steps — the morning report should call the multiplier **config/pin-proven** (A4/A5), not wall-proven. (Also: plan cites `config.yaml:190`; the worktree value sits at `:204` — line drift, value 300 verified.)
12. **"8 modified = 1+7" framing:** `phase1:410,438`/A16 vs `plan-overview.md:170`/DEV-1 — `ari/rule.md` is both pre-existing-drifted and edited this lane; state plainly in the morning report: "8 modified rows vs committed base = ari/rule.md (drifted at base, re-hashed this lane) + 7 pre-existing drift repairs."
13. **Pack normalization:** frozen `sonic-coder.deny = []` (`phase1:178`) vs meta.json omitting the key (`:110-113`) — a naive `== []` assertion KeyErrors. Normalize absent → `[]` before comparing.
14. **Worktree contention:** the dry-run boots and `uv venv`s inside the giter-owned commission worktree (`phase3:58,69`) — quiesce the worktree during D2 or boot from a copy; confirm Phase-4 cleanliness after (venv is gitignored).
15. **Staged RAM guard:** if the overnight daemon carries the staged v0.29.21 guard, large `uv sync`/pytest trees in Phases 1–2 may be tree-killed (a `ram_breach_observed` marker fired during this very review on ordinary commands). The timeboxes partially bound this — mind it.

---

## Overnight adjudications (council decisions, per the Overnight Rule — record all in the morning report)

1. **D13 / DEV-1 8-row baseline repair: INCLUDE** *(both councilors, independently).* Excluding is not merely worse — it **deadlocks Phase 4 by the plan's own gate** (`phase4 §3.3:105-109` explicit `--check`: any drift ⇒ STOP; the stage-side leg is allow-stale by design, so the explicit check is the only guard). All 8 rows verified byte-exact; blast radius is the baseline file's row values only, bounded by A28 (steady-team files untouched — A28 judges file content) and A16 (exact delta shape).
2. **D8/D9 blast-radius superset (routers in): KEEP** *(both).* Escalate-only, prompt-realized; false positive = one escalation hop, false negative = a load-bearing API edit; `daemon/routers/**` = 146 defect-set touches (`findings-C §1d`). The predicate being prose-not-infra argues *for* erring safe. Leader retains a one-line loosening option.
3. **Baseline 181→197: CONFIRMED** *(both).* 181 verified by row count; +16 = 4 dirs × 4 artifacts against the 8-entry `VERSIONED_PROMPT_ARTIFACTS`; skills-template contributes 0 rows; ari note is a hash-only row.
4. **Seal/terminology ceremony: CONFIRMED correct** *(both).* Re-pin anchored on the **current** seal `fc0a7542…` (`test_chart_image_delivery_e2e.py:1513`, prior `8063c9ce…` in comment); re-seal step present (§5.3-2); 35-row seal-map invariant preserved (agentic re-hashed all 35 — 0 stale); `workflow.md` seal deliberately untouched; duty pack `SEAL_REPINNED_BASE_VALUES` already contains `ari/rule.md` so the re-pin needs **no duty-pack seal edit**; exactly-1 `plane` in rule.md, 0 in workflow/soul, no `## Cardinal`.
5. **Duty/base-red adjudication (D2/D3): CORRECT, no silent re-baselining** *(both).* Canary lacks the block (root, verified); constants 45/43/177 stale vs actuals 46/44/181 (walk excludes `_` dirs); the 3 count-nodes going green via registration is population truth; the 4 canary nodes ride base-attributed; **A20a as independent proof is the right design** (the pack loop fails on canary first and cannot prove sonic compliance).
6. **A26 `snapshot_enabled: true`: ACCEPT** *(both).* Exact-list pin `["developer","developer[v2]","tester"]` verified (`test_snapshot_agent_spaces.py:212-216`); inert in v1; documented false-fallback stands.
7. **Leg A numbers: admissible steering, NOT invention** *(both).* 46.4% kb-writer share, 8–41h walls, p50/p90, 79× timeout are dispatcher-supplied, labeled `[Leg A]` throughout — keep the provenance label when cited as success metrics. Every claim traceable to findings-B/C checked out; **no invented mechanism found** (snapshot behavior, recursion ×7→2100, pool=5, 2096/125-min cap all verified).
8. **AC quality: sound.** 29+ ACs mechanically checkable, non-vacuous, counter-mutation leg per assertion; the `ac-comment-token-grep-collision` trap checked by both — **does not fire** (A8a scoped and justified at `phase2:85`, non-comment scoping at `:71`; every other negative grep scoped to meta.json arrays, never prose; mandated additions contain zero `plane`).
9. **Scope fence: HOLDS.** Content-only (20 new agent files + ari note + baseline regen + tests), zero migrations, zero daemon code, steady team untouched; C1–C8 honored as written (modulo the MF-3 wording reconciliation).
10. **Phase ordering build→pack→dry-run→stage: HOLDS**, hard stop before promote intact — with MF-1 as the single blocker that would otherwise stop the sequence dead overnight, and advisory #6 resolving the one circular gate.

## Councilor disagreements (surfaced transparently)

- **D16-vs-Phase-4 severity:** coding rates the contradiction **MUST-FIX**; agentic rates the same finding (same file:lines, same one-line fix) **ADVISORY** ("intent is clearly stage-yes/promote-no, but the contradiction should be explicit"). **Governor ruling: MUST-FIX stands** — in a no-human-ask overnight regime, a constraint ambiguity that makes a lane either refuse Phase 4 or violate D16 is operationally load-bearing, and the fix costs one sentence.
- **Nine-rule completeness nuance (reconciled, not a conflict):** coding reports rules (a)–(i) "complete, verbatim-pinned"; agentic notes (i) `ENSEMBLE_DB_AMBIENT_ALLOW=1` is pinned in the boot procedure but missing from the phase3 §3 spec list. Both are true at different scopes — advisory #7 captures the fix.
- Coding's MF-4 (unnamed bump/tag/push actor) was not independently raised by agentic; it is uncontradicted, verified against `decisions.md:26-27`, and stands.

## Honest limits (carried from both councilors)

- Neither councilor executed pytest (read-only; caches would dirty giter's worktree) — the duty pack's 7-failed/12-passed base counts are verified **statically** and remain to be proven live by A23 at implementation.
- Phase-2 resolver-import claims (`_auth.py` stdlib-only; `get_registry()` config-free) taken on findings citations — the pack's fail-closed design converts a wrong claim into a loud FAIL, not a silent pass.
- R2 watchover 3-strike quantification is carried by the plan as an open evidence gap; the dry-run is the probe.

**Bottom line:** approve the plan set. Apply MF-1 + MF-2 before the Phase-3 dry-run executes; apply MF-3 + MF-4 before Phase 4; treat advisories as cheap inline clarifications or morning-report items; record the ten adjudications above in the morning report. No promote, no live runtime contact, stage-only — as specified. Positive highlights worth preserving in the record: the fd-fence dry-run procedure is not just pinned but **proven** with pre/post evidence (`:298`); phase4 §7.2+§3.3's explicit stage-drift check is the single best defect-catch in the plan set; exactly one registered pack + PACKS.md row honors the aegis lesson.
