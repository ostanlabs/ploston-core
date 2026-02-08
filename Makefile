# Ploston Core
# ============
# Development commands for the core library
#
# Quick start:
#   make install    # Install dependencies
#   make test       # Run tests
#   make lint       # Run linter

# Configuration
PYTHON = uv run python
PYTEST = uv run pytest
PACKAGE_NAME = ploston-core

# Colors
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m

.PHONY: help install test lint format build build-dev publish publish-test-pypi publish-pypi clean

# =============================================================================
# HELP
# =============================================================================

help:
	@echo ""
	@echo "$(CYAN)Ploston Core$(RESET)"
	@echo "============"
	@echo ""
	@echo "$(GREEN)Development:$(RESET)"
	@echo "  make install          Install dependencies with uv"
	@echo "  make test             Run all tests"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests"
	@echo "  make test-property    Run property-based tests"
	@echo "  make test-security    Run security tests"
	@echo "  make lint             Run ruff linter"
	@echo "  make format           Format code with ruff"
	@echo "  make typecheck        Run type checker (mypy)"
	@echo "  make check            Run lint + format check + tests"
	@echo ""
	@echo "$(GREEN)Build & Publish:$(RESET)"
	@echo "  make build            Build package (sdist + wheel)"
	@echo "  make build-dev        Build dev package (X.Y.Z.devTIMESTAMP)"
	@echo "  make publish-test-pypi Publish to TestPyPI"
	@echo "  make publish-pypi     Publish to PyPI"
	@echo ""
	@echo "$(GREEN)Maintenance:$(RESET)"
	@echo "  make clean            Remove build artifacts"
	@echo ""

# =============================================================================
# DEVELOPMENT
# =============================================================================

## Install dependencies
install:
	@echo "$(CYAN)Installing dependencies...$(RESET)"
	uv sync --all-extras
	@echo "$(GREEN)Done!$(RESET)"

## Run all tests
test:
	@echo "$(CYAN)Running all tests...$(RESET)"
	$(PYTEST) tests/ -v

## Run unit tests only
test-unit:
	@echo "$(CYAN)Running unit tests...$(RESET)"
	$(PYTEST) tests/unit/ -v

## Run integration tests
test-integration:
	@echo "$(CYAN)Running integration tests...$(RESET)"
	$(PYTEST) tests/integration/ -v

## Run property-based tests
test-property:
	@echo "$(CYAN)Running property-based tests...$(RESET)"
	$(PYTEST) tests/property/ -v

## Run security tests
test-security:
	@echo "$(CYAN)Running security tests...$(RESET)"
	$(PYTEST) tests/security/ -v

## Run tests with coverage
test-cov:
	@echo "$(CYAN)Running tests with coverage...$(RESET)"
	$(PYTEST) tests/ -v --cov=ploston_core --cov-report=html --cov-report=term

## Run linter
lint:
	@echo "$(CYAN)Running linter...$(RESET)"
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Format code
format:
	@echo "$(CYAN)Formatting code...$(RESET)"
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

## Run type checker
typecheck:
	@echo "$(CYAN)Running type checker...$(RESET)"
	uv run mypy src/

## Run all checks
check: lint test
	@echo "$(GREEN)All checks passed!$(RESET)"

# =============================================================================
# BUILD & PUBLISH
# =============================================================================

## Build package (release version) - depends on lint and unit tests
build: lint test-unit clean
	@echo "$(CYAN)Building package...$(RESET)"
	uv build
	@echo "$(GREEN)Build complete!$(RESET)"
	@ls -la dist/

## Build dev package (X.Y.Z.devTIMESTAMP) - depends on lint and unit tests
build-dev: lint test-unit clean
	@echo "$(CYAN)Building dev package...$(RESET)"
	$(eval TIMESTAMP := $(shell date +%s))
	$(eval BASE_VERSION := $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/'))
	$(eval DEV_VERSION := $(BASE_VERSION).dev$(TIMESTAMP))
	@echo "$(CYAN)Version: $(DEV_VERSION)$(RESET)"
	@sed -i.bak 's/^version = ".*"/version = "$(DEV_VERSION)"/' pyproject.toml
	uv build
	@mv pyproject.toml.bak pyproject.toml
	@echo "$(GREEN)Build complete! Version: $(DEV_VERSION)$(RESET)"
	@ls -la dist/

## Publish to TestPyPI
publish-test-pypi:
	@echo "$(CYAN)Publishing to TestPyPI...$(RESET)"
	uv publish --publish-url https://test.pypi.org/legacy/
	@echo "$(GREEN)Published to TestPyPI!$(RESET)"

## Publish to PyPI (prod)
publish-pypi:
	@echo "$(CYAN)Publishing to PyPI...$(RESET)"
	uv publish
	@echo "$(GREEN)Published to PyPI!$(RESET)"

# =============================================================================
# MAINTENANCE
# =============================================================================

## Remove build artifacts
clean:
	@echo "$(CYAN)Cleaning build artifacts...$(RESET)"
	rm -rf build/ dist/ *.egg-info/
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/
	rm -rf htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)Clean!$(RESET)"

