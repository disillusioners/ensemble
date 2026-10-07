# Worktree import-check CWD-shadowing ≠ editable-install trap (plugin slices, 2026-10-06)

**Context**: Slice-① independent test pass, worktree `/home/nea/ensemble-src-wt-plugin-subsystem-01`.

**Symptom**: `python -c "import daemon; print(daemon.__file__)"` appears to resolve `daemon` to the MAIN checkout (`/home/nea/ensemble-src/daemon/...`) — reads exactly like the known editable-install trap (`.pth` pinned to original checkout).

**Root cause**: NOT the trap. The worktree `.venv`'s `_editable_impl_ensemble.pth` correctly targets the worktree. The false reading happens when the check runs from a CWD that itself contains a `daemon/` package dir (e.g. main repo root): `sys.path[0] = ''` (CWD) shadows the venv's editable path.

**Rule for all future slice verification passes**:
1. ALWAYS `cd <worktree>` before the import check — the path printed must start with the worktree prefix.
2. Only if it STILL resolves elsewhere after `cd`: run fresh `uv sync` in the worktree (fixes genuine `.pth` mis-pins).
3. Do not "fix" a CWD-shadowing artifact with `uv sync` — it wastes a sync and mis-attributes the cause.

**Companion note — shared live worktrees during the overnight build**: multiple lanes (build/review/test) hit the same worktree concurrently. For tester verification runs use `-p no:cacheprovider` so your runs neither write nor clobber `.pytest_cache` evidence, and so cache-write timestamps from OTHER agents aren't mis-attributed to you (nodeids refresh @ 18:07:35Z during slice-① verification was another lane's plain pytest run; lastfailed @ 17:40Z was mid-development state pre-green).
