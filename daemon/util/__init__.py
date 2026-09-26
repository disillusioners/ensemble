"""Internal helpers used by the daemon.

This package is distinct from the existing top-level ``daemon.utils``
module (``utils.py``) — that module carries the historical
fastapi-flavoured utilities and is imported by routers / lifespan code.
The ``daemon.util`` package is reserved for internal helpers that are
loaded eagerly at ``daemon`` import time and need a stable package
home (not a single-file module). New helpers should prefer
``daemon.util`` over extending ``utils.py``.

Day-1 contents (P3-WP10):

* :mod:`daemon.util.log_redaction_filter` — the KMS logging redaction
  filter installed at logger-configuration time.
"""