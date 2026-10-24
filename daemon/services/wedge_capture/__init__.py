"""The wedge-capture instrument package (D1 / D2 / A8 / RK-20).

THIS ``__init__`` IS INTENTIONALLY **EMPTY** (a docstring and nothing
else).

The AutoContinue boot-pass envelope imports the boot-record module
INSIDE a ``try:`` whose ``except`` only WARNS (``daemon/api.py``
around the boot pass). If this package's ``__init__`` imported
anything heavy — ``psycopg``, ``sqlmodel``, ``daemon.config`` — a
failure to import it would silently disable **BOTH** auto-continue
passes: a settled-item violation (A8, RK-20).

Therefore:

* this module imports NOTHING;
* consumers import the SUBMODULES directly
  (``from daemon.services.wedge_capture.config import
  WedgeCaptureConfig``), never the package's public surface;
* ``daemon/services/wedge_capture/boot_record.py`` is stdlib-only for
  the same reason.

A pack asserts both properties (AC12 / AC13).
"""
