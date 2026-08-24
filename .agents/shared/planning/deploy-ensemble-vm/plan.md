# Deploy agents-ensemble → ensemble-vm (192.168.1.151)

> Plan owner: DevOps agent · Drafted 2026-08-24 · **Rev 5 (tag v0.11.0)** · Status: awaiting approval
> Target: `ensemble-vm` (VMID 151, pve3) — Ubuntu 24.04.4, 8c/10 GB RAM, 230G NVMe root.
> Access: `ssh ensemble-local` (key auth), user `nea` (password sudo).
> Rev 5 change: **deploy the tag `v0.11.0`** (annotated tag → commit `4634969`).
> Rev 4 change: use the repo's OWN deployment system (deploy.sh + 3-env topology);
> dev + demo envs included for agent self-modification, separated from live.

## Deployment vehicle decision

| Goal | Repo mechanism | Why |
|---|---|---|
| Deploy a **tag** (this plan) | `scripts/deploy.sh live|demo` (via `make deploy-live`/`deploy-demo`) | builds from the **current checkout** — explicitly refuses the `ensure-latest` chain (deploy.sh:19-22), so the tag stays pinned |
| Deploy `latest` branch (NOT this plan) | `make install` | `ensure-latest` does `git checkout latest && git pull` — would yank the checkout off v0.11.0 |

Both share the same staging/start/health-gate pipeline. For v0.11.0 we use `deploy.sh`.

## The repo's canonical 3-env topology (scripts/deploy.sh + scripts/upgrade/lib.sh)

| Env | Location | Port | Postgres DB | Purpose |
|---|---|---|---|---|
| **dev** | repo checkout (`dev.sh`, reload) | 8079 | dev (repo `.env`) | agent self-modification sandbox #1 |
| **demo** | `~/agents-ensemble-demo` | 7979 | `ensemble_demo` | rehearsal target — REAL prod shape, upgrade-pipeline guinea pig |
| **live** | `~/agents-ensemble` (`INSTALL_DIR`) | 9797 (from staged `.env`, ADR-014) | `ensemble_prod` | the real thing |
| sandbox | explicit dir+port (fail-closed) | explicit | explicit | drills |

Pipeline rules we inherit for free: ownership-scoped stops (dev/demo/live coexist;
foreign daemons reported, never killed), live gated by `ENSEMBLE_DEPLOY_LIVE=1`,
demo env `.env.prod.demo` derived on demand from `.env.prod` (PORT=7979, DB=ensemble_demo),
health gates `/livez` ≤60s + `/readyz` ≤120s, upgrades via `scripts/upgrade/*.sh`
(local builds only, `ENSEMBLE_UPGRADE_LIVE=1` guard).

## Key facts

| Fact | Consequence |
|---|---|
| Repo `disillusioners/ensemble` is public | plain `git clone` on VM |
| **Deploy version: tag `v0.11.0`** → commit `4634969` | checkout the tag in `~/ensemble-src`; demo+live both at v0.11.0 (rehearsal fidelity) |
| `pyproject.toml` requires Python ≥3.13 | Ubuntu 24.04 → **uv** |
| Frontend: Angular 21 (`npm install && npm run build` inside deploy flow) | Node 22 on VM |
| DB = k8s **psql18** (PG 18.4.0, ns `postgres18`, ctx `mtri`) via NEW LB svc | house pattern (neo4j/harbor); NodePort fallback |
| ⚠️ Old PG17 (`psql`, LB 10.44.0.0) — migration planned, **read-only, hands off** | never point anything new at it |
| `.env.prod` gitignored (public repo) | scp Mac → VM repo checkout; adjust POSTGRES_HOST/PORT → LB; never echo values |
| launcher/plist/watchdog are macOS launchd | Linux: systemd wraps the launcher/binary; systemd `Restart=` replaces watchdog |

## Target state

```
ensemble-vm (192.168.1.151)
├── ~/ensemble-src                      ← git clone @ v0.11.0 = BUILD SOURCE + dev env (8079)
│   ├── .env.prod                       ← scp'd from Mac, POSTGRES_HOST/PORT → psql18-lb
│   └── .env (dev)                      ← optional, for dev.sh runs
├── ~/agents-ensemble                   ← LIVE (deploy.sh live): binary+launcher+.env(9797)+data
└── ~/agents-ensemble-demo              ← DEMO (deploy.sh demo): 7979, DB ensemble_demo
        │
        ▼  all DBs on k8s psql18 via svc psql18-lb (10.44.0.x:5432)
```

systemd: `ensemble-live.service` (+ optional `ensemble-demo.service`). Access via SSH
tunnels (9797 live / 7979 demo / 8079 dev).

## Execution phases

### Phase 0 — VM prep (Low)
```bash
sudo apt update && sudo apt install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

### Phase 1 — k8s psql18: LB + both databases (Medium)
```yaml
# data-center-scripts/infranstructure/k8s/postgres/psql18-lb.yaml (GitOps-tracked)
apiVersion: v1
kind: Service
metadata: {name: psql18-lb, namespace: postgres18}
spec:
  type: LoadBalancer
  selector: {app.kubernetes.io/name: postgresql, app.kubernetes.io/instance: psql18}
  ports: [{port: 5432, targetPort: 5432}]
```
```bash
kubectl --context mtri apply -f psql18-lb.yaml
DB_IP=$(kubectl --context mtri get svc -n postgres18 psql18-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
ssh ensemble-local "timeout 3 bash -c '</dev/tcp/$DB_IP/5432' && echo REACHABLE"
PW=$(grep '^POSTGRES_PASSWORD=' ~/All/Code/opensource-projects/agents-ensemble/.env.prod | cut -d= -f2-)
SU=$(kubectl --context mtri get secret -n postgres18 psql18-postgresql -o jsonpath='{.data.postgres-password}' | base64 -d)
kubectl --context mtri exec -n postgres18 psql18-postgresql-0 -- env PGPASSWORD="$SU" psql -U postgres \
  -c "CREATE USER ensemble WITH PASSWORD '$PW';" \
  -c "CREATE DATABASE ensemble_prod WITH OWNER ensemble;" \
  -c "CREATE DATABASE ensemble_demo  WITH OWNER ensemble;"
```
(live DB created manually — deploy.sh is report-only for live; demo pre-created so
`--create-db` is unneeded. Fallback if LB unreachable: NodePort 31318, any node IP.)

### Phase 2 — Source checkout @ v0.11.0 (Low)
```bash
git clone https://github.com/disillusioners/ensemble.git ~/ensemble-src
cd ~/ensemble-src && git fetch --tags && git checkout v0.11.0
git rev-parse HEAD        # MUST be 463496989d75cc00ac22af41d96fdaaa6759c47b
uv python install 3.13 && uv sync
```

### Phase 3 — Secrets (Mac side)
```bash
scp ~/All/Code/opensource-projects/agents-ensemble/.env.prod ensemble-local:~/ensemble-src/.env.prod
ssh ensemble-local "chmod 600 ~/ensemble-src/.env.prod && sed -i \
  -e 's/^POSTGRES_HOST=.*/POSTGRES_HOST=$DB_IP/' -e 's/^POSTGRES_PORT=.*/POSTGRES_PORT=5432/' \
  ~/ensemble-src/.env.prod"
```
(PORT stays 9797; deploy.sh derives `.env.prod.demo` from this file with PORT=7979 +
DB=ensemble_demo overrides when staging demo.)

### Phase 4 — LIVE @ v0.11.0 via deploy.sh (Medium)
```bash
cd ~/ensemble-src
ENSEMBLE_DEPLOY_LIVE=1 make deploy-live
```
- builds PyInstaller binary from the **current checkout (v0.11.0)** — no branch switching
- stages binary + agents/ + frontend/dist + config.yaml + launcher.sh + `.env` (from `.env.prod`)
  into `~/agents-ensemble`, ownership-scoped stop, starts, health-gates `/livez` + `/readyz`
- `--dry-run` first is available and will be used to preview the stage plan

### Phase 5 — systemd for boot persistence (Medium)
`/etc/systemd/system/ensemble-live.service`:
```ini
[Unit]
Description=Ensemble Live (agents-ensemble) v0.11.0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nea
WorkingDirectory=/home/nea/agents-ensemble
ExecStart=/home/nea/agents-ensemble/launcher.sh     # verify at execution; if self-daemonizing, exec ensemble-prod directly
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
(Stop the deploy.sh-started instance before enabling the unit, so systemd owns the process.)

### Phase 6 — DEMO @ v0.11.0 (Medium)
```bash
cd ~/ensemble-src && make deploy-demo
```
Optional `ensemble-demo.service` mirroring Phase 5.

### Phase 7 — Verify
1. live: `/livez` + `/readyz` on `:9797` green (deploy.sh gates these itself)
2. demo: same on `:7979`
3. DBs on psql18: `ensemble_prod` + `ensemble_demo` exist; tables appear after first boot
4. `systemctl status ensemble-live` + survives `sudo reboot`
5. `curl -s https://rag.mtri.app` from VM (RAG_IS_REQUIRED=true gate)
6. Mac tunnels: `ssh -L 9797:127.0.0.1:9797 -L 7979:127.0.0.1:7979 ensemble-local`
7. Version pin check: `~/agents-ensemble` runs binary built from `4634969`

### Phase 8 — Self-modification workflow (the point of dev/demo)
- Agents modify code in `~/ensemble-src` (dev env, `dev.sh`, port 8079) — reload mode, disposable
- Promote experiments: `make upgrade-stage TARGET=demo` → `upgrade-promote/rollback` (7979 guinea pig)
- Live only ever changes via `make deploy-live` (after `git checkout <new tag>`) or the
  upgrade pipeline with `ENSEMBLE_UPGRADE_LIVE=1` — never hand-edits in `~/agents-ensemble`

## Rollback
1. `sudo systemctl disable --now ensemble-live` (+ demo), rm units
2. `cd ~/ensemble-src && make uninstall` (removes `~/agents-ensemble`); rm `~/agents-ensemble-demo`
3. k8s: `kubectl delete svc psql18-lb -n postgres18`; optionally drop `ensemble_prod`/`ensemble_demo`
   (**data — pg_dump first**)
Blast radius: this VM + 1 svc + 2 isolated DBs on psql18. Old PG17 untouched.

## Risks & mitigations
| Risk | Mitigation |
|---|---|
| Accidental `make install` yanks checkout to `latest` | deploy.sh-only rule in this doc + runbook; verify SHA before every deploy |
| PyInstaller/Angular build RAM | 10 GB ample; builds sequential |
| launcher.sh under systemd misbehaves | verified at Phase 5; fallback = exec binary direct |
| Old PG touched by mistake | named trap; only endpoint is psql18-lb |
| LB IP unreachable | NodePort fallback (31318, any node IP) |
| Public repo secrets hygiene | `.env.prod`/`.env.prod.demo` gitignored, scp-only, 600 |

## Not in scope
nginx/TLS/LAN exposure (tunnels only), k8s deploy of ensemble, CI/CD, old-PG migration.

## Update runbook (post-deploy, tag-pinned)
```bash
ssh ensemble-local && cd ~/ensemble-src
git fetch --tags && git checkout v0.11.1          # new tag
git rev-parse HEAD                                # verify expected SHA
ENSEMBLE_DEPLOY_LIVE=1 make deploy-live           # live @ new tag
make deploy-demo                                  # demo @ same tag
sudo systemctl restart ensemble-live              # if unit-managed
```
