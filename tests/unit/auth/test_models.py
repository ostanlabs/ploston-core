"""Unit tests for Pro Auth Foundation models."""

from ploston_core.auth import (
    ANONYMOUS_PRINCIPAL,
    Principal,
    PrincipalContext,
    PrincipalType,
    Scope,
    ToolAccess,
    ToolAccessMode,
)


class TestScope:
    """Tests for Scope enum."""

    def test_scope_values(self):
        """Test scope enum values."""
        assert Scope.READ.value == "read"
        assert Scope.EXECUTE.value == "execute"
        assert Scope.WRITE.value == "write"
        assert Scope.ADMIN.value == "admin"

    def test_scope_from_string(self):
        """Test creating scope from string."""
        assert Scope("read") == Scope.READ
        assert Scope("execute") == Scope.EXECUTE
        assert Scope("write") == Scope.WRITE
        assert Scope("admin") == Scope.ADMIN


class TestPrincipalType:
    """Tests for PrincipalType enum."""

    def test_principal_type_values(self):
        """Test principal type enum values."""
        assert PrincipalType.USER.value == "user"
        assert PrincipalType.SERVICE.value == "service"


class TestToolAccess:
    """Tests for ToolAccess model."""

    def test_all_mode_allows_everything(self):
        """Test ALL mode allows all servers."""
        access = ToolAccess(mode=ToolAccessMode.ALL, servers=[])
        assert access.can_access_server("any-server")
        assert access.can_access_server("another-server")

    def test_allowlist_mode(self):
        """Test ALLOWLIST mode only allows listed servers."""
        access = ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=["server-a", "server-b"])
        assert access.can_access_server("server-a")
        assert access.can_access_server("server-b")
        assert not access.can_access_server("server-c")

    def test_denylist_mode(self):
        """Test DENYLIST mode blocks listed servers."""
        access = ToolAccess(mode=ToolAccessMode.DENYLIST, servers=["blocked-server"])
        assert access.can_access_server("allowed-server")
        assert not access.can_access_server("blocked-server")


class TestPrincipal:
    """Tests for Principal model."""

    def test_principal_creation(self):
        """Test creating a principal."""
        principal = Principal(
            id="usr_test",
            name="test-user",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ, Scope.EXECUTE},
        )
        assert principal.id == "usr_test"
        assert principal.name == "test-user"
        assert principal.type == PrincipalType.USER
        assert Scope.READ in principal.scopes
        assert Scope.EXECUTE in principal.scopes
        assert principal.enabled is True

    def test_has_scope(self):
        """Test has_scope method."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ, Scope.EXECUTE},
        )
        assert principal.has_scope(Scope.READ)
        assert principal.has_scope(Scope.EXECUTE)
        assert not principal.has_scope(Scope.WRITE)
        assert not principal.has_scope(Scope.ADMIN)

    def test_has_any_scope(self):
        """Test has_any_scope method."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
        )
        assert principal.has_any_scope({Scope.READ, Scope.WRITE})
        assert not principal.has_any_scope({Scope.WRITE, Scope.ADMIN})

    def test_tool_access_default(self):
        """Test default tool access is ALL."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
        )
        assert principal.tool_access.mode == ToolAccessMode.ALL


class TestAnonymousPrincipal:
    """Tests for ANONYMOUS_PRINCIPAL constant."""

    def test_anonymous_principal_has_all_scopes(self):
        """Test anonymous principal has all scopes."""
        assert ANONYMOUS_PRINCIPAL.has_scope(Scope.READ)
        assert ANONYMOUS_PRINCIPAL.has_scope(Scope.EXECUTE)
        assert ANONYMOUS_PRINCIPAL.has_scope(Scope.WRITE)
        assert ANONYMOUS_PRINCIPAL.has_scope(Scope.ADMIN)

    def test_anonymous_principal_has_all_tool_access(self):
        """Test anonymous principal has ALL tool access."""
        assert ANONYMOUS_PRINCIPAL.tool_access.mode == ToolAccessMode.ALL

    def test_anonymous_principal_id(self):
        """Test anonymous principal ID."""
        assert ANONYMOUS_PRINCIPAL.id == "anon"
        assert ANONYMOUS_PRINCIPAL.name == "anonymous"


class TestPrincipalContext:
    """Tests for PrincipalContext model."""

    def test_context_creation(self):
        """Test creating a principal context."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        assert context.principal == principal
        assert context.api_key_prefix == "plt_test"
