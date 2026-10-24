# Layer 1 ARM RUNBOOK — ownership separation for /home/nea/agents-ensemble
#
# ── LOCATION NOTE ────────────────────────────────────────────────────────
# REPO-CANONICAL COPY: docs/runbooks/install-dir-protection-layer1-arm.md
# (branch feature/install-dir-protection). Host convenience copy:
# /home/nea/.ensemble-layer1-arm-runbook.md — kept in sync; this repo copy
# is the canonical version going forward. Mirror of host armor in
# scripts/protection/.
# ────────────────────────────────────────────────────────────────────────

**Status:** PREP COMPLETE 2026-10-12 (DevOps operator lane). Arming is a
**USER-GATED WINDOW** — every step below runs only with the user present.
Companion artifacts: `/usr/local/bin/ensemble-promote` (staged, inert),
`/home/nea/.ensemble-layer1-unit-drop-in.candidate.conf` (inert candidate).

**Incident note (2026-10-12):** install dir deleted twice (≈12:14, ≈19:11 UTC)
by agent-authored test code (instance e312eb09, worktree
ensemble-src-wt-upg-overhaul, tests/test_journal_splice.sh §2). Immediate root
cause = TEST SCRIPT defect in the upgrade-speed overhaul lane, ALREADY manually
fixed by the user 2026-10-12. Layer 1 is the structural fix for the damage
class — not tied to the one script. Plan:
`/home/nea/ensemble-src/.agents/shared/protection-plan-install-dir-20261012.md`

---

## Why .env lock-down could NOT happen earlier (earliest-safe ruling)

Evidence captured 2026-10-12 (posture UPDATED 2026-10-23 — zero-env unit
contract, unit-secret-exposure):

- `ensemble-main.service` runs `User=nea`, `ExecStart=launcher.sh`, and has
  **no `EnvironmentFile=`**. Since the zero-env generator strip
  (2026-10-23) the unit also carries **NO inline secret env**: its only
  `Environment=` directive is the static `Environment=MALLOC_ARENA_MAX=2`.
  `.env` is now the SINGLE secret surface on the unit path — which
  STRENGTHENS this runbook's chown reasoning (one surface to own and lock,
  not two). (The PRE-STRIP live unit still carries six minted
  `Environment=` lines until the one-time remediation lands — see
  `docs/runbooks/systemd-adoption.md` §One-time live remediation.)
- `launcher.sh:1649` executes `load_env_file "$INSTALL_DIR/.env"` **as the
  unit user (nea) at every start**; the binary's own .env loader (run_app.py,
  frozen-binary-only) re-reads it as the same user.
- Therefore `chown ensemble:ensemble + chmod 600 .env` **before** the unit
  flips `User=ensemble` breaks the NEXT restart (launcher as nea cannot read
  .env → missing OPENAI_*/SYSTEM_* env → degraded boot or exit-78 refusal).
- **Ownership precondition of the 0600 posture (A6):** a future
  ROOT-owned 0600 `.env` would recreate exactly this failure
  (launcher can't read it) — **ownership == `User=` is a precondition of
  the 600 mode posture**; equally, stages/deploys must run as the install
  owner (a cross-uid stage re-owns `.env` via its `mv -f` inode replacement).
- Not provably safe for the running daemon + next boot ⇒ deferred into this
  window, where it lands automatically as part of the chown step (step 2).
  This is the commission's "runbook step 1b" fallback, by its own criteria.

Related observation for the deferred key-rotation workstream (report-only;
SCOPE SHRUNK 2026-10-23 — D8): post-strip the unit never needs rotation
edits again — secret rotation scope is the `.env` file ALONE (update the
standing deferred-key-rotation critical-note coupling when remediation
lands).

## Preconditions (verify at window open)

```bash
# Layer 3 armed + a current snapshot exists
sudo ls -1d /home/nea/.install-snapshots/snap-* | tail -1
systemctl list-timers 'ensemble-*' --no-pager        # snapshot 30m + treecheck 60s
# ensemble user exists (uid 999, locked, nologin)
getent passwd ensemble
# wrapper staged + inert
ls -la /usr/local/bin/ensemble-promote                 # root:root 755
grep -rl ensemble-promote /etc/systemd/system/ || echo "unwired (expected)"
```

Team-side coordination (plan §Layer 1) must be confirmed BEFORE the window:
launcher `_live_install_root()` / journal-path HOME resolution under
`HOME=/home/nea/agents-ensemble`; daemon-side promote executor (systemd-run
lane) switched from root to `ensemble`; note `data/` (launcher.log,
vscode-user-data) becomes ensemble-writable via the chown automatically.
The demo unit (`ensemble-demo.service`, tree `/home/nea/agents-ensemble-demo`,
User=nea) is a SEPARATE tree — unaffected, no action.

### Code-review prerequisites (2026-10-12 — executor-user mechanism review, commit c527cdd32)

Hard prerequisites for the flip window — findings from the code-review pass on
the executor-user mechanism, NOT optional:

**P1 — polkit authorization for the service user.** Both existing grants gate
on `subject.user == "nea"` (`/etc/polkit-1/rules.d/46-ensemble-transient-scope.rules`
and `50-ensemble-units.rules`), so post-flip the promote executor (running as
`ensemble`) loses transient-unit mint + reset-failed authorization and hits
the existing loud fail-closed R6 refusal (by design). Before the flip, add a
rule mirroring the 2026-09-28 pattern for the new subject — e.g.
`/etc/polkit-1/rules.d/47-ensemble-user.rules`:

```js
polkit.addRule(function(action, subject) {
    if (action.id !== "org.freedesktop.systemd1.manage-units") {
        return polkit.Result.NOT_HANDLED;
    }
    if (subject.user !== "ensemble") {
        return polkit.Result.NOT_HANDLED;
    }
    var unit = action.lookup("unit");
    if (unit == null) {            // detail-less class: transient mint, ResetFailed
        return polkit.Result.YES;
    }
    if (unit.match(/^ensemble-[0-9A-Za-z@._-]+\.scope$/) !== null) {
        return polkit.Result.YES;  // our own transient scopes only
    }
    return polkit.Result.NOT_HANDLED;
});
```

(polkitd 124 auto-reloads rules.d via inotify — no restart needed. If the
ensemble user must also manage regular `ensemble-*` service units
non-interactively, extend `50-ensemble-units.rules` to accept
`subject.user == "ensemble"` in the same shape.) Verification — both must
succeed pre-flip:

```bash
sudo -u ensemble systemd-run --scope --unit=ensemble-arm-drill-scope /bin/true
sudo -u ensemble systemctl reset-failed
```

**P2 — real HOME with exact basename `agents-ensemble`.** The executor
uid-pinning arming condition probes the LEXICAL basename of the service
user's HOME: it must be a real directory (not a symlink) whose basename is
exactly `agents-ensemble`. A mismatch does NOT error — the uid-pinning
silently does NOT arm. The flip sets `HOME=/home/nea/agents-ensemble` via the
drop-in, which satisfies the shape only while the path stays a real dir.
Verify before the flip:

```bash
test "$(basename /home/nea/agents-ensemble)" = "agents-ensemble" \
  && test -d /home/nea/agents-ensemble && ! test -L /home/nea/agents-ensemble \
  && echo "HOME shape OK (real dir, basename exact)" \
  || echo "HOME SHAPE MISMATCH — uid-pinning would silently NOT arm"
```

**P3 — uv resolvable PATH-first for the ensemble user.** stage.sh's
`$HOME/.local/bin/uv` fallback probes the SERVICE-USER home post-flip:
`/home/nea/agents-ensemble/.local/bin/uv` — absent today. The host's only uv
copy (`/home/nea/.local/bin/uv`, nea:nea) is not executable by `ensemble`
(parent traversal denied — verified 2026-10-12: `uv NOT resolvable for
ensemble`, PATH-first). Before the flip, either provision uv at a system path
on the ensemble user's PATH (e.g. root-owned symlink `/usr/local/bin/uv`) or
provision `/home/nea/agents-ensemble/.local/bin/uv` (ensemble-owned, mode
755). Verification — at least ONE branch must succeed:

```bash
sudo -u ensemble env HOME=/home/nea/agents-ensemble bash -c 'command -v uv && uv --version' \
  || sudo -u ensemble test -x /home/nea/agents-ensemble/.local/bin/uv \
  || echo "UV GAP — stage.sh fallback would miss post-flip"
```

## ORDER IS LOAD-BEARING: chown FIRST, unit flip SECOND.

### Step 1 — fresh insurance snapshot

```bash
sudo /usr/local/sbin/ensemble-install-snapshot.sh
```

### Step 2 — capture pre-arm mode map, then reown the tree

```bash
# 2a. mode map for rollback fidelity (owned by root ideally; at least keep it)
sudo find /home/nea/agents-ensemble -printf '%m %U:%G %p\n' \
    | sudo tee /home/nea/.layer1-prearm-modes.txt >/dev/null

# 2b. ownership separation (this is the moment agent rm -rf / writes / .env
#     reads all die — structural, not policy)
sudo chown -R ensemble:ensemble /home/nea/agents-ensemble
sudo chmod -R go-w /home/nea/agents-ensemble
sudo chmod 600 /home/nea/agents-ensemble/.env      # runbook step 1b lands HERE
sudo chmod 755 /home/nea/agents-ensemble           # 775 → 755 top dir
```

### Step 3 — stop the daemon (in-window coordinated restart)

```bash
sudo systemctl stop ensemble-main.service
```

### Step 4 — install the drop-in + reload

```bash
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo cp /home/nea/.ensemble-layer1-unit-drop-in.candidate.conf \
        /etc/systemd/system/ensemble-main.service.d/50-layer1-user.conf
sudo systemctl daemon-reload
```

### Step 5 — start + verify health

```bash
sudo systemctl start ensemble-main.service
systemctl status ensemble-main.service --no-pager
journalctl -u ensemble-main -n 100 --no-pager     # clean boot, no exit-78
# then the standard upgrade-lane health gates (livez → readyz)
```

### Step 6 — denial drills AS NEA (all three must FAIL)

```bash
rm -rf /home/nea/agents-ensemble/releases/v0.20.0   # → Permission denied
cat /home/nea/agents-ensemble/.env                  # → Permission denied
touch /home/nea/agents-ensemble/releases/x          # → Permission denied
```

### Step 7 — daemon functional smoke

One agent turn end-to-end (message → tool → reply), DB connectivity, and
`ens_env_read` (daemon runs as ensemble → still reads its own .env).

### ⛔ GATE — C1 WRAPPER HARDENING (HARD PRECONDITION before step 8 and ANY sudoers installation; 2026-10-12)

NO sudoers entry may be installed — and step 8 must NOT run — while a
pre-C1 wrapper is deployed at `/usr/local/bin/ensemble-promote` (review
finding C1: a `*.sh` glob over the nea-writable dev checkout under NOPASSWD
is a latent ensemble-user ACE = the exact incident damage class). The
DEPLOYED copy must contain the C1 fix (round 3: NEW-1 + NEW-2): exact
enumerated allowlist (promote.sh / rollback.sh / restart.sh dev-lane +
staged-payload promote.sh shapes only) + invocation-time control refusal —
group/other-writable, or OWNED by the invoking user UNCONDITIONALLY (NEW-1:
the owner write bit is NOT consulted; chmod needs ownership not write
permission, so a self-owned chmod-444 script is still invoker-controlled) —
plus canonicalization (readlink -f) BEFORE the allowlist match (NEW-2: the
case-pattern * crosses "/" and traversal strings must resolve away). Verify
BOTH refusals against the deployed copy — both must be refused AND journaled:

```bash
# (a) non-allowlisted script under the dev lane → exit 77 + journal line
sudo /usr/local/bin/ensemble-promote /home/nea/ensemble-src/scripts/upgrade/lib.sh
# (b) allowlisted but agent-OWNED (any mode — incl. chmod 444) → exit 76 + journal line
sudo /usr/local/bin/ensemble-promote /home/nea/ensemble-src/scripts/upgrade/promote.sh
sudo journalctl -t ensemble-promote --since "-2 min" --no-pager   # both C1 refusals visible
```

### Step 8 — OPTIONAL sudoers for the pipeline lane (GATED: only after the ⛔ C1 gate above passes)

```bash
echo 'nea ALL=(root) NOPASSWD: /usr/local/bin/ensemble-promote' \
    | sudo tee /etc/sudoers.d/ensemble-promote
sudo chmod 440 /etc/sudoers.d/ensemble-promote
sudo visudo -cf /etc/sudoers.d/ensemble-promote     # MUST print "parsed OK"
```

If `visudo -cf` shows ANY problem: `sudo rm /etc/sudoers.d/ensemble-promote`
and leave the wrapper sudo-invocable by the operator only.

### Step 9 — POST-WINDOW, user-gated: one full promote drill as ensemble

```bash
sudo /usr/local/bin/ensemble-promote \
     /home/nea/ensemble-src/scripts/upgrade/promote.sh <args per upgrade lane>
journalctl -t ensemble-promote --no-pager           # invocation audit trail
```

C1 note (2026-10-12): the hardened wrapper additionally refuses scripts
OWNED by the invoking user — unconditionally, regardless of mode (a
chmod-444 self-owned script is still invoker-controlled; NEW-1) — so the
dev-lane scripts (nea:nea) invoked as nea are refused BY DESIGN (exit 76;
that is the protection working, not a failure). Run the drill from a
non-nea operator account, or stage the script root-owned first (allowlist
additions = explicit commission + commit only).

## R — ROLLBACK (any failed step)

```bash
sudo systemctl stop ensemble-main.service
sudo rm -f /etc/systemd/system/ensemble-main.service.d/50-layer1-user.conf
sudo systemctl daemon-reload
sudo chown -R nea:nea /home/nea/agents-ensemble
# restore original modes from the step-2a map (minimal known set:
# top dir 775, launcher.sh 775, .env 600, config.yaml/state.json 644,
# group-write in data/ per the map):
sudo chmod 775 /home/nea/agents-ensemble /home/nea/agents-ensemble/launcher.sh
sudo chmod 600 /home/nea/agents-ensemble/.env
sudo systemctl start ensemble-main.service
```

Snapshots (Layer 3) remain the full-tree restore path if anything worse
happens: `rsync -aHAX /home/nea/.install-snapshots/snap-<ts>/ /home/nea/agents-ensemble/`
then re-chown per the era, then start the unit.
