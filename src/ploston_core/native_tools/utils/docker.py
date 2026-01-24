"""Docker environment detection and host resolution utilities.

This module provides utilities for detecting whether code is running inside
a Docker container and resolving localhost references to host.docker.internal
when appropriate.

Usage:
    from mcp_shared.utils import is_running_in_docker, resolve_url_for_docker

    # Check if running in Docker
    if is_running_in_docker():
        print("Running inside Docker container")

    # Resolve URLs for Docker environment
    url = resolve_url_for_docker("http://localhost:3002")
    # Returns "http://host.docker.internal:3002" if in Docker, otherwise unchanged
"""

import os
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

# Hosts that should be replaced with host.docker.internal when running in Docker
LOCALHOST_VARIANTS = frozenset(
    [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    ]
)

# The Docker host that allows containers to reach the host machine
DOCKER_HOST_INTERNAL = "host.docker.internal"


@lru_cache(maxsize=1)
def is_running_in_docker() -> bool:
    """Detect if the current process is running inside a Docker container.

    Uses multiple detection methods for reliability:
    1. Check for /.dockerenv file (most reliable)
    2. Check for 'docker' in /proc/1/cgroup (Linux containers)
    3. Check for DOCKER_CONTAINER environment variable (explicit marker)

    Returns:
        True if running inside a Docker container, False otherwise.

    Note:
        Result is cached after first call for performance.
    """
    # Method 1: Check for .dockerenv file (most common and reliable)
    if os.path.exists("/.dockerenv"):
        return True

    # Method 2: Check cgroup for docker signature (Linux)
    try:
        with open("/proc/1/cgroup") as f:
            cgroup_content = f.read()
            if "docker" in cgroup_content or "containerd" in cgroup_content:
                return True
    except (OSError, FileNotFoundError, PermissionError):
        pass

    # Method 3: Check for explicit environment variable
    # This can be set in docker-compose or Dockerfile
    if os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes"):
        return True

    # Method 4: Check for container ID in /proc/self/cgroup (alternative Linux check)
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                if "docker" in line or "kubepods" in line:
                    return True
    except (OSError, FileNotFoundError, PermissionError):
        pass

    return False


def resolve_host_for_docker(host: str, force_docker: bool | None = None) -> str:
    """Resolve a hostname for Docker environment.

    If running inside Docker and the host is a localhost variant,
    replaces it with host.docker.internal to allow the container
    to reach services on the host machine.

    Args:
        host: The hostname to resolve (e.g., "localhost", "127.0.0.1")
        force_docker: If provided, overrides automatic Docker detection.
                     True = treat as Docker environment
                     False = treat as non-Docker environment
                     None = auto-detect (default)

    Returns:
        The resolved hostname. Returns host.docker.internal if:
        - Running in Docker (or force_docker=True)
        - AND host is a localhost variant
        Otherwise returns the original host unchanged.

    Examples:
        # Inside Docker:
        resolve_host_for_docker("localhost")  # -> "host.docker.internal"
        resolve_host_for_docker("127.0.0.1")  # -> "host.docker.internal"
        resolve_host_for_docker("api.example.com")  # -> "api.example.com"

        # Outside Docker:
        resolve_host_for_docker("localhost")  # -> "localhost"
    """
    # Determine if we should apply Docker resolution
    in_docker = force_docker if force_docker is not None else is_running_in_docker()

    if not in_docker:
        return host

    # Normalize host for comparison (lowercase, strip whitespace)
    normalized_host = host.lower().strip()

    if normalized_host in LOCALHOST_VARIANTS:
        return DOCKER_HOST_INTERNAL

    return host


def resolve_url_for_docker(url: str, force_docker: bool | None = None) -> str:
    """Resolve a URL for Docker environment.

    If running inside Docker and the URL's host is a localhost variant,
    replaces it with host.docker.internal to allow the container
    to reach services on the host machine.

    Args:
        url: The URL to resolve (e.g., "http://localhost:3002/api")
        force_docker: If provided, overrides automatic Docker detection.
                     True = treat as Docker environment
                     False = treat as non-Docker environment
                     None = auto-detect (default)

    Returns:
        The resolved URL with host replaced if necessary.

    Examples:
        # Inside Docker:
        resolve_url_for_docker("http://localhost:3002")
        # -> "http://host.docker.internal:3002"

        resolve_url_for_docker("http://127.0.0.1:9092/topic")
        # -> "http://host.docker.internal:9092/topic"

        resolve_url_for_docker("https://api.example.com/v1")
        # -> "https://api.example.com/v1" (unchanged)

        # Outside Docker:
        resolve_url_for_docker("http://localhost:3002")
        # -> "http://localhost:3002" (unchanged)
    """
    # Determine if we should apply Docker resolution
    in_docker = force_docker if force_docker is not None else is_running_in_docker()

    if not in_docker:
        return url

    try:
        parsed = urlparse(url)

        # Only process if there's a host component
        if not parsed.hostname:
            return url

        # Check if host needs resolution
        resolved_host = resolve_host_for_docker(parsed.hostname, force_docker=True)

        if resolved_host == parsed.hostname:
            # No change needed
            return url

        # Reconstruct the netloc (host:port or just host)
        if parsed.port:
            new_netloc = f"{resolved_host}:{parsed.port}"
        else:
            new_netloc = resolved_host

        # Handle username:password in netloc if present
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            new_netloc = f"{auth}@{new_netloc}"

        # Reconstruct the URL with the new netloc
        new_parsed = parsed._replace(netloc=new_netloc)
        return urlunparse(new_parsed)

    except Exception:
        # If URL parsing fails, return original
        return url


def resolve_kafka_servers_for_docker(
    bootstrap_servers: str, force_docker: bool | None = None
) -> str:
    """Resolve Kafka bootstrap servers string for Docker environment.

    Kafka bootstrap servers are typically in format "host1:port1,host2:port2".
    This function resolves each host individually.

    Args:
        bootstrap_servers: Comma-separated list of host:port pairs
        force_docker: If provided, overrides automatic Docker detection.

    Returns:
        The resolved bootstrap servers string.

    Examples:
        # Inside Docker:
        resolve_kafka_servers_for_docker("localhost:9092")
        # -> "host.docker.internal:9092"

        resolve_kafka_servers_for_docker("localhost:9092,localhost:9093")
        # -> "host.docker.internal:9092,host.docker.internal:9093"

        resolve_kafka_servers_for_docker("kafka1.example.com:9092,kafka2.example.com:9092")
        # -> "kafka1.example.com:9092,kafka2.example.com:9092" (unchanged)
    """
    in_docker = force_docker if force_docker is not None else is_running_in_docker()

    if not in_docker:
        return bootstrap_servers

    resolved_servers = []

    for server in bootstrap_servers.split(","):
        server = server.strip()
        if not server:
            continue

        # Parse host:port
        if ":" in server:
            host, port = server.rsplit(":", 1)
            resolved_host = resolve_host_for_docker(host, force_docker=True)
            resolved_servers.append(f"{resolved_host}:{port}")
        else:
            # Just a host without port
            resolved_host = resolve_host_for_docker(server, force_docker=True)
            resolved_servers.append(resolved_host)

    return ",".join(resolved_servers)
