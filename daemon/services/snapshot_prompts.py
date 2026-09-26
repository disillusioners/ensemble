"""Prompt module for the Agent Snapshot system (R11 — PR4).

Owns the snapshot summarizer's steering surface. Three artifacts:

* :data:`R11_STEERING_BLOCK` — the user-ratified north-star steering
  block from design-exploration §6.3 R11, **verbatim** (pinned by
  test — drift here is digest-quality drift).
* :data:`DIGEST_EXTRACTION_FIELDS` — the R11 8-field extraction
  tuple. Exactly 8; the dead-ends norm (:data:`R11_DEAD_ENDS_NORM`)
  is a norm BESIDE the tuple, NOT a 9th field.
* :data:`SNAPSHOT_SUMMARIZER_PERSONA` — the full SystemMessage
  passed to :func:`daemon.compaction.call_summarization_llm_for_snapshot`
  (the ``system_message`` kwarg is REQUIRED on that call — the
  snapshot must never inherit the compaction persona).

D5 (digest-only): there is NO message-history parameter anywhere in
this surface — the capture stores HOW the instance worked, not its
transcript; tail results live in artifacts by pointer.

Quality steering: the ~25k-token injection ceiling is a CEILING, NOT
a TARGET — padding toward it is a prompt-level failure (the last
line of the steering block).
"""

from __future__ import annotations

# Prompt version stamped into every digest's provenance block
# (design §9 mitigation d — audit surface for digest drift).
SNAPSHOT_PROMPT_VERSION = "agent-snapshot-v1-r11.1"

# ── R11 steering block — VERBATIM from design-exploration §6.3 ──────────
# (the design's stated north star; test-pinned byte-for-byte — do NOT
# edit without re-pinning against the design doc).
R11_STEERING_BLOCK = (
    "Distill this instance's EXPERIENCE, not its transcript. Preserve: "
    "what worked and what wasted time — and why; gotchas and conventions "
    "discovered; mid-run workflow refinements you would apply next time; "
    "judgment calls and the reasoning behind them; dead ends worth "
    "remembering as dead ends. Do NOT restate results, logs, or outputs — "
    "reference them as artifacts (paths, job ids, commits). Flag timeless "
    "system knowledge for promotion (`promote-to-kb:`) and reusable "
    "procedures for skill graduation instead of embedding them. Capture "
    "only the delta over any inherited warm-start digest (already "
    "persisted). Prefer specific, reusable instincts over generic "
    "summary; when in doubt, keep the lesson, drop the detail. The ~25k "
    "ceiling is a ceiling, not a target."
)

# ── R11 8-field extraction tuple ─────────────────────────────────────────
# Each entry: (digest_key, field name, instruction). Exactly 8 — the
# dead-ends norm below is a norm BESIDE the tuple, not a 9th field.
DIGEST_EXTRACTION_FIELDS: tuple[tuple[str, str, str], ...] = (
    (
        "decisions",
        "Decisions",
        "Durable choices and their rationale.",
    ),
    (
        "gotchas",
        "Gotchas",
        "Discovered traps, sharp edges, \"don't repeat my mistake\" rules.",
    ),
    (
        "conventions",
        "Conventions",
        "Codebase-/project-local norms observed or established.",
    ),
    (
        "open_threads",
        "Open threads",
        "Unresolved work, deferred questions, follow-ups.",
    ),
    (
        "artifact_refs",
        "Artifact refs",
        "Paths, job ids, commit hashes, branch names, RESULTS file paths "
        "— reference them, do NOT mirror content.",
    ),
    (
        "worked_vs_wasted",
        "Worked-vs-wasted",
        "What approaches worked, what wasted time, and WHY each failed "
        "or succeeded (this field is what makes a snapshot re-usable "
        "for a different agent).",
    ),
    (
        "workflow_refinements",
        "Mid-run workflow refinements",
        "\"Next time probe X before Y\"; sequencing lessons; \"the order "
        "matters here.\"",
    ),
    (
        "judgment_calls",
        "Judgment calls",
        "What trade-offs were made, what was weighed, what was chosen, "
        "and the reasoning behind the choice.",
    ),
)

# Canonical digest keys, in render order (exactly the 8 R11 fields).
DIGEST_KEYS: tuple[str, ...] = tuple(key for key, _name, _inst in DIGEST_EXTRACTION_FIELDS)

# ── Dead-ends norm — BESIDE the tuple, NOT a 9th field ───────────────────
# (design R11: "an explicit dead-ends-remembered-as-dead-ends norm —
# record paths that didn't work as paths not to take, with a one-line
# reason." It steers HOW gotchas / worked-vs-wasted bullets are written;
# it does not add a digest section.)
R11_DEAD_ENDS_NORM = (
    "Record paths that didn't work as paths not to take — remember dead "
    "ends AS dead ends, each with a one-line reason."
)


def _compose_snapshot_summarizer_persona() -> str:
    """Compose the R11 SystemMessage persona for the digest LLM call.

    Internal helper — there is no external caller; the only use is
    the module-level cache (:data:`SNAPSHOT_SUMMARIZER_PERSONA`) at
    import time (NIT #8 / Wave 2a pre-step). Layout: the verbatim
    steering block first (it is the design's north star and
    test-pinned), then the 8-field extraction contract, then the
    dead-ends norm, then the output format.
    """
    field_lines = "\n".join(
        f"- **{name}** (`{key}`) — {instruction}"
        for key, name, instruction in DIGEST_EXTRACTION_FIELDS
    )
    return (
        "You are distilling an agent instance's accumulated working "
        "experience into a durable, searchable digest that will "
        "warm-start a future agent on recurring work.\n\n"
        f"{R11_STEERING_BLOCK}\n\n"
        "Extract EXACTLY these 8 fields:\n"
        f"{field_lines}\n\n"
        f"{R11_DEAD_ENDS_NORM}\n\n"
        "Output format: start with one short paragraph (2-4 sentences) "
        "summarizing the working state, then one `## <field name>` "
        "section per field, in the order above, using `- ` bullets. "
        "Omit a section's header ONLY if there is truly nothing to "
        "record for it. Reference artifacts by pointer; never mirror "
        "their content."
    )


# Module default so the persona is composed once at import (pure
# string assembly — no I/O). Kept as a module-level symbol (renamed
# from the prior ``build_snapshot_summarizer_persona`` factory to a
# private helper — NIT #8 / Wave 2a pre-step: the factory had no
# external caller, only the import-time cache, and the public name
# invited unintended reuse).
SNAPSHOT_SUMMARIZER_PERSONA = _compose_snapshot_summarizer_persona()

__all__ = [
    "SNAPSHOT_PROMPT_VERSION",
    "R11_STEERING_BLOCK",
    "DIGEST_EXTRACTION_FIELDS",
    "DIGEST_KEYS",
    "R11_DEAD_ENDS_NORM",
    "_compose_snapshot_summarizer_persona",
    "SNAPSHOT_SUMMARIZER_PERSONA",
]
