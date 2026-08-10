"""End-to-end integration tests for the shared_meta_kv system.

Historical note: this module previously contained four tests that exercised
the legacy ``system_prompt`` and ``message_body`` injection helpers in
``daemon.services.instance_lifecycle``. Both helpers were deleted when the
legacy context-injection mode was removed; only the ``human_messages`` mode
remains (context is rebuilt each turn as a ``[SYSTEM CONTEXT: ...]``
HumanMessage via :func:`assemble_context_messages`).

With the helpers gone, there is nothing left to exercise here end-to-end at
this layer — the e2e coverage of shared-context KV plumbing now lives
entirely in the unit/integration suites for the ``human_messages`` path
(see ``tests/unit/test_context_messages.py`` and the related integration
files). The file is retained as a stub so existing pytest collection
references stay stable.

The ``integration`` marker is preserved so the default ``pytest`` gate
(``addopts = "-m 'not integration and not postgres'"``) keeps skipping it.
"""

from __future__ import annotations

import os

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="Set OPENAI_API_KEY to run shared_meta_kv integration tests",
    ),
]