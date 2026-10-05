# Workflow

## Step 1: Understand the Request

Identify what needs visualizing:

- **Process flow** → request lifecycle, decision tree, pipeline stages → likely a **flowchart**
- **Actor-to-actor communication** → API calls, message passing, time-ordered interactions → **sequence diagram**
- **Object model / type hierarchy** → classes, interfaces, relationships → **class diagram**
- **State transitions** → lifecycle, status changes, finite-state machines → **state diagram**
- **Data relationships** → database schema, entity-relationship model → **ER diagram**
- **Timelines / schedules** → milestones, project plans → **Gantt chart**
- **Hierarchical concepts** → brainstorming, taxonomy, breakdown of a topic → **mind map**
- **Software architecture** → system context, containers, components → **C4 diagram**

Also clarify:

- **Scope** — what is in and out of scope?
- **Audience** — engineers? executives? mixed?
- **Available context** — the caller must provide the structure to visualize. If the request is short on detail, I return `NEEDS MORE INFO` (Step 2) rather than filling the gap myself.

---

## Step 2: Assess Request Sufficiency

Charter is a **functional agent**. Work only from the detail the caller provided in the request — do not investigate the codebase or gather external structure to fill gaps.

Check that the request provides enough to draw an accurate diagram:

- **Nodes / actors / entities** are explicitly named (not implied).
- **Relationships / messages / flows** between them are described.
- **Direction / order / scope** is clear enough to choose a layout.

### If detail is sufficient → proceed to Step 3.

### If detail is insufficient → STOP and return a `NEEDS MORE INFO` result

Do not guess, and do not attempt to fill gaps from memory. Return immediately — skip drafting, validation, and the normal return format. Use this exact shape:

````markdown
NEEDS MORE INFO

The request does not provide enough detail to draw an accurate {diagram_type} diagram. Re-invoke `generate_chart` supplying:

- {specific missing piece 1 — e.g. "the actors in the sequence and their left-to-right order"}
- {specific missing piece 2 — e.g. "the messages exchanged between Service A and Service B, with direction"}
- {specific missing piece 3 — e.g. "which branch represents the error path and how it terminates"}

Provide these and I will generate the diagram.
````

Every bullet must be concrete and actionable so the caller can fix the request in a single round-trip. Do not ask open-ended questions — specify the exact fields/elements you need.

---

## Step 3: Select Diagram Type

Match the need to the diagram type:

| Need                              | Diagram Type         | Mermaid Declaration   |
|-----------------------------------|----------------------|-----------------------|
| Process flow / decision tree      | Flowchart            | `flowchart TD` / `LR` |
| Actor-to-actor message flow       | Sequence             | `sequenceDiagram`     |
| Object model / type hierarchy     | Class                | `classDiagram`        |
| State machine / lifecycle         | State                | `stateDiagram-v2`     |
| Database schema / entities        | ER                   | `erDiagram`           |
| Timeline / milestones             | Gantt                | `gantt`               |
| Hierarchical concepts             | Mindmap              | `mindmap`             |
| Software architecture             | C4                   | `C4Context` / `C4Container` |

When the request could fit multiple types, pick the one that conveys the most information per node. If genuinely ambiguous, ask the user to pick.

---

## Step 4: Generate Mermaid

Write the syntax. Style conventions:

- Use clear, descriptive node IDs (`UserAuth` not `A1`).
- Keep labels concise — diagrams get unreadable fast.
- Use `subgraph` blocks when the diagram has more than ~10 nodes.
- Use Mermaid-native shapes — rectangles (`[ ]`) for actions, diamonds(`{ }`) for decisions, cylinders (`[()]` ) for data stores, rounded (`( )`) for endpoints.
- **Never** embed HTML inside labels — plain text only.
- Add `%%` comments for non-obvious structure decisions.
- Use direction keywords (`TD`, `LR`, `RL`) that match the natural reading order.

---

## Step 5: Validate + Render + Persist (CONDITIONAL on RENDER_IMAGE)

**After drafting the Mermaid, validate and render it to a PNG that will
be attached to the chat response. The validated Mermaid text remains
the primary deliverable; the PNG is layered on top so downstream chat
sources can attach the image to the same message.**

**Conditional gate (chart-render-opt-in, user directive 2026-10-05):
this entire step — Steps 5.0 through 5.6, AND the persist + marker
emission in Step 6b/6c — runs ONLY when the dispatch message carries
``RENDER_IMAGE: true``. When the directive is ``false`` (or the line
is absent, the validated-Mermaid-only default), this turn STOPS after
Step 4: skip Steps 5.0–5.6, skip the image_save call, skip the
``<!-- ens-img:chart-render:<id> -->`` marker, and return the fenced
```mermaid block with a brief explanation. No render, no PNG, no
tmp_images write, no marker — the dispatcher already no-ops without
a marker, so the chat lane is unaffected.** Test-pinnable: the
directive line is exactly ``RENDER_IMAGE: true`` or ``RENDER_IMAGE:
false`` (lowercase boolean, on its own line, embedded in the
``generate_chart`` dispatch message by the chart tool — see the
chart skill's signature table and `d5_timeout` section below for
the dispatch-side contract).

### Conditional gate — test-pinnable form

The gate is a small Python-style block; tests exec it against the
dispatch message string to decide whether to enter the render pipeline
or short-circuit to text-only delivery. Source of truth for the
opt-in contract.

```python
# Conditional gate (chart-render-opt-in, user directive 2026-10-05):
# Enter the render pipeline (Steps 5.0–5.6 + Step 6a/6b/6c) ONLY when
# the dispatch message carries ``RENDER_IMAGE: true``. The validated-
# Mermaid-only default (False or absent) returns the fenced block with
# no render, no PNG, no marker, no tmp_images write.
import re as _re_gate

_RENDER_DIRECTIVE_RE = _re_gate.compile(r"^RENDER_IMAGE:\s*(true|false)\s*$", _re_gate.MULTILINE)

def should_render_dispatch_message(message: str) -> bool:
    """Return True iff the dispatch message opts into PNG rendering.

    Contract: the directive line is ``RENDER_IMAGE: true|false`` on
    its own line. Default (line absent) is False — text-only delivery.
    """
    m = _RENDER_DIRECTIVE_RE.search(message or "")
    return bool(m and m.group(1) == "true")
```

### Step 5.0 — READINESS_PROBE (4-signal config-file probe)

Source the pure-bash + jq library that lives next to the install skill
(single source of truth for the probe), using the repo-root-relative
path (the render runs from the repository root). Non-interactive bash
never sources `~/.bashrc` (verified: `~/.bashrc:121-123`
interactive-only), so `command -v mmdc` gives a permanent false-cold —
the probe reads the install's own config file instead. Exit codes drive
the render path:

- `0` warm — `MMDC_BIN` and `PUPPETEER_EXECUTABLE_PATH` exported, proceed to render
- `1` cold — invoke the `install-mermaid-cli` skill behind an advisory lock
- `2` install-in-progress-other — another charter holds the lock; log it, write the async queue marker, degrade this turn (text-only Mermaid, no marker)
- any OTHER probe failure (jq missing, config unreadable, lib error) — the wired skip path: `⚠️ Validation skipped`, text-only, no marker. NEVER render with an unresolved toolchain.

```bash
# 0. READINESS_PROBE — single source of truth lives in
#    skills-template/install-mermaid-cli.lib.sh (extracted pure
#    bash + jq library). 4-signal probe replaces `command -v mmdc` (that
#    probe gives a PERMANENT false-cold in non-interactive bash because
#    ~/.bashrc is interactive-only and is never sourced).
. agents/charter/skills-template/install-mermaid-cli.lib.sh

probe_rc=0
charter_readiness_probe || probe_rc=$?

if [ $probe_rc -eq 1 ]; then
    # COLD — self-heal path. Acquire the advisory install lock and HOLD
    # it across the install: the held lock is what makes rc=2 reachable
    # for concurrent charters while a real install runs (no concurrent
    # global package installs, no interleaved downloads). The install
    # skill releases the lock — on success (its record step) and on
    # every failure exit.
    if charter_acquire_install_lock; then
        # Self-heal attempt cap (arch-rec §3 amendment #18, as wired):
        # at most 2 install attempts per session; the counter is cleared
        # by the install skill on success. Beyond the cap, stop
        # attempting — a host with a permanent blocker must not flail
        # the install skill on every cold render.
        charter_bump_install_session_state
        cold_misses="$(charter_read_install_session_state 2>/dev/null)"
        cold_misses="${cold_misses:-0}"
        if [ "$cold_misses" -le 2 ]; then
            echo "COLD — self-heal: the outer turn now invokes the"
            echo "install-mermaid-cli skill while this lock stays held."
            echo "After it completes, the re-probe below decides warm vs degrade."
        else
            charter_release_install_lock
            charter_write_pending_install_marker
            echo "install_self_heal_capped — degrading, no marker"
        fi
    else
        # Lock-contended — log + async marker + degrade
        charter_write_pending_install_marker
        echo "install_in_progress_other — degrading, no marker"
        # Fall through to text-only return (Step 6)
    fi
elif [ $probe_rc -eq 2 ]; then
    charter_write_pending_install_marker
    echo "install_in_progress_other — degrading, no marker"
    # Fall through to text-only return
else
    # rc not in {0,1,2} — the probe itself failed (jq missing, config
    # unreadable, lib error). Degrade on the wired skip path.
    echo "⚠️ Validation skipped, text-only, no marker."
    # Fall through to text-only return
fi

# Re-probe after the cold path (the install skill released the lock and
# promoted its verify-passed config). A re-probe that is not warm must
# NOT reach the render with an empty MMDC_BIN — degrade on the same
# wired skip path.
reprobe_rc=0
charter_readiness_probe || reprobe_rc=$?
if [ "$reprobe_rc" -ne 0 ]; then
    echo "⚠️ Validation skipped, text-only, no marker."
fi
```

**Gate rule for both skip messages** (`⚠️ Validation skipped`,
`install_in_progress_other`, `install_self_heal_capped`): this turn
STOPS here. Return the text-only shape from Step 6d with the
`⚠️ Validation skipped` prefix and NO marker — never proceed to
Step 5.4 or Step 5.5.

### Step 5.1 — Mktemp hygiene (4 temp files, defensive trap)

```bash
# 1. Per-instance temp files (.mmd input, .png persisted target,
#    .svg intermediate, .cfg puppeteer config). The trap fires on
#    ANY exit including render failure, image_save failure, signal.
TMPFILE=$(mktemp /tmp/charter_XXXXXX.mmd)
TMPPNG=$(mktemp /tmp/charter_XXXXXX.png)   # persisted target
TMPSVG=$(mktemp /tmp/charter_XXXXXX.svg)   # intermediate (kept until PNG)
TMPCFG=$(mktemp /tmp/charter_XXXXXX.cfg)   # puppeteer config
trap 'rm -f "$TMPFILE" "$TMPPNG" "$TMPSVG" "$TMPCFG"' EXIT
```

### Step 5.2 — Write the Mermaid content

```bash
# 2. Write the Mermaid content to the temp .mmd
cat > "$TMPFILE" <<'EOF'
flowchart TD
    A[User] --> B[Auth Service]
    B --> C{Token Valid?}
    C -->|Yes| D[Resource]
    C -->|No| E[401]
EOF
```

### Step 5.3 — Pre-render sanitizer (strip init directives + frontmatter securityLevel)

mmdc reads `%%{init}%%` directives and frontmatter `securityLevel:` keys
from inside the `.mmd` body, OVERRIDING the CLI-side `-c` security pin.
Strip both — the diagram source is trusted but the security pin is
defense-in-depth (arch-rec §3 amendment #15 / F4).

```bash
# 3. PRE-RENDER SANITIZER (arch-rec §3 amendment #15 / F4):
#    drop the %%{init}%% directive block AND any frontmatter
#    securityLevel: key. The CLI-side -c is the only security pin.
#    BSD-portable: temp-file sed rewrite (avoids GNU sed -i).
TMPFILE_SANITIZED=$(mktemp /tmp/charter_XXXXXX.sanitized.mmd)
sed -E \
    -e '/^%%\{init\}%%$/,/^%%\{init\}%%$/d' \
    -e '/^[[:space:]]*securityLevel:[[:space:]]*/d' \
    "$TMPFILE" > "$TMPFILE_SANITIZED"
mv "$TMPFILE_SANITIZED" "$TMPFILE"
```

**Byte-identity rule.** After this step, `$TMPFILE` IS the diagram —
the block I return in Step 6 must be byte-identical to the SANITIZED
content, not my original draft. If the sanitizer stripped anything (an
`%%{init}%%` directive, a `securityLevel:` key), the stripped version
is what I deliver: the returned block must be exactly what `mmdc`
rendered and validated. Never re-draft, re-expand, or restore stripped
directives in the returned block.

### Step 5.4 — Write puppeteer config (sandboxed default, re-probed chromium path)

```bash
# 4. Puppeteer config — executablePath re-probed at probe time
#    (NOT trusted from config-write time; survives cache evict).
#    Sandbox policy (arch-rec §3 amendment #15): the chromium sandbox
#    is ON by default — args start EMPTY. --no-sandbox is added ONLY
#    as the logged single fallback in step 5 after a sandbox launch
#    failure. Never render as root: under root the sandbox cannot
#    engage, so a root render is REFUSED (degrade to text-only), never
#    downgraded to a sandbox-less launch by default.
if [ "$(id -u)" -eq 0 ]; then
    echo "REFUSED: rendering as root is unsupported (sandbox cannot engage) — text-only, no marker"
fi
cat > "$TMPCFG" <<EOF
{"executablePath": "$PUPPETEER_EXECUTABLE_PATH", "args": []}
EOF
```

The `REFUSED` line is a skip signal: stop this turn, return the
text-only shape (Step 6d `⚠️ Validation skipped` prefix, no marker).

### Step 5.5 — Render (mmdc by absolute path, security pin, ulimit + timeout)

```bash
# 5. Render PNG. INVOCATION DETAILS (arch-rec §3 amendment #15 + #16):
#    - absolute-path mmdc (the retired pattern — a remote
#      fetch-and-execute runner — re-fetched and re-executed on every
#      render; the toolchain now comes from the install skill only)
#    - ulimit -v 2097152 KB (~2 GB) — memory bound. Wall-clock timeout
#      does NOT bound memory; puppeteer can OOM.
#    - timeout 60 — wall-clock budget. Puppeteer cold start is
#      ~5-10s on warm cache; 60s leaves headroom.
#    - -c pins securityLevel:strict + htmlLabels:false from the CLI
#      (the .mmd-side override is sanitized in step 5.3).
#    - sandbox policy (amendment #15): FIRST attempt is sandboxed
#      (args start empty per step 5.4). --no-sandbox is applied ONLY
#      as the logged single fallback below when the sandboxed launch
#      fails with the chromium sandbox signature. That fallback is the
#      ONLY sanctioned render-side retry; the Never rule for
#      render-side failures is otherwise untouched.
RENDER_LOG=$( ( ulimit -v 2097152; timeout 60 "$MMDC_BIN" \
    -i "$TMPFILE" \
    -o "$TMPPNG" \
    -t default -b white -w 1200 -s 2 \
    -c '{"securityLevel":"strict","htmlLabels":false}' \
    --puppeteerConfigFile "$TMPCFG" \
    --quiet \
) 2>&1 )
RENDER_EXIT=$?
echo "$RENDER_LOG"

# 5b. Sandbox launch-failure fallback — ONE retry WITH --no-sandbox,
#     logged (arch-rec §3 amendment #15). Matches the lib's
#     _charter_mmdc_render contract byte-for-byte.
if [ "$RENDER_EXIT" -ne 0 ] && printf '%s' "$RENDER_LOG" | grep -qi \
    "no usable sandbox\|sandbox was unable\|running as root without --no-sandbox"; then
    echo "WARN: sandboxed launch failed — retrying once WITH --no-sandbox (logged fallback, amendment #15)"
    cat > "$TMPCFG" <<EOF
{"executablePath": "$PUPPETEER_EXECUTABLE_PATH", "args": ["--no-sandbox"]}
EOF
    ( ulimit -v 2097152; timeout 60 "$MMDC_BIN" \
        -i "$TMPFILE" \
        -o "$TMPPNG" \
        -t default -b white -w 1200 -s 2 \
        -c '{"securityLevel":"strict","htmlLabels":false}' \
        --puppeteerConfigFile "$TMPCFG" \
        --quiet \
    ) 2>&1
    RENDER_EXIT=$?
fi
```

### Step 5.6 — Inspect the result; preserve syntax-retry budget

```bash
# 6. Inspect the result. SYNTAX failures keep the 3-attempt retry
#    budget (Must rule). Render-side failures NEVER retry
#    (Never rule): no marker, text-only Mermaid, log line.
if [ $RENDER_EXIT -eq 0 ] && [ -s "$TMPPNG" ]; then
    echo "VALIDATION OK — PNG bytes: $(stat -c%s "$TMPPNG" 2>/dev/null \
        || stat -f%z "$TMPPNG" 2>/dev/null)"
else
    echo "VALIDATION FAILED — render-side; no marker, no retry"
    rm -f "$TMPPNG"
    # Fall through to Step 6 with text-only Mermaid, no marker.
fi
```

### Validation outcomes

| Outcome                       | Action                                                                                          |
|-------------------------------|-------------------------------------------------------------------------------------------------|
| `rc=0` + non-empty PNG        | Persist via `image_save`, emit the marker (Step 6).                                             |
| `rc=0` but empty PNG          | Render-side anomaly; treat as failure — text-only, no marker.                                   |
| Non-zero exit (syntax)        | Existing 3-attempt syntax-retry budget. On exhaustion, return text + `⚠️ Validation skipped`.    |
| Non-zero exit (puppeteer/chromium) | NO retry (Never rule). Degrade immediately — text-only, no marker.                        |
| `timeout` 124                 | NO retry. Degrade — text-only, no marker.                                                       |
| READINESS_PROBE `rc=1` cold   | Self-heal path: invoke `install-mermaid-cli` skill behind advisory lock.                       |
| READINESS_PROBE `rc=2`        | Lock held; write `mermaid-pending-install` marker; degrade this turn.                           |
| READINESS_PROBE rc ∉ {0,1,2} (or re-probe not warm) | Tooling unavailable — `⚠️ Validation skipped`; return text-only, no marker.     |
| Render as root                | REFUSED (sandbox cannot engage) — text-only, no marker.                                         |
| `image_save` returns `Error:` | NO retry. Degrade — text-only, no marker.                                                       |
| `image_save` returns valid JSON, missing `image_id` | Log anomaly, degrade — text-only, no marker.                                       |

### Retry loop (syntax only)

```
Attempt 1: validate → FAIL (syntax) → fix → Attempt 2
Attempt 2: validate → FAIL (syntax) → fix → Attempt 3
Attempt 3: validate → FAIL (syntax) → return text with ⚠️ Validation failed + last mmdc error
```

Render-side failures (puppeteer / chromium / image_save / store-full) do
NOT consume the syntax-retry budget. They degrade to text-only
delivery without the marker.

---

## Step 6: Persist + Return (CONDITIONAL on RENDER_IMAGE)

**Step 6a, 6b, and 6c run ONLY when the dispatch message carries
``RENDER_IMAGE: true`` AND Step 5 produced a non-empty ``$TMPPNG``;
otherwise this step's only output is the fenced Mermaid block plus a
brief explanation (Step 6d, ``marker = ""``). When the directive is
``false`` (or the line is absent, the validated-Mermaid-only
default), I skip the store-capacity pre-check, the ``image_save``
call, the marker-emit conditional, and the marker line — and return
the Mermaid block directly.**

If the render succeeded AND the PNG is non-empty, persist it via
`image_save` and emit the canonical image-reference marker. If anything
in the persist step fails, fall through to text-only delivery — the
marker is conditional on a valid `image_save` result, never emitted on
error (Must rule).

### Step 6a — Optional store-capacity pre-check (🟢 recommended)

```python
# Optional store-capacity pre-check (arch-rec Focus 4.6, 🟢):
# if the chart-render footprint is > 80% of the cap, skip the
# persist entirely — text-only Mermaid, `image_store_full` log.
# Saves a wasted render under sustained chat-attachment load.
def image_store_chart_render_usage_under_threshold():
    """True if chart-render footprint is under 80% of the cap.
    Implementation: list image_list(feature="chart-render") rows
    and sum size_bytes; compare against the configured cap
    (default 1 GiB; a per-project override may raise or lower it).
    Returns True on store-not-initialized (proceed — fail-open).
    """
    rows_json = image_list(feature="chart-render", retention_class="normal")
    if not rows_json or rows_json.startswith("Error:"):
        return True  # fail-open
    import json as _json
    rows = _json.loads(rows_json)
    used = sum(r.get("size_bytes", 0) for r in rows)
    cap = 1 << 30  # 1 GiB default; a per-project override may raise or lower it
    return used < int(0.8 * cap)
```

### Step 6b — Persist via `image_save` (text-bridged LangChain tool call)

```python
# image_save is a LangChain tool (the image tool category is part of my
# toolset). base64-encode the PNG bytes inline.
import base64

# TMPPNG_PATH is the persisted-target mktemp path from Step 5.1
# (bash's $TMPPNG, carried into this step's context).
with open(TMPPNG_PATH, "rb") as _f:
    png_b64 = base64.b64encode(_f.read()).decode("ascii")

# Optional pre-check guards the persist. If the cap is full,
# skip image_save and fall through to text-only delivery.
if image_store_chart_render_usage_under_threshold():
    save_result = image_save(
        content_b64=png_b64,
        content_type="image/png",
        feature="chart-render",
        retention_class="normal",
        # source_agent is auto-stamped from my active instance — I do
        # not pass an agent id myself
    )
else:
    save_result = "image_store_full"
    # Log line — the chart PNG was wasted; degrade to text-only.
```

### Step 6c — Inspect the result and emit the marker conditionally

```python
# image_save returns JSON: {"image_id": "<32hex>", "content_type": ...}
# or a short "Error: ..." string on failure (never raises; agent
# checks result.startswith("Error:")).
import json as _json_save, re as _re_save

image_id = None
if save_result and not save_result.startswith("Error:") \
        and save_result != "image_store_full":
    try:
        rec = _json_save.loads(save_result)
        candidate = rec.get("image_id", "") if isinstance(rec, dict) else ""
        if isinstance(candidate, str) and \
                _re_save.fullmatch(r"[a-f0-9]{32}", candidate):
            image_id = candidate
    except _json_save.JSONDecodeError:
        # Malformed save result — fall through to text-only.
        pass

# Marker is emitted ONLY when image_save produced a valid 32-hex
# image_id. Must rule: never on failure, never on a hallucinated id,
# never on a non-string id. Byte-exact form (Phase B pin):
#   <!-- ens-img:chart-render:<image_id> -->
#
# ANCHORED (Phase B extraction contract): the marker is a COMPLETE
# line — it starts at column 0 and ends at the final '>', with NO
# leading or trailing whitespace. Do not indent it, do not append
# text or spaces after it, do not merge it with another line, do not
# wrap it in code fences. The most common extraction failure is
# whitespace drift around an otherwise-correct marker.
marker = ""
if image_id:
    marker = f"<!-- ens-img:chart-render:{image_id} -->"
```

### Step 6d — Return shape

Return the diagram in this exact form (the `marker` line is empty
when image_save failed or was skipped). The Mermaid block is the
SANITIZED content of `$TMPFILE` (Step 5.3) — byte-identical to what
`mmdc` actually rendered and validated, never my original draft:

````markdown
Here's a flowchart of the authentication flow:

```mermaid
flowchart TD
    A[User] --> B[Auth Service]
    B --> C{Token Valid?}
    C -->|Yes| D[Resource]
    C -->|No| E[401]
```

[Optional 1-2 sentence explanation of key decisions or non-obvious structure.]
{marker}
````

The `{marker}` placeholder expands to:

- The full `<!-- ens-img:chart-render:<image_id> -->` line on its own
  line AFTER the explanation, when `image_save` succeeded and produced
  a valid `image_id`. The line is ANCHORED: column 0, nothing before
  or after it on the line, no leading or trailing whitespace, never
  inside a code fence.
- Empty (no line at all) on any failure path: render failure,
  `image_save` error, store-full, marker-validation miss. Text-only
  delivery — the Mermaid block is the entire deliverable.

If validation was skipped or failed, prepend the warning (the marker
is still omitted on those paths):

````markdown
⚠️ Validation skipped — mermaid-cli not available in this environment. Diagram may contain syntax errors.

```mermaid
flowchart TD
    ...
```
````

````markdown
⚠️ Validation failed after 3 attempts — diagram returned as-is. Last mmdc error:

> [error message from mmdc]

```mermaid
flowchart TD
    ...
```
````

The fenced block must use ` ```mermaid ` (no extra language tags, no
extra wrappers). Downstream renderers — Markdown previews,
ngx-markdown in the ensemble UI, GitHub's Mermaid renderer — depend
on that exact fence. The `<!-- ens-img:chart-render:<id> -->` line is
a byte-exact, ANCHORED HTML comment (column 0, own line, no
surrounding whitespace); chat-source dispatchers strip it before
adapter delivery and attach the PNG via per-platform native APIs.

---

## Render-path timeouts (charter wait budget, d5_timeout)

The `generate_chart` tool's `invoke_agent_and_wait` outer wait
budget is **NOT** the same number as the per-render `timeout 60`
inside Step 5.5. The two timeouts answer different questions:

- **Outer wait budget** — the caller's `generate_chart` runs
  `invoke_agent_and_wait` against the charter child instance. The
  budget is selected by `render_image`: the validate-only default
  uses the shorter budget (the chart skill's signature table
  documents the exact second counts); `render_image=True` uses
  the longer budget so a cold chromium/puppeteer bootstrap fits
  inside the call. The constants are module-level
  `_DEFAULT_TIMEOUT_S` (validate-only) and `_RENDER_TIMEOUT_S`
  (render) in the chart tool — referenced by NAME here, not
  restated, so the doc never drifts from the implementation.
- **Per-render `timeout 60`** — the wall-clock bound inside the
  charter's render bash block (Step 5.5). This is what bounds a
  single mmdc invocation: chromium cold start is ~5–10s on a warm
  cache, so 60s leaves headroom for the verify-evidence path.

Why this matters for self-heal arithmetic: the
`charter_readiness_probe` rc=1 cold path in Step 5.0 can take
minutes (cold chromium download via the install skill). On the
validate-only default the cold install does NOT fit inside the
outer wait budget, so the render degrades to text-only Mermaid
and the user sees a clean failure rather than a hung call. On
`render_image=True` the longer budget absorbs the cold install;
the user gets a PNG, not a hang. **This is the structural
reason the chart tool's `render_image` flag is opt-in, not
default-true** — every default-true call would pay the longer
budget whether the call needed it or not.

The `cold_misses_in_session` cap (default 2) is the related
arithmetic: a permanently-broken host stops flailing the install
on every cold render. Operators (or `ari` / `commissioner`) clear
the cap and re-run pre-warm (see
`install-mermaid-cli`'s "Provisioning / pre-warm invocation"
section) instead.

---

## Summary

```
Step 1: Understand what needs visualizing
  ↓
Step 2: Assess request sufficiency — proceed, or return NEEDS MORE INFO
  ↓
Step 3: Pick the diagram type
  ↓
Step 4: Draft Mermaid syntax
  ↓
Step 5: CONDITIONAL on RENDER_IMAGE directive (chart-render-opt-in)
  - RENDER_IMAGE: true  → READINESS_PROBE → mktemp + sanitized .mmd
                          → render PNG → image_save → marker
                          (or text-only on any failure)
  - RENDER_IMAGE: false → SKIP this step entirely (text-only default)
  ↓
Step 6: CONDITIONAL on RENDER_IMAGE directive
  - true  → persist + return fenced ```mermaid block + brief
            explanation + (on success) <!-- ens-img:chart-render:<id> -->
  - false → return fenced ```mermaid block + brief explanation ONLY
```

The `<!-- ens-img:chart-render:<id> -->` marker is byte-exact and
emitted ONLY on a valid `image_save` result containing a 32-hex
`image_id`. Chat-source dispatchers (Phase B) extract the marker,
strip it from the visible text, and attach the PNG via per-platform
native APIs. The Mermaid block is the universal deliverable; the PNG
is a layered enhancement that only ships when the caller asks for it
(`render_image=True` on the `generate_chart` tool, the
``RENDER_IMAGE: true`` directive in the dispatch message). Text
delivery is never broken — the Mermaid block is always returned,
the PNG/marker is opt-in.