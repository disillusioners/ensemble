# Phase 8 — Staged release

**Depends on:** P7 (gates green is the entry condition). **Flags consumed:** — (consumes the standing rollback rule; carries flag #13's operator-lever note and DR-1/DR-3's visibility requirements).

## 1. Objective

Stage the release ceremony-ready: version slot derived at stage time, the full gate/stage chain, rollback-safe adjudication recorded under the standing rule, staging worktree + provenance sidecars kept, and the release/ceremony notes carrying the two blessed-text deviations visibly. **NO live promote — the user's 3-factor nonce ceremony owns the flip.**

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D8.1 | Version slot derived AT STAGE TIME per house W9: highest tag (local+remote) + `releases/` dir census + next free strictly above all — never hardcoded in the plan or code | every recent lane record carries the W9 convention (e.g. v0.28.6: "highest tag v0.28.5; live releases/ census; origin/latest unmoved") | reserving a version now (concurrent lanes invalidate reservations; the finalize-guard/reanchor pattern exists for exactly this) |
| D8.2 | Release composition: this lane's commits on `feature/ari-memoir-store` merged `--no-ff` onto the then-current `origin/latest`, bump commit EXACTLY the 4-file house set (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`) via bump_version.py (uv-PATH) or the disclosed manual 8f47661fe-semantics completion; CHANGELOG section below the live `[Unreleased]` if one exists | the house invariant (every recent lane record: "EXACTLY 4 files, 1 hit each") | `git add -A` ever; a CHANGELOG edit when no live Unreleased section exists |
| D8.3 | Rollback-safe adjudication (recorded in the stage journal + tag annotation): migration delta = **+1 file** (the four-table `.sql`), content additive `CREATE TABLE IF NOT EXISTS` ×4 + indexes; the PG `tsv` ensure is additive (`ALTER TABLE … ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`); **zero references to any memoir table/column in the pre-lane tree** (grep at the base = 0 hits) ⇒ the old binary has no model/code for any of it ⇒ INERT post-rollback (the board-tables v0.26.0 / notify_audit v0.27.8 adjudication class) ⇒ `ENSEMBLE_ROLLBACK_SAFE=1` source=explicit=true. CONSEQUENCE of rollback = FIX-LOSS (the leak returns; rows stay dormant in the DB), never incompatibility | the STANDING RULE (post-v0.25.4): a non-empty migration delta gets explicit semantic reasoning, not a reflex; every quoted adjudication class is from live lane records | reflex `=0` on seeing a non-empty delta (the conservative heuristic this house explicitly retired) |
| D8.4 | Stage per the house chain: `stage.sh` with `VERSION=v…` (v-PREFIXED — ADR-009 D3 guard; bare `0.x` is refused pre-build), L1 tip-identity, L2 provenance sidecars (git_head + dirty=false), staging worktree KEPT with BOTH sidecars (the v0.25.2 provenance-gap pattern avoided), payload source-less, prompt-artifact baseline 161/161 in tree AND payload | every staged_release_* record; the v0.27.11 stage-note ("VERSION must be the v-prefixed describe output") | pushing the tag before latest (f13a4f59e order: HEAD:latest FIRST, then the tag); deleting the staging worktree |
| D8.5 | Post-stage verification: 4-way sha (release dir == dist == manifest == sidecar), migration census embedded-in-binary vs live, isolated boot smoke (phase7's D7.3 conventions **including the embedding fence** and the worker census), live fence untouched (state.json byte-identical; `current->` unchanged; livez uptime continuous), one-way content markers (memoir symbols present-staged/absent-live) | the v0.28.x STAGED-VERIFIED shape (S1–S5) | trusting the manifest sha without re-hash ("stage re-hashed, never trusted the sidecar sha") |
| D8.6 | Ceremony handoff block: the promote command (env-first `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v… bash scripts/upgrade/promote.sh live --f2-verified-closed`), the **operator-lever note** (what OFF means for a running deployment — D8.7), the **two blessed-text deviations** (D8.8), the retention note (machinery-owned keep-3; protect live + pending), and the scripts-pin ancestry check if a scripted ceremony is used | house ceremony_notes convention | performing any promote-adjacent flip ourselves (explicitly out of scope) |
| D8.7 | **Operator-lever note (DR-3, visible in the ceremony notes):** `ENSEMBLE_MEMOIR_ENABLED=false` is a **clean, in-deployment, no-restart lever** for the whole memoir personality — the tools refuse `memoir_disabled`, the briefing is not built, the memory becomes **unreadable** (no other read path exists in v1), the embedding worker parks, the `inner_soul` ari branch still returns the loud not-recorded notice, and **the data is untouched** (the knob is not a delete). The knob is read at **call time**, so the flip needs no restart | the operator must know what OFF does to a LIVE deployment before the ceremony, not after | a knob described only as "disables the tools" (understates the blast — the memory becomes unreadable) |
| D8.8 | **Both blessed-text deviations stay VISIBLE in the release/ceremony notes** (never a quiet plan edit): **(DR-1)** the briefing's hard section ceiling is **24,000 characters** (tokens are the planning unit: persona ≤2,000, keepsakes ≤4,000 total, dynamic ≤3,000 oldest-first; the design's "~12k chars" survives as the NOMINAL size, typical fill ≈10–14k chars) — this **supersedes design §8.2's "~12k chars"** figure (surgically, per P-4 — `decisions.md` §4 row 3); **(DR-3)** the kill-switch polarity is a deliberate **divergence from the `notify_user` house precedent** (which repairs garbage to the default = ON = fail-OPEN): unset ⇒ config default ON with a loud per-boot WARNING; a present-but-unparseable value ⇒ INDETERMINATE ⇒ fail **CLOSED**/OFF + WARNING | the two supersessions are the only places the blessed design's literal text is superseded; P-4 requires them to be surgical AND visible | a silent plan edit (the council and the approver would never see the deviation) |

## 3. Tasks

| ID | Task | Outcome | Depends | AC |
|----|------|---------|---------|----|
| T8.1 | Pre-stage census: W9 derivation recorded; migration census 120 vs live 119; the zero-references grep at base archived | the derivation + census land in the stage notes | P7 | AC1 |
| T8.2 | Merge `--no-ff` onto origin/latest (conflicts: expect none — disjoint files; PACKS.md keep-both newest-first if touched) + bump + CHANGELOG + tag (annotated: the rollback adjudication + the no-promote disclosure) | the lineage is recorded exactly (merge/bump/tag shas) | T8.1 | AC2 |
| T8.3 | Stage (D8.4) + post-stage verification (D8.5) | the STAGED-VERIFIED report lands in `.agents/tester/RESULTS/`; the fences are proven | T8.2 | AC3 |
| T8.4 | Ceremony handoff + lane closeout: the operator-lever note (D8.7) + both deviations (D8.8) in the ceremony notes; the KV worktree claim released; the audit branch pushed; no promote | the handoff block lands in the lane report; `git status` is clean; nothing is pushed beyond the audit ref + latest/tag per convention | T8.3 | AC4 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | The version derivation is reproducible from the recorded census; the migration delta is exactly +1 with 120 distinct prefixes |
| AC2 | The bump is exactly 4 files / 1 hit each; the tag annotation carries the D8.3 adjudication verbatim-class reasoning; the push order is latest-first |
| AC3 | The 4-way sha matches; the boot smoke is green under the sweep-pin, per-variable PG scrub, **embedding-endpoint fence**, sandbox env, scratch DB, binary copy and live-fence byte equality; the memoir symbols are one-way present-staged/absent-live |
| AC4 | No promote fields are touched; no `git add -A` anywhere in the lane history (the commits are audited); the claim/branch hygiene is complete; the ceremony notes carry D8.7 and D8.8 verbatim |

## 5. Test packs

- The staged-verification legs (S1–S5 shape) are this phase's "packs" — executed by the verifying tester per house convention, registered in PACKS.md alongside P7's block.

## 6. Notes for the release lane

- The lane touches `agents/ari/{meta.json,tools_note.md}` ⇒ the release's `agents_tree_sha256` CHANGES vs live; the FE/plugins trees are byte-identical (daemon+agents release).
- If a concurrent lane stages between our merge and our stage, the finalize-guard/reanchor pattern applies (leader-adjudicated MERGE preserving gate anchors — the v0.28.5/v0.28.9 precedents).
- The embedded-binary `.sql` census includes our file (120) — the migration-delta leg must read the BINARY census, not the source tree alone (v0.27.13 method note).
- The two deviations (DR-1's ceiling, DR-3's polarity) are quoted in the ceremony notes so the promote-time reader sees them without opening this plan.
