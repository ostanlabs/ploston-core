"""ANSI color codes for terminal output.

This module provides color constants for colored terminal output using ANSI escape codes.
All colors use the 256-color palette for better compatibility and consistency.

Usage:
    from ploston_core.logging.colors import GREEN, RED, RESET

    print(f"{GREEN}Success!{RESET}")
    print(f"{RED}Error occurred{RESET}")
"""

# Basic colors
RESET = "\033[0m"

# Primary colors for status indication
GREEN = "\033[38;5;82m"  # Success - bright green
RED = "\033[38;5;196m"  # Failure - bright red
YELLOW = "\033[38;5;226m"  # Warnings - bright yellow
ORANGE = "\033[38;5;208m"  # Budget warnings - orange

# Secondary colors for information
LIGHT_BLUE = "\033[38;5;153m"  # Info/metrics - light blue (rgb 173, 216, 230)
CYAN = "\033[38;5;51m"  # Info - cyan
MAGENTA = "\033[38;5;201m"  # Contract info - magenta

# Color aliases for semantic meaning
SUCCESS = GREEN
FAILURE = RED
WARNING = YELLOW
INFO = LIGHT_BLUE
BUDGET_WARNING = ORANGE
CONTRACT_INFO = MAGENTA

__all__ = [
    "RESET",
    "GREEN",
    "RED",
    "YELLOW",
    "ORANGE",
    "LIGHT_BLUE",
    "CYAN",
    "MAGENTA",
    "SUCCESS",
    "FAILURE",
    "WARNING",
    "INFO",
    "BUDGET_WARNING",
    "CONTRACT_INFO",
]
