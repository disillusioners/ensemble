# Rehearsal (c) — staged-release flip drill on the demo env

**Task:** wi-52666630 · commission `macos-deployment-topology` · branch `feature/macos-deployment-topology`
**Date:** 2026-11-09, 19:10–19:12 UTC (02:10–02:12 local +07)
**Result: ✅ PASS** — atomic flip to `v0.10.7-p2.3-b65`, healthy boot on it, rollback-by-flip, all end-state asserts green, `state.json` byte-untouched.
**One mission-text defect found and proven (F1: `mv -f` is wrong; `mv -hf` required). One era finding (F2: version-string collision).**

---

## 1. Baseline — 19:10:21Z

```
demo NOT LOADED · :7979 FREE · PROD pid=51569 · :9797/livez 200

current -> releases/v0.10.8-p2.3-b7cyc1
releases/state.json baseline: sha e70c5a8a781b0ddcd568b0cc48eae884f2dcea32cc19f69eeaebba15b6164ed6
                              in_flight: None · history entries: 34
manifests present: v0.10.8-p2.3-b7cyc1/manifest.json ✓ · v0.10.7-p2.3-b65/manifest.json ✓

Binary identity map (sha256, release-ID proof independent of any version string):
  current/ensemble-prod                  bd740467e7d312a28cd4cb1c41d92254207236a4c14a9611aea94e2cf8823838
  releases/v0.10.8-p2.3-b7cyc1/…         bd740467…  (== current)
  releases/v0.10.7-p2.3-b65/…            c07e2598cf9b33b30076f0de041eff20d9b012905a584410281bf3924fc5727c
  releases/v0.10.6-p2.3-dr4reset/…       fd7c1ac0efd4dbfeaf7a11f0cf375efdce5c79051e0a0be5f4032d18879b9f0c
```

## 2. 🔴 FINDING F1 — the flip MUST be `mv -hf`, not `mv -f` (proven in /tmp)

When `current` is a **symlink-to-directory**, BSD `mv -f temp current` follows the destination symlink and moves the temp link **INSIDE** the old release — a **silent no-op flip**. Proof (`/tmp/reh-c/fliptrap`):

```
ln -s relA current; ln -sfn relB current.new.123
mv -f current.new.123 current   → exit 0
current is now: relA            ← UNCHANGED!
relA/ contents: bin current.new.123   ← temp link nested INSIDE relA

redo with mv -hf:
mv -hf current.new.123 current  → exit 0
current is now: relB            ← true atomic replace ✓
```

The repo's own launcher twin (`launcher.sh` `_js_flip_current`) already uses `mv -hf` with exactly this comment — **the mission text's `mv -f` shape is wrong; the launcher twin is authoritative.** G5/stage2 docs must carry the `-hf` form. (`-T` is GNU-only; on GNU/Linux `mv -T` is the equivalent.)

## 3. Atomic flip → v0.10.7-p2.3-b65 — 19:10:37Z

```
cd ~/agents-ensemble-demo
ln -sfn releases/v0.10.7-p2.3-b65 current.new.$$ && mv -hf current.new.$$ current
before: current -> releases/v0.10.8-p2.3-b7cyc1
after:  current -> releases/v0.10.7-p2.3-b65      ✓
no current.new.* residue ✓
current/ensemble-prod sha == c07e2598… == v0.10.7's binary ✓   (identity: the flip really serves b65)
```

## 4. Boot on the flipped release — 19:10:37Z → 19:10:52Z

```
bootstrap (ritual asserts PASS) → exit 0
/livez → 200 after 15 s: {"status":"alive","uptime_seconds":1.22,"version":"0.10.5"}   ← see F2
/readyz → 200 all-green (database/queue_freshness/services)
money-line: 02:10:37 launcher[47748]: ensemble launcher starting (INSTALL_DIR=/Users/nguyenminhkha/agents-ensemble-demo)
            02:10:48 daemon.api: Starting Ensemble v0.10.5
tree: 47748 (ppid=1) launcher → 47776 …/current/ensemble-prod   (resolves through current → v0.10.7)
PROD after phase: pid=51569 · :9797/livez 200 ✓
```

The v0.10.7-era binary booted **clean** against the v0.10.8-era database (this release pair has no schema refusal in this era's `_ensure_postgres_columns` additive model) — no crash-loop, first boot green.

**🟡 FINDING F2 — version-string collision:** the v0.10.7-p2.3-b65 binary self-reports the **same** `"version":"0.10.5"` as v0.10.8-p2.3-b7cyc1. The mission's "verify version string CHANGED" is **impossible** in this era: the embedded string is identical across the p2.3 builds and lags the release tag (extends rehearsal-a S1). **Release ID must come from `readlink current` + binary sha256** — both captured above and in §5.

## 5. Rollback-by-flip + end state — 19:11:11Z → 19:11:46Z

```
19:11:11Z bootout (ritual) → exit 0 · NOT LOADED · :7979 FREE
          D8: 02:11:11 received SIGTERM — forwarding to child (pid 47776)
              02:11:11 Graceful shutdown complete · 02:11:12 launcher exiting 143     [window ≈ 1 s]
19:11:1xZ ln -sfn releases/v0.10.8-p2.3-b7cyc1 current.new.$$ && mv -hf current.new.$$ current
          current -> releases/v0.10.8-p2.3-b7cyc1 ✓ · no temp residue ✓
          current/ensemble-prod sha == bd740467… (v0.10.8 baseline) ✓
19:11:14Z bootstrap → /livez 200 after 12 s (v0.10.5) · launcher start line at original path ✓
19:11:27Z final bootout → exit 0
END STATE: current -> releases/v0.10.8-p2.3-b7cyc1 ✓ · demo NOT LOADED ✓ · :7979 FREE ✓ ·
           no demo processes ✓ · PROD pid=51569 · :9797/livez 200 ✓
```

**Journal discipline (explicit):** this drill wrote **nothing** to `releases/state.json` — no promote, no commit, no in_flight. Proof: `cmp` live vs baseline → **BYTE-IDENTICAL** (`in_flight: None`, 34 history entries, unchanged). A manual flip is invisible to the journal by construction; the journal only moves via the promote/rollback tooling. (Corollary observed across rehearsals (a)+(b)+(c): daemon *boots* did not append journal events either — the 2026-11-09T16:45Z `mcp_server_state` history in the baseline predates these rehearsals.)

## 6. Findings for the doc

| # | Finding | Action |
|---|---|---|
| **F1** | `mv -f` on a symlink-to-dir destination = **silent no-op flip** (temp link nested inside old release); `mv -hf` = atomic replace. Proven in /tmp; launcher twin already correct. | Canonical doc + any G5/stage2 mention must use `ln -sfn … current.new.$$ && mv -hf current.new.$$ current`. Flag `mv -T` for GNU parity. |
| **F2** | `/livez` `version` string is identical ("0.10.5") across v0.10.7-p2.3-b65 and v0.10.8-p2.3-b7cyc1 and lags release tags — NOT a release discriminator in this era. | Release ID = `readlink current` + binary sha256 (captured in §1). Runbooks must not gate flips on the version string for this generation. |
| **F3** | Flip both directions is instant, leaves no `current.new.*` residue; boots green both directions (~12–15 s to livez). Downgrade boot v0.10.8→v0.10.7 clean (no schema refusal for this pair). | Supports the staged-release model; downgrade-safety is release-pair-specific and must NOT be assumed in the doc. |
| **F4** | Drill journal-untouched (byte-identical state.json); manual flips are invisible to the journal — reconcile/audit tooling keys off journal events, so a manual flip leaves `state.json.current` stale (journal says v0.10.8-p2.3-b7cyc1 while we ran b65 — reverted, so consistent again). | Doc note: manual flips (drills, emergency) should be followed by a journal `current` field reconciliation or explicitly forbidden outside drills. |

## 7. D7 / D8 opportunistic captures

- **D7 (Notification-Center under launchd-job context):** `osascript -e 'display notification … with title …'` from THIS daemon job context (gui-domain LaunchAgent, the environment rehearsals run in) → **exit 0**, notification delivered. Caveat: this proves the *gui-domain LaunchAgent* case; the architect's `launchctl asuser` variant (root LaunchDaemon → asuser hop) was NOT exercised — that arm remains open for the operator to probe.
- **D8 (TERM→KILL window):** 4 bootouts this session, every window **1–2 s** (`received SIGTERM` → `Graceful shutdown complete` → `launcher exiting 143`); `CHILD_STOP_WAIT_S=70` never engaged; no SIGKILL fallback ever needed.
