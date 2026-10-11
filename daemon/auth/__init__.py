"""OAuth2 auth core package — oauth2-auth Phase 3.

Request-path primitives shared by the middleware, the routes, and the
service layer (phase3-plan T3.1/T3.2/T3.4):

* :mod:`daemon.auth.context` — the :class:`AuthContext` identity shape
  (D3.6) attached at ``request.state.auth_context``.
* :mod:`daemon.auth.decorators` — the ``@require_scope`` policy surface
  (D3.7).
* :mod:`daemon.auth.cookie` — ``ensemble_sid`` / ``ensemble_csrf`` cookie
  helpers + the D8 v1.2 CSRF Origin-guard predicate.

Config lives in :class:`daemon.config.AuthConfig` (Phase 1); the store
and the provider client live in ``daemon.repositories.auth_session`` /
``daemon.services.oauth_client`` (Phase 2).
"""
