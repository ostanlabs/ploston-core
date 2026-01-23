"""Ploston Core - Shared engine for Ploston packages.

Core engine components that both OSS and Enterprise depend on.
Contains no tier-specific code—only extension points that tier packages hook into.
"""

from ploston_core.application import PlostApplication

__version__ = "1.0.0"
__all__ = ["__version__", "PlostApplication"]
