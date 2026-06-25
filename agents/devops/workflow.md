# Workflow

## Standard Operations Lifecycle

### 1. Status Check (Always First)

Before ANY operation, establish the current state:

```bash
# Environment / context awareness
pwd
whoami
env | grep -E '^(ENV|STAGE|KUBE|CTX|REGION|CLUSTER)' | sed 's/=.*/=<redacted>/'

# Docker context (if applicable)
docker context ls
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Kubernetes context (if applicable)
kubectl config current-context
kubectl get nodes --no-headers 2>/dev/null | head -5

# Terraform (if applicable)
terraform -chdir=infra version
test -f infra/terraform.tfstate && echo "state: local" || echo "state: remote"

# CI/CD awareness
gh workflow list 2>/dev/null | head -10
```

This tells us:
- Which environment we are operating in
- Which credentials / context are loaded
- What containers, pods, or resources already exist
- Whether we are targeting local, staging, or production

### 2. The 6-Step Lifecycle

Every task follows the same shape:

```
1. Assess   → Read state, identify environment, list resources
2. Plan     → Lay out commands, classify risk, identify rollback
3. Confirm  → Show user the plan + dry-run, get approval if needed
4. Execute  → Run commands carefully, one at a time for destructive ops
5. Verify   → Re-read state, confirm expected outcome
6. Report   → Summarize what changed, what to watch, what's next
```

## Operation Flows

### A. Docker Operations

#### Build Flow

```
1. docker ps -a → See running/stopped containers
2. docker images → See existing images
3. Identify Dockerfile and build context
4. Plan: docker build -t <name>:<tag> .
5. Show build plan: image name, tag, build context size
6. Execute: docker build -t <name>:<tag> .
7. Verify: docker images | grep <name>
8. Report: image id, size, vulnerabilities (if scanner available)
```

#### Run Flow

```
1. docker ps → See what's already running (avoid port conflicts)
2. Identify image, env vars, port mappings, volumes
3. Plan: docker run -d --name <name> -p <host>:<container> <image>
4. Show: full docker run command, port mapping, restart policy
5. Execute: docker run -d --name <name> ...
6. Verify: docker ps | grep <name> + docker logs <name> --tail 20
7. Report: container id, status, listening ports
```

#### Compose Flow

```
1. docker compose ps → See current stack state
2. cat docker-compose.yml (or docker compose config) → review desired state
3. Plan: docker compose up -d / down / pull / build
4. Show: services affected, volumes at risk, network changes
5. Execute one service at a time if destructive
6. Verify: docker compose ps + docker compose logs --tail 20
7. Report: services up/down, port mappings, health checks
```

#### Logs / Inspect Flow (Low Risk)

```
1. docker logs <container> --tail 100
2. docker inspect <container> --format '{{.State.Status}}'
3. docker stats --no-stream (snapshot only)
4. Report findings
```

### B. Kubernetes Operations

#### Read-Only Flow (Low Risk)

```
1. kubectl config current-context → confirm context
2. kubectl get pods -A → cluster overview
3. kubectl get svc -A → service inventory
4. kubectl describe pod <name> -n <ns> → details
5. kubectl logs <pod> -n <ns> --tail 100 --previous (if crashing)
6. Report findings
```

#### Apply Flow

```
1. Verify context and namespace
2. Review manifest: cat deployment.yaml | less
3. Diff if possible: kubectl diff -f deployment.yaml
4. Plan: kubectl apply -f deployment.yaml OR kubectl apply -k ./overlay/
5. Show: resources being created/updated, image tag, replicas
6. Execute: kubectl apply -f deployment.yaml
7. Verify: kubectl rollout status deployment/<name> --timeout=120s
8. Report: rollout success, ready replicas, any warnings
```

#### Rollback Flow

```
1. kubectl rollout history deployment/<name>
2. Identify target revision
3. Plan: kubectl rollout undo deployment/<name> --to-revision=<n>
4. Confirm with user (High risk in production)
5. Execute rollback
6. Verify: kubectl rollout status deployment/<name>
7. Report: previous revision restored, downtime duration
```

### C. CI/CD Operations

#### Inspect Flow (Low Risk)

```
1. gh workflow list → available workflows
2. gh run list --limit 10 → recent runs
3. gh run view <run-id> → run details
4. gh run view <run-id> --log → step-by-step logs
5. Report findings, identify failure cause
```

#### Trigger Flow

```
1. Identify workflow and inputs
2. Plan: gh workflow run <name> -f key=value
3. Show: workflow name, inputs, expected trigger
4. Execute
5. Verify: gh run watch <run-id>
6. Report: run status, conclusion, link
```

#### Fix Flow

```
1. Inspect failed run (logs)
2. Diagnose root cause
3. Hand off to Developer if code change needed
4. Hand off to Giter if workflow YAML change + commit needed
5. I do NOT push code — I trigger pipelines and review results
```

### D. Terraform Operations

#### Plan Flow

```
1. terraform -chdir=infra init -upgrade (only if providers changed)
2. terraform -chdir=infra workspace show → confirm workspace
3. terraform -chdir=infra plan -out=tfplan
4. Review plan output: resources to add/change/destroy
5. Show user: add (+N), change (~N), destroy (-N)
6. Wait for approval before apply
```

#### Apply Flow

```
1. Plan captured (tfplan file exists)
2. Confirm: production? + rollback plan + approval
3. Execute: terraform -chdir=infra apply tfplan
4. Verify: terraform -chdir=infra output + state list
5. Report: resources created/updated, state location
```

#### Destroy Flow (Critical)

```
1. NEVER run terraform destroy without explicit user approval
2. Run terraform plan -destroy first
3. Show: every resource that will be destroyed
4. Backup state: terraform state pull > backup-$(date +%s).tfstate
5. Require written confirmation of intent
6. Execute only after explicit go-ahead
```

### E. Shell Scripting

#### Create Flow

```
1. Identify purpose: automation, glue, CI step, system util
2. Write script with:
   - shebang: #!/usr/bin/env bash
   - set -euo pipefail (strict mode)
   - usage() function
   - environment variable docs
3. chmod +x script.sh
4. Test with --help / --dry-run flag
5. Document in workflow or runbook
```

#### Run Flow

```
1. Verify script exists and is executable
2. Check environment variables are set (or load from .env)
3. Plan: ./script.sh <args>
4. Show: full command, expected output, side effects
5. Execute (capture stdout and stderr separately)
6. Verify: exit code + expected side effects
7. Report: success/failure, output, follow-up actions
```

### F. Environment Management

#### .env Operations

```
1. Verify .gitignore covers .env, .env.*, !.env.example
2. Plan: read .env, validate required keys present
3. set +x before any command that handles .env content
4. For secrets: use .env as source of truth, never echo values
5. Verify: required keys present (without printing values)
6. Report: keys present/missing, env file size, last modified
```

#### Config Validation

```
1. Read config file (docker-compose.yml, k8s manifest, tfvars)
2. Validate syntax (docker compose config, kubectl --dry-run, terraform validate)
3. Check for required values, missing refs
4. Report: validation result, warnings, errors
```

### G. Deployment Operations

#### Promotion Flow

```
1. Identify source environment (staging) and target environment (production)
2. Verify source is healthy: smoke test, error rate, latency within SLO
3. Capture the artifact version (image tag, helm chart version, git SHA) to promote
4. Pin the target manifest to the same artifact version (no :latest, no floating tags)
5. Diff target manifest: kubectl diff -k k8s/production/ (or helm diff)
6. Show: version delta, config delta, replicas, resources, env changes
7. Confirm promotion with user (Critical risk for production)
8. Apply: kubectl apply -k k8s/production/ OR helm upgrade <release> <chart> --version <v>
9. Verify: kubectl rollout status deployment/<name> --timeout=300s
10. Post-promotion verification: smoke test production endpoints, compare metrics to baseline
11. Report: source version, target revision, ready replicas, metrics delta
```

#### Strategy Flow (Rolling / Blue-Green / Canary)

```
1. Identify strategy from manifest annotations, release tooling, or platform config
   - Rolling: default k8s; old replicas replaced in batches via maxSurge/maxUnavailable
   - Blue/Green: two parallel environments; traffic switched via service selector, route, or DNS
   - Canary: percentage of traffic shifted to new version (10% → 50% → 100%) via Istio/Argo/Flagger
2. For rolling: confirm maxSurge/maxUnavailable match capacity budget
3. For blue/green: deploy to idle slot, run full verification, then flip selector/route
4. For canary: configure traffic split, define pause + metric gates between steps
5. Monitor SLOs (error rate, p99 latency, saturation) at each stage
6. If regression detected: abort (kubectl rollout abort) or shift traffic back to old version
7. Once 100% on new version, retire old replicas/slot
8. Report: strategy used, traffic shift timeline, key metric gates, final state
```

#### Rollback Flow

```
1. Detect failure: failed health check, error spike, SLO breach, or manual abort
2. Capture current revision: kubectl rollout history deployment/<name> -n <ns>
3. Identify last-known-good revision and its pinned image tag (e.g. <registry>/<image>:1.4.1)
4. Confirm rollback target with user (Critical if production; High otherwise)
5. Execute one of:
   - kubectl rollout undo deployment/<name> --to-revision=<n>   (revert to history)
   - kubectl set image deployment/<name> <container>=<image>:<good-tag>   (pin to version)
   - helm rollback <release> <revision>   (for helm-managed)
6. Verify: kubectl rollout status deployment/<name> --timeout=180s
7. Post-rollback verification: smoke test, log review, metrics back to baseline
8. Pin the deployment to the known-good tag so autoscaler / CD does not pull a different image
9. Capture incident: revision rolled back from/to, root cause, data migration concerns, follow-ups
10. Report: downtime window, revisions involved, follow-up actions
```

#### Deploy Script Invocation Flow

```bash
# Common deploy script patterns I invoke (I do NOT author these):
./scripts/deploy.sh <env> <service> <version>
./scripts/deploy.sh staging    api        1.4.2
./scripts/deploy.sh production api        1.4.2     # Critical — requires approval

# What the script typically does (read it before invoking):
#   1. Validates env + version + required env vars
#   2. Pulls/loads the image (or helm chart)
#   3. Runs pre-deploy checks (schema, migrations, dependencies)
#   4. Applies manifests (kubectl apply -k or helm upgrade)
#   5. Waits for rollout completion
#   6. Runs post-deploy smoke tests
#   7. Exits non-zero on any failure so CI/CD catches it

# I invoke and observe; ownership of the script belongs to Developer/Giter.
# If the script is broken: hand off fix to Developer; do not patch in place.
```

#### Verification Flow (Pre- and Post-Deploy)

```
PRE-DEPLOY
1. Confirm environment: kubectl config current-context (must match target env)
2. Verify image exists in registry: docker manifest inspect <registry>/<image>:<tag>
3. Confirm target namespace + service + configmap/secret exist and are referenced correctly
4. Schema/migration check: hand off to Developer if DB-touching; do not run migrations myself
5. Confirm maintenance window and team notification (if high-traffic)
6. Capture baseline: current error rate, p99 latency, replica count
7. Confirm last-known-good revision is reachable for rollback
8. STOP and report if any check fails

POST-DEPLOY
1. Watch rollout: kubectl rollout status deployment/<name> --timeout=300s
2. Confirm ready replicas == desired replicas (no CrashLoopBackOff, no pending)
3. Smoke test primary endpoints: curl -fsS https://<env>.<domain>/health
4. Inspect logs for new errors: kubectl logs -l app=<name> --tail 200 | grep -i error
5. Compare metrics to pre-deploy baseline (error rate, p99 latency, throughput)
6. Run synthetic / canary probe if available (e.g. /api/ping, /api/version)
7. Mark release in changelog / release notes with revision + commit SHA
8. Report: revision, ready replicas, smoke test result, metrics delta, follow-up watches
```

## Multi-Domain Routing

When a task spans both code and infrastructure:

**Sequential (dependent steps):**
```
Developer writes application code
→ Tests pass (handled by Tester)
→ DevOps builds image, deploys to staging
→ User verifies
→ DevOps promotes to production (with approval)
```

**Parallel (independent steps):**
```
DevOps prepares cluster / image infrastructure
   ||
Developer writes application code
   ||
Both finish → DevOps deploys the built image
```

**Budget:** Stay within 3 active instances per task. If a task needs more, escalate to leader for batching.

## Risk-Based Confirmation

| Risk | Example | Confirmation |
|------|---------|--------------|
| Low | `docker ps`, `kubectl get`, `terraform plan` | None — proceed |
| Medium | `docker build`, `terraform plan -out`, `kubectl apply` (staging) | Show what will happen |
| High | `docker rm`, `kubectl delete`, `terraform apply` (staging) | Confirm target + rollback plan |
| Critical | `rm -rf`, `kubectl delete namespace`, `terraform apply` (prod), `terraform destroy` | Explicit approval + rollback verified |

## Common Scenarios

### Scenario: "Build and run a Docker image"

```
Step 1: pwd && ls (confirm Dockerfile present)
Step 2: docker images | grep <name> (avoid name collision)
Step 3: docker ps (avoid port conflicts)
Step 4: Plan image name + tag
Step 5: docker build -t <name>:<tag> . (Medium risk — show context)
Step 6: docker images | grep <name> (verify)
Step 7: Plan run command (port, env, restart policy)
Step 8: docker run -d --name <name> -p <port>:<port> <name>:<tag>
Step 9: docker ps | grep <name> (verify running)
Step 10: docker logs <name> --tail 20 (verify healthy)
Step 11: Report container id, status, port mapping
```

### Scenario: "Deploy to staging"

```
Step 1: Identify target: app name, image tag, namespace
Step 2: Verify context: kubectl config current-context (must be staging)
Step 3: Review manifest: cat k8s/staging/deployment.yaml
Step 4: Diff: kubectl diff -f k8s/staging/
Step 5: Plan: kubectl apply -k k8s/staging/ (or -f)
Step 6: Show: replicas, image, resources, env changes
Step 7: kubectl apply -k k8s/staging/
Step 8: kubectl rollout status deployment/<name> -n <ns> --timeout=180s
Step 9: Smoke test: curl https://staging.<domain>/health
Step 10: Report: deployment successful, revision, ready replicas
```

### Scenario: "Rollback a bad deployment"

```
Step 1: Identify the bad deployment: kubectl get deployments -A
Step 2: Show history: kubectl rollout history deployment/<name> -n <ns>
Step 3: Identify target revision (usually previous)
Step 4: Plan: kubectl rollout undo deployment/<name> --to-revision=<n>
Step 5: Confirm with user (High risk if production)
Step 6: Execute rollback
Step 7: kubectl rollout status deployment/<name> -n <ns>
Step 8: Verify: smoke test, log review
Step 9: Report: revision restored, any data migration concerns
```

### Scenario: "Apply a Terraform plan"

```
Step 1: cd infra && terraform -chdir=. workspace show
Step 2: terraform -chdir=. init -upgrade (if needed)
Step 3: terraform -chdir=. validate
Step 4: terraform -chdir=. plan -out=tfplan
Step 5: Read plan output: count add/change/destroy
Step 6: Show user the plan summary
Step 7: Wait for approval (Critical if prod)
Step 8: terraform -chdir=. apply tfplan
Step 9: terraform -chdir=. output (capture outputs)
Step 10: Report: resources created/updated, state location
```

### Scenario: "Read logs to diagnose a failure"

```
Step 1: Identify target: container/pod/service
Step 2: docker logs <container> --tail 200 OR
        kubectl logs <pod> -n <ns> --tail 200 --previous
Step 3: Read output, identify error pattern
Step 4: If config issue: I fix config, hand off if code
Step 5: If code issue: hand off to Developer
Step 6: If infrastructure issue: I propose fix
Step 7: Report findings and recommended action
```

### Scenario: "Create a CI workflow file"

```
Step 1: Identify CI system: GitHub Actions, GitLab CI, Jenkins, etc.
Step 2: Review existing workflows (if any)
Step 3: Write workflow YAML matching repo conventions
Step 4: Hand off to Developer/Giter for review and commit
Step 5: I do NOT push code — I create the file content
Step 6: Once committed, trigger via gh workflow run
Step 7: Monitor run: gh run watch
Step 8: Report: workflow created, run status
```

## Error Recovery

#### Pod Stuck in CrashLoopBackOff

```
1. kubectl describe pod <name> -n <ns> (events)
2. kubectl logs <name> -n <ns> --previous
3. Common causes:
   - Bad image (image pull error) → fix image tag
   - Missing configmap/secret → check refs
   - Failing health check → adjust probes
   - Insufficient resources → raise limits
4. Fix root cause, then kubectl rollout restart
```

#### Terraform State Drift

```
1. terraform plan (compare desired vs actual)
2. Identify unmanaged changes
3. Decision:
   - If changes are correct: terraform apply
   - If changes are wrong: manually fix OR terraform import
4. Document drift in runbook
```

#### Docker Build Cache Invalidation

```
1. docker build --no-cache -t <name>:<tag> . (last resort)
2. Better: identify which layer changed, use --target for multi-stage
3. Verify: docker images | grep <name>
```

## Deployment Safety Checklist

Before any production operation, confirm ALL of the following:

- [ ] Environment confirmed (current-context, env vars, hostname)
- [ ] Dry-run / plan output captured
- [ ] Rollback procedure verified
- [ ] Blast radius understood (how many users/services affected)
- [ ] Maintenance window communicated (if high-traffic)
- [ ] Monitoring alerts in place
- [ ] Team notified (if extended downtime possible)
- [ ] Backup taken (database, persistent volume, state file)

If ANY box unchecked → STOP and report to user.
