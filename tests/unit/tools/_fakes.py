"""Shared snapshot-test fakes (R18 test surface, deduplicated 2026-10-04).

The R18 auto-dispatch contract surfaces ``AsyncMessageResult`` from
``daemon.services.instance_messaging.enqueue_message``. The real type
lives in ``daemon.services.instance_messaging`` and is heavy to import,
so the Wave-2b / Wave-3 / spot-check test suites each carried their
own minimal stand-in. With three verbatim copies drifted by the
default ``message_id``, the hygiene commit extracted the shared
shape here so all suites import the same class.
"""

from __future__ import annotations


class FakeAsyncMessageResult:
    """Minimal stand-in for ``AsyncMessageResult`` (R18 test surface).

    Mirrors the real dataclass field shape (and defaults) that the R18
    auto-dispatch path now inspects: ``message_id`` (populated by the
    daemon) + ``status`` (hardcoded 'queued' on the only return site,
    but default to 'queued' here so success-path tests do not trip the
    tripwire) + ``queued`` (capacity flag, defaults to True for happy
    path). Tests can override any field via the constructor to exercise
    the non-queued / None / capacity-saturated branches the tripwire is
    designed to catch.
    """

    def __init__(
        self,
        message_id: str = "msg-auto-1",
        queued: bool = True,
        status: str = "queued",
    ) -> None:
        self.message_id = message_id
        self.queued = queued
        self.status = status