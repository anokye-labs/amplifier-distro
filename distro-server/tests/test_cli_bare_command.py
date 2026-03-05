"""Tests for running amp-distro without a subcommand (implicit serve).

Tests cover:
1. Bare 'amp-distro' (no subcommand) starts the server via serve_cmd
2. --host and --port overrides are forwarded to _run_foreground
3. All serve flags (--dev, --stub, --tls, --no-auth, etc.) work at group level
4. Subcommands still work (serve, backup, doctor) when options are on the group
5. Help output shows serve options at the top level
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from amplifier_distro.cli import main


class TestBareCommandStartsServer:
    """'amp-distro' with no subcommand must behave like 'amp-distro serve'."""

    def test_bare_invocation_calls_run_foreground(self) -> None:
        """'amp-distro' with no args starts the server."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_bare_defaults_match_serve_defaults(self) -> None:
        """Bare invocation passes the same defaults as 'amp-distro serve'."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "off"
        assert kwargs.get("ssl_certfile") == ""
        assert kwargs.get("ssl_keyfile") == ""
        assert kwargs.get("no_auth") is False


class TestBareCommandOptionForwarding:
    """Serve options on the bare command are forwarded to _run_foreground."""

    def test_host_override(self) -> None:
        """'amp-distro --host 127.0.0.1' forwards host."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--host", "127.0.0.1"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == "127.0.0.1"

    def test_port_override(self) -> None:
        """'amp-distro --port 9000' forwards port."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--port", "9000"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[1] == 9000

    def test_host_and_port_together(self) -> None:
        """'amp-distro --host 127.0.0.1 --port 9000' forwards both."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--host", "127.0.0.1", "--port", "9000"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == "127.0.0.1"
        assert args[1] == 9000

    def test_dev_flag(self) -> None:
        """'amp-distro --dev' forwards dev=True."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--dev"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[4] is True  # dev is 5th positional arg

    def test_stub_implies_dev(self) -> None:
        """'amp-distro --stub' enables both stub and dev."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--stub"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("stub") is True
        args, _ = mock_run.call_args
        assert args[4] is True  # dev forced on by --stub

    def test_tls_auto(self) -> None:
        """'amp-distro --tls auto' forwards tls_mode='auto'."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--tls", "auto"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "auto"

    def test_no_auth_flag(self) -> None:
        """'amp-distro --no-auth' forwards no_auth=True."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--no-auth"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("no_auth") is True

    def test_ssl_certfile_implies_manual(self) -> None:
        """'amp-distro --ssl-certfile /tmp/c.pem' implies manual TLS."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--ssl-certfile", "/tmp/c.pem"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("tls_mode") == "manual"
        assert kwargs.get("ssl_certfile") == "/tmp/c.pem"

    def test_reload_flag(self) -> None:
        """'amp-distro --reload' forwards reload=True."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["--reload"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[3] is True  # reload is 4th positional arg


class TestBareCommandDoesNotBreakSubcommands:
    """Subcommands must still work with invoke_without_command=True."""

    def test_serve_subcommand_still_works(self) -> None:
        """'amp-distro serve --port 9000' still works."""
        runner = CliRunner()
        mock_run = MagicMock()
        with patch("amplifier_distro.server.cli._run_foreground", mock_run):
            result = runner.invoke(main, ["serve", "--port", "9000"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[1] == 9000

    def test_doctor_subcommand_not_affected(self) -> None:
        """'amp-distro doctor --help' still works."""
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "Diagnose" in result.output

    def test_backup_subcommand_not_affected(self) -> None:
        """'amp-distro backup --help' still works."""
        runner = CliRunner()
        result = runner.invoke(main, ["backup", "--help"])
        assert result.exit_code == 0
        assert "Back up" in result.output


class TestBareCommandHelp:
    """Help output shows serve options at the top level."""

    def test_help_shows_host(self) -> None:
        """'amp-distro --help' lists --host."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--host" in result.output

    def test_help_shows_port(self) -> None:
        """'amp-distro --help' lists --port."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--port" in result.output

    def test_help_shows_dev(self) -> None:
        """'amp-distro --help' lists --dev."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--dev" in result.output

    def test_help_shows_tls(self) -> None:
        """'amp-distro --help' lists --tls."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--tls" in result.output
