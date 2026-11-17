# Group-chat v0.31.0 — FINAL RELEASE LEGS: S1d (at tag) + S4 (re-measure) + S5 (scratch boot smoke)

**Date**: 2026-11-17 · **Lane**: release verification (tester), commit-window fold · **Worktree**: /home/nea/ensemble-src-wt-group-chat-release @ `0181dadbf` (== annotated tag `v0.31.0` target, tag object `c47d3858f`, local-only)
**Staged payload**: /home/nea/agents-ensemble/releases/v0.31.0 — binary sha256 `5473a25f759ec6ef21fd85ebfb76939658ff4fa4fe9b73e07870ce17d9514743` (4-way verified), staged_at 2026-11-17T14:09:19Z, rollback_safe=true (explicit)
**Leg scorecard**: S1d ✅ PASS 19/19 · S4 ✅ PASS (after load-artifact adjudication) · S5 ✅ PASS-with-disclosures (5/5 criteria) — verification axis CLOSED.

---

## 1. S1d — prompt-artifact seal re-run AT the v0.31.0 tag (plan §4.3.2/§4.3.3)

**Verdict: PASS 19/19. NO prompt-artifact drift rode the release. No baseline bump. No seal re-pin.**

- Tag/tree facts: worktree HEAD == `v0.31.0^{commit}` == `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (== origin/latest tip); tag object `c47d3858f934be92a981b7668a98fa2a0efcf150` (annotated, `cat-file -t` = tag); `v0.30.12^{commit}` = `cf43ac00d0dfcaca2bd48d2025b95a6301c35b78` is an ancestor; `ls-remote origin refs/tags/v0.31.0` empty (local-only, unpushed); worktree porcelain-clean at start AND end; no commits/pushes/fetch/tags/checkout; `--all` never used.
- The three plan checks (v0.30.12 → v0.31.0): (1) `git diff --stat -- agents/.prompt-artifacts.sha256` EMPTY, `--exit-code` rc=0; (2) `git diff --stat -- 'agents/*.md'` EMPTY, `--exit-code` rc=0 (quoted-pathspec re-run, same); (3) seal content sha256 EQUAL three ways (v0.30.12 blob, v0.31.0 blob, worktree file) == `50561606a464df3d9484b93f78b1eb6880cc594badf27d69502d8d1f74c54979`; worktree-vs-tag `diff --exit-code v0.31.0` rc=0.
- **Seal blob id at v0.31.0: `cb51a277bea6334100f7f6139adfe6bef8512f7a`** — identical at `v0.30.12:`, `v0.31.0:`, `HEAD:` (blob-identical, stronger than content-diff).
- No re-pin in range (plain log, no `--all`): empty for the seal file AND for `agents/*.md`. Pathspec non-vacuity: `f0fa135e7^..f0fa135e7 -- 'agents/*.md'` lists `agents/ari/rule.md` + `agents/ari/tools_note.md`.
- Census: `agents/` delta in range = exactly 8 rows, all `M`, all `meta.json` (ari, leader, sonic-leader, video-capturer, video-editor, video-producer, video-verifier, worker — the `room` grant), 0 `.md`. `meta.json` is provably outside the seal (`.md`-only VERSIONED_PROMPT_ARTIFACTS).
- Repo checker: `generate_prompt_artifact_baseline.py --check --tree <WT>` rc=0, `PROMPT-BASELINE: OK — 217 versioned artifact(s) match` (note: this checker build emits no parenthetical counters — cosmetic difference from the 92fcf26af wording). Seal file: 217 lines, 0 malformed rows.
- WATCH-1: parked `feature/video-mock-prompts` commit `e559b4b80` NOT an ancestor of v0.31.0 (rc=1) — the future video-team re-pin did not ride.
- Worker: 6f828403 (skill-less, read-only). Evidence: assets/s1d/ (transcript 122 lines + checkG.out + start/end status).

## 2. S4 — gc_p6_room_family re-measure at the release tip

**Verdict: PASS — registered shape reproduced: 24 files / 421 collected / 421 passed / 0 failed / 0 errors / 0 skipped / exit 0 / wall 86 s / junit 421-0-0-0, quiet host.**

- First same-day measurement (registered `-n 8` shape, verbatim): **420/1** — single node `tests/unit/tools/test_spawn_room_join.py::TestJoinHappyPath::test_tier3_agent_fallback_in_result_and_instant`, ONLY its `elapsed < 2.0` wall-clock guard tripping at **2.0075 s** (+0.37%); every functional assertion passed. Exit 123 = xargs-masked pytest 1. Wall 88 s (+24% vs the registered 70.93 s — contention signal).
- **Classification — load-induced timing flake, 4 ways**: (1) node 3/3 PASS serially (14.9/11.1/10.2 s) + red REPRODUCED under loaded `-n 8` (2.0170 s); (2) `git diff f5af8739b..0181dadbf`: only daemon/ change = version bump `0.30.13→0.31.0`, room-family corpus byte-identical — no code regression possible; (3) host loadavg 10.20→13.73 (16 cores) under concurrent foreign lane `wt-prompt-reload-compact` (~12 workers, 75-92% CPU); (4) serial registered shape at the SAME tip: 421/0/0/0, exit 0, 372 s.
- **Confirmatory re-measure (load-gated)**: gate passed (1-min loadavg 6.52→5.39 ≤ 8; sustained-CPU window from /proc/<pid>/stat showed the foreign lane at 0.2% of one core — `ps %CPU` was lifetime averages; loadavg inflation was D-state IO, not CPU). Registered invocation verbatim → **24 files / 421 passed / 0 red / exit 0 / junit 421-0-0-0 / wall 86 s**; loadavg after 12.20 = the run's own xdist footprint (attributed). Previously-red node GREEN (junit case 2.822 vs 2.954 during the red).
- Fence: effective `POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_USER=ensemble_dev_app ENSEMBLE_SELF_ENV=dev` — never ensemble_prod; per-variable scrub (never `env -u POSTGRES_*`); password extracted non-sourcing, never printed.
- Anomaly `621fb76f` RESOLVED by the confirmatory leg. Residual test-hygiene item: the 2.0 s guard at `test_spawn_room_join.py:285` is load-fragile under parallel contention — owning lane's follow-up. NO QUARANTINE row (judgment): one-off environmental flip, green under the registered shape; revisit if it recurs.
- Workers: d0a0070e (`test-pack-execution` ×2). Evidence: assets/s4/ (first leg run/junit + serial probe + 3× serial green + parallel-red repro; confirmatory remeasure-* set incl. loadavg captures + fence line).

## 3. S5 — scratch-sandbox boot smoke of the staged payload (plan §5.4.0-5.4.4)

**Verdict: PASS-with-disclosures — all 5 plan criteria GREEN; the plan's §5.4.3 text required 4 disclosed corrections (PD-1..PD-4) to be executable at all.**

| Criterion | Verdict | Evidence |
|---|---|---|
| 1 boots in scratch sandbox | PASS | `Starting Ensemble v0.31.0`; `Bound 127.0.0.1:19101 on attempt 1`; PG `127.0.0.1:5432/ensemble_dev_scratch_s5`; graceful shutdown |
| 2 livez version 0.31.0 | PASS | `{"status":"alive","uptime_seconds":6.03,"version":"0.31.0"}` — first 200 at t=22 s |
| 3 readyz green | PASS | database/queue_freshness/services/checkpoint_saver all true |
| 4 room tables in scratch DB ONLY | PASS | post-boot chatrooms=10, chatroom_members=9, room_messages=14 cols (+member/resolution_warnings/source_message_id/trigger) + `uq_room_messages_proposal_dedup`; rows 0/0/0; 58→109 tables; shared `ensemble_dev_scratch` byte-for-byte UNCHANGED (58 tables/9-10-10/0 rows/no dedup index) |
| 5 ZERO sweep/reaper kills | PASS | SIGTERM=0, SIGKILL=0, killed=0, reaped=0; reaper `master switch OFF`; `[RamGuard] OFF`; all three terminators explicitly =0 |

- Cgroup isolation PROVEN: `systemd-run --user --scope` (with XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS set) → `/proc/917521/cgroup = 0::/user.slice/…/gc-s5-smoke.scope`; ensemble-main.service absent.
- Tool vocabulary: `room` category at `daemon/tools/room.py:129` (`ROOM_CATEGORY_KEY`), 8 agents grant it in the staged payload. NOTE: `.agents/shared/metrics/tool-categories.json` does NOT exist in the payload — checked against the real registry instead.
- Teardown/zero-residue: no listener on 19101, no scratch processes, clone DB dropped, port-claims emptied, `/tmp/gc_v0310_smoke.*` roots = 0.
- Live-untouched (all four): MainPID=700595 / NRestarts=0 / ActiveEnterTimestamp=2026-11-15 06:12:13 UTC identical; state.json current=v0.30.12, previous=v0.30.9, in_flight/pending_*=None; staged ensemble-prod sha + manifest.json byte-identical to pre-run; current → releases/v0.30.12. Unit lives in the SYSTEM manager (`--user` shows MainPID=0 — both forms recorded).
- **PLAN DEFECTS PD-1..PD-4 (§5.4.3 as written is NOT executable — amend before the ceremony):** PD-1 payload tier-2 DB guard refuses ambient DSN → exit 78 pre-TCP without `ENSEMBLE_DB_AMBIENT_ALLOW=1` (house harness template already sets it). PD-2 scrub omits the ambient `ENSEMBLE_AUTH_*` family (7 vars) → auth-preflight exit 78 (payload config ships auth.enabled:false). PD-3 `timeout -k 5 30` cannot reach livez (~29 s lifespan startup, port bound-not-listening) — needs ≥120 s. PD-4 the named scratch-DB role cannot create_all in place (55/58 objects superuser-owned) → boot against a re-owned TEMPLATE CLONE (`ensemble_dev_scratch_s5`; same pre-P3/P5 room shape so the 10→14 upgrade path was still exercised).
- Applied deviations D1–D9 fully ledgered in assets/s5/DEVIATIONS.md (never silently patched). Security note: an ambient ENSEMBLE_AUTH_CLIENT_SECRET rode into the smoke child — verified 0 occurrences in boot logs, never printed. Abort-path item 5 (room tables absent from prod) satisfied BY CONSTRUCTION (no prod DSN ever present; fence armed) — prod connection forbidden by the commissioning fence.
- Worker: df1b1e98 (skill-less). Evidence: assets/s5/ (28 files: 2195-line transcript, DEVIATIONS.md, livez/readyz bodies, cgroup proof, clone proofs, termination census, live-untouched, final sweep, SQL + stage scripts).

---

**Standing disclosures for the ceremony brief**: PD-1..PD-4 plan amendment (plan owner); test-hygiene 2.0 s guard (owning lane); host parallel-gate load sensitivity + disk 95% (14 G free); S1d does not need to re-run unless a leg forces a re-cut.
**Commit-window fold**: this file + PACKS.md final block + assets/ = the durable record; /tmp originals remain until swept.
