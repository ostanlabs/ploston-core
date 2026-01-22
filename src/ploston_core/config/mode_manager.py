"""AEL Mode Manager - tracks configuration/running mode."""

from collections.abc import Callable
from enum import Enum


class Mode(Enum):
    """AEL operating mode."""

    CONFIGURATION = "configuration"
    RUNNING = "running"


class ModeManager:
    """
    Tracks AEL operating mode and notifies on changes.

    AEL operates in one of two mutually exclusive modes:
    - CONFIGURATION: Only config tools available, no workflows
    - RUNNING: All tools and workflows available

    Mode transitions:
    - CONFIGURATION -> RUNNING: via config_done (validates + connects MCP)
    - RUNNING -> CONFIGURATION: via ael:configure
    """

    def __init__(self, initial_mode: Mode = Mode.CONFIGURATION):
        """Initialize mode manager.

        Args:
            initial_mode: Initial operating mode (default: CONFIGURATION)
        """
        self._mode = initial_mode
        self._on_change_callbacks: list[Callable[[Mode], None]] = []
        self._running_workflow_count = 0

    @property
    def mode(self) -> Mode:
        """Get current operating mode.

        Returns:
            Current Mode
        """
        return self._mode

    def is_configuration_mode(self) -> bool:
        """Check if in configuration mode.

        Returns:
            True if in CONFIGURATION mode
        """
        return self._mode == Mode.CONFIGURATION

    def is_running_mode(self) -> bool:
        """Check if in running mode.

        Returns:
            True if in RUNNING mode
        """
        return self._mode == Mode.RUNNING

    def set_mode(self, mode: Mode) -> None:
        """Change mode and notify callbacks.

        Args:
            mode: New mode to set
        """
        if mode != self._mode:
            self._mode = mode
            for callback in self._on_change_callbacks:
                try:
                    callback(mode)
                except Exception:
                    # Don't let callback errors break mode transition
                    pass

    def on_mode_change(self, callback: Callable[[Mode], None]) -> None:
        """Register callback for mode changes.

        Callback receives the new mode when mode changes.

        Args:
            callback: Function to call when mode changes
        """
        self._on_change_callbacks.append(callback)

    def remove_mode_change_callback(self, callback: Callable[[Mode], None]) -> bool:
        """Remove a registered callback.

        Args:
            callback: Callback to remove

        Returns:
            True if callback was found and removed
        """
        try:
            self._on_change_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def increment_running_workflows(self) -> None:
        """Increment count of running workflows.

        Called when a workflow starts execution.
        """
        self._running_workflow_count += 1

    def decrement_running_workflows(self) -> None:
        """Decrement count of running workflows.

        Called when a workflow completes execution.
        """
        self._running_workflow_count = max(0, self._running_workflow_count - 1)

    @property
    def running_workflow_count(self) -> int:
        """Get count of currently running workflows.

        Returns:
            Number of workflows currently executing
        """
        return self._running_workflow_count

    def can_start_workflow(self) -> bool:
        """Check if new workflows can be started.

        Workflows can only start in RUNNING mode.

        Returns:
            True if workflows can be started
        """
        return self._mode == Mode.RUNNING
