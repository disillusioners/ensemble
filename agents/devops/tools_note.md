# Tool Usage Notes

## Primary Tool

### bash

I execute infrastructure commands directly through bash terminal. This is my primary tool — like Giter uses bash for git, I use bash for docker, kubectl, terraform, aws, gcloud, and shell scripting.

**Usage:** All infrastructure operations use bash to execute commands.

```bash
# Docker
docker ps
docker images
docker build -t <name>:<tag> .
docker run -d --name <name> -p <port>:<port> <name>:<tag>
docker logs <container> --tail 100
docker compose -f compose.yml up -d

# Kubernetes
kubectl config current-context
kubectl get pods -A
kubectl describe pod <name> -n <ns>
kubectl logs <pod> -n <ns> --tail 200 --previous
kubectl apply -f deployment.yaml
kubectl rollout status deployment/<name> -n <ns>

# Terraform
terraform -chdir=infra init -upgrade
terraform -chdir=infra plan -out=tfplan
terraform -chdir=infra apply tfplan
terraform -chdir=infra output

# CI/CD
gh workflow list
gh run list --limit 10
gh run view <run-id> --log
gh workflow run <name> -f key=value

# Cloud
aws s3 ls
gcloud compute instances list
gcloud container clusters get-credentials <cluster>

# Shell utilities
set -euo pipefail
chmod +x script.sh
./script.sh --dry-run
```

---

## Background Process Tools (`proc` category)

Use `proc_*` tools for any long-running process: dev servers, watchers, debug sessions, background services.

### When to use `proc_*` vs `bash`

| Tool | Use For |
|------|---------|
| `bash` | Short-lived commands (`ls`, `git`, `kubectl get`, `terraform plan`) — blocks until exit, returns output |
| `proc_*` | Long-running processes (dev servers, watchers, file monitors) — returns immediately with `process_id` |

### Available tools

| Tool | Description |
|------|-------------|
| `proc_run` | Start a background process, returns `process_id` immediately |
| `proc_logs` | Read last N lines of captured stdout+stderr from a process |
| `proc_status` | Report process status (running/stopped), PID, uptime, exit code |
| `proc_stop` | Terminate a process: SIGTERM → SIGKILL after 5s grace period |
| `proc_list` | List all background processes owned by this instance |

### Constraints
- Max 10 concurrent background processes per instance
- Each process gets a 4 MB memory log buffer (spills to temp file on overflow)
- No stdin interaction — processes are fire-and-forget
- Processes are auto-cleaned when the instance terminates

### Typical workflow
```bash
# Start a dev server
proc_run(command="uvicorn main:app --port 8000")
→ returns process_id: "proc-abc123"

# Check status
proc_status(process_id="proc-abc123")

# Read recent logs
proc_logs(process_id="proc-abc123", lines=50)

# Stop when done
proc_stop(process_id="proc-abc123")
```

⚠️ **MUST use `proc_*` tools for long-running processes — NEVER use `bash` for servers, watchers, or background services.**

## Always Available

These tools are always available:

- **bash** — shell command execution
- **filesystem** — file read/write operations
- **time** — date and time queries
- **self** — agent self-modification (inner soul)
- **help** — tool help and discovery
- **knowledge** — RAG knowledge base query and record
- **mcp** — Model Context Protocol external tools
- **context** — shared context file access

## Docker Commands Reference

### Status & Info (Low Risk)

- `docker ps` — Running containers
- `docker ps -a` — All containers including stopped
- `docker images` — Local image inventory
- `docker logs <container> --tail 100` — Container logs
- `docker inspect <container>` — Container metadata
- `docker stats --no-stream` — Resource snapshot
- `docker network ls` — Network list
- `docker volume ls` — Volume list

### Build (Medium Risk)

- `docker build -t <name>:<tag> .` — Build image
- `docker build -f Dockerfile.dev -t <name>:dev .` — Specific Dockerfile
- `docker compose build` — Compose build
- `docker pull <image>:<tag>` — Pull image

### Run (Medium Risk)

- `docker run -d --name <name> <image>` — Detached run
- `docker run -d -p <host>:<container> --name <name> <image>` — With port
- `docker run -d --restart=unless-stopped <image>` — With restart policy
- `docker run --rm -it <image> /bin/sh` — Interactive shell
- `docker compose up -d` — Compose stack up
- `docker compose down` — Compose stack down (loses ephemeral data)

### Destructive (High / Critical Risk)

- `docker rm <container>` — Remove container (High)
- `docker rm -f <container>` — Force remove (High)
- `docker rmi <image>` — Remove image (High)
- `docker system prune` — Remove stopped containers, dangling images (High)
- `docker system prune -a` — Remove ALL stopped containers, unused images (Critical)
- `docker volume rm <volume>` — Remove volume (Critical — data loss)
- `docker network rm <network>` — Remove network (High)

## Container Exec Operations (High Risk)

`docker exec` and `kubectl exec` — High Risk: Allows arbitrary command execution inside containers. Can access mounted secrets, modify application state, or install packages. Use only for diagnostics, never as a substitute for proper debugging or persistent changes.

- `docker exec -it <container> /bin/sh` — Interactive shell in container (High)
- `docker exec <container> <cmd>` — Run a single command in container (High)
- `kubectl exec -it <pod> -n <ns> -- /bin/sh` — Interactive shell in pod (High)
- `kubectl exec <pod> -n <ns> -- <cmd>` — Run a single command in pod (High)

When you must exec:
1. Confirm the container/pod name and namespace explicitly
2. Prefer read-only diagnostics first (`ls`, `cat <logfile>`, `ps`, `env | grep -v SECRET`)
3. Never `cat` files that may contain secrets (`/run/secrets/*`, env files, credential mounts) — use redacted inspection instead
4. Never install packages or modify filesystem state inside a running container — that's a code/config change, fix it via a new image or manifest, not a hot-patch
5. Document the exec invocation in the runbook: pod, command, timestamp, why it was needed

## Kubernetes Commands Reference

### Read-Only (Low Risk)

- `kubectl config current-context` — Show active context
- `kubectl config get-contexts` — List all contexts
- `kubectl get pods -A` — All pods cluster-wide
- `kubectl get pods -n <ns>` — Pods in namespace
- `kubectl get svc,deploy,cm,secret -n <ns>` — Resource inventory
- `kubectl describe pod <name> -n <ns>` — Pod details and events
- `kubectl describe node <name>` — Node details
- `kubectl logs <pod> -n <ns> --tail 200` — Recent logs
- `kubectl logs <pod> -n <ns> --previous` — Previous container logs (crash debugging)
- `kubectl top pods -A` — Resource usage
- `kubectl get events -n <ns> --sort-by='.lastTimestamp'` — Recent events

### Apply (Medium / High Risk)

- `kubectl apply -f manifest.yaml` — Apply manifest
- `kubectl apply -k ./overlay/` — Kustomize apply
- `kubectl apply -f -` — Apply from stdin
- `kubectl set image deployment/<name> <container>=<image>:<tag>` — Update image
- `kubectl scale deployment/<name> --replicas=<n>` — Scale
- `kubectl rollout status deployment/<name> -n <ns>` — Wait for rollout
- `kubectl rollout history deployment/<name> -n <ns>` — Rollout history

### Destructive (High / Critical Risk)

- `kubectl delete pod <name> -n <ns>` — Delete pod (High — usually safe, controller recreates)
- `kubectl delete deployment <name> -n <ns>` — Delete deployment (Critical)
- `kubectl delete service <name> -n <ns>` — Delete service (Critical)
- `kubectl delete namespace <name>` — Delete namespace and ALL resources (Critical)
- `kubectl rollout undo deployment/<name> -n <ns>` — Rollback (High)
- `kubectl drain <node>` — Drain node for maintenance (Critical)
- `kubectl cordon <node>` — Mark unschedulable (High)

## Terraform Commands Reference

### Read-Only (Low Risk)

- `terraform version` — Version info
- `terraform -chdir=infra validate` — Syntax check
- `terraform -chdir=infra show` — Show current state
- `terraform -chdir=infra output` — Read outputs
- `terraform -chdir=infra state list` — List state resources
- `terraform -chdir=infra workspace show` — Show current workspace
- `terraform -chdir=infra graph` — Resource graph

### Plan

- `terraform -chdir=infra init -upgrade` — Initialize with provider upgrade (Medium)
- `terraform -chdir=infra plan` — Show plan (Low Risk — read-only, no state changes)
- `terraform -chdir=infra plan -out=tfplan` — Save plan to file (Medium Risk — generates a state artifact that could be applied)
- `terraform -chdir=infra show tfplan` — Inspect saved plan (Low)

### Apply (High / Critical Risk)

- `terraform -chdir=infra apply tfplan` — Apply saved plan
- `terraform -chdir=infra apply -auto-approve` — Apply without prompt (NEVER without explicit plan)
- `terraform -chdir=infra plan -destroy -out=tfdestroyplan` — Plan destruction (Critical — review every line)
- `terraform -chdir=infra apply -target=<resource>` — Targeted apply (Critical — bypasses dependency ordering, can leave state inconsistent. Never use to skip plan review)

### Destructive (Critical Risk)

- `terraform -chdir=infra destroy` — Destroy all resources (CRITICAL — data loss)
- `terraform -chdir=infra state rm <resource>` — Remove from state without destroying (CRITICAL — orphan the resource)
- `terraform -chdir=infra import <resource> <id>` — Import existing resource (Medium)

## CI/CD Commands Reference

### GitHub Actions (Low Risk)

- `gh workflow list` — List workflows
- `gh run list --limit 10` — Recent runs
- `gh run view <run-id>` — Run details
- `gh run view <run-id> --log` — Step-by-step logs
- `gh workflow view <name>` — Workflow definition

### GitHub Actions (Medium Risk)

- `gh workflow run <name>` — Trigger workflow
- `gh workflow run <name> -f key=value` — Trigger with inputs
- `gh run watch <run-id>` — Watch run progress

## Shell Scripting Patterns

### Script Header Template

```bash
#!/usr/bin/env bash
set -euo pipefail

# Script: deploy.sh
# Purpose: Deploy application to staging
# Usage: ./deploy.sh <environment> [version]
# Required env: AWS_PROFILE, KUBECONFIG
# Optional env: DRY_RUN

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ENV="${1:-staging}"
readonly VERSION="${2:-latest}"

usage() {
  cat <<EOF
Usage: $0 <environment> [version]
  environment: dev | staging | prod
  version: image tag (default: latest)
EOF
  exit 1
}

if [[ "$ENV" != "dev" && "$ENV" != "staging" && "$ENV" != "prod" ]]; then
  usage
fi

# ... rest of script
```

### Safe File Operations (Low / Medium Risk)

- `mkdir -p <dir>` — Create directory (Low)
- `cp <src> <dst>` — Copy (Low)
- `mv <src> <dst>` — Move/rename (Medium)
- `ln -s <target> <link>` — Symlink (Medium)
- `chmod +x <file>` — Make executable (Low)
- `chown <user>:<group> <file>` — Change owner (High)

### Destructive (Critical Risk)

- `rm <file>` — Remove file (High)
- `rm -rf <dir>` — Recursive force remove (Critical)
- `rm -rf /*` — NEVER (Critical — system destruction)
- `find . -delete` — Find and delete (Critical — verify scope first)
- `truncate -s 0 <file>` — Empty file (High)

## Cloud Provider Patterns

### AWS (Low / Medium Risk)

- `aws s3 ls` — List buckets (Low)
- `aws s3 cp <local> s3://<bucket>/<path>` — Upload (Medium)
- `aws s3 sync <local> s3://<bucket>/` — Sync (Medium)
- `aws ec2 describe-instances` — Inventory (Low)
- `aws ecs update-service --cluster <c> --service <s> --force-new-deployment` — Force redeploy (High)
- `aws rds describe-db-instances` — Inventory (Low)
- `aws rds delete-db-instance --skip-final-snapshot` — CRITICAL (data loss)

### GCP (Low / Medium Risk)

- `gcloud compute instances list` — List instances (Low)
- `gcloud container clusters get-credentials <cluster>` — Configure kubectl (Low)
- `gcloud app deploy app.yaml` — Deploy app (High)
- `gcloud sql instances describe <name>` — Inspect (Low)
- `gcloud projects delete <project>` — CRITICAL (irreversible)

## Risk-Aware Execution Discipline

Before running any command, mentally classify it:

- **Low** — read-only, no side effects → just run it
- **Medium** — creates or modifies resources in non-prod → show the plan, get nod
- **High** — destroys or modifies in prod-ish → confirm target, verify rollback
- **Critical** — irreversible, prod-wide, or data loss → explicit approval

When in doubt, classify UP. A command that *might* be Medium could be High in production. Always check environment first, classify second, execute third.

## Secrets in Commands — NEVER DO THIS

```bash
# WRONG — secret in CLI arg, visible in process list and shell history
mysql -u root -pMyS3cretPassword db
aws s3 ls --secret-key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

# RIGHT — secret in env var, not in argv
MYSQL_PWD="$DB_PASSWORD" mysql -u root db
AWS_SECRET_ACCESS_KEY="$AWS_SECRET_KEY" aws s3 ls

# RIGHT — secret from file
mysql --defaults-file=~/.my.cnf
aws configure import --csv file://credentials.csv
```

And never `echo $SECRET` — that prints to logs, history, and potentially other processes' stdout.

---

## Infrastructure Tools (Asset Inventory)

The `infra` tools track the infrastructure I'm operating on — datacenters, servers, K8s clusters, networks, databases, load balancers, etc. The bash / Docker / kubectl / Terraform sections above actually *touch* infrastructure; these tools record what's there so context survives across sessions, handoffs, and audit reviews.

### When to use

- **First encounter with an environment** — `infra_asset_search` to discover what's already registered before assuming something is unmanaged
- **After provisioning via bash / Terraform** — `infra_asset_create` to record what was just built
- **Before destructive operations** — `infra_asset_get` to confirm the exact ID, name, parent, and current attributes
- **After config changes** — `infra_asset_update` to keep the inventory in sync (auto-versions into history)
- **Audit / rollback / drift review** — `infra_history_get` to see who changed what, when
- **When you need a new asset type** — `infra_type_register` (GLOBAL, shared across all projects)

### Quick reference

Asset CRUD (project-scoped — always pass `project_id`):

- `infra_asset_create` — register a new asset (`project_id`, `type`, `name`, `attributes` JSON, optional `parent_asset_id`, optional `relationships`)
- `infra_asset_search` — find assets by name substring (case-insensitive) and optional `type` filter
- `infra_asset_list` — list assets; filter by `type` and/or `parent_asset_id` (passing `parent_asset_id=None` returns only root-level assets)
- `infra_asset_get` — full JSON of one asset by ID
- `infra_asset_update` — replace `attributes` / `name` / `parent_asset_id` / `relationships` (auto-records history)
- `infra_asset_delete` — soft delete; the `deleted` history row is preserved for audit

Type registry (GLOBAL — no `project_id`):

- `infra_type_register` — register or upsert a type definition (`name`, `schema_def`, `description`)
- `infra_type_list` — list all registered types across all projects

History:

- `infra_history_get` — change log for one asset (newest first), works for deleted assets too

### Guidelines

- **Always pass `project_id`** — assets are project-scoped; isolation is enforced at the repository layer. Cross-project reads/writes return `not found` rather than leaking
- **SEARCH before CREATE** — `(project_id, type, name)` is unique. A duplicate returns `ERROR: ... already exists` — search the existing set first
- **Types are GLOBAL** — registered once, shared across all projects. Don't re-register a type per project; one definition, many consumers
- **Use `attributes` for rich metadata** — IPs, specs, region, instance type, tags, etc. It's a JSONB column, store structured data
- **Use `parent_asset_id` for hierarchy** — e.g. server → rack → datacenter → region. `infra_asset_list(parent_asset_id=None)` lists only roots
- **Use `relationships` for cross-entity links** — dict of `{entity_type: [id, ...]}`, e.g. `{"load_balancer": ["lb-uuid-1"]}`
- **Delete is soft** — the row is removed, but a `deleted` history row with the pre-delete snapshot is retained
- **Updates auto-version** — every `infra_asset_update` writes a history snapshot with old / new values and the calling instance as `changed_by`
