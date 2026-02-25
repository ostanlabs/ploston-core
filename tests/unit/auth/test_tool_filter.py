"""Unit tests for Pro Auth Foundation tool filtering."""

from dataclasses import dataclass

from ploston_core.auth import (
    Principal,
    PrincipalContext,
    PrincipalType,
    Scope,
    ToolAccess,
    ToolAccessMode,
    can_access_tool,
    filter_tools_by_principal,
)


@dataclass
class MockToolInfo:
    """Mock ToolInfo for testing."""

    name: str
    server_name: str | None


class TestFilterToolsByPrincipal:
    """Tests for filter_tools_by_principal function."""

    def test_no_context_returns_all(self):
        """Test no principal context returns all tools."""
        tools = [
            MockToolInfo("tool1", "server-a"),
            MockToolInfo("tool2", "server-b"),
        ]
        result = filter_tools_by_principal(tools, None)
        assert len(result) == 2

    def test_all_mode_returns_all(self):
        """Test ALL mode returns all tools."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.ALL, servers=[]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        tools = [
            MockToolInfo("tool1", "server-a"),
            MockToolInfo("tool2", "server-b"),
        ]
        result = filter_tools_by_principal(tools, context)
        assert len(result) == 2

    def test_allowlist_filters_tools(self):
        """Test ALLOWLIST mode filters tools."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=["server-a"]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        tools = [
            MockToolInfo("tool1", "server-a"),
            MockToolInfo("tool2", "server-b"),
        ]
        result = filter_tools_by_principal(tools, context)
        assert len(result) == 1
        assert result[0].name == "tool1"

    def test_denylist_filters_tools(self):
        """Test DENYLIST mode filters tools."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.DENYLIST, servers=["server-b"]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        tools = [
            MockToolInfo("tool1", "server-a"),
            MockToolInfo("tool2", "server-b"),
        ]
        result = filter_tools_by_principal(tools, context)
        assert len(result) == 1
        assert result[0].name == "tool1"

    def test_bridge_filter_intersection(self):
        """Test intersection with bridge filter."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=["server-a", "server-b"]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        tools = [
            MockToolInfo("tool1", "server-a"),
            MockToolInfo("tool2", "server-b"),
            MockToolInfo("tool3", "server-c"),
        ]
        # Bridge only allows server-a
        result = filter_tools_by_principal(tools, context, bridge_filter_servers=["server-a"])
        assert len(result) == 1
        assert result[0].name == "tool1"


class TestCanAccessTool:
    """Tests for can_access_tool function."""

    def test_no_context_allows_all(self):
        """Test no context allows all tools."""
        assert can_access_tool("server-a", None)

    def test_all_mode_allows_all(self):
        """Test ALL mode allows all servers."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.ALL, servers=[]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        assert can_access_tool("any-server", context)

    def test_allowlist_checks_server(self):
        """Test ALLOWLIST mode checks server."""
        principal = Principal(
            id="usr_test",
            name="test",
            type=PrincipalType.USER,
            api_key_prefix="plt_test",
            scopes={Scope.READ},
            tool_access=ToolAccess(mode=ToolAccessMode.ALLOWLIST, servers=["allowed"]),
        )
        context = PrincipalContext(principal=principal, api_key_prefix="plt_test")
        assert can_access_tool("allowed", context)
        assert not can_access_tool("blocked", context)
