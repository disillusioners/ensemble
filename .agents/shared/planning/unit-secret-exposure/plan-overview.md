# unit-secret-exposure — Plan Overview

Overnight STAGE-ONLY security lane. The systemd unit generator (`scripts/upgrade/adopt-unit.sh`) mints `Environment=` lines from the install `.env` (PORT + all `POSTGRES_*`) into every generated unit; systemd exposes `Environment=` to any unprivileged local bus user via D-Bus (man 5 systemd.exec:2703-2708; live census: `systemctl show ensemble-main -p Environment` as a plain user returns the POSTGRES_PASSWORD value in cleartext). This plan set removes the minting at the generation seam (zero-env unit contract), amends every artifact premised on "the unit carries inline secrets", pins the contract with mutation-verified tests, and includes a bounded `.env` mode-0600 hardening. Live remediation is a documented user decision (runbook), never tonight's code.

Worktree: `/home/nea/ensemble-src-wt-unit-secret-exposure` (branch `fix/unit-secret-exposure` @ `9fc95176b8`, fenced `ENSEMBLE_SELF_ENV=dev` / `POSTGRES_DB=ensemble_dev` / `DAEMON_PORT=8079`). All anchors below verified read-only against this exact tree.

> **Mode-2 fold (2026-10-23):** architect **APPROVE-WITH-AMENDMENTS** — amendments **A1-A12** folded; canonical amendment record `architecture-recommendation.md` (unmodified input); fold map in `decisions.md` **D12**. Load-bearing: A1 (drop-in reset form → lone `Environment=` full-list reset), A2 (re-adoption = §5 teardown → §3(d) composite), A9 (corrected payload-delta/rollback adjudication input, §8).

## 1. Objective

Generated systemd units carry ZERO `.env`-derived `Environment=` lines (converging with the in-house zero-env precedents: both launchd plists, the host's ensemble-demo unit). Runtime env supply is unchanged — `launcher.sh` remains the single env source (load_env_file called env-first at `launcher.sh:1649`, last-wins), making every minted value redundant by construction. Docs, runbooks, and tests are amended to the new contract; the `.env` file becomes mode-0600 at every write site. Staged as the next free version above v0.27.5; **NO PROMOTE**.

## 2. Scope

- **In scope:** generator strip (adopt-unit.sh + unit template), launcher-seam verification + precedence-contract doc pin, docs/runbook contract amendment + ONE-TIME live-remediation runbook section (documentation only), test-pack replacement with mutation-verified zero-env pins, bounded `.env` 0600 hardening (3 write sites, ≤10 lines).
- **Out of scope (see §Non-Goals):** live unit edits, live `.env` edits, restarts, promote, any EnvironmentFile=/LoadCredential= mechanism, auto-regen of existing units, the coder-pool rider (routed out — premise disproved), historical archives.

### 2.1 Threat-model boundary (compressed faithfully from architecture-recommendation.md §2 — cite it for the full table)

- **What the strip CLOSES — exactly one surface:** the manager's stored unit `Environment=` list, re-served over D-Bus (`systemctl show -p Environment`) to **any local uid** (system bus socket world-connectable, property reads unauthenticated; the unit file's `install -m 0600` does NOT gate this — the manager parses as root and re-serves the parsed list). Closed class: **local, non-`nea`, non-root users** — for whom this was the **only** reachable secret surface on the host; for that class the closure is **total**. Temporal boundary: **future generations only** — the live Sep-30 unit stays exposed until the user's morning remediation (D10.1).
- **What it does NOT close (arch §2.2 surface table, now SIX rows — sixth added per W3, compressed):** (1) install `.env` — 644, `nea`-uid + root only before AND after 0600 (shield: `/home/nea` 750, empty `nea` group); (2) `/proc/<launcher|daemon>/environ` — owner/ancestors/root (Fernet master key + API keys live here — broader than the unit ever carried); (3) `.env.bak-2026-10-20T21:30:40Z` — 644, stale second copy (accepted residual, D10.6); (4) repo copies — `.env.prod` 600 (operator-created, W8), `.env.prod.demo` **644** (future generations covered by P5 per D5c; existing file = D10.7), `.env.backup` 600; (5) the live unit until morning remediation; (6) **transient executor units (W3)** — `daemon/tools/upgrade_journal.py:2156-2164` `EXECUTOR_ENV_PREFIXES=("PG",)` forwards every ambient `PG*` var (incl. `PGPASSWORD`) into D-Bus-readable `systemd-run` transient units; `POSTGRES_PASSWORD` excluded by name, prefix list unfenced; **NOT live today (verified)**.
- **Accepted residual (W3):** row (6)'s executor `--setenv` prefix forwarder stays open tonight — fence-or-narrow is a future commission (morning item D10.11).
- Post-strip the unit's stored env = `Environment=MALLOC_ARENA_MAX=2` only — no secret.

## 3. Non-Goals (explicit)

1. **Coder-pool rider — ROUTED OUT.** Premise disproved: nothing in-repo references `minimax-m3.1-flash-free`; `agents/worker/meta.json` + `agents/coder/meta.json` are pinned to single-string `coding` since v0.25.1 (pin intact at tip `9fc95176b8`). The defect is the live PROXY store's `coding-free` row over a deprecated upstream — a proxy-host-side fix, routed to the morning report (D7).
2. **Live unit edits / live `.env` edits / restarts / instance surgery** — never tonight. The live unit is a frozen write-once snapshot (clobber refusal `adopt-unit.sh:517-521` exit 78) never re-rendered by promote/restart/rollback (`promote.sh:418-428`+`:652-656`, `restart.sh:234-239`); the fix changes FUTURE generations only.
3. **Promote** — stage-only as the next free version above v0.27.5 (giter re-derives at stage time per house arbitration). NO PROMOTE under any circumstance; the user's 3-factor nonce ceremony owns it.
4. **EnvironmentFile= / LoadCredential=** — rejected alternatives (D1). EnvironmentFile= is additionally barred as an invariant (D9): never introduced anywhere.
5. **Auto-regen / heal mode for existing units** — no operator-surface code tonight; remediation stays a documented user decision.
6. **Historical archives / investigation docs** citing the old minting — left as historical records, note only (D11).
7. **Live contact** — no systemctl mutations, no DB writes, no boots against live, no paused-lineage touches, no shared-checkout or other-worktree writes.

## 4. Phases

| # | Name | File | Objective | Depends on |
|---|------|------|-----------|------------|
| 1 | Generator Strip | phase1-plan.md | adopt-unit.sh + template mint ZERO env lines; dead code + stale hygiene prose removed; keep-list preserved | — |
| 2 | Launcher Seam | phase2-plan.md | Verify zero functional change; pin the 4-rung precedence contract (.env > unit > set-environment > code default) in docs | P1 (comment coherence) |
| 3 | Docs + Remediation Runbook | phase3-plan.md | Amend every site premised on inline unit secrets; add the one-time live-remediation runbook (drop-in vs re-adoption, user decides) | P1, P2 |
| 4 | Tests | phase4-plan.md | Replace 8 asserts + remove 2 sections in test_adopt_unit.sh; add mutation-verified zero-env pins; spec the scratch boot-smoke fixture | P1, P5 |
| 5 | Bounded .env mode-0600 | phase5-plan.md | INCLUDE (≤10 lines): chmod 600 after every `.env` write site (deploy.sh, stage.sh ×3) — load-bearing because stage.sh's `mv -f` re-derives the mode from the temp's creation mode (post-W2-fold: `umask 077` fence + explicit chmod together enforce 0600) | — (before P4) |

## 5. Coupling Map

| | P1 | P2 | P3 | P4 | P5 |
|---|----|----|----|----|----|
| **P1** | — | template comments coherent | hygiene prose final shape feeds docs | **strong**: pins assert P1 output; must land together | — |
| **P2** | | — | precedence TEXT authored here, placed in P3's runbook pass | — | — |
| **P3** | | | — | — | independent |
| **P4** | | | — | — | mode pins added here |
| **P5** | | | | | — |

Shared files: `adopt-unit.sh` (P1 edits; P3 cross-ref only; P5 1-line comment), `ensemble-daemon.service` template (P1 :15-16/:106-109/:112; P3 :76-79), `systemd-adoption.md` (P2 precedence text + P3 rewrite + P3 new §), `tests/test_adopt_unit.sh` (**P4 edits; P5 APPENDS the mode pins** — reconciled per A7'), `stage.sh`/`deploy.sh` (P5 only).

## 6. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | Anchor drift at edit time (ranges verified @9fc95176b8; the lane's own edits shift lines) | medium | Dev locates by the exact content strings in each task table, re-greps before editing; drift corrections recorded in D6 |
| R2 | P1/P4 must land together — stale banner/masking asserts go red mid-stream | medium | P4 enumerates the exact old asserts (:350-352, :375, :377, :397-405 — W1-corrected); run test_adopt_unit.sh immediately after P1 |
| R3 | Accidental `EnvironmentFile=` introduction | medium | Invariant D9 + repo-wide grep gate in ACs (baseline: zero hits in scripts/launcher/tests today) |
| R4 | Stale "unit carries secret env" prose survives somewhere | medium | AC grep sweep for `carries secret env` / `secret env value masked` / `POSTGRES_* values` across scripts/ + docs/ (excluding oauth2-auth.md — unrelated mechanism — and historical archives) |
| R5 | Torn-token guard interaction after splice removal | low | Guard `:431-435` KEPT verbatim (D2) — now defends against a stale/hand-edited template; test :372 keeps passing (verified: pin checks `"__"` against generated output) |
| R6 | stage.sh chmod ordered wrong (before `mv -f` = useless; mv replaces the inode) | medium | T5.2 spells the insertion point: chmod strictly AFTER each successful mv/create |
| R7 | Checklist edits break the opt_o7 fence | low | KEEP the pinned literals `MALLOC_ARENA_MAX=2` + `adoption/unit-regeneration` (tests/test_opt_o7_checklist_fence.py:159-174) |
| R8 | Real-systemd mutation path unpinnable | medium | Honest disclosure: mutation verification runs at generator/DRY_RUN level (T4.4); §19 NAMED-SKIP precedent (`test_release_journal.sh:2474-2517`) documented |
| R9 | Secret values leak into artifacts | high | Hygiene rule: key names + value lengths only; no values anywhere (not even dummy fixture values quoted in artifacts) |
| R10 | New precedence text contradicts launcher.sh:144-145 launchd prose | low | :144-145 stays TRUE for launchd (plists already zero-env) — keep verbatim; new text scoped to the systemd rung |

## 7. Success Criteria (measurable)

- **SC1:** DRY_RUN preview from a fixture `.env` (PORT + all 5 POSTGRES_* keys) contains ZERO of: `Environment=PORT=`, `Environment=POSTGRES_`, `__ENV_LINES__`; contains the static template directives incl. `Environment=MALLOC_ARENA_MAX=2` (comment-stripped haystack).
- **SC2:** Zero-env pins proven mutation-meaningful: plant an `Environment=POSTGRES_PASSWORD=<dummy>` line in a throwaway template copy → RED; restore → GREEN; both legs recorded (house rule).
- **SC3 (rescoped per approver amendment N1, 2026-10-23):** scope = LIVE SURFACES ONLY — `scripts/`, `scripts/systemd/`, `docs/runbooks/` — EXCLUDING `.agents/shared/planning/` (W4 — this plan set's own text carries the `__ENV_LINES__` token and ships with the lane) AND `docs/investigations/` (historical archives carry LEGITIMATE frozen mentions by design, D11 — e.g. `docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md:140`). Gate: zero `__ENV_LINES__` hits after P1 within that scope; zero `EnvironmentFile=` additions vs baseline (D9 whitelist gate). (The earlier "repo-wide" wording was literally unsatisfiable — historical archives keep frozen mentions and must not be retro-edited.)
- **SC4:** `tests/test_adopt_unit.sh` green; all census-surviving pins untouched and green (full census in T4.5).
- **SC5:** No live-surface prose (script headers/comments, systemd-adoption.md, install-dir-protection-layer1-arm.md) still asserts the unit carries secret env; historical archives untouched.
- **SC6:** Remediation runbook section exists with BOTH options in mechanically-correct form — (a) drop-in with the **LONE `Environment=` full-list reset** (A1; per-variable empty-string form absent) and gate `systemctl show -p Environment` → NO `KEY=VALUE` entries (an emptied list still prints the bare `Environment=` line — that bare line is NOT a failure; do not re-apply anything over it); (b) the corrected **§5-teardown → §3(d)-adoption composite** (A2) with clobber precondition, interim-daemon stop step, backup hazard; honest citations, recommendation, explicit "user decides / no live action tonight".
- **SC7 (P5 — hygiene framing per A6):** every `.env` write site (deploy.sh install-copy cp :327 + chmod :330, the deploy.sh demo-guard `.env.prod.demo` generation (fence :251-252, mv :256, chmod :260 — post-fold anchors), `stage.sh` rewrite ×2 + create paths) followed by `chmod 600`; static byte-adjacency pins green + W5 mutation leg; behavioral pin green or documented-deferred; the ∅ exclusion delta disclosed (hygiene, not threat closure).
- **SC8:** Zero live contact (verifiable: no systemctl mutations, no boots against live, scratch-only fixtures with FAKE_HOME/mktemp fences, per-variable env scrub — never `env -u POSTGRES_*`).
- **SC9:** Lane contract honored: merge --no-ff → bump → tag → STAGE-ONLY above v0.27.5 (**re-derived from `releases/` dirs + LOCAL tags at stage time — never the remote tag**, W9) → NO PROMOTE.

## 8. Lane Contract & Downstream Gates

**Lane contract (from the shared Meta KV `wt.active.fix/unit-secret-exposure`; amended per W9):** dev in worktree → gates → merge `--no-ff` into `latest` → bump (exact 4-file house set: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`) → tag → STAGE-ONLY above v0.27.5 (**re-derived from `releases/` dirs + LOCAL tags at stage time — NEVER the remote tag**) → **NO PROMOTE** (user 3-factor nonce ceremony owns it).

**⚠️ Stage-time re-derivation hazard (W9 — spot-check-confirmed):** the REMOTE tag `v0.27.5` MIS-POINTS — peeled: remote → `9fc95176b` (== this lane's HEAD), while the staged release was built from the LOCAL tag → `6bebb9ba` (payload-diff proven; the unpeeled annotated-tag-object shas also differ). Any version/delta re-derivation that resolves the REMOTE tag lands on this HEAD and silently mis-adjudicates. **Use `releases/` dirs + LOCAL tags only.** The leader flags the anomaly to the giter + morning report.

**Live-boundary disclosure (W2 — leader carries into the morning report):** P5's stage-time chmod touches the **LIVE install `.env`** (`stage.sh:679` — `ENV_FILE="$INSTALL_DIR/.env"` IS the live install during a real stage). Benign, mode-only, ∅ exclusion delta — but it is a boundary delta inside a no-live-contact lane and is hereby **DISCLOSED, not silent** (see decisions.md D5).

**Fix-delivery path (A3):** `scripts/` does NOT ship in the staged payload (`stage.sh:395-425`; header inventory :5-16; `adopt-unit.sh` runs from a repo checkout, `:97`; deploy.sh never invokes it) — **the fixed generator reaches operators via an updated checkout post-merge; the staged release does not carry it.** The runbook (T3.7) and morning framing (D10.1) say so.

**Stage/rollback adjudication input — CORRECTED per A9 (architecture-recommendation.md §6, verbatim-faithful; supersedes the earlier "all payload trees byte-identical" briefing):**
- **What this change set ships: NOTHING in any payload tree.** Touches `scripts/` (generator + stage/deploy chmod — does not ship), `docs/runbooks/`, `tests/` only; `launcher.sh` untouched (P2 is verify+doc only); `.env` never ships (ADR-014/m6, stage.sh:20-21).
- **Expected manifest delta vs v0.27.5:** `agents/` **CHANGES** — 9 files, the pre-existing **gaia rider** merged into latest BEFORE this lane (v0.27.5 is an ancestor; merge-base verified) = **inherited base content, NOT this lane's delta**; `frontend/`, `plugins/`, `config.yaml`, `launcher.sh` — byte-identical; `ensemble-prod` — rebuilt (version bump + base-tip daemon content; this lane contributes no `daemon/` code). Manifest scalar delta: `version`, `binary_version`, `staged_at`, `binary_sha256`, `agents_tree_sha256` (+ agents per-file map).
- **Migration delta: EMPTY.** 109 .sql at this tip AND at tag v0.27.5 (`git diff v0.27.5..HEAD -- daemon/migrations/versions/` empty); this change set adds zero .sql (the 109th, wedge-capture, is already inside v0.27.5).
- **Runtime effect on the running service: ZERO** *(CONDITIONED — PRECONDITION, NOT ENFORCED: per approver amendment N3, 2026-10-23 and the leader ruling 2026-10-24, the stage must run as the INSTALL OWNER — the account named by the unit's `User=` — because `stage.sh:692-708` rewrites the live `.env` via `sed > tmp && mv -f`, an inode replacement that re-owns the file to the staging uid, and a 0600 `.env` owned by a different uid is unreadable by the daemon — the UNREADABLE case does NOT raise the launcher's "env file not found" WARN (that WARN fires only for a MISSING file): the real signals are the launcher's **"Permission denied"** while reading the `.env` + **zero exported keys** → the D4 boot-block family (exit-75 retry loop; the unit stays "active"; the journal is the only signal); the P5 `chmod 600` lands on that re-owned inode. Tonight's lane stages as `nea` — the install owner — so the precondition holds. It is a DOCUMENTED PRECONDITION ONLY: no mechanical stage-uid == unit-`User=` guard ships tonight (leader ruling: new operator-surface code immediately before a ceremony is not worth the risk) — that guard is deferred as a possible future lane (decisions.md D10.2). It is recorded as a standing stage precondition in decisions.md D5 and phase5 T5.2).* No daemon/ or launcher.sh changes; the live unit is frozen write-once and never re-rendered by promote/restart/rollback; P5 chmod changes file MODE only. A promote restarts the daemon against the same live unit + same `.env` → identical runtime env (launcher re-exports).
- **Rollback = symlink flip** to v0.27.5; schema unchanged (empty delta) → old binary compatible; `agents/` reverts to v0.27.5's tree = exactly the pre-promote live state (normal inherited-content rollback semantics, not a defect of this lane). No down-migration exists or is needed. **`ENSEMBLE_ROLLBACK_SAFE=1` is legitimate on this corrected basis** — zero schema mutation, zero this-lane payload-tree change.

**Downstream gates this plan feeds:** architect enrichment → reviewer plan review → fresh approver → developer → code review + tidier → tester → merge/bump/tag/stage → staged verification. This plan set is the input to the first two gates; expect Mode-2 delta-folds back into this directory.

## 9. ID Conventions & Assumptions

- `D#` decisions: continuous across the set; canonical ledger in `decisions.md` (phase tables reference it); **D12 = the Mode-2 fold record (A1-A12 → landing sites)**.
- `R#` risks: continuous across the set (§6 above).
- `T<phase>.<n>` tasks and `AC<phase>.<n>` acceptance criteria: phase-scoped (this set's declared convention).
- Assumptions: (a) host systemd is 255 (empty-string reset semantics valid, man 5 systemd.exec:2685-2687); (b) the live unit is the frozen Sep-30 generation; (c) fixture/test values are dummy — no real secret values appear in any artifact; (d) anchors verified @9fc95176b8 — dev re-greps by content at edit time (R1).
