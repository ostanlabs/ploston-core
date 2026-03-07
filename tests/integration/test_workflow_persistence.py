"""Integration tests for workflow persistence (M-068 / S-228).

Tests that workflows survive registry teardown and reinitialisation,
covering both OSS (disk) and Premium (Redis mock) paths.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from ploston_core.workflow.registry import WorkflowRegistry

SAMPLE_YAML = """\
name: persist-test
version: "1.0.0"
description: Persistence integration test
steps:
  - id: step1
    tool: echo
    params:
      message: hello
"""

SAMPLE_YAML_V2 = """\
name: persist-test
version: "2.0.0"
description: Updated persistence test
steps:
  - id: step1
    tool: echo
    params:
      message: hello v2
"""


def _make_config(tmp_path: Path) -> MagicMock:
    config = MagicMock()
    config.directory = str(tmp_path / "workflows")
    return config


def _make_tool_registry() -> MagicMock:
    tr = MagicMock()
    tr.get_tool.return_value = MagicMock()
    return tr


class TestWorkflowSurvivesRestartOSS:
    """OSS mode: workflow persisted to disk survives registry restart."""

    def test_workflow_survives_restart(self, tmp_path: Path):
        """Register with persist=True, tear down, reinitialise -> workflow present."""
        config = _make_config(tmp_path)
        tool_reg = _make_tool_registry()

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                reg1 = WorkflowRegistry(tool_reg, config)
                reg1.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)

                target = Path(config.directory) / "persist-test.yaml"
                assert target.exists()

                reg2 = WorkflowRegistry(tool_reg, config)
                count = await reg2.initialize()
                assert count >= 1

                wf = reg2.get("persist-test")
                assert wf is not None
                assert wf.version == "1.0.0"
                assert wf.yaml_content == SAMPLE_YAML

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_deleted_workflow_absent_after_restart(self, tmp_path: Path):
        """Register, unregister, reinitialise -> workflow absent."""
        config = _make_config(tmp_path)
        tool_reg = _make_tool_registry()

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                reg1 = WorkflowRegistry(tool_reg, config)
                reg1.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)

                reg1.unregister("persist-test")
                await asyncio.sleep(0.1)

                target = Path(config.directory) / "persist-test.yaml"
                assert not target.exists()

                reg2 = WorkflowRegistry(tool_reg, config)
                await reg2.initialize()
                assert reg2.get("persist-test") is None

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_put_overwrites_persisted_yaml(self, tmp_path: Path):
        """Register v1, simulate PUT with v2, reinitialise -> v2 loaded."""
        config = _make_config(tmp_path)
        tool_reg = _make_tool_registry()

        loop = asyncio.new_event_loop()
        try:

            async def _run():
                reg1 = WorkflowRegistry(tool_reg, config)
                reg1.register_from_yaml(SAMPLE_YAML, persist=True)
                await asyncio.sleep(0.1)

                reg1.unregister("persist-test")
                await asyncio.sleep(0.1)
                reg1.register_from_yaml(SAMPLE_YAML_V2, persist=True)
                await asyncio.sleep(0.1)

                reg2 = WorkflowRegistry(tool_reg, config)
                await reg2.initialize()

                wf = reg2.get("persist-test")
                assert wf is not None
                assert wf.version == "2.0.0"

            loop.run_until_complete(_run())
        finally:
            loop.close()
