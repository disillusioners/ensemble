"""Host-capture middleware (Phase 2 of the live-view subsystem).

Reads the inbound ``Host`` and ``X-Forwarded-Proto`` headers and
writes them to ``app.state.host_recorder`` — the second-tier input
of the URL base-resolution chain (see
``daemon.services.live_views.BaseURLResolver``). Last-write-wins
semantics: the most recent request's host becomes the next minted
URL base. Syntactic validation lives in
``daemon.services.live_views.HostRecorder.record``; this
middleware is intentionally a thin pass-through (no failure path;
a bad Host is dropped at the recorder, never an error).

The middleware looks up the recorder via ``scope["app"]``
(Starlette 0.30+) rather than capturing it at construction
— ``app.add_middleware`` is invoked BEFORE the lifespan runs,
so the recorder does not yet exist at construction time.
Lookup at request time is the canonical pattern.

Module-level placement (in its own module, rather than
create_app()-local in ``daemon/api.py``) so tests and the smoke
harness can import the class directly. Wiring in create_app()
is unchanged: ``app.add_middleware(HostCaptureMiddleware)``
at the same site, same behavior.
"""


class HostCaptureMiddleware:
    """Capture inbound Host + X-Forwarded-Proto for URL minting."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            app_ref = scope.get("app")
            recorder = (
                getattr(app_ref.state, "host_recorder", None)
                if app_ref is not None
                else None
            )
            if recorder is not None:
                # ASGI headers are list[tuple[bytes, bytes]].
                # Decode once, lower-case keys, ignore any
                # malformed pair — the recorder drops bad
                # values; the middleware must NEVER raise.
                headers: dict[str, str] = {}
                for raw_k, raw_v in scope.get("headers", []):
                    try:
                        key = raw_k.decode("latin-1").lower()
                        val = raw_v.decode("latin-1")
                    except (UnicodeDecodeError, AttributeError):
                        continue
                    headers[key] = val
                host = headers.get("host")
                if host:
                    proto = headers.get("x-forwarded-proto")
                    recorder.record(host, proto)
        await self.app(scope, receive, send)
