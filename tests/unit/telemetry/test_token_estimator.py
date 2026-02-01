"""Unit tests for TokenEstimator."""

from ploston_core.telemetry.token_estimator import (
    DEFAULT_PRICING,
    TokenEstimationConfig,
    TokenEstimator,
    TokenSavingsResult,
)


class TestTokenEstimationConfig:
    """Tests for TokenEstimationConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = TokenEstimationConfig()
        assert config.tokens_per_step == 500
        assert config.base_tokens == 200
        assert config.entry_tokens == 100
        assert config.exit_tokens_base == 200
        assert config.exit_tokens_max == 500
        assert config.default_model == "claude_sonnet"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = TokenEstimationConfig(
            tokens_per_step=600,
            base_tokens=300,
            entry_tokens=150,
            default_model="gpt4o",
        )
        assert config.tokens_per_step == 600
        assert config.base_tokens == 300
        assert config.entry_tokens == 150
        assert config.default_model == "gpt4o"

    def test_get_pricing_default(self):
        """Test getting default pricing."""
        config = TokenEstimationConfig()
        pricing = config.get_pricing()
        assert pricing == DEFAULT_PRICING["claude_sonnet"]

    def test_get_pricing_specific_model(self):
        """Test getting pricing for specific model."""
        config = TokenEstimationConfig()
        pricing = config.get_pricing("gpt4o")
        assert pricing == DEFAULT_PRICING["gpt4o"]

    def test_get_pricing_custom(self):
        """Test getting custom pricing."""
        custom = {"input": 1.0, "output": 2.0}
        config = TokenEstimationConfig(custom_pricing={"my_model": custom})
        pricing = config.get_pricing("my_model")
        assert pricing == custom

    def test_get_pricing_unknown_model_fallback(self):
        """Test fallback to default for unknown model."""
        config = TokenEstimationConfig()
        pricing = config.get_pricing("unknown_model")
        assert pricing == DEFAULT_PRICING["claude_sonnet"]


class TestTokenEstimator:
    """Tests for TokenEstimator."""

    def test_estimate_raw_mcp_tokens_single_step(self):
        """Test raw MCP estimation for single step."""
        estimator = TokenEstimator()
        # base_tokens (200) + context_growth (500 * 1)
        tokens = estimator.estimate_raw_mcp_tokens(1)
        assert tokens == 700

    def test_estimate_raw_mcp_tokens_multiple_steps(self):
        """Test raw MCP estimation for multiple steps."""
        estimator = TokenEstimator()
        # base_tokens (200) + context_growth (500*1 + 500*2 + 500*3)
        # = 200 + 500 + 1000 + 1500 = 3200
        tokens = estimator.estimate_raw_mcp_tokens(3)
        assert tokens == 3200

    def test_estimate_raw_mcp_tokens_five_steps(self):
        """Test raw MCP estimation for 5 steps (spec example)."""
        estimator = TokenEstimator()
        # base_tokens (200) + context_growth (500*1 + 500*2 + 500*3 + 500*4 + 500*5)
        # = 200 + 500 + 1000 + 1500 + 2000 + 2500 = 7700
        tokens = estimator.estimate_raw_mcp_tokens(5)
        assert tokens == 7700

    def test_estimate_raw_mcp_tokens_zero_steps(self):
        """Test raw MCP estimation for zero steps."""
        estimator = TokenEstimator()
        tokens = estimator.estimate_raw_mcp_tokens(0)
        assert tokens == 200  # Just base tokens

    def test_estimate_ploston_tokens_small_output(self):
        """Test Ploston estimation for small output."""
        estimator = TokenEstimator()
        # entry_tokens (100) + exit_tokens (200 + 100//4 = 225)
        tokens = estimator.estimate_ploston_tokens(100)
        assert tokens == 325

    def test_estimate_ploston_tokens_large_output(self):
        """Test Ploston estimation for large output (capped)."""
        estimator = TokenEstimator()
        # entry_tokens (100) + exit_tokens (capped at 500)
        tokens = estimator.estimate_ploston_tokens(10000)
        assert tokens == 600  # 100 + 500 (capped)

    def test_estimate_ploston_tokens_zero_output(self):
        """Test Ploston estimation for zero output."""
        estimator = TokenEstimator()
        # entry_tokens (100) + exit_tokens (200 + 0 = 200)
        tokens = estimator.estimate_ploston_tokens(0)
        assert tokens == 300

    def test_estimate_cost_savings(self):
        """Test cost savings estimation."""
        estimator = TokenEstimator()
        # 1000 tokens saved, 60/40 split
        # input: 600 tokens * $3/M = $0.0018
        # output: 400 tokens * $15/M = $0.006
        # total: $0.0078
        cost = estimator.estimate_cost_savings(1000)
        assert abs(cost - 0.0078) < 0.0001

    def test_calculate_savings(self):
        """Test full savings calculation."""
        estimator = TokenEstimator()
        result = estimator.calculate_savings(steps=3, output_size=500)

        assert isinstance(result, TokenSavingsResult)
        assert result.raw_mcp_tokens == 3200  # 200 + 500 + 1000 + 1500
        assert result.ploston_tokens == 425  # 100 + (200 + 500//4) = 100 + 325
        assert result.tokens_saved == 2775
        assert result.savings_percentage > 80  # Should be ~86%
        assert result.cost_saved_usd > 0

    def test_calculate_savings_five_step_workflow(self):
        """Test savings for 5-step workflow (spec example)."""
        estimator = TokenEstimator()
        result = estimator.calculate_savings(steps=5, output_size=1000)

        # Raw MCP: 7700 tokens
        # Ploston: 100 + min(200 + 250, 500) = 100 + 450 = 550
        # Savings: 7700 - 550 = 7150 (~93%)
        assert result.raw_mcp_tokens == 7700
        assert result.ploston_tokens == 550
        assert result.tokens_saved == 7150
        assert result.savings_percentage > 90

    def test_custom_config_affects_estimation(self):
        """Test that custom config affects estimation."""
        config = TokenEstimationConfig(
            tokens_per_step=1000,
            base_tokens=100,
        )
        estimator = TokenEstimator(config=config)

        # base_tokens (100) + context_growth (1000*1 + 1000*2)
        tokens = estimator.estimate_raw_mcp_tokens(2)
        assert tokens == 3100


class TestDefaultPricing:
    """Tests for default pricing constants."""

    def test_claude_sonnet_pricing(self):
        """Test Claude Sonnet pricing."""
        assert DEFAULT_PRICING["claude_sonnet"]["input"] == 3.0
        assert DEFAULT_PRICING["claude_sonnet"]["output"] == 15.0

    def test_claude_haiku_pricing(self):
        """Test Claude Haiku pricing."""
        assert DEFAULT_PRICING["claude_haiku"]["input"] == 0.25
        assert DEFAULT_PRICING["claude_haiku"]["output"] == 1.25

    def test_gpt4o_pricing(self):
        """Test GPT-4o pricing."""
        assert DEFAULT_PRICING["gpt4o"]["input"] == 2.5
        assert DEFAULT_PRICING["gpt4o"]["output"] == 10.0

    def test_gpt4o_mini_pricing(self):
        """Test GPT-4o-mini pricing."""
        assert DEFAULT_PRICING["gpt4o_mini"]["input"] == 0.15
        assert DEFAULT_PRICING["gpt4o_mini"]["output"] == 0.6
