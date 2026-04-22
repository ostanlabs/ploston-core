"""Tests for S-225: sandbox library import whitelist (T-686, T-687, T-688).

Also covers S-272 T-865: _strptime whitelist + three-way drift guard.
"""

from ploston_core.config.models import PythonExecConfig
from ploston_core.sandbox import SandboxConfig
from ploston_core.sandbox.sandbox import SAFE_IMPORTS


class TestSafeImportsWhitelist:
    """Verify SAFE_IMPORTS contains the expected third-party libraries."""

    def test_anthropic_in_safe_imports(self) -> None:
        """T-687: anthropic must be in SAFE_IMPORTS."""
        assert "anthropic" in SAFE_IMPORTS

    def test_pypdf_in_safe_imports(self) -> None:
        """T-686: pypdf must be in SAFE_IMPORTS."""
        assert "pypdf" in SAFE_IMPORTS

    def test_io_in_safe_imports(self) -> None:
        """T-688 audit: io must be in SAFE_IMPORTS for BytesIO usage."""
        assert "io" in SAFE_IMPORTS

    def test_standard_library_modules_present(self) -> None:
        """All standard library modules must remain in SAFE_IMPORTS."""
        expected = {
            "json",
            "math",
            "datetime",
            "time",
            "random",
            "itertools",
            "functools",
            "collections",
            "typing",
            "re",
            "decimal",
            "statistics",
            "operator",
            "copy",
            "uuid",
            "hashlib",
        }
        assert expected.issubset(SAFE_IMPORTS)


class TestSandboxConfigDefaults:
    """Verify SandboxConfig defaults stay in sync with SAFE_IMPORTS."""

    def test_sandbox_config_default_includes_anthropic(self) -> None:
        """T-687: SandboxConfig default allowed_imports must include anthropic."""
        config = SandboxConfig()
        assert "anthropic" in config.allowed_imports

    def test_sandbox_config_default_includes_pypdf(self) -> None:
        """T-686: SandboxConfig default allowed_imports must include pypdf."""
        config = SandboxConfig()
        assert "pypdf" in config.allowed_imports

    def test_sandbox_config_default_includes_io(self) -> None:
        """T-688: SandboxConfig default allowed_imports must include io."""
        config = SandboxConfig()
        assert "io" in config.allowed_imports

    def test_sandbox_config_defaults_match_safe_imports(self) -> None:
        """SandboxConfig defaults must be a superset of SAFE_IMPORTS."""
        config = SandboxConfig()
        config_set = set(config.allowed_imports)
        assert SAFE_IMPORTS.issubset(config_set), (
            f"SAFE_IMPORTS has entries not in SandboxConfig defaults: {SAFE_IMPORTS - config_set}"
        )

    def test_custom_allowed_imports_override(self) -> None:
        """Custom allowed_imports should not include anthropic unless specified."""
        config = SandboxConfig(allowed_imports=["json"])
        assert "anthropic" not in config.allowed_imports
        assert "pypdf" not in config.allowed_imports


class TestStrptimeAndDriftGuards:
    """S-272 T-865: _strptime whitelist + three-way drift guard."""

    # SP-01
    def test_sp01_strptime_in_production_gate(self) -> None:
        """_strptime must be in PythonExecConfig.default_imports (PRODUCTION GATE)."""
        cfg = PythonExecConfig()
        assert "_strptime" in cfg.default_imports

    # SP-02
    def test_sp02_strptime_in_safe_imports(self) -> None:
        """_strptime must be in SAFE_IMPORTS for drift parity."""
        assert "_strptime" in SAFE_IMPORTS

    # SP-02b
    def test_sp02_strptime_in_sandbox_config_defaults(self) -> None:
        """_strptime must be in SandboxConfig defaults for drift parity."""
        assert "_strptime" in SandboxConfig().allowed_imports

    # SP-03
    def test_sp03_three_way_drift_guard(self) -> None:
        """PythonExecConfig.default_imports, SAFE_IMPORTS, and SandboxConfig
        defaults must all contain the same module set.
        """
        prod = set(PythonExecConfig().default_imports)
        safe = set(SAFE_IMPORTS)
        sbx = set(SandboxConfig().allowed_imports)
        assert prod == safe == sbx, (
            f"Import allowlists drifted:\n"
            f"  prod-only: {prod - safe - sbx}\n"
            f"  safe-only: {safe - prod - sbx}\n"
            f"  sbx-only:  {sbx - prod - safe}"
        )
