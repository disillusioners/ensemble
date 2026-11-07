"""Project Operating Knowledge (POK) — repository package.

Phase 1 core surface of the project-operating-knowledge commission
(Emperor GO 2026-11-07). This package holds the SQLModel pair
(``.models`` — §4.1 verbatim) and the engine-passed repository
(``.repository``); it deliberately re-exports NOTHING — consumers
import from the submodules, so the write-path fence/budget logic and
the per-view injection admission stay in the service layer
(``daemon/services/pok_service.py`` / ``daemon/services/pok_injection.py``).
"""
