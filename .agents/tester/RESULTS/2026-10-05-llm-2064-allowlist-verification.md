# Verification Report — fix/llm-2064-transient-allowlist

**Date:** 2026-10-05
**Verifier:** tester (dispatched) → worker `2cdcfa8f-3705-488b-a77b-a234503e191f`
**Branch / worktree:** `fix/llm-2064-transient-allowlist` @ `/home/nea/ensemble-src-wt-2064-allowlist`
**HEAD at start:** `0b1b380dd43a43eaa982c176854e9c9d210be5c6`
**HEAD at end:** `0b1b380dd43a43eaa982c176854e9c9d210be5c6` (no commits, no pushes, no branch changes)
**Final state:** `git status --porcelain` empty · `git diff HEAD --stat` empty · `config.yaml:185` byte-equal to committed state

---

## Scope Decision

Config-only fix: `config.yaml:185` allowlist gain of two entries (`'high load'`, `'(2064)'`) + one new unit test in `tests/unit/test_llm_error_classifier.py`. Blast radius: single module + single config line. **Full test suite not warranted.** Verification scoped to: new test (green), independent red reproduction, module suite, and config-consumer grep sweep.

### ensure.md mapping
- **In-scope (Core, Critical):** "No regressions in changed packs — every pack in the blast-radius change set returns PASS" → covered by module suite + consumer sweep (both 125 passed).
- **Out of scope (correctly excluded):** concurrency pack, dev.sh static check, Release Gate. This is a config-driven classifier change, not a concurrency/boot/release change; running those gates would be off-blast-radius.
- **No contradiction found:** the verify protocol uses scoped pytest commands with `timeout 300` wrappers, not bare unbounded `pytest tests/`, and no `-x`.

---

## Step 0 — Environment — **PASS** (~3s)

```
HEAD: 0b1b380dd43a43eaa982c176854e9c9d210be5c6  ✓ matches expected
branch: fix/llm-2064-transient-allowlist
git status --porcelain: (empty — clean)
.venv/bin/python: cpython-3.14 (avoids the 3.13 module-level forward-ref rot)
daemon.__file__: /home/nea/ensemble-src-wt-2064-allowlist/daemon/__init__.py
                  ✓ resolves inside worktree (not the main-checkout .venv trap)
```

`grep -n -B2 -A6 'transient_apierror_allowlist' config.yaml`:
```
183-  #     Corpus: 'All models rate limited' — 21 events 2026-08-19→26,
184-  #     instance deaths on attempt 1 of 10 (bug doc RC1/C1).
185:  transient_apierror_allowlist: ['all models rate limited', 'context deadline exceeded', 'high load', '(2064)']
186-  #   Timeout-body subset of the allowlist — relayed upstream timeouts
```
Both `'high load'` and `'(2064)'` present in the committed state. ✓

> Note: the worktree's Python 3.14 venv is the right call against the project's 3.13-rot trap (`Python interpreter rot (durable blocker)` in Repo & Dev Environment Conventions); the developer's choice to sync 3.14 in this worktree preserved a clean module collection.

---

## Step 1 — GREEN on committed state — **PASS** (0.56s)

```
$ timeout 300 .venv/bin/python -m pytest \
    "tests/unit/test_llm_error_classifier.py::TestTransientChannelClassification::test_2064_high_load_load_shed_via_config_pattern" \
    -v --tb=short

collected 1 item
tests/unit/test_llm_error_classifier.py::TestTransientChannelClassification::test_2064_high_load_load_shed_via_config_pattern PASSED [100%]

======================== 1 passed, 3 warnings in 0.56s =========================
```
Exit code: 0.

---

## Step 2 — RED reproduction — **PASS**, **RESTORE PASS** (~2s + ~1s)

### Edit (precise python read-modify-write, `count == 1` asserted)
```
old = "transient_apierror_allowlist: ['all models rate limited', 'context deadline exceeded', 'high load', '(2064)']"
new = "transient_apierror_allowlist: ['all models rate limited', 'context deadline exceeded']"
```

### `git diff --stat` (mid-edit):
```
 config.yaml | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```
Only `config.yaml` touched. ✓

### Same test re-run (FAIL as expected — strongest evidence the test is sensitive to exactly these two entries):
```
tests/unit/test_llm_error_classifier.py::TestTransientChannelClassification::test_2064_high_load_load_shed_via_config_pattern FAILED [100%]

...
daemon/llm_error_classifier.py:909: in _run_with_classification
    result = llm_with_tools.invoke(messages, **kwargs)
...
E   openai.APIError: server cluster under high load, please retry (2064)
------------------------------ Captured log call -------------------------------
ERROR    daemon.llm_error_classifier:llm_error_classifier.py:972 [LLM] Non-retryable API error: server cluster under high load, please retry (2064)

======================== 1 failed, 3 warnings in 0.89s =========================
```
Exit code: 1.

**Discriminator captured:** without the entries, the classifier's own log states *"Non-retryable API error"* — i.e., classification flips from transient+retryable → non-retryable. The error body still mentions `(2064)` and `high load`, so without the allowlist match, the body-substring branch does NOT fire. With the entries restored (Step 1 green), the same body would match `'high load'` (case-insensitive substring) and `'(2064)'` (substring on the parenthetical status). This is the strongest possible evidence that the new test asserts exactly the right behavior.

### Restore (`git checkout -- config.yaml`):
```
git status --porcelain:  (empty)
git diff HEAD --stat:    (empty)
sed -n '185p' config.yaml:
  transient_apierror_allowlist: ['all models rate limited', 'context deadline exceeded', 'high load', '(2064)']
```
Restoration clean, byte-equal to committed state. ✓

---

## Step 3 — Full module suite — **PASS** (0.97s)

```
$ timeout 300 .venv/bin/python -m pytest tests/unit/test_llm_error_classifier.py -q --tb=short
125 passed, 119 warnings in 0.97s
```
Exit code: 0. **Matches developer claim of "125 passed / 0 failed".** Zero collection errors.

---

## Step 4 — Config-consumer sweep — **PASS** (1.09s)

### `grep -rn -l -e 'transient_apierror_allowlist' -e 'configure_transient_channel_patterns' tests/`
```
/home/nea/ensemble-src-wt-2064-allowlist/tests/unit/test_llm_error_classifier.py
```
**Exactly one file** — 13 line-matches, all inside the same module. No spread of consumers across the test tree.

### Targeted re-run:
```
$ timeout 300 .venv/bin/python -m pytest tests/unit/test_llm_error_classifier.py -q --tb=short
125 passed, 119 warnings in 1.09s
```
Exit code: 0. ✓ (Same suite — only one file consumes the config keys, so the sweep collapses to the module suite.)

---

## Step 5 — Symptom restatement (in verifier's own words)

With `config.yaml:185` allowlist `['all models rate limited', 'context deadline exceeded', 'high load', '(2064)']`, an `openai.APIError(status=2064, message="server cluster under high load, please retry")` raised during a wake race classifies **TRANSIENT + RETRYABLE**. Evidence chain: Step 1 `test_2064_high_load_load_shed_via_config_pattern` PASSED on committed state (the allowlist matcher's body-substring branch consumed the body, retry path absorbed the error); Step 2 FAILED on the same test after removing only those two entries, with the captured classifier log explicitly stating *"Non-retryable API error: server cluster under high load, please retry (2064)"*. The test is sensitive to exactly those two config keys, and their presence flips classification from non-retryable to transient+retryable.

---

## Verdict Summary

| Item | Result | Evidence |
|------|--------|----------|
| ENV | **PASS** | HEAD match; clean status; worktree venv (Python 3.14) resolves to worktree; allowlist entries present at line 185 |
| GREEN | **PASS** | 1 passed in 0.56s, exit 0 |
| RED | **PASS** | 1 failed with captured log *"Non-retryable API error"* — discriminator confirmed |
| RESTORE | **PASS** | porcelain + HEAD diff both empty; line 185 byte-equal |
| SUITE | **PASS** | 125 passed in 0.97s (matches developer claim) |
| SWEEP | **PASS** | single matched file, 125 passed in 1.09s — no off-module consumers |

**Overall:** **PASS** — config-only fix verified independently; new test is sensitive to exactly the two new allowlist entries; full module suite is green; no off-module regression surface. Safe to merge `--no-ff` to `latest`.

---

## Constraints Honored

- ✓ Worktree only (`/home/nea/ensemble-src-wt-2064-allowlist`).
- ✓ Per-worktree venv (`.venv/bin/python`) — never the main checkout's venv; import path verified inside worktree.
- ✓ READ-ONLY on prod daemon (port 9797) — no API calls, no restart, no reload, no process contact.
- ✓ No commits, no pushes, no branch switches, no `--force` anything.
- ✓ Only permitted mutation: temporary Step-2 edit of `config.yaml` (immediately restored; final state byte-equal).
- ✓ No port 8088 / ensemble-self-system contact.
- ✓ Quick Fix Authorization: NONE — zero fixes applied, all failures reported as findings (there were none).
- ✓ Every test invocation wrapped in `timeout 300` (command-level); step-internal timeouts bounded per command.
- ✓ `test-pack-execution` skill applied for scope-locked single-test runs + dual-layer timeouts.

---

## Quick Fixes Applied (n/a)

None — this was a verification-only flow with Quick Fix Authorization explicitly **NONE**.

---

## Documentation Updated

- [x] `.agents/tester/RESULTS/2026-10-05-llm-2064-allowlist-verification.md` — this report
- [ ] `PACKS.md` — not modified (no pack scripts created or run in this verification)
- [ ] `LESSONS/` — no new entries (the Python 3.14 worktree-venv choice that avoided the 3.13-rot trap is already documented in `Repo & Dev Environment Conventions` blueprint)
- [ ] `MOCK_TESTS.md` — not relevant
- [ ] `QUARANTINE.md` — not relevant