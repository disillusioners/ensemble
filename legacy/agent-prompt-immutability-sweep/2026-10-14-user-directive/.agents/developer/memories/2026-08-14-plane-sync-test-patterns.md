# Plane Project Sync Test Patterns

## Summary
Comprehensive unit test patterns for the Plane project sync subsystem (HTTP client + service + tool).

## Key Patterns

### 1. httpx.MockTransport injection via monkeypatch
The Plane client creates `httpx.AsyncClient(timeout=...)` fresh per call inside `_request`. To inject a mock transport, monkey-patch `daemon.clients.plane_http_client.httpx.AsyncClient` so it forwards to a *real* `httpx.AsyncClient(transport=mock_transport, ...)`. This keeps the real async/await semantics while substituting the network layer. See `_patched_async_client` in `tests/unit/test_plane_sync.py`.

### 2. SQLite in-memory repo + StaticPool for service tests
```python
eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_ = (Project, ProjectMetadataRecord, ProjectShortnameLink)  # force model registration
SQLModel.metadata.create_all(eng)
return SQLModelProjectRepository(eng)
```
StaticPool keeps the in-memory DB alive across threads.

### 3. CR-6 metadata contract
`sync_project` reads metadata via `list_metadata_records(project_id)` once and filters client-side — NOT `get_metadata(key)` per key. This avoids the 3-roundtrip pattern. The `_set_metadata` writes go through `set_metadata_record` (low-level), not `set_metadata` (which also rewrites `Project.updated_at`).

### 4. Circuit breaker isolation
The module-level `_plane_breaker` is shared. Inject a per-test `CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)` via the `breaker=` constructor arg to avoid cross-test pollution.

### 5. Module-level cooldown state
`_last_sync` is module-level dict. The `clear_cooldown` fixture calls `_last_sync.clear()` on both setup and teardown to prevent test bleed.

## File
- `tests/unit/test_plane_sync.py` — 74 tests across 14 test classes
