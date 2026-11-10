# Patch draft — data-center-scripts `ensemble-vm/README.md` refresh

- **Target file (NOT modified by this draft):** `/Users/nguyenminhkha/All/Code/data-center-scripts/infranstructure/ensemble-vm/README.md` (259 lines, read 2026-11-09)
- **Drafted:** 2026-11-10 · commission `wi-52666630` (macos-deployment-topology, doc leg)
- **Applier:** a giter lane, by hand, in the data-center-scripts repo. This file is a precise BEFORE/AFTER patch — nothing here has been applied.
- **Sources for every change:** the guard contract on `feature/macos-deployment-topology` (`scripts/install-dir-guard.sh` + Makefile header); the unit-rename ops record already in the target README (§2026-11-01); commission verification 2026-11-09/10 (live = v0.29.7).

**Scope guard for the applier:** the k8s Service named `ensemble-live` (public-chain line: `→ svc ensemble-live (selectorless) → Endpoints 192.168.1.151:9797`) is a **Kubernetes service name, NOT the systemd unit** — it is correct as-is and MUST NOT be renamed. Only the five `systemctl`-context occurrences below change.

---

## Item 1 — stale `ensemble-live` unit name → `ensemble-main.service`

The unit was renamed 2026-11 (`ensemble-live.service` → `ensemble-main.service`; the README's own §2026-11-01 record and topology table already say so — five older command blocks still carry the dead name).

### 1a · Deploy runbook (README lines ≈49 and ≈57)

**BEFORE**

```bash
sudo systemctl stop ensemble-live ensemble-demo
```

```bash
sudo systemctl start ensemble-live ensemble-demo
```

**AFTER**

```bash
sudo systemctl stop ensemble-main ensemble-demo
```

```bash
sudo systemctl start ensemble-main ensemble-demo
```

### 1b · Rollback section (README line ≈92)

**BEFORE**

```bash
sudo systemctl disable --now ensemble-live ensemble-demo
```

**AFTER**

```bash
sudo systemctl disable --now ensemble-main ensemble-demo
```

### 1c · Self-restart audit, re-own note (README lines ≈119–122)

**BEFORE**

```
   `systemctl status ensemble-live` shows inactive while the daemon serves. Re-own:
   ```bash
   bash ~/ensemble-src/scripts/stop-ensemble.sh ~/agents-ensemble 9797 && sudo systemctl start ensemble-live
```

**AFTER**

```
   `systemctl status ensemble-main` shows inactive while the daemon serves. Re-own:
   ```bash
   bash ~/ensemble-src/scripts/stop-ensemble.sh ~/agents-ensemble 9797 && sudo systemctl start ensemble-main
```

---

## Item 2 — rollback carries the install-guard confirm var + sudo-correct form

Once the VM's `~/ensemble-src` checkout includes the install guard (post merge of `feature/macos-deployment-topology` to `latest`, and the VM checkout refreshed past it), a bare `make uninstall` **refuses with exit 78** when `INSTALL_DIR` resolves to a live dir (`$HOME/agents-ensemble` is in the live set). The rollback line must carry the confirm var, and — because rollback runs under `sudo` and env vars do not survive `sudo`'s env_reset — the sudo-correct form is `sudo env VAR=… make …`.

**BEFORE (README lines ≈93–94)**

```bash
cd ~/ensemble-src && make uninstall            # removes ~/agents-ensemble
rm -rf ~/agents-ensemble-demo
```

**AFTER**

```bash
cd ~/ensemble-src && sudo env ENSEMBLE_INSTALL_LIVE=1 make uninstall INSTALL_DIR='/home/nea/agents-ensemble'   # removes ~/agents-ensemble
# (guard: INSTALL_DIR is ALWAYS required — without it the guard exits 78 with
#  "INSTALL_DIR is not set"; a live target additionally needs ENSEMBLE_INSTALL_LIVE=1,
#  and under sudo the var must ride `sudo env` — plain `sudo make …` drops it via
#  env_reset. The confirm var states INTENT only, never a path. If the VM checkout
#  predates the guard merge, plain `make uninstall` still runs — that is exactly the
#  hazard the guard closes.)
rm -rf ~/agents-ensemble-demo
```

<!--GUARD_VAR_NAME-->
> **Revision-pass caveat:** `ENSEMBLE_INSTALL_LIVE=1` / `ENSEMBLE_INSTALL_DEMO=1` are read from the code leg's worktree state (`scripts/install-dir-guard.sh:23-70`, Makefile:15-21, 2026-11-09). **Re-verify the final var names against merged `latest` after the guard merge lands** and correct this patch + `docs/deployment.md` §6.4 together if the developer's naming changed. Until the guard is merged AND the VM checkout refreshed past it, the VM still runs the unguarded Makefile — the AFTER block above is the forward-correct form.

---

## Item 3 — version drift refresh + "last verified" stamp

Drift being closed: README header says **v0.28.4** (stale since 2026-11-01); the README's own ops record says promoted **v0.28.7** on 2026-11-02; commission verification 2026-11-09/10 reports VM live at **v0.29.7**. The header should state live reality and carry a maintenance stamp so the next drift is visible.

**BEFORE (README line 3)**

```
> Deployed 2026-08-24 (v0.11.0) · currently **v0.28.4** (2026-11-01, releases/ layout + upgrade pipeline) · status: **LIVE**
```

**AFTER**

```
> Deployed 2026-08-24 (v0.11.0) · currently **v0.29.7** (releases/ layout + upgrade pipeline; promoted v0.28.4 → v0.28.7 on 2026-11-02, since advanced to v0.29.7) · status: **LIVE**
> **Last verified:** 2026-11-09 (commission wi-52666630) — update this line whenever the live version or topology is re-verified.
```

---

## Applier checklist

- [ ] Apply Items 1a/1b/1c/2/3 exactly; do NOT touch the k8s `svc ensemble-live` reference (public-chain section) — different namespace, correct name.
- [ ] After Item 2: confirm the guard var names against merged `latest` (caveat above); if unchanged, delete the placeholder note from the applied result (the caveat stays in THIS draft file, not the target README).
- [ ] Commit message suggestion: `ensemble-vm: refresh README — unit rename (ensemble-main), guarded rollback, version stamp`.
- [ ] Cross-check: `docs/deployment.md` §6.3/§6.4 in the agents-ensemble repo carries the same rollback form — keep the two in sync (deployment.md is canonical; this README quotes it).
