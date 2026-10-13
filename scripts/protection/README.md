# Install-Dir Protection — host armor (Layers 1/3/4)

**Incident note (2026-10-12):** `/home/nea/agents-ensemble` was deleted twice
in one day (≈12:14 and ≈19:11 UTC) by agent-authored test code (instance
`e312eb09`, worktree `ensemble-src-wt-upg-overhaul`, `tests/test_journal_splice.sh`
§2 — no-op HOME sandbox + `rm -rf "$HOME/agents-ensemble"` cleanup). Immediate
root cause = TEST SCRIPT defect in the upgrade-speed overhaul lane, ALREADY
manually fixed by the user on 2026-10-12. These artifacts are the
DEFENSE-IN-DEPTH structural backstop against the damage class (future script
regressions, agent mistakes, human error) — NOT tied to the one fixed script.
Plan: `.agents/shared/protection-plan-install-dir-20261012.md`.

This directory is the **version-controlled source of truth** for the host
armor deployed 2026-10-12. Each file carries a header mapping it to its
deployed host path. Edit here, redeploy to the host, and keep both in sync.

## Deployment map

| Repo source (scripts/protection/) | Host path | Deploy command | Enable command |
|---|---|---|---|
| `ensemble-install-snapshot.sh` | `/usr/local/sbin/ensemble-install-snapshot.sh` | `sudo install -o root -g root -m 755 ensemble-install-snapshot.sh /usr/local/sbin/` | (timer below) |
| `ensemble-treecheck.sh` | `/usr/local/sbin/ensemble-treecheck.sh` | `sudo install -o root -g root -m 755 ensemble-treecheck.sh /usr/local/sbin/` | (timer below) |
| `ensemble-promote` (STAGED/INERT — Layer 1) | `/usr/local/bin/ensemble-promote` | `sudo install -o root -g root -m 755 ensemble-promote /usr/local/bin/` | none until Layer 1 arm window; optional sudoers entry per runbook step 8 (`visudo -cf` before leaving active) |
| `ensemble-install-snapshot.service` | `/etc/systemd/system/` | `sudo install -o root -g root -m 644 ensemble-install-snapshot.service /etc/systemd/system/` | via timer |
| `ensemble-install-snapshot.timer` | `/etc/systemd/system/` | `sudo install -o root -g root -m 644 ensemble-install-snapshot.timer /etc/systemd/system/` | `sudo systemctl daemon-reload && sudo systemctl enable --now ensemble-install-snapshot.timer` |
| `ensemble-treecheck.service` | `/etc/systemd/system/` | `sudo install -o root -g root -m 644 ensemble-treecheck.service /etc/systemd/system/` | via timer |
| `ensemble-treecheck.timer` | `/etc/systemd/system/` | `sudo install -o root -g root -m 644 ensemble-treecheck.timer /etc/systemd/system/` | `sudo systemctl daemon-reload && sudo systemctl enable --now ensemble-treecheck.timer` |
| `ensemble-layer1-unit-drop-in.candidate.conf` (INERT candidate) | `/home/nea/.ensemble-layer1-unit-drop-in.candidate.conf` (staging; NOT under `/etc` until the arm window) | `cp ensemble-layer1-unit-drop-in.candidate.conf /home/nea/.ensemble-layer1-unit-drop-in.candidate.conf` | **DO NOT install as a live drop-in** before the arm-window chown — see runbook |

Snapshot data lives at `/home/nea/.install-snapshots` (root:root 700 —
contains `.env`; the snapshot script creates/permissions it itself).

**Repo-native exception:** `generate_prompt_artifact_baseline.py` (Phase A
T13, agent-prompt-immutability S9) is NOT a host-armor mirror — it has no
deployed host path. It generates/verifies the committed
`agents/.prompt-artifacts.sha256` baseline consumed by the
`scripts/upgrade/stage.sh` drift leg (read-only + journaled, never gating)
and the daemon boot WARN (`daemon/api.py` lifespan).

Full Layer 1 arm procedure (user-gated window): see
`docs/runbooks/install-dir-protection-layer1-arm.md` (repo-canonical copy;
host convenience copy at `/home/nea/.ensemble-layer1-arm-runbook.md`).

## Status as of 2026-10-12 (deployed by DevOps operator lane)

| Layer | Status | Verification evidence (one-liners) |
|---|---|---|
| 3 — snapshots | **ARMED** | first snapshot `snap-20261012T193901Z` full 166 MB at 19:39:01Z; 2nd snap +4.4 MB with **5357/5358 files hardlinked** (links=2, same inode across snaps; live tree isolated at links=1); timer enabled+active, next run 20:00 UTC; `nea` denied at snapshot root (secrets safe) |
| 4.2 — tree-loss alert | **ARMED** | drill on throwaway absent path fired journal **CRIT** (`journalctl -t ensemble-treecheck -p crit` shows `TREE LOSS DETECTED … Restore path: /home/nea/.install-snapshots`); real path passes clean exit 0; 60 s timer enabled+active, silent when healthy |
| 4.1 — auditd attribution | **BLOCKED (absent + no-install rule)** | see below |
| 1 — ownership separation | **PREP COMPLETE, arming user-gated** | user `ensemble` (uid 999, locked, nologin); wrapper staged inert; runbook delivered |

### auditd gap (Layer 4.1) — arm-once-approved

`auditd`/`auditctl`/`ausearch` are **not installed** on the host and the
standing host rule forbids unprompted package installs (evidence + options
only). Detection + recovery are covered by 4.2 + Layer 3; **per-event
attribution (uid/pid/exe on unlinks) is NOT available** until auditd lands.
Once the user approves `apt install auditd` (or `auditd audispd-plugins`):

```bash
sudo apt install auditd                      # USER-APPROVED INSTALL ONLY
sudo auditctl -w /home/nea/agents-ensemble -p wa -k ensemble-install
printf -- '-w /home/nea/agents-ensemble -p wa -k ensemble-install\n' \
    | sudo tee /etc/audit/rules.d/ensemble-install.rules
# drill: create + unlink a clearly-marked file inside the tree, then:
sudo ausearch -k ensemble-install -i         # expect uid/pid/exe for the unlink
```

### Deployed-mechanism notes

- Snapshot isolation is deliberate: snapshot inodes are never shared with the
  live tree, so in-place live writes cannot corrupt a snapshot and live-tree
  deletion cannot touch snapshot data.
- Tree-loss polling (60 s timer) is a **documented deviation** from the plan's
  `.path` unit: systemd `.path` cannot watch for ABSENCE (`PathExists=` fires
  on presence); a 60 s polling timer is the faithful mechanism. Detection
  latency ≤ ~65 s vs the incident-day mean time to notice of ~6 h.
- 30-min snapshot cadence follows the plan's prose (its `hourly.{0..N}` naming
  was a drafting artifact); retention 96 snaps ≈ 48 h.
