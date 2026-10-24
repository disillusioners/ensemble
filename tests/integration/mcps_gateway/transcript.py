"""The centre-side frame TRANSCRIPT and the envelope VIOLATION RECORDER.

DAEMON-FREE BY CONSTRUCTION
---------------------------
This module imports NOTHING from ``daemon.*``.  It is pure stdlib: the
frames it records are captured by the ONE sanctioned importer
(``tls_harness.py``), which taps the transport and the service in-process
and hands the raw text over.  Keeping this module daemon-free is what
makes the findings of the interop lane attributable to the WIRE and not
to the centre's internals.

WHAT IT OWNS
------------
* :class:`Frame` — one frame that crossed the centre's WebSocket, in
  order, with the parsed envelope (when it parses).
* :class:`Transcript` — the ordered frame log with the selectors the
  assertions need (``find`` / ``wait_for`` / ``sent`` / ``received``).
* :class:`ViolationRecorder` — the envelope deviations of §6.1, RECORDED
  and NEVER RAISED.

THE RECORDER NEVER RAISES
--------------------------
A deviation is a FINDING, not a crash: the gate has to finish its run and
report every deviation it saw.  ``observe`` therefore appends to
``violations`` and returns; it never raises and never asserts.  The
assertions decide what to DO with a deviation.

THE DEVIATIONS IT WATCHES
-------------------------
============================  ================================================
``missing_v``                 a frame without the ``v`` member (§6.1: it is
                              REQUIRED).
``wrong_v``                   a frame whose ``v`` is not the protocol
                              version.
``missing_id``                a ``response``/``error`` frame without the
                              ``id`` member (§6.1: it is required on
                              request/response/error).
``uncorrelatable_id``         a reply whose ``id`` matches no request the
                              centre sent (§6.1: the responder MUST echo
                              the exact id).
``session_id_mismatch``       the ``session_id`` of the envelope and the
                              one repeated inside ``result`` differ
                              (§6.4: the two MUST match).
============================  ================================================
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

#: The protocol version of §6.1 (``"v"``).
PROTOCOL_VERSION = "0.1"

#: The envelope types of §6.1.
TYPE_REQUEST = "request"
TYPE_RESPONSE = "response"
TYPE_ERROR = "error"
TYPE_NOTIFICATION = "notification"


@dataclass
class Frame:
    """One frame that crossed the centre's WebSocket."""

    index: int
    direction: str  # "send" (centre -> machine) | "recv" (machine -> centre)
    raw: str
    at: float
    parsed: dict[str, Any] | None = None

    # ── envelope accessors (never raise on a malformed frame) ──────────

    @property
    def type(self) -> str:
        return str((self.parsed or {}).get("type") or "")

    @property
    def method(self) -> str:
        return str((self.parsed or {}).get("method") or "")

    @property
    def id(self) -> Any:
        return (self.parsed or {}).get("id")

    @property
    def session_id(self) -> str:
        return str((self.parsed or {}).get("session_id") or "")

    @property
    def result(self) -> dict[str, Any]:
        result = (self.parsed or {}).get("result")
        return result if isinstance(result, dict) else {}

    @property
    def error(self) -> dict[str, Any]:
        error = (self.parsed or {}).get("error")
        return error if isinstance(error, dict) else {}

    @property
    def error_code(self) -> str:
        return str(self.error.get("code") or "")

    def __str__(self) -> str:  # pragma: no cover - transcript rendering
        return f"{self.index:03d} {self.direction:4} {self.raw}"


class Transcript:
    """Every frame that crossed the centre's WebSocket, in order."""

    def __init__(self) -> None:
        self._frames: list[Frame] = []

    # ── recording ──────────────────────────────────────────────────────

    def add(self, direction: str, raw: str) -> Frame:
        """Record one frame (the raw text is kept even if it is not JSON)."""
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                parsed = None
        except Exception:  # noqa: BLE001 - a non-JSON frame is recorded raw
            parsed = None
        frame = Frame(
            index=len(self._frames),
            direction=direction,
            raw=raw,
            at=time.monotonic(),
            parsed=parsed,
        )
        self._frames.append(frame)
        return frame

    # ── reading ────────────────────────────────────────────────────────

    @property
    def frames(self) -> list[Frame]:
        return list(self._frames)

    def sent(self) -> list[Frame]:
        return [f for f in self._frames if f.direction == "send"]

    def received(self) -> list[Frame]:
        return [f for f in self._frames if f.direction == "recv"]

    def find(
        self,
        *,
        direction: str | None = None,
        method: str | None = None,
        type: str | None = None,
        error_code: str | None = None,
        session_id: str | None = None,
    ) -> list[Frame]:
        """Every frame that matches the given selectors."""
        out: list[Frame] = []
        for frame in self._frames:
            if direction is not None and frame.direction != direction:
                continue
            if method is not None and frame.method != method:
                continue
            if type is not None and frame.type != type:
                continue
            if error_code is not None and frame.error_code != error_code:
                continue
            if session_id is not None and frame.session_id != session_id:
                continue
            out.append(frame)
        return out

    def methods_sent(self) -> list[str]:
        """The methods of every REQUEST the centre sent, in order."""
        return [f.method for f in self.sent() if f.type == TYPE_REQUEST]

    def request_ids_sent(self) -> set[str]:
        """The ids of every request the centre sent (the correlation set)."""
        return {
            str(f.id)
            for f in self.sent()
            if f.type == TYPE_REQUEST and f.id is not None
        }

    def errors(self) -> list[Frame]:
        return [f for f in self._frames if f.type == TYPE_ERROR]

    def render(self, limit: int = 60) -> str:
        """The last ``limit`` frames, one per line."""
        return "\n".join(str(f) for f in self._frames[-limit:])

    async def wait_for(
        self,
        predicate: Callable[[Frame], bool],
        *,
        timeout: float = 20.0,
        poll: float = 0.02,
    ) -> Frame | None:
        """Wait for a frame that satisfies ``predicate`` (or ``None``)."""
        import asyncio

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self._frames:
                if predicate(frame):
                    return frame
            await asyncio.sleep(poll)
        for frame in self._frames:
            if predicate(frame):
                return frame
        return None


@dataclass
class Violation:
    """One recorded envelope deviation."""

    kind: str
    detail: str
    raw: str
    at: float = field(default_factory=time.monotonic)

    def __str__(self) -> str:  # pragma: no cover - report rendering
        return f"[{self.kind}] {self.detail} — frame: {self.raw}"


class ViolationRecorder:
    """The §6.1 envelope deviations — RECORDED, NEVER RAISED.

    The recorder is fed the frames of the transcript.  It knows which
    request ids the centre sent, so it can tell an uncorrelatable reply
    from a correlated one.
    """

    def __init__(self, *, protocol_version: str = PROTOCOL_VERSION) -> None:
        self.protocol_version = protocol_version
        self.violations: list[Violation] = []
        self._known_request_ids: set[str] = set()

    # ── the id set ─────────────────────────────────────────────────────

    def note_request_id(self, request_id: Any) -> None:
        """Register an id the centre sent (the correlation set grows)."""
        if request_id is not None:
            self._known_request_ids.add(str(request_id))

    def known_request_ids(self) -> set[str]:
        return set(self._known_request_ids)

    # ── recording ──────────────────────────────────────────────────────

    def _record(self, kind: str, detail: str, raw: str) -> None:
        self.violations.append(Violation(kind=kind, detail=detail, raw=raw))

    def observe(self, frame: Frame) -> None:
        """Observe one frame and record every deviation it carries."""
        if frame.parsed is None:
            # A frame that is not a JSON object is not a §6.1 envelope at
            # all — recorded as its own kind so it is visible.
            self._record("unparsable_frame", "the frame is not a JSON object", frame.raw)
            return
        if "v" not in frame.parsed:
            self._record("missing_v", "the frame carries no 'v' member", frame.raw)
        elif frame.parsed.get("v") != self.protocol_version:
            self._record(
                "wrong_v",
                f"the frame carries v={frame.parsed.get('v')!r} (expected {self.protocol_version!r})",
                frame.raw,
            )
        frame_type = frame.type
        if frame_type == TYPE_REQUEST:
            self.note_request_id(frame.id)
            if frame.id is None:
                self._record("missing_id", "a request frame carries no 'id'", frame.raw)
            return
        if frame_type in (TYPE_RESPONSE, TYPE_ERROR):
            if frame.id is None:
                self._record(
                    "missing_id",
                    f"a {frame_type} frame carries no 'id'",
                    frame.raw,
                )
            elif (
                self._known_request_ids
                and str(frame.id) not in self._known_request_ids
            ):
                self._record(
                    "uncorrelatable_id",
                    f"the reply carries the id {frame.id!r}, which matches no request "
                    f"the centre sent",
                    frame.raw,
                )
        # §6.4: the session_id of the envelope is the canonical one; the
        # copy inside the result MUST be the same value.
        result = frame.result
        if result:
            inner = result.get("session_id")
            if isinstance(inner, str) and inner and frame.session_id and inner != frame.session_id:
                self._record(
                    "session_id_mismatch",
                    f"the envelope carries session_id={frame.session_id!r} but the "
                    f"result carries {inner!r}",
                    frame.raw,
                )

    def observe_all(self, frames: Iterable[Frame]) -> None:
        for frame in frames:
            self.observe(frame)

    # ── reporting ──────────────────────────────────────────────────────

    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for violation in self.violations:
            counts[violation.kind] = counts.get(violation.kind, 0) + 1
        return counts

    def summary(self) -> str:
        if not self.violations:
            return "no envelope deviation recorded"
        return "; ".join(
            f"{kind} x{count}" for kind, count in sorted(self.kinds().items())
        )

    def render(self, limit: int = 40) -> str:
        return "\n".join(str(v) for v in self.violations[:limit])


__all__ = [
    "Frame",
    "PROTOCOL_VERSION",
    "TYPE_ERROR",
    "TYPE_NOTIFICATION",
    "TYPE_REQUEST",
    "TYPE_RESPONSE",
    "Transcript",
    "Violation",
    "ViolationRecorder",
]
