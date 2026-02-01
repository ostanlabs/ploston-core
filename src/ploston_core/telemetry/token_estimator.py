"""Token Savings Estimation - Calculate estimated token savings from workflows.

This module provides token estimation for demonstrating Ploston's value:
- Estimates tokens that would be used with raw MCP (agent orchestrating directly)
- Estimates tokens used with Ploston workflows
- Calculates savings and emits Prometheus metrics

The estimation model is based on typical LLM context patterns:
- Raw MCP: Each step requires sending previous results back to LLM, causing context growth
- Ploston: LLM only touches entry (workflow selection) and exit (final result)
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry import metrics

if TYPE_CHECKING:
    from ploston_core.engine import ExecutionResult


# Default pricing per million tokens (USD)
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude_sonnet": {"input": 3.0, "output": 15.0},
    "claude_haiku": {"input": 0.25, "output": 1.25},
    "gpt4o": {"input": 2.5, "output": 10.0},
    "gpt4o_mini": {"input": 0.15, "output": 0.6},
}


@dataclass
class TokenEstimationConfig:
    """Configuration for token estimation."""

    # Estimation parameters
    tokens_per_step: int = 500  # Average tokens per MCP round-trip
    base_tokens: int = 200  # Initial request tokens
    entry_tokens: int = 100  # Ploston workflow selection tokens
    exit_tokens_base: int = 200  # Ploston result processing base tokens
    exit_tokens_max: int = 500  # Maximum exit tokens

    # Cost estimation
    default_model: str = "claude_sonnet"
    custom_pricing: dict[str, dict[str, float]] = field(default_factory=dict)

    def get_pricing(self, model: str | None = None) -> dict[str, float]:
        """Get pricing for a model."""
        model = model or self.default_model
        if model in self.custom_pricing:
            return self.custom_pricing[model]
        return DEFAULT_PRICING.get(model, DEFAULT_PRICING["claude_sonnet"])


@dataclass
class TokenSavingsResult:
    """Result of token savings calculation."""

    raw_mcp_tokens: int  # Estimated tokens without Ploston
    ploston_tokens: int  # Estimated tokens with Ploston
    tokens_saved: int  # Difference
    savings_percentage: float  # Percentage saved
    cost_saved_usd: float  # Estimated cost saved


class TokenEstimator:
    """Estimate token savings from using Ploston workflows.

    Provides:
    - Token estimation for raw MCP vs Ploston execution
    - Cost savings calculation
    - Prometheus metrics emission
    """

    def __init__(
        self,
        config: TokenEstimationConfig | None = None,
        meter: metrics.Meter | None = None,
    ):
        """Initialize token estimator.

        Args:
            config: Token estimation configuration
            meter: OpenTelemetry Meter for metrics (optional)
        """
        self._config = config or TokenEstimationConfig()
        self._meter = meter
        self._setup_metrics()

    def _setup_metrics(self) -> None:
        """Set up Prometheus metrics."""
        if not self._meter:
            return

        # Counter: Total tokens saved
        self._tokens_saved_total = self._meter.create_counter(
            name="ploston_tokens_saved_total",
            description="Total estimated tokens saved by using Ploston workflows",
            unit="1",
        )

        # Counter: Total cost saved (in cents for precision)
        self._cost_saved_cents_total = self._meter.create_counter(
            name="ploston_cost_saved_cents_total",
            description="Total estimated cost saved in cents",
            unit="1",
        )

        # Counter: Raw MCP estimate (for calculating savings rate)
        self._raw_mcp_estimate_total = self._meter.create_counter(
            name="ploston_raw_mcp_estimate_total",
            description="Estimated tokens if using raw MCP (for comparison)",
            unit="1",
        )

        # Histogram: Tokens saved per execution
        self._tokens_saved_per_execution = self._meter.create_histogram(
            name="ploston_tokens_saved_per_execution",
            description="Tokens saved per workflow execution",
            unit="1",
        )

    def estimate_raw_mcp_tokens(self, steps: int) -> int:
        """Estimate tokens if agent orchestrated directly via MCP.

        With raw MCP, each step requires:
        1. Tool result sent back to LLM (input tokens)
        2. LLM reasoning about what to do next (output tokens)
        3. Context accumulation - previous results stay in context

        Args:
            steps: Number of workflow steps

        Returns:
            Estimated token count
        """
        # Context grows with each step: step 1 adds 500, step 2 adds 1000, etc.
        context_growth = sum(self._config.tokens_per_step * i for i in range(1, steps + 1))
        return self._config.base_tokens + context_growth

    def estimate_ploston_tokens(self, output_size: int) -> int:
        """Estimate tokens with Ploston execution.

        With Ploston, LLM only touches entry and exit:
        1. Workflow selection - LLM decides which workflow to call
        2. Final response - LLM receives final result

        Args:
            output_size: Size of workflow output in characters

        Returns:
            Estimated token count
        """
        # Exit tokens scale slightly with output size, but capped
        exit_tokens = min(
            self._config.exit_tokens_base + output_size // 4,
            self._config.exit_tokens_max,
        )
        return self._config.entry_tokens + exit_tokens

    def estimate_cost_savings(self, tokens_saved: int, model: str | None = None) -> float:
        """Estimate cost savings in USD.

        Assumes 60/40 input/output token split.

        Args:
            tokens_saved: Number of tokens saved
            model: LLM model name (uses default if not specified)

        Returns:
            Estimated cost saved in USD
        """
        pricing = self._config.get_pricing(model)

        # Assume 60/40 input/output split
        input_tokens = tokens_saved * 0.6
        output_tokens = tokens_saved * 0.4

        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def calculate_savings(
        self, steps: int, output_size: int, model: str | None = None
    ) -> TokenSavingsResult:
        """Calculate token savings for a workflow execution.

        Args:
            steps: Number of workflow steps
            output_size: Size of workflow output in characters
            model: LLM model name for cost calculation

        Returns:
            TokenSavingsResult with all savings metrics
        """
        raw_mcp = self.estimate_raw_mcp_tokens(steps)
        ploston = self.estimate_ploston_tokens(output_size)
        tokens_saved = raw_mcp - ploston
        percentage = ((raw_mcp - ploston) / raw_mcp) * 100 if raw_mcp > 0 else 0
        cost_saved = self.estimate_cost_savings(tokens_saved, model)

        return TokenSavingsResult(
            raw_mcp_tokens=raw_mcp,
            ploston_tokens=ploston,
            tokens_saved=tokens_saved,
            savings_percentage=percentage,
            cost_saved_usd=cost_saved,
        )

    def record_workflow_savings(
        self,
        execution_result: "ExecutionResult",
        model: str | None = None,
    ) -> TokenSavingsResult:
        """Record token savings metrics for a workflow execution.

        This is the main integration point - call after workflow completion.

        Args:
            execution_result: Completed workflow execution result
            model: LLM model name for cost calculation

        Returns:
            TokenSavingsResult with all savings metrics
        """
        # Calculate output size
        output_str = json.dumps(execution_result.outputs, default=str)
        output_size = len(output_str)

        # Count steps (completed + failed, not skipped)
        steps = execution_result.steps_completed + execution_result.steps_failed

        # Calculate savings
        savings = self.calculate_savings(steps, output_size, model)

        # Record metrics if meter is configured
        if self._meter:
            workflow_name = execution_result.workflow_id
            model_name = model or self._config.default_model

            self._tokens_saved_total.add(
                savings.tokens_saved,
                {"workflow_name": workflow_name},
            )

            self._cost_saved_cents_total.add(
                int(savings.cost_saved_usd * 100),
                {"workflow_name": workflow_name, "model": model_name},
            )

            self._raw_mcp_estimate_total.add(
                savings.raw_mcp_tokens,
                {"workflow_name": workflow_name},
            )

            self._tokens_saved_per_execution.record(
                savings.tokens_saved,
                {"workflow_name": workflow_name},
            )

        return savings
