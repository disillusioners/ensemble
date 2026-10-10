"""ASGI middleware package for the Ensemble daemon.

Each submodule is a self-contained, module-level middleware class
wired in ``daemon/api.py:create_app`` via ``app.add_middleware``.
House growth policy (M10 hygiene, 2026-10-10): new self-contained
middleware belongs HERE, not in ``daemon/api.py`` — api.py keeps
only the wiring (and create_app()-local middlewares that need
closure over factory state).

- ``host_capture`` — captures the inbound ``Host`` +
  ``X-Forwarded-Proto`` headers into ``app.state.host_recorder``
  as the second-tier input of the live-view URL base-resolution
  chain (see ``daemon.services.live_views.BaseURLResolver``).
"""
