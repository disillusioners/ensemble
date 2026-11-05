# Phase 8 — Staged release

**Depends on:** P7 (gates green is the entry condition). **Flags consumed:** — (consumes the standing rollback rule; carries flag #13's operator-lever note).

## 1. Objective

Stage the release ceremony-ready: version slot derived at stage time, the full gate/stage chain, rollback-safe adjudication recorded under the standing rule, staging worktree + provenance sidecars kept. **NO live promote — the user's 3-factor nonce ceremony owns the flip.**

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|----------------------|
| D8.1 | Version slot derived AT STAGE TIME per house W9: highest tag (local+remote) + `releases/` dir census + next free strictly above all — never hardcoded in the plan or code | every recent lane record carries the W9 convention (e.g. v0.28.6: "highest tag v0.28.5; live releases/ census; origin/latest unmoved") | reserving a version now (concurrent lanes invalidate reservations; the finalize-guard/reanchor pattern exists for exactly this) |
| D8.2 | Release composition: this lane's commits on `feature/ari-memoir-store` merged `--no-ff` onto the then-current `origin/latest`, bump commit EXACTLY the 4-file house set (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`) via bump_version.py (uv-PATH) or the disclosed manual 8f47661fe-semantics completion; CHANGELOG section below the live `[Unreleased]` if one exists | the house invariant (every recent lane record: "EXACTLY 4 files, 1 hit each") | add -A ever; CHANGELOG untouched when no live Unreleased (v0.27.x precedent) — check at stage time |
| D8.3 | Rollback-safe adjudication (recorded in stage journal + tag annotation): migration delta = **+1 file** (the four-table `.sql`), content additive `CREATE TABLE IF NOT EXISTS` ×4 + indexes; PG `tsv` ALTER-ensure is additive (`ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`); **zero references to any memoir table/column in the pre-lane tree** (grep at the base = 0 hits) ⇒ the old binary has no model/code for any of it ⇒ INERT post-rollback (the board-tables v0.26.0 / notify_audit v0.27.8 adjudication class) ⇒ `ENSEMBLE_ROLLBACK_SAFE=1` source=explicit=true. CONSEQUENCE of rollback = FIX-LOSS (the leak returns; rows stay dormant in the DB), never incompatibility | the STANDING RULE (post-v0.25.4): non-empty migration delta gets explicit semantic reasoning, not a reflex; every quoted adjudication class is from live lane records | reflex `=0` on seeing a non-empty delta (the conservative heuristic this house explicitly retired) |
| D8.4 | Stage per the house chain: `stage.sh` with `VERSION=v…` (v-PREFIXED — ADR-009 D3 guard; bare `0.x` is refused pre-build), L1 tip-identity, L2 provenance sidecars (git_head + dirty=false), staging worktree KEPT with BOTH sidecars (the v0.25.2 provenance-gap pattern avoided), payload source-less, prompt-artifact baseline 161/161 in tree AND payload | every staged_release_* record; the v0.27.11 stage-note ("VERSION must be the v-prefixed describe output") | pushing the tag before latest (f13a4f59e order: HEAD:latest FIRST, then the tag); deleting the staging worktree |
| D8.5 | Post-stage verification (this lane's tester or the commissioning lane's): 4-way sha (release dir == dist == manifest == sidecar), migration census embedded-in-binary vs live, isolated boot smoke (P7's D7.3 conventions), live fence untouched (state.json byte-identical; `current->` unchanged; livez uptime continuous), one-way content markers (memoir symbols present-staged/absent-live) | the v0.28.x STAGED-VERIFIED shape (S1–S5) | trusting the manifest sha without re-hash ("stage re-hashed, never trusted the sidecar sha" — v0.27.3) |
| D8.6 | Ceremony handoff block: promote command (env-first `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v… bash scripts/upgrade/promote.sh live --f2-verified-closed`), the flag #13 operator note (OFF is the clean rollback-adjacent lever: tools+briefing+worker off, data untouched), retention note (machinery-owned keep-3; protect live + pending), and the scripts-pin ancestry check if a scripted ceremony is used | house ceremony_notes convention | performing any promote-adjacent flip ourselves (explicitly out of scope) |

## 3. Tasks

| ID | Task | Outcome | Depends | AC |
|----|------|---------|---------|----|
| T8.1 | Pre-stage census: W9 derivation recorded; migration census 120 vs live 119; zero-references grep at base archived | derivation + census in the stage notes | P7 | AC1 |
| T8.2 | Merge `--no-ff` onto origin/latest (conflicts: expect none — disjoint files; PACKS.md keep-both newest-first if touched) + bump + CHANGELOG + tag (annotated: rollback adjudication + no-promote disclosure) | lineage recorded exactly (merge/bump/tag shas) | T8.1 | AC2 |
| T8.3 | Stage (D8.4) + post-stage verification (D8.5) | STAGED-VERIFIED report in `.agents/tester/RESULTS/`; fences proven | T8.2 | AC3 |
| T8.4 | Ceremony handoff + lane closeout: KV worktree claim released, audit branch pushed, no promote | handoff block in the lane report; `git status` clean; nothing pushed beyond the audit ref + latest/tag per convention | T8.3 | AC4 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Version derivation reproducible from the recorded census; migration delta exactly +1 with 120 distinct prefixes |
| AC2 | Bump = exactly 4 files 1 hit each; tag annotation carries the D8.3 adjudication verbatim-class reasoning; push order latest-first |
| AC3 | 4-way sha match; boot smoke green under the sweep-pin + per-var-scrub conventions; live fence byte-identical PRE==MID==POST; memoir symbols one-way present-staged/absent-live |
| AC4 | No promote fields touched; no `git add -A` anywhere in the lane history (commits audited); claim/branch hygiene complete |

## 5. Test packs

- The staged-verification legs (S1–S5 shape) are this phase's "packs" — executed by the verifying tester per house convention, registered in PACKS.md alongside P7's block.

## 6. Notes for the release lane

- The lane touches `agents/ari/{meta.json,tools_note.md}` ⇒ the release's `agents_tree_sha256` CHANGES vs live; FE/plugins trees byte-identical (daemon+agents release).
- If a concurrent lane stages between our merge and our stage, the finalize-guard/reanchor pattern applies (leader-adjudicated MERGE preserving gate anchors — the v0.28.5/v0.28.9 precedents).
- The embedded-binary `.sql` census includes our file (120) — the migration-delta leg must read the BINARY census, not the source tree alone (v0.27.13 method note).
