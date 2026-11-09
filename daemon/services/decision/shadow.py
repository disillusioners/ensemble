"""Decision module — the shadow writer (``controls-and-phasing.md`` §2.6).

The shadow log is the **A/B record** required by D3: a structured
daemon-log row (``event=decision_overlay_shadow``), one row per
consult, written next to the heuristic's answer.

**CONTENT-FREE BY CONSTRUCTION.** Allowed: ids, hashes, scores,
probabilities, counts, latencies, enum labels. NEVER: credentials, any
part of an auth header, the request payload, the raw response body, or
raw user content. The sharp edge is S1 — the stage-3 prompt carries
snapshot **titles, tags and summaries** — so every string that reaches
a row passes through :func:`sanitize_token`, which replaces anything
that does not look like an opaque token with its sha256 prefix.

**A WRITER FAILURE NEVER BLOCKS.** If the writer raises — sink
unavailable, serialization error, disk full — the failure is COUNTED
(``decision_overlay_shadow_write_failures_total``) and SWALLOWED. The
writer sits on the failure-tolerant side of the fence: it may lose a
row, it may **never** fail a caller.

**RETENTION** is bounded on two axes: the daemon log's own rotation
(``RotatingFileHandler`` on ``ensemble.log``, 10 MB) PLUS an explicit
**row-count cap per seam per window** enforced here (a rotating file
has no row-count truncation of its own). Dropped rows are counted in
``decision_overlay_shadow_rows_dropped_total``.

**A non-agent-readable sink is a NAMED FUTURE (v2)** — this module does
NOT pretend one exists.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from daemon.services.decision.types import (
    VERDICT_NO_OVERLAY,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_ROWS_PER_SEAM_PER_WINDOW",
    "DEFAULT_WINDOW_SECONDS",
    "ShadowRow",
    "ShadowWriter",
    "assemble_id_only_row",
    "content_hash",
    "sanitize_token",
]


#: The per-seam, per-window row cap (§2.8 — the writer's own bound).
DEFAULT_MAX_ROWS_PER_SEAM_PER_WINDOW = 1000

#: The retention window for the row cap.
DEFAULT_WINDOW_SECONDS = 3600.0

#: The structured row's event name — the greppable handle.
EVENT_NAME = "decision_overlay_shadow"

#: The daemon log's structured row format (``event=... key=value ...``),
#: mirroring the house convention (``daemon/services/attestation_gate.py``
#: ``event=leader_completion_gate``).
_ROW_FORMAT = (
    "event={event} ts={ts} seam_id={seam_id} question_id={question_id} "
    "mode={mode} overlay_outcome={overlay_outcome} "
    "heuristic_outcome={heuristic_outcome} verdict={verdict} "
    "latency_ms={latency_ms} failure_class={failure_class} "
    "model_id={model_id} reason={reason} switch_state={switch_state}"
)


def sanitize_token(value: str) -> str:
    """Return an opaque token, or a sha256 prefix when the value is prose.

    A value that looks like an opaque token (short, no whitespace) is
    kept verbatim — snapshot ids, seam ids and enum labels pass through
    unchanged. Anything else (a title, a summary, a prompt fragment) is
    replaced by ``sha256:<16 hex>`` so the CONTENT never reaches the
    row.
    """
    text = value.strip()
    if text and len(text) <= 64 and not any(ch.isspace() for ch in text):
        return text
    return "sha256:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class ShadowRow:
    """One content-free A/B row.

    Attributes:
        seam_id: The seam identifier.
        question_id: An opaque, non-content question id.
        mode: ``shadow`` | ``deciding``.
        overlay_outcome: The overlay's answer, summarized (ids/scores).
        heuristic_outcome: The heuristic's answer, summarized.
        verdict: ``agree`` | ``disagree`` | ``no_overlay``.
        latency_ms: The overlay call's latency.
        failure_class: The failure label (empty on success).
        model_id: The configured purpose-id (never the provider).
        reason: The suppression reason (set on a suppressed row).
        switch_state: The switch state that permitted (or blocked) the
            consult.
    """

    seam_id: str
    question_id: str
    mode: str
    overlay_outcome: Mapping[str, Any] | None = None
    heuristic_outcome: Mapping[str, Any] | None = None
    verdict: str = VERDICT_NO_OVERLAY
    latency_ms: int = 0
    failure_class: str = ""
    model_id: str = ""
    reason: str = ""
    switch_state: str = ""


class ShadowWriter:
    """Write the content-free shadow rows.

    Args:
        sink: The row sink — a callable receiving the rendered row. The
            default writes a structured daemon-log row.
        max_rows_per_seam_per_window: The writer's own row cap (§2.8).
        window_seconds: The retention window for that cap.
        clock: Monotonic clock seam (the window boundary).
    """

    def __init__(
        self,
        *,
        sink: Callable[[str], None] | None = None,
        max_rows_per_seam_per_window: int = DEFAULT_MAX_ROWS_PER_SEAM_PER_WINDOW,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._max_rows = max(0, int(max_rows_per_seam_per_window))
        self._window = max(1.0, float(window_seconds))
        self._clock = clock
        self._counts: dict[str, tuple[float, int]] = {}
        self._write_failures = 0
        self._rows_dropped = 0

    # ── public surface ──────────────────────────────────────────────────

    def write(self, row: ShadowRow) -> bool:
        """Write one row. NEVER raises; never fails a caller.

        Returns:
            True when the row was written; False when it was dropped by
            the row cap or when the writer failed (both are COUNTED).
        """
        try:
            if not self._admit(row.seam_id):
                self._rows_dropped += 1
                self._count_drop(row.seam_id)
                return False
            rendered = self._render(row)
            sink = self._sink
            if sink is None:
                logger.info(rendered)
            else:
                sink(rendered)
            return True
        except Exception as exc:  # noqa: BLE001 — the writer never fails a caller
            self._write_failures += 1
            self._count_write_failure()
            logger.debug(
                "decision shadow write failed (non-fatal): %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    def write_many(self, rows: Sequence[ShadowRow]) -> int:
        """Write several rows; returns the number actually written."""
        return sum(1 for row in rows if self.write(row))

    # ── counters (test/diagnostic helpers) ──────────────────────────────

    @property
    def write_failures(self) -> int:
        """How many writes failed (counted, swallowed)."""
        return self._write_failures

    @property
    def rows_dropped(self) -> int:
        """How many rows the row cap dropped."""
        return self._rows_dropped

    # ── internals ───────────────────────────────────────────────────────

    def _admit(self, seam_id: str) -> bool:
        """Apply the per-seam, per-window row cap."""
        if self._max_rows <= 0:
            return False
        now = self._clock()
        window_start, count = self._counts.get(seam_id, (now, 0))
        if now - window_start >= self._window:
            window_start, count = now, 0
        if count >= self._max_rows:
            self._counts[seam_id] = (window_start, count)
            return False
        self._counts[seam_id] = (window_start, count + 1)
        return True

    def _render(self, row: ShadowRow) -> str:
        """Render the structured row (content-free)."""
        return _ROW_FORMAT.format(
            event=EVENT_NAME,
            ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            seam_id=sanitize_token(row.seam_id),
            question_id=sanitize_token(row.question_id),
            mode=sanitize_token(row.mode),
            overlay_outcome=_render_summary(row.overlay_outcome),
            heuristic_outcome=_render_summary(row.heuristic_outcome),
            verdict=sanitize_token(row.verdict),
            latency_ms=int(row.latency_ms),
            failure_class=sanitize_token(row.failure_class),
            model_id=sanitize_token(row.model_id),
            reason=sanitize_token(row.reason),
            switch_state=sanitize_token(row.switch_state),
        )

    @staticmethod
    def _count_drop(seam_id: str) -> None:
        """Count a dropped row in the metrics registry."""
        from daemon.services.decision import metrics

        metrics.record_shadow_row_dropped(seam_id)

    @staticmethod
    def _count_write_failure() -> None:
        """Count a writer failure in the metrics registry."""
        from daemon.services.decision import metrics

        metrics.record_shadow_write_failure()


def _render_summary(summary: Mapping[str, Any] | None) -> str:
    """Render a content-free summary as a compact ``k=v`` string."""
    if not summary:
        return "-"
    parts: list[str] = []
    for key, value in summary.items():
        if isinstance(value, bool):
            rendered = "1" if value else "0"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = sanitize_token(value)
        elif isinstance(value, Mapping):
            rendered = _render_summary(value)
        elif isinstance(value, (list, tuple)):
            rendered = ",".join(sanitize_token(str(item)) for item in value)
        else:
            rendered = sanitize_token(str(value))
        parts.append(f"{key}={rendered}")
    return ";".join(parts) if parts else "-"


# ── the id-only row (assembled BY HAND) ─────────────────────────────────


def content_hash(ids: Sequence[str]) -> str:
    """A stable, opaque hash of an id SET.

    There is NO content hash available in the snapshot subsystem (the
    only sha256 there is the capture idempotency key, derived from the
    transcript and not persisted on the row; ``git_sha`` is a repo
    anchor, not a content hash), so the row computes its own — over
    the SELECTED IDS, never over the content they were derived from.
    """
    payload = ",".join(sorted(str(i) for i in ids or ()))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


def assemble_id_only_row(
    *,
    seam_id: str,
    question_id: str,
    mode: str,
    candidate_ids: Sequence[str],
    selected_ids: Sequence[str],
    heuristic_ids: Sequence[str],
    verdict: str = "no_overlay",
    latency_ms: int = 0,
    model_id: str = "",
) -> ShadowRow:
    """Assemble the id-only shadow row BY HAND.

    The row is built field by field — there is NO generic dump anywhere
    in this path. The ids are validated against the candidate set first
    (``intersect_selection``), so an unknown id (which
    ``_parse_selection`` accepts) can never reach the row.

    Attributes:
        candidate_ids: The candidate set (the validated universe).
        selected_ids: The overlay's selection — INTERSECTED with the
            candidate set before it is written.
        heuristic_ids: The heuristic's selection — intersected too.
    """
    from daemon.services.decision.port import intersect_selection

    validated_selected = intersect_selection(selected_ids, candidate_ids)
    validated_heuristic = intersect_selection(heuristic_ids, candidate_ids)
    overlay: dict[str, Any] = {
        "kind": "selection",
        "ids": list(validated_selected),
        "count": len(validated_selected),
        "hash": content_hash(validated_selected),
    }
    heuristic: dict[str, Any] = {
        "kind": "selection",
        "ids": list(validated_heuristic),
        "count": len(validated_heuristic),
        "hash": content_hash(validated_heuristic),
    }
    return ShadowRow(
        seam_id=seam_id,
        question_id=question_id,
        mode=mode,
        overlay_outcome=overlay,
        heuristic_outcome=heuristic,
        verdict=verdict,
        latency_ms=latency_ms,
        failure_class="",
        model_id=model_id,
        reason="",
        switch_state="on",
    )
