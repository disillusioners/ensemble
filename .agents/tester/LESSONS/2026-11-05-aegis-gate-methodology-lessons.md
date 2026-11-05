# Gate methodology lessons — aegis-security-family Phase-5 functional gate (2026-11-05)

Reusable findings from the `aegis-security-family` Phase-5 gate (plan-locked Gates A–H). Evidence:
`.agents/tester/RESULTS/2026-11-05-aegis-security-family-functional-gate.md`.

## L1 — pytest param ids over an `rglob` are NOT stable across agent additions → compare at PATH level

`tests/unit/tools/test_prompt_section_reference_integrity.py` parametrizes with
`ids=lambda p: p.name` over an `rglob` of `agents/`. Pytest dedups duplicate names with a
numeric suffix (`workflow.md4`, `workflow.md12`, …), and the **index shifts when files are added**.
Adding four new agents therefore moved the same four pre-existing failing files from
`workflow.md11/17` + `rule.md3` + `workflow.md3` (base) to `workflow.md12/19` + `rule.md4` +
`workflow.md4` (HEAD) — a *pure id shift* with byte-identical file content.

**Rule:** for any whole-tree FAILED-list diff, resolve param ids back to **file paths** before
classifying. A literal-id diff will report a false net-new on every agent addition. Fix upstream:
parametrize on the repo-relative path so ids are path-stable.

## L2 — a "Green" acceptance criterion written against an assumed-clean base can red on debt you do not own

The plan's Gates B and D-2 expected "Green", but the **true base** already carried 4 stale
`SEALED_SHA_BASELINES` entries and 5 charter/developer prompt-debt ids. Both reds were proven
base-red by blob comparison (BASE == HEAD) and by the dual-side digest diff (red on both legs).

**Rule:** before accepting any "expect Green" leg, establish the base state first. A red that is
provably red at base is **disclosed debt**, not a lane blocker — same doctrine as Gate A. Adjudicate
with: (i) blob-identity BASE↔HEAD for the implicated files, (ii) presence/absence of the id in BOTH
digests, (iii) path-level resolution for parametrized ids (L1).

## L3 — a dual-side FAILED-list diff needs the ID form normalized, and the two forms must agree

The plan's literal `grep -E "^(FAILED|ERROR)" | sort` form also captures **daemon log lines**
that begin with `ERROR`/`FAILED` (53 such lines in this run). The canonical comparison input is the
**bare-id** form (prefix and ` - msg` suffix stripped, `LC_ALL=C sort -u`); the house comparator
`sweep_digest_verdict.sh` runs BOTH forms and asserts they agree (FAIL-LOUD, non-vacuous).
Always record the digest sha256 so the next lane compares like-with-like.

## L4 — `git diff <BASE>` (working-tree-inclusive) is the wrong scope form for a lane-own scope gate

A lane-own scope gate must diff the **committed** range (`git diff <BASE>..<HEAD>`). The
working-tree form folds any foreign unstaged WIP (here another lane's
`install-audit.jsonl`) into the complement and false-reds it as creep.

**Rule:** scope gates diff the committed range; foreign-WIP hygiene is a separate leg (prove the
path is absent from the committed diff and that its bytes/status are unchanged pre/post).

## L5 — a plan predicate can go stale relative to the tree it governs (tolerances are dated artifacts)

The Gate H tolerance list was frozen ~5 h before the tidier committed `.agents/tidier/notes.md`
(lane-review artifact; tidier's declared write scope is `.agents/tidier/` only; identical shape in
the preceding lane). The gate then "failed" on a house-conventional artifact.

**Rule:** when a scope gate reds on exactly one path that a *later* commit introduced, check for a
named tolerance in the plan texts **and** the commissioning dispatch before calling it creep; if
the artifact is canonical-by-house-rule, disclose it as a stale-predicate tolerance and surface the
predicate-staleness upstream — never silently amend a locked plan, and never "fix" it by rewriting
history of a gate-anchored commit.

## L6 — base-leg provisioning is a wedge risk; time-box it and keep a fallback interpreter

The first base-leg worker wedged 1 h 48 min inside venv provisioning (host-level wedge class:
several lane instances wedged this period). The replacement completed by **reusing** the already
provisioned worktree-local venv, and — when a venv was unnecessary — by running the **lane venv's
interpreter by absolute path with cwd = the other worktree** (deps are identical when the lane diff
touches no `pyproject.toml`; the repo code resolves from cwd).

**Rule:** every package-manager call gets `timeout 600` and an explicit fallback; a base-leg worker
must never be the only path to the digest.

## L7 — a source-mode boot-scan needs the sweep pin proven from the DAEMON's environ, not the shell

`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` must be verified in `/proc/<pid>/environ` of the booted
daemon — the launching shell's env is not proof (the pin can be lost to a wrapper). Corroborating
proof: the sibling lane's live pytest workers all survived the boot. Also assert the resolved
`POSTGRES_DB != ensemble_prod` **before** the boot and cross-check with `pg_stat_activity` —
`daemon/persistence.py:326` logs **file-config** DSN values while the effective DSN is env-first,
so that log line is a false prod-contact signal.

## L8 — the uniform-deletion mutation is caught by TWO layers, not one

The T30 byte-identity pin (`md_fences_bullet_lists_byte_identical`) is a **two-clause** check:
cross-body identity (survives a uniform deletion) **plus** an A-7 bullet-count clause (fires on
it). A plan expecting "byte-identity survives → only the per-bullet pin reds" under-counted the
expected reds (3 vs 4). Read the failing **line number** in the assertion to decompose clauses
instead of assuming — and report the stronger property rather than the plan's expectation.

## L9 — mutation legs must live in a scratch worktree

Planted-defect legs mutate tracked files. Doing that in the lane worktree risks colliding with a
concurrent whole-suite leg and contaminating both. Use `git worktree add --detach /tmp/<x> <sha>`
+ the lane venv interpreter by absolute path with cwd = scratch; restore with
`git -C <scratch> checkout -- <paths>`; assert `porcelain` empty before and after every leg;
remove the worktree at the end (note: `git worktree remove` returns rc=128 when the tree holds
untracked artefacts such as a `.venv` — either clean it or retain it deliberately as the replay
fixture).
