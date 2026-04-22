"""S-272 T-865: runtime verification that datetime.strptime and
datetime.fromisoformat work inside the sandbox with the shipped import
allowlist.

The production gate is PythonExecConfig.default_imports (config/models.py);
these tests exercise PythonExecSandbox directly, which uses SAFE_IMPORTS
when no explicit list is provided. SP-03 in test_library_imports.py asserts
the two lists are in sync, so a pass here implies a pass under the live
production gate as well.
"""

import pytest

from ploston_core.sandbox import PythonExecSandbox


@pytest.fixture
def sandbox():
    return PythonExecSandbox(timeout=5)


# ── SP-01: datetime.strptime succeeds in the sandbox ──


@pytest.mark.asyncio
async def test_sp01_strptime_parses_iso_z_format(sandbox):
    """datetime.strptime with %Y-%m-%dT%H:%M:%SZ should succeed."""
    code = (
        "from datetime import datetime\n"
        "result = datetime.strptime("
        "'2026-04-05T03:37:42Z', '%Y-%m-%dT%H:%M:%SZ'"
        ").isoformat()\n"
    )
    res = await sandbox.execute(code, {})
    assert res.success, f"strptime failed: {res.error}"
    assert res.result == "2026-04-05T03:37:42"


@pytest.mark.asyncio
async def test_sp01b_strptime_import_style_module(sandbox):
    """`import datetime; datetime.datetime.strptime(...)` works."""
    code = (
        "import datetime\n"
        "result = datetime.datetime.strptime("
        "'2026-04-05 03:37:42', '%Y-%m-%d %H:%M:%S'"
        ").year\n"
    )
    res = await sandbox.execute(code, {})
    assert res.success, f"strptime failed: {res.error}"
    assert res.result == 2026


# ── SP-02: datetime.fromisoformat succeeds ──


@pytest.mark.asyncio
async def test_sp02_fromisoformat_with_offset(sandbox):
    """datetime.fromisoformat handles offset-aware timestamps."""
    code = (
        "from datetime import datetime\n"
        "result = datetime.fromisoformat("
        "'2026-04-05T03:37:42+00:00'"
        ").isoformat()\n"
    )
    res = await sandbox.execute(code, {})
    assert res.success, f"fromisoformat failed: {res.error}"
    assert res.result.startswith("2026-04-05T03:37:42")


@pytest.mark.asyncio
async def test_sp02b_fromisoformat_basic(sandbox):
    """datetime.fromisoformat on a plain ISO timestamp."""
    code = (
        "from datetime import datetime\n"
        "dt = datetime.fromisoformat('2026-04-05T03:37:42')\n"
        "result = (dt.year, dt.month, dt.day)\n"
    )
    res = await sandbox.execute(code, {})
    assert res.success, f"fromisoformat failed: {res.error}"
    assert res.result == (2026, 4, 5)
