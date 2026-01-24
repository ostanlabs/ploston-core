"""Ploston Core - Shared engine for Ploston packages.

Core engine components that both OSS and Enterprise depend on.
Contains no tier-specific code—only extension points that tier packages hook into.
"""

from ploston_core.application import PlostApplication

# Native tools are available as a submodule
# Usage: from ploston_core import native_tools
# Or: from ploston_core.native_tools import read_file_content

__version__ = "1.1.0"
__all__ = ["__version__", "PlostApplication", "native_tools"]
