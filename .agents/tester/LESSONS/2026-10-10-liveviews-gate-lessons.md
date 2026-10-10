# Live-Views Gate Lessons (2026-10-10)

Gate: `RESULTS/2026-10-10-live-views-url-and-rendering-gate.md` · worktree `/home/nea/ensemble-src-wt-liveviews-20261010` @ 8dbbcb6a1, base 0c040e5f8.

## 1. `.smoke/` harnesses derive the worktree from `__file__` — /tmp drivers must set `LIVE_VIEWS_SMOKE_WORKTREE`
`.smoke/smoke_live_views.py` resolves its served tree via `Path(__file__).resolve().parents[1]` with an env fallback (`LIVE_VIEWS_SMOKE_WORKTREE`). A driver script living in `/tmp` resolves parents[1] to `/` → every request 404s with `PathNotFoundError` (cost the smoke worker ~50 min to diagnose). **Rule: any out-of-tree driver for this harness MUST export `LIVE_VIEWS_SMOKE_WORKTREE=<worktree>`; prefer reading the harness source before designing scenarios.**

## 2. A/B base worktrees must live under `/home`, never `/tmp` — filesystem pin tests hard-red under temp dirs
Base re-runs at `0c040e5f8` in `/tmp/lvbase-…` produced 5 extra reds in `test_filesystem_walk_guardrails` (4) + `test_filesystem_workdir` (1): these derive repo-root from `__file__` and fail LOUD BY DESIGN when the checkout sits in a temp dir ("pin meaningless on this filesystem"). Triple-adjudicated: base@/tmp RED, base@/home GREEN (77/77), branch@/home GREEN. **Rule: build base disambiguation worktrees under `/home/…` (e.g. `/home/nea/lvbase-home-check`), or pre-exclude the filesystem-pin family from /tmp A/B comparisons.**

## 3. xdist bookkeeping can re-split a fixture-error family across files — adjudicate with standalone runs
Full-glob runs on branch vs base showed `builtin_mcp_servers` 19E/`context7_builtin` 2E vs 17E/4E — same `Mock 'service_tool'` root cause, same TOTAL (21E), different per-file split (xdist worker assignment of fixture teardown noise). Standalone single-file runs on BOTH sides gave the identical 17E/4E split. **Rule: when per-file error counts drift but totals conserve, run the affected files standalone on both sides before calling it a delta.**

## 4. The `sweep_top_hm` near-cap warning is resolved by the h-i/j-m split — keep it
2026-10-07 flagged `h-m` at 288s (near cap). Split `h-i` (185 tests, 13s) / `j-m` (2043, 95s) keeps every top-level slice far under cap. The h-m weight was almost entirely `test_l*`/`test_m*` files (j-m range). Estimates: letter-range packs were 2.5–40× off in BOTH directions (h-i 185 actual vs ~1100 est) — always size by collection count, not prior-letter heuristics.

## 5. Project-scoped live-views URLs require the `<shortname>/` prefix
`root type="project_scoped"` (e.g. `planning`) serves at `/views/planning/<shortname>/<path>` (`ens/` in the harness). Paths without the prefix 404. Test briefs and fixtures must use `ens/smoke/...`-style rel paths; `build_url("planning", "ens/smoke/sample.md")` is the canonical shape.

## 6. DOMPurify strips (not escapes) payloads on the success path — escaped form lives only in the fail-closed `<pre>`
The wrapper's server-side `<pre>` fallback carries the raw markdown HTML-escaped; after the designed success path (marked.parse → DOMPurify.sanitize → replace), `<script>` payloads are REMOVED entirely (no node, no text). Assertions should check: (a) escaped payload present in SERVED SOURCE `<pre>`, (b) zero content-derived script nodes in the RENDERED DOM — not "payload visible as escaped text after render".
