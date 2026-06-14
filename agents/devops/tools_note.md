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

## Always Available

These tools are always available:

- **bash** — Execute infrastructure commands
- **time** — Get current time/date
- **read_file** — Read files for context
- **list_directory** — List directory contents
- **glob_files** — Find files by pattern
- **inner_soul** — Remember and evolve

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
