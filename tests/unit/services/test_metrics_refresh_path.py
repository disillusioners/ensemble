"""FIX ROUND 1 / F1 — the scrape path never blocks on a pooled checkout.

The scrape-path managed-key DB refresh was (dual-confirmed):

* **NOT single-flight** — 20 concurrent scrapes produced 20 provider
  calls (each stacked a pooled checkout);
* **PAID EVEN BY THE EMERGENCY OFF PATH** — the refresh ran even when
  ``ENSEMBLE_METRICS_ENABLED`` was explicitly set;
* **ON THE REQUEST THREAD** — a pooled checkout can block for tens of
  seconds under the Oct-26/28 pool-poisoning class, while Prometheus's
  ``scrape_timeout`` is 10 s: a blocked scrape is an ``up==0`` exactly
  when the data matters most.

These legs pin the fix:

* **(a)** an EXPLICIT env tier ⇒ ZERO provider calls (no DB touch at
  all — not on the request path, not on the refresher);
* **(b)** concurrent scrapes ⇒ exactly ONE refresh (the single-flight
  proof: N threads inside the TTL window ⇒ the provider is called
  once);
* **(c)** the request path never performs the read itself — the read
  happens on the refresher thread.
"""

from __future__ import annotations

import threading
import time

import pytest

from daemon.services import metrics_gate


class _CountingProvider:
    """A provider that COUNTS its calls (and can block)."""

    def __init__(self, values: dict | None = None, block_s: float = 0.0) -> None:
        self.values = dict(values or {})
        self.block_s = block_s
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> dict:
        with self._lock:
            self.calls += 1
        if self.block_s:
            time.sleep(self.block_s)
        return dict(self.values)


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ─── (a) the emergency-off path pays nothing ──────────────────────────────


@pytest.mark.parametrize("env_value", ["0", "false", "no", "1", "true", "yes"])
def test_an_explicit_env_tier_never_calls_the_provider(monkeypatch, env_value):
    """(a) The env tier wins — the managed chain is NOT read at all."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True})
    metrics_gate.set_managed_metrics_provider(provider)
    monkeypatch.setenv(metrics_gate.ENV_VAR, env_value)

    for _ in range(5):
        metrics_gate.request_managed_values_refresh()
    # The refresher thread (if it woke) must not read either.
    time.sleep(0.2)

    assert provider.calls == 0, "the explicit env path must not touch the DB"


def test_an_explicit_env_tier_makes_no_db_touch_through_the_listener(
    monkeypatch,
):
    """The LISTENER's request path is the emergency-off path — it must
    not trigger a provider call either."""
    from daemon.services.metrics_listener import MetricsListener

    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True})
    metrics_gate.set_managed_metrics_provider(provider)
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")

    listener = MetricsListener(port=0, gate_enabled=None)
    for _ in range(5):
        listener.build_response("/metrics", {})
    time.sleep(0.2)
    assert provider.calls == 0


# ─── (b) single-flight ────────────────────────────────────────────────────


def test_concurrent_scrapes_cause_exactly_one_refresh():
    """(b) N concurrent scrapes inside the TTL window ⇒ ONE provider
    call (the single-flight proof)."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True})
    metrics_gate.set_managed_metrics_provider(provider)

    threads_count = 20
    barrier = threading.Barrier(threads_count)

    def _scrape() -> None:
        barrier.wait()
        metrics_gate.request_managed_values_refresh()

    threads = [
        threading.Thread(target=_scrape) for _ in range(threads_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert _wait_for(lambda: provider.calls >= 1)
    # Give a late wake-up a chance to (wrongly) add another call.
    time.sleep(0.3)
    assert provider.calls == 1, (
        f"expected exactly ONE provider call, got {provider.calls}"
    )


def test_the_request_path_does_not_perform_the_read_itself():
    """(c) The request path only SIGNALS the refresher — the read
    happens on the refresher thread."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True})
    metrics_gate.set_managed_metrics_provider(provider)

    metrics_gate.request_managed_values_refresh()
    # The call returned immediately — the provider may not have been
    # called yet (the read is the refresher's job).
    assert _wait_for(lambda: provider.calls == 1)


def test_a_slow_provider_does_not_block_the_request_path():
    """A provider that blocks for a second must NOT delay the caller."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True}, block_s=1.0)
    metrics_gate.set_managed_metrics_provider(provider)

    started = time.perf_counter()
    metrics_gate.request_managed_values_refresh()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1, f"the request path waited {elapsed * 1000:.1f} ms"


def test_a_refresh_in_progress_is_never_stacked():
    """(b) The single-flight flag: a read in flight ⇒ the later caller
    gets the last-known values instead of stacking a second call."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True}, block_s=0.5)
    metrics_gate.set_managed_metrics_provider(provider)

    started = threading.Event()
    release = threading.Event()

    def _slow_provider() -> dict:
        with provider._lock:
            provider.calls += 1
        started.set()
        release.wait(timeout=5.0)
        return dict(provider.values)

    metrics_gate.set_managed_metrics_provider(_slow_provider)

    worker = threading.Thread(
        target=lambda: metrics_gate.refresh_managed_values(now=None, ttl_s=0.0)
    )
    worker.start()
    assert started.wait(timeout=5.0)

    # The read is in flight ⇒ the concurrent caller must NOT stack it.
    metrics_gate.refresh_managed_values(now=None, ttl_s=0.0)
    assert provider.calls == 1

    release.set()
    worker.join(timeout=5.0)


def test_the_refresh_is_ttl_bounded():
    """A scrape burst does not turn into a burst of DB reads."""
    provider = _CountingProvider({metrics_gate.MANAGED_KEY: True})
    metrics_gate.set_managed_metrics_provider(provider)

    metrics_gate.request_managed_values_refresh()
    assert _wait_for(lambda: provider.calls == 1)
    # Inside the TTL window ⇒ no new read is requested.
    for _ in range(10):
        metrics_gate.request_managed_values_refresh()
    time.sleep(0.3)
    assert provider.calls == 1
