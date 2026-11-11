"""The ONE shared canonical-render helper (prompt-assent, J-3 / K-7 — task 1.10).

Single render truth for the conversational approval lane:

* the REST diff route (``daemon/routers/prompts.py``) imports
  :func:`unified_diff` — a PURE LIFT-AND-SHIFT of the former
  router-local ``_unified_diff`` (stdlib ``difflib``, D12: no new dep) —
  so the FE diff output is byte-identical to the pre-change output
  (pinned by test);
* the mint path (P2 tool / P3 service) renders through
  :func:`canonical_render` — the **named canonical render**: the render
  format tag ``v1``, the item key, the labels, the FULL pending content,
  the FULL effective content, and the unified diff between them. The
  token's ``render_sha256`` is ``sha256`` of EXACTLY these bytes — the
  bytes placed in the mint response (``render_sha256`` helper) — and the
  D-4 hook re-checks the same bytes inside the guarded transition
  (O-L, the only render-hash site).

The tool layer must NEVER import ``daemon.routers.prompts`` — this
module is the one owner of the render (pinned by test). Embedding the
FULL contents (not just the diff) closes the invisible-mutation hole:
a unified diff only shows ±3 context lines, so a mutation far from any
change would leave the diff bytes identical while the approved content
actually differs — the render binds the WHOLE pending + effective
bodies, so ANY mutation on either side moves the hash and invalidates
the outstanding token (``token-content-mismatch``).

The render format tag (``RENDER_FORMAT_TAG = "v1"``) is part of the
hashed bytes: a format change is a deliberate, visible breaking change
(every outstanding token invalidates — fail-closed).
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Optional

#: The render format tag — part of the hashed bytes (a tag change moves
#: every render_sha256 by construction; that is the point).
RENDER_FORMAT_TAG = "v1"

#: The render family prefix (carries the tag; asserted in tests).
RENDER_FAMILY = "prompt-assent/canonical-render"


def unified_diff(
    content_a: str, content_b: str, label_a: str, label_b: str
) -> str:
    """``difflib.unified_diff`` text join (D12 — stdlib, no new dep).

    PURE LIFT-AND-SHIFT of the router-local ``_unified_diff`` — the FE
    diff route imports THIS function; its output is byte-identical to
    the pre-change output (pinned by test).
    """
    a_lines = content_a.splitlines(keepends=True)
    b_lines = content_b.splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines, b_lines, fromfile=label_a, tofile=label_b, lineterm=""
    )
    return "\n".join(diff)


def canonical_render(
    *,
    agent_id: str,
    version_tag: str,
    artifact: str,
    version: int,
    version_id: str,
    pending_content: str,
    effective_content: str,
    effective_version_id: Optional[str],
) -> str:
    """Assemble the NAMED canonical render — the mint's exact bytes.

    Deterministic envelope: format tag + item key + labels + the FULL
    effective content + the FULL pending content + the unified diff
    (effective as ``a``, pending as ``b`` — the same orientation the FE
    diff route uses). Any mutation on either side, any item-key change,
    or any format-tag change moves the hash.
    """
    eff_label = f"effective/{effective_version_id}" if effective_version_id else "effective/none"
    pend_label = f"pending/{version_id}"
    header = "\n".join([
        f"{RENDER_FAMILY}/{RENDER_FORMAT_TAG}",
        f"item: {agent_id}/{version_tag}/{artifact}",
        f"version: {version}",
        f"pending-version-id: {version_id}",
        f"effective-version-id: {effective_version_id or 'none'}",
    ])
    diff = unified_diff(
        effective_content or "",
        pending_content or "",
        eff_label,
        pend_label,
    )
    return "\n".join([
        header,
        "--- effective content ---",
        effective_content or "",
        "--- pending content ---",
        pending_content or "",
        "--- unified diff ---",
        diff,
        f"end {RENDER_FAMILY}/{RENDER_FORMAT_TAG}",
    ])


def render_sha256(
    *,
    agent_id: str,
    version_tag: str,
    artifact: str,
    version: int,
    version_id: str,
    pending_content: str,
    effective_content: str,
    effective_version_id: Optional[str],
) -> str:
    """``sha256`` of :func:`canonical_render` — the mint-side hash rule.

    Computed at mint FROM THE EXACT BYTES PLACED IN THE MINT RESPONSE
    (J-3/K-7): the mint path calls this with the same keyword arguments
    it renders, so hashed == shown == re-checked-at-approve.
    """
    rendered = canonical_render(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact=artifact,
        version=version,
        version_id=version_id,
        pending_content=pending_content,
        effective_content=effective_content,
        effective_version_id=effective_version_id,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
