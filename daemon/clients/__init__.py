"""External HTTP clients for the agents-ensemble daemon.

Each submodule encapsulates an outbound integration:

- ``plane_http_client`` — REST client for the Plane (plane.so) project
  management API. Feature-gated on ``PLANE_BASE_URL`` / ``PLANE_MCP_API_KEY``
  / ``PLANE_MCP_WORKSPACE_SLUG``; absence of the env vars disables the
  integration cleanly (no DB record, no connection).

Clients follow the convention of constructing a fresh ``httpx.AsyncClient``
per call rather than holding a module-level singleton — this avoids the
event-loop binding hazard in async context managers.
"""