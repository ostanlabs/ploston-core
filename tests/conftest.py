"""
Pytest configuration and shared fixtures for AEL tests.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# =============================================================================
# Path Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def tests_dir() -> Path:
    """Return the tests directory."""
    return Path(__file__).parent


@pytest.fixture(scope="session")
def fixtures_dir(tests_dir: Path) -> Path:
    """Return the fixtures directory."""
    return tests_dir / "fixtures"


@pytest.fixture(scope="session")
def configs_dir(fixtures_dir: Path) -> Path:
    """Return the config fixtures directory."""
    return fixtures_dir / "configs"


@pytest.fixture(scope="session")
def workflows_dir(fixtures_dir: Path) -> Path:
    """Return the workflow fixtures directory."""
    return fixtures_dir / "workflows"


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def test_config_path(configs_dir: Path) -> Path:
    """Return path to test configuration file."""
    return configs_dir / "test-config.yaml"


@pytest.fixture(scope="session")
def test_config(test_config_path: Path) -> dict[str, Any]:
    """Load test configuration."""
    if test_config_path.exists():
        with test_config_path.open() as f:
            return yaml.safe_load(f)
    return {}


# =============================================================================
# Event Loop Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# CLI Runner Fixtures
# =============================================================================


@pytest.fixture
def ael_cli(
    project_root: Path, test_config_path: Path
) -> Callable[..., subprocess.CompletedProcess]:
    """Fixture to run AEL CLI commands."""

    def _run_cli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "ael.cli"]
        if test_config_path.exists():
            cmd.extend(["--config", str(test_config_path)])
        cmd.extend(args)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_root,
            env={**os.environ, "PYTHONPATH": str(project_root / "src")},
        )

    return _run_cli


@pytest.fixture
def workflow_runner(ael_cli: Callable, workflows_dir: Path) -> Callable:
    """Fixture to run workflows via CLI."""

    def _run_workflow(
        workflow_name: str,
        inputs: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess:
        workflow_path = workflows_dir / workflow_name
        args = ["run", str(workflow_path)]

        if inputs:
            for key, value in inputs.items():
                args.extend(["--input", f"{key}={value}"])

        return ael_cli(*args, timeout=timeout)

    return _run_workflow


# =============================================================================
# Markers Configuration
# =============================================================================


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "workflow: Workflow engine tests")
    config.addinivalue_line("markers", "registry: Registry tests")
    config.addinivalue_line("markers", "security: Security tests")
    config.addinivalue_line("markers", "cli: CLI tests")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "mcp_client: MCP client integration tests")
    config.addinivalue_line("markers", "mcp_http_client: MCP HTTP client integration tests")
    config.addinivalue_line("markers", "frontend: MCP frontend tests")
    config.addinivalue_line("markers", "homelab: Homelab K3s deployment integration tests")
    config.addinivalue_line(
        "markers",
        "requires_running_mode: Tests that require AEL to be in running mode (not configuration mode)",
    )


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption("--run-slow", action="store_true", default=False, help="Run slow tests")
