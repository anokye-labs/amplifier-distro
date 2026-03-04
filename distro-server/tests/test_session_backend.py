"""Tests for FoundationBackend concurrency fixes (Issue #57, Fix 4).

FoundationBackend is production-only (requires amplifier-foundation).
All tests mock the bridge and session handles so they run in CI
without a real Amplifier installation.
"""

import asyncio
import os
import sys
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_handle(session_id: str = "test-session-0001") -> MagicMock:
    """Build a mock SessionHandle with a controllable run() method."""
    handle = MagicMock()
    handle.session_id = session_id
    handle.project_id = "test-project"
    handle.working_dir = "/tmp/test"
    handle.run = AsyncMock(return_value=f"[response from {session_id}]")
    handle.cleanup = AsyncMock()
    return handle


@pytest.fixture
def bridge_backend():
    """FoundationBackend with mocked LocalBridge."""
    target = "amplifier_distro.server.session_backend.FoundationBackend.__init__"
    with patch(target) as mock_init:
        mock_init.return_value = None  # suppress real __init__

        from amplifier_distro.server.session_backend import FoundationBackend

        backend = FoundationBackend.__new__(FoundationBackend)
        backend._bundle_name = "test-bundle"
        backend._sessions = {}
        backend._reconnect_locks = {}
        backend._session_queues = {}
        backend._worker_tasks = {}
        backend._ended_sessions = set()
        backend._approval_systems = {}
        backend._wired_sessions = set()
        backend._queue_holders = {}
        backend._event_forwarders = {}
        return backend


class TestFoundationBackendDefaultBundleName:
    """The default bundle_name must be a full git URI, not a bare name."""

    def test_default_bundle_name_is_full_git_uri(self):
        """FoundationBackend default _bundle_name must be a resolvable git URI.

        Bare names like 'amplifier-start' cannot be resolved by
        amplifier_foundation.load_bundle() — the full git+https:// URI
        is required.
        """
        from amplifier_distro.features import AMPLIFIER_START_URI
        from amplifier_distro.server.session_backend import FoundationBackend

        backend = FoundationBackend()
        assert backend._bundle_name == AMPLIFIER_START_URI
        assert backend._bundle_name.startswith("git+https://"), (
            f"Default bundle_name must be a git URI, got: {backend._bundle_name}"
        )


class TestFoundationBackendQueueInfrastructure:
    """Verify the queue-based session worker infrastructure."""

    def test_backend_has_session_queues_dict(self, bridge_backend):
        assert hasattr(bridge_backend, "_session_queues")
        assert isinstance(bridge_backend._session_queues, dict)

    def test_backend_has_worker_tasks_dict(self, bridge_backend):
        assert hasattr(bridge_backend, "_worker_tasks")
        assert isinstance(bridge_backend._worker_tasks, dict)

    def test_backend_has_ended_sessions_set(self, bridge_backend):
        assert hasattr(bridge_backend, "_ended_sessions")
        assert isinstance(bridge_backend._ended_sessions, set)

    async def test_create_session_starts_worker_task(self, bridge_backend):
        """create_session() must pre-start a session worker."""
        # Mock the foundation bundle loading chain:
        # _load_bundle() -> prepared -> prepared.create_session() -> session
        mock_session = MagicMock()
        mock_session.session_id = "sess-0001"
        mock_session.project_id = "test-project"

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.create_session(
            bridge_backend,
            working_dir="/tmp",
            description="test",
        )

        assert "sess-0001" in bridge_backend._worker_tasks
        worker = bridge_backend._worker_tasks["sess-0001"]
        assert not worker.done(), "Worker task should still be running"
        # Cleanup
        worker.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await worker


class TestFoundationBackendCreateSession:
    """Verify FoundationBackend.create_session calls foundation correctly."""

    async def test_create_session_calls_load_bundle(self, bridge_backend):
        """create_session must call _load_bundle and prepared.create_session."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-create-001"

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)

        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        info = await FoundationBackend.create_session(
            bridge_backend,
            working_dir="/home/user/project",
            description="test session",
        )

        bridge_backend._load_bundle.assert_called_once()
        mock_prepared.create_session.assert_called_once()
        assert info.session_id == "sess-create-001"
        assert info.working_dir == "/home/user/project"
        assert info.description == "test session"
        assert info.is_active is True

        # Cleanup worker
        if "sess-create-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-create-001"].cancel()

    async def test_create_session_with_custom_bundle(self, bridge_backend):
        """create_session accepts an optional bundle_name override."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-custom-001"

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)

        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.create_session(
            bridge_backend,
            working_dir="/tmp",
            bundle_name="custom-bundle",
        )

        bridge_backend._load_bundle.assert_called_once_with("custom-bundle")

        # Cleanup worker
        if "sess-custom-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-custom-001"].cancel()

    async def test_create_session_returns_session_info(self, bridge_backend):
        """create_session returns a SessionInfo with correct fields."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-info-001"

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)

        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import (
            FoundationBackend,
            SessionInfo,
        )

        info = await FoundationBackend.create_session(
            bridge_backend,
            working_dir="~",
        )

        assert isinstance(info, SessionInfo)
        assert info.session_id == "sess-info-001"

        # Cleanup worker
        if "sess-info-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-info-001"].cancel()


class TestFoundationBackendSerialization:
    """Verify messages for the same session are serialized through a queue."""

    async def test_send_message_serializes_concurrent_calls(self, bridge_backend):
        """Concurrent send_message calls for the same session run sequentially."""
        session_id = "sess-serial-001"
        handle = _make_mock_handle(session_id)
        bridge_backend._sessions[session_id] = handle

        call_order = []

        async def ordered_run(message):
            call_order.append(f"start:{message}")
            await asyncio.sleep(0.01)
            call_order.append(f"end:{message}")
            return f"resp:{message}"

        handle.run = ordered_run

        from amplifier_distro.server.session_backend import FoundationBackend

        queue = asyncio.Queue()
        bridge_backend._session_queues[session_id] = queue
        bridge_backend._worker_tasks[session_id] = asyncio.create_task(
            FoundationBackend._session_worker(bridge_backend, session_id)  # type: ignore[attr-defined]
        )

        try:
            r1, r2 = await asyncio.gather(
                FoundationBackend.send_message(bridge_backend, session_id, "A"),
                FoundationBackend.send_message(bridge_backend, session_id, "B"),
            )
        finally:
            bridge_backend._worker_tasks[session_id].cancel()

        assert r1 == "resp:A" or r1 == "resp:B"
        assert r2 == "resp:A" or r2 == "resp:B"
        assert r1 != r2

        a_start = call_order.index("start:A")
        a_end = call_order.index("end:A")
        b_start = call_order.index("start:B")
        b_end = call_order.index("end:B")
        assert a_end < b_start or b_end < a_start, f"Calls interleaved: {call_order}"

    async def test_send_message_propagates_exceptions(self, bridge_backend):
        """If handle.run() raises, the exception propagates to the caller."""
        session_id = "sess-exc-001"
        handle = _make_mock_handle(session_id)
        handle.run = AsyncMock(side_effect=RuntimeError("LLM exploded"))
        bridge_backend._sessions[session_id] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        queue = asyncio.Queue()
        bridge_backend._session_queues[session_id] = queue
        bridge_backend._worker_tasks[session_id] = asyncio.create_task(
            FoundationBackend._session_worker(bridge_backend, session_id)  # type: ignore[attr-defined]
        )

        try:
            with pytest.raises(RuntimeError, match="LLM exploded"):
                await FoundationBackend.send_message(bridge_backend, session_id, "hi")
        finally:
            bridge_backend._worker_tasks[session_id].cancel()


class TestFoundationBackendSendMessageQueue:
    """send_message() routes through the per-session queue."""

    async def test_send_message_uses_queue(self, bridge_backend):
        """send_message() puts work on the queue; result comes back via future."""
        session_id = "sess-queue-001"
        handle = _make_mock_handle(session_id)
        bridge_backend._sessions[session_id] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        # Manually pre-start queue and worker (as create_session will do)
        queue = asyncio.Queue()
        bridge_backend._session_queues[session_id] = queue
        bridge_backend._worker_tasks[session_id] = asyncio.create_task(
            FoundationBackend._session_worker(bridge_backend, session_id)
        )

        try:
            result = await FoundationBackend.send_message(
                bridge_backend, session_id, "test message"
            )
        finally:
            bridge_backend._worker_tasks[session_id].cancel()

        assert result == f"[response from {session_id}]"
        handle.run.assert_called_once_with("test message")


class TestFoundationBackendCancellation:
    """Verify that cancelling the worker during handle.run() is clean."""

    async def test_no_double_task_done_on_cancel_during_run(self, bridge_backend):
        """Cancelling the worker during handle.run() must not raise ValueError."""
        session_id = "sess-cancel-run-001"
        handle = _make_mock_handle(session_id)
        bridge_backend._sessions[session_id] = handle

        run_started = asyncio.Event()

        async def slow_run(message):
            run_started.set()
            await asyncio.sleep(10)  # long enough to cancel
            return "never"

        handle.run = slow_run

        from amplifier_distro.server.session_backend import FoundationBackend

        queue = asyncio.Queue()
        bridge_backend._session_queues[session_id] = queue
        worker = asyncio.create_task(
            FoundationBackend._session_worker(bridge_backend, session_id)
        )
        bridge_backend._worker_tasks[session_id] = worker

        # Enqueue a message and wait for run() to start
        fut = asyncio.get_event_loop().create_future()
        await queue.put(("cancel-me", fut))
        await run_started.wait()

        # Cancel worker while handle.run() is in-flight
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

        # fut should be cancelled, queue should be consistent (no ValueError raised)
        assert fut.cancelled() or fut.done()
        # If we get here without ValueError, the bug is fixed


class TestFoundationBackendEndSession:
    """end_session() must tombstone, drain the worker, then call bridge.end_session."""

    async def test_end_session_adds_tombstone(self, bridge_backend):
        """Session ID is added to _ended_sessions before anything else."""
        session_id = "sess-end-001"
        handle = _make_mock_handle(session_id)
        bridge_backend._sessions[session_id] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.end_session(bridge_backend, session_id)

        assert session_id in bridge_backend._ended_sessions

    async def test_end_session_drains_worker(self, bridge_backend):
        """end_session() waits for in-flight work to complete before returning."""
        session_id = "sess-end-002"
        handle = _make_mock_handle(session_id)
        bridge_backend._sessions[session_id] = handle

        completed = []

        async def slow_run(message):
            await asyncio.sleep(0.03)
            completed.append(message)
            return f"done:{message}"

        handle.run = slow_run

        from amplifier_distro.server.session_backend import FoundationBackend

        # Pre-start worker
        queue: asyncio.Queue = asyncio.Queue()
        bridge_backend._session_queues[session_id] = queue
        bridge_backend._worker_tasks[session_id] = asyncio.create_task(
            FoundationBackend._session_worker(bridge_backend, session_id)
        )

        # Start a send (don't await yet) then immediately end
        send_task = asyncio.create_task(
            FoundationBackend.send_message(bridge_backend, session_id, "finishing")
        )
        await asyncio.sleep(0)  # let the message enqueue

        await FoundationBackend.end_session(bridge_backend, session_id)

        if not send_task.done():
            send_task.cancel()

        assert "finishing" in completed or send_task.done()

    async def test_reconnect_blocked_after_end_session(self, bridge_backend):
        """_reconnect() must raise ValueError for tombstoned sessions."""
        session_id = "sess-end-003"
        bridge_backend._ended_sessions.add(session_id)

        from amplifier_distro.server.session_backend import FoundationBackend

        with pytest.raises(ValueError, match="intentionally ended"):
            await FoundationBackend._reconnect(bridge_backend, session_id)


class TestFoundationBackendStop:
    """stop() sends sentinels to all workers and awaits them."""

    async def test_stop_signals_all_workers(self, bridge_backend):
        """stop() sends None sentinel to every active queue."""
        from amplifier_distro.server.session_backend import FoundationBackend

        for sid in ("sess-stop-001", "sess-stop-002"):
            handle = _make_mock_handle(sid)
            bridge_backend._sessions[sid] = handle
            queue: asyncio.Queue = asyncio.Queue()
            bridge_backend._session_queues[sid] = queue
            bridge_backend._worker_tasks[sid] = asyncio.create_task(
                FoundationBackend._session_worker(bridge_backend, sid)
            )

        await FoundationBackend.stop(bridge_backend)

        for task in bridge_backend._worker_tasks.values():
            assert task.done(), "Worker should be done after stop()"

    async def test_stop_is_idempotent_with_no_sessions(self, bridge_backend):
        """stop() on a backend with no sessions must not raise."""
        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.stop(bridge_backend)  # should not raise


class TestStopServicesShutdown:
    """stop_services() calls backend.stop() if available."""

    async def test_stop_services_calls_backend_stop(self):
        """stop_services() must call backend.stop() when the backend has it."""
        from amplifier_distro.server.services import (
            init_services,
            reset_services,
            stop_services,
        )

        mock_backend = AsyncMock()
        mock_backend.stop = AsyncMock()

        reset_services()
        init_services(backend=mock_backend)

        await stop_services()

        mock_backend.stop.assert_awaited_once()
        reset_services()

    async def test_stop_services_safe_without_stop_method(self):
        """stop_services() must not raise if backend lacks stop()."""
        from amplifier_distro.server.services import (
            init_services,
            reset_services,
            stop_services,
        )
        from amplifier_distro.server.session_backend import MockBackend

        reset_services()
        init_services(backend=MockBackend())

        await stop_services()  # MockBackend has no stop() — should not raise
        reset_services()

    async def test_stop_services_safe_before_init(self):
        """stop_services() must not raise if services were never initialized."""
        from amplifier_distro.server.services import reset_services, stop_services

        reset_services()
        await stop_services()  # should silently do nothing


class TestFoundationBackendReconnect:
    """Verify the _reconnect and resume_session methods."""

    async def test_reconnect_raises_for_ended_session(self, bridge_backend):
        """Tombstoned sessions raise ValueError on reconnect."""
        bridge_backend._ended_sessions.add("sess-ended-001")

        from amplifier_distro.server.session_backend import FoundationBackend

        with pytest.raises(ValueError, match="intentionally ended"):
            await FoundationBackend._reconnect(bridge_backend, "sess-ended-001")

    async def test_reconnect_raises_when_no_transcript(self, bridge_backend):
        """Missing transcript raises ValueError."""
        from amplifier_distro.server.session_backend import FoundationBackend

        def no_transcript(session_id):
            raise FileNotFoundError(f"No transcript found for session {session_id}")

        bridge_backend._find_transcript = no_transcript

        with pytest.raises(ValueError, match="Unknown session"):
            await FoundationBackend._reconnect(bridge_backend, "sess-missing-001")

    async def test_resume_session_delegates_to_reconnect(self, bridge_backend):
        """resume_session passes working_dir through to _reconnect."""
        mock_reconnect = AsyncMock()
        bridge_backend._reconnect = mock_reconnect

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.resume_session(
            bridge_backend, "sess-resume-001", working_dir="/custom/path"
        )

        mock_reconnect.assert_awaited_once_with(
            "sess-resume-001", working_dir="/custom/path"
        )

    async def test_resume_session_skips_if_already_cached(self, bridge_backend):
        """resume_session is a no-op if handle already exists."""
        from unittest.mock import MagicMock

        from amplifier_distro.server.session_backend import FoundationBackend

        mock_handle = MagicMock()
        bridge_backend._sessions["sess-cached-001"] = mock_handle

        mock_reconnect = AsyncMock()
        bridge_backend._reconnect = mock_reconnect

        await FoundationBackend.resume_session(
            bridge_backend, "sess-cached-001", working_dir="~"
        )

        mock_reconnect.assert_not_awaited()

    def test_find_transcript_reads_jsonl(self, bridge_backend, tmp_path, monkeypatch):
        """_find_transcript loads transcript.jsonl from the correct directory."""
        session_id = "test-sess-transcript"
        project_dir = tmp_path / ".amplifier" / "projects" / "test-project"
        transcript_dir = project_dir / "sessions" / session_id
        transcript_dir.mkdir(parents=True)

        transcript_path = transcript_dir / "transcript.jsonl"
        transcript_path.write_text(
            '{"role": "user", "content": "hello"}\n'
            '{"role": "assistant", "content": "hi there"}\n'
        )

        monkeypatch.setenv("HOME", str(tmp_path))

        from amplifier_distro.server.session_backend import FoundationBackend

        messages = FoundationBackend._find_transcript(bridge_backend, session_id)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi there"

    async def test_reconnect_chdir_home_if_cwd_deleted(self, bridge_backend):
        """_reconnect() must chdir to ~ and continue if os.getcwd() raises.

        When the server process's CWD has been deleted, BundleRegistry calls
        os.getcwd() and raises FileNotFoundError. The fix adds a guard before
        _load_bundle() that catches this and chdirs to home.
        """
        from amplifier_distro.server.session_backend import FoundationBackend

        mock_session = MagicMock()
        mock_session.session_id = "sess-cwd-001"
        mock_session.coordinator = MagicMock()
        mock_context = MagicMock()
        mock_context.get_messages = AsyncMock(return_value=[])
        mock_context.set_messages = AsyncMock()
        mock_session.coordinator.get = MagicMock(return_value=mock_context)

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)
        bridge_backend._find_transcript = MagicMock(
            return_value=[{"role": "user", "content": "hello"}]
        )

        mock_af_session = MagicMock()
        mock_af_session.find_orphaned_tool_calls.return_value = []

        home_dir = os.path.expanduser("~")

        with (
            patch.dict(sys.modules, {"amplifier_foundation.session": mock_af_session}),
            patch("os.getcwd", side_effect=FileNotFoundError("No such file")),
            patch("os.chdir") as mock_chdir,
        ):
            handle = await FoundationBackend._reconnect(bridge_backend, "sess-cwd-001")

        mock_chdir.assert_called_once_with(home_dir)
        assert handle.session_id == "sess-cwd-001"

        if "sess-cwd-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-cwd-001"].cancel()


# ── _SessionHandle.cancel ──────────────────────────────────────────────


class TestSessionHandleCancel:
    async def test_cancel_calls_coordinator_request_cancel(self):
        """cancel() delegates to session.coordinator.request_cancel()."""
        from amplifier_distro.server.session_backend import _SessionHandle

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.request_cancel = MagicMock()

        handle = _SessionHandle(
            session_id="s001",
            project_id="p001",
            working_dir=Path("/tmp"),
            session=mock_session,
        )
        await handle.cancel("graceful")
        # "graceful" maps to immediate=False (the bool the Rust binding expects)
        mock_session.coordinator.request_cancel.assert_called_once_with(False)

    async def test_cancel_no_session_does_not_raise(self):
        """cancel() returns early when session is None — must not raise."""
        from amplifier_distro.server.session_backend import _SessionHandle

        handle = _SessionHandle(
            session_id="s002",
            project_id="p002",
            working_dir=Path("/tmp"),
            session=None,
        )
        await handle.cancel("graceful")  # must not raise

    async def test_cancel_no_coordinator_does_not_raise(self):
        """cancel() returns early when coordinator is absent — must not raise."""
        from amplifier_distro.server.session_backend import _SessionHandle

        mock_session = MagicMock(spec=[])  # no coordinator attr
        handle = _SessionHandle(
            session_id="s003",
            project_id="p003",
            working_dir=Path("/tmp"),
            session=mock_session,
        )
        await handle.cancel("graceful")  # must not raise

    async def test_cancel_immediate_passes_true(self):
        """cancel('immediate') must pass immediate=True to the coordinator."""
        from amplifier_distro.server.session_backend import _SessionHandle

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.request_cancel = MagicMock()

        handle = _SessionHandle(
            session_id="s-imm",
            project_id="p-imm",
            working_dir=Path("/tmp"),
            session=mock_session,
        )
        await handle.cancel("immediate")
        mock_session.coordinator.request_cancel.assert_called_once_with(True)

    async def test_cancel_awaits_coroutine_request_cancel(self):
        """cancel() must await request_cancel when it is a coroutine function.

        The coordinator's request_cancel is async in production. The old code
        called request_cancel(level) without await, silently discarding the
        coroutine. This test uses AsyncMock to prove the coroutine is awaited.
        """
        from amplifier_distro.server.session_backend import _SessionHandle

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.request_cancel = AsyncMock()  # async — must be awaited

        handle = _SessionHandle(
            session_id="s-await-001",
            project_id="p-await-001",
            working_dir=Path("/tmp"),
            session=mock_session,
        )
        await handle.cancel("graceful")

        # "graceful" maps to immediate=False
        mock_session.coordinator.request_cancel.assert_awaited_once_with(False)


# ── FoundationBackend.execute ──────────────────────────────────────────


class TestFoundationBackendExecute:
    async def test_execute_calls_handle_run(self, bridge_backend):
        handle = _make_mock_handle("sess-exec-001")
        bridge_backend._sessions["sess-exec-001"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.execute(bridge_backend, "sess-exec-001", "hello")
        handle.run.assert_called_once_with("hello")

    async def test_execute_raises_on_unknown_session(self, bridge_backend):
        from amplifier_distro.server.session_backend import FoundationBackend

        with pytest.raises(ValueError, match="Unknown session"):
            await FoundationBackend.execute(bridge_backend, "no-such", "hi")

    async def test_execute_with_images_still_calls_run(self, bridge_backend):
        handle = _make_mock_handle("sess-img-001")
        bridge_backend._sessions["sess-img-001"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.execute(
            bridge_backend, "sess-img-001", "describe", images=["base64data"]
        )
        handle.run.assert_called_once_with("describe")


# ── FoundationBackend.cancel_session ───────────────────────────────────


class TestFoundationBackendCancelSession:
    async def test_cancel_session_delegates_to_handle(self, bridge_backend):
        mock_handle = MagicMock()
        mock_handle.cancel = AsyncMock()
        bridge_backend._sessions["sess-cancel-001"] = mock_handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.cancel_session(
            bridge_backend, "sess-cancel-001", "graceful"
        )
        mock_handle.cancel.assert_awaited_once_with("graceful")

    async def test_cancel_session_unknown_id_does_not_raise(self, bridge_backend):
        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.cancel_session(
            bridge_backend, "no-such", "immediate"
        )  # must not raise


# ── FoundationBackend.resolve_approval ─────────────────────────────────


class TestFoundationBackendResolveApproval:
    def test_resolve_delegates_to_approval_system(self, bridge_backend):
        mock_approval = MagicMock()
        mock_approval.handle_response = MagicMock(return_value=True)
        bridge_backend._approval_systems["s001"] = mock_approval

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.resolve_approval(
            bridge_backend, "s001", "req-001", "allow"
        )
        assert result is True
        mock_approval.handle_response.assert_called_once_with("req-001", "allow")

    def test_resolve_unknown_session_returns_false(self, bridge_backend):
        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.resolve_approval(
            bridge_backend, "no-session", "req", "allow"
        )
        assert result is False


# ── Event Queue Wiring ─────────────────────────────────────────────────


class TestFoundationBackendEventQueueWiring:
    async def test_create_session_with_queue_stores_approval_system(
        self, bridge_backend
    ):
        """When event_queue is provided, an ApprovalSystem is stored."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-eq-001"
        mock_session.project_id = "test-project"
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.hooks = MagicMock()

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        event_queue: asyncio.Queue = asyncio.Queue()
        await FoundationBackend.create_session(
            bridge_backend, working_dir="/tmp", event_queue=event_queue
        )

        assert "sess-eq-001" in bridge_backend._approval_systems
        # Cleanup
        if "sess-eq-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-eq-001"].cancel()

    async def test_create_session_without_queue_no_approval_system(
        self, bridge_backend
    ):
        """Without event_queue, no ApprovalSystem is created."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-eq-002"
        mock_session.project_id = "test-project"

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.create_session(bridge_backend, working_dir="/tmp")

        assert "sess-eq-002" not in bridge_backend._approval_systems
        if "sess-eq-002" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-eq-002"].cancel()

    async def test_end_session_cleans_up_approval_system(self, bridge_backend):
        """end_session() removes the approval system for that session."""
        from amplifier_distro.server.protocol_adapters import ApprovalSystem

        bridge_backend._approval_systems["sess-cleanup"] = ApprovalSystem()
        handle = _make_mock_handle("sess-cleanup")
        bridge_backend._sessions["sess-cleanup"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.end_session(bridge_backend, "sess-cleanup")

        assert "sess-cleanup" not in bridge_backend._approval_systems

    async def test_stop_clears_all_approval_systems(self, bridge_backend):
        """stop() clears the entire _approval_systems dict."""
        from amplifier_distro.server.protocol_adapters import ApprovalSystem

        bridge_backend._approval_systems["a"] = ApprovalSystem()
        bridge_backend._approval_systems["b"] = ApprovalSystem()

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.stop(bridge_backend)

        assert len(bridge_backend._approval_systems) == 0


class TestDoubleHookRegistrationGuard:
    """_wire_event_queue must not double-register hooks on resume."""

    async def test_wire_twice_does_not_double_register_hooks(self, bridge_backend):
        """Calling _wire_event_queue twice for the same session must not
        register hooks a second time (guards against page-refresh duplication)."""
        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.hooks = MagicMock()

        from amplifier_distro.server.session_backend import FoundationBackend

        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()

        # First wire — hooks should be registered
        FoundationBackend._wire_event_queue(
            bridge_backend, mock_session, "sess-double-001", q1
        )
        first_register_count = mock_session.coordinator.hooks.register.call_count

        # Second wire (simulating page refresh) — hooks must NOT be re-registered
        FoundationBackend._wire_event_queue(
            bridge_backend, mock_session, "sess-double-001", q2
        )
        second_register_count = mock_session.coordinator.hooks.register.call_count

        assert second_register_count == first_register_count, (
            f"Hooks registered twice: {first_register_count} -> {second_register_count}"
        )

    async def test_wire_guard_still_updates_approval_system(self, bridge_backend):
        """Second _wire_event_queue call must still update the approval system
        (new queue connection needs a new approval instance)."""
        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.hooks = MagicMock()

        from amplifier_distro.server.session_backend import FoundationBackend

        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()

        FoundationBackend._wire_event_queue(
            bridge_backend, mock_session, "sess-double-002", q1
        )
        approval_1 = bridge_backend._approval_systems.get("sess-double-002")

        FoundationBackend._wire_event_queue(
            bridge_backend, mock_session, "sess-double-002", q2
        )
        approval_2 = bridge_backend._approval_systems.get("sess-double-002")

        assert approval_1 is not None
        assert approval_2 is not None
        assert approval_1 is not approval_2, (
            "Approval system should be replaced on re-wire"
        )

    async def test_end_session_clears_wired_sessions(self, bridge_backend):
        """end_session must remove session from _wired_sessions set."""
        bridge_backend._wired_sessions = {"sess-end-wire"}
        handle = _make_mock_handle("sess-end-wire")
        bridge_backend._sessions["sess-end-wire"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.end_session(bridge_backend, "sess-end-wire")
        assert "sess-end-wire" not in bridge_backend._wired_sessions

    async def test_stop_clears_wired_sessions(self, bridge_backend):
        """stop() must clear the entire _wired_sessions set."""
        bridge_backend._wired_sessions = {"a", "b"}

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.stop(bridge_backend)
        assert len(bridge_backend._wired_sessions) == 0


# ── FoundationBackend.list_tools / list_modes / set_mode ──────────────────


class TestFoundationBackendListTools:
    """Verify FoundationBackend.list_tools uses coordinator.get('tools')."""

    def test_returns_tool_names_and_descriptions(self, bridge_backend):
        """list_tools returns name+description from live mounted tool objects."""
        mock_tool_bash = MagicMock()
        mock_tool_bash.description = "Execute shell commands"
        mock_tool_read = MagicMock()
        mock_tool_read.description = "Read file contents"

        mock_coordinator = MagicMock()
        mock_coordinator.get = MagicMock(
            return_value={"bash": mock_tool_bash, "read_file": mock_tool_read}
        )

        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-tools-001")
        handle.session = mock_session
        bridge_backend._sessions["sess-tools-001"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_tools(bridge_backend, "sess-tools-001")

        assert result is not None
        assert len(result) == 2
        names = [t["name"] for t in result]
        assert "bash" in names
        assert "read_file" in names
        descs = {t["name"]: t["description"] for t in result}
        assert descs["bash"] == "Execute shell commands"
        mock_coordinator.get.assert_called_with("tools")

    def test_returns_none_for_unknown_session(self, bridge_backend):
        """list_tools returns None when session doesn't exist."""
        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_tools(bridge_backend, "no-such")
        assert result is None

    def test_returns_empty_list_when_no_tools(self, bridge_backend):
        """list_tools returns [] when coordinator has no tools mounted."""
        mock_coordinator = MagicMock()
        mock_coordinator.get = MagicMock(return_value={})
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-tools-empty")
        handle.session = mock_session
        bridge_backend._sessions["sess-tools-empty"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_tools(bridge_backend, "sess-tools-empty")
        assert result == []

    def test_handles_missing_description(self, bridge_backend):
        """list_tools uses fallback when tool lacks .description."""
        mock_tool = MagicMock(spec=[])  # no description attr

        mock_coordinator = MagicMock()
        mock_coordinator.get = MagicMock(return_value={"weird_tool": mock_tool})
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-tools-nodesc")
        handle.session = mock_session
        bridge_backend._sessions["sess-tools-nodesc"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_tools(bridge_backend, "sess-tools-nodesc")
        assert result[0]["description"] == "No description"

    def test_handles_none_session(self, bridge_backend):
        """list_tools returns None when handle.session is None."""
        handle = _make_mock_handle("sess-tools-none")
        handle.session = None
        bridge_backend._sessions["sess-tools-none"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_tools(bridge_backend, "sess-tools-none")
        assert result is None


class TestFoundationBackendListModes:
    """Verify FoundationBackend.list_modes reads session_state defensively."""

    def test_returns_modes_with_active(self, bridge_backend):
        """list_modes returns mode list and active mode from session_state."""
        mock_discovery = MagicMock()
        mock_discovery.list_modes = MagicMock(
            return_value=[
                ("plan", "Planning mode", "modes-bundle"),
                ("explore", "Exploration mode", "modes-bundle"),
            ]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {
            "active_mode": "plan",
            "mode_discovery": mock_discovery,
        }
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-modes-001")
        handle.session = mock_session
        bridge_backend._sessions["sess-modes-001"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_modes(bridge_backend, "sess-modes-001")

        assert result is not None
        assert result["active_mode"] == "plan"
        assert len(result["modes"]) == 2
        assert result["modes"][0]["name"] == "plan"
        assert result["modes"][1]["source"] == "modes-bundle"

    def test_returns_empty_when_no_session_state(self, bridge_backend):
        """list_modes degrades gracefully when session_state doesn't exist."""
        mock_coordinator = MagicMock(spec=[])  # no session_state
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-modes-nostate")
        handle.session = mock_session
        bridge_backend._sessions["sess-modes-nostate"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_modes(bridge_backend, "sess-modes-nostate")
        assert result == {"active_mode": None, "modes": []}

    def test_returns_empty_when_no_discovery(self, bridge_backend):
        """list_modes degrades when mode_discovery not in session_state."""
        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {"active_mode": None}
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-modes-nodisc")
        handle.session = mock_session
        bridge_backend._sessions["sess-modes-nodisc"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_modes(bridge_backend, "sess-modes-nodisc")
        assert result == {"active_mode": None, "modes": []}

    def test_returns_none_for_unknown_session(self, bridge_backend):
        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.list_modes(bridge_backend, "no-such")
        assert result is None


class TestFoundationBackendSetMode:
    """Verify FoundationBackend.set_mode validates and mutates session_state."""

    def test_activate_valid_mode(self, bridge_backend):
        """set_mode activates a mode and calls reset_warnings."""
        mock_mode_def = MagicMock()
        mock_discovery = MagicMock()
        mock_discovery.find = MagicMock(return_value=mock_mode_def)
        mock_hooks = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {
            "active_mode": None,
            "mode_discovery": mock_discovery,
            "mode_hooks": mock_hooks,
        }
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-set-001")
        handle.session = mock_session
        bridge_backend._sessions["sess-set-001"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "sess-set-001", "plan")

        assert result["active_mode"] == "plan"
        assert result["previous_mode"] is None
        assert mock_coordinator.session_state["active_mode"] == "plan"
        mock_discovery.find.assert_called_once_with("plan")
        mock_hooks.reset_warnings.assert_called_once()

    def test_deactivate_mode(self, bridge_backend):
        """set_mode(None) deactivates the current mode."""
        mock_hooks = MagicMock()

        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {
            "active_mode": "plan",
            "mode_discovery": MagicMock(),
            "mode_hooks": mock_hooks,
        }
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-set-002")
        handle.session = mock_session
        bridge_backend._sessions["sess-set-002"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "sess-set-002", None)

        assert result["active_mode"] is None
        assert result["previous_mode"] == "plan"
        assert mock_coordinator.session_state["active_mode"] is None
        mock_hooks.reset_warnings.assert_called_once()

    def test_rejects_invalid_mode_name(self, bridge_backend):
        """set_mode returns error for non-existent mode with available list."""
        mock_discovery = MagicMock()
        mock_discovery.find = MagicMock(return_value=None)
        mock_discovery.list_modes = MagicMock(
            return_value=[("plan", "Plan", "s"), ("explore", "Explore", "s")]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {
            "active_mode": None,
            "mode_discovery": mock_discovery,
            "mode_hooks": MagicMock(),
        }
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-set-003")
        handle.session = mock_session
        bridge_backend._sessions["sess-set-003"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "sess-set-003", "bogus")

        assert "error" in result
        assert "bogus" in result["error"]
        assert "plan" in result["available_modes"]
        # active_mode should NOT have changed
        assert mock_coordinator.session_state["active_mode"] is None

    def test_error_when_no_session_state(self, bridge_backend):
        """set_mode returns error when session_state doesn't exist."""
        mock_coordinator = MagicMock(spec=[])  # no session_state
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-set-nostate")
        handle.session = mock_session
        bridge_backend._sessions["sess-set-nostate"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "sess-set-nostate", "plan")
        assert "error" in result

    def test_error_for_unknown_session(self, bridge_backend):
        """set_mode returns error when session doesn't exist."""
        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "no-such", "plan")
        assert "error" in result

    def test_works_without_mode_hooks(self, bridge_backend):
        """set_mode activates even when mode_hooks isn't in session_state."""
        mock_mode_def = MagicMock()
        mock_discovery = MagicMock()
        mock_discovery.find = MagicMock(return_value=mock_mode_def)

        mock_coordinator = MagicMock()
        mock_coordinator.session_state = {
            "active_mode": None,
            "mode_discovery": mock_discovery,
            # no mode_hooks key
        }
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator

        handle = _make_mock_handle("sess-set-nohooks")
        handle.session = mock_session
        bridge_backend._sessions["sess-set-nohooks"] = handle

        from amplifier_distro.server.session_backend import FoundationBackend

        result = FoundationBackend.set_mode(bridge_backend, "sess-set-nohooks", "plan")
        assert result["active_mode"] == "plan"


class TestSessionBackendProtocol:
    def test_protocol_declares_resume_session(self):
        from amplifier_distro.server.session_backend import SessionBackend

        assert hasattr(SessionBackend, "resume_session"), (
            "SessionBackend Protocol must declare resume_session"
        )


# ── MockBackend new method stubs ───────────────────────────────────────


class TestFoundationBackendSpawnRegistration:
    """Verify session.spawn capability is registered on create and reconnect."""

    async def test_create_session_registers_spawn_capability(self, bridge_backend):
        """create_session() must register session.spawn on the coordinator."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-spawn-create-001"
        mock_session.project_id = "test-project"
        mock_session.coordinator = MagicMock()

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)

        from amplifier_distro.server.session_backend import FoundationBackend

        await FoundationBackend.create_session(
            bridge_backend,
            working_dir="/tmp",
            description="spawn test",
        )

        mock_session.coordinator.register_capability.assert_any_call(
            "session.spawn", unittest.mock.ANY
        )
        # Cleanup
        if "sess-spawn-create-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-spawn-create-001"].cancel()

    async def test_reconnect_registers_spawn_capability(self, bridge_backend):
        """_reconnect() must register session.spawn on the coordinator."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-spawn-rc-001"
        mock_session.coordinator = MagicMock()

        # Context needs async-compatible methods for transcript injection
        mock_context = MagicMock()
        mock_context.get_messages = AsyncMock(return_value=[])
        mock_context.set_messages = AsyncMock()
        mock_session.coordinator.get = MagicMock(return_value=mock_context)

        mock_prepared = MagicMock()
        mock_prepared.create_session = AsyncMock(return_value=mock_session)
        bridge_backend._load_bundle = AsyncMock(return_value=mock_prepared)
        bridge_backend._find_transcript = MagicMock(
            return_value=[{"role": "user", "content": "hello"}]
        )

        # Ensure amplifier_foundation.session is mockable even without a real install
        mock_af_session = MagicMock()
        mock_af_session.find_orphaned_tool_calls.return_value = []

        with patch.dict(
            sys.modules,
            {"amplifier_foundation.session": mock_af_session},
        ):
            from amplifier_distro.server.session_backend import FoundationBackend

            await FoundationBackend._reconnect(bridge_backend, "sess-spawn-rc-001")

        mock_session.coordinator.register_capability.assert_any_call(
            "session.spawn", unittest.mock.ANY
        )
        # Cleanup
        if "sess-spawn-rc-001" in bridge_backend._worker_tasks:
            bridge_backend._worker_tasks["sess-spawn-rc-001"].cancel()


class TestMockBackendNewMethods:
    async def test_create_session_accepts_event_queue(self):
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        q: asyncio.Queue = asyncio.Queue()
        info = await backend.create_session(working_dir="~", event_queue=q)
        assert info.session_id is not None

    async def test_execute_records_call(self):
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        info = await backend.create_session()
        await backend.execute(info.session_id, "hello")
        assert any(c["method"] == "execute" for c in backend.calls)

    async def test_cancel_session_records_call(self):
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        await backend.cancel_session("any-id", "graceful")
        assert any(c["method"] == "cancel_session" for c in backend.calls)

    def test_resolve_approval_returns_false(self):
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        assert backend.resolve_approval("s", "r", "allow") is False

    async def test_resume_session_accepts_event_queue(self):
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        q: asyncio.Queue = asyncio.Queue()
        await backend.resume_session("s", "~", event_queue=q)
        assert any(c["method"] == "resume_session" for c in backend.calls)


class TestUpdateSessionMetadata:
    """Verify update_session_metadata on MockBackend and Protocol compliance."""

    async def test_mock_backend_update_session_metadata_returns_true(self):
        """MockBackend.update_session_metadata records the call and returns True."""
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        result = await backend.update_session_metadata(
            "sess-001", {"name": "My Session"}
        )

        assert result is True
        assert any(
            c["method"] == "update_session_metadata" and c["session_id"] == "sess-001"
            for c in backend.calls
        )

    async def test_mock_backend_update_records_updates_dict(self):
        """MockBackend records the full updates dict in the call log."""
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        await backend.update_session_metadata("sess-002", {"name": "Renamed"})

        call = next(
            c for c in backend.calls if c["method"] == "update_session_metadata"
        )
        assert call["updates"] == {"name": "Renamed"}

    async def test_protocol_has_update_session_metadata(self):
        """SessionBackend Protocol must declare update_session_metadata."""
        from amplifier_distro.server.session_backend import SessionBackend

        assert hasattr(SessionBackend, "update_session_metadata")


class TestFoundationBackendUpdateSessionMetadata:
    """Verify FoundationBackend.update_session_metadata.

    Covers active, inactive, and missing session scenarios.
    """

    async def test_active_session_writes_metadata(self, bridge_backend, tmp_path):
        """Active session: resolves dir via handle, calls write_metadata."""
        handle = _make_mock_handle("sess-active-001")
        handle.project_id = "proj-a"
        bridge_backend._sessions["sess-active-001"] = handle

        # Create the session directory under projects/ (PROJECTS_DIR = "projects")
        session_dir = tmp_path / "projects" / "proj-a" / "sessions" / "sess-active-001"
        session_dir.mkdir(parents=True)

        from amplifier_distro.server.session_backend import FoundationBackend

        with (
            patch(
                "amplifier_distro.server.session_backend.AMPLIFIER_HOME", str(tmp_path)
            ),
            patch(
                "amplifier_distro.server.session_backend.write_metadata"
            ) as mock_write,
        ):
            result = await FoundationBackend.update_session_metadata(
                bridge_backend, "sess-active-001", {"name": "Renamed"}
            )

        assert result is True
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][1] == {"name": "Renamed"}

    async def test_inactive_session_scans_disk(self, bridge_backend, tmp_path):
        """Inactive session: falls back to disk scan like _find_transcript."""
        # No handle in _sessions — session is inactive
        session_dir = (
            tmp_path / "projects" / "proj-x" / "sessions" / "sess-inactive-001"
        )
        session_dir.mkdir(parents=True)

        from amplifier_distro.server.session_backend import FoundationBackend

        with (
            patch(
                "amplifier_distro.server.session_backend.AMPLIFIER_HOME", str(tmp_path)
            ),
            patch(
                "amplifier_distro.server.session_backend.write_metadata"
            ) as mock_write,
        ):
            result = await FoundationBackend.update_session_metadata(
                bridge_backend, "sess-inactive-001", {"name": "Offline Rename"}
            )

        assert result is True
        mock_write.assert_called_once()

    async def test_missing_session_returns_false(self, bridge_backend, tmp_path):
        """Session not found anywhere: returns False."""
        from amplifier_distro.server.session_backend import FoundationBackend

        # Create empty projects dir — no session directories inside
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "amplifier_distro.server.session_backend.AMPLIFIER_HOME", str(tmp_path)
        ):
            result = await FoundationBackend.update_session_metadata(
                bridge_backend, "sess-nonexistent", {"name": "Ghost"}
            )

        assert result is False


class TestQueueRewire:
    """Tests for queue holder fanout on reconnect."""

    def _make_wired_backend(self):
        """Build a FoundationBackend with a wired session for testing."""
        from unittest.mock import patch

        from amplifier_distro.server.session_backend import FoundationBackend

        target = "amplifier_distro.server.session_backend.FoundationBackend.__init__"
        with patch(target) as mock_init:
            mock_init.return_value = None
            backend = FoundationBackend.__new__(FoundationBackend)
            backend._bundle_name = "test-bundle"
            backend._sessions = {}
            backend._reconnect_locks = {}
            backend._session_queues = {}
            backend._worker_tasks = {}
            backend._ended_sessions = set()
            backend._approval_systems = {}
            backend._wired_sessions = set()
            backend._queue_holders = {}
            backend._event_forwarders = {}
        return backend

    def _make_mock_session(self):
        """Build a mock session with coordinator + hooks."""
        session = MagicMock()
        coordinator = MagicMock()
        hooks = MagicMock()
        hooks.register = MagicMock()
        hooks.unregister = MagicMock()
        coordinator.hooks = hooks
        coordinator.set = MagicMock()
        session.coordinator = coordinator
        return session

    def _capture_on_stream(self, session):
        """Extract the on_stream hook handler from mock register calls."""
        register_calls = session.coordinator.hooks.register.call_args_list
        for call in register_calls:
            args = call[0]
            if len(args) >= 2 and callable(args[1]):
                return args[1]
        raise AssertionError("on_stream hook must be registered")

    # ------------------------------------------------------------------
    # Fanout: both original and reconnected clients receive events
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_events_fanout_to_both_queues_after_reconnect(self):
        """After reconnect, events must fan out to BOTH queues (not just new)."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-fanout"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        # First wire: create with queue_a (BrowserA)
        queue_a = asyncio.Queue()
        unregister = backend._wire_event_queue(session, session_id, queue_a)
        handle.hook_unregister = unregister

        on_stream = self._capture_on_stream(session)

        # Verify events go to queue_a
        await on_stream("test:event", {"data": "first"})
        assert queue_a.qsize() == 1
        queue_a.get_nowait()

        # Reconnect: wire with queue_b (BrowserB)
        queue_b = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue_b)

        # Events must fan out to BOTH queues
        await on_stream("test:event", {"data": "second"})
        assert queue_a.qsize() == 1, "Original queue must still receive events"
        assert queue_b.qsize() == 1, "New queue must also receive events"
        assert queue_a.get_nowait() == ("test:event", {"data": "second"})
        assert queue_b.get_nowait() == ("test:event", {"data": "second"})

    @pytest.mark.asyncio
    async def test_display_system_uses_holder_for_fanout(self):
        """Display system must be wired to the holder, not a raw queue."""

        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-display-holder"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue_a = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue_a)

        # The initial display system should have been set with the holder
        set_calls = session.coordinator.set.call_args_list
        display_calls = [c for c in set_calls if c[0][0] == "display"]
        assert len(display_calls) >= 1, "Display system must be set on initial wire"

        # On reconnect, display should NOT be re-set (holder already fans out)
        queue_b = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue_b)

        display_calls_after = [
            c for c in session.coordinator.set.call_args_list if c[0][0] == "display"
        ]
        # Should still be just the one initial call — reconnect doesn't re-set
        assert len(display_calls_after) == len(display_calls), (
            "Display system should not be re-set on reconnect (holder fans out)"
        )

    @pytest.mark.asyncio
    async def test_queue_holder_cleaned_up_on_unregister(self):
        """Calling unregister must remove the queue holder."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-cleanup"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue = asyncio.Queue()
        unregister = backend._wire_event_queue(session, session_id, queue)

        assert session_id in backend._queue_holders
        assert session_id in backend._wired_sessions

        unregister()

        assert session_id not in backend._queue_holders
        assert session_id not in backend._wired_sessions

    @pytest.mark.asyncio
    async def test_rapid_reconnect_cycles_fanout_to_all(self):
        """Multiple rapid reconnects must fan out events to all queues."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-rapid"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        # First wire
        queue_1 = asyncio.Queue()
        unregister = backend._wire_event_queue(session, session_id, queue_1)
        handle.hook_unregister = unregister

        on_stream = self._capture_on_stream(session)

        # 5 rapid reconnects
        all_queues = [queue_1]
        for _ in range(5):
            q = asyncio.Queue()
            all_queues.append(q)
            backend._wire_event_queue(session, session_id, q)

        # Events should fan out to ALL 6 queues
        await on_stream("test:event", {"cycle": "final"})
        for i, q in enumerate(all_queues):
            assert q.qsize() == 1, f"Queue {i} must receive the event"

    # ------------------------------------------------------------------
    # dequeue_client: cleanup on disconnect
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_dequeue_client_removes_queue_from_fanout(self):
        """Disconnected clients must stop receiving events."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-dequeue"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue_a = asyncio.Queue()
        queue_b = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue_a)
        backend._wire_event_queue(session, session_id, queue_b)

        on_stream = self._capture_on_stream(session)

        # Both receive events
        await on_stream("test:event", {"data": "both"})
        assert queue_a.qsize() == 1
        assert queue_b.qsize() == 1
        queue_a.get_nowait()
        queue_b.get_nowait()

        # BrowserA disconnects
        backend.dequeue_client(session_id, queue_a)

        # Only queue_b should receive events now
        await on_stream("test:event", {"data": "only-b"})
        assert queue_a.qsize() == 0, "Dequeued client must not receive events"
        assert queue_b.qsize() == 1, "Remaining client must still receive events"

    def test_dequeue_client_noop_for_unknown_session(self):
        """dequeue_client must be safe to call for unknown sessions."""
        backend = self._make_wired_backend()
        # Should not raise
        backend.dequeue_client("nonexistent", asyncio.Queue())

    # ------------------------------------------------------------------
    # broadcast_user_message: user input fanout to other clients
    # ------------------------------------------------------------------

    def test_broadcast_user_message_delivers_to_all_queues(self):
        """broadcast_user_message must push user_message to all queues."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-user-msg"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue_a = asyncio.Queue()
        queue_b = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue_a)
        backend._wire_event_queue(session, session_id, queue_b)

        backend.broadcast_user_message(session_id, "hello world")

        for q in (queue_a, queue_b):
            assert q.qsize() == 1
            event_name, data = q.get_nowait()
            assert event_name == "user_message"
            assert data["content"] == "hello world"

    def test_broadcast_user_message_includes_images(self):
        """broadcast_user_message must include images when provided."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-user-msg-img"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue)

        backend.broadcast_user_message(
            session_id, "look at this", images=["data:image/png;base64,abc"]
        )

        event_name, data = queue.get_nowait()
        assert event_name == "user_message"
        assert data["content"] == "look at this"
        assert data["images"] == ["data:image/png;base64,abc"]

    def test_broadcast_user_message_noop_for_unknown_session(self):
        """broadcast_user_message must not raise for unknown sessions."""
        backend = self._make_wired_backend()
        # Should not raise
        backend.broadcast_user_message("nonexistent", "hello")

    def test_broadcast_user_message_no_images_key_when_none(self):
        """broadcast_user_message must omit images key when not provided."""
        backend = self._make_wired_backend()
        session = self._make_mock_session()
        session_id = "test-no-img"

        handle = MagicMock()
        handle.session = session
        handle.hook_unregister = None
        backend._sessions[session_id] = handle

        queue = asyncio.Queue()
        backend._wire_event_queue(session, session_id, queue)

        backend.broadcast_user_message(session_id, "no images")

        _, data = queue.get_nowait()
        assert "images" not in data


class TestQueueHolder:
    """Unit tests for the _QueueHolder fanout wrapper."""

    def test_single_queue_put_nowait(self):
        """put_nowait with a single queue delivers the item."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q = asyncio.Queue()
        holder = _QueueHolder(q)
        holder.put_nowait(("event", {"data": 1}))
        assert q.qsize() == 1
        assert q.get_nowait() == ("event", {"data": 1})

    def test_fanout_to_multiple_queues(self):
        """put_nowait broadcasts to all registered queues."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        q3 = asyncio.Queue()
        holder = _QueueHolder(q1)
        holder.add(q2)
        holder.add(q3)

        holder.put_nowait(("event", {"data": "all"}))
        for q in (q1, q2, q3):
            assert q.qsize() == 1
            assert q.get_nowait() == ("event", {"data": "all"})

    def test_remove_stops_delivery(self):
        """After remove(), that queue no longer receives events."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        holder = _QueueHolder(q1)
        holder.add(q2)

        holder.remove(q1)
        holder.put_nowait(("event", {}))
        assert q1.qsize() == 0, "Removed queue must not get events"
        assert q2.qsize() == 1

    def test_remove_unknown_queue_is_noop(self):
        """remove() with an unknown queue does not raise."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q1 = asyncio.Queue()
        holder = _QueueHolder(q1)
        # Should not raise
        holder.remove(asyncio.Queue())

    def test_put_nowait_empty_set_raises_queue_full(self):
        """put_nowait on an empty holder raises QueueFull."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q = asyncio.Queue()
        holder = _QueueHolder(q)
        holder.remove(q)  # Now empty

        with pytest.raises(asyncio.QueueFull):
            holder.put_nowait(("event", {}))

    def test_partial_queue_full_still_delivers_to_others(self):
        """If one queue is full, others still receive the event."""
        from amplifier_distro.server.session_backend import _QueueHolder

        full_q = asyncio.Queue(maxsize=1)
        full_q.put_nowait("filler")  # Fill it up
        normal_q = asyncio.Queue()

        holder = _QueueHolder(full_q)
        holder.add(normal_q)

        # Should NOT raise — partial delivery is ok
        holder.put_nowait(("event", {"data": "partial"}))
        assert normal_q.qsize() == 1
        assert normal_q.get_nowait() == ("event", {"data": "partial"})

    def test_add_same_queue_twice_delivers_once(self):
        """Adding the same queue twice must not cause double delivery."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q = asyncio.Queue()
        holder = _QueueHolder(q)
        holder.add(q)  # duplicate add — set ignores it
        holder.put_nowait(("event", {"data": "once"}))
        assert q.qsize() == 1, "Duplicate add must not cause double delivery"

    def test_all_queues_full_raises_queue_full(self):
        """If ALL queues are full, put_nowait raises QueueFull."""
        from amplifier_distro.server.session_backend import _QueueHolder

        q1 = asyncio.Queue(maxsize=1)
        q2 = asyncio.Queue(maxsize=1)
        q1.put_nowait("filler")
        q2.put_nowait("filler")

        holder = _QueueHolder(q1)
        holder.add(q2)

        with pytest.raises(asyncio.QueueFull):
            holder.put_nowait(("event", {}))
