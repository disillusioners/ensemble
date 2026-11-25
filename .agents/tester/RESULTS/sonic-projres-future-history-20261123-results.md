# SONIC TESTER RESULTS — projres-future-history-20261123

**Verdict: TEST: PASS** (merged-tree acceptance gate green; zero net-new reds)

| Field | Value |
|---|---|
| Commission | `projres-future-history-20261123` (standard path, sonic lane) |
| Worktree | `/home/nea/ensemble-src-wt-projres-fh-integ` (integration, kept) |
| Diff range | `1b70a58ca..449163f17` (base..merged) |
| Merged tip | `449163f17309bdb0beec5c68a4754f4877854979` (== `origin/latest` == `v0.31.38^{}`) |
| Handoff | `.agents/shared/planning/sonic-team/projres-future-history-20261123/HANDOFF.md` |
| Tester | sonic-tester — independent re-run; developer numbers NOT trusted, reproduced |
| Date | 2026-11-23 |

---

## 1. Blast radius (derived from the FULL merged diff, not the handoff's list)

`git diff --name-status 1b70a58ca..449163f17` — 15 files:

| File | Change | Test surface |
|---|---|---|
| `daemon/tools/project_future.py` | M (+210) — triad resolution on 3 tools | pack leg 3, 4, 5 |
| `daemon/tools/project_history.py` | M (+200) — triad resolution on 4 tools | pack leg 4, 6 |
| `daemon/tools/project.py` | M (+3) — `project_get` AMBIGUOUS carries `query`; annotation fix | pack leg 1 |
| `daemon/__init__.py` | M — version `0.31.37`→`0.31.38` | §5 seam |
| `pyproject.toml`, `uv.lock` | M — version bump | §5 seam |
| `CHANGELOG.md` | M — `[v0.31.38]` section | §5 seam |
| `tests/test_api.py` | M (1 line) — version pin `0.31.37`→`0.31.38` | §4 |
| `tests/test_project_resolution.py` | M (+1) — `query` assertion | pack leg 1 |
| `tests/unit/test_project_future_resolution.py` | **A** (NEW, 330) | pack leg 5 |
| `tests/unit/test_project_history_resolution.py` | **A** (NEW, 526) | pack leg 6 |
| `tests/unit/test_project_future_tools.py` | M (+119) | pack leg 3 |
| `tests/unit/test_project_history_tools.py` | M (+28) | pack leg 4 |
| `.agents/tester/PACKS.md` | M (+8) — pack registration | §6 |
| `HANDOFF.md` | **A** | doc |

**Not in range (deliberately):** `daemon/repositories/project/repository.py` — the ladder
(`resolve_project_reference`) itself is INHERITED from the prior `project_get` fix. This
lane is pure tool-layer wiring, so the pack's scope is the tool layer. Correct.

---

## 2. Packs run (per-pack counts)

All runs: worktree `.venv` only (never the main `.venv`), `PYTHONPATH=$WORKTREE`,
`ENSEMBLE_SELF_ENV=dev` explicit (the ambient shell carries `ENSEMBLE_SELF_ENV=live` —
the explicit marker wins), `POSTGRES_*` scrubbed **per variable** (the `env -u POSTGRES_*`
glob form is a silent NO-OP — bash does not glob env names).

| # | Pack / leg | Result |
|---|---|---|
| 1 | **Registered pack `project_future_history_resolution`** — 6-file command VERBATIM from `PACKS.md`, on the merged tree | **218P / 0F**, 21.99s, **0 skipped** |
| 2 | Same registered pack, RE-RUN after the mutation-probe restore | **218P / 0F**, 21.99s (reproduced twice) |
| 3 | Rider `tests/unit/test_project_future_registration.py` | 8P / 0F |
| 4 | Rider `tests/services/test_blueprint_phase3_hooks.py` (C8 pending-queue hook, resolved-id threading) | 20P / 0F |
| 5 | New registered pack `project_resolution_triad_runtime_proof` (runtime driver, §3) | **35/35 checks PASS**, `RESULT: PASS` |
| 6 | `tests/test_api.py` (in-range touched) — A/B, §4 | see §4 |
| 7 | `tests/unit/test_wedge_capture_wiring.py` (version-bump seam) | 3P / 0F |

Registered pack command, verbatim:

```
cd /home/nea/ensemble-src-wt-projres-fh-integ && env -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL ENSEMBLE_SELF_ENV=dev \
  PYTHONPATH=/home/nea/ensemble-src-wt-projres-fh-integ .venv/bin/python -m pytest \
  tests/test_project_resolution.py tests/test_project_tools.py \
  tests/unit/test_project_future_tools.py tests/unit/test_project_history_tools.py \
  tests/unit/test_project_future_resolution.py tests/unit/test_project_history_resolution.py \
  -q --tb=short -p no:randomly --timeout=60
```

```
........................................................................ [ 33%]
........................................................................ [ 66%]
........................................................................ [ 99%]
..                                                                       [100%]
218 passed, 1 warning in 21.99s
```

**Independent reproduction: the developer's 218P/0F is CONFIRMED.** Zero skipped —
every collected node executed (no `s` in the progress stream), so the directional
assertions are LIVE, not skipped.

---

## 3. Directional spot-proof of the Emperor's case (INDEPENDENT runtime driver)

`test/packs/project_resolution_triad_runtime_proof.sh` (NEW, registered) drives the 7
tools **directly** against a REAL `SQLModelProjectRepository` over in-memory SQLite.
This is a driver, not a replay of the developer's test files, so the verdict rests on
observed runtime facts.

| Behaviour | Coverage | Result |
|---|---|---|
| **A** shortname `ensemble` → project `agents-ensemble` | all 7 tools (future add/list/remove + history add/list/search/delete) | 7/7 PASS |
| **B** id path never calls the resolver (call count == 0) | all 7 tools | 7/7 PASS |
| **C** ambiguous → `AMBIGUOUS_PROJECT`, 2 candidates, query echoed, no silent pick | all 7 tools | 7/7 PASS |
| **D** unknown → typed `NOT_FOUND`, query echoed, `candidates: []` | all 7 tools | 7/7 PASS |
| **E** no reference → `Must provide either project_id, name, or shortname` | all 7 tools | 7/7 PASS |

**35/35 checks PASS.** Observed: every one of the 7 tools returned the SAME resolved
`project_id` (`534c6e7a-…`) for `shortname="ensemble"`; the resolver spy recorded
`resolver_calls=0` on all 7 id-path invocations; the ambiguity refusal listed both
candidate ids and carried **no** `project_id` key.

### The new test files DO assert this (read + confirmed)

`tests/unit/test_project_future_resolution.py` (330 lines) and
`tests/unit/test_project_history_resolution.py` (526 lines) assert, per tool:

- `test_{add,list,remove}_resolves_shortname` / `test_{add,list,search,delete}_resolves_shortname`
  — `shortname="ensemble"` → the `agents-ensemble` project's id;
- `TestExactNameResolution` — `name="agents-ensemble"` resolves on the first ladder tier;
- `TestIdPathUnchanged` — `test_id_path_never_calls_the_resolver` (a `mock.patch` spy in the
  future file, a `_NoResolverStore` proxy that raises on any resolver call in the history
  file), plus `test_id_path_miss_is_typed_not_found` and `test_explicit_id_wins_over_name`;
- `TestAmbiguousReference` — `error_code`, `query`, `candidate_count`, and the candidate
  id set; `test_ambiguous_candidates_name_both_projects`;
- `TestUnknownReference` — exact-dict equality on the typed `NOT_FOUND` shape;
- `TestNoReference` / `TestMissingReference` — the must-provide refusal;
- `TestPendingQueueHookSeam` — the C8 Blueprint pending queue is driven by the RESOLVED
  project (`pending_repo.enqueue(project_id=<resolved>)`, `source_type="history"`).

**LIVE, not skipped** — proven two ways: the pack run shows `218 passed` with no skip
marks, and the runtime driver above independently re-derives every one of those facts.

---

## 4. Pre-existing-reds hygiene

### 4a. `tests/test_project_history_api.py` — 26 reds, handoff claim VERIFIED

- **QUARANTINE.md**: `grep -n -i "test_project_history_api" .agents/tester/QUARANTINE.md`
  → **NO ROW**. The handoff correctly did NOT claim a quarantine entry.
- **Untouched by the range**: `git diff 1b70a58ca..449163f17 -- tests/test_project_history_api.py`
  → **EMPTY**; `git diff --name-only` over the range → not listed. Zero causal reach.
- **A/B-EXECUTED at base** (not merely byte-argued): `git archive 1b70a58ca | tar -x` into a
  mktemp dir; `daemon.__file__` asserted to resolve inside the extraction
  (`BASE-TREE-RESOLVED: OK`); run with the worktree `.venv`.

| Fence | Merged tree | Base extraction | Verdict |
|---|---|---|---|
| ambient auth ON | **26F** | **26F** — identical node set, md5 `5cbf9be875154533a77bd3027fd5289f` | PRE-EXISTING, proven by execution |
| `ENSEMBLE_AUTH_ENABLED=false` | **26P / 0F** | **26P / 0F** | GREEN in the standard (auth-off) environment on BOTH legs |

Failure signature characterized: `assert 302 == 200` with
`[AuthAudit] event=request_denied_302 path=/api/projects/<id>/history` — the ambient
`ENSEMBLE_AUTH_ENABLED=true` makes `AuthMiddleware` redirect. With auth off the file is
**fully green on both legs**. ⇒ The 26 reds have **zero** causal relationship to the
project-resolution change; they are an auth-subsystem environment artifact, present at
base, and they vanish entirely in the standard environment.

### 4b. `tests/test_api.py` — in-range touched (one version line) ⇒ also A/B-proven

The diff touches this file (`assert data["version"] == "0.31.37"` → `"0.31.38"`), so it is
inside the blast radius and could not be waved off.

| Fence | Merged tree | Base extraction | Failure-set md5 |
|---|---|---|---|
| ambient auth ON | 41F / 6P | 41F / 6P | `074fc195915f36911edca6a5dd2bb057` — **identical** |
| `ENSEMBLE_AUTH_ENABLED=false` | 9F / 38P | 9F / 38P | `0dbf11a1112525d813e2db2459053e26` — **identical** |

The 9 auth-off reds are the documented stale-expectation / plain-Mock-awaited family
(`test_list_instances_*_project_id_filter` ×4, `test_send_message_success`,
`test_global_exception_handler`, plus `test_get_instance_success`,
`test_get_instance_not_found`, `test_send_message_instance_not_found`).
**ZERO net-new reds** from the version line. `test_health_check` (the node carrying the
changed assertion) PASSES.

*Ledger drift observed (not a blocker):* the QUARANTINE.md row for `tests/test_api.py`
documents **6** nodes; the observed auth-off set is **9**. The 3 extra are the same
"plain-Mock awaited" family (`RuntimeError: Task …`) and are base-identical. The row's
rationale already names that family, so this is a census staleness, not a new red.

---

## 5. Other in-range seams

- **Version-bump seam** — `daemon/__init__.py` = `0.31.38`; `pyproject.toml` = `0.31.38`;
  `uv.lock` package `ensemble` = `0.31.38` (`source = { editable = "." }`). Consistent.
- `tests/unit/test_wedge_capture_wiring.py` — pins `release_version == daemon.__version__`
  (evaluates the real `daemon.api` namespace): **3P / 0F**.
- **CHANGELOG** — the diff adds a `## [v0.31.38] — 2026-11-23` section; the `[Unreleased]`
  section is **UNTOUCHED**, so the `tests/test_phase8_docs_gate.py` [Unreleased] assertion
  is unaffected.
- **Zero migrations** in the delta (confirmed: no `daemon/migrations/versions/` file in the range).

---

## 6. Mutation probe (ONE resolution wire, spot-probed)

**Target:** `daemon/tools/project_future.py` — the `name` branch of
`_resolve_project_reference`; the `store.resolve_project_reference(name, scope="name")`
call replaced with `{"found": False, "error_code": "NOT_FOUND"}` (i.e. "the resolver never
resolves on the name path").

**Result — the directional test went RED on exactly the name-path nodes:**

```
FAILED tests/unit/test_project_future_resolution.py::TestShortnameResolution::test_name_path_also_resolves_a_shortname
FAILED tests/unit/test_project_future_resolution.py::TestExactNameResolution::test_add_resolves_exact_name
FAILED tests/unit/test_project_future_resolution.py::TestExactNameResolution::test_list_resolves_exact_name
FAILED tests/unit/test_project_future_resolution.py::TestExactNameResolution::test_remove_resolves_exact_name
FAILED tests/unit/test_project_future_resolution.py::TestAmbiguousReference::test_add_refuses_ambiguous_name
FAILED tests/unit/test_project_future_resolution.py::TestAmbiguousReference::test_list_refuses_ambiguous_name
6 failed, 15 passed, 1 warning in 3.95s
```

Two things this single probe proves at once:

1. **The resolution wire is load-bearing** — the directional test DETECTS its removal
   (6 RED). The developer's "7/7 mutation probes RED" claim is consistent with this.
2. **The id path does NOT depend on the resolver** — the 15 that stayed GREEN include the
   whole `TestIdPathUnchanged` class (`test_id_path_add/list/remove`,
   `test_id_path_never_calls_the_resolver`) and the whole shortname-path class (the
   shortname wire was not the mutated one; `test_remove_refuses_ambiguous_shortname`
   correctly stayed green). The "id-path unchanged" claim is therefore structurally
   pinned, not merely asserted.

**Restore:** byte-copied back from a `/tmp` backup (NO git write of any kind —
`git checkout`/`stash` were not used).
`PRE-MUTATION sha256 = POST-RESTORE sha256 = 320b0eb493b808493728f0f63cf0b658d91bb0cb78406f250e42f065c0ea4d64`;
`git status --short` empty; `git diff --stat` empty; `git diff --quiet -- daemon/tools/project_future.py`
→ `RESTORE-VERIFIED: file byte-identical to HEAD`.

---

## 7. Tree hygiene after the run

- **No production code was modified.** `git diff --stat` in the integ worktree shows
  `.agents/tester/PACKS.md` only (2 lines: the refreshed row + the new pack row).
- New untracked tester artifacts: `test/packs/project_resolution_triad_runtime_proof.sh`,
  `.agents/tester/RESULTS/sonic-projres-future-history-20261123-results.md`.
- **The main checkout `/home/nea/ensemble-src` was NOT touched** — read-only inspection
  only. It remains detached at `bf4bfde6e` (BEHIND `origin/latest`), so it does not carry
  either pack row (`grep -c` = 0 for both) — consistent with the standing note that the
  canonical checkout lags and later `PACKS.md` rows are missing there.
- **The PACKS.md edit is UNCOMMITTED by design** — this lane's git use is read-only
  inspection; the branch tip `449163f17` is the STAGED v0.31.38 release tip recorded in
  the shared KV (`tip == origin/latest == v0.31.38^{}`) and must not be moved by the
  tester. **Leader sweep action: land the 2-line `.agents/tester/PACKS.md` update and the
  2 new tester artifacts.**

---

## 8. Residual gaps (NOT covered by this gate — carried forward honestly)

1. **No PostgreSQL-dialect run of the resolution ladder** — in-memory SQLite only. The
   ASCII-vs-Unicode case-folding divergence across engines is inherited pre-existing seam
   behaviour; the ladder file itself is not in this range.
2. **No live-daemon / HTTP / REST exercise of the 7 tools** — the tool layer is proven
   against a REAL repository, not over the wire.
3. **No LLM-facing render of the new tool JSON schemas** — schema required-sets were
   asserted programmatically by the developer; not re-rendered here.
4. **MCP `kb_server` resolver NOT unified** — explicitly out of scope, parked.
5. **No daemon boot smoke carrying the change; no promote** — staged-only lane; the
   promote ceremony remains the Emperor's.

---

## 9. Verdict

**TEST: PASS**

- Registered pack `project_future_history_resolution`: **218P / 0F, 0 skipped**, reproduced
  twice on the merged tree `449163f17`.
- New registered pack `project_resolution_triad_runtime_proof`: **35/35 checks PASS** —
  the Emperor's case proven at runtime on all 7 tools.
- Riders: **28P / 0F**. Version seam: **3P / 0F**.
- Zero net-new reds inside the declared blast radius; both red-carrying in-range files
  A/B-EXECUTED at base with byte-identical failure sets (md5) on two fences.
- Mutation probe RED on the name-path wire; restore byte-verified.
- **No failing node ids.**

**Gate-relevant and evidence-bearing ⇒ this RESULTS file is written.**
