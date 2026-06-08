"""SPEC tests for ploston_core.native_tools.utils.docker.

These assert the *intended* behavior of the Docker environment detection and
host/URL/Kafka resolution helpers, mocking the OS boundary
(os.path.exists / builtins.open / os.getenv) so the suite runs identically
inside and outside a container.

The module exposes pure functions; there is no subprocess or docker-SDK
boundary. is_running_in_docker() is memoized with lru_cache, so every test
that exercises it clears the cache first.
"""

from __future__ import annotations

import builtins
from io import StringIO
from unittest.mock import mock_open, patch

import pytest

from ploston_core.native_tools.utils import docker as docker_mod
from ploston_core.native_tools.utils.docker import (
    DOCKER_HOST_INTERNAL,
    LOCALHOST_VARIANTS,
    is_running_in_docker,
    resolve_host_for_docker,
    resolve_kafka_servers_for_docker,
    resolve_url_for_docker,
)


@pytest.fixture(autouse=True)
def _clear_docker_detection_cache():
    """is_running_in_docker is lru_cached; reset between every test."""
    is_running_in_docker.cache_clear()
    yield
    is_running_in_docker.cache_clear()


# --------------------------------------------------------------------------- #
# is_running_in_docker - detection methods
# --------------------------------------------------------------------------- #


def test_detects_docker_via_dockerenv_file():
    """Presence of /.dockerenv is sufficient to report Docker."""
    with patch.object(docker_mod.os.path, "exists", return_value=True):
        assert is_running_in_docker() is True


def test_detects_docker_via_proc1_cgroup_docker_signature():
    """A 'docker' signature in /proc/1/cgroup reports Docker."""
    with (
        patch.object(docker_mod.os.path, "exists", return_value=False),
        patch.object(
            builtins,
            "open",
            mock_open(read_data="12:cpuset:/docker/abc123"),
        ),
    ):
        assert is_running_in_docker() is True


def test_detects_docker_via_proc1_cgroup_containerd_signature():
    """A 'containerd' signature in /proc/1/cgroup also reports Docker."""
    with (
        patch.object(docker_mod.os.path, "exists", return_value=False),
        patch.object(
            builtins,
            "open",
            mock_open(read_data="0::/system.slice/containerd.service"),
        ),
    ):
        assert is_running_in_docker() is True


def test_detects_docker_via_env_var():
    """DOCKER_CONTAINER=1/true/yes is an explicit Docker marker."""
    for truthy in ("1", "true", "TRUE", "yes", "Yes"):
        is_running_in_docker.cache_clear()
        with (
            patch.object(docker_mod.os.path, "exists", return_value=False),
            patch.object(builtins, "open", side_effect=FileNotFoundError),
            patch.dict(docker_mod.os.environ, {"DOCKER_CONTAINER": truthy}, clear=False),
        ):
            assert is_running_in_docker() is True, f"value {truthy!r} should mark Docker"


def test_detects_docker_via_proc_self_cgroup_kubepods():
    """A 'kubepods' signature in /proc/self/cgroup reports Docker (k8s pod)."""
    contents = {
        "/proc/1/cgroup": "0::/init.scope",  # no docker signature
        "/proc/self/cgroup": "0::/kubepods/pod1234/abcd",
    }

    def fake_open(path, *args, **kwargs):
        return StringIO(contents[path])

    with (
        patch.object(docker_mod.os.path, "exists", return_value=False),
        patch.object(docker_mod.os, "getenv", return_value=""),
        patch.object(builtins, "open", side_effect=fake_open),
    ):
        assert is_running_in_docker() is True


def test_not_in_docker_when_all_signals_absent():
    """With no dockerenv, no cgroup signature, no env var -> not Docker."""
    with (
        patch.object(docker_mod.os.path, "exists", return_value=False),
        patch.object(docker_mod.os, "getenv", return_value=""),
        patch.object(
            builtins,
            "open",
            mock_open(read_data="0::/init.scope"),
        ),
    ):
        assert is_running_in_docker() is False


def test_cgroup_read_errors_are_swallowed_not_raised():
    """OSError/PermissionError reading cgroup must not propagate; -> not Docker."""
    with (
        patch.object(docker_mod.os.path, "exists", return_value=False),
        patch.object(docker_mod.os, "getenv", return_value=""),
        patch.object(builtins, "open", side_effect=PermissionError("denied")),
    ):
        # Must not raise.
        assert is_running_in_docker() is False


# --------------------------------------------------------------------------- #
# resolve_host_for_docker
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variant", sorted(LOCALHOST_VARIANTS))
def test_resolve_host_rewrites_localhost_variants_in_docker(variant):
    """Every localhost variant maps to host.docker.internal inside Docker."""
    assert resolve_host_for_docker(variant, force_docker=True) == DOCKER_HOST_INTERNAL


def test_resolve_host_is_case_and_whitespace_insensitive():
    """Host normalization should handle surrounding whitespace / case."""
    assert resolve_host_for_docker("  LocalHost  ", force_docker=True) == DOCKER_HOST_INTERNAL


def test_resolve_host_leaves_real_hostnames_untouched_in_docker():
    """Non-localhost hosts are returned unchanged even inside Docker."""
    assert resolve_host_for_docker("api.example.com", force_docker=True) == "api.example.com"


def test_resolve_host_is_noop_outside_docker():
    """Outside Docker, localhost is left alone."""
    assert resolve_host_for_docker("localhost", force_docker=False) == "localhost"


def test_resolve_host_uses_detection_when_force_is_none():
    """force_docker=None defers to is_running_in_docker()."""
    with patch.object(docker_mod, "is_running_in_docker", return_value=True):
        assert resolve_host_for_docker("localhost") == DOCKER_HOST_INTERNAL
    with patch.object(docker_mod, "is_running_in_docker", return_value=False):
        assert resolve_host_for_docker("localhost") == "localhost"


# --------------------------------------------------------------------------- #
# resolve_url_for_docker
# --------------------------------------------------------------------------- #


def test_resolve_url_rewrites_host_preserving_port_and_path():
    assert (
        resolve_url_for_docker("http://localhost:3002/api", force_docker=True)
        == "http://host.docker.internal:3002/api"
    )


def test_resolve_url_rewrites_ip_variant():
    assert (
        resolve_url_for_docker("http://127.0.0.1:9092/topic", force_docker=True)
        == "http://host.docker.internal:9092/topic"
    )


def test_resolve_url_preserves_userinfo():
    """username:password@ credentials must survive host rewrite."""
    assert (
        resolve_url_for_docker("http://user:pass@localhost:5432/db", force_docker=True)
        == "http://user:pass@host.docker.internal:5432/db"
    )


def test_resolve_url_preserves_username_only():
    assert (
        resolve_url_for_docker("http://user@localhost:5432/db", force_docker=True)
        == "http://user@host.docker.internal:5432/db"
    )


def test_resolve_url_leaves_real_host_unchanged():
    assert (
        resolve_url_for_docker("https://api.example.com/v1", force_docker=True)
        == "https://api.example.com/v1"
    )


def test_resolve_url_is_noop_outside_docker():
    assert (
        resolve_url_for_docker("http://localhost:3002", force_docker=False)
        == "http://localhost:3002"
    )


def test_resolve_url_rewrites_host_without_port():
    """A localhost URL with no explicit port rewrites the host alone."""
    assert (
        resolve_url_for_docker("http://localhost/api", force_docker=True)
        == "http://host.docker.internal/api"
    )


def test_resolve_url_without_host_component_returned_unchanged():
    """A string with no parseable host is returned as-is."""
    assert resolve_url_for_docker("not-a-url", force_docker=True) == "not-a-url"


def test_resolve_url_falls_back_to_original_on_parse_failure():
    """If URL processing raises, the original string is returned unchanged."""
    bad = "http://localhost:9092/api"
    with patch.object(docker_mod, "urlparse", side_effect=ValueError("boom")):
        assert resolve_url_for_docker(bad, force_docker=True) == bad


# --------------------------------------------------------------------------- #
# resolve_kafka_servers_for_docker
# --------------------------------------------------------------------------- #


def test_resolve_kafka_single_server():
    assert (
        resolve_kafka_servers_for_docker("localhost:9092", force_docker=True)
        == "host.docker.internal:9092"
    )


def test_resolve_kafka_multiple_servers():
    assert (
        resolve_kafka_servers_for_docker("localhost:9092,localhost:9093", force_docker=True)
        == "host.docker.internal:9092,host.docker.internal:9093"
    )


def test_resolve_kafka_mixed_real_and_local():
    assert (
        resolve_kafka_servers_for_docker("localhost:9092,kafka.example.com:9092", force_docker=True)
        == "host.docker.internal:9092,kafka.example.com:9092"
    )


def test_resolve_kafka_real_hosts_unchanged():
    assert (
        resolve_kafka_servers_for_docker(
            "kafka1.example.com:9092,kafka2.example.com:9092", force_docker=True
        )
        == "kafka1.example.com:9092,kafka2.example.com:9092"
    )


def test_resolve_kafka_host_without_port():
    assert resolve_kafka_servers_for_docker("localhost", force_docker=True) == DOCKER_HOST_INTERNAL


def test_resolve_kafka_skips_empty_segments_and_trims_whitespace():
    """Empty comma segments are dropped; whitespace around entries trimmed."""
    assert (
        resolve_kafka_servers_for_docker(" localhost:9092 , ,localhost:9093 ", force_docker=True)
        == "host.docker.internal:9092,host.docker.internal:9093"
    )


def test_resolve_kafka_is_noop_outside_docker():
    assert (
        resolve_kafka_servers_for_docker("localhost:9092", force_docker=False) == "localhost:9092"
    )
