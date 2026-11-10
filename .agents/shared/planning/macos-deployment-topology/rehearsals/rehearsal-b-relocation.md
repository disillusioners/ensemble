# Rehearsal (b) — G5 Stage-1 relocation drill on the demo env

**Task:** wi-52666630 · commission `macos-deployment-topology` · branch `feature/macos-deployment-topology`
**Date:** 2026-11-09, 19:06–19:10 UTC (02:06–02:10 local +07)
**Source procedure:** `g5-stage1-relocation.md` (architect) — executed against the DEMO install with path adaptations.
**Result: ✅ PASS** — move lossless (marker A == B, 1415 files), plist round-trip byte-identical, PATH block byte-identical, healthy boot at the drill path AND back at the original path, prod untouched throughout.
**One G5 defect found and proven (F1 below); one mission-text defect found and proven (mv flag, see rehearsal-c).**

Paths: demo `~/agents-ensemble-demo` ⇄ drill target `~/ensemble-demo-drill-tmp` (same volume, home sibling — NOT under `~/agents-ensemble*`, NOT /tmp).

---

## 0. Safety framing

Identical to rehearsal (a): every `launchctl` call echoed first, asserted to contain the demo target string, asserted free of `prod|watchdog` substrings; no glob sed — every `sed` names the demo plist file explicitly; live install read-only; prod asserted (pid + `:9797/livez` 200) after **every** mutation phase. The anchored-sed hazard is inverted vs G5 here: the LIVE path (`/Users/nguyenminhkha/agents-ensemble`) is a *prefix* of the demo anchor — protection is that no live file is ever named in a write command, plus the pre-edit assertion `grep -c` live-path in demo plist = **0** (passed).

---

## 1. Baseline (G5 Phase-1 analogs) — 19:06:46Z

```
launchctl: 51569 0 com.ensemble.prod · - 0 com.ensemble.watchdog · - 0 com.apple.ensemble
           com.ensemble.demo: NOT LOADED (rehearsal-a end state) · :7979 FREE · no demo processes
PROD: pid=51569 status=0 · :9797/livez HTTP 200

Same-volume proof (G5 1.3): stat -f %d → 16777232 == 16777232 == 16777232  ✓ rename(2) holds
Drill target: ABSENT (fresh, G5 2.1 FRESH-check analog) ✓

Plist byte-baseline (G5 1.6): installed == /tmp copy == later staged copy
  5e2bf45ca4a47eeedc3dadb9a8b2ebcadf685d989b723e4625401117af216d2  (all three)

Pre-edit assertions (G5 1.5 analog):
  live-path refs in demo plist: 0 ✓   demo-anchor count: 4 ✓   PATH∩demo-path: 0 ✓

EnvironmentVariables.PATH baseline (PlistBuddy → shasum):
  7d4f6a9b72b78fc9b9b68d5b800a7fa6ee8a14f9a9dfb58157f8689efec6e757

.env census (G5 1.7 analog): grep -c 'agents-ensemble-demo' .env → 0 (no path-valued keys) ✓
Staged copy pair: cp -p installed → data/ensemble-demo.plist (sha 5e2bf45c…, identical)
```

Marker A (tree identity manifest, daemon stopped): **1415 files**, `du -sk` **226948**, manifest total-sha `ba96f3ac…`.

---

## 2. 🔴 FINDING F1 — G5 2.1+2.2 as written NEST the tree (proven in /tmp, then avoided)

G5 2.1 creates the (empty) target `/opt/ensemble`; G5 2.2 then runs `mv ~/agents-ensemble /opt/ensemble`. BSD `mv dir existing-dir` moves the tree **INSIDE** the target. Proof (in `/tmp/reh-b/nest-demo`, no real target touched):

```
mkdir target tree; echo x > tree/launcher.sh; mv tree target
→ target/tree/launcher.sh EXISTS · target/launcher.sh MISSING
G5-2.3-style check [ -e target/launcher.sh ] → MISSING  ✗
```

**Correct shape (used for both move directions of this drill):** do NOT pre-create the leaf — `mv <tree> <nonexistent-target>` is a true atomic rename. G5 2.1 should be a *freshness test only* (`[ ! -e /opt/ensemble ]`), never a mkdir feeding 2.2; or 2.2 becomes `mv ~/agents-ensemble /opt/` with `mkdir /opt/ensemble` — but NOT both as written. **G5 must be corrected before live execution.**

(The mission's "mkdir the drill target" instruction has the same trap — resolved here by create-then-`rmdir` as a freshness proof, then renaming onto the absent path.)

---

## 3. The move + lossless proof — 19:07:24Z

```
mv /Users/nguyenminhkha/agents-ensemble-demo /Users/nguyenminhkha/ensemble-demo-drill-tmp   (exit 0, NO sudo)
ls DRILL/launcher.sh ✓ · DRILL/current ✓ · ls ~/agents-ensemble-demo → No such file or directory ✓
```

Marker B at the drill path: **1415 files, du 226948** — `diff marker-A marker-B` → **identical**.
**LOSSLESS MOVE PROOF: every file byte-identical, count and order identical, du identical.**

Sudo note (mission-required statement): G5 Phase 2 needs `sudo` for `/opt` (root-owned parent); the demo drill needed **no sudo anywhere** — both paths are inside the operator's own home. This is the expected live-vs-drill difference; nothing else about the move differs.

**Hazard-(b) window documented:** immediately post-move, the installed plist still pointed at the (now missing) demo path with the label unloaded — exactly G5's "tree moved, plists unedited" 🔴 state. Nothing could auto-start it (label not loaded), and the window was closed by §4 before any load.

---

## 4. Anchored-sed re-points + PATH preservation (G5 2.4–2.6 analogs)

```
sed -i '' 's|/Users/nguyenminhkha/agents-ensemble-demo|/Users/nguyenminhkha/ensemble-demo-drill-tmp|g' \
    ~/Library/LaunchAgents/ensemble-demo.plist                                   # installed (explicit file)
sed -i '' 's|…demo|…drill-tmp|g'  …/ensemble-demo-drill-tmp/data/ensemble-demo.plist   # staged copy in moved tree
```

Verification:

```
plutil -lint both → OK
ProgramArguments = { /bin/bash, DRILL/launcher.sh } · WorkingDirectory = DRILL
StandardOutPath/StandardErrorPath = DRILL/data/launcher{,.err}.log
old-path residue: installed 0 · staged 0      new-path count: installed 4 · staged 4

PATH-PRESERVATION PROOF (the G5 2.4 trap):
  baseline shasum 7d4f6a9b72b78fc9b9b68d5b800a7fa6ee8a14f9a9dfb58157f8689efec6e757
  after    shasum 7d4f6a9b72b78fc9b9b68d5b800a7fa6ee8a14f9a9dfb58157f8689efec6e757  → BYTE-IDENTICAL ✓
  nvm node bin (~/.nvm/versions/node/v22.21.1/bin) present in the value ✓
```

**Hazard-(b) lint-invisibility simulation** (safe, /tmp copy of the stale-path baseline plist): `plutil -lint` → **OK** — a stale-path plist is *syntactically perfect*; lint cannot catch hazard (b). Recommendation for G5: insert an explicit `[ -e <ProgramArguments[1]> ]` existence assert between plist edit and supervisor reload (F3).

---

## 5. Bootstrap from the drill path + verification battery (G5 Phase 3/4 analogs) — 19:08:28Z

Note: the demo label was **unloaded**, so no G5-3.1-style bootout was needed — launchd holds no cached plist; `bootstrap` reads the edited file fresh. G5's bootout-then-bootstrap is for LOADED jobs (hazard (a) — launchd caches the plist at bootstrap).

```
bootstrap gui/501 ~/Library/LaunchAgents/ensemble-demo.plist → exit 0   (ritual asserts all PASS)
state = running · runs = 1 · pid 45690 · working directory = DRILL · stdout path = DRILL/data/launcher.log
process tree: 45690 (ppid=1) bash DRILL/launcher.sh → 45712 DRILL/current/ensemble-prod
/livez  → 200 {"status":"alive","uptime_seconds":4.14,"version":"0.10.5"}
/readyz → 200 {"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},…}

money-line: 2026-11-10T02:08:28+0700 launcher[45690]: ensemble launcher starting
            (INSTALL_DIR=/Users/nguyenminhkha/ensemble-demo-drill-tmp)      ← relocation took
MCP (era caveat — from launcher.err.log, NOT /readyz):
  Context7 Documentation MCP Server v4.6.0 running on stdio
  MCP warm-up pool ready: {'context7': {'available': 1, 'pool_size': 1, 'healthy': True}}   ← PATH survived the move
  eager_warm_schemas: primed 1/2 (plane: 0 tools, context7: 2 tool(s))    (plane: known non-PATH failure)
PROD after phase: pid=51569 status=0 · :9797/livez 200 ✓
```

---

## 6. Rollback (mission spec: inverse sed; G5 RB.3 spec: byte-restore — both executed, byte-compare adjudicates)

```
19:09:06Z  bootout gui/501/com.ensemble.demo → exit 0 · NOT LOADED · :7979 FREE
           D8 trace: 02:09:06 received SIGTERM — forwarding to child (pid 45712)
                     02:09:07 Graceful shutdown complete
                     02:09:08 launcher: shutdown complete — exiting 143 (child's exit code)   [window ≈ 2 s]
19:09:08Z  mv DRILL → ~/agents-ensemble-demo (target absent → true rename; exit 0; drill path gone ✓)
inverse sed (both files): drill-path count 0/0 · demo-path count 4/4 · plutil -lint OK
BYTE-COMPARE vs /tmp baseline:
  installed  5e2bf45ca4a47eeedc3dadb9a8b2ebcadf685d989b723e4625401117af216d2a
  baseline   5e2bf45ca4a47eeedc3dadb9a8b2ebcadf685d989b723e4625401117af216d2a   → IDENTICAL ✓
  staged     5e2bf45ca4a47eeedc3dadb9a8b2ebcadf685d989b723e4625401117af216d2a   → IDENTICAL ✓
  PATH value shasum after round-trip: 7d4f6a9b… == baseline ✓
19:09:30Z  bootstrap at ORIGINAL path → running pid 46721 (ppid=1) → child …/agents-ensemble-demo/current/ensemble-prod
           /livez 200 (v0.10.5) · /readyz ready all-green
           money-line: INSTALL_DIR=/Users/nguyenminhkha/agents-ensemble-demo ✓
19:09:50Z  final bootout → NOT LOADED · :7979 FREE
layout restore: rm data/ensemble-demo.plist (staged copy was a drill artifact; content archived /tmp/reh-b)
residue: drill dir ABSENT ✓ · PROD: pid=51569 · :9797/livez 200 ✓
```

**Round-trip verdict:** inverse-sed restored **byte-identical** files — so G5's byte-restore (RB.3) and a reverse sed are equivalent *for this anchor pair*; G5's `cp -p` restore remains the safer canonical (no pattern risk), and the byte-compare is the evidence that either path is clean.

---

## 7. Findings for the doc (ranked)

| # | Finding | Action for G5/canonical doc |
|---|---|---|
| **F1** | **G5 2.1+2.2 nesting bug** — mkdir target + `mv tree target` nests the tree inside it (proven §2); 2.3's verify would fail, and worse, an operator might not run 2.3 before Phase 3. | Change 2.1 to a freshness test only (`[ ! -e /opt/ensemble ]`), keep 2.2 `mv` onto the NONEXISTENT path; or `mv ~/agents-ensemble /opt/` after mkdir. Must fix before live. |
| **F2** | Lossless-move proof method that works: relpath+sha256 manifest of all 1415 files + du (marker A == marker B). Cheap (~seconds for ~227 MB). | Add as an optional verification step; gives the operator a hard lossless guarantee, not just `ls` spot-checks. |
| **F3** | **Hazard (b) is invisible to `plutil -lint`** (simulated: stale-path plist lints OK). | Insert `[ -e <ProgramArguments[1]> ]` assert between plist edit and supervisor reload in G5. |
| **F4** | Anchored sed round-trip (forward+inverse) is byte-clean on both installed and staged copies; PATH block byte-identical throughout (shasum-proven twice). The G5 1.5-style pre-edit grep assertions worked cleanly in the inverted-hazard direction. | Keep G5's byte-restore as canonical; note round-trip equivalence as evidence. |
| **F5** | Relocated boot is fully healthy incl. MCP (context7 healthy via preserved PATH) — relocation interacts with NOTHING in the tree; `.env` has zero path-valued keys (G5 1.7 holds). | — |
| **F6** | Demo drill needs **no sudo** (home sibling); live needs sudo only for `/opt`. Everything else transfers 1:1. | State in doc; not a defect. |
| **F7** | D8: TERM→exit window ≈ **1–2 s** across 4 bootouts (log-proven `received SIGTERM → Graceful shutdown complete → exiting 143`); `CHILD_STOP_WAIT_S=70` never engaged. Launcher **does** log the TERM receipt line (`received SIGTERM — forwarding to child`) — 4 instances in err.log. | Runbook can state sub-5 s expected stop windows for this daemon generation. |
| **F8** | G5 3.1's bootout-then-bootstrap applies only to LOADED jobs (plist cache). For an unloaded label, plain bootstrap suffices — but the edited-while-loaded case (hazard (a)) is real and must keep the bootout step. | No change; clarify wording ("if loaded"). |

## 8. End state (verified)

`com.ensemble.demo` NOT LOADED · `:7979` FREE · no demo processes · demo tree at **original** path `~/agents-ensemble-demo` · installed plist byte-identical to rehearsal-a end-state (`5e2bf45c…`) · no staged copy in `data/` · drill dir gone · PROD pid 51569 · `:9797/livez` 200.
