"""Shared fixtures + service-availability guard for integration tests.

Testing strategy (team policy): infra-dependent tests must NOT be silently
skipped in CI, because "skipping masks real infrastructure problems". But a
developer running the suite locally without Docker should still get a green
run instead of a wall of ERRORs.

This module implements that policy:

* Locally (no ``CI`` / ``PLOSTON_REQUIRE_SERVICES``), if the required service
  is unreachable, the test is ``pytest.skip``-ped.
* In CI (``CI`` env var truthy) or when ``PLOSTON_REQUIRE_SERVICES=1`` is set,
  an unreachable service is a hard ``pytest.fail`` — the infrastructure problem
  is surfaced rather than hidden.

The guard is wired up as an autouse fixture keyed off pytest markers. Tests
that need a Docker daemon (the ClickHouse suites, which use testcontainers)
carry ``@pytest.mark.docker``; the guard probes Docker for those and leaves
non-Docker integration tests untouched. The ``docker`` marker is registered
here in ``pytest_configure`` so no top-level config change is required.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from urllib.parse import urlparse

import pytest

# Fast probes — we only want "is the service reachable?", never a long hang.
_CONNECT_TIMEOUT_S = 2.0

_TRUTHY = {"1", "true", "yes", "on"}


def _require_services() -> bool:
    """True when an unreachable service should FAIL rather than skip.

    Triggered by a truthy ``CI`` env var (set by virtually every CI provider)
    or by an explicit ``PLOSTON_REQUIRE_SERVICES`` opt-in.
    """
    ci = os.getenv("CI", "").strip().lower()
    require = os.getenv("PLOSTON_REQUIRE_SERVICES", "").strip().lower()
    return ci in _TRUTHY or require in _TRUTHY


def _unavailable(service: str, detail: str) -> None:
    """Skip locally, fail in CI, for an unreachable ``service``."""
    msg = f"{service} not available for integration test: {detail}"
    if _require_services():
        pytest.fail(
            f"{msg}\n"
            "Refusing to skip because CI / PLOSTON_REQUIRE_SERVICES is set "
            "(skipping would mask a real infrastructure problem)."
        )
    pytest.skip(msg)


@lru_cache(maxsize=1)
def _docker_probe() -> str | None:
    """Return ``None`` if a Docker daemon is reachable, else an error string.

    testcontainers needs a live Docker daemon to boot ClickHouse. We ping the
    daemon directly (cheap) rather than starting a container (expensive) so the
    guard fails fast.
    """
    try:
        import docker  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover - docker SDK always installed here
        return f"docker SDK import failed: {exc}"

    try:
        client = docker.from_env(timeout=int(_CONNECT_TIMEOUT_S))
        client.ping()
        client.close()
    except Exception as exc:
        return str(exc)
    return None


def _tcp_probe(host: str, port: int) -> str | None:
    """Return ``None`` if a TCP connect to ``host:port`` succeeds, else error."""
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S):
            return None
    except OSError as exc:
        return f"cannot connect to {host}:{port}: {exc}"


def require_docker() -> None:
    """Guard helper: ensure a Docker daemon is reachable (skip/fail otherwise)."""
    err = _docker_probe()
    if err is not None:
        _unavailable("Docker daemon", err)


def require_redis(url: str | None = None) -> None:
    """Guard helper: ensure Redis is reachable (skip/fail otherwise)."""
    url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    err = _tcp_probe(host, port)
    if err is not None:
        _unavailable("Redis", err)


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``docker`` marker so ``--strict-markers`` runs stay green."""
    config.addinivalue_line(
        "markers",
        "docker: integration test that requires a reachable Docker daemon "
        "(e.g. testcontainers-backed ClickHouse). Guarded by conftest: skips "
        "locally when Docker is down, fails in CI / PLOSTON_REQUIRE_SERVICES.",
    )
    config.addinivalue_line(
        "markers",
        "redis: integration test that requires a reachable Redis instance. "
        "Guarded by conftest with the same skip-local / fail-CI policy.",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Probe required services before *any* fixture setup runs.

    Implemented as a hook rather than an autouse fixture so it fires earlier
    than the module-scoped ``testcontainers`` fixtures the tests depend on —
    a function-scoped autouse fixture resolves *after* higher-scoped fixtures
    requested by the test, which would let the Docker connection error escape
    as a setup ERROR before the guard ever ran.

    Keyed off per-test markers so it only fires for tests that declare a
    dependency, turning an unreachable service into a clean skip (local) /
    fail (CI).
    """
    if item.get_closest_marker("docker") is not None:
        require_docker()
    if item.get_closest_marker("redis") is not None:
        require_redis()
