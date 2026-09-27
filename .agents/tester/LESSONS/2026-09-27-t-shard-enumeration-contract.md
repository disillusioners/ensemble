# T-shard enumeration contract: enumerate `test_*.py`, never bare `*.py` (2026-09-27)

**Context:** final maintenance-console whole-tree sweep. The T-shards (tests top-level) were dispatched with `ls tests/*.py | sort` enumeration (155 files). Phase-1 had used `test_*.py` enumeration (142 files). The delta: 13 non-test files (`mock_*` ×7, `manual_*` ×3, `conftest.py`, `resume_mock_test.py`, `__init__.py`) entered the shard as EXPLICIT pytest file args — and explicit file args BYPASS pytest's `python_files = test_*.py` filename filter, so pytest force-collected them → 56 ERROR nodes (3 classes: fixture `name` not found ×2; fixture `session` not found ×6; `JobLockManager.__init__() missing 'lock_repo'` ×48) that looked like 56 brand-new failures vs the 204-node phase-1 ledger.

**Disposition cost:** a read-only git-forensics leg (provenance: files tracked, md5-identical at base `666c089d` and HEAD `c939aaa0`, added by pre-base commits `1e7e9223`/`acc1ff4e`/`4ed0d49e`) + an empirical base-parity leg (scope-matched 4-file runs at BOTH commits in scratch worktrees → 56 ≡ 56, node-set md5-identical) were needed to classify the 56 as pre-existing harness dust. Correct, but avoidable.

**Rules going forward:**
1. Whole-tree shard enumeration MUST respect the pytest `python_files` contract: enumerate `test_*.py` (and known test dirs), never bare `*.py`. Explicit file args override collection filters — that is pytest design, not a bug.
2. "File-count drift" between sweeps is an enumeration-definition smell FIRST, tree-change second. Compare `ls tests/*.py | wc -l` vs `ls tests/test_*.py | wc -l` vs `git ls-files` before concluding the tree grew.
3. `mock_*` / `manual_*` harness scripts are runner-driven (their fixtures live in shard-runner harnesses, not conftest); plain pytest file-args error on them from inception. If a sweep must touch them, expect the 3-class error signature and pre-classify as artifact.
4. `git ls-files 'tests/*.py' | grep -v '/'` is a command-construction bug (every git path contains `/`); top-level filter is `awk -F/ 'NF==2'`.
5. Boundary overlap is benign, boundary GAP is not: verify first+last file of every chunk and their union coverage (this sweep had a cosmetic 1-file overlap at `test_main_entry.py`, green both sides).
