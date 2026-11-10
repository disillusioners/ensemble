# EI gate 2026-11-10 — base-leg methodology lessons (explain_image related-dirs verification)

Gate: `fix/explain-image-related-dirs` @ `0efd07f03` vs base `9969f0c9b`. Full report: RESULTS/2026-11-10-explain-image-related-dirs-verification.md.

## 1. `git archive` base legs are INVALID for suites that shell out to git

- Symptom: archive-based aegis base leg reported **2F/554P** — a phantom extra failure `TestWorkerUntouched::test_worker_untouched` → `git diff failed: error: Could not access '1f6898ae...'`.
- Mechanism: `git archive` exports have NO `.git`; the test shells out to `git diff LANE_BASE_COMMIT` with `cwd=REPO_ROOT`. The commit existed (`cat-file -t` → commit) — the failure was the missing object store, not the code.
- Fix: `git clone --shared --no-checkout` + `checkout --detach <base>` (+ `.pth`/shebang rewrite, `__pycache__` purge, import proof) → artifact vanished, base = 1F/555P identical to prediction.
- Rule: BEFORE building any base leg, grep the target suite for subprocess/git usage. Pure-FS suites (prompt-integrity) are archive-safe — its archive leg reproduced the byte-identical failure signature (sha `549f04f5…` both legs).

## 2. Non-vacuity overlay: prove NEW tests gate the change

- Recipe (worker-codified as skill `new-test-red-at-base-non-vacuity-overlay`): extract base tree (archive suffices when the suite has no git shell-outs), overlay the BRANCH test file, run → the new nodes must go RED at base for the right reason (`workdir_invalid`), everything else green (76P/2F, failures exactly the 2 new ids).
- Stronger than "must pass at HEAD" (D1 rule): proves the tests actually pin the widened behavior.

## 3. Executing the BASE module beats quoting it

- For closing-shape proof, load `git show <base>:<file>` as a module and RUN it against the identical synthetic fixture (base REFUSED the related-dir image with an under-main green control) — an executed A/B, not a documentary one.

## 4. Small hygiene folds

- Solo/verification re-runs need `-p no:cacheprovider` too — otherwise pytest writes `.pytest_cache/v/cache/{nodeids,lastfailed}` inside the (read-only-gated) worktree. Gitignored ⇒ porcelain stays clean, but it IS a WT write; attribute peer writes by content (node ids you never ran) + mtime, don't delete others' byproducts.
- Pytest-session import proof (plugin printing `daemon.__file__` + cwd check + hard-fail exit) is stronger than `python -c "import daemon"` — the latter proves only interpreter resolution, not what the test session imports.
- Probe expectation over-specification is a probe bug, not a product bug: `relA/../../elsewhere` overshoots to a nonexistent path → refused as "file does not exist" (still refused, still no dispatch). Fix by adding the STRICTER sub-case (traversal resolving onto a REAL outside file → must refuse on containment), never by relaxing.

## 5. Process note (self)

- ensure.md tier recipe (`.agents/tester/skills/blast-radius-leg/`) should be read BEFORE dispatching legs, not at aggregation. The legs happened to satisfy its substance (D1/D2 classes, slicing proofs, FAILED|ERROR digests, scrub fences, budget); load order is still a deviation worth not repeating.
