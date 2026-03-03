"""Tests for server-side slash command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def make_connection(session_id: str = "test-sess"):
    from amplifier_distro.server.apps.chat.connection import ChatConnection

    ws = MagicMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    backend = MagicMock()
    backend.create_session = AsyncMock(
        return_value=MagicMock(session_id="new-sess", working_dir="/new")
    )
    backend.cancel_session = AsyncMock(return_value=None)
    backend.end_session = AsyncMock(return_value=None)
    config = MagicMock()
    config.server.api_key = None
    conn = ChatConnection(ws, backend, config)
    conn._session_id = session_id
    return conn, ws, backend


class TestCommandDispatch:
    @pytest.mark.asyncio
    async def test_status_command_returns_session_id(self):
        """status command returns current session_id and status."""
        conn, _ws, _backend = make_connection("sess-001")
        result = await conn._dispatch_command("status", [])
        assert result["session_id"] == "sess-001"
        assert "status" in result

    @pytest.mark.asyncio
    async def test_status_command_no_session(self):
        """status command with no session returns no_session status."""
        conn, _ws, _backend = make_connection()
        conn._session_id = None
        result = await conn._dispatch_command("status", [])
        assert result["session_id"] is None
        assert result["status"] == "no_session"

    @pytest.mark.asyncio
    async def test_bundle_command_creates_new_session(self):
        """bundle command creates a new session with the specified bundle."""
        conn, _ws, backend = make_connection()
        result = await conn._dispatch_command("bundle", ["my-bundle"])
        backend.create_session.assert_awaited_once()
        assert "session_id" in result

    @pytest.mark.asyncio
    async def test_bundle_command_passes_bundle_name(self):
        """bundle command passes the bundle name to create_session."""
        conn, _ws, backend = make_connection()
        await conn._dispatch_command("bundle", ["foundation"])
        call_kwargs = backend.create_session.call_args.kwargs
        assert call_kwargs.get("bundle_name") == "foundation"

    @pytest.mark.asyncio
    async def test_cwd_command_creates_new_session(self):
        """cwd command creates a new session with the specified working directory."""
        conn, _ws, backend = make_connection()
        result = await conn._dispatch_command("cwd", ["/new/path"])
        backend.create_session.assert_awaited_once()
        assert "cwd" in result

    @pytest.mark.asyncio
    async def test_cwd_command_passes_working_dir(self):
        """cwd command passes the new cwd to create_session."""
        conn, _ws, backend = make_connection()
        await conn._dispatch_command("cwd", ["/home/user/projects"])
        call_kwargs = backend.create_session.call_args.kwargs
        assert call_kwargs.get("working_dir") == "/home/user/projects"

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error(self):
        """Unknown commands return an error dict with 'error' key."""
        conn, _ws, _backend = make_connection()
        result = await conn._dispatch_command("nonexistent", [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_bundle_command_no_args_returns_error(self):
        """bundle command with no args falls to unknown command path."""
        conn, _ws, _backend = make_connection()
        result = await conn._dispatch_command("bundle", [])
        # bundle without args doesn't match the 'bundle' if args case
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cwd_command_no_args_returns_error(self):
        """cwd command with no args falls to unknown command path."""
        conn, _ws, _backend = make_connection()
        result = await conn._dispatch_command("cwd", [])
        assert "error" in result


class TestToolsCommand:
    @pytest.mark.asyncio
    async def test_tools_returns_tool_list(self):
        """tools command returns formatted tool list from backend."""
        conn, _ws, backend = make_connection()
        backend.list_tools = MagicMock(return_value=[
            {"name": "bash", "description": "Execute shell commands"},
            {"name": "read_file", "description": "Read file contents"},
        ])
        result = await conn._dispatch_command("tools", [])
        assert result["type"] == "tools"
        assert len(result["tools"]) == 2
        assert result["tools"][0]["name"] == "bash"
        backend.list_tools.assert_called_once_with("test-sess")

    @pytest.mark.asyncio
    async def test_tools_no_session_returns_error(self):
        """tools command with no session returns error."""
        conn, _ws, _backend = make_connection()
        conn._session_id = None
        result = await conn._dispatch_command("tools", [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tools_backend_returns_none(self):
        """tools command handles backend returning None."""
        conn, _ws, backend = make_connection()
        backend.list_tools = MagicMock(return_value=None)
        result = await conn._dispatch_command("tools", [])
        assert "error" in result


class TestAgentsCommand:
    @pytest.mark.asyncio
    async def test_agents_returns_agent_list(self):
        """agents command returns filtered agent list from config."""
        conn, _ws, backend = make_connection()
        backend.get_session_config = MagicMock(return_value={
            "agents": {
                "foundation:explorer": {"description": "Explores code"},
                "foundation:git-ops": {"description": "Git operations"},
                "dirs": ["/some/path"],
                "include": ["something"],
            }
        })
        result = await conn._dispatch_command("agents", [])
        assert result["type"] == "agents"
        assert len(result["agents"]) == 2
        names = [a["name"] for a in result["agents"]]
        assert "foundation:explorer" in names
        assert "foundation:git-ops" in names
        # Structural keys filtered out
        assert "dirs" not in names
        assert "include" not in names

    @pytest.mark.asyncio
    async def test_agents_no_session_returns_error(self):
        """agents command with no session returns error."""
        conn, _ws, _backend = make_connection()
        conn._session_id = None
        result = await conn._dispatch_command("agents", [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_agents_empty_config(self):
        """agents command with empty agents config returns empty list."""
        conn, _ws, backend = make_connection()
        backend.get_session_config = MagicMock(return_value={"agents": {}})
        result = await conn._dispatch_command("agents", [])
        assert result["type"] == "agents"
        assert result["agents"] == []


class TestModesCommand:
    @pytest.mark.asyncio
    async def test_modes_returns_mode_list(self):
        """modes command returns available modes from backend."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": None,
            "modes": [
                {"name": "plan", "description": "Planning mode", "source": "modes"},
                {"name": "explore", "description": "Explore mode", "source": "modes"},
            ],
        })
        result = await conn._dispatch_command("modes", [])
        assert result["type"] == "modes"
        assert len(result["modes"]) == 2
        assert result["active_mode"] is None
        backend.list_modes.assert_called_once_with("test-sess")

    @pytest.mark.asyncio
    async def test_modes_no_session_returns_error(self):
        """modes command with no session returns error."""
        conn, _ws, _backend = make_connection()
        conn._session_id = None
        result = await conn._dispatch_command("modes", [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_modes_shows_active_mode(self):
        """modes command includes active_mode when one is set."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": "plan",
            "modes": [{"name": "plan", "description": "Plan", "source": "modes"}],
        })
        result = await conn._dispatch_command("modes", [])
        assert result["active_mode"] == "plan"


class TestModeCommand:
    @pytest.mark.asyncio
    async def test_mode_no_args_shows_current(self):
        """mode with no args shows current active mode."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": "plan",
            "modes": [],
        })
        result = await conn._dispatch_command("mode", [])
        assert result["type"] == "mode"
        assert result["active_mode"] == "plan"

    @pytest.mark.asyncio
    async def test_mode_no_args_no_active(self):
        """mode with no args and no active mode shows message."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": None,
            "modes": [],
        })
        result = await conn._dispatch_command("mode", [])
        assert result["type"] == "mode"
        assert result["active_mode"] is None
        assert "message" in result

    @pytest.mark.asyncio
    async def test_mode_activate(self):
        """mode <name> activates the mode."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": None,
            "modes": [],
        })
        backend.set_mode = MagicMock(return_value={
            "active_mode": "plan",
            "previous_mode": None,
        })
        result = await conn._dispatch_command("mode", ["plan"])
        assert result["type"] == "mode"
        assert result["active_mode"] == "plan"
        backend.set_mode.assert_called_once_with("test-sess", "plan")

    @pytest.mark.asyncio
    async def test_mode_off_deactivates(self):
        """mode off deactivates the current mode."""
        conn, _ws, backend = make_connection()
        backend.set_mode = MagicMock(return_value={
            "active_mode": None,
            "previous_mode": "plan",
        })
        result = await conn._dispatch_command("mode", ["off"])
        assert result["type"] == "mode"
        assert result["active_mode"] is None
        assert result["previous_mode"] == "plan"
        backend.set_mode.assert_called_once_with("test-sess", None)

    @pytest.mark.asyncio
    async def test_mode_name_on(self):
        """mode <name> on force-activates."""
        conn, _ws, backend = make_connection()
        backend.set_mode = MagicMock(return_value={
            "active_mode": "explore",
            "previous_mode": None,
        })
        result = await conn._dispatch_command("mode", ["explore", "on"])
        assert result["active_mode"] == "explore"
        backend.set_mode.assert_called_once_with("test-sess", "explore")

    @pytest.mark.asyncio
    async def test_mode_name_off(self):
        """mode <name> off deactivates only if that mode is active."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": "plan",
            "modes": [],
        })
        backend.set_mode = MagicMock(return_value={
            "active_mode": None,
            "previous_mode": "plan",
        })
        result = await conn._dispatch_command("mode", ["plan", "off"])
        assert result["active_mode"] is None
        backend.set_mode.assert_called_once_with("test-sess", None)

    @pytest.mark.asyncio
    async def test_mode_name_off_not_active(self):
        """mode <name> off when a different mode is active does nothing."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": "explore",
            "modes": [],
        })
        result = await conn._dispatch_command("mode", ["plan", "off"])
        assert result["active_mode"] == "explore"
        assert result["message"] == "Not active"

    @pytest.mark.asyncio
    async def test_mode_toggle_off(self):
        """mode <name> toggles off if already active."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": "plan",
            "modes": [],
        })
        backend.set_mode = MagicMock(return_value={
            "active_mode": None,
            "previous_mode": "plan",
        })
        result = await conn._dispatch_command("mode", ["plan"])
        # Should toggle OFF since plan is already active
        backend.set_mode.assert_called_once_with("test-sess", None)

    @pytest.mark.asyncio
    async def test_mode_toggle_on(self):
        """mode <name> toggles on if not active."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": None,
            "modes": [],
        })
        backend.set_mode = MagicMock(return_value={
            "active_mode": "plan",
            "previous_mode": None,
        })
        result = await conn._dispatch_command("mode", ["plan"])
        backend.set_mode.assert_called_once_with("test-sess", "plan")

    @pytest.mark.asyncio
    async def test_mode_no_session_returns_error(self):
        """mode command with no session returns error."""
        conn, _ws, _backend = make_connection()
        conn._session_id = None
        result = await conn._dispatch_command("mode", [])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_mode_set_error_propagated(self):
        """mode command propagates error from set_mode."""
        conn, _ws, backend = make_connection()
        backend.list_modes = MagicMock(return_value={
            "active_mode": None,
            "modes": [],
        })
        backend.set_mode = MagicMock(return_value={
            "error": "Mode not found: nonexistent",
            "available_modes": ["plan", "explore"],
        })
        result = await conn._dispatch_command("mode", ["nonexistent"])
        assert "error" in result
