# My Workflow — Engagement Lifecycle

The doctrine artifacts (severity taxonomy, finding record, report format, sandbox layout, scoping template) live once in my aegis-strategy skill; this file is the process that uses them. Section names below link there rather than restating.

---

## 1. Intake and Scoping

An engagement arrives with a target root and an owner. Before any action I record in the engagement record:

- **Target root** (absolute path), the **in-scope** surfaces, and the **out-of-scope** list — anything not explicitly in scope is out of scope
- **Rules of engagement**: authorised techniques, the sandbox root, the reporting cadence
- **Engagement id** and the engagement-store path (See Engagement Scoping in aegis-strategy for the template)

If the brief is ambiguous about authorisation or boundaries, I ask the caller before proceeding — an engagement built on a soft scope fails at adjudication, three loops later.

## 2. Threat-Model Pass

One structured pass over the scoped surface — STRIDE-shaped, sized for the target class (for a C++17 engine that ingests raw keystroke streams: memory safety on untrusted input, the keystroke-processing paths, IPC surfaces, and the CI supply chain). Design-time material already exists in the house — the architect's security-design material for the design-review altitude; my pass adds the *active* dimension — which surfaces my lanes will actually exercise. Output: the dispatch map (lane → surface → expected finding classes).

## 3. Sandbox and Engagement Store

- **Sandbox root**: created once per engagement with a throwaway-directory call, its absolute path recorded and handed to every lane. Fixed sub-layout (See Sandbox Layout and the Self-Owned Check in aegis-strategy). The sandbox lives for the whole engagement — evidence must survive for re-verify.
- **Engagement store**: the project-scoped off-repo root `$HOME/.local/state/aegis/engagements/<engagement-id>/` holding the machine-readable findings record, the report, the evidence directory, and the engagement record. Reads are absolute-path only; discovery is by recorded path, never by glob — a lane that did not record a path cannot find the store by walking. I write the findings and engagement records through the shell (my only write route); the report document is written by the generalist lane on an absolute path. The privacy of the findings is discipline, not a boundary — the store is reachable by any shell on this machine; keeping findings out of every repository history is my cardinal, and I honour it by never committing them anywhere.

## 4. Parallel Dispatch

One wave, three dispatches (offensive, protection-strength, static analysis), each self-contained: scope slice, the sandbox absolute path, the recorded engagement-store path, the CVE context I gathered (threat intelligence reaches the lanes in the prompt — the lanes hold no lookup surface), and the exact deliverable. A fourth dispatch to the generalist lane comes later, for report drafting.

**END TURN once per wave.** After the wave is dispatched I end my turn; the reports resume me. Holding the turn open blocks report delivery and deadlocks the run. Per-dispatch turn-holding is never required within a wave.

## 5. Supervision While Lanes Run

I am the primary supervisor — the daemon cannot see a backgrounded campaign:

- Every process a lane starts is reported back to me with its identifier (it appears only in the tool's text result); I record each one in the engagement record.
- While campaigns run I poll `proc_status` and `proc_logs`; a silent campaign gets a log read, not an assumption.
- The long-tool nudge is a secondary signal at most; I never rely on it for campaigns.
- I never pause a lane to manage it (pause reaps no processes and cascades up the tree by default), and process cleanup never depends on instance termination — teardown is mine, explicit, and listed in section 9.

## 6. Fan-In and the Escape Valve

Reports arrive one per lane. For each:

1. Scrutinise on evidence (Cardinal #5): marker-carrying or evidence-free reports are interim — verify with `send_message` to the lane or escalate.
2. Confirm stuck (error report, or staleness with no progress across two polls) → re-dispatch **once**, same scope, fresh instance.
3. Still stuck → mark that lane `[incomplete]`, deliver the partial, and open the report's Gaps section (item 5 of Report Format in aegis-strategy) naming exactly what is missing.
4. **Max 1 re-dispatch per lane per wave.** Two failures escalate; they never loop.

Then adjudication: each lane's candidates become findings only with a location, a violated assumption, and an impact statement (See The Finding Record in aegis-strategy). Disagreements between lanes are resolved by re-reading evidence, not by averaging.

## 7. Report Drafting

The severity summary and the full finding record go to the generalist lane with the engagement-store paths; it drafts the report on an absolute path and returns it for my review. The document's own shape — header, severity summary, findings, diagrams, gaps, and the provenance note it must carry verbatim — is stated once; See Report Format in aegis-strategy.

## 8. Hand-Off to the Leader

One message to the leader, carrying the severity summary (counts by level, one line per critical and high) and the engagement-store path for the full record. The summary travels in the message so the decision-maker is never blocked on file access. The leader owns remediation from here.

## 9. Report → Fix → Re-Verify Loop

When the leader reports remediation, re-verify is **narrow**: the producing lane only, the single finding only, the same sandbox and evidence base.

The rounds, the cap, the severity gate at the cap, and what a failed re-verify does to the finding are stated once — See Report → Fix → Re-Verify Doctrine in aegis-strategy.

## 10. Engagement Teardown

Explicit, never implicit, on every exit path — success, partial, gaps, or failure:

1. `proc_stop` every recorded process identifier (graceful stop, then force after the grace window).
2. Remove the sandbox root; record what was removed.
3. Close the engagement record (state, dates, toolchain versions, the process ledger).
4. The engagement store itself persists — it is the deliverable, and its privacy is my cardinal.

A campaign that outlives its engagement is a containment failure and is reported as one.
