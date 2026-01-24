"""Property-based tests for configuration validation.

Tests env var resolution, YAML parsing, config validation, and defaults.
"""

import os
import pytest
from hypothesis import given, strategies as st, settings, assume
from unittest.mock import patch

from ploston_core.config.loader import (
    resolve_env_vars,
    _resolve_env_vars_recursive,
    ConfigLoader,
)
from ploston_core.errors import AELError


# =============================================================================
# Strategies for generating config data
# =============================================================================

# Valid env var names
valid_env_var_name = st.from_regex(r'^[A-Z][A-Z0-9_]{0,20}$', fullmatch=True)

# Valid env var values (no special chars that break shell)
valid_env_var_value = st.from_regex(r'^[a-zA-Z0-9_\-./]{1,50}$', fullmatch=True)

# Valid config keys
valid_config_key = st.from_regex(r'^[a-z][a-z0-9_]{0,15}$', fullmatch=True)

# Simple config values
simple_values = st.one_of(
    st.booleans(),
    st.integers(min_value=-1000, max_value=10000),
    st.text(alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='_-'), min_size=1, max_size=50),
)


# =============================================================================
# Property Tests for Environment Variable Resolution
# =============================================================================

@pytest.mark.property
class TestEnvVarResolution:
    """Property tests for environment variable resolution."""
    
    @given(valid_env_var_name, valid_env_var_value)
    @settings(max_examples=50)
    def test_env_var_resolved_when_set(self, var_name, var_value):
        """Environment variables should be resolved when set."""
        with patch.dict(os.environ, {var_name: var_value}):
            result = resolve_env_vars(f"${{{var_name}}}")
            assert result == var_value
    
    @given(valid_env_var_name, valid_env_var_value, valid_env_var_value)
    @settings(max_examples=50)
    def test_env_var_with_default_uses_value_when_set(self, var_name, var_value, default):
        """When env var is set, its value should be used over default."""
        with patch.dict(os.environ, {var_name: var_value}):
            result = resolve_env_vars(f"${{{var_name}:-{default}}}")
            assert result == var_value
    
    @given(valid_env_var_name, valid_env_var_value)
    @settings(max_examples=50)
    def test_env_var_with_default_uses_default_when_unset(self, var_name, default):
        """When env var is not set, default should be used."""
        # Ensure var is not set
        env = {k: v for k, v in os.environ.items() if k != var_name}
        with patch.dict(os.environ, env, clear=True):
            result = resolve_env_vars(f"${{{var_name}:-{default}}}")
            assert result == default
    
    @given(valid_env_var_name)
    @settings(max_examples=30)
    def test_required_env_var_raises_when_unset(self, var_name):
        """Required env vars should raise error when not set."""
        # Ensure var is not set
        env = {k: v for k, v in os.environ.items() if k != var_name}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(AELError) as exc_info:
                resolve_env_vars(f"${{{var_name}}}")
            assert "CONFIG_INVALID" in str(exc_info.value.code)
    
    @given(valid_env_var_name)
    @settings(max_examples=30)
    def test_custom_error_syntax_raises(self, var_name):
        """Custom error syntax should raise CONFIG_INVALID when var not set."""
        env = {k: v for k, v in os.environ.items() if k != var_name}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(AELError) as exc_info:
                resolve_env_vars(f"${{{var_name}:?Custom error message}}")
            assert exc_info.value.code == "CONFIG_INVALID"
    
    @given(st.text(alphabet=st.characters(whitelist_categories=('L', 'N')), min_size=1, max_size=50))
    @settings(max_examples=30)
    def test_string_without_env_vars_unchanged(self, text):
        """Strings without env var syntax should be unchanged."""
        assume('$' not in text)
        result = resolve_env_vars(text)
        assert result == text


# =============================================================================
# Property Tests for Recursive Env Var Resolution
# =============================================================================

@pytest.mark.property
class TestRecursiveEnvVarResolution:
    """Property tests for recursive env var resolution in data structures."""
    
    @given(valid_env_var_name, valid_env_var_value)
    @settings(max_examples=30)
    def test_nested_dict_resolution(self, var_name, var_value):
        """Env vars in nested dicts should be resolved."""
        with patch.dict(os.environ, {var_name: var_value}):
            data = {
                "level1": {
                    "level2": f"${{{var_name}}}"
                }
            }
            result = _resolve_env_vars_recursive(data)
            assert result["level1"]["level2"] == var_value
    
    @given(valid_env_var_name, valid_env_var_value)
    @settings(max_examples=30)
    def test_list_resolution(self, var_name, var_value):
        """Env vars in lists should be resolved."""
        with patch.dict(os.environ, {var_name: var_value}):
            data = ["static", f"${{{var_name}}}", "another"]
            result = _resolve_env_vars_recursive(data)
            assert result[1] == var_value
            assert result[0] == "static"
            assert result[2] == "another"
    
    @given(st.integers(), st.booleans())
    @settings(max_examples=30)
    def test_non_string_values_unchanged(self, int_val, bool_val):
        """Non-string values should pass through unchanged."""
        data = {
            "int_val": int_val,
            "bool_val": bool_val,
            "none_val": None,
        }
        result = _resolve_env_vars_recursive(data)
        assert result["int_val"] == int_val
        assert result["bool_val"] == bool_val
        assert result["none_val"] is None


# =============================================================================
# Property Tests for Config Validation
# =============================================================================

@pytest.mark.property
class TestConfigValidation:
    """Property tests for configuration validation."""
    
    @given(st.integers(min_value=1, max_value=65535))
    @settings(max_examples=30)
    def test_valid_server_port(self, port):
        """Valid server ports should pass validation."""
        loader = ConfigLoader()
        data = {"server": {"port": port}}
        result = loader.validate(data)
        
        port_errors = [e for e in result.errors if "port" in e.path]
        assert len(port_errors) == 0
    
    @given(st.integers(min_value=1, max_value=10000))
    @settings(max_examples=30)
    def test_valid_execution_timeout(self, timeout):
        """Valid execution timeouts should pass validation."""
        loader = ConfigLoader()
        data = {"execution": {"default_timeout": timeout}}
        result = loader.validate(data)
        
        timeout_errors = [e for e in result.errors if "timeout" in e.path]
        assert len(timeout_errors) == 0
    
    @given(st.integers(max_value=0))
    @settings(max_examples=30)
    def test_invalid_execution_timeout_rejected(self, timeout):
        """Non-positive execution timeouts should be rejected."""
        loader = ConfigLoader()
        data = {"execution": {"default_timeout": timeout}}
        result = loader.validate(data)
        
        timeout_errors = [e for e in result.errors if "timeout" in e.path]
        assert len(timeout_errors) == 1
    
    @given(valid_config_key)
    @settings(max_examples=30)
    def test_unknown_keys_generate_warnings(self, unknown_key):
        """Unknown top-level keys should generate warnings."""
        assume(unknown_key not in {
            "server", "mcp", "tools", "workflows", "execution",
            "python_exec", "logging", "plugins", "security", "telemetry"
        })
        
        loader = ConfigLoader()
        data = {unknown_key: "some_value"}
        result = loader.validate(data)
        
        key_warnings = [w for w in result.warnings if unknown_key in w.message]
        assert len(key_warnings) == 1


# =============================================================================
# Property Tests for Config Defaults
# =============================================================================

@pytest.mark.property
class TestConfigDefaults:
    """Property tests for configuration defaults."""
    
    def test_empty_config_uses_defaults(self):
        """Empty config should use all defaults."""
        loader = ConfigLoader()
        config = loader.load_from_dict({})
        
        # Check defaults are applied
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 8080
        assert config.execution.default_timeout == 300
        assert config.execution.step_timeout == 30
        assert config.execution.max_steps == 100
    
    @given(st.integers(min_value=1, max_value=65535))
    @settings(max_examples=30)
    def test_partial_config_preserves_other_defaults(self, port):
        """Partial config should preserve defaults for unspecified fields."""
        loader = ConfigLoader()
        config = loader.load_from_dict({"server": {"port": port}})
        
        # Specified value used
        assert config.server.port == port
        # Defaults preserved
        assert config.server.host == "0.0.0.0"
        assert config.execution.default_timeout == 300
    
    @given(
        st.integers(min_value=1, max_value=1000),
        st.integers(min_value=1, max_value=100)
    )
    @settings(max_examples=30)
    def test_multiple_overrides(self, timeout, max_steps):
        """Multiple config overrides should all be applied."""
        loader = ConfigLoader()
        config = loader.load_from_dict({
            "execution": {
                "default_timeout": timeout,
                "max_steps": max_steps
            }
        })
        
        assert config.execution.default_timeout == timeout
        assert config.execution.max_steps == max_steps
        # Default preserved
        assert config.execution.step_timeout == 30
