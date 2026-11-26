# HANDOFF — v0.31.54 re-gate residual landing (3 bounded items)

**Commission:** `dump-health-residuals-v03154` — STANDARD sonic path, 3 items.
**Lane:** sonic-developer `476c4993-fcbb-4885-bd42-3526ea67a82a`.
**Integration branch:** `feature/sonic-v03154-residuals` — code integration tip `095e9c72892166c7627833a32342f351353a51ae` (the three module merges). This HANDOFF lives in commits stacked ABOVE that code tip — the initial handoff record plus a later DOC-ONLY polish commit (review follow-up: 2 MINOR + 1 advisory, markdown only); NO code change exists above `095e9c728`.
**Review/test:** REVIEW: PASS + TEST: PASS on `5accddb6e` (the tester's evidence stands for the code — the polish commit changes no code).
**Base (recorded, branched from after `git fetch origin latest`):** `ed8ab508cee09a05228cfead90ce0793d66e9829`.
**Fence honoured — NO release cut:** no version bump, no `bump_version.py`, no new `## [vX]` CHANGELOG section, no tag, no stage, no push to `origin latest` (nothing pushed at all). `.agents/tester/PACKS.md` and the five `por_*` packs untouched. No deletions. No force-push.
**Explore:** the single plan-open `explore()` call timed out at 300 s and returned nothing. No second knowledge call was made; the briefs were distilled from the commission text plus direct reads of the fresh tree. Recorded here as a lane residual, not hidden.

## Files touched across modules

Module A — `v03154-probe-overflow` (item 1) — `feature/sonic-v03154-modA` @ `f17a40a78ec37371384a555bcd2779fd36c932b9`
- `scripts/audit/dump_lane_staleness.py` (modified)
- `tests/unit/test_dump_lane_staleness_probe.py` (modified)

Module B — `v03154-pokgate-scratch` (item 2) — `feature/sonic-v03154-modB` @ `d416048b25e56fc41f2b6ef37939ff42c26c7f52`
- `scripts/pok_gate_prompt_mutations.py` (modified)
- `tests/unit/test_pok_gate_prompt_mutations_scratch.py` (added)

Module C — `v03154-doc-accuracy` (item 3) — `feature/sonic-v03154-modC` @ `a89c2cb69bb08df69f16eb6ffd0dfe769054b19e`
- `docs/runbooks/dump-lane-health-probe.md` (modified)
- `CHANGELOG.md` (modified)
- `.agents/shared/planning/sonic-team/dump-health-nits-v03154/HANDOFF.md` (modified)

Integration (developer-authored, this file): `.agents/shared/planning/sonic-team/dump-health-residuals-v03154/HANDOFF.md`

Merges (each `--no-ff`, zero conflicts): `8b594a5c5d923ad265fe157af552c077102f8ac3` (A) → `0496df8bad532b3d4f48c3c5e6e0bddfe9995c50` (B) → `095e9c72892166c7627833a32342f351353a51ae` (C).

## What changed

**Item 1 — R3(i) overflow hole (Module A).** `main()` computed `int(round(args.max_age_hours * 3600))` after checking only the *pre-multiply* finiteness. A finite-but-huge threshold (`1e305`) passed that check, the x3600 multiply overflowed to `inf`, and `int(round(inf))` raised `OverflowError` — a bare traceback at exit 1, outside the exit-78 family. The guard now computes the seconds FIRST and tests `math.isfinite(computed_seconds)`, so `nan`/`inf`/`+inf`/`infinity` AND `1e305` all land on the existing `_refuse(REASON_BAD_MAX_AGE)` path. No new slug: the closed reason vocabulary and the runbook one-row-per-slug pin are untouched. `"1e305"` was added to the spelling tuple; the node count stayed 39.

**Item 2 — shared scratch dir (Module B).** `SCRATCH` was a hard-coded global `/tmp/pok-gate-prompt-mutations`, so two concurrent runs against different checkouts overwrote each other's backup and one could restore ANOTHER CHECKOUT'S file; the old sha256 check ran AFTER the restore (detection, not prevention). Now: a unique `tempfile.mkdtemp(prefix="pok-gate-prompt-mutations-")` per invocation, removed in a `finally` on success/failure/interrupt; `restore_verified()` checks the backup's sha256 against the pre-mutation sha BEFORE copying it back, and on mismatch refuses to install it, rewrites the pre-mutation TEXT, and fails the leg loudly. That rewrite is byte-exact for the LF/UTF-8/no-BOM target it is used on — the sole LEGS target `daemon/services/pok_gate.py` (0 CRLF, no BOM, valid UTF-8; measured) — but it is NOT general: a CRLF-line-ending target would not round-trip byte-exactly (the text-mode read normalises CRLF to LF), and a BOM target is outside the measured set (unpinned). The docstring now states what is actually guarded. A hermetic 5-node test file was added (two concurrent PROCESSES against two throwaway checkouts with DIFFERENT pre-run sha256s; tampered/truncated backup refusal; good-backup non-vacuity; cleanup on a failing leg). A `POK_GATE_MUTATIONS_DRY_RUN=1` seam (off by default) stubs only the pytest invocations so the lifecycle can be pinned without real mutation runs.

**Item 3 — doc-accuracy corrections (Module C, TEXT ONLY).** (a) runbook §1: the false reason ("bf4bfde6e predates `scripts/audit/` / ships no `scripts/audit/`") replaced with the verified one (that checkout carries `scripts/audit/` but not the probe file); the conclusion, the v0.31.53-or-later precondition and both exit-78 guards were kept; §4 needed no edit (it references §1 without repeating the false reason). (a2) DELIBERATE EXTENSION, same false-statement class: the v0.31.54 CHANGELOG R1 bullet corrected in place; the separate "release payload ships `scripts/upgrade/` only" clause left alone. (b) nits HANDOFF: `every daemon/** file (no daemon code)` → `no daemon BEHAVIOR touched; daemon/__init__.py carries the version bump`. (c) nits HANDOFF: `both result sets` → `the result set`; the design doc §4.0 was verified to carry NO such phrase, so it was not edited. (d) the v0.31.53 CHANGELOG bullet now carries the `[corrected 2026-11-25 by the v0.31.54 slice: ...]` marker verbatim, placed where the dropped outage-duration claim sat. EXTRA (deliberate, same class): the nits HANDOFF `:56-57` repeated the same false bf4bfde6e claim and was corrected too.

## What is UNTESTED

- **Module B — `main()`'s refuse-to-restore path is untested end-to-end.** The new test file drives `restore_verified` in ISOLATION (tampered/truncated backup → refused, target not overwritten); NO node drives `main()` into the mismatch branch (backup sha mismatch → refuse to install → rewrite the pre-mutation text → leg FAIL → the green re-run is skipped). The isolated guard is covered; the end-to-end path through `main()` is NOT.
- **Module B — cleanup under SIGKILL/SIGTERM mid-run.** `try/finally` covers exceptions and `KeyboardInterrupt`, not uncatchable signals; a SIGKILL mid-leg leaves that run's scratch dir behind (it is unique per run, so it cannot contaminate another run — it is only litter).
- **Module B — the automated concurrency test's sensitivity to the PRE-fix code was not measured.** Under the dry-run seam the race window is milliseconds, so this is a post-fix regression pin, not a proven detector of the old bug. The authoritative evidence for the fix is the MANUAL interleaved pin (real pytest, no dry-run).
- **Module B — the manual pin's second checkout exited rc=1.** That is an artifact of the throwaway tree lacking the pytest target files (collection error rc=4), not a restore/guard failure; all four of its legs still restored sha-identically. Stated rather than hidden.
- **Module B — a stale pre-existing `/tmp/pok-gate-prompt-mutations` dir (dated 12:30Z, another lane) still exists.** The new code never uses that path; it was not deleted (out of scope).
- **No full-suite run.** Only the scoped files were run (probe 39 nodes + scratch 5 nodes) on the merged tree; the rest of the unit suite and every other lane's tests were not run.
- **No markdown lint / link check / render pass** on the edited docs (text-only change).
- **Integration seam:** the `1e305` pin was re-run end-to-end on the merged tree (green), but the probe was not run against a live database in this lane.
- **Module A boundary:** a finite value that survives the multiply (e.g. `4e304`) still proceeds as an enormous-but-valid threshold — intended, not tested as a refusal.

## Known risks

- **STALE PACKS.md ROW (owned by another lane — NOT fixed here, by fence).** `dump_lane_staleness_probe_unit_test` pins the probe's sha256 `9f87040f07628fd103d72002474c78641048f827e50a200059fb4886d775e153`; item 1 legitimately changes that file, so the probe now hashes `320ea02b245828f9fa166e7e0eb3be8d250671ac10e86f0c81f1b88b0a29939a`. The row's node count (39) is still correct. The coverage lane `d3997df2` owns PACKS.md — this row needs a re-stamp there.
- **The new test file is NOT registered in PACKS.md** (same fence). Until registered it is not gate-verifiable through the registry.
- **The dry-run seam is a new env-gated branch in a harness script.** It is off by default and only stubs `run_pytest`; a run with `POK_GATE_MUTATIONS_DRY_RUN=1` set by accident would report PASS/FAIL legs without running pytest. Low likelihood (the var is namespaced and documented only in the script and its test), but it is a new behaviour surface.
- **`restore_verified` rewrites the in-memory original on mismatch.** Within one process that leaves the tree clean; if the process died between backup and restore, the only remaining copy is the (refused) backup — the file stays mutated and the leg reports loudly. That is the intended fail-closed trade (better a loud mutation than a silent wrong-file restore), but it is a trade.
- **No release note for items 1 and 2.** By design: the v0.31.54 CHANGELOG section describes an already-cut release, so adding bullets there would be false, and a new section is fenced off. The next cut (the in-flight 0.31.58 lane) owns the release note for both fixes.
- **Runbook edited in a text-only module while the reason vocabulary is closed.** Verified safe: the runbook-coverage node is green on the merged tree (1 passed, 38 deselected), so no slug row was removed or duplicated.
- **Explore produced nothing** (300 s timeout). If the knowledge base held a convention relevant to these three items, it did not reach the briefs.

## How to verify

Run from the integration worktree `/home/nea/ensemble-src-wt-v03154-residuals`. A fresh worktree has no `.venv`; the canonical venv's python resolves `daemon` to the checkout because cwd precedes the editable `.pth` — verify that first.

```bash
cd /home/nea/ensemble-src-wt-v03154-residuals
/home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"   # must print THIS worktree

# 1) scoped suites on the merged tree — expect 44 passed (39 probe + 5 scratch)
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/test_dump_lane_staleness_probe.py \
  tests/unit/test_pok_gate_prompt_mutations_scratch.py -p no:randomly -q

# 2) item 1 pin — expect exit=78, ONE "RESULT:" line, ZERO tracebacks
/home/nea/ensemble-src/.venv/bin/python scripts/audit/dump_lane_staleness.py \
  --provider file --marker-file /dev/null --max-age-hours 1e305; echo "exit=$?"

# 3) item 2 — run the real mutation harness from a clean checkout; expect 4/4 PASS, rc=0,
#    and `git status --short daemon/services/pok_gate.py` BLANK afterwards
/home/nea/ensemble-src/.venv/bin/python scripts/pok_gate_prompt_mutations.py; echo "exit=$?"
git status --short daemon/services/pok_gate.py

# 4) item 3 — the corrected reason is the verified one
git ls-tree bf4bfde6e scripts/audit/          # 3 blobs, NO dump_lane_staleness.py
grep -n "carries .scripts/audit/. but not" docs/runbooks/dump-lane-health-probe.md CHANGELOG.md
grep -n "corrected 2026-11-25 by the v0.31.54 slice" CHANGELOG.md   # the v0.31.53 bullet
grep -n "no daemon BEHAVIOR touched" .agents/shared/planning/sonic-team/dump-health-nits-v03154/HANDOFF.md
grep -n "the result set are recorded" .agents/shared/planning/sonic-team/dump-health-nits-v03154/HANDOFF.md

# 5) fences — every line must print nothing
git diff --name-only ed8ab508cee09a05228cfead90ce0793d66e9829..HEAD -- .agents/tester/PACKS.md pyproject.toml daemon/__init__.py
```

## Bash-raises used

none — every command completed well inside the 600 s default; no raise was requested or consumed.

## Complexity self-assessment

**MEDIUM** (assessed before dispatch; no handover raised, no override involved).

Why not low: item 2 required a genuine cross-process race proof (two interleaved invocations against two checkouts whose target files must start with DIFFERENT sha256s, or the wrong-file restore is invisible), plus a new fail-closed guard and a new test file; item 1 changed an exit-code contract guarded by a closed reason vocabulary and a registered pack row; item 3 required verifying each "false" claim against the tree before correcting it.

Why not high: the three modules had disjoint file sets, each fitted one sonic-coder one-shot turn, there was no daemon behavior change, no schema/migration, no live-store write, no POK write, and all three merges resolved with zero conflicts.
