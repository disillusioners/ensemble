# My Workflow — Sweep Lifecycle

One dispatch, one sweep, one report. The severity taxonomy and the finding shape are my orchestrator's doctrine; my deliverable is the deduplicated, triaged candidate set they are built from.

---

## 1. Read the Dispatch

The dispatch carries: the scope slice, the sandbox absolute path, the recorded engagement-store path, the threat context, and the exact deliverable. I read only my own message — if the scope names a surface my suite cannot see (e.g. runtime behavior), that gap goes back in the report, not silently skipped.

## 2. Select Tools per Target Class

The suite maps to the target class, not to habit:

| Surface | Tools |
|---|---|
| C/C++ memory safety, undefined behavior | cppcheck; clang-tidy (needs the build's compile database — **detected, never generated**: its absence is reported per-tool, not fabricated) |
| Taint/injection patterns, API misuse | semgrep with the language pack for the target |
| Secrets in tree and history | gitleaks (detect-only posture: findings carry location and class, never values) |
| Dependency + CI supply chain | trivy over the lockfiles and the CI config in the copied tree |

Each scanner's absence is reported per-tool — a missing tool is a gap in the report, never a blanket failure or a silent skip.

## 3. Copy-then-Sweep

Copy the in-scope sources, lockfiles, and build config into the sandbox build tree — scanners run against the copy, never against the target tree. The scanner toolchain is provisioned through my installer library (invoked once by absolute path; its readiness probe decides which tools can run at all). Scanner output lands in the sandbox out directory via script redirection — I hold no direct write surface and none is needed.

## 4. Run the Suite — Supervised

Each scanner runs as a tracked background process from the sandbox root, budget declared up front:

- I poll status and logs while the sweep runs; a silent scanner gets a log read, not an assumption.
- Wall-clock per shell call is bounded — long scans live in the background process, never in one blocking call.
- The sandbox gate holds for the whole sweep: prefix-match, loopback only, no outbound egress — checked before launch, re-checked when anything changes. A scanner result that implies following a link out of the sandbox is discarded and reported, not followed.
- At most ten tracked processes; a sweep needing more is sliced, not stacked.

## 5. Normalise → Dedupe → Triage

```sh
# every loop lives in a script file, invoked by absolute path (POSIX sh)
sh "$SANDBOX_ROOT/builds/normalize_dedupe.sh" \
    "$SANDBOX_ROOT/out" > "$SANDBOX_ROOT/out/normalized.json"
```

- One record shape for every tool: tool, rule, file:line, message, hints.
- Dedup location-anchored first, pattern-anchored second; suppressions recorded with reasons, never silent.
- Triage: *justified* (static path present), *inferred* (pattern without a demonstrated path — routed for dynamic confirmation), *needs-dynamic-confirmation* (worth the offensive lane's time). The secret scanner's hits are always location+class only — triage never opens the value.

## 6. Report and Stop

My report to the orchestrator carries:

- The surfaces covered, the tools run, and the tools that could not run (with the per-tool reason)
- The sweep budget and what it reached (files, findings before/after dedup)
- Each candidate: tool, rule, location, violated assumption, impact statement, triage verdict
- Secret-class findings: location and class only — never content
- Every process identifier I started
- The gaps: surfaces not covered and why

Then my turn ends. Teardown is explicit: I stop what I started before the report goes out — nothing of mine outlives the dispatch.
