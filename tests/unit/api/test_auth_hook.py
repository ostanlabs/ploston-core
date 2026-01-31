"""Tests for Auth Hook pattern.

Implements S-188: Auth Hook Pattern
- UT-110: AuthHook base class
- UT-111: auth_hook global instance
- UT-112: RunnerPermissions constants
- UT-113: Enterprise override pattern
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from ploston_core.api.auth import AuthHook, RunnerPermissions, auth_hook


class TestAuthHookBase:
    """Tests for AuthHook base class (UT-110)."""

    @pytest.mark.asyncio
    async def test_check_permission_allows_all(self):
        """Test that OSS auth hook allows all requests."""
        hook = AuthHook()
        request = MagicMock()
        
        # Should not raise
        await hook.check_permission(request, "runner:create")
        await hook.check_permission(request, "runner:delete")
        await hook.check_permission(request, "any:permission")

    @pytest.mark.asyncio
    async def test_get_user_returns_none(self):
        """Test that OSS auth hook returns None for user."""
        hook = AuthHook()
        request = MagicMock()
        
        user = await hook.get_user(request)
        assert user is None

    @pytest.mark.asyncio
    async def test_get_user_id_returns_none(self):
        """Test that OSS auth hook returns None for user ID."""
        hook = AuthHook()
        request = MagicMock()
        
        user_id = await hook.get_user_id(request)
        assert user_id is None


class TestGlobalAuthHook:
    """Tests for global auth_hook instance (UT-111)."""

    def test_auth_hook_is_auth_hook_instance(self):
        """Test that global auth_hook is an AuthHook instance."""
        assert isinstance(auth_hook, AuthHook)

    @pytest.mark.asyncio
    async def test_global_hook_allows_all(self):
        """Test that global hook allows all requests."""
        request = MagicMock()
        
        # Should not raise
        await auth_hook.check_permission(request, "runner:create")


class TestRunnerPermissions:
    """Tests for RunnerPermissions constants (UT-112)."""

    def test_create_permission(self):
        """Test CREATE permission constant."""
        assert RunnerPermissions.CREATE == "runner:create"

    def test_list_permission(self):
        """Test LIST permission constant."""
        assert RunnerPermissions.LIST == "runner:list"

    def test_read_permission(self):
        """Test READ permission constant."""
        assert RunnerPermissions.READ == "runner:read"

    def test_delete_permission(self):
        """Test DELETE permission constant."""
        assert RunnerPermissions.DELETE == "runner:delete"

    def test_update_permission(self):
        """Test UPDATE permission constant."""
        assert RunnerPermissions.UPDATE == "runner:update"


class TestEnterpriseOverride:
    """Tests for Enterprise override pattern (UT-113)."""

    @pytest.mark.asyncio
    async def test_enterprise_hook_can_deny(self):
        """Test that Enterprise hook can deny requests."""
        
        class EnterpriseAuthHook(AuthHook):
            async def check_permission(self, request, permission: str) -> None:
                if permission == "runner:delete":
                    raise HTTPException(status_code=403, detail="Permission denied")
        
        hook = EnterpriseAuthHook()
        request = MagicMock()
        
        # Should allow create
        await hook.check_permission(request, "runner:create")
        
        # Should deny delete
        with pytest.raises(HTTPException) as exc_info:
            await hook.check_permission(request, "runner:delete")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_enterprise_hook_can_return_user(self):
        """Test that Enterprise hook can return user."""
        
        class MockUser:
            def __init__(self, user_id: str):
                self.id = user_id
        
        class EnterpriseAuthHook(AuthHook):
            async def get_user(self, request) -> MockUser:
                return MockUser("user-123")
        
        hook = EnterpriseAuthHook()
        request = MagicMock()
        
        user = await hook.get_user(request)
        assert user.id == "user-123"
        
        user_id = await hook.get_user_id(request)
        assert user_id == "user-123"

    def test_can_replace_global_hook(self):
        """Test that global hook can be replaced at runtime."""
        import ploston_core.api.auth as auth_module
        
        original_hook = auth_module.auth_hook
        
        class CustomHook(AuthHook):
            pass
        
        # Replace
        auth_module.auth_hook = CustomHook()
        assert isinstance(auth_module.auth_hook, CustomHook)
        
        # Restore
        auth_module.auth_hook = original_hook
        assert auth_module.auth_hook is original_hook
