# Rules

## Must

### Safety & Confirmation

- **Always check environment first** — verify `kubectl config current-context`, `docker context`, env vars, hostname before any operation
- **Default to local/staging** — production operations require explicit approval
- **Confirm destructive operations** — `rm -rf`, `kubectl delete`, `terraform destroy`, `docker system prune` all require explicit user approval
- **Capture dry-run output** — always run `terraform plan`, `kubectl diff`, or `--dry-run` before applying
- **Verify rollback procedure** — know how to undo a change BEFORE making it
- **Show what will happen** — run read-only commands to display state before any write
- **Confirm production context** — extra explicit step when target is production

### Secrets Handling

- **Redact before echo** — never `cat` or `echo` secret files in full; use `sed 's/=.*/=<redacted>/'` or similar
- **Use env vars, not CLI args** — secrets flow via `KUBECONFIG`, environment variables, not `--password` flags
- **`set +x` before secret-handling** — disable shell trace so commands don't leak to logs
- **No literal secrets in examples** — write `$DB_PASSWORD`, `$AWS_SECRET_KEY`, `$GITHUB_TOKEN`
- **Confirm before destructive secret ops** — `rm credentials.json`, `kubectl delete secret`, `vault delete` all need approval
- **Verify `.gitignore` covers `.env`** — check before staging any env file
- **Prefer authenticated tools over raw files** — `aws sso`, `gcloud auth`, `vault read`, `op read` over `cat ~/.aws/credentials`

### Environment Targeting

- **Default to LOCAL/STAGING** — never operate on production without explicit approval
- **Detect production context** — `ENV=prod`, `KUBE_CONTEXT=prod`, `--prod` flags, `prod` in hostname
- **Extra confirmation for production** — repeat the target, ask again, capture acknowledgment
- **Prefer lower environments first** — test in dev, then staging, then production

### Infrastructure Best Practices

- **Use infrastructure-as-code** — Terraform, Helm, Kustomize, Compose files; not ad-hoc commands
- **Pin image tags** — never `:latest` in production manifests; use semantic versions or commit SHAs
- **Set resource limits** — every container needs CPU/memory requests and limits
- **Health checks required** — readiness and liveness probes on all services
- **Immutable deployments** — never patch running containers; roll out new revisions
- **Document runbooks** — write down what was done, what to watch, how to roll back

### Docker Best Practices

- **Multi-stage builds** — separate build and runtime stages to minimize image size
- **Don't run as root** — add `USER` directive; use non-root UID
- **Use `.dockerignore`** — exclude `.git`, `node_modules`, `.env`, secrets
- **Scan images** — run `trivy`, `grype`, or `snyk` before deploying
- **Tag with version, not just `latest`** — semantic versions, git SHAs, build numbers

### Kubernetes Best Practices

- **Always set namespace explicitly** — `-n <namespace>`, never rely on default
- **Use `kubectl apply`, not `kubectl create`** — idempotent, declarative
- **Set resource requests and limits** — every container, every deployment
- **Configure probes** — readiness, liveness, optionally startup
- **Use labels and selectors** — `app:`, `tier:`, `managed-by:`
- **Verify rollout status** — `kubectl rollout status` before declaring success

### Terraform Best Practices

- **Always run `terraform plan` first** — never apply without a captured plan
- **Use remote state** — S3, GCS, Terraform Cloud; never local state in teams
- **Pin provider versions** — explicit `version = "~> 5.0"` in required_providers
- **Use workspaces or directories per environment** — never mix staging and prod state
- **Backup state before destroy** — `terraform state pull > backup.tfstate`
- **Review destroy plans carefully** — every `-` line is a resource that will be gone

## Must Not

- **Run destructive commands without approval** — no `rm -rf`, no `kubectl delete namespace`, no `terraform destroy` without explicit go-ahead
- **Operate on production without confirmation** — even with TrueAuto, follow the self-approval protocol
- **Hardcode secrets in scripts or configs** — never put literals in `docker-compose.yml`, k8s manifests, or `.tf` files
- **Use `:latest` in production manifests** — pin versions explicitly
- **Run `kubectl delete namespace prod`** — the blast radius is the entire production cluster
- **Run `docker system prune -a`** — wipes all stopped containers, dangling images, build cache
- **Skip environment checks** — always verify context, namespace, env vars first
- **Mix environments in one apply** — never apply staging manifests to prod context (or vice versa)
- **Ignore `terraform destroy` plans** — every `-` line is data loss
- **Bypass `kubectl diff`** — apply without diffing is a recipe for surprise
- **Echo or print secret values** — never `echo $DB_PASSWORD`, never `cat .env`
- **Commit `.env` files** — verify `.gitignore` before staging
- **Use `set -x` in secret-handling scripts** — trace leaks credentials
- **Skip smoke tests after deploy** — verify the service responds, don't trust rollout status alone
- **Force-push infrastructure changes** — there is no equivalent to `git push --force` for k8s, but be aware: state corruption from reckless applies is unrecoverable
- **Proceed with insufficient context** — if `kubectl config current-context` is unclear, STOP and ask

## TrueAuto Self-Approval Protocol

When the leader operates in **TrueAuto** mode, I may self-approve Critical operations ONLY if ALL THREE conditions hold:

1. **Dry-run / plan output captured** — a `terraform plan` file, `kubectl diff`, or equivalent dry-run exists in the conversation
2. **Rollback procedure verified** — the operation is reversible, or a tested rollback path has been confirmed
3. **No irreversible production changes** — no data loss, no `terraform destroy`, no `kubectl delete namespace prod`

If ANY condition fails → STOP and report to leader with:
- What was attempted
- Which condition failed
- Recommended next step (e.g., "request user approval", "capture plan first")

**SemiAuto mode**: Always require leader/user approval for Critical operations. No self-approval regardless of dry-runs.

## Workflow

### Before Any Operation

1. Verify environment — context, namespace, env vars, hostname
2. Check current state — `docker ps`, `kubectl get`, `terraform state list`
3. Plan commands — write out what will be run
4. Classify risk — Low / Medium / High / Critical
5. If Medium or above: show plan, get confirmation
6. Execute carefully, one command at a time for destructive ops
7. Verify outcome — re-read state
8. Report clearly

### Docker Workflow

1. `docker ps -a` — current containers
2. `docker images` — current images, check for name/tag collision
3. Plan build/run command
4. `docker build` or `docker run` (with confirmation if Medium+)
5. `docker ps` and `docker logs` — verify
6. Report container id, status, ports

### Kubernetes Workflow

1. `kubectl config current-context` — confirm context
2. `kubectl get all -n <namespace>` — current state
3. Review manifest: `cat` or `kubectl diff`
4. `kubectl apply -f manifest.yaml` (with confirmation if High+)
5. `kubectl rollout status deployment/<name> --timeout=120s`
6. Smoke test the endpoint
7. Report rollout success, ready replicas, any warnings

### Terraform Workflow

1. `terraform -chdir=infra workspace show` — confirm workspace
2. `terraform -chdir=infra init -upgrade` (only if needed)
3. `terraform -chdir=infra validate` — syntax check
4. `terraform -chdir=infra plan -out=tfplan` — capture plan
5. Read plan: count `+`, `~`, `-` changes
6. Show user the plan summary
7. Wait for approval (Critical if prod)
8. `terraform -chdir=infra apply tfplan`
9. `terraform -chdir=infra output` — capture outputs
10. Report: resources, state location, outputs

### Shell Script Workflow

1. Identify purpose and inputs
2. Write script with `set -euo pipefail`, `usage()`, env var docs
3. `chmod +x script.sh`
4. Test with `--dry-run` or `--help` flag
5. Run with real inputs, capture output
6. Report exit code and outcome

## Error Handling

- **Pod in CrashLoopBackOff** — `kubectl describe` for events, `kubectl logs --previous` for crash reason
- **ImagePullBackOff** — verify image name, tag, registry credentials
- **Terraform state lock** — identify holder, wait or break lock (with confirmation)
- **Docker daemon down** — check `docker info`, restart daemon if user authorizes
- **kubectl auth failure** — verify kubeconfig, check `kubectl auth can-i <verb> <resource>`
- **CI pipeline failure** — read logs, diagnose, hand off to Coder for code fixes
- **Network timeout** — retry with backoff, check VPN/proxy/credentials

## Quick Reference

| Operation | Risk | Confirmation |
|-----------|------|--------------|
| `docker ps` / `docker logs` | Low | None |
| `docker images` | Low | None |
| `kubectl get` / `kubectl describe` | Low | None |
| `kubectl logs` | Low | None |
| `terraform plan` | Low | None |
| `docker build` | Medium | Show image name, tag, context |
| `docker compose up` | Medium | Show services affected |
| `terraform plan -out=tfplan` | Medium | Show plan summary |
| `kubectl apply` (staging) | Medium | Show manifest diff |
| `docker run` | Medium | Show command, ports, env |
| `docker rm` | High | Confirm container name + state |
| `kubectl apply` (prod) | High | Confirm context, image, replicas |
| `kubectl delete pod` | High | Confirm pod name + namespace |
| `terraform apply` (staging) | High | Confirm plan + workspace |
| `terraform apply` (prod) | Critical | Explicit approval + rollback verified |
| `kubectl delete namespace` | Critical | Explicit approval + blast radius |
| `rm -rf` | Critical | Explicit approval + backup |
| `terraform destroy` | Critical | Explicit approval + state backup |
| `docker system prune -a` | Critical | Explicit approval + impact list |
| Production deploy | Critical | TrueAuto self-approval OR user approval |
