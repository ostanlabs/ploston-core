"""Unit tests for ModeManager."""

import asyncio

import pytest

from ploston_core.config.mode_manager import Mode, ModeManager


class TestMode:
    """Tests for Mode enum."""

    def test_mode_values(self):
        """Test Mode enum has expected values."""
        assert Mode.CONFIGURATION.value == "configuration"
        assert Mode.RUNNING.value == "running"

    def test_mode_members(self):
        """Test Mode enum has exactly two members."""
        assert len(Mode) == 2
        assert Mode.CONFIGURATION in Mode
        assert Mode.RUNNING in Mode


class TestModeManager:
    """Tests for ModeManager class."""

    def test_default_initial_mode(self):
        """Test default initial mode is CONFIGURATION."""
        manager = ModeManager()
        assert manager.mode == Mode.CONFIGURATION
        assert manager.is_configuration_mode()
        assert not manager.is_running_mode()

    def test_custom_initial_mode(self):
        """Test custom initial mode."""
        manager = ModeManager(initial_mode=Mode.RUNNING)
        assert manager.mode == Mode.RUNNING
        assert manager.is_running_mode()
        assert not manager.is_configuration_mode()

    def test_set_mode(self):
        """Test setting mode."""
        manager = ModeManager()
        manager.set_mode(Mode.RUNNING)
        assert manager.mode == Mode.RUNNING
        assert manager.is_running_mode()

    def test_set_same_mode_no_callback(self):
        """Test setting same mode doesn't trigger callback."""
        manager = ModeManager()
        callback_count = 0

        def callback(mode: Mode):
            nonlocal callback_count
            callback_count += 1

        manager.on_mode_change(callback)
        manager.set_mode(Mode.CONFIGURATION)  # Same mode
        assert callback_count == 0

    def test_mode_change_callback(self):
        """Test mode change triggers callback."""
        manager = ModeManager()
        received_modes: list[Mode] = []

        def callback(mode: Mode):
            received_modes.append(mode)

        manager.on_mode_change(callback)
        manager.set_mode(Mode.RUNNING)

        assert len(received_modes) == 1
        assert received_modes[0] == Mode.RUNNING

    def test_multiple_callbacks(self):
        """Test multiple callbacks are all called."""
        manager = ModeManager()
        callback1_called = False
        callback2_called = False

        def callback1(mode: Mode):
            nonlocal callback1_called
            callback1_called = True

        def callback2(mode: Mode):
            nonlocal callback2_called
            callback2_called = True

        manager.on_mode_change(callback1)
        manager.on_mode_change(callback2)
        manager.set_mode(Mode.RUNNING)

        assert callback1_called
        assert callback2_called

    def test_callback_error_doesnt_break_transition(self):
        """Test callback error doesn't prevent mode change."""
        manager = ModeManager()
        good_callback_called = False

        def bad_callback(mode: Mode):
            raise RuntimeError("Callback error")

        def good_callback(mode: Mode):
            nonlocal good_callback_called
            good_callback_called = True

        manager.on_mode_change(bad_callback)
        manager.on_mode_change(good_callback)
        manager.set_mode(Mode.RUNNING)

        assert manager.mode == Mode.RUNNING
        assert good_callback_called

    def test_remove_callback(self):
        """Test removing a callback."""
        manager = ModeManager()
        callback_count = 0

        def callback(mode: Mode):
            nonlocal callback_count
            callback_count += 1

        manager.on_mode_change(callback)
        result = manager.remove_mode_change_callback(callback)

        assert result is True
        manager.set_mode(Mode.RUNNING)
        assert callback_count == 0

    def test_remove_nonexistent_callback(self):
        """Test removing non-existent callback returns False."""
        manager = ModeManager()

        def callback(mode: Mode):
            pass

        result = manager.remove_mode_change_callback(callback)
        assert result is False

    def test_workflow_count_initial(self):
        """Test initial workflow count is zero."""
        manager = ModeManager()
        assert manager.running_workflow_count == 0

    def test_increment_workflow_count(self):
        """Test incrementing workflow count."""
        manager = ModeManager()
        manager.increment_running_workflows()
        assert manager.running_workflow_count == 1
        manager.increment_running_workflows()
        assert manager.running_workflow_count == 2

    def test_decrement_workflow_count(self):
        """Test decrementing workflow count."""
        manager = ModeManager()
        manager.increment_running_workflows()
        manager.increment_running_workflows()
        manager.decrement_running_workflows()
        assert manager.running_workflow_count == 1

    def test_decrement_workflow_count_floor(self):
        """Test workflow count doesn't go below zero."""
        manager = ModeManager()
        manager.decrement_running_workflows()
        assert manager.running_workflow_count == 0

    def test_can_start_workflow_configuration_mode(self):
        """Test can't start workflow in configuration mode."""
        manager = ModeManager(initial_mode=Mode.CONFIGURATION)
        assert not manager.can_start_workflow()

    def test_can_start_workflow_running_mode(self):
        """Test can start workflow in running mode."""
        manager = ModeManager(initial_mode=Mode.RUNNING)
        assert manager.can_start_workflow()


class _FakeRedisStore:
    """Minimal stand-in for RedisConfigStore used to exercise persistence."""

    def __init__(self, connected: bool = True):
        self.connected = connected
        self.set_mode_calls: list[str] = []

    async def set_mode(self, value: str) -> None:
        self.set_mode_calls.append(value)


class TestModeManagerRedisPersistence:
    """Tests for the Redis persistence path in set_mode.

    These cover the asyncio.get_event_loop() -> get_running_loop()/asyncio.run()
    migration: behavior must be preserved for both the "no running loop"
    (sync caller) and "running loop" (async caller) cases.
    """

    def test_set_mode_persists_when_no_running_loop(self):
        """Sync caller with no running loop persists synchronously via asyncio.run."""
        store = _FakeRedisStore(connected=True)
        manager = ModeManager(redis_store=store)

        manager.set_mode(Mode.RUNNING)

        assert manager.mode == Mode.RUNNING
        # asyncio.run path runs the coroutine to completion before returning.
        assert store.set_mode_calls == ["RUNNING"]

    def test_set_mode_skips_persist_when_not_connected(self):
        """No persistence attempted when the store is not connected."""
        store = _FakeRedisStore(connected=False)
        manager = ModeManager(redis_store=store)

        manager.set_mode(Mode.RUNNING)

        assert manager.mode == Mode.RUNNING
        assert store.set_mode_calls == []

    @pytest.mark.asyncio
    async def test_set_mode_persists_when_running_loop(self):
        """Async caller (running loop) schedules persistence fire-and-forget."""
        store = _FakeRedisStore(connected=True)
        manager = ModeManager(redis_store=store)

        manager.set_mode(Mode.RUNNING)

        assert manager.mode == Mode.RUNNING
        # create_task schedules but does not await; yield control so it runs.
        await asyncio.sleep(0)
        assert store.set_mode_calls == ["RUNNING"]
